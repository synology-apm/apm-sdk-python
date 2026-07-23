"""LogCollection — collection interface for server-scoped log queries."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .._http import WebAPISession
from ..enums import APMActivityLogType, LogLevel
from ..models.backup_server import BackupServer
from ..models.log import APMActivityLog, ConnectionLog, DriveLog, SystemLog
from ._shared import ListResult, _tunnel_headers

_LEVEL_API: dict[LogLevel, str] = {
    LogLevel.INFO: "LEVEL_INFORMATION",
    LogLevel.WARNING:     "LEVEL_WARNING",
    LogLevel.ERROR:       "LEVEL_ERROR",
}

_LEVEL_PARSE: dict[str, LogLevel] = {v: k for k, v in _LEVEL_API.items()}

_TYPE_API: dict[APMActivityLogType, str] = {
    APMActivityLogType.PROTECTION:  "PROTECTION",
    APMActivityLogType.SYSTEM:      "SYSTEM",
    APMActivityLogType.DATA_ACCESS: "DATA_ACCESS",
}

_TYPE_PARSE: dict[str, APMActivityLogType] = {v: k for k, v in _TYPE_API.items()}


def _parse_ts(raw_ts: str | int) -> datetime:
    # Distinct from _shared._parse_ts_optional/_parse_ts_or_now on purpose: log entries
    # always carry a real timestamp, so there is no "0"/None sentinel to map to None or now.
    return datetime.fromtimestamp(int(raw_ts), tz=UTC)


def _to_int_ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _build_log_params(
    *,
    levels: list[LogLevel] | None,
    since: datetime | None,
    until: datetime | None,
    keyword: str | None,
    limit: int,
    offset: int,
    log_type: APMActivityLogType | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Build the query params shared by all four log endpoints, in canonical order."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if levels:
        params["levels"] = [_LEVEL_API[lv] for lv in levels]
    if log_type is not None:
        params["type"] = _TYPE_API[log_type]
    if since is not None:
        params["startTime"] = _to_int_ts(since)
    if until is not None:
        params["endTime"] = _to_int_ts(until)
    if keyword:
        params["keyword"] = keyword
    if location:
        params["location"] = location
    return params


class LogCollection:
    """Collection interface for querying server-scoped logs in APM.

    Accessed via APMClient.logs; should not be instantiated directly.
    All methods require a BackupServer to route the request to the correct backup server.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def _list_logs(
        self,
        server: BackupServer,
        endpoint: str,
        resp_key: str,
        params: dict[str, Any],
        *,
        with_total: bool = False,
    ) -> ListResult[dict[str, Any]]:
        """Fetch one page of a server-scoped log endpoint and return (raw entries, total).

        total is None when with_total is False (the endpoint does not report a
        reliable count for this log type).
        """
        raw = await self._session.get(
            endpoint,
            params=params,
            headers=_tunnel_headers(server.namespace),
        )
        return ListResult(raw.get(resp_key, []), raw.get("total") if with_total else None)

    async def list_activity(
        self,
        server: BackupServer,
        *,
        levels: list[LogLevel] | None = None,
        log_type: APMActivityLogType | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        keyword: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> ListResult[APMActivityLog]:
        """List activity logs from the specified backup server.

        Args:
            server: Target backup server (obtained from BackupServerCollection).
            levels: Filter by severity level; pass multiple values to include more than one.
            log_type: Filter by log category. Single value only.
            since: Start of time range (inclusive).
            until: End of time range (inclusive).
            keyword: Keyword search string.
            limit: Maximum number of records to return (default 25).
            offset: Pagination start offset (default 0).

        Returns:
            (list of APMActivityLog, None — total count is not available for this log type)
        """
        params = _build_log_params(
            levels=levels, log_type=log_type, since=since, until=until,
            keyword=keyword, limit=limit, offset=offset,
        )
        entries_raw, total = await self._list_logs(server, "/api/v1/log/aem-log", "aemLogs", params)
        return ListResult([_parse_activity_log(e) for e in entries_raw], total)

    async def list_drive(
        self,
        server: BackupServer,
        *,
        levels: list[LogLevel] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        keyword: str | None = None,
        location: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> ListResult[DriveLog]:
        """List drive information logs from the specified backup server.

        Args:
            server: Target backup server (obtained from BackupServerCollection).
            levels: Filter by severity level; pass multiple values to include more than one.
            since: Start of time range (inclusive).
            until: End of time range (inclusive).
            keyword: Keyword search string.
            location: Drive location filter.
            limit: Maximum number of records to return (default 25).
            offset: Pagination start offset (default 0).

        Returns:
            (list of DriveLog, total count)
        """
        params = _build_log_params(
            levels=levels, since=since, until=until, keyword=keyword,
            location=location, limit=limit, offset=offset,
        )
        entries_raw, total = await self._list_logs(
            server, "/api/v1/log/drive-log", "driveLogs", params, with_total=True,
        )
        return ListResult([_parse_drive_log(e) for e in entries_raw], total)

    async def list_connection(
        self,
        server: BackupServer,
        *,
        levels: list[LogLevel] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        keyword: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> ListResult[ConnectionLog]:
        """List connection logs from the specified backup server.

        Args:
            server: Target backup server (obtained from BackupServerCollection).
            levels: Filter by severity level; pass multiple values to include more than one.
            since: Start of time range (inclusive).
            until: End of time range (inclusive).
            keyword: Keyword search string.
            limit: Maximum number of records to return (default 25).
            offset: Pagination start offset (default 0).

        Returns:
            (list of ConnectionLog, None — total count is not available for this log type)
        """
        params = _build_log_params(
            levels=levels, since=since, until=until, keyword=keyword,
            limit=limit, offset=offset,
        )
        entries_raw, total = await self._list_logs(
            server, "/api/v1/log/connection-log", "connectionLogs", params,
        )
        return ListResult([ConnectionLog(**_user_log_fields(e)) for e in entries_raw], total)

    async def list_system(
        self,
        server: BackupServer,
        *,
        levels: list[LogLevel] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        keyword: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> ListResult[SystemLog]:
        """List advanced system logs from the specified backup server.

        Args:
            server: Target backup server (obtained from BackupServerCollection).
            levels: Filter by severity level; pass multiple values to include more than one.
            since: Start of time range (inclusive).
            until: End of time range (inclusive).
            keyword: Keyword search string.
            limit: Maximum number of records to return (default 25).
            offset: Pagination start offset (default 0).

        Returns:
            (list of SystemLog, None — total count is not available for this log type)
        """
        params = _build_log_params(
            levels=levels, since=since, until=until, keyword=keyword,
            limit=limit, offset=offset,
        )
        entries_raw, total = await self._list_logs(
            server, "/api/v1/log/general-log", "generalLogs", params,
        )
        return ListResult([SystemLog(**_user_log_fields(e)) for e in entries_raw], total)


def _parse_activity_log(raw: dict[str, Any]) -> APMActivityLog:
    """Convert a raw aem-log entry to APMActivityLog."""
    return APMActivityLog(
        level=_LEVEL_PARSE.get(raw.get("level") or "", LogLevel.INFO),
        log_type=_TYPE_PARSE.get(raw.get("type") or ""),
        timestamp=_parse_ts(raw.get("timestamp") or 0),
        username=raw.get("username") or "",
        description=raw.get("description") or "",
    )


def _parse_drive_log(raw: dict[str, Any]) -> DriveLog:
    """Convert a raw drive-log entry to DriveLog."""
    return DriveLog(
        level=_LEVEL_PARSE.get(raw.get("level") or "", LogLevel.INFO),
        timestamp=_parse_ts(raw.get("timestamp") or 0),
        description=raw.get("description") or "",
        server_name=raw.get("deviceName") or "-",
        model=raw.get("model") or "-",
        location=raw.get("location") or "-",
        serial=raw.get("serial") or "-",
    )


def _user_log_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Shared level/timestamp/username/description fields of connection and system logs."""
    return {
        "level": _LEVEL_PARSE.get(raw.get("level") or "", LogLevel.INFO),
        "timestamp": _parse_ts(raw.get("timestamp") or 0),
        "username": raw.get("username") or "",
        "description": raw.get("description") or "",
    }
