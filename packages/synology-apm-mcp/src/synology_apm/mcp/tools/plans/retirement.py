"""Retirement plan list/get/create/update/delete tools."""
from __future__ import annotations

from typing import Any

from fastmcp import Context

from synology_apm.mcp._helpers import LIST_RESULT_SUFFIX, clamp_limit, get_tool, list_tool
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import DESTRUCTIVE_PREVIEW_SUFFIX, run_audited_tool
from synology_apm.mcp.tools.plans.common import register_delete_plan_tool
from synology_apm.sdk import APMClient, RetirementPlanCreateRequest


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register retirement plan tools onto server."""

    @registrar.tool(description=f"List retirement plans. Filter by name. {LIST_RESULT_SUFFIX}")
    async def list_retirement_plans(
        ctx: Context,
        name_contains: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await list_tool(
            apm.retirement_plans.list(name_contains=name_contains, limit=clamp_limit(limit), offset=offset),
            lambda x: x.to_dict(),
            offset=offset,
        )

    @registrar.tool(description="Get a single retirement plan by ID. Use list_retirement_plans (optionally with name_contains) to find the ID.")
    async def get_retirement_plan(
        ctx: Context,
        plan_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.retirement_plans.get(plan_id), lambda x: x.to_dict())

    @registrar.tool("admin", description="Create a retirement plan. retention_days=None keeps versions indefinitely.")
    async def create_retirement_plan(
        ctx: Context,
        name: str,
        retention_days: int | None = None,
        keep_latest_version: bool = True,
        description: str = "",
        run_schedule_by_controller_time: bool = False,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _create() -> dict[str, Any]:
            plan = await apm.retirement_plans.create(
                RetirementPlanCreateRequest(
                    name=name,
                    retention_days=retention_days,
                    keep_latest_version=keep_latest_version,
                    description=description,
                    run_schedule_by_controller_time=run_schedule_by_controller_time,
                )
            )
            return plan.to_dict()

        return await run_audited_tool(_create(), action="create_retirement_plan", params={"name": name})

    @registrar.tool("admin", description=(
        "Update an existing retirement plan by ID. All fields except run_schedule_by_controller_time "
        "(defaults to false, resetting it if omitted) must be supplied explicitly — call "
        "get_retirement_plan first and resupply its current values for anything you don't intend to "
        "change."
    ))
    async def update_retirement_plan(
        ctx: Context,
        plan_id: str,
        name: str,
        retention_days: int | None,
        keep_latest_version: bool,
        description: str,
        run_schedule_by_controller_time: bool = False,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            updated = await apm.retirement_plans.update(
                plan_id,
                RetirementPlanCreateRequest(
                    name=name,
                    retention_days=retention_days,
                    keep_latest_version=keep_latest_version,
                    description=description,
                    run_schedule_by_controller_time=run_schedule_by_controller_time,
                ),
            )
            return updated.to_dict()

        return await run_audited_tool(_update(), action="update_retirement_plan", params={"plan_id": plan_id})

    register_delete_plan_tool(
        registrar,
        name="delete_retirement_plan",
        description=f"Delete a retirement plan by ID. {DESTRUCTIVE_PREVIEW_SUFFIX}",
        warning="This permanently deletes the retirement plan. Pass confirm=true to proceed.",
        collection_fn=lambda apm: apm.retirement_plans,
    )
