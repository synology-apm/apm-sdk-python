"""Unit tests for M365WorkloadCollection read paths (list/get/get_by_name/list_versions/get_latest_version).

See test_m365_workloads_actions.py for backup_now/cancel_backup/retire/change_plan/delete, and
test_m365_workloads_facade.py for M365PlanCollection (list/get/get_by_name).
"""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections.m365 import M365WorkloadCollection, _parse_m365_workload
from synology_apm.sdk.enums import (
    M365WorkloadType,
    RetentionType,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
    make_session,
    null_out,
    request_json,
)

WORKLOAD_POST_URL = f"{BASE_URL}/api/v1/workload/m365_workload"

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
    body = request_json(m, post_key)
    assert body["filter"]["m365WorkloadFilter"]["m365WorkloadType"] == "USER_EXCHANGE"


async def test_list_post_body_includes_required_fields() -> None:
    """list() POST body must include primKey=tenant_id and m365WorkloadFilter."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload=EMPTY_M365_RESPONSE, repeat=True)

        collection = M365WorkloadCollection(session)
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.ONEDRIVE)
        await session.disconnect()

    post_key = ("POST", URL(WORKLOAD_POST_URL))
    body = request_json(m, post_key)
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


@pytest.mark.parametrize("name", ["alice@contoso.com", "ALICE"], ids=["matches_email", "case_insensitive"])
async def test_get_by_name_matches_user_principal_name(name: str) -> None:
    """get_by_name() matches on user_principal_name, case-insensitively."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_M365_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name(name, TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
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


@pytest.mark.parametrize("is_retired,expected_plan_type", [
    (False, "BACKUP"),
    (True, "ARCHIVE"),
])
async def test_list_sends_plantype_for_is_retired(is_retired: bool, expected_plan_type: str) -> None:
    """list(is_retired=...) sends the matching planType server-side."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=is_retired)

    body = mock_post.call_args[1]["json"]["filter"]
    assert body["planType"] == expected_plan_type


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
        with contextlib.suppress(Exception):
            await collection.get_by_name("unknown-workload", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

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


@pytest.mark.parametrize("field_name", ["keyword", "planUids", "backupStatus"])
async def test_list_omits_optional_filter_fields_when_not_provided(field_name: str) -> None:
    """list() without keyword/plan/status should NOT include the corresponding optional
    field in the POST body's filter."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": []}
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert field_name not in body


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


async def test_list_namespace_survives_null_backup_servers_key() -> None:
    """_resolve_namespace_to_server_id: backupServers present as JSON null (key present,
    value null — distinct from an absent key) paginates as an empty page, same as an
    absent key, and raises ResourceNotFoundError rather than crashing."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"backupServers": None, "total": 0}

        collection = M365WorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, namespace="no-such-ns")
        mock_post.assert_not_called()

    assert_resource_error(exc_info, resource_type="BackupServer", resource_id="no-such-ns")


async def test_list_namespace_survives_null_backup_server_id() -> None:
    """_resolve_namespace_to_server_id: a matching backup server whose "id" is JSON null
    (key present, value null) resolves to an empty string instead of crashing. That empty
    string is falsy, so the caller's `if backup_server_id:` guard drops the
    backupServerUids constraint entirely rather than posting a bogus [""] filter."""
    from unittest.mock import AsyncMock, patch

    BS_NAMESPACE = "ns-m365-002"
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {
            "backupServers": [{"id": None, "namespace": BS_NAMESPACE, "spec": {}, "status": {}}],
        }
        mock_post.return_value = {"m365Workloads": [SAMPLE_M365_WORKLOAD]}

        collection = M365WorkloadCollection(session)
        await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, namespace=BS_NAMESPACE)

    posted_filter = mock_post.call_args[1]["json"]["filter"]
    assert "backupServerUids" not in posted_filter


# ── _parse_m365_workload: null vs. absent JSON field handling ─────────────
#
# Called directly (not through list()) per the project's null-field testing convention —
# it's a standalone parser function, so there's no need to drive it through a mocked HTTP
# round-trip. See test_m365_integration.py for the SHAREPOINT/TEAMS/GROUP_EXCHANGE
# per-workload-type info-block variants.


def test_parse_m365_workload_returns_none_for_null_workload_type() -> None:
    """workloadType JSON null (key present, value null — distinct from an absent key)
    must not crash _parse_m365_workload(); the workload is dropped (returns None), same
    as an unrecognized workloadType (see test_list_skips_unknown_workload_type)."""
    raw = null_out(SAMPLE_M365_WORKLOAD, "workloadType")

    assert _parse_m365_workload(raw) is None


def test_parse_m365_workload_survives_null_entity_meta() -> None:
    """entityMeta JSON null must not crash _parse_m365_workload(); tenant_id and the
    EXCHANGE/ONEDRIVE/CHAT user_principal_name/name both fall back to empty strings."""
    raw = null_out(SAMPLE_M365_WORKLOAD, "entityMeta")

    wl = _parse_m365_workload(raw)

    assert wl is not None
    assert wl.tenant_id == ""
    assert isinstance(wl.info, M365UserInfo)
    assert wl.info.user_principal_name == ""
    assert wl.name == ""


def test_parse_m365_workload_survives_null_common_fields() -> None:
    """Every top-level/common field _parse_m365_workload() touches with `or` defaults, as
    JSON null, must not crash it and must fall back to its documented safe default: string
    fields to "", numeric usage fields to 0, backup_status to the NO_BACKUPS default, and
    the server/copy location blocks to None. Reuses the EXCHANGE workload_type branch."""
    raw = null_out(
        SAMPLE_M365_WORKLOAD,
        "uid", "namespace", "planId", "planName", "planType",
        "backupUsage", "copyUsage", "backupStatus",
        "backupServerInfo", "backupCopyServerInfo",
        "entityMeta.spec.tenantId", "entityMeta.spec.userInfo",
    )

    wl = _parse_m365_workload(raw)

    assert wl is not None
    assert wl.workload_id == ""
    assert wl.namespace == ""
    assert wl.tenant_id == ""
    assert wl.plan.plan_id == ""
    assert wl.plan.name == ""
    assert wl.is_retired is False
    assert wl.protected_data_bytes == 0
    assert wl.backup_copy_data_bytes == 0
    assert wl.status == WorkloadStatus.NO_BACKUPS
    assert wl.backup_server is None
    assert wl.backup_copy_destination is None
    assert isinstance(wl.info, M365UserInfo)
    assert wl.info.user_principal_name == ""
    assert wl.name == ""


# ── M365WorkloadCollection.list()/get()/get_by_name(): null "m365Workloads" key ────


async def test_list_survives_null_m365_workloads_key() -> None:
    """m365Workloads JSON null (key present, value null — distinct from an absent key)
    must not crash list(); it is treated as an empty page instead of raising."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": None})

        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert workloads == []


async def test_get_survives_null_m365_workloads_key() -> None:
    """m365Workloads JSON null in the nsUidPair lookup response must not crash get(); it
    is treated as no match (ResourceNotFoundError), same as an empty list."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": None})

        collection = M365WorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get(WORKLOAD_UID, NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="M365Workload", resource_id=WORKLOAD_UID)


async def test_get_by_name_survives_null_m365_workloads_key() -> None:
    """m365Workloads JSON null in the keyword-search response must not crash
    get_by_name(); it is treated as no match (ResourceNotFoundError), same as an empty list."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": None})

        collection = M365WorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("Alice", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="M365Workload", resource_id="Alice")
