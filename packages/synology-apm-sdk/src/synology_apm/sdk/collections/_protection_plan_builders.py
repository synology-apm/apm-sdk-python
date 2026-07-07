"""Request-body builders for Protection Plan collections.

Private module backing collections/protection_plans.py; converts SDK create/update
request objects into raw plan API request bodies. The enum→API string directions
are derived by inverting the parse maps in _protection_plan_parsers.py so both
directions stay in sync.
"""
from __future__ import annotations

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
    WeekDay,
)
from ..models.backup_server import BackupServer
from ..models.protection_plan import (
    BackupCopyConfig,
    M365PlanCreateRequest,
    MachineBackupWindow,
    MachineDbConfig,
    MachinePcConfig,
    MachinePlanCreateRequest,
    MachinePsConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineVmConfig,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from ._protection_plan_parsers import (
    _DB_ACTION_MAP,
    _MSSQL_LOG_MAP,
    _ORACLE_LOG_MAP,
    _SOURCE_TYPE_MAP,
)
from ._shared import _STORAGE_TYPE_TO_DEST_TYPE

_SCOPE_TO_SOURCE_TYPE: dict[MachineTaskScope, str] = {v: k for k, v in _SOURCE_TYPE_MAP.items()}
_DB_ACTION_TO_API: dict[DbActionOnError, str] = {v: k for k, v in _DB_ACTION_MAP.items()}
_MSSQL_LOG_TO_API: dict[MssqlLogSetting, str] = {v: k for k, v in _MSSQL_LOG_MAP.items()}
_ORACLE_LOG_TO_API: dict[OracleLogSetting, str] = {v: k for k, v in _ORACLE_LOG_MAP.items()}


def _build_device_body(request: MachinePlanCreateRequest) -> dict[str, Any]:
    return {
        "plan": {
            "name": request.name,
            "description": request.description,
            "isImmutable": request.is_immutable,
            "serviceType": "DEVICE",
            "retention": _build_retention_dict(request.retention),
            "configDevice": {
                "task": _build_task_list(request.tasks, request.schedule),
                "configVm": _build_vm_config_dict(request.vm_config),
                "configPc": _build_pc_config_dict(request.pc_config),
                "configPs": _build_ps_config_dict(request.ps_config),
                "backupWindow": _build_backup_window_dict(request.backup_window),
                "mainSchedule": _build_main_schedule_dict(request.schedule),
                "configSqlServer": _build_db_config_dict(request.db_config),
            },
            "backupCopy": _build_backup_copy_dict(request.backup_copy),
        },
        "runScheduleByControllerTime": request.run_schedule_by_controller_time,
    }


def _build_m365_body(request: M365PlanCreateRequest) -> dict[str, Any]:
    return {
        "plan": {
            "name": request.name,
            "description": request.description,
            "isImmutable": request.is_immutable,
            "serviceType": "M365",
            "retention": _build_retention_dict(request.retention),
            "configM365": {
                "exchangeScope": [
                    "GROUP_MAIL", "GROUP_CALENDAR", "MAIL",
                    "CONTACTS", "CALENDAR", "ARCHIVE_MAIL",
                ],
                "schedule": _build_main_schedule_dict(request.schedule),
            },
            "backupCopy": _build_backup_copy_dict(request.backup_copy),
        },
        "runScheduleByControllerTime": request.run_schedule_by_controller_time,
    }


def _build_retention_dict(retention: ProtectionRetentionPolicy) -> dict[str, Any]:
    if retention.retention_type == RetentionType.KEEP_ALL:
        return {"keepAll": True}
    if retention.retention_type == RetentionType.KEEP_DAYS:
        return {"keepAll": False, "keepDays": retention.days or 0}
    if retention.retention_type == RetentionType.KEEP_VERSIONS:
        return {"keepAll": False, "keepVersions": retention.versions or 0}
    if retention.retention_type == RetentionType.KEEP_ADVANCED:
        gfs = retention.gfs
        return {
            "keepAll": False,
            "keepDays": retention.days or 0,
            "keepVersions": retention.versions or 0,
            "gfsDays":   gfs.daily_versions   if gfs else 0,
            "gfsWeeks":  gfs.weekly_versions  if gfs else 0,
            "gfsMonths": gfs.monthly_versions if gfs else 0,
            "gfsYears":  gfs.yearly_versions  if gfs else 0,
        }
    return {"keepAll": False}


def _build_freq_dict(
    freq: ScheduleFrequency,
    h: int,
    m: int,
    weekdays: tuple[WeekDay, ...],
) -> dict[str, Any]:
    if freq == ScheduleFrequency.MANUAL:
        return {"scheduleType": "NONE", "repeatType": "DAILY",
                "runWeekday": [5], "repeatHour": 0, "runHour": 0, "runMin": 0}
    if freq == ScheduleFrequency.HOURLY:
        return {"scheduleType": "SCHEDULE", "repeatType": "DAILY",
                "repeatHour": 1, "runWeekday": [5], "runHour": 0, "runMin": m}
    if freq == ScheduleFrequency.WEEKLY:
        return {"scheduleType": "SCHEDULE", "repeatType": "WEEKLY",
                "repeatHour": 0, "runWeekday": [d.value for d in weekdays],
                "runHour": h, "runMin": m}
    return {"scheduleType": "SCHEDULE", "repeatType": "DAILY",
            "repeatHour": 0, "runWeekday": [5], "runHour": h, "runMin": m}


def _build_main_schedule_dict(schedule: ProtectionSchedule) -> dict[str, Any]:
    t = schedule.start_time
    h = t.hour if t else 0
    m = t.minute if t else 0
    return {"lastRunHour": 0, "lastRunMin": 0,
            **_build_freq_dict(schedule.frequency, h, m, schedule.weekdays)}


def _set_period(sched: dict[str, Any], secs: int) -> None:
    if secs % 86400 == 0:
        sched["periodBase"] = "DAY"
        sched["periodLength"] = secs // 86400
    elif secs % 3600 == 0:
        sched["periodBase"] = "HOUR"
        sched["periodLength"] = secs // 3600
    else:
        sched["periodBase"] = "MIN"
        sched["periodLength"] = max(1, secs // 60)


def _build_task_schedule_dict(
    main_schedule: ProtectionSchedule,
    *,
    enable_events: bool = False,
    task_schedule: MachineTaskSchedule | None = None,
) -> dict[str, Any]:
    """Build per-task schedule dict. Uses task_schedule when provided; mirrors main otherwise."""
    ts: ProtectionSchedule | None = None
    if task_schedule is not None:
        ts = task_schedule.time_schedule
        freq = ts.frequency if ts else ScheduleFrequency.MANUAL
        t = ts.start_time if ts else None
        h = t.hour if t else 0
        m = t.minute if t else 0
        weekdays = ts.weekdays if ts else ()
        et = task_schedule.event_trigger
    else:
        freq = main_schedule.frequency
        t = main_schedule.start_time
        h = t.hour if t else 0
        m = t.minute if t else 0
        weekdays = main_schedule.weekdays
        et = None

    # EVENT mode: time_schedule=None + event_trigger set (PC only; enable_events gates non-PC)
    if ts is None and et is not None and enable_events:
        secs = int(et.min_interval.total_seconds())
        sched: dict[str, Any] = {
            "scheduleType": "EVENT",
            "logOff": et.on_sign_out,
            "screenLock": et.on_lock,
            "startup": et.on_startup,
        }
        _set_period(sched, secs)
        return sched

    # NONE / SCHEDULE / SCHEDULE_AND_EVENT path
    sched = _build_freq_dict(freq, h, m, weekdays)

    if task_schedule is not None:
        # Custom schedule: emit event flags only when event_trigger is explicitly set
        if et is not None and enable_events:
            on_sign_out, on_lock, on_startup = et.on_sign_out, et.on_lock, et.on_startup
            secs = int(et.min_interval.total_seconds())
        else:
            on_sign_out = on_lock = on_startup = False
            secs = 3600
    else:
        # use_main_schedule=True: inherit main schedule, enable all event flags for PC tasks
        on_sign_out = on_lock = on_startup = enable_events
        secs = 3600

    sched["logOff"] = on_sign_out and enable_events
    sched["screenLock"] = on_lock and enable_events
    sched["startup"] = on_startup and enable_events

    # Upgrade SCHEDULE → SCHEDULE_AND_EVENT if any event flag is active
    if sched.get("scheduleType") == "SCHEDULE" and (sched["logOff"] or sched["screenLock"] or sched["startup"]):
        sched["scheduleType"] = "SCHEDULE_AND_EVENT"

    _set_period(sched, secs)
    return sched


def _build_task_agent_scope(task: MachineTaskConfig) -> dict[str, Any] | None:
    """Build agentScope dict; returns None for VM/FS tasks (no agentScope)."""
    if task.workload_type in (MachineWorkloadType.VM, MachineWorkloadType.FS):
        return None
    if task.scope is None:
        return None
    source_type = _SCOPE_TO_SOURCE_TYPE[task.scope]
    return {
        "sourceType": source_type,
        "customVolume": list(task.custom_volumes),
        "enableBackupExternal": task.include_external_drives,
        "includeBootPartition": task.include_boot_partition,
    }


def _build_task_dict(task: MachineTaskConfig, main_schedule: ProtectionSchedule) -> dict[str, Any]:
    is_pc = task.workload_type == MachineWorkloadType.PC
    if task.use_main_schedule:
        task_sched = _build_task_schedule_dict(main_schedule, enable_events=is_pc)
    else:
        task_sched = _build_task_schedule_dict(
            main_schedule, enable_events=is_pc, task_schedule=task.schedule
        )
    d: dict[str, Any] = {
        "workloadType": task.workload_type.name,
        "osType": task.os_type.name,
        "schedule": task_sched,
        "useMainSchedule": task.use_main_schedule,
    }
    agent_scope = _build_task_agent_scope(task)
    if agent_scope is not None:
        d["agentScope"] = agent_scope
    return d


def _build_task_list(
    tasks: tuple[MachineTaskConfig, ...] | None,
    schedule: ProtectionSchedule,
) -> list[dict[str, Any]]:
    if tasks is None:
        return _build_default_task_list(schedule)
    return [_build_task_dict(t, schedule) for t in tasks]


def _build_default_task_list(schedule: ProtectionSchedule) -> list[dict[str, Any]]:
    _defaults: list[tuple[MachineWorkloadType, MachineOsType, MachineTaskScope | None, bool]] = [
        (MachineWorkloadType.PC, MachineOsType.WINDOWS, MachineTaskScope.ENTIRE_MACHINE, True),
        (MachineWorkloadType.PC, MachineOsType.MAC,     MachineTaskScope.ENTIRE_MACHINE, True),
        (MachineWorkloadType.PS, MachineOsType.WINDOWS, MachineTaskScope.ENTIRE_MACHINE, True),
        (MachineWorkloadType.PS, MachineOsType.LINUX,   MachineTaskScope.ENTIRE_MACHINE, False),
        (MachineWorkloadType.FS, MachineOsType.NONE,    None,                            False),
        (MachineWorkloadType.VM, MachineOsType.NONE,    None,                            False),
    ]
    result = []
    for wl_type, os_type, scope, include_ext in _defaults:
        task = MachineTaskConfig(
            workload_type=wl_type,
            os_type=os_type,
            scope=scope,
            include_external_drives=include_ext,
            use_main_schedule=True,
        )
        result.append(_build_task_dict(task, schedule))
    return result


def _build_vm_config_dict(cfg: MachineVmConfig | None) -> dict[str, Any]:
    c = cfg or MachineVmConfig()
    return {
        "enableAppAwareBkp":           c.enable_app_aware_bkp,
        "enableVerification":          c.enable_verification,
        "verificationPolicy":          c.verification_video_duration_seconds,
        "enableDatastoreAware":        c.enable_datastore_usage_detection,
        "datastoreReservedPercentage": c.datastore_min_free_space_percent,
    }


def _build_pc_config_dict(cfg: MachinePcConfig | None) -> dict[str, Any]:
    c = cfg or MachinePcConfig()
    return {
        "shutdownAfterComplete": c.shutdown_after_backup,
        "wakeUp":                c.wake_for_backup,
        "windowsWorkingState":   c.prevent_sleep_during_backup,
    }


def _build_ps_config_dict(cfg: MachinePsConfig | None) -> dict[str, Any]:
    c = cfg or MachinePsConfig()
    return {
        "enableAppAwareBkp":    c.enable_app_aware_bkp,
        "enableVerification":   c.enable_verification,
        "verificationPolicy":   c.verification_video_duration_seconds,
        "shutdownAfterComplete": c.shutdown_after_backup,
        "wakeUp":               c.wake_for_backup,
        "windowsWorkingState":  c.prevent_sleep_during_backup,
    }


def _build_db_config_dict(cfg: MachineDbConfig | None) -> dict[str, Any]:
    if cfg is None:
        return {
            "disableDbBackup": True,
            "logsProcessing": "DISABLED",
            "mssqlServer": {"logSettings": "DELETE_LOGS_BY_DB_RULE"},
            "oracleServer": {"logSettings": "NOT_DELETE_LOGS"},
            "enableDefaultCredential": False,
            "guestOsCredential": {"userName": "", "password": ""},
            "dbCredentialSql": {"userName": "", "password": ""},
            "dbCredentialOracle": {"userName": "", "password": ""},
        }
    return {
        "disableDbBackup": False,
        "logsProcessing": _DB_ACTION_TO_API[cfg.action_on_error],
        "mssqlServer": {"logSettings": _MSSQL_LOG_TO_API[cfg.mssql_log_setting]},
        "oracleServer": {"logSettings": _ORACLE_LOG_TO_API[cfg.oracle_log_setting]},
        "enableDefaultCredential": False,
        "guestOsCredential": {"userName": "", "password": ""},
        "dbCredentialSql": {"userName": "", "password": ""},
        "dbCredentialOracle": {"userName": "", "password": ""},
    }


def _build_backup_window_dict(cfg: MachineBackupWindow | None) -> dict[str, Any]:
    if cfg is None or not cfg.enabled:
        return {"enabled": False, "data": "1" * 168}
    data_chars = ["0"] * 168
    for day, hours in cfg.allowed_hours.items():
        base = day.value * 24
        for h in hours:
            if 0 <= h <= 23:
                data_chars[base + h] = "1"
    return {"enabled": True, "data": "".join(data_chars)}


def _build_backup_copy_schedule_dict(schedule: ProtectionSchedule) -> dict[str, Any]:
    if schedule.frequency == ScheduleFrequency.AFTER_BACKUP:
        return {"scheduleType": "EVENT", "runHour": 20, "runMin": 0}
    t = schedule.start_time
    h = t.hour if t else 0
    m = t.minute if t else 0
    if schedule.frequency == ScheduleFrequency.WEEKLY:
        return {
            "scheduleType": "SCHEDULE", "repeatType": "WEEKLY",
            "runWeekday": [d.value for d in schedule.weekdays],
            "runHour": h, "runMin": m,
        }
    return {"scheduleType": "SCHEDULE", "runHour": h, "runMin": m}


def _build_backup_copy_dict(cfg: BackupCopyConfig | None) -> dict[str, Any]:
    if cfg is None:
        return {
            "enabled": False,
            "destinationType": "APPLIANCE",
            "destination": "",
            "schedule": {"scheduleType": "EVENT", "runHour": 20, "runMin": 0},
            "retention": {"keepAll": False, "keepDays": 30},
        }
    if isinstance(cfg.destination, BackupServer):
        dest_type = "APPLIANCE"
        dest_id = cfg.destination.namespace
    else:
        _dt = _STORAGE_TYPE_TO_DEST_TYPE.get(cfg.destination.storage_type)
        if _dt is None:
            raise ValueError(
                f"Unsupported RemoteStorage type {cfg.destination.storage_type!r}."
            )
        dest_type = _dt
        dest_id = cfg.destination.storage_id
    return {
        "enabled": True,
        "destinationType": dest_type,
        "destination": dest_id,
        "schedule": _build_backup_copy_schedule_dict(cfg.schedule),
        "retention": _build_retention_dict(cfg.retention),
    }
