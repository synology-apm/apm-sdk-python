"""Unit tests for apm plan protection get command's Custom Scopes & Schedules (tasks) section."""
from __future__ import annotations

import dataclasses
import json
from datetime import time, timedelta
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import (
    M365WorkloadType,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    EventTriggerConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
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
