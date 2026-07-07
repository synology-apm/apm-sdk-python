"""RetirementPlanCollection — collection interface for retirement plans."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..exceptions import ResourceNotFoundError
from ..models.retirement_plan import RetirementPlan, RetirementPlanCreateRequest, RetirementRetentionPolicy
from ._shared import (
    _create_plan_and_fetch,
    _delete_plan_checked,
    _not_found_as,
    _paginate,
    _update_plan_and_fetch,
)

_RESOURCE_TYPE = "RetirementPlan"


class RetirementPlanCollection:
    """Collection interface for managing retirement plans.

    Accessed via APMClient.retirement_plans; should not be instantiated directly.
    Retirement plans are category-agnostic and apply across all workload categories.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        name_contains: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[RetirementPlan], int]:
        """List all Retirement Plans.

        Args:
            name_contains: Name keyword search. None = no filter.
            limit:         Maximum records to return (default 500).
            offset:        Pagination start offset (default 0).

        Returns:
            (list of RetirementPlan, total count matching the filter)
        """
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if name_contains:
            params["keyword"] = name_contains

        raw = await self._session.get("/api/v1/plan/archive_plan", params=params)
        return [_parse_retirement_plan(p) for p in raw.get("plans", [])], raw.get("total", 0)

    async def get(self, plan_id: str) -> RetirementPlan:
        """Fetch a Retirement Plan by UUID.

        Args:
            plan_id: Plan UUID.

        Raises:
            ResourceNotFoundError: No matching plan found.
        """
        with _not_found_as(_RESOURCE_TYPE, plan_id, detail_code=4002):
            raw = await self._session.get(f"/api/v1/plan/archive_plan/{plan_id}")
        return _parse_retirement_plan(raw)

    async def get_by_name(self, name: str) -> RetirementPlan:
        """Fetch a Retirement Plan by name.

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
            raw = await self._session.get("/api/v1/plan/archive_plan", params=params)
            return raw.get("plans", []), raw.get("total", 0)

        async for p in _paginate(fetch):
            plan = _parse_retirement_plan(p)
            if plan.name.lower() == q:
                return plan
        raise ResourceNotFoundError(
            f"RetirementPlan '{name}' not found.",
            resource_type=_RESOURCE_TYPE,
            resource_id=name,
        )

    async def create(self, request: RetirementPlanCreateRequest) -> RetirementPlan:
        """Create a Retirement Plan.

        Args:
            request: Plan creation parameters.

        Returns:
            The created plan.

        Raises:
            PlanNameConflictError: A plan with this name already exists.
        """
        body = _build_retirement_body(request)
        return await _create_plan_and_fetch(
            self._session, "/api/v1/plan/archive_plan", body, request.name, _RESOURCE_TYPE, self.get,
        )

    async def update(self, plan_id: str, request: RetirementPlanCreateRequest) -> RetirementPlan:
        """Update an existing Retirement Plan.

        Args:
            plan_id: UUID of the plan to update.
            request: New plan configuration.

        Returns:
            The updated plan.

        Raises:
            PlanNameConflictError: The new name is already taken by another plan.
        """
        body = _build_retirement_body(request)
        return await _update_plan_and_fetch(
            self._session, f"/api/v1/plan/archive_plan/{plan_id}", body, request.name, _RESOURCE_TYPE,
            lambda: self.get(plan_id),
        )

    async def delete(self, plan: RetirementPlan | str) -> None:
        """Delete a Retirement Plan by plan object or UUID.

        Deleting a plan that does not exist is a no-op (the operation succeeds silently).

        Args:
            plan: RetirementPlan object or plan UUID string.

        Raises:
            PlanInUseError: The plan is still assigned to workloads.
        """
        plan_id = plan.plan_id if isinstance(plan, RetirementPlan) else plan
        await _delete_plan_checked(
            self._session, f"/api/v1/plan/archive_plan/{plan_id}", plan_id, _RESOURCE_TYPE,
            in_use_flags={4019: "has_workloads"},
            message=f"Cannot delete plan {plan_id!r}: plan is still in use.",
        )


def _build_retirement_body(request: RetirementPlanCreateRequest) -> dict[str, Any]:
    if request.retention_days is None:
        retention: dict[str, Any] = {
            "keepAll": True,
            "keepVersions": 0,
            "keepDays": 0,
            "gfsDays": 0, "gfsWeeks": 0, "gfsMonths": 0, "gfsYears": 0,
        }
    else:
        retention = {
            "keepAll": False,
            "keepVersions": 1 if request.keep_latest_version else 0,
            "keepDays": request.retention_days,
            "gfsDays": 0, "gfsWeeks": 0, "gfsMonths": 0, "gfsYears": 0,
        }
    return {
        "plan": {
            "name": request.name,
            "description": request.description,
            "retention": retention,
        },
        "runScheduleByControllerTime": request.run_schedule_by_controller_time,
    }


def _parse_retirement_retention(raw: dict[str, Any]) -> RetirementRetentionPolicy:
    """Convert an API retention object to a RetirementRetentionPolicy.

    keepDays  → days (0 converted to None per SDK convention)
    keepVersions > 0 → keep_latest_version=True (boolean flag; value is irrelevant)
    keepAll is not handled separately: when keepAll=True the API returns keepDays=0 and
    keepVersions=0, which maps naturally to days=None, keep_latest_version=False.
    """
    keep_days = raw.get("keepDays", 0)
    return RetirementRetentionPolicy(
        days=keep_days if keep_days > 0 else None,
        keep_latest_version=raw.get("keepVersions", 0) > 0,
    )


def _parse_retirement_plan(raw: dict[str, Any]) -> RetirementPlan:
    """Convert an archive plan object from an API response to the RetirementPlan model."""
    spec: dict[str, Any] = raw.get("spec", {})
    return RetirementPlan(
        plan_id=raw["id"],
        name=spec.get("name", ""),
        description=spec.get("description", ""),
        retention=_parse_retirement_retention(spec.get("retention", {})),
        workload_count=raw.get("workloadCount", 0),
        run_schedule_by_controller_time=spec.get("controllerUtcOffset") is not None,
    )
