"""TieringPlanCollection — collection interface for tiering plans."""
from __future__ import annotations

import asyncio
from datetime import time
from typing import Any

from .._http import WebAPISession
from ..exceptions import ResourceNotFoundError
from ..models.location import LocationInfo
from ..models.tiering_plan import TieringPlan, TieringPlanCreateRequest
from ._shared import (
    _STORAGE_TYPE_TO_DEST_TYPE,
    _build_remote_location_cache,
    _create_plan_and_fetch,
    _delete_plan_checked,
    _fetch_remote_storage_location,
    _not_found_as,
    _paginate,
    _parse_tiering_status,
    _update_plan_and_fetch,
)

_RESOURCE_TYPE = "TieringPlan"


class TieringPlanCollection:
    """Collection interface for managing tiering plans.

    Accessed via APMClient.tiering_plans; should not be instantiated directly.
    Tiering plans move backup versions to a remote storage destination after a
    configurable number of days.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        name_contains: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[TieringPlan], int]:
        """List all Tiering Plans.

        Destination details are resolved concurrently for all plans in the page.

        Args:
            name_contains: Name keyword search. None = no filter.
            limit:         Maximum records to return (default 500).
            offset:        Pagination start offset (default 0).

        Returns:
            (list of TieringPlan, total count matching the filter)
        """
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if name_contains:
            params["keyword"] = name_contains

        raw = await self._session.get("/api/v1/plan/tiering_plan", params=params)
        plans_raw = raw.get("plans", [])
        cache = await _build_destination_cache(self._session, plans_raw)
        return [_parse_tiering_plan(p, cache) for p in plans_raw], raw.get("total", 0)

    async def get(self, plan_id: str) -> TieringPlan:
        """Fetch a Tiering Plan by UUID.

        Args:
            plan_id: Plan UUID.

        Raises:
            ResourceNotFoundError: No matching plan found.
        """
        with _not_found_as(_RESOURCE_TYPE, plan_id, detail_code=4003):
            raw = await self._session.get(f"/api/v1/plan/tiering_plan/{plan_id}")
        dest_id = raw.get("spec", {}).get("destination", "")
        dest = await _fetch_remote_storage_location(self._session, dest_id) if dest_id else None
        return _parse_tiering_plan(raw, {dest_id: dest} if dest else {})

    async def get_by_name(self, name: str) -> TieringPlan:
        """Fetch a Tiering Plan by name.

        Pages through results with keyword=name until an exact name match is found.
        The destination is resolved only for the matching plan.

        Args:
            name: Plan name (exact match, case-insensitive).

        Raises:
            ResourceNotFoundError: Plan not found.
        """
        q = name.lower()

        async def fetch(offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
            params: dict[str, Any] = {
                "keyword": name,
                "limit": limit,
                "offset": offset,
            }
            raw = await self._session.get("/api/v1/plan/tiering_plan", params=params)
            return raw.get("plans", []), raw.get("total", 0)

        async for p in _paginate(fetch):
            if p.get("spec", {}).get("name", "").lower() == q:
                dest_id = p.get("spec", {}).get("destination", "")
                dest = await _fetch_remote_storage_location(self._session, dest_id) if dest_id else None
                return _parse_tiering_plan(p, {dest_id: dest} if dest else {})
        raise ResourceNotFoundError(
            f"TieringPlan '{name}' not found.",
            resource_type=_RESOURCE_TYPE,
            resource_id=name,
        )

    async def create(self, request: TieringPlanCreateRequest) -> TieringPlan:
        """Create a Tiering Plan.

        Args:
            request: Plan creation parameters (destination must be from apm.remote_storages).

        Returns:
            The created plan with destination resolved.

        Raises:
            PlanNameConflictError: A plan with this name already exists.
        """
        body = _build_tiering_body(request)
        return await _create_plan_and_fetch(
            self._session, "/api/v1/plan/tiering_plan", body, request.name, _RESOURCE_TYPE, self.get,
        )

    async def update(self, plan_id: str, request: TieringPlanCreateRequest) -> TieringPlan:
        """Update an existing Tiering Plan.

        Args:
            plan_id: UUID of the plan to update.
            request: New plan configuration.

        Returns:
            The updated plan with destination resolved.

        Raises:
            PlanNameConflictError: The new name is already taken by another plan.
        """
        body = _build_tiering_body(request)
        return await _update_plan_and_fetch(
            self._session, f"/api/v1/plan/tiering_plan/{plan_id}", body, request.name, _RESOURCE_TYPE,
            lambda: self.get(plan_id),
        )

    async def delete(self, plan: TieringPlan | str) -> None:
        """Delete a Tiering Plan by plan object or UUID.

        Deleting a plan that does not exist is a no-op (the operation succeeds silently).

        Args:
            plan: TieringPlan object or plan UUID string.

        Raises:
            PlanInUseError: Backup servers are assigned to this plan.
        """
        plan_id = plan.plan_id if isinstance(plan, TieringPlan) else plan
        await _delete_plan_checked(
            self._session, f"/api/v1/plan/tiering_plan/{plan_id}", plan_id, _RESOURCE_TYPE,
            in_use_flags={4029: "has_backup_servers"},
            message=f"Cannot delete plan {plan_id!r}: backup servers are assigned to this plan.",
        )


def _build_tiering_body(request: TieringPlanCreateRequest) -> dict[str, Any]:
    dest_type = _STORAGE_TYPE_TO_DEST_TYPE.get(request.destination.storage_type)
    if dest_type is None:
        raise ValueError(
            f"Unsupported RemoteStorage type {request.destination.storage_type!r}."
        )
    t = request.daily_check_time
    return {
        "plan": {
            "name": request.name,
            "description": request.description,
            "destinationType": dest_type,
            "destination": request.destination.storage_id,
            "schedule": {
                "scheduleType": "SCHEDULE",
                "repeatType": "DAILY",
                "runHour": t.hour,
                "runMin": t.minute,
            },
            "tieringAfterDays": request.tier_after_days,
        },
        "runScheduleByControllerTime": request.run_schedule_by_controller_time,
    }


async def _build_destination_cache(
    session: WebAPISession,
    plans_raw: list[dict[str, Any]],
) -> dict[str, LocationInfo]:
    """Collect unique destination IDs from a plan list and resolve them concurrently."""
    unique: list[str] = []
    seen: set[str] = set()
    for p in plans_raw:
        dest_id = p.get("spec", {}).get("destination", "")
        if dest_id and dest_id not in seen:
            unique.append(dest_id)
            seen.add(dest_id)
    return await _build_remote_location_cache(session, unique)


async def _get_plans_bulk(
    session: WebAPISession,
    plan_ids: list[str],
) -> dict[str, TieringPlan]:
    """Fetch multiple tiering plans concurrently, resolving all destinations in one batch.

    Used by BackupServerCollection to resolve per-server tiering plan references
    without the per-plan sequential destination fetch of get(). Plans that cannot
    be fetched or parsed are silently omitted.
    """
    async def fetch_raw(pid: str) -> dict[str, Any] | None:
        try:
            raw: dict[str, Any] = await session.get(f"/api/v1/plan/tiering_plan/{pid}")
            return raw
        except Exception:
            return None

    raws = await asyncio.gather(*[fetch_raw(p) for p in plan_ids])
    plan_raws = [(pid, raw) for pid, raw in zip(plan_ids, raws) if raw is not None]
    dest_cache = await _build_destination_cache(session, [raw for _, raw in plan_raws])
    result: dict[str, TieringPlan] = {}
    for pid, raw in plan_raws:
        try:
            result[pid] = _parse_tiering_plan(raw, dest_cache)
        except Exception:
            continue
    return result


def _parse_tiering_plan(raw: dict[str, Any], dest_cache: dict[str, LocationInfo]) -> TieringPlan:
    """Convert a tiering plan API response object to the SDK TieringPlan model."""
    spec: dict[str, Any] = raw.get("spec", {})
    tiering_info: dict[str, Any] = raw.get("tieringInfo", {})
    schedule: dict[str, Any] = spec.get("schedule", {})
    dest_id = spec.get("destination", "")
    return TieringPlan(
        plan_id=raw["id"],
        name=spec.get("name", ""),
        description=spec.get("description", ""),
        tiering_after_days=spec.get("tieringAfterDays", 0),
        daily_check_time=time(schedule.get("runHour", 0), schedule.get("runMin", 0)),
        destination=dest_cache.get(dest_id),
        server_count=tiering_info.get("protectedServerCount", 0),
        tiering_status=_parse_tiering_status(tiering_info),
        run_schedule_by_controller_time=spec.get("controllerUtcOffset") is not None,
    )
