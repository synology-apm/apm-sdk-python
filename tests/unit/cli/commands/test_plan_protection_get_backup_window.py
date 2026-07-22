"""Unit tests for apm plan protection get command's Backup Window section."""
from __future__ import annotations

import dataclasses
import json
from datetime import time
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import (
    M365WorkloadType,
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
    MachineBackupWindow,
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
