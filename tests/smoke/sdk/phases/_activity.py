"""Activity domain phase: apm.activities.backup / apm.activities.restore.

Reads ctx.data["machine_workloads"] and ctx.data["machine_versions"] (from the machine phase)
for the workload-filter, bogus-workload-filter, and get_by_version checks. Populates
ctx.data["backup_activities"] and ctx.data["restore_activities"] for use by later phases.
"""
from __future__ import annotations

import asyncio
import dataclasses

from synology_apm.sdk import (
    APIError,
    BackupActivityStatus,
    MachineWorkload,
    ResourceNotFoundError,
    RestoreActivity,
    RestoreActivityStatus,
    WorkloadVersion,
)

from .._context import SmokeContext
from ._shared import SENTINEL_NAME as _SENTINEL_NAME
from ._shared import ZERO_UUID as _ZERO_UUID

DOMAIN = "activity"

_CANCEL_POLL_ATTEMPTS = 5
_CANCEL_POLL_INTERVAL_SECONDS = 2.0
_CANCEL_SETTLED_STATUSES = frozenset(
    {RestoreActivityStatus.CANCELING, RestoreActivityStatus.CANCELED}
)


async def run(ctx: SmokeContext) -> None:
    machine_workloads: list[MachineWorkload] = ctx.data.get("machine_workloads", [])
    machine_versions: list[WorkloadVersion] = ctx.data.get("machine_versions", [])

    await _run_backup(ctx, machine_workloads, machine_versions)
    await _run_restore(ctx, machine_workloads)


async def _run_backup(
    ctx: SmokeContext, machine_workloads: list[MachineWorkload], machine_versions: list[WorkloadVersion]
) -> None:
    apm = ctx.apm

    await ctx.call(
        DOMAIN, "activity.backup.list[ongoing]", lambda: apm.activities.backup.list(history=False, limit=100)
    )

    history_result = await ctx.call(
        DOMAIN, "activity.backup.list[history]", lambda: apm.activities.backup.list(history=True, limit=100)
    )
    backup_activities, _total = history_result if history_result is not None else ([], 0)
    ctx.data["backup_activities"] = backup_activities

    failed_result = await ctx.call(
        DOMAIN, "activity.backup.list[status_failed]",
        lambda: apm.activities.backup.list(status=[BackupActivityStatus.FAILED], history=True, limit=100),
    )
    failed_activities, _total = failed_result if failed_result is not None else ([], 0)
    ctx.check(
        DOMAIN, "activity.backup.check[status_failed_mapping]",
        all(a.status == BackupActivityStatus.FAILED for a in failed_activities),
        note="status=[FAILED] filter must only return failed activities.",
    )

    # ── not-found errors ──────────────────────────────────────────────────────

    await ctx.call_expect_not_found(DOMAIN, "activity.backup", "get",
        lambda: apm.activities.backup.get(_ZERO_UUID), "Activity", _ZERO_UUID)

    get_latest_exc = await ctx.call_expect_error(
        DOMAIN, "activity.backup.get_latest_by_workload_name[not_found]",
        lambda: apm.activities.backup.get_latest_by_workload_name(_SENTINEL_NAME),
        ResourceNotFoundError,
    )
    ctx.check_exc_attr(DOMAIN, "activity.backup.check[get_latest_nf_resource_type]",
        get_latest_exc, "resource_type", "Activity")
    ctx.check_exc_attr(DOMAIN, "activity.backup.check[get_latest_nf_resource_id]",
        get_latest_exc, "resource_id", _SENTINEL_NAME)

    if not machine_workloads:
        for step, reason in (
            ("activity.backup.list[machine_workload_filter]", "No Machine Workloads found"),
            ("activity.backup.check[workload_filter]", "No Machine Workloads found"),
            ("activity.backup.list[bogus_workload_filter]", "No Machine Workloads found"),
            ("activity.backup.check[bogus_workload_empty]", "No Machine Workloads found"),
            ("activity.backup.get_latest_by_workload_name", "No Machine Workloads found"),
        ):
            ctx.skip(DOMAIN, step, reason)
    else:
        w0 = machine_workloads[0]

        filtered_result = await ctx.call(
            DOMAIN, "activity.backup.list[machine_workload_filter]",
            lambda: apm.activities.backup.list(workload=w0, history=True, limit=10),
        )
        filtered_activities, _total = filtered_result if filtered_result is not None else ([], 0)
        ctx.check(
            DOMAIN, "activity.backup.check[workload_filter]",
            all(a.workload_id == w0.workload_id for a in filtered_activities),
            note="workload filter must only return activities for that specific workload.",
        )

        bogus = dataclasses.replace(w0, workload_id="bogus-workload-id-00000000")
        bogus_result = await ctx.call(
            DOMAIN, "activity.backup.list[bogus_workload_filter]",
            lambda: apm.activities.backup.list(workload=bogus, history=True, limit=10),
        )
        if bogus_result is None:
            ctx.check(DOMAIN, "activity.backup.check[bogus_workload_empty]", False)
        else:
            bogus_activities, bogus_total = bogus_result
            ctx.check(
                DOMAIN, "activity.backup.check[bogus_workload_empty]",
                bogus_activities == [] and bogus_total == 0,
            )

        await ctx.call(
            DOMAIN, "activity.backup.get_latest_by_workload_name",
            lambda: apm.activities.backup.get_latest_by_workload_name(w0.name),
        )

    if not backup_activities:
        ctx.skip(DOMAIN, "activity.backup.get[direct]", "No backup activity history found")
    else:
        await ctx.call(
            DOMAIN, "activity.backup.get[direct]", lambda: apm.activities.backup.get(backup_activities[0].activity_id)
        )

    if not machine_versions:
        ctx.skip(DOMAIN, "activity.backup.get_by_version", "No Machine Workload backup versions found")
    else:
        v0 = machine_versions[0]
        await ctx.call(DOMAIN, "activity.backup.get_by_version", lambda: apm.activities.backup.get_by_version(v0))


async def _run_restore(ctx: SmokeContext, machine_workloads: list[MachineWorkload]) -> None:
    apm = ctx.apm

    ongoing_result = await ctx.call(
        DOMAIN, "activity.restore.list[ongoing]", lambda: apm.activities.restore.list(history=False, limit=100)
    )
    ongoing_activities, _total = ongoing_result if ongoing_result is not None else ([], 0)

    history_result = await ctx.call(
        DOMAIN, "activity.restore.list[history]", lambda: apm.activities.restore.list(history=True, limit=100)
    )
    restore_activities, _total = history_result if history_result is not None else ([], 0)
    ctx.data["restore_activities"] = restore_activities

    # ── not-found errors ──────────────────────────────────────────────────────

    await ctx.call_expect_not_found(DOMAIN, "activity.restore", "get",
        lambda: apm.activities.restore.get(_ZERO_UUID), "Activity", _ZERO_UUID)

    get_latest_rest_exc = await ctx.call_expect_error(
        DOMAIN, "activity.restore.get_latest_by_workload_name[not_found]",
        lambda: apm.activities.restore.get_latest_by_workload_name(_SENTINEL_NAME),
        ResourceNotFoundError,
    )
    ctx.check_exc_attr(DOMAIN, "activity.restore.check[get_latest_nf_resource_type]",
        get_latest_rest_exc, "resource_type", "Activity")
    ctx.check_exc_attr(DOMAIN, "activity.restore.check[get_latest_nf_resource_id]",
        get_latest_rest_exc, "resource_id", _SENTINEL_NAME)

    if not machine_workloads:
        for step, reason in (
            ("activity.restore.list[machine_workload_filter]", "No Machine Workloads found"),
            ("activity.restore.list[bogus_workload_filter]", "No Machine Workloads found"),
            ("activity.restore.check[bogus_workload_raises]", "No Machine Workloads found"),
        ):
            ctx.skip(DOMAIN, step, reason)
    else:
        w0 = machine_workloads[0]

        await ctx.call(
            DOMAIN, "activity.restore.list[machine_workload_filter]",
            lambda: apm.activities.restore.list(workload=w0, history=True, limit=10),
        )

        bogus = dataclasses.replace(w0, workload_id="bogus-workload-id-00000000")
        exc = await ctx.call_expect_error(
            DOMAIN, "activity.restore.list[bogus_workload_filter]",
            lambda: apm.activities.restore.list(workload=bogus, history=True, limit=10),
            ResourceNotFoundError,
            note="A bogus workload reference must raise ResourceNotFoundError for restore list.",
        )
        if isinstance(exc, ResourceNotFoundError):
            ctx.check(
                DOMAIN, "activity.restore.check[bogus_workload_raises]",
                exc.resource_type == "Workload" and exc.resource_id == bogus.workload_id,
            )
        else:
            ctx.check(
                DOMAIN, "activity.restore.check[bogus_workload_raises]", False,
            )

    if not restore_activities:
        ctx.skip(DOMAIN, "activity.restore.get_latest_by_workload_name", "No restore activity history found")
        ctx.skip(DOMAIN, "activity.restore.get[direct]", "No restore activity history found")
    else:
        _restored_name = restore_activities[0].workload_name
        await ctx.call(
            DOMAIN, "activity.restore.get_latest_by_workload_name",
            lambda: apm.activities.restore.get_latest_by_workload_name(_restored_name),
        )
        await ctx.call(
            DOMAIN, "activity.restore.get[direct]",
            lambda: apm.activities.restore.get(restore_activities[0].activity_id),
        )

    await _run_restore_cancel(ctx, ongoing_activities)


async def _run_restore_cancel(ctx: SmokeContext, ongoing_activities: list[RestoreActivity]) -> None:
    """Cancel an in-progress restore, if one exists at run time.

    The SDK has no call that triggers a restore, so this step relies on the tester starting
    one (e.g. from the APM web UI) shortly before the activity phase runs — see TEST_DATA.md.
    """
    apm = ctx.apm
    _steps = (
        "activity.restore.cancel[ongoing]",
        "activity.restore.list[post_cancel]",
        "activity.restore.check[cancel_settled]",
    )

    if not ongoing_activities:
        ctx.skip_remaining(
            DOMAIN, _steps,
            reason="No in-progress restore found — start a restore (e.g. from the APM web UI)"
            " before running the activity phase to exercise cancel; see TEST_DATA.md.",
        )
        return

    a0 = ongoing_activities[0]

    async def _cancel() -> dict[str, str]:
        await apm.activities.restore.cancel(a0)
        return {"canceled_activity_id": a0.activity_id}

    cancel_result = await ctx.call(
        DOMAIN, _steps[0], _cancel,
        expect_error=APIError,
        note="Cancels the first in-progress restore found via list(history=False). An APIError"
        " is accepted as expected: the restore may have completed between list and cancel.",
    )
    if cancel_result is None:
        ctx.skip_remaining(
            DOMAIN, _steps,
            reason="Cancel was rejected — the restore likely completed between list and cancel.",
        )
        return

    async def _wait_settled() -> dict[str, object]:
        last_status: str | None = None
        for attempt in range(_CANCEL_POLL_ATTEMPTS):
            items, _total = await apm.activities.restore.list(history=False, limit=100)
            target = next((a for a in items if a.activity_id == a0.activity_id), None)
            if target is None:
                return {"settled": True, "last_ongoing_status": last_status}
            last_status = target.status.value
            if target.status in _CANCEL_SETTLED_STATUSES:
                return {"settled": True, "last_ongoing_status": last_status}
            if attempt + 1 < _CANCEL_POLL_ATTEMPTS:
                await asyncio.sleep(_CANCEL_POLL_INTERVAL_SECONDS)
        return {"settled": False, "last_ongoing_status": last_status}

    settle_result = await ctx.call(
        DOMAIN, _steps[1], _wait_settled,
        note="Polls the ongoing list until the canceled activity disappears or reports a"
        " canceling/canceled status.",
    )
    ctx.check(
        DOMAIN, _steps[2],
        settle_result is not None and bool(settle_result.get("settled")),
        note="After cancel, the activity must leave the ongoing list or report"
        " canceling/canceled.",
    )

