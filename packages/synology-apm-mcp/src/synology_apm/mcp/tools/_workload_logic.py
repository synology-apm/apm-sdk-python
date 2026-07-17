"""Non-closure business logic shared by machine and M365 workload tools.

Extracted from register_workload_tools() (see _workload.py) so the resolve and
mutation logic is directly unit-testable rather than living as closures over
is_m365. _workload.py owns the FastMCP registration/signature layer only; every
function here is parameterized explicitly by WorkloadCategory instead.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from synology_apm.mcp._helpers import (
    clamp_limit,
    list_result,
    parse_dt_optional,
    resolve_m365_version,
    resolve_machine_version,
)
from synology_apm.mcp._security import destructive_tool
from synology_apm.sdk import APMClient, M365WorkloadType


@dataclass(frozen=True)
class WorkloadCategory:
    """Parameterizes the shared workload tool logic for machine vs. M365 workloads."""

    is_m365: bool
    name_prefix: str
    collection_fn: Callable[[APMClient], Any]
    serializer: Callable[[Any], dict[str, Any]]


async def resolve_workload(
    cat: WorkloadCategory,
    apm: APMClient,
    *,
    workload_id: str,
    namespace: str,
    tenant_id: str | None = None,
    workload_type: str | None = None,
) -> Any:
    """Resolve a workload for either category; tenant_id/workload_type are only
    used (and only supplied by callers) when cat.is_m365 is True."""
    if cat.is_m365:
        if tenant_id is None:
            raise ValueError("tenant_id is required for M365 workload resolution.")
        if workload_type is None:
            raise ValueError("workload_type is required for M365 workload resolution.")
        return await apm.m365.workloads.get(
            workload_id, namespace, tenant_id=tenant_id, workload_type=M365WorkloadType(workload_type)
        )
    return await apm.machine.workloads.get(workload_id, namespace)


async def resolve_version(
    cat: WorkloadCategory,
    apm: APMClient,
    *,
    version_id: str | None,
    workload_id: str,
    namespace: str,
    tenant_id: str | None = None,
    workload_type: str | None = None,
) -> tuple[Any, Any]:
    """Resolve a workload and one of its versions for either category."""
    if cat.is_m365:
        if tenant_id is None:
            raise ValueError("tenant_id is required for M365 version resolution.")
        if workload_type is None:
            raise ValueError("workload_type is required for M365 version resolution.")
        return await resolve_m365_version(
            apm, workload_id=workload_id, namespace=namespace,
            tenant_id=tenant_id, workload_type=workload_type, version_id=version_id,
        )
    return await resolve_machine_version(apm, workload_id=workload_id, namespace=namespace, version_id=version_id)


def mutation_params(
    cat: WorkloadCategory, workload_id: str, tenant_id: str | None, workload_type: str | None, **extra: Any
) -> dict[str, Any]:
    """Build the audit-log params dict shared by mutation tools: workload_id plus
    tenant_id/workload_type when this is the M365 variant, plus any tool-specific extras."""
    params: dict[str, Any] = {"workload_id": workload_id, **extra}
    if cat.is_m365:
        params["tenant_id"] = tenant_id
        params["workload_type"] = workload_type
    return params


async def backup_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str,
    tenant_id: str | None = None, workload_type: str | None = None,
) -> Any:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
    return await cat.collection_fn(apm).backup_now(workload)


async def cancel_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str,
    tenant_id: str | None = None, workload_type: str | None = None,
) -> Any:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
    return await cat.collection_fn(apm).cancel_backup(workload)


async def list_versions_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, since: str | None, until: str | None,
    limit: int, offset: int, tenant_id: str | None = None, workload_type: str | None = None,
) -> dict[str, Any]:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
    return await list_result(
        cat.collection_fn(apm).list_versions(
            workload,
            since=parse_dt_optional(since),
            until=parse_dt_optional(until),
            limit=clamp_limit(limit),
            offset=offset,
        ),
        lambda x: x.to_dict(),
        offset=offset,
    )


async def get_version_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, version_id: str | None,
    tenant_id: str | None = None, workload_type: str | None = None,
) -> dict[str, Any]:
    _, version = await resolve_version(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type, version_id=version_id)
    return version.to_dict()  # type: ignore[no-any-return]


async def lock_version_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, version_id: str,
    tenant_id: str | None = None, workload_type: str | None = None,
) -> Any:
    _, version = await resolve_version(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type, version_id=version_id)
    return await cat.collection_fn(apm).lock_version(version)


async def unlock_version_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, version_id: str,
    tenant_id: str | None = None, workload_type: str | None = None,
) -> Any:
    _, version = await resolve_version(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type, version_id=version_id)
    return await cat.collection_fn(apm).unlock_version(version)


async def change_plan_body(
    cat: WorkloadCategory, apm: APMClient, *, plan_id: str, workload_id: str, namespace: str,
    tenant_id: str | None = None, workload_type: str | None = None,
) -> Any:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
    plan = (
        await apm.retirement_plans.get(plan_id)
        if workload.is_retired
        else await apm.plans.get(plan_id)
    )
    return await cat.collection_fn(apm).change_plan(workload, plan)


async def retire_workload(apm: APMClient, workload: Any, plan_id: str, collection_fn: Callable[[APMClient], Any]) -> dict[str, Any]:
    plan = await apm.retirement_plans.get(plan_id)
    await collection_fn(apm).retire(workload, plan)
    return {"ok": True, "workload_id": workload.workload_id, "retirement_plan_id": plan.plan_id}


async def destructive_workload_mutation(
    cat: WorkloadCategory,
    apm: APMClient,
    *,
    action_verb: str,
    warning: str,
    workload_id: str,
    namespace: str,
    confirm: bool,
    execute_fn: Callable[[Any], Awaitable[Any]],
    tenant_id: str | None = None,
    workload_type: str | None = None,
) -> str:
    """Shared resolve-then-preview-or-execute helper for retire/delete workload
    tools, which differ only in their action verb, warning text, and execute_fn."""
    return await destructive_tool(
        confirm=confirm,
        action=f"{action_verb}_{cat.name_prefix}_workload",
        warning=warning,
        resolve_coro=resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type),
        preview_target_fn=lambda w: {"name": w.name, "workload_id": w.workload_id},
        execute_fn=execute_fn,
        params=mutation_params(cat, workload_id, tenant_id, workload_type, confirm=confirm),
    )
