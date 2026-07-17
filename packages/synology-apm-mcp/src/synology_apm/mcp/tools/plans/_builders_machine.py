"""Machine-only request-construction helpers for the machine protection plan tools.

No tool registration lives here — these are pure parsers and request-builders
consumed by tools/plans/machine.py. Shared primitives (used by M365 plans too)
live in _builders_common.py.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal

from synology_apm.mcp._enums import WeekDayLiteral
from synology_apm.mcp.tools.plans._builders_common import (
    _DAY_MAP,
    _build_backup_copy,
    _build_retention,
    _build_schedule,
    _parse_time,
    _parse_weekdays,
)
from synology_apm.sdk import (
    APMClient,
    DbActionOnError,
    EventTriggerConfig,
    MachineBackupWindow,
    MachineDbConfig,
    MachineOsType,
    MachinePcConfig,
    MachinePlanCreateRequest,
    MachinePsConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineTaskScope,
    MachineVmConfig,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    ProtectionSchedule,
    ScheduleFrequency,
    WeekDay,
)

_DB_ACTION_ON_ERROR = Literal["continue", "stop"]
_MSSQL_LOG_SETTING = Literal["do_not_truncate", "truncate"]
_ORACLE_LOG_SETTING = Literal["do_not_delete", "delete"]


def _build_optional_config(config_cls: type, **fields: Any) -> Any | None:
    """Build a dataclass instance from sparse optional fields, or None if all are unset.

    Only fields with a non-None value are passed through, so config_cls's own
    dataclass default applies to any omitted field (e.g. MachineVmConfig's
    enable_app_aware_bkp defaults to True when not explicitly set here).
    """
    if all(v is None for v in fields.values()):
        return None
    return config_cls(**{k: v for k, v in fields.items() if v is not None})


def _build_vm_config(
    enable_app_aware_bkp: bool | None,
    enable_verification: bool | None,
    verification_video_duration_seconds: int | None,
    enable_datastore_usage_detection: bool | None,
    datastore_min_free_space_percent: int | None,
) -> MachineVmConfig | None:
    return _build_optional_config(
        MachineVmConfig,
        enable_app_aware_bkp=enable_app_aware_bkp,
        enable_verification=enable_verification,
        verification_video_duration_seconds=verification_video_duration_seconds,
        enable_datastore_usage_detection=enable_datastore_usage_detection,
        datastore_min_free_space_percent=datastore_min_free_space_percent,
    )


def _build_pc_config(
    shutdown_after_backup: bool | None,
    wake_for_backup: bool | None,
    prevent_sleep_during_backup: bool | None,
) -> MachinePcConfig | None:
    return _build_optional_config(
        MachinePcConfig,
        shutdown_after_backup=shutdown_after_backup,
        wake_for_backup=wake_for_backup,
        prevent_sleep_during_backup=prevent_sleep_during_backup,
    )


def _build_ps_config(
    enable_app_aware_bkp: bool | None,
    enable_verification: bool | None,
    verification_video_duration_seconds: int | None,
    shutdown_after_backup: bool | None,
    wake_for_backup: bool | None,
    prevent_sleep_during_backup: bool | None,
) -> MachinePsConfig | None:
    return _build_optional_config(
        MachinePsConfig,
        enable_app_aware_bkp=enable_app_aware_bkp,
        enable_verification=enable_verification,
        verification_video_duration_seconds=verification_video_duration_seconds,
        shutdown_after_backup=shutdown_after_backup,
        wake_for_backup=wake_for_backup,
        prevent_sleep_during_backup=prevent_sleep_during_backup,
    )


def _build_db_config(
    action_on_error: str | None,
    mssql_log_setting: str | None,
    oracle_log_setting: str | None,
) -> MachineDbConfig | None:
    return _build_optional_config(
        MachineDbConfig,
        action_on_error=DbActionOnError(action_on_error) if action_on_error is not None else None,
        mssql_log_setting=MssqlLogSetting(mssql_log_setting) if mssql_log_setting is not None else None,
        oracle_log_setting=OracleLogSetting(oracle_log_setting) if oracle_log_setting is not None else None,
    )


def _parse_backup_window(enabled: bool, spec: str | None) -> MachineBackupWindow | None:
    if not enabled and not spec:
        return None
    allowed_hours: dict[WeekDay, frozenset[int]] = {}
    for day_part in (spec or "").split(";"):
        day_part = day_part.strip()
        if not day_part:
            continue
        if ":" not in day_part:
            raise ValueError(f"Unrecognized backup window entry: {day_part!r}. Use DAY:H-H,H-H;DAY:...")
        day_token, hours_part = day_part.split(":", 1)
        day_key = day_token.strip().lower()[:3]
        if day_key not in _DAY_MAP:
            raise ValueError(f"Unrecognized weekday: {day_token.strip()!r}.")
        hours: set[int] = set()
        for rng in hours_part.split(","):
            rng = rng.strip()
            if not rng:
                continue
            if "-" in rng:
                start_s, end_s = rng.split("-", 1)
                hours.update(range(int(start_s), int(end_s) + 1))
            else:
                hours.add(int(rng))
        allowed_hours[WeekDay(_DAY_MAP[day_key])] = frozenset(hours)
    return MachineBackupWindow(enabled=enabled, allowed_hours=allowed_hours)


def _parse_tasks_json(raw: str | None) -> tuple[MachineTaskConfig, ...] | None:
    if not raw:
        return None
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"tasks_json is not valid JSON: {e}") from e
    if not isinstance(entries, list):
        raise ValueError("tasks_json must be a JSON array of task objects.")

    tasks = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each tasks_json entry must be a JSON object.")
        schedule = None
        raw_schedule = entry.get("schedule")
        if raw_schedule is not None:
            time_schedule = None
            raw_time_schedule = raw_schedule.get("time_schedule")
            if raw_time_schedule is not None:
                time_schedule = ProtectionSchedule(
                    frequency=ScheduleFrequency(raw_time_schedule["frequency"]),
                    start_time=_parse_time(raw_time_schedule.get("start_time")),
                    weekdays=_parse_weekdays(list(raw_time_schedule.get("weekdays", ()))),
                )
            event_trigger = None
            raw_event_trigger = raw_schedule.get("event_trigger")
            if raw_event_trigger is not None:
                event_trigger = EventTriggerConfig(
                    on_sign_out=raw_event_trigger.get("on_sign_out", False),
                    on_lock=raw_event_trigger.get("on_lock", False),
                    on_startup=raw_event_trigger.get("on_startup", False),
                    min_interval=timedelta(seconds=raw_event_trigger.get("min_interval_seconds", 3600)),
                )
            schedule = MachineTaskSchedule(time_schedule=time_schedule, event_trigger=event_trigger)
        scope_raw = entry.get("scope")
        tasks.append(
            MachineTaskConfig(
                workload_type=MachineWorkloadType(entry["workload_type"]),
                os_type=MachineOsType(entry["os_type"]),
                scope=MachineTaskScope(scope_raw) if scope_raw else None,
                custom_volumes=tuple(entry.get("custom_volumes", ())),
                include_external_drives=entry.get("include_external_drives", False),
                include_boot_partition=entry.get("include_boot_partition", True),
                use_main_schedule=entry.get("use_main_schedule", True),
                schedule=schedule,
            )
        )
    return tuple(tasks)


async def _build_machine_plan_request(
    apm: APMClient,
    *,
    name: str,
    retention_type: str,
    retention_days: int | None,
    retention_versions: int | None,
    gfs_daily_versions: int | None,
    gfs_weekly_versions: int | None,
    gfs_monthly_versions: int | None,
    gfs_yearly_versions: int | None,
    schedule_frequency: str,
    schedule_time: str | None,
    weekdays: list[WeekDayLiteral] | None,
    description: str,
    is_immutable: bool,
    run_schedule_by_controller_time: bool,
    vm_enable_app_aware_bkp: bool | None,
    vm_enable_verification: bool | None,
    vm_verification_video_duration_seconds: int | None,
    vm_enable_datastore_usage_detection: bool | None,
    vm_datastore_min_free_space_percent: int | None,
    pc_shutdown_after_backup: bool | None,
    pc_wake_for_backup: bool | None,
    pc_prevent_sleep_during_backup: bool | None,
    ps_enable_app_aware_bkp: bool | None,
    ps_enable_verification: bool | None,
    ps_verification_video_duration_seconds: int | None,
    ps_shutdown_after_backup: bool | None,
    ps_wake_for_backup: bool | None,
    ps_prevent_sleep_during_backup: bool | None,
    db_action_on_error: str | None,
    db_mssql_log_setting: str | None,
    db_oracle_log_setting: str | None,
    backup_window_enabled: bool,
    backup_window_allowed_hours: str | None,
    tasks_json: str | None,
    backup_copy_destination_type: str | None,
    backup_copy_destination_id: str | None,
    backup_copy_retention_type: str | None,
    backup_copy_retention_days: int | None,
    backup_copy_retention_versions: int | None,
    backup_copy_gfs_daily_versions: int | None,
    backup_copy_gfs_weekly_versions: int | None,
    backup_copy_gfs_monthly_versions: int | None,
    backup_copy_gfs_yearly_versions: int | None,
    backup_copy_schedule_frequency: str | None,
    backup_copy_schedule_time: str | None,
    backup_copy_weekdays: list[WeekDayLiteral] | None,
) -> MachinePlanCreateRequest:
    """Shared request-builder for create_machine_protection_plan and
    update_machine_protection_plan — both tools resolve their own defaults (create
    has sensible ones; update requires base fields explicit) before calling this,
    so no defaulting happens in here."""
    return MachinePlanCreateRequest(
        name=name,
        retention=_build_retention(
            retention_type, retention_days, retention_versions,
            gfs_daily_versions, gfs_weekly_versions, gfs_monthly_versions, gfs_yearly_versions,
        ),
        schedule=_build_schedule(schedule_frequency, schedule_time, weekdays),
        description=description,
        is_immutable=is_immutable,
        run_schedule_by_controller_time=run_schedule_by_controller_time,
        vm_config=_build_vm_config(
            vm_enable_app_aware_bkp, vm_enable_verification, vm_verification_video_duration_seconds,
            vm_enable_datastore_usage_detection, vm_datastore_min_free_space_percent,
        ),
        pc_config=_build_pc_config(pc_shutdown_after_backup, pc_wake_for_backup, pc_prevent_sleep_during_backup),
        ps_config=_build_ps_config(
            ps_enable_app_aware_bkp, ps_enable_verification, ps_verification_video_duration_seconds,
            ps_shutdown_after_backup, ps_wake_for_backup, ps_prevent_sleep_during_backup,
        ),
        db_config=_build_db_config(db_action_on_error, db_mssql_log_setting, db_oracle_log_setting),
        backup_window=_parse_backup_window(backup_window_enabled, backup_window_allowed_hours),
        tasks=_parse_tasks_json(tasks_json),
        backup_copy=await _build_backup_copy(
            apm,
            backup_copy_destination_type, backup_copy_destination_id,
            backup_copy_retention_type, backup_copy_retention_days, backup_copy_retention_versions,
            backup_copy_gfs_daily_versions, backup_copy_gfs_weekly_versions,
            backup_copy_gfs_monthly_versions, backup_copy_gfs_yearly_versions,
            backup_copy_schedule_frequency, backup_copy_schedule_time, backup_copy_weekdays,
        ),
    )
