"""Unit tests for MachineWorkloadCollection: list_versions/get_latest_version/get_version/lock_version/unlock_version."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from synology_apm.sdk.collections.machine import MachineWorkloadCollection
from synology_apm.sdk.enums import MachineWorkloadType, VersionStatus, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.exceptions import APIError, ResourceNotFoundError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.sdk.conftest import assert_resource_error, make_session

WORKLOAD_ID = "wl-id-001"
NAMESPACE = "ns-001"

SAMPLE_WL_OBJ = MachineWorkload(
    workload_id=WORKLOAD_ID, name="CORP-PC-001", category=WorkloadCategory.MACHINE,
    namespace=NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-x", name="Test Plan", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC, agent_version=None,
)

VERSION_ID = "ver-uuid-001"
COPY_NS = "ns-copy-002"
COPY_VER = "ver-copy-001"


def _make_location(namespace: str, name: str, address: str, version_id: str) -> VersionLocation:
    info = LocationInfo(is_remote_storage=False, identifier=namespace, name=name, endpoint=address, vault=None)
    return VersionLocation(namespace=namespace, location_info=info, location_id=version_id)

def _make_version_with_locations(primary_uid: str = VERSION_ID, copy_uid: str | None = None) -> WorkloadVersion:
    locations = [_make_location(NAMESPACE, "apm-server-01", "192.0.2.1", primary_uid)]
    if copy_uid:
        locations.append(_make_location(COPY_NS, "apm-server-02", "192.0.2.2", copy_uid))
    return WorkloadVersion(
        version_id=primary_uid,
        workload_id=WORKLOAD_ID,
        namespace=NAMESPACE,
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="EX_1",
        locked=False,
        changed_size_bytes=0,
        locations=locations,
    )


# ── list_versions() ───────────────────────────────────────────────────────


async def test_list_versions_calls_correct_url() -> None:
    """list_versions(id, ns) calls /api/v1/workload/{ns}/{id}/version directly."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [], "total": 0}
        await collection.list_versions(SAMPLE_WL_OBJ)

    assert mock_get.call_count == 1
    called_path = mock_get.call_args[0][0]
    assert called_path == f"/api/v1/workload/{NAMESPACE}/{WORKLOAD_ID}/version"


async def test_list_versions_filters_by_since() -> None:
    """list_versions(since=...) sends createStartTimestamp as server-side param."""
    from datetime import datetime
    from unittest.mock import AsyncMock, patch

    cutoff = datetime.fromtimestamp(1700050000, tz=UTC)
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [
                {"id": "ver-new", "spec": {"backupType": "FULL_BACKUP", "executionId": "A", "locked": False},
                 "status": {"startTime": "1700100000", "transferredSize": "0"}},
            ],
            "total": 1,
        }
        versions, total = await collection.list_versions(SAMPLE_WL_OBJ, since=cutoff)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("createStartTimestamp") == "1700050000"
    assert "createEndTimestamp" not in params
    assert total == 1
    assert len(versions) == 1
    assert versions[0].version_id == "ver-new"


async def test_list_versions_filters_by_until() -> None:
    """list_versions(until=...) sends createEndTimestamp as server-side param."""
    from datetime import datetime
    from unittest.mock import AsyncMock, patch

    cutoff = datetime.fromtimestamp(1700050000, tz=UTC)
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [
                {"id": "ver-old", "spec": {"backupType": "FULL_BACKUP", "executionId": "B", "locked": False},
                 "status": {"startTime": "1700000000", "transferredSize": "0"}},
            ],
            "total": 1,
        }
        versions, total = await collection.list_versions(SAMPLE_WL_OBJ, until=cutoff)

    params = dict(mock_get.call_args[1]["params"])
    assert params.get("createEndTimestamp") == "1700050000"
    assert "createStartTimestamp" not in params
    assert total == 1
    assert len(versions) == 1
    assert versions[0].version_id == "ver-old"


async def test_list_versions_sends_status_filter_params() -> None:
    """list_versions() sends status=COMPLETED/PARTIAL/FAILED/CANCELED to exclude BACKING_UP server-side."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [], "total": 0}
        collection = MachineWorkloadCollection(session)
        await collection.list_versions(SAMPLE_WL_OBJ)

    params: list[tuple[str, str]] = mock_get.call_args[1]["params"]
    status_values = [v for k, v in params if k == "status"]
    assert set(status_values) == {"COMPLETED", "PARTIAL", "FAILED", "CANCELED"}


# ── get_latest_version() ──────────────────────────────────────────────────


async def test_get_latest_version_returns_first_result() -> None:
    """get_latest_version() calls list_versions(limit=1) and returns the first item."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    sample_version = {"id": "ver-001", "spec": {"backupType": "FULL_BACKUP", "executionId": "A", "locked": False},
                      "status": {"startTime": "1700100000", "transferredSize": "0"}}

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [sample_version], "total": 1}
        v = await collection.get_latest_version(SAMPLE_WL_OBJ)

    assert v.version_id == "ver-001"
    params = dict(mock_get.call_args[1]["params"])
    assert params["limit"] == 1


async def test_get_latest_version_raises_when_no_versions() -> None:
    """get_latest_version() raises ResourceNotFoundError when list_versions returns empty."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [], "total": 0}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_latest_version(SAMPLE_WL_OBJ)

    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id=WORKLOAD_ID)


# ── get_version() ───────────────────────────────────────────────────────────

async def test_get_version_returns_matching_version_on_first_page() -> None:
    """get_version() returns version when found on first page."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    target = _make_version_with_locations()
    other = _make_version_with_locations("other-ver")

    with patch.object(collection, "list_versions", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([other, target], 2)
        result = await collection.get_version(SAMPLE_WL_OBJ, VERSION_ID)

    assert result.version_id == VERSION_ID


async def test_get_version_paginates_to_second_page() -> None:
    """get_version() advances to next page when version not on first page."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    target = _make_version_with_locations()
    page1 = [_make_version_with_locations(f"v-{i}") for i in range(50)]
    page2 = [target]

    with patch.object(collection, "list_versions", new_callable=AsyncMock) as mock_list:
        mock_list.side_effect = [(page1, 50), (page2, 51)]
        result = await collection.get_version(SAMPLE_WL_OBJ, VERSION_ID)

    assert result.version_id == VERSION_ID


async def test_get_version_raises_not_found_when_exhausted() -> None:
    """get_version() raises ResourceNotFoundError when version is absent."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.exceptions import ResourceNotFoundError

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(collection, "list_versions", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = ([], 0)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_version(SAMPLE_WL_OBJ, "nonexistent")

    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id="nonexistent")


# ── lock_version(WorkloadVersion) / unlock_version(WorkloadVersion) ────────

async def test_lock_version_posts_correct_endpoint_and_body() -> None:
    """lock_version(version) POSTs batch/lock directly from the version's location data."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    version = _make_version_with_locations()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [], "allFailedSameReason": False}
        await collection.lock_version(version)

    mock_post.assert_called_once()
    assert mock_post.call_args[0][0] == "/api/v1/version/batch/lock"
    body = mock_post.call_args[1]["json"]
    # groupLeader = version.namespace + version.version_id (top-level field)
    assert body["groups"][0]["groupLeader"] == {"namespace": NAMESPACE, "uid": VERSION_ID}
    assert {"namespace": NAMESPACE, "uid": VERSION_ID} in body["groups"][0]["nsUidPairs"]


async def test_lock_version_includes_all_copy_locations_in_body() -> None:
    """lock_version() nsUidPairs includes primary + copy location pairs."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    version = _make_version_with_locations(copy_uid=COPY_VER)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [], "allFailedSameReason": False}
        await collection.lock_version(version)

    body = mock_post.call_args[1]["json"]
    pairs = body["groups"][0]["nsUidPairs"]
    assert {"namespace": NAMESPACE, "uid": VERSION_ID} in pairs
    assert {"namespace": COPY_NS, "uid": COPY_VER} in pairs
    assert len(pairs) == 2


async def test_unlock_version_posts_correct_endpoint_and_body() -> None:
    """unlock_version(version) POSTs batch/unlock directly from the version's location data."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    version = _make_version_with_locations()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [], "allFailedSameReason": False}
        await collection.unlock_version(version)

    assert mock_post.call_args[0][0] == "/api/v1/version/batch/unlock"
    body = mock_post.call_args[1]["json"]
    assert body["groups"][0]["groupLeader"] == {"namespace": NAMESPACE, "uid": VERSION_ID}
    assert {"namespace": NAMESPACE, "uid": VERSION_ID} in body["groups"][0]["nsUidPairs"]


async def test_lock_version_raises_api_error_when_errors_returned() -> None:
    """lock_version() raises APIError when APM returns errors in response."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    version = _make_version_with_locations()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [{"code": 1001, "message": "forbidden"}], "allFailedSameReason": True}
        with pytest.raises(APIError):
            await collection.lock_version(version)


async def test_unlock_version_raises_api_error_when_errors_returned() -> None:
    """unlock_version() raises APIError when APM returns errors in response."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    version = _make_version_with_locations()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": [{"code": 1001, "message": "forbidden"}], "allFailedSameReason": True}
        with pytest.raises(APIError):
            await collection.unlock_version(version)


async def test_lock_version_raises_api_error_when_version_has_no_locations() -> None:
    """lock_version(version) raises APIError when the version has no location data."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    empty_version = WorkloadVersion(
        version_id=VERSION_ID,
        workload_id=WORKLOAD_ID,
        namespace=NAMESPACE,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="EX_EMPTY",
        locked=False,
        changed_size_bytes=0,
        locations=[],
    )

    with patch.object(session, "post", new_callable=AsyncMock):
        with pytest.raises(APIError, match="no location data"):
            await collection.lock_version(empty_version)


# ── _parse_version_location (via list_versions) ───────────────────────────


async def test_list_versions_parses_appliance_location() -> None:
    """list_versions parses backupServerInfo into a non-remote-storage VersionLocation."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    version_with_loc = {
        "id": VERSION_ID,
        "namespace": NAMESPACE,
        "spec": {"backupType": "FULL_BACKUP", "executionId": "A", "locked": False},
        "status": {"startTime": "1700100000", "transferredSize": "1024"},
        "locations": [
            {
                "namespace": NAMESPACE,
                "locationType": "APPLIANCE",
                "backupServerInfo": {"hostName": "apm-server-01", "address": "192.0.2.1"},
                "versionUids": [VERSION_ID, "ver-extra-001"],
            }
        ],
    }
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [version_with_loc], "total": 1}
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert len(versions) == 1
    locs = versions[0].locations
    assert len(locs) == 2
    assert locs[0].namespace == NAMESPACE
    assert locs[0].location_id == VERSION_ID
    assert locs[1].namespace == NAMESPACE
    assert locs[1].location_id == "ver-extra-001"
    assert locs[0].location_info.name == "apm-server-01"
    assert locs[0].location_info.endpoint == "192.0.2.1"
    assert locs[0].location_info.is_remote_storage is False
    assert locs[0].location_info.vault is None
    assert locs[0].location_info is locs[1].location_info


async def test_list_versions_parses_remote_storage_location() -> None:
    """list_versions parses externalStorageInfo into a remote-storage VersionLocation."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    version_with_ext = {
        "id": VERSION_ID,
        "namespace": NAMESPACE,
        "spec": {"backupType": "FULL_BACKUP", "executionId": "B", "locked": False},
        "status": {"startTime": "1700100000", "transferredSize": "512"},
        "locations": [
            {
                "namespace": "shared",
                "locationType": "APV",
                "externalStorageInfo": {
                    "storageUid": "ext-uid-001",
                    "displayName": "APV Vault",
                    "endpoint": "apv.example.com",
                    "vaultName": "my-bucket",
                },
                "versionUids": ["ver-ext-001"],
            }
        ],
    }
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [version_with_ext], "total": 1}
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    loc = versions[0].locations[0]
    assert loc.location_info.is_remote_storage is True
    assert loc.location_info.name == "APV Vault"
    assert loc.location_info.identifier == "ext-uid-001"
    assert loc.location_info.endpoint == "apv.example.com"
    assert loc.location_info.vault == "my-bucket"
    assert loc.location_id == "ver-ext-001"


# ── copy status parsing ───────────────────────────────────────────────────


def _ver_raw(copy_status: str, inner_status: str = "", inner_reason: str = "") -> dict:  # type: ignore[type-arg]
    raw: dict = {  # type: ignore[type-arg]
        "id": VERSION_ID, "namespace": NAMESPACE,
        "spec": {"backupType": "FULL_BACKUP", "executionId": "A", "locked": False},
        "status": {"startTime": "1700100000", "transferredSize": "0"},
        "copyStatus": copy_status,
    }
    if inner_status:
        raw["status"]["copyStatus"] = inner_status
    if inner_reason:
        raw["status"]["copyStatusReason"] = inner_reason
    return raw


async def test_list_versions_parses_copy_status_completed() -> None:
    """COPY_STATUS_NONE maps to VersionCopyStatus.COMPLETED with no copy_reason."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import VersionCopyStatus

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [_ver_raw("COPY_STATUS_NONE")], "total": 1}
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions[0].copy_status == VersionCopyStatus.COMPLETED
    assert versions[0].copy_reason is None


async def test_list_versions_parses_copy_status_not_enabled() -> None:
    """COPY_STATUS_NOT_ENABLED maps to VersionCopyStatus.NOT_ENABLED."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import VersionCopyStatus

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [_ver_raw("COPY_STATUS_NOT_ENABLED")], "total": 1}
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions[0].copy_status == VersionCopyStatus.NOT_ENABLED


async def test_list_versions_parses_copy_reason_for_retry() -> None:
    """COPY_STATUS_RETRY outer status resolves inner copyStatus to CopyReason."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import CopyReason, VersionCopyStatus

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [_ver_raw("COPY_STATUS_RETRY", inner_status="DESTINATION_DISCONNECTED")],
            "total": 1,
        }
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions[0].copy_status == VersionCopyStatus.RETRY
    assert versions[0].copy_reason == CopyReason.DESTINATION_DISCONNECTED


async def test_list_versions_parses_copy_reason_skipped_with_reason() -> None:
    """COPY_STATUS_SKIPPED with SKIPPED_WORKLOAD + reason string resolves to specific CopyReason."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import CopyReason, VersionCopyStatus

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [_ver_raw(
                "COPY_STATUS_SKIPPED",
                inner_status="SKIPPED_WORKLOAD",
                inner_reason="REASON_SKIPPED_FOR_NAS_ENCRYPTED_SHARED_FOLDER",
            )],
            "total": 1,
        }
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions[0].copy_status == VersionCopyStatus.SKIPPED
    assert versions[0].copy_reason == CopyReason.SKIPPED_NAS_ENCRYPTED


async def test_list_versions_copy_reason_none_when_status_is_completed() -> None:
    """copy_reason is always None when copy_status is COMPLETED."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import VersionCopyStatus

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [_ver_raw("COPY_STATUS_NONE")], "total": 1}
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions[0].copy_status == VersionCopyStatus.COMPLETED
    assert versions[0].copy_reason is None


async def test_list_versions_unknown_copy_status_falls_back_to_none() -> None:
    """An unrecognized copyStatus string maps to copy_status=None (graceful unknown enum handling)."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "versions": [_ver_raw("COPY_STATUS_UNKNOWN_FUTURE_VALUE")],
            "total": 1,
        }
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions[0].copy_status is None
    assert versions[0].copy_reason is None
