"""Unit tests for apm machine commands: list/get/backup/retire/cancel/change-plan."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, time
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import (
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
    VerifyStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.cli.conftest import invoke_cli

SAMPLE_WL = MachineWorkload(
    workload_id="wl-id-001",
    name="CORP-PC-001",
    category=WorkloadCategory.MACHINE,
    namespace="ns-001",
    last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    is_retired=False,
    protected_data_bytes=1024 * 1024 * 500,
    status=WorkloadStatus.SUCCESS,
    plan=ProtectionPlan(plan_id="plan-id-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC,
    agent_version="1.2.0",
    backup_server=LocationInfo(
        is_remote_storage=False,
        identifier="ns-server-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    ),
)


SAMPLE_PLAN = ProtectionPlan(
    plan_id="plan-id-001",
    name="Daily Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=10),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(2, 0)),
    ),
    workload_count=3,
)


SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="retire-plan-001",
    name="30-Day Archive",
    description="",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=False),
    workload_count=1,
)


SAMPLE_WL_RETIRED = dataclasses.replace(
    SAMPLE_WL, is_retired=True, status=WorkloadStatus.RETIRED,
    plan=RetirementPlan(plan_id="retire-plan-001", name="30-Day Archive"),
)


def make_mock_client(
    workloads: list[MachineWorkload] | None = None, plans: list[ProtectionPlan] | None = None
) -> AsyncMock:
    mock = AsyncMock()
    mock.machine.workloads.list.return_value = (workloads or [SAMPLE_WL], 5)
    mock.machine.workloads.get.return_value = SAMPLE_WL
    mock.machine.workloads.get_by_name.return_value = SAMPLE_WL
    mock.machine.workloads.backup_now.return_value = None
    mock.machine.workloads.cancel_backup.return_value = None
    mock.machine.workloads.retire.return_value = None
    mock.machine.workloads.change_plan.return_value = None
    mock.retirement_plans.get.return_value = SAMPLE_RETIREMENT_PLAN
    mock.retirement_plans.get_by_name.return_value = SAMPLE_RETIREMENT_PLAN
    mock.machine.workloads.list_versions.return_value = ([], 0)
    mock.machine.workloads.lock_version.return_value = None
    mock.machine.workloads.unlock_version.return_value = None
    mock.machine.plans.list.return_value = (plans or [SAMPLE_PLAN], 5)
    mock.machine.plans.get.return_value = SAMPLE_PLAN
    mock.plans.get.return_value = SAMPLE_PLAN
    mock.plans.get_by_name.return_value = SAMPLE_PLAN
    mock.activities.list.return_value = ([], 5)
    return mock


def _sdk_error() -> ResourceNotFoundError:
    return ResourceNotFoundError("not found", resource_type="Workload", resource_id="x")


SAMPLE_WL_2 = dataclasses.replace(SAMPLE_WL, workload_id="wl-id-002", name="CORP-PC-002")


def test_machine_list_invalid_type() -> None:
    """machine list --type <invalid> should exit with code 1."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--type", "server"])
    assert result.exit_code == 1


def test_machine_all_list_table_output() -> None:
    """machine list (no parameters) default output should include the workload name."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output


def test_machine_all_list_json_output() -> None:
    """machine list --output json should output a JSON array."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--output", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["workload_id"] == "wl-id-001"


def test_machine_list_namespace_filter() -> None:
    """machine list --namespace <ns> should pass namespace to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--namespace", "ns-001"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["namespace"] == "ns-001"


def test_machine_list_plan_filter_resolves_by_name() -> None:
    """machine list --plan <name> resolves against Protection Plans and passes plan= to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--plan", "Daily Backup"])
    assert result.exit_code == 0, result.output
    mock_apm.plans.get_by_name.assert_awaited_once_with("Daily Backup")
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["plan"] == [SAMPLE_PLAN]


def test_machine_list_plan_filter_resolves_against_retirement_plans_when_retired() -> None:
    """machine list --retired --plan <name> resolves against Retirement Plans."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--retired", "--plan", "30-Day Archive"])
    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_awaited_once_with("30-Day Archive")
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["plan"] == [SAMPLE_RETIREMENT_PLAN]


def test_machine_list_no_plan_passes_none() -> None:
    """machine list without --plan passes plan=None to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["plan"] is None


def test_machine_list_shows_backup_server_name() -> None:
    """machine list table should display the backup server hostname."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output


def test_machine_list_hypervisor_filter() -> None:
    """machine list --hypervisor <id> should pass hypervisor_id to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "list", "--hypervisor", "978eabd4-e332-459f-a8e0-35a0aa312118",
    ])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["hypervisor_id"] == "978eabd4-e332-459f-a8e0-35a0aa312118"


@pytest.mark.parametrize("status_flags,expected_status", [
    (["--status", "failed"], [WorkloadStatus.FAILED]),
    (
        ["--status", "failed", "--status", "partial"],
        [WorkloadStatus.FAILED, WorkloadStatus.PARTIAL],
    ),
])
def test_machine_list_status_filter(status_flags: list[str], expected_status: list[WorkloadStatus]) -> None:
    """machine list --status <value> (repeatable) should pass status= to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", *status_flags])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["status"] == expected_status


def test_machine_list_no_status_passes_none() -> None:
    """machine list without --status should pass status=None to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["status"] is None


def test_machine_list_invalid_status_exits_1() -> None:
    """machine list --status <invalid> should exit with code 1."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--status", "nope"])
    assert result.exit_code == 1


@pytest.mark.parametrize("status_flags,expected_status", [
    (["--verify-status", "failed"], [VerifyStatus.FAILED]),
    (
        ["--verify-status", "not_enabled", "--verify-status", "waiting"],
        [VerifyStatus.NOT_ENABLED, VerifyStatus.WAITING],
    ),
])
def test_machine_list_verify_status_filter(
    status_flags: list[str], expected_status: list[VerifyStatus]
) -> None:
    """machine list --verify-status <value> (repeatable) should pass verify_status= to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", *status_flags])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["verify_status"] == expected_status


def test_machine_list_no_verify_status_passes_none() -> None:
    """machine list without --verify-status should pass verify_status=None to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["verify_status"] is None


def test_machine_list_invalid_verify_status_exits_1() -> None:
    """machine list --verify-status <invalid> should exit with code 1."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--verify-status", "nope"])
    assert result.exit_code == 1


@pytest.mark.parametrize("subcommand,expected_type", [
    ("vm", MachineWorkloadType.VM),
    ("pc", MachineWorkloadType.PC),
    ("ps", MachineWorkloadType.PS),
    ("fs", MachineWorkloadType.FS),
])
def test_machine_list_subcommand_passes_workload_type(subcommand: str, expected_type: MachineWorkloadType) -> None:
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--type", subcommand])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["workload_types"] == [expected_type]


def test_machine_list_multi_type_passes_workload_types() -> None:
    """machine list --type vm --type fs should pass both types as a list to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--type", "vm", "--type", "fs"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["workload_types"] == [MachineWorkloadType.VM, MachineWorkloadType.FS]


def test_machine_list_no_type_passes_none() -> None:
    """machine list without --type should pass workload_types=None to the SDK."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list.call_args.kwargs
    assert call_kwargs["workload_types"] is None


def test_machine_get_table_output() -> None:
    """machine get should display the details of the specified workload."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "get", "wl-id-001"])
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output
    mock_apm.machine.workloads.get_by_name.assert_called_once_with("wl-id-001", is_retired=False)


def test_machine_get_json_output() -> None:
    """machine get --output json should output a JSON object."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "get", "wl-id-001", "--output", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["workload_id"] == "wl-id-001"


def test_machine_backup_success() -> None:
    """machine backup should call backup_now and display a confirmation message."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "backup", "wl-id-001"])
    assert result.exit_code == 0, result.output
    mock_apm.machine.workloads.backup_now.assert_called_once_with(SAMPLE_WL)
    assert "Backup triggered" in result.output


def test_machine_backup_quiet_mode() -> None:
    """machine backup --quiet should produce no output."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "backup", "wl-id-001", "--quiet"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_machine_retire_with_yes_flag() -> None:
    """machine retire --yes should skip confirmation, resolve --plan by name, and call retire."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "retire", "wl-id-001",
        "--plan", "archive-plan-001",
        "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_called_once_with("archive-plan-001")
    mock_apm.retirement_plans.get.assert_not_called()
    mock_apm.machine.workloads.retire.assert_called_once_with(SAMPLE_WL, SAMPLE_RETIREMENT_PLAN)
    assert "retired" in result.output


def test_machine_retire_resolves_plan_by_uuid() -> None:
    """--plan with a UUID resolves via retirement_plans.get(), never calls retirement_plans.get_by_name()."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "retire", "wl-id-001",
        "--plan", "0c8f033b-1111-1111-1111-000000000001",
        "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get.assert_called_once_with("0c8f033b-1111-1111-1111-000000000001")
    mock_apm.retirement_plans.get_by_name.assert_not_called()


def test_machine_retire_shows_plan_info() -> None:
    """machine retire should display plan name and retention before prompting."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "retire", "wl-id-001",
        "--plan", "archive-plan-001", "--yes",
    ])
    assert result.exit_code == 0, result.output
    assert "30-Day Archive" in result.output        # plan name
    assert "30 days" in result.output               # retention from SAMPLE_RETIREMENT_PLAN
    assert "CORP-PC-001" in result.output           # workload name from SAMPLE_WL


def test_machine_retire_abort_on_no() -> None:
    """machine retire should exit with code 4 when user declines the confirmation prompt."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "retire", "wl-id-001",
        "--plan", "archive-plan-001",
    ], input="n\n")
    assert result.exit_code == 4
    mock_apm.machine.workloads.retire.assert_not_called()


# ── change-plan ──────────────────────────────────────────────────────────


def test_machine_change_plan_search_mode_active_workload() -> None:
    """change-plan on an active workload resolves --plan against Protection Plans by name."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "change-plan", "CORP-PC-001", "--plan", "Daily Backup", "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.plans.get_by_name.assert_called_once_with("Daily Backup")
    mock_apm.plans.get.assert_not_called()
    mock_apm.machine.workloads.change_plan.assert_called_once_with(SAMPLE_WL, SAMPLE_PLAN)
    assert "Plan changed" in result.output


def test_machine_change_plan_search_mode_retired_workload() -> None:
    """change-plan --retired resolves --plan against Retirement Plans for an already-retired workload."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_by_name.return_value = SAMPLE_WL_RETIRED
    result = invoke_cli(mock_apm, [
        "machine", "change-plan", "CORP-PC-001", "--retired", "--plan", "30-Day Archive", "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.machine.workloads.get_by_name.assert_called_once_with("CORP-PC-001", is_retired=True)
    mock_apm.retirement_plans.get_by_name.assert_called_once_with("30-Day Archive")
    mock_apm.retirement_plans.get.assert_not_called()
    mock_apm.machine.workloads.change_plan.assert_called_once_with(SAMPLE_WL_RETIRED, SAMPLE_RETIREMENT_PLAN)


def test_machine_change_plan_direct_mode() -> None:
    """change-plan --id/--namespace resolves the workload via get() (direct mode)."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "change-plan", "--id", "wl-id-001", "--namespace", "ns-001",
        "--plan", "plan-id-001", "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.machine.workloads.get.assert_called_once_with("wl-id-001", namespace="ns-001")
    mock_apm.machine.workloads.change_plan.assert_called_once_with(SAMPLE_WL, SAMPLE_PLAN)


def test_machine_change_plan_resolves_plan_by_uuid() -> None:
    """--plan with a UUID resolves via plans.get() (direct mode), never calls plans.get_by_name()."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "change-plan", "CORP-PC-001",
        "--plan", "0c8f033b-1111-1111-1111-000000000001", "--yes",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.plans.get.assert_called_once_with("0c8f033b-1111-1111-1111-000000000001")
    mock_apm.plans.get_by_name.assert_not_called()


def test_machine_change_plan_abort_on_no() -> None:
    """change-plan should exit with code 4 when user declines the confirmation prompt."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, [
        "machine", "change-plan", "CORP-PC-001", "--plan", "Daily Backup",
    ], input="n\n")
    assert result.exit_code == 4
    mock_apm.machine.workloads.change_plan.assert_not_called()


def test_machine_change_plan_no_plan_shows_help() -> None:
    """change-plan without --plan should show help and exit 0."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "change-plan", "CORP-PC-001"])
    assert result.exit_code == 0
    assert "Usage" in result.output
    mock_apm.machine.workloads.change_plan.assert_not_called()


def test_machine_change_plan_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.get_by_name.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, [
        "machine", "change-plan", "no-such-machine", "--plan", "plan-001", "--yes",
    ])
    assert result.exit_code == 1


def test_machine_all_list_verbose() -> None:
    """machine list --verbose should additionally show Workload ID and Namespace."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--verbose"], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "wl-id-001" in result.output
    assert "ns-001" in result.output


def test_machine_all_list_yaml_output() -> None:
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--output", "yaml"])
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output


def test_machine_get_yaml_output() -> None:
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "get", "wl-id-001", "--output", "yaml"])
    assert result.exit_code == 0, result.output
    assert "wl-id-001" in result.output


def test_machine_cancel_with_yes_flag() -> None:
    """machine cancel --yes should skip confirmation and call cancel_backup directly."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "cancel", "wl-id-001", "--yes"])
    assert result.exit_code == 0, result.output
    mock_apm.machine.workloads.cancel_backup.assert_called_once_with(SAMPLE_WL)
    assert "cancel" in result.output.lower()


def test_machine_cancel_abort_on_no() -> None:
    """machine cancel should exit with code 4 when user declines the confirmation prompt."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "cancel", "wl-id-001"], input="n\n")
    assert result.exit_code == 4
    mock_apm.machine.workloads.cancel_backup.assert_not_called()


def test_machine_list_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.list.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 1


def test_machine_get_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.get_by_name.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, ["machine", "get", "no-such-machine"])
    assert result.exit_code == 1


def test_machine_backup_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.get_by_name.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, ["machine", "backup", "no-such-machine"])
    assert result.exit_code == 1


def test_machine_cancel_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.get_by_name.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, ["machine", "cancel", "no-such-machine", "--yes"])
    assert result.exit_code == 1


def test_machine_retire_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.get_by_name.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, [
        "machine", "retire", "no-such-machine", "--plan", "plan-001", "--yes",
    ])
    assert result.exit_code == 1


def test_machine_list_csv_output() -> None:
    """machine list --output csv should output flat CSV with backup_server_name field, not nested backup_server dict."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "list", "--output", "csv"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "workload_id" in headers
    assert "backup_server_name" in headers      # flattened field
    assert "backup_server" not in headers       # nested field name must not appear
    assert "wl-id-001" in result.output
    assert "apm-server-01" in result.output     # value of backup_server.name


def test_machine_get_namespace_without_id_exits_1() -> None:
    """--namespace without --id should exit with code 1."""
    result = invoke_cli(AsyncMock(), ["machine", "get", "--namespace", "ns-001"])
    assert result.exit_code == 1


def test_machine_get_id_without_namespace_exits_1() -> None:
    """machine get --id X (no --namespace) should print error and exit 1."""
    result = invoke_cli(AsyncMock(), ["machine", "get", "--id", "wl-id-001"])
    assert result.exit_code == 1
    assert "--id requires --namespace" in result.output


def test_machine_get_name_and_id_conflict_exits_1() -> None:
    """machine get NAME --id X --namespace Y should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "machine", "get", "MyPC", "--id", "wl-id-001", "--namespace", "ns-001",
    ])
    assert result.exit_code == 1
    assert "cannot be used" in result.output


def test_machine_get_no_args_shows_help() -> None:
    """machine get (no positional arg, no --id) should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["machine", "get"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_machine_retire_no_plan_shows_help() -> None:
    """machine retire without --plan should show help and exit 0."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "retire", "CORP-PC-001"])
    assert result.exit_code == 0
    assert "Usage" in result.output
    mock_apm.machine.workloads.retire.assert_not_called()


def test_machine_list_all_status_label_branches() -> None:
    """machine list renders fmt_workload_status labels correctly for all WorkloadStatus values."""
    def _wl(
        status: WorkloadStatus,
        *,
        items_backed_up: int | None = None,
        backup_progress: int | None = None,
        is_retired: bool = False,
    ) -> MachineWorkload:
        return MachineWorkload(
            workload_id="wl-x",
            name="TestWL",
            category=WorkloadCategory.MACHINE,
            namespace="ns-001",
            last_backup_at=None,
            is_retired=is_retired,
            protected_data_bytes=0,
            status=status,
            plan=(
                RetirementPlan(plan_id="retire-x", name="Retirement Plan")
                if is_retired else ProtectionPlan(plan_id="plan-x", name="Test Plan", category=WorkloadCategory.MACHINE)
            ),
            workload_type=MachineWorkloadType.PC,
            agent_version=None,
            items_backed_up=items_backed_up,
            backup_progress=backup_progress,
        )

    workloads = [
        _wl(WorkloadStatus.QUEUING),                             # "Waiting for Backup"
        _wl(WorkloadStatus.BACKING_UP, items_backed_up=42),     # "Backing up (42 items)"
        _wl(WorkloadStatus.BACKING_UP, backup_progress=60),     # "Backing up (60%)"
        _wl(WorkloadStatus.BACKING_UP),                         # plain "Backing up"
        _wl(WorkloadStatus.FAILED),
        _wl(WorkloadStatus.PARTIAL),
        _wl(WorkloadStatus.CANCELED),
        _wl(WorkloadStatus.NO_BACKUPS),
        _wl(WorkloadStatus.DELETING),
        _wl(WorkloadStatus.RETIRED, is_retired=True),
    ]
    mock_apm = make_mock_client(workloads=workloads)
    result = invoke_cli(mock_apm, ["machine", "list"], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "Waiting for Backup" in result.output
    assert "Backing up (42 items)" in result.output
    assert "Backing up (60%)" in result.output
    assert "Backing up" in result.output
    assert "Failed" in result.output
    assert "Partial" in result.output
    assert "Canceled" in result.output
    assert "No Backups" in result.output
    assert "Deleting" in result.output
    assert "Retired" in result.output


def test_machine_list_page_all_table_combines_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """machine list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list.side_effect = [
        ([SAMPLE_WL], 2),
        ([SAMPLE_WL_2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "machine", "list", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output
    assert "CORP-PC-002" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.machine.workloads.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.machine.workloads.list.call_args_list[1].kwargs["offset"] == 1


def test_machine_list_page_all_json_output_is_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    """machine list --page-all --output json should stream one compact JSON object per item (NDJSON)."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list.side_effect = [
        ([SAMPLE_WL], 2),
        ([SAMPLE_WL_2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "machine", "list", "--limit", "1", "--page-all", "--output", "json",
    ])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.strip().splitlines() if ln]
    assert len(lines) == 2
    records = [json.loads(ln) for ln in lines]
    assert records[0]["workload_id"] == "wl-id-001"
    assert records[1]["workload_id"] == "wl-id-002"


def test_machine_list_page_all_csv_output_header_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """machine list --page-all --output csv should write the header row once, before all data rows."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list.side_effect = [
        ([SAMPLE_WL], 2),
        ([SAMPLE_WL_2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "machine", "list", "--limit", "1", "--page-all", "--output", "csv",
    ])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0].split(",").count("workload_id") == 1
    assert "wl-id-001" in result.output
    assert "wl-id-002" in result.output
    assert sum(1 for ln in lines if "workload_id" in ln) == 1


def test_machine_list_page_all_yaml_output_multi_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    """machine list --page-all --output yaml should produce a multi-document stream (--- per page)."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list.side_effect = [
        ([SAMPLE_WL], 2),
        ([SAMPLE_WL_2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "machine", "list", "--limit", "1", "--page-all", "--output", "yaml",
    ])
    assert result.exit_code == 0, result.output
    assert result.output.count("---") == 2
    assert "wl-id-001" in result.output
    assert "wl-id-002" in result.output


# ── handle_apm_error exit-code coverage ───────────────────────────────────


def test_machine_list_exits_3_on_not_management_server_error(mock_apm: AsyncMock) -> None:
    """handle_apm_error maps NotManagementServerError to exit code 3."""
    from synology_apm.sdk.exceptions import NotManagementServerError

    mock_apm.machine.workloads.list.side_effect = NotManagementServerError("not a management server")
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 3


def test_machine_list_exits_1_on_plan_name_conflict_error(mock_apm: AsyncMock) -> None:
    """handle_apm_error maps PlanNameConflictError to exit code 1."""
    from synology_apm.sdk.exceptions import PlanNameConflictError

    mock_apm.machine.workloads.list.side_effect = PlanNameConflictError(
        "plan name already exists", resource_type="ProtectionPlan", resource_id="Daily Backup"
    )
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 1


def test_machine_list_exits_1_on_duplicate_workload_error(mock_apm: AsyncMock) -> None:
    """handle_apm_error maps DuplicateWorkloadError to exit code 1."""
    from synology_apm.sdk.exceptions import DuplicateWorkloadError

    mock_apm.machine.workloads.list.side_effect = DuplicateWorkloadError(
        "duplicate workload", resource_type="file_server", resource_id="192.0.2.10"
    )
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 1


def test_machine_list_exits_1_on_resource_not_ready_error(mock_apm: AsyncMock) -> None:
    """handle_apm_error maps ResourceNotReadyError to exit code 1."""
    from synology_apm.sdk.exceptions import ResourceNotReadyError

    mock_apm.machine.workloads.list.side_effect = ResourceNotReadyError("resource not ready")
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == 1


# ── FS workload type label ─────────────────────────────────────────────────


def test_machine_list_fs_workload_shows_fs_type_label() -> None:
    """machine list shows 'File Server / SMB' in the Type column for an FS workload with SMB fs_config."""
    from synology_apm.sdk.enums import FileServerType
    from synology_apm.sdk.models.workload import FileServerConfig, FileServerPathSelector

    fs_wl = MachineWorkload(
        workload_id="wl-fs-001",
        name="Corp Share",
        category=WorkloadCategory.MACHINE,
        namespace="ns-001",
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.SUCCESS,
        plan=ProtectionPlan(plan_id="plan-id-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
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
    mock_apm = make_mock_client(workloads=[fs_wl])
    result = invoke_cli(mock_apm, ["machine", "list", "--type", "fs"], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "File Server / SMB" in result.output


def test_machine_get_shows_optional_detail_fields() -> None:
    """machine get renders the optional detail lines when the fields are populated."""
    from synology_apm.sdk.enums import VerifyStatus

    wl = dataclasses.replace(
        SAMPLE_WL,
        inventory_name="esxi1.example.com",
        inventory_type="ESXi",
        device_uuid="9c2ee5c9-7d47-4c4a-8a3f-3f0a26b7e0aa",
        ip_address="192.0.2.55",
        verify_status=VerifyStatus.SUCCESS,
        backup_copy_data_bytes=1024**3,
    )
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_by_name.return_value = wl

    result = invoke_cli(mock_apm, ["machine", "get", "CORP-PC-001"])

    assert result.exit_code == 0, result.output
    host_line = next(line for line in result.output.splitlines() if "Host:" in line)
    assert "esxi1.example.com (ESXi)" in host_line
    uuid_line = next(line for line in result.output.splitlines() if "Device UUID:" in line)
    assert "9c2ee5c9-7d47-4c4a-8a3f-3f0a26b7e0aa" in uuid_line
    ip_line = next(line for line in result.output.splitlines() if "IP:" in line)
    assert "192.0.2.55" in ip_line
    assert "Verification:" in result.output
    copy_line = next(line for line in result.output.splitlines() if "Copy Size:" in line)
    assert "1.0 GB" in copy_line
