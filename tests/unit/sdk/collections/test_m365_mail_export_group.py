"""Unit tests for GroupExportCollection: start/cancel (group-only scenarios)."""
from __future__ import annotations

from datetime import UTC

import pytest

from synology_apm.sdk.enums import M365WorkloadType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.models.activity import M365ExportActivity
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import VersionLocation
from synology_apm.sdk.models.version import WorkloadVersion as _WV
from synology_apm.sdk.models.workload import M365GroupInfo, M365Workload
from tests.unit.sdk.conftest import assert_resource_error, make_session

TENANT_ID = "tenant-aaa-001"

# ── GroupExportCollection constants and factories ─────────────────────────────

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

# Copy-location constants shared with the exchange export tests (kept in sync manually;
# see test_m365_mail_export.py for the exchange-side counterparts).
EXCHANGE_COPY_VERSION_ID = "73b7402b-03dd-43fa-9994-8b2cf83750a3"
EXCHANGE_COPY_NAMESPACE = "2d90eeaf-efb0-4089-a84c-264d2e9e2d68"
EXCHANGE_COPY_CONNECTION_ID = "x9TlHZa9AUNc"


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

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection

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


async def test_group_export_start_survives_null_task_execution_id() -> None:
    """taskExecutionId JSON null (key present, value null — distinct from an absent key) in
    the start_export response must not crash GroupExportCollection.start(); the resulting
    M365ExportStartResult.execution_id falls back to an empty string."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.collections.m365_mail_export import GroupExportCollection

    session = make_session()
    col = GroupExportCollection(session)
    version = _make_group_export_version()

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_get.return_value = {"folderList": [{"id": "folder-1"}]}
        mock_post.return_value = {"provideLink": True, "taskExecutionId": None}
        result = await col.start(SAMPLE_M365_GROUP_WL_OBJ, version)

    assert result.execution_id == ""


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
