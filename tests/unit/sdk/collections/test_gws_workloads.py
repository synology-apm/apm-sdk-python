"""Unit tests for GWSWorkloadCollection read paths (list/get/get_by_name/list_versions/get_latest_version).

See test_gws_workloads_actions.py for backup_now/cancel_backup/retire/change_plan/delete, and
test_gws_workloads_facade.py for GWSPlanCollection (list/get/get_by_name).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections.gws import GWSWorkloadCollection, _parse_gws_workload
from synology_apm.sdk.enums import (
    GWSWorkloadType,
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
from synology_apm.sdk.models.workload import GWSSharedDriveInfo, GWSUserInfo, GWSWorkload
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
    make_session,
    null_out,
    request_json,
)

WORKLOAD_LIST_URL = f"{BASE_URL}/api/v1/workload/gw_workload/list"

DOMAIN_ID = "gwsdemo.example.com"
WORKLOAD_UID = "wl-gws-uid-001"
NAMESPACE = "ns-gws-001"
PLAN_ID = "0c8f033b-fb57-4f46-9a9d-85e9d21c08ab"
ARCHIVE_PLAN_ID = "cc39711f-deb9-40fa-b6c4-27ca82958d3c"

SAMPLE_GWS_WL_OBJ = GWSWorkload(
    workload_id=WORKLOAD_UID, name="alice@gwsdemo.example.com", category=WorkloadCategory.GWS,
    namespace=NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id=PLAN_ID, name="Daily Backup (saas)", category=WorkloadCategory.GWS),
    workload_type=GWSWorkloadType.MAIL, domain=DOMAIN_ID, info=GWSUserInfo(email="alice@gwsdemo.example.com"),
)

SAMPLE_PROTECTION_PLAN = ProtectionPlan(
    plan_id=PLAN_ID,
    name="Daily Backup (saas)",
    category=WorkloadCategory.GWS,
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

SAMPLE_GWS_WORKLOAD = {
    "workloadType": "MAIL",
    "uid": WORKLOAD_UID,
    "namespace": NAMESPACE,
    "domain": DOMAIN_ID,
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
            "domain": DOMAIN_ID,
            "userInfo": {
                "email": "alice@gwsdemo.example.com",
                "name": "Alice",
            },
        }
    },
    "backupUserInfo": {"id": "", "name": "", "mail": "", "isDeleted": False},
    "isAnomaly": False,
}

SAMPLE_SHARED_DRIVE_WORKLOAD = {
    "workloadType": "TEAM_DRIVE",
    "uid": "wl-gws-shared-drive-001",
    "namespace": NAMESPACE,
    "domain": DOMAIN_ID,
    "planId": PLAN_ID,
    "planName": "Daily Backup (saas)",
    "planType": "BACKUP",
    "lastBackupTime": "0",
    "backupUsage": "0",
    "copyUsage": "0",
    "entityMeta": {
        "spec": {
            "domain": DOMAIN_ID,
            "teamDriveInfo": {"id": "drive-id-001", "name": "Marketing Drive"},
        }
    },
    "backupUserInfo": {"id": "user-id-002", "name": "Bob", "mail": "bob@gwsdemo.example.com", "isDeleted": False},
    "isAnomaly": True,
}

EMPTY_GWS_RESPONSE: dict[str, Any] = {"gwWorkloads": []}

# ── GWSWorkloadCollection.list() ──────────────────────────────────────────


async def test_list_requires_domain() -> None:
    """list(domain, workload_type) must send primKey=domain."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_GWS_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert len(workloads) == 1
    assert isinstance(workloads[0], GWSWorkload)
    post_key = ("POST", URL(WORKLOAD_LIST_URL))
    body = request_json(m, post_key)
    assert body["filter"]["primKey"] == DOMAIN_ID


async def test_list_queries_single_scope_when_scope_given() -> None:
    """list(domain, workload_type=MAIL) should make exactly 1 POST request."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload=EMPTY_GWS_RESPONSE)

        collection = GWSWorkloadCollection(session)
        await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    post_key = ("POST", URL(WORKLOAD_LIST_URL))
    assert len(m.requests[post_key]) == 1
    body = request_json(m, post_key)
    assert body["filter"]["gwWorkloadFilter"]["gwWorkloadType"] == "MAIL"


@pytest.mark.parametrize(
    "workload_type,api_type",
    [
        (GWSWorkloadType.DRIVE, "DRIVE"),
        (GWSWorkloadType.MAIL, "MAIL"),
        (GWSWorkloadType.CONTACT, "CONTACT"),
        (GWSWorkloadType.CALENDAR, "CALENDAR"),
        (GWSWorkloadType.SHARED_DRIVE, "TEAM_DRIVE"),
    ],
)
async def test_list_maps_workload_type_to_api_string(workload_type: GWSWorkloadType, api_type: str) -> None:
    """list() maps each GWSWorkloadType to its raw API string — SHARED_DRIVE maps to the
    legacy TEAM_DRIVE token (see the SDK's Naming decision for GWS)."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": []}
        await collection.list(DOMAIN_ID, workload_type=workload_type)

    body = mock_post.call_args[1]["json"]["filter"]
    assert body["gwWorkloadFilter"]["gwWorkloadType"] == api_type


async def test_list_namespace_resolves_backup_server_id_and_filters_server_side() -> None:
    """list(namespace=...) looks up backup_server first to get backup_server_id, then passes it via filter.backupServerUids."""
    from unittest.mock import AsyncMock, patch

    BS_NAMESPACE = NAMESPACE
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
        mock_post.return_value = {"gwWorkloads": [SAMPLE_GWS_WORKLOAD]}

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, namespace=[BS_NAMESPACE])

    posted_filter = mock_post.call_args[1]["json"]["filter"]
    assert posted_filter.get("backupServerUids") == [BS_ID]
    assert len(workloads) == 1


async def test_list_multiple_namespaces_resolves_all_and_filters_server_side() -> None:
    """list(namespace=[a, b]) resolves both namespaces in one backup-server scan and posts
    both resolved IDs as filter.backupServerUids, in the same order as the input."""
    from unittest.mock import AsyncMock, patch

    NS_1, ID_1 = "ns-gws-001", "bs-id-001"
    NS_2, ID_2 = "ns-gws-002", "bs-id-002"
    SERVERS_RESPONSE = {
        "backupServers": [
            {"id": ID_1, "namespace": NS_1, "spec": {}, "status": {}},
            {"id": ID_2, "namespace": NS_2, "spec": {}, "status": {}},
        ]
    }
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = SERVERS_RESPONSE
        mock_post.return_value = {"gwWorkloads": [SAMPLE_GWS_WORKLOAD]}

        collection = GWSWorkloadCollection(session)
        await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, namespace=[NS_1, NS_2])

    assert mock_get.call_count == 1  # one shared scan, not one per namespace
    posted_filter = mock_post.call_args[1]["json"]["filter"]
    assert posted_filter.get("backupServerUids") == [ID_1, ID_2]


async def test_list_multiple_namespaces_all_unmatched_returns_empty() -> None:
    """list(namespace=[bad1, bad2]) short-circuits to an empty result (no workload API call)
    when none of the given namespaces resolve to a backup server."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"backupServers": [], "total": 0}

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(
            DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, namespace=["bad-ns-1", "bad-ns-2"]
        )
        mock_post.assert_not_called()

    assert workloads == []
    assert total == 0


async def test_list_multiple_namespaces_partial_match_filters_by_resolved_id_only() -> None:
    """list(namespace=[good, bad]) still queries workloads, scoped to only the namespace(s)
    that resolved — an unmatched namespace among several contributes nothing (OR-filter
    semantics), it does not invalidate the whole lookup."""
    from unittest.mock import AsyncMock, patch

    NS_GOOD, ID_GOOD = NAMESPACE, "bs-id-001"
    SERVERS_RESPONSE = {
        "backupServers": [
            {"id": ID_GOOD, "namespace": NS_GOOD, "spec": {}, "status": {}},
        ]
    }
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = SERVERS_RESPONSE
        mock_post.return_value = {"gwWorkloads": [SAMPLE_GWS_WORKLOAD]}

        collection = GWSWorkloadCollection(session)
        await collection.list(
            DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, namespace=[NS_GOOD, "bad-ns"]
        )

    posted_filter = mock_post.call_args[1]["json"]["filter"]
    assert posted_filter.get("backupServerUids") == [ID_GOOD]


async def test_list_parses_workload_fields() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_GWS_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    wl = workloads[0]
    assert wl.workload_id == WORKLOAD_UID
    assert wl.namespace == NAMESPACE
    assert wl.domain == DOMAIN_ID
    assert wl.workload_type == GWSWorkloadType.MAIL
    assert wl.category == WorkloadCategory.GWS
    assert wl.is_retired is False
    assert isinstance(wl.plan, ProtectionPlan)
    assert wl.plan.plan_id == PLAN_ID
    assert wl.plan.name == "Daily Backup (saas)"
    assert wl.plan.category == WorkloadCategory.GWS
    assert isinstance(wl.info, GWSUserInfo)
    assert wl.info.email == "alice@gwsdemo.example.com"
    assert wl.backup_user is None
    assert wl.is_anomaly is False


async def test_list_parses_shared_drive_workload() -> None:
    """A TEAM_DRIVE-typed raw workload parses as SHARED_DRIVE with GWSSharedDriveInfo,
    a populated backup_user, and is_anomaly."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_SHARED_DRIVE_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.SHARED_DRIVE)
        await session.disconnect()

    wl = workloads[0]
    assert wl.workload_type == GWSWorkloadType.SHARED_DRIVE
    assert isinstance(wl.info, GWSSharedDriveInfo)
    assert wl.info.drive_id == "drive-id-001"
    assert wl.info.drive_name == "Marketing Drive"
    assert wl.name == "Marketing Drive"
    assert wl.backup_user == "bob@gwsdemo.example.com"
    assert wl.is_anomaly is True


async def test_list_parses_archive_plan_type_as_retirement_plan() -> None:
    """A workload whose planType is ARCHIVE parses plan as a RetirementPlan."""
    archived_workload = {
        **SAMPLE_GWS_WORKLOAD,
        "planId": ARCHIVE_PLAN_ID,
        "planName": "Compliance Retention",
        "planType": "ARCHIVE",
    }
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [archived_workload]})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, is_retired=True)
        await session.disconnect()

    wl = workloads[0]
    assert isinstance(wl.plan, RetirementPlan)
    assert wl.plan.plan_id == ARCHIVE_PLAN_ID
    assert wl.plan.name == "Compliance Retention"


async def test_list_parses_backup_server() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_GWS_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    loc = workloads[0].backup_server
    assert loc is not None
    assert loc.name == "apm-server-01"
    assert loc.identifier == "ns-server-001"
    assert loc.is_remote_storage is False


async def test_list_backup_copy_destination_none_when_empty() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_GWS_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert workloads[0].backup_copy_destination is None


async def test_list_parses_backup_copy_data_bytes() -> None:
    async with connected_session() as (session, m):

        raw = {**SAMPLE_GWS_WORKLOAD, "copyUsage": "209715200"}
        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [raw]})

        collection = GWSWorkloadCollection(session)
        workloads, _ = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert workloads[0].backup_copy_data_bytes == 209715200


async def test_list_skips_unknown_workload_type() -> None:
    """Workloads with an unrecognised workloadType (or the NONE default) should be silently dropped."""
    unknown = {**SAMPLE_GWS_WORKLOAD, "workloadType": "NONE", "uid": "unk-uid"}
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_GWS_WORKLOAD, unknown]})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert len(workloads) == 1
    assert workloads[0].workload_id == WORKLOAD_UID


# ── GWSWorkloadCollection.get() ────────────────────────────────────────────


async def test_get_uses_nsuidpair() -> None:
    """get(uid, namespace, domain=did) sends nsUidPair in filter body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": [SAMPLE_GWS_WORKLOAD]}
        wl = await collection.get(WORKLOAD_UID, NAMESPACE, domain=DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)

    assert mock_post.call_count == 1
    body = mock_post.call_args[1]["json"]["filter"]
    assert body["primKey"] == DOMAIN_ID
    assert body["nsUidPair"] == {"namespace": NAMESPACE, "uid": WORKLOAD_UID}
    assert wl.workload_id == WORKLOAD_UID


async def test_get_raises_not_found() -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": []}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("bad-uid", NAMESPACE, domain=DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)

    assert_resource_error(exc_info, resource_type="GWSWorkload", resource_id="bad-uid")


async def test_get_raises_not_found_for_http_404() -> None:
    async with connected_session() as (session, m):
        m.post(WORKLOAD_LIST_URL, status=404)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await GWSWorkloadCollection(session).get(
                "bad-uid", NAMESPACE, domain=DOMAIN_ID, workload_type=GWSWorkloadType.MAIL
            )
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="GWSWorkload", resource_id="bad-uid")
    assert exc_info.value.error_code == 404


# ── GWSWorkloadCollection.get_by_name() ───────────────────────────────────


async def test_get_by_name_uses_keyword_only() -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": [SAMPLE_GWS_WORKLOAD]}
        wl = await collection.get_by_name("Alice", DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)

    assert mock_post.call_count == 1
    body = mock_post.call_args[1]["json"]["filter"]
    assert body["keyword"] == "Alice"
    assert "nsUidPair" not in body
    assert wl.workload_id == WORKLOAD_UID


@pytest.mark.parametrize("name", ["alice@gwsdemo.example.com", "ALICE@GWSDEMO.EXAMPLE.COM"], ids=["matches_email", "case_insensitive"])
async def test_get_by_name_matches_email(name: str) -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_GWS_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        wl = await collection.get_by_name(name, DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert wl.workload_id == WORKLOAD_UID


async def test_get_by_name_matches_shared_drive_name() -> None:
    """get_by_name() matches a Shared Drive workload by its drive name."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [SAMPLE_SHARED_DRIVE_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        wl = await collection.get_by_name("Marketing Drive", DOMAIN_ID, workload_type=GWSWorkloadType.SHARED_DRIVE)
        await session.disconnect()

    assert wl.workload_id == "wl-gws-shared-drive-001"


async def test_get_by_name_skips_unparseable_entry_before_match() -> None:
    """An entry with an unrecognized workloadType is skipped rather than raised on, and
    get_by_name still finds a later matching entry in the same page."""
    unparseable_entry = {"workloadType": "SOME_UNKNOWN_TYPE"}
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": [unparseable_entry, SAMPLE_GWS_WORKLOAD]})

        collection = GWSWorkloadCollection(session)
        wl = await collection.get_by_name("Alice", DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert wl.workload_id == WORKLOAD_UID


async def test_get_by_name_raises_not_found_when_keyword_returns_empty() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload=EMPTY_GWS_RESPONSE)

        collection = GWSWorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("no-such-name", DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="GWSWorkload", resource_id="no-such-name")


# ── GWSWorkloadCollection.list(): filter body construction ────────────────


@pytest.mark.parametrize("is_retired,expected_plan_type", [
    (False, "BACKUP"),
    (True, "ARCHIVE"),
])
async def test_list_sends_plantype_for_is_retired(is_retired: bool, expected_plan_type: str) -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": []}
        await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, is_retired=is_retired)

    body = mock_post.call_args[1]["json"]["filter"]
    assert body["planType"] == expected_plan_type


async def test_list_plan_is_passed_as_plan_uids_in_filter_body() -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": []}
        await collection.list(
            DOMAIN_ID, workload_type=GWSWorkloadType.MAIL,
            plan=[SAMPLE_PROTECTION_PLAN, SAMPLE_RETIREMENT_PLAN],
        )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert body["planUids"] == [SAMPLE_PROTECTION_PLAN.plan_id, SAMPLE_RETIREMENT_PLAN.plan_id]


async def test_list_keyword_is_passed_as_keyword_in_filter_body() -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": []}
        await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, keyword="Alice")

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert body["keyword"] == "Alice"


async def test_list_status_is_passed_as_backup_status_in_filter_body() -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": []}
        await collection.list(
            DOMAIN_ID, workload_type=GWSWorkloadType.MAIL,
            status=[WorkloadStatus.FAILED, WorkloadStatus.PARTIAL],
        )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert body["backupStatus"] == ["ERROR", "WARNING"]


async def test_list_status_retired_raises_value_error() -> None:
    session = make_session()
    collection = GWSWorkloadCollection(session)

    with pytest.raises(ValueError, match="RETIRED"):
        await collection.list(
            DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, status=[WorkloadStatus.RETIRED]
        )


@pytest.mark.parametrize("field_name", ["keyword", "planUids", "backupStatus"])
async def test_list_omits_optional_filter_fields_when_not_provided(field_name: str) -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"gwWorkloads": []}
        await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)

    _, kwargs = mock_post.call_args
    body = kwargs["json"]["filter"]
    assert field_name not in body


async def test_list_unknown_namespace_returns_empty() -> None:
    """list(namespace=...) short-circuits to an empty result (no workload API call) when no
    backup server matches — the namespace filter matches nothing, it is not an error."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"backupServers": [], "total": 0}

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(
            DOMAIN_ID, workload_type=GWSWorkloadType.MAIL, namespace=["no-such-ns"]
        )
        mock_post.assert_not_called()

    assert workloads == []
    assert total == 0


# ── GWSWorkloadCollection.list_versions() / get_latest_version() ─────────


async def test_gws_list_versions_filters_by_since() -> None:
    from unittest.mock import AsyncMock, patch

    cutoff = datetime.fromtimestamp(1700050000, tz=UTC)
    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [
                {"id": "ver-new", "spec": {"backupType": "FULL_BACKUP", "executionId": "G_A", "locked": False},
                 "status": {"startTime": "1700100000", "transferredSize": "0"}},
            ],
            "total": 1,
        }
        versions, total = await collection.list_versions(SAMPLE_GWS_WL_OBJ, since=cutoff)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("createStartTimestamp") == "1700050000"
    assert total == 1
    assert len(versions) == 1
    assert versions[0].version_id == "ver-new"


async def test_gws_get_latest_version_returns_first_result() -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)
    sample_ver = {"id": "ver-gws-001", "spec": {"backupType": "FULL_BACKUP", "executionId": "G_1", "locked": False},
                  "status": {"startTime": "1700100000", "transferredSize": "0"}}

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [sample_ver], "total": 1}
        v = await collection.get_latest_version(SAMPLE_GWS_WL_OBJ)

    assert v.version_id == "ver-gws-001"
    params = dict(mock_get.call_args[1]["params"])
    assert params["limit"] == 1


async def test_gws_get_latest_version_raises_when_no_versions() -> None:
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = GWSWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [], "total": 0}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_latest_version(SAMPLE_GWS_WL_OBJ)

    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id=WORKLOAD_UID)


# ── _parse_gws_workload: null vs. absent JSON field handling ──────────────


def test_parse_gws_workload_returns_none_for_null_workload_type() -> None:
    raw = null_out(SAMPLE_GWS_WORKLOAD, "workloadType")

    assert _parse_gws_workload(raw) is None


def test_parse_gws_workload_survives_null_entity_meta() -> None:
    raw = null_out(SAMPLE_GWS_WORKLOAD, "entityMeta")

    wl = _parse_gws_workload(raw)

    assert wl is not None
    assert isinstance(wl.info, GWSUserInfo)
    assert wl.info.email == ""
    assert wl.name == ""


def test_parse_gws_workload_survives_null_common_fields() -> None:
    raw = null_out(
        SAMPLE_GWS_WORKLOAD,
        "uid", "namespace", "domain", "planId", "planName", "planType",
        "backupUsage", "copyUsage", "backupStatus",
        "backupServerInfo", "backupCopyServerInfo", "backupUserInfo", "isAnomaly",
        "entityMeta.spec.userInfo",
    )

    wl = _parse_gws_workload(raw)

    assert wl is not None
    assert wl.workload_id == ""
    assert wl.namespace == ""
    assert wl.domain == ""
    assert wl.plan.plan_id == ""
    assert wl.plan.name == ""
    assert wl.is_retired is False
    assert wl.protected_data_bytes == 0
    assert wl.backup_copy_data_bytes == 0
    assert wl.status == WorkloadStatus.NO_BACKUPS
    assert wl.backup_server is None
    assert wl.backup_copy_destination is None
    assert wl.backup_user is None
    assert wl.is_anomaly is False
    assert isinstance(wl.info, GWSUserInfo)
    assert wl.info.email == ""
    assert wl.name == ""


# ── GWSWorkloadCollection.list()/get()/get_by_name(): null "gwWorkloads" key ──


async def test_list_survives_null_gws_workloads_key() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": None})

        collection = GWSWorkloadCollection(session)
        workloads, total = await collection.list(DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert workloads == []


async def test_get_survives_null_gws_workloads_key() -> None:
    async with connected_session() as (session, m):

        m.post(WORKLOAD_LIST_URL, payload={"gwWorkloads": None})

        collection = GWSWorkloadCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get(WORKLOAD_UID, NAMESPACE, domain=DOMAIN_ID, workload_type=GWSWorkloadType.MAIL)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="GWSWorkload", resource_id=WORKLOAD_UID)
