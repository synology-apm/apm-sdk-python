"""m365_rule domain phase: M365AutoBackupRuleCollection CRUD roundtrips.

Tests user-service rule create/update/delete and the duplicate-create error path, plus
collaboration service settings enable/change-plan/disable.

Reads from ctx.data:
  servers: list[BackupServer]  — from infra phase (optional); if absent, fetches servers directly

Writes to ctx.data: nothing (self-contained; test plans and test rule deleted before phase exits)

Skip conditions:
  - No M365 tenant (apm.saas.list() returns no M365-category tenant)
  - No backup servers available (ctx.data["servers"] empty and apm.backup_servers.list() empty)
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import time
from typing import Any

from synology_apm.sdk import (
    APIError,
    APMClient,
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
    M365PlanCreateRequest,
    M365WorkloadType,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RetentionType,
    ScheduleFrequency,
    WorkloadCategory,
)

from .._context import SmokeContext

DOMAIN = "m365_rule"

_M365_PLAN_DELETE_RETRIES = 12
_M365_PLAN_DELETE_RETRY_WAIT = 10  # seconds; per plan — two plans deleted sequentially → 240 s worst-case total

_TEST_KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
_TEST_SCHEDULE = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0))
# Synthetic group IDs — APM stores them without Azure AD validation, safe for CRUD mechanics
_TEST_GROUP_A = "00000000-dead-beef-cafe-000000000001"
_TEST_GROUP_B = "00000000-dead-beef-cafe-000000000002"



async def run(ctx: SmokeContext) -> None:
    apm = ctx.apm
    uid = str(uuid.uuid4())[:8]

    # ── Tenant ────────────────────────────────────────────────────────────────
    # Use ctx.data["m365_tenant"] when populated by the m365 phase; fetch directly otherwise so
    # that --group m365_rule works as a standalone run.
    if "m365_tenant" not in ctx.data:
        saas_result = await ctx.call(DOMAIN, "m365_rule.saas.list", lambda: apm.saas.list(limit=500))
        tenants, _ = saas_result if saas_result is not None else ([], 0)
        m365_tenant = next((t for t in tenants if t.category == WorkloadCategory.M365), None)
    else:
        m365_tenant = ctx.data.get("m365_tenant")
        suffix = " (no tenant found)" if m365_tenant is None else ""
        ctx.na(DOMAIN, "m365_rule.saas.list", f"m365_tenant already available from m365 phase{suffix}")
    if m365_tenant is None:
        ctx.skip(DOMAIN, "m365_rule.prereq.check[tenant]", "No M365 tenant configured")
        return
    tenant_id = m365_tenant.tenant_id

    # ── Namespace from first backup server ────────────────────────────────────
    # Use ctx.data["servers"] when populated by the infra phase; fetch directly otherwise so
    # that --group m365_rule works as a standalone run.
    servers: list[Any] = ctx.data.get("servers", [])
    if not servers:
        servers_result = await ctx.call(
            DOMAIN, "m365_rule.prereq.servers.list",
            lambda: apm.backup_servers.list(limit=500),
        )
        fetched, _ = servers_result if servers_result is not None else ([], 0)
        servers = list(fetched)
    else:
        ctx.na(DOMAIN, "m365_rule.prereq.servers.list", "servers already available from infra phase")
    if not servers:
        ctx.skip(DOMAIN, "m365_rule.prereq.check[namespace]", "No backup servers available")
        return
    namespace: str = servers[0].namespace

    # ── Create two dedicated test M365 plans (inside try so finally cleans up both) ──
    plan_a: ProtectionPlan | None = None
    plan_b: ProtectionPlan | None = None
    try:
        plan_a = await ctx.call(
            DOMAIN, "m365_rule.plan.create[a]",
            lambda: apm.m365.plans.create(
                M365PlanCreateRequest(
                    name=f"smoke-m365-rule-{uid}-a",
                    retention=_TEST_KEEP_DAYS,
                    schedule=_TEST_SCHEDULE,
                )
            ),
        )
        plan_b = await ctx.call(
            DOMAIN, "m365_rule.plan.create[b]",
            lambda: apm.m365.plans.create(
                M365PlanCreateRequest(
                    name=f"smoke-m365-rule-{uid}-b",
                    retention=_TEST_KEEP_DAYS,
                    schedule=_TEST_SCHEDULE,
                )
            ),
        )

        if plan_a is None:
            ctx.skip(DOMAIN, "m365_rule.prereq.check[plans]", "Test plan creation failed")
            return  # return inside try → finally still runs, cleaning up plan_b if it exists

        await _run_user_rule_roundtrip(ctx, apm, tenant_id, namespace, plan_a, plan_b)
        await _run_collab_roundtrip(ctx, apm, tenant_id, namespace, plan_a, plan_b)
    finally:
        for step_name, _plan in (
            ("m365_rule.plan.delete[a]", plan_a),
            ("m365_rule.plan.delete[b]", plan_b),
        ):
            if _plan is None:
                ctx.na(DOMAIN, step_name, "Plan creation failed")
                continue
            _p = _plan
            deleted = False
            removed_workloads = 0
            last_exc: Exception | None = None
            for attempt in range(_M365_PLAN_DELETE_RETRIES + 1):
                try:
                    await apm.m365.plans.delete(_p)
                    deleted = True
                    break
                except Exception as exc:
                    last_exc = exc
                    removed_workloads += await _delete_plan_workloads(apm, tenant_id, _p)
                    if attempt < _M365_PLAN_DELETE_RETRIES:
                        await asyncio.sleep(_M365_PLAN_DELETE_RETRY_WAIT)
            notes: list[str] = []
            if removed_workloads:
                notes.append(f"removed {removed_workloads} auto-protected workload(s) blocking plan deletion")
            if not deleted and last_exc is not None:
                notes.append(f"last error: {last_exc!r}")
            ctx.check(DOMAIN, step_name, deleted, note="; ".join(notes))


async def _delete_plan_workloads(apm: APMClient, tenant_id: str, plan: ProtectionPlan) -> int:
    """Best-effort: delete M365 workloads still attached to `plan`, returning the count removed.

    While a test rule or collab setting points at a plan, APM's auto-backup engine may
    auto-protect real tenant resources under it. Deleting the rule / disabling the setting
    does not unprotect those workloads, and the plan cannot be deleted while they remain.
    """
    removed = 0
    for workload_type in M365WorkloadType:
        try:
            workloads, _total = await apm.m365.workloads.list(tenant_id, workload_type, plan=[plan])
        except Exception:
            continue
        for workload in workloads:
            try:
                await apm.m365.workloads.delete(workload)
                removed += 1
            except Exception:
                pass
    return removed


async def _run_user_rule_roundtrip(
    ctx: SmokeContext,
    apm: APMClient,
    tenant_id: str,
    namespace: str,
    plan_a: ProtectionPlan,
    plan_b: ProtectionPlan | None,
) -> None:
    test_rule: M365AutoBackupRule | None = None
    current_plan_id = plan_a.plan_id

    try:
        # ── Baseline ──────────────────────────────────────────────────────────
        await ctx.call(
            DOMAIN, "m365_rule.user_rule.list[baseline]",
            lambda: apm.m365.auto_backup_rules.list(tenant_id),
        )

        # ── Create ────────────────────────────────────────────────────────────
        await ctx.call(
            DOMAIN, "m365_rule.user_rule.create",
            lambda: apm.m365.auto_backup_rules.create(
                tenant_id=tenant_id,
                namespace=namespace,
                plan_id=plan_a.plan_id,
                exchange_group_ids=[_TEST_GROUP_A],
            ),
        )

        # Fetch the created rule object for subsequent operations
        list_after_create: M365AutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "m365_rule.user_rule.list[after_create]",
            lambda: apm.m365.auto_backup_rules.list(tenant_id),
        )
        if list_after_create is not None:
            test_rule = next(
                (r for r in list_after_create.rules
                 if r.namespace == namespace and r.plan_id == plan_a.plan_id),
                None,
            )
            ctx.check(
                DOMAIN, "m365_rule.user_rule.check[created]",
                test_rule is not None,
                note="Created rule must appear in list() with matching namespace and plan_id.",
            )
            if test_rule is not None:
                ctx.check(
                    DOMAIN, "m365_rule.user_rule.check[initial_groups]",
                    test_rule.exchange_group_ids == (_TEST_GROUP_A,),
                    note="exchange_group_ids must match the value supplied at create().",
                )

        if test_rule is None:
            for step in (
                "m365_rule.user_rule.update[add_group]",
                "m365_rule.user_rule.list[after_add_group]",
                "m365_rule.user_rule.check[add_group]",
                "m365_rule.user_rule.update[remove_group]",
                "m365_rule.user_rule.list[after_remove_group]",
                "m365_rule.user_rule.check[remove_group]",
                "m365_rule.user_rule.update[change_plan]",
                "m365_rule.user_rule.list[after_change_plan]",
                "m365_rule.user_rule.check[change_plan]",
                "m365_rule.user_rule.create[duplicate]",
                "m365_rule.user_rule.check[duplicate_error_code]",
                "m365_rule.user_rule.check[duplicate_error_message]",
            ):
                ctx.skip(DOMAIN, step, "Rule creation failed or rule not found after create")
            return

        # ── Update: add group B ───────────────────────────────────────────────
        _rule_add = test_rule  # local capture; narrowed to M365AutoBackupRule after early return
        await ctx.call(
            DOMAIN, "m365_rule.user_rule.update[add_group]",
            lambda: apm.m365.auto_backup_rules.update(
                _rule_add,
                exchange_group_ids=[_TEST_GROUP_A, _TEST_GROUP_B],
            ),
        )
        list_after_add: M365AutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "m365_rule.user_rule.list[after_add_group]",
            lambda: apm.m365.auto_backup_rules.list(tenant_id),
        )
        if list_after_add is not None:
            updated = next((r for r in list_after_add.rules if r.uid == test_rule.uid), None)
            ctx.check(
                DOMAIN, "m365_rule.user_rule.check[add_group]",
                updated is not None
                and set(updated.exchange_group_ids) == {_TEST_GROUP_A, _TEST_GROUP_B},
                note="Both groups must be present after update.",
            )
            if updated is not None:
                test_rule = updated

        # ── Update: remove group A ────────────────────────────────────────────
        _rule_remove = test_rule  # re-capture after possible reassignment above
        await ctx.call(
            DOMAIN, "m365_rule.user_rule.update[remove_group]",
            lambda: apm.m365.auto_backup_rules.update(
                _rule_remove,
                exchange_group_ids=[_TEST_GROUP_B],
            ),
        )
        list_after_remove: M365AutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "m365_rule.user_rule.list[after_remove_group]",
            lambda: apm.m365.auto_backup_rules.list(tenant_id),
        )
        if list_after_remove is not None:
            updated = next((r for r in list_after_remove.rules if r.uid == test_rule.uid), None)
            ctx.check(
                DOMAIN, "m365_rule.user_rule.check[remove_group]",
                updated is not None and updated.exchange_group_ids == (_TEST_GROUP_B,),
                note="Only GROUP_B must remain after removing GROUP_A.",
            )
            if updated is not None:
                test_rule = updated

        # ── Update: change plan ───────────────────────────────────────────────
        if plan_b is not None:
            _rule_cp = test_rule  # re-capture; _plan_b avoids lambda narrowing issue for plan_b
            _plan_b = plan_b
            await ctx.call(
                DOMAIN, "m365_rule.user_rule.update[change_plan]",
                lambda: apm.m365.auto_backup_rules.update(_rule_cp, plan_id=_plan_b.plan_id),
            )
            list_after_cp: M365AutoBackupRuleListResult | None = await ctx.call(
                DOMAIN, "m365_rule.user_rule.list[after_change_plan]",
                lambda: apm.m365.auto_backup_rules.list(tenant_id),
            )
            if list_after_cp is not None:
                updated = next((r for r in list_after_cp.rules if r.uid == test_rule.uid), None)
                ctx.check(
                    DOMAIN, "m365_rule.user_rule.check[change_plan]",
                    updated is not None and updated.plan_id == plan_b.plan_id,
                    note="plan_id must match plan_b after update.",
                )
                if updated is not None:
                    test_rule = updated
                    current_plan_id = plan_b.plan_id
        else:
            ctx.na(DOMAIN, "m365_rule.user_rule.update[change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "m365_rule.user_rule.list[after_change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "m365_rule.user_rule.check[change_plan]", "plan_b creation failed")

        # ── Duplicate create (expect APIError 400) ────────────────────────────
        _cp = current_plan_id
        exc = await ctx.call_expect_error(
            DOMAIN, "m365_rule.user_rule.create[duplicate]",
            lambda: apm.m365.auto_backup_rules.create(
                tenant_id=tenant_id,
                namespace=namespace,
                plan_id=_cp,
            ),
            expect_error=APIError,
            note="Creating a rule with the same namespace+plan must be rejected.",
        )
        ctx.check(
            DOMAIN, "m365_rule.user_rule.check[duplicate_error_code]",
            exc is not None and exc.error_code == 400,
            note="Duplicate rule error must carry HTTP status 400.",
        )
        ctx.check(
            DOMAIN, "m365_rule.user_rule.check[duplicate_error_message]",
            exc is not None and "should not have multiple" in exc.message,
            note="Error message must describe the uniqueness constraint.",
        )

    finally:
        # ── Cleanup: delete test rule ─────────────────────────────────────────
        if test_rule is not None:
            _tr = test_rule
            await ctx.call(
                DOMAIN, "m365_rule.user_rule.delete",
                lambda: apm.m365.auto_backup_rules.delete(_tr),
            )
            list_after_del: M365AutoBackupRuleListResult | None = await ctx.call(
                DOMAIN, "m365_rule.user_rule.list[after_delete]",
                lambda: apm.m365.auto_backup_rules.list(tenant_id),
            )
            if list_after_del is not None:
                ctx.check(
                    DOMAIN, "m365_rule.user_rule.check[deleted]",
                    all(r.uid != _tr.uid for r in list_after_del.rules),
                    note="Deleted rule must not appear in subsequent list().",
                )
            else:
                ctx.na(DOMAIN, "m365_rule.user_rule.check[deleted]", "list[after_delete] failed")
        else:
            ctx.na(DOMAIN, "m365_rule.user_rule.delete", "No test rule was created")
            ctx.na(DOMAIN, "m365_rule.user_rule.list[after_delete]", "No test rule was created")
            ctx.na(DOMAIN, "m365_rule.user_rule.check[deleted]", "No test rule was created")


async def _run_collab_roundtrip(
    ctx: SmokeContext,
    apm: APMClient,
    tenant_id: str,
    namespace: str,
    plan_a: ProtectionPlan,
    plan_b: ProtectionPlan | None,
) -> None:
    setting_a = M365CollabServiceSetting(plan_id=plan_a.plan_id, namespace=namespace)

    try:
        # ── Baseline ──────────────────────────────────────────────────────────
        await ctx.call(
            DOMAIN, "m365_rule.collab.list[baseline]",
            lambda: apm.m365.auto_backup_rules.list(tenant_id),
        )

        # ── Enable group_exchange + sharepoint ────────────────────────────────
        await ctx.call(
            DOMAIN, "m365_rule.collab.update[enable]",
            lambda: apm.m365.auto_backup_rules.update_collab_settings(
                tenant_id,
                group_exchange=setting_a,
                sharepoint=setting_a,
            ),
        )
        list_after_enable: M365AutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "m365_rule.collab.list[after_enable]",
            lambda: apm.m365.auto_backup_rules.list(tenant_id),
        )
        if list_after_enable is not None:
            ctx.check(
                DOMAIN, "m365_rule.collab.check[group_exchange_enabled]",
                list_after_enable.group_exchange.enabled
                and list_after_enable.group_exchange.plan_id == plan_a.plan_id,
                note="group_exchange must be enabled with plan_a after update.",
            )
            ctx.check(
                DOMAIN, "m365_rule.collab.check[sharepoint_enabled]",
                list_after_enable.sharepoint.enabled,
                note="sharepoint must be enabled after update.",
            )
            ctx.check(
                DOMAIN, "m365_rule.collab.check[mysite_disabled]",
                not list_after_enable.mysite.enabled,
                note="mysite must remain disabled (was not included in update).",
            )
            ctx.check(
                DOMAIN, "m365_rule.collab.check[teams_disabled]",
                not list_after_enable.teams.enabled,
                note="teams must remain disabled (was not included in update).",
            )

        # ── Change plan on group_exchange ─────────────────────────────────────
        if plan_b is not None:
            setting_b = M365CollabServiceSetting(plan_id=plan_b.plan_id, namespace=namespace)
            await ctx.call(
                DOMAIN, "m365_rule.collab.update[change_plan]",
                lambda: apm.m365.auto_backup_rules.update_collab_settings(
                    tenant_id,
                    group_exchange=setting_b,
                    sharepoint=setting_a,
                ),
            )
            list_after_cp: M365AutoBackupRuleListResult | None = await ctx.call(
                DOMAIN, "m365_rule.collab.list[after_change_plan]",
                lambda: apm.m365.auto_backup_rules.list(tenant_id),
            )
            if list_after_cp is not None:
                ctx.check(
                    DOMAIN, "m365_rule.collab.check[change_plan]",
                    list_after_cp.group_exchange.plan_id == plan_b.plan_id,
                    note="group_exchange plan_id must reflect plan_b after change.",
                )
        else:
            ctx.na(DOMAIN, "m365_rule.collab.update[change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "m365_rule.collab.list[after_change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "m365_rule.collab.check[change_plan]", "plan_b creation failed")

    finally:
        # ── Always disable all collab settings ────────────────────────────────
        await ctx.call(
            DOMAIN, "m365_rule.collab.update[disable]",
            lambda: apm.m365.auto_backup_rules.update_collab_settings(tenant_id),
        )
        list_after_disable: M365AutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "m365_rule.collab.list[after_disable]",
            lambda: apm.m365.auto_backup_rules.list(tenant_id),
        )
        if list_after_disable is not None:
            ctx.check(
                DOMAIN, "m365_rule.collab.check[all_disabled]",
                not list_after_disable.group_exchange.enabled
                and not list_after_disable.mysite.enabled
                and not list_after_disable.sharepoint.enabled
                and not list_after_disable.teams.enabled,
                note="All four collab settings must be disabled after update_collab_settings().",
            )
