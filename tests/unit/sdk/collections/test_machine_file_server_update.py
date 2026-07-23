"""Unit tests for MachineWorkloadCollection.update_file_server()."""
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
from synology_apm.sdk.exceptions import APIError, DuplicateWorkloadError, InvalidOperationError
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.workload import (
    FileServerConfig,
    FileServerPathSelector,
    FileServerUpdateRequest,
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

    with (
        patch.object(session, "get", new_callable=AsyncMock) as mock_get,
        pytest.raises(InvalidOperationError) as exc_info,
    ):
        await collection.update_file_server(SAMPLE_WL_OBJ, req)
    mock_get.assert_not_called()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=SAMPLE_WL_OBJ.workload_id)


@pytest.mark.parametrize("login_password", ["", "   "], ids=["empty_string", "whitespace_only"])
def test_update_file_server_blank_password_raises_value_error(login_password: str) -> None:
    """FileServerUpdateRequest raises ValueError at construction when login_password is
    empty or whitespace only."""
    with pytest.raises(ValueError, match="login_password"):
        FileServerUpdateRequest(host_ip="192.0.2.10", login_user="admin", login_password=login_password)


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
