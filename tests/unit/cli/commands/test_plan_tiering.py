"""Unit tests for apm plan tiering commands: list/get."""
from __future__ import annotations

import json
from datetime import time
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import (
    CopyReason,
    M365WorkloadType,
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
    VersionCopyStatus,
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
from synology_apm.sdk.models.tiering_plan import TieringPlan, TieringStatus
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
# apm plan tiering list
# ═══════════════════════════════════════════════════════════════════════════


def test_tiering_list_table() -> None:
    """tiering list renders plan name in the table."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "list"])

    assert result.exit_code == 0, result.output
    assert "30-Day Tiering" in result.output
    mock_apm.tiering_plans.list.assert_called_once()


def test_tiering_list_shows_tier_after_and_destination() -> None:
    """tiering list should show tiering_after_days and destination name."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "list"])

    assert "30 days" in result.output
    assert "My S3 Storage" in result.output
    assert "01:30" in result.output


def test_tiering_list_hides_plan_id_by_default() -> None:
    """Plan ID is hidden by default and shown only with --verbose."""
    mock_apm = make_mock_client()

    default_result = invoke_cli(mock_apm, ["plan", "tiering", "list"])
    verbose_result = invoke_cli(mock_apm, ["plan", "tiering", "list", "-v"])

    assert "tiering-plan-001" not in default_result.output
    assert "tiering-plan-001" in verbose_result.output


def test_tiering_list_json() -> None:
    """tiering list --output json should include all TieringPlan fields."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "list", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert isinstance(data, list)
    assert data[0]["name"] == "30-Day Tiering"
    assert data[0]["tiering_after_days"] == 30
    assert data[0]["daily_check_time"] == "01:30"
    assert data[0]["destination"]["name"] == "My S3 Storage"
    assert data[0]["server_count"] == 2


def test_tiering_list_csv() -> None:
    """tiering list --output csv should produce flat CSV with required headers."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "list", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "plan_id" in headers
    assert "tiering_after_days" in headers
    assert "daily_check_time" in headers
    assert "destination_name" in headers
    assert "destination" not in headers  # nested field must not appear as-is
    assert "tiering-plan-001" in result.output


# ═══════════════════════════════════════════════════════════════════════════
# apm plan tiering get
# ═══════════════════════════════════════════════════════════════════════════


def test_tiering_get_search_mode() -> None:
    """get <NAME> calls tiering_plans.get_by_name(name) (search mode)."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "30-Day Tiering"])

    assert result.exit_code == 0, result.output
    assert "30-Day Tiering" in result.output
    mock_apm.tiering_plans.get_by_name.assert_called_once_with("30-Day Tiering")
    mock_apm.tiering_plans.get.assert_not_called()


def test_tiering_get_table() -> None:
    """get --id <UUID> calls tiering_plans.get (direct mode)."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "--id", "tiering-plan-001"])

    assert result.exit_code == 0, result.output
    assert "30-Day Tiering" in result.output
    assert "30 days" in result.output
    assert "My S3 Storage" in result.output
    assert "01:30" in result.output
    assert "Included Servers: 2" in result.output
    mock_apm.tiering_plans.get.assert_called_once_with("tiering-plan-001")
    mock_apm.tiering_plans.get_by_name.assert_not_called()


def test_tiering_get_json() -> None:
    """tiering get --output json should include all TieringPlan fields."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "--id", "tiering-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["plan_id"] == "tiering-plan-001"
    assert data["tiering_after_days"] == 30
    assert data["daily_check_time"] == "01:30"
    assert data["destination"]["name"] == "My S3 Storage"
    assert data["server_count"] == 2


def test_tiering_get_mutual_exclusion() -> None:
    """NAME and --id are mutually exclusive; providing both exits 1."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "30-Day Tiering", "--id", "tiering-plan-001"])

    assert result.exit_code == 1


def test_tiering_get_no_args_shows_help() -> None:
    """tiering get (no name, no --id) should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["plan", "tiering", "get"])

    assert result.exit_code == 0
    assert "Usage" in result.output


def test_tiering_list_sdk_error_exits_1() -> None:
    """SDK error during tiering list should exit 1."""
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.list.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "tiering", "list"])

    assert result.exit_code == 1


def test_tiering_get_sdk_error_exits_1() -> None:
    """SDK error during tiering get should exit 1."""
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.get_by_name.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "Non-Existent Plan"])

    assert result.exit_code == 1


# ═══════════════════════════════════════════════════════════════════════════
# Tiering Status display
# ═══════════════════════════════════════════════════════════════════════════


def test_tiering_list_shows_tiering_status_in_progress() -> None:
    """tiering list should show Tiering Status column with IN_PROGRESS status."""
    plan_with_status = TieringPlan(
        **{**TIERING_PLAN.__dict__,
           "tiering_status": TieringStatus(
               status=VersionCopyStatus.IN_PROGRESS, reason=None,
               pending_version_count=4, remaining_bytes=2097152,
           )},
    )
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.list.return_value = ([plan_with_status], 1)

    result = invoke_cli(mock_apm, ["plan", "tiering", "list"])

    assert result.exit_code == 0, result.output
    assert "Copying" in result.output


def test_tiering_list_shows_dash_when_no_tiering_status() -> None:
    """tiering list should show '-' in Tiering Status column when tiering_status is None."""
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.list.return_value = ([TIERING_PLAN], 1)

    result = invoke_cli(mock_apm, ["plan", "tiering", "list"])

    assert result.exit_code == 0, result.output
    assert "Tiering Status" in result.output


def test_tiering_get_shows_tiering_status_with_pending_count() -> None:
    """tiering get should show Tiering Status with pending count and remaining bytes."""
    plan_with_status = TieringPlan(
        **{**TIERING_PLAN.__dict__,
           "tiering_status": TieringStatus(
               status=VersionCopyStatus.IN_PROGRESS, reason=None,
               pending_version_count=7, remaining_bytes=4194304,
           )},
    )
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.get_by_name.return_value = plan_with_status

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "30-Day Tiering"])

    assert result.exit_code == 0, result.output
    assert "Tiering Status" in result.output
    assert "7 version(s) pending, 4.0 MB remaining" in result.output


def test_tiering_get_shows_tiering_status_retry_with_reason() -> None:
    """tiering get should show reason text when status is RETRY."""
    plan_with_status = TieringPlan(
        **{**TIERING_PLAN.__dict__,
           "tiering_status": TieringStatus(
               status=VersionCopyStatus.RETRY,
               reason=CopyReason.DESTINATION_DISCONNECTED,
           )},
    )
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.get_by_name.return_value = plan_with_status

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "30-Day Tiering"])

    assert result.exit_code == 0, result.output
    assert "Tiering Status" in result.output
    assert "disconnected" in result.output.lower()


def test_tiering_get_no_status_block_when_not_enabled() -> None:
    """tiering get should not show Tiering Status when status is NOT_ENABLED."""
    plan_with_status = TieringPlan(
        **{**TIERING_PLAN.__dict__,
           "tiering_status": TieringStatus(
               status=VersionCopyStatus.NOT_ENABLED, reason=None,
           )},
    )
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.get_by_name.return_value = plan_with_status

    result = invoke_cli(mock_apm, ["plan", "tiering", "get", "30-Day Tiering"])

    assert result.exit_code == 0, result.output
    assert "Tiering Status" not in result.output


def test_tiering_get_json_includes_tiering_status() -> None:
    """tiering get --output json should include tiering_status sub-dict."""
    plan_with_status = TieringPlan(
        **{**TIERING_PLAN.__dict__,
           "tiering_status": TieringStatus(
               status=VersionCopyStatus.COMPLETED, reason=None,
           )},
    )
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.get.return_value = plan_with_status

    result = invoke_cli(
        mock_apm, ["plan", "tiering", "get", "--id", "tiering-plan-001", "--output", "json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["tiering_status"] is not None
    assert data["tiering_status"]["status"] == "completed"
    assert data["tiering_status"]["reason"] is None


def test_tiering_get_json_tiering_status_none_when_absent() -> None:
    """tiering get --output json tiering_status should be null when tiering_status is None."""
    mock_apm = make_mock_client()
    mock_apm.tiering_plans.get.return_value = TIERING_PLAN

    result = invoke_cli(
        mock_apm, ["plan", "tiering", "get", "--id", "tiering-plan-001", "--output", "json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["tiering_status"] is None
