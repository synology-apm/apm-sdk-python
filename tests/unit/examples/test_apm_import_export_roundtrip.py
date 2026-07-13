"""Round-trip tests: serializers → YAML → parsers preserve data for every section."""
from __future__ import annotations

import io
from datetime import time, timedelta
from typing import Any

import apm_import_export as ie
import pytest
import yaml

from synology_apm.sdk import (
    BackupCopyPolicy,
    DbActionOnError,
    EventTriggerConfig,
    FileServerPathSelector,
    FileServerUpdateRequest,
    GenericS3StorageAddRequest,
    GFSRetention,
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
    M365PlanCreateRequest,
    MachineBackupWindow,
    MachineDbConfig,
    MachineOsType,
    MachinePcConfig,
    MachinePsConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineTaskScope,
    MachineVmConfig,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorageType,
    RetentionType,
    RetirementPlan,
    RetirementRetentionPolicy,
    ScheduleFrequency,
    TieringPlan,
    WeekDay,
    WorkloadCategory,
)
from synology_apm.sdk.models.protection_plan import MachinePlanCreateRequest
from synology_apm.sdk.models.retirement_plan import RetirementPlanCreateRequest
from synology_apm.sdk.models.tiering_plan import TieringPlanCreateRequest
from tests.unit.examples._fixtures import (
    make_backup_server,
    make_file_server_config,
    make_location_info,
    make_machine_workload,
    make_protection_plan,
    make_remote_storage,
    make_saas_tenant,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _default_task(
    workload_type: MachineWorkloadType, os_type: MachineOsType
) -> MachineTaskConfig:
    """A minimal valid task entry for one (workload_type, os_type) pair."""
    is_pc_or_ps = workload_type in (MachineWorkloadType.PC, MachineWorkloadType.PS)
    return MachineTaskConfig(
        workload_type=workload_type,
        os_type=os_type,
        scope=MachineTaskScope.ENTIRE_MACHINE if is_pc_or_ps else None,
        custom_volumes=(),
        include_external_drives=False,
        include_boot_partition=True,
        use_main_schedule=True,
        schedule=None,
    )


# Create requests require every mandatory (workload_type, os_type) pair; tests replace
# the PC/Windows entry with a customized one and keep these defaults for the rest.
_OTHER_MANDATORY_TASKS: tuple[MachineTaskConfig, ...] = (
    _default_task(MachineWorkloadType.PC, MachineOsType.MAC),
    _default_task(MachineWorkloadType.PS, MachineOsType.WINDOWS),
    _default_task(MachineWorkloadType.PS, MachineOsType.LINUX),
    _default_task(MachineWorkloadType.VM, MachineOsType.NONE),
    _default_task(MachineWorkloadType.FS, MachineOsType.NONE),
)


def _roundtrip(
    plan: ProtectionPlan,
    bs_ref_keys: dict[str, str],
    rs_ref_keys: dict[str, str],
    ref_key: str,
    backup_servers_by_ref: dict[str, Any],
    remote_storages_by_ref: dict[str, Any],
) -> MachinePlanCreateRequest | M365PlanCreateRequest:
    """Serialize a plan to YAML and parse it back; returns the parsed request."""
    d = ie._ser_protection_plan(plan, bs_ref_keys, rs_ref_keys, ref_key)
    buf = io.StringIO()
    ie._write_commented_section(buf, "protection_plans", [d])
    entry = yaml.safe_load(buf.getvalue())["protection_plans"][0]
    return ie._parse_protection_request(entry, backup_servers_by_ref, remote_storages_by_ref)


# ── Machine plan with GFS retention and backup copy to RemoteStorage ──────────


def test_machine_plan_roundtrip_gfs_retention_and_backup_copy_to_remote_storage() -> None:
    """A fully populated machine plan round-trips with whole-object equality per sub-config."""
    fake_rs = make_remote_storage(name="DSM-Storage", storage_id="rs-id-001")

    rs_location = make_location_info(
        is_remote_storage=True,
        identifier="rs-id-001",
        name="DSM-Storage",
        endpoint="192.0.2.20:8444",
    )
    bcp = BackupCopyPolicy(
        destination=rs_location,
        retention=ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=7),
        schedule=ProtectionSchedule(ScheduleFrequency.AFTER_BACKUP, start_time=None),
    )
    gfs_retention = ProtectionRetentionPolicy(
        retention_type=RetentionType.KEEP_ADVANCED,
        days=30,
        versions=None,
        gfs=GFSRetention(
            daily_versions=7,
            weekly_versions=4,
            monthly_versions=12,
            yearly_versions=3,
        ),
    )
    weekly_schedule = ProtectionSchedule(
        frequency=ScheduleFrequency.WEEKLY,
        start_time=time(3, 0),
        weekdays=(WeekDay.MONDAY, WeekDay.WEDNESDAY, WeekDay.FRIDAY),
    )
    plan = make_protection_plan(
        policy=ProtectionPlanPolicy(retention=gfs_retention, schedule=weekly_schedule),
        backup_copy_policy=bcp,
        vm_config=MachineVmConfig(
            enable_app_aware_bkp=True,
            enable_verification=True,
            verification_video_duration_seconds=60,
            enable_datastore_usage_detection=True,
            datastore_min_free_space_percent=15,
        ),
        pc_config=MachinePcConfig(
            shutdown_after_backup=False,
            wake_for_backup=True,
            prevent_sleep_during_backup=True,
        ),
        ps_config=MachinePsConfig(
            enable_app_aware_bkp=False,
        ),
        db_config=MachineDbConfig(
            action_on_error=DbActionOnError.STOP,
            mssql_log_setting=MssqlLogSetting.TRUNCATE,
            oracle_log_setting=OracleLogSetting.DELETE,
        ),
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={WeekDay.MONDAY: frozenset({9, 10, 11})},
        ),
    )

    rs_ref_keys: dict[str, str] = {fake_rs.name: "ref-rs"}
    remote_storages_by_ref: dict[str, Any] = {"ref-rs": fake_rs}

    req = _roundtrip(plan, {}, rs_ref_keys, "plan-1", {}, remote_storages_by_ref)

    assert isinstance(req, MachinePlanCreateRequest)
    assert req.name == "Daily Backup"
    assert req.retention == gfs_retention
    assert req.schedule == weekly_schedule
    assert req.backup_copy is not None
    assert req.backup_copy.destination is fake_rs
    assert req.backup_copy.retention == bcp.retention
    # Sub-configs are frozen dataclasses — whole-object equality catches every field.
    assert req.vm_config == plan.vm_config
    assert req.pc_config == plan.pc_config
    assert req.ps_config == plan.ps_config
    assert req.db_config == plan.db_config
    assert req.backup_window == plan.backup_window


def test_machine_plan_roundtrip_backup_window_allowed_hours() -> None:
    """allowed_hours round-trips frozenset → sorted YAML list → frozenset, per weekday."""
    plan = make_protection_plan(
        backup_window=MachineBackupWindow(
            enabled=False,
            allowed_hours={
                WeekDay.SUNDAY: frozenset({0, 1, 23}),
                WeekDay.THURSDAY: frozenset({12}),
            },
        ),
    )

    req = _roundtrip(plan, {}, {}, "plan-1", {}, {})

    assert isinstance(req, MachinePlanCreateRequest)
    assert req.backup_window == MachineBackupWindow(
        enabled=False,
        allowed_hours={
            WeekDay.SUNDAY: frozenset({0, 1, 23}),
            WeekDay.THURSDAY: frozenset({12}),
        },
    )


def test_machine_plan_roundtrip_tasks_through_parse_protection_request() -> None:
    """The tasks list round-trips through the real _parse_protection_request tasks branch."""
    pc_task = MachineTaskConfig(
        workload_type=MachineWorkloadType.PC,
        os_type=MachineOsType.WINDOWS,
        scope=MachineTaskScope.CUSTOM_VOLUME,
        custom_volumes=("C:", "D:"),
        include_external_drives=True,
        include_boot_partition=False,
        use_main_schedule=False,
        schedule=MachineTaskSchedule(
            time_schedule=ProtectionSchedule(
                frequency=ScheduleFrequency.DAILY,
                start_time=time(2, 0),
                weekdays=(),
            ),
            event_trigger=EventTriggerConfig(
                on_sign_out=True, on_lock=False, on_startup=True,
                min_interval=timedelta(hours=2),
            ),
        ),
    )
    plan = make_protection_plan(tasks=(pc_task, *_OTHER_MANDATORY_TASKS))

    req = _roundtrip(plan, {}, {}, "plan-1", {}, {})

    assert isinstance(req, MachinePlanCreateRequest)
    assert req.tasks == (pc_task, *_OTHER_MANDATORY_TASKS)


# ── M365 plan round-trip ──────────────────────────────────────────────────────


def test_m365_plan_roundtrip_retention_and_schedule() -> None:
    """M365 plan round-trip preserves retention type, version count, and weekly schedule."""
    retention = ProtectionRetentionPolicy(
        retention_type=RetentionType.KEEP_VERSIONS,
        versions=10,
    )
    schedule = ProtectionSchedule(
        frequency=ScheduleFrequency.WEEKLY,
        start_time=time(4, 30),
        weekdays=(WeekDay.TUESDAY, WeekDay.SATURDAY),
    )
    plan = make_protection_plan(
        plan_id="123e4567-e89b-12d3-a456-426614174002",
        name="M365 Weekly Backup",
        category=WorkloadCategory.M365,
        policy=ProtectionPlanPolicy(retention=retention, schedule=schedule),
    )

    req = _roundtrip(plan, {}, {}, "plan-2", {}, {})

    assert isinstance(req, M365PlanCreateRequest)
    assert req.name == "M365 Weekly Backup"
    assert req.retention == retention
    assert req.schedule == schedule


# ── _parse_retention ∘ _ser_retention == identity for all 4 types ─────────────


@pytest.mark.parametrize(
    "retention",
    [
        ProtectionRetentionPolicy(RetentionType.KEEP_ALL),
        ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=30),
        ProtectionRetentionPolicy(RetentionType.KEEP_VERSIONS, versions=5),
        ProtectionRetentionPolicy(
            RetentionType.KEEP_ADVANCED,
            days=30,
            versions=None,
            gfs=GFSRetention(
                daily_versions=7,
                weekly_versions=4,
                monthly_versions=12,
                yearly_versions=3,
            ),
        ),
    ],
    ids=["keep_all", "keep_days", "keep_versions", "gfs"],
)
def test_ser_parse_retention_identity(retention: ProtectionRetentionPolicy) -> None:
    """_parse_retention(_ser_retention(r)) == r for all retention types."""
    serialized = ie._ser_retention(retention)
    restored = ie._parse_retention(serialized)
    assert restored == retention


# ── Schedule round-trip ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "schedule",
    [
        ProtectionSchedule(
            frequency=ScheduleFrequency.WEEKLY,
            start_time=time(3, 0),
            weekdays=(WeekDay.MONDAY, WeekDay.WEDNESDAY, WeekDay.FRIDAY),
        ),
        ProtectionSchedule(
            frequency=ScheduleFrequency.MANUAL,
            start_time=None,
            weekdays=(),
        ),
    ],
    ids=["weekly", "manual"],
)
def test_schedule_roundtrip(schedule: ProtectionSchedule) -> None:
    """_parse_schedule(_ser_schedule(s)) == s for weekly and manual schedules."""
    serialized = ie._ser_schedule(schedule)
    restored = ie._parse_schedule(serialized)
    assert restored == schedule


# ── YAML schema pin test (hand-crafted dict → parse → assert fields) ──────────


def test_yaml_schema_pin_machine_plan() -> None:
    """A hand-crafted dict matching the documented YAML schema parses correctly.

    This catches symmetric bugs where serializer and parser both use the same wrong key
    name (which a pure round-trip test would not detect).
    """
    hand_crafted: dict[str, Any] = {
        "name_or_id": "Daily Backup",
        "type": "machine",
        "description": "Hand-crafted schema pin",
        "is_immutable": False,
        "retention": {"type": "keep_days", "days": 14},
        "schedule": {"frequency": "daily", "start_time": "02:00", "weekdays": []},
        "run_schedule_by_controller_time": False,
        "backup_copy": None,
        "vm_config": {
            "enable_app_aware_bkp": True,
            "enable_verification": True,
            "verification_video_duration_seconds": 90,
            "enable_datastore_usage_detection": False,
            "datastore_min_free_space_percent": 20,
        },
        "pc_config": {
            "shutdown_after_backup": True,
            "wake_for_backup": False,
            "prevent_sleep_during_backup": True,
        },
        "ps_config": {
            "enable_app_aware_bkp": False,
            "enable_verification": False,
            "verification_video_duration_seconds": 120,
            "shutdown_after_backup": False,
            "wake_for_backup": True,
            "prevent_sleep_during_backup": False,
        },
        "db_config": {
            "action_on_error": "stop",
            "mssql_log_setting": "truncate",
            "oracle_log_setting": "delete",
        },
        "backup_window": {
            "enabled": True,
            "allowed_hours": {"monday": [9, 10, 11], "friday": [22, 23]},
        },
        "tasks": [
            {
                "workload_type": "pc",
                "os_type": "windows",
                "scope": "entire_machine",
                "custom_volumes": [],
                "include_external_drives": True,
                "include_boot_partition": True,
                "use_main_schedule": True,
                "schedule": None,
            },
            *(
                {
                    "workload_type": t.workload_type.value,
                    "os_type": t.os_type.value,
                    "scope": t.scope.value if t.scope is not None else None,
                    "custom_volumes": [],
                    "include_external_drives": False,
                    "include_boot_partition": True,
                    "use_main_schedule": True,
                    "schedule": None,
                }
                for t in _OTHER_MANDATORY_TASKS
            ),
        ],
    }

    req = ie._parse_protection_request(hand_crafted, {}, {})

    assert isinstance(req, MachinePlanCreateRequest)
    assert req.name == "Daily Backup"
    assert req.description == "Hand-crafted schema pin"
    assert req.is_immutable is False
    assert req.retention == ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=14)
    assert req.schedule.frequency == ScheduleFrequency.DAILY
    assert req.schedule.start_time == time(2, 0)
    assert req.backup_copy is None
    assert req.run_schedule_by_controller_time is False
    assert req.vm_config == MachineVmConfig(
        enable_app_aware_bkp=True,
        enable_verification=True,
        verification_video_duration_seconds=90,
        enable_datastore_usage_detection=False,
        datastore_min_free_space_percent=20,
    )
    assert req.pc_config == MachinePcConfig(
        shutdown_after_backup=True,
        wake_for_backup=False,
        prevent_sleep_during_backup=True,
    )
    assert req.ps_config == MachinePsConfig(
        enable_app_aware_bkp=False,
        enable_verification=False,
        verification_video_duration_seconds=120,
        shutdown_after_backup=False,
        wake_for_backup=True,
        prevent_sleep_during_backup=False,
    )
    assert req.db_config == MachineDbConfig(
        action_on_error=DbActionOnError.STOP,
        mssql_log_setting=MssqlLogSetting.TRUNCATE,
        oracle_log_setting=OracleLogSetting.DELETE,
    )
    assert req.backup_window == MachineBackupWindow(
        enabled=True,
        allowed_hours={
            WeekDay.MONDAY: frozenset({9, 10, 11}),
            WeekDay.FRIDAY: frozenset({22, 23}),
        },
    )
    assert req.tasks == (
        MachineTaskConfig(
            workload_type=MachineWorkloadType.PC,
            os_type=MachineOsType.WINDOWS,
            scope=MachineTaskScope.ENTIRE_MACHINE,
            custom_volumes=(),
            include_external_drives=True,
            include_boot_partition=True,
            use_main_schedule=True,
            schedule=None,
        ),
        *_OTHER_MANDATORY_TASKS,
    )


def test_yaml_schema_pin_m365_plan_weekly() -> None:
    """A hand-crafted M365 dict with a weekly schedule round-trips cleanly."""
    hand_crafted: dict[str, Any] = {
        "name_or_id": "M365 Daily Backup",
        "type": "m365",
        "description": "",
        "is_immutable": False,
        "retention": {"type": "keep_versions", "versions": 10},
        "schedule": {
            "frequency": "weekly",
            "start_time": "03:00",
            "weekdays": ["monday", "wednesday", "friday"],
        },
        "run_schedule_by_controller_time": True,
        "backup_copy": None,
    }

    req = ie._parse_protection_request(hand_crafted, {}, {})

    assert isinstance(req, M365PlanCreateRequest)
    assert req.name == "M365 Daily Backup"
    assert req.retention.retention_type == RetentionType.KEEP_VERSIONS
    assert req.retention.versions == 10
    assert req.schedule.frequency == ScheduleFrequency.WEEKLY
    assert req.schedule.start_time == time(3, 0)
    assert WeekDay.MONDAY in req.schedule.weekdays
    assert WeekDay.WEDNESDAY in req.schedule.weekdays
    assert WeekDay.FRIDAY in req.schedule.weekdays
    assert req.run_schedule_by_controller_time is True


# ── Output key-names assertion ────────────────────────────────────────────────


def test_ser_protection_plan_output_has_required_keys_machine() -> None:
    """_ser_protection_plan returns a dict with all expected top-level keys for a machine plan."""
    plan = make_protection_plan(plan_id="123e4567-e89b-12d3-a456-426614174003")

    result = ie._ser_protection_plan(plan, {}, {}, "plan-1")

    for key in (
        "ref_key", "name_or_id", "type", "retention", "schedule",
        "vm_config", "pc_config", "ps_config", "db_config", "backup_window", "tasks",
    ):
        assert key in result, f"Expected key {key!r} not found in serialized plan"

    assert result["ref_key"] == "plan-1"
    assert result["name_or_id"] == "Daily Backup"
    assert result["type"] == "machine"


def test_ser_protection_plan_output_has_required_keys_m365() -> None:
    """_ser_protection_plan returns a dict without machine-only keys for an M365 plan."""
    plan = make_protection_plan(
        plan_id="123e4567-e89b-12d3-a456-426614174004",
        name="M365 Daily Backup",
        category=WorkloadCategory.M365,
    )

    result = ie._ser_protection_plan(plan, {}, {}, "plan-2")

    for key in ("ref_key", "name_or_id", "type", "retention", "schedule"):
        assert key in result, f"Expected key {key!r} not found in serialized M365 plan"

    assert result["type"] == "m365"
    for machine_only_key in (
        "vm_config", "pc_config", "ps_config", "db_config", "backup_window", "tasks",
    ):
        assert machine_only_key not in result


# ── Backup copy with appliance destination round-trip ─────────────────────────


def test_machine_plan_roundtrip_backup_copy_to_appliance() -> None:
    """Backup copy pointing to an appliance (BackupServer) round-trips correctly."""
    fake_bs = make_backup_server(name="apm-server-01")
    bs_location = make_location_info()  # apm-server-01 appliance location
    bcp = BackupCopyPolicy(
        destination=bs_location,
        retention=ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=14),
        schedule=ProtectionSchedule(ScheduleFrequency.AFTER_BACKUP, start_time=None),
    )
    plan = make_protection_plan(
        plan_id="123e4567-e89b-12d3-a456-426614174005",
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=30),
            schedule=ProtectionSchedule(ScheduleFrequency.DAILY, start_time=time(2, 0)),
        ),
        backup_copy_policy=bcp,
    )

    bs_ref_keys: dict[str, str] = {fake_bs.name: "ref-bs"}
    backup_servers_by_ref: dict[str, Any] = {"ref-bs": fake_bs}

    req = _roundtrip(plan, bs_ref_keys, {}, "plan-5", backup_servers_by_ref, {})

    assert isinstance(req, MachinePlanCreateRequest)
    assert req.backup_copy is not None
    assert req.backup_copy.destination is fake_bs
    assert req.backup_copy.retention.days == 14


# ── Tasks: _ser_task / _parse_task_schedule_dict round-trip ──────────────────


def test_task_schedule_time_and_event_trigger_roundtrip() -> None:
    """_ser_task_schedule followed by _parse_task_schedule_dict preserves both components."""
    task_sched = MachineTaskSchedule(
        time_schedule=ProtectionSchedule(
            frequency=ScheduleFrequency.DAILY,
            start_time=time(2, 0),
            weekdays=(),
        ),
        event_trigger=EventTriggerConfig(
            on_sign_out=True, on_lock=False, on_startup=False,
            min_interval=timedelta(hours=2),
        ),
    )
    task = MachineTaskConfig(
        workload_type=MachineWorkloadType.PC,
        os_type=MachineOsType.WINDOWS,
        scope=None,
        custom_volumes=(),
        include_external_drives=False,
        include_boot_partition=True,
        use_main_schedule=False,
        schedule=task_sched,
    )

    serialized = ie._ser_task(task)
    assert serialized["workload_type"] == "pc"
    assert serialized["os_type"] == "windows"
    assert serialized["use_main_schedule"] is False

    restored_sched = ie._parse_task_schedule_dict(serialized["schedule"])

    assert restored_sched == task_sched


def test_task_schedule_event_only_no_time_schedule_roundtrip() -> None:
    """A task with only an event_trigger (no time_schedule) round-trips correctly."""
    task_sched = MachineTaskSchedule(
        time_schedule=None,
        event_trigger=EventTriggerConfig(
            on_sign_out=False, on_lock=True, on_startup=False,
            min_interval=timedelta(minutes=30),
        ),
    )
    task = MachineTaskConfig(
        workload_type=MachineWorkloadType.PC,
        os_type=MachineOsType.MAC,
        scope=None,
        custom_volumes=(),
        include_external_drives=False,
        include_boot_partition=True,
        use_main_schedule=False,
        schedule=task_sched,
    )

    serialized = ie._ser_task(task)
    restored_sched = ie._parse_task_schedule_dict(serialized["schedule"])

    assert restored_sched == task_sched


# ── Retirement plan round-trip ────────────────────────────────────────────────


def test_retirement_plan_roundtrip_with_retention() -> None:
    """Retirement plan with a retention policy round-trips correctly."""
    plan = RetirementPlan(
        plan_id="123e4567-e89b-12d3-a456-426614174007",
        name="Compliance Retention",
        description="Long-term compliance archive",
        retention=RetirementRetentionPolicy(days=365, keep_latest_version=True),
        run_schedule_by_controller_time=False,
    )

    d = ie._ser_retirement_plan(plan)
    req = ie._parse_retirement_request(d)

    assert isinstance(req, RetirementPlanCreateRequest)
    assert req.name == "Compliance Retention"
    assert req.description == "Long-term compliance archive"
    assert req.retention_days == 365
    assert req.keep_latest_version is True
    assert req.run_schedule_by_controller_time is False


def test_retirement_plan_roundtrip_retention_none_uses_defaults() -> None:
    """Retirement plan with retention=None serializes and parses back with None days and True keep_latest."""
    plan = RetirementPlan(
        plan_id="123e4567-e89b-12d3-a456-426614174008",
        name="Compliance Retention",
        retention=None,
    )

    d = ie._ser_retirement_plan(plan)
    req = ie._parse_retirement_request(d)

    assert req.retention_days is None
    assert req.keep_latest_version is True


# ── Tiering plan round-trip ───────────────────────────────────────────────────


def test_tiering_plan_roundtrip_with_destination() -> None:
    """Tiering plan with a remote storage destination round-trips correctly."""
    fake_rs = make_remote_storage(name="tiering-remote", storage_id="123e4567-e89b-12d3-a456-426614174031")
    destination = make_location_info(
        is_remote_storage=True,
        identifier=fake_rs.storage_id,
        name=fake_rs.name,
        endpoint=fake_rs.endpoint,
    )
    plan = TieringPlan(
        plan_id="123e4567-e89b-12d3-a456-426614174009",
        name="My Tiering Plan",
        description="Tiering to cold storage",
        tiering_after_days=30,
        daily_check_time=time(20, 0),
        destination=destination,
        server_count=1,
        run_schedule_by_controller_time=False,
    )

    rs_ref_keys: dict[str, str] = {fake_rs.name: "ref-rs-t"}
    remote_storages_by_ref: dict[str, Any] = {"ref-rs-t": fake_rs}

    d = ie._ser_tiering_plan(plan, rs_ref_keys)
    req = ie._parse_tiering_request(d, remote_storages_by_ref)

    assert isinstance(req, TieringPlanCreateRequest)
    assert req.name == "My Tiering Plan"
    assert req.description == "Tiering to cold storage"
    assert req.tier_after_days == 30
    assert req.daily_check_time == time(20, 0)
    assert req.destination is fake_rs
    assert req.run_schedule_by_controller_time is False


# ── File server round-trip ────────────────────────────────────────────────────


def test_file_server_roundtrip_selectors_and_password() -> None:
    """_ser_file_server → _build_fs_add_request preserves selectors and passes through password."""
    selector = FileServerPathSelector(path="/share", excluded_paths=("/share/tmp",))
    fs_cfg = make_file_server_config(
        host_ip="10.0.0.10",
        host_port=445,
        login_user="svc_backup",
        enable_vss=True,
        connection_timeout_seconds=120,
        selectors=(selector,),
    )
    plan = make_protection_plan(plan_id="123e4567-e89b-12d3-a456-426614174050")
    bs_loc = make_location_info(name="apm-server-01", is_remote_storage=False)
    wl = make_machine_workload(
        workload_type=MachineWorkloadType.FS,
        is_retired=False,
        namespace="ns-apm-server-01",
        fs_config=fs_cfg,
        plan=plan,
        backup_server=bs_loc,
    )
    bs_ref_keys: dict[str, str] = {"apm-server-01": "ref-bs-01"}
    plan_ref_by_id: dict[str, str] = {"123e4567-e89b-12d3-a456-426614174050": "ref-plan-01"}

    raw = ie._ser_file_server(wl, bs_ref_keys, plan_ref_by_id)

    req = ie._build_fs_add_request(
        raw,
        plan_id="123e4567-e89b-12d3-a456-426614174050",
        namespace="ns-apm-server-01",
        password="secret-pw",
        login_user=fs_cfg.login_user,
    )

    assert req.host_ip == "10.0.0.10"
    assert req.host_port == 445
    assert req.login_user == "svc_backup"
    assert req.login_password == "secret-pw"
    assert req.enable_vss is True
    assert req.connection_timeout_seconds == 120
    assert req.selectors == (selector,)


def test_file_server_roundtrip_update_request() -> None:
    """_ser_file_server → _build_fs_update_request builds a full update request; a None
    password keeps the existing stored credential."""
    selector = FileServerPathSelector(path="/share", excluded_paths=("/share/tmp",))
    fs_cfg = make_file_server_config(
        host_ip="10.0.0.10",
        host_port=445,
        login_user="svc_backup",
        enable_vss=True,
        connection_timeout_seconds=120,
        selectors=(selector,),
    )
    wl = make_machine_workload(
        workload_type=MachineWorkloadType.FS,
        is_retired=False,
        namespace="ns-apm-server-01",
        fs_config=fs_cfg,
        backup_server=make_location_info(),
    )

    raw = ie._ser_file_server(wl, {}, {})
    req = ie._build_fs_update_request(raw, password=None, login_user="svc_backup")

    assert req == FileServerUpdateRequest(
        host_ip="10.0.0.10",
        login_user="svc_backup",
        login_password=None,
        host_port=445,
        enable_vss=True,
        connection_timeout_seconds=120,
        selectors=(selector,),
    )


# ── Remote storage round-trip ─────────────────────────────────────────────────


def test_remote_storage_roundtrip_s3_compatible_to_add_request() -> None:
    """_ser_remote_storage → _parse_rs_entries → _build_rs_add_request preserves the
    endpoint, vault name, and trust flag for an S3-compatible storage."""
    rs = make_remote_storage(storage_type=RemoteStorageType.S3_COMPATIBLE)
    d = ie._ser_remote_storage(rs, "storage-1")
    buf = io.StringIO()
    ie._write_commented_section(buf, "remote_storages", [d])
    data = yaml.safe_load(buf.getvalue())
    creds = {
        ("s3_compatible", rs.endpoint, rs.vault_name): {
            "access_key": "AK",
            "secret_key": "SK",
            "relink_encryption_key": "RK",
        }
    }

    entries = ie._parse_rs_entries(data, creds)

    assert len(entries) == 1
    rse = entries[0]
    assert rse.parse_error is None
    assert rse.name_or_id == "tiering-remote"
    assert rse.ref_key == "storage-1"

    actions = ie._select_rs_actions(entries, creds, "skip", {}, {})

    assert actions == {ie._rs_key(rse): "create"}
    assert rse.request == GenericS3StorageAddRequest(
        access_key="AK",
        secret_key="SK",
        vault_name="my-bucket",
        endpoint="https://s3.example.com:443",
        encryption_enabled=False,
        relink_encryption_key="RK",
        trust_self_signed=True,
    )


# ── M365 auto-backup rules round-trip ─────────────────────────────────────────


def test_m365_rules_roundtrip_user_rule_and_collab() -> None:
    """_ser_m365_auto_backup_rules_block → _parse_m365_rule_entries restores the original
    namespace, plan ID, and group lists."""
    plan_uuid = "123e4567-e89b-12d3-a456-426614174001"
    tenant = make_saas_tenant()
    rule = M365AutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        tenant_id=tenant.tenant_id,
        plan_id=plan_uuid,
        exchange_group_ids=("123e4567-e89b-12d3-a456-426614174012",),
        onedrive_group_ids=(),
        chat_group_ids=(),
    )
    disabled = M365CollabServiceSetting(plan_id="", namespace="")
    enabled = M365CollabServiceSetting(plan_id=plan_uuid, namespace="ns-apm-server-01")
    result_obj = M365AutoBackupRuleListResult(
        rules=(rule,),
        group_exchange=disabled,
        mysite=disabled,
        sharepoint=enabled,
        teams=disabled,
    )
    block = ie._ser_m365_auto_backup_rules_block(
        tenant, result_obj,
        {plan_uuid: "plan-1"}, {"ns-apm-server-01": "server-1"}, "tenant-1",
    )
    assert block is not None

    data = {"m365_auto_backup_rules": [block]}
    bs = make_backup_server(namespace="ns-apm-server-01")
    rule_entries, collab_entries = ie._parse_m365_rule_entries(
        data,
        backup_servers_by_ref={"server-1": bs},
        m365_plans_by_name={"M365 Daily Backup": plan_uuid},
        plan_name_by_ref={"plan-1": "M365 Daily Backup"},
        saas_tenants_by_ref={"tenant-1": tenant.tenant_id},
    )

    assert len(rule_entries) == 1
    re_ = rule_entries[0]
    assert re_.parse_error is None
    assert re_.tenant_id == tenant.tenant_id
    assert re_.resolved_namespace == "ns-apm-server-01"
    assert re_.resolved_plan_id == plan_uuid
    assert re_.exchange_groups == ["123e4567-e89b-12d3-a456-426614174012"]
    assert re_.onedrive_groups == []
    assert re_.chat_groups == []

    assert len(collab_entries) == 1
    ce = collab_entries[0]
    assert ce.parse_error is None
    assert ce.sharepoint == M365CollabServiceSetting(
        plan_id=plan_uuid, namespace="ns-apm-server-01"
    )
    assert ce.group_exchange is None
    assert ce.mysite is None
    assert ce.teams is None
