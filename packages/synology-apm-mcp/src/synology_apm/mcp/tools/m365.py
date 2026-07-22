"""M365 workload tools: shared workload tools + exports + auto-backup rules + tenants."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import Context

from synology_apm.mcp._enums import M365WorkloadTypeLiteral
from synology_apm.mcp._errors import run_tool
from synology_apm.mcp._helpers import (
    JSON_LIST_VALIDATOR,
    LIST_RESULT_SUFFIX,
    clamp_limit,
    get_tool,
    list_result,
    list_tool,
    resolve_export_activity,
)
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import DESTRUCTIVE_PREVIEW_SUFFIX, destructive_tool, run_audited_tool
from synology_apm.mcp.tools._workload import register_workload_tools
from synology_apm.mcp.tools._workload_logic import WorkloadCategory, resolve_workload
from synology_apm.sdk import APMClient, M365AutoBackupRule, M365CollabServiceSetting, M365Workload

_M365_CATEGORY = WorkloadCategory(is_m365=True, name_prefix="m365", collection_fn=lambda apm: apm.m365.workloads, serializer=lambda wl: wl.to_dict())


async def _get_m365_workload(
    apm: APMClient, *, workload_id: str, namespace: str, tenant_id: str, workload_type: M365WorkloadTypeLiteral,
) -> M365Workload:
    """Resolve an M365 workload for export tools, reusing the same lookup logic
    register_workload_tools() uses for the shared workload tools."""
    return await resolve_workload(_M365_CATEGORY, apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)


async def _find_rule(apm: APMClient, tenant_id: str, rule_uid: str) -> M365AutoBackupRule:
    """Resolve an M365 auto-backup rule by uid within a tenant, or raise ValueError."""
    result = await apm.m365.auto_backup_rules.list(tenant_id)
    rule = next((r for r in result.rules if r.uid == rule_uid), None)
    if rule is None:
        raise ValueError(f"Auto-backup rule {rule_uid!r} not found for tenant.")
    return rule


def _collab_setting(plan_id: str | None, namespace: str | None) -> M365CollabServiceSetting | None:
    """Build a collaboration-service setting, or None to leave it disabled.

    Both plan_id and namespace must be given together (a service is fully
    configured or fully unset); passing only one is a validation error.
    """
    if plan_id and namespace:
        return M365CollabServiceSetting(plan_id=plan_id, namespace=namespace)
    if plan_id or namespace:
        raise ValueError(
            "Both plan_id and namespace must be provided together to set a "
            "collaboration service (or leave both unset to disable it)."
        )
    return None


def _register_export_tools(
    registrar: ToolRegistrar,
    *,
    name_prefix: str,
    collection_fn: Callable[[APMClient], Any],
    default_workload_type: M365WorkloadTypeLiteral,
    label: str,
    required_mode: str = "operator",
) -> None:
    """Register list/cancel/get_download_url export tools, shared by Exchange and
    Group exports since those three are fully parameter-identical apart from
    which SDK collection is used and the default workload_type.

    start_{name_prefix}_export is deliberately NOT included here: start_exchange_export
    takes an extra archive_mailbox parameter that start_group_export does not, so
    the two stay as their own distinct tool bodies in register() below rather
    than being forced into this factory.
    """

    async def _list(
        ctx: Context,
        *,
        workload_id: str,
        namespace: str,
        tenant_id: str,
        workload_type: M365WorkloadTypeLiteral = default_workload_type,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _do() -> dict[str, Any]:
            workload = await _get_m365_workload(apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
            return await list_result(
                collection_fn(apm).list(workload, limit=clamp_limit(limit), offset=offset),
                lambda x: x.to_dict(),
                offset=offset,
            )

        return await run_tool(_do())

    registrar.tool(
        required_mode, name=f"list_{name_prefix}_exports",
        description=f"List {label} export activities for an M365 workload. {LIST_RESULT_SUFFIX}",
    )(_list)

    async def _cancel(
        ctx: Context,
        activity_id: str,
        *,
        workload_id: str,
        namespace: str,
        tenant_id: str,
        workload_type: M365WorkloadTypeLiteral = default_workload_type,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _do() -> dict[str, Any]:
            workload = await _get_m365_workload(apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
            activity = await resolve_export_activity(collection_fn(apm), workload, activity_id)
            await collection_fn(apm).cancel(activity)
            return {"ok": True, "activity_id": activity_id}

        return await run_audited_tool(
            _do(),
            action=f"cancel_{name_prefix}_export",
            params={"activity_id": activity_id, "workload_id": workload_id},
        )

    registrar.tool(
        required_mode, name=f"cancel_{name_prefix}_export",
        description=f"Cancel an in-progress {label} export.",
    )(_cancel)

    async def _get_url(
        ctx: Context,
        activity_id: str,
        *,
        workload_id: str,
        namespace: str,
        tenant_id: str,
        workload_type: M365WorkloadTypeLiteral = default_workload_type,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _do() -> dict[str, Any]:
            workload = await _get_m365_workload(apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
            activity = await resolve_export_activity(collection_fn(apm), workload, activity_id)
            url = await collection_fn(apm).get_download_url_by_activity(activity)
            return {"url": url, "activity_id": activity_id}

        return await run_tool(_do())

    registrar.tool(
        required_mode, name=f"get_{name_prefix}_export_download_url",
        description=(
            f"Get the time-limited download URL for a completed {label} export. Raises if the export is still "
            f"being prepared; check list_{name_prefix}_exports for status first."
        ),
    )(_get_url)


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register all M365 tools onto server."""

    # ── Tenant lookup tools ───────────────────────────────────────────────────

    @registrar.tool(description=(
        "List all SaaS tenants connected to APM — Microsoft 365 and Google Workspace, M365 tenants listed "
        f"first. {LIST_RESULT_SUFFIX}"
    ))
    async def list_saas_tenants(ctx: Context, limit: int = 100, offset: int = 0) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await list_tool(apm.saas.list(limit=clamp_limit(limit), offset=offset), lambda x: x.to_dict(), offset=offset)

    @registrar.tool(description="Get M365 tenant details by tenant_id (see list_saas_tenants for valid IDs). protected_data_bytes in the response is always 0; use list_saas_tenants for actual usage.")
    async def get_saas_tenant(ctx: Context, tenant_id: str) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.saas.get_m365_tenant(tenant_id), lambda x: x.to_dict())

    # ── Shared workload tools (list, get, backup, cancel, versions, lock, retire, delete) ─

    register_workload_tools(
        registrar,
        name_prefix="m365",
        collection_fn=lambda apm: apm.m365.workloads,
        serializer=lambda wl: wl.to_dict(),
    )

    # ── Auto backup rules ─────────────────────────────────────────────────────

    @registrar.tool(description="List M365 auto-backup rules and collaboration service settings for a tenant.")
    async def list_m365_auto_backup_rules(
        ctx: Context,
        *,
        tenant_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.m365.auto_backup_rules.list(tenant_id), lambda x: x.to_dict())

    # ── Export tools ──────────────────────────────────────────────────────────

    _register_export_tools(
        registrar, name_prefix="exchange", collection_fn=lambda apm: apm.m365.exchange_export,
        default_workload_type="exchange", label="Exchange mailbox",
    )

    @registrar.tool("operator", description=(
        "Start an Exchange mailbox export for a specific backup version; archive_mailbox=True exports "
        "the archive mailbox instead of the primary one. location_id selects a backup destination when "
        "the workload has more than one. If not immediately ready to download, poll list_exchange_exports "
        "until the export completes, then call get_exchange_export_download_url."
    ))
    async def start_exchange_export(
        ctx: Context,
        version_id: str,
        *,
        workload_id: str,
        namespace: str,
        tenant_id: str,
        workload_type: M365WorkloadTypeLiteral = "exchange",
        archive_mailbox: bool = False,
        export_name: str | None = None,
        location_id: str | None = None,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _start() -> dict[str, Any]:
            workload = await _get_m365_workload(apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
            version = await apm.m365.workloads.get_version(workload, version_id)
            result = await apm.m365.exchange_export.start(
                workload, version,
                archive_mailbox=archive_mailbox,
                export_name=export_name,
                location_id=location_id,
            )
            return result.to_dict()

        return await run_audited_tool(
            _start(),
            action="start_exchange_export",
            params={"workload_id": workload_id, "version_id": version_id},
        )

    _register_export_tools(
        registrar, name_prefix="group", collection_fn=lambda apm: apm.m365.group_export,
        default_workload_type="group", label="group/team mailbox",
    )

    @registrar.tool("operator", description=(
        "Start a group/team mailbox export for a specific backup version. location_id selects a backup "
        "destination when the workload has more than one. If not immediately ready to download, poll "
        "list_group_exports until the export completes, then call get_group_export_download_url."
    ))
    async def start_group_export(
        ctx: Context,
        version_id: str,
        *,
        workload_id: str,
        namespace: str,
        tenant_id: str,
        workload_type: M365WorkloadTypeLiteral = "group",
        export_name: str | None = None,
        location_id: str | None = None,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _start() -> dict[str, Any]:
            workload = await _get_m365_workload(apm, workload_id=workload_id, namespace=namespace, tenant_id=tenant_id, workload_type=workload_type)
            version = await apm.m365.workloads.get_version(workload, version_id)
            result = await apm.m365.group_export.start(workload, version, export_name=export_name, location_id=location_id)
            return result.to_dict()

        return await run_audited_tool(
            _start(),
            action="start_group_export",
            params={"workload_id": workload_id, "version_id": version_id},
        )

    # ── Admin M365 tools ──────────────────────────────────────────────────────

    @registrar.tool("admin", description="Create a new M365 auto-backup rule for a tenant. Optionally specify group IDs for Exchange, OneDrive, and Teams Chat auto-backup.")
    async def create_m365_auto_backup_rule(
        ctx: Context,
        namespace: str,
        plan_id: str,
        *,
        tenant_id: str,
        exchange_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        onedrive_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        chat_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _create() -> dict[str, Any]:
            await apm.m365.auto_backup_rules.create(
                tenant_id=tenant_id,
                namespace=namespace,
                plan_id=plan_id,
                exchange_group_ids=exchange_group_ids,
                onedrive_group_ids=onedrive_group_ids,
                chat_group_ids=chat_group_ids,
            )
            return {"ok": True, "tenant_id": tenant_id, "plan_id": plan_id}

        return await run_audited_tool(
            _create(),
            action="create_m365_auto_backup_rule",
            params={"tenant_id": tenant_id, "plan_id": plan_id},
        )

    @registrar.tool("admin", description="Update an existing M365 auto-backup rule; find its uid via list_m365_auto_backup_rules. Omit a field (leave it unset) to keep its current value; pass an empty list `[]` for a group-id list you want cleared.")
    async def update_m365_auto_backup_rule(
        ctx: Context,
        rule_uid: str,
        plan_id: str | None = None,
        exchange_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        onedrive_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        chat_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        *,
        tenant_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            rule = await _find_rule(apm, tenant_id, rule_uid)
            await apm.m365.auto_backup_rules.update(
                rule,
                plan_id=plan_id,
                exchange_group_ids=exchange_group_ids,
                onedrive_group_ids=onedrive_group_ids,
                chat_group_ids=chat_group_ids,
            )
            return {"ok": True, "uid": rule_uid}

        return await run_audited_tool(
            _update(),
            action="update_m365_auto_backup_rule",
            params={"rule_uid": rule_uid},
        )

    @registrar.tool("admin", description=(
        "Replace all four M365 collaboration service settings (group Exchange, MySite, SharePoint, "
        "Teams) for a tenant. Omitted services are disabled, not preserved — pass current values from "
        "list_m365_auto_backup_rules to keep them unchanged. Each service requires both its plan_id and "
        "namespace together, or neither."
    ))
    async def update_m365_collab_settings(
        ctx: Context,
        *,
        tenant_id: str,
        group_exchange_plan_id: str | None = None,
        group_exchange_namespace: str | None = None,
        mysite_plan_id: str | None = None,
        mysite_namespace: str | None = None,
        sharepoint_plan_id: str | None = None,
        sharepoint_namespace: str | None = None,
        teams_plan_id: str | None = None,
        teams_namespace: str | None = None,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            await apm.m365.auto_backup_rules.update_collab_settings(
                tenant_id=tenant_id,
                group_exchange=_collab_setting(group_exchange_plan_id, group_exchange_namespace),
                mysite=_collab_setting(mysite_plan_id, mysite_namespace),
                sharepoint=_collab_setting(sharepoint_plan_id, sharepoint_namespace),
                teams=_collab_setting(teams_plan_id, teams_namespace),
            )
            return {"ok": True, "tenant_id": tenant_id}

        return await run_audited_tool(
            _update(),
            action="update_m365_collab_settings",
            params={"tenant_id": tenant_id},
        )

    @registrar.tool("admin", description=(
        f"Delete an M365 auto-backup rule; find its uid via list_m365_auto_backup_rules. "
        f"{DESTRUCTIVE_PREVIEW_SUFFIX}"
    ))
    async def delete_m365_auto_backup_rule(
        ctx: Context,
        rule_uid: str,
        *,
        tenant_id: str,
        confirm: bool = False,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        return await destructive_tool(
            confirm=confirm,
            action="delete_m365_auto_backup_rule",
            warning="This permanently removes the auto-backup rule. Pass confirm=true to proceed.",
            resolve_coro=_find_rule(apm, tenant_id, rule_uid),
            preview_target_fn=lambda r: {"uid": r.uid, "tenant_id": r.tenant_id, "plan_id": r.plan_id},
            execute_fn=lambda r: apm.m365.auto_backup_rules.delete(r),
            params={"rule_uid": rule_uid, "confirm": confirm},
        )
