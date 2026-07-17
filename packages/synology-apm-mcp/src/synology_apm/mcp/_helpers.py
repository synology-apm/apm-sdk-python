"""Resolution helpers and pagination utilities for MCP tool handlers."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from pydantic import BeforeValidator

from synology_apm.mcp._errors import run_tool
from synology_apm.sdk import (
    APMClient,
    ExchangeExportCollection,
    GroupExportCollection,
    M365ExportActivity,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    ProtectionPlan,
    ResourceNotFoundError,
    RetirementPlan,
    WorkloadVersion,
)

_T = TypeVar("_T")
_U = TypeVar("_U")
_EnumT = TypeVar("_EnumT", bound=Enum)

MAX_LIST_LIMIT = 500

# Append to a list tool's description= so the documented result shape always matches
# the reliable_total mode it actually passes to list_result()/list_tool(), instead of
# each call site retyping (and risking drift from) this sentence.
LIST_RESULT_SUFFIX = (
    "Returns {items, total, truncated?} (truncated appears only when more results exist beyond this page)."
)
LIST_RESULT_SUFFIX_UNRELIABLE_TOTAL = (
    "Returns {items, total, truncated?} (total is always null since this endpoint does not report an "
    "accurate count; truncated indicates more results may exist beyond this page)."
)


def clamp_limit(limit: int) -> int:
    """Cap a caller-supplied page size at the maximum the API accepts per call."""
    return min(limit, MAX_LIST_LIMIT)


def to_enum_list(cls: type[_EnumT], values: Sequence[str] | None) -> list[_EnumT] | None:
    """Convert an optional list of raw strings to a list of enum members, or None if empty/omitted."""
    return [cls(v) for v in values] if values else None


def coerce_json_encoded_list(value: object) -> object:
    """Parse a JSON-encoded array string back into a list before validation.

    Some MCP clients encode list-typed tool arguments as a JSON string (e.g.
    '["mon","wed"]') instead of a native JSON array. fastmcp works around this
    for prompt arguments (fastmcp.prompts.function_prompt._convert_string_arguments)
    but not for tool arguments, so tool-level list parameters need this applied
    explicitly. Non-string values, and strings that aren't a JSON array, pass
    through unchanged so normal pydantic validation still reports the real error.
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    return parsed if isinstance(parsed, list) else value


JSON_LIST_VALIDATOR = BeforeValidator(coerce_json_encoded_list)


async def list_result(
    coro: Awaitable[tuple[list[_T], int]],
    serializer: Callable[[_T], Any],
    *,
    limit: int | None = None,
    offset: int = 0,
    reliable_total: bool = True,
) -> dict[str, Any]:
    """Await a paginated list coroutine and return {items, total, truncated?}.

    When reliable_total is True (the default), total reflects the endpoint's real
    count and truncated is included and set to True only when offset + the returned
    item count is less than total, signalling that the caller should use pagination
    to retrieve the rest. Some endpoints never report a real total; pass
    reliable_total=False and the effective limit used for the call, and total is
    reported as None while truncated is instead inferred from the page being full
    (len(items) == limit), which signals there may be more results beyond this page.
    """
    items, total = await coro
    result: dict[str, Any] = {"items": [serializer(x) for x in items]}
    if reliable_total:
        result["total"] = total
        if offset + len(items) < total:
            result["truncated"] = True
    else:
        result["total"] = None
        if limit is not None and len(items) == limit:
            result["truncated"] = True
    return result


async def get_result(coro: Awaitable[_T], serializer: Callable[[_T], Any]) -> Any:
    """Await a single-item coroutine and return the serialized result."""
    return serializer(await coro)


async def list_tool(
    coro: Awaitable[tuple[list[_T], int]],
    serializer: Callable[[_T], Any],
    *,
    limit: int | None = None,
    offset: int = 0,
    reliable_total: bool = True,
) -> str:
    """Combine list_result() + run_tool() for the common paginated-list tool body."""
    return await run_tool(list_result(coro, serializer, limit=limit, offset=offset, reliable_total=reliable_total))


async def get_tool(coro: Awaitable[_T], serializer: Callable[[_T], Any]) -> str:
    """Combine get_result() + run_tool() for the common single-item tool body."""
    return await run_tool(get_result(coro, serializer))


def parse_dt_optional(s: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string, or return None if s is empty/None."""
    if not s:
        return None
    return datetime.fromisoformat(s)


async def resolve_export_activity(
    collection: ExchangeExportCollection | GroupExportCollection,
    workload: M365Workload,
    activity_id: str,
) -> M365ExportActivity:
    """Resolve an M365 export activity by ID from the workload's export activity list."""
    offset = 0
    while True:
        activities, total = await collection.list(workload, limit=MAX_LIST_LIMIT, offset=offset)
        activity = next((a for a in activities if a.activity_id == activity_id), None)
        if activity is not None:
            return activity
        offset += len(activities)
        if not activities or offset >= total:
            break
    raise ResourceNotFoundError(
        f"Export activity {activity_id!r} not found for this workload.",
        resource_type="M365ExportActivity",
        resource_id=activity_id,
    )


async def _resolve_one_plan(apm: APMClient, plan_id: str) -> ProtectionPlan | RetirementPlan:
    """Look up a plan id as a protection plan first, falling back to a retirement plan."""
    try:
        return await apm.plans.get(plan_id)
    except ResourceNotFoundError:
        return await apm.retirement_plans.get(plan_id)


async def resolve_plan_filter(
    apm: APMClient,
    plan_ids: list[str] | None,
) -> list[ProtectionPlan | RetirementPlan] | None:
    """Resolve a list of plan ids to protection/retirement plan objects, concurrently.

    Each id is looked up as a protection plan first, falling back to a retirement
    plan, since workload list() filters accept either.
    """
    if not plan_ids:
        return None
    ids = [plan_id for plan_id in plan_ids if plan_id]
    return list(await asyncio.gather(*(_resolve_one_plan(apm, plan_id) for plan_id in ids)))


async def _resolve_version_for_workload(collection: Any, workload: Any, version_id: str | None) -> WorkloadVersion:
    """Resolve one version of an already-resolved workload, or its latest if version_id is None."""
    if version_id:
        return await collection.get_version(workload, version_id)
    return await collection.get_latest_version(workload)


async def resolve_machine_version(
    apm: APMClient,
    *,
    workload_id: str,
    namespace: str,
    version_id: str | None,
) -> tuple[MachineWorkload, WorkloadVersion]:
    """Resolve a machine workload and then one of its versions, or the latest if version_id is None."""
    workload = await apm.machine.workloads.get(workload_id, namespace)
    version = await _resolve_version_for_workload(apm.machine.workloads, workload, version_id)
    return workload, version


async def resolve_m365_version(
    apm: APMClient,
    *,
    workload_id: str,
    namespace: str,
    tenant_id: str,
    workload_type: str,
    version_id: str | None,
) -> tuple[M365Workload, WorkloadVersion]:
    """Resolve an M365 workload and then one of its versions, or the latest if version_id is None."""
    workload = await apm.m365.workloads.get(
        workload_id, namespace, tenant_id=tenant_id, workload_type=M365WorkloadType(workload_type)
    )
    version = await _resolve_version_for_workload(apm.m365.workloads, workload, version_id)
    return workload, version
