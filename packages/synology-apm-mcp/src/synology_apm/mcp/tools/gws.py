"""GWS (Google Workspace) workload tools: shared workload tools + auto-backup rules + domain lookup."""
from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context

from synology_apm.mcp._helpers import (
    JSON_LIST_VALIDATOR,
    ToolResult,
    auto_backup_rule_delete_desc,
    auto_backup_rule_update_desc,
    get_tool,
)
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import destructive_tool, run_audited_tool
from synology_apm.mcp.tools._workload import register_workload_tools
from synology_apm.sdk import APMClient, GWSAutoBackupRule, GWSSharedDriveSetting


async def _find_rule(apm: APMClient, domain: str, rule_uid: str) -> GWSAutoBackupRule:
    """Resolve a GWS auto-backup rule by uid within a domain, or raise ValueError."""
    result = await apm.gws.auto_backup_rules.list(domain)
    rule = next((r for r in result.rules if r.uid == rule_uid), None)
    if rule is None:
        raise ValueError(f"Auto-backup rule {rule_uid!r} not found for domain.")
    return rule


def _shared_drive_setting(
    plan_id: str | None, namespace: str | None, backup_user_id: str | None
) -> GWSSharedDriveSetting | None:
    """Build the Shared Drive collaboration-service setting, or None to leave it disabled.

    plan_id and namespace must be given together (a service is fully configured
    or fully unset); backup_user_id is optional (omitted = auto-selected).
    """
    if plan_id and namespace:
        return GWSSharedDriveSetting(plan_id=plan_id, namespace=namespace, backup_user_id=backup_user_id or "")
    if plan_id or namespace:
        raise ValueError(
            "Both plan_id and namespace must be provided together to set the Shared Drive "
            "collaboration service (or leave both unset to disable it)."
        )
    return None


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    # ── Domain lookup tool ────────────────────────────────────────────────────

    @registrar.tool(description="Get GWS domain details by domain (see list_saas_applications for valid IDs).")
    async def get_gws_domain(ctx: Context, domain: str) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.saas.get_gws_domain(domain), lambda x: x.to_dict())

    # ── Shared workload tools (list, get, backup, cancel, versions, lock, retire, delete) ─

    register_workload_tools(
        registrar,
        name_prefix="gws",
        variant="gws",
        collection_fn=lambda apm: apm.gws.workloads,
        serializer=lambda wl: wl.to_dict(),
    )

    # ── Auto backup rules ─────────────────────────────────────────────────────

    @registrar.tool(description="List GWS auto-backup rules, collaboration service settings, and protected-account-type settings for a domain.")
    async def list_gws_auto_backup_rules(
        ctx: Context,
        *,
        domain: str,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.gws.auto_backup_rules.list(domain), lambda x: x.to_dict())

    # ── Admin GWS tools ───────────────────────────────────────────────────────

    @registrar.tool("admin", description="Create a new GWS User Services auto-backup rule for a domain. Optionally specify group IDs for Mail, Calendar, Contact, and Drive auto-backup.")
    async def create_gws_auto_backup_rule(
        ctx: Context,
        namespace: str,
        plan_id: str,
        *,
        domain: str,
        mail_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        calendar_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        contact_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        drive_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _create() -> dict[str, Any]:
            await apm.gws.auto_backup_rules.create(
                domain=domain,
                namespace=namespace,
                plan_id=plan_id,
                mail_group_ids=mail_group_ids,
                calendar_group_ids=calendar_group_ids,
                contact_group_ids=contact_group_ids,
                drive_group_ids=drive_group_ids,
            )
            return {"ok": True, "domain": domain, "plan_id": plan_id}

        return await run_audited_tool(
            _create(),
            action="create_gws_auto_backup_rule",
            params={"domain": domain, "plan_id": plan_id},
        )

    @registrar.tool("admin", description=auto_backup_rule_update_desc("gws"))
    async def update_gws_auto_backup_rule(
        ctx: Context,
        rule_uid: str,
        plan_id: str | None = None,
        mail_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        calendar_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        contact_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        drive_group_ids: Annotated[list[str], JSON_LIST_VALIDATOR] | None = None,
        *,
        domain: str,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            rule = await _find_rule(apm, domain, rule_uid)
            await apm.gws.auto_backup_rules.update(
                rule,
                plan_id=plan_id,
                mail_group_ids=mail_group_ids,
                calendar_group_ids=calendar_group_ids,
                contact_group_ids=contact_group_ids,
                drive_group_ids=drive_group_ids,
            )
            return {"ok": True, "uid": rule_uid}

        return await run_audited_tool(
            _update(),
            action="update_gws_auto_backup_rule",
            params={"rule_uid": rule_uid},
        )

    @registrar.tool("admin", description=(
        "Replace the GWS Shared Drive collaboration service setting for a domain (all shared drives "
        "are included automatically when enabled). Omitting shared_drive_plan_id/shared_drive_namespace "
        "disables it — pass current values from list_gws_auto_backup_rules to keep it unchanged. "
        "shared_drive_backup_user_id selects which Google user account performs the backup; omit to "
        "let APM auto-select one."
    ))
    async def update_gws_collab_settings(
        ctx: Context,
        *,
        domain: str,
        shared_drive_plan_id: str | None = None,
        shared_drive_namespace: str | None = None,
        shared_drive_backup_user_id: str | None = None,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            await apm.gws.auto_backup_rules.update_collab_settings(
                domain=domain,
                shared_drive=_shared_drive_setting(shared_drive_plan_id, shared_drive_namespace, shared_drive_backup_user_id),
            )
            return {"ok": True, "domain": domain}

        return await run_audited_tool(
            _update(),
            action="update_gws_collab_settings",
            params={"domain": domain},
        )

    @registrar.tool("admin", description=(
        "Update which additional Google Workspace account types are auto-protected for a domain. "
        "Licensed accounts are always auto-protected; this controls whether unlicensed and/or "
        "archived accounts are also included."
    ))
    async def update_gws_protected_account_types(
        ctx: Context,
        *,
        domain: str,
        include_unlicensed_accounts: bool,
        include_archived_accounts: bool,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            await apm.gws.auto_backup_rules.update_protected_account_types(
                domain,
                include_unlicensed_accounts=include_unlicensed_accounts,
                include_archived_accounts=include_archived_accounts,
            )
            return {
                "ok": True,
                "domain": domain,
                "include_unlicensed_accounts": include_unlicensed_accounts,
                "include_archived_accounts": include_archived_accounts,
            }

        return await run_audited_tool(
            _update(),
            action="update_gws_protected_account_types",
            params={
                "domain": domain,
                "include_unlicensed_accounts": include_unlicensed_accounts,
                "include_archived_accounts": include_archived_accounts,
            },
        )

    @registrar.tool("admin", description=auto_backup_rule_delete_desc("a", "gws"))
    async def delete_gws_auto_backup_rule(
        ctx: Context,
        rule_uid: str,
        *,
        domain: str,
        confirm: bool = False,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        return await destructive_tool(
            confirm=confirm,
            action="delete_gws_auto_backup_rule",
            warning="This permanently removes the auto-backup rule. Pass confirm=true to proceed.",
            resolve_coro=_find_rule(apm, domain, rule_uid),
            preview_target_fn=lambda r: {"uid": r.uid, "domain": r.domain, "plan_id": r.plan_id},
            execute_fn=lambda r: apm.gws.auto_backup_rules.delete(r),
            params={"rule_uid": rule_uid, "confirm": confirm},
        )
