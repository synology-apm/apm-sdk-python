"""Response parsers and API string maps for Protection Plan collections.

Private module backing collections/protection_plans.py; converts raw plan API
response objects into SDK models. The API↔enum string maps defined here are also
the source of truth for the request-body builders in _protection_plan_builders.py.
"""
from __future__ import annotations

from datetime import time, timedelta
from typing import Any

from ..enums import (
    DbActionOnError,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    RetentionType,
    ScheduleFrequency,
    VersionCopyStatus,
    WeekDay,
    WorkloadCategory,
)
from ..models.location import LocationInfo
from ..models.protection_plan import (
    BackupCopyPolicy,
    EventTriggerConfig,
    GFSRetention,
    MachineBackupWindow,
    MachineDbConfig,
    MachinePcConfig,
    MachinePsConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineVmConfig,
    PlanBackupCopyStatus,
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from ._shared import (
    _MACHINE_WORKLOAD_TYPE_MAP,
    _parse_bytes_field,
    _parse_copy_status_core,
    _parse_count_field,
)

# ── API string maps ───────────────────────────────────────────────────────


_OS_TYPE_MAP: dict[str, MachineOsType] = {
    "WINDOWS": MachineOsType.WINDOWS,
    "MAC":     MachineOsType.MAC,
    "LINUX":   MachineOsType.LINUX,
    "NONE":    MachineOsType.NONE,
}

_SOURCE_TYPE_MAP: dict[str, MachineTaskScope] = {
    "BACKUP_SOURCE_BAREMETAL": MachineTaskScope.ENTIRE_MACHINE,
    "BACKUP_SOURCE_SYSVOL":    MachineTaskScope.SYSTEM_VOLUME,
    "BACKUP_SOURCE_CUSVOL":    MachineTaskScope.CUSTOM_VOLUME,
}

_DB_ACTION_MAP: dict[str, DbActionOnError] = {
    "IGNORE_FAILURES": DbActionOnError.CONTINUE,
    "REQUIRE_SUCCESS": DbActionOnError.STOP,
}

_MSSQL_LOG_MAP: dict[str, MssqlLogSetting] = {
    "DELETE_LOGS_BY_DB_RULE": MssqlLogSetting.DO_NOT_TRUNCATE,
    "TRUNCATE_LOGS":          MssqlLogSetting.TRUNCATE,
}

_ORACLE_LOG_MAP: dict[str, OracleLogSetting] = {
    "NOT_DELETE_LOGS": OracleLogSetting.DO_NOT_DELETE,
    "DELETE_LOGS":     OracleLogSetting.DELETE,
}

_PERIOD_BASE_SECS: dict[str, int] = {"MIN": 60, "HOUR": 3600, "DAY": 86400}


# ── Parsers ───────────────────────────────────────────────────────────────


def _parse_retention(raw: dict[str, Any]) -> ProtectionRetentionPolicy:
    """Convert an API retention object to a ProtectionRetentionPolicy.

    Type mapping (mirrors JS parseRetentionConfig):
      keepAll=True                             → KEEP_ALL
      (keepDays>0 AND keepVersions>0) or GFS   → KEEP_ADVANCED
      keepVersions>0 only                      → KEEP_VERSIONS
      keepDays>0 only                          → KEEP_DAYS
      all zero / absent                        → NONE
    """
    if raw.get("keepAll"):
        return ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ALL)

    keep_days: int = raw.get("keepDays") or 0
    keep_versions: int = raw.get("keepVersions") or 0
    gfs_days: int = raw.get("gfsDays") or 0
    gfs_weeks: int = raw.get("gfsWeeks") or 0
    gfs_months: int = raw.get("gfsMonths") or 0
    gfs_years: int = raw.get("gfsYears") or 0
    has_gfs = gfs_days > 0 or gfs_weeks > 0 or gfs_months > 0 or gfs_years > 0

    if (keep_days > 0 and keep_versions > 0) or has_gfs:
        return ProtectionRetentionPolicy(
            retention_type=RetentionType.KEEP_ADVANCED,
            days=keep_days or None,
            versions=keep_versions or None,
            gfs=GFSRetention(
                daily_versions=gfs_days,
                weekly_versions=gfs_weeks,
                monthly_versions=gfs_months,
                yearly_versions=gfs_years,
            ),
        )
    if keep_versions > 0:
        return ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=keep_versions)
    if keep_days > 0:
        return ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=keep_days)
    return ProtectionRetentionPolicy(retention_type=RetentionType.NONE)


def _parse_schedule(raw: dict[str, Any]) -> ProtectionSchedule:
    """Convert an API schedule object to a ProtectionSchedule."""
    schedule_type = raw.get("scheduleType")
    if schedule_type == "NONE":
        return ProtectionSchedule(frequency=ScheduleFrequency.MANUAL, start_time=None)
    if schedule_type == "EVENT":
        return ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None)

    repeat_type = raw.get("repeatType")
    repeat_hour: int = raw.get("repeatHour") or 0
    run_hour: int = raw.get("runHour") or 0
    run_min: int = raw.get("runMin") or 0

    if repeat_type == "WEEKLY":
        frequency = ScheduleFrequency.WEEKLY
        weekdays = tuple(
            WeekDay(d) for d in (raw.get("runWeekday") or [])
            if d in [w.value for w in WeekDay]
        )
        start_time: time | None = time(run_hour, run_min)
    elif repeat_type == "DAILY" and repeat_hour == 1:
        frequency = ScheduleFrequency.HOURLY
        weekdays = ()
        start_time = time(0, run_min)
    else:
        frequency = ScheduleFrequency.DAILY
        weekdays = ()
        start_time = time(run_hour, run_min)

    return ProtectionSchedule(frequency=frequency, start_time=start_time, weekdays=weekdays)


def _parse_backup_copy_status(bcs: dict[str, Any] | None) -> PlanBackupCopyStatus | None:
    """Parse a backupCopyStatus dict from the plan API response into PlanBackupCopyStatus."""
    if bcs is None:
        return None
    raw_status = bcs.get("copyStatus") or ""
    pending = _parse_count_field(bcs.get("pendingVersionCount"))
    remaining = _parse_bytes_field(bcs.get("remainingBytes"))
    resolved = _parse_copy_status_core(raw_status, pending, remaining, bcs.get("statusReason"))
    if resolved is None:
        return None
    status, reason, pending, remaining = resolved
    skipped = 0
    if status == VersionCopyStatus.SKIPPED:
        skipped = _parse_count_field(bcs.get("skippedWorkloadCount"))
    return PlanBackupCopyStatus(status=status, reason=reason, pending_version_count=pending,
                                remaining_bytes=remaining, skipped_workload_count=skipped)


def _parse_vm_config(raw: dict[str, Any]) -> MachineVmConfig:
    return MachineVmConfig(
        enable_app_aware_bkp=bool(raw.get("enableAppAwareBkp", True)),
        enable_verification=bool(raw.get("enableVerification") or False),
        verification_video_duration_seconds=int(raw.get("verificationPolicy") or 120),
        enable_datastore_usage_detection=bool(raw.get("enableDatastoreAware") or False),
        datastore_min_free_space_percent=int(raw.get("datastoreReservedPercentage") or 10),
    )


def _parse_pc_config(raw: dict[str, Any]) -> MachinePcConfig:
    return MachinePcConfig(
        shutdown_after_backup=bool(raw.get("shutdownAfterComplete") or False),
        wake_for_backup=bool(raw.get("wakeUp") or False),
        prevent_sleep_during_backup=bool(raw.get("windowsWorkingState") or False),
    )


def _parse_ps_config(raw: dict[str, Any]) -> MachinePsConfig:
    return MachinePsConfig(
        enable_app_aware_bkp=bool(raw.get("enableAppAwareBkp", True)),
        enable_verification=bool(raw.get("enableVerification") or False),
        verification_video_duration_seconds=int(raw.get("verificationPolicy") or 120),
        shutdown_after_backup=bool(raw.get("shutdownAfterComplete") or False),
        wake_for_backup=bool(raw.get("wakeUp") or False),
        prevent_sleep_during_backup=bool(raw.get("windowsWorkingState") or False),
    )


def _parse_db_config(raw: dict[str, Any]) -> MachineDbConfig | None:
    if raw.get("disableDbBackup", True):
        return None
    mssql = raw.get("mssqlServer") or {}
    oracle = raw.get("oracleServer") or {}
    return MachineDbConfig(
        action_on_error=_DB_ACTION_MAP.get(raw.get("logsProcessing") or "", DbActionOnError.CONTINUE),
        mssql_log_setting=_MSSQL_LOG_MAP.get(mssql.get("logSettings") or "", MssqlLogSetting.DO_NOT_TRUNCATE),
        oracle_log_setting=_ORACLE_LOG_MAP.get(oracle.get("logSettings") or "", OracleLogSetting.DO_NOT_DELETE),
    )


def _parse_backup_window(raw: dict[str, Any]) -> MachineBackupWindow:
    enabled = bool(raw.get("enabled") or False)
    data = raw.get("data") or ""
    allowed_hours: dict[WeekDay, frozenset[int]] = {}
    if enabled and len(data) == 168:
        for day in WeekDay:
            hours: set[int] = set()
            base = day.value * 24
            for h in range(24):
                if data[base + h] == "1":
                    hours.add(h)
            if hours:
                allowed_hours[day] = frozenset(hours)
    return MachineBackupWindow(enabled=enabled, allowed_hours=allowed_hours)


def _parse_task_schedule(raw: dict[str, Any]) -> MachineTaskSchedule:
    schedule_type = raw.get("scheduleType")

    log_off = bool(raw.get("logOff") or False)
    screen_lock = bool(raw.get("screenLock") or False)
    startup = bool(raw.get("startup") or False)
    period_length = int(raw.get("periodLength") or 1)
    secs = _PERIOD_BASE_SECS.get(raw.get("periodBase") or "", 3600) * period_length

    event_trigger: EventTriggerConfig | None = None
    if log_off or screen_lock or startup:
        event_trigger = EventTriggerConfig(
            on_sign_out=log_off,
            on_lock=screen_lock,
            on_startup=startup,
            min_interval=timedelta(seconds=secs),
        )

    if schedule_type == "EVENT":
        return MachineTaskSchedule(time_schedule=None, event_trigger=event_trigger)

    return MachineTaskSchedule(
        time_schedule=_parse_schedule(raw),
        event_trigger=event_trigger,
    )


def _parse_task_config(raw: dict[str, Any]) -> MachineTaskConfig:
    wl_type = _MACHINE_WORKLOAD_TYPE_MAP.get(raw.get("workloadType") or "", MachineWorkloadType.PC)
    os_type = _OS_TYPE_MAP.get(raw.get("osType") or "", MachineOsType.NONE)
    use_main = bool(raw.get("useMainSchedule", True))

    agent_scope_raw = raw.get("agentScope")
    scope: MachineTaskScope | None = None
    custom_volumes: tuple[str, ...] = ()
    include_external_drives = False
    include_boot_partition = True

    if agent_scope_raw is not None:
        source_type = agent_scope_raw.get("sourceType")
        scope = _SOURCE_TYPE_MAP.get(source_type, MachineTaskScope.ENTIRE_MACHINE)
        custom_volumes = tuple(agent_scope_raw.get("customVolume") or [])
        include_external_drives = bool(agent_scope_raw.get("enableBackupExternal") or False)
        include_boot_partition = bool(agent_scope_raw.get("includeBootPartition", True))

    schedule: MachineTaskSchedule | None = None
    if not use_main:
        schedule = _parse_task_schedule(raw.get("schedule") or {})

    return MachineTaskConfig(
        workload_type=wl_type,
        os_type=os_type,
        scope=scope,
        custom_volumes=custom_volumes,
        include_external_drives=include_external_drives,
        include_boot_partition=include_boot_partition,
        use_main_schedule=use_main,
        schedule=schedule,
    )


def _parse_plan(
    raw: dict[str, Any],
    location_cache: dict[str, LocationInfo] | None = None,
) -> ProtectionPlan:
    """API response → SDK ProtectionPlan. Category is inferred from spec.serviceType."""
    spec: dict[str, Any] = raw.get("spec") or {}
    service_type = spec.get("serviceType")
    category = WorkloadCategory.M365 if service_type == "M365" else WorkloadCategory.MACHINE

    retention = _parse_retention(spec.get("retention") or {})

    schedule: ProtectionSchedule | None = None
    config_device: dict[str, Any] = {}
    if category == WorkloadCategory.MACHINE:
        config_device = spec.get("configDevice") or {}
        main_schedule = config_device.get("mainSchedule")
        if main_schedule:
            schedule = _parse_schedule(main_schedule)
    else:
        config_m365 = spec.get("configM365") or {}
        sched_raw = config_m365.get("schedule")
        if sched_raw:
            schedule = _parse_schedule(sched_raw)

    policy = ProtectionPlanPolicy(retention=retention, schedule=schedule)

    bc = spec.get("backupCopy") or {}
    backup_copy_policy: BackupCopyPolicy | None = None
    if bc.get("enabled") and bc.get("destination"):
        dest_loc = (location_cache or {}).get(bc["destination"])
        if dest_loc is not None:
            bc_sched_raw = bc.get("schedule")
            bc_sched = (
                _parse_schedule(bc_sched_raw)
                if bc_sched_raw
                else ProtectionSchedule(ScheduleFrequency.AFTER_BACKUP, start_time=None)
            )
            backup_copy_policy = BackupCopyPolicy(
                destination=dest_loc,
                retention=_parse_retention(bc.get("retention") or {}),
                schedule=bc_sched,
            )

    protected = raw.get("protectedWorkloadCount") or 0
    unprotected = raw.get("unprotectedWorkloadCount") or 0
    backup_copy_status = _parse_backup_copy_status(raw.get("backupCopyStatus"))

    # Config fields — only present in get() responses (configDevice will be absent or empty in list())
    vm_config: MachineVmConfig | None = None
    pc_config: MachinePcConfig | None = None
    ps_config: MachinePsConfig | None = None
    db_config: MachineDbConfig | None = None
    backup_window: MachineBackupWindow | None = None
    tasks: tuple[MachineTaskConfig, ...] | None = None

    if category == WorkloadCategory.MACHINE:
        if "configVm" in config_device:
            vm_config = _parse_vm_config(config_device["configVm"])
        if "configPc" in config_device:
            pc_config = _parse_pc_config(config_device["configPc"])
        if "configPs" in config_device:
            ps_config = _parse_ps_config(config_device["configPs"])
        if "configSqlServer" in config_device:
            db_config = _parse_db_config(config_device["configSqlServer"])
        if "backupWindow" in config_device:
            backup_window = _parse_backup_window(config_device["backupWindow"])
        if "task" in config_device and isinstance(config_device["task"], list):
            tasks = tuple(_parse_task_config(t) for t in config_device["task"])

    return ProtectionPlan(
        plan_id=raw["id"],
        name=spec.get("name") or "",
        category=category,
        policy=policy,
        workload_count=protected + unprotected,
        description=spec.get("description") or "",
        successful_workload_count=protected,
        unsuccessful_workload_count=unprotected,
        is_immutable=bool(spec.get("isImmutable") or False),
        backup_copy_policy=backup_copy_policy,
        backup_copy_status=backup_copy_status,
        run_schedule_by_controller_time=spec.get("controllerUtcOffset") is not None,
        vm_config=vm_config,
        pc_config=pc_config,
        ps_config=ps_config,
        db_config=db_config,
        backup_window=backup_window,
        tasks=tasks,
    )
