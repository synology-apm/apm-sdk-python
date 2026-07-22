"""Log tools: activity, drive, connection, and system logs (DP servers only)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import Context

from synology_apm.mcp._enums import LogLevelLiteral
from synology_apm.mcp._errors import run_tool
from synology_apm.mcp._helpers import (
    JSON_LIST_VALIDATOR,
    LIST_RESULT_SUFFIX,
    LIST_RESULT_SUFFIX_UNRELIABLE_TOTAL,
    clamp_limit,
    list_result,
    parse_dt_optional,
    to_enum_list,
)
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.sdk import APMActivityLogType, APMClient, BackupServerType, LogLevel

_LOG_TYPE = Literal["protection", "system", "data_access"]


async def _resolve_dp_server(apm: APMClient, server_id: str):
    """Resolve a backup server, rejecting NAS servers (logs only available on DP)."""
    server = await apm.backup_servers.get(server_id)
    if server.server_type == BackupServerType.NAS:
        raise ValueError(f"Log access is not supported for NAS-type server {server.name!r}. Only DP appliances expose logs via this API.")
    return server


async def _list_dp_server_logs(
    apm: APMClient,
    server_id: str,
    sdk_list_fn: Callable[..., Any],
    *,
    limit: int,
    offset: int,
    **extra_kwargs: Any,
) -> dict[str, Any]:
    """Shared body for the 4 list_*_logs tools: resolve the (non-NAS) server, call
    the given SDK log-list method, and wrap the result via list_result().

    The NAS-rejection in _resolve_dp_server must stay inside this coroutine (rather
    than the whole thing being expressed as list_tool(...)) so the outer run_tool()
    at each call site catches that ValueError too, not just errors from the list call.

    Whether the result's total is reliable varies by log type (True only for drive
    logs, since that's the only one APM reports a true total for); list_result()
    derives this directly from whether the coroutine's total is None, so it does
    not need to be passed in here.
    """
    eff_limit = clamp_limit(limit)
    dp_server = await _resolve_dp_server(apm, server_id)
    coro = sdk_list_fn(dp_server, limit=eff_limit, offset=offset, **extra_kwargs)
    return await list_result(coro, lambda x: x.to_dict(), limit=eff_limit, offset=offset)


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register all log tools onto server."""

    @registrar.tool(description=(
        "List APM activity logs for a backup server (DP appliances only). Filter by level "
        "(info/warning/error), log_type (protection/system/data_access), time window, or keyword. "
        f"{LIST_RESULT_SUFFIX_UNRELIABLE_TOTAL}"
    ))
    async def list_activity_logs(
        ctx: Context,
        server_id: str,
        levels: Annotated[list[LogLevelLiteral], JSON_LIST_VALIDATOR] | None = None,
        log_type: _LOG_TYPE | None = None,
        since: str | None = None,
        until: str | None = None,
        keyword: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_tool(_list_dp_server_logs(
            apm, server_id, apm.logs.list_activity,
            limit=limit, offset=offset,
            levels=to_enum_list(LogLevel, levels),
            log_type=APMActivityLogType(log_type) if log_type else None,
            since=parse_dt_optional(since),
            until=parse_dt_optional(until),
            keyword=keyword,
        ))

    @registrar.tool(description=(
        "List drive/disk logs for a backup server (DP appliances only). Filter by level (info/warning/error), "
        f"time window, location, or keyword. {LIST_RESULT_SUFFIX}"
    ))
    async def list_drive_logs(
        ctx: Context,
        server_id: str,
        levels: Annotated[list[LogLevelLiteral], JSON_LIST_VALIDATOR] | None = None,
        since: str | None = None,
        until: str | None = None,
        keyword: str | None = None,
        location: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_tool(_list_dp_server_logs(
            apm, server_id, apm.logs.list_drive,
            limit=limit, offset=offset,
            levels=to_enum_list(LogLevel, levels),
            since=parse_dt_optional(since),
            until=parse_dt_optional(until),
            keyword=keyword,
            location=location,
        ))

    @registrar.tool(description=(
        "List connection/authentication logs for a backup server (DP appliances only). Filter by level "
        f"(info/warning/error), time window, or keyword. {LIST_RESULT_SUFFIX_UNRELIABLE_TOTAL}"
    ))
    async def list_connection_logs(
        ctx: Context,
        server_id: str,
        levels: Annotated[list[LogLevelLiteral], JSON_LIST_VALIDATOR] | None = None,
        since: str | None = None,
        until: str | None = None,
        keyword: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_tool(_list_dp_server_logs(
            apm, server_id, apm.logs.list_connection,
            limit=limit, offset=offset,
            levels=to_enum_list(LogLevel, levels),
            since=parse_dt_optional(since),
            until=parse_dt_optional(until),
            keyword=keyword,
        ))

    @registrar.tool(description=(
        "List advanced system logs for a backup server (DP appliances only). Filter by level "
        f"(info/warning/error), time window, or keyword. {LIST_RESULT_SUFFIX_UNRELIABLE_TOTAL}"
    ))
    async def list_system_logs(
        ctx: Context,
        server_id: str,
        levels: Annotated[list[LogLevelLiteral], JSON_LIST_VALIDATOR] | None = None,
        since: str | None = None,
        until: str | None = None,
        keyword: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_tool(_list_dp_server_logs(
            apm, server_id, apm.logs.list_system,
            limit=limit, offset=offset,
            levels=to_enum_list(LogLevel, levels),
            since=parse_dt_optional(since),
            until=parse_dt_optional(until),
            keyword=keyword,
        ))
