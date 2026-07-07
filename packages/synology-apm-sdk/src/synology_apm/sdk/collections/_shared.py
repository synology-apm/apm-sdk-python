"""Shared collection helpers for Machine, M365, and tiering/copy-status parsing.

This module holds parsing logic common to multiple collection modules.
To avoid import cycles it may import only from models / enums / exceptions and _http —
never from another collection module.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar

from .._http import WebAPISession, _get_all_detail_codes, _has_detail_code
from ..enums import (
    _VERIFY_STATUS_MAP,
    _VERSION_COPY_STATUS_MAP,
    CopyReason,
    MachineWorkloadType,
    RemoteStorageType,
    VerifyStatus,
    VersionCopyStatus,
    VersionStatus,
    WorkloadCategory,
)
from ..exceptions import (
    APIError,
    InvalidOperationError,
    PlanInUseError,
    PlanNameConflictError,
    ResourceNotFoundError,
)
from ..models.location import LocationInfo
from ..models.protection_plan import ProtectionPlan
from ..models.retirement_plan import RetirementPlan
from ..models.tiering_plan import TieringStatus
from ..models.version import VersionLocation, WorkloadVersion
from ..models.workload import Workload

# Shared mapping: RemoteStorageType → API destinationType string for tiering and backup copy.
# Used by both protection_plans.py and tiering_plans.py.
_STORAGE_TYPE_TO_DEST_TYPE: dict[RemoteStorageType, str] = {
    RemoteStorageType.ACTIVE_PROTECT_VAULT: "ACTIVE_BACKUP_ENTERPRISE_VAULT",
    RemoteStorageType.C2_OBJECT_STORAGE:    "C2_OBJECT_STORAGE",
    RemoteStorageType.AMAZON_S3:            "AWS_S3",
    RemoteStorageType.AMAZON_S3_CHINA:      "AWS_S3",
    RemoteStorageType.WASABI:               "WASABI_S3",
    RemoteStorageType.AZURE_BLOB:           "AZURE_BLOB",
    RemoteStorageType.AZURE_BLOB_CHINA:     "AZURE_BLOB",
    RemoteStorageType.S3_COMPATIBLE:        "COMPATIBLE_S3",
}

_VERSION_STATUS_MAP: dict[str, VersionStatus] = {
    "NONE":         VersionStatus.NO_BACKUPS,
    "BACKING_UP":   VersionStatus.BACKING_UP,
    "COMPLETED":    VersionStatus.SUCCESS,
    "FAILED":       VersionStatus.FAILED,
    "PARTIAL":      VersionStatus.PARTIAL,
    "CANCELED":     VersionStatus.CANCELED,
    "PAUSED":       VersionStatus.PAUSED,
    "DELETING":     VersionStatus.DELETING,
    "DELETE_FAILED": VersionStatus.DELETE_FAILED,
}

# API workloadType string → MachineWorkloadType (shared by machine and protection_plans parsers).
# The API uses the uppercase enum member name ("PC", "PS", "VM", "FS") as the workloadType value,
# which equals MachineWorkloadType.<member>.name; .value holds the lowercase SDK/JSON string.
_MACHINE_WORKLOAD_TYPE_MAP: dict[str, MachineWorkloadType] = {
    "PC": MachineWorkloadType.PC,
    "PS": MachineWorkloadType.PS,
    "VM": MachineWorkloadType.VM,
    "FS": MachineWorkloadType.FS,
}

_VERIFY_SUPPORTED_TYPES: frozenset[MachineWorkloadType] = frozenset({
    MachineWorkloadType.PS,
    MachineWorkloadType.VM,
})


_T = TypeVar("_T")


# ── Pagination helper ─────────────────────────────────────────────────────────


async def _paginate(
    fetch_page: Callable[[int, int], Awaitable[tuple[list[_T], int | None]]],
    page_size: int = 100,
) -> AsyncIterator[_T]:
    """Yield items across pages of a paginated endpoint.

    fetch_page(offset, limit) returns (items, total). Iteration stops once offset
    reaches total; when total is None (endpoint does not report one), it stops on
    the first short page instead.
    """
    offset = 0
    while True:
        items, total = await fetch_page(offset, page_size)
        for item in items:
            yield item
        offset += page_size
        if total is not None:
            if offset >= total:
                break
        elif len(items) < page_size:
            break


# ── Remote storage destination lookup (shared by tiering and protection plans) ──


async def _fetch_remote_storage_location(
    session: WebAPISession,
    dest_id: str,
) -> LocationInfo | None:
    """Look up the LocationInfo for a single remote storage destination. Returns None silently on failure."""
    try:
        raw = await session.get(f"/api/v1/external_storage/{dest_id}")
        name = raw.get("displayName", "")
        if not name:
            return None
        return LocationInfo(
            is_remote_storage=True,
            identifier=raw.get("id", dest_id),
            name=name,
            endpoint=raw.get("endpoint", ""),
            vault=raw.get("vaultName") or None,
        )
    except Exception:
        return None


async def _build_remote_location_cache(
    session: WebAPISession,
    dest_ids: list[str],
) -> dict[str, LocationInfo]:
    """Resolve remote storage destinations concurrently and return {dest_id: LocationInfo}.

    dest_ids should already be deduplicated; unresolvable destinations are omitted.
    """
    if not dest_ids:
        return {}
    results = await asyncio.gather(
        *[_fetch_remote_storage_location(session, d) for d in dest_ids]
    )
    return {k: v for k, v in zip(dest_ids, results) if v is not None}


# ── Plan error helpers ────────────────────────────────────────────────────────


def _raise_if_name_conflict(exc: APIError, name: str, resource_type: str) -> None:
    if _has_detail_code(exc.response_body, 4013):
        raise PlanNameConflictError(
            f"A plan named {name!r} already exists.",
            resource_type=resource_type,
            resource_id=name,
            error_code=4013,
            response_body=exc.response_body,
        ) from exc


@contextmanager
def _not_found_as(
    resource_type: str,
    resource_id: str,
    *,
    message: str | None = None,
    detail_code: int | None = None,
) -> Iterator[None]:
    """Attach the caller's resource identity to a not-found error from the wrapped lookup.

    Re-raises a ResourceNotFoundError escaping the block (the session's generic
    HTTP-404 mapping, or a bare one raised by the caller for an empty-body
    response) enriched with resource_type/resource_id, preserving error_code and
    response_body. When detail_code is given, an APIError carrying that detail
    code is converted to the same enriched ResourceNotFoundError.

    Wrap only the primary lookup call (plus its response guard) — not nested
    lookups such as location-cache building, whose own not-found errors would
    otherwise be mislabeled with this resource's identity.
    """
    try:
        yield
    except ResourceNotFoundError as exc:
        raise ResourceNotFoundError(
            message or f"{resource_type} '{resource_id}' not found.",
            resource_type=resource_type,
            resource_id=resource_id,
            error_code=exc.error_code,
            response_body=exc.response_body,
        ) from exc
    except APIError as exc:
        if detail_code is None or not _has_detail_code(exc.response_body, detail_code):
            raise
        raise ResourceNotFoundError(
            message or f"{resource_type} '{resource_id}' not found.",
            resource_type=resource_type,
            resource_id=resource_id,
            error_code=exc.error_code,
            response_body=exc.response_body,
        ) from exc


async def _create_plan_and_fetch(
    session: WebAPISession,
    endpoint: str,
    body: dict[str, Any],
    name: str,
    resource_type: str,
    fetch: Callable[[str], Awaitable[_T]],
) -> _T:
    """POST a plan-creation body, map name conflicts, and fetch the created plan by ID."""
    try:
        resp = await session.post(endpoint, json=body)
    except APIError as exc:
        _raise_if_name_conflict(exc, name, resource_type)
        raise
    plan_id: str | None = resp.get("id")
    if not plan_id:
        raise APIError("Plan created but response contained no plan ID", error_code=0, response_body=resp)
    return await fetch(plan_id)


async def _update_plan_and_fetch(
    session: WebAPISession,
    endpoint: str,
    body: dict[str, Any],
    name: str,
    resource_type: str,
    fetch: Callable[[], Awaitable[_T]],
) -> _T:
    """PUT a plan-update body, map name conflicts, and fetch the updated plan."""
    try:
        await session.put(endpoint, json=body)
    except APIError as exc:
        _raise_if_name_conflict(exc, name, resource_type)
        raise
    return await fetch()


async def _delete_plan_checked(
    session: WebAPISession,
    endpoint: str,
    plan_id: str,
    resource_type: str,
    in_use_flags: dict[int, str],
    message: str,
) -> None:
    """DELETE a plan; map in-use detail codes to PlanInUseError flags, re-raise other errors.

    in_use_flags maps an API detail errorCode to the PlanInUseError flag it sets
    (e.g. ``{4019: "has_workloads", 4017: "has_server_template"}``). Any mapped code
    in the error response triggers PlanInUseError; each flag reflects whether its
    own code was present.
    """
    try:
        await session.delete(endpoint)
    except APIError as exc:
        codes = _get_all_detail_codes(exc.response_body)
        if codes & set(in_use_flags):
            raise PlanInUseError(
                message,
                resource_type=resource_type,
                resource_id=plan_id,
                error_code=exc.error_code,
                response_body=exc.response_body,
                **{flag: (code in codes) for code, flag in in_use_flags.items()},
            ) from exc
        raise


def _parse_bytes_field(raw: str | int | None) -> int | None:
    """Parse a byte-count field where "0" and empty string both mean "no value"."""
    return int(raw) if raw and raw != "0" and raw != 0 else None


def _parse_count_field(raw: str | int | None) -> int:
    """Parse a count field where empty string / None mean 0."""
    return int(raw) if raw else 0


# ── Timestamp parsing helpers ──────────────────────────────────────────────


def _parse_ts_optional(raw: str | int | None) -> datetime | None:
    """Parse a Unix-second timestamp into an aware UTC datetime.

    Returns None for the API's "no value" markers: None, empty string, "0", or 0.
    """
    if not raw or raw == "0":
        return None
    return datetime.fromtimestamp(int(raw), tz=UTC)


def _parse_ts_or_now(raw: str | int | None) -> datetime:
    """Like _parse_ts_optional, but falls back to the current UTC time when no value is present."""
    return _parse_ts_optional(raw) or datetime.now(tz=UTC)


def _parse_int_or_none(raw: str | int | None, *, none_value: int = -1) -> int | None:
    """Parse an integer-valued API field where a sentinel marks the absence of data.

    Returns None when raw is None or equals the sentinel (default -1); otherwise int(raw).
    """
    if raw is None:
        return None
    value = int(raw)
    return None if value == none_value else value


def _tunnel_headers(namespace: str) -> dict[str, str]:
    """Build the request header that routes a call to the backup server owning `namespace`."""
    return {"x-syno-tunnel-route": namespace}


# ── _VersionMixin ─────────────────────────────────────────────────────────


class _VersionMixin:
    """Mixin providing shared version access methods for workload collections."""

    _session: WebAPISession

    def _version_wl_type(self, workload: Workload) -> MachineWorkloadType | None:
        """Hook: workload sub-type passed to the version parser for verify-status resolution.

        Returns None by default (no verify status); MachineWorkloadCollection overrides it.
        """
        return None

    async def list_versions(
        self,
        workload: Workload,
        limit: int = 20,
        offset: int = 0,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[list[WorkloadVersion], int]:
        """List backup version history for a Workload (descending order, newest first).

        Args:
            workload: Workload object (obtained via get()).
            limit:    Maximum versions to return.
            offset:   Pagination start offset (default 0).
            since:    Return only versions created after this time.
            until:    Return only versions created before this time.

        Returns:
            Tuple of (versions, total) where total is the count of all
            matching versions (before limit/offset are applied).
        """
        params: list[tuple[str, str | int]] = [
            ("limit", limit),
            ("offset", offset),
            ("orderBy", "ORDER_BY_START_TIME"),
            ("orderDirection", "ORDER_DIRECTION_DESC"),
            ("status", "COMPLETED"),
            ("status", "PARTIAL"),
            ("status", "FAILED"),
            ("status", "CANCELED"),
        ]
        if since:
            params.append(("createStartTimestamp", str(int(since.timestamp()))))
        if until:
            params.append(("createEndTimestamp", str(int(until.timestamp()))))
        raw = await self._session.get(
            f"/api/v1/workload/{workload.namespace}/{workload.workload_id}/version",
            params=params,
        )
        wl_type = self._version_wl_type(workload)
        return (
            [_parse_version(v, workload.workload_id, wl_type) for v in raw.get("versions", [])],
            raw.get("total", 0),
        )

    async def get_latest_version(self, workload: Workload) -> WorkloadVersion:
        """Get the latest backup version for a Workload (list DESC, first result).

        Args:
            workload: Workload object (obtained via get()).

        Raises:
            ResourceNotFoundError: No backup versions exist yet.
        """
        versions, _ = await self.list_versions(workload, limit=1)
        if not versions:
            raise ResourceNotFoundError(
                f"No backup version found for workload '{workload.workload_id}'.",
                resource_type="WorkloadVersion",
                resource_id=workload.workload_id,
            )
        return versions[0]

    async def get_version(self, workload: Workload, version_id: str) -> WorkloadVersion:
        """Search for a backup version by version_id and return it when found.

        Pages through results (50 per page); complements get_latest_version().
        The returned WorkloadVersion contains full location data suitable for
        lock_version() / unlock_version().

        Args:
            workload:   Workload object (obtained via get()).
            version_id: Target version ID.

        Raises:
            ResourceNotFoundError: The specified version_id was not found.
        """
        async def fetch(offset: int, limit: int) -> tuple[list[WorkloadVersion], int | None]:
            versions, _ = await self.list_versions(workload, limit=limit, offset=offset)
            return versions, None

        async for v in _paginate(fetch, page_size=50):
            if v.version_id == version_id:
                return v
        raise ResourceNotFoundError(
            f"Version '{version_id}' not found for workload '{workload.workload_id}'.",
            resource_type="WorkloadVersion",
            resource_id=version_id,
        )

    async def lock_version(self, version: WorkloadVersion) -> None:
        """Lock a backup version to prevent deletion by retention rules.

        Args:
            version: WorkloadVersion with location data, as returned by
                list_versions(), get_latest_version(), or get_version().

        Raises:
            APIError: The version has no location data, or APM rejected the lock operation.
        """
        await _post_version_lock(self._session, version, locked=True)

    async def unlock_version(self, version: WorkloadVersion) -> None:
        """Unlock a backup version, allowing retention rules to delete it.

        Args:
            version: WorkloadVersion with location data, as returned by
                list_versions(), get_latest_version(), or get_version().

        Raises:
            APIError: The version has no location data, or APM rejected the unlock operation.
        """
        await _post_version_lock(self._session, version, locked=False)


_COPY_REASON_MAP: dict[str, CopyReason] = {
    "DESTINATION_DISCONNECTED": CopyReason.DESTINATION_DISCONNECTED,
    "SOURCE_INCOMPATIBLE":      CopyReason.VERSION_INCOMPATIBLE,
    "DESTINATION_INCOMPATIBLE": CopyReason.VERSION_INCOMPATIBLE,
    "UNDER_MAINTENANCE":        CopyReason.DESTINATION_UPDATING,
    "AUTHENTICATION_FAIL":      CopyReason.AUTH_FAILED,
    "OUT_OF_STORAGE":           CopyReason.STORAGE_FULL,
    "OUT_OF_LICENSE_QUOTA":     CopyReason.QUOTA_EXCEEDED,
    "INFRASTRUCTURE_ERROR":     CopyReason.INFRASTRUCTURE_ERROR,
    "VAULT_NOT_MOUNTED":        CopyReason.VAULT_SETUP_ISSUE,
    "DESTINATION_DATA_CORRUPTED": CopyReason.DATA_CORRUPTED,
    "MISSING_LINK_KEY":         CopyReason.DESTINATION_MISSING,
    "FS_READONLY":              CopyReason.VOLUME_READONLY,
    "SSL_VERIFY_FAILED":        CopyReason.CERT_AUTH_FAILED,
    "DESTINATION_NOT_EXIST":    CopyReason.NO_DESTINATION,
}

_COPY_REASON_SKIPPED_MAP: dict[str, CopyReason] = {
    "REASON_SKIPPED_FOR_DB_VERSION_INVALID":            CopyReason.SKIPPED_DB_OUTDATED,
    "REASON_SKIPPED_FOR_NAS_ENCRYPTED_SHARED_FOLDER":   CopyReason.SKIPPED_NAS_ENCRYPTED,
    "REASON_SKIPPED_FOR_NAS_TO_EXTERNAL_STORAGE":       CopyReason.SKIPPED_NAS_TO_EXTERNAL,
    "REASON_SKIPPED_FOR_SOURCE_EQUAL_TO_DESTINATION":   CopyReason.SKIPPED_SOURCE_EQ_DEST,
}


def _resolve_copy_reason(status: str, reason: str | None = None) -> CopyReason | None:
    """Map an inner API (copyStatus, copyStatusReason) pair to a CopyReason.

    Used by version and plan parsers; tiering callers may omit reason.
    Returns None for non-error states (NONE, DOING, COMPLETED, NOT_ENABLED, NO_VERSIONS_TO_COPY).
    """
    if status == "SKIPPED_WORKLOAD":
        return _COPY_REASON_SKIPPED_MAP.get(reason or "", None)
    return _COPY_REASON_MAP.get(status)


_COPY_ERROR_STATUS_MAP: dict[str, VersionCopyStatus] = {
    # RETRY-class: transient / recoverable
    "DESTINATION_DISCONNECTED": VersionCopyStatus.RETRY,
    "UNDER_MAINTENANCE":        VersionCopyStatus.RETRY,
    "AUTHENTICATION_FAIL":      VersionCopyStatus.RETRY,
    "OUT_OF_STORAGE":           VersionCopyStatus.RETRY,
    "OUT_OF_LICENSE_QUOTA":     VersionCopyStatus.RETRY,
    "SOURCE_INCOMPATIBLE":      VersionCopyStatus.RETRY,
    "DESTINATION_INCOMPATIBLE": VersionCopyStatus.RETRY,
    "SSL_VERIFY_FAILED":        VersionCopyStatus.RETRY,
    # FAILED-class: persistent / requires action
    "INFRASTRUCTURE_ERROR":       VersionCopyStatus.FAILED,
    "VAULT_NOT_MOUNTED":          VersionCopyStatus.FAILED,
    "DESTINATION_DATA_CORRUPTED": VersionCopyStatus.FAILED,
    "DESTINATION_NOT_EXIST":      VersionCopyStatus.FAILED,
    "MISSING_LINK_KEY":           VersionCopyStatus.FAILED,
    "FS_READONLY":                VersionCopyStatus.FAILED,
}


_PENDING_RELEVANT_STATUSES = frozenset({
    VersionCopyStatus.WAITING,
    VersionCopyStatus.IN_PROGRESS,
    VersionCopyStatus.RETRY,
    VersionCopyStatus.FAILED,
})


def _parse_copy_status_core(
    raw_status: str, pending: int, remaining: int | None, raw_reason: str | None,
) -> tuple[VersionCopyStatus, CopyReason | None, int, int | None] | None:
    """Shared status/reason resolution for tiering and plan backup-copy status strings.

    Returns (status, reason, pending_version_count, remaining_bytes); pending/remaining are
    zeroed out when not meaningful for the resolved status. Returns None for an
    unrecognized raw_status.
    """
    if raw_status in ("NONE", "COMPLETED"):
        status = VersionCopyStatus.WAITING if pending > 0 else VersionCopyStatus.COMPLETED
        reason = None
    elif raw_status == "NOT_ENABLED":
        status, reason = VersionCopyStatus.NOT_ENABLED, None
    elif raw_status == "DOING":
        status, reason = VersionCopyStatus.IN_PROGRESS, None
    elif raw_status == "NO_VERSIONS_TO_COPY":
        status, reason = VersionCopyStatus.COMPLETED, CopyReason.NO_VERSIONS_TO_COPY
    elif raw_status == "SKIPPED_WORKLOAD":
        status, reason = VersionCopyStatus.SKIPPED, _resolve_copy_reason("SKIPPED_WORKLOAD", raw_reason)
    else:
        outer_status = _COPY_ERROR_STATUS_MAP.get(raw_status)
        if outer_status is None:
            return None
        status, reason = outer_status, _resolve_copy_reason(raw_status, raw_reason)

    if status not in _PENDING_RELEVANT_STATUSES:
        pending, remaining = 0, None
    return status, reason, pending, remaining


def _parse_tiering_status(tiering_info: dict[str, Any]) -> TieringStatus | None:
    """Parse a tieringInfo dict from an API response into TieringStatus.

    Returns None when tieringStatus is absent or empty.
    """
    raw_status = tiering_info.get("tieringStatus", "")
    if not raw_status:
        return None
    pending = _parse_count_field(tiering_info.get("pendingVersionCount", "0"))
    remaining = _parse_bytes_field(tiering_info.get("remainingBytes", ""))
    resolved = _parse_copy_status_core(raw_status, pending, remaining, tiering_info.get("statusReason"))
    if resolved is None:
        return None
    status, reason, pending, remaining = resolved
    return TieringStatus(status=status, reason=reason, pending_version_count=pending, remaining_bytes=remaining)


def _parse_verify_status_core(raw: str | None, *, verify_supported: bool) -> VerifyStatus | None:
    """Map a raw verifyStatus string to VerifyStatus.

    VERIFY_NONE and unknown strings map to None; NOT_ENABLED maps to None when the
    workload/subtype does not support verification.
    """
    if not raw or raw == "VERIFY_NONE":
        return None
    mapped = _VERIFY_STATUS_MAP.get(raw)
    if mapped is None:
        return None
    if mapped == VerifyStatus.NOT_ENABLED and not verify_supported:
        return None
    return mapped


def _parse_verify_status(raw: str | None, wl_type: MachineWorkloadType | None) -> VerifyStatus | None:
    """Map a raw verifyStatus string to VerifyStatus; VERIFY_NONE and non-PS/VM NOT_ENABLED → None."""
    return _parse_verify_status_core(raw, verify_supported=wl_type in _VERIFY_SUPPORTED_TYPES)


def _build_location_info(server_info: dict[str, Any]) -> LocationInfo | None:
    """Build LocationInfo from a backupServerInfo or backupCopyServerInfo dict.

    Returns None when hostName is empty (no server configured).
    namespace falls back to uid when absent/empty or equal to "shared".
    """
    name = server_info.get("hostName", "")
    if not name:
        return None
    namespace = server_info.get("namespace", "")
    if not namespace or namespace == "shared":
        namespace = server_info.get("uid", "")
    return LocationInfo(
        is_remote_storage=server_info.get("destinationType", "APPLIANCE") != "APPLIANCE",
        identifier=namespace,
        name=name,
        endpoint=server_info.get("addr", ""),
        vault=server_info.get("vaultName") or None,
    )


# ── Shared version lock helper ─────────────────────────────────────────────


async def _post_version_lock(
    session: WebAPISession, version: WorkloadVersion, locked: bool
) -> None:
    """POST /api/v1/version/batch/lock or .../unlock.

    groupLeader is the top-level namespace + version_id of the WorkloadVersion.
    nsUidPairs is built from each VersionLocation's namespace + id directly.
    """
    leader = {"namespace": version.namespace, "uid": version.version_id}
    pairs: list[dict[str, str]] = [
        {"namespace": loc.namespace, "uid": loc.location_id} for loc in version.locations
    ]
    if not pairs:
        raise APIError(
            f"Version '{version.version_id}' has no location data; cannot lock/unlock.",
            response_body=None,
        )

    endpoint = "/api/v1/version/batch/lock" if locked else "/api/v1/version/batch/unlock"
    resp = await session.post(
        endpoint,
        json={"groups": [{"groupLeader": leader, "nsUidPairs": pairs}]},
    )
    errors = resp.get("errors", []) if resp else []
    if errors:
        action = "lock" if locked else "unlock"
        raise APIError(
            f"Failed to {action} version '{version.version_id}': {errors}",
            response_body=resp,
        )


# ── Version response parsers ───────────────────────────────────────────────


def _parse_version(
    raw: dict[str, Any], workload_id: str, wl_type: MachineWorkloadType | None = None
) -> WorkloadVersion:
    """Convert a single version object from an API response to the SDK model."""
    spec: dict[str, Any] = raw.get("spec", {})
    status: dict[str, Any] = raw.get("status", {})

    portal_version_id: str = spec.get("versionId", "")
    version_id: str = raw.get("id", "")
    created_at = _parse_ts_or_now(status.get("startTime"))

    version_status = _VERSION_STATUS_MAP.get(status.get("status", ""), VersionStatus.NO_BACKUPS)

    transferred_raw = status.get("transferredSize", "0")
    size_bytes = int(transferred_raw) if transferred_raw else 0

    locations = [
        loc
        for raw_loc in raw.get("locations", [])
        for loc in _parse_version_location(raw_loc)
    ]
    verify_status = _parse_verify_status(status.get("verifyStatus"), wl_type) if wl_type is not None else None

    copy_status = _VERSION_COPY_STATUS_MAP.get(raw.get("copyStatus", ""))
    copy_reason: CopyReason | None = None
    if copy_status in (VersionCopyStatus.SKIPPED, VersionCopyStatus.RETRY, VersionCopyStatus.FAILED):
        copy_reason = _resolve_copy_reason(
            status.get("copyStatus", ""),
            status.get("copyStatusReason"),
        )

    return WorkloadVersion(
        version_id=version_id,
        workload_id=workload_id,
        namespace=raw.get("namespace", ""),
        created_at=created_at,
        status=version_status,
        execution_id=spec.get("executionId", ""),
        locked=bool(spec.get("locked", False)),
        changed_size_bytes=size_bytes,
        portal_version_id=portal_version_id,
        snapshot_id=spec.get("snapshotId", ""),
        verify_status=verify_status,
        locations=locations,
        copy_status=copy_status,
        copy_reason=copy_reason,
    )


# ── Retirement filter helpers ──────────────────────────────────────────────


def _machine_protect_status(is_retired: bool) -> str:
    """Return the protectStatus filter value for a device workload query."""
    return "PROTECT_STATUS_ARCHIVED" if is_retired else "PROTECT_STATUS_PROTECTED"


def _m365_plan_type(is_retired: bool) -> str:
    """Return the planType filter value for an M365 workload query."""
    return "ARCHIVE" if is_retired else "BACKUP"


def _build_workload_plan_ref(
    plan_id: str, name: str, *, is_archive: bool, category: WorkloadCategory
) -> ProtectionPlan | RetirementPlan:
    """Build a lightweight Plan reference embedded in a workload response."""
    if is_archive:
        return RetirementPlan(plan_id=plan_id, name=name)
    return ProtectionPlan(plan_id=plan_id, name=name, category=category)


# ── Workload action preconditions (shared by Machine/M365 collections) ─────


def _check_active_for_write(workload: Workload, action: str) -> None:
    """Raise InvalidOperationError if workload.is_retired; used by backup_now()/cancel_backup()."""
    if workload.is_retired:
        raise InvalidOperationError(
            f"Workload '{workload.name}' is retired and {action}.",
            resource_type="Workload",
            resource_id=workload.workload_id,
        )


def _check_not_retired(workload: Workload) -> None:
    """Raise InvalidOperationError if workload.is_retired is already True; used by retire()."""
    if workload.is_retired:
        raise InvalidOperationError(
            f"Workload '{workload.name}' is already retired.",
            resource_type="Workload",
            resource_id=workload.workload_id,
        )


def _check_change_plan_preconditions(workload: Workload, plan: ProtectionPlan | RetirementPlan) -> None:
    """Validate the workload/plan-type/category combination accepted by change_plan()."""
    if isinstance(plan, RetirementPlan):
        if not workload.is_retired:
            raise InvalidOperationError(
                f"Workload '{workload.name}' must be retired before a retirement plan can be assigned.",
                resource_type="Workload",
                resource_id=workload.workload_id,
            )
    else:
        if workload.is_retired:
            raise InvalidOperationError(
                f"Cannot apply a protection plan to retired workload '{workload.name}'.",
                resource_type="Workload",
                resource_id=workload.workload_id,
            )
        if plan.category is not workload.category:
            raise InvalidOperationError(
                f"Plan '{plan.name}' belongs to category '{plan.category.value}', which "
                f"does not match the category '{workload.category.value}' of workload '{workload.name}'.",
                resource_type="Workload",
                resource_id=workload.workload_id,
            )


async def _resolve_namespace_to_server_id(session: WebAPISession, namespace: str) -> str:
    """Resolve a backup server namespace to its internal server ID.

    Pages through all backup servers until a match is found.

    Raises:
        ResourceNotFoundError: No backup server with the given namespace exists.
    """
    async def fetch(offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
        raw = await session.get(
            "/api/v1/infra/backup_server",
            params={"offset": offset, "limit": limit},
        )
        return raw.get("backupServers", []), raw.get("total", 0)

    async for server in _paginate(fetch, page_size=500):
        if server.get("namespace") == namespace:
            return str(server.get("id", ""))
    raise ResourceNotFoundError(
        f"No backup server with namespace {namespace!r}.",
        resource_type="BackupServer",
        resource_id=namespace,
    )


def _parse_version_location(raw: dict[str, Any]) -> list[VersionLocation]:
    """Expand one API locations[] entry into one VersionLocation per versionUid."""
    is_remote = raw.get("locationType", "APPLIANCE") != "APPLIANCE"
    ext = raw.get("externalStorageInfo")
    if ext:
        info = LocationInfo(
            is_remote_storage=is_remote,
            identifier=ext.get("storageUid", ""),
            name=ext.get("displayName", ""),
            endpoint=ext.get("endpoint", ""),
            vault=ext.get("vaultName") or None,
        )
    else:
        server_info = raw.get("backupServerInfo", {})
        info = LocationInfo(
            is_remote_storage=is_remote,
            identifier=raw.get("namespace", ""),
            name=server_info.get("hostName", ""),
            endpoint=server_info.get("address", ""),
            vault=None,
        )
    namespace = raw.get("namespace", "")
    connection_id: str | None = raw.get("connectionId") or None
    return [
        VersionLocation(
            namespace=namespace,
            location_info=info,
            location_id=uid,
            connection_id=connection_id,
        )
        for uid in raw.get("versionUids", [])
    ]
