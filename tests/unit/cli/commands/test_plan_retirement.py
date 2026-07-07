"""Unit tests for apm plan retirement commands: list/get."""
from __future__ import annotations

import json
from datetime import time
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import (
    M365WorkloadType,
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
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
from synology_apm.sdk.models.tiering_plan import TieringPlan
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload, MachineWorkload
from tests.unit.cli.conftest import invoke_cli

# ── Fixtures ──────────────────────────────────────────────────────────────


MACHINE_PLAN = ProtectionPlan(
    plan_id="machine-plan-001",
    name="Daily Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=10),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(2, 0)),
    ),
    workload_count=3,
    description="Default Machine Plan",
    successful_workload_count=2,
    unsuccessful_workload_count=1,
    is_immutable=False,
)

M365_PLAN = ProtectionPlan(
    plan_id="m365-plan-001",
    name="Daily Backup (M365)",
    category=WorkloadCategory.M365,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=None,
    ),
    workload_count=5,
    description="",
    successful_workload_count=4,
    unsuccessful_workload_count=1,
    is_immutable=True,
)

RETIREMENT_PLAN = RetirementPlan(
    plan_id="retire-plan-001",
    name="30-Day Retention",
    description="Default Retirement Plan",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=False),
    workload_count=1,
)

TIERING_PLAN = TieringPlan(
    plan_id="tiering-plan-001",
    name="30-Day Tiering",
    description="Move old versions to S3",
    tiering_after_days=30,
    daily_check_time=time(1, 30),
    destination=LocationInfo(
        is_remote_storage=True,
        identifier="dest-ns-001",
        name="My S3 Storage",
        endpoint="s3.amazonaws.com",
        vault=None,
    ),
    server_count=2,
)

SAMPLE_WL = MachineWorkload(
    workload_id="wl-id-001",
    name="CORP-PC-001",
    category=WorkloadCategory.MACHINE,
    namespace="ns-001",
    last_backup_at=None,
    is_retired=False,
    protected_data_bytes=0,
    status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC,
    agent_version="1.2.0",
)

SAMPLE_M365_WL = M365Workload(
    workload_id="wl-m365-001",
    name="Alice",
    category=WorkloadCategory.M365,
    namespace="ns-m365-001",
    last_backup_at=None,
    is_retired=False,
    protected_data_bytes=0,
    status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-m365-001", name="Daily Backup (M365)", category=WorkloadCategory.M365),
    tenant_id="tenant-001",
    workload_type=M365WorkloadType.EXCHANGE,
    info=M365UserInfo(user_principal_name="alice@contoso.com"),
)

def make_mock_client() -> AsyncMock:
    mock = AsyncMock()
    mock.plans.list.return_value = ([MACHINE_PLAN, M365_PLAN], 5)
    mock.plans.get.return_value = MACHINE_PLAN
    mock.plans.get_by_name.return_value = MACHINE_PLAN
    mock.machine.workloads.get.return_value = SAMPLE_WL
    mock.machine.workloads.get_by_name.return_value = SAMPLE_WL
    mock.machine.workloads.retire.return_value = None
    mock.m365.workloads.get.return_value = SAMPLE_M365_WL
    mock.m365.workloads.get_by_name.return_value = SAMPLE_M365_WL
    mock.m365.workloads.retire.return_value = None
    mock.retirement_plans.list.return_value = ([RETIREMENT_PLAN], 5)
    mock.retirement_plans.get.return_value = RETIREMENT_PLAN
    mock.retirement_plans.get_by_name.return_value = RETIREMENT_PLAN
    mock.tiering_plans.list.return_value = ([TIERING_PLAN], 1)
    mock.tiering_plans.get.return_value = TIERING_PLAN
    mock.tiering_plans.get_by_name.return_value = TIERING_PLAN
    return mock


def _plan_error() -> ResourceNotFoundError:
    return ResourceNotFoundError("not found", resource_type="Plan", resource_id="x")


# ═══════════════════════════════════════════════════════════════════════════
# apm plan retirement list
# ═══════════════════════════════════════════════════════════════════════════


def test_retirement_list_table() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "list"])

    assert result.exit_code == 0, result.output
    assert "30-Day Retention" in result.output
    mock_apm.retirement_plans.list.assert_called_once()


def test_retirement_list_json() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "list", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert isinstance(data, list)
    assert data[0]["name"] == "30-Day Retention"
    assert data[0]["retention"]["days"] == 30
    assert data[0]["retention"]["keep_latest_version"] is False


def test_retirement_list_csv() -> None:
    """plan retirement list --output csv should output flat CSV with retention_days/keep_latest, not nested dict."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "list", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "plan_id" in headers
    assert "retention_days" in headers
    assert "retention_keep_latest" in headers
    assert "retention_type" not in headers      # removed field
    assert "retention_versions" not in headers  # removed field
    assert "retention" not in headers           # nested field must not appear
    assert "retire-plan-001" in result.output


def test_retirement_list_shows_plan_id_in_verbose() -> None:
    """Plan ID is hidden by default and shown only with --verbose."""
    mock_apm = make_mock_client()

    default_result = invoke_cli(mock_apm, ["plan", "retirement", "list"])
    verbose_result = invoke_cli(mock_apm, ["plan", "retirement", "list", "-v"])

    assert default_result.exit_code == 0, default_result.output
    assert "retire-plan-001" not in default_result.output
    assert verbose_result.exit_code == 0, verbose_result.output
    assert "retire-plan-001" in verbose_result.output


# ═══════════════════════════════════════════════════════════════════════════
# apm plan retirement get
# ═══════════════════════════════════════════════════════════════════════════


def test_retirement_get_search_mode() -> None:
    """get <NAME> calls retirement_plans.get_by_name(name) (search mode)."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "get", "30-Day Retention"])

    assert result.exit_code == 0, result.output
    assert "30-Day Retention" in result.output
    mock_apm.retirement_plans.get_by_name.assert_called_once_with("30-Day Retention")
    mock_apm.retirement_plans.get.assert_not_called()


def test_retirement_get_table() -> None:
    """get --id <UUID> calls retirement_plans.get (direct mode)."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "get", "--id", "retire-plan-001"])

    assert result.exit_code == 0, result.output
    assert "30-Day Retention" in result.output
    mock_apm.retirement_plans.get.assert_called_once_with("retire-plan-001")
    mock_apm.retirement_plans.get_by_name.assert_not_called()


def test_retirement_get_json() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "get", "--id", "retire-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["plan_id"] == "retire-plan-001"
    assert data["retention"]["days"] == 30


def test_retirement_get_mutual_exclusion() -> None:
    """NAME and --id are mutually exclusive; should exit 1."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "get", "Compliance Retention", "--id", "retire-plan-001"])

    assert result.exit_code == 1


# ── YAML output ───────────────────────────────────────────────────────────


def test_retirement_list_yaml_output() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "list", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "plan_id" in result.output


def test_retirement_get_yaml_output() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "get", "--id", "cc39711f-0000-0000-0000-000000000000", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "plan_id" in result.output


# ── error paths: SDK raises APMError → exit 1 ────────────────────────────


def test_retirement_list_sdk_error_exits_1() -> None:
    mock_apm = make_mock_client()
    mock_apm.retirement_plans.list.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "retirement", "list"])

    assert result.exit_code == 1


def test_retirement_get_sdk_error_exits_1() -> None:
    mock_apm = make_mock_client()
    mock_apm.retirement_plans.get_by_name.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "retirement", "get", "Non-Existent Plan"])

    assert result.exit_code == 1


# ── Additional output format / edge case tests ────────────────────────────


def test_retirement_list_csv_output() -> None:
    """plan retirement list --output csv should produce CSV with plan_id header."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "retirement", "list", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "plan_id" in lines[0]
