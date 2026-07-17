"""Cross-category protection plan tools (list/get/delete span machine + M365)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastmcp import Context

from synology_apm.mcp._helpers import LIST_RESULT_SUFFIX, clamp_limit, get_tool, list_tool
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import DESTRUCTIVE_PREVIEW_SUFFIX, destructive_tool
from synology_apm.sdk import APMClient, WorkloadCategory

_CATEGORY = Literal["machine", "m365"]


def register_delete_plan_tool(
    registrar: ToolRegistrar,
    *,
    name: str,
    description: str,
    warning: str,
    collection_fn: Callable[[APMClient], Any],
) -> None:
    """Register a delete_*_plan tool: resolve by plan_id, then preview-or-execute.

    Shared by protection/retirement/tiering plan deletion, which are otherwise
    identical apart from which SDK collection is used and the warning text.
    """

    async def _delete(ctx: Context, plan_id: str, confirm: bool = False) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await destructive_tool(
            confirm=confirm,
            action=name,
            warning=warning,
            resolve_coro=collection_fn(apm).get(plan_id),
            preview_target_fn=lambda p: {"name": p.name, "plan_id": p.plan_id},
            execute_fn=lambda p: collection_fn(apm).delete(p),
            params={"plan_id": plan_id, "confirm": confirm},
        )

    registrar.tool("admin", name=name, description=description)(_delete)


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register cross-category protection plan tools onto server."""

    @registrar.tool(description=f"List protection plans. Filter by category (machine/m365) or name. {LIST_RESULT_SUFFIX}")
    async def list_protection_plans(
        ctx: Context,
        category: _CATEGORY | None = None,
        name_contains: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        cat = WorkloadCategory(category) if category else None
        return await list_tool(
            apm.plans.list(category=cat, name_contains=name_contains, limit=clamp_limit(limit), offset=offset),
            lambda x: x.to_dict(),
            offset=offset,
        )

    @registrar.tool(description="Get a single protection plan by ID. Use list_protection_plans (optionally with name_contains) to find the ID.")
    async def get_protection_plan(
        ctx: Context,
        plan_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.plans.get(plan_id), lambda x: x.to_dict())

    register_delete_plan_tool(
        registrar,
        name="delete_protection_plan",
        description=f"Delete a protection plan by ID. {DESTRUCTIVE_PREVIEW_SUFFIX}",
        warning="This permanently deletes the protection plan. Workloads assigned to it will need a new plan. Pass confirm=true to proceed.",
        collection_fn=lambda apm: apm.plans,
    )
