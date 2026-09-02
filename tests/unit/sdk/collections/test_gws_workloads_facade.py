"""Unit tests for GWSPlanCollection (list/get/get_by_name).

Split out of test_gws_workloads.py: GWSPlanCollection is a separate collection class
(defined in collections/protection_plans.py) from GWSWorkloadCollection, so its tests get
their own facade-split sibling file per the naming convention — see
test_gws_workloads.py / test_gws_workloads_actions.py for GWSWorkloadCollection itself, and
test_protection_plans_write.py for create()/update()/delete().
"""
from __future__ import annotations

import pytest

from synology_apm.sdk.collections.protection_plans import GWSPlanCollection
from synology_apm.sdk.enums import RetentionType, WorkloadCategory
from synology_apm.sdk.exceptions import ResourceNotFoundError
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session

PLAN_ID = "0c8f033b-fb57-4f46-9a9d-85e9d21c08ab"
PLANS_URL = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=GW"

SAMPLE_GWS_PLAN = {
    "id": PLAN_ID,
    "spec": {
        "name": "GWS Daily",
        "serviceType": "GW",
        "retention": {"keepDays": 30},
        "configGw": {
            "schedule": {
                "repeatType": "DAILY",
                "runHour": 2,
                "runMin": 30,
            },
            "enableLabelBackup": True,
        },
        "backupCopy": {"enabled": False, "destination": ""},
    },
    "protectedWorkloadCount": 5,
    "unprotectedWorkloadCount": 2,
}


# ── GWSPlanCollection.list() ───────────────────────────────────────────────


async def test_gws_plan_list_parses_fields() -> None:
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_GWS_PLAN]})
        collection = GWSPlanCollection(session)
        result, total = await collection.list()
        await session.disconnect()

    assert len(result) == 1
    plan = result[0]
    assert plan.plan_id == PLAN_ID
    assert plan.name == "GWS Daily"
    assert plan.category == WorkloadCategory.GWS
    assert plan.workload_count == 7  # 5 + 2
    assert plan.policy is not None
    assert plan.policy.retention.retention_type == RetentionType.KEEP_DAYS
    assert plan.policy.retention.days == 30


async def test_gws_plan_list_name_filter_passes_keyword() -> None:
    """list(keyword=...) should append keyword param to the request."""
    async with connected_session() as (session, m):

        filtered_url = f"{PLANS_URL}&keyword=Daily"
        m.get(filtered_url, payload={"plans": [SAMPLE_GWS_PLAN]})
        collection = GWSPlanCollection(session)
        result, total = await collection.list(keyword="Daily")
        await session.disconnect()

    assert len(result) == 1


# ── GWSPlanCollection.get() ────────────────────────────────────────────────


async def test_gws_plan_get_calls_direct_endpoint() -> None:
    async with connected_session() as (session, m):

        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_GWS_PLAN)
        collection = GWSPlanCollection(session)
        plan = await collection.get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "GWS Daily"


# ── GWSPlanCollection.get_by_name() ────────────────────────────────────────


async def test_gws_plan_get_by_name_resolves_via_list() -> None:
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=GWS+Daily&limit=100&offset=0&serviceType=GW"
        m.get(keyword_url, payload={"plans": [SAMPLE_GWS_PLAN]})
        collection = GWSPlanCollection(session)
        plan = await collection.get_by_name("GWS Daily")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID


async def test_gws_plan_get_by_nonexistent_name_raises_not_found() -> None:
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=No+Such+Plan&limit=100&offset=0&serviceType=GW"
        m.get(keyword_url, payload={"plans": []})
        collection = GWSPlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("No Such Plan")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="No Such Plan")
