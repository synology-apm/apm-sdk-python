"""Unit tests for apm plan protection get command (table/JSON/YAML output, copy policy, copy status)."""
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
    BackupCopyPolicy,
    GFSRetention,
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
# apm plan protection get
# ═══════════════════════════════════════════════════════════════════════════


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


def test_protection_get_yaml_output() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "plan-001", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "plan_id" in result.output


# ── error paths: SDK raises APMError → exit 1 ────────────────────────────


def test_protection_get_sdk_error_exits_1() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.get.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "protection", "get", "--id", "plan-001"])

    assert result.exit_code == 1


# ── Additional output format / edge case tests ────────────────────────────


def test_protection_get_no_args_shows_help() -> None:
    """plan protection get (no name, no --id) should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["plan", "protection", "get"])

    assert result.exit_code == 0
    assert "Usage" in result.output


# ── Copy status section ────────────────────────────────────────────────────


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
