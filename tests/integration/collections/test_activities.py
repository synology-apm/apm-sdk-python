"""Integration tests: BackupActivityCollection / RestoreActivityCollection"""
from __future__ import annotations

import pytest

from synology_apm.sdk import (
    Activity,
    APMClient,
    BackupActivityStatus,
    MachineWorkload,
    MachineWorkloadType,
    ProtectionPlan,
    RestoreActivityStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from tests.unit.sdk.conftest import assert_resource_error

# Synthetic workload reference -- guaranteed not to exist on any APM server.
_NONEXISTENT_WORKLOAD = MachineWorkload(
    workload_id="00000000-0000-0000-0000-000000000000",
    name="nonexistent-workload",
    category=WorkloadCategory.MACHINE,
    namespace="00000000-0000-0000-0000-000000000000",
    last_backup_at=None,
    is_retired=False,
    protected_data_bytes=0,
    status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="", name="", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC,
    agent_version=None,
)

pytestmark = pytest.mark.integration


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_returns_list(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list()
    assert isinstance(activities, list)


async def test_list_items_are_activity_instances(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list()
    for act in activities:
        assert isinstance(act, Activity)


async def test_list_activity_ids_are_nonempty(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list()
    for act in activities:
        assert act.activity_id, f"activity_id empty for execution_id={act.execution_id}"


async def test_list_started_at_is_timezone_aware(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list()
    for act in activities:
        assert act.started_at.tzinfo is not None, (
            f"started_at is not timezone-aware for activity {act.activity_id}"
        )


async def test_list_status_is_valid_enum(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list()
    valid = set(BackupActivityStatus)
    for act in activities:
        assert act.status in valid


async def test_list_workload_type_is_valid_enum(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list()
    valid = set(WorkloadCategory)
    for act in activities:
        assert act.category in valid


async def test_list_completed_activities_have_finished_at(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list()
    for act in activities:
        if act.status == BackupActivityStatus.SUCCESS:
            assert act.finished_at is not None, (
                f"COMPLETED activity {act.activity_id} should have finished_at"
            )


async def test_list_filter_by_completed_status(apm: APMClient) -> None:
    completed, _ = await apm.activities.backup.list(status=[BackupActivityStatus.SUCCESS])
    assert all(act.status == BackupActivityStatus.SUCCESS for act in completed)


async def test_list_filter_by_machine_workload_type(apm: APMClient) -> None:
    machine_acts, _ = await apm.activities.backup.list(
        machine_types=[
            MachineWorkloadType.PC,
            MachineWorkloadType.PS,
            MachineWorkloadType.VM,
            MachineWorkloadType.FS,
        ]
    )
    assert all(act.category == WorkloadCategory.MACHINE for act in machine_acts)


async def test_list_with_limit_respects_bound(apm: APMClient) -> None:
    activities, _ = await apm.activities.backup.list(limit=5)
    assert len(activities) <= 5


async def test_list_since_filter_excludes_older(apm: APMClient) -> None:
    # history=True: the RECENT (ongoing-only) view rarely has 2+ entries on a
    # quiet test server, but completed activity history does.
    all_acts, _ = await apm.activities.backup.list(limit=50, history=True)
    if len(all_acts) < 2:
        pytest.skip("Fewer than 2 activities — cannot test since filter")
    # Use the second-newest as the cutoff
    sorted_acts = sorted(all_acts, key=lambda a: a.started_at, reverse=True)
    cutoff = sorted_acts[1].started_at
    filtered, _ = await apm.activities.backup.list(since=cutoff, limit=50, history=True)
    assert all(act.started_at >= cutoff for act in filtered)


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_raises_resource_not_found(apm: APMClient) -> None:
    """get() is intentionally unimplemented — requires executionId + workloadUid."""
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.activities.backup.get("ABE_1")
    assert_resource_error(exc_info, resource_type="Activity", resource_id="ABE_1")


# ── namespace-filtered activities ──────────────────────────────────────────────────


async def test_backup_activities_namespace_filter_returns_list(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    server = servers[0]
    server_acts, _ = await apm.activities.backup.list(namespace=[server.namespace])
    assert isinstance(server_acts, list)


async def test_backup_activities_namespace_filter_matches_server_namespace(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    server = servers[0]
    acts, _ = await apm.activities.backup.list(namespace=[server.namespace])
    for act in acts:
        assert act.namespace == server.namespace, (
            f"Activity namespace {act.namespace!r} != server.namespace {server.namespace!r}"
        )


# ── workload-filtered activities ────────────────────────────────────────────


async def test_backup_activities_workload_filter_matches_workload(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list(is_retired=False, limit=10)
    for wl in workloads:
        versions, _ = await apm.machine.workloads.list_versions(wl, limit=1)
        if versions:
            acts, _ = await apm.activities.backup.list(workload=wl, history=True)
            assert acts
            for act in acts:
                assert (act.workload_id, act.workload_namespace) == (wl.workload_id, wl.namespace)
            return
    pytest.skip("No workload with backup versions available")


async def test_backup_activities_workload_filter_nonexistent_returns_empty(apm: APMClient) -> None:
    acts, total = await apm.activities.backup.list(workload=_NONEXISTENT_WORKLOAD)
    assert acts == []
    assert total == 0


# ── get_by_version() ──────────────────────────────────────────────────────────


async def test_get_by_version_returns_activity(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list(is_retired=False, limit=10)
    for wl in workloads:
        versions, _ = await apm.machine.workloads.list_versions(wl, limit=1)
        if versions:
            fetched = await apm.activities.backup.get_by_version(versions[0])
            assert fetched.execution_id == versions[0].execution_id
            return
    pytest.skip("No workload with backup versions available")


async def test_get_by_version_includes_log_entries(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list(is_retired=False, limit=10)
    for wl in workloads:
        versions, _ = await apm.machine.workloads.list_versions(wl, limit=1)
        if versions:
            fetched = await apm.activities.backup.get_by_version(versions[0])
            assert fetched.log_entries is not None
            return
    pytest.skip("No workload with backup versions available")


# ── RestoreActivityCollection ──────────────────────────────────────────────────


async def test_restore_list_returns_list(apm: APMClient) -> None:
    activities, _ = await apm.activities.restore.list()
    assert isinstance(activities, list)


async def test_restore_list_items_are_activity_instances(apm: APMClient) -> None:
    activities, _ = await apm.activities.restore.list()
    for act in activities:
        assert isinstance(act, Activity)


async def test_restore_list_status_is_valid_enum(apm: APMClient) -> None:
    activities, _ = await apm.activities.restore.list()
    valid = set(RestoreActivityStatus)
    for act in activities:
        assert act.status in valid


async def test_restore_list_with_limit_respects_bound(apm: APMClient) -> None:
    activities, _ = await apm.activities.restore.list(limit=5)
    assert len(activities) <= 5


async def test_restore_get_raises_not_found_for_bad_id(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.activities.restore.get("__nonexistent_activity_id__")
    assert_resource_error(exc_info, resource_type="Activity", resource_id="__nonexistent_activity_id__")


async def test_restore_get_returns_activity_with_logs(apm: APMClient) -> None:
    # history=True: completed restores show up in activity history, not the
    # RECENT (ongoing-only) view.
    activities, _ = await apm.activities.restore.list(
        status=[RestoreActivityStatus.SUCCESS], limit=5, history=True
    )
    if not activities:
        pytest.skip("No completed restore activities available")
    act = activities[0]
    fetched = await apm.activities.restore.get(act.activity_id)
    assert fetched.activity_id == act.activity_id
    assert fetched.log_entries is not None


# ── workload-filtered activities ────────────────────────────────────────────


async def test_restore_activities_workload_filter_matches_workload(apm: APMClient) -> None:
    restore_acts, _ = await apm.activities.restore.list(limit=50, history=True)
    if not restore_acts:
        pytest.skip("No restore activities available")
    workloads, _ = await apm.machine.workloads.list(is_retired=False, limit=500)
    by_id = {w.workload_id: w for w in workloads}
    for restore_act in restore_acts:
        wl = by_id.get(restore_act.workload_id)
        if wl is not None:
            acts, _ = await apm.activities.restore.list(workload=wl, history=True)
            assert acts
            for act in acts:
                assert (act.workload_id, act.workload_namespace) == (wl.workload_id, wl.namespace)
            return
    pytest.skip("No restore activity matches a current machine workload")


async def test_restore_activities_workload_filter_nonexistent_raises_not_found(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.activities.restore.list(workload=_NONEXISTENT_WORKLOAD)
    assert_resource_error(exc_info, resource_type="Workload", resource_id=_NONEXISTENT_WORKLOAD.workload_id)
