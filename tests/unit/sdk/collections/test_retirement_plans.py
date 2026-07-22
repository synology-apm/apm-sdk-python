"""Unit tests for RetirementPlanCollection."""
from __future__ import annotations

from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections.retirement_plans import RetirementPlanCollection
from synology_apm.sdk.exceptions import APIError, PlanInUseError, PlanNameConflictError, ResourceNotFoundError
from synology_apm.sdk.models.retirement_plan import (
    RetirementPlan,
    RetirementPlanCreateRequest,
    RetirementRetentionPolicy,
)
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
    request_json,
)

PLANS_URL = f"{BASE_URL}/api/v1/plan/archive_plan?offset=0&limit=500"
PLAN_ID = "rp-uuid-001"

SAMPLE_PLAN_RAW: dict[str, Any] = {
    "id": PLAN_ID,
    "spec": {
        "name": "Keep 30 Days",
        "description": "Retire and keep 30 days",
        "retention": {"keepDays": 30},
    },
    "workloadCount": 5,
}

PLAN_KEEP_LATEST_RAW: dict[str, Any] = {
    "id": "rp-uuid-002",
    "spec": {
        "name": "Keep Latest Version",
        "description": "",
        "retention": {"keepVersions": 1},
    },
    "workloadCount": 1,
}


# ── list() ────────────────────────────────────────────────────────────────────


async def test_list_parses_plan_fields() -> None:
    """list() should correctly map all API fields to RetirementPlan."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        col = RetirementPlanCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert total == 1
    plan = result[0]
    assert plan.plan_id == PLAN_ID
    assert plan.name == "Keep 30 Days"
    assert plan.description == "Retire and keep 30 days"
    assert plan.workload_count == 5
    assert plan.retention is not None
    assert plan.retention.days == 30
    assert plan.retention.keep_latest_version is False


async def test_list_returns_empty_when_no_plans() -> None:
    """list() should return [] when API returns empty plans array."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [], "total": 0})
        col = RetirementPlanCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert result == []


async def test_list_with_name_contains_sends_keyword_param() -> None:
    """list(name_contains=...) should append keyword= to the request."""
    keyword_url = f"{BASE_URL}/api/v1/plan/archive_plan?offset=0&limit=500&keyword=Keep"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        col = RetirementPlanCollection(session)
        result, total = await col.list(name_contains="Keep")
        await session.disconnect()

    assert len(result) == 1


async def test_list_retention_keep_latest() -> None:
    """Retention with keepVersions > 0 should set keep_latest_version=True, days=None."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [PLAN_KEEP_LATEST_RAW], "total": 1})
        col = RetirementPlanCollection(session)
        result, total = await col.list()
        await session.disconnect()

    retention = result[0].retention
    assert retention is not None
    assert retention.keep_latest_version is True
    assert retention.days is None


# ── get() ─────────────────────────────────────────────────────────────────


async def test_get_calls_direct_endpoint() -> None:
    """get() should call GET /api/v1/plan/archive_plan/{id}."""
    direct_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    async with connected_session() as (session, m):

        m.get(direct_url, payload=SAMPLE_PLAN_RAW)
        col = RetirementPlanCollection(session)
        plan = await col.get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "Keep 30 Days"


# ── get_by_name() ─────────────────────────────────────────────────────────


async def test_get_by_name_resolves_directly() -> None:
    """get_by_name(name) should search by keyword and return the exact match directly."""
    keyword_url = f"{BASE_URL}/api/v1/plan/archive_plan?keyword=Keep+30+Days&limit=100&offset=0"

    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        col = RetirementPlanCollection(session)
        plan = await col.get_by_name("Keep 30 Days")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "Keep 30 Days"


async def test_get_by_name_raises_not_found_when_missing() -> None:
    """get_by_name(name) should raise ResourceNotFoundError when plan doesn't exist."""
    keyword_url = f"{BASE_URL}/api/v1/plan/archive_plan?keyword=Non-existent&limit=100&offset=0"

    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [], "total": 0})
        col = RetirementPlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get_by_name("Non-existent")
        await session.disconnect()

    assert exc_info.value.resource_type == "RetirementPlan"
    assert exc_info.value.resource_id == "Non-existent"


async def test_get_by_name_raises_not_found_when_no_exact_match() -> None:
    """get_by_name(name) should raise ResourceNotFoundError when keyword matches different name."""
    keyword_url = f"{BASE_URL}/api/v1/plan/archive_plan?keyword=Keep&limit=100&offset=0"
    # API returns a plan but its name is "Keep 30 Days", not exactly "Keep"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        col = RetirementPlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get_by_name("Keep")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="RetirementPlan", resource_id="Keep")


async def test_get_by_name_returns_exact_match_among_partials() -> None:
    """get_by_name(name) returns the exact match when keyword hits multiple results."""
    keyword_url = f"{BASE_URL}/api/v1/plan/archive_plan?keyword=Keep+30+Days&limit=100&offset=0"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW, PLAN_KEEP_LATEST_RAW], "total": 2})
        col = RetirementPlanCollection(session)
        plan = await col.get_by_name("Keep 30 Days")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID


# ── create() ─────────────────────────────────────────────────────────────────


def _assert_sample_retirement_plan(plan: RetirementPlan) -> None:
    assert plan.plan_id == PLAN_ID
    assert plan.name == "Keep 30 Days"
    assert plan.description == "Retire and keep 30 days"
    assert plan.workload_count == 5
    assert plan.retention is not None
    assert plan.retention.days == 30
    assert plan.retention.keep_latest_version is False


async def test_create_posts_body_and_returns_plan() -> None:
    """create() should POST to /api/v1/plan/archive_plan and return the created plan via get()."""
    create_url = f"{BASE_URL}/api/v1/plan/archive_plan"
    get_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.post(create_url, payload={"id": PLAN_ID})
        m.get(get_url, payload=SAMPLE_PLAN_RAW)
        col = RetirementPlanCollection(session)
        plan = await col.create(RetirementPlanCreateRequest(name="Keep 30 Days", retention_days=30))
        await session.disconnect()

    _assert_sample_retirement_plan(plan)

    post_key = ("POST", URL(create_url))
    body = request_json(m, post_key)
    assert body["plan"]["name"] == "Keep 30 Days"
    assert body["plan"]["retention"]["keepDays"] == 30
    assert body["plan"]["retention"]["keepAll"] is False


async def test_create_keep_all_sends_correct_retention() -> None:
    """create(retention_days=None) should send keepAll=true in the request body."""
    create_url = f"{BASE_URL}/api/v1/plan/archive_plan"
    get_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.post(create_url, payload={"id": PLAN_ID})
        m.get(get_url, payload=SAMPLE_PLAN_RAW)
        col = RetirementPlanCollection(session)
        await col.create(RetirementPlanCreateRequest(name="Keep All", retention_days=None))
        await session.disconnect()

    post_key = ("POST", URL(create_url))
    body = request_json(m, post_key)
    assert body["plan"]["retention"]["keepAll"] is True


async def test_create_duplicate_name_raises_plan_name_conflict_error() -> None:
    """create() should raise PlanNameConflictError when the API returns errorCode 4013."""
    create_url = f"{BASE_URL}/api/v1/plan/archive_plan"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4013, "errorString": {"key": "plan_name_conflict"}}]}
    }
    async with connected_session() as (session, m):
        m.post(create_url, status=500, payload=error_body)
        col = RetirementPlanCollection(session)
        with pytest.raises(PlanNameConflictError) as exc_info:
            await col.create(RetirementPlanCreateRequest(name="Duplicate Plan", retention_days=30))
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="RetirementPlan", resource_id="Duplicate Plan")


async def test_create_missing_id_in_response_raises_api_error() -> None:
    """create() raises APIError when the POST response contains no 'id' field."""
    create_url = f"{BASE_URL}/api/v1/plan/archive_plan"
    async with connected_session() as (session, m):
        m.post(create_url, payload={})
        col = RetirementPlanCollection(session)
        with pytest.raises(APIError, match="no plan ID"):
            await col.create(RetirementPlanCreateRequest(name="Test Plan", retention_days=30))
        await session.disconnect()


# ── update() ─────────────────────────────────────────────────────────────────


async def test_update_puts_body_and_returns_plan() -> None:
    """update() should PUT to /api/v1/plan/archive_plan/{id} and return the updated plan."""
    update_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    get_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.put(update_url, payload={})
        m.get(get_url, payload=SAMPLE_PLAN_RAW)
        col = RetirementPlanCollection(session)
        plan = await col.update(PLAN_ID, RetirementPlanCreateRequest(name="Keep 30 Days", retention_days=30))
        await session.disconnect()

    _assert_sample_retirement_plan(plan)
    put_key = ("PUT", URL(update_url))
    body = request_json(m, put_key)
    assert body["plan"]["name"] == "Keep 30 Days"
    assert body["plan"]["retention"]["keepDays"] == 30


async def test_update_duplicate_name_raises_plan_name_conflict_error() -> None:
    """update() should raise PlanNameConflictError when the API returns errorCode 4013."""
    update_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4013, "errorString": {"key": "plan_name_conflict"}}]}
    }
    async with connected_session() as (session, m):
        m.put(update_url, status=500, payload=error_body)
        col = RetirementPlanCollection(session)
        with pytest.raises(PlanNameConflictError) as exc_info:
            await col.update(PLAN_ID, RetirementPlanCreateRequest(name="Conflict", retention_days=30))
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="RetirementPlan", resource_id="Conflict")


# ── delete() ─────────────────────────────────────────────────────────────────


async def test_delete_sends_delete_request() -> None:
    """delete() should send DELETE to /api/v1/plan/archive_plan/{id}."""
    delete_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.delete(delete_url, payload={})
        col = RetirementPlanCollection(session)
        await col.delete(PLAN_ID)
        await session.disconnect()

    assert ("DELETE", URL(delete_url)) in m.requests


async def test_delete_accepts_plan_object() -> None:
    """delete() should accept a RetirementPlan object and use its plan_id."""
    delete_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    plan_obj = RetirementPlan(
        plan_id=PLAN_ID,
        name="Test",
        description="",
        retention=RetirementRetentionPolicy(days=30, keep_latest_version=True),
        workload_count=0,
    )
    async with connected_session() as (session, m):
        m.delete(delete_url, payload={})
        col = RetirementPlanCollection(session)
        await col.delete(plan_obj)
        await session.disconnect()

    assert ("DELETE", URL(delete_url)) in m.requests


async def test_delete_plan_in_use_raises_plan_in_use_error() -> None:
    """delete() should raise PlanInUseError(has_workloads=True) when errorCode 4019 is returned."""
    delete_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4019, "errorString": {"key": "delete_failed_reason_workload_use"}}]}
    }
    async with connected_session() as (session, m):
        m.delete(delete_url, status=500, payload=error_body)
        col = RetirementPlanCollection(session)
        with pytest.raises(PlanInUseError) as exc_info:
            await col.delete(PLAN_ID)
        await session.disconnect()

    err = exc_info.value
    assert err.has_workloads is True
    assert err.has_server_template is False
    assert err.has_backup_servers is False




# ── run_schedule_by_controller_time parsing ────────────────────────────────────


async def test_run_schedule_by_controller_time_absent_means_false() -> None:
    """run_schedule_by_controller_time is False when the controller-time flag is absent."""
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        col = RetirementPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    assert result[0].run_schedule_by_controller_time is False


async def test_run_schedule_by_controller_time_present_means_true() -> None:
    """run_schedule_by_controller_time is True when the controller-time flag is present."""
    plan_raw = {
        **SAMPLE_PLAN_RAW,
        "spec": {**SAMPLE_PLAN_RAW["spec"], "controllerUtcOffset": 0},
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan_raw], "total": 1})
        col = RetirementPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    assert result[0].run_schedule_by_controller_time is True


# ── get() error handling ───────────────────────────────────────────────────


async def test_get_raises_resource_not_found_on_api_error_4002() -> None:
    """get() should raise ResourceNotFoundError when the API returns error detail code 4002."""
    direct_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4002}]}
    }
    async with connected_session() as (session, m):
        m.get(direct_url, status=500, payload=error_body)
        col = RetirementPlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get(PLAN_ID)
        await session.disconnect()

    err = exc_info.value
    assert err.resource_type == "RetirementPlan"
    assert err.resource_id == PLAN_ID


async def test_update_reraises_non_conflict_api_error() -> None:
    """update() re-raises an APIError that is not a name conflict."""
    update_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {"error": {"code": 500, "details": [{"errorCode": 9999}]}}
    async with connected_session() as (session, m):
        m.put(update_url, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await RetirementPlanCollection(session).update(
                PLAN_ID, RetirementPlanCreateRequest(name="Keep 30 Days", retention_days=30)
            )
        await session.disconnect()

    assert exc_info.type is APIError


async def test_delete_reraises_non_in_use_api_error() -> None:
    """delete() re-raises an APIError whose detail codes are not the in-use code."""
    delete_url = f"{BASE_URL}/api/v1/plan/archive_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {"error": {"code": 500, "details": [{"errorCode": 5000}]}}
    async with connected_session() as (session, m):
        m.delete(delete_url, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await RetirementPlanCollection(session).delete(PLAN_ID)
        await session.disconnect()

    assert exc_info.type is APIError


async def test_create_reraises_non_conflict_api_error() -> None:
    """create() re-raises an APIError that is not a name conflict."""
    create_url = f"{BASE_URL}/api/v1/plan/archive_plan"
    error_body: dict[str, Any] = {"error": {"code": 500, "details": [{"errorCode": 9999}]}}
    async with connected_session() as (session, m):
        m.post(create_url, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await RetirementPlanCollection(session).create(
                RetirementPlanCreateRequest(name="Keep 30 Days", retention_days=30)
            )
        await session.disconnect()

    assert exc_info.type is APIError
