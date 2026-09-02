"""Non-closure business logic shared by machine, M365, and GWS workload tools.

Extracted from register_workload_tools() (see _workload.py) so the resolve and
mutation logic is directly unit-testable rather than living as closures over a
category flag. _workload.py owns the FastMCP registration/signature layer only;
every function here is parameterized explicitly by WorkloadCategory instead.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from synology_apm.mcp._helpers import ToolResult, VersionResolution, list_result, parse_dt_optional
from synology_apm.mcp._security import destructive_tool
from synology_apm.sdk import APMClient


@dataclass(frozen=True)
class WorkloadCategory:
    """Parameterizes the shared workload tool logic for machine, M365, and GWS workloads.

    needs_saas_scope: True for M365/GWS, whose workloads are scoped by a
    tenant/domain id (a "SaaS id") and a per-category workload_type, in addition
    to workload_id + namespace; False for machine, which needs only the latter two.
    workload_type_enum: the SDK enum class used to convert the tool's raw
    workload_type string to a real enum member (M365WorkloadType/GWSWorkloadType);
    None for machine, which has no saas-scoped workload_type parameter.
    scope_param_name: the audit-log key mutation_params() uses for the tenant/domain
    value ("tenant_id" for M365, "domain" for GWS) — irrelevant when
    needs_saas_scope is False.
    """

    needs_saas_scope: bool
    name_prefix: str
    collection_fn: Callable[[APMClient], Any]
    serializer: Callable[[Any], dict[str, Any]]
    workload_type_enum: type[Enum] | None = None
    scope_param_name: str = "tenant_id"


async def resolve_workload(
    cat: WorkloadCategory,
    apm: APMClient,
    *,
    workload_id: str,
    namespace: str,
    saas_id: str | None = None,
    workload_type: str | None = None,
) -> Any:
    """Resolve a workload for any category; saas_id/workload_type are only
    used (and only supplied by callers) when cat.needs_saas_scope is True."""
    if cat.needs_saas_scope:
        if saas_id is None:
            raise ValueError("saas_id is required for saas-scoped workload resolution.")
        if workload_type is None:
            raise ValueError("workload_type is required for saas-scoped workload resolution.")
        assert cat.workload_type_enum is not None
        return await cat.collection_fn(apm).get(
            workload_id, namespace, saas_id, cat.workload_type_enum(workload_type)
        )
    return await cat.collection_fn(apm).get(workload_id, namespace)


async def resolve_version(
    cat: WorkloadCategory,
    apm: APMClient,
    *,
    version_id: str | None,
    workload_id: str,
    namespace: str,
    saas_id: str | None = None,
    workload_type: str | None = None,
) -> VersionResolution[Any]:
    """Resolve a workload and one of its versions for any category.

    Both halves are typed Any/generic-Any because the workload's concrete type
    varies by category and none of this module's callers use it (all discard
    it via `_, version = ...`).
    """
    workload = await resolve_workload(
        cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type
    )
    collection = cat.collection_fn(apm)
    version = await collection.get_version(workload, version_id) if version_id else await collection.get_latest_version(workload)
    return VersionResolution(workload, version)


def mutation_params(
    cat: WorkloadCategory, workload_id: str, saas_id: str | None, workload_type: str | None, **extra: Any
) -> dict[str, Any]:
    """Build the audit-log params dict shared by mutation tools: workload_id plus
    tenant_id/domain (keyed by cat.scope_param_name) and workload_type when this
    category is saas-scoped, plus any tool-specific extras."""
    params: dict[str, Any] = {"workload_id": workload_id, **extra}
    if cat.needs_saas_scope:
        params[cat.scope_param_name] = saas_id
        params["workload_type"] = workload_type
    return params


async def backup_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str,
    saas_id: str | None = None, workload_type: str | None = None,
) -> Any:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type)
    return await cat.collection_fn(apm).backup_now(workload)


async def cancel_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str,
    saas_id: str | None = None, workload_type: str | None = None,
) -> Any:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type)
    return await cat.collection_fn(apm).cancel_backup(workload)


async def list_versions_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, since: str | None, until: str | None,
    limit: int, offset: int, saas_id: str | None = None, workload_type: str | None = None,
) -> dict[str, Any]:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type)
    return await list_result(
        cat.collection_fn(apm).list_versions(
            workload,
            since=parse_dt_optional(since),
            until=parse_dt_optional(until),
            limit=limit,
            offset=offset,
        ),
        lambda x: x.to_dict(),
        offset=offset,
    )


async def get_version_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, version_id: str | None,
    saas_id: str | None = None, workload_type: str | None = None,
) -> dict[str, Any]:
    _, version = await resolve_version(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type, version_id=version_id)
    return version.to_dict()


async def lock_version_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, version_id: str,
    saas_id: str | None = None, workload_type: str | None = None,
) -> Any:
    _, version = await resolve_version(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type, version_id=version_id)
    return await cat.collection_fn(apm).lock_version(version)


async def unlock_version_body(
    cat: WorkloadCategory, apm: APMClient, *, workload_id: str, namespace: str, version_id: str,
    saas_id: str | None = None, workload_type: str | None = None,
) -> Any:
    _, version = await resolve_version(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type, version_id=version_id)
    return await cat.collection_fn(apm).unlock_version(version)


async def change_plan_body(
    cat: WorkloadCategory, apm: APMClient, *, plan_id: str, workload_id: str, namespace: str,
    saas_id: str | None = None, workload_type: str | None = None,
) -> Any:
    workload = await resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type)
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
    saas_id: str | None = None,
    workload_type: str | None = None,
) -> ToolResult:
    """Shared resolve-then-preview-or-execute helper for retire/delete workload
    tools, which differ only in their action verb, warning text, and execute_fn."""
    return await destructive_tool(
        confirm=confirm,
        action=f"{action_verb}_{cat.name_prefix}_workload",
        warning=warning,
        resolve_coro=resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=saas_id, workload_type=workload_type),
        preview_target_fn=lambda w: {"name": w.name, "workload_id": w.workload_id},
        execute_fn=execute_fn,
        params=mutation_params(cat, workload_id, saas_id, workload_type, confirm=confirm),
    )
