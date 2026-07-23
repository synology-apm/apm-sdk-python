"""Machine protection plan create/update tools."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import Context

from synology_apm.mcp._enums import WeekDayLiteral
from synology_apm.mcp._helpers import JSON_LIST_VALIDATOR
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import run_audited_tool
from synology_apm.mcp.tools.plans._builders_common import (
    _BACKUP_COPY_FREQUENCY,
    _FREQUENCY,
    _RETENTION_SCHEDULE_DESC,
    _RETENTION_TYPE,
)
from synology_apm.mcp.tools.plans._builders_machine import (
    _DB_ACTION_ON_ERROR,
    _MSSQL_LOG_SETTING,
    _ORACLE_LOG_SETTING,
    _build_machine_plan_request,
)
from synology_apm.sdk import APMClient


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register machine protection plan create/update tools onto server."""

    @registrar.tool("admin", description=(
        f"Create a machine protection plan (fails if the name is already taken). {_RETENTION_SCHEDULE_DESC} "
        "Optional advanced sections: vm_*/pc_*/ps_*/db_* device-type settings, backup_window_enabled + "
        "backup_window_allowed_hours (format 'mon:0-8,13-18;tue:0-23'), tasks_json (JSON array overriding "
        "the per-workload-type task scope/schedule; omit for the default 6-entry task set), and "
        "backup_copy_* (cross-server/storage Backup Copy destination, retention, and schedule — "
        "backup_copy_schedule_frequency accepts after_backup here in addition to daily/weekly; "
        "weekly requires at least one weekday in backup_copy_weekdays)."
    ))
    async def create_machine_protection_plan(
        ctx: Context,
        name: str,
        retention_type: _RETENTION_TYPE = "keep_days",
        retention_days: int | None = 30,
        retention_versions: int | None = None,
        gfs_daily_versions: int | None = None,
        gfs_weekly_versions: int | None = None,
        gfs_monthly_versions: int | None = None,
        gfs_yearly_versions: int | None = None,
        schedule_frequency: _FREQUENCY = "daily",
        schedule_time: str | None = "02:00",
        weekdays: Annotated[list[WeekDayLiteral], JSON_LIST_VALIDATOR] | None = None,
        description: str = "",
        is_immutable: bool = False,
        run_schedule_by_controller_time: bool = False,
        vm_enable_app_aware_bkp: bool | None = None,
        vm_enable_verification: bool | None = None,
        vm_verification_video_duration_seconds: int | None = None,
        vm_enable_datastore_usage_detection: bool | None = None,
        vm_datastore_min_free_space_percent: int | None = None,
        pc_shutdown_after_backup: bool | None = None,
        pc_wake_for_backup: bool | None = None,
        pc_prevent_sleep_during_backup: bool | None = None,
        ps_enable_app_aware_bkp: bool | None = None,
        ps_enable_verification: bool | None = None,
        ps_verification_video_duration_seconds: int | None = None,
        ps_shutdown_after_backup: bool | None = None,
        ps_wake_for_backup: bool | None = None,
        ps_prevent_sleep_during_backup: bool | None = None,
        db_action_on_error: _DB_ACTION_ON_ERROR | None = None,
        db_mssql_log_setting: _MSSQL_LOG_SETTING | None = None,
        db_oracle_log_setting: _ORACLE_LOG_SETTING | None = None,
        backup_window_enabled: bool = False,
        backup_window_allowed_hours: str | None = None,
        tasks_json: str | None = None,
        backup_copy_destination_type: Literal["backup_server", "remote_storage"] | None = None,
        backup_copy_destination_id: str | None = None,
        backup_copy_retention_type: _RETENTION_TYPE | None = None,
        backup_copy_retention_days: int | None = None,
        backup_copy_retention_versions: int | None = None,
        backup_copy_gfs_daily_versions: int | None = None,
        backup_copy_gfs_weekly_versions: int | None = None,
        backup_copy_gfs_monthly_versions: int | None = None,
        backup_copy_gfs_yearly_versions: int | None = None,
        backup_copy_schedule_frequency: _BACKUP_COPY_FREQUENCY | None = None,
        backup_copy_schedule_time: str | None = None,
        backup_copy_weekdays: Annotated[list[WeekDayLiteral], JSON_LIST_VALIDATOR] | None = None,
    ) -> str:
        # Snapshot as the first statement so locals() holds only this function's own
        # parameters (see _build_machine_plan_request for the matching signature).
        build_kwargs = {k: v for k, v in locals().items() if k != "ctx"}
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _create() -> dict[str, Any]:
            request = await _build_machine_plan_request(apm, **build_kwargs)
            plan = await apm.machine.plans.create(request)
            return plan.to_dict()

        return await run_audited_tool(
            _create(),
            action="create_machine_protection_plan",
            params={"name": name},
        )

    @registrar.tool("admin", description=(
        "Update an existing machine protection plan by ID. Base fields (name, retention_type, retention_days, "
        "retention_versions, schedule_frequency, schedule_time, weekdays, description, is_immutable) must be "
        "supplied explicitly every call — call get_protection_plan first and resupply current values for "
        "anything unchanged. is_immutable requires keep_days retention; weekly needs at least one weekday. "
        "This is a full replace: gfs_* must be resupplied whenever retention_type=keep_advanced (otherwise "
        "the update fails); any other optional advanced field (vm_*/pc_*/ps_*/db_*, backup_window_*, "
        "tasks_json, backup_copy_*) left unset resets that feature to disabled/default."
    ))
    async def update_machine_protection_plan(
        ctx: Context,
        plan_id: str,
        name: str,
        retention_type: _RETENTION_TYPE,
        retention_days: int | None,
        retention_versions: int | None,
        schedule_frequency: _FREQUENCY,
        schedule_time: str | None,
        weekdays: Annotated[list[WeekDayLiteral], JSON_LIST_VALIDATOR] | None,
        description: str,
        is_immutable: bool,
        gfs_daily_versions: int | None = None,
        gfs_weekly_versions: int | None = None,
        gfs_monthly_versions: int | None = None,
        gfs_yearly_versions: int | None = None,
        run_schedule_by_controller_time: bool = False,
        vm_enable_app_aware_bkp: bool | None = None,
        vm_enable_verification: bool | None = None,
        vm_verification_video_duration_seconds: int | None = None,
        vm_enable_datastore_usage_detection: bool | None = None,
        vm_datastore_min_free_space_percent: int | None = None,
        pc_shutdown_after_backup: bool | None = None,
        pc_wake_for_backup: bool | None = None,
        pc_prevent_sleep_during_backup: bool | None = None,
        ps_enable_app_aware_bkp: bool | None = None,
        ps_enable_verification: bool | None = None,
        ps_verification_video_duration_seconds: int | None = None,
        ps_shutdown_after_backup: bool | None = None,
        ps_wake_for_backup: bool | None = None,
        ps_prevent_sleep_during_backup: bool | None = None,
        db_action_on_error: _DB_ACTION_ON_ERROR | None = None,
        db_mssql_log_setting: _MSSQL_LOG_SETTING | None = None,
        db_oracle_log_setting: _ORACLE_LOG_SETTING | None = None,
        backup_window_enabled: bool = False,
        backup_window_allowed_hours: str | None = None,
        tasks_json: str | None = None,
        backup_copy_destination_type: Literal["backup_server", "remote_storage"] | None = None,
        backup_copy_destination_id: str | None = None,
        backup_copy_retention_type: _RETENTION_TYPE | None = None,
        backup_copy_retention_days: int | None = None,
        backup_copy_retention_versions: int | None = None,
        backup_copy_gfs_daily_versions: int | None = None,
        backup_copy_gfs_weekly_versions: int | None = None,
        backup_copy_gfs_monthly_versions: int | None = None,
        backup_copy_gfs_yearly_versions: int | None = None,
        backup_copy_schedule_frequency: _BACKUP_COPY_FREQUENCY | None = None,
        backup_copy_schedule_time: str | None = None,
        backup_copy_weekdays: Annotated[list[WeekDayLiteral], JSON_LIST_VALIDATOR] | None = None,
    ) -> str:
        # Snapshot as the first statement so locals() holds only this function's own
        # parameters (see _build_machine_plan_request for the matching signature).
        build_kwargs = {k: v for k, v in locals().items() if k not in ("ctx", "plan_id")}
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            request = await _build_machine_plan_request(apm, **build_kwargs)
            updated = await apm.machine.plans.update(plan_id, request)
            return updated.to_dict()

        return await run_audited_tool(
            _update(),
            action="update_machine_protection_plan",
            params={"plan_id": plan_id},
        )
