"""Request-construction primitives shared by machine and M365 protection plan tools.

No tool registration lives here — these are pure parsers and request-builders
consumed by tools/plans/machine.py, tools/plans/m365.py, and tools/plans/_builders_machine.py.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import time
from typing import Literal

from synology_apm.mcp._enums import WeekDayLiteral
from synology_apm.sdk import (
    APMClient,
    BackupCopyConfig,
    BackupServer,
    GFSRetention,
    M365PlanCreateRequest,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorage,
    RetentionType,
    ScheduleFrequency,
    WeekDay,
)

_RETENTION_TYPE = Literal["keep_all", "keep_days", "keep_versions", "keep_advanced", "none"]
_FREQUENCY = Literal["manual", "hourly", "daily", "weekly"]
_BACKUP_COPY_FREQUENCY = Literal["daily", "weekly", "after_backup"]

_RETENTION_SCHEDULE_DESC = (
    "retention_type: keep_all, keep_days, keep_versions, keep_advanced (requires the gfs_* params), "
    "none; is_immutable requires keep_days. schedule_frequency: manual, hourly, daily, weekly (weekly "
    "requires at least one weekday in weekdays). schedule_time: HH:MM. weekdays: list of sun,mon,tue,..."
)
"""Shared clause for create_machine_protection_plan and create_m365_protection_plan
descriptions, documenting the retention/schedule semantics both plan types share
identically, so the wording can't drift between the two tool files."""

_DAY_MAP = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _parse_time(t: str | None) -> time | None:
    if not t:
        return None
    parts = t.split(":")
    if len(parts) not in (1, 2):
        raise ValueError(f"Unrecognized time: {t!r}. Use HH:MM.")
    h, m = parts if len(parts) == 2 else (parts[0], "0")
    return time(int(h), int(m))


def _parse_required_time(t: str) -> time:
    parsed = _parse_time(t)
    if parsed is None:
        raise ValueError(f"Unrecognized time: {t!r}. Use HH:MM.")
    return parsed


def _parse_weekdays(days: Sequence[str] | None) -> tuple[WeekDay, ...]:
    if not days:
        return ()
    result = []
    for token in days:
        if token not in _DAY_MAP:
            raise ValueError(f"Unrecognized weekday: {token!r}. Use sun, mon, tue, wed, thu, fri, sat.")
        result.append(WeekDay(_DAY_MAP[token]))
    return tuple(result)


def _build_retention(
    retention_type: str,
    retention_days: int | None,
    retention_versions: int | None,
    gfs_daily_versions: int | None = None,
    gfs_weekly_versions: int | None = None,
    gfs_monthly_versions: int | None = None,
    gfs_yearly_versions: int | None = None,
) -> ProtectionRetentionPolicy:
    rtype = RetentionType(retention_type)
    gfs = None
    if rtype == RetentionType.KEEP_ADVANCED:
        if (
            gfs_daily_versions is None
            or gfs_weekly_versions is None
            or gfs_monthly_versions is None
            or gfs_yearly_versions is None
        ):
            raise ValueError(
                "retention_type=keep_advanced requires gfs_daily_versions, gfs_weekly_versions, "
                "gfs_monthly_versions, and gfs_yearly_versions."
            )
        gfs = GFSRetention(
            daily_versions=gfs_daily_versions,
            weekly_versions=gfs_weekly_versions,
            monthly_versions=gfs_monthly_versions,
            yearly_versions=gfs_yearly_versions,
        )
    return ProtectionRetentionPolicy(
        retention_type=rtype,
        days=retention_days,
        versions=retention_versions,
        gfs=gfs,
    )


def _build_schedule(
    frequency: str,
    schedule_time: str | None,
    weekdays: list[WeekDayLiteral] | None,
) -> ProtectionSchedule:
    return ProtectionSchedule(
        frequency=ScheduleFrequency(frequency),
        start_time=_parse_time(schedule_time),
        weekdays=_parse_weekdays(weekdays),
    )


async def _build_backup_copy(
    apm: APMClient,
    destination_type: str | None,
    destination_id: str | None,
    retention_type: str | None,
    retention_days: int | None,
    retention_versions: int | None,
    gfs_daily_versions: int | None,
    gfs_weekly_versions: int | None,
    gfs_monthly_versions: int | None,
    gfs_yearly_versions: int | None,
    schedule_frequency: str | None,
    schedule_time: str | None,
    weekdays: list[WeekDayLiteral] | None,
) -> BackupCopyConfig | None:
    if not destination_id:
        return None
    if destination_type is None:
        raise ValueError("backup_copy_destination_type is required when backup_copy_destination_id is given.")
    if retention_type is None:
        raise ValueError("backup_copy_retention_type is required when backup_copy_destination_id is given.")
    if schedule_frequency is None:
        raise ValueError("backup_copy_schedule_frequency is required when backup_copy_destination_id is given.")
    destination: BackupServer | RemoteStorage
    if destination_type == "backup_server":
        destination = await apm.backup_servers.get(destination_id)
    elif destination_type == "remote_storage":
        destination = await apm.remote_storages.get(destination_id)
    else:
        raise ValueError(
            f"Unsupported backup_copy_destination_type: {destination_type!r}. Must be 'backup_server' or 'remote_storage'."
        )
    retention = _build_retention(
        retention_type, retention_days, retention_versions,
        gfs_daily_versions, gfs_weekly_versions, gfs_monthly_versions, gfs_yearly_versions,
    )
    schedule = _build_schedule(schedule_frequency, schedule_time, weekdays)
    return BackupCopyConfig(destination=destination, retention=retention, schedule=schedule)


async def _build_m365_plan_request(
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
) -> M365PlanCreateRequest:
    """Shared request-builder for create_m365_protection_plan and
    update_m365_protection_plan. Narrower than _build_machine_plan_request (in
    _builders_machine.py) — M365 plans have no vm_config/pc_config/ps_config/db_config."""
    return M365PlanCreateRequest(
        name=name,
        retention=_build_retention(
            retention_type, retention_days, retention_versions,
            gfs_daily_versions, gfs_weekly_versions, gfs_monthly_versions, gfs_yearly_versions,
        ),
        schedule=_build_schedule(schedule_frequency, schedule_time, weekdays),
        description=description,
        is_immutable=is_immutable,
        run_schedule_by_controller_time=run_schedule_by_controller_time,
        backup_copy=await _build_backup_copy(
            apm,
            backup_copy_destination_type, backup_copy_destination_id,
            backup_copy_retention_type, backup_copy_retention_days, backup_copy_retention_versions,
            backup_copy_gfs_daily_versions, backup_copy_gfs_weekly_versions,
            backup_copy_gfs_monthly_versions, backup_copy_gfs_yearly_versions,
            backup_copy_schedule_frequency, backup_copy_schedule_time, backup_copy_weekdays,
        ),
    )
