"""Unit tests for APMClient."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aioresponses import aioresponses
from yarl import URL

from synology_apm.sdk.client import APMClient
from synology_apm.sdk.collections.activities import ActivityCollection
from synology_apm.sdk.collections.backup_servers import BackupServerCollection
from synology_apm.sdk.collections.hypervisors import HypervisorCollection
from synology_apm.sdk.collections.logs import LogCollection
from synology_apm.sdk.collections.m365 import M365Collection
from synology_apm.sdk.collections.machine import MachineCollection
from synology_apm.sdk.collections.protection_plans import ProtectionPlanCollection
from synology_apm.sdk.collections.remote_storages import RemoteStorageCollection
from synology_apm.sdk.collections.retirement_plans import RetirementPlanCollection
from synology_apm.sdk.collections.saas import SaasCollection
from synology_apm.sdk.collections.tiering_plans import TieringPlanCollection
from synology_apm.sdk.enums import BackupServerRole, WorkloadStatType
from synology_apm.sdk.exceptions import APIError, AuthenticationError, NotManagementServerError

BASE_URL = "https://fake-apm.test"
HOST = "fake-apm.test"
WEBAPI_URL = f"{BASE_URL}/webapi/entry.cgi"
ME_URL = f"{BASE_URL}/api/v1/infra/backup_server/me"
AUTH_TYPE_OK = {"success": True, "data": {"logintype": "local"}}
LOGIN_OK = {"success": True, "data": {"sid": "abc", "synotoken": "tok"}}
LOGOUT_OK: dict[str, Any] = {}

ME_OK = {
    "id": "bs-me",
    "namespace": "ns-me",
    "role": "LEADER",
    "spec": {"addr": "fake-apm.test", "type": "DP"},
    "status": {
        "hostName": "APM-Server",
        "model": "DP320",
        "firmwareVer": "APM 1.2-71845",
        "serial": "SN-ME",
        "status": "NORMAL",
    },
}


def make_client(**kwargs: Any) -> APMClient:
    return APMClient(HOST, "user", "pass", verify_ssl=False, **kwargs)


# ── Collection properties ──────────────────────────────────────────────────


@pytest.mark.parametrize("attr,expected_type", [
    ("machine", MachineCollection),
    ("m365", M365Collection),
    ("activities", ActivityCollection),
    ("backup_servers", BackupServerCollection),
    ("saas", SaasCollection),
    ("retirement_plans", RetirementPlanCollection),
    ("plans", ProtectionPlanCollection),
    ("remote_storages", RemoteStorageCollection),
    ("hypervisors", HypervisorCollection),
    ("logs", LogCollection),
    ("tiering_plans", TieringPlanCollection),
])
def test_collection_property_returns_correct_type(attr: str, expected_type: type) -> None:
    client = make_client()
    assert isinstance(getattr(client, attr), expected_type)


def test_my_server_raises_when_not_connected() -> None:
    """Accessing my_server before connect() should raise AuthenticationError."""
    client = make_client()
    with pytest.raises(AuthenticationError, match="Not connected"):
        _ = client.my_server


def test_collection_properties_return_same_instance() -> None:
    """Accessing the same collection property multiple times on the same APMClient should return the same object."""
    client = make_client()
    assert client.machine is client.machine
    assert client.m365 is client.m365
    assert client.saas is client.saas
    assert client.plans is client.plans
    assert client.retirement_plans is client.retirement_plans
    assert client.remote_storages is client.remote_storages
    assert client.hypervisors is client.hypervisors
    assert client.logs is client.logs


# ── Context manager ────────────────────────────────────────────────────────

LOGIN_URL = (
    "https://fake-apm.test/webapi/entry.cgi"
    "?account=user&api=SYNO.API.Auth&client=browser"
    "&enable_syno_token=yes&method=login&passwd=pass&session=webui&version=6"
)


async def test_aenter_calls_connect() -> None:
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        client = make_client()
        result = await client.__aenter__()
        assert result is client
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        await client.__aexit__(None, None, None)


async def test_aexit_calls_disconnect() -> None:
    logout_url = f"{BASE_URL}/api/v1/preference/logout"
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        client = make_client()
        await client.__aenter__()
        m.get(logout_url, payload=LOGOUT_OK)
        await client.__aexit__(None, None, None)
        assert ("GET", URL(logout_url)) in m.requests


async def test_async_with_pattern() -> None:
    logout_url = f"{BASE_URL}/api/v1/preference/logout"
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(logout_url, payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            assert apm is not None
        assert ("GET", URL(logout_url)) in m.requests


async def test_context_manager_disconnects_on_exception() -> None:
    """Even when an exception is raised inside the with block, __aexit__ should still close the connection."""
    logout_url = f"{BASE_URL}/api/v1/preference/logout"
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(logout_url, payload=LOGOUT_OK)
        with pytest.raises(ValueError):
            async with APMClient(HOST, "user", "pass", verify_ssl=False):
                raise ValueError("test error")
        assert ("GET", URL(logout_url)) in m.requests


async def test_connect_failure_propagates_authentication_error() -> None:
    with aioresponses() as m:
        m.get(LOGIN_URL, payload={"success": False, "error": {"code": 400}})
        client = make_client()
        with pytest.raises(AuthenticationError):
            await client.connect()


async def test_connect_raises_api_error_on_non_json_login_response() -> None:
    """connect() raises APIError when the login endpoint returns a non-JSON body."""
    with aioresponses() as m:
        m.get(LOGIN_URL, body=b"<html>Not an APM device</html>", status=200)
        client = make_client()
        with pytest.raises(APIError) as exc_info:
            await client.connect()
    assert "cannot connect" in exc_info.value.message.lower()


# ── management server verification ────────────────────────────────────────


async def test_my_server_property_set_after_connect() -> None:
    """apm.my_server is populated after a successful connect()."""
    logout_url = f"{BASE_URL}/api/v1/preference/logout"
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(logout_url, payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            assert apm.my_server.name == "APM-Server"
            assert apm.my_server.role == BackupServerRole.PRIMARY
            assert apm.my_server.system_version == "APM 1.2-71845"


async def test_connect_raises_not_management_server_when_not_apm() -> None:
    """connect() raises NotManagementServerError when get_me() returns 404 (not an APM host)."""
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, status=404, payload={"message": "not found"})
        client = make_client()
        with pytest.raises(NotManagementServerError) as exc_info:
            await client.connect()
    assert "not an apm server" in exc_info.value.message.lower()


async def test_connect_raises_not_management_server_when_replica_role() -> None:
    """connect() raises NotManagementServerError when the server's role is REPLICA."""
    replica_me = {**ME_OK, "role": "REPLICA"}
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=replica_me)
        client = make_client()
        with pytest.raises(NotManagementServerError) as exc_info:
            await client.connect()
    assert "not the primary management server" in exc_info.value.message.lower()


async def test_connect_session_cleanup_on_management_server_error() -> None:
    """connect() calls logout when the management server check fails."""
    logout_url = f"{BASE_URL}/api/v1/preference/logout"
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, status=404, payload={"message": "not found"})
        m.get(logout_url, payload=LOGOUT_OK)
        client = make_client()
        with pytest.raises(NotManagementServerError):
            await client.connect()
        assert ("GET", URL(logout_url)) in m.requests


async def test_connect_baseexception_cleanup_disconnects_and_reraises() -> None:
    """connect() BaseException handler disconnects the session and re-raises non-ResourceNotFound errors."""
    logout_url = f"{BASE_URL}/api/v1/preference/logout"
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, status=500, payload={"message": "internal server error"})
        m.get(logout_url, payload=LOGOUT_OK)
        client = make_client()
        with pytest.raises(APIError):
            await client.connect()
        assert ("GET", URL(logout_url)) in m.requests


async def test_download_file_saves_content(tmp_path: Path) -> None:
    """APMClient.download_file() saves the downloaded content to the specified path."""
    dest = tmp_path / "out.pst"
    download_url = f"{BASE_URL}/portal/download/token"
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(download_url, body=b"binary data")
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            await apm.download_file(download_url, str(dest))

    assert dest.read_bytes() == b"binary data"


# ── get_site_info() ────────────────────────────────────────────────────────


async def test_get_site_info_returns_complete_site_info() -> None:
    """get_site_info() calls four endpoints in parallel then paginates backup servers for roles."""
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            m.get(f"{BASE_URL}/api/v1/license/info", payload={"uuid": "site-uuid-001"})
            m.get(f"{BASE_URL}/api/v1/cluster/site_info", payload={"externalAddress": "apm.corp.com", "port": "443"})
            m.get(
                f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500",
                payload={
                    "backupServers": [
                        {
                            "id": "bs-leader", "namespace": "ns-leader",
                            "role": "LEADER",
                            "spec": {"addr": "192.0.2.1", "type": "DP"},
                            "status": {
                                "hostName": "apm-server-01", "model": "DP320",
                                "firmwareVer": "APM 1.2-71845", "serial": "SN123",
                                "status": "NORMAL",
                            },
                        },
                        {
                            "id": "bs-replica", "namespace": "ns-replica",
                            "role": "REPLICA",
                            "spec": {"addr": "192.0.2.2", "type": "DP"},
                            "status": {
                                "hostName": "apm-server-02", "model": "DP320",
                                "firmwareVer": "APM 1.2-71845", "serial": "SN456",
                                "status": "NORMAL",
                            },
                        },
                    ],
                    "total": 2,
                },
            )
            m.get(
                f"{BASE_URL}/api/v1/infra/backup_server/storage_statistics",
                payload={"transferBytes": 1000, "backupServerUsageBytes": 600},
            )
            m.get(
                f"{BASE_URL}/api/v1/dashboard/get_workload_statistics",
                payload={"workloadStatistics": [
                    {"workloadType": "MACHINE_PC",  "successCount": "1", "warningCount": "0", "errorCount": "0", "noBackupCount": "0", "dataUsage": "500"},
                    {"workloadType": "MACHINE_VM",  "successCount": "2", "warningCount": "1", "errorCount": "0", "noBackupCount": "0", "dataUsage": "1000"},
                    {"workloadType": "APPLICATION_M365", "successCount": "5", "warningCount": "0", "errorCount": "0", "noBackupCount": "0", "dataUsage": "2000"},
                    {"workloadType": "FUTURE_TYPE", "successCount": "9", "warningCount": "0", "errorCount": "0", "noBackupCount": "0", "dataUsage": "9000"},
                ]},
            )
            site = await apm.get_site_info()
            m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)

    assert site.site_uuid == "site-uuid-001"
    assert site.external_address == "apm.corp.com"
    assert site.port == "443"
    assert site.primary_management_server is not None
    assert site.primary_management_server.name == "apm-server-01"
    assert site.primary_management_server.model == "DP320"
    assert site.primary_management_server.system_version == "APM 1.2-71845"
    assert site.primary_management_server.serial == "SN123"
    assert site.secondary_management_server is not None
    assert site.secondary_management_server.name == "apm-server-02"
    assert site.site_storage.logical_backup_data_bytes == 1000
    assert site.site_storage.physical_backup_data_bytes == 600
    assert site.workload_usage.total_count == 9   # 1 + 3 + 5
    assert site.workload_usage.total_protected_data_bytes == 3500  # 500 + 1000 + 2000
    by_type = {s.workload_type: s for s in site.workload_usage.by_type}
    # the unrecognized FUTURE_TYPE entry is skipped entirely
    assert len(by_type) == 3
    assert by_type[WorkloadStatType.MACHINE_PC].total_count == 1
    assert by_type[WorkloadStatType.MACHINE_VM].total_count == 3
    assert by_type[WorkloadStatType.M365].protected_data_bytes == 2000


async def test_get_site_info_paginates_all_backup_server_pages() -> None:
    """get_site_info() should continue paging until LEADER and REPLICA are found or all pages exhausted."""
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            m.get(f"{BASE_URL}/api/v1/license/info", payload={"uuid": "site-uuid-002"})
            m.get(f"{BASE_URL}/api/v1/cluster/site_info", payload={"externalAddress": "", "port": ""})
            # Page 1: 500 regular servers, no LEADER yet
            regular = {
                "id": "bs-reg", "namespace": "ns-reg", "role": "FOLLOWER",
                "spec": {"addr": "192.0.2.3", "type": "DP"},
                "status": {"hostName": "apm-server-03", "model": "DP100", "firmwareVer": "APM 1.2-0",
                           "serial": "SN0", "status": "NORMAL"},
            }
            m.get(
                f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500",
                payload={"backupServers": [regular] * 500, "total": 501},
            )
            # Page 2: the LEADER is on the second page
            leader = {
                "id": "bs-leader2", "namespace": "ns-leader2", "role": "LEADER",
                "spec": {"addr": "192.0.2.1", "type": "DP"},
                "status": {"hostName": "apm-server-01", "model": "DP320", "firmwareVer": "APM 1.2-71845",
                           "serial": "SN-L2", "status": "NORMAL"},
            }
            m.get(
                f"{BASE_URL}/api/v1/infra/backup_server?offset=500&limit=500",
                payload={"backupServers": [leader], "total": 501},
            )
            m.get(
                f"{BASE_URL}/api/v1/infra/backup_server/storage_statistics",
                payload={"transferBytes": 0, "backupServerUsageBytes": 0},
            )
            m.get(
                f"{BASE_URL}/api/v1/dashboard/get_workload_statistics",
                payload={"workloadStatistics": []},
            )
            site = await apm.get_site_info()
            m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)

    assert site.primary_management_server is not None
    assert site.primary_management_server.name == "apm-server-01"
    assert site.secondary_management_server is None


async def test_get_site_info_no_management_server_when_none_found() -> None:
    """When no LEADER is present in the cluster list, primary_management_server should be None."""
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            m.get(f"{BASE_URL}/api/v1/license/info", payload={"uuid": "site-uuid-003"})
            m.get(f"{BASE_URL}/api/v1/cluster/site_info", payload={"externalAddress": "", "port": ""})
            m.get(
                f"{BASE_URL}/api/v1/infra/backup_server?offset=0&limit=500",
                payload={"backupServers": [], "total": 0},
            )
            m.get(
                f"{BASE_URL}/api/v1/infra/backup_server/storage_statistics",
                payload={"transferBytes": 0, "backupServerUsageBytes": 0},
            )
            m.get(
                f"{BASE_URL}/api/v1/dashboard/get_workload_statistics",
                payload={"workloadStatistics": []},
            )
            site = await apm.get_site_info()
            m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)

    assert site.primary_management_server is None
    assert site.secondary_management_server is None



def test_activity_collection_subcollection_wiring() -> None:
    """activities.backup / activities.restore expose the concrete activity collections."""
    from synology_apm.sdk.collections.activities import (
        BackupActivityCollection,
        RestoreActivityCollection,
    )

    client = make_client()
    assert isinstance(client.activities.backup, BackupActivityCollection)
    assert isinstance(client.activities.restore, RestoreActivityCollection)
