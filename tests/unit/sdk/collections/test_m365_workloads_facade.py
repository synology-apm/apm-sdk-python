"""Unit tests for M365PlanCollection (list/get/get_by_name).

Split out of test_m365_workloads.py: M365PlanCollection is a separate collection class
(defined in collections/protection_plans.py) from M365WorkloadCollection, so its tests get
their own facade-split sibling file per the naming convention — see
test_m365_workloads.py / test_m365_workloads_actions.py for M365WorkloadCollection itself.
"""
from __future__ import annotations

import pytest

from synology_apm.sdk.collections.protection_plans import M365PlanCollection
from synology_apm.sdk.enums import RetentionType, ScheduleFrequency, WorkloadCategory
from synology_apm.sdk.exceptions import ResourceNotFoundError
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session

PLAN_ID = "0c8f033b-fb57-4f46-9a9d-85e9d21c08ab"
PLANS_URL = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=M365"

SAMPLE_M365_PLAN = {
    "id": PLAN_ID,
    "spec": {
        "name": "Daily Backup (saas)",
        "serviceType": "M365",
        "retention": {"keepDays": 30},
        "configM365": {
            "schedule": {
                "repeatType": "DAILY",
                "runHour": 2,
                "runMin": 30,
            }
        },
        "backupCopy": {"enabled": False, "destination": ""},
    },
    "protectedWorkloadCount": 5,
    "unprotectedWorkloadCount": 2,
}


# ── M365PlanCollection.list() ─────────────────────────────────────────────


async def test_m365_plan_list_parses_fields() -> None:
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        result, total = await collection.list()
        await session.disconnect()

    assert len(result) == 1
    plan = result[0]
    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup (saas)"
    assert plan.category == WorkloadCategory.M365
    assert plan.workload_count == 7  # 5 + 2
    assert plan.policy is not None
    assert plan.policy.retention.retention_type == RetentionType.KEEP_DAYS
    assert plan.policy.retention.days == 30


async def test_m365_plan_list_parses_schedule() -> None:
    """M365 list endpoint returns schedule (in configM365.schedule)."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        result, total = await collection.list()
        await session.disconnect()

    plan = result[0]
    assert plan.policy is not None
    assert plan.policy.schedule is not None
    assert plan.policy.schedule.frequency == ScheduleFrequency.DAILY
    assert plan.policy.schedule.start_time is not None
    assert plan.policy.schedule.start_time.hour == 2
    assert plan.policy.schedule.start_time.minute == 30


async def test_m365_plan_list_name_filter_passes_keyword() -> None:
    """list(name_contains=...) should append keyword param to the request."""
    async with connected_session() as (session, m):

        filtered_url = f"{PLANS_URL}&keyword=Daily"
        m.get(filtered_url, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        result, total = await collection.list(name_contains="Daily")
        await session.disconnect()

    assert len(result) == 1


# ── M365PlanCollection.get() ─────────────────────────────────────────────


async def test_m365_plan_get_calls_direct_endpoint() -> None:
    async with connected_session() as (session, m):

        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_M365_PLAN)
        collection = M365PlanCollection(session)
        plan = await collection.get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup (saas)"


# ── M365PlanCollection.get_by_name() ─────────────────────────────────────


async def test_m365_plan_get_by_name_resolves_via_list() -> None:
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=Daily+Backup+(saas)&limit=100&offset=0&serviceType=M365"
        m.get(keyword_url, payload={"plans": [SAMPLE_M365_PLAN]})
        collection = M365PlanCollection(session)
        plan = await collection.get_by_name("Daily Backup (saas)")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID


async def test_m365_plan_get_by_nonexistent_name_raises_not_found() -> None:
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=No+Such+Plan&limit=100&offset=0&serviceType=M365"
        m.get(keyword_url, payload={"plans": []})
        collection = M365PlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("No Such Plan")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="No Such Plan")


