"""Unit tests for apm activity restore commands: list/get/cancel."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    HypervisorType,
    LogLevel,
    RestoreActivityStatus,
    RestoreType,
    WorkloadCategory,
)
from synology_apm.sdk.models.activity import ActivityLogEntry, RestoreActivity
from synology_apm.sdk.models.hypervisor import Hypervisor
from synology_apm.sdk.models.location import LocationInfo
from tests.unit.cli.conftest import invoke_cli

SAMPLE_RESTORE_ACT = RestoreActivity(
    activity_id="rst-uid-001",
    execution_id="97",
    namespace="ns-001",
    category=WorkloadCategory.M365,
    workload_type=ActivityWorkloadType.M365,
    workload_id="wl-uid-001",
    workload_namespace="wl-ns-001",
    workload_name="alice@contoso.com",
    plan_name="",
    status=RestoreActivityStatus.SUCCESS,
    started_at=datetime(2026, 4, 27, 9, 0, tzinfo=UTC),
    finished_at=datetime(2026, 4, 27, 9, 0, 6, tzinfo=UTC),
    duration_seconds=6,
    data_transferred_bytes=1601,
    progress=100,
    restore_type=RestoreType.FILE_LEVEL,
    restore_destination="alice@contoso.com",
    operator="admin",
    processed_success_count=2,
    processed_warning_count=0,
    processed_error_count=0,
    version_timestamp=datetime(2026, 4, 25, 0, 0, tzinfo=UTC),
    restore_from_info=LocationInfo(
        is_remote_storage=False, identifier="", name="apm-server-01", endpoint="192.0.2.1", vault=None,
    ),
    destination_path="/some/path",
    destination_inventory=Hypervisor(
        hypervisor_id="", hostname="esxi1.example.com", address="192.0.2.40",
        host_type=HypervisorType.VSPHERE_ESXI, account="", description="", port=0, version="",
    ),
)


RUNNING_RESTORE_ACT = RestoreActivity(
    activity_id="rst-uid-002",
    execution_id="98",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_VM,
    workload_id="wl-uid-002",
    workload_namespace="",
    workload_name="vm-web-01",
    plan_name="",
    status=RestoreActivityStatus.RESTORING,
    started_at=datetime(2026, 4, 27, 10, 0, tzinfo=UTC),
    finished_at=None,
    duration_seconds=None,
    data_transferred_bytes=None,
    progress=55,
    restore_type=RestoreType.FULL,
    restore_destination="vm-web-01-restored",
    operator="admin",
)


_RESTORE_ACT_WITH_LOGS = RestoreActivity(
    activity_id="rst-uid-010",
    execution_id="99",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_VM,
    workload_id="wl-uid-002",
    workload_namespace="",
    workload_name="vm-app-01",
    plan_name="",
    status=RestoreActivityStatus.SUCCESS,
    started_at=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
    finished_at=datetime(2026, 5, 1, 8, 10, tzinfo=UTC),
    duration_seconds=600,
    data_transferred_bytes=500,
    progress=100,
    restore_type=RestoreType.FULL,
    restore_destination="vm-app-01-restored",
    operator="admin",
    log_entries=(
        ActivityLogEntry(timestamp=datetime(2026, 5, 1, 8, 0, tzinfo=UTC), level=LogLevel.INFO, message="Restore started"),
        ActivityLogEntry(timestamp=datetime(2026, 5, 1, 8, 10, tzinfo=UTC), level=LogLevel.ERROR, message="Disk error on block 4"),
    ),
)


_RUNNING_RESTORE_ACT_WITH_ITEMS = RestoreActivity(
    activity_id="rst-uid-020",
    execution_id="200",
    namespace="ns-001",
    category=WorkloadCategory.M365,
    workload_type=ActivityWorkloadType.M365,
    workload_id="wl-uid-m365",
    workload_namespace="",
    workload_name="alice@contoso.com",
    plan_name="",
    status=RestoreActivityStatus.RESTORING,
    started_at=datetime(2026, 5, 1, 11, 0, tzinfo=UTC),
    finished_at=None,
    duration_seconds=None,
    data_transferred_bytes=None,
    progress=0,
    restore_type=RestoreType.FILE_LEVEL,
    restore_destination="alice@contoso.com",
    operator="admin",
    processed_success_count=80,
    processed_warning_count=2,
    processed_error_count=0,
)


def test_restore_list_table_output(mock_apm: AsyncMock) -> None:
    """activity restore list should display workload names and status."""
    mock_apm.activities.restore.list.return_value = ([SAMPLE_RESTORE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "restore", "list"])
    assert result.exit_code == 0, result.output
    assert "alice@contoso.com" in result.output
    assert "admin" in result.output


def test_restore_list_verbose_shows_workload_namespace(mock_apm: AsyncMock) -> None:
    """activity restore list --verbose should display Workload ID and Workload Namespace columns."""
    mock_apm.activities.restore.list.return_value = ([SAMPLE_RESTORE_ACT], 5)
    result = invoke_cli(
        mock_apm,
        ["activity", "restore", "list", "--verbose"],
        env={"COLUMNS": "300"},
    )
    assert result.exit_code == 0, result.output
    assert "wl-uid-001" in result.output
    assert "wl-ns-001" in result.output


def test_restore_list_csv_output(mock_apm: AsyncMock) -> None:
    """activity restore list --output csv should output CSV with an activity_id field."""
    mock_apm.activities.restore.list.return_value = ([SAMPLE_RESTORE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--output", "csv"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "activity_id" in lines[0]
    assert "rst-uid-001" in result.output


@pytest.mark.parametrize("status_flags,expected_status", [
    (["--status", "restoring"], [RestoreActivityStatus.RESTORING]),
    (["--status", "success", "--status", "failed"], [RestoreActivityStatus.SUCCESS, RestoreActivityStatus.FAILED]),
])
def test_restore_list_status_filter(mock_apm: AsyncMock, status_flags: list[str], expected_status: list[RestoreActivityStatus]) -> None:
    mock_apm.activities.restore.list.return_value = ([RUNNING_RESTORE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "restore", "list"] + status_flags)
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.restore.list.call_args.kwargs
    assert call_kwargs["status"] == expected_status


def test_activity_restore_list_search_filter(mock_apm: AsyncMock) -> None:
    """activity restore list --search <keyword> should pass keyword to the SDK."""
    mock_apm.activities.restore.list.return_value = ([RUNNING_RESTORE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--search", "corp-pc"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.restore.list.call_args.kwargs
    assert call_kwargs["keyword"] == "corp-pc"


def test_restore_list_invalid_status(mock_apm: AsyncMock) -> None:
    """activity restore list --status invalid should exit with code 1."""
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--status", "backing_up"])
    assert result.exit_code == 1


def test_restore_get_with_id(mock_apm: AsyncMock) -> None:
    """activity restore get --id <id> should call activities.restore.get and display the details."""
    mock_apm.activities.restore.get.return_value = SAMPLE_RESTORE_ACT
    result = invoke_cli(mock_apm, ["activity", "restore", "get", "--id", "rst-uid-001"])
    assert result.exit_code == 0, result.output
    mock_apm.activities.restore.get.assert_called_once_with("rst-uid-001")
    assert "alice@contoso.com" in result.output
    assert "File Level Restore" in result.output


def test_restore_get_without_id_shows_help(mock_apm: AsyncMock) -> None:
    """activity restore get without --id should show help and exit with code 0."""
    result = invoke_cli(mock_apm, ["activity", "restore", "get"])
    assert result.exit_code == 0
    assert "--id" in result.output


def test_restore_get_shows_operator_and_destination(mock_apm: AsyncMock) -> None:
    """restore get should display Operator and Destination."""
    mock_apm.activities.restore.get.return_value = SAMPLE_RESTORE_ACT
    result = invoke_cli(mock_apm, ["activity", "restore", "get", "--id", "rst-uid-001"])
    assert result.exit_code == 0, result.output
    assert "admin" in result.output
    assert "Destination" in result.output


def test_restore_get_shows_version_and_destination_details(mock_apm: AsyncMock) -> None:
    """restore get should display Version, Restore from, Destination path, and Destination hypervisor."""
    mock_apm.activities.restore.get.return_value = SAMPLE_RESTORE_ACT
    result = invoke_cli(mock_apm, ["activity", "restore", "get", "--id", "rst-uid-001"])
    assert result.exit_code == 0, result.output
    assert "Version" in result.output
    assert "Restore from" in result.output
    assert "apm-server-01" in result.output
    assert "Destination path" in result.output
    assert "/some/path" in result.output
    assert "Destination hypervisor" in result.output
    assert "esxi1.example.com" in result.output
    assert "192.0.2.40" in result.output


def test_restore_get_hides_optional_fields_when_absent(mock_apm: AsyncMock) -> None:
    """restore get should not show Version/Restore from/Destination path/Destination hypervisor when unset."""
    mock_apm.activities.restore.get.return_value = RUNNING_RESTORE_ACT
    result = invoke_cli(mock_apm, ["activity", "restore", "get", "--id", "rst-uid-002"])
    assert result.exit_code == 0, result.output
    assert "Version:" not in result.output
    assert "Restore from:" not in result.output
    assert "Destination path:" not in result.output
    assert "Destination hypervisor:" not in result.output


def test_restore_cancel_with_yes_flag(mock_apm: AsyncMock) -> None:
    """activity restore cancel --yes should query the list then call cancel with the Activity object."""
    mock_apm.activities.restore.list.return_value = ([RUNNING_RESTORE_ACT], 5)
    mock_apm.activities.restore.cancel.return_value = None
    result = invoke_cli(mock_apm, [
        "activity", "restore", "cancel", "--id", "rst-uid-002", "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.activities.restore.cancel.assert_called_once_with(RUNNING_RESTORE_ACT)


def test_restore_cancel_not_found_exits_error(mock_apm: AsyncMock) -> None:
    """activity restore cancel should exit with code 1 when the activity is not found."""
    mock_apm.activities.restore.list.return_value = ([], 5)
    result = invoke_cli(mock_apm, [
        "activity", "restore", "cancel", "--id", "nonexistent", "--yes",
    ])
    assert result.exit_code == 1


def test_restore_cancel_abort_on_no(mock_apm: AsyncMock) -> None:
    """activity restore cancel should exit with code 4 when user declines the confirmation prompt."""
    mock_apm.activities.restore.list.return_value = ([RUNNING_RESTORE_ACT], 5)
    result = invoke_cli(mock_apm, [
        "activity", "restore", "cancel", "--id", "rst-uid-002",
    ], input="n\n")
    assert result.exit_code == 4
    mock_apm.activities.restore.cancel.assert_not_called()


def test_activity_restore_get_search_mode_calls_sdk_method(mock_apm: AsyncMock) -> None:
    """activity restore get <NAME> should call get_latest_by_workload_name()."""
    mock_apm.activities.restore.get_latest_by_workload_name.return_value = SAMPLE_RESTORE_ACT
    result = invoke_cli(mock_apm, ["activity", "restore", "get", "alice@contoso.com"])
    assert result.exit_code == 0, result.output
    mock_apm.activities.restore.get_latest_by_workload_name.assert_called_once_with("alice@contoso.com")


def test_activity_restore_get_search_mode_not_found_exits_error(mock_apm: AsyncMock) -> None:
    """Should exit with code 1 when get_latest_by_workload_name raises ResourceNotFoundError."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    mock_apm.activities.restore.get_latest_by_workload_name.side_effect = ResourceNotFoundError(
        "No restore activity found for workload 'Corp Share'.",
        resource_type="Activity",
        resource_id="Corp Share",
    )
    result = invoke_cli(mock_apm, ["activity", "restore", "get", "Corp Share"])
    assert result.exit_code == 1


def test_restore_list_json_output(mock_apm: AsyncMock) -> None:
    """activity restore list --output json should produce a JSON array."""
    mock_apm.activities.restore.list.return_value = ([SAMPLE_RESTORE_ACT], 1)
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--output", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["activity_id"] == "rst-uid-001"


def test_restore_list_yaml_output(mock_apm: AsyncMock) -> None:
    """activity restore list --output yaml should produce YAML."""
    mock_apm.activities.restore.list.return_value = ([SAMPLE_RESTORE_ACT], 1)
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--output", "yaml"])
    assert result.exit_code == 0, result.output
    assert "rst-uid-001" in result.output


def test_restore_list_apm_error_exits_with_code_1(mock_apm: AsyncMock) -> None:
    """activity restore list should exit with code 1 when the SDK raises an APMError."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    mock_apm.activities.restore.list.side_effect = ResourceNotFoundError(
        "Not found.", resource_type="Activity", resource_id=""
    )
    result = invoke_cli(mock_apm, ["activity", "restore", "list"])
    assert result.exit_code == 1


def test_restore_get_json_output(mock_apm: AsyncMock) -> None:
    """activity restore get --id X --output json should produce JSON with activity_id."""
    mock_apm.activities.restore.get.return_value = SAMPLE_RESTORE_ACT
    result = invoke_cli(mock_apm, [
        "activity", "restore", "get", "--id", "rst-uid-001", "--output", "json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["activity_id"] == "rst-uid-001"
    assert "version_timestamp" in data
    assert "restore_from_info" in data
    assert data["destination_path"] == "/some/path"
    assert data["destination_inventory"]["hostname"] == "esxi1.example.com"
    assert data["destination_inventory"]["address"] == "192.0.2.40"
    assert data["destination_inventory"]["host_type"] == "vsphere_esxi"


def test_restore_get_yaml_output(mock_apm: AsyncMock) -> None:
    """activity restore get --id X --output yaml should produce YAML with activity_id."""
    mock_apm.activities.restore.get.return_value = SAMPLE_RESTORE_ACT
    result = invoke_cli(mock_apm, [
        "activity", "restore", "get", "--id", "rst-uid-001", "--output", "yaml",
    ])
    assert result.exit_code == 0, result.output
    assert "rst-uid-001" in result.output
    assert "version_timestamp" in result.output
    assert "restore_from_info" in result.output
    assert "destination_path" in result.output
    assert "/some/path" in result.output
    assert "destination_inventory" in result.output
    assert "esxi1.example.com" in result.output
    assert "192.0.2.40" in result.output
    assert "vsphere_esxi" in result.output


def test_restore_get_with_log_entries_displays_log_table(mock_apm: AsyncMock) -> None:
    """restore get detail view should display a log table when log_entries is set."""
    mock_apm.activities.restore.get.return_value = _RESTORE_ACT_WITH_LOGS
    result = invoke_cli(mock_apm, ["activity", "restore", "get", "--id", "rst-uid-010"])
    assert result.exit_code == 0, result.output
    assert "Logs" in result.output
    assert "Restore started" in result.output
    assert "Disk error on block 4" in result.output


def test_restore_cancel_without_id_shows_help(mock_apm: AsyncMock) -> None:
    """activity restore cancel without --id should show help and exit with code 0."""
    result = invoke_cli(mock_apm, ["activity", "restore", "cancel"])
    assert result.exit_code == 0
    assert "--id" in result.output


def test_restore_cancel_confirmation_shows_items_processed(mock_apm: AsyncMock) -> None:
    """restore cancel confirmation should display items count when items_processed is set."""
    mock_apm.activities.restore.list.return_value = ([_RUNNING_RESTORE_ACT_WITH_ITEMS], 1)
    result = invoke_cli(mock_apm, [
        "activity", "restore", "cancel", "--id", "rst-uid-020",
    ], input="n\n")
    assert result.exit_code == 4
    assert "items" in result.output


def test_activity_restore_list_history_flag_passes_history_true(mock_apm: AsyncMock) -> None:
    """activity restore list --history should pass history=True to the SDK."""
    mock_apm.activities.restore.list.return_value = ([SAMPLE_RESTORE_ACT], 1)
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--history"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.restore.list.call_args.kwargs
    assert call_kwargs["history"] is True


def test_activity_restore_list_shows_no_ongoing_message_when_empty(mock_apm: AsyncMock) -> None:
    """activity restore list with empty result and no --history should show a friendly empty message."""
    mock_apm.activities.restore.list.return_value = ([], 0)
    result = invoke_cli(mock_apm, ["activity", "restore", "list"])
    assert result.exit_code == 0, result.output
    assert "No ongoing restore tasks" in result.output


def test_restore_cancel_uses_recent_mode_without_status_filter(mock_apm: AsyncMock) -> None:
    """activity restore cancel should call list() with only limit=500 (RECENT mode, no status filter)."""
    mock_apm.activities.restore.list.return_value = ([RUNNING_RESTORE_ACT], 1)
    mock_apm.activities.restore.cancel.return_value = None
    invoke_cli(mock_apm, [
        "activity", "restore", "cancel", "--id", "rst-uid-002", "--yes",
    ])
    call_kwargs = mock_apm.activities.restore.list.call_args.kwargs
    assert call_kwargs.get("limit") == 500
    assert "status" not in call_kwargs


def test_activity_restore_list_history_empty_does_not_show_ongoing_message(mock_apm: AsyncMock) -> None:
    """activity restore list --history with empty result should NOT show the 'No ongoing' message."""
    mock_apm.activities.restore.list.return_value = ([], 0)
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--history"])
    assert result.exit_code == 0, result.output
    assert "No ongoing" not in result.output


def test_activity_restore_list_page_all_combines_pages(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """activity restore list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    act_2 = dataclasses.replace(SAMPLE_RESTORE_ACT, activity_id="rst-uid-003", workload_name="bob@contoso.com")
    mock_apm.activities.restore.list.side_effect = [
        ([SAMPLE_RESTORE_ACT], 2),
        ([act_2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "activity", "restore", "list", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "alice@contoso.com" in result.output
    assert "bob@contoso.com" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.activities.restore.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.activities.restore.list.call_args_list[1].kwargs["offset"] == 1
