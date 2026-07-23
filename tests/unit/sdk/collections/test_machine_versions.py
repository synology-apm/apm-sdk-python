"""Unit tests for MachineWorkloadCollection: list_versions/get_latest_version/get_version/lock_version/unlock_version."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from synology_apm.sdk.collections._shared import _parse_version, _parse_version_location
from synology_apm.sdk.collections.machine import MachineWorkloadCollection
from synology_apm.sdk.enums import (
    MachineWorkloadType,
    VersionCopyStatus,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import APIError, ResourceNotFoundError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.sdk.conftest import assert_resource_error, make_session, null_out

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

    with patch.object(session, "post", new_callable=AsyncMock), pytest.raises(APIError, match="no location data"):
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


# ── _parse_version / _parse_version_location — null field handling ────────


async def test_list_versions_versions_null_returns_empty_list() -> None:
    """list_versions() returns an empty list without raising when the API's versions key
    is JSON null (key present, value null) rather than absent."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": None, "total": 0}
        versions, total = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions == []
    assert total == 0


_VALID_VERSION_RAW: dict[str, Any] = {
    "id": VERSION_ID,
    "namespace": NAMESPACE,
    "spec": {"versionId": "portal-ver-001", "executionId": "EX_1", "locked": True, "snapshotId": "snap-001"},
    "status": {"startTime": "1700100000", "status": "COMPLETED", "transferredSize": "1024"},
    "locations": [],
    "copyStatus": "COPY_STATUS_NONE",
}


@pytest.mark.parametrize("null_paths", [
    (
        "id", "namespace", "locations", "copyStatus",
        "spec.versionId", "spec.executionId", "spec.locked", "spec.snapshotId",
        "status.status", "status.transferredSize",
    ),
    ("spec", "status"),
], ids=["null_nested_fields", "null_spec_and_status"])
def test_parse_version_survives_null_fields(null_paths: tuple[str, ...]) -> None:
    """_parse_version must not crash when every touched field is JSON null; all falsy-typed
    fields fall back to their documented safe defaults, whether the null is a nested
    sub-field of a present spec/status or the spec/status container itself."""
    raw = null_out(_VALID_VERSION_RAW, *null_paths)
    v = _parse_version(raw, WORKLOAD_ID)

    assert v.portal_version_id == ""
    assert v.execution_id == ""
    assert v.locked is False
    assert v.snapshot_id == ""
    assert v.status == VersionStatus.NO_BACKUPS
    assert v.changed_size_bytes == 0


def test_parse_version_survives_null_top_level_fields() -> None:
    """version_id/namespace/locations/copy_status fall back to their safe defaults when the
    top-level id/namespace/locations/copyStatus fields are JSON null."""
    raw = null_out(_VALID_VERSION_RAW, "id", "namespace", "locations", "copyStatus")
    v = _parse_version(raw, WORKLOAD_ID)

    assert v.version_id == ""
    assert v.namespace == ""
    assert v.locations == []
    assert v.copy_status is None


def test_parse_version_copy_reason_survives_null_inner_copy_status() -> None:
    """copy_reason falls back to None when the inner status.copyStatus (used only to resolve
    the reason for a RETRY/SKIPPED/FAILED copy_status) is JSON null, distinct from the outer
    top-level copyStatus that determines copy_status itself."""
    raw = {
        **_VALID_VERSION_RAW,
        "copyStatus": "COPY_STATUS_RETRY",
        "status": {**_VALID_VERSION_RAW["status"], "copyStatus": None},
    }
    v = _parse_version(raw, WORKLOAD_ID)
    assert v.copy_status == VersionCopyStatus.RETRY
    assert v.copy_reason is None


_VALID_EXT_LOCATION_RAW: dict[str, Any] = {
    "namespace": NAMESPACE,
    "locationType": "APV",
    "externalStorageInfo": {
        "storageUid": "ext-uid-001", "displayName": "APV Vault",
        "endpoint": "apv.example.com", "vaultName": "my-bucket",
    },
    "versionUids": ["ver-ext-001"],
}

_VALID_BS_LOCATION_RAW: dict[str, Any] = {
    "namespace": NAMESPACE,
    "locationType": "APPLIANCE",
    "backupServerInfo": {"hostName": "apm-server-01", "address": "192.0.2.1"},
    "versionUids": ["ver-bs-001"],
}


@pytest.mark.parametrize("raw,null_paths", [
    (
        _VALID_EXT_LOCATION_RAW,
        ("namespace", "locationType", "externalStorageInfo.storageUid",
         "externalStorageInfo.displayName", "externalStorageInfo.endpoint"),
    ),
    (
        _VALID_BS_LOCATION_RAW,
        ("namespace", "locationType", "backupServerInfo.hostName", "backupServerInfo.address"),
    ),
    (_VALID_BS_LOCATION_RAW, ("namespace", "locationType", "backupServerInfo")),
], ids=["null_external_storage_fields", "null_backup_server_fields", "null_backup_server_info_dict"])
def test_parse_version_location_survives_null_fields(
    raw: dict[str, Any], null_paths: tuple[str, ...]
) -> None:
    """_parse_version_location must not crash when locationType, externalStorageInfo /
    backupServerInfo sub-fields (or the whole backupServerInfo dict), or namespace are
    JSON null; all fall back to their documented safe defaults."""
    loc_raw = null_out(raw, *null_paths)
    locs = _parse_version_location(loc_raw)

    assert len(locs) == 1
    loc = locs[0]
    assert loc.namespace == ""
    assert loc.location_info.is_remote_storage is False  # locationType null -> "APPLIANCE" default
    assert loc.location_info.identifier == ""
    assert loc.location_info.name == ""
    assert loc.location_info.endpoint == ""


def test_parse_version_location_versionuids_null_returns_no_locations() -> None:
    """_parse_version_location returns an empty list (not a crash) when versionUids is
    JSON null rather than absent."""
    raw = null_out(_VALID_BS_LOCATION_RAW, "versionUids")
    assert _parse_version_location(raw) == []


async def test_lock_version_no_error_when_errors_field_is_null() -> None:
    """lock_version() completes without raising when the API response's errors key is
    JSON null (key present, value null) rather than absent."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)
    version = _make_version_with_locations()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"errors": None, "allFailedSameReason": False}
        await collection.lock_version(version)  # must not raise


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


@pytest.mark.parametrize("copy_status_raw,expected_copy_status", [
    ("COPY_STATUS_NONE", VersionCopyStatus.COMPLETED),
    ("COPY_STATUS_NOT_ENABLED", VersionCopyStatus.NOT_ENABLED),
])
async def test_list_versions_copy_status_mapping(copy_status_raw: str, expected_copy_status: VersionCopyStatus) -> None:
    """copyStatus maps to VersionCopyStatus, with copy_reason None for non-error statuses."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"versions": [_ver_raw(copy_status_raw)], "total": 1}
        versions, _ = await collection.list_versions(SAMPLE_WL_OBJ)

    assert versions[0].copy_status == expected_copy_status
    assert versions[0].copy_reason is None


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
