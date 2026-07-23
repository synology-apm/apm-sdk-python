"""Unit tests for MachineWorkloadCollection: get/get_by_name/backup_now/cancel_backup/retire, list() filters."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections._shared import _build_location_info
from synology_apm.sdk.collections.machine import MachineWorkloadCollection, _parse_workload
from synology_apm.sdk.enums import (
    MachineWorkloadType,
    RetentionType,
    VerifyStatus,
    WorkloadCategory,
    WorkloadStatus,
)
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
    null_out,
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


async def test_get_by_name_does_not_match_workload_id() -> None:
    """get_by_name() should not match on workload_id; ID lookup goes through get()."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name(WORKLOAD_ID)

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)


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


@pytest.mark.parametrize("field_name", ["filter.planId", "filter.verifyStatus"])
async def test_list_omits_optional_filter_params_when_not_provided(field_name: str) -> None:
    """plan=None and verify_status=None must not add their corresponding filter param
    to the request."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list()

    params = mock_get.call_args[1]["params"]
    assert not any(k == field_name for k, _ in params)


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([WorkloadStatus.SUCCESS], [("filter.latestVersionResult", "VERSION_RESULT_SUCCESS")]),
        (
            [WorkloadStatus.FAILED, WorkloadStatus.PARTIAL],
            [
                ("filter.latestVersionResult", "VERSION_RESULT_FAILED"),
                ("filter.latestVersionResult", "VERSION_RESULT_PARTIAL"),
            ],
        ),
        ([WorkloadStatus.QUEUING], [("filter.jobStatus", "WAITING_TASK")]),
        ([WorkloadStatus.BACKING_UP], [("filter.jobStatus", "RUNNING")]),
        ([WorkloadStatus.DELETING], [("filter.jobStatus", "DELETING")]),
        ([WorkloadStatus.NO_BACKUPS], [("filter.latestVersionResult", "VERSION_RESULT_NONE")]),
    ],
)
async def test_list_status_filter_sends_expected_params(
    statuses: list[WorkloadStatus], expected: list[tuple[str, str]]
) -> None:
    """status maps QUEUING/BACKING_UP/DELETING to filter.jobStatus, others to filter.latestVersionResult."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list(status=statuses)

    params = mock_get.call_args[1]["params"]
    for pair in expected:
        assert pair in params


async def test_list_no_status_omits_status_params() -> None:
    """status=None must not add filter.jobStatus or filter.latestVersionResult to the request."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list()

    params = mock_get.call_args[1]["params"]
    assert not any(k in ("filter.jobStatus", "filter.latestVersionResult") for k, _ in params)


async def test_list_status_retired_raises_value_error() -> None:
    """status=[WorkloadStatus.RETIRED] is rejected; use is_retired=True instead."""
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with pytest.raises(ValueError, match="RETIRED"):
        await collection.list(status=[WorkloadStatus.RETIRED])


async def test_list_verify_status_filter_sends_repeated_verify_status() -> None:
    """verify_status=[FAILED, NOT_ENABLED] sends one filter.verifyStatus query param per value."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [], "total": 0}
        await collection.list(verify_status=[VerifyStatus.FAILED, VerifyStatus.NOT_ENABLED])

    params = mock_get.call_args[1]["params"]
    verify_status_values = [v for k, v in params if k == "filter.verifyStatus"]
    assert verify_status_values == ["VERIFY_FAILED", "VERIFY_NOT_ENABLED"]


# ── _parse_workload — null field handling ───────────────────────────────────


@pytest.mark.parametrize("null_paths", [
    (
        "namespace", "copyDataUsage", "planName",
        "spec.workloadName", "spec.workloadType", "spec.planRef.uid",
        "status.usage", "status.jobStatus", "status.latestVersionResult",
    ),
    ("namespace", "copyDataUsage", "planName", "spec", "status"),
], ids=["null_nested_fields", "null_spec_and_status"])
def test_parse_workload_survives_null_fields(null_paths: tuple[str, ...]) -> None:
    """_parse_workload must not crash when every touched field is JSON null; all falsy-typed
    fields fall back to their documented safe defaults, whether the null is a nested
    sub-field of a present spec/status or the spec/status container itself."""
    raw = null_out(SAMPLE_WORKLOAD, *null_paths)
    wl = _parse_workload(raw)

    assert wl.workload_id == WORKLOAD_ID
    assert wl.namespace == ""
    assert wl.name == ""
    assert wl.protected_data_bytes == 0
    assert wl.backup_copy_data_bytes == 0
    assert wl.workload_type == MachineWorkloadType.PC  # workloadType null -> "" -> unmapped -> PC default
    assert wl.plan.plan_id == ""
    assert wl.plan.name == ""
    assert wl.status == WorkloadStatus.NO_BACKUPS  # jobStatus null -> latestVersionResult fallback, also null


def test_build_location_info_survives_null_fields() -> None:
    """_build_location_info (used for both backup_server and backup_copy_destination) falls
    back to safe defaults when hostName is present but namespace/uid/destinationType/addr are
    JSON null, and returns None when hostName itself is JSON null (same as absent)."""
    server_info = null_out(
        SAMPLE_WORKLOAD["backupServerInfo"],
        "namespace", "uid", "destinationType", "addr",
    )
    loc = _build_location_info(server_info)
    assert loc is not None
    assert loc.identifier == ""  # namespace null -> "" -> falls back to uid, also null -> ""
    assert loc.is_remote_storage is False  # destinationType null -> "APPLIANCE" default
    assert loc.endpoint == ""

    none_info = null_out(SAMPLE_WORKLOAD["backupServerInfo"], "hostName")
    assert _build_location_info(none_info) is None


async def test_retire_batch_error_null_fields_fall_back_to_safe_defaults() -> None:
    """retire()'s failed-batch-entry handling falls back safely when the top-level "failed"
    key, or an entry's "error" key, is JSON null — _batch_errors_from_failed() and
    _raise_first_batch_error() (shared with M365) both handle the null case distinctly from
    the key being absent."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {"succeeded": {"namespaceWorkloadListMap": {}}, "failed": None}
        await collection.retire(SAMPLE_WL_OBJ, SAMPLE_RETIREMENT_PLAN)  # must not raise

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {
            "succeeded": {"namespaceWorkloadListMap": {}},
            "failed": {"entries": [{"error": None}]},
        }
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.retire(SAMPLE_WL_OBJ, SAMPLE_RETIREMENT_PLAN)

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_ID)
    assert exc_info.value.error_code is None
    assert exc_info.value.message == "Workload plan change failed"


# ── get_by_name() — is_retired filter params ────────────────────────────────


@pytest.mark.parametrize("is_retired,expected_filter", [
    (True, "PROTECT_STATUS_ARCHIVED"),
    (False, "PROTECT_STATUS_PROTECTED"),
])
async def test_get_by_name_sends_protect_status_filter_for_is_retired(is_retired: bool, expected_filter: str) -> None:
    """get_by_name(name, is_retired=...) appends the matching filter.protectStatus."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        await collection.get_by_name("CORP-PC-001", is_retired=is_retired)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("filter.protectStatus") == expected_filter


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
