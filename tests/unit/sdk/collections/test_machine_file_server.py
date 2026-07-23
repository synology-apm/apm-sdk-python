"""Unit tests for MachineWorkloadCollection: VM/PC/PS/FS workload variants, add_file_server,
get_verification_video_url, MachineCollection properties, verify-status / backup-server
parsing.
"""
from __future__ import annotations

import json
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiointercept import aiointercept

from synology_apm.sdk.collections.machine import MachineCollection, MachineWorkloadCollection
from synology_apm.sdk.enums import (
    FileServerType,
    MachineWorkloadType,
    VerifyStatus,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import APIError, DuplicateWorkloadError
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import WorkloadVersion
from synology_apm.sdk.models.workload import (
    FileServerAddRequest,
    FileServerPathSelector,
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
    async with aiointercept(mock_external_urls=True) as m:
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
    async with aiointercept(mock_external_urls=True) as m:
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
    async with aiointercept(mock_external_urls=True) as m:
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


@pytest.mark.parametrize("login_password", ["", "   "], ids=["empty", "whitespace_only"])
def test_add_file_server_blank_password_raises_value_error(login_password: str) -> None:
    """FileServerAddRequest raises ValueError at construction when login_password is empty
    or whitespace only."""
    with pytest.raises(ValueError, match="login_password"):
        FileServerAddRequest(
            namespace=NAMESPACE,
            host_ip="192.0.2.3",
            server_type=FileServerType.SMB,
            plan_id="plan-uuid-001",
            login_user="testuser",
            login_password=login_password,
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
