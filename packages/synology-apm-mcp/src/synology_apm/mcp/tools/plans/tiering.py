"""Tiering plan list/get/create/update/delete tools."""
from __future__ import annotations

from datetime import time
from typing import Any

from fastmcp import Context

from synology_apm.mcp._helpers import LIST_RESULT_SUFFIX, clamp_limit, get_tool, list_tool
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import DESTRUCTIVE_PREVIEW_SUFFIX, run_audited_tool
from synology_apm.mcp.tools.plans._builders_common import _parse_required_time, _parse_time
from synology_apm.mcp.tools.plans.common import register_delete_plan_tool
from synology_apm.sdk import APMClient, TieringPlanCreateRequest


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register tiering plan tools onto server."""

    @registrar.tool(description=f"List tiering plans. Filter by name. {LIST_RESULT_SUFFIX}")
    async def list_tiering_plans(
        ctx: Context,
        name_contains: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await list_tool(
            apm.tiering_plans.list(name_contains=name_contains, limit=clamp_limit(limit), offset=offset),
            lambda x: x.to_dict(),
            offset=offset,
        )

    @registrar.tool(description="Get a single tiering plan by ID. Use list_tiering_plans (optionally with name_contains) to find the ID.")
    async def get_tiering_plan(
        ctx: Context,
        plan_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.tiering_plans.get(plan_id), lambda x: x.to_dict())

    @registrar.tool("admin", description="Create a tiering plan. destination_storage_id identifies the remote storage (use list_remote_storages to find it). tiering_after_days: data older than this is tiered. daily_check_time: HH:MM (default 20:00).")
    async def create_tiering_plan(
        ctx: Context,
        name: str,
        tiering_after_days: int,
        destination_storage_id: str,
        daily_check_time: str = "20:00",
        description: str = "",
        run_schedule_by_controller_time: bool = False,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _create() -> dict[str, Any]:
            storage = await apm.remote_storages.get(destination_storage_id)
            plan = await apm.tiering_plans.create(
                TieringPlanCreateRequest(
                    name=name,
                    tiering_after_days=tiering_after_days,
                    destination=storage,
                    daily_check_time=_parse_time(daily_check_time) or time(20, 0),
                    description=description,
                    run_schedule_by_controller_time=run_schedule_by_controller_time,
                )
            )
            return plan.to_dict()

        return await run_audited_tool(_create(), action="create_tiering_plan", params={"name": name})

    @registrar.tool("admin", description=(
        "Update an existing tiering plan by ID. All fields except run_schedule_by_controller_time "
        "(defaults to false, resetting it if omitted) must be supplied explicitly — call "
        "get_tiering_plan first and resupply its current values for anything you don't intend to change."
    ))
    async def update_tiering_plan(
        ctx: Context,
        plan_id: str,
        name: str,
        tiering_after_days: int,
        destination_storage_id: str,
        daily_check_time: str,
        description: str,
        run_schedule_by_controller_time: bool = False,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            storage = await apm.remote_storages.get(destination_storage_id)
            updated = await apm.tiering_plans.update(
                plan_id,
                TieringPlanCreateRequest(
                    name=name,
                    tiering_after_days=tiering_after_days,
                    destination=storage,
                    daily_check_time=_parse_required_time(daily_check_time),
                    description=description,
                    run_schedule_by_controller_time=run_schedule_by_controller_time,
                ),
            )
            return updated.to_dict()

        return await run_audited_tool(_update(), action="update_tiering_plan", params={"plan_id": plan_id})

    register_delete_plan_tool(
        registrar,
        name="delete_tiering_plan",
        description=f"Delete a tiering plan by ID. {DESTRUCTIVE_PREVIEW_SUFFIX}",
        warning="This permanently deletes the tiering plan. Pass confirm=true to proceed.",
        collection_fn=lambda apm: apm.tiering_plans,
    )
