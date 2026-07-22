"""Unit tests for BackupServerCollection: list/get/get_by_name, status/type filters, NAS field parsing."""
from __future__ import annotations

import copy
from datetime import time
from typing import Any
from unittest.mock import patch

import pytest
from aiointercept import aiointercept
from yarl import URL

from synology_apm.sdk.collections.backup_servers import BackupServerCollection
from synology_apm.sdk.enums import BackupServerType, CopyReason, ServerStatus, VersionCopyStatus
from synology_apm.sdk.exceptions import InvalidOperationError, ResourceNotFoundError
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.tiering_plan import TieringPlan
from tests.unit.sdk.conftest import (
    BASE_URL,
    LOGIN_OK,
    LOGIN_URL,
    assert_resource_error,
    connected_session,
    make_session,
)

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

SAMPLE_SERVER_OFFLINE: dict[str, Any] = {
    "id": "offline-server-id",
    "namespace": "offline-ns",
    "spec": {"addr": "192.0.2.4", "type": "DP"},
    "status": {
        "hostName": "apm-server-dr",
        "model": "DP100",
        "firmwareVer": "APM 1.2-71000",
        "serial": "SN456",
        "status": "DISCONNECTED",
        "dpStorage": {
            "totalBytes": "0",
            "backupBytes": "0",
            "systemBytes": "0",
        },
        "storageStatistic": {
            "transferBytes": "0",
            "usageBytes": "0",
        },
    },
}


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_parses_backup_server_fields() -> None:
    async with connected_session() as (session, m):

        m.get(SERVERS_URL, payload={"backupServers": [SAMPLE_SERVER_RAW], "total": 1})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    assert total == 1
    server = servers[0]
    assert server.backup_server_id == SERVER_ID
    assert server.server_type == BackupServerType.DP
    assert server.name == "apm-server-01"
    assert server.hostname == "192.0.2.1"
    assert server.model == "DP320"
    assert server.system_version == "APM 1.2-71845"
    assert server.is_updating is False
    assert server.serial == "SN123"
    assert server.status == ServerStatus.HEALTHY
    assert server.storage_total_bytes == 7670124400640
    assert server.storage_used_bytes == 400000000000 + 86050291712  # backupBytes + systemBytes
    assert server.logical_backup_data_bytes == 1073741824000
    assert server.physical_backup_data_bytes == 429496729600


async def test_list_offline_server_is_disconnected() -> None:
    async with connected_session() as (session, m):

        m.get(SERVERS_URL, payload={"backupServers": [SAMPLE_SERVER_OFFLINE]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    assert servers[0].status == ServerStatus.DISCONNECTED


async def test_list_filter_by_name_contains_sends_keyword_param() -> None:
    """name_contains should be passed to the API as a keyword query parameter (server-side filtering)."""
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&keyword=apm-server-01"
        m.get(keyword_url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(name_contains="apm-server-01")
        await session.disconnect()

    assert len(result) == 1
    assert result[0].name == "apm-server-01"


async def test_list_name_contains_passes_keyword_to_api() -> None:
    """When name_contains is provided, the API receives the keyword param; case handling is done server-side."""
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&keyword=APM-SERVER-01"
        m.get(keyword_url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(name_contains="APM-SERVER-01")
        await session.disconnect()

    assert len(result) == 1


# ── status_filter ──────────────────────────────────────────────────────────

async def test_status_filter_healthy_sends_status_normal() -> None:
    """status_filter=[HEALTHY] → API receives status=NORMAL."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&status=NORMAL"
        m.get(url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=[ServerStatus.HEALTHY])
        await session.disconnect()
    assert len(result) == 1


async def test_status_filter_warning_sends_status_attention() -> None:
    """status_filter=[WARNING] → API receives status=ATTENTION."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&status=ATTENTION"
        m.get(url, payload={"backupServers": []})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=[ServerStatus.WARNING])
        await session.disconnect()
    assert result == []


async def test_status_filter_critical_sends_status_danger() -> None:
    """status_filter=[CRITICAL] → API receives status=DANGER."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&status=DANGER"
        m.get(url, payload={"backupServers": []})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=[ServerStatus.CRITICAL])
        await session.disconnect()
    assert result == []


async def test_status_filter_disconnected_sends_sync_status_params() -> None:
    """status_filter=[DISCONNECTED] → API receives syncStatus=DISCONNECTED&syncStatus=JOINING_DISCONNECTED."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&syncStatus=DISCONNECTED&syncStatus=JOINING_DISCONNECTED"
        m.get(url, payload={"backupServers": [SAMPLE_SERVER_OFFLINE]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=[ServerStatus.DISCONNECTED])
        await session.disconnect()
    assert len(result) == 1
    assert result[0].status == ServerStatus.DISCONNECTED


async def test_status_filter_syncing_sends_sync_status_joining() -> None:
    """status_filter=[SYNCING] → API receives syncStatus=JOINING."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&syncStatus=JOINING"
        m.get(url, payload={"backupServers": []})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=[ServerStatus.SYNCING])
        await session.disconnect()
    assert result == []


async def test_status_filter_multiple_values_sends_combined_params() -> None:
    """status_filter=[HEALTHY, DISCONNECTED] → API receives status=NORMAL&syncStatus=DISCONNECTED&syncStatus=JOINING_DISCONNECTED."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&status=NORMAL&syncStatus=DISCONNECTED&syncStatus=JOINING_DISCONNECTED"
        m.get(url, payload={"backupServers": [SAMPLE_SERVER_RAW, SAMPLE_SERVER_OFFLINE]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=[ServerStatus.HEALTHY, ServerStatus.DISCONNECTED])
        await session.disconnect()
    assert len(result) == 2


async def test_status_filter_deduplicates_repeated_values() -> None:
    """Duplicate values in status_filter should send status=NORMAL only once."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&status=NORMAL"
        m.get(url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=[ServerStatus.HEALTHY, ServerStatus.HEALTHY])
        await session.disconnect()
    assert len(result) == 1


async def test_status_filter_none_does_not_add_filter_params() -> None:
    """When status_filter=None, no filter query parameters should be added."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(status_filter=None)
        await session.disconnect()
    assert len(result) == 1


# ── type_filter ────────────────────────────────────────────────────────────


async def test_type_filter_dp_sends_type_dp() -> None:
    """type_filter=[DP] → API receives type=DP."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&type=DP"
        m.get(url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(type_filter=[BackupServerType.DP])
        await session.disconnect()
    assert len(result) == 1


async def test_type_filter_nas_sends_type_nas() -> None:
    """type_filter=[NAS] → API receives type=NAS."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&type=NAS"
        m.get(url, payload={"backupServers": []})
        collection = BackupServerCollection(session)
        result, total = await collection.list(type_filter=[BackupServerType.NAS])
        await session.disconnect()
    assert result == []


async def test_type_filter_multiple_sends_both_type_params() -> None:
    """type_filter=[DP, NAS] → API receives type=DP&type=NAS."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&type=DP&type=NAS"
        m.get(url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(type_filter=[BackupServerType.DP, BackupServerType.NAS])
        await session.disconnect()
    assert len(result) == 1


async def test_type_filter_deduplicates_repeated_values() -> None:
    """Duplicate values in type_filter should send type=DP only once."""
    async with connected_session() as (session, m):
        url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500&type=DP"
        m.get(url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(type_filter=[BackupServerType.DP, BackupServerType.DP])
        await session.disconnect()
    assert len(result) == 1


async def test_type_filter_none_does_not_add_type_param() -> None:
    """When type_filter=None, no type query parameter should be added."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        collection = BackupServerCollection(session)
        result, total = await collection.list(type_filter=None)
        await session.disconnect()
    assert len(result) == 1


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_returns_backup_server_by_id() -> None:
    async with connected_session() as (session, m):

        m.get(
            f"{BASE_URL}/api/v1/infra/backup_server/{SERVER_ID}",
            payload={"backupServer": SAMPLE_SERVER_RAW},
        )
        collection = BackupServerCollection(session)
        server = await collection.get(SERVER_ID)
        await session.disconnect()

    assert server.backup_server_id == SERVER_ID
    assert server.name == "apm-server-01"
    assert server.hostname == "192.0.2.1"
    assert server.server_type == BackupServerType.DP
    assert server.status == ServerStatus.HEALTHY
    assert server.storage_total_bytes == 7670124400640
    assert server.storage_used_bytes == 400000000000 + 86050291712


async def test_get_by_name_returns_server_by_display_name() -> None:
    """get_by_name() should do a case-insensitive exact match on name (hostName)."""
    session = make_session()
    page_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=100&keyword=apm-server-01"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(page_url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        server = await BackupServerCollection(session).get_by_name("apm-server-01")
        await session.disconnect()

    assert server.backup_server_id == SERVER_ID
    assert server.name == "apm-server-01"


async def test_get_by_name_returns_server_by_hostname() -> None:
    """get_by_name() should do a case-insensitive exact match on hostname."""
    session = make_session()
    page_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=100&keyword=192.0.2.1"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(page_url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        server = await BackupServerCollection(session).get_by_name("192.0.2.1")
        await session.disconnect()

    assert server.hostname == "192.0.2.1"


async def test_get_by_name_does_not_match_server_id() -> None:
    """get_by_name() should not match on backup_server_id; ID lookup goes through get()."""
    session = make_session()
    page_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=100&keyword={SERVER_ID}"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(page_url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await BackupServerCollection(session).get_by_name(SERVER_ID)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="BackupServer", resource_id=SERVER_ID)


async def test_get_by_name_case_insensitive_name() -> None:
    """get_by_name() name matching should be case-insensitive."""
    session = make_session()
    page_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=100&keyword=APM-SERVER-01"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(page_url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        server = await BackupServerCollection(session).get_by_name("APM-SERVER-01")  # different case
        await session.disconnect()

    assert server.name == "apm-server-01"


async def test_get_by_name_raises_when_keyword_matches_but_name_does_not() -> None:
    """Should raise ResourceNotFoundError when keyword returns results but none is an exact match (no fuzzy return)."""
    session = make_session()
    page_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=100&keyword=apm-server"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        # API returns "apm-server-01" but identity is "apm-server" (partial match)
        m.get(page_url, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await BackupServerCollection(session).get_by_name("apm-server")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="BackupServer", resource_id="apm-server")


async def test_get_by_name_raises_when_empty_result() -> None:
    """Should raise ResourceNotFoundError when API returns an empty array."""
    session = make_session()
    page_url = f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=100&keyword=no-such"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(page_url, payload={"backupServers": []})
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await BackupServerCollection(session).get_by_name("no-such")
        await session.disconnect()

    assert exc_info.value.resource_type == "BackupServer"
    assert exc_info.value.resource_id == "no-such"


async def test_list_parses_description_from_spec() -> None:
    """description should be parsed from spec.description; defaults to empty string when absent."""
    with_desc = copy.deepcopy(SAMPLE_SERVER_RAW)
    with_desc["spec"]["description"] = "Primary lab server"
    without_desc = copy.deepcopy(SAMPLE_SERVER_RAW)
    without_desc["id"] = "other-id"
    without_desc["namespace"] = "other-ns"
    # spec has no description key

    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [with_desc, without_desc]})
        collection = BackupServerCollection(session)
        servers, _ = await collection.list()
        await session.disconnect()

    assert servers[0].description == "Primary lab server"
    assert servers[1].description == ""


async def test_get_by_name_paginates_to_next_page() -> None:
    """When the first page has no exact match and is full (total > page_size), should continue to the second page."""
    from synology_apm.sdk.models.backup_server import BackupServer

    # First page: 100 entries, none is the target (total=200 triggers pagination)
    page1_servers = [
        BackupServer(
            backup_server_id=f"s-{i}", namespace="ns", server_type=BackupServerType.DP,
            name=f"Server-{i:03d}", hostname=f"host{i}.example.com", model="M",
            system_version="v", serial="S", status=ServerStatus.HEALTHY,
            is_updating=False, storage_total_bytes=None, storage_used_bytes=None,
            logical_backup_data_bytes=None, physical_backup_data_bytes=None,
        )
        for i in range(100)
    ]
    # Second page: contains the target
    page2_servers = [BackupServer(
        backup_server_id=SERVER_ID, namespace=SERVER_ID, server_type=BackupServerType.DP,
        name="apm-server-01", hostname="192.0.2.1", model="DP320",
        system_version="APM 1.2-71845", serial="SN123",
        status=ServerStatus.HEALTHY, is_updating=False,
        storage_total_bytes=None, storage_used_bytes=None,
        logical_backup_data_bytes=None, physical_backup_data_bytes=None,
    )]

    call_count = 0

    async def fake_list(
        name_contains: str | None = None, limit: int = 500, offset: int = 0, **kwargs: object
    ) -> tuple[list[BackupServer], int]:
        nonlocal call_count
        result = [(page1_servers, 200), (page2_servers, 200)][call_count]
        call_count += 1
        return result

    session = make_session()
    collection = BackupServerCollection(session)
    with patch.object(collection, "list", side_effect=fake_list):
        server = await collection.get_by_name("apm-server-01")

    assert server.name == "apm-server-01"
    assert call_count == 2


async def test_get_raises_not_found_for_empty_response() -> None:
    async with connected_session() as (session, m):

        m.get(
            f"{BASE_URL}/api/v1/infra/backup_server/bad-id",
            payload={"backupServer": {}},
        )
        collection = BackupServerCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("bad-id")
        await session.disconnect()

    assert exc_info.value.resource_type == "BackupServer"
    assert exc_info.value.resource_id == "bad-id"


async def test_get_raises_not_found_for_http500_errorcode_1402() -> None:
    """HTTP 500 with error.details[0].errorCode=1402 should raise ResourceNotFoundError with the server ID."""
    async with connected_session() as (session, m):

        m.get(
            f"{BASE_URL}/api/v1/infra/backup_server/no-such-id",
            status=500,
            payload={
                "error": {
                    "code": 500,
                    "message": "backup server not found",
                    "details": [{"errorCode": 1402, "message": "backup server not found"}],
                }
            },
        )
        collection = BackupServerCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("no-such-id")
        await session.disconnect()

    assert exc_info.value.resource_type == "BackupServer"
    assert exc_info.value.resource_id == "no-such-id"


# ── NAS server parsing ──────────────────────────────────────────────────────

NAS_SERVER_RAW: dict[str, Any] = {
    "id": "nas-server-id",
    "namespace": "nas-ns",
    "spec": {"addr": "10.0.0.10", "type": "NAS"},
    "status": {
        "hostName": "nas-server-01",
        "model": "DS1823xs+",
        "firmwareVer": "7.2.2-72806",
        "serial": "NAS001",
        "status": "NORMAL",
        "nasStorage": [
            {"totalBytes": "8000000000000", "usedBytes": "3000000000000"},
            {"totalBytes": "4000000000000", "usedBytes": "1000000000000"},
        ],
        "storageStatistic": {
            "transferBytes": "500000000000",
            "usageBytes": "200000000000",
        },
    },
}

NAS_SERVER_EMPTY_STORAGE_RAW: dict[str, Any] = {
    "id": "nas-empty-id",
    "namespace": "nas-empty-ns",
    "spec": {"addr": "10.0.0.11", "type": "NAS"},
    "status": {
        "hostName": "nas-server-02",
        "model": "DS923+",
        "firmwareVer": "7.2.1-69057",
        "serial": "NAS002",
        "status": "ATTENTION",
        "nasStorage": [],
        "storageStatistic": {},
    },
}

DP_EMPTY_STORAGE_RAW: dict[str, Any] = {
    "id": "dp-empty-id",
    "namespace": "dp-empty-ns",
    "spec": {"addr": "dp.corp.com", "type": "DP"},
    "status": {
        "hostName": "DP-Empty",
        "model": "DP100",
        "firmwareVer": "APM 1.2-71845",
        "serial": "DP001",
        "status": "ATTENTION",
        "dpStorage": {},
        "storageStatistic": {},
    },
}


async def test_nas_server_system_version_is_none() -> None:
    """NAS server system_version should be None (DP-only field)."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [NAS_SERVER_RAW]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    assert servers[0].system_version is None


async def test_nas_server_storage_from_nas_storage() -> None:
    """NAS server storage_total_bytes and storage_used_bytes should be summed from nasStorage[]."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [NAS_SERVER_RAW]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    server = servers[0]
    assert server.storage_total_bytes == 8000000000000 + 4000000000000
    assert server.storage_used_bytes == 3000000000000 + 1000000000000
    assert server.logical_backup_data_bytes == 500000000000
    assert server.physical_backup_data_bytes == 200000000000


async def test_nas_empty_storage_returns_none() -> None:
    """When nasStorage=[], storage_total_bytes and storage_used_bytes should be None."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [NAS_SERVER_EMPTY_STORAGE_RAW]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    server = servers[0]
    assert server.storage_total_bytes is None
    assert server.storage_used_bytes is None
    assert server.logical_backup_data_bytes is None
    assert server.physical_backup_data_bytes is None


async def test_dp_empty_storage_returns_none() -> None:
    """When dpStorage={} storage fields should be None; when storageStatistic={} data fields should be None."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [DP_EMPTY_STORAGE_RAW]})
        collection = BackupServerCollection(session)
        servers, total = await collection.list()
        await session.disconnect()

    server = servers[0]
    assert server.storage_total_bytes is None
    assert server.storage_used_bytes is None
    assert server.logical_backup_data_bytes is None
    assert server.physical_backup_data_bytes is None
    assert server.system_version == "APM 1.2-71845"


# ── tiering_status parsing ──────────────────────────────────────────────────


async def test_tiering_status_completed_when_none_status() -> None:
    """tieringInfo.tieringStatus='NONE' with no pending → tiering_status.status == COMPLETED."""
    import copy
    server_raw = copy.deepcopy(SAMPLE_SERVER_RAW)
    server_raw["tieringInfo"] = {"tieringStatus": "NONE", "pendingVersionCount": "0"}
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [server_raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    ts = servers[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.COMPLETED
    assert ts.reason is None
    assert ts.pending_version_count == 0


async def test_tiering_status_in_progress_with_pending() -> None:
    """tieringInfo.tieringStatus='DOING' → tiering_status.status == IN_PROGRESS with counts."""
    import copy
    server_raw = copy.deepcopy(SAMPLE_SERVER_RAW)
    server_raw["tieringInfo"] = {
        "tieringStatus": "DOING",
        "pendingVersionCount": "7",
        "remainingBytes": "4194304",
    }
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [server_raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    ts = servers[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.IN_PROGRESS
    assert ts.pending_version_count == 7
    assert ts.remaining_bytes == 4194304


async def test_tiering_status_retry_with_reason() -> None:
    """tieringInfo.tieringStatus='AUTHENTICATION_FAIL' → tiering_status RETRY + AUTH_FAILED."""
    import copy
    server_raw = copy.deepcopy(SAMPLE_SERVER_RAW)
    server_raw["tieringInfo"] = {
        "tieringStatus": "AUTHENTICATION_FAIL",
        "pendingVersionCount": "1",
    }
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [server_raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    ts = servers[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.RETRY
    assert ts.reason == CopyReason.AUTH_FAILED


async def test_tiering_status_none_when_no_tiering_info() -> None:
    """When tieringInfo is absent, tiering_status should be None."""
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [SAMPLE_SERVER_RAW]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].tiering_status is None


# ── change_tiering_plan() ──────────────────────────────────────────────────

CHANGE_TIERING_URL = f"{BASE_URL}/api/v1/infra/backup_server/tiering_plan"

SAMPLE_DP_SERVER = BackupServer(
    backup_server_id="bs-dp-001",
    namespace="ns-dp-001",
    server_type=BackupServerType.DP,
    name="apm-server-01",
    hostname="192.0.2.1",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN001",
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

SAMPLE_NAS_SERVER = BackupServer(
    backup_server_id="bs-nas-001",
    namespace="ns-nas-001",
    server_type=BackupServerType.NAS,
    name="nas-server-01",
    hostname="10.0.0.10",
    model="DS720+",
    system_version=None,
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN002",
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

SAMPLE_TIERING_PLAN = TieringPlan(
    plan_id="f56f8969-a831-47a6-9de0-279696dafea6",
    name="30-Day Tiering",
    description="",
    tiering_after_days=30,
    daily_check_time=time(2, 0),
    destination=None,
    server_count=1,
    tiering_status=None,
    run_schedule_by_controller_time=False,
)


async def test_change_tiering_plan_apply_sends_correct_body() -> None:
    """Applying a plan sends nsUidPairs + tieringPlanId in the PUT body."""
    async with connected_session() as (session, m):
        m.put(CHANGE_TIERING_URL, payload={"success": True})
        await BackupServerCollection(session).change_tiering_plan(SAMPLE_DP_SERVER, SAMPLE_TIERING_PLAN)
        await session.disconnect()

    assert ("PUT", URL(CHANGE_TIERING_URL)) in m.requests
    body = m.requests[("PUT", URL(CHANGE_TIERING_URL))][0].kwargs["json"]
    assert body == {
        "nsUidPairs": [{"namespace": "ns-dp-001", "uid": "bs-dp-001"}],
        "tieringPlanId": "f56f8969-a831-47a6-9de0-279696dafea6",
    }


async def test_change_tiering_plan_remove_omits_plan_id() -> None:
    """Removing a plan sends nsUidPairs only (no tieringPlanId key) in the PUT body."""
    async with connected_session() as (session, m):
        m.put(CHANGE_TIERING_URL, payload={"success": True})
        await BackupServerCollection(session).change_tiering_plan(SAMPLE_DP_SERVER, None)
        await session.disconnect()

    body = m.requests[("PUT", URL(CHANGE_TIERING_URL))][0].kwargs["json"]
    assert body == {"nsUidPairs": [{"namespace": "ns-dp-001", "uid": "bs-dp-001"}]}
    assert "tieringPlanId" not in body


async def test_change_tiering_plan_nas_raises_invalid_operation() -> None:
    """change_tiering_plan raises InvalidOperationError for NAS servers without calling the API."""
    async with connected_session() as (session, m):
        with pytest.raises(InvalidOperationError) as exc_info:
            await BackupServerCollection(session).change_tiering_plan(SAMPLE_NAS_SERVER, SAMPLE_TIERING_PLAN)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="BackupServer", resource_id="bs-nas-001")
    assert ("PUT", URL(CHANGE_TIERING_URL)) not in m.requests


async def test_tiering_status_unknown_raw_status_is_none() -> None:
    """An unrecognized tieringInfo.tieringStatus string yields tiering_status=None."""
    import copy
    server_raw = copy.deepcopy(SAMPLE_SERVER_RAW)
    server_raw["tieringInfo"] = {"tieringStatus": "SOME_UNKNOWN_STATUS", "pendingVersionCount": "1"}
    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [server_raw]})
        servers, _ = await BackupServerCollection(session).list()
        await session.disconnect()

    assert servers[0].name == "apm-server-01"
    assert servers[0].tiering_status is None


async def test_list_unparsable_tiering_plan_propagates() -> None:
    """A tiering plan response that fails to parse propagates instead of being silently omitted."""
    import copy
    good_server = copy.deepcopy(SAMPLE_SERVER_RAW)
    good_server["spec"]["tieringPlanRef"] = {"uid": "plan-good"}
    bad_server = copy.deepcopy(SAMPLE_SERVER_OFFLINE)
    bad_server["spec"]["tieringPlanRef"] = {"uid": "plan-bad"}

    good_plan_raw = {
        "id": "plan-good",
        "spec": {
            "name": "30-Day Tiering", "description": "", "destination": "",
            "schedule": {"runHour": 1, "runMin": 0}, "tieringAfterDays": 30,
        },
        "tieringInfo": {},
    }
    bad_plan_raw = {"spec": {"name": "broken"}}  # missing "id" → parse error

    async with connected_session() as (session, m):
        m.get(SERVERS_URL, payload={"backupServers": [good_server, bad_server], "total": 2})
        m.get(f"{BASE_URL}/api/v1/plan/tiering_plan/plan-good", payload=good_plan_raw)
        m.get(f"{BASE_URL}/api/v1/plan/tiering_plan/plan-bad", payload=bad_plan_raw)
        with pytest.raises(KeyError):
            await BackupServerCollection(session).list()
        await session.disconnect()
