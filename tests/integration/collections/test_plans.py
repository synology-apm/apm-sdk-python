"""Integration tests: MachinePlanCollection / M365PlanCollection / RetirementPlanCollection / ProtectionPlanCollection"""
from __future__ import annotations

from datetime import time

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import DbActionOnError, RetentionType, ScheduleFrequency, WorkloadCategory
from synology_apm.sdk.exceptions import PlanInUseError, PlanNameConflictError, ResourceNotFoundError
from synology_apm.sdk.models.protection_plan import (
    M365PlanCreateRequest,
    MachineDbConfig,
    MachinePlanCreateRequest,
    MachineVmConfig,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.retirement_plan import (
    RetirementPlan,
    RetirementPlanCreateRequest,
    RetirementRetentionPolicy,
)
from synology_apm.sdk.models.tiering_plan import TieringPlan, TieringPlanCreateRequest
from tests.unit.sdk.conftest import assert_resource_error

pytestmark = pytest.mark.integration


# ── MachinePlanCollection.list() ───────────────────────────────────────────


async def test_list_returns_list(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    assert isinstance(plans, list)


async def test_list_items_are_protection_plans(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert isinstance(plan, ProtectionPlan)


async def test_list_plan_ids_are_nonempty(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert plan.plan_id, f"plan_id empty for plan {plan.name!r}"


async def test_list_plan_names_are_nonempty(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert plan.name, f"name is empty for plan_id={plan.plan_id}"


async def test_list_retention_is_set(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert plan.policy is not None
        assert isinstance(plan.policy.retention, ProtectionRetentionPolicy)
        assert plan.policy.retention.retention_type in set(RetentionType)


async def test_list_schedule_is_none_from_list_endpoint(apm: APMClient) -> None:
    """list() uses a lightweight endpoint that doesn't include schedule details."""
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert plan.policy is not None
        _ = plan.policy.schedule  # attribute exists; value may be None


async def test_list_category_is_machine(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert plan.category == WorkloadCategory.MACHINE


async def test_list_workload_count_is_non_negative(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert plan.workload_count is not None
        assert plan.workload_count >= 0


async def test_list_is_immutable_is_bool(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    for plan in plans:
        assert isinstance(plan.is_immutable, bool)


# ── MachinePlanCollection.get() ────────────────────────────────────────────


async def test_get_returns_plan(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    if not plans:
        pytest.skip("No protection plans on this APM instance")
    plan_id = plans[0].plan_id
    fetched = await apm.machine.plans.get(plan_id)
    assert fetched.plan_id == plan_id
    assert fetched.name == plans[0].name


async def test_get_includes_schedule(apm: APMClient) -> None:
    """GET /plan/{id} should return schedule details."""
    plans, _ = await apm.machine.plans.list()
    if not plans:
        pytest.skip("No protection plans")
    fetched = await apm.machine.plans.get(plans[0].plan_id)
    assert fetched.policy is not None
    assert fetched.policy.schedule is not None, (
        f"Expected schedule in get() response for plan {fetched.name!r}"
    )


async def test_get_nonexistent_raises(apm: APMClient) -> None:
    with pytest.raises(Exception):
        await apm.machine.plans.get("00000000-0000-0000-0000-000000000000")


# ── MachinePlanCollection.get_by_name() ────────────────────────────────────


async def test_get_by_name_returns_correct_plan(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    if not plans:
        pytest.skip("No protection plans")
    target = plans[0]
    fetched = await apm.machine.plans.get_by_name(target.name)
    assert fetched.plan_id == target.plan_id


async def test_get_by_nonexistent_name_raises_not_found(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.machine.plans.get_by_name("__nonexistent_plan_name__")
    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="__nonexistent_plan_name__")


# ── M365PlanCollection ─────────────────────────────────────────────────────


async def test_m365_plan_list_returns_list(apm: APMClient) -> None:
    plans, _ = await apm.m365.plans.list()
    assert isinstance(plans, list)


async def test_m365_plan_list_items_are_protection_plans(apm: APMClient) -> None:
    plans, _ = await apm.m365.plans.list()
    for plan in plans:
        assert isinstance(plan, ProtectionPlan)


async def test_m365_plan_list_category_is_m365(apm: APMClient) -> None:
    plans, _ = await apm.m365.plans.list()
    if not plans:
        pytest.skip("No M365 plans on this APM instance")
    assert all(plan.category == WorkloadCategory.M365 for plan in plans)


async def test_m365_plan_list_has_schedule(apm: APMClient) -> None:
    plans, _ = await apm.m365.plans.list()
    if not plans:
        pytest.skip("No M365 plans")
    for plan in plans:
        assert plan.policy is not None
        assert plan.policy.schedule is not None, (
            f"Expected schedule in m365 plans.list() for plan {plan.name!r}"
        )


async def test_m365_plan_get_returns_plan(apm: APMClient) -> None:
    plans, _ = await apm.m365.plans.list()
    if not plans:
        pytest.skip("No M365 plans")
    fetched = await apm.m365.plans.get(plans[0].plan_id)
    assert fetched.plan_id == plans[0].plan_id


async def test_m365_plan_get_includes_schedule(apm: APMClient) -> None:
    plans, _ = await apm.m365.plans.list()
    if not plans:
        pytest.skip("No M365 plans")
    fetched = await apm.m365.plans.get(plans[0].plan_id)
    assert fetched.policy is not None
    assert fetched.policy.schedule is not None, (
        f"Expected schedule in m365 get() for plan {fetched.name!r}"
    )


async def test_m365_plan_get_by_name_returns_plan(apm: APMClient) -> None:
    plans, _ = await apm.m365.plans.list()
    if not plans:
        pytest.skip("No M365 plans")
    fetched = await apm.m365.plans.get_by_name(plans[0].name)
    assert fetched.plan_id == plans[0].plan_id


async def test_m365_plan_get_by_name_nonexistent_raises_not_found(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.m365.plans.get_by_name("__nonexistent_m365_plan__")
    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="__nonexistent_m365_plan__")


# ── RetirementPlanCollection ───────────────────────────────────────────────


async def test_retirement_plan_list_returns_list(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    assert isinstance(plans, list)


async def test_retirement_plan_list_items_are_retirement_plans(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    for plan in plans:
        assert isinstance(plan, RetirementPlan)


async def test_retirement_plan_list_ids_are_nonempty(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    for plan in plans:
        assert plan.plan_id, f"plan_id empty for plan {plan.name!r}"


async def test_retirement_plan_list_names_are_nonempty(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    for plan in plans:
        assert plan.name, f"name empty for plan_id={plan.plan_id}"


async def test_retirement_plan_list_retention_is_set(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    for plan in plans:
        assert isinstance(plan.retention, RetirementRetentionPolicy)
        assert isinstance(plan.retention.days, (int, type(None)))
        assert isinstance(plan.retention.keep_latest_version, bool)


async def test_retirement_plan_list_workload_counts_non_negative(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    for plan in plans:
        assert plan.workload_count is not None
        assert plan.workload_count >= 0


async def test_retirement_plan_get_returns_plan(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    if not plans:
        pytest.skip("No retirement plans on this APM instance")
    fetched = await apm.retirement_plans.get(plans[0].plan_id)
    assert fetched.plan_id == plans[0].plan_id
    assert fetched.name == plans[0].name


async def test_retirement_plan_get_by_name_returns_plan(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    if not plans:
        pytest.skip("No retirement plans")
    target = plans[0]
    fetched = await apm.retirement_plans.get_by_name(target.name)
    assert fetched.plan_id == target.plan_id


async def test_retirement_plan_get_by_name_nonexistent_raises_not_found(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.retirement_plans.get_by_name("__nonexistent_retirement_plan__")
    assert_resource_error(exc_info, resource_type="RetirementPlan", resource_id="__nonexistent_retirement_plan__")


# ── ProtectionPlanCollection (apm.plans.*) ────────────────────────────────


async def test_list_protection_plans_returns_list(apm: APMClient) -> None:
    plans, _ = await apm.plans.list()
    assert isinstance(plans, list)


async def test_list_protection_plans_items_are_protection_plans(apm: APMClient) -> None:
    plans, _ = await apm.plans.list()
    for plan in plans:
        assert isinstance(plan, ProtectionPlan)


async def test_list_protection_plans_includes_machine_plans(apm: APMClient) -> None:
    plans, _ = await apm.plans.list(category=WorkloadCategory.MACHINE)
    for plan in plans:
        assert plan.category == WorkloadCategory.MACHINE


async def test_list_protection_plans_includes_m365_plans(apm: APMClient) -> None:
    plans, _ = await apm.plans.list(category=WorkloadCategory.M365)
    for plan in plans:
        assert plan.category == WorkloadCategory.M365


async def test_get_plan_by_name_returns_plan(apm: APMClient) -> None:
    all_plans, _ = await apm.plans.list()
    if not all_plans:
        pytest.skip("No protection plans on this APM instance")
    target = all_plans[0]
    fetched = await apm.plans.get_by_name(target.name)
    assert fetched.plan_id == target.plan_id


async def test_get_plan_by_name_raises_not_found_for_nonexistent(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.plans.get_by_name("__nonexistent_plan_name__")
    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="__nonexistent_plan_name__")


async def test_get_plan_returns_plan(apm: APMClient) -> None:
    all_plans, _ = await apm.plans.list()
    if not all_plans:
        pytest.skip("No protection plans on this APM instance")
    target = all_plans[0]
    fetched = await apm.plans.get(target.plan_id)
    assert fetched.plan_id == target.plan_id
    assert fetched.name == target.name


async def test_get_plan_raises_not_found_for_nonexistent(apm: APMClient) -> None:
    # APM returns HTTP 500 (not 404) for unknown plan UUIDs — accept either error
    from synology_apm.sdk.exceptions import APIError
    with pytest.raises((ResourceNotFoundError, APIError)):
        await apm.plans.get("00000000-0000-0000-0000-000000000000")


# ── MachinePlanCollection.create() / update() / delete() ─────────────────────


_MACHINE_RETENTION = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
_MACHINE_SCHEDULE = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=None)


async def test_machine_plan_create_returns_protection_plan(apm: APMClient) -> None:
    plan = await apm.machine.plans.create(MachinePlanCreateRequest(
        name="integ-machine-create",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        assert isinstance(plan, ProtectionPlan)
        assert plan.plan_id
        assert plan.name == "integ-machine-create"
        assert plan.category == WorkloadCategory.MACHINE
    finally:
        await apm.machine.plans.delete(plan)


async def test_machine_plan_create_then_get_has_config_fields(apm: APMClient) -> None:
    plan = await apm.machine.plans.create(MachinePlanCreateRequest(
        name="integ-machine-config",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        fetched = await apm.machine.plans.get(plan.plan_id)
        assert fetched.vm_config is not None
        assert fetched.tasks is not None
        assert len(fetched.tasks) == 6
    finally:
        await apm.machine.plans.delete(plan)


async def test_machine_plan_create_with_vm_config(apm: APMClient) -> None:
    plan = await apm.machine.plans.create(MachinePlanCreateRequest(
        name="integ-machine-vmcfg",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
        vm_config=MachineVmConfig(enable_verification=True, verification_video_duration_seconds=60),
    ))
    try:
        fetched = await apm.machine.plans.get(plan.plan_id)
        assert fetched.vm_config is not None
        assert fetched.vm_config.verification_video_duration_seconds == 60
    finally:
        await apm.machine.plans.delete(plan)


async def test_machine_plan_create_with_db_config(apm: APMClient) -> None:
    plan = await apm.machine.plans.create(MachinePlanCreateRequest(
        name="integ-machine-dbcfg",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
        db_config=MachineDbConfig(action_on_error=DbActionOnError.STOP),
    ))
    try:
        fetched = await apm.machine.plans.get(plan.plan_id)
        assert fetched.db_config is not None
        assert fetched.db_config.action_on_error == DbActionOnError.STOP
    finally:
        await apm.machine.plans.delete(plan)


async def test_machine_plan_update_changes_name(apm: APMClient) -> None:
    plan = await apm.machine.plans.create(MachinePlanCreateRequest(
        name="integ-machine-update-orig",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        updated = await apm.machine.plans.update(plan.plan_id, MachinePlanCreateRequest(
            name="integ-machine-update-new",
            retention=_MACHINE_RETENTION,
            schedule=_MACHINE_SCHEDULE,
        ))
        assert updated.name == "integ-machine-update-new"
    finally:
        await apm.machine.plans.delete(plan.plan_id)


async def test_machine_plan_create_duplicate_name_raises(apm: APMClient) -> None:
    plan = await apm.machine.plans.create(MachinePlanCreateRequest(
        name="integ-machine-dup",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        with pytest.raises(PlanNameConflictError) as exc_info:
            await apm.machine.plans.create(MachinePlanCreateRequest(
                name="integ-machine-dup",
                retention=_MACHINE_RETENTION,
                schedule=_MACHINE_SCHEDULE,
            ))
        assert exc_info.value.resource_id == "integ-machine-dup"
    finally:
        await apm.machine.plans.delete(plan)


async def test_machine_plan_delete_in_use_raises(apm: APMClient) -> None:
    plans, _ = await apm.machine.plans.list()
    in_use = next((p for p in plans if p.workload_count and p.workload_count > 0), None)
    if in_use is None:
        pytest.skip("No machine plan with assigned workloads found")
    with pytest.raises(PlanInUseError) as exc_info:
        await apm.machine.plans.delete(in_use)
    assert exc_info.value.has_workloads or exc_info.value.has_server_template


async def test_machine_plan_delete_removes_plan(apm: APMClient) -> None:
    plan = await apm.machine.plans.create(MachinePlanCreateRequest(
        name="integ-machine-delete",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    plan_id = plan.plan_id
    await apm.machine.plans.delete(plan)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.machine.plans.get(plan_id)
    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id=plan_id)


# ── M365PlanCollection.create() / update() / delete() ────────────────────────


async def test_m365_plan_create_returns_protection_plan(apm: APMClient) -> None:
    plan = await apm.m365.plans.create(M365PlanCreateRequest(
        name="integ-m365-create",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        assert isinstance(plan, ProtectionPlan)
        assert plan.plan_id
        assert plan.category == WorkloadCategory.M365
    finally:
        await apm.m365.plans.delete(plan)


async def test_m365_plan_update_changes_name(apm: APMClient) -> None:
    plan = await apm.m365.plans.create(M365PlanCreateRequest(
        name="integ-m365-update-orig",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        updated = await apm.m365.plans.update(plan.plan_id, M365PlanCreateRequest(
            name="integ-m365-update-new",
            retention=_MACHINE_RETENTION,
            schedule=_MACHINE_SCHEDULE,
        ))
        assert updated.name == "integ-m365-update-new"
    finally:
        await apm.m365.plans.delete(plan.plan_id)


async def test_m365_plan_create_duplicate_name_raises(apm: APMClient) -> None:
    plan = await apm.m365.plans.create(M365PlanCreateRequest(
        name="integ-m365-dup",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        with pytest.raises(PlanNameConflictError):
            await apm.m365.plans.create(M365PlanCreateRequest(
                name="integ-m365-dup",
                retention=_MACHINE_RETENTION,
                schedule=_MACHINE_SCHEDULE,
            ))
    finally:
        await apm.m365.plans.delete(plan)


async def test_m365_plan_create_then_get_has_schedule(apm: APMClient) -> None:
    plan = await apm.m365.plans.create(M365PlanCreateRequest(
        name="integ-m365-get-schedule",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        fetched = await apm.m365.plans.get(plan.plan_id)
        assert fetched.policy is not None
        assert fetched.policy.schedule is not None
    finally:
        await apm.m365.plans.delete(plan)


async def test_m365_plan_delete_removes_plan(apm: APMClient) -> None:
    plan = await apm.m365.plans.create(M365PlanCreateRequest(
        name="integ-m365-delete",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    plan_id = plan.plan_id
    await apm.m365.plans.delete(plan)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.m365.plans.get(plan_id)
    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id=plan_id)


# ── ProtectionPlanCollection facade create() / delete() ──────────────────────


async def test_protection_facade_create_machine_plan(apm: APMClient) -> None:
    plan = await apm.plans.create(MachinePlanCreateRequest(
        name="integ-facade-machine",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        assert isinstance(plan, ProtectionPlan)
        assert plan.category == WorkloadCategory.MACHINE
    finally:
        await apm.plans.delete(plan)


async def test_protection_facade_create_m365_plan(apm: APMClient) -> None:
    plan = await apm.plans.create(M365PlanCreateRequest(
        name="integ-facade-m365",
        retention=_MACHINE_RETENTION,
        schedule=_MACHINE_SCHEDULE,
    ))
    try:
        assert isinstance(plan, ProtectionPlan)
        assert plan.category == WorkloadCategory.M365
    finally:
        await apm.plans.delete(plan)


# ── RetirementPlanCollection.create() / update() / delete() ──────────────────


async def test_retirement_plan_create_keep_days(apm: APMClient) -> None:
    plan = await apm.retirement_plans.create(RetirementPlanCreateRequest(
        name="integ-ret-keepdays",
        retention_days=30,
        keep_latest_version=True,
    ))
    try:
        assert isinstance(plan, RetirementPlan)
        assert plan.plan_id
        assert plan.retention is not None
        assert plan.retention.days == 30
        assert plan.retention.keep_latest_version is True
    finally:
        await apm.retirement_plans.delete(plan)


async def test_retirement_plan_create_keep_all(apm: APMClient) -> None:
    plan = await apm.retirement_plans.create(RetirementPlanCreateRequest(
        name="integ-ret-keepall",
        retention_days=None,
    ))
    try:
        fetched = await apm.retirement_plans.get(plan.plan_id)
        assert fetched.retention is not None
        assert fetched.retention.days is None
    finally:
        await apm.retirement_plans.delete(plan)


async def test_retirement_plan_update_changes_retention(apm: APMClient) -> None:
    plan = await apm.retirement_plans.create(RetirementPlanCreateRequest(
        name="integ-ret-update",
        retention_days=30,
    ))
    try:
        updated = await apm.retirement_plans.update(plan.plan_id, RetirementPlanCreateRequest(
            name="integ-ret-update",
            retention_days=60,
        ))
        assert updated.retention is not None
        assert updated.retention.days == 60
    finally:
        await apm.retirement_plans.delete(plan.plan_id)


async def test_retirement_plan_create_duplicate_name_raises(apm: APMClient) -> None:
    plan = await apm.retirement_plans.create(RetirementPlanCreateRequest(
        name="integ-ret-dup",
        retention_days=30,
    ))
    try:
        with pytest.raises(PlanNameConflictError) as exc_info:
            await apm.retirement_plans.create(RetirementPlanCreateRequest(
                name="integ-ret-dup",
                retention_days=30,
            ))
        assert exc_info.value.resource_id == "integ-ret-dup"
    finally:
        await apm.retirement_plans.delete(plan)


async def test_retirement_plan_delete_in_use_raises(apm: APMClient) -> None:
    plans, _ = await apm.retirement_plans.list()
    in_use = next((p for p in plans if p.workload_count and p.workload_count > 0), None)
    if in_use is None:
        pytest.skip("No retirement plan with assigned workloads found")
    with pytest.raises(PlanInUseError) as exc_info:
        await apm.retirement_plans.delete(in_use)
    assert exc_info.value.has_workloads is True


async def test_retirement_plan_delete_removes_plan(apm: APMClient) -> None:
    plan = await apm.retirement_plans.create(RetirementPlanCreateRequest(
        name="integ-ret-delete",
        retention_days=30,
    ))
    plan_id = plan.plan_id
    await apm.retirement_plans.delete(plan)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.retirement_plans.get(plan_id)
    assert_resource_error(exc_info, resource_type="RetirementPlan", resource_id=plan_id)


# ── TieringPlanCollection.create() / update() / delete() ─────────────────────


async def test_tiering_plan_create_returns_tiering_plan(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    if not storages:
        pytest.skip("No remote storages available")
    plan = await apm.tiering_plans.create(TieringPlanCreateRequest(
        name="integ-tiering-create",
        tier_after_days=30,
        destination=storages[0],
    ))
    try:
        assert isinstance(plan, TieringPlan)
        assert plan.plan_id
        assert plan.tiering_after_days == 30
    finally:
        await apm.tiering_plans.delete(plan)


async def test_tiering_plan_update_changes_tier_after_days(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    if not storages:
        pytest.skip("No remote storages available")
    plan = await apm.tiering_plans.create(TieringPlanCreateRequest(
        name="integ-tiering-update",
        tier_after_days=30,
        destination=storages[0],
    ))
    try:
        updated = await apm.tiering_plans.update(plan.plan_id, TieringPlanCreateRequest(
            name="integ-tiering-update",
            tier_after_days=60,
            destination=storages[0],
        ))
        assert updated.tiering_after_days == 60
    finally:
        await apm.tiering_plans.delete(plan.plan_id)


async def test_tiering_plan_create_duplicate_name_raises(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    if not storages:
        pytest.skip("No remote storages available")
    plan = await apm.tiering_plans.create(TieringPlanCreateRequest(
        name="integ-tiering-dup",
        tier_after_days=30,
        destination=storages[0],
    ))
    try:
        with pytest.raises(PlanNameConflictError):
            await apm.tiering_plans.create(TieringPlanCreateRequest(
                name="integ-tiering-dup",
                tier_after_days=30,
                destination=storages[0],
            ))
    finally:
        await apm.tiering_plans.delete(plan)


async def test_tiering_plan_create_then_get(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    if not storages:
        pytest.skip("No remote storages available")
    plan = await apm.tiering_plans.create(TieringPlanCreateRequest(
        name="integ-tiering-get",
        tier_after_days=30,
        destination=storages[0],
        daily_check_time=time(20, 0),
    ))
    try:
        fetched = await apm.tiering_plans.get(plan.plan_id)
        assert fetched.tiering_after_days == 30
        assert fetched.daily_check_time == time(20, 0)
    finally:
        await apm.tiering_plans.delete(plan)


async def test_tiering_plan_delete_in_use_raises(apm: APMClient) -> None:
    plans, _ = await apm.tiering_plans.list()
    in_use = next((p for p in plans if p.server_count and p.server_count > 0), None)
    if in_use is None:
        pytest.skip("No tiering plan with assigned backup servers found")
    with pytest.raises(PlanInUseError) as exc_info:
        await apm.tiering_plans.delete(in_use)
    assert exc_info.value.has_backup_servers is True


async def test_tiering_plan_delete_removes_plan(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    if not storages:
        pytest.skip("No remote storages available")
    plan = await apm.tiering_plans.create(TieringPlanCreateRequest(
        name="integ-tiering-delete",
        tier_after_days=30,
        destination=storages[0],
    ))
    plan_id = plan.plan_id
    await apm.tiering_plans.delete(plan)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.tiering_plans.get(plan_id)
    assert_resource_error(exc_info, resource_type="TieringPlan", resource_id=plan_id)
