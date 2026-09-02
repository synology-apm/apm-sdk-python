"""Shared workload tool registration for machine, M365, and GWS categories.

Machine, M365, and GWS workloads share ~11 tools with identical shapes (list,
get, backup, cancel, list_versions, get_version, lock_version, unlock_version,
retire, change_plan, delete). This module registers them for all three
categories via register_workload_tools(), parameterized by name_prefix/variant.

FastMCP generates JSON Schema from function type annotations, so each tool
needs a distinct definition per category (M365 adds tenant_id + workload_type;
GWS adds domain + workload_type) — that pairing is irreducible and kept
explicit below. The resolve/mutation business logic itself lives in
_workload_logic.py as plain, directly-testable functions parameterized by
WorkloadCategory, so only the FastMCP-facing signature/registration
boilerplate remains here.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Annotated, Any, Literal

from fastmcp import Context

from synology_apm.mcp._enums import (
    GWSWorkloadTypeLiteral,
    M365WorkloadTypeLiteral,
    MachineWorkloadTypeLiteral,
    VerifyStatusLiteral,
    WorkloadStatusLiteral,
)
from synology_apm.mcp._errors import run_tool
from synology_apm.mcp._helpers import (
    JSON_LIST_VALIDATOR,
    LIST_RESULT_SUFFIX,
    ToolResult,
    get_tool,
    list_tool,
    resolve_plan_filter,
    to_enum_list,
)
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import DESTRUCTIVE_PREVIEW_SUFFIX, run_audited_tool
from synology_apm.mcp.tools._workload_logic import (
    WorkloadCategory,
    backup_body,
    cancel_body,
    change_plan_body,
    destructive_workload_mutation,
    get_version_body,
    list_versions_body,
    lock_version_body,
    mutation_params,
    resolve_workload,
    retire_workload,
    unlock_version_body,
)
from synology_apm.sdk import (
    APMClient,
    GWSWorkloadType,
    M365WorkloadType,
    MachineWorkloadType,
    VerifyStatus,
    WorkloadStatus,
)

_ALREADY_RETIRED = "Fails if the workload is already retired."

_WORKLOAD_TYPE_ENUM: dict[str, type[Enum]] = {"m365": M365WorkloadType, "gws": GWSWorkloadType}


def register_workload_tools(  # pragma: no cover
    registrar: ToolRegistrar,
    *,
    name_prefix: str,
    variant: Literal["machine", "m365", "gws"],
    collection_fn: Callable[[APMClient], Any],
    serializer: Callable[[Any], dict[str, Any]],
) -> None:
    """Register list/get/backup/cancel/versions/lock/retire/change_plan/delete tools.

    name_prefix: "machine", "m365", or "gws". variant selects which FastMCP signature shape
    to register (see the module docstring above for why each needs its own definition).
    """
    cat = WorkloadCategory(
        needs_saas_scope=variant != "machine",
        name_prefix=name_prefix,
        collection_fn=collection_fn,
        serializer=serializer,
        workload_type_enum=_WORKLOAD_TYPE_ENUM.get(variant),
        scope_param_name="domain" if variant == "gws" else "tenant_id",
    )

    # ── list ─────────────────────────────────────────────────────────────────

    if variant == "machine":
        async def _list(
            ctx: Context,
            workload_types: Annotated[list[MachineWorkloadTypeLiteral], JSON_LIST_VALIDATOR] | None = None,
            namespaces: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
            is_retired: bool = False,
            keyword: str | None = None,
            plan_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
            hypervisor_id: str | None = None,
            status: Annotated[list[WorkloadStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
            verify_status: Annotated[list[VerifyStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            types = to_enum_list(MachineWorkloadType, workload_types)
            plans = await resolve_plan_filter(apm, plan_ids)
            return await list_tool(
                collection_fn(apm).list(
                    workload_types=types,
                    namespace=namespaces,
                    plan=plans,
                    is_retired=is_retired,
                    keyword=keyword,
                    hypervisor_id=hypervisor_id,
                    status=to_enum_list(WorkloadStatus, status),
                    verify_status=to_enum_list(VerifyStatus, verify_status),
                    limit=limit,
                    offset=offset,
                ),
                serializer,
                offset=offset,
            )
    elif variant == "m365":
        async def _list(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_type: M365WorkloadTypeLiteral = "exchange",
            tenant_id: str,
            namespaces: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
            is_retired: bool = False,
            keyword: str | None = None,
            plan_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
            status: Annotated[list[WorkloadStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            plans = await resolve_plan_filter(apm, plan_ids)
            return await list_tool(
                collection_fn(apm).list(
                    tenant_id,
                    workload_type=M365WorkloadType(workload_type),
                    namespace=namespaces,
                    plan=plans,
                    is_retired=is_retired,
                    keyword=keyword,
                    status=to_enum_list(WorkloadStatus, status),
                    limit=limit,
                    offset=offset,
                ),
                serializer,
                offset=offset,
            )
    else:  # gws
        async def _list(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_type: GWSWorkloadTypeLiteral = "mail",
            domain: str,
            namespaces: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
            is_retired: bool = False,
            keyword: str | None = None,
            plan_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
            status: Annotated[list[WorkloadStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            plans = await resolve_plan_filter(apm, plan_ids)
            return await list_tool(
                collection_fn(apm).list(
                    domain,
                    workload_type=GWSWorkloadType(workload_type),
                    namespace=namespaces,
                    plan=plans,
                    is_retired=is_retired,
                    keyword=keyword,
                    status=to_enum_list(WorkloadStatus, status),
                    limit=limit,
                    offset=offset,
                ),
                serializer,
                offset=offset,
            )

    if variant == "machine":
        desc = (
            "List machine workloads. Filter by workload_types (pc,ps,vm,fs), namespaces (repeatable), name, retired status, "
            "plan_ids (protection/retirement plan ids, OR logic), hypervisor_id (VM workloads only), status "
            "(queuing,backing_up,success,failed,partial,canceled,no_backups,deleting), or verify_status "
            "(verifying,success,failed,canceled,not_supported,not_enabled,partial,waiting; PS/VM workloads only). "
            f"{LIST_RESULT_SUFFIX}"
        )
    elif variant == "m365":
        desc = (
            "List M365 workloads of a given type for a tenant. workload_type: exchange, onedrive, chat, sharepoint, "
            "teams, group. Covers one workload_type per call; there is no all-types option — call once per type for a "
            "full tenant inventory. Filter by namespaces (repeatable), retired status, keyword, plan_ids (protection/retirement "
            "plan ids, OR logic), or status (queuing,backing_up,success,failed,partial,canceled,no_backups,deleting). "
            f"{LIST_RESULT_SUFFIX}"
        )
    else:
        desc = (
            "List GWS (Google Workspace) workloads of a given type for a domain. workload_type: drive, mail, "
            "contact, calendar, shared_drive. Covers one workload_type per call; there is no all-types option — "
            "call once per type for a full domain inventory. Filter by namespaces (repeatable), retired status, keyword, "
            "plan_ids (protection/retirement plan ids, OR logic), or status "
            "(queuing,backing_up,success,failed,partial,canceled,no_backups,deleting). "
            f"{LIST_RESULT_SUFFIX}"
        )
    registrar.tool(name=f"list_{name_prefix}_workloads", description=desc)(_list)

    # ── get ──────────────────────────────────────────────────────────────────

    if variant == "machine":
        async def _get(
            ctx: Context,
            workload_id: str,
            namespace: str,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await get_tool(
                resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace),
                serializer,
            )
    elif variant == "m365":
        async def _get(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await get_tool(
                resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=tenant_id, workload_type=workload_type),
                serializer,
            )
    else:  # gws
        async def _get(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await get_tool(
                resolve_workload(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=domain, workload_type=workload_type),
                serializer,
            )

    if variant == "machine":
        desc = "Get a single machine workload by ID and namespace. Use list_machine_workloads (optionally with keyword) to find them."
    elif variant == "m365":
        desc = "Get a single M365 workload by ID, namespace, tenant_id, and workload_type. Use list_m365_workloads (optionally with keyword) to find them."
    else:
        desc = "Get a single GWS workload by ID, namespace, domain, and workload_type. Use list_gws_workloads (optionally with keyword) to find them."
    registrar.tool(name=f"get_{name_prefix}_workload", description=desc)(_get)

    # ── backup / cancel ──────────────────────────────────────────────────────

    if variant == "machine":
        async def _backup(
            ctx: Context,
            workload_id: str,
            namespace: str,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                backup_body(cat, apm, workload_id=workload_id, namespace=namespace),
                action=f"backup_{name_prefix}_workload",
                params=mutation_params(cat, workload_id, None, None),
            )

        async def _cancel(
            ctx: Context,
            workload_id: str,
            namespace: str,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                cancel_body(cat, apm, workload_id=workload_id, namespace=namespace),
                action=f"cancel_{name_prefix}_backup",
                params=mutation_params(cat, workload_id, None, None),
            )
    elif variant == "m365":
        async def _backup(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                backup_body(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=tenant_id, workload_type=workload_type),
                action=f"backup_{name_prefix}_workload",
                params=mutation_params(cat, workload_id, tenant_id, workload_type),
            )

        async def _cancel(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                cancel_body(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=tenant_id, workload_type=workload_type),
                action=f"cancel_{name_prefix}_backup",
                params=mutation_params(cat, workload_id, tenant_id, workload_type),
            )
    else:  # gws
        async def _backup(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                backup_body(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=domain, workload_type=workload_type),
                action=f"backup_{name_prefix}_workload",
                params=mutation_params(cat, workload_id, domain, workload_type),
            )

        async def _cancel(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                cancel_body(cat, apm, workload_id=workload_id, namespace=namespace, saas_id=domain, workload_type=workload_type),
                action=f"cancel_{name_prefix}_backup",
                params=mutation_params(cat, workload_id, domain, workload_type),
            )

    registrar.tool("operator", name=f"backup_{name_prefix}_workload", description=f"Trigger an immediate backup of a {name_prefix} workload. {_ALREADY_RETIRED}")(_backup)
    registrar.tool("operator", name=f"cancel_{name_prefix}_backup", description=f"Cancel the currently running backup for a {name_prefix} workload. {_ALREADY_RETIRED}")(_cancel)

    # ── versions ─────────────────────────────────────────────────────────────

    if variant == "machine":
        async def _list_versions(
            ctx: Context,
            workload_id: str,
            namespace: str,
            since: str | None = None,
            until: str | None = None,
            limit: int = 20,
            offset: int = 0,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_tool(list_versions_body(cat, apm, workload_id=workload_id, namespace=namespace, since=since, until=until, limit=limit, offset=offset))
    elif variant == "m365":
        async def _list_versions(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
            since: str | None = None,
            until: str | None = None,
            limit: int = 20,
            offset: int = 0,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_tool(list_versions_body(
                cat, apm, workload_id=workload_id, namespace=namespace, since=since, until=until,
                limit=limit, offset=offset, saas_id=tenant_id, workload_type=workload_type,
            ))
    else:  # gws
        async def _list_versions(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
            since: str | None = None,
            until: str | None = None,
            limit: int = 20,
            offset: int = 0,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_tool(list_versions_body(
                cat, apm, workload_id=workload_id, namespace=namespace, since=since, until=until,
                limit=limit, offset=offset, saas_id=domain, workload_type=workload_type,
            ))

    registrar.tool(
        name=f"list_{name_prefix}_versions",
        description=(
            f"List backup versions for a {name_prefix} workload. Filter by time window (since/until as ISO 8601). "
            "Only completed/partial/failed/canceled versions are returned (an in-progress version is excluded); "
            f"results are ordered newest-first. {LIST_RESULT_SUFFIX}"
        ),
    )(_list_versions)

    if variant == "machine":
        async def _get_version(
            ctx: Context,
            workload_id: str,
            namespace: str,
            version_id: str | None = None,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_tool(get_version_body(cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id))
    elif variant == "m365":
        async def _get_version(  # type: ignore[misc]
            ctx: Context,
            version_id: str | None = None,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_tool(get_version_body(
                cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id,
                saas_id=tenant_id, workload_type=workload_type,
            ))
    else:  # gws
        async def _get_version(  # type: ignore[misc]
            ctx: Context,
            version_id: str | None = None,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_tool(get_version_body(
                cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id,
                saas_id=domain, workload_type=workload_type,
            ))

    registrar.tool(name=f"get_{name_prefix}_version", description=f"Get a backup version of a {name_prefix} workload by version_id, or the latest version if version_id is omitted.")(_get_version)

    # ── lock / unlock versions ────────────────────────────────────────────────

    if variant == "machine":
        async def _lock_version(
            ctx: Context,
            version_id: str,
            workload_id: str,
            namespace: str,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                lock_version_body(cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id),
                action=f"lock_{name_prefix}_version",
                params=mutation_params(cat, workload_id, None, None, version_id=version_id),
            )

        async def _unlock_version(
            ctx: Context,
            version_id: str,
            workload_id: str,
            namespace: str,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                unlock_version_body(cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id),
                action=f"unlock_{name_prefix}_version",
                params=mutation_params(cat, workload_id, None, None, version_id=version_id),
            )
    elif variant == "m365":
        async def _lock_version(  # type: ignore[misc]
            ctx: Context,
            version_id: str,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                lock_version_body(cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id, saas_id=tenant_id, workload_type=workload_type),
                action=f"lock_{name_prefix}_version",
                params=mutation_params(cat, workload_id, tenant_id, workload_type, version_id=version_id),
            )

        async def _unlock_version(  # type: ignore[misc]
            ctx: Context,
            version_id: str,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                unlock_version_body(cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id, saas_id=tenant_id, workload_type=workload_type),
                action=f"unlock_{name_prefix}_version",
                params=mutation_params(cat, workload_id, tenant_id, workload_type, version_id=version_id),
            )
    else:  # gws
        async def _lock_version(  # type: ignore[misc]
            ctx: Context,
            version_id: str,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                lock_version_body(cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id, saas_id=domain, workload_type=workload_type),
                action=f"lock_{name_prefix}_version",
                params=mutation_params(cat, workload_id, domain, workload_type, version_id=version_id),
            )

        async def _unlock_version(  # type: ignore[misc]
            ctx: Context,
            version_id: str,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                unlock_version_body(cat, apm, workload_id=workload_id, namespace=namespace, version_id=version_id, saas_id=domain, workload_type=workload_type),
                action=f"unlock_{name_prefix}_version",
                params=mutation_params(cat, workload_id, domain, workload_type, version_id=version_id),
            )

    registrar.tool("admin", name=f"lock_{name_prefix}_version", description=f"Lock a {name_prefix} backup version to prevent automatic deletion.")(_lock_version)
    registrar.tool("admin", name=f"unlock_{name_prefix}_version", description=f"Unlock a {name_prefix} backup version to allow automatic deletion.")(_unlock_version)

    # ── admin mutations ───────────────────────────────────────────────────────

    if variant == "machine":
        async def _change_plan(
            ctx: Context,
            plan_id: str,
            workload_id: str,
            namespace: str,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                change_plan_body(cat, apm, plan_id=plan_id, workload_id=workload_id, namespace=namespace),
                action=f"change_{name_prefix}_workload_plan",
                params=mutation_params(cat, workload_id, None, None, plan_id=plan_id),
            )
    elif variant == "m365":
        async def _change_plan(  # type: ignore[misc]
            ctx: Context,
            plan_id: str,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                change_plan_body(cat, apm, plan_id=plan_id, workload_id=workload_id, namespace=namespace, saas_id=tenant_id, workload_type=workload_type),
                action=f"change_{name_prefix}_workload_plan",
                params=mutation_params(cat, workload_id, tenant_id, workload_type, plan_id=plan_id),
            )
    else:  # gws
        async def _change_plan(  # type: ignore[misc]
            ctx: Context,
            plan_id: str,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_audited_tool(
                change_plan_body(cat, apm, plan_id=plan_id, workload_id=workload_id, namespace=namespace, saas_id=domain, workload_type=workload_type),
                action=f"change_{name_prefix}_workload_plan",
                params=mutation_params(cat, workload_id, domain, workload_type, plan_id=plan_id),
            )

    registrar.tool(
        "admin",
        name=f"change_{name_prefix}_workload_plan",
        description=(
            f"Reassign a {name_prefix} workload to a plan by ID: a protection plan if the workload is active, "
            "or a retirement plan if already retired. The plan's category must also match the workload's "
            "category."
        ),
    )(_change_plan)

    # retire (admin + confirm)
    async def _retire_via(
        apm: APMClient, *, retirement_plan_id: str, workload_id: str, namespace: str, confirm: bool,
        saas_id: str | None = None, workload_type: str | None = None,
    ) -> ToolResult:
        return await destructive_workload_mutation(
            cat, apm,
            action_verb="retire",
            warning="This moves the workload to a retirement plan and stops active protection. This is irreversible. Pass confirm=true to proceed.",
            workload_id=workload_id, namespace=namespace, confirm=confirm,
            execute_fn=lambda w: retire_workload(apm, w, retirement_plan_id, collection_fn),
            saas_id=saas_id, workload_type=workload_type,
        )

    if variant == "machine":
        async def _retire(
            ctx: Context,
            retirement_plan_id: str,
            workload_id: str,
            namespace: str,
            confirm: bool = False,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await _retire_via(apm, retirement_plan_id=retirement_plan_id, workload_id=workload_id, namespace=namespace, confirm=confirm)
    elif variant == "m365":
        async def _retire(  # type: ignore[misc]
            ctx: Context,
            retirement_plan_id: str,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
            confirm: bool = False,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await _retire_via(
                apm, retirement_plan_id=retirement_plan_id, workload_id=workload_id, namespace=namespace,
                confirm=confirm, saas_id=tenant_id, workload_type=workload_type,
            )
    else:  # gws
        async def _retire(  # type: ignore[misc]
            ctx: Context,
            retirement_plan_id: str,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
            confirm: bool = False,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await _retire_via(
                apm, retirement_plan_id=retirement_plan_id, workload_id=workload_id, namespace=namespace,
                confirm=confirm, saas_id=domain, workload_type=workload_type,
            )

    registrar.tool(
        "admin",
        name=f"retire_{name_prefix}_workload",
        description=(
            f"Move a {name_prefix} workload to a retirement plan. {_ALREADY_RETIRED} "
            f"This is irreversible. {DESTRUCTIVE_PREVIEW_SUFFIX}"
        ),
    )(_retire)

    # delete (admin + confirm)
    async def _delete_via(
        apm: APMClient, *, workload_id: str, namespace: str, confirm: bool,
        saas_id: str | None = None, workload_type: str | None = None,
    ) -> ToolResult:
        return await destructive_workload_mutation(
            cat, apm,
            action_verb="delete",
            warning="This permanently removes the workload and all its backup data. Pass confirm=true to proceed.",
            workload_id=workload_id, namespace=namespace, confirm=confirm,
            execute_fn=lambda w: collection_fn(apm).delete(w),
            saas_id=saas_id, workload_type=workload_type,
        )

    if variant == "machine":
        async def _delete(
            ctx: Context,
            workload_id: str,
            namespace: str,
            confirm: bool = False,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await _delete_via(apm, workload_id=workload_id, namespace=namespace, confirm=confirm)
    elif variant == "m365":
        async def _delete(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            tenant_id: str,
            workload_type: M365WorkloadTypeLiteral,
            confirm: bool = False,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await _delete_via(
                apm, workload_id=workload_id, namespace=namespace, confirm=confirm,
                saas_id=tenant_id, workload_type=workload_type,
            )
    else:  # gws
        async def _delete(  # type: ignore[misc]
            ctx: Context,
            *,
            workload_id: str,
            namespace: str,
            domain: str,
            workload_type: GWSWorkloadTypeLiteral,
            confirm: bool = False,
        ) -> ToolResult:
            apm: APMClient = ctx.lifespan_context["apm"]
            return await _delete_via(
                apm, workload_id=workload_id, namespace=namespace, confirm=confirm,
                saas_id=domain, workload_type=workload_type,
            )

    delete_desc = f"Permanently delete a {name_prefix} workload and all its backup data. {DESTRUCTIVE_PREVIEW_SUFFIX}"
    registrar.tool("admin", name=f"delete_{name_prefix}_workload", description=delete_desc)(_delete)
