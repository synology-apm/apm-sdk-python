"""Unit tests for M365 status/items_backed_up parsing, SharePoint/Teams/Group workload variants, version lock/unlock, get_version, M365Collection properties, and is_retired filter params."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from synology_apm.sdk.collections.m365 import M365Collection, M365WorkloadCollection
from synology_apm.sdk.collections.protection_plans import M365PlanCollection
from synology_apm.sdk.enums import M365WorkloadType, VersionStatus, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import VersionLocation
from synology_apm.sdk.models.version import WorkloadVersion as _WV
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session, make_session

WORKLOAD_POST_URL = f"{BASE_URL}/api/v1/workload/m365_workload"

TENANT_ID = "tenant-aaa-001"
WORKLOAD_UID = "wl-m365-uid-001"
NAMESPACE = "ns-m365-001"
PLAN_ID = "0c8f033b-fb57-4f46-9a9d-85e9d21c08ab"

SAMPLE_M365_WL_OBJ = M365Workload(
    workload_id=WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
    namespace=NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id=PLAN_ID, name="Daily Backup (saas)", category=WorkloadCategory.M365),
    workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
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

SAMPLE_SHAREPOINT_WORKLOAD = {
    "workloadType": "SITE",
    "uid": "sp-uid-001",
    "namespace": NAMESPACE,
    "planId": PLAN_ID,
    "planName": "Daily Backup (saas)",
    "planType": "BACKUP",
    "lastBackupTime": "1700100000",
    "backupUsage": "512000",
    "entityMeta": {
        "spec": {
            "tenantId": TENANT_ID,
            "siteInfo": {
                "url": "https://contoso.sharepoint.com/sites/Marketing",
                "siteName": "Marketing",
            },
        }
    },
}

SAMPLE_TEAMS_WORKLOAD = {
    "workloadType": "TEAMS",
    "uid": "teams-uid-001",
    "namespace": NAMESPACE,
    "planId": PLAN_ID,
    "planName": "Daily Backup (saas)",
    "planType": "BACKUP",
    "lastBackupTime": "0",
    "backupUsage": "0",
    "entityMeta": {
        "spec": {
            "tenantId": TENANT_ID,
            "teamInfo": {
                "id": "team-id-001",
                "name": "Sales Team",
                "webUrl": "https://teams.microsoft.com/l/team/abc123",
            },
        }
    },
}

SAMPLE_GROUP_WORKLOAD = {
    "workloadType": "GROUP_EXCHANGE",
    "uid": "grp-uid-001",
    "namespace": NAMESPACE,
    "planId": PLAN_ID,
    "planName": "Daily Backup (saas)",
    "planType": "BACKUP",
    "lastBackupTime": "0",
    "backupUsage": "0",
    "entityMeta": {
        "spec": {
            "tenantId": TENANT_ID,
            "groupInfo": {
                "id": "grp-id-001",
                "displayName": "Marketing Group",
                "mail": "marketing@contoso.com",
            },
        }
    },
}


# ── SaasCollection.get_m365_tenant ────────────────────────────────────────


# ── items_backed_up during BACKING_UP ─────────────────────────────────────


async def test_m365_backuping_reads_process_item_count() -> None:
    """BACKUPING status: items_backed_up is read from processItemCount; backup_progress is None."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import WorkloadStatus
    backuping_wl = {
        **SAMPLE_M365_WORKLOAD,
        "backupStatus": "BACKUPING",
        "processItemCount": 512,
    }
    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [backuping_wl]}
        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    wl = workloads[0]
    assert wl.status == WorkloadStatus.BACKING_UP
    assert wl.backup_progress is None
    assert wl.items_backed_up == 512


async def test_m365_backuping_without_process_item_count() -> None:
    """BACKUPING status with missing processItemCount: items_backed_up should be None without crashing."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import WorkloadStatus
    backuping_wl = {
        **SAMPLE_M365_WORKLOAD,
        "backupStatus": "BACKUPING",
    }
    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [backuping_wl]}
        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    wl = workloads[0]
    assert wl.status == WorkloadStatus.BACKING_UP
    assert wl.backup_progress is None
    assert wl.items_backed_up is None


async def test_m365_non_backuping_items_backed_up_is_none() -> None:
    """When not in BACKING_UP status, items_backed_up should be None."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_M365_WORKLOAD]}
        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    wl = workloads[0]
    assert wl.items_backed_up is None


async def test_m365_retired_workload_has_retired_status() -> None:
    """Retired M365 Workload (planType=ARCHIVE) should have status=WorkloadStatus.RETIRED."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk import WorkloadStatus
    retired_wl = {**SAMPLE_M365_WORKLOAD, "planType": "ARCHIVE"}
    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [retired_wl]}
        collection = M365WorkloadCollection(session)
        workloads, total = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    wl = workloads[0]
    assert wl.is_retired is True
    assert wl.status == WorkloadStatus.RETIRED


# ── QUEUING status ────────────────────────────────────────────────────────


async def test_m365_queuing_status() -> None:
    """backupStatus=QUEUING should map to WorkloadStatus.QUEUING with no items_backed_up."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import WorkloadStatus

    queuing_wl = {**SAMPLE_M365_WORKLOAD, "backupStatus": "QUEUING"}
    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [queuing_wl]}
        collection = M365WorkloadCollection(session)
        workloads, _ = await collection.list(TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    wl = workloads[0]
    assert wl.status == WorkloadStatus.QUEUING
    assert wl.items_backed_up is None


# ── SharePoint / Teams / Group workload types ─────────────────────────────


async def test_list_parses_sharepoint_workload() -> None:
    """SITE workloadType is parsed as SHAREPOINT with correct name and M365SiteInfo."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.models.workload import M365SiteInfo

    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_SHAREPOINT_WORKLOAD]}
        collection = M365WorkloadCollection(session)
        workloads, _ = await collection.list(TENANT_ID, workload_type=M365WorkloadType.SHAREPOINT)

    wl = workloads[0]
    assert wl.workload_id == "sp-uid-001"
    assert wl.workload_type == M365WorkloadType.SHAREPOINT
    assert wl.name == "Marketing"
    assert wl.last_backup_at is not None
    assert isinstance(wl.info, M365SiteInfo)
    assert wl.info.site_url == "https://contoso.sharepoint.com/sites/Marketing"
    assert wl.info.site_name == "Marketing"


async def test_list_parses_teams_workload() -> None:
    """TEAMS workloadType is parsed as TEAMS with correct name and M365TeamInfo."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.models.workload import M365TeamInfo

    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_TEAMS_WORKLOAD]}
        collection = M365WorkloadCollection(session)
        workloads, _ = await collection.list(TENANT_ID, workload_type=M365WorkloadType.TEAMS)

    wl = workloads[0]
    assert wl.workload_id == "teams-uid-001"
    assert wl.workload_type == M365WorkloadType.TEAMS
    assert wl.name == "Sales Team"
    assert isinstance(wl.info, M365TeamInfo)
    assert wl.info.team_id == "team-id-001"
    assert wl.info.web_url == "https://teams.microsoft.com/l/team/abc123"


async def test_list_parses_group_workload() -> None:
    """GROUP_EXCHANGE workloadType is parsed as GROUP with correct name and M365GroupInfo."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.models.workload import M365GroupInfo

    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_GROUP_WORKLOAD]}
        collection = M365WorkloadCollection(session)
        workloads, _ = await collection.list(TENANT_ID, workload_type=M365WorkloadType.GROUP)

    wl = workloads[0]
    assert wl.workload_id == "grp-uid-001"
    assert wl.workload_type == M365WorkloadType.GROUP
    assert wl.name == "Marketing Group"
    assert isinstance(wl.info, M365GroupInfo)
    assert wl.info.mail == "marketing@contoso.com"
    assert wl.info.display_name == "Marketing Group"


async def test_get_by_name_matches_sharepoint_by_site_name() -> None:
    """get_by_name(site_name, tid, workload_type=SHAREPOINT) finds the workload by site name."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_SHAREPOINT_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name("Marketing", TENANT_ID, workload_type=M365WorkloadType.SHAREPOINT)
        await session.disconnect()

    assert wl.workload_id == "sp-uid-001"


async def test_get_by_name_matches_teams_by_team_name() -> None:
    """get_by_name(team_name, tid, workload_type=TEAMS) finds the workload by team name."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_TEAMS_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name("Sales Team", TENANT_ID, workload_type=M365WorkloadType.TEAMS)
        await session.disconnect()

    assert wl.workload_id == "teams-uid-001"


async def test_get_by_name_matches_group_by_mail() -> None:
    """get_by_name(mail, tid, workload_type=GROUP) finds the workload by group mail."""
    async with connected_session() as (session, m):

        m.post(WORKLOAD_POST_URL, payload={"m365Workloads": [SAMPLE_GROUP_WORKLOAD]})

        collection = M365WorkloadCollection(session)
        wl = await collection.get_by_name("marketing@contoso.com", TENANT_ID, workload_type=M365WorkloadType.GROUP)
        await session.disconnect()

    assert wl.workload_id == "grp-uid-001"


# ── lock_version(WorkloadVersion) / unlock_version(WorkloadVersion) ────────

M365_VERSION_ID = "m365-ver-uuid-001"


def _make_m365_version() -> _WV:
    _info = LocationInfo(is_remote_storage=False, identifier=NAMESPACE, name="srv", endpoint="", vault=None)
    return _WV(
        version_id=M365_VERSION_ID,
        workload_id=WORKLOAD_UID,
        namespace=NAMESPACE,
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="M365_EX",
        locked=False,
        changed_size_bytes=0,
        locations=[VersionLocation(namespace=NAMESPACE, location_info=_info, location_id=M365_VERSION_ID)],
    )


async def test_m365_lock_version_posts_correct_endpoint_and_body() -> None:
    """M365 lock_version(version) POSTs batch/lock directly from the version's location data."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)
    version = _make_m365_version()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [], "allFailedSameReason": False}
        await collection.lock_version(version)

    assert mock_post.call_args[0][0] == "/api/v1/version/batch/lock"
    body = mock_post.call_args[1]["json"]
    assert body["groups"][0]["groupLeader"] == {"namespace": NAMESPACE, "uid": M365_VERSION_ID}
    assert {"namespace": NAMESPACE, "uid": M365_VERSION_ID} in body["groups"][0]["nsUidPairs"]


async def test_m365_unlock_version_posts_correct_endpoint_and_body() -> None:
    """M365 unlock_version(version) POSTs batch/unlock directly from the version's location data."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)
    version = _make_m365_version()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [], "allFailedSameReason": False}
        await collection.unlock_version(version)

    assert mock_post.call_args[0][0] == "/api/v1/version/batch/unlock"
    body = mock_post.call_args[1]["json"]
    assert body["groups"][0]["groupLeader"] == {"namespace": NAMESPACE, "uid": M365_VERSION_ID}
    assert {"namespace": NAMESPACE, "uid": M365_VERSION_ID} in body["groups"][0]["nsUidPairs"]


async def test_m365_lock_version_raises_api_error_when_errors_returned() -> None:
    """M365 lock_version() raises APIError when APM returns errors in response."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.exceptions import APIError

    session = make_session()
    collection = M365WorkloadCollection(session)
    version = _make_m365_version()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [{"code": 1001, "message": "forbidden"}], "allFailedSameReason": True}
        with pytest.raises(APIError):
            await collection.lock_version(version)


# ── M365Collection properties ──────────────────────────────────────────────


def test_m365_collection_workloads_returns_workload_collection() -> None:
    """M365Collection.workloads should return an M365WorkloadCollection."""
    session = make_session()
    col = M365Collection(session)
    assert isinstance(col.workloads, M365WorkloadCollection)


def test_m365_collection_plans_returns_plan_collection() -> None:
    """M365Collection.plans should return an M365PlanCollection."""
    session = make_session()
    col = M365Collection(session)
    assert isinstance(col.plans, M365PlanCollection)


def test_m365_collection_exchange_export_returns_exchange_export_collection() -> None:
    """M365Collection.exchange_export should return an ExchangeExportCollection."""
    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection
    session = make_session()
    col = M365Collection(session)
    assert isinstance(col.exchange_export, ExchangeExportCollection)


def test_m365_collection_group_export_returns_group_export_collection() -> None:
    """M365Collection.group_export should return a GroupExportCollection."""
    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection
    session = make_session()
    col = M365Collection(session)
    assert isinstance(col.group_export, GroupExportCollection)


# ── M365WorkloadCollection.get_version() ──────────────────────────────────


async def test_m365_get_version_returns_matching_version() -> None:
    """get_version() returns the version when found on the first page."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)
    target = _make_m365_version()
    other_info = LocationInfo(is_remote_storage=False, identifier=NAMESPACE, name="srv", endpoint="", vault=None)
    other = _WV(
        version_id="other-ver",
        workload_id=WORKLOAD_UID,
        namespace=NAMESPACE,
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="OTHER",
        locked=False,
        changed_size_bytes=0,
        locations=[VersionLocation(namespace=NAMESPACE, location_info=other_info, location_id="other-ver")],
    )

    with patch.object(collection, "list_versions", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([other, target], 2)
        result = await collection.get_version(SAMPLE_M365_WL_OBJ, M365_VERSION_ID)

    assert result.version_id == M365_VERSION_ID


async def test_m365_get_version_raises_not_found_when_absent() -> None:
    """get_version() raises ResourceNotFoundError when version is not in any page."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(collection, "list_versions", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([], 0)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_version(SAMPLE_M365_WL_OBJ, "nonexistent-ver")

    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id="nonexistent-ver")


# ── get_by_name() — is_retired filter params ────────────────────────────────


async def test_m365_get_by_name_with_is_retired_true_sends_archive_plan_type() -> None:
    """get_by_name(name, is_retired=True) should POST filter_body with planType=ARCHIVE."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_M365_WORKLOAD]}
        await collection.get_by_name(WORKLOAD_UID, TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=True)

    body = mock_post.call_args[1]["json"]
    assert body["filter"]["planType"] == "ARCHIVE"


async def test_m365_get_by_name_with_is_retired_false_sends_backup_plan_type() -> None:
    """get_by_name(name, is_retired=False) should POST filter_body with planType=BACKUP."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [SAMPLE_M365_WORKLOAD]}
        await collection.get_by_name(WORKLOAD_UID, TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=False)

    body = mock_post.call_args[1]["json"]
    assert body["filter"]["planType"] == "BACKUP"


async def test_m365_get_by_name_skips_workload_with_unknown_type() -> None:
    """get_by_name() skips workloads whose type cannot be parsed and raises ResourceNotFoundError."""
    from unittest.mock import AsyncMock, patch

    unknown_wl = {**SAMPLE_M365_WORKLOAD, "workloadType": "UNKNOWN_FUTURE_TYPE", "uid": "unknown-uid"}
    session = make_session()
    collection = M365WorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365Workloads": [unknown_wl]}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("unknown-uid", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE)

    assert_resource_error(exc_info, resource_type="M365Workload", resource_id="unknown-uid")


async def test_m365_get_version_paginates_to_second_page() -> None:
    """get_version() increments offset and queries the next page when the first page is full."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = M365WorkloadCollection(session)

    _page_size = 50
    dummy_info = LocationInfo(is_remote_storage=False, identifier=NAMESPACE, name="srv", endpoint="", vault=None)

    def _dummy(ver_id: str) -> _WV:
        return _WV(
            version_id=ver_id, workload_id=WORKLOAD_UID, namespace=NAMESPACE,
            created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
            status=VersionStatus.SUCCESS, execution_id="EX", locked=False, changed_size_bytes=0,
            locations=[VersionLocation(namespace=NAMESPACE, location_info=dummy_info, location_id=ver_id)],
        )

    page1 = [_dummy(f"ver-{i:03d}") for i in range(_page_size)]
    target = _make_m365_version()

    with patch.object(collection, "list_versions", new_callable=AsyncMock) as mock_list:
        mock_list.side_effect = [
            (page1, _page_size),   # first page: full — does NOT break; offset += page_size
            ([target], 1),         # second page: target found
        ]
        result = await collection.get_version(SAMPLE_M365_WL_OBJ, M365_VERSION_ID)

    assert result.version_id == M365_VERSION_ID
    assert mock_list.call_count == 2
    # verify the second call used offset=50
    second_call_kwargs = mock_list.call_args_list[1][1]
    assert second_call_kwargs["offset"] == _page_size


async def test_m365_collection_auto_backup_rules_returns_collection() -> None:
    """M365Collection.auto_backup_rules exposes the M365AutoBackupRuleCollection."""
    from synology_apm.sdk.collections.m365_auto_backup_rule import M365AutoBackupRuleCollection

    session = make_session()
    col = M365Collection(session)
    assert isinstance(col.auto_backup_rules, M365AutoBackupRuleCollection)
