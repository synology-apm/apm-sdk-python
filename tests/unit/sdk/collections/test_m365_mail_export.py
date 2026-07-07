"""Unit tests for ExchangeExportCollection and GroupExportCollection: list/start/cancel/get_download_url/get_activity_by_result."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from synology_apm.sdk.collections.m365_mail_export import (
    ExchangeExportCollection,
    GroupExportCollection,
    M365ExportStartResult,
)
from synology_apm.sdk.enums import M365ExportStatus, M365WorkloadType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.exceptions import ResourceNotReadyError
from synology_apm.sdk.models.activity import M365ExportActivity
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import VersionLocation
from synology_apm.sdk.models.version import WorkloadVersion as _WV
from synology_apm.sdk.models.workload import M365GroupInfo, M365UserInfo, M365Workload
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, make_session

TENANT_ID = "tenant-aaa-001"
EXCHANGE_WORKLOAD_UID = "wl-m365-uid-001"
EXCHANGE_NAMESPACE = "ns-m365-001"

SAMPLE_M365_EXCHANGE_WL_OBJ = M365Workload(
    workload_id=EXCHANGE_WORKLOAD_UID, name="alice@contoso.com", category=WorkloadCategory.M365,
    namespace=EXCHANGE_NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-x", name="Daily Backup (saas)", category=WorkloadCategory.M365),
    workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID, info=M365UserInfo(user_principal_name="alice@contoso.com"),
)


# ── ExchangeExportCollection constants and factories ─────────────────────────

EXCHANGE_EXEC_ID = "188"
EXCHANGE_PORTAL_VERSION_ID = "4"

SAMPLE_EXCHANGE_EXPORT_ACTIVITY: dict[str, Any] = {
    "uid": "47c3b536-9e2b-469f-9aa8-d4aa1dbe147e",
    "namespace": EXCHANGE_NAMESPACE,
    "spec": {
        "executionId": EXCHANGE_EXEC_ID,
        "workload": {"uid": EXCHANGE_WORKLOAD_UID, "namespace": EXCHANGE_NAMESPACE},
        "sourceName": "Top of Information Store",
        "isArchiveMail": True,
        "versionId": EXCHANGE_PORTAL_VERSION_ID,
        "versionTimestamp": "1778700000",
    },
    "status": {
        "exportStatus": "READY_TO_DOWNLOAD",
        "startTime": "1778732975",
        "endTime": "1778734652",
    },
}

EXCHANGE_VERSION_ID = "0d65d6d7-abad-42bf-a4f8-cd4941c1ab52"
EXCHANGE_SNAPSHOT_ID = "ActiveBackup-Office365|SERIALNUMBER01|133676651|1776934911708990"
EXCHANGE_COPY_VERSION_ID = "73b7402b-03dd-43fa-9994-8b2cf83750a3"
EXCHANGE_COPY_NAMESPACE = "2d90eeaf-efb0-4089-a84c-264d2e9e2d68"
EXCHANGE_COPY_CONNECTION_ID = "x9TlHZa9AUNc"


def _make_exchange_export_version(*, with_copy: bool = False) -> _WV:
    from datetime import datetime

    from synology_apm.sdk.enums import VersionStatus
    _info = LocationInfo(
        is_remote_storage=False, identifier=EXCHANGE_NAMESPACE, name="srv", endpoint="", vault=None,
    )
    locations = [VersionLocation(namespace=EXCHANGE_NAMESPACE, location_info=_info, location_id=EXCHANGE_VERSION_ID)]
    if with_copy:
        _copy_info = LocationInfo(
            is_remote_storage=False, identifier=EXCHANGE_COPY_NAMESPACE, name="copy-srv",
            endpoint="", vault=None,
        )
        locations.append(VersionLocation(
            namespace=EXCHANGE_COPY_NAMESPACE, location_info=_copy_info,
            location_id=EXCHANGE_COPY_VERSION_ID, connection_id=EXCHANGE_COPY_CONNECTION_ID,
        ))
    return _WV(
        version_id=EXCHANGE_VERSION_ID,
        workload_id=EXCHANGE_WORKLOAD_UID,
        namespace=EXCHANGE_NAMESPACE,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="107",
        locked=False,
        changed_size_bytes=0,
        portal_version_id=EXCHANGE_PORTAL_VERSION_ID,
        snapshot_id=EXCHANGE_SNAPSHOT_ID,
        locations=locations,
    )


def _make_exchange_start_result(*, with_copy: bool = False, ready: bool = True) -> M365ExportStartResult:
    version = _make_exchange_export_version(with_copy=with_copy)
    location = version.locations[1 if with_copy else 0]
    return M365ExportStartResult(
        execution_id=EXCHANGE_EXEC_ID,
        ready_to_download=ready,
        export_name="alice_20260514.pst",
        location=location,
        workload=SAMPLE_M365_EXCHANGE_WL_OBJ,
        version=version,
    )


# ── GroupExportCollection constants and factories ─────────────────────────────
# (defined before parametrized tests so they are available at module load time)

GROUP_WORKLOAD_UID = "fd53ac91-392a-4abc-af42-1afc9df367a9"
GROUP_NAMESPACE = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"
GROUP_EXEC_ID = "291"

SAMPLE_M365_GROUP_WL_OBJ = M365Workload(
    workload_id=GROUP_WORKLOAD_UID, name="marketing@contoso.com", category=WorkloadCategory.M365,
    namespace=GROUP_NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-group-x", name="Test Plan", category=WorkloadCategory.M365),
    workload_type=M365WorkloadType.GROUP, tenant_id=TENANT_ID,
    info=M365GroupInfo(group_id="group-uuid-001", display_name="marketing", mail="marketing@contoso.com"),
)

GROUP_PORTAL_VERSION_ID = "3"
GROUP_VERSION_ID = "e02902f1-6665-4ccf-8029-dbbe6076167c"

SAMPLE_GROUP_EXPORT_ACTIVITY: dict[str, Any] = {
    "uid": "a1b2c3d4-0000-0000-0000-000000000291",
    "namespace": GROUP_NAMESPACE,
    "spec": {
        "executionId": GROUP_EXEC_ID,
        "workload": {"uid": GROUP_WORKLOAD_UID, "namespace": GROUP_NAMESPACE},
        "sourceName": "Group mailbox",
        "isArchiveMail": False,
        "versionId": GROUP_PORTAL_VERSION_ID,
        "versionTimestamp": "1778700000",
    },
    "status": {
        "exportStatus": "READY_TO_DOWNLOAD",
        "startTime": "1778732975",
        "endTime": "1778734652",
    },
}


def _make_group_export_version(*, with_copy: bool = False) -> _WV:
    from datetime import datetime

    from synology_apm.sdk.enums import VersionStatus
    _info = LocationInfo(
        is_remote_storage=False, identifier=GROUP_NAMESPACE, name="srv", endpoint="", vault=None,
    )
    locations = [VersionLocation(namespace=GROUP_NAMESPACE, location_info=_info, location_id=GROUP_VERSION_ID)]
    if with_copy:
        _copy_info = LocationInfo(
            is_remote_storage=False, identifier=EXCHANGE_COPY_NAMESPACE, name="copy-srv",
            endpoint="", vault=None,
        )
        locations.append(VersionLocation(
            namespace=EXCHANGE_COPY_NAMESPACE, location_info=_copy_info,
            location_id=EXCHANGE_COPY_VERSION_ID, connection_id=EXCHANGE_COPY_CONNECTION_ID,
        ))
    return _WV(
        version_id=GROUP_VERSION_ID,
        workload_id=GROUP_WORKLOAD_UID,
        namespace=GROUP_NAMESPACE,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="290",
        locked=False,
        changed_size_bytes=0,
        portal_version_id=GROUP_PORTAL_VERSION_ID,
        snapshot_id="ActiveBackup-Office365|SERIALNUMBER01|928114927|1776843318959120",
        locations=locations,
    )


def _make_group_start_result(*, with_copy: bool = False, ready: bool = True) -> M365ExportStartResult:
    version = _make_group_export_version(with_copy=with_copy)
    location = version.locations[1 if with_copy else 0]
    return M365ExportStartResult(
        execution_id=GROUP_EXEC_ID,
        ready_to_download=ready,
        export_name="marketing_20260514.pst",
        location=location,
        workload=SAMPLE_M365_GROUP_WL_OBJ,
        version=version,
    )


def _make_exchange_export_activity(
    *,
    exec_id: str = EXCHANGE_EXEC_ID,
    status: M365ExportStatus | None = None,
) -> M365ExportActivity:
    from datetime import datetime

    resolved_status = status if status is not None else M365ExportStatus.READY_TO_DOWNLOAD
    return M365ExportActivity(
        activity_id="47c3b536-9e2b-469f-9aa8-d4aa1dbe147e",
        execution_id=exec_id,
        namespace=EXCHANGE_NAMESPACE,
        workload_id=EXCHANGE_WORKLOAD_UID,
        workload_namespace=EXCHANGE_NAMESPACE,
        source_name="Entire mailbox",
        is_archive_mail=False,
        status=resolved_status,
        started_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        finished_at=None,
        version_timestamp=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
    )


def _make_group_export_activity(
    *,
    exec_id: str = GROUP_EXEC_ID,
    status: M365ExportStatus | None = None,
) -> M365ExportActivity:
    from datetime import datetime

    resolved_status = status if status is not None else M365ExportStatus.READY_TO_DOWNLOAD
    return M365ExportActivity(
        activity_id="a1b2c3d4-0000-0000-0000-000000000291",
        execution_id=exec_id,
        namespace=GROUP_NAMESPACE,
        workload_id=GROUP_WORKLOAD_UID,
        workload_namespace=GROUP_NAMESPACE,
        source_name="Group mailbox",
        is_archive_mail=False,
        status=resolved_status,
        started_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        finished_at=None,
        version_timestamp=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
    )


# ── ExchangeExportCollection tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exchange_export_list_returns_activities() -> None:
    """ExchangeExportCollection.list() returns a list of M365ExportActivity."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection
    from synology_apm.sdk.enums import M365ExportStatus

    session = make_session()
    col = ExchangeExportCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [SAMPLE_EXCHANGE_EXPORT_ACTIVITY], "total": 1}
        activities, total = await col.list(SAMPLE_M365_EXCHANGE_WL_OBJ)

    assert total == 1
    assert len(activities) == 1
    act = activities[0]
    assert act.activity_id == "47c3b536-9e2b-469f-9aa8-d4aa1dbe147e"
    assert act.execution_id == EXCHANGE_EXEC_ID
    assert act.namespace == EXCHANGE_NAMESPACE
    assert act.workload_id == EXCHANGE_WORKLOAD_UID
    assert act.workload_namespace == EXCHANGE_NAMESPACE
    assert act.source_name == "Top of Information Store"
    assert act.is_archive_mail is True
    assert act.status == M365ExportStatus.READY_TO_DOWNLOAD
    assert act.started_at == datetime.fromtimestamp(1778732975, tz=UTC)
    assert act.finished_at == datetime.fromtimestamp(1778734652, tz=UTC)
    assert act.version_timestamp == datetime.fromtimestamp(1778700000, tz=UTC)


@pytest.mark.asyncio
async def test_exchange_export_list_source_name_is_entire_mailbox_when_root_folder() -> None:
    """ExchangeExportCollection.list() sets source_name to 'Entire mailbox' when spec.isRootFolder is true."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    root_activity = {
        **SAMPLE_EXCHANGE_EXPORT_ACTIVITY,
        "spec": {**SAMPLE_EXCHANGE_EXPORT_ACTIVITY["spec"], "isRootFolder": True, "isArchiveMail": False},
    }

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [root_activity], "total": 1}
        activities, _ = await col.list(SAMPLE_M365_EXCHANGE_WL_OBJ)

    assert activities[0].source_name == "Entire mailbox"


@pytest.mark.asyncio
async def test_exchange_export_list_source_name_is_entire_archive_mailbox_when_root_and_archive() -> None:
    """ExchangeExportCollection.list() sets source_name to 'Entire archive mailbox' when isRootFolder and isArchiveMail."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    root_archive_activity = {
        **SAMPLE_EXCHANGE_EXPORT_ACTIVITY,
        "spec": {**SAMPLE_EXCHANGE_EXPORT_ACTIVITY["spec"], "isRootFolder": True, "isArchiveMail": True},
    }

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [root_archive_activity], "total": 1}
        activities, _ = await col.list(SAMPLE_M365_EXCHANGE_WL_OBJ)

    assert activities[0].source_name == "Entire archive mailbox"


@pytest.mark.asyncio
async def test_exchange_export_list_sends_correct_params() -> None:
    """ExchangeExportCollection.list() sends workload namespace and uid as query params."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [], "total": 0}
        await col.list(SAMPLE_M365_EXCHANGE_WL_OBJ)

    mock_get.assert_called_once()
    params = mock_get.call_args[1]["params"]
    assert params["workload.namespace"] == EXCHANGE_NAMESPACE
    assert params["workload.uid"] == EXCHANGE_WORKLOAD_UID


@pytest.mark.asyncio
@pytest.mark.parametrize("collection_class,workload,expected_namespace", [
    (ExchangeExportCollection, SAMPLE_M365_EXCHANGE_WL_OBJ, EXCHANGE_NAMESPACE),
    (GroupExportCollection, SAMPLE_M365_GROUP_WL_OBJ, GROUP_NAMESPACE),
])
async def test_export_list_sends_tunnel_header(
    collection_class: type[ExchangeExportCollection] | type[GroupExportCollection],
    workload: M365Workload,
    expected_namespace: str,
) -> None:
    """list() includes the x-syno-tunnel-route header for both exchange and group collections."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = collection_class(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [], "total": 0}
        await col.list(workload)

    mock_get.assert_called_once()
    headers = mock_get.call_args[1]["headers"]
    assert headers["x-syno-tunnel-route"] == expected_namespace


@pytest.mark.asyncio
async def test_exchange_export_start_sends_start_export_request() -> None:
    """ExchangeExportCollection.start() calls folders API then start_export API."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version()

    folder_id = "QVFNa0FHSmpZ..."
    folders_resp = {"folderList": [{"id": folder_id, "name": "Top of Information Store"}]}
    start_resp = {"provideLink": True, "taskId": "2", "taskExecutionId": "188"}

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = folders_resp
        mock_post.return_value = start_resp
        result = await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version, archive_mailbox=True)

    assert result.execution_id == "188"
    assert result.ready_to_download is False  # provideLink=True → not ready immediately
    assert result.export_name != ""
    # Folders GET uses correct path containing portal_version_id
    mock_get.assert_called_once()
    get_path = mock_get.call_args[0][0]
    assert f"/versions/{EXCHANGE_PORTAL_VERSION_ID}/folders" in get_path
    assert mock_get.call_args[1]["params"]["isArchive"] == "true"
    # start_export POST uses correct path and body
    mock_post.assert_called_once()
    post_path = mock_post.call_args[0][0]
    assert f"/versions/{EXCHANGE_PORTAL_VERSION_ID}/start_export" in post_path
    post_body = mock_post.call_args[1]["json"]
    assert post_body["mailExportOption"] == "ARCHIVE_USER"
    assert post_body["isArchive"] is True
    assert post_body["mailFolderList"] == [{"id": folder_id}]


@pytest.mark.asyncio
async def test_exchange_export_start_raises_when_no_portal_version_id() -> None:
    """ExchangeExportCollection.start() raises ResourceNotFoundError when version.portal_version_id is empty."""
    from datetime import datetime

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection
    from synology_apm.sdk.enums import VersionStatus
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    session = make_session()
    col = ExchangeExportCollection(session)
    version_no_pid = _WV(
        version_id="some-uuid", workload_id=EXCHANGE_WORKLOAD_UID,
        namespace=EXCHANGE_NAMESPACE, created_at=datetime(2026, 5, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS, execution_id="1", locked=False,
        changed_size_bytes=0, portal_version_id="",
    )
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version_no_pid)

    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id="some-uuid")


@pytest.mark.asyncio
async def test_exchange_export_cancel_sends_correct_body() -> None:
    """ExchangeExportCollection.cancel() sends workload uid, namespace, and executionId from activity."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    activity = _make_exchange_export_activity()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await col.cancel(activity)

    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == EXCHANGE_NAMESPACE
    body = mock_post.call_args[1]["json"]
    assert body["workload"]["uid"] == EXCHANGE_WORKLOAD_UID
    assert body["workload"]["namespace"] == EXCHANGE_NAMESPACE
    assert body["executionId"] == EXCHANGE_EXEC_ID
    assert "workloadName" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("collection_class,activity,expected_namespace,expected_wl_id,expected_exec_id", [
    (
        ExchangeExportCollection,
        _make_exchange_export_activity(),
        EXCHANGE_NAMESPACE,
        EXCHANGE_WORKLOAD_UID,
        EXCHANGE_EXEC_ID,
    ),
    (
        GroupExportCollection,
        _make_group_export_activity(),
        GROUP_NAMESPACE,
        GROUP_WORKLOAD_UID,
        GROUP_EXEC_ID,
    ),
])
async def test_export_get_download_url_by_activity_returns_url(
    collection_class: type[ExchangeExportCollection] | type[GroupExportCollection],
    activity: M365ExportActivity,
    expected_namespace: str,
    expected_wl_id: str,
    expected_exec_id: str,
) -> None:
    """get_download_url_by_activity() returns url using activity fields for both collection types."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = collection_class(session)
    expected_url = f"{BASE_URL}/portal/api/v1/portal/download/token123"

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"url": expected_url}
        url = await col.get_download_url_by_activity(activity)

    assert url == expected_url
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == expected_namespace
    body = mock_post.call_args[1]["json"]
    assert body["workload"]["uid"] == expected_wl_id
    assert body["workload"]["namespace"] == expected_namespace
    assert body["abmParams"]["mailParam"]["downloadMailParam"]["taskExecutionId"] == expected_exec_id
    assert "id" not in body
    assert "snapshotId" not in body.get("abmParams", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("collection_class,start_result,expected_namespace", [
    (ExchangeExportCollection, _make_exchange_start_result(), EXCHANGE_NAMESPACE),
    (GroupExportCollection, _make_group_start_result(), GROUP_NAMESPACE),
])
async def test_export_get_download_url_by_ready_result_sends_full_body(
    collection_class: type[ExchangeExportCollection] | type[GroupExportCollection],
    start_result: M365ExportStartResult,
    expected_namespace: str,
) -> None:
    """get_download_url_by_ready_result() sends version_id, snapshot_id, export_name for both collection types."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = collection_class(session)
    expected_url = f"{BASE_URL}/portal/api/v1/portal/download/token456"

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"url": expected_url}
        url = await col.get_download_url_by_ready_result(start_result)

    assert url == expected_url
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == expected_namespace
    body = mock_post.call_args[1]["json"]
    assert body["id"] == start_result.location.location_id
    abm = body["abmParams"]
    assert abm["snapshotId"] == start_result.version.snapshot_id
    assert abm["versionId"] == start_result.version.portal_version_id
    assert abm["exportName"] == start_result.export_name
    assert abm["mailParam"]["downloadMailParam"]["taskExecutionId"] == start_result.execution_id
    assert "connectionId" not in abm


@pytest.mark.asyncio
async def test_exchange_export_get_download_url_by_ready_result_raises_when_not_ready() -> None:
    """get_download_url_by_ready_result() raises ResourceNotReadyError when ready_to_download=False."""
    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    start_result = _make_exchange_start_result(ready=False)

    with pytest.raises(ResourceNotReadyError):
        await col.get_download_url_by_ready_result(start_result)


@pytest.mark.asyncio
async def test_exchange_export_start_uses_location_namespace_for_tunnel_header() -> None:
    """ExchangeExportCollection.start() uses location.namespace (not workload.namespace) for the tunnel header."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version()

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": "100"}
        await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version)

    mock_get.assert_called_once()
    assert mock_get.call_args[1]["headers"]["x-syno-tunnel-route"] == EXCHANGE_NAMESPACE
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == EXCHANGE_NAMESPACE


@pytest.mark.asyncio
async def test_exchange_export_start_uses_copy_location_when_location_id_given() -> None:
    """ExchangeExportCollection.start() routes to copy server and adds connectionId when location_id points to a copy."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version(with_copy=True)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": "200"}
        await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version, location_id=EXCHANGE_COPY_VERSION_ID)

    mock_get.assert_called_once()
    assert mock_get.call_args[1]["headers"]["x-syno-tunnel-route"] == EXCHANGE_COPY_NAMESPACE
    assert mock_get.call_args[1]["params"]["connectionId"] == EXCHANGE_COPY_CONNECTION_ID
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == EXCHANGE_COPY_NAMESPACE
    post_body = mock_post.call_args[1]["json"]
    assert post_body["connectionId"] == EXCHANGE_COPY_CONNECTION_ID


@pytest.mark.asyncio
async def test_exchange_export_start_no_connection_id_in_body_for_primary_location() -> None:
    """ExchangeExportCollection.start() does not include connectionId in body for primary (appliance) location."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version()

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": "100"}
        await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version)

    mock_post.assert_called_once()
    post_body = mock_post.call_args[1]["json"]
    assert "connectionId" not in post_body


@pytest.mark.asyncio
async def test_exchange_export_get_download_url_by_ready_result_uses_copy_location() -> None:
    """get_download_url_by_ready_result() uses copy location's id, namespace, and connectionId."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    start_result = _make_exchange_start_result(with_copy=True)
    expected_url = f"{BASE_URL}/portal/api/v1/portal/download/token789"

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"url": expected_url}
        url = await col.get_download_url_by_ready_result(start_result)

    assert url == expected_url
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == EXCHANGE_COPY_NAMESPACE
    body = mock_post.call_args[1]["json"]
    assert body["id"] == EXCHANGE_COPY_VERSION_ID
    assert body["workload"]["namespace"] == EXCHANGE_NAMESPACE  # always primary workload namespace
    abm = body["abmParams"]
    assert abm["connectionId"] == EXCHANGE_COPY_CONNECTION_ID


@pytest.mark.asyncio
async def test_exchange_export_start_raises_not_found_for_unknown_location_id() -> None:
    """ExchangeExportCollection.start() raises ResourceNotFoundError when location_id is not in version.locations."""
    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version()

    with pytest.raises(ResourceNotFoundError) as exc_info:
        await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version, location_id="nonexistent-location-id")

    assert_resource_error(exc_info, resource_type="VersionLocation", resource_id="nonexistent-location-id")


@pytest.mark.asyncio
@pytest.mark.parametrize("collection_class,start_result,matching_activity,raw_activities", [
    (
        ExchangeExportCollection,
        _make_exchange_start_result(),
        _make_exchange_export_activity(exec_id=EXCHANGE_EXEC_ID),
        [SAMPLE_EXCHANGE_EXPORT_ACTIVITY],
    ),
    (
        GroupExportCollection,
        _make_group_start_result(),
        _make_group_export_activity(exec_id=GROUP_EXEC_ID),
        [SAMPLE_GROUP_EXPORT_ACTIVITY],
    ),
])
async def test_export_get_activity_by_result_returns_matching_activity(
    collection_class: type[ExchangeExportCollection] | type[GroupExportCollection],
    start_result: M365ExportStartResult,
    matching_activity: M365ExportActivity,
    raw_activities: list[dict[str, Any]],
) -> None:
    """get_activity_by_result() finds the activity matching both namespace and execution_id."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = collection_class(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": raw_activities, "total": 1}
        result = await col.get_activity_by_result(start_result)

    assert result is not None
    assert result.activity_id == matching_activity.activity_id
    assert result.execution_id == matching_activity.execution_id


@pytest.mark.asyncio
@pytest.mark.parametrize("collection_class,start_result", [
    (ExchangeExportCollection, _make_exchange_start_result()),
    (GroupExportCollection, _make_group_start_result()),
])
async def test_export_get_activity_by_result_returns_none_when_not_found(
    collection_class: type[ExchangeExportCollection] | type[GroupExportCollection],
    start_result: M365ExportStartResult,
) -> None:
    """get_activity_by_result() returns None when no activity matches."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = collection_class(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [], "total": 0}
        result = await col.get_activity_by_result(start_result)

    assert result is None


@pytest.mark.asyncio
async def test_exchange_export_status_unknown_for_unrecognized_value() -> None:
    """ExchangeExportCollection.list() maps unrecognized exportStatus to M365ExportStatus.UNKNOWN."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection
    from synology_apm.sdk.enums import M365ExportStatus

    session = make_session()
    col = ExchangeExportCollection(session)
    raw_unknown = {
        **SAMPLE_EXCHANGE_EXPORT_ACTIVITY,
        "status": {"exportStatus": "SOME_FUTURE_STATUS", "startTime": "0", "endTime": "0"},
    }

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [raw_unknown], "total": 1}
        activities, _ = await col.list(SAMPLE_M365_EXCHANGE_WL_OBJ)

    assert activities[0].status == M365ExportStatus.UNKNOWN


@pytest.mark.asyncio
async def test_exchange_export_list_parses_version_timestamp() -> None:
    """ExchangeExportCollection.list() parses spec.versionTimestamp into version_timestamp."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [SAMPLE_EXCHANGE_EXPORT_ACTIVITY], "total": 1}
        activities, _ = await col.list(SAMPLE_M365_EXCHANGE_WL_OBJ)

    assert activities[0].version_timestamp is not None
    assert activities[0].version_timestamp.timestamp() == 1778700000


@pytest.mark.asyncio
async def test_exchange_export_list_preparing_sets_finished_at_none() -> None:
    """ExchangeExportCollection.list() sets finished_at=None when status is PREPARING."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection
    from synology_apm.sdk.enums import M365ExportStatus

    session = make_session()
    col = ExchangeExportCollection(session)
    preparing_raw = {
        **SAMPLE_EXCHANGE_EXPORT_ACTIVITY,
        "status": {
            "exportStatus": "PREPARING",
            "startTime": "1778732975",
            "endTime": "1778734652",  # non-zero endTime that should be ignored
        },
    }

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"activities": [preparing_raw], "total": 1}
        activities, _ = await col.list(SAMPLE_M365_EXCHANGE_WL_OBJ)

    act = activities[0]
    assert act.status == M365ExportStatus.PREPARING
    assert act.finished_at is None


@pytest.mark.asyncio
async def test_exchange_export_get_download_url_by_activity_raises_when_preparing() -> None:
    """get_download_url_by_activity() raises ResourceNotReadyError when activity status is PREPARING."""
    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    activity = _make_exchange_export_activity(status=M365ExportStatus.PREPARING)

    with pytest.raises(ResourceNotReadyError):
        await col.get_download_url_by_activity(activity)


# ── GroupExportCollection tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_group_export_start_sends_is_group_true() -> None:
    """GroupExportCollection.start() sends isGroup=True and isArchive=False."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection

    session = make_session()
    col = GroupExportCollection(session)
    version = _make_group_export_version()

    folder_id = "QVFNa0FEbGhObUU0TUdRM..."
    folders_resp = {"folderList": [{"id": folder_id, "name": "Inbox"}]}
    start_resp = {"provideLink": False, "taskId": "2", "taskExecutionId": GROUP_EXEC_ID}

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = folders_resp
        mock_post.return_value = start_resp
        result = await col.start(SAMPLE_M365_GROUP_WL_OBJ, version)

    assert result.execution_id == GROUP_EXEC_ID
    assert result.ready_to_download is True  # provideLink=False → ready immediately
    # Folders GET uses isGroup=true and isArchive=false
    mock_get.assert_called_once()
    get_params = mock_get.call_args[1]["params"]
    assert get_params["isGroup"] == "true"
    assert get_params["isArchive"] == "false"
    # start_export POST body
    mock_post.assert_called_once()
    post_body = mock_post.call_args[1]["json"]
    assert post_body["isGroup"] is True
    assert post_body["isArchive"] is False
    assert post_body["mailExportOption"] == "USER"
    assert post_body["mailFolderList"] == [{"id": folder_id}]


@pytest.mark.asyncio
async def test_group_export_start_raises_when_no_portal_version_id() -> None:
    """GroupExportCollection.start() raises ResourceNotFoundError when version.portal_version_id is empty."""
    from datetime import datetime

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection
    from synology_apm.sdk.enums import VersionStatus
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    session = make_session()
    col = GroupExportCollection(session)
    version_no_pid = _WV(
        version_id="some-uuid", workload_id=GROUP_WORKLOAD_UID,
        namespace=GROUP_NAMESPACE, created_at=datetime(2026, 5, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS, execution_id="1", locked=False,
        changed_size_bytes=0, portal_version_id="",
    )
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await col.start(SAMPLE_M365_GROUP_WL_OBJ, version_no_pid)

    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id="some-uuid")


@pytest.mark.asyncio
async def test_group_export_start_uses_location_namespace_for_tunnel_header() -> None:
    """GroupExportCollection.start() uses location.namespace for both the GET and POST tunnel headers."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = GroupExportCollection(session)
    version = _make_group_export_version()

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": GROUP_EXEC_ID}
        await col.start(SAMPLE_M365_GROUP_WL_OBJ, version)

    mock_get.assert_called_once()
    assert mock_get.call_args[1]["headers"]["x-syno-tunnel-route"] == GROUP_NAMESPACE
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == GROUP_NAMESPACE


@pytest.mark.asyncio
async def test_exchange_export_start_sends_is_group_false() -> None:
    """ExchangeExportCollection.start() sends isGroup=False."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version()

    folders_resp = {"folderList": [{"id": "folder-id", "name": "Top of Information Store"}]}
    start_resp = {"provideLink": True, "taskId": "1", "taskExecutionId": "100"}

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = folders_resp
        mock_post.return_value = start_resp
        await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version)

    mock_get.assert_called_once()
    get_params = mock_get.call_args[1]["params"]
    assert get_params["isGroup"] == "false"
    mock_post.assert_called_once()
    post_body = mock_post.call_args[1]["json"]
    assert post_body["isGroup"] is False


@pytest.mark.asyncio
async def test_group_export_cancel_sends_correct_body() -> None:
    """GroupExportCollection.cancel() sends workload uid, namespace, and executionId from activity."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection
    from synology_apm.sdk.enums import M365ExportStatus

    session = make_session()
    col = GroupExportCollection(session)
    group_activity = M365ExportActivity(
        activity_id="group-act-uuid-291",
        execution_id="291",
        namespace=GROUP_NAMESPACE,
        workload_id=GROUP_WORKLOAD_UID,
        workload_namespace=GROUP_NAMESPACE,
        source_name="Group mailbox",
        is_archive_mail=False,
        status=M365ExportStatus.READY_TO_DOWNLOAD,
        started_at=None,
        finished_at=None,
    )

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await col.cancel(group_activity)

    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["x-syno-tunnel-route"] == GROUP_NAMESPACE
    body = mock_post.call_args[1]["json"]
    assert body["workload"]["uid"] == GROUP_WORKLOAD_UID
    assert body["workload"]["namespace"] == GROUP_NAMESPACE
    assert body["executionId"] == "291"
    assert "workloadName" not in body


# ── ExchangeExportCollection: additional edge cases ───────────────────────


@pytest.mark.asyncio
async def test_exchange_export_start_raises_not_found_when_folder_list_empty() -> None:
    """ExchangeExportCollection.start() raises ResourceNotFoundError when the folder list is empty."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version()

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"folderList": []}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.start(SAMPLE_M365_EXCHANGE_WL_OBJ, version)

    assert exc_info.value.resource_type == "MailboxFolder"
    assert exc_info.value.resource_id == version.version_id


@pytest.mark.asyncio
async def test_exchange_export_start_name_falls_back_to_workload_name_when_upn_empty() -> None:
    """ExchangeExportCollection.start() uses workload.name for the export name when user_principal_name is empty."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import ExchangeExportCollection

    session = make_session()
    col = ExchangeExportCollection(session)
    version = _make_exchange_export_version()
    wl_no_upn = M365Workload(
        workload_id=EXCHANGE_WORKLOAD_UID, name="alice",
        category=WorkloadCategory.M365,
        namespace=EXCHANGE_NAMESPACE, last_backup_at=None, is_retired=False,
        protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
        plan=ProtectionPlan(plan_id="plan-x", name="Daily Backup (saas)", category=WorkloadCategory.M365),
        workload_type=M365WorkloadType.EXCHANGE, tenant_id=TENANT_ID,
        info=M365UserInfo(user_principal_name=""),
    )

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": "100"}
        await col.start(wl_no_upn, version)

    mock_post.assert_called_once()
    post_body = mock_post.call_args[1]["json"]
    # export name must be derived from workload.name ("alice"), not the empty UPN
    assert post_body["exportName"].startswith("alice's mailbox_")
    assert post_body["exportName"].endswith(".pst")


async def test_group_export_start_raises_when_version_has_no_locations() -> None:
    """start() raises APIError when the version carries no location data."""
    from datetime import datetime

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection
    from synology_apm.sdk.enums import VersionStatus
    from synology_apm.sdk.exceptions import APIError

    session = make_session()
    col = GroupExportCollection(session)
    version_no_locations = _WV(
        version_id=GROUP_VERSION_ID, workload_id=GROUP_WORKLOAD_UID,
        namespace=GROUP_NAMESPACE, created_at=datetime(2026, 5, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS, execution_id="1", locked=False,
        changed_size_bytes=0, portal_version_id=GROUP_PORTAL_VERSION_ID,
        locations=[],
    )
    with pytest.raises(APIError, match="no location data"):
        await col.start(SAMPLE_M365_GROUP_WL_OBJ, version_no_locations)


async def test_group_export_start_default_name_falls_back_to_workload_name() -> None:
    """When the group info has no mail address, the default export name uses the workload name."""
    import dataclasses
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection
    from synology_apm.sdk.models.workload import M365GroupInfo

    session = make_session()
    col = GroupExportCollection(session)
    version = _make_group_export_version()
    workload = dataclasses.replace(
        SAMPLE_M365_GROUP_WL_OBJ,
        info=M365GroupInfo(group_id="group-uuid-001", display_name="marketing", mail=""),
    )

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": GROUP_EXEC_ID}
        await col.start(workload, version)

    post_body = mock_post.call_args[1]["json"]
    assert post_body["exportName"].startswith(f"{workload.name}'s group_mailbox_")
    assert post_body["exportName"].endswith(".pst")


async def test_group_export_start_sends_connection_id_for_copy_location() -> None:
    """start() includes connectionId in the body when the selected location carries one."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection

    session = make_session()
    col = GroupExportCollection(session)
    version = _make_group_export_version(with_copy=True)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": GROUP_EXEC_ID}
        await col.start(SAMPLE_M365_GROUP_WL_OBJ, version, location_id=EXCHANGE_COPY_VERSION_ID)

    post_body = mock_post.call_args[1]["json"]
    assert post_body["connectionId"] == EXCHANGE_COPY_CONNECTION_ID
