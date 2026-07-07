"""ActivityCollection — interface for querying backup/restore activity records."""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from datetime import datetime
from typing import Any, ClassVar, Generic, TypeVar

from .._http import WebAPISession
from ..enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    BackupScope,
    HypervisorType,
    LogLevel,
    M365WorkloadType,
    MachineWorkloadType,
    RestoreActivityStatus,
    RestoreType,
    VerifyStatus,
    WorkloadCategory,
)
from ..exceptions import InvalidOperationError, ResourceNotFoundError
from ..models.activity import ActivityLogEntry, BackupActivity, RestoreActivity
from ..models.hypervisor import Hypervisor
from ..models.location import LocationInfo
from ..models.version import WorkloadVersion
from ..models.workload import Workload
from ._shared import (
    _not_found_as,
    _paginate,
    _parse_int_or_none,
    _parse_ts_optional,
    _parse_ts_or_now,
    _parse_verify_status_core,
    _tunnel_headers,
)
from .hypervisors import _HOST_TYPE_MAP

_BACKUP_SCOPE_MAP: dict[str, BackupScope] = {
    "ENTIRE_DEVICE_WITH_EXT_DRIVES": BackupScope.ENTIRE_DEVICE_WITH_EXT_DRIVES,
    "ENTIRE_DEVICE":                 BackupScope.ENTIRE_DEVICE,
    "VOLUME":                        BackupScope.VOLUME,
    "FILE":                          BackupScope.FILE,
}

_BACKUP_STATUS_MAP: dict[str, BackupActivityStatus] = {
    "QUEUING":           BackupActivityStatus.QUEUING,
    "BACKUPING":         BackupActivityStatus.BACKING_UP,
    "CANCELING":         BackupActivityStatus.CANCELING,
    "SUCCESS":           BackupActivityStatus.SUCCESS,
    "FAILED":            BackupActivityStatus.FAILED,
    "ERROR":             BackupActivityStatus.FAILED,
    "UNKNOWN":           BackupActivityStatus.FAILED,
    "WARNING":           BackupActivityStatus.PARTIAL,
    "CANCELED":          BackupActivityStatus.CANCELED,
    "NOT_BACKED_UP_YET": BackupActivityStatus.QUEUING,
}

# SDK BackupActivityStatus → API backupStatus values (one-to-many)
_BACKUP_STATUS_TO_API: dict[BackupActivityStatus, list[str]] = {
    BackupActivityStatus.QUEUING:    ["QUEUING", "NOT_BACKED_UP_YET"],
    BackupActivityStatus.BACKING_UP: ["BACKUPING"],
    BackupActivityStatus.CANCELING:  ["CANCELING"],
    BackupActivityStatus.SUCCESS:    ["SUCCESS"],
    BackupActivityStatus.FAILED:     ["ERROR", "UNKNOWN"],
    BackupActivityStatus.PARTIAL:    ["WARNING"],
    BackupActivityStatus.CANCELED:   ["CANCELED"],
}

_RESTORE_STATUS_MAP: dict[str, RestoreActivityStatus] = {
    "PREPARING":           RestoreActivityStatus.PREPARING,
    "RESTORING":           RestoreActivityStatus.RESTORING,
    "CANCELING":           RestoreActivityStatus.CANCELING,
    "READY_FOR_MIGRATE":   RestoreActivityStatus.READY_FOR_MIGRATE,
    "MIGRATE_VM_MANUALLY": RestoreActivityStatus.MIGRATE_VM_MANUALLY,
    "MIGRATING":           RestoreActivityStatus.MIGRATING,
    "SUCCESS":             RestoreActivityStatus.SUCCESS,
    "FAILED":              RestoreActivityStatus.FAILED,
    "ERROR":               RestoreActivityStatus.FAILED,
    "DEVICE_MISSING":      RestoreActivityStatus.FAILED,
    "MIGRATE_FAILED":      RestoreActivityStatus.FAILED,
    "WARNING":             RestoreActivityStatus.PARTIAL,
    "PARTIAL_SUCCESS":     RestoreActivityStatus.PARTIAL,
    "CANCELED":            RestoreActivityStatus.CANCELED,
}

# SDK RestoreActivityStatus → API restoreStatus values (one-to-many)
_RESTORE_STATUS_TO_API: dict[RestoreActivityStatus, list[str]] = {
    RestoreActivityStatus.PREPARING:           ["PREPARING"],
    RestoreActivityStatus.RESTORING:           ["RESTORING"],
    RestoreActivityStatus.CANCELING:           ["CANCELING"],
    RestoreActivityStatus.READY_FOR_MIGRATE:   ["READY_FOR_MIGRATE"],
    RestoreActivityStatus.MIGRATE_VM_MANUALLY: ["MIGRATE_VM_MANUALLY"],
    RestoreActivityStatus.MIGRATING:           ["MIGRATING"],
    RestoreActivityStatus.SUCCESS:             ["SUCCESS"],
    RestoreActivityStatus.FAILED:              ["FAILED", "DEVICE_MISSING", "MIGRATE_FAILED"],
    RestoreActivityStatus.PARTIAL:             ["WARNING", "PARTIAL_SUCCESS"],
    RestoreActivityStatus.CANCELED:            ["CANCELED"],
}

_RESTORE_TYPE_MAP: dict[str, RestoreType] = {
    "FILE_LEVEL_RESTORE":         RestoreType.FILE_LEVEL,
    "FULL_RESTORE":               RestoreType.FULL,
    "RESTORE_SYSTEM_VOLUME":      RestoreType.SYSTEM_VOLUME,
    "RESTORE_CUSTOMIZED_VOLUME":  RestoreType.CUSTOMIZED_VOLUME,
    "VM_FULL_RESTORE":            RestoreType.VM_FULL,
    "INSTANT_RESTORE_AEM":        RestoreType.INSTANT_AEM,
    "INSTANT_RESTORE_VMWARE":     RestoreType.INSTANT_VMWARE,
    "INSTANT_RESTORE_HYPERV":     RestoreType.INSTANT_HYPERV,
    "ORACLE_DATABASE_RESTORE":    RestoreType.ORACLE_DATABASE,
    "MSSQL_DATABASE_RESTORE":     RestoreType.MSSQL_DATABASE,
    "INSTANT_RESTORE_NUTANIX":    RestoreType.INSTANT_NUTANIX,
    "INSTANT_RESTORE_PROXMOX":    RestoreType.INSTANT_PROXMOX,
}

_MACHINE_TYPE_TO_CATEGORY_SERVICE: dict[MachineWorkloadType, str] = {
    MachineWorkloadType.PC: "MACHINE_PC",
    MachineWorkloadType.PS: "MACHINE_PS",
    MachineWorkloadType.VM: "MACHINE_VM",
    MachineWorkloadType.FS: "MACHINE_FS",
}

_M365_TYPE_TO_SAAS_SERVICE: dict[M365WorkloadType, str] = {
    M365WorkloadType.EXCHANGE:   "M365_USER_EXCHANGE",
    M365WorkloadType.ONEDRIVE:   "M365_USER_DRIVE",
    M365WorkloadType.CHAT:       "M365_USER_CHAT",
    M365WorkloadType.SHAREPOINT: "M365_SITE",
    M365WorkloadType.TEAMS:      "M365_TEAMS",
    M365WorkloadType.GROUP:      "M365_GROUP_EXCHANGE",
}

# API workloadType raw string → ActivityWorkloadType enum
_RAW_TO_SUBTYPE: dict[str, ActivityWorkloadType] = {
    "MACHINE_PC":         ActivityWorkloadType.MACHINE_PC,
    "MACHINE_PS":         ActivityWorkloadType.MACHINE_PS,
    "MACHINE_VM":         ActivityWorkloadType.MACHINE_VM,
    "MACHINE_FS":         ActivityWorkloadType.MACHINE_FS,
    "MACHINE_CLOUDVM":    ActivityWorkloadType.MACHINE_CLOUDVM,
    "APPLICATION_M365":   ActivityWorkloadType.M365,
    "APPLICATION_GW":     ActivityWorkloadType.GWS,
    "APPLICATION_ORACLE": ActivityWorkloadType.ORACLE,
    "APPLICATION_MSSQL":  ActivityWorkloadType.MSSQL,
}

# ActivityWorkloadType → WorkloadCategory
_ACTIVITY_TYPE_TO_CATEGORY: dict[ActivityWorkloadType, WorkloadCategory] = {
    ActivityWorkloadType.MACHINE_PC:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_PS:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_VM:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_FS:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_CLOUDVM: WorkloadCategory.MACHINE,
    ActivityWorkloadType.M365:            WorkloadCategory.M365,
    ActivityWorkloadType.GWS:             WorkloadCategory.GWS,
    ActivityWorkloadType.ORACLE:          WorkloadCategory.MACHINE,
    ActivityWorkloadType.MSSQL:           WorkloadCategory.MACHINE,
    ActivityWorkloadType.UNKNOWN:         WorkloadCategory.MACHINE,
}

# ActivityWorkloadType → API workloadType string for restore cancel requests
_SUBTYPE_TO_CANCEL_TYPE: dict[ActivityWorkloadType, str] = {
    ActivityWorkloadType.MACHINE_PC:      "MACHINE_PC",
    ActivityWorkloadType.MACHINE_PS:      "MACHINE_PS",
    ActivityWorkloadType.MACHINE_VM:      "MACHINE_VM",
    ActivityWorkloadType.MACHINE_FS:      "MACHINE_FS",
    ActivityWorkloadType.MACHINE_CLOUDVM: "MACHINE_CLOUDVM",
    ActivityWorkloadType.M365:            "APPLICATION_M365",
    ActivityWorkloadType.GWS:             "APPLICATION_GW",
    ActivityWorkloadType.ORACLE:          "APPLICATION_ORACLE",
    ActivityWorkloadType.MSSQL:           "APPLICATION_MSSQL",
}

# Subtypes whose activities track per-item processed counts (FS and M365)
_ITEM_BASED_SUBTYPES: frozenset[ActivityWorkloadType] = frozenset({
    ActivityWorkloadType.MACHINE_FS,
    ActivityWorkloadType.M365,
})

# Subtypes that support backup verification (PS and VM only)
_VERIFY_SUPPORTED_SUBTYPES: frozenset[ActivityWorkloadType] = frozenset({
    ActivityWorkloadType.MACHINE_PS,
    ActivityWorkloadType.MACHINE_VM,
})


def _parse_activity_verify_status(raw: str | None, subtype: ActivityWorkloadType) -> VerifyStatus | None:
    """Map a raw verifyStatus string to VerifyStatus; VERIFY_NONE and non-PS/VM NOT_ENABLED → None."""
    return _parse_verify_status_core(raw, verify_supported=subtype in _VERIFY_SUPPORTED_SUBTYPES)


ActivityT = TypeVar("ActivityT", BackupActivity, RestoreActivity)


async def _get_latest_by_name(
    search: Callable[[bool], Awaitable[tuple[list[ActivityT], int]]],
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
    ) -> tuple[list[ActivityT], int]:
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
        for api_status in status_api:
            params.append((self._status_param, api_status))
        if since:
            params.append(("rangeStartTime", str(int(since.timestamp()))))
        if until:
            params.append(("rangeEndTime", str(int(until.timestamp()))))
        if keyword:
            params.append(("keyword", keyword))

        raw = await self._session.get(self._list_endpoint, params=params)
        return [self._parse(item["activity"]) for item in raw.get("activities", [])], raw.get("total", 0)

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
                return raw_page.get("activities", []), None

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
    ) -> tuple[list[BackupActivity], int]:
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
        extra_params: list[tuple[str, str | int]] = []
        for ns in namespace or []:
            extra_params.append(("namespace", ns))
        for mt in machine_types or []:
            extra_params.append(("categoryService", _MACHINE_TYPE_TO_CATEGORY_SERVICE[mt]))
        for st in m365_types or []:
            extra_params.append(("saasServiceType", _M365_TYPE_TO_SAAS_SERVICE[st]))
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

        raw_detail = await self._session.get(
            "/api/v1/activity/backup/activity",
            params={
                "executionId": target.execution_id,
                "workloadUid": target.workload_id,
                "namespace": target.namespace,
            },
        )
        detail = raw_detail.get("activity", {})
        detail_status = detail.get("status", {})
        detail_spec = detail.get("spec", {})

        data_change, data_deduped = _parse_data_sizes(detail_status)
        machine_info = detail_spec.get("machineInfo") or {}
        _scope_raw = machine_info.get("backupScope") or None
        backup_scope: BackupScope | None = _BACKUP_SCOPE_MAP.get(_scope_raw) if _scope_raw else None

        raw_logs = await self._session.get(
            "/api/v1/log/detail-log",
            params={"limit": 1001, "offset": 0, "backupActivityUid": activity_id},
            headers=_tunnel_headers(target.namespace),
        )
        log_entries = _parse_log_entries(raw_logs.get("detailLogs", []))

        return dataclasses.replace(
            target,
            data_change_bytes=data_change,
            data_deduped_bytes=data_deduped,
            backup_scope=backup_scope,
            log_entries=log_entries,
        )

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
        raw_detail = await self._session.get(
            "/api/v1/activity/backup/activity",
            params={
                "executionId": version.execution_id,
                "workloadUid": version.workload_id,
                "namespace": version.namespace,
            },
        )
        detail = raw_detail.get("activity", {})
        detail_status = detail.get("status", {})
        detail_spec = detail.get("spec", {})
        activity_uid = detail.get("uid", "")

        base = _parse_backup_activity(detail)

        data_change, data_deduped = _parse_data_sizes(detail_status)
        machine_info = detail_spec.get("machineInfo") or {}
        _scope_raw = machine_info.get("backupScope") or None
        backup_scope: BackupScope | None = _BACKUP_SCOPE_MAP.get(_scope_raw) if _scope_raw else None

        raw_logs = await self._session.get(
            "/api/v1/log/detail-log",
            params={"limit": 1001, "offset": 0, "backupActivityUid": activity_uid},
            headers=_tunnel_headers(version.namespace),
        )
        log_entries = _parse_log_entries(raw_logs.get("detailLogs", []))

        return dataclasses.replace(
            base,
            data_change_bytes=data_change,
            data_deduped_bytes=data_deduped,
            backup_scope=backup_scope,
            log_entries=log_entries,
        )

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
    ) -> tuple[list[RestoreActivity], int]:
        """List restore activity records.

        Args:
            status: Restore activity status filter (OR logic).
            workload: Restrict results to a single workload's activities, matched by
                ``(workload.workload_id, workload.namespace)``. If no workload matching
                this reference exists on the server, raises ``ResourceNotFoundError``
                (see Raises below) -- unlike ``BackupActivityCollection.list()``, which
                returns ``([], 0)`` in that case.
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

        Raises:
            ResourceNotFoundError: ``workload`` does not match any workload on the
                server.
        """
        status_api = [api for s in (status or []) for api in _RESTORE_STATUS_TO_API.get(s, [])]
        extra_params: list[tuple[str, str | int]] | None = None
        if workload is not None:
            extra_params = [
                ("workload.uid", workload.workload_id),
                ("workload.namespace", workload.namespace),
            ]
        enrich = (
            _not_found_as("Workload", workload.workload_id, message=f"Workload '{workload.name}' not found.")
            if workload is not None
            else nullcontext()
        )
        with enrich:
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
        log_entries = _parse_log_entries(raw_logs.get("detailLogs", []))
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


# ── Shared parser helpers ─────────────────────────────────────────────────

_LOG_LEVEL_MAP: dict[str, LogLevel] = {
    "LEVEL_INFORMATION": LogLevel.INFO,
    "LEVEL_WARNING":     LogLevel.WARNING,
    "LEVEL_ERROR":       LogLevel.ERROR,
}


def _parse_data_sizes(status: dict[str, Any]) -> tuple[int | None, int | None]:
    """Parse changeDataSize / dedupedDataSize. "-1" becomes None; 0 and positive values are kept as-is."""
    return (
        _parse_int_or_none(status.get("changeDataSize")),
        _parse_int_or_none(status.get("dedupedDataSize")),
    )


def _parse_log_entries(raw_logs: list[dict[str, Any]]) -> tuple[ActivityLogEntry, ...]:
    entries = []
    for log in raw_logs:
        dt = _parse_ts_or_now(log.get("timestamp"))
        level = _LOG_LEVEL_MAP.get(log.get("level", ""), LogLevel.INFO)
        entries.append(ActivityLogEntry(timestamp=dt, level=level, message=log.get("description", "")))
    return tuple(entries)


def _parse_restore_from_info(raw: dict[str, Any] | None) -> LocationInfo | None:
    """Build a LocationInfo from spec.restoreFromInfo.

    Returns None when raw is missing or empty. `identifier` is always ""
    -- restoreFromInfo has no namespace/uid equivalent.
    """
    if not raw:
        return None
    return LocationInfo(
        is_remote_storage=raw.get("destinationType", "APPLIANCE") != "APPLIANCE",
        identifier="",
        name=raw.get("hostname", ""),
        endpoint=raw.get("address", ""),
        vault=raw.get("containerName") or None,
    )


def _parse_destination_inventory(machine_info: dict[str, Any] | None) -> Hypervisor | None:
    """Build a Hypervisor from spec.machineInfo.additionalInfo (a JSON-encoded string).

    Only hostname, address, and host_type are populated (from inventory_name,
    inventory_addr, inventory_type via the existing _HOST_TYPE_MAP); the
    remaining Hypervisor fields have no equivalent in additionalInfo and are
    left empty/zero. Returns None when machineInfo/additionalInfo is missing,
    not valid JSON, or inventory_name is empty.
    """
    if not machine_info:
        return None
    raw_info = machine_info.get("additionalInfo")
    if not raw_info:
        return None
    try:
        info = json.loads(raw_info)
    except (TypeError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    name = info.get("inventory_name") or ""
    if not name:
        return None
    return Hypervisor(
        hypervisor_id="",
        hostname=name,
        address=info.get("inventory_addr", ""),
        host_type=_HOST_TYPE_MAP.get(info.get("inventory_type", ""), HypervisorType.UNKNOWN),
        account="",
        description="",
        port=0,
        version="",
    )


def _parse_activity_common(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse the constructor fields shared by backup and restore activities."""
    spec: dict[str, Any] = raw.get("spec", {})
    status_raw: dict[str, Any] = raw.get("status", {})

    subtype = _RAW_TO_SUBTYPE.get(spec.get("workloadType", ""), ActivityWorkloadType.UNKNOWN)
    workload_ref = spec.get("workload", {})

    processed_success_count = processed_warning_count = processed_error_count = None
    if subtype in _ITEM_BASED_SUBTYPES:
        processed_success_count = int(status_raw.get("processedSuccessCount", 0))
        processed_warning_count = int(status_raw.get("processedWarningCount", 0))
        processed_error_count   = int(status_raw.get("processedErrorCount", 0))

    return {
        "activity_id": raw.get("uid", ""),
        "workload_id": workload_ref.get("uid", ""),
        "workload_name": spec.get("workloadName", ""),
        "category": _ACTIVITY_TYPE_TO_CATEGORY[subtype],
        "workload_type": subtype,
        "namespace": raw.get("namespace", ""),
        "plan_name": spec.get("planName", ""),
        "started_at": _parse_ts_or_now(status_raw.get("startTime")),
        "finished_at": _parse_ts_optional(status_raw.get("endTime")),
        "progress": int(status_raw.get("progress", 0)),
        "processed_success_count": processed_success_count,
        "processed_warning_count": processed_warning_count,
        "processed_error_count": processed_error_count,
        "workload_namespace": workload_ref.get("namespace", ""),
    }


def _parse_restore_activity(raw: dict[str, Any]) -> RestoreActivity:
    """Convert a restore activity object from an API response to the SDK RestoreActivity model."""
    spec: dict[str, Any] = raw.get("spec", {})
    status_raw: dict[str, Any] = raw.get("status", {})
    common = _parse_activity_common(raw)

    # Restore API does not return durationTime; compute from start/end if available.
    duration_raw = status_raw.get("durationTime")
    finished_at: datetime | None = common["finished_at"]
    if duration_raw is not None:
        _d = int(duration_raw)
        duration_seconds: int | None = _d if _d > 0 else None
    elif finished_at is not None:
        delta = int(finished_at.timestamp()) - int(common["started_at"].timestamp())
        duration_seconds = delta if delta > 0 else None
    else:
        duration_seconds = None

    restore_status = status_raw.get("restoreStatus", "")

    return RestoreActivity(
        **common,
        execution_id=spec.get("executionId", ""),
        status=_RESTORE_STATUS_MAP.get(restore_status, RestoreActivityStatus.RESTORING),
        duration_seconds=duration_seconds,
        data_transferred_bytes=_parse_int_or_none(status_raw.get("transferredSize")),
        restore_type=(
            _RESTORE_TYPE_MAP.get(spec.get("restoreType", ""), RestoreType.UNKNOWN)
            if spec.get("restoreType") else None
        ),
        restore_destination=spec.get("destination"),
        operator=spec.get("operator"),
        version_timestamp=_parse_ts_optional(spec.get("versionTimestamp")),
        restore_from_info=_parse_restore_from_info(spec.get("restoreFromInfo")),
        destination_path=spec.get("destinationPath") or None,
        destination_inventory=_parse_destination_inventory(spec.get("machineInfo")),
    )


def _parse_backup_activity(raw: dict[str, Any]) -> BackupActivity:
    """Convert an activity object from an API response to the SDK BackupActivity model."""
    status_raw: dict[str, Any] = raw.get("status", {})
    common = _parse_activity_common(raw)

    duration_raw = status_raw.get("durationTime", "0")
    _duration_int = int(duration_raw) if duration_raw else 0
    duration_seconds = _duration_int if _duration_int > 0 else None

    data_change, data_deduped = _parse_data_sizes(status_raw)

    backup_status = status_raw.get("backupStatus", "")

    machine_status_info: dict[str, Any] | None = status_raw.get("machineStatusInfo")
    verify_status: VerifyStatus | None = None
    if machine_status_info is not None:
        verify_status = _parse_activity_verify_status(
            machine_status_info.get("verifyStatus"), common["workload_type"]
        )

    return BackupActivity(
        **common,
        execution_id=status_raw.get("executionId", ""),
        status=_BACKUP_STATUS_MAP.get(backup_status, BackupActivityStatus.BACKING_UP),
        duration_seconds=duration_seconds,
        data_transferred_bytes=_parse_int_or_none(status_raw.get("transferredDataSize")),
        verify_status=verify_status,
        data_change_bytes=data_change,
        data_deduped_bytes=data_deduped,
    )
