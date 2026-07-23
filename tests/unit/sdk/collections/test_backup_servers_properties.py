"""Unit tests for BackupServer: sync-status edge cases, data-reduction properties, is_updating, role, tiering-plan fields."""
from __future__ import annotations

import copy
from typing import Any

import pytest

from synology_apm.sdk.collections._shared import _fetch_remote_storage_location
from synology_apm.sdk.collections.backup_servers import BackupServerCollection
from synology_apm.sdk.enums import BackupServerRole, BackupServerType, ServerStatus
from synology_apm.sdk.exceptions import APIError
from synology_apm.sdk.models.location import LocationInfo
from tests.unit.sdk.conftest import BASE_URL, connected_session

SERVERS_URL = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500"

SERVER_ID = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"

SAMPLE_SERVER_RAW: dict[str, Any] = {
    "id": SERVER_ID,
    "namespace": SERVER_ID,
    "spec": {"addr": "192.0.2.1", "type": "DP"},
    "status": {
        "hostName": "apm-server-01",
        "model": "DP320",
        "firmwareVer": "APM 1.2-71845",
        "serial": "SN123",
        "status": "NORMAL",
        "dpStorage": {
            "totalBytes": "7670124400640",
            "backupBytes": "400000000000",
            "systemBytes": "86050291712",
        },
        "storageStatistic": {
            "transferBytes": "1073741824000",
            "usageBytes": "429496729600",
        },
    },
}


# ── sync-status edge cases ──────────────────────────────────────────────────


@pytest.mark.parametrize("sync_status", ["DISCONNECTED", "JOINING_DISCONNECTED"])
async def test_sync_status_disconnected_variants_override_normal_status(sync_status: str) -> None:
    """When spec.syncStatus is DISCONNECTED or JOINING_DISCONNECTED, status should be
    DISCONNECTED even if status.status=NORMAL."""
    raw = {
        "id": "srv-3", "namespace": "ns-3",
        "spec": {"addr": "1.2.3.6", "syncStatus": sync_status},
        "status": {
            "hostName": "APM-SyncFail", "model": "DP100", "firmwareVer": "APM 1.2-0",
            "serial": "SN2", "timezone": "UTC", "status": "NORMAL",
            "dpStorage": {"totalBytes": "0", "backupBytes": "0", "systemBytes": "0"},
            "storageStatistic": {"transferBytes": "0", "usageBytes": "0"},
        },
    }
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    assert servers[0].status == ServerStatus.DISCONNECTED


async def test_sync_status_synced_does_not_force_disconnected() -> None:
    """When spec.syncStatus=SYNCED, status should be parsed normally from status.status."""
    raw = {
        "id": "srv-5", "namespace": "ns-5",
        "spec": {"addr": "1.2.3.8", "syncStatus": "SYNCED"},
        "status": {
            "hostName": "APM-Synced", "model": "DP100", "firmwareVer": "APM 1.2-0",
            "serial": "SN4", "timezone": "UTC", "status": "NORMAL",
            "dpStorage": {"totalBytes": "100", "backupBytes": "10", "systemBytes": "5"},
            "storageStatistic": {"transferBytes": "0", "usageBytes": "0"},
        },
    }
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    assert servers[0].status == ServerStatus.HEALTHY


async def test_spec_sync_status_joining_maps_to_syncing() -> None:
    """When spec.syncStatus=JOINING, status should be SYNCING."""
    raw = {
        "id": "srv-6", "namespace": "ns-6",
        "spec": {"addr": "1.2.3.9", "syncStatus": "JOINING"},
        "status": {
            "hostName": "APM-New", "model": "DP100", "firmwareVer": "APM 1.2-0",
            "serial": "SN5", "timezone": "UTC", "status": "NORMAL",
            "dpStorage": {"totalBytes": "0", "backupBytes": "0", "systemBytes": "0"},
            "storageStatistic": {"transferBytes": "0", "usageBytes": "0"},
        },
    }
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    assert servers[0].status == ServerStatus.SYNCING


@pytest.mark.parametrize("api_status", ["NOTINITIALIZED", "INCOMPATIBLE"])
async def test_notinitialized_and_incompatible_server_map_to_disconnected(api_status: str) -> None:
    """When the API returns NOTINITIALIZED or INCOMPATIBLE, SDK status should be DISCONNECTED."""
    raw = {
        "id": "srv-1", "namespace": "ns-1",
        "spec": {"addr": "1.2.3.4"},
        "status": {
            "hostName": "APM-New", "model": "DP100", "firmwareVer": "APM 1.2-0",
            "serial": "SN0", "status": api_status,
            "dpStorage": {"totalBytes": "0", "backupBytes": "0", "systemBytes": "0"},
            "storageStatistic": {"transferBytes": "0", "usageBytes": "0"},
        },
    }
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    assert servers[0].status == ServerStatus.DISCONNECTED


# ── Data reduction computed properties ──────────────────────────────────────


def test_backup_data_reduction_bytes() -> None:
    """backup_data_reduction_bytes = logical_backup_data_bytes - physical_backup_data_bytes."""
    from synology_apm.sdk.models.backup_server import BackupServer
    server = BackupServer(
        backup_server_id="s", namespace="n", server_type=BackupServerType.DP,
        name="N", hostname="h", model="M", system_version="F", is_updating=False,
        status=ServerStatus.HEALTHY, serial="S",
        storage_total_bytes=0, storage_used_bytes=0,
        logical_backup_data_bytes=1000, physical_backup_data_bytes=400,
    )
    assert server.backup_data_reduction_bytes == 600
    assert abs(server.backup_data_reduction_ratio - 60.0) < 0.01


def test_backup_data_reduction_ratio_zero_logical() -> None:
    """When logical_backup_data_bytes=0, reduction ratio should be 0.0 without dividing by zero."""
    from synology_apm.sdk.models.backup_server import BackupServer
    server = BackupServer(
        backup_server_id="s", namespace="n", server_type=BackupServerType.DP,
        name="N", hostname="h", model="M", system_version="F", is_updating=False,
        status=ServerStatus.HEALTHY, serial="S",
        storage_total_bytes=0, storage_used_bytes=0,
        logical_backup_data_bytes=0, physical_backup_data_bytes=0,
    )
    assert server.backup_data_reduction_ratio == 0.0


def test_backup_data_reduction_bytes_none_when_data_unavailable() -> None:
    """When logical_backup_data_bytes=None, backup_data_reduction_bytes should be None."""
    from synology_apm.sdk.models.backup_server import BackupServer
    server = BackupServer(
        backup_server_id="s", namespace="n", server_type=BackupServerType.DP,
        name="N", hostname="h", model="M", system_version="F", is_updating=False,
        status=ServerStatus.HEALTHY, serial="S",
        storage_total_bytes=None, storage_used_bytes=None,
        logical_backup_data_bytes=None, physical_backup_data_bytes=None,
    )
    assert server.backup_data_reduction_bytes is None
    assert server.backup_data_reduction_ratio == 0.0
    assert server.storage_usage_pct == 0.0


# ── is_updating ─────────────────────────────────────────────────────────────


_BASE_RAW: dict[str, Any] = {
    "id": "srv-upd", "namespace": "ns-upd",
    "spec": {"addr": "1.2.3.100", "type": "DP"},
    "status": {
        "hostName": "APM-Update", "model": "DP320", "firmwareVer": "APM 1.2-71845",
        "serial": "SN9", "status": "NORMAL",
        "dpStorage": {"totalBytes": "100", "backupBytes": "10", "systemBytes": "5"},
        "storageStatistic": {"transferBytes": "0", "usageBytes": "0"},
    },
}


def _raw_with_upgrade_status(upgrade_status: str) -> dict[str, Any]:
    raw = copy.deepcopy(_BASE_RAW)
    raw["status"]["upgrade"] = {"upgradeStatus": upgrade_status}
    return raw


@pytest.mark.parametrize("upgrade_status", ["PRECHECK", "BUILTIN_UPDATING", "DSM_UPDATING", "REBOOTING"])
async def test_is_updating_true_for_active_states(upgrade_status: str) -> None:
    """is_updating should be True when upgradeStatus is one of the active update states."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [_raw_with_upgrade_status(upgrade_status)]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].is_updating is True


@pytest.mark.parametrize("upgrade_status", ["SUCCESS", "UPDATE_AVAILABLE", "UPDATE_SCHEDULED", "FAIL", ""])
async def test_is_updating_false_for_idle_states(upgrade_status: str) -> None:
    """is_updating should be False when upgradeStatus is not an active update state."""
    async with connected_session() as (session, m):
        raw = _raw_with_upgrade_status(upgrade_status)
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].is_updating is False


async def test_is_updating_false_when_upgrade_field_absent() -> None:
    """is_updating should be False when the upgrade object is absent from the API response."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [_BASE_RAW]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].is_updating is False


# ── role field ──────────────────────────────────────────────────────────────


async def test_leader_role_parses_as_primary() -> None:
    """API role=LEADER should be parsed as BackupServerRole.PRIMARY."""
    raw = {**SAMPLE_SERVER_RAW, "role": "LEADER"}
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].role == BackupServerRole.PRIMARY


async def test_replica_role_parses_as_secondary() -> None:
    """API role=REPLICA should be parsed as BackupServerRole.SECONDARY."""
    raw = {**SAMPLE_SERVER_RAW, "role": "REPLICA"}
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].role == BackupServerRole.SECONDARY


async def test_absent_role_is_none() -> None:
    """When the role field is absent from the API response, BackupServer.role should be None."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].role is None


async def test_follower_role_is_none() -> None:
    """API role=FOLLOWER (not mapped) should result in BackupServer.role being None."""
    raw = {**SAMPLE_SERVER_RAW, "role": "FOLLOWER"}
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].role is None


# ── tiering plan fields ─────────────────────────────────────────────────────

_TIERING_PLAN_UID     = "3d5bf700-4bb6-4eec-a709-c15f04cd0af1"
_TIERING_STORAGE_ID   = "external-storage-uuid-001"
_TIERING_PLAN_URL     = f"{BASE_URL}/api/v1/plan/tiering_plan/{_TIERING_PLAN_UID}"
_TIERING_STORAGE_URL  = f"{BASE_URL}/api/v1/external_storage/{_TIERING_STORAGE_ID}"

_TIERING_SERVER_RAW: dict[str, Any] = {
    **SAMPLE_SERVER_RAW,
    "spec": {
        **SAMPLE_SERVER_RAW["spec"],
        "tieringPlanRef": {"kind": "TieringPlan", "uid": _TIERING_PLAN_UID, "namespace": ""},
    },
    "tieringInfo": {"tieringPlanName": "tiering plan 1", "tieringStatus": "NONE"},
}

_TIERING_PLAN_PAYLOAD: dict[str, Any] = {
    "id": _TIERING_PLAN_UID,
    "spec": {
        "name": "tiering plan 1",
        "destination": _TIERING_STORAGE_ID,
        "tieringAfterDays": 9999,
        "schedule": {"runHour": 1, "runMin": 17},
    },
    "tieringInfo": {"protectedServerCount": 1},
}

_TIERING_STORAGE_PAYLOAD: dict[str, Any] = {
    "id": _TIERING_STORAGE_ID,
    "displayName": "tiering-remote",
    "endpoint": "https://s3.example.com:443",
    "vaultName": "tiering-remote",
}


async def test_tiering_plan_fields_parsed_when_assigned() -> None:
    """When spec.tieringPlanRef.uid is present, tiering plan name and destination are fetched and populated."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [_TIERING_SERVER_RAW]})
        m.get(_TIERING_PLAN_URL, payload=_TIERING_PLAN_PAYLOAD)
        m.get(_TIERING_STORAGE_URL, payload=_TIERING_STORAGE_PAYLOAD)
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    server = servers[0]
    assert server.tiering_plan_name == "tiering plan 1"
    assert isinstance(server.tiering_plan_destination, LocationInfo)
    assert server.tiering_plan_destination.is_remote_storage is True
    assert server.tiering_plan_destination.identifier == _TIERING_STORAGE_ID
    assert server.tiering_plan_destination.name == "tiering-remote"
    assert server.tiering_plan_destination.endpoint == "https://s3.example.com:443"
    assert server.tiering_plan_destination.vault == "tiering-remote"


async def test_tiering_plan_fields_none_when_not_assigned() -> None:
    """When spec.tieringPlanRef is null, tiering fields should be None with no extra API calls."""
    raw = {**SAMPLE_SERVER_RAW, "spec": {**SAMPLE_SERVER_RAW["spec"], "tieringPlanRef": None}}
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    server = servers[0]
    assert server.tiering_plan_name is None
    assert server.tiering_plan_destination is None


async def test_tiering_plan_fields_none_when_tiering_ref_absent() -> None:
    """When spec.tieringPlanRef is absent entirely, tiering fields should default to None."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].tiering_plan_name is None
    assert servers[0].tiering_plan_destination is None


async def test_tiering_plan_fields_none_when_plan_no_longer_exists() -> None:
    """A dangling tiering plan reference (plan deleted) falls back to None tiering fields."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [_TIERING_SERVER_RAW]})
        m.get(_TIERING_PLAN_URL, status=404)
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].tiering_plan_name is None
    assert servers[0].tiering_plan_destination is None


async def test_tiering_plan_fetch_server_error_propagates() -> None:
    """A server error while resolving the tiering plan propagates instead of being silently dropped."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [_TIERING_SERVER_RAW]})
        m.get(_TIERING_PLAN_URL, status=500, payload={"error": {"code": 500}})
        with pytest.raises(APIError):
            await BackupServerCollection(session).list()
        await session.disconnect()


async def test_tiering_destination_none_when_external_storage_no_longer_exists() -> None:
    """A dangling tiering destination reference keeps tiering_plan_name but yields destination None."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [_TIERING_SERVER_RAW]})
        m.get(_TIERING_PLAN_URL, payload=_TIERING_PLAN_PAYLOAD)
        m.get(_TIERING_STORAGE_URL, status=404)
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].tiering_plan_name == "tiering plan 1"
    assert servers[0].tiering_plan_destination is None


async def test_fetch_remote_storage_location_survives_null_id_and_endpoint() -> None:
    """_fetch_remote_storage_location falls back to the dest_id and "" endpoint when the
    external storage response's id/endpoint fields are JSON null, while a non-null
    displayName still yields a populated LocationInfo (a null displayName instead yields
    None, same as the dangling-reference case covered elsewhere)."""
    async with connected_session() as (session, m):
        m.get(_TIERING_STORAGE_URL, payload={
            "id": None, "displayName": "tiering-remote", "endpoint": None, "vaultName": None,
        })
        loc = await _fetch_remote_storage_location(session, _TIERING_STORAGE_ID)
        await session.disconnect()

    assert loc is not None
    assert loc.identifier == _TIERING_STORAGE_ID  # id is null -> falls back to dest_id
    assert loc.name == "tiering-remote"
    assert loc.endpoint == ""
    assert loc.vault is None


@pytest.mark.parametrize("total_bytes,used_bytes,expected", [
    (1000, 250, 25.0),
    (0, 100, 0.0),
], ids=["nonzero_total", "zero_total"])
def test_storage_usage_pct(total_bytes: int, used_bytes: int, expected: float) -> None:
    """storage_usage_pct should be used/total*100 when total is non-zero, and 0.0 when the
    reported total capacity is 0."""
    from synology_apm.sdk.models.backup_server import BackupServer

    server = BackupServer(
        backup_server_id="bs-usage-001",
        namespace="ns-usage-001",
        server_type=BackupServerType.DP,
        name="apm-server-01",
        hostname="192.0.2.1",
        model="DP320",
        system_version="APM 1.2-71845",
        status=ServerStatus.HEALTHY,
        is_updating=False,
        serial="SN001",
        storage_total_bytes=total_bytes,
        storage_used_bytes=used_bytes,
        logical_backup_data_bytes=None,
        physical_backup_data_bytes=None,
    )
    assert server.storage_usage_pct == expected
