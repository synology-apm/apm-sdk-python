"""GWS domain phase: apm.saas, apm.gws.workloads per GWSWorkloadType.

Populates ctx.data["gws_domain"], ctx.data["gws_workloads"] (dict keyed by scope, for
change_plan round trips), and ctx.data["gws_retired_workloads"] (dict keyed by scope).

Also runs the ``apm.gws.workloads.change_plan()`` round trips across all scopes' workloads
(same switch/restore/retired-no-op shape as the m365/machine phases' round trips). Reads
ctx.data["protection_plans"] and ctx.data["retirement_plans"] (from the plan phase, which
runs first).

Unlike M365, GWS has no export feature (no exchange/group-style export API) and no
per-scope subset flag — GWSWorkloadType has only 5 fixed members with no export branching
to iterate on independently, so every scope always runs.
"""
from __future__ import annotations

from synology_apm.sdk import (
    APIError,
    GWSDomainInfo,
    GWSSharedDriveInfo,
    GWSUserInfo,
    GWSWorkload,
    GWSWorkloadType,
    InvalidOperationError,
    ProtectionPlan,
    ResourceNotFoundError,
    RetirementPlan,
    WorkloadCategory,
    WorkloadStatus,
    WorkloadVersion,
)

from .._context import SmokeContext
from . import _shared
from ._shared import SENTINEL_NAME as _SENTINEL_NAME

DOMAIN = "gws"
_SCOPE_INFO_TYPE: dict[GWSWorkloadType, type] = {
    GWSWorkloadType.MAIL: GWSUserInfo,
    GWSWorkloadType.DRIVE: GWSUserInfo,
    GWSWorkloadType.CONTACT: GWSUserInfo,
    GWSWorkloadType.CALENDAR: GWSUserInfo,
    GWSWorkloadType.SHARED_DRIVE: GWSSharedDriveInfo,
}
_SCOPE_INFO_FIELD: dict[GWSWorkloadType, str] = {
    GWSWorkloadType.MAIL: "email", GWSWorkloadType.DRIVE: "email",
    GWSWorkloadType.CONTACT: "email", GWSWorkloadType.CALENDAR: "email",
    GWSWorkloadType.SHARED_DRIVE: "drive_name",
}
_VERSIONS_FALLBACK_LIMIT = 5  # max extra workloads to probe when finding wv/wnv


async def run(ctx: SmokeContext) -> None:
    apm = ctx.apm

    saas_result = await ctx.call(DOMAIN, "gws.saas.list", lambda: apm.saas.list(limit=500))
    apps, _total = saas_result if saas_result is not None else ([], 0)
    gws_domain = next((a for a in apps if isinstance(a, GWSDomainInfo)), None)

    ctx.data["gws_domain"] = gws_domain

    if gws_domain is None:
        all_remaining = [step for wt in GWSWorkloadType for step in _scope_steps(wt.value)]
        all_remaining += _NON_SCOPE_STEPS
        ctx.skip_remaining(DOMAIN, all_remaining, reason="No GWS domain configured")
        return

    await ctx.call(
        DOMAIN, "gws.saas.get_gws_domain[direct]",
        lambda: apm.saas.get_gws_domain(gws_domain.domain),
    )

    await ctx.call_expect_not_found(DOMAIN, "gws.saas", "get_gws_domain",
        lambda: apm.saas.get_gws_domain(_SENTINEL_NAME), "GWSDomainInfo", _SENTINEL_NAME)

    ctx.data["gws_workloads"] = {}
    ctx.data["gws_retired_workloads"] = {}
    for workload_type in GWSWorkloadType:
        await _run_scope(ctx, gws_domain.domain, workload_type)

    await _run_change_plan_roundtrips(ctx)

    # ── Client-side guard tests ───────────────────────────────────────────────

    gws_retirement_plan = next((p for p in ctx.data.get("retirement_plans", [])), None)
    gws_prot_plan = next(
        (p for p in ctx.data.get("protection_plans", []) if p.category == WorkloadCategory.GWS), None)
    gws_machine_prot_plan = next(
        (p for p in ctx.data.get("protection_plans", []) if p.category == WorkloadCategory.MACHINE), None)
    gws_active_wl = next(
        (w for scope_wls in ctx.data.get("gws_workloads", {}).values() for w in scope_wls), None)
    gws_retired_wl = next(
        (w for scope_wls in ctx.data.get("gws_retired_workloads", {}).values() for w in scope_wls), None)

    _act = gws_active_wl
    _ret = gws_retired_wl
    _rp = gws_retirement_plan
    _pp = gws_prot_plan
    _mp = gws_machine_prot_plan

    await ctx.guard_error(
        DOMAIN, "gws.workloads.change_plan[active+ret_raises]",
        "gws.workloads.check[change_plan_active_ret",
        _act is not None and _rp is not None,
        lambda: apm.gws.workloads.change_plan(_act, _rp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _act.workload_id if _act is not None else None,
        skip_reason="No active GWS workload or no retirement plan",
    )
    await ctx.guard_error(
        DOMAIN, "gws.workloads.change_plan[retired+gws_prot_raises]",
        "gws.workloads.check[change_plan_retired_gws_prot",
        _ret is not None and _pp is not None,
        lambda: apm.gws.workloads.change_plan(_ret, _pp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired GWS workload or no GWS protection plan",
    )
    await ctx.guard_error(
        DOMAIN, "gws.workloads.change_plan[category_mismatch_raises]",
        "gws.workloads.check[change_plan_category_mismatch",
        _act is not None and _mp is not None,
        lambda: apm.gws.workloads.change_plan(_act, _mp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _act.workload_id if _act is not None else None,
        skip_reason="No active GWS workload or no machine protection plan",
    )
    await ctx.guard_error(
        DOMAIN, "gws.workloads.cancel_backup[retired_raises]",
        "gws.workloads.check[cancel_backup_retired",
        _ret is not None,
        lambda: apm.gws.workloads.cancel_backup(_ret),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired GWS workloads found",
    )
    await ctx.guard_error(
        DOMAIN, "gws.workloads.retire[already_retired_raises]",
        "gws.workloads.check[retire_already_retired",
        _ret is not None and _rp is not None,
        lambda: apm.gws.workloads.retire(_ret, _rp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired GWS workload or no retirement plan",
    )


def _scope_steps(scope: str) -> tuple[str, ...]:
    return (
        f"gws.{scope}.list[all]",
        f"gws.{scope}.list[retired]",
        f"gws.{scope}.check[workload_type]",
        f"gws.{scope}.get[direct]",
        f"gws.{scope}.get_by_name[search]",
        f"gws.{scope}.check[info_type]",
        f"gws.{scope}.check[get_by_name_label]",
        f"gws.{scope}.get[not_found]",
        f"gws.{scope}.check[get_nf_resource_type]",
        f"gws.{scope}.check[get_nf_resource_id]",
        f"gws.{scope}.get_by_name[not_found]",
        f"gws.{scope}.check[get_by_name_nf_resource_type]",
        f"gws.{scope}.check[get_by_name_nf_resource_id]",
        f"gws.{scope}.versions.list[search]",
        f"gws.{scope}.versions.get_latest",
        f"gws.{scope}.versions.get_latest[nv_raises]",
        f"gws.{scope}.check[get_latest_nv_resource_type]",
        f"gws.{scope}.check[get_latest_nv_resource_id]",
        f"gws.{scope}.versions.get_version[not_found]",
        f"gws.{scope}.versions.check[get_version_nf_resource_type]",
        f"gws.{scope}.versions.check[get_version_nf_resource_id]",
        f"gws.{scope}.lock_unlock_roundtrip",
        f"gws.{scope}.check[lock_roundtrip]",
        f"gws.{scope}.versions.lock_version[empty_loc_raises]",
        f"gws.{scope}.check[lock_empty_loc_exception]",
        f"gws.{scope}.backup_cancel_roundtrip",
    )


async def _run_scope(ctx: SmokeContext, domain: str, workload_type: GWSWorkloadType) -> None:
    apm = ctx.apm
    scope = workload_type.value

    all_result = await ctx.call(
        DOMAIN, f"gws.{scope}.list[all]",
        lambda: apm.gws.workloads.list(domain, workload_type, limit=500),
    )
    workloads, _total = all_result if all_result is not None else ([], 0)
    ctx.data["gws_workloads"][scope] = workloads

    retired_result = await ctx.call(
        DOMAIN, f"gws.{scope}.list[retired]",
        lambda: apm.gws.workloads.list(domain, workload_type, is_retired=True, limit=500),
    )
    retired_workloads, _retired_total = retired_result if retired_result is not None else ([], 0)
    ctx.data["gws_retired_workloads"][scope] = retired_workloads

    ctx.check(
        DOMAIN, f"gws.{scope}.check[workload_type]",
        all(w.workload_type == workload_type for w in workloads),
        note="list() with workload_type must only contain that type.",
    )

    status_result = await ctx.call(
        DOMAIN, f"gws.{scope}.list[status]",
        lambda: apm.gws.workloads.list(domain, workload_type, status=[WorkloadStatus.SUCCESS], limit=500),
    )
    if status_result is not None:
        status_workloads, _status_total = status_result
        ctx.check(
            DOMAIN, f"gws.{scope}.check[status_filter]",
            all(w.status == WorkloadStatus.SUCCESS for w in status_workloads),
            note="Every workload returned by status=[SUCCESS] must report status SUCCESS.",
        )

    if not workloads:
        reason = f"No {workload_type.name} GWS Workloads found"
        ctx.skip_remaining(DOMAIN, _scope_steps(scope), reason=reason)
        return

    w0 = workloads[0]

    await ctx.call(
        DOMAIN, f"gws.{scope}.get[direct]",
        lambda: apm.gws.workloads.get(w0.workload_id, w0.namespace, domain, workload_type),
    )

    by_name = await ctx.call(
        DOMAIN, f"gws.{scope}.get_by_name[search]",
        lambda: apm.gws.workloads.get_by_name(w0.name, domain, workload_type),
    )

    ctx.check(
        DOMAIN, f"gws.{scope}.check[info_type]",
        isinstance(w0.info, _SCOPE_INFO_TYPE[workload_type])
        and bool(getattr(w0.info, _SCOPE_INFO_FIELD[workload_type], "")),
    )

    if by_name is not None:
        ctx.check(
            DOMAIN, f"gws.{scope}.check[get_by_name_label]", by_name.workload_id == w0.workload_id,
        )
    else:
        ctx.skip(
            DOMAIN, f"gws.{scope}.check[get_by_name_label]",
            f"gws.{scope}.get_by_name[search] did not return a result",
        )

    # ── not-found errors ──────────────────────────────────────────────────────

    _w0_ns = w0.namespace
    await ctx.call_expect_not_found(DOMAIN, f"gws.{scope}", "get",
        lambda: apm.gws.workloads.get(_SENTINEL_NAME, _w0_ns, domain, workload_type),
        "GWSWorkload", _SENTINEL_NAME)
    await ctx.call_expect_not_found(DOMAIN, f"gws.{scope}", "get_by_name",
        lambda: apm.gws.workloads.get_by_name(_SENTINEL_NAME, domain, workload_type),
        "GWSWorkload", _SENTINEL_NAME)

    versions_result = await ctx.call(
        DOMAIN, f"gws.{scope}.versions.list[search]", lambda: apm.gws.workloads.list_versions(w0, limit=20)
    )
    versions, _versions_total = versions_result if versions_result is not None else ([], 0)

    # Identify wv (workload with versions, for the happy path) and wnv (workload without
    # versions, for the no-version error path).  w0 always starts in one bucket; other
    # workloads of the same scope are probed silently until both buckets are filled.
    wv: GWSWorkload | None = w0 if versions else None
    wnv: GWSWorkload | None = w0 if not versions else None
    _wv_versions: list[WorkloadVersion] = list(versions)

    for wx in [w for w in workloads if w.workload_id != w0.workload_id][:_VERSIONS_FALLBACK_LIMIT]:
        if wv is not None and wnv is not None:
            break
        try:
            wx_result = await apm.gws.workloads.list_versions(wx, limit=20)
            wx_versions, _ = wx_result if wx_result is not None else ([], 0)
        except Exception:
            wx_versions = []
        if wx_versions and wv is None:
            wv = wx
            _wv_versions = list(wx_versions)
        elif not wx_versions and wnv is None:
            wnv = wx

    # ── get_latest: happy path (wv) ──────────────────────────────────────────

    _no_wv_reason = "No workload with backup versions found"
    if wv is not None:
        _wv = wv
        await ctx.call(
            DOMAIN, f"gws.{scope}.versions.get_latest",
            lambda: apm.gws.workloads.get_latest_version(_wv),
        )
    else:
        ctx.skip(DOMAIN, f"gws.{scope}.versions.get_latest", _no_wv_reason)

    # ── get_latest: error path (wnv) ─────────────────────────────────────────

    _no_wnv_reason = "No workload without backup versions found"
    if wnv is not None:
        _wnv = wnv
        glv_exc = await ctx.call_expect_error(
            DOMAIN, f"gws.{scope}.versions.get_latest[nv_raises]",
            lambda: apm.gws.workloads.get_latest_version(_wnv), ResourceNotFoundError,
        )
        ctx.check_exc_attr(DOMAIN, f"gws.{scope}.check[get_latest_nv_resource_type]",
            glv_exc, "resource_type", "WorkloadVersion")
        ctx.check_exc_attr(DOMAIN, f"gws.{scope}.check[get_latest_nv_resource_id]",
            glv_exc, "resource_id", _wnv.workload_id)
    else:
        ctx.skip(DOMAIN, f"gws.{scope}.versions.get_latest[nv_raises]", _no_wnv_reason)
        ctx.skip(DOMAIN, f"gws.{scope}.check[get_latest_nv_resource_type]", _no_wnv_reason)
        ctx.skip(DOMAIN, f"gws.{scope}.check[get_latest_nv_resource_id]", _no_wnv_reason)

    # ── get_version[not_found] (requires wv) ─────────────────────────────────

    if wv is not None:
        _wv_nf = wv
        await ctx.call_expect_not_found(DOMAIN, f"gws.{scope}.versions", "get_version",
            lambda: apm.gws.workloads.get_version(_wv_nf, "bogus-version-id"),
            "WorkloadVersion", "bogus-version-id")
    else:
        ctx.skip_remaining(DOMAIN, (
            f"gws.{scope}.versions.get_version[not_found]",
            f"gws.{scope}.versions.check[get_version_nf_resource_type]",
            f"gws.{scope}.versions.check[get_version_nf_resource_id]",
        ), reason=_no_wv_reason)

    # ── lock success path (v_nonempty) and error path (v_empty) ──────────────

    v_nonempty: WorkloadVersion | None = next((v for v in _wv_versions if v.locations), None)
    v_empty: WorkloadVersion | None = next((v for v in _wv_versions if not v.locations), None)

    if v_nonempty is not None:
        _vnp = v_nonempty
        _vnp_wl = wv  # not None: v_nonempty came from _wv_versions which requires wv
        roundtrip = await ctx.call(
            DOMAIN, f"gws.{scope}.lock_unlock_roundtrip",
            lambda: _shared.lock_unlock_roundtrip(apm.gws.workloads, _vnp_wl, _vnp),
            note="lock_version()/unlock_version() should toggle WorkloadVersion.locked.",
        )
        if roundtrip is not None:
            after_first, after_second, first_expected, second_expected = roundtrip
            ctx.check(
                DOMAIN, f"gws.{scope}.check[lock_roundtrip]",
                after_first.locked == first_expected and after_second.locked == second_expected,
            )
        else:
            ctx.skip(DOMAIN, f"gws.{scope}.check[lock_roundtrip]",
                "lock_unlock_roundtrip did not return a value")
    else:
        ctx.skip(DOMAIN, f"gws.{scope}.lock_unlock_roundtrip",
            "No version with non-empty locations found")
        ctx.skip(DOMAIN, f"gws.{scope}.check[lock_roundtrip]",
            "No version with non-empty locations found")

    if v_empty is not None:
        _ve = v_empty
        lock_exc = await ctx.call_expect_error(
            DOMAIN, f"gws.{scope}.versions.lock_version[empty_loc_raises]",
            lambda: apm.gws.workloads.lock_version(_ve), APIError,
            note="v.locations is empty: lock_version() is expected to raise APIError.",
        )
        ctx.check(DOMAIN, f"gws.{scope}.check[lock_empty_loc_exception]", isinstance(lock_exc, APIError))
    else:
        ctx.skip(DOMAIN, f"gws.{scope}.versions.lock_version[empty_loc_raises]",
            "No version with empty locations found")
        ctx.skip(DOMAIN, f"gws.{scope}.check[lock_empty_loc_exception]",
            "No version with empty locations found")

    if w0.is_retired:
        await ctx.call_expect_error(
            DOMAIN, f"gws.{scope}.backup_cancel_roundtrip",
            lambda: apm.gws.workloads.backup_now(w0), InvalidOperationError,
            note="w0.is_retired is True: backup_now() is expected to raise InvalidOperationError.",
        )
    else:
        await ctx.call(
            DOMAIN, f"gws.{scope}.backup_cancel_roundtrip", lambda: _shared.backup_cancel_roundtrip(apm.gws.workloads, w0)
        )


_GUARD_STEPS: tuple[str, ...] = (
    "gws.workloads.change_plan[active+ret_raises]",
    "gws.workloads.check[change_plan_active_ret_resource_type]",
    "gws.workloads.check[change_plan_active_ret_resource_id]",
    "gws.workloads.change_plan[retired+gws_prot_raises]",
    "gws.workloads.check[change_plan_retired_gws_prot_resource_type]",
    "gws.workloads.check[change_plan_retired_gws_prot_resource_id]",
    "gws.workloads.change_plan[category_mismatch_raises]",
    "gws.workloads.check[change_plan_category_mismatch_resource_type]",
    "gws.workloads.check[change_plan_category_mismatch_resource_id]",
    "gws.workloads.cancel_backup[retired_raises]",
    "gws.workloads.check[cancel_backup_retired_resource_type]",
    "gws.workloads.check[cancel_backup_retired_resource_id]",
    "gws.workloads.retire[already_retired_raises]",
    "gws.workloads.check[retire_already_retired_resource_type]",
    "gws.workloads.check[retire_already_retired_resource_id]",
)
_NON_SCOPE_STEPS: tuple[str, ...] = (
    "gws.saas.get_gws_domain[direct]",
    "gws.saas.get_gws_domain[not_found]",
    "gws.saas.check[get_gws_domain_nf_resource_type]",
    "gws.saas.check[get_gws_domain_nf_resource_id]",
    "gws.change_plan[switch]",
    "gws.change_plan[restore]",
    "gws.change_plan[retired_noop]",
    *_GUARD_STEPS,
)


async def _run_change_plan_roundtrips(ctx: SmokeContext) -> None:
    apm = ctx.apm
    protection_plans: list[ProtectionPlan] = ctx.data.get("protection_plans", [])
    retirement_plans: list[RetirementPlan] = ctx.data.get("retirement_plans", [])
    gws_plans = [p for p in protection_plans if p.category == WorkloadCategory.GWS]
    workloads = [w for scope_workloads in ctx.data["gws_workloads"].values() for w in scope_workloads]
    retired_workloads = [w for scope_workloads in ctx.data["gws_retired_workloads"].values() for w in scope_workloads]

    if not workloads:
        reason = "No GWS Workloads found"
        ctx.skip(DOMAIN, "gws.change_plan[switch]", reason)
        ctx.skip(DOMAIN, "gws.change_plan[restore]", reason)
    elif not gws_plans:
        reason = "No GWS-category Protection Plans found"
        ctx.skip(DOMAIN, "gws.change_plan[switch]", reason)
        ctx.skip(DOMAIN, "gws.change_plan[restore]", reason)
    else:
        candidate: tuple[GWSWorkload, ProtectionPlan, ProtectionPlan] | None = None
        for workload in workloads:
            original_plan = next((p for p in gws_plans if p.name == workload.plan.name), None)
            if original_plan is None:
                continue
            other_plan = next(
                (p for p in gws_plans if p.plan_id != original_plan.plan_id and not p.is_immutable),
                None,
            )
            if other_plan is None:
                continue
            candidate = (workload, original_plan, other_plan)
            break

        if candidate is None:
            reason = (
                "No GWS Workload found whose current Protection Plan and a different, "
                "non-immutable GWS-category Protection Plan could both be resolved"
            )
            ctx.skip(DOMAIN, "gws.change_plan[switch]", reason)
            ctx.skip(DOMAIN, "gws.change_plan[restore]", reason)
        else:
            scratch, original_plan, other_plan = candidate
            switch_plan, restore_plan = other_plan, original_plan
            await ctx.call(
                DOMAIN, "gws.change_plan[switch]",
                lambda: apm.gws.workloads.change_plan(scratch, switch_plan),
            )
            await ctx.call(
                DOMAIN, "gws.change_plan[restore]",
                lambda: apm.gws.workloads.change_plan(scratch, restore_plan),
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
            DOMAIN, "gws.change_plan[retired_noop]",
            "No retired GWS Workload with a plan.name matching an existing Retirement Plan found",
        )
    else:
        retired_workload, matching_plan = reapply_candidate
        await ctx.call(
            DOMAIN, "gws.change_plan[retired_noop]",
            lambda: apm.gws.workloads.change_plan(retired_workload, matching_plan),
        )
