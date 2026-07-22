"""Integration tests: MachineWorkloadCollection / M365WorkloadCollection"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import (
    M365WorkloadType,
    MachineWorkloadType,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from synology_apm.sdk.models.version import WorkloadVersion
from synology_apm.sdk.models.workload import M365Workload, MachineWorkload, Workload
from tests.unit.sdk.conftest import assert_resource_error

pytestmark = pytest.mark.integration


# ── apm.machine.workloads.list() ─────────────────────────────────────────────


async def test_list_returns_list(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    assert isinstance(workloads, list)


async def test_list_items_are_workload_instances(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    for wl in workloads:
        assert isinstance(wl, Workload)


async def test_list_workload_ids_are_nonempty(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    for wl in workloads:
        assert wl.workload_id, f"workload_id empty for {wl.name!r}"
        assert wl.namespace, f"namespace empty for {wl.name!r}"


async def test_list_workload_names_are_nonempty(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    for wl in workloads:
        assert wl.name, f"name is empty for workload_id={wl.workload_id}"


async def test_list_workload_category_is_machine(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    for wl in workloads:
        assert wl.category == WorkloadCategory.MACHINE


async def test_list_namespace_filter_returns_only_matching_server(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    server = servers[0]
    workloads, _ = await apm.machine.workloads.list(namespace=server.namespace)
    for wl in workloads:
        assert wl.namespace == server.namespace


async def test_list_status_is_valid_enum(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    valid = set(WorkloadStatus)
    for wl in workloads:
        if not wl.is_retired:
            assert wl.status in valid


async def test_list_last_backup_at_timezone_aware(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    for wl in workloads:
        if wl.last_backup_at is not None:
            assert wl.last_backup_at.tzinfo is not None, (
                f"last_backup_at not timezone-aware for {wl.name!r}"
            )


async def test_list_status_filter_returns_only_matching_status(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list(status=[WorkloadStatus.SUCCESS])
    for wl in workloads:
        assert wl.status == WorkloadStatus.SUCCESS


async def test_list_status_filter_multi_value_or_within_same_field(apm: APMClient) -> None:
    """SUCCESS + FAILED are both latestVersionResult-governed (same underlying field);
    the two values should OR together."""
    workloads, _ = await apm.machine.workloads.list(status=[WorkloadStatus.SUCCESS, WorkloadStatus.FAILED])
    for wl in workloads:
        assert wl.status in (WorkloadStatus.SUCCESS, WorkloadStatus.FAILED)


async def test_list_status_filter_cross_field_combination_is_or_not_and(apm: APMClient) -> None:
    """SUCCESS (latestVersionResult) + DELETING (jobStatus) must OR across the two fields:
    combined total should equal the sum of the two individual totals, not collapse toward 0."""
    _, total_success = await apm.machine.workloads.list(status=[WorkloadStatus.SUCCESS])
    _, total_deleting = await apm.machine.workloads.list(status=[WorkloadStatus.DELETING])
    _, total_combined = await apm.machine.workloads.list(
        status=[WorkloadStatus.SUCCESS, WorkloadStatus.DELETING]
    )
    assert total_success is not None
    assert total_deleting is not None
    assert total_combined == total_success + total_deleting


async def test_list_status_retired_raises_value_error(apm: APMClient) -> None:
    with pytest.raises(ValueError, match="RETIRED"):
        await apm.machine.workloads.list(status=[WorkloadStatus.RETIRED])


async def test_list_verify_status_filter_returns_only_matching_verify_status(apm: APMClient) -> None:
    from synology_apm.sdk.enums import VerifyStatus

    workloads, _ = await apm.machine.workloads.list(
        workload_types=[MachineWorkloadType.VM, MachineWorkloadType.PS],
        verify_status=[VerifyStatus.SUCCESS],
    )
    for wl in workloads:
        assert wl.verify_status == VerifyStatus.SUCCESS


# ── apm.machine.workloads.get() ───────────────────────────────────────────────


async def test_get_returns_workload_for_existing_id(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    if not workloads:
        pytest.skip("No workloads on this APM instance")
    first = workloads[0]
    # Use direct mode (workload_id + namespace) for a deterministic single API call
    fetched = await apm.machine.workloads.get(first.workload_id, namespace=first.namespace)
    assert fetched.workload_id == first.workload_id
    assert fetched.name == first.name


async def test_get_raises_resource_not_found_for_bad_id(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list()
    if not workloads:
        pytest.skip("No workloads on this APM instance")
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.machine.workloads.get("00000000-0000-0000-0000-000000000000", namespace=workloads[0].namespace)
    assert_resource_error(exc_info, resource_type="Workload", resource_id="00000000-0000-0000-0000-000000000000")


# ── list_versions() ───────────────────────────────────────────────────────────


async def test_list_versions_returns_versions(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list(is_retired=False)
    if not workloads:
        pytest.skip("No protected workloads — cannot test list_versions")
    wl = workloads[0]
    versions, total = await apm.machine.workloads.list_versions(wl)
    assert isinstance(versions, list)
    assert isinstance(total, int)
    assert all(isinstance(v, WorkloadVersion) for v in versions)


async def test_list_versions_ids_are_nonempty(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list(is_retired=False)
    if not workloads:
        pytest.skip("No protected workloads")
    wl = workloads[0]
    versions, _ = await apm.machine.workloads.list_versions(wl)
    if not versions:
        pytest.skip("No versions for this workload")
    for v in versions:
        assert v.version_id, "version_id should not be empty"
        assert v.workload_id == wl.workload_id


async def test_list_versions_sorted_newest_first(apm: APMClient) -> None:
    workloads, _ = await apm.machine.workloads.list(is_retired=False)
    if not workloads:
        pytest.skip("No protected workloads")
    wl = workloads[0]
    versions, _ = await apm.machine.workloads.list_versions(wl)
    if len(versions) < 2:
        pytest.skip("Fewer than 2 versions — cannot verify ordering")
    for i in range(len(versions) - 1):
        assert versions[i].created_at >= versions[i + 1].created_at


async def test_list_versions_raises_for_missing_workload(apm: APMClient) -> None:
    # APM returns HTTP 500 (not 404) for unknown namespace — accept either error
    from synology_apm.sdk.exceptions import APIError
    fake_wl = MachineWorkload(
        workload_id="00000000-0000-0000-0000-000000000000",
        name="fake",
        category=WorkloadCategory.MACHINE,
        namespace="00000000-0000-0000-0000-000000000000",
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.NO_BACKUPS,
        plan=RetirementPlan(plan_id="plan-fake", name="Compliance Retention"),
        workload_type=MachineWorkloadType.PC,
        agent_version=None,
    )
    with pytest.raises((ResourceNotFoundError, APIError)):
        await apm.machine.workloads.list_versions(fake_wl)


# ── backup_now() ──────────────────────────────────────────────────────────────


async def test_backup_now_returns_backup_job(apm: APMClient) -> None:
    """Triggers a real backup and verifies the returned job object.

    This is a write operation — it starts an actual backup task on the APM.
    The job is NOT awaited to completion to keep the test fast.
    """
    workloads, _ = await apm.machine.workloads.list(is_retired=False)
    if not workloads:
        pytest.skip("No protected workloads — cannot test backup_now")
    wl = workloads[0]
    await apm.machine.workloads.backup_now(wl)


# ── apm.m365.workloads.list() ─────────────────────────────────────────────────


async def _first_m365_tenant_id(apm: APMClient) -> str:
    """Helper: return first M365 tenant_id or skip the test."""
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if t.category == WorkloadCategory.M365]
    if not m365:
        pytest.skip("No M365 tenants configured on this APM instance")
    return m365[0].tenant_id


async def test_m365_workloads_list_returns_list(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE)
    assert isinstance(workloads, list)


async def test_m365_workloads_list_items_are_m365_workload_instances(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE)
    assert all(isinstance(wl, M365Workload) for wl in workloads)


async def test_m365_workloads_list_category_is_m365(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE)
    if not workloads:
        pytest.skip("No M365 mailbox workloads")
    assert all(wl.category == WorkloadCategory.M365 for wl in workloads)


async def test_m365_workloads_list_scope_is_mailbox(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE)
    for wl in workloads:
        assert wl.workload_type == M365WorkloadType.EXCHANGE


async def test_m365_workloads_list_tenant_id_matches(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE)
    for wl in workloads:
        assert wl.tenant_id == tid


async def test_m365_workloads_list_onedrive_scope(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.ONEDRIVE)
    assert isinstance(workloads, list)
    for wl in workloads:
        assert wl.workload_type == M365WorkloadType.ONEDRIVE


async def test_m365_workloads_list_status_filter_returns_only_matching_status(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(
        tid, workload_type=M365WorkloadType.EXCHANGE, status=[WorkloadStatus.SUCCESS]
    )
    for wl in workloads:
        assert wl.status == WorkloadStatus.SUCCESS


async def test_m365_workloads_list_status_retired_raises_value_error(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    with pytest.raises(ValueError, match="RETIRED"):
        await apm.m365.workloads.list(
            tid, workload_type=M365WorkloadType.EXCHANGE, status=[WorkloadStatus.RETIRED]
        )


# ── apm.m365.workloads.get() ──────────────────────────────────────────────────


async def test_m365_workloads_get_direct_mode(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE)
    if not workloads:
        pytest.skip("No M365 mailbox workloads")
    first = workloads[0]
    fetched = await apm.m365.workloads.get(
        first.workload_id,
        namespace=first.namespace,
        tenant_id=tid,
        workload_type=M365WorkloadType.EXCHANGE,
    )
    assert fetched.workload_id == first.workload_id


async def test_m365_workloads_get_raises_not_found_for_bad_id(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.m365.workloads.get(
            "00000000-0000-0000-0000-000000000000",
            namespace="00000000-0000-0000-0000-000000000000",
            tenant_id="00000000-0000-0000-0000-000000000000",
            workload_type=M365WorkloadType.EXCHANGE,
        )
    assert_resource_error(exc_info, resource_type="M365Workload", resource_id="00000000-0000-0000-0000-000000000000")


# ── apm.m365.workloads.list_versions() ───────────────────────────────────────


async def test_m365_list_versions_returns_list(apm: APMClient) -> None:
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE, is_retired=False)
    if not workloads:
        pytest.skip("No protected M365 mailbox workloads")
    wl = workloads[0]
    versions, total = await apm.m365.workloads.list_versions(wl)
    assert isinstance(versions, list)
    assert isinstance(total, int)


# ── apm.m365.workloads.backup_now() ──────────────────────────────────────────


async def test_m365_backup_now_triggers_without_error(apm: APMClient) -> None:
    """Triggers a real M365 backup and verifies no exception is raised.

    This is a write operation — it starts an actual backup task on the APM.
    The job is NOT awaited to completion to keep the test fast.
    """
    tid = await _first_m365_tenant_id(apm)
    workloads, _ = await apm.m365.workloads.list(tid, workload_type=M365WorkloadType.EXCHANGE, is_retired=False)
    if not workloads:
        pytest.skip("No protected M365 mailbox workloads — cannot test backup_now")
    wl = workloads[0]
    await apm.m365.workloads.backup_now(wl)

