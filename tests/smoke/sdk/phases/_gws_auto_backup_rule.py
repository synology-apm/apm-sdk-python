"""gws_rule domain phase: GWSAutoBackupRuleCollection CRUD roundtrips.

Tests user-service rule create/update/delete and the duplicate-create error path, the
Shared Drive collaboration service settings enable/change-plan/disable, and the
domain-wide protected-account-types setting (unlicensed/archived accounts) — the last of
these has no M365 equivalent.

Reads from ctx.data:
  gws_domain: GWSDomainInfo | None — from gws phase (optional); if absent, fetches directly
  servers: list[BackupServer]  — from infra phase (optional); if absent, fetches servers directly

Writes to ctx.data: nothing (self-contained; test plans and test rule deleted before phase exits)

Skip conditions:
  - No GWS domain (apm.saas.list() returns no GWS-category application)
  - No backup servers available (ctx.data["servers"] empty and apm.backup_servers.list() empty)
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import time

from synology_apm.sdk import (
    APIError,
    APMClient,
    BackupServer,
    GWSAutoBackupRule,
    GWSAutoBackupRuleListResult,
    GWSDomainInfo,
    GWSPlanCreateRequest,
    GWSSharedDriveSetting,
    GWSWorkloadType,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RetentionType,
    ScheduleFrequency,
)

from .._context import SmokeContext

DOMAIN = "gws_rule"

_GWS_PLAN_DELETE_RETRIES = 12
_GWS_PLAN_DELETE_RETRY_WAIT = 10  # seconds; per plan — two plans deleted sequentially → 240 s worst-case total

_TEST_KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
_TEST_SCHEDULE = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0))
# Synthetic group IDs — APM stores them without Google Workspace validation, safe for CRUD mechanics
_TEST_GROUP_A = "00000000-dead-beef-cafe-000000000001"
_TEST_GROUP_B = "00000000-dead-beef-cafe-000000000002"


async def run(ctx: SmokeContext) -> None:
    apm = ctx.apm
    uid = str(uuid.uuid4())[:8]

    # ── Domain ────────────────────────────────────────────────────────────────
    # Use ctx.data["gws_domain"] when populated by the gws phase; fetch directly otherwise so
    # that --group gws_rule works as a standalone run.
    if "gws_domain" not in ctx.data:
        saas_result = await ctx.call(DOMAIN, "gws_rule.saas.list", lambda: apm.saas.list(limit=500))
        apps, _ = saas_result if saas_result is not None else ([], 0)
        gws_domain = next((a for a in apps if isinstance(a, GWSDomainInfo)), None)
    else:
        gws_domain = ctx.data.get("gws_domain")
        suffix = " (no domain found)" if gws_domain is None else ""
        ctx.na(DOMAIN, "gws_rule.saas.list", f"gws_domain already available from gws phase{suffix}")
    if gws_domain is None:
        ctx.skip(DOMAIN, "gws_rule.prereq.check[domain]", "No GWS domain configured")
        return
    domain = gws_domain.domain

    # ── Namespace from first backup server ────────────────────────────────────
    # Use ctx.data["servers"] when populated by the infra phase; fetch directly otherwise so
    # that --group gws_rule works as a standalone run.
    servers: list[BackupServer] = ctx.data.get("servers", [])
    if not servers:
        servers_result = await ctx.call(
            DOMAIN, "gws_rule.prereq.servers.list",
            lambda: apm.backup_servers.list(limit=500),
        )
        fetched, _ = servers_result if servers_result is not None else ([], 0)
        servers = list(fetched)
    else:
        ctx.na(DOMAIN, "gws_rule.prereq.servers.list", "servers already available from infra phase")
    if not servers:
        ctx.skip(DOMAIN, "gws_rule.prereq.check[namespace]", "No backup servers available")
        return
    namespace: str = servers[0].namespace

    # ── Create two dedicated test GWS plans (inside try so finally cleans up both) ──
    plan_a: ProtectionPlan | None = None
    plan_b: ProtectionPlan | None = None
    try:
        plan_a = await ctx.call(
            DOMAIN, "gws_rule.plan.create[a]",
            lambda: apm.gws.plans.create(
                GWSPlanCreateRequest(
                    name=f"smoke-gws-rule-{uid}-a",
                    retention=_TEST_KEEP_DAYS,
                    schedule=_TEST_SCHEDULE,
                )
            ),
        )
        plan_b = await ctx.call(
            DOMAIN, "gws_rule.plan.create[b]",
            lambda: apm.gws.plans.create(
                GWSPlanCreateRequest(
                    name=f"smoke-gws-rule-{uid}-b",
                    retention=_TEST_KEEP_DAYS,
                    schedule=_TEST_SCHEDULE,
                )
            ),
        )

        if plan_a is None:
            ctx.skip(DOMAIN, "gws_rule.prereq.check[plans]", "Test plan creation failed")
            return  # return inside try → finally still runs, cleaning up plan_b if it exists

        await _run_user_rule_roundtrip(ctx, apm, domain, namespace, plan_a, plan_b)
        await _run_collab_roundtrip(ctx, apm, domain, namespace, plan_a, plan_b)
        await _run_protected_account_types_roundtrip(ctx, apm, domain)
    finally:
        for step_name, _plan in (
            ("gws_rule.plan.delete[a]", plan_a),
            ("gws_rule.plan.delete[b]", plan_b),
        ):
            if _plan is None:
                ctx.na(DOMAIN, step_name, "Plan creation failed")
                continue
            _p = _plan
            deleted = False
            removed_workloads = 0
            last_exc: Exception | None = None
            for attempt in range(_GWS_PLAN_DELETE_RETRIES + 1):
                try:
                    await apm.gws.plans.delete(_p)
                    deleted = True
                    break
                except Exception as exc:
                    last_exc = exc
                    removed_workloads += await _delete_plan_workloads(apm, domain, _p)
                    if attempt < _GWS_PLAN_DELETE_RETRIES:
                        await asyncio.sleep(_GWS_PLAN_DELETE_RETRY_WAIT)
            notes: list[str] = []
            if removed_workloads:
                notes.append(f"removed {removed_workloads} auto-protected workload(s) blocking plan deletion")
            if not deleted and last_exc is not None:
                notes.append(f"last error: {last_exc!r}")
            ctx.check(DOMAIN, step_name, deleted, note="; ".join(notes))


async def _delete_plan_workloads(apm: APMClient, domain: str, plan: ProtectionPlan) -> int:
    """Best-effort: delete GWS workloads still attached to `plan`, returning the count removed.

    While a test rule or collab setting points at a plan, APM's auto-backup engine may
    auto-protect real domain resources under it. Deleting the rule / disabling the setting
    does not unprotect those workloads, and the plan cannot be deleted while they remain.
    """
    removed = 0
    for workload_type in GWSWorkloadType:
        try:
            workloads, _total = await apm.gws.workloads.list(domain, workload_type, plan=[plan])
        except Exception:
            continue
        for workload in workloads:
            try:
                await apm.gws.workloads.delete(workload)
                removed += 1
            except Exception:
                pass
    return removed


async def _run_user_rule_roundtrip(
    ctx: SmokeContext,
    apm: APMClient,
    domain: str,
    namespace: str,
    plan_a: ProtectionPlan,
    plan_b: ProtectionPlan | None,
) -> None:
    test_rule: GWSAutoBackupRule | None = None
    current_plan_id = plan_a.plan_id

    try:
        # ── Baseline ──────────────────────────────────────────────────────────
        await ctx.call(
            DOMAIN, "gws_rule.user_rule.list[baseline]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )

        # ── Create ────────────────────────────────────────────────────────────
        await ctx.call(
            DOMAIN, "gws_rule.user_rule.create",
            lambda: apm.gws.auto_backup_rules.create(
                domain=domain,
                namespace=namespace,
                plan_id=plan_a.plan_id,
                mail_group_ids=[_TEST_GROUP_A],
            ),
        )

        # Fetch the created rule object for subsequent operations
        list_after_create: GWSAutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "gws_rule.user_rule.list[after_create]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )
        if list_after_create is not None:
            test_rule = next(
                (r for r in list_after_create.rules
                 if r.namespace == namespace and r.plan_id == plan_a.plan_id),
                None,
            )
            ctx.check(
                DOMAIN, "gws_rule.user_rule.check[created]",
                test_rule is not None,
                note="Created rule must appear in list() with matching namespace and plan_id.",
            )
            if test_rule is not None:
                ctx.check(
                    DOMAIN, "gws_rule.user_rule.check[initial_groups]",
                    test_rule.mail_group_ids == (_TEST_GROUP_A,),
                    note="mail_group_ids must match the value supplied at create().",
                )

        if test_rule is None:
            for step in (
                "gws_rule.user_rule.update[add_group]",
                "gws_rule.user_rule.list[after_add_group]",
                "gws_rule.user_rule.check[add_group]",
                "gws_rule.user_rule.update[remove_group]",
                "gws_rule.user_rule.list[after_remove_group]",
                "gws_rule.user_rule.check[remove_group]",
                "gws_rule.user_rule.update[change_plan]",
                "gws_rule.user_rule.list[after_change_plan]",
                "gws_rule.user_rule.check[change_plan]",
                "gws_rule.user_rule.create[duplicate]",
                "gws_rule.user_rule.check[duplicate_error_code]",
                "gws_rule.user_rule.check[duplicate_error_message]",
            ):
                ctx.skip(DOMAIN, step, "Rule creation failed or rule not found after create")
            return

        # ── Update: add group B ───────────────────────────────────────────────
        _rule_add = test_rule  # local capture; narrowed to GWSAutoBackupRule after early return
        await ctx.call(
            DOMAIN, "gws_rule.user_rule.update[add_group]",
            lambda: apm.gws.auto_backup_rules.update(
                _rule_add,
                mail_group_ids=[_TEST_GROUP_A, _TEST_GROUP_B],
            ),
        )
        list_after_add: GWSAutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "gws_rule.user_rule.list[after_add_group]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )
        if list_after_add is not None:
            updated = next((r for r in list_after_add.rules if r.uid == test_rule.uid), None)
            ctx.check(
                DOMAIN, "gws_rule.user_rule.check[add_group]",
                updated is not None
                and set(updated.mail_group_ids) == {_TEST_GROUP_A, _TEST_GROUP_B},
                note="Both groups must be present after update.",
            )
            if updated is not None:
                test_rule = updated

        # ── Update: remove group A ────────────────────────────────────────────
        _rule_remove = test_rule  # re-capture after possible reassignment above
        await ctx.call(
            DOMAIN, "gws_rule.user_rule.update[remove_group]",
            lambda: apm.gws.auto_backup_rules.update(
                _rule_remove,
                mail_group_ids=[_TEST_GROUP_B],
            ),
        )
        list_after_remove: GWSAutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "gws_rule.user_rule.list[after_remove_group]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )
        if list_after_remove is not None:
            updated = next((r for r in list_after_remove.rules if r.uid == test_rule.uid), None)
            ctx.check(
                DOMAIN, "gws_rule.user_rule.check[remove_group]",
                updated is not None and updated.mail_group_ids == (_TEST_GROUP_B,),
                note="Only GROUP_B must remain after removing GROUP_A.",
            )
            if updated is not None:
                test_rule = updated

        # ── Update: change plan ───────────────────────────────────────────────
        if plan_b is not None:
            _rule_cp = test_rule  # re-capture; _plan_b avoids lambda narrowing issue for plan_b
            _plan_b = plan_b
            await ctx.call(
                DOMAIN, "gws_rule.user_rule.update[change_plan]",
                lambda: apm.gws.auto_backup_rules.update(_rule_cp, plan_id=_plan_b.plan_id),
            )
            list_after_cp: GWSAutoBackupRuleListResult | None = await ctx.call(
                DOMAIN, "gws_rule.user_rule.list[after_change_plan]",
                lambda: apm.gws.auto_backup_rules.list(domain),
            )
            if list_after_cp is not None:
                updated = next((r for r in list_after_cp.rules if r.uid == test_rule.uid), None)
                ctx.check(
                    DOMAIN, "gws_rule.user_rule.check[change_plan]",
                    updated is not None and updated.plan_id == plan_b.plan_id,
                    note="plan_id must match plan_b after update.",
                )
                if updated is not None:
                    test_rule = updated
                    current_plan_id = plan_b.plan_id
        else:
            ctx.na(DOMAIN, "gws_rule.user_rule.update[change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "gws_rule.user_rule.list[after_change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "gws_rule.user_rule.check[change_plan]", "plan_b creation failed")

        # ── Duplicate create (expect APIError; see README's "needs live confirmation" note) ──
        # M365's equivalent asserts error_code == 400 and "should not have multiple" in the
        # message, discovered empirically (neither collection's create() docstring documents
        # this). Written here with the same expected shape by structural symmetry — confirm
        # against a live APM on first run and adjust if GWS's actual behavior differs.
        _cp = current_plan_id
        exc = await ctx.call_expect_error(
            DOMAIN, "gws_rule.user_rule.create[duplicate]",
            lambda: apm.gws.auto_backup_rules.create(
                domain=domain,
                namespace=namespace,
                plan_id=_cp,
            ),
            expect_error=APIError,
            note="Creating a rule with the same namespace+plan must be rejected.",
        )
        ctx.check(
            DOMAIN, "gws_rule.user_rule.check[duplicate_error_code]",
            exc is not None and exc.error_code == 400,
            note="Duplicate rule error must carry HTTP status 400.",
        )
        ctx.check(
            DOMAIN, "gws_rule.user_rule.check[duplicate_error_message]",
            exc is not None and "should not have multiple" in exc.message,
            note="Error message must describe the uniqueness constraint.",
        )

    finally:
        # ── Cleanup: delete test rule ─────────────────────────────────────────
        if test_rule is not None:
            _tr = test_rule
            await ctx.call(
                DOMAIN, "gws_rule.user_rule.delete",
                lambda: apm.gws.auto_backup_rules.delete(_tr),
            )
            list_after_del: GWSAutoBackupRuleListResult | None = await ctx.call(
                DOMAIN, "gws_rule.user_rule.list[after_delete]",
                lambda: apm.gws.auto_backup_rules.list(domain),
            )
            if list_after_del is not None:
                ctx.check(
                    DOMAIN, "gws_rule.user_rule.check[deleted]",
                    all(r.uid != _tr.uid for r in list_after_del.rules),
                    note="Deleted rule must not appear in subsequent list().",
                )
            else:
                ctx.na(DOMAIN, "gws_rule.user_rule.check[deleted]", "list[after_delete] failed")
        else:
            ctx.na(DOMAIN, "gws_rule.user_rule.delete", "No test rule was created")
            ctx.na(DOMAIN, "gws_rule.user_rule.list[after_delete]", "No test rule was created")
            ctx.na(DOMAIN, "gws_rule.user_rule.check[deleted]", "No test rule was created")


async def _run_collab_roundtrip(
    ctx: SmokeContext,
    apm: APMClient,
    domain: str,
    namespace: str,
    plan_a: ProtectionPlan,
    plan_b: ProtectionPlan | None,
) -> None:
    setting_a = GWSSharedDriveSetting(plan_id=plan_a.plan_id, namespace=namespace, backup_user_id="")

    try:
        # ── Baseline ──────────────────────────────────────────────────────────
        await ctx.call(
            DOMAIN, "gws_rule.collab.list[baseline]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )

        # ── Enable Shared Drives ──────────────────────────────────────────────
        await ctx.call(
            DOMAIN, "gws_rule.collab.update[enable]",
            lambda: apm.gws.auto_backup_rules.update_collab_settings(domain, shared_drive=setting_a),
        )
        list_after_enable: GWSAutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "gws_rule.collab.list[after_enable]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )
        if list_after_enable is not None:
            ctx.check(
                DOMAIN, "gws_rule.collab.check[shared_drive_enabled]",
                list_after_enable.shared_drive_setting is not None
                and list_after_enable.shared_drive_setting.enabled
                and list_after_enable.shared_drive_setting.plan_id == plan_a.plan_id,
                note="shared_drive must be enabled with plan_a after update.",
            )

        # ── Change plan on Shared Drives ──────────────────────────────────────
        if plan_b is not None:
            setting_b = GWSSharedDriveSetting(plan_id=plan_b.plan_id, namespace=namespace, backup_user_id="")
            await ctx.call(
                DOMAIN, "gws_rule.collab.update[change_plan]",
                lambda: apm.gws.auto_backup_rules.update_collab_settings(domain, shared_drive=setting_b),
            )
            list_after_cp: GWSAutoBackupRuleListResult | None = await ctx.call(
                DOMAIN, "gws_rule.collab.list[after_change_plan]",
                lambda: apm.gws.auto_backup_rules.list(domain),
            )
            if list_after_cp is not None:
                ctx.check(
                    DOMAIN, "gws_rule.collab.check[change_plan]",
                    list_after_cp.shared_drive_setting is not None
                    and list_after_cp.shared_drive_setting.plan_id == plan_b.plan_id,
                    note="shared_drive plan_id must reflect plan_b after change.",
                )
        else:
            ctx.na(DOMAIN, "gws_rule.collab.update[change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "gws_rule.collab.list[after_change_plan]", "plan_b creation failed")
            ctx.na(DOMAIN, "gws_rule.collab.check[change_plan]", "plan_b creation failed")

    finally:
        # ── Always disable the Shared Drive collab setting ────────────────────
        await ctx.call(
            DOMAIN, "gws_rule.collab.update[disable]",
            lambda: apm.gws.auto_backup_rules.update_collab_settings(domain),
        )
        list_after_disable: GWSAutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "gws_rule.collab.list[after_disable]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )
        if list_after_disable is not None:
            ctx.check(
                DOMAIN, "gws_rule.collab.check[all_disabled]",
                list_after_disable.shared_drive_setting is None
                or not list_after_disable.shared_drive_setting.enabled,
                note="shared_drive must be disabled after update_collab_settings() with no argument.",
            )


async def _run_protected_account_types_roundtrip(ctx: SmokeContext, apm: APMClient, domain: str) -> None:
    """Domain-wide unlicensed/archived-account inclusion setting — no M365 equivalent."""
    try:
        await ctx.call(
            DOMAIN, "gws_rule.protected_account_types.list[baseline]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )

        await ctx.call(
            DOMAIN, "gws_rule.protected_account_types.update[enable]",
            lambda: apm.gws.auto_backup_rules.update_protected_account_types(
                domain, include_unlicensed_accounts=True, include_archived_accounts=True,
            ),
        )
        list_after_enable: GWSAutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "gws_rule.protected_account_types.list[after_enable]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )
        if list_after_enable is not None:
            ctx.check(
                DOMAIN, "gws_rule.protected_account_types.check[unlicensed_enabled]",
                list_after_enable.include_unlicensed_accounts is True,
            )
            ctx.check(
                DOMAIN, "gws_rule.protected_account_types.check[archived_enabled]",
                list_after_enable.include_archived_accounts is True,
            )
    finally:
        await ctx.call(
            DOMAIN, "gws_rule.protected_account_types.update[disable]",
            lambda: apm.gws.auto_backup_rules.update_protected_account_types(
                domain, include_unlicensed_accounts=False, include_archived_accounts=False,
            ),
        )
        list_after_disable: GWSAutoBackupRuleListResult | None = await ctx.call(
            DOMAIN, "gws_rule.protected_account_types.list[after_disable]",
            lambda: apm.gws.auto_backup_rules.list(domain),
        )
        if list_after_disable is not None:
            ctx.check(
                DOMAIN, "gws_rule.protected_account_types.check[all_disabled]",
                not list_after_disable.include_unlicensed_accounts
                and not list_after_disable.include_archived_accounts,
            )
