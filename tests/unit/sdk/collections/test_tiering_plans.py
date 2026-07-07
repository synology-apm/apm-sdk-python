"""Unit tests for TieringPlanCollection."""
from __future__ import annotations

from datetime import time
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections.tiering_plans import TieringPlanCollection
from synology_apm.sdk.enums import CopyReason, RemoteStorageType, VersionCopyStatus
from synology_apm.sdk.exceptions import APIError, PlanInUseError, PlanNameConflictError, ResourceNotFoundError
from synology_apm.sdk.models.remote_storage import RemoteStorage
from synology_apm.sdk.models.tiering_plan import TieringPlan, TieringPlanCreateRequest
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
)

PLANS_URL = f"{BASE_URL}/api/v1/plan/tiering_plan?offset=0&limit=500"
PLAN_ID = "3d5bf700-4bb6-4eec-a709-c15f04cd0af1"
DEST_ID = "ca786d49-74c2-5e43-a968-10bf111d3388"
DEST_URL = f"{BASE_URL}/api/v1/external_storage/{DEST_ID}"

SAMPLE_PLAN_RAW: dict[str, Any] = {
    "id": PLAN_ID,
    "spec": {
        "name": "tiering plan 1",
        "description": "",
        "destinationType": "COMPATIBLE_S3",
        "destination": DEST_ID,
        "schedule": {"runHour": 1, "runMin": 17},
        "tieringAfterDays": 9876,
    },
    "status": {},
    "tieringInfo": {"tieringStatus": "NONE", "pendingVersionCount": "0", "protectedServerCount": 1},
}

SAMPLE_DEST_RAW: dict[str, Any] = {
    "id": DEST_ID,
    "displayName": "My S3 Storage",
    "endpoint": "s3.amazonaws.com",
    "vaultName": None,
}

PLAN_NO_DEST_RAW = {
    "id": "f56f8969-a831-47a6-9de0-279696dafea6",
    "spec": {
        "name": "tiering 2",
        "description": "desc",
        "destinationType": "ACTIVE_BACKUP_ENTERPRISE_VAULT",
        "destination": "f0d5d047-7dda-59fe-8d1b-47441c80bd1e",
        "schedule": {"runHour": 2, "runMin": 0},
        "tieringAfterDays": 30,
    },
    "status": {},
    "tieringInfo": {"tieringStatus": "NONE", "pendingVersionCount": "0", "protectedServerCount": 0},
}


# ── list() ────────────────────────────────────────────────────────────────────


async def test_list_parses_plan_fields() -> None:
    """list() should correctly map all API fields to TieringPlan."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert total == 1
    plan = result[0]
    assert plan.plan_id == PLAN_ID
    assert plan.name == "tiering plan 1"
    assert plan.description == ""
    assert plan.tiering_after_days == 9876
    assert plan.daily_check_time == time(1, 17)
    assert plan.server_count == 1


async def test_list_resolves_destination() -> None:
    """list() should populate destination with LocationInfo from external_storage."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    dest = result[0].destination
    assert dest is not None
    assert dest.is_remote_storage is True
    assert dest.name == "My S3 Storage"
    assert dest.endpoint == "s3.amazonaws.com"
    assert dest.vault is None


async def test_list_destination_none_when_fetch_fails() -> None:
    """list() should set destination=None when the external_storage fetch fails."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        m.get(DEST_URL, status=404, payload={"error": "not found"})
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    assert result[0].destination is None


async def test_list_deduplicates_destination_fetches() -> None:
    """list() should fetch each unique destination ID only once."""
    from yarl import URL
    async with connected_session() as (session, m):

        # Two plans sharing the same destination ID
        plan2 = {**SAMPLE_PLAN_RAW, "id": "plan-002"}
        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW, plan2], "total": 2})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    assert len(result) == 2
    fetch_key = ("GET", URL(DEST_URL))
    assert len(m.requests[fetch_key]) == 1


async def test_list_with_name_contains_sends_keyword() -> None:
    """list(name_contains=...) should append keyword= to the request."""
    keyword_url = f"{BASE_URL}/api/v1/plan/tiering_plan?offset=0&limit=500&keyword=tiering"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list(name_contains="tiering")
        await session.disconnect()

    assert len(result) == 1


async def test_list_empty_plans() -> None:
    """list() should return ([], 0) when API returns no plans."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [], "total": 0})
        col = TieringPlanCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert result == []
    assert total == 0


# ── get() ─────────────────────────────────────────────────────────────────


async def test_get_calls_direct_endpoint() -> None:
    """get() should call GET /api/v1/plan/tiering_plan/{id}."""
    direct_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    async with connected_session() as (session, m):

        m.get(direct_url, payload=SAMPLE_PLAN_RAW)
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        plan = await col.get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "tiering plan 1"
    assert plan.destination is not None
    assert plan.destination.name == "My S3 Storage"


# ── get_by_name() ─────────────────────────────────────────────────────────


async def test_get_by_name_resolves_directly() -> None:
    """get_by_name(name) should search by keyword and return the exact match."""
    keyword_url = f"{BASE_URL}/api/v1/plan/tiering_plan?keyword=tiering+plan+1&limit=100&offset=0"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        plan = await col.get_by_name("tiering plan 1")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.destination is not None
    assert plan.destination.name == "My S3 Storage"


async def test_get_by_name_raises_not_found_when_missing() -> None:
    """get_by_name(name) should raise ResourceNotFoundError when plan does not exist."""
    keyword_url = f"{BASE_URL}/api/v1/plan/tiering_plan?keyword=Non-existent&limit=100&offset=0"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [], "total": 0})
        col = TieringPlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get_by_name("Non-existent")
        await session.disconnect()

    assert exc_info.value.resource_type == "TieringPlan"
    assert exc_info.value.resource_id == "Non-existent"


async def test_get_by_name_raises_not_found_when_no_exact_match() -> None:
    """get_by_name(name) should raise ResourceNotFoundError when keyword matches a different name."""
    keyword_url = f"{BASE_URL}/api/v1/plan/tiering_plan?keyword=tiering&limit=100&offset=0"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        col = TieringPlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get_by_name("tiering")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="TieringPlan", resource_id="tiering")


async def test_get_by_name_fetches_destination_only_for_match() -> None:
    """get_by_name(name) should only fetch the destination for the matched plan, not others."""
    from yarl import URL
    other_dest_id = "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"
    keyword_url = f"{BASE_URL}/api/v1/plan/tiering_plan?keyword=tiering+plan+1&limit=100&offset=0"
    async with connected_session() as (session, m):

        m.get(keyword_url, payload={"plans": [PLAN_NO_DEST_RAW, SAMPLE_PLAN_RAW], "total": 2})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        plan = await col.get_by_name("tiering plan 1")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    # The other plan's destination (other_dest_id) was never fetched
    assert ("GET", URL(f"{BASE_URL}/api/v1/external_storage/{other_dest_id}")) not in m.requests


# ── tiering_status parsing ────────────────────────────────────────────────


async def test_tiering_status_none_for_completed_no_pending() -> None:
    """tieringStatus='NONE' with no pending versions → TieringStatus(COMPLETED)."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    ts = result[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.COMPLETED
    assert ts.reason is None
    assert ts.pending_version_count == 0


async def test_tiering_status_in_progress_with_pending() -> None:
    """tieringStatus='DOING' → TieringStatus(IN_PROGRESS) with pending count and remaining bytes."""
    plan = {
        **SAMPLE_PLAN_RAW,
        "tieringInfo": {
            "tieringStatus": "DOING",
            "pendingVersionCount": "5",
            "remainingBytes": "2097152",
            "protectedServerCount": 1,
        },
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    ts = result[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.IN_PROGRESS
    assert ts.pending_version_count == 5
    assert ts.remaining_bytes == 2097152
    assert ts.reason is None


async def test_tiering_status_waiting_when_completed_with_pending() -> None:
    """tieringStatus='COMPLETED' with pendingVersionCount>0 → TieringStatus(WAITING)."""
    plan = {
        **SAMPLE_PLAN_RAW,
        "tieringInfo": {
            "tieringStatus": "COMPLETED",
            "pendingVersionCount": "3",
            "remainingBytes": "1048576",
            "protectedServerCount": 1,
        },
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    ts = result[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.WAITING
    assert ts.pending_version_count == 3
    assert ts.remaining_bytes == 1048576


async def test_tiering_status_retry_with_reason() -> None:
    """tieringStatus='DESTINATION_DISCONNECTED' → TieringStatus(RETRY, DESTINATION_DISCONNECTED)."""
    plan = {
        **SAMPLE_PLAN_RAW,
        "tieringInfo": {
            "tieringStatus": "DESTINATION_DISCONNECTED",
            "pendingVersionCount": "2",
            "remainingBytes": "0",
            "protectedServerCount": 1,
        },
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    ts = result[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.RETRY
    assert ts.reason == CopyReason.DESTINATION_DISCONNECTED
    assert ts.pending_version_count == 2
    assert ts.remaining_bytes is None


async def test_tiering_status_failed_with_reason() -> None:
    """tieringStatus='INFRASTRUCTURE_ERROR' → TieringStatus(FAILED, INFRASTRUCTURE_ERROR)."""
    plan = {
        **SAMPLE_PLAN_RAW,
        "tieringInfo": {
            "tieringStatus": "INFRASTRUCTURE_ERROR",
            "pendingVersionCount": "0",
            "protectedServerCount": 1,
        },
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    ts = result[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.FAILED
    assert ts.reason == CopyReason.INFRASTRUCTURE_ERROR


async def test_tiering_status_no_versions_to_copy() -> None:
    """tieringStatus='NO_VERSIONS_TO_COPY' → TieringStatus(COMPLETED, NO_VERSIONS_TO_COPY)."""
    plan = {
        **SAMPLE_PLAN_RAW,
        "tieringInfo": {
            "tieringStatus": "NO_VERSIONS_TO_COPY",
            "pendingVersionCount": "0",
            "protectedServerCount": 1,
        },
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    ts = result[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.COMPLETED
    assert ts.reason == CopyReason.NO_VERSIONS_TO_COPY


async def test_tiering_status_none_when_absent() -> None:
    """When tieringInfo has no tieringStatus key, tiering_status should be None."""
    plan = {
        **SAMPLE_PLAN_RAW,
        "tieringInfo": {"protectedServerCount": 1},
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    assert result[0].tiering_status is None


# ── create() ─────────────────────────────────────────────────────────────────


def _assert_sample_tiering_plan(plan: TieringPlan) -> None:
    assert plan.plan_id == PLAN_ID
    assert plan.name == "tiering plan 1"
    assert plan.tiering_after_days == 9876
    assert plan.daily_check_time == time(1, 17)
    assert plan.server_count == 1
    assert plan.destination is not None
    assert plan.destination.name == "My S3 Storage"
    assert plan.destination.endpoint == "s3.amazonaws.com"


def _make_fake_remote_storage() -> RemoteStorage:
    from synology_apm.sdk.enums import RemoteStorageStatus
    return RemoteStorage(
        storage_id=DEST_ID,
        name="My S3 Storage",
        storage_type=RemoteStorageType.S3_COMPATIBLE,
        device_model="",
        endpoint="s3.example.com:443",
        status=RemoteStorageStatus.CONNECTED,
        used_bytes=None,
        remaining_bytes=None,
    )


async def test_create_posts_body_and_returns_plan() -> None:
    """create() should POST to /api/v1/plan/tiering_plan and return the created plan via get()."""
    create_url = f"{BASE_URL}/api/v1/plan/tiering_plan"
    get_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.post(create_url, payload={"id": PLAN_ID})
        m.get(get_url, payload=SAMPLE_PLAN_RAW)
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        plan = await col.create(TieringPlanCreateRequest(
            name="tiering plan 1",
            tier_after_days=9876,
            destination=_make_fake_remote_storage(),
            daily_check_time=time(1, 17),
        ))
        await session.disconnect()

    _assert_sample_tiering_plan(plan)

    post_key = ("POST", URL(create_url))
    body: dict[str, Any] = m.requests[post_key][0].kwargs["json"]
    assert body["plan"]["name"] == "tiering plan 1"
    assert body["plan"]["tieringAfterDays"] == 9876
    assert body["plan"]["destination"] == DEST_ID
    assert body["plan"]["destinationType"] == "COMPATIBLE_S3"
    assert body["plan"]["schedule"]["runHour"] == 1
    assert body["plan"]["schedule"]["runMin"] == 17


async def test_create_duplicate_name_raises_plan_name_conflict_error() -> None:
    """create() should raise PlanNameConflictError when errorCode 4013 is returned."""
    create_url = f"{BASE_URL}/api/v1/plan/tiering_plan"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4013}]}
    }
    async with connected_session() as (session, m):
        m.post(create_url, status=500, payload=error_body)
        col = TieringPlanCollection(session)
        with pytest.raises(PlanNameConflictError) as exc_info:
            await col.create(TieringPlanCreateRequest(
                name="Duplicate",
                tier_after_days=30,
                destination=_make_fake_remote_storage(),
            ))
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="TieringPlan", resource_id="Duplicate")


# ── update() ─────────────────────────────────────────────────────────────────


async def test_update_puts_body_and_returns_plan() -> None:
    """update() should PUT to /api/v1/plan/tiering_plan/{id} and return the updated plan."""
    update_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.put(update_url, payload={})
        m.get(update_url, payload=SAMPLE_PLAN_RAW)
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        plan = await col.update(PLAN_ID, TieringPlanCreateRequest(
            name="tiering plan 1",
            tier_after_days=9876,
            destination=_make_fake_remote_storage(),
        ))
        await session.disconnect()

    _assert_sample_tiering_plan(plan)
    put_key = ("PUT", URL(update_url))
    body = m.requests[put_key][0].kwargs["json"]
    assert body["plan"]["name"] == "tiering plan 1"
    assert body["plan"]["tieringAfterDays"] == 9876
    assert body["plan"]["destination"] == DEST_ID
    assert body["plan"]["destinationType"] == "COMPATIBLE_S3"


async def test_update_duplicate_name_raises_plan_name_conflict_error() -> None:
    """update() should raise PlanNameConflictError when errorCode 4013 is returned."""
    update_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4013}]}
    }
    async with connected_session() as (session, m):
        m.put(update_url, status=500, payload=error_body)
        col = TieringPlanCollection(session)
        with pytest.raises(PlanNameConflictError) as exc_info:
            await col.update(PLAN_ID, TieringPlanCreateRequest(
                name="Conflict",
                tier_after_days=30,
                destination=_make_fake_remote_storage(),
            ))
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="TieringPlan", resource_id="Conflict")


# ── delete() ─────────────────────────────────────────────────────────────────


async def test_delete_sends_delete_request() -> None:
    """delete() should send DELETE to /api/v1/plan/tiering_plan/{id}."""
    delete_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.delete(delete_url, payload={})
        col = TieringPlanCollection(session)
        await col.delete(PLAN_ID)
        await session.disconnect()

    assert ("DELETE", URL(delete_url)) in m.requests


async def test_delete_accepts_tiering_plan_object() -> None:
    """delete() should accept a TieringPlan object and use its plan_id."""
    delete_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    plan_obj = TieringPlan(
        plan_id=PLAN_ID,
        name="tiering plan 1",
        description="",
        tiering_after_days=30,
        daily_check_time=time(1, 0),
        destination=None,
        server_count=0,
    )
    async with connected_session() as (session, m):
        m.delete(delete_url, payload={})
        col = TieringPlanCollection(session)
        await col.delete(plan_obj)
        await session.disconnect()

    assert ("DELETE", URL(delete_url)) in m.requests


async def test_delete_plan_in_use_raises_plan_in_use_error() -> None:
    """delete() should raise PlanInUseError(has_backup_servers=True) when errorCode 4029 is returned."""
    delete_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4029}]}
    }
    async with connected_session() as (session, m):
        m.delete(delete_url, status=500, payload=error_body)
        col = TieringPlanCollection(session)
        with pytest.raises(PlanInUseError) as exc_info:
            await col.delete(PLAN_ID)
        await session.disconnect()

    err = exc_info.value
    assert err.has_backup_servers is True
    assert err.has_workloads is False


# ── run_schedule_by_controller_time parsing ────────────────────────────────────


async def test_run_schedule_by_controller_time_absent_means_false() -> None:
    """run_schedule_by_controller_time is False when the controller-time flag is absent."""
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
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
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    assert result[0].run_schedule_by_controller_time is True


# ── get() error handling ───────────────────────────────────────────────────


async def test_get_raises_resource_not_found_on_api_error_4003() -> None:
    """get() should raise ResourceNotFoundError when the API returns error detail code 4003."""
    direct_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4003}]}
    }
    async with connected_session() as (session, m):
        m.get(direct_url, status=500, payload=error_body)
        col = TieringPlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get(PLAN_ID)
        await session.disconnect()

    err = exc_info.value
    assert err.resource_type == "TieringPlan"
    assert err.resource_id == PLAN_ID


# ── tiering_status SKIPPED_WORKLOAD ───────────────────────────────────────


async def test_tiering_status_skipped_workload_with_reason() -> None:
    """tieringStatus='SKIPPED_WORKLOAD' → TieringStatus(SKIPPED) with reason from statusReason."""
    plan = {
        **SAMPLE_PLAN_RAW,
        "tieringInfo": {
            "tieringStatus": "SKIPPED_WORKLOAD",
            "statusReason": "REASON_SKIPPED_FOR_NAS_TO_EXTERNAL_STORAGE",
            "pendingVersionCount": "0",
            "protectedServerCount": 1,
        },
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan], "total": 1})
        m.get(DEST_URL, payload=SAMPLE_DEST_RAW)
        col = TieringPlanCollection(session)
        result, _ = await col.list()
        await session.disconnect()

    ts = result[0].tiering_status
    assert ts is not None
    assert ts.status == VersionCopyStatus.SKIPPED
    assert ts.reason == CopyReason.SKIPPED_NAS_TO_EXTERNAL
    assert ts.pending_version_count == 0


async def test_get_reraises_non_not_found_api_error() -> None:
    """get() re-raises an APIError whose detail codes do not mean not-found."""
    get_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {"error": {"code": 500, "details": [{"errorCode": 9999}]}}
    async with connected_session() as (session, m):
        m.get(get_url, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await TieringPlanCollection(session).get(PLAN_ID)
        await session.disconnect()

    assert exc_info.type is APIError


async def test_update_reraises_non_conflict_api_error() -> None:
    """update() re-raises an APIError that is not a name conflict."""
    update_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {"error": {"code": 500, "details": [{"errorCode": 9999}]}}
    async with connected_session() as (session, m):
        m.put(update_url, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await TieringPlanCollection(session).update(PLAN_ID, TieringPlanCreateRequest(
                name="tiering plan 1",
                tier_after_days=30,
                destination=_make_fake_remote_storage(),
            ))
        await session.disconnect()

    assert exc_info.type is APIError


async def test_delete_reraises_non_in_use_api_error() -> None:
    """delete() re-raises an APIError whose detail codes are not the in-use code."""
    delete_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {"error": {"code": 500, "details": [{"errorCode": 5000}]}}
    async with connected_session() as (session, m):
        m.delete(delete_url, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await TieringPlanCollection(session).delete(PLAN_ID)
        await session.disconnect()

    assert exc_info.type is APIError


async def test_delete_reraises_api_error_with_non_dict_body() -> None:
    """delete() re-raises an APIError whose response body is not a JSON object."""
    delete_url = f"{BASE_URL}/api/v1/plan/tiering_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.delete(delete_url, status=500, payload="unexpected failure")
        with pytest.raises(APIError) as exc_info:
            await TieringPlanCollection(session).delete(PLAN_ID)
        await session.disconnect()

    assert exc_info.type is APIError


async def test_create_unsupported_storage_type_raises_value_error() -> None:
    """create() with an unmapped RemoteStorage type raises ValueError before any request."""
    import dataclasses

    create_url = f"{BASE_URL}/api/v1/plan/tiering_plan"
    destination = dataclasses.replace(_make_fake_remote_storage(), storage_type=RemoteStorageType.UNKNOWN)
    async with connected_session() as (session, m):
        with pytest.raises(ValueError, match="Unsupported RemoteStorage"):
            await TieringPlanCollection(session).create(TieringPlanCreateRequest(
                name="Bad Destination",
                tier_after_days=30,
                destination=destination,
            ))
        await session.disconnect()

    assert ("POST", URL(create_url)) not in m.requests
