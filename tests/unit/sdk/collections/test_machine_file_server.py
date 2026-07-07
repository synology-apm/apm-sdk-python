"""Unit tests for MachineWorkloadCollection: VM/PC/PS/FS workload variants, add_file_server,
update_file_server, delete, get_verification_video_url, MachineCollection properties,
verify-status / backup-server parsing, fs_config parsing.
"""
from __future__ import annotations

import json
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses

from synology_apm.sdk.collections.machine import MachineCollection, MachineWorkloadCollection
from synology_apm.sdk.enums import (
    FileServerType,
    MachineWorkloadType,
    VerifyStatus,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import APIError, DuplicateWorkloadError, InvalidOperationError
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import WorkloadVersion
from synology_apm.sdk.models.workload import (
    FileServerAddRequest,
    FileServerConfig,
    FileServerPathSelector,
    FileServerUpdateRequest,
    MachineWorkload,
)
from tests.unit.sdk.conftest import (
    BASE_URL,
    LOGIN_OK,
    LOGIN_URL,
    assert_resource_error,
    connected_session,
    make_session,
)

WORKLOAD_ID = "wl-id-001"
NAMESPACE = "ns-001"

SAMPLE_WL_OBJ = MachineWorkload(
    workload_id=WORKLOAD_ID, name="CORP-PC-001", category=WorkloadCategory.MACHINE,
    namespace=NAMESPACE, last_backup_at=None, is_retired=False,
    protected_data_bytes=0, status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-x", name="Test Plan", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC, agent_version=None,
)

LIST_URL = (
    f"{BASE_URL}/api/v2/workload/device_workload"
    "?filter.isFilterBasedOnNonWorkloadType=true&filter.limit=500&filter.offset=0"
    "&filter.protectStatus=PROTECT_STATUS_PROTECTED"
)

SAMPLE_WORKLOAD: dict[str, Any] = {
    "id": WORKLOAD_ID,
    "namespace": NAMESPACE,
    "spec": {
        "workloadType": "PC",
        "workloadName": "CORP-PC-001",
        "workloadUid": "wl-uid-001",
        "protectStatus": "PROTECT_STATUS_PROTECTED",
        "planRef": {"kind": "BackupPlan", "uid": "", "namespace": ""},
    },
    "status": {
        "lastBackupTime": "1776734685",
        "usage": "524288000",
    },
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
    "copyDataUsage": "104857600",
    "planName": "Test Plan",
}


# ── list() — backup server / copy destination parsing ─────────────────────


async def test_list_parses_backup_server_from_top_level() -> None:
    """backup_server should be parsed from the top-level backupServerInfo field (not from inside status)."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        workloads, total = await collection.list()

    loc = workloads[0].backup_server
    assert loc is not None
    assert loc.name == "apm-server-01"
    assert loc.endpoint == "192.0.2.1"
    assert loc.identifier == "ns-server-001"
    assert loc.is_remote_storage is False
    assert loc.vault is None


async def test_list_backup_server_none_when_missing() -> None:
    """When backupServerInfo is absent, backup_server should be None."""
    from unittest.mock import AsyncMock, patch

    raw = {k: v for k, v in SAMPLE_WORKLOAD.items() if k != "backupServerInfo"}
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, total = await collection.list()

    assert workloads[0].backup_server is None


async def test_list_backup_copy_destination_none_when_empty() -> None:
    """When backupCopyServerInfo hostName is empty, backup_copy_destination should be None."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        workloads, total = await collection.list()

    assert workloads[0].backup_copy_destination is None


async def test_list_backup_copy_destination_parsed_with_vault() -> None:
    """When backupCopyServerInfo has hostName and vaultName, backup_copy_destination should be parsed correctly."""
    from unittest.mock import AsyncMock, patch

    raw = {
        **SAMPLE_WORKLOAD,
        "backupCopyServerInfo": {
            "uid": "copy-uid-001",
            "hostName": "APV-Server",
            "addr": "10.1.1.1",
            "namespace": "ns-copy-001",
            "destinationType": "APV",
            "vaultName": "MyVault",
        },
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, total = await collection.list()

    loc = workloads[0].backup_copy_destination
    assert loc is not None
    assert loc.name == "APV-Server"
    assert loc.vault == "MyVault"
    assert loc.identifier == "ns-copy-001"
    assert loc.is_remote_storage is True


async def test_list_backup_copy_destination_namespace_shared_falls_back_to_uid() -> None:
    """When backupCopyServerInfo.namespace == 'shared', fall back to uid as namespace."""
    from unittest.mock import AsyncMock, patch

    raw = {
        **SAMPLE_WORKLOAD,
        "backupCopyServerInfo": {
            "uid": "copy-uid-001",
            "hostName": "APV-Server",
            "addr": "10.1.1.1",
            "namespace": "shared",
            "destinationType": "APV",
            "vaultName": "MyVault",
        },
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, total = await collection.list()

    loc = workloads[0].backup_copy_destination
    assert loc is not None
    assert loc.identifier == "copy-uid-001"


async def test_list_parses_backup_copy_data_bytes() -> None:
    """copyDataUsage from the response should be parsed into backup_copy_data_bytes."""
    from unittest.mock import AsyncMock, patch

    raw = {**SAMPLE_WORKLOAD, "copyDataUsage": "209715200"}
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, _ = await collection.list()

    assert workloads[0].backup_copy_data_bytes == 209715200


async def test_list_backup_copy_data_bytes_defaults_to_zero_when_absent() -> None:
    """backup_copy_data_bytes should be 0 when copyDataUsage is absent from the response."""
    from unittest.mock import AsyncMock, patch

    raw = {k: v for k, v in SAMPLE_WORKLOAD.items() if k != "copyDataUsage"}
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, _ = await collection.list()

    assert workloads[0].backup_copy_data_bytes == 0


LIST_ALL_URL = LIST_URL

SAMPLE_VM_WORKLOAD = {
    "id": "vm-id-001",
    "namespace": NAMESPACE,
    "spec": {
        "workloadType": "VM",
        "workloadName": "Ubuntu-VM",
        "workloadUid": "vm-uid-001",
        "protectStatus": "PROTECT_STATUS_PROTECTED",
        "configVm": {"deviceUuid": "vm-uuid-001"},
    },
    "status": {
        "lastBackupTime": "0",
        "usage": "0",
    },
    "inventoryName": "esx-host-01",
    "inventoryType": "ESXi",
}


# ── VM/PC/PS/FS workload variants ──────────────────────────────────────────


async def test_vm_workload_agent_version_and_ip_are_none() -> None:
    """VM workloads have agent_version=None and ip_address=None (agentless)."""
    async with connected_session() as (session, m):

        m.get(LIST_ALL_URL, payload={"workloads": [SAMPLE_VM_WORKLOAD], "total": 1})
        collection = MachineWorkloadCollection(session)
        result, total = await collection.list()
        await session.disconnect()

    from synology_apm.sdk.models.workload import MachineWorkload
    wl = result[0]
    assert isinstance(wl, MachineWorkload)
    assert wl.agent_version is None
    assert wl.ip_address is None


async def test_pc_workload_parses_device_uuid_agent_version_ip() -> None:
    """PC workloads parse device_uuid, agent_version, ip_address from status.configPc."""
    from unittest.mock import AsyncMock, patch
    pc_wl = {**SAMPLE_WORKLOAD, "status": {
        **SAMPLE_WORKLOAD["status"],
        "configPc": {
            "deviceUuid": "pc-uuid-001",
            "versionNumber": "1.2.0",
            "publicIp": "10.0.0.5",
        },
    }}
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [pc_wl], "total": 1}
        result, _ = await collection.list()

    from synology_apm.sdk.models.workload import MachineWorkload
    wl = result[0]
    assert isinstance(wl, MachineWorkload)
    assert wl.device_uuid == "pc-uuid-001"
    assert wl.agent_version == "1.2.0"
    assert wl.ip_address == "10.0.0.5"


async def test_ps_workload_parses_device_uuid_agent_version_ip() -> None:
    """PS workloads parse device_uuid, agent_version, ip_address from status.configPs."""
    from unittest.mock import AsyncMock, patch
    ps_wl = {
        "id": "ps-id-001",
        "namespace": NAMESPACE,
        "spec": {"workloadType": "PS", "workloadName": "Rack-Server", "protectStatus": "PROTECT_STATUS_PROTECTED"},
        "status": {
            "lastBackupTime": "0",
            "usage": "0",
            "configPs": {
                "deviceUuid": "ps-uuid-001",
                "versionNumber": "1.3.0",
                "publicIp": "192.168.1.10",
            },
        },
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [ps_wl], "total": 1}
        result, _ = await collection.list()

    from synology_apm.sdk.models.workload import MachineWorkload
    wl = result[0]
    assert isinstance(wl, MachineWorkload)
    assert wl.device_uuid == "ps-uuid-001"
    assert wl.agent_version == "1.3.0"
    assert wl.ip_address == "192.168.1.10"


async def test_vm_workload_parses_device_uuid_from_spec_and_inventory() -> None:
    """VM workloads parse device_uuid from spec.configVm and inventory fields from top-level."""
    async with connected_session() as (session, m):

        m.get(LIST_ALL_URL, payload={"workloads": [SAMPLE_VM_WORKLOAD], "total": 1})
        collection = MachineWorkloadCollection(session)
        result, total = await collection.list()
        await session.disconnect()

    from synology_apm.sdk.models.workload import MachineWorkload
    wl = result[0]
    assert isinstance(wl, MachineWorkload)
    assert wl.device_uuid == "vm-uuid-001"
    assert wl.inventory_name == "esx-host-01"
    assert wl.inventory_type == "ESXi"


async def test_fs_workload_has_no_device_uuid_agent_version_ip() -> None:
    """FS workloads have device_uuid=None, agent_version=None, ip_address=None."""
    from unittest.mock import AsyncMock, patch
    fs_wl = {
        "id": "fs-id-001",
        "namespace": NAMESPACE,
        "spec": {"workloadType": "FS", "workloadName": "Corp Share", "protectStatus": "PROTECT_STATUS_PROTECTED"},
        "status": {"lastBackupTime": "0", "usage": "0"},
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [fs_wl], "total": 1}
        result, _ = await collection.list()

    from synology_apm.sdk.models.workload import MachineWorkload
    wl = result[0]
    assert isinstance(wl, MachineWorkload)
    assert wl.device_uuid is None
    assert wl.agent_version is None
    assert wl.ip_address is None


async def test_retired_workload_has_retired_status() -> None:
    """Retired workload status should be WorkloadStatus.RETIRED."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk import WorkloadStatus
    retired_wl = {
        **SAMPLE_WORKLOAD,
        "spec": {**SAMPLE_WORKLOAD["spec"], "planRef": {"kind": "ArchivePlan", "uid": "", "namespace": ""}},
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [retired_wl], "total": 1}
        result, _ = await collection.list()

    wl = result[0]
    assert wl.is_retired is True
    assert wl.status == WorkloadStatus.RETIRED


# ── progress fields during BACKING_UP ─────────────────────────────────────


async def test_pc_running_reads_backup_progress_from_cache() -> None:
    """PC RUNNING: backup_progress is read from cache.progress; items_backed_up is None."""
    session = make_session()
    running_workload = {
        "id": WORKLOAD_ID,
        "namespace": NAMESPACE,
        "spec": {"workloadType": "PC", "workloadName": "TestPC", "protectStatus": "PROTECT_STATUS_PROTECTED"},
        "status": {"lastBackupTime": "0", "usage": "0", "jobStatus": "RUNNING"},
        "cache": {"progress": 45.7, "processedSuccessCount": 200},
    }
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(LIST_URL, payload={"workloads": [running_workload], "total": 1})
        collection = MachineWorkloadCollection(session)
        workloads, total = await collection.list()
        await session.disconnect()

    from synology_apm.sdk.enums import WorkloadStatus
    wl = workloads[0]
    assert wl.status == WorkloadStatus.BACKING_UP
    assert wl.backup_progress == 45
    assert wl.items_backed_up is None


async def test_fs_running_reads_items_backed_up_from_cache() -> None:
    """FS RUNNING: items_backed_up is read from cache.processedSuccessCount; backup_progress is None."""
    session = make_session()
    running_fs = {
        "id": "fs-id-001",
        "namespace": NAMESPACE,
        "spec": {
            "workloadType": "FS",
            "workloadName": "Corp Share",
            "protectStatus": "PROTECT_STATUS_PROTECTED",
        },
        "status": {"lastBackupTime": "0", "usage": "0", "jobStatus": "RUNNING"},
        "cache": {"progress": 60.0, "processedSuccessCount": 1234},
    }
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(LIST_URL, payload={"workloads": [running_fs], "total": 1})
        collection = MachineWorkloadCollection(session)
        workloads, total = await collection.list()
        await session.disconnect()

    from synology_apm.sdk.enums import WorkloadStatus
    from synology_apm.sdk.models.workload import MachineWorkload
    wl = workloads[0]
    assert isinstance(wl, MachineWorkload)
    assert wl.workload_type == MachineWorkloadType.FS
    assert wl.status == WorkloadStatus.BACKING_UP
    assert wl.backup_progress is None
    assert wl.items_backed_up == 1234


async def test_waiting_task_maps_to_queuing_status() -> None:
    """WAITING_TASK job status should map to WorkloadStatus.QUEUING."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import WorkloadStatus

    waiting_wl = {**SAMPLE_WORKLOAD, "status": {**SAMPLE_WORKLOAD["status"], "jobStatus": "WAITING_TASK"}}
    session = make_session()
    collection = MachineWorkloadCollection(session)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [waiting_wl], "total": 1}
        result, _ = await collection.list()
    assert result[0].status == WorkloadStatus.QUEUING


async def test_deleting_maps_to_deleting_status() -> None:
    """DELETING job status should map to WorkloadStatus.DELETING."""
    from unittest.mock import AsyncMock, patch

    from synology_apm.sdk.enums import WorkloadStatus

    deleting_wl = {**SAMPLE_WORKLOAD, "status": {**SAMPLE_WORKLOAD["status"], "jobStatus": "DELETING"}}
    session = make_session()
    collection = MachineWorkloadCollection(session)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [deleting_wl], "total": 1}
        result, _ = await collection.list()
    assert result[0].status == WorkloadStatus.DELETING


async def test_pc_running_with_no_cache_field_defaults_to_zero_progress() -> None:
    """PC RUNNING with no cache field in response: backup_progress defaults to 0 without crashing."""
    session = make_session()
    running_no_cache = {
        "id": WORKLOAD_ID,
        "namespace": NAMESPACE,
        "spec": {"workloadType": "PC", "workloadName": "TestPC", "protectStatus": "PROTECT_STATUS_PROTECTED"},
        "status": {"lastBackupTime": "0", "usage": "0", "jobStatus": "RUNNING"},
    }
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(LIST_URL, payload={"workloads": [running_no_cache], "total": 1})
        collection = MachineWorkloadCollection(session)
        workloads, total = await collection.list()
        await session.disconnect()

    from synology_apm.sdk.enums import WorkloadStatus
    wl = workloads[0]
    assert wl.status == WorkloadStatus.BACKING_UP
    assert wl.backup_progress == 0
    assert wl.items_backed_up is None


# ── add_file_server() ─────────────────────────────────────────────────────


async def test_add_file_server_success_returns_none() -> None:
    """add_file_server() returns None and sends the correct request body on success."""
    session = make_session()
    req = FileServerAddRequest(
        namespace=NAMESPACE,
        host_ip="192.0.2.3",
        server_type=FileServerType.SMB,
        plan_id="plan-uuid-001",
        login_user="testuser",
        login_password="testpass",
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "errors": []}
        await collection.add_file_server(req)

    call_body = mock_post.call_args[1]["json"]
    assert len(call_body["requests"]) == 1
    spec = call_body["requests"][0]["spec"]
    assert spec["workloadType"] == "FS"
    assert spec["configFs"]["osName"] == "smb"
    assert spec["configFs"]["hostIp"] == "192.0.2.3"
    assert spec["configFs"]["connectionTimeout"] == 180  # default
    assert spec["planRef"]["uid"] == "plan-uuid-001"


async def test_add_file_server_custom_connection_timeout() -> None:
    """add_file_server() sends the specified connection_timeout_seconds in the request body."""
    session = make_session()
    req = FileServerAddRequest(
        namespace=NAMESPACE,
        host_ip="192.0.2.3",
        server_type=FileServerType.SMB,
        plan_id="plan-uuid-001",
        login_user="testuser",
        login_password="testpass",
        connection_timeout_seconds=60,
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "errors": []}
        await collection.add_file_server(req)

    cfg = mock_post.call_args[1]["json"]["requests"][0]["spec"]["configFs"]
    assert cfg["connectionTimeout"] == 60


def test_add_file_server_empty_password_raises_value_error() -> None:
    """FileServerAddRequest raises ValueError at construction when login_password is empty."""
    with pytest.raises(ValueError, match="login_password"):
        FileServerAddRequest(
            namespace=NAMESPACE,
            host_ip="192.0.2.3",
            server_type=FileServerType.SMB,
            plan_id="plan-uuid-001",
            login_user="testuser",
            login_password="",
        )


def test_add_file_server_whitespace_only_password_raises_value_error() -> None:
    """FileServerAddRequest raises ValueError at construction when login_password is whitespace only."""
    with pytest.raises(ValueError, match="login_password"):
        FileServerAddRequest(
            namespace=NAMESPACE,
            host_ip="192.0.2.3",
            server_type=FileServerType.SMB,
            plan_id="plan-uuid-001",
            login_user="testuser",
            login_password="   ",
        )


async def test_add_file_server_duplicate_raises_duplicate_workload_error() -> None:
    """add_file_server() raises DuplicateWorkloadError when errorCode is 7001."""
    session = make_session()
    req = FileServerAddRequest(
        namespace=NAMESPACE,
        host_ip="192.0.2.3",
        server_type=FileServerType.SMB,
        plan_id="plan-uuid-001",
        login_user="testuser",
        login_password="testpass",
    )
    collection = MachineWorkloadCollection(session)

    duplicate_resp = {
        "success": False,
        "errors": [{"errorCode": 7001, "message": "fs workload already exists"}],
    }

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = duplicate_resp
        with pytest.raises(DuplicateWorkloadError) as exc_info:
            await collection.add_file_server(req)

    assert_resource_error(exc_info, resource_type="file_server", resource_id="192.0.2.3")
    assert exc_info.value.error_code == 7001


async def test_add_file_server_other_error_raises_api_error() -> None:
    """add_file_server() raises APIError for non-7001 error codes."""
    session = make_session()
    req = FileServerAddRequest(
        namespace=NAMESPACE,
        host_ip="192.0.2.99",
        server_type=FileServerType.SYNOLOGY_NAS,
        plan_id="plan-uuid-002",
        login_user="admin",
        login_password="secret",
    )
    collection = MachineWorkloadCollection(session)

    error_resp = {
        "success": False,
        "errors": [{"errorCode": 5000, "message": "connection refused"}],
    }

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = error_resp
        with pytest.raises(APIError) as exc_info:
            await collection.add_file_server(req)

    assert exc_info.value.error_code == 5000


async def test_add_file_server_whole_machine_default_selector() -> None:
    """add_file_server() default selectors send selected_path='' filtered_paths=[]."""
    session = make_session()
    req = FileServerAddRequest(
        namespace=NAMESPACE,
        host_ip="192.0.2.3",
        server_type=FileServerType.SMB,
        plan_id="plan-uuid-001",
        login_user="u",
        login_password="p",
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "errors": []}
        await collection.add_file_server(req)

    body = mock_post.call_args[1]["json"]
    sessions = json.loads(body["requests"][0]["spec"]["configFs"]["remoteSessionList"])
    assert sessions == [{"selected_path": "", "filtered_paths": []}]


async def test_get_verification_video_url_posts_correct_path() -> None:
    """get_verification_video_url() POSTs to /api/v1/version/{version_id}/video:download."""
    from datetime import datetime
    from unittest.mock import AsyncMock, patch

    session = make_session()
    version = WorkloadVersion(
        version_id="a975cd55-6715-41d0-848f-f9d3af687717",
        workload_id=WORKLOAD_ID,
        namespace=NAMESPACE,
        created_at=datetime(2026, 5, 15, 14, 23, 5, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="exec-001",
        locked=False,
        changed_size_bytes=0,
        verify_status=VerifyStatus.SUCCESS,
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"url": "https://fake-apm.test/portal/api/v1/portal/download/tok"}
        await collection.get_verification_video_url(SAMPLE_WL_OBJ, version)

    called_path = mock_post.call_args[0][0]
    assert called_path == "/api/v1/version/a975cd55-6715-41d0-848f-f9d3af687717/video:download"


async def test_get_verification_video_url_sends_correct_body_and_header() -> None:
    """get_verification_video_url() sends workload uid/namespace in body and namespace in header."""
    from datetime import datetime
    from unittest.mock import AsyncMock, patch

    session = make_session()
    version = WorkloadVersion(
        version_id="ver-uuid-001",
        workload_id=WORKLOAD_ID,
        namespace=NAMESPACE,
        created_at=datetime(2026, 5, 15, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="exec-001",
        locked=False,
        changed_size_bytes=0,
        verify_status=VerifyStatus.SUCCESS,
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"url": "https://fake-apm.test/portal/api/v1/portal/download/tok"}
        await collection.get_verification_video_url(SAMPLE_WL_OBJ, version)

    kwargs = mock_post.call_args[1]
    assert kwargs["json"] == {
        "workload": {"uid": WORKLOAD_ID, "namespace": NAMESPACE},
        "abbParams": {},
    }
    assert kwargs["headers"] == {"x-syno-tunnel-route": NAMESPACE}


async def test_get_verification_video_url_returns_url_from_response() -> None:
    """get_verification_video_url() returns the url field from the API response."""
    from datetime import datetime
    from unittest.mock import AsyncMock, patch

    expected_url = "https://fake-apm.test/portal/api/v1/portal/download/teQKpbucK8x9bDDO"
    session = make_session()
    version = WorkloadVersion(
        version_id="ver-uuid-001",
        workload_id=WORKLOAD_ID,
        namespace=NAMESPACE,
        created_at=datetime(2026, 5, 15, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="exec-001",
        locked=False,
        changed_size_bytes=0,
        verify_status=VerifyStatus.SUCCESS,
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"url": expected_url}
        result = await collection.get_verification_video_url(SAMPLE_WL_OBJ, version)

    assert result == expected_url


async def test_add_file_server_selectors_with_exclusions_build_session_list() -> None:
    """add_file_server() with selectors containing excluded_paths serializes filtered_paths correctly."""
    session = make_session()
    req = FileServerAddRequest(
        namespace=NAMESPACE,
        host_ip="192.0.2.3",
        server_type=FileServerType.SMB,
        plan_id="plan-uuid-001",
        login_user="u",
        login_password="p",
        selectors=(
            FileServerPathSelector(path="", excluded_paths=("docker", "tmp")),
        ),
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "errors": []}
        await collection.add_file_server(req)

    body = mock_post.call_args[1]["json"]
    sessions = json.loads(body["requests"][0]["spec"]["configFs"]["remoteSessionList"])
    assert sessions == [{"selected_path": "", "filtered_paths": ["docker", "tmp"]}]


async def test_add_file_server_multiple_selectors_build_session_list() -> None:
    """add_file_server() with multiple selectors builds multiple session entries."""
    session = make_session()
    req = FileServerAddRequest(
        namespace=NAMESPACE,
        host_ip="192.0.2.3",
        server_type=FileServerType.SMB,
        plan_id="plan-uuid-001",
        login_user="u",
        login_password="p",
        selectors=(
            FileServerPathSelector(path="share1"),
            FileServerPathSelector(path="share2", excluded_paths=("archive",)),
        ),
    )
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "errors": []}
        await collection.add_file_server(req)

    body = mock_post.call_args[1]["json"]
    sessions = json.loads(body["requests"][0]["spec"]["configFs"]["remoteSessionList"])
    assert sessions == [
        {"selected_path": "share1", "filtered_paths": []},
        {"selected_path": "share2", "filtered_paths": ["archive"]},
    ]


# ── MachineCollection properties ───────────────────────────────────────────


def test_machine_collection_workloads_returns_workload_collection() -> None:
    """MachineCollection.workloads should return a MachineWorkloadCollection."""
    session = make_session()
    collection = MachineCollection(session)
    assert isinstance(collection.workloads, MachineWorkloadCollection)


def test_machine_collection_plans_returns_plan_collection() -> None:
    """MachineCollection.plans should return a MachinePlanCollection."""
    from synology_apm.sdk.collections.protection_plans import MachinePlanCollection
    session = make_session()
    collection = MachineCollection(session)
    assert isinstance(collection.plans, MachinePlanCollection)


# ── _parse_verify_status — indirect coverage via list() ───────────────────


async def test_list_parses_verify_status_completed_for_vm() -> None:
    """Parser maps verifyStatus=VERIFY_COMPLETED to VerifyStatus.SUCCESS for VM workloads."""
    vm_workload = {
        "id": WORKLOAD_ID,
        "namespace": NAMESPACE,
        "spec": {"workloadType": "VM", "workloadName": "CORP-VM-01", "protectStatus": "PROTECT_STATUS_PROTECTED"},
        "status": {"lastBackupTime": "1776734685", "usage": "0", "verifyStatus": "VERIFY_COMPLETED"},
    }
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"workloads": [vm_workload], "total": 1})
        workloads, _ = await MachineWorkloadCollection(session).list()
        await session.disconnect()

    assert workloads[0].verify_status == VerifyStatus.SUCCESS


async def test_list_parses_verify_status_not_enabled_for_pc_returns_none() -> None:
    """Parser converts verifyStatus=VERIFY_NOT_ENABLED to None for PC workloads (unsupported type)."""
    pc_workload = {
        "id": WORKLOAD_ID,
        "namespace": NAMESPACE,
        "spec": {"workloadType": "PC", "workloadName": "CORP-PC-001", "protectStatus": "PROTECT_STATUS_PROTECTED"},
        "status": {"lastBackupTime": "1776734685", "usage": "0", "verifyStatus": "VERIFY_NOT_ENABLED"},
    }
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"workloads": [pc_workload], "total": 1})
        workloads, _ = await MachineWorkloadCollection(session).list()
        await session.disconnect()

    assert workloads[0].verify_status is None


async def test_list_parses_unknown_verify_status_returns_none() -> None:
    """Parser returns None for an unrecognised verifyStatus string."""
    ps_workload = {
        "id": WORKLOAD_ID,
        "namespace": NAMESPACE,
        "spec": {"workloadType": "PS", "workloadName": "CORP-PS-01", "protectStatus": "PROTECT_STATUS_PROTECTED"},
        "status": {"lastBackupTime": "0", "usage": "0", "verifyStatus": "VERIFY_UNKNOWN_FUTURE_VALUE"},
    }
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"workloads": [ps_workload], "total": 1})
        workloads, _ = await MachineWorkloadCollection(session).list()
        await session.disconnect()

    assert workloads[0].verify_status is None


# ── fs_config parsing ──────────────────────────────────────────────────────

SAMPLE_FS_RAW: dict[str, Any] = {
    "id": "fs-id-001",
    "namespace": NAMESPACE,
    "spec": {
        "workloadType": "FS",
        "workloadName": "192.0.2.10",
        "planRef": {"kind": "BackupPlan", "uid": "plan-uuid-001", "namespace": ""},
        "configFs": {
            "hostIp": "192.0.2.10",
            "hostPort": 445,
            "osName": "smb",
            "loginUser": "admin",
            "loginPassword": "",
            "remoteSessionList": '[{"selected_path":"","filtered_paths":["docker"]}]',
            "agentlessEnableWindowsVss": True,
            "connectionTimeout": 120,
        },
    },
    "status": {"lastBackupTime": "0", "usage": "0"},
    "planName": "Daily Backup",
}


async def test_fs_workload_parses_fs_config_from_spec() -> None:
    """FS workload list result has fs_config populated from spec.configFs."""
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_FS_RAW], "total": 1}
        workloads, _ = await collection.list()

    wl = workloads[0]
    assert wl.workload_type == MachineWorkloadType.FS
    cfg = wl.fs_config
    assert cfg is not None
    assert cfg.host_ip == "192.0.2.10"
    assert cfg.host_port == 445
    assert cfg.server_type == FileServerType.SMB
    assert cfg.login_user == "admin"
    assert cfg.enable_vss is True
    assert cfg.connection_timeout_seconds == 120
    assert cfg.selectors == (FileServerPathSelector(path="", excluded_paths=("docker",)),)


async def test_fs_workload_fs_config_none_when_configfs_absent() -> None:
    """FS workload with no configFs in spec gets fs_config=None."""
    raw = {**SAMPLE_FS_RAW, "spec": {k: v for k, v in SAMPLE_FS_RAW["spec"].items() if k != "configFs"}}
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, _ = await collection.list()

    assert workloads[0].fs_config is None


async def test_fs_workload_parses_selector_with_excluded_paths() -> None:
    """remoteSessionList with filtered_paths parses into excluded_paths on the selector."""
    raw = {
        **SAMPLE_FS_RAW,
        "spec": {
            **SAMPLE_FS_RAW["spec"],
            "configFs": {
                **SAMPLE_FS_RAW["spec"]["configFs"],
                "remoteSessionList": '[{"selected_path":"","filtered_paths":["docker","tmp"]}]',
            },
        },
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, _ = await collection.list()

    cfg = workloads[0].fs_config
    assert cfg is not None
    assert cfg.selectors == (FileServerPathSelector(path="", excluded_paths=("docker", "tmp")),)


async def test_fs_workload_parses_multiple_selectors() -> None:
    """remoteSessionList with two entries parses into two FileServerPathSelector objects."""
    sessions_json = json.dumps([
        {"selected_path": "share1", "filtered_paths": []},
        {"selected_path": "share2", "filtered_paths": ["archive"]},
    ])
    raw = {
        **SAMPLE_FS_RAW,
        "spec": {
            **SAMPLE_FS_RAW["spec"],
            "configFs": {**SAMPLE_FS_RAW["spec"]["configFs"], "remoteSessionList": sessions_json},
        },
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, _ = await collection.list()

    cfg = workloads[0].fs_config
    assert cfg is not None
    assert cfg.selectors == (
        FileServerPathSelector(path="share1"),
        FileServerPathSelector(path="share2", excluded_paths=("archive",)),
    )


async def test_fs_workload_empty_remote_session_list_defaults_to_whole_machine() -> None:
    """Empty remoteSessionList ('[]') defaults to a single whole-machine selector."""
    raw = {
        **SAMPLE_FS_RAW,
        "spec": {
            **SAMPLE_FS_RAW["spec"],
            "configFs": {**SAMPLE_FS_RAW["spec"]["configFs"], "remoteSessionList": "[]"},
        },
    }
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [raw], "total": 1}
        workloads, _ = await collection.list()

    cfg = workloads[0].fs_config
    assert cfg is not None
    assert cfg.selectors == (FileServerPathSelector(path=""),)


async def test_non_fs_workload_fs_config_is_none() -> None:
    """PC workload gets fs_config=None."""
    session = make_session()
    collection = MachineWorkloadCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"workloads": [SAMPLE_WORKLOAD], "total": 1}
        workloads, _ = await collection.list()

    assert workloads[0].fs_config is None


# ── update_file_server() ───────────────────────────────────────────────────

FS_WORKLOAD_OBJ = MachineWorkload(
    workload_id="fs-id-001",
    name="192.0.2.10",
    category=WorkloadCategory.MACHINE,
    namespace=NAMESPACE,
    last_backup_at=None,
    is_retired=False,
    protected_data_bytes=0,
    status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-uuid-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.FS,
    agent_version=None,
    fs_config=FileServerConfig(
        host_ip="192.0.2.10",
        host_port=445,
        server_type=FileServerType.SMB,
        login_user="admin",
        enable_vss=False,
        connection_timeout_seconds=180,
        selectors=(FileServerPathSelector(path=""),),
    ),
)

CURRENT_SPEC: dict[str, Any] = {
    "workloadType": "FS",
    "workloadName": "192.0.2.10",
    "configFs": {
        "hostIp": "192.0.2.10",
        "hostPort": 445,
        "osName": "smb",
        "loginUser": "admin",
        "loginPassword": "",
        "remoteSessionList": '[{"selected_path":"","filtered_paths":[]}]',
        "agentlessEnableWindowsVss": False,
        "connectionTimeout": 180,
        "extraField": "should-be-preserved",
    },
    "planRef": {"kind": "BackupPlan", "uid": "plan-uuid-001", "namespace": ""},
}


async def test_update_file_server_fetches_spec_then_puts() -> None:
    """update_file_server() issues GET then PUT to the correct paths."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(host_ip="192.0.2.10", login_user="admin", login_password=None)

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        patch.object(session, "put", new_callable=AsyncMock) as mock_put,
    ):
        mock_get.return_value = {"spec": dict(CURRENT_SPEC)}
        mock_put.return_value = {"success": True, "error": None}
        await collection.update_file_server(FS_WORKLOAD_OBJ, req)

    mock_get.assert_called_once()
    get_path = mock_get.call_args[0][0]
    assert get_path == f"/api/v1/workload/device_workload/{FS_WORKLOAD_OBJ.workload_id}"

    mock_put.assert_called_once()
    put_path = mock_put.call_args[0][0]
    assert put_path == f"/api/v1/workload/device_workload/{FS_WORKLOAD_OBJ.workload_id}"

    put_body = mock_put.call_args[1]["json"]
    assert "opcode" not in put_body.get("spec", {})


async def test_update_file_server_merges_configfs_fields() -> None:
    """update_file_server() overwrites updated fields while preserving others (e.g. extraField)."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(
        host_ip="192.0.2.11",
        login_user="newuser",
        login_password="newpass",
        host_port=139,
        enable_vss=True,
        connection_timeout_seconds=60,
    )

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        patch.object(session, "put", new_callable=AsyncMock) as mock_put,
    ):
        mock_get.return_value = {"spec": dict(CURRENT_SPEC)}
        mock_put.return_value = {"success": True, "error": None}
        await collection.update_file_server(FS_WORKLOAD_OBJ, req)

    put_cfg = mock_put.call_args[1]["json"]["spec"]["configFs"]
    assert put_cfg["hostIp"] == "192.0.2.11"
    assert put_cfg["loginUser"] == "newuser"
    assert put_cfg["loginPassword"] == "newpass"
    assert put_cfg["hostPort"] == 139
    assert put_cfg["agentlessEnableWindowsVss"] is True
    assert put_cfg["connectionTimeout"] == 60
    assert put_cfg["extraField"] == "should-be-preserved"


async def test_update_file_server_builds_correct_remote_session_list() -> None:
    """update_file_server() serializes selectors into remoteSessionList correctly."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(
        host_ip="192.0.2.10",
        login_user="admin",
        login_password=None,
        selectors=(
            FileServerPathSelector(path="share1"),
            FileServerPathSelector(path="share2", excluded_paths=("archive",)),
        ),
    )

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        patch.object(session, "put", new_callable=AsyncMock) as mock_put,
    ):
        mock_get.return_value = {"spec": dict(CURRENT_SPEC)}
        mock_put.return_value = {"success": True, "error": None}
        await collection.update_file_server(FS_WORKLOAD_OBJ, req)

    raw_sessions = mock_put.call_args[1]["json"]["spec"]["configFs"]["remoteSessionList"]
    sessions = json.loads(raw_sessions)
    assert sessions == [
        {"selected_path": "share1", "filtered_paths": []},
        {"selected_path": "share2", "filtered_paths": ["archive"]},
    ]


async def test_update_file_server_whole_machine_selector() -> None:
    """update_file_server() with path='' sends selected_path='' filtered_paths=[]."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(
        host_ip="192.0.2.10",
        login_user="admin",
        login_password=None,
        selectors=(FileServerPathSelector(path=""),),
    )

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        patch.object(session, "put", new_callable=AsyncMock) as mock_put,
    ):
        mock_get.return_value = {"spec": dict(CURRENT_SPEC)}
        mock_put.return_value = {"success": True, "error": None}
        await collection.update_file_server(FS_WORKLOAD_OBJ, req)

    raw_sessions = mock_put.call_args[1]["json"]["spec"]["configFs"]["remoteSessionList"]
    sessions = json.loads(raw_sessions)
    assert sessions == [{"selected_path": "", "filtered_paths": []}]


async def test_update_file_server_none_password_sends_empty_string_to_api() -> None:
    """update_file_server() with login_password=None sends loginPassword='' (keep-existing sentinel)."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(host_ip="192.0.2.10", login_user="admin", login_password=None)

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        patch.object(session, "put", new_callable=AsyncMock) as mock_put,
    ):
        mock_get.return_value = {"spec": dict(CURRENT_SPEC)}
        mock_put.return_value = {"success": True, "error": None}
        await collection.update_file_server(FS_WORKLOAD_OBJ, req)

    put_cfg = mock_put.call_args[1]["json"]["spec"]["configFs"]
    assert put_cfg["loginPassword"] == ""


async def test_update_file_server_raises_for_non_fs_workload() -> None:
    """update_file_server() raises InvalidOperationError when workload_type is not FS."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(host_ip="192.0.2.10", login_user="u", login_password="p")

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.update_file_server(SAMPLE_WL_OBJ, req)
    mock_get.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=SAMPLE_WL_OBJ.workload_id)


def test_update_file_server_empty_string_password_raises_value_error() -> None:
    """FileServerUpdateRequest raises ValueError at construction when login_password is empty string."""
    with pytest.raises(ValueError, match="login_password"):
        FileServerUpdateRequest(host_ip="192.0.2.10", login_user="admin", login_password="")


def test_update_file_server_whitespace_only_password_raises_value_error() -> None:
    """FileServerUpdateRequest raises ValueError at construction when login_password is whitespace only."""
    with pytest.raises(ValueError, match="login_password"):
        FileServerUpdateRequest(host_ip="192.0.2.10", login_user="admin", login_password="   ")


async def test_update_file_server_raises_duplicate_on_conflict_response() -> None:
    """update_file_server() raises DuplicateWorkloadError when PUT raises APIError with code 7001."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(host_ip="192.0.2.3", login_user="u", login_password="p")

    conflict_body = {"success": False, "error": {"errorCode": 7001, "message": "fs workload already exists"}}

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        patch.object(session, "put", new_callable=AsyncMock) as mock_put,
    ):
        mock_get.return_value = {"spec": dict(CURRENT_SPEC)}
        mock_put.side_effect = APIError("fs workload already exists", error_code=7001, response_body=conflict_body)
        with pytest.raises(DuplicateWorkloadError) as exc_info:
            await collection.update_file_server(FS_WORKLOAD_OBJ, req)

    assert_resource_error(exc_info, resource_type="file_server", resource_id="192.0.2.3")
    assert exc_info.value.error_code == 7001


async def test_update_file_server_raises_api_error_on_other_failure() -> None:
    """update_file_server() re-raises APIError from PUT when the error code is not 7001."""
    session = make_session()
    collection = MachineWorkloadCollection(session)
    req = FileServerUpdateRequest(host_ip="192.0.2.10", login_user="u", login_password="p")

    failure_body = {"success": False, "error": {"errorCode": 9999, "message": "unexpected error"}}

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        patch.object(session, "put", new_callable=AsyncMock) as mock_put,
    ):
        mock_get.return_value = {"spec": dict(CURRENT_SPEC)}
        mock_put.side_effect = APIError("unexpected error", error_code=9999, response_body=failure_body)
        with pytest.raises(APIError) as exc_info:
            await collection.update_file_server(FS_WORKLOAD_OBJ, req)

    assert exc_info.value.error_code == 9999


# ── delete() ───────────────────────────────────────────────────────────────


async def test_delete_sends_correct_request() -> None:
    """delete() sends DELETE with the correct path and workloadRefs body."""
    session = make_session()
    collection = MachineWorkloadCollection(session)

    success_resp = {
        "succeeded": {"namespaceWorkloadListMap": {}},
        "failed": {"entries": []},
    }
    with patch.object(session, "delete", new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = success_resp
        await collection.delete(FS_WORKLOAD_OBJ)

    mock_delete.assert_called_once()
    path = mock_delete.call_args[0][0]
    assert path == "/api/v1/workload/device_workload/batch"
    body = mock_delete.call_args[1]["json"]
    assert body == {"workloadRefs": [{"uid": FS_WORKLOAD_OBJ.workload_id, "namespace": NAMESPACE}]}


async def test_delete_works_for_non_fs_workload() -> None:
    """delete() sends the correct request for any workload type, not just FS."""
    session = make_session()
    collection = MachineWorkloadCollection(session)

    success_resp = {"succeeded": {"namespaceWorkloadListMap": {}}, "failed": {"entries": []}}
    with patch.object(session, "delete", new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = success_resp
        await collection.delete(SAMPLE_WL_OBJ)

    mock_delete.assert_called_once()
    body = mock_delete.call_args[1]["json"]
    assert body == {"workloadRefs": [{"uid": SAMPLE_WL_OBJ.workload_id, "namespace": SAMPLE_WL_OBJ.namespace}]}


async def test_delete_raises_on_failed_entries() -> None:
    """delete() raises InvalidOperationError when the response contains failed entries."""
    session = make_session()
    collection = MachineWorkloadCollection(session)

    failed_resp = {
        "succeeded": {"namespaceWorkloadListMap": {}},
        "failed": {
            "entries": [
                {
                    "error": {"errorCode": 7018, "message": "workload is initializing"},
                    "workloadUid": FS_WORKLOAD_OBJ.workload_id,
                    "namespace": NAMESPACE,
                }
            ]
        },
    }
    with patch.object(session, "delete", new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = failed_resp
        with pytest.raises(InvalidOperationError) as exc_info:
            await collection.delete(FS_WORKLOAD_OBJ)

    assert_resource_error(exc_info, resource_type="Workload", resource_id=FS_WORKLOAD_OBJ.workload_id)
    assert exc_info.value.error_code == 7018
    assert "initializing" in exc_info.value.message
