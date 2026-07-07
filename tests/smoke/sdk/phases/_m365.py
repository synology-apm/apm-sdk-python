"""M365 domain phase: apm.saas, apm.m365.workloads per M365WorkloadType, and exchange/group export.

Populates ctx.data["m365_workloads"] (dict keyed by scope, for the activity phase),
ctx.data["m365_retired_workloads"] (dict keyed by scope, for the change_plan round trips),
and ctx.data["m365_exports"] (dict keyed by scope, for the exchange/group export round trip).

Also runs the ``apm.m365.workloads.change_plan()`` round trips across all scopes' workloads,
run automatically when suitable data exists (same switch/restore/retired-no-op shape as the
machine phase's round trips). Reads ctx.data["protection_plans"] and
ctx.data["retirement_plans"] (from the plan phase, which runs first).
"""
from __future__ import annotations

import asyncio

from synology_apm.sdk import (
    APIError,
    APMError,
    ExchangeExportCollection,
    GroupExportCollection,
    InvalidOperationError,
    M365ExportStartResult,
    M365ExportStatus,
    M365GroupInfo,
    M365SiteInfo,
    M365TeamInfo,
    M365UserInfo,
    M365Workload,
    M365WorkloadType,
    ProtectionPlan,
    ResourceNotFoundError,
    ResourceNotReadyError,
    RetirementPlan,
    WorkloadCategory,
    WorkloadVersion,
)

from .._context import SmokeContext
from .._trace import current_step as _current_step
from . import _shared
from ._shared import SENTINEL_NAME as _SENTINEL_NAME
from ._shared import ZERO_UUID as _ZERO_UUID

DOMAIN = "m365"
_EXPORT_SCOPES = ("exchange", "group")
_SCOPE_INFO_TYPE: dict[str, type] = {
    "exchange": M365UserInfo,
    "onedrive": M365UserInfo,
    "chat": M365UserInfo,
    "sharepoint": M365SiteInfo,
    "teams": M365TeamInfo,
    "group": M365GroupInfo,
}
_SCOPE_INFO_FIELD: dict[str, str] = {
    "exchange": "user_principal_name", "onedrive": "user_principal_name",
    "chat": "user_principal_name",
    "sharepoint": "site_url", "teams": "team_name", "group": "display_name",
}
_EXPORT_POLL_ATTEMPTS = 5
_EXPORT_POLL_INTERVAL_SECONDS = 2.0
_EXPORT_WORKLOAD_FALLBACK_LIMIT = 5  # max extra workloads to try when one has no exportable folders
_VERSIONS_FALLBACK_LIMIT = 5         # max extra workloads to probe when finding wv/wnv


async def run(ctx: SmokeContext) -> None:
    apm = ctx.apm

    saas_result = await ctx.call(DOMAIN, "m365.saas.list", lambda: apm.saas.list(limit=500))
    tenants, _total = saas_result if saas_result is not None else ([], 0)
    m365_tenant = next((t for t in tenants if t.category == WorkloadCategory.M365), None)

    ctx.data["m365_tenant"] = m365_tenant

    if m365_tenant is None:
        all_remaining = [step for scope in ctx.m365_scopes for step in _scope_steps(scope)]
        all_remaining += _NON_SCOPE_STEPS
        ctx.skip_remaining(DOMAIN, all_remaining, reason="No M365 tenant configured")
        return

    await ctx.call(
        DOMAIN, "m365.saas.get_m365_tenant[direct]",
        lambda: apm.saas.get_m365_tenant(m365_tenant.tenant_id),
    )

    await ctx.call_expect_not_found(DOMAIN, "m365.saas", "get_m365_tenant",
        lambda: apm.saas.get_m365_tenant(_ZERO_UUID), "SaasTenant", _ZERO_UUID)

    ctx.data["m365_workloads"] = {}
    ctx.data["m365_retired_workloads"] = {}
    for scope in ctx.m365_scopes:
        await _run_scope(ctx, m365_tenant.tenant_id, scope)

    await _run_change_plan_roundtrips(ctx)

    # ── Client-side guard tests ───────────────────────────────────────────────

    m365_retirement_plan = next((p for p in ctx.data.get("retirement_plans", [])), None)
    m365_prot_plan = next(
        (p for p in ctx.data.get("protection_plans", []) if p.category == WorkloadCategory.M365), None)
    m365_machine_prot_plan = next(
        (p for p in ctx.data.get("protection_plans", []) if p.category == WorkloadCategory.MACHINE), None)
    m365_active_wl = next(
        (w for scope_wls in ctx.data.get("m365_workloads", {}).values() for w in scope_wls), None)
    m365_retired_wl = next(
        (w for scope_wls in ctx.data.get("m365_retired_workloads", {}).values() for w in scope_wls), None)

    _act = m365_active_wl
    _ret = m365_retired_wl
    _rp = m365_retirement_plan
    _pp = m365_prot_plan
    _mp = m365_machine_prot_plan

    await ctx.guard_error(
        DOMAIN, "m365.workloads.change_plan[active+ret_raises]",
        "m365.workloads.check[change_plan_active_ret",
        _act is not None and _rp is not None,
        lambda: apm.m365.workloads.change_plan(_act, _rp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _act.workload_id if _act is not None else None,
        skip_reason="No active M365 workload or no retirement plan",
    )
    await ctx.guard_error(
        DOMAIN, "m365.workloads.change_plan[retired+m365_prot_raises]",
        "m365.workloads.check[change_plan_retired_m365_prot",
        _ret is not None and _pp is not None,
        lambda: apm.m365.workloads.change_plan(_ret, _pp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired M365 workload or no M365 protection plan",
    )
    await ctx.guard_error(
        DOMAIN, "m365.workloads.change_plan[category_mismatch_raises]",
        "m365.workloads.check[change_plan_category_mismatch",
        _act is not None and _mp is not None,
        lambda: apm.m365.workloads.change_plan(_act, _mp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _act.workload_id if _act is not None else None,
        skip_reason="No active M365 workload or no machine protection plan",
    )
    await ctx.guard_error(
        DOMAIN, "m365.workloads.cancel_backup[retired_raises]",
        "m365.workloads.check[cancel_backup_retired",
        _ret is not None,
        lambda: apm.m365.workloads.cancel_backup(_ret),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired M365 workloads found",
    )
    await ctx.guard_error(
        DOMAIN, "m365.workloads.retire[already_retired_raises]",
        "m365.workloads.check[retire_already_retired",
        _ret is not None and _rp is not None,
        lambda: apm.m365.workloads.retire(_ret, _rp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired M365 workload or no retirement plan",
    )


def _scope_steps(scope: str) -> tuple[str, ...]:
    steps: tuple[str, ...] = (
        f"m365.{scope}.list[all]",
        f"m365.{scope}.list[retired]",
        f"m365.{scope}.check[workload_type]",
        f"m365.{scope}.get[direct]",
        f"m365.{scope}.get_by_name[search]",
        f"m365.{scope}.check[info_type]",
        f"m365.{scope}.check[get_by_name_label]",
        f"m365.{scope}.get[not_found]",
        f"m365.{scope}.check[get_nf_resource_type]",
        f"m365.{scope}.check[get_nf_resource_id]",
        f"m365.{scope}.get_by_name[not_found]",
        f"m365.{scope}.check[get_by_name_nf_resource_type]",
        f"m365.{scope}.check[get_by_name_nf_resource_id]",
        f"m365.{scope}.versions.list[search]",
        f"m365.{scope}.versions.get_latest",
        f"m365.{scope}.versions.get_latest[nv_raises]",
        f"m365.{scope}.check[get_latest_nv_resource_type]",
        f"m365.{scope}.check[get_latest_nv_resource_id]",
        f"m365.{scope}.versions.get_version[not_found]",
        f"m365.{scope}.versions.check[get_version_nf_resource_type]",
        f"m365.{scope}.versions.check[get_version_nf_resource_id]",
        f"m365.{scope}.lock_unlock_roundtrip",
        f"m365.{scope}.check[lock_roundtrip]",
        f"m365.{scope}.versions.lock_version[empty_loc_raises]",
        f"m365.{scope}.check[lock_empty_loc_exception]",
        f"m365.{scope}.backup_cancel_roundtrip",
    )
    if scope in _EXPORT_SCOPES:
        steps += (
            f"m365.{scope}.export.start",
            f"m365.{scope}.export.list",
            f"m365.{scope}.export.download_url.get",
            f"m365.{scope}.export.cancel",
        )
    return steps


async def _run_scope(ctx: SmokeContext, tenant_id: str, scope: str) -> None:
    apm = ctx.apm
    workload_type = M365WorkloadType(scope)

    all_result = await ctx.call(
        DOMAIN, f"m365.{scope}.list[all]",
        lambda: apm.m365.workloads.list(tenant_id, workload_type, limit=500),
    )
    workloads, _total = all_result if all_result is not None else ([], 0)
    ctx.data["m365_workloads"][scope] = workloads

    retired_result = await ctx.call(
        DOMAIN, f"m365.{scope}.list[retired]",
        lambda: apm.m365.workloads.list(tenant_id, workload_type, is_retired=True, limit=500),
    )
    retired_workloads, _retired_total = retired_result if retired_result is not None else ([], 0)
    ctx.data["m365_retired_workloads"][scope] = retired_workloads

    ctx.check(
        DOMAIN, f"m365.{scope}.check[workload_type]",
        all(w.workload_type == workload_type for w in workloads),
        note="list() with workload_type must only contain that type.",
    )

    if not workloads:
        reason = f"No {workload_type.name} M365 Workloads found"
        ctx.skip_remaining(DOMAIN, _scope_steps(scope), reason=reason)
        return

    w0 = workloads[0]

    await ctx.call(
        DOMAIN, f"m365.{scope}.get[direct]",
        lambda: apm.m365.workloads.get(w0.workload_id, w0.namespace, tenant_id, workload_type),
    )

    by_name = await ctx.call(
        DOMAIN, f"m365.{scope}.get_by_name[search]",
        lambda: apm.m365.workloads.get_by_name(w0.name, tenant_id, workload_type),
    )

    ctx.check(
        DOMAIN, f"m365.{scope}.check[info_type]",
        isinstance(w0.info, _SCOPE_INFO_TYPE[scope])
        and bool(getattr(w0.info, _SCOPE_INFO_FIELD[scope], "")),
    )

    if by_name is not None:
        ctx.check(
            DOMAIN, f"m365.{scope}.check[get_by_name_label]", by_name.workload_id == w0.workload_id,
        )
    else:
        ctx.skip(
            DOMAIN, f"m365.{scope}.check[get_by_name_label]",
            f"m365.{scope}.get_by_name[search] did not return a result",
        )

    # ── not-found errors ──────────────────────────────────────────────────────

    _w0_ns = w0.namespace
    await ctx.call_expect_not_found(DOMAIN, f"m365.{scope}", "get",
        lambda: apm.m365.workloads.get(_ZERO_UUID, _w0_ns, tenant_id, workload_type),
        "M365Workload", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, f"m365.{scope}", "get_by_name",
        lambda: apm.m365.workloads.get_by_name(_SENTINEL_NAME, tenant_id, workload_type),
        "M365Workload", _SENTINEL_NAME)

    versions_result = await ctx.call(
        DOMAIN, f"m365.{scope}.versions.list[search]", lambda: apm.m365.workloads.list_versions(w0, limit=20)
    )
    versions, _versions_total = versions_result if versions_result is not None else ([], 0)

    # Identify wv (workload with versions, for the happy path) and wnv (workload without
    # versions, for the no-version error path).  w0 always starts in one bucket; other
    # workloads of the same scope are probed silently until both buckets are filled.
    wv: M365Workload | None = w0 if versions else None
    wnv: M365Workload | None = w0 if not versions else None
    _wv_versions: list[WorkloadVersion] = list(versions)

    for wx in [w for w in workloads if w.workload_id != w0.workload_id][:_VERSIONS_FALLBACK_LIMIT]:
        if wv is not None and wnv is not None:
            break
        try:
            wx_result = await apm.m365.workloads.list_versions(wx, limit=20)
            wx_versions, _ = wx_result if wx_result is not None else ([], 0)
        except Exception:
            wx_versions = []
        if wx_versions and wv is None:
            wv = wx
            _wv_versions = list(wx_versions)
        elif not wx_versions and wnv is None:
            wnv = wx

    v0: WorkloadVersion | None = _wv_versions[0] if _wv_versions else None

    # ── get_latest: happy path (wv) ──────────────────────────────────────────

    _no_wv_reason = "No workload with backup versions found"
    if wv is not None:
        _wv = wv
        await ctx.call(
            DOMAIN, f"m365.{scope}.versions.get_latest",
            lambda: apm.m365.workloads.get_latest_version(_wv),
        )
    else:
        ctx.skip(DOMAIN, f"m365.{scope}.versions.get_latest", _no_wv_reason)

    # ── get_latest: error path (wnv) ─────────────────────────────────────────

    _no_wnv_reason = "No workload without backup versions found"
    if wnv is not None:
        _wnv = wnv
        glv_exc = await ctx.call_expect_error(
            DOMAIN, f"m365.{scope}.versions.get_latest[nv_raises]",
            lambda: apm.m365.workloads.get_latest_version(_wnv), ResourceNotFoundError,
        )
        ctx.check_exc_attr(DOMAIN, f"m365.{scope}.check[get_latest_nv_resource_type]",
            glv_exc, "resource_type", "WorkloadVersion")
        ctx.check_exc_attr(DOMAIN, f"m365.{scope}.check[get_latest_nv_resource_id]",
            glv_exc, "resource_id", _wnv.workload_id)
    else:
        ctx.skip(DOMAIN, f"m365.{scope}.versions.get_latest[nv_raises]", _no_wnv_reason)
        ctx.skip(DOMAIN, f"m365.{scope}.check[get_latest_nv_resource_type]", _no_wnv_reason)
        ctx.skip(DOMAIN, f"m365.{scope}.check[get_latest_nv_resource_id]", _no_wnv_reason)

    # ── get_version[not_found] (requires wv) ─────────────────────────────────

    if wv is not None:
        _wv_nf = wv
        await ctx.call_expect_not_found(DOMAIN, f"m365.{scope}.versions", "get_version",
            lambda: apm.m365.workloads.get_version(_wv_nf, "bogus-version-id"),
            "WorkloadVersion", "bogus-version-id")
    else:
        ctx.skip_remaining(DOMAIN, (
            f"m365.{scope}.versions.get_version[not_found]",
            f"m365.{scope}.versions.check[get_version_nf_resource_type]",
            f"m365.{scope}.versions.check[get_version_nf_resource_id]",
        ), reason=_no_wv_reason)

    # ── lock success path (v_nonempty) and error path (v_empty) ──────────────

    v_nonempty: WorkloadVersion | None = next((v for v in _wv_versions if v.locations), None)
    v_empty: WorkloadVersion | None = next((v for v in _wv_versions if not v.locations), None)

    if v_nonempty is not None:
        _vnp = v_nonempty
        _vnp_wl = wv  # not None: v_nonempty came from _wv_versions which requires wv
        roundtrip = await ctx.call(
            DOMAIN, f"m365.{scope}.lock_unlock_roundtrip",
            lambda: _shared.lock_unlock_roundtrip(apm.m365.workloads, _vnp_wl, _vnp),
            note="lock_version()/unlock_version() should toggle WorkloadVersion.locked.",
        )
        if roundtrip is not None:
            after_first, after_second, first_expected, second_expected = roundtrip
            ctx.check(
                DOMAIN, f"m365.{scope}.check[lock_roundtrip]",
                after_first.locked == first_expected and after_second.locked == second_expected,
            )
        else:
            ctx.skip(DOMAIN, f"m365.{scope}.check[lock_roundtrip]",
                "lock_unlock_roundtrip did not return a value")
    else:
        ctx.skip(DOMAIN, f"m365.{scope}.lock_unlock_roundtrip",
            "No version with non-empty locations found")
        ctx.skip(DOMAIN, f"m365.{scope}.check[lock_roundtrip]",
            "No version with non-empty locations found")

    if v_empty is not None:
        _ve = v_empty
        lock_exc = await ctx.call_expect_error(
            DOMAIN, f"m365.{scope}.versions.lock_version[empty_loc_raises]",
            lambda: apm.m365.workloads.lock_version(_ve), APIError,
            note="v.locations is empty: lock_version() is expected to raise APIError.",
        )
        ctx.check(DOMAIN, f"m365.{scope}.check[lock_empty_loc_exception]", isinstance(lock_exc, APIError))
    else:
        ctx.skip(DOMAIN, f"m365.{scope}.versions.lock_version[empty_loc_raises]",
            "No version with empty locations found")
        ctx.skip(DOMAIN, f"m365.{scope}.check[lock_empty_loc_exception]",
            "No version with empty locations found")

    if w0.is_retired:
        await ctx.call_expect_error(
            DOMAIN, f"m365.{scope}.backup_cancel_roundtrip",
            lambda: apm.m365.workloads.backup_now(w0), InvalidOperationError,
            note="w0.is_retired is True: backup_now() is expected to raise InvalidOperationError.",
        )
    else:
        await ctx.call(
            DOMAIN, f"m365.{scope}.backup_cancel_roundtrip", lambda: _shared.backup_cancel_roundtrip(apm.m365.workloads, w0)
        )

    if scope in _EXPORT_SCOPES:
        await _run_export(ctx, scope, wv if wv is not None else w0, v0)


async def _run_export(ctx: SmokeContext, scope: str, w0: M365Workload, v0: WorkloadVersion | None) -> None:
    apm = ctx.apm
    export_steps = (
        f"m365.{scope}.export.start", f"m365.{scope}.export.list",
        f"m365.{scope}.export.download_url.get", f"m365.{scope}.export.cancel",
    )

    if v0 is None or not v0.portal_version_id:
        _skip_export(ctx, scope, "Workload has no backup version with export data available")
        return

    if scope == "exchange":
        export_collection: ExchangeExportCollection | GroupExportCollection = apm.m365.exchange_export
        start_result, export_wl = await _start_exchange_export_with_fallback(
            ctx, w0, v0, export_steps[0],
        )
        if export_wl is None:
            for step in export_steps:
                ctx.na(DOMAIN, step, "No exchange workload has mailbox folders available to export")
            return
    else:
        export_collection = apm.m365.group_export
        export_wl = w0
        start_result = await ctx.call(
            DOMAIN, export_steps[0], lambda: apm.m365.group_export.start(w0, v0),
        )

    ctx.data.setdefault("m365_exports", {})[scope] = start_result

    if start_result is None:
        reason = f"{export_steps[0]} did not return a result"
        for step in export_steps[1:]:
            ctx.skip(DOMAIN, step, reason)
        return

    _export_wl = export_wl
    await ctx.call(DOMAIN, export_steps[1], lambda: export_collection.list(_export_wl, limit=50))

    await ctx.call(
        DOMAIN, export_steps[2], lambda: _get_download_url(export_collection, start_result),
        expect_error=ResourceNotReadyError,
    )

    await ctx.call(DOMAIN, export_steps[3], lambda: _cancel_export(export_collection, start_result))


def _skip_export(ctx: SmokeContext, scope: str, reason: str) -> None:
    ctx.skip_remaining(DOMAIN, (
        f"m365.{scope}.export.start", f"m365.{scope}.export.list",
        f"m365.{scope}.export.download_url.get", f"m365.{scope}.export.cancel",
    ), reason=reason)


async def _start_exchange_export_with_fallback(
    ctx: SmokeContext, w0: M365Workload, v0: WorkloadVersion, step: str,
) -> tuple[M365ExportStartResult | None, M365Workload | None]:
    """Start a mailbox export, trying other workloads when one has no exportable folders.

    Some mailboxes (e.g. unlicensed accounts) have backup versions without mailbox folders
    and cannot be exported. Tries w0/v0 first, then the latest version of up to
    _EXPORT_WORKLOAD_FALLBACK_LIMIT other exchange workloads. Returns
    (start_result, workload_used); (None, None) means every candidate lacks mailbox folders
    and the export steps should be marked N/A. Any other failure is recorded on the step
    and returns (None, workload), so the remaining export steps get skipped.
    """
    apm = ctx.apm

    async def _record(outcome: M365ExportStartResult | APMError) -> M365ExportStartResult | None:
        async def _replay() -> M365ExportStartResult:
            if isinstance(outcome, APMError):
                raise outcome
            return outcome
        return await ctx.call(DOMAIN, step, _replay)

    others = [
        w for w in ctx.data.get("m365_workloads", {}).get("exchange", [])
        if w.workload_id != w0.workload_id
    ]
    candidates: list[tuple[M365Workload, WorkloadVersion | None]] = [(w0, v0)]
    candidates += [(w, None) for w in others[:_EXPORT_WORKLOAD_FALLBACK_LIMIT]]

    token = _current_step.set(step)
    try:
        for wx, vx in candidates:
            version = vx
            if version is None:
                try:
                    version = await apm.m365.workloads.get_latest_version(wx)
                except APMError:
                    continue
            if not version.portal_version_id:
                continue
            try:
                result = await apm.m365.exchange_export.start(wx, version)
            except ResourceNotFoundError as exc:
                if exc.resource_type == "MailboxFolder":
                    continue
                return await _record(exc), wx
            except APMError as exc:
                return await _record(exc), wx
            return await _record(result), wx
    finally:
        _current_step.reset(token)
    return None, None


async def _get_download_url(
    export_collection: ExchangeExportCollection | GroupExportCollection, result: M365ExportStartResult
) -> str:
    if result.ready_to_download:
        return await export_collection.get_download_url_by_ready_result(result)
    for _ in range(_EXPORT_POLL_ATTEMPTS):
        activity = await export_collection.get_activity_by_result(result)
        if activity is not None and activity.status != M365ExportStatus.PREPARING:
            return await export_collection.get_download_url_by_activity(activity)
        await asyncio.sleep(_EXPORT_POLL_INTERVAL_SECONDS)
    raise ResourceNotReadyError("Export did not reach a downloadable state within the polling window.")


async def _cancel_export(
    export_collection: ExchangeExportCollection | GroupExportCollection, result: M365ExportStartResult
) -> bool:
    """Cancel the export started by start(); returns False if its activity is not yet visible via list()."""
    activity = await export_collection.get_activity_by_result(result)
    if activity is None:
        return False
    await export_collection.cancel(activity)
    return True



_GUARD_STEPS: tuple[str, ...] = (
    "m365.workloads.change_plan[active+ret_raises]",
    "m365.workloads.check[change_plan_active_ret_resource_type]",
    "m365.workloads.check[change_plan_active_ret_resource_id]",
    "m365.workloads.change_plan[retired+m365_prot_raises]",
    "m365.workloads.check[change_plan_retired_m365_prot_resource_type]",
    "m365.workloads.check[change_plan_retired_m365_prot_resource_id]",
    "m365.workloads.change_plan[category_mismatch_raises]",
    "m365.workloads.check[change_plan_category_mismatch_resource_type]",
    "m365.workloads.check[change_plan_category_mismatch_resource_id]",
    "m365.workloads.cancel_backup[retired_raises]",
    "m365.workloads.check[cancel_backup_retired_resource_type]",
    "m365.workloads.check[cancel_backup_retired_resource_id]",
    "m365.workloads.retire[already_retired_raises]",
    "m365.workloads.check[retire_already_retired_resource_type]",
    "m365.workloads.check[retire_already_retired_resource_id]",
)
_NON_SCOPE_STEPS: tuple[str, ...] = (
    "m365.saas.get_m365_tenant[direct]",
    "m365.saas.get_m365_tenant[not_found]",
    "m365.saas.check[get_m365_tenant_nf_resource_type]",
    "m365.saas.check[get_m365_tenant_nf_resource_id]",
    "m365.change_plan[switch]",
    "m365.change_plan[restore]",
    "m365.change_plan[retired_noop]",
    *_GUARD_STEPS,
)


async def _run_change_plan_roundtrips(ctx: SmokeContext) -> None:
    apm = ctx.apm
    protection_plans: list[ProtectionPlan] = ctx.data.get("protection_plans", [])
    retirement_plans: list[RetirementPlan] = ctx.data.get("retirement_plans", [])
    m365_plans = [p for p in protection_plans if p.category == WorkloadCategory.M365]
    workloads = [w for scope_workloads in ctx.data["m365_workloads"].values() for w in scope_workloads]
    retired_workloads = [w for scope_workloads in ctx.data["m365_retired_workloads"].values() for w in scope_workloads]

    if not workloads:
        reason = "No M365 Workloads found"
        ctx.skip(DOMAIN, "m365.change_plan[switch]", reason)
        ctx.skip(DOMAIN, "m365.change_plan[restore]", reason)
    elif not m365_plans:
        reason = "No M365-category Protection Plans found"
        ctx.skip(DOMAIN, "m365.change_plan[switch]", reason)
        ctx.skip(DOMAIN, "m365.change_plan[restore]", reason)
    else:
        candidate: tuple[M365Workload, ProtectionPlan, ProtectionPlan] | None = None
        for workload in workloads:
            original_plan = next((p for p in m365_plans if p.name == workload.plan.name), None)
            if original_plan is None:
                continue
            other_plan = next(
                (p for p in m365_plans if p.plan_id != original_plan.plan_id and not p.is_immutable),
                None,
            )
            if other_plan is None:
                continue
            candidate = (workload, original_plan, other_plan)
            break

        if candidate is None:
            reason = (
                "No M365 Workload found whose current Protection Plan and a different, "
                "non-immutable M365-category Protection Plan could both be resolved"
            )
            ctx.skip(DOMAIN, "m365.change_plan[switch]", reason)
            ctx.skip(DOMAIN, "m365.change_plan[restore]", reason)
        else:
            scratch, original_plan, other_plan = candidate
            switch_plan, restore_plan = other_plan, original_plan
            await ctx.call(
                DOMAIN, "m365.change_plan[switch]",
                lambda: apm.m365.workloads.change_plan(scratch, switch_plan),
            )
            await ctx.call(
                DOMAIN, "m365.change_plan[restore]",
                lambda: apm.m365.workloads.change_plan(scratch, restore_plan),
            )

    retirement_plan_by_name = {p.name: p for p in retirement_plans}
    reapply_candidate = next(
        (
            (w, retirement_plan_by_name[w.plan.name])
            for w in retired_workloads
            if w.plan.name in retirement_plan_by_name
        ),
        None,
    )
    if reapply_candidate is None:
        ctx.skip(
            DOMAIN, "m365.change_plan[retired_noop]",
            "No retired M365 Workload with a plan.name matching an existing Retirement Plan found",
        )
    else:
        retired_workload, matching_plan = reapply_candidate
        await ctx.call(
            DOMAIN, "m365.change_plan[retired_noop]",
            lambda: apm.m365.workloads.change_plan(retired_workload, matching_plan),
        )
