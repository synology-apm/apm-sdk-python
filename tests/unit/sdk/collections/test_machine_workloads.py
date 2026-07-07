"""Unit tests for MachineWorkloadCollection: get/get_by_name/backup_now/cancel_backup/retire, list() filters."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections.machine import MachineWorkloadCollection
from synology_apm.sdk.enums import MachineWorkloadType, RetentionType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.exceptions import InvalidOperationError, ResourceNotFoundError
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
    make_session,
)

WORKLOAD_ID = "wl-id-001"
NAMESPACE = "ns-001"

SAMPLE_WL_OBJ = MachineWorkload(
    workload_id=WORKLOAD_ID, name="CORP-PC-001", category=WorkloadCategory.MACHINE,
    namespace=NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-x", name="Test Plan", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC, agent_version=None,
)

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="archive-plan-001",
    name="Compliance Retention",
    description="",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=False),
    workload_count=1,
)

SAMPLE_PROTECTION_PLAN = ProtectionPlan(
    plan_id="protection-plan-001",
    name="Daily Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=None,
    ),
    workload_count=2,
)

LIST_PC_URL = (
    f"{BASE_URL}/api/v2/workload/device_workload"
    "?filter.isFilterBasedOnNonWorkloadType=true&filter.workloadType=PC"
    "&filter.limit=500&filter.offset=0"
    "&filter.protectStatus=PROTECT_STATUS_PROTECTED"
)
DIRECT_URL = f"{BASE_URL}/api/v1/workload/device_workload/{WORKLOAD_ID}?namespace={NAMESPACE}"
CANCEL_URL = f"{BASE_URL}/api/v1/workload/device_workload/cancel"
BACKUP_URL = f"{BASE_URL}/api/v1/workload/device_workload/backup"
RETIRE_URL = f"{BASE_URL}/api/v1/workload/device_workloads/plan"

SAMPLE_WORKLOAD: dict[str, Any] = {
    "id": WORKLOAD_ID,
    "namespace": NAMESPACE,
    "spec": {
        "workloadType": "PC",
        "workloadName": "CORP-PC-001",
        "workloadUid": "wl-uid-001",
        "protectStatus": "PROTECT_STATUS_PROTECTED",
        "planRef": {"kind": "BackupPlan", "uid": "plan-uid-001", "namespace": ""},
    },
    "status": {
        "lastBackupTime": "1776734685",
        "usage": "524288000",
    },
    "backupServerInfo": {
        "uid": "b49110b0-b7c5-55a8-a613-23ebc800d144",
        "hostName": "apm-server-01",
        "addr": "192.0.2.1",
        "namespace": "ns-server-001",
        "destinationType": "APPLIANCE",
    },
    "backupCopyServerInfo": {
        "uid": "",
        "hostName": "",
        "addr": "",
        "namespace": "",
        "destinationType": "APPLIANCE",
        "vaultName": "",
    },
    "copyDataUsage": "104857600",
    "planName": "Test Plan",
}


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_calls_single_endpoint() -> None:
    """get(id, namespace=ns) calls GET /api/v1/workload/device_workload/{id}?namespace={ns}."""
    async with connected_session() as (session, m):

        m.get(DIRECT_URL, payload=SAMPLE_WORKLOAD)

        collection = MachineWorkloadCollection(session)
        wl = await collection.get(WORKLOAD_ID, namespace=NAMESPACE)
        await session.disconnect()

    assert wl.workload_id == WORKLOAD_ID
    assert wl.namespace == NAMESPACE
    assert wl.name == "CORP-PC-001"
    assert wl.workload_type == MachineWorkloadType.PC
    assert wl.last_backup_at == datetime.fromtimestamp(1776734685, tz=UTC)
    assert wl.protected_data_bytes == 524288000
    assert isinstance(wl.plan, ProtectionPlan)
    assert wl.plan.plan_id == "plan-uid-001"
    assert wl.plan.name == "Test Plan"
    assert wl.plan.category == WorkloadCategory.MACHINE


async def test_get_parses_archive_plan_ref_as_retirement_plan() -> None:
    """A workload whose planRef.kind is ArchivePlan parses plan as a RetirementPlan."""
    archived_workload = {
        **SAMPLE_WORKLOAD,
        "spec": {
            **SAMPLE_WORKLOAD["spec"],
            "planRef": {"kind": "ArchivePlan", "uid": "archive-uid-001", "namespace": ""},
        },
        "planName": "Compliance Retention",
    }
    async with connected_session() as (session, m):

        m.get(DIRECT_URL, payload=archived_workload)

        collection = MachineWorkloadCollection(session)
        wl = await collection.get(WORKLOAD_ID, namespace=NAMESPACE)
        await session.disconnect()

    assert isinstance(wl.plan, RetirementPlan)
    assert wl.plan.plan_id == "archive-uid-001"
    assert wl.plan.name == "Compliance Retention"


async def test_get_does_not_call_list() -> None:
    """get(id, namespace=ns) must NOT call the list endpoint."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = SAMPLE_WORKLOAD
        await collection.get(WORKLOAD_ID, namespace=NAMESPACE)

    assert mock_get.call_count == 1
    called_path = mock_get.call_args[0][0]
    assert called_path == f"/api/v1/workload/device_workload/{WORKLOAD_ID}"


async def test_get_raises_not_found_for_missing() -> None:
    """get(id, namespace=ns) raises ResourceNotFoundError when server returns empty."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {}  # no "id" key
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("bad-id", namespace=NAMESPACE)

    assert_resource_error(exc_info, resource_type="Workload", resource_id="bad-id")


async def test_get_raises_not_found_for_http_404() -> None:
    """get() populates the resource fields when the lookup answers HTTP 404 (observed live behavior)."""
    import re

    async with connected_session() as (session, m):
        m.get(re.compile(r".*/api/v1/workload/device_workload/bad-id.*"), status=404)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await MachineWorkloadCollection(session).get("bad-id", namespace=NAMESPACE)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Workload", resource_id="bad-id")


# ── get_by_name() ──────────────────────────────────────────────────────────


async def test_get_by_name_uses_keyword() -> None:
    """get_by_name(name) sends filter.keyword and does not call the by-ID endpoint."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        wl = await collection.get_by_name("CORP-PC-001")

    assert mock_get.call_count == 1
    params = dict(mock_get.call_args[1]["params"])  # list-of-tuples → dict
    assert params["filter.keyword"] == "CORP-PC-001"
    assert wl.workload_id == WORKLOAD_ID


async def test_get_by_name_raises_not_found() -> None:
    """get_by_name("no-match") raises ResourceNotFoundError when keyword returns nothing."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("no-match")

    assert_resource_error(exc_info, resource_type="Workload", resource_id="no-match")


async def test_get_by_name_uses_limit_100() -> None:
    """get_by_name(name) sends filter.limit=100 to API."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD]}
        await collection.get_by_name("CORP-PC-001")

    params = dict(mock_get.call_args[1]["params"])
    assert params["filter.limit"] == 100


async def test_get_by_name_finds_exact_match_among_partials() -> None:
    """get_by_name() returns the exact match when keyword hits multiple results, without raising an error."""
    workload_b = {**SAMPLE_WORKLOAD, "id": "wl-id-002", "spec": {**SAMPLE_WORKLOAD["spec"], "workloadName": "CORP-PC-001-PRO"}}
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD, workload_b]}
        wl = await collection.get_by_name("CORP-PC-001")

    assert wl.workload_id == WORKLOAD_ID


# ── backup_now() ──────────────────────────────────────────────────────────


async def test_backup_now_posts_correct_body() -> None:
    """backup_now(id, ns) POSTs workloadRefs without internal get()."""
    async with connected_session() as (session, m):

        m.post(BACKUP_URL, payload={"failed": {"entries": []}})

        collection = MachineWorkloadCollection(session)
        await collection.backup_now(SAMPLE_WL_OBJ)
        await session.disconnect()

    backup_key = ("POST", URL(BACKUP_URL))
    assert backup_key in m.requests
    body = m.requests[backup_key][0].kwargs["json"]
    assert body == {"workloadRefs": [{"uid": WORKLOAD_ID, "namespace": NAMESPACE}]}


async def test_backup_now_does_not_call_list() -> None:
    """backup_now() must NOT call the workload list endpoint."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await collection.backup_now(SAMPLE_WL_OBJ)

    mock_get.assert_not_called()


# ── cancel_backup() ───────────────────────────────────────────────────────


async def test_cancel_backup_posts_correct_body() -> None:
    """cancel_backup(id, ns) POSTs workloadRefs without internal get()."""
    async with connected_session() as (session, m):

        m.post(CANCEL_URL, payload={})

        collection = MachineWorkloadCollection(session)
        await collection.cancel_backup(SAMPLE_WL_OBJ)
        await session.disconnect()

    cancel_key = ("POST", URL(CANCEL_URL))
    assert cancel_key in m.requests
    body = m.requests[cancel_key][0].kwargs["json"]
    assert body == {"workloadRefs": [{"uid": WORKLOAD_ID, "namespace": NAMESPACE}]}


async def test_cancel_backup_does_not_call_list() -> None:
    """cancel_backup() must NOT call the workload list endpoint."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await collection.cancel_backup(SAMPLE_WL_OBJ)

    mock_get.assert_not_called()


@pytest.mark.parametrize("method_name", ["backup_now", "cancel_backup"])
async def test_raises_for_retired_workload(method_name: str) -> None:
    """backup_now() and cancel_backup() raise InvalidOperationError when workload.is_retired is True."""
    from unittest.mock import AsyncMock, patch

    retired_wl = MachineWorkload(
        workload_id=WORKLOAD_ID, name="CORP-PC-001", category=WorkloadCategory.MACHINE,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-x", name="Retirement Plan"),
        workload_type=MachineWorkloadType.PC, agent_version=None,
    )
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        with pytest.raises(InvalidOperationError) as exc_info:
            await getattr(collection, method_name)(retired_wl)
        mock_post.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)


# ── retire() ─────────────────────────────────────────────────────────────


async def test_retire_sends_correct_put_body() -> None:
    """retire(workload, plan) PUTs nsWorkloadMap without internal get()."""
    async with connected_session() as (session, m):

        m.put(RETIRE_URL, payload={})

        collection = MachineWorkloadCollection(session)
        await collection.retire(SAMPLE_WL_OBJ, SAMPLE_RETIREMENT_PLAN)
        await session.disconnect()

    put_key = ("PUT", URL(RETIRE_URL))
    assert put_key in m.requests
    body = m.requests[put_key][0].kwargs["json"]
    assert body == {
        "nsWorkloadMap": {NAMESPACE: {"ids": [WORKLOAD_ID]}},
        "planId": "archive-plan-001",
    }


async def test_retire_does_not_call_list_or_archive_plan() -> None:
    """retire() must NOT call list or archive_plan endpoints."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await collection.retire(SAMPLE_WL_OBJ, SAMPLE_RETIREMENT_PLAN)

    mock_get.assert_not_called()


async def test_retire_raises_invalid_operation_for_already_retired_workload() -> None:
    """retire() should raise InvalidOperationError when workload.is_retired is True."""
    from unittest.mock import AsyncMock, patch

    retired_wl = MachineWorkload(
        workload_id=WORKLOAD_ID, name="CORP-PC-001", category=WorkloadCategory.MACHINE,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-x", name="Retirement Plan"),
        workload_type=MachineWorkloadType.PC, agent_version=None,
    )
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.retire(retired_wl, SAMPLE_RETIREMENT_PLAN)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)


async def test_retire_raises_invalid_operation_on_failed_batch_entry() -> None:
    """retire() raises InvalidOperationError when APM rejects the workload in the batch response."""
    async with connected_session() as (session, m):

        m.put(RETIRE_URL, payload={
            "succeeded": {"namespaceWorkloadListMap": {}},
            "failed": {"entries": [{"error": {
                "category": "abc",
                "errorCode": 7018,
                "message": "workload is initializing",
            }}]},
        })

        collection = MachineWorkloadCollection(session)
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.retire(SAMPLE_WL_OBJ, SAMPLE_RETIREMENT_PLAN)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)
    assert exc_info.value.error_code == 7018


# ── change_plan() ──────────────────────────────────────────────────────────


async def test_change_plan_with_protection_plan_on_active_workload() -> None:
    """change_plan(workload, ProtectionPlan) PUTs nsWorkloadMap with the protection plan's ID."""
    async with connected_session() as (session, m):

        m.put(RETIRE_URL, payload={})

        collection = MachineWorkloadCollection(session)
        await collection.change_plan(SAMPLE_WL_OBJ, SAMPLE_PROTECTION_PLAN)
        await session.disconnect()

    put_key = ("PUT", URL(RETIRE_URL))
    assert put_key in m.requests
    body = m.requests[put_key][0].kwargs["json"]
    assert body == {
        "nsWorkloadMap": {NAMESPACE: {"ids": [WORKLOAD_ID]}},
        "planId": "protection-plan-001",
    }


async def test_change_plan_with_retirement_plan_on_retired_workload() -> None:
    """change_plan(workload, RetirementPlan) PUTs nsWorkloadMap with the retirement plan's ID."""
    retired_wl = MachineWorkload(
        workload_id=WORKLOAD_ID, name="CORP-PC-001", category=WorkloadCategory.MACHINE,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-old", name="30-Day Retention"),
        workload_type=MachineWorkloadType.PC, agent_version=None,
    )
    async with connected_session() as (session, m):

        m.put(RETIRE_URL, payload={})

        collection = MachineWorkloadCollection(session)
        await collection.change_plan(retired_wl, SAMPLE_RETIREMENT_PLAN)
        await session.disconnect()

    put_key = ("PUT", URL(RETIRE_URL))
    assert put_key in m.requests
    body = m.requests[put_key][0].kwargs["json"]
    assert body == {
        "nsWorkloadMap": {NAMESPACE: {"ids": [WORKLOAD_ID]}},
        "planId": "archive-plan-001",
    }


async def test_change_plan_raises_invalid_operation_on_failed_batch_entry() -> None:
    """change_plan() raises InvalidOperationError when APM rejects the workload in the batch response."""
    async with connected_session() as (session, m):

        m.put(RETIRE_URL, payload={
            "succeeded": {"namespaceWorkloadListMap": {}},
            "failed": {"entries": [{"error": {
                "category": "abc",
                "errorCode": 7018,
                "message": "workload is initializing",
            }}]},
        })

        collection = MachineWorkloadCollection(session)
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(SAMPLE_WL_OBJ, SAMPLE_PROTECTION_PLAN)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)
    assert exc_info.value.error_code == 7018


async def test_change_plan_raises_invalid_operation_for_protection_plan_on_retired_workload() -> None:
    """change_plan(retired_workload, ProtectionPlan) should raise InvalidOperationError."""
    from unittest.mock import AsyncMock, patch

    retired_wl = MachineWorkload(
        workload_id=WORKLOAD_ID, name="CORP-PC-001", category=WorkloadCategory.MACHINE,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-old", name="30-Day Retention"),
        workload_type=MachineWorkloadType.PC, agent_version=None,
    )
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(retired_wl, SAMPLE_PROTECTION_PLAN)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)


async def test_change_plan_raises_invalid_operation_for_retirement_plan_on_active_workload() -> None:
    """change_plan(active_workload, RetirementPlan) should raise InvalidOperationError."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(SAMPLE_WL_OBJ, SAMPLE_RETIREMENT_PLAN)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)


async def test_change_plan_raises_invalid_operation_for_category_mismatch() -> None:
    """change_plan(machine_workload, m365_protection_plan) should raise InvalidOperationError."""
    from unittest.mock import AsyncMock, patch

    m365_plan = replace(SAMPLE_PROTECTION_PLAN, category=WorkloadCategory.M365)
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(SAMPLE_WL_OBJ, m365_plan)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)


# ── list() edge cases ─────────────────────────────────────────────────────


async def test_list_with_machine_type_adds_workload_type_param() -> None:
    """list(workload_types=[PC]) sends filter.workloadType=PC alongside isFilterBasedOnNonWorkloadType."""
    async with connected_session() as (session, m):

        m.get(LIST_PC_URL, payload={"workloads": [SAMPLE_WORKLOAD], "total": 1})

        collection = MachineWorkloadCollection(session)
        workloads, total = await collection.list(workload_types=[MachineWorkloadType.PC])
        await session.disconnect()

    assert len(workloads) == 1


async def test_list_with_name_contains_adds_keyword_param() -> None:
    """list(name_contains=...) sends filter.keyword to API."""
    async with connected_session() as (session, m):

        url_with_keyword = (
            f"{BASE_URL}/api/v2/workload/device_workload"
            "?filter.isFilterBasedOnNonWorkloadType=true"
            "&filter.limit=500&filter.offset=0&filter.keyword=CORP-PC"
            "&filter.protectStatus=PROTECT_STATUS_PROTECTED"
        )
        m.get(url_with_keyword, payload={"workloads": [SAMPLE_WORKLOAD], "total": 1})

        collection = MachineWorkloadCollection(session)
        workloads, total = await collection.list(name_contains="CORP-PC")
        await session.disconnect()

    assert len(workloads) == 1


async def test_list_not_retired_sends_protect_status_protected_server_side() -> None:
    """list(is_retired=False) sends filter.protectStatus=PROTECT_STATUS_PROTECTED."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list(is_retired=False)

    params = mock_get.call_args[1]["params"]
    assert ("filter.protectStatus", "PROTECT_STATUS_PROTECTED") in params
    # must NOT also include UNMANAGED/ARCHIVED
    assert not any(k == "filter.protectStatus" and v != "PROTECT_STATUS_PROTECTED"
                   for k, v in params)


async def test_list_retired_sends_protect_status_archived_server_side() -> None:
    """list(is_retired=True) sends PROTECT_STATUS_ARCHIVED only (UNMANAGED is not included)."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list(is_retired=True)

    params = mock_get.call_args[1]["params"]
    protectStatus_values = [v for k, v in params if k == "filter.protectStatus"]
    assert "PROTECT_STATUS_ARCHIVED" in protectStatus_values
    assert "PROTECT_STATUS_UNMANAGED" not in protectStatus_values
    assert "PROTECT_STATUS_PROTECTED" not in protectStatus_values


async def test_list_default_sends_protect_status_protected() -> None:
    """list() without is_retired defaults to filter.protectStatus=PROTECT_STATUS_PROTECTED."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list()

    params = mock_get.call_args[1]["params"]
    assert ("filter.protectStatus", "PROTECT_STATUS_PROTECTED") in params


async def test_list_filter_by_namespace() -> None:
    """namespace sends filter.namespace as server-side param."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        workloads, total = await collection.list(namespace=NAMESPACE)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("filter.namespace") == NAMESPACE
    assert len(workloads) == 1
    assert workloads[0].namespace == NAMESPACE


async def test_list_filter_by_hypervisor_id() -> None:
    """hypervisor_id sends filter.filterVm.inventoryId as server-side param."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    hv_id = "978eabd4-e332-459f-a8e0-35a0aa312118"

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        await collection.list(hypervisor_id=hv_id)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("filter.filterVm.inventoryId") == hv_id


async def test_list_no_hypervisor_id_omits_param() -> None:
    """hypervisor_id=None must not add filter.filterVm.inventoryId to the request."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list()

    params = dict(mock_get.call_args[1]["params"])
    assert "filter.filterVm.inventoryId" not in params


async def test_list_filter_by_plan_sends_repeated_plan_id() -> None:
    """plan=[plan1, plan2] sends one filter.planId query param per plan's plan_id."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list(plan=[SAMPLE_PROTECTION_PLAN, SAMPLE_RETIREMENT_PLAN])

    params = mock_get.call_args[1]["params"]
    plan_id_values = [v for k, v in params if k == "filter.planId"]
    assert plan_id_values == [SAMPLE_PROTECTION_PLAN.plan_id, SAMPLE_RETIREMENT_PLAN.plan_id]


async def test_list_no_plan_omits_plan_id_param() -> None:
    """plan=None must not add filter.planId to the request."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list()

    params = mock_get.call_args[1]["params"]
    assert not any(k == "filter.planId" for k, _ in params)


# ── get_by_name() — is_retired filter params ────────────────────────────────


async def test_get_by_name_with_is_retired_true_sends_archived_filter() -> None:
    """get_by_name(name, is_retired=True) appends filter.protectStatus=PROTECT_STATUS_ARCHIVED."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        await collection.get_by_name("CORP-PC-001", is_retired=True)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("filter.protectStatus") == "PROTECT_STATUS_ARCHIVED"


async def test_get_by_name_with_is_retired_false_sends_protected_filter() -> None:
    """get_by_name(name, is_retired=False) appends filter.protectStatus=PROTECT_STATUS_PROTECTED."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        await collection.get_by_name("CORP-PC-001", is_retired=False)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("filter.protectStatus") == "PROTECT_STATUS_PROTECTED"


async def test_get_by_name_default_sends_protected_filter() -> None:
    """get_by_name(name) without is_retired defaults to filter.protectStatus=PROTECT_STATUS_PROTECTED."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        await collection.get_by_name("CORP-PC-001")

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("filter.protectStatus") == "PROTECT_STATUS_PROTECTED"
