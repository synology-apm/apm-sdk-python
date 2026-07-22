"""Machine domain phase: per-type blocks (PC/PS/VM/FS) + fake FS CRUD lifecycle tests.

Populates ctx.data["machine_workloads"], ctx.data["retired_machine_workloads"], and
ctx.data["machine_versions"] for use by later phases.
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import time
from typing import Any

from synology_apm.sdk import (
    APIError,
    APMClient,
    DuplicateWorkloadError,
    FileServerAddRequest,
    FileServerPathSelector,
    FileServerType,
    FileServerUpdateRequest,
    InvalidOperationError,
    MachinePlanCreateRequest,
    MachineWorkload,
    MachineWorkloadType,
    PlanInUseError,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    ResourceNotFoundError,
    RetentionType,
    RetirementPlan,
    RetirementPlanCreateRequest,
    ScheduleFrequency,
    VerifyStatus,
    WorkloadCategory,
    WorkloadStatus,
    WorkloadVersion,
)

from .._context import SmokeContext
from .._trace import current_step as _current_step
from . import _shared
from ._shared import SENTINEL_NAME as _SENTINEL_NAME
from ._shared import ZERO_UUID as _ZERO_UUID

DOMAIN = "machine"
_FS_RETIRE_PROP_WAIT = 3    # let APM propagate retired state before re-fetch / list check
_FS_INIT_POLL_RETRIES = 10  # up to ~30 s (10 × 3 s) for workload init before retire/update
_FS_INIT_POLL_WAIT = 3
_FS_CRUD_FAKE_IP_PREFIX = "192.0.2"  # RFC 5737 non-routable /24


async def run(ctx: SmokeContext) -> None:
    apm = ctx.apm

    all_result = await ctx.call(
        DOMAIN, "machine.workloads.list[all]", lambda: apm.machine.workloads.list(limit=500)
    )
    workloads, total = all_result if all_result is not None else ([], 0)
    assert total is not None  # MachineWorkloadCollection.list() always reports a real total
    ctx.data["machine_workloads"] = workloads

    retired_result = await ctx.call(
        DOMAIN, "machine.workloads.list[retired]", lambda: apm.machine.workloads.list(is_retired=True, limit=500)
    )
    retired_workloads, _retired_total = retired_result if retired_result is not None else ([], 0)
    ctx.data["retired_machine_workloads"] = retired_workloads

    ctx.check(
        DOMAIN, "machine.workloads.check[list_total]", total >= len(workloads),
        note="Reported total must be >= number of items returned.",
    )
    ctx.check(
        DOMAIN, "machine.workloads.check[retired_disjoint]",
        {w.workload_id for w in workloads}.isdisjoint(w.workload_id for w in retired_workloads),
        note="A workload ID must not appear in both the protected and retired lists.",
    )

    status_result = await ctx.call(
        DOMAIN, "machine.workloads.list[status]",
        lambda: apm.machine.workloads.list(status=[WorkloadStatus.SUCCESS], limit=500),
    )
    if status_result is not None:
        status_workloads, _status_total = status_result
        ctx.check(
            DOMAIN, "machine.workloads.check[status_filter]",
            all(w.status == WorkloadStatus.SUCCESS for w in status_workloads),
            note="Every workload returned by status=[SUCCESS] must report status SUCCESS.",
        )

    verify_status_result = await ctx.call(
        DOMAIN, "machine.workloads.list[verify_status]",
        lambda: apm.machine.workloads.list(verify_status=[VerifyStatus.NOT_ENABLED], limit=500),
    )
    if verify_status_result is not None:
        verify_status_workloads, _verify_status_total = verify_status_result
        ctx.check(
            DOMAIN, "machine.workloads.check[verify_status_filter]",
            all(
                w.verify_status in (VerifyStatus.NOT_ENABLED, None)
                for w in verify_status_workloads
            ),
            note=(
                "verify_status=[NOT_ENABLED] can also match PC/FS workloads, which always "
                "report verify_status=None (not tracked for them)."
            ),
        )

    await _run_type_block(ctx, workloads, MachineWorkloadType.PC, "pc")
    await _run_type_block(ctx, workloads, MachineWorkloadType.PS, "ps")
    await _run_type_block(ctx, workloads, MachineWorkloadType.VM, "vm")
    await _run_type_block(ctx, workloads, MachineWorkloadType.FS, "fs")
    ctx.data.setdefault("machine_versions", [])

    await _run_fs_crud_active(ctx)
    await _run_fs_crud_retired(ctx)

    # ── Client-side guard tests ───────────────────────────────────────────────

    machine_retirement_plan = next((p for p in ctx.data.get("retirement_plans", [])), None)
    machine_m365_plan = next(
        (p for p in ctx.data.get("protection_plans", []) if p.category == WorkloadCategory.M365), None)
    machine_prot_plan = next(
        (p for p in ctx.data.get("protection_plans", []) if p.category == WorkloadCategory.MACHINE), None)
    active_wl = workloads[0] if workloads else None
    retired_wl = retired_workloads[0] if retired_workloads else None
    non_fs_wl = next((w for w in workloads if w.workload_type != MachineWorkloadType.FS), None)

    _act = active_wl
    _rp = machine_retirement_plan
    await ctx.guard_error(
        DOMAIN, "machine.workloads.change_plan[active+ret_raises]",
        "machine.workloads.check[change_plan_active_ret",
        _act is not None and _rp is not None,
        lambda: apm.machine.workloads.change_plan(_act, _rp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _act.workload_id if _act is not None else None,
        skip_reason="No active workload or no retirement plan",
    )

    _ret = retired_wl
    _pp = machine_prot_plan
    await ctx.guard_error(
        DOMAIN, "machine.workloads.change_plan[retired+prot_raises]",
        "machine.workloads.check[change_plan_retired_prot",
        _ret is not None and _pp is not None,
        lambda: apm.machine.workloads.change_plan(_ret, _pp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired workload or no machine protection plan",
    )

    _m365p = machine_m365_plan
    await ctx.guard_error(
        DOMAIN, "machine.workloads.change_plan[category_mismatch_raises]",
        "machine.workloads.check[change_plan_category_mismatch",
        _act is not None and _m365p is not None,
        lambda: apm.machine.workloads.change_plan(_act, _m365p),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _act.workload_id if _act is not None else None,
        skip_reason="No active workload or no M365 protection plan",
    )

    await ctx.guard_error(
        DOMAIN, "machine.workloads.cancel_backup[retired_raises]",
        "machine.workloads.check[cancel_backup_retired",
        _ret is not None,
        lambda: apm.machine.workloads.cancel_backup(_ret),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired workloads found",
    )

    await ctx.guard_error(
        DOMAIN, "machine.workloads.retire[already_retired_raises]",
        "machine.workloads.check[retire_already_retired",
        _ret is not None and _rp is not None,
        lambda: apm.machine.workloads.retire(_ret, _rp),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _ret.workload_id if _ret is not None else None,
        skip_reason="No retired workload or no retirement plan",
    )

    _nfs = non_fs_wl
    _nfs_upd = FileServerUpdateRequest(
        host_ip="192.0.2.1",
        login_user="administrator",
        login_password=None,
        selectors=(FileServerPathSelector(path=""),),
    )
    await ctx.guard_error(
        DOMAIN, "machine.workloads.update_file_server[non_fs_raises]",
        "machine.workloads.check[update_file_server_non_fs",
        _nfs is not None,
        lambda: apm.machine.workloads.update_file_server(_nfs, _nfs_upd),  # type: ignore[arg-type]
        InvalidOperationError, "Workload", _nfs.workload_id if _nfs is not None else None,
        skip_reason="No non-FS workloads found",
    )


async def _run_type_block(
    ctx: SmokeContext,
    workloads: list[MachineWorkload],
    wl_type: MachineWorkloadType,
    prefix: str,
) -> None:
    """Run common smoke steps for one workload type using the first matching workload."""
    apm = ctx.apm

    _COMMON_STEPS = (
        f"machine.{prefix}.get[direct]",
        f"machine.{prefix}.get_by_name[search]",
        f"machine.{prefix}.check[get_by_name_consistency]",
        f"machine.{prefix}.get[not_found]",
        f"machine.{prefix}.check[get_nf_resource_type]",
        f"machine.{prefix}.check[get_nf_resource_id]",
        f"machine.{prefix}.get_by_name[not_found]",
        f"machine.{prefix}.check[get_by_name_nf_resource_type]",
        f"machine.{prefix}.check[get_by_name_nf_resource_id]",
        f"machine.{prefix}.change_plan[switch]",
        f"machine.{prefix}.change_plan[restore]",
        f"machine.{prefix}.versions.list[search]",
        f"machine.{prefix}.versions.get_latest",
        f"machine.{prefix}.versions.get_latest[nv_raises]",
        f"machine.{prefix}.check[get_latest_nv_resource_type]",
        f"machine.{prefix}.check[get_latest_nv_resource_id]",
        f"machine.{prefix}.versions.get_version[not_found]",
        f"machine.{prefix}.versions.check[get_version_nf_resource_type]",
        f"machine.{prefix}.versions.check[get_version_nf_resource_id]",
        f"machine.{prefix}.versions.lock_unlock_roundtrip",
        f"machine.{prefix}.versions.check[lock_roundtrip]",
        f"machine.{prefix}.versions.lock_version[empty_loc_raises]",
        f"machine.{prefix}.check[lock_empty_loc_exception]",
        f"machine.{prefix}.backup_cancel_roundtrip",
    )
    _PS_VM_STEPS = (f"machine.{prefix}.video_url.get",)
    _FS_STEPS = (
        f"machine.{prefix}.check[fs_config_populated]",
        f"machine.{prefix}.check[fs_selectors_nonempty]",
        f"machine.{prefix}.update[noop_roundtrip]",
    )

    w0: MachineWorkload | None = next((w for w in workloads if w.workload_type == wl_type), None)

    if w0 is None:
        reason = f"No {wl_type.name} workloads found"
        all_steps: tuple[str, ...] = _COMMON_STEPS
        if wl_type in (MachineWorkloadType.PS, MachineWorkloadType.VM):
            all_steps = all_steps + _PS_VM_STEPS
        if wl_type == MachineWorkloadType.FS:
            all_steps = all_steps + _FS_STEPS
        ctx.skip_remaining(DOMAIN, all_steps, reason=reason)
        return

    # ── get() / get_by_name() ─────────────────────────────────────────────────

    await ctx.call(
        DOMAIN, f"machine.{prefix}.get[direct]",
        lambda: apm.machine.workloads.get(w0.workload_id, w0.namespace),
    )

    by_name = await ctx.call(
        DOMAIN, f"machine.{prefix}.get_by_name[search]",
        lambda: apm.machine.workloads.get_by_name(w0.name),
    )
    if by_name is not None:
        ctx.check(
            DOMAIN, f"machine.{prefix}.check[get_by_name_consistency]",
            by_name.workload_id == w0.workload_id,
            note=f"get_by_name(w0.name) must resolve back to the same {prefix.upper()} workload.",
        )
    else:
        ctx.skip(
            DOMAIN, f"machine.{prefix}.check[get_by_name_consistency]",
            f"machine.{prefix}.get_by_name[search] did not return a result",
        )

    # ── not-found errors ──────────────────────────────────────────────────────

    _w0_ns = w0.namespace
    await ctx.call_expect_not_found(DOMAIN, f"machine.{prefix}", "get",
        lambda: apm.machine.workloads.get(_ZERO_UUID, _w0_ns), "Workload", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, f"machine.{prefix}", "get_by_name",
        lambda: apm.machine.workloads.get_by_name(_SENTINEL_NAME), "Workload", _SENTINEL_NAME)

    # ── change_plan() round-trip using a disposable plan ──────────────────────

    uid = secrets.token_hex(4)
    _keep_days = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    _daily = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0))

    original_plan: ProtectionPlan | None = None
    plan_switch: ProtectionPlan | None = None
    try:
        original_plan = await apm.machine.plans.get(w0.plan.plan_id)
    except Exception:
        original_plan = None

    if original_plan is None:
        ctx.skip(DOMAIN, f"machine.{prefix}.change_plan[switch]", "Could not fetch workload's current plan")
        ctx.skip(DOMAIN, f"machine.{prefix}.change_plan[restore]", "Could not fetch workload's current plan")
    else:
        try:
            plan_switch = await apm.machine.plans.create(MachinePlanCreateRequest(
                name=f"smoke-chgplan-{prefix}-{uid}",
                retention=_keep_days,
                schedule=_daily,
            ))
        except Exception:
            plan_switch = None

        if plan_switch is None:
            ctx.skip(DOMAIN, f"machine.{prefix}.change_plan[switch]", "Could not create disposable plan")
            ctx.skip(DOMAIN, f"machine.{prefix}.change_plan[restore]", "Could not create disposable plan")
        else:
            _orig = original_plan
            _sw = plan_switch
            _w0 = w0
            try:
                await ctx.call(
                    DOMAIN, f"machine.{prefix}.change_plan[switch]",
                    lambda: apm.machine.workloads.change_plan(_w0, _sw),
                )
                await ctx.call(
                    DOMAIN, f"machine.{prefix}.change_plan[restore]",
                    lambda: apm.machine.workloads.change_plan(_w0, _orig),
                )
            finally:
                # Ensure workload is back on original plan before deleting the disposable one.
                # If restore already succeeded this is a no-op (server ignores same plan re-apply).
                with contextlib.suppress(Exception):
                    await apm.machine.workloads.change_plan(_w0, _orig)
                with contextlib.suppress(Exception):
                    await apm.machine.plans.delete(_sw)

    # ── versions ──────────────────────────────────────────────────────────────

    versions_result = await ctx.call(
        DOMAIN, f"machine.{prefix}.versions.list[search]",
        lambda: apm.machine.workloads.list_versions(w0, limit=20),
    )
    versions, _versions_total = versions_result if versions_result is not None else ([], 0)

    # Identify wv (workload with versions, for the happy path) and wnv (workload without
    # versions, for the no-version error path).  w0 always starts in one bucket; other
    # same-type workloads are probed silently until both buckets are filled.
    _type_wls = [w for w in workloads if w.workload_type == wl_type]
    wv: MachineWorkload | None = w0 if versions else None
    wnv: MachineWorkload | None = w0 if not versions else None
    _wv_versions: list[WorkloadVersion] = list(versions)

    for wx in [w for w in _type_wls if w.workload_id != w0.workload_id][:_VERSIONS_FALLBACK_LIMIT]:
        if wv is not None and wnv is not None:
            break
        try:
            wx_result = await apm.machine.workloads.list_versions(wx, limit=20)
            wx_versions, _ = wx_result if wx_result is not None else ([], 0)
        except Exception:
            wx_versions = []
        if wx_versions and wv is None:
            wv = wx
            _wv_versions = list(wx_versions)
        elif not wx_versions and wnv is None:
            wnv = wx

    # Populate ctx.data["machine_versions"] for the activity phase (first type with versions).
    if not ctx.data.get("machine_versions") and _wv_versions:
        ctx.data["machine_versions"] = _wv_versions

    # ── get_latest: happy path (wv) ──────────────────────────────────────────

    _no_wv_reason = f"No {prefix.upper()} workload with backup versions found"
    if wv is not None:
        _wv = wv
        await ctx.call(
            DOMAIN, f"machine.{prefix}.versions.get_latest",
            lambda: apm.machine.workloads.get_latest_version(_wv),
        )
    else:
        ctx.skip(DOMAIN, f"machine.{prefix}.versions.get_latest", _no_wv_reason)

    # ── get_latest: error path (wnv) ─────────────────────────────────────────

    _no_wnv_reason = f"No {prefix.upper()} workload without backup versions found"
    if wnv is not None:
        _wnv = wnv
        glv_exc = await ctx.call_expect_error(
            DOMAIN, f"machine.{prefix}.versions.get_latest[nv_raises]",
            lambda: apm.machine.workloads.get_latest_version(_wnv), ResourceNotFoundError,
        )
        ctx.check_exc_attr(DOMAIN, f"machine.{prefix}.check[get_latest_nv_resource_type]",
            glv_exc, "resource_type", "WorkloadVersion")
        ctx.check_exc_attr(DOMAIN, f"machine.{prefix}.check[get_latest_nv_resource_id]",
            glv_exc, "resource_id", _wnv.workload_id)
    else:
        ctx.skip(DOMAIN, f"machine.{prefix}.versions.get_latest[nv_raises]", _no_wnv_reason)
        ctx.skip(DOMAIN, f"machine.{prefix}.check[get_latest_nv_resource_type]", _no_wnv_reason)
        ctx.skip(DOMAIN, f"machine.{prefix}.check[get_latest_nv_resource_id]", _no_wnv_reason)

    # ── get_version[not_found] (requires wv) ─────────────────────────────────

    if wv is not None:
        _wv_nf = wv
        await ctx.call_expect_not_found(DOMAIN, f"machine.{prefix}.versions", "get_version",
            lambda: apm.machine.workloads.get_version(_wv_nf, "bogus-version-id"),
            "WorkloadVersion", "bogus-version-id")
    else:
        ctx.skip_remaining(DOMAIN, (
            f"machine.{prefix}.versions.get_version[not_found]",
            f"machine.{prefix}.versions.check[get_version_nf_resource_type]",
            f"machine.{prefix}.versions.check[get_version_nf_resource_id]",
        ), reason=_no_wv_reason)

    # ── lock success path (v_nonempty) and error path (v_empty) ──────────────

    v_nonempty: WorkloadVersion | None = next((v for v in _wv_versions if v.locations), None)
    v_empty: WorkloadVersion | None = next((v for v in _wv_versions if not v.locations), None)

    if v_nonempty is not None:
        _vnp = v_nonempty
        _vnp_wl = wv  # not None: v_nonempty came from _wv_versions which requires wv
        roundtrip = await ctx.call(
            DOMAIN, f"machine.{prefix}.versions.lock_unlock_roundtrip",
            lambda: _shared.lock_unlock_roundtrip(apm.machine.workloads, _vnp_wl, _vnp),
            note="lock_version()/unlock_version() should toggle WorkloadVersion.locked.",
        )
        if roundtrip is not None:
            after_first, after_second, first_expected, second_expected = roundtrip
            ctx.check(
                DOMAIN, f"machine.{prefix}.versions.check[lock_roundtrip]",
                after_first.locked == first_expected and after_second.locked == second_expected,
                note="lock_version()/unlock_version() should toggle WorkloadVersion.locked.",
            )
        else:
            ctx.skip(DOMAIN, f"machine.{prefix}.versions.check[lock_roundtrip]",
                "lock_unlock_roundtrip did not return a result")
    else:
        ctx.skip(DOMAIN, f"machine.{prefix}.versions.lock_unlock_roundtrip",
            f"No {prefix.upper()} version with non-empty locations found")
        ctx.skip(DOMAIN, f"machine.{prefix}.versions.check[lock_roundtrip]",
            f"No {prefix.upper()} version with non-empty locations found")

    if v_empty is not None:
        _ve = v_empty
        lock_exc = await ctx.call_expect_error(
            DOMAIN, f"machine.{prefix}.versions.lock_version[empty_loc_raises]",
            lambda: apm.machine.workloads.lock_version(_ve), APIError,
            note="v.locations is empty: lock_version() is expected to raise APIError.",
        )
        ctx.check(DOMAIN, f"machine.{prefix}.check[lock_empty_loc_exception]", isinstance(lock_exc, APIError))
    else:
        ctx.skip(DOMAIN, f"machine.{prefix}.versions.lock_version[empty_loc_raises]",
            f"No {prefix.upper()} version with empty locations found")
        ctx.skip(DOMAIN, f"machine.{prefix}.check[lock_empty_loc_exception]",
            f"No {prefix.upper()} version with empty locations found")

    # ── backup_cancel_roundtrip ───────────────────────────────────────────────

    if w0.is_retired:
        await ctx.call_expect_error(
            DOMAIN, f"machine.{prefix}.backup_cancel_roundtrip",
            lambda: apm.machine.workloads.backup_now(w0), InvalidOperationError,
            note="w0.is_retired is True: backup_now() is expected to raise InvalidOperationError.",
        )
    else:
        await ctx.call(
            DOMAIN, f"machine.{prefix}.backup_cancel_roundtrip",
            lambda: _shared.backup_cancel_roundtrip(apm.machine.workloads, w0),
        )

    # ── PS/VM: video_url ──────────────────────────────────────────────────────

    if wl_type in (MachineWorkloadType.PS, MachineWorkloadType.VM):
        verified_wl: MachineWorkload | None = None
        verified_version: WorkloadVersion | None = None

        # Check w0's already-fetched versions first (no extra API call).
        for v in versions:
            if v.verify_status == VerifyStatus.SUCCESS:
                verified_wl = w0
                verified_version = v
                break

        # Fall back to other workloads of the same type if w0 has no verified version.
        fallback_checked = 0
        if verified_version is None:
            fallback_wls = [
                w for w in workloads
                if w.workload_type == wl_type and w.workload_id != w0.workload_id
            ]
            for wx in fallback_wls[:_VIDEO_URL_FALLBACK_LIMIT]:
                fallback_checked += 1
                try:
                    wx_versions_result = await apm.machine.workloads.list_versions(wx, limit=20)
                except Exception:
                    continue
                wx_versions, _ = wx_versions_result if wx_versions_result is not None else ([], 0)
                for v in wx_versions:
                    if v.verify_status == VerifyStatus.SUCCESS:
                        verified_wl = wx
                        verified_version = v
                        break
                if verified_version is not None:
                    break

        if verified_wl is not None and verified_version is not None:
            _vwl = verified_wl
            _vv = verified_version
            await ctx.call(
                DOMAIN, f"machine.{prefix}.video_url.get",
                lambda: apm.machine.workloads.get_verification_video_url(_vwl, _vv),
            )
        else:
            ctx.skip(
                DOMAIN, f"machine.{prefix}.video_url.get",
                f"No {prefix.upper()} version with verify_status == SUCCESS found"
                f" (checked w0 + {fallback_checked} fallback workload(s))",
            )

    # ── FS-specific checks ────────────────────────────────────────────────────

    if wl_type == MachineWorkloadType.FS:
        ctx.check(
            DOMAIN, f"machine.{prefix}.check[fs_config_populated]",
            w0.fs_config is not None and w0.fs_config.host_ip != "",
        )
        ctx.check(
            DOMAIN, f"machine.{prefix}.check[fs_selectors_nonempty]",
            w0.fs_config is not None and len(w0.fs_config.selectors) >= 1,
        )

        if w0.fs_config is not None:
            cfg = w0.fs_config
            noop_req = FileServerUpdateRequest(
                host_ip=cfg.host_ip,
                login_user=cfg.login_user,
                login_password=None,
                host_port=cfg.host_port,
                enable_vss=cfg.enable_vss,
                connection_timeout_seconds=cfg.connection_timeout_seconds,
                selectors=cfg.selectors,
            )
            _w0_fs = w0
            await ctx.call(
                DOMAIN, f"machine.{prefix}.update[noop_roundtrip]",
                lambda: apm.machine.workloads.update_file_server(_w0_fs, noop_req),
            )
        else:
            ctx.skip(DOMAIN, f"machine.{prefix}.update[noop_roundtrip]", "First FS workload has no fs_config")


_FS_DELETE_RETRIES = 3
_FS_DELETE_RETRY_WAIT = 2  # seconds
_VIDEO_URL_FALLBACK_LIMIT = 5   # max extra workloads to check for a verified version
_VERSIONS_FALLBACK_LIMIT = 5    # max extra workloads to probe when finding wv/wnv


async def _delete_with_retry(apm: APMClient, wl: MachineWorkload, *, trace_step: str = "") -> bool:
    """Attempt to delete wl, retrying on InvalidOperationError. Each attempt is tagged in the API trace."""
    for attempt in range(_FS_DELETE_RETRIES + 1):
        token = _current_step.set(trace_step) if trace_step else None
        try:
            await apm.machine.workloads.delete(wl)
            return True
        except InvalidOperationError:
            if attempt < _FS_DELETE_RETRIES:
                await asyncio.sleep(_FS_DELETE_RETRY_WAIT)
        finally:
            if token is not None:
                _current_step.reset(token)
    return False


async def _call_with_init_poll(fn: Callable[[], Awaitable[Any]]) -> Any:
    """Call fn(), retrying on initialization errors (error_code 7018)."""
    for attempt in range(_FS_INIT_POLL_RETRIES + 1):
        try:
            return await fn()
        except (APIError, InvalidOperationError) as exc:
            if exc.error_code == 7018 and attempt < _FS_INIT_POLL_RETRIES:
                await asyncio.sleep(_FS_INIT_POLL_WAIT)
            else:
                raise
    return None


async def _retire_with_init_poll(
    apm: APMClient, wl: MachineWorkload, retirement_plan: RetirementPlan
) -> None:
    """Retire wl, retrying on initialization errors (error_code 7018)."""
    await _call_with_init_poll(lambda: apm.machine.workloads.retire(wl, retirement_plan))


async def _cleanup_orphan_fs(apm: APMClient, namespace: str, host_ip: str) -> None:
    """Silently delete any leftover FS workloads at host_ip from previous failed runs."""
    for is_retired in (False, True):
        try:
            result = await apm.machine.workloads.list(
                is_retired=is_retired, workload_types=[MachineWorkloadType.FS], namespace=namespace
            )
            for wl in result[0]:
                if wl.fs_config and wl.fs_config.host_ip == host_ip:
                    with contextlib.suppress(Exception):
                        await apm.machine.workloads.delete(wl)
        except Exception:
            pass


async def _wait_for_fs_workload_gone(apm: APMClient, namespace: str, wl_id: str) -> None:
    """Poll get() until the FS workload is gone (ResourceNotFoundError) or 180 s elapsed."""
    for attempt in range(60):
        try:
            await apm.machine.workloads.get(wl_id, namespace)
        except ResourceNotFoundError:
            break
        except Exception:
            pass  # transient error; keep polling
        if attempt < 59:
            await asyncio.sleep(3)


# Steps that can be bulk-skipped when a prerequisite fails.
# Cleanup steps (plan_b[delete], plan_a[delete]) are excluded — they are always emitted
# by the finally block and must not be double-emitted by skip_remaining.
_FS_CRUD_ACTIVE_STEPS_PREREQ = (
    "machine.fs_crud.active.plan_a[create]",
    "machine.fs_crud.active.plan_b[create]",
    "machine.fs_crud.active.add[empty_password_raises]",
    "machine.fs_crud.active.add[smoke]",
    "machine.fs_crud.active.list[after_add]",
    "machine.fs_crud.active.check[add_appears_in_list]",
    "machine.fs_crud.active.check[add_fs_config_fields]",
    "machine.fs_crud.active.get[direct]",
    "machine.fs_crud.active.get_by_name",
    "machine.fs_crud.active.check[get_by_name]",
    "machine.fs_crud.active.add[duplicate_raises]",
    "machine.fs_crud.active.check[duplicate_error_code]",
    "machine.fs_crud.active.check[duplicate_resource_type]",
    "machine.fs_crud.active.check[duplicate_resource_id]",
    "machine.fs_crud.active.add[fake_ip_c]",
    "machine.fs_crud.active.list[after_add_c]",
    "machine.fs_crud.active.update_file_server[dup_raises]",
    "machine.fs_crud.active.check[update_dup_resource_type]",
    "machine.fs_crud.active.check[update_dup_resource_id]",
    "machine.fs_crud.active.update[empty_password_raises]",
    "machine.fs_crud.active.update[smoke]",
    "machine.fs_crud.active.list[after_update]",
    "machine.fs_crud.active.check[update_fields_applied]",
    "machine.fs_crud.active.backup_cancel",
    "machine.fs_crud.active.plan_a[delete_inuse_raises]",
    "machine.fs_crud.active.check[plan_a_inuse_resource_type]",
    "machine.fs_crud.active.check[plan_a_inuse_resource_id]",
    "machine.fs_crud.active.change_plan[switch]",
    "machine.fs_crud.active.retire[success]",
    "machine.fs_crud.active.get[post_retire]",
    "machine.fs_crud.active.check[retire_is_retired]",
    "machine.fs_crud.active.retire[already_retired_raises]",
    "machine.fs_crud.active.check[retire_already_retired_resource_type]",
    "machine.fs_crud.active.check[retire_already_retired_resource_id]",
    "machine.fs_crud.active.backup_now[retired_raises]",
    "machine.fs_crud.active.check[backup_now_retired_resource_type]",
    "machine.fs_crud.active.check[backup_now_retired_resource_id]",
    "machine.fs_crud.active.cancel_backup[retired_raises]",
    "machine.fs_crud.active.check[cancel_backup_retired_resource_type]",
    "machine.fs_crud.active.check[cancel_backup_retired_resource_id]",
    "machine.fs_crud.active.delete[smoke]",
    "machine.fs_crud.active.get[after_delete]",
    "machine.fs_crud.active.check[delete_removes_workload]",
)


async def _run_fs_crud_active(ctx: SmokeContext) -> None:
    """Flow 1 — active workload lifecycle: add → update → backup_cancel → change_plan → delete."""
    apm = ctx.apm

    _infra_servers: list[Any] = ctx.data.get("servers", [])
    if _infra_servers:
        ctx.na(DOMAIN, "machine.fs_crud.active.backup_servers.list", "Reusing server from infra phase")
        server = _infra_servers[0]
        ctx.data["_fs_crud_server_result"] = ([server], 1)
    else:
        _servers_result = await ctx.call(
            DOMAIN, "machine.fs_crud.active.backup_servers.list",
            lambda: apm.backup_servers.list(limit=1),
        )
        if not _servers_result or not _servers_result[0]:
            ctx.skip_remaining(DOMAIN, _FS_CRUD_ACTIVE_STEPS_PREREQ, reason="No backup servers available")
            return
        server = _servers_result[0][0]
        ctx.data["_fs_crud_server_result"] = _servers_result
    uid = secrets.token_hex(4)
    FAKE_IP_A = f"{_FS_CRUD_FAKE_IP_PREFIX}.{int(uid[:2], 16) % 100 + 50}"
    FAKE_IP_C = f"{_FS_CRUD_FAKE_IP_PREFIX}.{int(uid[2:4], 16) % 48 + 2}"  # [2, 49]
    _keep_days = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    _daily = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0))

    plan_a: ProtectionPlan | None = None
    plan_b: ProtectionPlan | None = None
    wl_to_delete_id: str | None = None
    fake_ip_c_wl: MachineWorkload | None = None

    try:
        plan_a = await ctx.call(
            DOMAIN, "machine.fs_crud.active.plan_a[create]",
            lambda: apm.machine.plans.create(MachinePlanCreateRequest(
                name=f"smoke-fs-active-a-{uid}", retention=_keep_days, schedule=_daily,
            )),
        )
        plan_b = await ctx.call(
            DOMAIN, "machine.fs_crud.active.plan_b[create]",
            lambda: apm.machine.plans.create(MachinePlanCreateRequest(
                name=f"smoke-fs-active-b-{uid}", retention=_keep_days, schedule=_daily,
            )),
        )

        if plan_a is None or plan_b is None:
            missing = [n for n, p in [("plan_a", plan_a), ("plan_b", plan_b)] if p is None]
            reason = f"Disposable plan creation failed: {', '.join(missing)}"
            ctx.skip_remaining(DOMAIN, _FS_CRUD_ACTIVE_STEPS_PREREQ, reason=reason)
            return

        _plan_a: ProtectionPlan = plan_a
        _plan_b: ProtectionPlan = plan_b

        # ── Pre-cleanup: remove any orphan FS workloads at FAKE_IP_A / FAKE_IP_C ──
        await _cleanup_orphan_fs(apm, server.namespace, FAKE_IP_A)
        await _cleanup_orphan_fs(apm, server.namespace, FAKE_IP_C)

        # ── ValueError: empty password on add ────────────────────────────────

        await ctx.call_expect_value_error(
            DOMAIN, "machine.fs_crud.active.add[empty_password_raises]",
            lambda: apm.machine.workloads.add_file_server(FileServerAddRequest(
                namespace=server.namespace,
                host_ip=FAKE_IP_A,
                server_type=FileServerType.SMB,
                plan_id=_plan_a.plan_id,
                login_user="administrator",
                login_password="",
                connection_timeout_seconds=60,
            )),
            note="Empty login_password must raise ValueError before any API call is made.",
        )

        # ── Successful add ────────────────────────────────────────────────────

        add_req = FileServerAddRequest(
            namespace=server.namespace,
            host_ip=FAKE_IP_A,
            server_type=FileServerType.SMB,
            plan_id=_plan_a.plan_id,
            login_user="administrator",
            login_password="smoke-test-placeholder",
            connection_timeout_seconds=60,
            selectors=(FileServerPathSelector(path=""),),
        )
        await ctx.call(
            DOMAIN, "machine.fs_crud.active.add[smoke]",
            lambda: apm.machine.workloads.add_file_server(add_req),
        )

        # ── Locate and verify added workload ──────────────────────────────────

        after_add_result = await ctx.call(
            DOMAIN, "machine.fs_crud.active.list[after_add]",
            lambda: apm.machine.workloads.list(
                workload_types=[MachineWorkloadType.FS], namespace=server.namespace
            ),
        )
        after_add: list[MachineWorkload] = list(after_add_result[0]) if after_add_result else []
        added_wl: MachineWorkload | None = next(
            (wl for wl in after_add
             if wl.fs_config and wl.fs_config.host_ip == FAKE_IP_A
             and wl.plan.plan_id == _plan_a.plan_id
             and wl.namespace == server.namespace),
            None,
        )

        ctx.check(
            DOMAIN, "machine.fs_crud.active.check[add_appears_in_list]",
            added_wl is not None and added_wl.fs_config is not None and added_wl.fs_config.host_ip == FAKE_IP_A,
        )
        ctx.check(
            DOMAIN, "machine.fs_crud.active.check[add_fs_config_fields]",
            (
                added_wl is not None
                and added_wl.fs_config is not None
                and added_wl.fs_config.server_type == FileServerType.SMB
                and added_wl.fs_config.login_user == "administrator"
                and len(added_wl.fs_config.selectors) >= 1
                and added_wl.fs_config.connection_timeout_seconds == 60
            ),
        )

        if added_wl is not None:
            _added = added_wl
            await ctx.call(
                DOMAIN, "machine.fs_crud.active.get[direct]",
                lambda: apm.machine.workloads.get(_added.workload_id, _added.namespace),
            )
            fetched_by_name = await ctx.call(
                DOMAIN, "machine.fs_crud.active.get_by_name",
                lambda: apm.machine.workloads.get_by_name(_added.name),
            )
            ctx.check(
                DOMAIN, "machine.fs_crud.active.check[get_by_name]",
                fetched_by_name is not None and fetched_by_name.workload_id == _added.workload_id,
            )
        else:
            ctx.skip_remaining(
                DOMAIN, _FS_CRUD_ACTIVE_STEPS_PREREQ,
                reason=f"Added FS workload at {FAKE_IP_A!r} not found in list",
            )
            return

        # ── Duplicate add → DuplicateWorkloadError ────────────────────────────

        dup_exc = await ctx.call_expect_error(
            DOMAIN, "machine.fs_crud.active.add[duplicate_raises]",
            lambda: apm.machine.workloads.add_file_server(add_req),
            DuplicateWorkloadError,
        )
        ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[duplicate_error_code]",
            dup_exc, "error_code", 7001)
        ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[duplicate_resource_type]",
            dup_exc, "resource_type", "file_server")
        ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[duplicate_resource_id]",
            dup_exc, "resource_id", FAKE_IP_A)

        # ── DuplicateWorkloadError from update_file_server() ─────────────────
        # Add a second FS workload at FAKE_IP_C, then attempt to rename it to FAKE_IP_A.

        _fake_c_req = FileServerAddRequest(
            namespace=server.namespace,
            host_ip=FAKE_IP_C,
            server_type=FileServerType.SMB,
            plan_id=_plan_a.plan_id,
            login_user="administrator",
            login_password="smoke-test-placeholder",
            connection_timeout_seconds=60,
            selectors=(FileServerPathSelector(path=""),),
        )
        await ctx.call(
            DOMAIN, "machine.fs_crud.active.add[fake_ip_c]",
            lambda: apm.machine.workloads.add_file_server(_fake_c_req),
        )
        _after_c_result = await ctx.call(
            DOMAIN, "machine.fs_crud.active.list[after_add_c]",
            lambda: apm.machine.workloads.list(
                workload_types=[MachineWorkloadType.FS], namespace=server.namespace
            ),
        )
        fake_ip_c_wl = next(
            (wl for wl in (_after_c_result[0] if _after_c_result else [])
             if wl.fs_config and wl.fs_config.host_ip == FAKE_IP_C
             and wl.namespace == server.namespace),
            None,
        )

        if fake_ip_c_wl is not None:
            _fc_wl = fake_ip_c_wl
            _fc_upd_req = FileServerUpdateRequest(
                host_ip=FAKE_IP_A,
                login_user="administrator",
                login_password=None,
                selectors=(FileServerPathSelector(path=""),),
            )
            dup_upd_exc = await ctx.call_expect_error(
                DOMAIN, "machine.fs_crud.active.update_file_server[dup_raises]",
                lambda: _call_with_init_poll(
                    lambda: apm.machine.workloads.update_file_server(_fc_wl, _fc_upd_req)
                ),
                DuplicateWorkloadError,
            )
            ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[update_dup_resource_type]",
                dup_upd_exc, "resource_type", "file_server")
            ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[update_dup_resource_id]",
                dup_upd_exc, "resource_id", FAKE_IP_A)
        else:
            ctx.skip(DOMAIN, "machine.fs_crud.active.update_file_server[dup_raises]",
                "Failed to locate fake_ip_c workload after add")
            ctx.skip(DOMAIN, "machine.fs_crud.active.check[update_dup_resource_type]",
                "Failed to locate fake_ip_c workload after add")
            ctx.skip(DOMAIN, "machine.fs_crud.active.check[update_dup_resource_id]",
                "Failed to locate fake_ip_c workload after add")

        # ── ValueError: empty password on update ──────────────────────────────

        added_wl_ref = added_wl
        await ctx.call_expect_value_error(
            DOMAIN, "machine.fs_crud.active.update[empty_password_raises]",
            lambda: apm.machine.workloads.update_file_server(added_wl_ref, FileServerUpdateRequest(
                host_ip=FAKE_IP_A,
                login_user="administrator",
                login_password="",
                selectors=(FileServerPathSelector(path=""),),
            )),
        )

        # ── Successful update with field changes ──────────────────────────────

        upd_req = FileServerUpdateRequest(
            host_ip=FAKE_IP_A,
            login_user="updated-user",
            login_password=None,
            selectors=(
                FileServerPathSelector(path="share1"),
                FileServerPathSelector(path="share2", excluded_paths=("tmp",)),
            ),
        )
        await _call_with_init_poll(
            lambda: apm.machine.workloads.update_file_server(added_wl_ref, upd_req)
        )
        update_succeeded = True

        ctx.check(DOMAIN, "machine.fs_crud.active.update[smoke]", update_succeeded)

        # ── Re-list and verify updated fields ─────────────────────────────────

        updated_wl: MachineWorkload | None = None
        if not update_succeeded:
            ctx.skip(DOMAIN, "machine.fs_crud.active.list[after_update]", "update[smoke] did not succeed")
            ctx.skip(DOMAIN, "machine.fs_crud.active.check[update_fields_applied]", "update[smoke] did not succeed")
        else:
            after_upd_result = await ctx.call(
                DOMAIN, "machine.fs_crud.active.list[after_update]",
                lambda: apm.machine.workloads.list(
                    workload_types=[MachineWorkloadType.FS], namespace=server.namespace
                ),
            )
            after_upd: list[MachineWorkload] = list(after_upd_result[0]) if after_upd_result else []
            updated_wl = next(
                (wl for wl in after_upd if wl.workload_id == added_wl.workload_id),
                None,
            )
            ctx.check(
                DOMAIN, "machine.fs_crud.active.check[update_fields_applied]",
                (
                    updated_wl is not None
                    and updated_wl.fs_config is not None
                    and updated_wl.fs_config.login_user == "updated-user"
                    and len(updated_wl.fs_config.selectors) == 2
                ),
            )

        # ── backup_now() + cancel_backup() round-trip ─────────────────────────

        current_wl: MachineWorkload = updated_wl if updated_wl is not None else added_wl
        current_wl_ref = current_wl
        await ctx.call(
            DOMAIN, "machine.fs_crud.active.backup_cancel",
            lambda: _shared.backup_cancel_roundtrip(apm.machine.workloads, current_wl_ref),
        )

        # ── PlanInUseError from plan_a.delete() ───────────────────────────────
        # current_wl is still on plan_a at this point.

        _plan_a_ref = _plan_a
        inuse_exc = await ctx.call_expect_error(
            DOMAIN, "machine.fs_crud.active.plan_a[delete_inuse_raises]",
            lambda: apm.machine.plans.delete(_plan_a_ref),
            PlanInUseError,
        )
        ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[plan_a_inuse_resource_type]",
            inuse_exc, "resource_type", "ProtectionPlan")
        ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[plan_a_inuse_resource_id]",
            inuse_exc, "resource_id", _plan_a.plan_id)

        # ── change_plan(): switch from plan_a to plan_b ───────────────────────

        await ctx.call(
            DOMAIN, "machine.fs_crud.active.change_plan[switch]",
            lambda: apm.machine.workloads.change_plan(current_wl_ref, _plan_b),
        )

        # ── Retire current_wl and verify post-retire guards ───────────────────
        # current_wl is now on plan_b.

        _retire_plan = next((p for p in ctx.data.get("retirement_plans", [])), None)
        _retire_steps = (
            "machine.fs_crud.active.retire[success]",
            "machine.fs_crud.active.get[post_retire]",
            "machine.fs_crud.active.check[retire_is_retired]",
            "machine.fs_crud.active.retire[already_retired_raises]",
            "machine.fs_crud.active.check[retire_already_retired_resource_type]",
            "machine.fs_crud.active.check[retire_already_retired_resource_id]",
            "machine.fs_crud.active.backup_now[retired_raises]",
            "machine.fs_crud.active.check[backup_now_retired_resource_type]",
            "machine.fs_crud.active.check[backup_now_retired_resource_id]",
            "machine.fs_crud.active.cancel_backup[retired_raises]",
            "machine.fs_crud.active.check[cancel_backup_retired_resource_type]",
            "machine.fs_crud.active.check[cancel_backup_retired_resource_id]",
        )
        if _retire_plan is None:
            ctx.skip_remaining(DOMAIN, _retire_steps, reason="No Retirement Plans found")
        else:
            _rp = _retire_plan
            _retire_wl = current_wl_ref
            await ctx.call(
                DOMAIN, "machine.fs_crud.active.retire[success]",
                lambda: _retire_with_init_poll(apm, _retire_wl, _rp),
            )
            await asyncio.sleep(_FS_RETIRE_PROP_WAIT)
            retired_current_wl: MachineWorkload | None = await ctx.call(
                DOMAIN, "machine.fs_crud.active.get[post_retire]",
                lambda: apm.machine.workloads.get(_retire_wl.workload_id, _retire_wl.namespace),
            )
            ctx.check(
                DOMAIN, "machine.fs_crud.active.check[retire_is_retired]",
                retired_current_wl is not None and retired_current_wl.is_retired is True,
            )
            if retired_current_wl is not None:
                _ret_wl = retired_current_wl
                already_ret_exc = await ctx.call_expect_error(
                    DOMAIN, "machine.fs_crud.active.retire[already_retired_raises]",
                    lambda: apm.machine.workloads.retire(_ret_wl, _rp),
                    InvalidOperationError,
                )
                ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[retire_already_retired_resource_type]",
                    already_ret_exc, "resource_type", "Workload")
                ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[retire_already_retired_resource_id]",
                    already_ret_exc, "resource_id", _ret_wl.workload_id)

                bkp_exc = await ctx.call_expect_error(
                    DOMAIN, "machine.fs_crud.active.backup_now[retired_raises]",
                    lambda: apm.machine.workloads.backup_now(_ret_wl),
                    InvalidOperationError,
                )
                ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[backup_now_retired_resource_type]",
                    bkp_exc, "resource_type", "Workload")
                ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[backup_now_retired_resource_id]",
                    bkp_exc, "resource_id", _ret_wl.workload_id)

                can_exc = await ctx.call_expect_error(
                    DOMAIN, "machine.fs_crud.active.cancel_backup[retired_raises]",
                    lambda: apm.machine.workloads.cancel_backup(_ret_wl),
                    InvalidOperationError,
                )
                ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[cancel_backup_retired_resource_type]",
                    can_exc, "resource_type", "Workload")
                ctx.check_exc_attr(DOMAIN, "machine.fs_crud.active.check[cancel_backup_retired_resource_id]",
                    can_exc, "resource_id", _ret_wl.workload_id)
            else:
                ctx.skip_remaining(DOMAIN, _retire_steps, reason="Could not re-fetch workload after retire")

        # ── Delete the active FS workload ─────────────────────────────────────

        wl_to_delete: MachineWorkload = current_wl
        wl_to_delete_id = wl_to_delete.workload_id
        delete_succeeded = await _delete_with_retry(
            apm, wl_to_delete, trace_step="machine.fs_crud.active.delete[smoke]"
        )

        ctx.check(
            DOMAIN, "machine.fs_crud.active.delete[smoke]",
            delete_succeeded,
            note="delete() may need retries if the workload is still initializing.",
        )

        if not delete_succeeded:
            for step in ("machine.fs_crud.active.get[after_delete]", "machine.fs_crud.active.check[delete_removes_workload]"):
                ctx.skip(DOMAIN, step, f"Delete did not succeed after {_FS_DELETE_RETRIES} retries")
        else:
            # _snap distinguishes expected ResourceNotFoundError from an unexpected APMError:
            # ctx.call() returns None in both cases; only unexpected errors increment the counter.
            _snap = ctx.stats[DOMAIN].unexpected
            wl_after = await ctx.call(
                DOMAIN, "machine.fs_crud.active.get[after_delete]",
                lambda: apm.machine.workloads.get(wl_to_delete_id, server.namespace),
                expect_error=ResourceNotFoundError,
            )
            workload_done = (ctx.stats[DOMAIN].unexpected == _snap) and (
                wl_after is None or wl_after.status == WorkloadStatus.DELETING
            )
            ctx.check(DOMAIN, "machine.fs_crud.active.check[delete_removes_workload]", workload_done)

    finally:
        _wl_delete_id = wl_to_delete_id
        if _wl_delete_id is not None:
            await _wait_for_fs_workload_gone(apm, server.namespace, _wl_delete_id)

        if fake_ip_c_wl is not None:
            _fc_cleanup = fake_ip_c_wl
            try:
                await _delete_with_retry(apm, _fc_cleanup)
                await _wait_for_fs_workload_gone(apm, server.namespace, _fc_cleanup.workload_id)
            except Exception:
                pass

        for step_name, mplan in (
            ("machine.fs_crud.active.plan_b[delete]", plan_b),
            ("machine.fs_crud.active.plan_a[delete]", plan_a),
        ):
            if mplan is None:
                ctx.skip(DOMAIN, step_name, "Plan was not created")
                continue
            _mp = mplan
            try:
                await apm.machine.plans.delete(_mp)
                ctx.check(DOMAIN, step_name, True)
            except Exception:
                ctx.check(DOMAIN, step_name, False)


# Cleanup steps (ret_plan_b[delete], ret_plan_a[delete], plan_c[delete]) are excluded —
# they are always emitted by the finally block and must not be double-emitted by skip_remaining.
_FS_CRUD_RETIRED_STEPS_PREREQ = (
    "machine.fs_crud.retired.plan_c[create]",
    "machine.fs_crud.retired.ret_plan_a[create]",
    "machine.fs_crud.retired.ret_plan_b[create]",
    "machine.fs_crud.retired.add[smoke]",
    "machine.fs_crud.retired.list[after_add]",
    "machine.fs_crud.retired.retire",
    "machine.fs_crud.retired.list[after_retire]",
    "machine.fs_crud.retired.check[retire_in_retired_list]",
    "machine.fs_crud.retired.change_plan[retire_b]",
    "machine.fs_crud.retired.delete[smoke]",
    "machine.fs_crud.retired.get[after_delete]",
    "machine.fs_crud.retired.check[delete_removes_workload]",
)


async def _run_fs_crud_retired(ctx: SmokeContext) -> None:
    """Flow 2 — retirement lifecycle: add → retire → change_plan (retirement) → delete."""
    apm = ctx.apm

    servers_result = ctx.data.get("_fs_crud_server_result")
    if servers_result is None:
        servers_result = await ctx.call(
            DOMAIN, "machine.fs_crud.retired.backup_servers.list",
            lambda: apm.backup_servers.list(limit=1),
        )
    else:
        ctx.na(DOMAIN, "machine.fs_crud.retired.backup_servers.list", "Reusing server from active flow")

    if not servers_result or not servers_result[0]:
        ctx.skip_remaining(DOMAIN, _FS_CRUD_RETIRED_STEPS_PREREQ, reason="No backup servers available")
        return

    server = servers_result[0][0]
    uid = secrets.token_hex(4)
    # FAKE_IP_A uses [50, 149]; FAKE_IP_B uses [150, 249] — non-overlapping halves.
    FAKE_IP_B = f"{_FS_CRUD_FAKE_IP_PREFIX}.{int(uid[:2], 16) % 100 + 150}"
    _keep_days = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    _daily = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0))

    plan_c: ProtectionPlan | None = None
    ret_plan_a: RetirementPlan | None = None
    ret_plan_b: RetirementPlan | None = None
    wl_to_delete_id: str | None = None

    try:
        plan_c = await ctx.call(
            DOMAIN, "machine.fs_crud.retired.plan_c[create]",
            lambda: apm.machine.plans.create(MachinePlanCreateRequest(
                name=f"smoke-fs-ret-c-{uid}", retention=_keep_days, schedule=_daily,
            )),
        )
        ret_plan_a = await ctx.call(
            DOMAIN, "machine.fs_crud.retired.ret_plan_a[create]",
            lambda: apm.retirement_plans.create(RetirementPlanCreateRequest(
                name=f"smoke-fs-ret-a-{uid}", retention_days=30,
            )),
        )
        ret_plan_b = await ctx.call(
            DOMAIN, "machine.fs_crud.retired.ret_plan_b[create]",
            lambda: apm.retirement_plans.create(RetirementPlanCreateRequest(
                name=f"smoke-fs-ret-b-{uid}", retention_days=30,
            )),
        )

        if plan_c is None or ret_plan_a is None or ret_plan_b is None:
            missing = [n for n, p in [("plan_c", plan_c), ("ret_plan_a", ret_plan_a), ("ret_plan_b", ret_plan_b)] if p is None]
            reason = f"Disposable plan creation failed: {', '.join(missing)}"
            ctx.skip_remaining(DOMAIN, _FS_CRUD_RETIRED_STEPS_PREREQ, reason=reason)
            return

        _plan_c: ProtectionPlan = plan_c
        _ret_plan_a: RetirementPlan = ret_plan_a
        _ret_plan_b: RetirementPlan = ret_plan_b

        # ── Pre-cleanup: remove any orphan FS workloads at FAKE_IP_B ─────────
        await _cleanup_orphan_fs(apm, server.namespace, FAKE_IP_B)

        # ── Add ───────────────────────────────────────────────────────────────

        add_req = FileServerAddRequest(
            namespace=server.namespace,
            host_ip=FAKE_IP_B,
            server_type=FileServerType.SMB,
            plan_id=_plan_c.plan_id,
            login_user="administrator",
            login_password="smoke-test-placeholder",
            connection_timeout_seconds=60,
            selectors=(FileServerPathSelector(path=""),),
        )
        await ctx.call(
            DOMAIN, "machine.fs_crud.retired.add[smoke]",
            lambda: apm.machine.workloads.add_file_server(add_req),
        )

        # ── Locate added workload ─────────────────────────────────────────────

        after_add_result = await ctx.call(
            DOMAIN, "machine.fs_crud.retired.list[after_add]",
            lambda: apm.machine.workloads.list(
                workload_types=[MachineWorkloadType.FS], namespace=server.namespace
            ),
        )
        added_wl: MachineWorkload | None = next(
            (wl for wl in (after_add_result[0] if after_add_result else [])
             if wl.fs_config and wl.fs_config.host_ip == FAKE_IP_B
             and wl.namespace == server.namespace),
            None,
        ) if after_add_result else None

        if added_wl is None:
            ctx.skip_remaining(
                DOMAIN, _FS_CRUD_RETIRED_STEPS_PREREQ,
                reason=f"Added FS workload at {FAKE_IP_B!r} not found in list",
            )
            return

        wl_to_delete_id = added_wl.workload_id

        # ── Retire onto ret_plan_a (retries on initialization errors) ─────────

        _added = added_wl
        await ctx.call(
            DOMAIN, "machine.fs_crud.retired.retire",
            lambda: _retire_with_init_poll(apm, _added, _ret_plan_a),
        )

        # ── Confirm workload appears in retired list ───────────────────────────

        await asyncio.sleep(_FS_RETIRE_PROP_WAIT)
        after_retire_result = await ctx.call(
            DOMAIN, "machine.fs_crud.retired.list[after_retire]",
            lambda: apm.machine.workloads.list(
                is_retired=True, workload_types=[MachineWorkloadType.FS], namespace=server.namespace
            ),
        )
        retired_wl: MachineWorkload | None = next(
            (w for w in (after_retire_result[0] if after_retire_result else [])
             if w.workload_id == added_wl.workload_id),
            None,
        ) if after_retire_result else None
        ctx.check(
            DOMAIN, "machine.fs_crud.retired.check[retire_in_retired_list]",
            retired_wl is not None and retired_wl.status == WorkloadStatus.RETIRED,
        )

        if retired_wl is not None:
            _retired = retired_wl
            await ctx.call(
                DOMAIN, "machine.fs_crud.retired.change_plan[retire_b]",
                lambda: apm.machine.workloads.change_plan(_retired, _ret_plan_b),
            )
        else:
            ctx.skip(
                DOMAIN, "machine.fs_crud.retired.change_plan[retire_b]",
                "Workload not found in retired list after retire()",
            )

        # ── Delete the retired FS workload ────────────────────────────────────

        wl_to_delete: MachineWorkload = retired_wl if retired_wl is not None else added_wl
        wl_to_delete_id = wl_to_delete.workload_id
        delete_succeeded = await _delete_with_retry(
            apm, wl_to_delete, trace_step="machine.fs_crud.retired.delete[smoke]"
        )

        ctx.check(
            DOMAIN, "machine.fs_crud.retired.delete[smoke]",
            delete_succeeded,
            note="delete() on a retired workload may need retries if still deregistering.",
        )

        if not delete_succeeded:
            for step in ("machine.fs_crud.retired.get[after_delete]", "machine.fs_crud.retired.check[delete_removes_workload]"):
                ctx.skip(DOMAIN, step, f"Delete did not succeed after {_FS_DELETE_RETRIES} retries")
        else:
            # _snap distinguishes expected ResourceNotFoundError from an unexpected APMError:
            # ctx.call() returns None in both cases; only unexpected errors increment the counter.
            _snap = ctx.stats[DOMAIN].unexpected
            wl_after = await ctx.call(
                DOMAIN, "machine.fs_crud.retired.get[after_delete]",
                lambda: apm.machine.workloads.get(wl_to_delete_id, server.namespace),
                expect_error=ResourceNotFoundError,
            )
            workload_done = (ctx.stats[DOMAIN].unexpected == _snap) and (
                wl_after is None or wl_after.status == WorkloadStatus.DELETING
            )
            ctx.check(DOMAIN, "machine.fs_crud.retired.check[delete_removes_workload]", workload_done)

    finally:
        # ── Wait for deleted workload to clear, then delete plans ─────────────

        _wl_delete_id = wl_to_delete_id
        if _wl_delete_id is not None:
            await _wait_for_fs_workload_gone(apm, server.namespace, _wl_delete_id)

        for step_name, plan_obj in (
            ("machine.fs_crud.retired.ret_plan_b[delete]", ret_plan_b),
            ("machine.fs_crud.retired.ret_plan_a[delete]", ret_plan_a),
        ):
            if plan_obj is None:
                ctx.skip(DOMAIN, step_name, "Plan was not created")
                continue
            _p = plan_obj
            deleted = False
            for attempt in range(3):
                try:
                    await apm.retirement_plans.delete(_p)
                    deleted = True
                    break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(3)
            ctx.check(DOMAIN, step_name, deleted)

        for step_name, mplan in (
            ("machine.fs_crud.retired.plan_c[delete]", plan_c),
        ):
            if mplan is None:
                ctx.skip(DOMAIN, step_name, "Plan was not created")
                continue
            _mp = mplan
            try:
                await apm.machine.plans.delete(_mp)
                ctx.check(DOMAIN, step_name, True)
            except Exception:
                ctx.check(DOMAIN, step_name, False)
