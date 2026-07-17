"""Protection Plan collections: ProtectionPlanCollection / MachinePlanCollection / M365PlanCollection.

Request-body construction lives in _protection_plan_builders.py; response parsing
and the API↔enum string maps live in _protection_plan_parsers.py.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from .._http import WebAPISession
from ..enums import WorkloadCategory
from ..exceptions import ResourceNotFoundError
from ..models.location import LocationInfo
from ..models.protection_plan import (
    M365PlanCreateRequest,
    MachinePlanCreateRequest,
    ProtectionPlan,
)
from ._protection_plan_builders import _build_device_body, _build_m365_body
from ._protection_plan_parsers import _parse_plan
from ._shared import (
    _build_remote_location_cache,
    _create_plan_and_fetch,
    _delete_plan_checked,
    _not_found_as,
    _paginate,
    _update_plan_and_fetch,
)

_RESOURCE_TYPE = "ProtectionPlan"

_CATEGORY_SERVICE_TYPE: dict[WorkloadCategory, str] = {
    WorkloadCategory.MACHINE: "DEVICE",
    WorkloadCategory.M365:    "M365",
}

_DEST_TYPE_APPLIANCE = "APPLIANCE"
_BACKUP_SERVER_MAX_COUNT = 3000


# ── Destination lookup helpers ─────────────────────────────────────────────


async def _build_location_cache(
    session: WebAPISession,
    plans_raw: list[dict[str, Any]],
) -> dict[str, LocationInfo]:
    """Collect enabled copy destinations from raw plan list data and return {dest_key: LocationInfo}."""
    appliance_namespaces: set[str] = set()
    remote_ids: list[str] = []
    seen: set[str] = set()

    for p in plans_raw:
        bc = p.get("spec", {}).get("backupCopy", {})
        if not (bc.get("enabled") and bc.get("destination")):
            continue
        dest_id = bc["destination"]
        if dest_id in seen:
            continue
        seen.add(dest_id)
        dest_type = bc.get("destinationType", _DEST_TYPE_APPLIANCE)
        if dest_type == _DEST_TYPE_APPLIANCE:
            appliance_namespaces.add(dest_id)
        else:
            remote_ids.append(dest_id)

    if not appliance_namespaces and not remote_ids:
        return {}

    cache: dict[str, LocationInfo] = {}

    if appliance_namespaces:
        raw = await session.get(
            "/api/v1/infra/backup_server",
            params={"limit": _BACKUP_SERVER_MAX_COUNT, "offset": 0},
        )
        for s in raw.get("backupServers", []):
            ns = s.get("namespace", "")
            if ns not in appliance_namespaces:
                continue
            name = s.get("status", {}).get("hostName", "")
            if name:
                cache[ns] = LocationInfo(
                    is_remote_storage=False,
                    identifier=ns,
                    name=name,
                    endpoint=s.get("spec", {}).get("addr", ""),
                    vault=None,
                )

    cache.update(await _build_remote_location_cache(session, remote_ids))

    return cache


# ── Shared list / get helpers ──────────────────────────────────────────────


async def _list_plans(
    session: WebAPISession,
    service_types: str | list[str],
    name_contains: str | None,
    limit: int,
    offset: int,
) -> tuple[list[ProtectionPlan], int]:
    """Fetch a page of plans for the given serviceType(s) and parse them."""
    params: dict[str, Any] = {"offset": offset, "limit": limit, "serviceType": service_types}
    if name_contains:
        params["keyword"] = name_contains
    raw = await session.get("/api/v1/plan/backup_plan", params=params)
    plans_raw = raw.get("plans", [])
    cache = await _build_location_cache(session, plans_raw)
    return [_parse_plan(p, cache) for p in plans_raw], raw.get("total", 0)


async def _get_plan_by_name(
    session: WebAPISession,
    service_types: str | list[str],
    name: str,
) -> ProtectionPlan:
    """Page through plans for the given serviceType(s) until an exact name match is found."""
    q = name.lower()

    async def fetch(offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {
            "keyword": name,
            "limit": limit,
            "offset": offset,
            "serviceType": service_types,
        }
        raw = await session.get("/api/v1/plan/backup_plan", params=params)
        return raw.get("plans", []), raw.get("total", 0)

    async for p in _paginate(fetch):
        if p.get("spec", {}).get("name", "").lower() == q:
            cache = await _build_location_cache(session, [p])
            return _parse_plan(p, cache)
    raise ResourceNotFoundError(
        f"ProtectionPlan '{name}' not found.",
        resource_type=_RESOURCE_TYPE,
        resource_id=name,
    )


async def _get_plan_by_id(session: WebAPISession, plan_id: str) -> ProtectionPlan:
    """Fetch a single plan by UUID and parse it."""
    with _not_found_as(_RESOURCE_TYPE, plan_id, detail_code=4001):
        raw = await session.get(f"/api/v1/plan/backup_plan/{plan_id}")
    plan_raw = raw if "id" in raw else raw.get("plan", raw)
    cache = await _build_location_cache(session, [plan_raw])
    return _parse_plan(plan_raw, cache)


# ── ProtectionPlanCollection ─────────────────────────────────────────────


class ProtectionPlanCollection:
    """Cross-domain collection for querying Protection Plans across all categories.

    Accessed via APMClient.plans; should not be instantiated directly.
    To change the plan assigned to a specific workload, use
    APMClient.machine.workloads.change_plan() or APMClient.m365.workloads.change_plan() instead.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        category: WorkloadCategory | None = None,
        name_contains: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[ProtectionPlan], int]:
        """List Protection Plans; supports cross-category queries.

        Args:
            category:      WorkloadCategory filter; None (default) lists all categories.
            name_contains: Name keyword search.
            limit:         Maximum number of records to return (default 500).
            offset:        Pagination start offset (default 0).

        Returns:
            (list of ProtectionPlan, total count matching the filter)
        """
        if category == WorkloadCategory.MACHINE:
            service_types: list[str] | str = _CATEGORY_SERVICE_TYPE[WorkloadCategory.MACHINE]
        elif category == WorkloadCategory.M365:
            service_types = _CATEGORY_SERVICE_TYPE[WorkloadCategory.M365]
        else:
            service_types = list(_CATEGORY_SERVICE_TYPE.values())
        return await _list_plans(self._session, service_types, name_contains, limit, offset)

    async def get(self, plan_id: str) -> ProtectionPlan:
        """Fetch a Protection Plan by UUID (category-agnostic).

        Args:
            plan_id: Plan UUID.

        Raises:
            ResourceNotFoundError: The specified plan_id does not exist.
        """
        return await _get_plan_by_id(self._session, plan_id)

    async def get_by_name(self, name: str) -> ProtectionPlan:
        """Fetch a Protection Plan by name (cross-category search).

        Args:
            name: Plan name (exact match, case-insensitive).

        Raises:
            ResourceNotFoundError: The specified plan does not exist.
        """
        return await _get_plan_by_name(
            self._session, list(_CATEGORY_SERVICE_TYPE.values()), name
        )

    async def create(
        self,
        request: MachinePlanCreateRequest | M365PlanCreateRequest,
    ) -> ProtectionPlan:
        """Create a Protection Plan (Machine or M365), dispatching by request type.

        Args:
            request: MachinePlanCreateRequest or M365PlanCreateRequest.

        Returns:
            The created plan with all fields populated.

        Raises:
            PlanNameConflictError: A plan with this name already exists.
        """
        if isinstance(request, MachinePlanCreateRequest):
            return await _create_plan(self._session, request, _build_device_body)
        return await _create_plan(self._session, request, _build_m365_body)

    async def delete(self, plan: ProtectionPlan | str) -> None:
        """Delete a Protection Plan by plan object or UUID.

        Deleting a plan that does not exist is a no-op (the operation succeeds silently).

        Args:
            plan: ProtectionPlan object or plan UUID string.

        Raises:
            PlanInUseError: The plan is still assigned to workloads or used as a server template.
        """
        await _delete_plan(self._session, plan, _RESOURCE_TYPE)


# ── Shared base ───────────────────────────────────────────────────────────


class _BasePlanCollection:
    """Shared list / get / get_by_name / delete logic; subclasses only need to set _service_type."""

    _service_type: ClassVar[str]

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        name_contains: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[ProtectionPlan], int]:
        """List Protection Plans.

        Args:
            name_contains: Name fuzzy search. None = no filter.
            limit:         Maximum records to return (default 500).
            offset:        Pagination start offset (default 0).

        Returns:
            (list of ProtectionPlan, total count matching the filter)
        """
        return await _list_plans(self._session, self._service_type, name_contains, limit, offset)

    async def get(self, plan_id: str) -> ProtectionPlan:
        """Fetch a Protection Plan by UUID.

        Args:
            plan_id: Plan UUID.

        Raises:
            ResourceNotFoundError: No matching plan found.
        """
        return await _get_plan_by_id(self._session, plan_id)

    async def get_by_name(self, name: str) -> ProtectionPlan:
        """Fetch a Protection Plan by name.

        Args:
            name: Plan name (exact match, case-insensitive).

        Raises:
            ResourceNotFoundError: Plan not found.
        """
        return await _get_plan_by_name(self._session, self._service_type, name)

    async def delete(self, plan: ProtectionPlan | str) -> None:
        """Delete a Protection Plan by plan object or UUID.

        Deleting a plan that does not exist is a no-op (the operation succeeds silently).

        Args:
            plan: ProtectionPlan object or plan UUID string.

        Raises:
            PlanInUseError: The plan is still assigned to workloads or used as a server template.
        """
        await _delete_plan(self._session, plan, _RESOURCE_TYPE)


# ── MachinePlanCollection ─────────────────────────────────────────────────


class MachinePlanCollection(_BasePlanCollection):
    """Collection interface for managing device backup plans (Machine Protection Plans).

    Accessed via APMClient.machine.plans; should not be instantiated directly.
    Handles only category=MACHINE plans; for other categories use the corresponding domain collection.
    """

    _service_type = _CATEGORY_SERVICE_TYPE[WorkloadCategory.MACHINE]

    async def create(self, request: MachinePlanCreateRequest) -> ProtectionPlan:
        """Create a Machine (DEVICE) Protection Plan.

        Args:
            request: Plan creation parameters.

        Returns:
            The created plan with all config fields populated.

        Raises:
            PlanNameConflictError: A plan with this name already exists.
        """
        return await _create_plan(self._session, request, _build_device_body)

    async def update(self, plan_id: str, request: MachinePlanCreateRequest) -> ProtectionPlan:
        """Update an existing Machine Protection Plan.

        Args:
            plan_id: UUID of the plan to update.
            request: New plan configuration.

        Returns:
            The updated plan with all config fields populated.

        Raises:
            PlanNameConflictError: The new name is already taken by another plan.
        """
        return await _update_plan(self._session, plan_id, request, _build_device_body)


# ── M365PlanCollection ────────────────────────────────────────────────────


class M365PlanCollection(_BasePlanCollection):
    """Collection interface for managing M365 backup plans.

    Accessed via APMClient.m365.plans; should not be instantiated directly.
    """

    _service_type = _CATEGORY_SERVICE_TYPE[WorkloadCategory.M365]

    async def create(self, request: M365PlanCreateRequest) -> ProtectionPlan:
        """Create an M365 Protection Plan.

        Args:
            request: Plan creation parameters.

        Returns:
            The created plan with all fields populated.

        Raises:
            PlanNameConflictError: A plan with this name already exists.
        """
        return await _create_plan(self._session, request, _build_m365_body)

    async def update(self, plan_id: str, request: M365PlanCreateRequest) -> ProtectionPlan:
        """Update an existing M365 Protection Plan.

        Args:
            plan_id: UUID of the plan to update.
            request: New plan configuration.

        Returns:
            The updated plan with all fields populated.

        Raises:
            PlanNameConflictError: The new name is already taken by another plan.
        """
        return await _update_plan(self._session, plan_id, request, _build_m365_body)


# ── Shared create / update / delete helpers ───────────────────────────────


_PlanRequestT = TypeVar("_PlanRequestT", MachinePlanCreateRequest, M365PlanCreateRequest)


async def _create_plan(
    session: WebAPISession,
    request: _PlanRequestT,
    body_builder: Callable[[_PlanRequestT], dict[str, Any]],
) -> ProtectionPlan:
    body = body_builder(request)
    return await _create_plan_and_fetch(
        session, "/api/v1/plan/backup_plan", body, request.name, _RESOURCE_TYPE,
        lambda plan_id: _get_plan_by_id(session, plan_id),
    )


async def _update_plan(
    session: WebAPISession,
    plan_id: str,
    request: _PlanRequestT,
    body_builder: Callable[[_PlanRequestT], dict[str, Any]],
) -> ProtectionPlan:
    body = body_builder(request)
    return await _update_plan_and_fetch(
        session, f"/api/v1/plan/backup_plan/{plan_id}", body, request.name, _RESOURCE_TYPE,
        lambda: _get_plan_by_id(session, plan_id),
    )


async def _delete_plan(
    session: WebAPISession,
    plan: ProtectionPlan | str,
    resource_type: str,
) -> None:
    plan_id = plan.plan_id if isinstance(plan, ProtectionPlan) else plan
    await _delete_plan_checked(
        session, f"/api/v1/plan/backup_plan/{plan_id}", plan_id, resource_type,
        in_use_flags={4019: "has_workloads", 4017: "has_server_template"},
        message=f"Cannot delete plan {plan_id!r}: plan is still in use.",
    )
