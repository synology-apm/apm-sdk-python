"""Unit tests for M365WorkloadCollection action methods (backup_now/cancel_backup/retire/change_plan/delete).

See test_m365_workloads.py for list/get/get_by_name/list_versions/get_latest_version, and
test_m365_workloads_facade.py for M365PlanCollection (list/get/get_by_name).
"""
from __future__ import annotations

import re
from dataclasses import replace

import pytest
from yarl import URL

from synology_apm.sdk.collections.m365 import M365WorkloadCollection
from synology_apm.sdk.enums import M365WorkloadType, RetentionType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.exceptions import InvalidOperationError
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session, make_session, request_json

WORKLOAD_POST_URL = f"{BASE_URL}/api/v1/workload/m365_workload"
APPLY_URL = f"{BASE_URL}/api/v1/workload/m365_workload/batch/change_plan"
BACKUP_URL = f"{BASE_URL}/api/v1/workload/m365_workload/batch/backup"
CANCEL_URL = f"{BASE_URL}/api/v1/workload/m365_workload/batch/cancel"

TENANT_ID = "tenant-aaa-001"
WORKLOAD_UID = "wl-m365-uid-001"
NAMESPACE = "ns-m365-001"
PLAN_ID = "0c8f033b-fb57-4f46-9a9d-85e9d21c08ab"
ARCHIVE_PLAN_ID = "cc39711f-deb9-40fa-b6c4-27ca82958d3c"

SAMPLE_M365_WL_OBJ = M365Workload(
    workload_id=WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
    namespace=NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id=PLAN_ID, name="Daily Backup (saas)", category=WorkloadCategory.M365),
    workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
)

SAMPLE_PROTECTION_PLAN = ProtectionPlan(
    plan_id=PLAN_ID,
    name="Daily Backup (saas)",
    category=WorkloadCategory.M365,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=None,
    ),
    workload_count=1,
)

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id=ARCHIVE_PLAN_ID,
    name="Compliance Retention",
    description="",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=False),
    workload_count=1,
)

# ── M365WorkloadCollection.backup_now() ──────────────────────────────────


async def test_backup_now_posts_correct_body() -> None:
    """backup_now(uid, tid, ns) POSTs to batch/backup without internal get()."""
    async with connected_session() as (session, m):

        m.post(BACKUP_URL, payload={"success": True, "errors": []})

        collection = M365WorkloadCollection(session)
        await collection.backup_now(SAMPLE_M365_WL_OBJ)
        await session.disconnect()

    post_key = ("POST", URL(BACKUP_URL))
    assert post_key in m.requests
    body = request_json(m, post_key)
    assert body["tenantId"] == TENANT_ID
    assert body["nsUidPairs"] == [{"namespace": NAMESPACE, "uid": WORKLOAD_UID}]


async def test_backup_now_does_not_call_workload_list() -> None:
    """backup_now() must NOT call the workload list endpoint — caller owns resolution."""
    async with connected_session() as (session, m):

        m.post(BACKUP_URL, payload={"success": True})

        collection = M365WorkloadCollection(session)
        await collection.backup_now(SAMPLE_M365_WL_OBJ)
        await session.disconnect()

    workload_list_key = ("POST", URL(WORKLOAD_POST_URL))
    assert workload_list_key not in m.requests


# ── M365WorkloadCollection.cancel_backup() ───────────────────────────────


async def test_cancel_backup_posts_correct_body() -> None:
    """cancel_backup(uid, tid, ns) POSTs to batch/cancel without internal get()."""
    async with connected_session() as (session, m):

        m.post(CANCEL_URL, payload={"success": True, "errors": []})

        collection = M365WorkloadCollection(session)
        await collection.cancel_backup(SAMPLE_M365_WL_OBJ)
        await session.disconnect()

    post_key = ("POST", URL(CANCEL_URL))
    assert post_key in m.requests
    body = request_json(m, post_key)
    assert body["nsUidPairs"] == [{"namespace": NAMESPACE, "uid": WORKLOAD_UID}]


async def test_m365_backup_now_raises_for_retired_workload() -> None:
    """backup_now() raises InvalidOperationError when workload.is_retired is True."""
    from unittest.mock import AsyncMock, patch

    retired_wl = M365Workload(
        workload_id=WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-x", name="Retirement Plan"),
        workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
    )
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.backup_now(retired_wl)
        mock_post.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)


async def test_m365_cancel_backup_raises_for_retired_workload() -> None:
    """cancel_backup() raises InvalidOperationError when workload.is_retired is True."""
    from unittest.mock import AsyncMock, patch

    retired_wl = M365Workload(
        workload_id=WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-x", name="Retirement Plan"),
        workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
    )
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.cancel_backup(retired_wl)
        mock_post.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)


# ── M365WorkloadCollection.retire() ──────────────────────────────────────


async def test_retire_sends_change_plan_with_provided_archive_plan_id() -> None:
    """retire(workload, plan) PUTs change_plan without internal get()."""
    async with connected_session() as (session, m):

        m.put(APPLY_URL, payload={})

        collection = M365WorkloadCollection(session)
        await collection.retire(SAMPLE_M365_WL_OBJ, SAMPLE_RETIREMENT_PLAN)
        await session.disconnect()

    put_key = ("PUT", URL(APPLY_URL))
    assert put_key in m.requests
    body = request_json(m, put_key)
    assert body["planId"] == ARCHIVE_PLAN_ID
    assert body["planType"] == "ARCHIVE"
    assert body["tenantId"] == TENANT_ID
    assert body["nsUidPairs"] == [{"namespace": NAMESPACE, "uid": WORKLOAD_UID}]
    assert body["isFromUnmanagedWorkload"] is False


async def test_retire_does_not_call_archive_plan_api() -> None:
    """retire() must NOT auto-fetch /api/v1/plan/archive_plan."""
    archive_plan_url = f"{BASE_URL}/api/v1/plan/archive_plan"
    async with connected_session() as (session, m):

        m.put(APPLY_URL, payload={})

        collection = M365WorkloadCollection(session)
        await collection.retire(SAMPLE_M365_WL_OBJ, SAMPLE_RETIREMENT_PLAN)
        await session.disconnect()

    archive_key = ("GET", URL(archive_plan_url))
    assert archive_key not in m.requests


async def test_retire_raises_invalid_operation_for_already_retired_workload() -> None:
    """retire() should raise InvalidOperationError when workload.is_retired is True."""
    from unittest.mock import AsyncMock, patch

    retired_wl = M365Workload(
        workload_id=WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-x", name="Retirement Plan"),
        workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
    )
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.retire(retired_wl, SAMPLE_RETIREMENT_PLAN)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)


# ── M365WorkloadCollection.change_plan() ─────────────────────────────────


async def test_change_plan_with_protection_plan_on_active_workload() -> None:
    """change_plan(workload, ProtectionPlan) PUTs batch/change_plan with planType=BACKUP."""
    async with connected_session() as (session, m):

        m.put(APPLY_URL, payload={})

        collection = M365WorkloadCollection(session)
        await collection.change_plan(SAMPLE_M365_WL_OBJ, SAMPLE_PROTECTION_PLAN)
        await session.disconnect()

    put_key = ("PUT", URL(APPLY_URL))
    assert put_key in m.requests
    body = request_json(m, put_key)
    assert body["planId"] == PLAN_ID
    assert body["planType"] == "BACKUP"
    assert body["tenantId"] == TENANT_ID
    assert body["nsUidPairs"] == [{"namespace": NAMESPACE, "uid": WORKLOAD_UID}]
    assert body["isFromUnmanagedWorkload"] is False


async def test_change_plan_with_retirement_plan_on_retired_workload() -> None:
    """change_plan(workload, RetirementPlan) PUTs batch/change_plan with planType=ARCHIVE."""
    retired_wl = M365Workload(
        workload_id=WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-old", name="30-Day Retention"),
        workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
    )
    async with connected_session() as (session, m):

        m.put(APPLY_URL, payload={})

        collection = M365WorkloadCollection(session)
        await collection.change_plan(retired_wl, SAMPLE_RETIREMENT_PLAN)
        await session.disconnect()

    put_key = ("PUT", URL(APPLY_URL))
    assert put_key in m.requests
    body = request_json(m, put_key)
    assert body["planId"] == ARCHIVE_PLAN_ID
    assert body["planType"] == "ARCHIVE"
    assert body["tenantId"] == TENANT_ID
    assert body["nsUidPairs"] == [{"namespace": NAMESPACE, "uid": WORKLOAD_UID}]


async def test_change_plan_raises_invalid_operation_for_protection_plan_on_retired_workload() -> None:
    """change_plan(retired_workload, ProtectionPlan) should raise InvalidOperationError."""
    from unittest.mock import AsyncMock, patch

    retired_wl = M365Workload(
        workload_id=WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
        namespace=NAMESPACE, last_backup_at=None, is_retired=True,
        protected_data_bytes=0, status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-old", name="30-Day Retention"),
        workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
    )
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(retired_wl, SAMPLE_PROTECTION_PLAN)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)


async def test_change_plan_raises_invalid_operation_for_retirement_plan_on_active_workload() -> None:
    """change_plan(active_workload, RetirementPlan) should raise InvalidOperationError."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(SAMPLE_M365_WL_OBJ, SAMPLE_RETIREMENT_PLAN)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)


async def test_change_plan_raises_invalid_operation_for_category_mismatch() -> None:
    """change_plan(m365_workload, machine_protection_plan) should raise InvalidOperationError."""
    from unittest.mock import AsyncMock, patch

    machine_plan = replace(SAMPLE_PROTECTION_PLAN, category=WorkloadCategory.MACHINE)
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(SAMPLE_M365_WL_OBJ, machine_plan)
        mock_put.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)


async def test_change_plan_raises_invalid_operation_on_response_errors() -> None:
    """change_plan() raises InvalidOperationError when the response errors array is non-empty."""
    payload = {
        "success": False,
        "errors": [{"message": "workload is initializing", "errorCode": 7018}],
        "allFailedSameReason": True,
        "successDetail": {"topSuccessItemNames": [], "successCount": 0, "successUidGroups": []},
    }
    async with connected_session() as (session, m):

        m.put(APPLY_URL, payload=payload)
        collection = M365WorkloadCollection(session)
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.change_plan(SAMPLE_M365_WL_OBJ, SAMPLE_PROTECTION_PLAN)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)
    assert exc_info.value.error_code == 7018


async def test_retire_raises_invalid_operation_on_response_errors() -> None:
    """retire() raises InvalidOperationError when the response errors array is non-empty."""
    payload = {
        "success": False,
        "errors": [{"message": "workload is initializing", "errorCode": 7018}],
        "allFailedSameReason": True,
        "successDetail": {"topSuccessItemNames": [], "successCount": 0, "successUidGroups": []},
    }
    async with connected_session() as (session, m):

        m.put(APPLY_URL, payload=payload)
        collection = M365WorkloadCollection(session)
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.retire(SAMPLE_M365_WL_OBJ, SAMPLE_RETIREMENT_PLAN)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)
    assert exc_info.value.error_code == 7018


# ── M365WorkloadCollection.delete() ──────────────────────────────────────


DELETE_URL = f"{BASE_URL}/api/v1/workload/m365_workload/batch"


async def test_delete_sends_correct_request() -> None:
    """delete() sends DELETE to m365_workload/batch with tenantId, isFromUnmanagedWorkload=False, nsUidPairs."""
    payload = {
        "success": True,
        "errors": [],
        "allFailedSameReason": False,
        "successDetail": {"topSuccessItemNames": [], "successCount": 1, "successUidGroups": []},
    }
    async with connected_session() as (session, m):

        m.delete(re.compile(rf"{re.escape(BASE_URL)}/api/v1/workload/m365_workload/batch"), payload=payload)
        collection = M365WorkloadCollection(session)
        await collection.delete(SAMPLE_M365_WL_OBJ)
        await session.disconnect()

    del_key = ("DELETE", URL(DELETE_URL))
    body = request_json(m, del_key)
    assert body["tenantId"] == TENANT_ID
    assert body["isFromUnmanagedWorkload"] is False
    assert body["nsUidPairs"] == [{"namespace": NAMESPACE, "uid": WORKLOAD_UID}]


async def test_delete_raises_on_errors() -> None:
    """delete() raises InvalidOperationError when the response errors array is non-empty."""
    payload = {"success": False, "errors": [{"message": "not allowed", "errorCode": 9999}]}
    async with connected_session() as (session, m):

        m.delete(re.compile(rf"{re.escape(BASE_URL)}/api/v1/workload/m365_workload/batch"), payload=payload)
        collection = M365WorkloadCollection(session)
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.delete(SAMPLE_M365_WL_OBJ)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=WORKLOAD_UID)
    assert exc_info.value.error_code == 9999


