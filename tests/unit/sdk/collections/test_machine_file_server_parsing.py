"""Unit tests for MachineWorkloadCollection: fs_config parsing (list() FS workload variant)
and delete().
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from synology_apm.sdk.collections.machine import MachineWorkloadCollection
from synology_apm.sdk.enums import (
    FileServerType,
    MachineWorkloadType,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import InvalidOperationError
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.workload import (
    FileServerConfig,
    FileServerPathSelector,
    MachineWorkload,
)
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
