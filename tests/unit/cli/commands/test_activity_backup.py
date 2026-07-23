"""Unit tests for apm activity backup commands: list/get/cancel."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    BackupScope,
    LogLevel,
    M365WorkloadType,
    MachineWorkloadType,
    VerifyStatus,
    WorkloadCategory,
)
from synology_apm.sdk.models.activity import ActivityLogEntry, BackupActivity
from tests.unit.cli.conftest import invoke_cli

SAMPLE_ACT = BackupActivity(
    activity_id="act-uid-001",
    execution_id="ABE_1",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_PC,
    workload_id="wl-uid-001",
    workload_namespace="wl-ns-001",
    workload_name="CORP-PC-001",
    plan_name="Daily Backup",
    status=BackupActivityStatus.SUCCESS,
    started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    finished_at=datetime(2026, 4, 21, 9, 30, tzinfo=UTC),
    duration_seconds=1800,
    data_transferred_bytes=1024 * 1024 * 100,
    progress=100,
)


RUNNING_ACT = BackupActivity(
    activity_id="act-uid-002",
    execution_id="ABE_2",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_PC,
    workload_id="wl-uid-001",
    workload_namespace="",
    workload_name="CORP-PC-001",
    plan_name="Daily Backup",
    status=BackupActivityStatus.BACKING_UP,
    started_at=datetime(2026, 4, 21, 10, 0, tzinfo=UTC),
    finished_at=None,
    duration_seconds=None,
    data_transferred_bytes=None,
    progress=45,
)


_ACT_WITH_SCOPE = BackupActivity(
    activity_id="act-scope-001",
    execution_id="ABE_10",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_PC,
    workload_id="wl-uid-001",
    workload_namespace="",
    workload_name="CORP-PC-001",
    plan_name="Daily",
    status=BackupActivityStatus.SUCCESS,
    started_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
    finished_at=datetime(2026, 5, 1, 9, 30, tzinfo=UTC),
    duration_seconds=1800,
    data_transferred_bytes=None,
    progress=100,
    backup_scope=BackupScope.ENTIRE_DEVICE,
    verify_status=VerifyStatus.SUCCESS,
    log_entries=(
        ActivityLogEntry(
            timestamp=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
            level=LogLevel.INFO,
            message="Backup started",
        ),
        ActivityLogEntry(
            timestamp=datetime(2026, 5, 1, 9, 30, tzinfo=UTC),
            level=LogLevel.WARNING,
            message="One item skipped",
        ),
    ),
)


_RUNNING_ACT_WITH_ITEMS = BackupActivity(
    activity_id="act-uid-003",
    execution_id="ABE_3",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_PC,
    workload_id="wl-uid-001",
    workload_namespace="",
    workload_name="CORP-PC-001",
    plan_name="Daily Backup",
    status=BackupActivityStatus.BACKING_UP,
    started_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    finished_at=None,
    duration_seconds=None,
    data_transferred_bytes=None,
    progress=0,
    processed_success_count=120,
    processed_warning_count=3,
    processed_error_count=0,
)


def test_activity_list_table_output(mock_apm: AsyncMock) -> None:
    """activity list should display workload names and status."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "backup", "list"])
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output


def test_activity_list_json_output(mock_apm: AsyncMock) -> None:
    """activity list --output json should output a JSON array."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "backup", "list", "--output", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["activity_id"] == "act-uid-001"


def test_activity_backup_list_csv_output(mock_apm: AsyncMock) -> None:
    """activity backup list --output csv should output CSV with an activity_id field."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 1)
    result = invoke_cli(mock_apm, ["activity", "backup", "list", "--output", "csv"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "activity_id" in lines[0]
    assert "act-uid-001" in result.output


@pytest.mark.parametrize("status_flags,expected_status", [
    (["--status", "backing_up"], [BackupActivityStatus.BACKING_UP]),
    (["--status", "failed", "--status", "partial"], [BackupActivityStatus.FAILED, BackupActivityStatus.PARTIAL]),
])
def test_activity_backup_list_status_filter(mock_apm: AsyncMock, status_flags: list[str], expected_status: list[BackupActivityStatus]) -> None:
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "backup", "list"] + status_flags)
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.backup.list.call_args.kwargs
    assert call_kwargs["status"] == expected_status


@pytest.mark.parametrize("args,kwarg_name,expected_value", [
    (["--namespace", "ns-001"], "namespace", ["ns-001"]),
    (["--search", "corp-pc"], "keyword", "corp-pc"),
])
def test_activity_backup_list_string_filter(mock_apm: AsyncMock, args: list[str], kwarg_name: str, expected_value: object) -> None:
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 5)
    result = invoke_cli(mock_apm, ["activity", "backup", "list"] + args)
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.backup.list.call_args.kwargs
    assert call_kwargs[kwarg_name] == expected_value


@pytest.mark.parametrize("option_flag", [
    "--status",
    "--machine-type",
    "--m365-type",
], ids=["status", "machine-type", "m365-type"])
def test_activity_backup_list_invalid_filter_value(mock_apm: AsyncMock, option_flag: str) -> None:
    """activity backup list with an invalid filter option value should exit with code 1."""
    result = invoke_cli(mock_apm, [
        "activity", "backup", "list", option_flag, "invalid",
    ])
    assert result.exit_code == 1


@pytest.mark.parametrize("args,expected_machine_types,expected_m365_types", [
    (
        ["--machine-type", "vm", "--machine-type", "fs"],
        [MachineWorkloadType.VM, MachineWorkloadType.FS],
        None,
    ),
    (
        ["--m365-type", "exchange", "--m365-type", "teams"],
        None,
        [M365WorkloadType.EXCHANGE, M365WorkloadType.TEAMS],
    ),
])
def test_activity_backup_list_workload_type_filter(
    args: list[str],
    expected_machine_types: list[MachineWorkloadType] | None,
    expected_m365_types: list[M365WorkloadType] | None,
) -> None:
    mock_apm = AsyncMock()
    mock_apm.activities.backup.list.return_value = ([], 0)
    result = invoke_cli(mock_apm, ["activity", "backup", "list"] + args)
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.backup.list.call_args.kwargs
    assert call_kwargs["machine_types"] == expected_machine_types
    assert call_kwargs["m365_types"] == expected_m365_types


def test_activity_list_verbose_shows_transferred(mock_apm: AsyncMock) -> None:
    """activity list --verbose should display Transferred, Workload ID, and Workload Namespace columns."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 5)
    result = invoke_cli(
        mock_apm,
        ["activity", "backup", "list", "--verbose"],
        env={"COLUMNS": "300"},
    )
    assert result.exit_code == 0, result.output
    assert "Transferred" in result.output
    assert "wl-uid-001" in result.output
    assert "wl-ns-001" in result.output


def test_activity_get_with_id_option(mock_apm: AsyncMock) -> None:
    """activity get --id <id> should call activities.get and display the details."""
    mock_apm.activities.backup.get.return_value = SAMPLE_ACT
    result = invoke_cli(mock_apm, ["activity", "backup", "get", "--id", "act-uid-001"])
    assert result.exit_code == 0, result.output
    mock_apm.activities.backup.get.assert_called_once_with("act-uid-001")


def test_activity_get_without_id_shows_help(mock_apm: AsyncMock) -> None:
    """activity get without --id should show help and exit with code 0."""
    result = invoke_cli(mock_apm, ["activity", "backup", "get"])
    assert result.exit_code == 0
    assert "--id" in result.output


def test_activity_get_m365_shows_processed_items(mock_apm: AsyncMock) -> None:
    """M365 activity get detail should display the Processed items row."""
    m365_act = BackupActivity(
        activity_id="act-m365-001",
        execution_id="M365_1",
        namespace="ns-001",
        category=WorkloadCategory.M365,
        workload_type=ActivityWorkloadType.M365,
        workload_id="m365-uid-001",
        workload_namespace="",
        workload_name="alice@contoso.com",
        plan_name="M365 Daily",
        status=BackupActivityStatus.SUCCESS,
        started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 21, 9, 30, tzinfo=UTC),
        duration_seconds=1800,
        data_transferred_bytes=None,
        progress=0,
        processed_success_count=5,
        processed_warning_count=2,
        processed_error_count=1,
    )
    mock_apm.activities.backup.get.return_value = m365_act
    result = invoke_cli(mock_apm, ["activity", "backup", "get", "--id", "act-m365-001"])
    assert result.exit_code == 0, result.output
    assert "Processed items" in result.output
    assert "5 succeeded" in result.output
    assert "2 warning" in result.output
    assert "1 error" in result.output


def test_activity_get_machine_no_processed_items(mock_apm: AsyncMock) -> None:
    """Machine activity get detail should not display the Processed items row."""
    mock_apm.activities.backup.get.return_value = SAMPLE_ACT
    result = invoke_cli(mock_apm, ["activity", "backup", "get", "--id", "act-uid-001"])
    assert result.exit_code == 0, result.output
    assert "Processed items" not in result.output


def test_activity_cancel_with_yes_flag(mock_apm: AsyncMock) -> None:
    """activity cancel --yes should skip confirmation and call activities.cancel."""
    mock_apm.activities.backup.list.return_value = ([RUNNING_ACT], 5)
    mock_apm.activities.backup.cancel.return_value = None
    result = invoke_cli(mock_apm, [
        "activity", "backup", "cancel", "--id", "act-uid-002", "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.activities.backup.cancel.assert_called_once_with(RUNNING_ACT)


def test_activity_cancel_not_found_exits_error(mock_apm: AsyncMock) -> None:
    """activity cancel should exit with code 1 when the activity is not found."""
    mock_apm.activities.backup.list.return_value = ([], 5)  # no running activities
    result = invoke_cli(mock_apm, [
        "activity", "backup", "cancel", "--id", "nonexistent", "--yes",
    ])
    assert result.exit_code == 1


def test_activity_cancel_abort_on_no(mock_apm: AsyncMock) -> None:
    """activity cancel should exit with code 4 when user declines the confirmation prompt."""
    mock_apm.activities.backup.list.return_value = ([RUNNING_ACT], 5)
    result = invoke_cli(mock_apm, [
        "activity", "backup", "cancel", "--id", "act-uid-002",
    ], input="n\n")
    assert result.exit_code == 4
    mock_apm.activities.backup.cancel.assert_not_called()


def test_activity_backup_get_search_mode_calls_sdk_method(mock_apm: AsyncMock) -> None:
    """activity backup get <NAME> should call get_latest_by_workload_name()."""
    mock_apm.activities.backup.get_latest_by_workload_name.return_value = SAMPLE_ACT
    result = invoke_cli(mock_apm, ["activity", "backup", "get", "CORP-PC-001"])
    assert result.exit_code == 0, result.output
    mock_apm.activities.backup.get_latest_by_workload_name.assert_called_once_with("CORP-PC-001")


def test_activity_backup_get_search_mode_not_found_exits_error(mock_apm: AsyncMock) -> None:
    """Should exit with code 1 when get_latest_by_workload_name raises ResourceNotFoundError."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    mock_apm.activities.backup.get_latest_by_workload_name.side_effect = ResourceNotFoundError(
        "No backup activity found for workload 'CORP-PC-001'.",
        resource_type="Activity",
        resource_id="CORP-PC-001",
    )
    result = invoke_cli(mock_apm, ["activity", "backup", "get", "CORP-PC-001"])
    assert result.exit_code == 1


def test_activity_backup_list_yaml_output(mock_apm: AsyncMock) -> None:
    """activity backup list --output yaml should produce YAML with activity_id field."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 1)
    result = invoke_cli(mock_apm, ["activity", "backup", "list", "--output", "yaml"])
    assert result.exit_code == 0, result.output
    assert "act-uid-001" in result.output


def test_activity_backup_list_apm_error_exits_with_code_1(mock_apm: AsyncMock) -> None:
    """activity backup list should exit with code 1 when the SDK raises ResourceNotFoundError."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    mock_apm.activities.backup.list.side_effect = ResourceNotFoundError(
        "No activities found.", resource_type="Activity", resource_id=""
    )
    result = invoke_cli(mock_apm, ["activity", "backup", "list"])
    assert result.exit_code == 1


def test_activity_backup_get_json_output(mock_apm: AsyncMock) -> None:
    """activity backup get --id X --output json should return JSON with activity_id field."""
    mock_apm.activities.backup.get.return_value = SAMPLE_ACT
    result = invoke_cli(mock_apm, [
        "activity", "backup", "get", "--id", "act-uid-001", "--output", "json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["activity_id"] == "act-uid-001"


def test_activity_backup_get_yaml_output(mock_apm: AsyncMock) -> None:
    """activity backup get --id X --output yaml should return YAML with activity_id field."""
    mock_apm.activities.backup.get.return_value = SAMPLE_ACT
    result = invoke_cli(mock_apm, [
        "activity", "backup", "get", "--id", "act-uid-001", "--output", "yaml",
    ])
    assert result.exit_code == 0, result.output
    assert "act-uid-001" in result.output


def test_activity_backup_get_with_backup_scope_displays_scope(mock_apm: AsyncMock) -> None:
    """backup get detail view should display Backup Scope when the field is set."""
    mock_apm.activities.backup.get.return_value = _ACT_WITH_SCOPE
    result = invoke_cli(mock_apm, [
        "activity", "backup", "get", "--id", "act-scope-001",
    ])
    assert result.exit_code == 0, result.output
    assert "Backup Scope" in result.output
    assert "Entire Device" in result.output


def test_activity_backup_get_with_log_entries_displays_log_table(mock_apm: AsyncMock) -> None:
    """backup get detail view should display a log table when log_entries is set."""
    mock_apm.activities.backup.get.return_value = _ACT_WITH_SCOPE
    result = invoke_cli(mock_apm, [
        "activity", "backup", "get", "--id", "act-scope-001",
    ])
    assert result.exit_code == 0, result.output
    assert "Logs" in result.output
    assert "Backup started" in result.output
    assert "One item skipped" in result.output


def test_activity_backup_get_json_includes_backup_scope_and_verify_status(mock_apm: AsyncMock) -> None:
    """backup get --output json should include backup_scope and verify_status when set."""
    mock_apm.activities.backup.get.return_value = _ACT_WITH_SCOPE
    result = invoke_cli(mock_apm, [
        "activity", "backup", "get", "--id", "act-scope-001", "--output", "json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["backup_scope"] == "entire_device"
    assert data["verify_status"] == "success"
    assert len(data["log_entries"]) == 2
    assert data["log_entries"][0]["message"] == "Backup started"
    assert data["log_entries"][1]["message"] == "One item skipped"


def test_activity_backup_cancel_without_id_shows_help(mock_apm: AsyncMock) -> None:
    """activity backup cancel without --id should show help and exit with code 0."""
    result = invoke_cli(mock_apm, ["activity", "backup", "cancel"])
    assert result.exit_code == 0
    assert "--id" in result.output


def test_activity_backup_cancel_confirmation_shows_items_processed(mock_apm: AsyncMock) -> None:
    """activity backup cancel confirmation should display items count when items_processed is set."""
    mock_apm.activities.backup.list.return_value = ([_RUNNING_ACT_WITH_ITEMS], 1)
    result = invoke_cli(mock_apm, [
        "activity", "backup", "cancel", "--id", "act-uid-003",
    ], input="n\n")
    assert result.exit_code == 4
    assert "items" in result.output


def test_activity_backup_list_history_flag_passes_history_true(mock_apm: AsyncMock) -> None:
    """activity backup list --history should pass history=True to the SDK."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 1)
    result = invoke_cli(mock_apm, ["activity", "backup", "list", "--history"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.backup.list.call_args.kwargs
    assert call_kwargs["history"] is True


def test_activity_backup_list_default_mode_is_ongoing(mock_apm: AsyncMock) -> None:
    """activity backup list (no --history) should pass history=False to the SDK."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 1)
    result = invoke_cli(mock_apm, ["activity", "backup", "list"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.backup.list.call_args.kwargs
    assert call_kwargs["history"] is False


def test_activity_backup_list_offset_passed_to_sdk(mock_apm: AsyncMock) -> None:
    """activity backup list --offset 50 should pass offset=50 to the SDK."""
    mock_apm.activities.backup.list.return_value = ([SAMPLE_ACT], 100)
    result = invoke_cli(mock_apm, ["activity", "backup", "list", "--offset", "50"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.activities.backup.list.call_args.kwargs
    assert call_kwargs["offset"] == 50


def test_activity_backup_list_shows_no_ongoing_message_when_empty(mock_apm: AsyncMock) -> None:
    """activity backup list with empty result and no --history should show a friendly empty message."""
    mock_apm.activities.backup.list.return_value = ([], 0)
    result = invoke_cli(mock_apm, ["activity", "backup", "list"])
    assert result.exit_code == 0, result.output
    assert "No ongoing backup tasks" in result.output


def test_activity_backup_cancel_uses_recent_mode_without_status_filter(mock_apm: AsyncMock) -> None:
    """activity backup cancel should call list() with only limit=500 (RECENT mode, no status filter)."""
    mock_apm.activities.backup.list.return_value = ([RUNNING_ACT], 1)
    mock_apm.activities.backup.cancel.return_value = None
    invoke_cli(mock_apm, [
        "activity", "backup", "cancel", "--id", "act-uid-002", "--yes",
    ])
    call_kwargs = mock_apm.activities.backup.list.call_args.kwargs
    assert call_kwargs.get("limit") == 500
    assert "status" not in call_kwargs


def test_activity_backup_list_history_empty_does_not_show_ongoing_message(mock_apm: AsyncMock) -> None:
    """activity backup list --history with empty result should NOT show the 'No ongoing' message."""
    mock_apm.activities.backup.list.return_value = ([], 0)
    result = invoke_cli(mock_apm, ["activity", "backup", "list", "--history"])
    assert result.exit_code == 0, result.output
    assert "No ongoing" not in result.output


def test_activity_backup_list_page_all_combines_pages(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """activity backup list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    act_2 = dataclasses.replace(SAMPLE_ACT, activity_id="act-uid-003", workload_name="CORP-PC-002")
    mock_apm.activities.backup.list.side_effect = [
        ([SAMPLE_ACT], 2),
        ([act_2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "activity", "backup", "list", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output
    assert "CORP-PC-002" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.activities.backup.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.activities.backup.list.call_args_list[1].kwargs["offset"] == 1
