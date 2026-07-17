"""Plan domain phase: apm.plans, apm.retirement_plans, apm.tiering_plans.

Runs first among the workload-related phases (before machine/m365) so that
ctx.data["protection_plans"] and ctx.data["retirement_plans"] are available to the machine
phase's apm.machine.workloads.change_plan() round trips. Reads ctx.data["remote_storages"]
(from the infra phase) for the tiering destination-resolution check.
"""
from __future__ import annotations

from datetime import time
from typing import TypedDict
from uuid import uuid4

from synology_apm.sdk import ProtectionPlan, RetirementPlan, TieringPlan, WorkloadCategory
from synology_apm.sdk.enums import DbActionOnError, RetentionType, ScheduleFrequency
from synology_apm.sdk.exceptions import PlanNameConflictError, ResourceNotFoundError
from synology_apm.sdk.models.protection_plan import (
    M365PlanCreateRequest,
    MachineDbConfig,
    MachinePlanCreateRequest,
    MachineVmConfig,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlanCreateRequest
from synology_apm.sdk.models.tiering_plan import TieringPlanCreateRequest

from .._context import SmokeContext
from ._shared import SENTINEL_NAME as _SENTINEL_NAME
from ._shared import ZERO_UUID as _ZERO_UUID

DOMAIN = "plan"


class _PlanReads(TypedDict):
    protection_plans: list[ProtectionPlan]
    machine_plans_by_facade: list[ProtectionPlan]
    machine_plans_by_collection: list[ProtectionPlan]
    m365_plans_by_facade: list[ProtectionPlan]
    m365_plans_by_collection: list[ProtectionPlan]
    machine_facade_ok: bool
    machine_coll_ok: bool
    m365_facade_ok: bool
    m365_coll_ok: bool
    by_name: ProtectionPlan | None
    retirement_plans: list[RetirementPlan]
    tiering_plans: list[TieringPlan]


async def run(ctx: SmokeContext) -> None:
    reads = await _run_reads(ctx)
    _run_checks(ctx, **reads)
    await _run_mutating_checks(ctx)


async def _run_reads(ctx: SmokeContext) -> _PlanReads:
    apm = ctx.apm

    all_result = await ctx.call(DOMAIN, "plan.protection.list[all]", lambda: apm.plans.list(limit=500))
    protection_plans, _total = all_result if all_result is not None else ([], 0)
    ctx.data["protection_plans"] = protection_plans

    machine_result = await ctx.call(
        DOMAIN, "plan.protection.list[machine]",
        lambda: apm.plans.list(category=WorkloadCategory.MACHINE, limit=500),
    )
    machine_plans_by_facade, _total = machine_result if machine_result is not None else ([], 0)

    m365_result = await ctx.call(
        DOMAIN, "plan.protection.list[m365]", lambda: apm.plans.list(category=WorkloadCategory.M365, limit=500)
    )
    m365_plans_by_facade, _total = m365_result if m365_result is not None else ([], 0)

    by_name: ProtectionPlan | None = None
    if protection_plans:
        p0 = protection_plans[0]
        await ctx.call(DOMAIN, "plan.protection.get[direct]", lambda: apm.plans.get(p0.plan_id))
        by_name = await ctx.call(
            DOMAIN, "plan.protection.get_by_name[search]", lambda: apm.plans.get_by_name(p0.name)
        )
    else:
        ctx.skip(DOMAIN, "plan.protection.get[direct]", "No Protection Plans found")
        ctx.skip(DOMAIN, "plan.protection.get_by_name[search]", "No Protection Plans found")

    machine_coll_result = await ctx.call(
        DOMAIN, "plan.machine.list",
        lambda: apm.machine.plans.list(limit=500),
    )
    machine_plans_by_collection, _total = (
        machine_coll_result if machine_coll_result is not None else ([], 0)
    )

    if machine_plans_by_facade:
        mp0 = machine_plans_by_facade[0]
        await ctx.call(
            DOMAIN, "plan.machine.get_by_name[search]",
            lambda: apm.machine.plans.get_by_name(mp0.name),
        )
    else:
        ctx.skip(DOMAIN, "plan.machine.get_by_name[search]", "No Machine Protection Plans found")

    m365_coll_result = await ctx.call(
        DOMAIN, "plan.m365.list",
        lambda: apm.m365.plans.list(limit=500),
    )
    m365_plans_by_collection, _total = (
        m365_coll_result if m365_coll_result is not None else ([], 0)
    )

    if m365_plans_by_facade:
        ep0 = m365_plans_by_facade[0]
        await ctx.call(
            DOMAIN, "plan.m365.get_by_name[search]",
            lambda: apm.m365.plans.get_by_name(ep0.name),
        )
    else:
        ctx.skip(DOMAIN, "plan.m365.get_by_name[search]", "No M365 Protection Plans found")

    retirement_result = await ctx.call(
        DOMAIN, "plan.retirement.list", lambda: apm.retirement_plans.list(limit=500)
    )
    retirement_plans, _total = retirement_result if retirement_result is not None else ([], 0)
    ctx.data["retirement_plans"] = retirement_plans

    if retirement_plans:
        r0 = retirement_plans[0]
        await ctx.call(DOMAIN, "plan.retirement.get[direct]", lambda: apm.retirement_plans.get(r0.plan_id))
        await ctx.call(
            DOMAIN, "plan.retirement.get_by_name[search]", lambda: apm.retirement_plans.get_by_name(r0.name)
        )
    else:
        ctx.skip(DOMAIN, "plan.retirement.get[direct]", "No Retirement Plans found")
        ctx.skip(DOMAIN, "plan.retirement.get_by_name[search]", "No Retirement Plans found")

    tiering_result = await ctx.call(DOMAIN, "plan.tiering.list", lambda: apm.tiering_plans.list(limit=500))
    tiering_plans, _total = tiering_result if tiering_result is not None else ([], 0)
    ctx.data["tiering_plans"] = tiering_plans

    if tiering_plans:
        t0 = tiering_plans[0]
        await ctx.call(DOMAIN, "plan.tiering.get[direct]", lambda: apm.tiering_plans.get(t0.plan_id))
        await ctx.call(
            DOMAIN, "plan.tiering.get_by_name[search]", lambda: apm.tiering_plans.get_by_name(t0.name)
        )
    else:
        ctx.skip(DOMAIN, "plan.tiering.get[direct]", "No Tiering Plans found")
        ctx.skip(DOMAIN, "plan.tiering.get_by_name[search]", "No Tiering Plans found")

    await ctx.call_expect_not_found(DOMAIN, "plan.protection", "get",
        lambda: apm.plans.get(_ZERO_UUID), "ProtectionPlan", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, "plan.machine", "get_by_name",
        lambda: apm.machine.plans.get_by_name(_SENTINEL_NAME), "ProtectionPlan", _SENTINEL_NAME)
    await ctx.call_expect_not_found(DOMAIN, "plan.m365", "get_by_name",
        lambda: apm.m365.plans.get_by_name(_SENTINEL_NAME), "ProtectionPlan", _SENTINEL_NAME)
    await ctx.call_expect_not_found(DOMAIN, "plan.retirement", "get",
        lambda: apm.retirement_plans.get(_ZERO_UUID), "RetirementPlan", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, "plan.retirement", "get_by_name",
        lambda: apm.retirement_plans.get_by_name(_SENTINEL_NAME), "RetirementPlan", _SENTINEL_NAME)
    await ctx.call_expect_not_found(DOMAIN, "plan.tiering", "get",
        lambda: apm.tiering_plans.get(_ZERO_UUID), "TieringPlan", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, "plan.tiering", "get_by_name",
        lambda: apm.tiering_plans.get_by_name(_SENTINEL_NAME), "TieringPlan", _SENTINEL_NAME)

    return _PlanReads(
        protection_plans=protection_plans,
        machine_plans_by_facade=machine_plans_by_facade,
        machine_plans_by_collection=machine_plans_by_collection,
        m365_plans_by_facade=m365_plans_by_facade,
        m365_plans_by_collection=m365_plans_by_collection,
        machine_facade_ok=machine_result is not None,
        machine_coll_ok=machine_coll_result is not None,
        m365_facade_ok=m365_result is not None,
        m365_coll_ok=m365_coll_result is not None,
        by_name=by_name,
        retirement_plans=retirement_plans,
        tiering_plans=tiering_plans,
    )


def _run_checks(
    ctx: SmokeContext,
    *,
    protection_plans: list[ProtectionPlan],
    machine_plans_by_facade: list[ProtectionPlan],
    machine_plans_by_collection: list[ProtectionPlan],
    m365_plans_by_facade: list[ProtectionPlan],
    m365_plans_by_collection: list[ProtectionPlan],
    machine_facade_ok: bool,
    machine_coll_ok: bool,
    m365_facade_ok: bool,
    m365_coll_ok: bool,
    by_name: ProtectionPlan | None,
    retirement_plans: list[RetirementPlan],
    tiering_plans: list[TieringPlan],
) -> None:
    ctx.check(
        DOMAIN, "plan.protection.check[category_partition]",
        all(p.category == WorkloadCategory.MACHINE for p in machine_plans_by_facade)
        and all(p.category == WorkloadCategory.M365 for p in m365_plans_by_facade),
    )

    if protection_plans and by_name is not None:
        p0 = protection_plans[0]
        ctx.check(
            DOMAIN, "plan.protection.check[get_by_name_consistency]", by_name.plan_id == p0.plan_id,
        )
    else:
        ctx.skip(DOMAIN, "plan.protection.check[get_by_name_consistency]", "No Protection Plans found")

    if machine_facade_ok and machine_coll_ok:
        machine_facade_ids = {p.plan_id for p in machine_plans_by_facade}
        machine_coll_ids = {p.plan_id for p in machine_plans_by_collection}
        ctx.check(
            DOMAIN, "plan.machine.check[list_id_consistency]",
            machine_facade_ids == machine_coll_ids,
            note="machine.plans.list() must return the same plan IDs as plans.list(category=MACHINE).",
        )
    else:
        ctx.skip(DOMAIN, "plan.machine.check[list_id_consistency]", "One or both machine plan list calls failed")

    if m365_facade_ok and m365_coll_ok:
        m365_facade_ids = {p.plan_id for p in m365_plans_by_facade}
        m365_coll_ids = {p.plan_id for p in m365_plans_by_collection}
        ctx.check(
            DOMAIN, "plan.m365.check[list_id_consistency]",
            m365_facade_ids == m365_coll_ids,
            note="m365.plans.list() must return the same plan IDs as plans.list(category=M365).",
        )
    else:
        ctx.skip(DOMAIN, "plan.m365.check[list_id_consistency]", "One or both M365 plan list calls failed")

    ctx.check(
        DOMAIN, "plan.retirement.check[workload_count_nonneg]",
        all(r.workload_count is not None and r.workload_count >= 0 for r in retirement_plans),
    )

    if tiering_plans:
        remote_storage_ids = {s.storage_id for s in ctx.data.get("remote_storages", [])}
        ctx.check(
            DOMAIN, "plan.tiering.check[destination_resolution]",
            all(
                p.destination is None
                or (p.destination.is_remote_storage and p.destination.identifier in remote_storage_ids)
                for p in tiering_plans
            ),
        )
    else:
        ctx.skip(DOMAIN, "plan.tiering.check[destination_resolution]", "No Tiering Plans found")


async def _run_mutating_checks(ctx: SmokeContext) -> None:
    """Create / update / delete round-trips for all four plan types."""
    apm = ctx.apm
    uid = uuid4().hex[:8]

    _keep_days = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    _daily_03 = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0))

    # ── Client-side validation (ValueError, no API calls) ─────────────────────
    await ctx.call_expect_value_error(
        DOMAIN, "plan.machine.create[immutable_keep_all_raises]",
        lambda: apm.machine.plans.create(MachinePlanCreateRequest(
            name=f"smoke-machine-{uid}-immutable",
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ALL),
            schedule=_daily_03,
            is_immutable=True,
        )),
        note="is_immutable=True with KEEP_ALL retention must raise ValueError.",
    )
    await ctx.call_expect_value_error(
        DOMAIN, "plan.machine.create[weekly_no_weekdays_raises]",
        lambda: apm.machine.plans.create(MachinePlanCreateRequest(
            name=f"smoke-machine-{uid}-weekly",
            retention=_keep_days,
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.WEEKLY, start_time=None, weekdays=()),
        )),
        note="WEEKLY schedule with weekdays=() must raise ValueError.",
    )

    # ── Machine plan ──────────────────────────────────────────────────────────
    machine_plan: ProtectionPlan | None = None
    machine_plan = await ctx.call(DOMAIN, "plan.machine.create", lambda: apm.machine.plans.create(
        MachinePlanCreateRequest(
            name=f"smoke-machine-{uid}",
            retention=_keep_days,
            schedule=_daily_03,
            vm_config=MachineVmConfig(enable_verification=True, verification_video_duration_seconds=60),
            db_config=MachineDbConfig(action_on_error=DbActionOnError.STOP),
        )
    ))
    if machine_plan is not None:
        ctx.check(
            DOMAIN, "plan.machine.check[create_name_roundtrip]",
            machine_plan.name == f"smoke-machine-{uid}",
        )
        _mp_name = machine_plan.name
        conflict_name_exc = await ctx.call_expect_error(
            DOMAIN, "plan.machine.create[duplicate_name]",
            lambda: apm.machine.plans.create(MachinePlanCreateRequest(
                name=_mp_name,
                retention=_keep_days,
                schedule=_daily_03,
            )),
            PlanNameConflictError,
        )
        ctx.check_exc_attr(DOMAIN, "plan.machine.check[duplicate_name_resource_type]",
            conflict_name_exc, "resource_type", "ProtectionPlan")
        ctx.check_exc_attr(DOMAIN, "plan.machine.check[duplicate_name_resource_id]",
            conflict_name_exc, "resource_id", _mp_name)

        conflict_plan: ProtectionPlan | None = None
        try:
            conflict_plan = await ctx.call(
                DOMAIN, "plan.machine.create[conflict_target]",
                lambda: apm.machine.plans.create(MachinePlanCreateRequest(
                    name=f"smoke-machine-{uid}-ct",
                    retention=_keep_days,
                    schedule=_daily_03,
                )),
            )
            if conflict_plan is not None:
                _cp = conflict_plan
                dup_upd_exc = await ctx.call_expect_error(
                    DOMAIN, "plan.machine.update[duplicate_name]",
                    lambda: apm.machine.plans.update(
                        _cp.plan_id,
                        MachinePlanCreateRequest(
                            name=_mp_name,
                            retention=_keep_days,
                            schedule=_daily_03,
                        ),
                    ),
                    PlanNameConflictError,
                )
                ctx.check_exc_attr(DOMAIN, "plan.machine.check[duplicate_update_resource_type]",
                    dup_upd_exc, "resource_type", "ProtectionPlan")
                ctx.check_exc_attr(DOMAIN, "plan.machine.check[duplicate_update_resource_id]",
                    dup_upd_exc, "resource_id", _mp_name)
            else:
                ctx.skip(DOMAIN, "plan.machine.update[duplicate_name]", "conflict_target creation failed")
                ctx.skip(DOMAIN, "plan.machine.check[duplicate_update_resource_type]", "conflict_target creation failed")
                ctx.skip(DOMAIN, "plan.machine.check[duplicate_update_resource_id]", "conflict_target creation failed")
        finally:
            if conflict_plan is not None:
                _cp_del = conflict_plan
                await ctx.call(
                    DOMAIN, "plan.machine.delete[conflict_target]",
                    lambda: apm.machine.plans.delete(_cp_del),
                )
            else:
                ctx.skip(DOMAIN, "plan.machine.delete[conflict_target]", "conflict_target was not created")

        fetched = await ctx.call(
            DOMAIN, "plan.machine.get[post_create]",
            lambda: apm.machine.plans.get(machine_plan.plan_id),
        )
        ctx.check(
            DOMAIN, "plan.machine.check[tasks_6_entries]",
            fetched is not None and fetched.tasks is not None and len(fetched.tasks) == 6,
        )
        ctx.check(
            DOMAIN, "plan.machine.check[vm_config_roundtrip]",
            fetched is not None and fetched.vm_config is not None
            and fetched.vm_config.verification_video_duration_seconds == 60,
        )
        ctx.check(
            DOMAIN, "plan.machine.check[db_config_roundtrip]",
            fetched is not None and fetched.db_config is not None
            and fetched.db_config.action_on_error == DbActionOnError.STOP,
        )
        updated_machine = await ctx.call(
            DOMAIN, "plan.machine.update",
            lambda: apm.machine.plans.update(
                machine_plan.plan_id,
                MachinePlanCreateRequest(
                    name=f"smoke-machine-{uid}-updated",
                    retention=_keep_days,
                    schedule=_daily_03,
                ),
            ),
        )
        ctx.check(
            DOMAIN, "plan.machine.check[update_name]",
            updated_machine is not None and updated_machine.name == f"smoke-machine-{uid}-updated",
        )
        await ctx.call(
            DOMAIN, "plan.machine.delete",
            lambda: apm.machine.plans.delete(machine_plan),
        )
        machine_plan_id = machine_plan.plan_id
        del_exc_machine = await ctx.call_expect_error(
            DOMAIN, "plan.machine.get[post_delete]",
            lambda: apm.machine.plans.get(machine_plan_id),
            ResourceNotFoundError,
        )
        ctx.check_exc_attr(DOMAIN, "plan.machine.check[post_delete_resource_type]",
            del_exc_machine, "resource_type", "ProtectionPlan")
        ctx.check_exc_attr(DOMAIN, "plan.machine.check[post_delete_resource_id]",
            del_exc_machine, "resource_id", machine_plan_id)

    # ── M365 plan ─────────────────────────────────────────────────────────────
    m365_plan: ProtectionPlan | None = None
    m365_plan = await ctx.call(DOMAIN, "plan.m365.create", lambda: apm.m365.plans.create(
        M365PlanCreateRequest(
            name=f"smoke-m365-{uid}",
            retention=_keep_days,
            schedule=_daily_03,
        )
    ))
    if m365_plan is not None:
        ctx.check(
            DOMAIN, "plan.m365.check[create_name_roundtrip]",
            m365_plan.name == f"smoke-m365-{uid}",
        )
        ctx.check(
            DOMAIN, "plan.m365.check[category]",
            m365_plan.category == WorkloadCategory.M365,
        )
        fetched_m365 = await ctx.call(
            DOMAIN, "plan.m365.get[post_create]",
            lambda: apm.m365.plans.get(m365_plan.plan_id),
        )
        ctx.check(
            DOMAIN, "plan.m365.check[get_schedule]",
            fetched_m365 is not None and fetched_m365.policy is not None
            and fetched_m365.policy.schedule is not None
            and fetched_m365.policy.schedule.frequency == ScheduleFrequency.DAILY,
        )
        updated_m365 = await ctx.call(
            DOMAIN, "plan.m365.update",
            lambda: apm.m365.plans.update(
                m365_plan.plan_id,
                M365PlanCreateRequest(
                    name=f"smoke-m365-{uid}-updated",
                    retention=_keep_days,
                    schedule=_daily_03,
                ),
            ),
        )
        ctx.check(
            DOMAIN, "plan.m365.check[update_name]",
            updated_m365 is not None and updated_m365.name == f"smoke-m365-{uid}-updated",
        )
        await ctx.call(
            DOMAIN, "plan.m365.delete",
            lambda: apm.m365.plans.delete(m365_plan),
        )
        m365_plan_id = m365_plan.plan_id
        del_exc_m365 = await ctx.call_expect_error(
            DOMAIN, "plan.m365.get[post_delete]",
            lambda: apm.m365.plans.get(m365_plan_id),
            ResourceNotFoundError,
        )
        ctx.check_exc_attr(DOMAIN, "plan.m365.check[post_delete_resource_type]",
            del_exc_m365, "resource_type", "ProtectionPlan")
        ctx.check_exc_attr(DOMAIN, "plan.m365.check[post_delete_resource_id]",
            del_exc_m365, "resource_id", m365_plan_id)

    # ── Retirement plan ───────────────────────────────────────────────────────
    ret_plan: RetirementPlan | None = None
    ret_plan = await ctx.call(DOMAIN, "plan.retirement.create", lambda: apm.retirement_plans.create(
        RetirementPlanCreateRequest(name=f"smoke-ret-{uid}", retention_days=30, keep_latest_version=True)
    ))
    if ret_plan is not None:
        ctx.check(
            DOMAIN, "plan.retirement.check[create_name_roundtrip]",
            ret_plan.name == f"smoke-ret-{uid}",
        )
        fetched_ret = await ctx.call(
            DOMAIN, "plan.retirement.get[post_create]",
            lambda: apm.retirement_plans.get(ret_plan.plan_id),
        )
        ctx.check(
            DOMAIN, "plan.retirement.check[retention_roundtrip]",
            fetched_ret is not None and fetched_ret.retention is not None
            and fetched_ret.retention.days == 30 and fetched_ret.retention.keep_latest_version is True,
        )
        updated_ret = await ctx.call(
            DOMAIN, "plan.retirement.update",
            lambda: apm.retirement_plans.update(
                ret_plan.plan_id,
                RetirementPlanCreateRequest(
                    name=f"smoke-ret-{uid}-updated", retention_days=60, keep_latest_version=False
                ),
            ),
        )
        ctx.check(
            DOMAIN, "plan.retirement.check[update_retention]",
            updated_ret is not None and updated_ret.retention is not None
            and updated_ret.retention.days == 60,
        )
        await ctx.call(
            DOMAIN, "plan.retirement.delete",
            lambda: apm.retirement_plans.delete(ret_plan),
        )
        ret_plan_id = ret_plan.plan_id
        del_exc_ret = await ctx.call_expect_error(
            DOMAIN, "plan.retirement.get[post_delete]",
            lambda: apm.retirement_plans.get(ret_plan_id),
            ResourceNotFoundError,
        )
        ctx.check_exc_attr(DOMAIN, "plan.retirement.check[post_delete_resource_type]",
            del_exc_ret, "resource_type", "RetirementPlan")
        ctx.check_exc_attr(DOMAIN, "plan.retirement.check[post_delete_resource_id]",
            del_exc_ret, "resource_id", ret_plan_id)

    # ── Tiering plan ──────────────────────────────────────────────────────────
    remote_storages = ctx.data.get("remote_storages", [])
    if remote_storages:
        tier_plan: TieringPlan | None = None
        tier_plan = await ctx.call(DOMAIN, "plan.tiering.create", lambda: apm.tiering_plans.create(
            TieringPlanCreateRequest(
                name=f"smoke-tier-{uid}",
                tiering_after_days=30,
                destination=remote_storages[0],
                daily_check_time=time(20, 0),
            )
        ))
        if tier_plan is not None:
            ctx.check(
                DOMAIN, "plan.tiering.check[create_name_roundtrip]",
                tier_plan.name == f"smoke-tier-{uid}",
            )
            ctx.check(
                DOMAIN, "plan.tiering.check[tiering_after_days]",
                tier_plan.tiering_after_days == 30,
            )
            fetched_tier = await ctx.call(
                DOMAIN, "plan.tiering.get[post_create]",
                lambda: apm.tiering_plans.get(tier_plan.plan_id),
            )
            ctx.check(
                DOMAIN, "plan.tiering.check[get_roundtrip]",
                fetched_tier is not None and fetched_tier.tiering_after_days == 30,
            )
            updated_tier = await ctx.call(
                DOMAIN, "plan.tiering.update",
                lambda: apm.tiering_plans.update(
                    tier_plan.plan_id,
                    TieringPlanCreateRequest(
                        name=f"smoke-tier-{uid}-updated",
                        tiering_after_days=60,
                        destination=remote_storages[0],
                        daily_check_time=time(20, 0),
                    ),
                ),
            )
            ctx.check(
                DOMAIN, "plan.tiering.check[update_tiering_after_days]",
                updated_tier is not None and updated_tier.tiering_after_days == 60,
            )
            await ctx.call(
                DOMAIN, "plan.tiering.delete",
                lambda: apm.tiering_plans.delete(tier_plan),
            )
            tier_plan_id = tier_plan.plan_id
            del_exc_tier = await ctx.call_expect_error(
                DOMAIN, "plan.tiering.get[post_delete]",
                lambda: apm.tiering_plans.get(tier_plan_id),
                ResourceNotFoundError,
            )
            ctx.check_exc_attr(DOMAIN, "plan.tiering.check[post_delete_resource_type]",
                del_exc_tier, "resource_type", "TieringPlan")
            ctx.check_exc_attr(DOMAIN, "plan.tiering.check[post_delete_resource_id]",
                del_exc_tier, "resource_id", tier_plan_id)
    else:
        for op in ["create", "get[post_create]", "update", "check[create_name_roundtrip]",
                   "check[tiering_after_days]", "check[get_roundtrip]", "check[update_tiering_after_days]",
                   "delete", "get[post_delete]", "check[post_delete_resource_type]",
                   "check[post_delete_resource_id]"]:
            ctx.skip(DOMAIN, f"plan.tiering.{op}", "No remote storages available")
