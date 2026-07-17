"""Backup and restore activity tools."""
from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context

from synology_apm.mcp._enums import (
    BackupActivityStatusLiteral,
    M365WorkloadTypeLiteral,
    MachineWorkloadTypeLiteral,
    RestoreActivityStatusLiteral,
)
from synology_apm.mcp._helpers import (
    JSON_LIST_VALIDATOR,
    LIST_RESULT_SUFFIX,
    clamp_limit,
    get_tool,
    list_tool,
    parse_dt_optional,
    to_enum_list,
)
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import run_audited_tool
from synology_apm.sdk import (
    APMClient,
    BackupActivityStatus,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    MachineWorkloadType,
    RestoreActivityStatus,
    Workload,
)


async def _cancel_activity(collection: Any, activity_id: str, action: str) -> str:
    """Resolve an activity by id, cancel it, and return the standard audited result.

    Shared by cancel_backup_activity/cancel_restore_activity, which are identical
    apart from which apm.activities.* sub-collection they act on.
    """

    async def _cancel() -> dict[str, Any]:
        activity = await collection.get(activity_id)
        await collection.cancel(activity)
        return {"ok": True, "activity_id": activity_id}

    return await run_audited_tool(_cancel(), action=action, params={"activity_id": activity_id})


async def _resolve_activity_workload(
    apm: APMClient,
    *,
    workload_id: str | None,
    workload_namespace: str | None,
    tenant_id: str | None,
    workload_type: str | None,
) -> Workload | None:
    """Resolve the optional single-workload scope shared by backup/restore activity list()."""
    if not workload_id:
        return None
    if not workload_namespace:
        raise ValueError("workload_namespace is required when workload_id is given.")
    if tenant_id:
        if not workload_type:
            raise ValueError("workload_type is required when tenant_id is given (M365 workload).")
        m365_workload: M365Workload = await apm.m365.workloads.get(
            workload_id, workload_namespace, tenant_id=tenant_id, workload_type=M365WorkloadType(workload_type)
        )
        return m365_workload
    machine_workload: MachineWorkload = await apm.machine.workloads.get(workload_id, workload_namespace)
    return machine_workload


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register all activity tools onto server."""

    @registrar.tool(description=(
        "List backup activities. Filter by status (queuing/backing_up/canceling/success/failed/partial/canceled), "
        "machine types (pc,ps,vm,fs), M365 types (exchange,onedrive,chat,sharepoint,teams,group), backup-server "
        "namespaces (see list_backup_servers), a single workload (workload_id + workload_namespace, plus "
        "tenant_id + workload_type — same M365 type options — for M365), a time window (since/until as ISO 8601), "
        "or keyword. machine_types and m365_types are mutually exclusive — use one or the other. history=true "
        f"includes completed activities. {LIST_RESULT_SUFFIX}"
    ))
    async def list_backup_activities(
        ctx: Context,
        status: Annotated[list[BackupActivityStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
        machine_types: Annotated[list[MachineWorkloadTypeLiteral], JSON_LIST_VALIDATOR] | None = None,
        m365_types: Annotated[list[M365WorkloadTypeLiteral], JSON_LIST_VALIDATOR] | None = None,
        namespaces: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        workload_id: str | None = None,
        workload_namespace: str | None = None,
        tenant_id: str | None = None,
        workload_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        keyword: str | None = None,
        history: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        if machine_types and m365_types:
            raise ValueError("machine_types and m365_types are mutually exclusive — pass one or the other, not both.")

        status_filter = to_enum_list(BackupActivityStatus, status)
        machine_filter = to_enum_list(MachineWorkloadType, machine_types)
        m365_filter = to_enum_list(M365WorkloadType, m365_types)
        namespace_filter = namespaces if namespaces else None
        workload = await _resolve_activity_workload(
            apm, workload_id=workload_id, workload_namespace=workload_namespace, tenant_id=tenant_id, workload_type=workload_type
        )

        return await list_tool(
            apm.activities.backup.list(
                status=status_filter,
                machine_types=machine_filter,
                m365_types=m365_filter,
                namespace=namespace_filter,
                workload=workload,
                since=parse_dt_optional(since),
                until=parse_dt_optional(until),
                keyword=keyword,
                history=history,
                limit=clamp_limit(limit),
                offset=offset,
            ),
            lambda x: x.to_dict(),
            offset=offset,
        )

    @registrar.tool(description="Get a single backup activity by activity_id, including log entries. Use list_backup_activities to find the ID.")
    async def get_backup_activity(ctx: Context, activity_id: str) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.activities.backup.get(activity_id), lambda x: x.to_dict())

    @registrar.tool(description=(
        "List restore activities. Filter by status (preparing/restoring/canceling/ready_for_migrate/"
        "migrate_vm_manually/migrating/success/failed/partial/canceled), a single workload (workload_id + "
        "workload_namespace, plus tenant_id + workload_type — exchange/onedrive/chat/sharepoint/teams/group — "
        "for M365), a time window (since/until as ISO 8601), or keyword. history=true includes completed "
        f"restores. {LIST_RESULT_SUFFIX}"
    ))
    async def list_restore_activities(
        ctx: Context,
        status: Annotated[list[RestoreActivityStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
        workload_id: str | None = None,
        workload_namespace: str | None = None,
        tenant_id: str | None = None,
        workload_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        keyword: str | None = None,
        history: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        status_filter = to_enum_list(RestoreActivityStatus, status)
        workload = await _resolve_activity_workload(
            apm, workload_id=workload_id, workload_namespace=workload_namespace, tenant_id=tenant_id, workload_type=workload_type
        )

        return await list_tool(
            apm.activities.restore.list(
                status=status_filter,
                workload=workload,
                since=parse_dt_optional(since),
                until=parse_dt_optional(until),
                keyword=keyword,
                history=history,
                limit=clamp_limit(limit),
                offset=offset,
            ),
            lambda x: x.to_dict(),
            offset=offset,
        )

    @registrar.tool(description="Get a single restore activity by activity_id, including log entries. Use list_restore_activities to find the ID.")
    async def get_restore_activity(ctx: Context, activity_id: str) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.activities.restore.get(activity_id), lambda x: x.to_dict())

    @registrar.tool("operator", description="Cancel a running backup activity by activity_id.")
    async def cancel_backup_activity(ctx: Context, activity_id: str) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _cancel_activity(apm.activities.backup, activity_id, "cancel_backup_activity")

    @registrar.tool("operator", description="Cancel a running restore activity by activity_id.")
    async def cancel_restore_activity(ctx: Context, activity_id: str) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _cancel_activity(apm.activities.restore, activity_id, "cancel_restore_activity")
