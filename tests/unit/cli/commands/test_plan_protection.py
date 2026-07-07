"""Unit tests for apm plan protection commands: list/get."""
from __future__ import annotations

import dataclasses
import json
from datetime import time, timedelta
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import (
    M365WorkloadType,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
    WeekDay,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    BackupCopyPolicy,
    EventTriggerConfig,
    GFSRetention,
    MachineBackupWindow,
    MachineTaskConfig,
    MachineTaskSchedule,
    PlanBackupCopyStatus,
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

MACHINE_PLAN_NO_SCHEDULE = ProtectionPlan(
    plan_id="machine-plan-002",
    name="Weekly Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=None,
    ),
    workload_count=0,
    description="",
    successful_workload_count=0,
    unsuccessful_workload_count=0,
    is_immutable=False,
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
# apm plan protection list
# ═══════════════════════════════════════════════════════════════════════════


def test_protection_list_all_shows_both_categories() -> None:
    """list (no arguments) calls plans.list(category=None) and should show both plans."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list"])

    assert result.exit_code == 0, result.output
    assert "Daily Backup" in result.output
    assert "Daily Backup (M365)" in result.output
    mock_apm.plans.list.assert_called_once_with(category=None, name_contains=None, limit=25, offset=0)


@pytest.mark.parametrize("cli_category,sdk_category", [
    ("machine", WorkloadCategory.MACHINE),
    ("m365", WorkloadCategory.M365),
])
def test_protection_list_category_filter(
    cli_category: str, sdk_category: WorkloadCategory
) -> None:
    """list --category <category> calls plans.list(category=<sdk_category>)."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", cli_category])

    assert result.exit_code == 0, result.output
    mock_apm.plans.list.assert_called_once_with(
        category=sdk_category, name_contains=None, limit=25, offset=0
    )


def test_protection_list_invalid_category() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "unknown"])

    assert result.exit_code == 1


def test_protection_list_json() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN], 5)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert isinstance(data, list)
    assert data[0]["name"] == "Daily Backup"


def test_protection_list_csv() -> None:
    """plan protection list --output csv should output flat CSV with retention_type, not nested policy dict."""
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN], 5)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "plan_id" in headers
    assert "retention_type" in headers          # flattened field
    assert "retention_versions" in headers      # renamed from retention_count
    assert "policy" not in headers              # nested field name must not appear
    assert "machine-plan-001" in result.output


def test_protection_list_shows_plan_id_in_verbose() -> None:
    """Plan ID is shown only in verbose mode (-v)."""
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN], 5)

    default_result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])
    verbose_result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine", "-v"])

    assert default_result.exit_code == 0, default_result.output
    assert "machine-plan-001" not in default_result.output
    assert verbose_result.exit_code == 0, verbose_result.output
    assert "machine-plan-001" in verbose_result.output


def test_protection_list_retention_by_days() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN_NO_SCHEDULE], 5)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])

    assert result.exit_code == 0, result.output
    assert "30 days" in result.output


def test_protection_list_retention_keep_all() -> None:
    """KEEP_ALL retention should display 'Keep all' in the table."""
    plan = ProtectionPlan(
        plan_id="p-keep-all",
        name="Keep All Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ALL),
            schedule=None,
        ),
        workload_count=0,
    )
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([plan], 1)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])

    assert result.exit_code == 0, result.output
    assert "Keep all" in result.output


def test_protection_list_retention_keep_advanced() -> None:
    """KEEP_ADVANCED retention should display 'GFS' with slot summary in the table."""
    plan = ProtectionPlan(
        plan_id="p-gfs",
        name="GFS Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(
                retention_type=RetentionType.KEEP_ADVANCED,
                gfs=GFSRetention(
                    daily_versions=7,
                    weekly_versions=4,
                    monthly_versions=12,
                    yearly_versions=1,
                ),
            ),
            schedule=None,
        ),
        workload_count=0,
    )
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([plan], 1)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])

    assert result.exit_code == 0, result.output
    assert "Advanced rules" in result.output


def test_protection_get_advanced_rules() -> None:
    """KEEP_ADVANCED retention in get detail view should display individual rules."""
    plan = ProtectionPlan(
        plan_id="p-gfs-get",
        name="GFS Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(
                retention_type=RetentionType.KEEP_ADVANCED,
                days=30,
                versions=5,
                gfs=GFSRetention(
                    daily_versions=7,
                    weekly_versions=4,
                    monthly_versions=12,
                    yearly_versions=1,
                ),
            ),
            schedule=None,
        ),
        workload_count=0,
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "p-gfs-get"])

    assert result.exit_code == 0, result.output
    # Full rule-string contracts live in test_display.py::test_fmt_advanced_rules_lines.
    assert "Keep all versions for 30 days" in result.output
    assert "Number of latest version to keep: 5 versions" in result.output


def test_protection_get_retention_none() -> None:
    """NONE retention type should display '-' in the get detail view."""
    plan = ProtectionPlan(
        plan_id="p-none",
        name="No Retention Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.NONE),
            schedule=None,
        ),
        workload_count=0,
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "p-none"])

    assert result.exit_code == 0, result.output
    assert "Default Schedule: -" in result.output
    assert "days" not in result.output
    assert "versions" not in result.output
    assert "Keep all" not in result.output


# ═══════════════════════════════════════════════════════════════════════════
# apm plan protection get
# ═══════════════════════════════════════════════════════════════════════════


def test_protection_get_search_mode() -> None:
    """get <NAME> calls plans.get_by_name(name) (search mode)."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "Daily Backup"])

    assert result.exit_code == 0, result.output
    assert "Daily Backup" in result.output
    mock_apm.plans.get_by_name.assert_called_once_with("Daily Backup")
    mock_apm.plans.get.assert_not_called()


def test_protection_get_table() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Daily Backup" in result.output
    assert "Backup Policy" in result.output
    assert "Backup Copy Policy" in result.output
    assert "No Backup Copy enabled." in result.output
    mock_apm.plans.get.assert_called_once_with("machine-plan-001")
    mock_apm.plans.get_by_name.assert_not_called()


def test_protection_get_mutual_exclusion() -> None:
    """NAME and --id are mutually exclusive; should exit 1."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "Daily Backup", "--id", "plan-001"])

    assert result.exit_code == 1


def test_protection_get_json() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["plan_id"] == "machine-plan-001"
    assert "policy" in data
    assert "schedule" in data["policy"]
    assert data["policy"]["schedule"]["start_time"] == "02:00"


def test_protection_get_no_schedule() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = MACHINE_PLAN_NO_SCHEDULE

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-002"])

    assert result.exit_code == 0, result.output
    assert "Weekly Backup" in result.output
    assert "No Backup Copy enabled." in result.output


def test_protection_get_shows_copy_policy_section() -> None:
    """When backup_copy_policy is present, the Backup Copy Policy section should be displayed (including Schedule / Retention / Destination)."""
    plan_with_copy = ProtectionPlan(
        plan_id="copy-plan-001",
        name="Copy Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=None),
        ),
        workload_count=1,
        backup_copy_policy=BackupCopyPolicy(
            destination=LocationInfo(
                is_remote_storage=False,
                identifier="ns-001",
                name="My NAS",
                endpoint="192.0.2.1",
                vault=None,
            ),
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=1),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_with_copy

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "copy-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Backup Policy" in result.output
    assert "Backup Copy Policy" in result.output
    assert "After Backup" in result.output
    assert "1 day" in result.output
    assert "My NAS" in result.output
    assert "No Backup Copy enabled." not in result.output



# ── YAML output ───────────────────────────────────────────────────────────


def test_protection_list_yaml_output() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "plan_id" in result.output


def test_protection_get_yaml_output() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "plan-001", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "plan_id" in result.output


# ── error paths: SDK raises APMError → exit 1 ────────────────────────────


def test_protection_list_sdk_error_exits_1() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.list.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "protection", "list"])

    assert result.exit_code == 1


def test_protection_get_sdk_error_exits_1() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.get.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "plan-001"])

    assert result.exit_code == 1


# ── Additional output format / edge case tests ────────────────────────────


def test_protection_list_csv_output() -> None:
    """plan protection list --output csv should produce CSV with plan_id header."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "plan_id" in lines[0]
    assert "machine-plan-001" in result.output


def test_protection_get_no_args_shows_help() -> None:
    """plan protection get (no name, no --id) should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["plan", "protection", "get"])

    assert result.exit_code == 0
    assert "Usage" in result.output


# ── --page-all ───────────────────────────────────────────────────────────────


def test_protection_list_page_all_combines_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan protection list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    second_plan = dataclasses.replace(M365_PLAN, plan_id="machine-plan-002", name="Weekly Backup")
    mock_apm = make_mock_client()
    mock_apm.plans.list.side_effect = [
        ([MACHINE_PLAN], 2),
        ([second_plan], 2),
    ]

    result = invoke_cli(mock_apm, [
        "plan", "protection", "list", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Daily Backup" in result.output
    assert "Weekly Backup" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.plans.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.plans.list.call_args_list[1].kwargs["offset"] == 1


def test_protection_list_shows_copy_status_column() -> None:
    """plan protection list table should include 'Copy Status' column when plan has backup_copy_status."""
    from synology_apm.sdk.enums import VersionCopyStatus

    plan_with_bcs = dataclasses.replace(
        MACHINE_PLAN,
        backup_copy_status=PlanBackupCopyStatus(status=VersionCopyStatus.COMPLETED, reason=None),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([plan_with_bcs], 1)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Copy Status" in result.output


def test_protection_get_shows_copy_status_section() -> None:
    """plan protection get should display Copy Status in the Successful/Unsuccessful section, before Backup Policy."""
    from synology_apm.sdk.enums import CopyReason, VersionCopyStatus

    plan_with_copy = ProtectionPlan(
        plan_id="copy-plan-bcs",
        name="Copy Plan With Status",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_policy=BackupCopyPolicy(
            destination=LocationInfo(
                is_remote_storage=False, identifier="ns-copy-001",
                name="apm-server-02", endpoint="192.0.2.2", vault=None,
            ),
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=1),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        ),
        backup_copy_status=PlanBackupCopyStatus(
            status=VersionCopyStatus.RETRY,
            reason=CopyReason.AUTH_FAILED,
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_with_copy

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "copy-plan-bcs"])

    assert result.exit_code == 0, result.output
    assert "Copy Status" in result.output
    assert "Waiting for retry" in result.output  # RETRY display string
    assert "Authentication error" in result.output  # AUTH_FAILED reason


def test_protection_get_copy_status_waiting_shows_pending_count() -> None:
    """plan protection get with WAITING copy status should display pending version count."""
    from synology_apm.sdk.enums import VersionCopyStatus

    plan_waiting = ProtectionPlan(
        plan_id="copy-plan-wait",
        name="Waiting Copy Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_policy=BackupCopyPolicy(
            destination=LocationInfo(
                is_remote_storage=False, identifier="ns-copy-001",
                name="apm-server-02", endpoint="192.0.2.2", vault=None,
            ),
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        ),
        backup_copy_status=PlanBackupCopyStatus(
            status=VersionCopyStatus.WAITING,
            reason=None,
            pending_version_count=5,
            remaining_bytes=None,
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_waiting

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "copy-plan-wait"])

    assert result.exit_code == 0, result.output
    assert "Waiting" in result.output
    assert "5 version(s) pending" in result.output


def test_protection_get_shows_copy_status_when_backup_copy_policy_is_none() -> None:
    """plan protection get should show backup_copy_status even when backup_copy_policy is None."""
    from synology_apm.sdk.enums import CopyReason, VersionCopyStatus

    plan_no_copy = ProtectionPlan(
        plan_id="plan-no-copy-policy",
        name="Daily Backup",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_status=PlanBackupCopyStatus(
            status=VersionCopyStatus.FAILED,
            reason=CopyReason.INFRASTRUCTURE_ERROR,
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_no_copy

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "plan-no-copy-policy"])

    assert result.exit_code == 0, result.output
    assert "No Backup Copy enabled." in result.output
    assert "Copy Status" in result.output
    assert "Unable to perform" in result.output  # FAILED display string
    assert "Issue detected" in result.output      # INFRASTRUCTURE_ERROR reason
    assert "Destination:" not in result.output    # no copy destination line


def test_protection_get_in_progress_shows_pending_count() -> None:
    """plan protection get with IN_PROGRESS copy status should display pending count and remaining bytes."""
    from synology_apm.sdk.enums import VersionCopyStatus

    plan_in_progress = ProtectionPlan(
        plan_id="copy-plan-ip",
        name="In Progress Copy Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_policy=BackupCopyPolicy(
            destination=LocationInfo(
                is_remote_storage=False, identifier="ns-copy-001",
                name="apm-server-02", endpoint="192.0.2.2", vault=None,
            ),
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        ),
        backup_copy_status=PlanBackupCopyStatus(
            status=VersionCopyStatus.IN_PROGRESS,
            reason=None,
            pending_version_count=7,
            remaining_bytes=2097152,
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_in_progress

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "copy-plan-ip"])

    assert result.exit_code == 0, result.output
    assert "Copying" in result.output          # IN_PROGRESS display string
    assert "7 version(s) pending, 2.0 MB remaining" in result.output


def test_protection_get_in_progress_omits_remaining_when_none() -> None:
    """plan protection get with IN_PROGRESS and remaining_bytes=None should omit the bytes suffix."""
    from synology_apm.sdk.enums import VersionCopyStatus

    plan_in_progress_no_bytes = ProtectionPlan(
        plan_id="copy-plan-ip2",
        name="In Progress No Bytes",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_policy=BackupCopyPolicy(
            destination=LocationInfo(
                is_remote_storage=False, identifier="ns-copy-001",
                name="apm-server-02", endpoint="192.0.2.2", vault=None,
            ),
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        ),
        backup_copy_status=PlanBackupCopyStatus(
            status=VersionCopyStatus.IN_PROGRESS,
            reason=None,
            pending_version_count=3,
            remaining_bytes=None,
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_in_progress_no_bytes

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "copy-plan-ip2"])

    assert result.exit_code == 0, result.output
    assert "3 version(s) pending" in result.output
    assert "remaining" not in result.output    # no bytes suffix when remaining_bytes is None


def test_protection_get_shows_copy_reason_when_present() -> None:
    """plan protection get should display the copy reason detail string when reason is non-None."""
    from synology_apm.sdk.enums import CopyReason, VersionCopyStatus

    plan_with_reason = ProtectionPlan(
        plan_id="plan-reason-001",
        name="Plan With Reason",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_policy=BackupCopyPolicy(
            destination=LocationInfo(
                is_remote_storage=False,
                identifier="ns-copy-002",
                name="apm-server-02",
                endpoint="192.0.2.2",
                vault=None,
            ),
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        ),
        backup_copy_status=PlanBackupCopyStatus(
            status=VersionCopyStatus.FAILED,
            reason=CopyReason.STORAGE_FULL,
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_with_reason

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "plan-reason-001"])

    assert result.exit_code == 0, result.output
    assert "Copy Status" in result.output
    assert "Unable to perform" in result.output  # FAILED display string
    assert "Storage is full" in result.output    # STORAGE_FULL reason from fmt_copy_reason


# ═══════════════════════════════════════════════════════════════════════════
# apm plan protection get — Custom Scopes & Schedules section
# ═══════════════════════════════════════════════════════════════════════════


def _make_default_tasks() -> tuple[MachineTaskConfig, ...]:
    return (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC, os_type=MachineOsType.WINDOWS,
                          scope=MachineTaskScope.ENTIRE_MACHINE, use_main_schedule=True),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC, os_type=MachineOsType.MAC,
                          scope=MachineTaskScope.ENTIRE_MACHINE, use_main_schedule=True),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS, os_type=MachineOsType.WINDOWS,
                          scope=MachineTaskScope.ENTIRE_MACHINE, use_main_schedule=True),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS, os_type=MachineOsType.LINUX,
                          scope=MachineTaskScope.ENTIRE_MACHINE, use_main_schedule=True),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS, os_type=MachineOsType.NONE,
                          use_main_schedule=True),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM, os_type=MachineOsType.NONE,
                          use_main_schedule=True),
    )


def test_protection_get_tasks_section_shown_for_machine_plan() -> None:
    """Custom Scopes & Schedules section is shown in the detail view when tasks are present."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=_make_default_tasks())
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Custom Scopes & Schedules" in result.output
    assert "PC" in result.output
    assert "Windows" in result.output
    assert "Mac" in result.output
    assert "Physical Server" in result.output
    assert "Linux" in result.output
    assert "File Server" in result.output
    assert "Virtual Machine" in result.output


def test_protection_get_tasks_use_main_schedule_label() -> None:
    """use_main_schedule=True renders as 'Follow the default schedule'."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(workload_type=MachineWorkloadType.VM, os_type=MachineOsType.NONE,
                          use_main_schedule=True),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Follow the default schedule" in result.output


def test_protection_get_tasks_custom_schedule() -> None:
    """use_main_schedule=False with a time schedule renders the schedule string."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PS,
            os_type=MachineOsType.WINDOWS,
            scope=MachineTaskScope.ENTIRE_MACHINE,
            use_main_schedule=False,
            schedule=MachineTaskSchedule(
                time_schedule=ProtectionSchedule(
                    frequency=ScheduleFrequency.DAILY, start_time=time(14, 30)
                ),
                event_trigger=None,
            ),
        ),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Daily, 14:30" in result.output
    assert "Follow the default schedule" not in result.output


def test_protection_get_tasks_event_trigger_shows_enabled_events() -> None:
    """Event trigger with on_sign_out and on_lock shows only those two events."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PC,
            os_type=MachineOsType.WINDOWS,
            scope=MachineTaskScope.ENTIRE_MACHINE,
            use_main_schedule=False,
            schedule=MachineTaskSchedule(
                time_schedule=ProtectionSchedule(
                    frequency=ScheduleFrequency.DAILY, start_time=time(2, 0)
                ),
                event_trigger=EventTriggerConfig(
                    on_sign_out=True, on_lock=True, on_startup=False,
                    min_interval=timedelta(hours=1),
                ),
            ),
        ),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Sign-out" in result.output
    assert "Screen lock" in result.output
    assert "Startup" not in result.output
    assert "1h" in result.output


def test_protection_get_tasks_event_only_schedule() -> None:
    """time_schedule=None + event_trigger renders Events line directly on Schedule."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PC,
            os_type=MachineOsType.MAC,
            scope=MachineTaskScope.ENTIRE_MACHINE,
            use_main_schedule=False,
            schedule=MachineTaskSchedule(
                time_schedule=None,
                event_trigger=EventTriggerConfig(
                    on_sign_out=True, on_lock=False, on_startup=True,
                    min_interval=timedelta(minutes=30),
                ),
            ),
        ),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Events:" in result.output
    assert "Sign-out" in result.output
    assert "Startup" in result.output
    assert "Screen lock" not in result.output
    assert "30 min." in result.output


def test_protection_get_tasks_include_external_drives() -> None:
    """include_external_drives=True is shown; False is not shown."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PC,
            os_type=MachineOsType.WINDOWS,
            scope=MachineTaskScope.ENTIRE_MACHINE,
            include_external_drives=True,
            use_main_schedule=True,
        ),
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PC,
            os_type=MachineOsType.MAC,
            scope=MachineTaskScope.ENTIRE_MACHINE,
            include_external_drives=False,
            use_main_schedule=True,
        ),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"],
                        env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "Include external drives" in result.output


def test_protection_get_tasks_custom_volume_scope() -> None:
    """Custom Volume scope shows volume list and Include boot partition when set."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PS,
            os_type=MachineOsType.WINDOWS,
            scope=MachineTaskScope.CUSTOM_VOLUME,
            custom_volumes=("C:", "D:"),
            include_boot_partition=True,
            use_main_schedule=True,
        ),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"],
                        env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "Custom Volume" in result.output
    assert "C:" in result.output
    assert "D:" in result.output
    assert "Include boot partition" in result.output


def test_protection_get_tasks_not_shown_when_none() -> None:
    """Custom Scopes & Schedules section is not shown when plan.tasks is None (e.g. list-level data)."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=None)
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Custom Scopes & Schedules" not in result.output


def test_protection_get_json_includes_tasks() -> None:
    """JSON output includes a 'tasks' array when tasks are present."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(
            workload_type=MachineWorkloadType.VM,
            os_type=MachineOsType.NONE,
            use_main_schedule=True,
        ),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert "tasks" in data
    assert isinstance(data["tasks"], list)
    assert len(data["tasks"]) == 1
    task = data["tasks"][0]
    assert task["workload_type"] == "vm"
    assert task["os_type"] == "none"
    assert task["use_main_schedule"] is True
    assert task["schedule"] is None


def test_protection_get_json_tasks_none_when_absent() -> None:
    """JSON output has tasks=null when plan.tasks is None."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=None)
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["tasks"] is None


def test_protection_get_json_tasks_with_custom_schedule() -> None:
    """JSON output serializes per-task schedule correctly."""
    plan = dataclasses.replace(MACHINE_PLAN, tasks=(
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PC,
            os_type=MachineOsType.WINDOWS,
            scope=MachineTaskScope.SYSTEM_VOLUME,
            use_main_schedule=False,
            schedule=MachineTaskSchedule(
                time_schedule=ProtectionSchedule(
                    frequency=ScheduleFrequency.DAILY, start_time=time(3, 0)
                ),
                event_trigger=EventTriggerConfig(
                    on_sign_out=True, on_lock=False, on_startup=False,
                    min_interval=timedelta(hours=2),
                ),
            ),
        ),
    ))
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    task = data["tasks"][0]
    assert task["scope"] == "system_volume"
    assert task["use_main_schedule"] is False
    sched = task["schedule"]
    assert sched["time_schedule"]["frequency"] == "daily"
    assert sched["time_schedule"]["start_time"] == "03:00"
    assert sched["event_trigger"]["on_sign_out"] is True
    assert sched["event_trigger"]["on_lock"] is False
    assert sched["event_trigger"]["min_interval_seconds"] == 7200


# ═══════════════════════════════════════════════════════════════════════════
# apm plan protection get — Backup Window section
# ═══════════════════════════════════════════════════════════════════════════


def test_protection_get_backup_window_disabled_shows_no_restriction() -> None:
    plan = dataclasses.replace(
        MACHINE_PLAN,
        backup_window=MachineBackupWindow(enabled=False),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Backup Window" in result.output
    assert "No restriction" in result.output


def test_protection_get_backup_window_all_hours_shows_unrestricted() -> None:
    plan = dataclasses.replace(
        MACHINE_PLAN,
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={d: frozenset(range(24)) for d in WeekDay},
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "unrestricted" in result.output
    assert "00:00" not in result.output


def test_protection_get_backup_window_hour_ranges() -> None:
    """Contiguous hour set renders as a compact range; non-contiguous renders multiple ranges."""
    plan = dataclasses.replace(
        MACHINE_PLAN,
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={
                WeekDay.MONDAY: frozenset(range(0, 8)) | frozenset(range(20, 24)),
                WeekDay.TUESDAY: frozenset(range(0, 6)),
            },
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "00:00–08:00" in result.output
    assert "20:00–24:00" in result.output
    assert "00:00–06:00" in result.output


def test_protection_get_backup_window_blocked_day() -> None:
    """A day absent from allowed_hours renders as 'blocked'."""
    plan = dataclasses.replace(
        MACHINE_PLAN,
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={
                d: frozenset(range(24))
                for d in WeekDay if d != WeekDay.WEDNESDAY
            },
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Wed." in result.output
    assert "blocked" in result.output


def test_protection_get_backup_window_not_shown_when_none() -> None:
    """Backup Window section is absent when plan.backup_window is None."""
    plan = dataclasses.replace(MACHINE_PLAN, backup_window=None)
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    assert "Backup Window" not in result.output


def test_protection_get_backup_window_day_order() -> None:
    """Days are shown Mon–Sun regardless of insertion order in allowed_hours."""
    plan = dataclasses.replace(
        MACHINE_PLAN,
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={d: frozenset(range(24)) for d in WeekDay},
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001"])

    assert result.exit_code == 0, result.output
    mon_pos = result.output.index("Mon.")
    sun_pos = result.output.index("Sun.")
    assert mon_pos < sun_pos


def test_protection_get_json_includes_backup_window() -> None:
    """JSON output includes backup_window with enabled flag and allowed_hours."""
    plan = dataclasses.replace(
        MACHINE_PLAN,
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={WeekDay.MONDAY: frozenset(range(0, 8))},
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert "backup_window" in data
    bw = data["backup_window"]
    assert bw["enabled"] is True
    assert "monday" in bw["allowed_hours"]
    assert bw["allowed_hours"]["monday"] == list(range(0, 8))


def test_protection_get_json_backup_window_null_when_absent() -> None:
    """JSON output has backup_window=null when plan.backup_window is None."""
    plan = dataclasses.replace(MACHINE_PLAN, backup_window=None)
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "machine-plan-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["backup_window"] is None


def test_protection_get_skipped_copy_status_shows_skipped_count() -> None:
    """plan protection get with SKIPPED copy status shows the skipped workload count."""
    from synology_apm.sdk.enums import VersionCopyStatus

    plan_skipped = ProtectionPlan(
        plan_id="copy-plan-skip",
        name="Skipped Copy Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_status=PlanBackupCopyStatus(
            status=VersionCopyStatus.SKIPPED,
            reason=None,
            pending_version_count=0,
            remaining_bytes=None,
            skipped_workload_count=4,
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan_skipped

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "copy-plan-skip"])

    assert result.exit_code == 0, result.output
    assert "4 workload(s) skipped." in result.output


def test_protection_get_task_event_interval_mixed_hours_and_minutes() -> None:
    """A 90-minute event-trigger interval renders as '1h 30 min.'."""
    from datetime import timedelta

    from synology_apm.sdk.enums import MachineOsType, MachineWorkloadType
    from synology_apm.sdk.models.protection_plan import (
        EventTriggerConfig,
        MachineTaskConfig,
        MachineTaskSchedule,
    )

    plan = ProtectionPlan(
        plan_id="p-interval",
        name="Interval Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=0,
        tasks=(
            MachineTaskConfig(
                MachineWorkloadType.PC, MachineOsType.WINDOWS,
                use_main_schedule=False,
                schedule=MachineTaskSchedule(
                    time_schedule=None,
                    event_trigger=EventTriggerConfig(on_sign_out=True, min_interval=timedelta(minutes=90)),
                ),
            ),
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.get.return_value = plan

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "p-interval"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "1h 30 min." in result.output
