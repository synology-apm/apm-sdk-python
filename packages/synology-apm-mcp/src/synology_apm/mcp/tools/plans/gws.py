"""GWS protection plan create/update tools."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import Context

from synology_apm.mcp._enums import WeekDayLiteral
from synology_apm.mcp._helpers import JSON_LIST_VALIDATOR, ToolResult
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import run_audited_tool
from synology_apm.mcp.tools.plans._builders_common import (
    _BACKUP_COPY_FREQUENCY,
    _FREQUENCY,
    _RETENTION_TYPE,
    _build_gws_plan_request,
    _create_plan_desc,
    _update_plan_desc,
)
from synology_apm.sdk import APMClient


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register GWS protection plan create/update tools onto server."""

    @registrar.tool("admin", description=_create_plan_desc("a GWS"))
    async def create_gws_protection_plan(
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
    ) -> ToolResult:
        # Snapshot as the first statement so locals() holds only this function's own
        # parameters (see _build_gws_plan_request for the matching signature).
        build_kwargs = {k: v for k, v in locals().items() if k != "ctx"}
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _create() -> dict[str, Any]:
            request = await _build_gws_plan_request(apm, **build_kwargs)
            plan = await apm.gws.plans.create(request)
            return plan.to_dict()

        return await run_audited_tool(
            _create(),
            action="create_gws_protection_plan",
            params={"name": name},
        )

    @registrar.tool("admin", description=_update_plan_desc("GWS"))
    async def update_gws_protection_plan(
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
    ) -> ToolResult:
        # Snapshot as the first statement so locals() holds only this function's own
        # parameters (see _build_gws_plan_request for the matching signature).
        build_kwargs = {k: v for k, v in locals().items() if k not in ("ctx", "plan_id")}
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            request = await _build_gws_plan_request(apm, **build_kwargs)
            updated = await apm.gws.plans.update(plan_id, request)
            return updated.to_dict()

        return await run_audited_tool(
            _update(),
            action="update_gws_protection_plan",
            params={"plan_id": plan_id},
        )
