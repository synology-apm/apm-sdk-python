"""ActivityCollection — interface for querying backup/restore activity records.

Request-body construction (filter/cancel-type maps) and response parsing live in
_activity_parsers.py.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, ClassVar, Generic, TypeVar

from .._http import WebAPISession, _has_detail_code
from ..enums import (
    BackupActivityStatus,
    BackupScope,
    M365WorkloadType,
    MachineWorkloadType,
    RestoreActivityStatus,
    WorkloadCategory,
)
from ..exceptions import InvalidOperationError, ResourceNotFoundError
from ..models.activity import BackupActivity, RestoreActivity
from ..models.version import WorkloadVersion
from ..models.workload import Workload
from ._activity_parsers import (
    _BACKUP_SCOPE_MAP,
    _BACKUP_STATUS_TO_API,
    _M365_TYPE_TO_SAAS_SERVICE,
    _MACHINE_TYPE_TO_CATEGORY_SERVICE,
    _RESTORE_STATUS_TO_API,
    _SUBTYPE_TO_CANCEL_TYPE,
    _parse_backup_activity,
    _parse_data_sizes,
    _parse_log_entries,
    _parse_restore_activity,
)
from ._shared import ListResult, _paginate, _tunnel_headers

ActivityT = TypeVar("ActivityT", BackupActivity, RestoreActivity)


async def _get_latest_by_name(
    search: Callable[[bool], Awaitable[ListResult[ActivityT]]],
    get: Callable[[str], Awaitable[ActivityT]],
    name: str,
    noun: str,
) -> ActivityT:
    """Shared body for get_latest_by_workload_name(): search RECENT then HISTORY, exact-match the name."""
    q = name.lower()
    for history in (False, True):
        candidates, _ = await search(history)
        match = next((a for a in candidates if a.workload_name.lower() == q), None)
        if match is not None:
            return await get(match.activity_id)
    raise ResourceNotFoundError(
        f"No {noun} activity found for workload '{name}'.",
        resource_type="Activity",
        resource_id=name,
    )


async def _fetch_backup_activity_detail(
    session: WebAPISession, *, execution_id: str, workload_id: str, namespace: str,
) -> dict[str, Any]:
    """GET /api/v1/activity/backup/activity and return its `activity` sub-object."""
    raw_detail = await session.get(
        "/api/v1/activity/backup/activity",
        params={"executionId": execution_id, "workloadUid": workload_id, "namespace": namespace},
    )
    detail: dict[str, Any] = raw_detail.get("activity") or {}
    return detail


async def _fetch_backup_detail_extras(
    session: WebAPISession, detail: dict[str, Any], *, log_uid: str, log_namespace: str,
) -> dict[str, Any]:
    """Derive the get()/get_by_version() dataclasses.replace() fields from a fetched activity detail.

    Shared tail of BackupActivityCollection.get() and .get_by_version(): resolves
    data_change_bytes/data_deduped_bytes/backup_scope from the detail, then fetches and
    parses the activity's detail log.
    """
    detail_status = detail.get("status") or {}
    detail_spec = detail.get("spec") or {}

    data_change, data_deduped = _parse_data_sizes(detail_status)
    machine_info = detail_spec.get("machineInfo") or {}
    _scope_raw = machine_info.get("backupScope") or None
    backup_scope: BackupScope | None = _BACKUP_SCOPE_MAP.get(_scope_raw) if _scope_raw else None

    raw_logs = await session.get(
        "/api/v1/log/detail-log",
        params={"limit": 1001, "offset": 0, "backupActivityUid": log_uid},
        headers=_tunnel_headers(log_namespace),
    )
    log_entries = _parse_log_entries(raw_logs.get("detailLogs") or [])

    return {
        "data_change_bytes": data_change,
        "data_deduped_bytes": data_deduped,
        "backup_scope": backup_scope,
        "log_entries": log_entries,
    }


class _BaseActivityCollection(Generic[ActivityT]):
    """Shared list / scan / latest-by-name logic for activity collections.

    Subclasses set the class-level hooks below and implement _parse(), get(), and cancel().
    """

    _list_endpoint: ClassVar[str]
    _status_param: ClassVar[str]

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    def _parse(self, raw: dict[str, Any]) -> ActivityT:
        """Convert one raw API activity object to its SDK model. Implemented by each subclass."""
        raise NotImplementedError  # pragma: no cover

    async def get(self, activity_id: str) -> ActivityT:
        """Fetch full activity details by activity_id. Implemented by each subclass."""
        raise NotImplementedError  # pragma: no cover

    async def _list_impl(
        self,
        *,
        status_api: list[str],
        extra_params: list[tuple[str, str | int]] | None = None,
        since: datetime | None,
        until: datetime | None,
        keyword: str | None,
        history: bool,
        limit: int,
        offset: int,
    ) -> ListResult[ActivityT]:
        """Shared body for list(): build the query params and parse the response."""
        params: list[tuple[str, str | int]] = [
            ("listMethod", "HISTORY" if history else "RECENT"),
            ("offset", offset),
            ("limit", limit),
            ("orderBy", "ORDER_BY_START_TIME"),
            ("orderDirection", "ORDER_DIRECTION_DESC"),
        ]
        if extra_params:
            params.extend(extra_params)
        params.extend((self._status_param, api_status) for api_status in status_api)
        if since:
            params.append(("rangeStartTime", str(int(since.timestamp()))))
        if until:
            params.append(("rangeEndTime", str(int(until.timestamp()))))
        if keyword:
            params.append(("keyword", keyword))

        raw = await self._session.get(self._list_endpoint, params=params)
        return ListResult(
            [self._parse(item["activity"]) for item in raw.get("activities") or []], raw.get("total")
        )

    async def _find_by_id(self, activity_id: str) -> ActivityT | None:
        """Page through recent then historical activities to locate one by activity_id."""
        list_method: str
        for list_method in ("RECENT", "HISTORY"):

            async def fetch(
                offset: int, limit: int, method: str = list_method
            ) -> tuple[list[dict[str, Any]], int | None]:
                params: list[tuple[str, str | int]] = [
                    ("listMethod", method),
                    ("offset", offset),
                    ("limit", limit),
                    ("orderBy", "ORDER_BY_START_TIME"),
                    ("orderDirection", "ORDER_DIRECTION_DESC"),
                ]
                raw_page = await self._session.get(self._list_endpoint, params=params)
                return raw_page.get("activities") or [], raw_page.get("total")

            async for item in _paginate(fetch):
                act = self._parse(item["activity"])
                if act.activity_id == activity_id:
                    return act
        return None


class BackupActivityCollection(_BaseActivityCollection[BackupActivity]):
    """Interface for querying backup activity records.

    Accessed via ActivityCollection.backup.
    """

    _list_endpoint = "/api/v2/activity/backup/activities"
    _status_param = "backupStatus"

    def _parse(self, raw: dict[str, Any]) -> BackupActivity:
        return _parse_backup_activity(raw)

    async def list(
        self,
        status: list[BackupActivityStatus] | None = None,
        machine_types: list[MachineWorkloadType] | None = None,
        m365_types: list[M365WorkloadType] | None = None,
        namespace: list[str] | None = None,
        workload: Workload | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        keyword: str | None = None,
        history: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> ListResult[BackupActivity]:
        """List backup activity records.

        Args:
            status: Backup activity status filter (OR logic).
            machine_types: Machine workload sub-type filter (OR logic).
            m365_types: M365 service type filter (OR logic).
            namespace: Backup server namespace filter (OR logic). Restricts results
                to activities on the given backup server(s). Namespace values can be
                obtained from ``apm.backup_servers.list()``.
            workload: Restrict results to a single workload's activities, matched by
                ``(workload.workload_id, workload.namespace)``. Composes with
                ``namespace`` above (workload-level vs. backup-server-level scoping).
            since: Only include activities started at or after this time
                (inclusive).
            until: Only include activities started at or before this time
                (inclusive).
            keyword: Free-text search across workload name / plan name.
            history: False (default) = ongoing activities; True = completed
                activity history.
            limit: Maximum number of records to return (default 100).
            offset: Pagination start offset (default 0).

        Returns:
            (list of BackupActivity, total) for the selected mode.
        """
        status_api = [api for s in (status or []) for api in _BACKUP_STATUS_TO_API[s]]
        extra_params: list[tuple[str, str | int]] = [("namespace", ns) for ns in namespace or []]
        extra_params.extend(
            ("categoryService", _MACHINE_TYPE_TO_CATEGORY_SERVICE[mt]) for mt in machine_types or []
        )
        extra_params.extend(("saasServiceType", _M365_TYPE_TO_SAAS_SERVICE[st]) for st in m365_types or [])
        if workload is not None:
            extra_params.append(("workload.uid", workload.workload_id))
            extra_params.append(("workload.namespace", workload.namespace))
        return await self._list_impl(
            status_api=status_api,
            extra_params=extra_params or None,
            since=since,
            until=until,
            keyword=keyword,
            history=history,
            limit=limit,
            offset=offset,
        )

    async def get(self, activity_id: str) -> BackupActivity:
        """Fetch the details (including logs) of a backup activity by activity_id.

        Args:
            activity_id: Unique activity identifier.

        Raises:
            ResourceNotFoundError: The specified ID does not exist.
        """
        target = await self._find_by_id(activity_id)
        if target is None:
            raise ResourceNotFoundError(
                f"Activity '{activity_id}' not found.",
                resource_type="Activity",
                resource_id=activity_id,
            )

        detail = await _fetch_backup_activity_detail(
            self._session,
            execution_id=target.execution_id,
            workload_id=target.workload_id,
            namespace=target.namespace,
        )
        extras = await _fetch_backup_detail_extras(
            self._session, detail, log_uid=activity_id, log_namespace=target.namespace,
        )
        return dataclasses.replace(target, **extras)

    async def get_latest_by_workload_name(self, name: str) -> BackupActivity:
        """Fetch the latest backup activity details (including logs) for a given workload name.

        Searches ongoing activities first, then history; exact-matches workload_name and
        calls get() for full details.

        Args:
            name: Workload display name (exact match, case-insensitive).

        Raises:
            ResourceNotFoundError: No activity found with a matching workload_name.
        """
        return await _get_latest_by_name(
            lambda history: self.list(keyword=name, limit=50, history=history),
            self.get,
            name,
            "backup",
        )

    async def get_by_version(self, version: WorkloadVersion) -> BackupActivity:
        """Fetch backup activity details (including logs) for a specific backup version.

        Args:
            version: WorkloadVersion object (obtained from workloads.list_versions() or
                     workloads.get_version()).
        """
        detail = await _fetch_backup_activity_detail(
            self._session,
            execution_id=version.execution_id,
            workload_id=version.workload_id,
            namespace=version.namespace,
        )
        base = _parse_backup_activity(detail)
        extras = await _fetch_backup_detail_extras(
            self._session, detail, log_uid=detail.get("uid") or "", log_namespace=version.namespace,
        )
        return dataclasses.replace(base, **extras)

    async def cancel(self, activity: BackupActivity) -> None:
        """Cancel a running backup activity.

        Args:
            activity: The backup activity to cancel (obtained from list()).

        Raises:
            PermissionDeniedError: Insufficient permission to cancel backups.
            APIError: APM rejected the cancel request.
        """
        pair = {"namespace": activity.namespace, "uid": activity.activity_id}
        if activity.category == WorkloadCategory.M365:
            body = {"deviceNsUidPairs": [], "m365NsUidPairs": [pair], "gwNsUidPairs": []}
        else:
            body = {"deviceNsUidPairs": [pair], "m365NsUidPairs": [], "gwNsUidPairs": []}
        await self._session.post(
            "/api/v1/activity/cancel/backup/activities",
            json=body,
        )


# errorCode confirmed live on /api/v2/activity/restore/activities when the workload.uid/
# workload.namespace filter doesn't match any workload (errorString.key: "database_query_failed").
_RESTORE_WORKLOAD_NOT_FOUND_CODE = 1002


class RestoreActivityCollection(_BaseActivityCollection[RestoreActivity]):
    """Interface for querying restore activity records.

    Accessed via ActivityCollection.restore.
    """

    _list_endpoint = "/api/v2/activity/restore/activities"
    _status_param = "restoreStatus"

    def _parse(self, raw: dict[str, Any]) -> RestoreActivity:
        return _parse_restore_activity(raw)

    async def list(
        self,
        status: list[RestoreActivityStatus] | None = None,
        workload: Workload | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        keyword: str | None = None,
        history: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> ListResult[RestoreActivity]:
        """List restore activity records.

        Args:
            status: Restore activity status filter (OR logic).
            workload: Restrict results to a single workload's activities, matched by
                ``(workload.workload_id, workload.namespace)``. Returns ``([], 0)``
                if no workload matching this reference exists on the server.
            since: Only include activities started at or after this time
                (inclusive).
            until: Only include activities started at or before this time
                (inclusive).
            keyword: Free-text search across workload name / plan name.
            history: False (default) = ongoing activities; True = completed
                activity history.
            limit: Maximum number of records to return (default 100).
            offset: Pagination start offset (default 0).

        Returns:
            (list of RestoreActivity, total) for the selected mode.
        """
        status_api = [api for s in (status or []) for api in _RESTORE_STATUS_TO_API.get(s, [])]
        extra_params: list[tuple[str, str | int]] | None = None
        if workload is not None:
            extra_params = [
                ("workload.uid", workload.workload_id),
                ("workload.namespace", workload.namespace),
            ]
        try:
            return await self._list_impl(
                status_api=status_api,
                extra_params=extra_params,
                since=since,
                until=until,
                keyword=keyword,
                history=history,
                limit=limit,
                offset=offset,
            )
        except ResourceNotFoundError as exc:
            if workload is None or not _has_detail_code(exc.response_body, _RESTORE_WORKLOAD_NOT_FOUND_CODE):
                raise
            return ListResult([], 0)

    async def get(self, activity_id: str) -> RestoreActivity:
        """Fetch the details (including logs) of a restore activity by activity_id.

        Args:
            activity_id: Unique activity identifier.

        Raises:
            ResourceNotFoundError: The specified ID does not exist.
        """
        target = await self._find_by_id(activity_id)
        if target is None:
            raise ResourceNotFoundError(
                f"Activity '{activity_id}' not found.",
                resource_type="Activity",
                resource_id=activity_id,
            )

        raw_logs = await self._session.get(
            "/api/v1/log/detail-log",
            params={"limit": 1001, "offset": 0, "restoreActivityUid": activity_id},
            headers=_tunnel_headers(target.namespace),
        )
        log_entries = _parse_log_entries(raw_logs.get("detailLogs") or [])
        return dataclasses.replace(target, log_entries=log_entries)

    async def get_latest_by_workload_name(self, name: str) -> RestoreActivity:
        """Fetch the latest restore activity details (including logs) for a given workload name.

        Searches ongoing activities first, then history; exact-matches workload_name and
        calls get() for full details.

        Args:
            name: Workload display name (exact match, case-insensitive).

        Raises:
            ResourceNotFoundError: No activity found with a matching workload_name.
        """
        return await _get_latest_by_name(
            lambda history: self.list(keyword=name, limit=50, history=history),
            self.get,
            name,
            "restore",
        )

    async def cancel(self, activity: RestoreActivity) -> None:
        """Cancel a running restore activity.

        Args:
            activity: The restore activity to cancel (obtained from list()).

        Raises:
            PermissionDeniedError: Insufficient permission to cancel restores.
            APIError: APM rejected the cancel request.
        """
        cancel_type = _SUBTYPE_TO_CANCEL_TYPE.get(activity.workload_type)
        if cancel_type is None:
            raise InvalidOperationError(
                f"Cannot cancel restore activity with unknown workload type: {activity.workload_type!r}",
                resource_type="RestoreActivity",
                resource_id=activity.activity_id,
            )
        await self._session.post(
            "/api/v1/activity/cancel/restore/activities",
            json={
                "activities": [
                    {
                        "workload": {
                            "uid": activity.workload_id,
                            "namespace": activity.workload_namespace,
                        },
                        "executionId": activity.execution_id,
                        "namespace": activity.namespace,
                        "workloadType": cancel_type,
                    }
                ]
            },
        )


class ActivityCollection:
    """Namespace interface for backup and restore activity records.

    Accessed via APMClient.activities.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._backup  = BackupActivityCollection(session)
        self._restore = RestoreActivityCollection(session)

    @property
    def backup(self) -> BackupActivityCollection:
        """Access the BackupActivityCollection."""
        return self._backup

    @property
    def restore(self) -> RestoreActivityCollection:
        """Access the RestoreActivityCollection."""
        return self._restore

