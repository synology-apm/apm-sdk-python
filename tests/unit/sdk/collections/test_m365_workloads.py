"""Unit tests for M365WorkloadCollection (list/get/get_by_name/list_versions/get_latest_version/backup_now/cancel_backup/retire/change_plan/delete) and M365PlanCollection (list/get/get_by_name)."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections.m365 import M365WorkloadCollection
from synology_apm.sdk.collections.protection_plans import M365PlanCollection
from synology_apm.sdk.enums import (
    M365WorkloadType,
    RetentionType,
    ScheduleFrequency,
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
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session, make_session

WORKLOAD_POST_URL = f"{BASE_URL}/api/v1/workload/m365_workload"
PLANS_URL = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=M365"
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

SAMPLE_M365_WORKLOAD = {
    "workloadType": "USER_EXCHANGE",
    "uid": WORKLOAD_UID,
    "namespace": "ns-m365-001",
    "planId": PLAN_ID,
    "planName": "Daily Backup (saas)",
    "planType": "BACKUP",
    "lastBackupTime": "0",
    "backupUsage": "0",
    "copyUsage": "52428800",
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
    "entityMeta": {
        "spec": {
            "tenantId": TENANT_ID,
            "userInfo": {
                "email": "alice@contoso.com",
                "userName": "Alice",
            },
        }
    },
}

SAMPLE_M365_PLAN = {
    "id": PLAN_ID,
    "spec": {
        "name": "Daily Backup (saas)",
        "serviceType": "M365",
        "retention": {"keepDays": 30},
        "configM365": {
            "schedule": {
                "repeatType": "DAILY",
                "runHour": 2,
                "runMin": 30,
            }
        },
        "backupCopy": {"enabled": False, "destination": ""},
    },
    "protectedWorkloadCount": 5,
    "unprotectedWorkloadCount": 2,
}

EMPTY_M365_RESPONSE: dict[str, Any] = {"m365Workloads": []}


# ── M365WorkloadCollection.list() ─────────────────────────────────────────


async def test_list_requires_tenant_id_and_does_not_call_tenants_endpoint() -> None:
    """list(tenant_id, workload_type) must NOT call /api/v1/portal/tenants."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert len(workloads) == 1
    assert isinstance(workloads[0], M365Workload)
    tenants_key = ("GET", URL(f"{BASE_URL}/api/v1/portal/tenants"))
    assert tenants_key not in m.requests


async def test_list_queries_single_scope_when_scope_given() -> None:
    """list(tenant_id, scope=MAILBOX) should make exactly 1 POST request."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload=EMPTY_M365_RESPONSE)

        collection = M365WorkloadCollection(session)
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    post_key = ("POST", URL(WORKLOAD_POST_URL))
    assert len(m.requests[post_key]) == 1
    body = m.requests[post_key][0].kwargs["json"]
    assert body["filter"]["m365WorkloadFilter"]["m365WorkloadType"] == "USER_EXCHANGE"


async def test_list_post_body_includes_required_fields() -> None:
    """list() POST body must include primKey=tenant_id and m365WorkloadFilter."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload=EMPTY_M365_RESPONSE, repeat=True)

        collection = M365WorkloadCollection(session)
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.ONEDRIVE)
        await session.disconnect()

    post_key = ("POST", URL(WORKLOAD_POST_URL))
    body = m.requests[post_key][0].kwargs["json"]
    flt = body["filter"]
    assert flt["primKey"] == TENANT_ID
    assert "m365WorkloadFilter" in flt
    assert flt["m365WorkloadFilter"]["m365WorkloadType"] == "USER_DRIVE"


async def test_list_namespace_resolves_backup_server_id_and_filters_server_side() -> None:
    """list(namespace=...) looks up backup_server first to get backup_server_id, then passes it via filter.backupServerUids."""
    from unittest.mock import AsyncMock, patch

    BS_NAMESPACE = "ns-m365-001"
    BS_ID = "bs-id-abc123"
    SERVERS_RESPONSE = {
        "backupServers": [
            {"id": BS_ID, "namespace": BS_NAMESPACE, "spec": {}, "status": {}},
        ]
    }
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = SERVERS_RESPONSE
        mock_post.return_value = {"m365Workloads": [SAMPLE_M365_WORKLOAD]}

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, namespace=BS_NAMESPACE)

    # backupServerUids contains the resolved backup_server_id (not namespace)
    posted_filter = mock_post.call_args[1]["json"]["filter"]
    assert posted_filter.get("backupServerUids") == [BS_ID]
    assert len(workloads) == 1


async def test_list_parses_workload_category() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert workloads[0].category == WorkloadCategory.M365


async def test_list_parses_workload_fields() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    wl = workloads[0]
    assert wl.workload_id == WORKLOAD_UID
    assert wl.namespace == "ns-m365-001"
    assert wl.tenant_id == TENANT_ID
    assert wl.workload_type == M365WorkloadType.EXCHANGE
    assert wl.is_retired is False
    assert isinstance(wl.plan, ProtectionPlan)
    assert wl.plan.plan_id == PLAN_ID
    assert wl.plan.name == "Daily Backup (saas)"
    assert wl.plan.category == WorkloadCategory.M365


async def test_list_parses_archive_plan_type_as_retirement_plan() -> None:
    """A workload whose planType is ARCHIVE parses plan as a RetirementPlan."""
    archived_workload = {
        **SAMPLE_M365_WORKLOAD,
        "planId": ARCHIVE_PLAN_ID,
        "planName": "Compliance Retention",
        "planType": "ARCHIVE",
    }
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [archived_workload]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=True)
        await session.disconnect()

    wl = workloads[0]
    assert isinstance(wl.plan, RetirementPlan)
    assert wl.plan.plan_id == ARCHIVE_PLAN_ID
    assert wl.plan.name == "Compliance Retention"


async def test_list_parses_backup_server() -> None:
    """backup_server should be correctly parsed from backupServerInfo."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    loc = workloads[0].backup_server
    assert loc is not None
    assert loc.name == "apm-server-01"
    assert loc.identifier == "ns-server-001"
    assert loc.is_remote_storage is False


async def test_list_backup_server_none_when_missing() -> None:
    """When backupServerInfo is absent or hostName is empty, backup_server should be None."""
    raw = {**SAMPLE_M365_WORKLOAD}
    raw.pop("backupServerInfo", None)
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [raw]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert workloads[0].backup_server is None


async def test_list_backup_copy_destination_none_when_empty() -> None:
    """When backupCopyServerInfo hostName is empty, backup_copy_destination should be None."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert workloads[0].backup_copy_destination is None


async def test_list_parses_backup_copy_data_bytes() -> None:
    """copyUsage from the response should be parsed into backup_copy_data_bytes."""
    async with connected_session() as (session, m):

        raw = {**SAMPLE_M365_WORKLOAD, "copyUsage": "209715200"}
        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [raw]})

        collection = M365WorkloadCollection(session)
        workloads, _ = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert workloads[0].backup_copy_data_bytes == 209715200


async def test_list_backup_copy_data_bytes_defaults_to_zero_when_absent() -> None:
    """backup_copy_data_bytes should be 0 when copyUsage is absent from the response."""
    async with connected_session() as (session, m):

        raw = {k: v for k, v in SAMPLE_M365_WORKLOAD.items() if k != "copyUsage"}
        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [raw]})

        collection = M365WorkloadCollection(session)
        workloads, _ = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert workloads[0].backup_copy_data_bytes == 0


async def test_list_skips_unknown_workload_type() -> None:
    """Workloads with an unrecognised workloadType should be silently dropped."""
    unknown = {**SAMPLE_M365_WORKLOAD, "workloadType": "UNKNOWN_TYPE", "uid": "unk-uid"}
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD, unknown]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert len(workloads) == 1
    assert workloads[0].workload_id == WORKLOAD_UID


# ── M365WorkloadCollection.get() ──────────────────────────────────────────


async def test_get_uses_nsuidpair() -> None:
    """get(uid, namespace, tenant_id=tid) sends nsUidPair in filter body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_M365_WORKLOAD]}
        wl = await collection.get(WORKLOAD_UID, NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    assert mock_post.call_count == 1
    body = mock_post.call_args[1]["json"]["filter"]
    assert body["primKey"] == TENANT_ID
    assert body["nsUidPair"] == {"namespace": NAMESPACE, "uid": WORKLOAD_UID}
    assert wl.workload_id == WORKLOAD_UID


async def test_get_raises_not_found() -> None:
    """get(uid, namespace, tenant_id=tid) raises ResourceNotFoundError when nsUidPair returns empty."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("bad-uid", NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    assert_resource_error(exc_info, resource_type="M365Workload", resource_id="bad-uid")


async def test_get_raises_not_found_for_http_404() -> None:
    """get() populates the resource fields when the lookup answers HTTP 404 (observed live behavior)."""
    async with connected_session() as (session, m):
        m.post(WORKLOAD_POST_URL, status=404)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await M365WorkloadCollection(session).get(
                "bad-uid", NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE
            )
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="M365Workload", resource_id="bad-uid")
    assert exc_info.value.error_code == 404


# ── M365WorkloadCollection.get_by_name() ──────────────────────────────────


async def test_get_by_name_uses_keyword_only() -> None:
    """get_by_name(name, tid) uses keyword search — does NOT send nsUidPair."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_M365_WORKLOAD]}
        wl = await collection.get_by_name("Alice", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    assert mock_post.call_count == 1
    body = mock_post.call_args[1]["json"]["filter"]
    assert body["keyword"] == "Alice"
    assert "nsUidPair" not in body
    assert wl.workload_id == WORKLOAD_UID


async def test_get_by_name_matches_display_name() -> None:
    """get_by_name("Alice") finds workload by exact name match in keyword results."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name("Alice", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert wl.workload_id == WORKLOAD_UID
    assert wl.name == "Alice"


async def test_get_by_name_matches_email() -> None:
    """get_by_name("alice@contoso.com") matches on user_principal_name."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name("alice@contoso.com", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert wl.workload_id == WORKLOAD_UID


async def test_get_by_name_match_is_case_insensitive() -> None:
    """Name match ignores case."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name("ALICE", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert wl.workload_id == WORKLOAD_UID


async def test_get_by_name_raises_not_found_when_keyword_returns_empty() -> None:
    """get_by_name("no-such-name") raises ResourceNotFoundError when keyword search returns nothing."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload=EMPTY_M365_RESPONSE)

        collection = M365WorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("no-such-name", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="M365Workload", resource_id="no-such-name")


async def test_get_by_name_finds_exact_match_among_partials() -> None:
    """get_by_name() returns the first exact match when keyword hits multiple results, without raising an error."""
    workload_b = {**SAMPLE_M365_WORKLOAD, "uid": "wl-m365-uid-002"}
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD, workload_b]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name("Alice", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert wl.workload_id == WORKLOAD_UID


async def test_get_by_name_does_not_match_workload_id() -> None:
    """get_by_name() should not match on workload_id; ID lookup goes through get()."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name(WORKLOAD_UID, TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="M365Workload", resource_id=WORKLOAD_UID)


# ── M365WorkloadCollection.list(keyword=) ────────────────────────────────


async def test_list_not_retired_sends_plantype_backup() -> None:
    """list(is_retired=False) sends planType=BACKUP server-side."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=False)

    body = mock_post.call_args[1]["json"]["filter"]
    assert body["planType"] == "BACKUP"


async def test_list_retired_sends_plantype_archive() -> None:
    """list(is_retired=True) sends planType=ARCHIVE server-side."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=True)

    body = mock_post.call_args[1]["json"]["filter"]
    assert body["planType"] == "ARCHIVE"


async def test_list_default_sends_plantype_backup() -> None:
    """list() without is_retired defaults to planType=BACKUP in filter body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    body = mock_post.call_args[1]["json"]["filter"]
    assert body.get("planType") == "BACKUP"


async def test_get_by_name_default_sends_plantype_backup() -> None:
    """get_by_name() default sends planType=BACKUP (same as list() default)."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        try:
            await collection.get_by_name("unknown-workload", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        except Exception:
            pass

    body = mock_post.call_args[1]["json"]["filter"]
    assert body.get("planType") == "BACKUP"


async def test_list_keyword_is_passed_in_filter_body() -> None:
    """list(keyword=...) should include keyword in the POST body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, keyword="testuser4")

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert body["keyword"] == "testuser4"


async def test_list_without_keyword_omits_keyword_field() -> None:
    """list() without keyword should NOT include keyword in the POST body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert "keyword" not in body


async def test_list_plan_is_passed_as_plan_uids_in_filter_body() -> None:
    """list(plan=[plan1, plan2]) should include planUids (one per plan's plan_id) in the POST body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(
            TENANT_ID, workload_type=M365WorkloadType.EXCHANGE,
            plan=[SAMPLE_PROTECTION_PLAN, SAMPLE_RETIREMENT_PLAN],
        )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert body["planUids"] == [SAMPLE_PROTECTION_PLAN.plan_id, SAMPLE_RETIREMENT_PLAN.plan_id]


async def test_list_without_plan_omits_plan_uids_field() -> None:
    """list() without plan should NOT include planUids in the POST body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert "planUids" not in body


async def test_list_status_is_passed_as_backup_status_in_filter_body() -> None:
    """list(status=[FAILED, PARTIAL]) should include backupStatus (raw API values) in the POST body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(
            TENANT_ID, workload_type=M365WorkloadType.EXCHANGE,
            status=[WorkloadStatus.FAILED, WorkloadStatus.PARTIAL],
        )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert body["backupStatus"] == ["ERROR", "WARNING"]


async def test_list_without_status_omits_backup_status_field() -> None:
    """list() without status should NOT include backupStatus in the POST body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert "backupStatus" not in body


async def test_list_status_retired_raises_value_error() -> None:
    """status=[WorkloadStatus.RETIRED] is rejected; use is_retired=True instead."""
    session = make_session()
    collection = M365WorkloadCollection(session)

    with pytest.raises(ValueError, match="RETIRED"):
        await collection.list(
            TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, status=[WorkloadStatus.RETIRED]
        )


# ── M365WorkloadCollection.list_versions() ────────────────────────────────


async def test_m365_list_versions_filters_by_since() -> None:
    """list_versions(since=...) sends createStartTimestamp as a server-side query param."""
    from unittest.mock import AsyncMock, patch

    cutoff = datetime.fromtimestamp(1700050000, tz=UTC)
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [
                {"id": "ver-new", "spec": {"backupType": "FULL_BACKUP", "executionId": "M_A", "locked": False},
                 "status": {"startTime": "1700100000", "transferredSize": "0"}},
            ],
            "total": 1,
        }
        versions, total = await collection.list_versions(SAMPLE_M365_WL_OBJ, since=cutoff)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("createStartTimestamp") == "1700050000"
    assert "createEndTimestamp" not in params
    assert total == 1
    assert len(versions) == 1
    assert versions[0].version_id == "ver-new"


async def test_m365_list_versions_filters_by_until() -> None:
    """list_versions(until=...) sends createEndTimestamp as a server-side query param."""
    from unittest.mock import AsyncMock, patch

    cutoff = datetime.fromtimestamp(1700050000, tz=UTC)
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [
                {"id": "ver-old", "spec": {"backupType": "FULL_BACKUP", "executionId": "M_B", "locked": False},
                 "status": {"startTime": "1700000000", "transferredSize": "0"}},
            ],
            "total": 1,
        }
        versions, total = await collection.list_versions(SAMPLE_M365_WL_OBJ, until=cutoff)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("createEndTimestamp") == "1700050000"
    assert "createStartTimestamp" not in params
    assert total == 1
    assert len(versions) == 1
    assert versions[0].version_id == "ver-old"


# ── M365WorkloadCollection.get_latest_version() ──────────────────────────


async def test_m365_get_latest_version_returns_first_result() -> None:
    """get_latest_version() calls list_versions(limit=1) and returns the first item."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)
    sample_ver = {"id": "ver-m365-001", "spec": {"backupType": "FULL_BACKUP", "executionId": "M_1", "locked": False},
                  "status": {"startTime": "1700100000", "transferredSize": "0"}}

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [sample_ver], "total": 1}
        v = await collection.get_latest_version(SAMPLE_M365_WL_OBJ)

    assert v.version_id == "ver-m365-001"
    params = dict(mock_get.call_args[1]["params"])
    assert params["limit"] == 1


async def test_m365_get_latest_version_raises_when_no_versions() -> None:
    """get_latest_version() raises ResourceNotFoundError when list_versions returns empty."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [], "total": 0}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_latest_version(SAMPLE_M365_WL_OBJ)

    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id=WORKLOAD_UID)


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
    body = m.requests[post_key][0].kwargs["json"]
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
    body = m.requests[post_key][0].kwargs["json"]
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
    body = m.requests[put_key][0].kwargs["json"]
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
    body = m.requests[put_key][0].kwargs["json"]
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
    body = m.requests[put_key][0].kwargs["json"]
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
    body = m.requests[del_key][0].kwargs["json"]
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


# ── M365PlanCollection.list() ─────────────────────────────────────────────


async def test_m365_plan_list_parses_fields() -> None:
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        result, total = await collection.list()
        await session.disconnect()

    assert len(result) == 1
    plan = result[0]
    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup (saas)"
    assert plan.category == WorkloadCategory.M365
    assert plan.workload_count == 7  # 5 + 2
    assert plan.policy is not None
    assert plan.policy.retention.retention_type == RetentionType.KEEP_DAYS
    assert plan.policy.retention.days == 30


async def test_m365_plan_list_parses_schedule() -> None:
    """M365 list endpoint returns schedule (in configM365.schedule)."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        result, total = await collection.list()
        await session.disconnect()

    plan = result[0]
    assert plan.policy is not None
    assert plan.policy.schedule is not None
    assert plan.policy.schedule.frequency == ScheduleFrequency.DAILY
    assert plan.policy.schedule.start_time is not None
    assert plan.policy.schedule.start_time.hour == 2
    assert plan.policy.schedule.start_time.minute == 30


async def test_m365_plan_list_name_filter_passes_keyword() -> None:
    """list(name_contains=...) should append keyword param to the request."""
    async with connected_session() as (session, m):

        filtered_url = f"{PLANS_URL}&keyword=Daily"
        m.get(filtered_url, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        result, total = await collection.list(name_contains="Daily")
        await session.disconnect()

    assert len(result) == 1


# ── M365PlanCollection.get() ─────────────────────────────────────────────


async def test_m365_plan_get_calls_direct_endpoint() -> None:
    async with connected_session() as (session, m):

        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_M365_PLAN)
        collection = M365PlanCollection(session)
        plan = await collection.get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup (saas)"


# ── M365PlanCollection.get_by_name() ─────────────────────────────────────


async def test_m365_plan_get_by_name_resolves_via_list() -> None:
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=Daily+Backup+%2528saas%2529&limit=100&offset=0&serviceType=M365"
        m.get(keyword_url, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        plan = await collection.get_by_name("Daily Backup (saas)")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID


async def test_m365_plan_get_by_nonexistent_name_raises_not_found() -> None:
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=No+Such+Plan&limit=100&offset=0&serviceType=M365"
        m.get(keyword_url, payload={"plans": []})
        collection = M365PlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("No Such Plan")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="No Such Plan")






async def test_list_parses_deleting_backup_status() -> None:
    """A workload being deleted parses as DELETING with no item count."""
    deleting_workload = {**SAMPLE_M365_WORKLOAD, "backupStatus": "DELETING"}
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [deleting_workload]})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    wl = workloads[0]
    assert wl.status == WorkloadStatus.DELETING
    assert wl.items_backed_up is None


async def test_list_unknown_namespace_raises_not_found() -> None:
    """list(namespace=...) raises ResourceNotFoundError when no backup server matches."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"backupServers": [], "total": 0}

        collection = M365WorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, namespace="no-such-ns")
        mock_post.assert_not_called()

    assert_resource_error(exc_info, resource_type="BackupServer", resource_id="no-such-ns")
