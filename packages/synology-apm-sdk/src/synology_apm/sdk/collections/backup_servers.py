"""BackupServerCollection — collection interface for managing cluster backup servers."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..enums import BackupServerRole, BackupServerType, ServerStatus
from ..exceptions import InvalidOperationError, ResourceNotFoundError
from ..models.backup_server import BackupServer
from ..models.tiering_plan import TieringPlan
from ._shared import ListResult, _not_found_as, _paginate, _parse_tiering_status
from .tiering_plans import _get_plans_bulk

_SERVER_STATUS_MAP: dict[str, ServerStatus] = {
    "NORMAL":               ServerStatus.HEALTHY,
    "ATTENTION":            ServerStatus.WARNING,
    "DANGER":               ServerStatus.CRITICAL,
    "DISCONNECTED":         ServerStatus.DISCONNECTED,
    "JOINING_DISCONNECTED": ServerStatus.DISCONNECTED,
    "NOTINITIALIZED":       ServerStatus.DISCONNECTED,
    "INCOMPATIBLE":         ServerStatus.DISCONNECTED,
}

_SYNC_DISCONNECTED = {"DISCONNECTED", "JOINING_DISCONNECTED"}

_ROLE_MAP: dict[str, BackupServerRole] = {
    "LEADER":  BackupServerRole.PRIMARY,
    "REPLICA": BackupServerRole.SECONDARY,
}

_STATUS_FILTER_MAP: dict[ServerStatus, str] = {
    ServerStatus.HEALTHY:  "NORMAL",
    ServerStatus.WARNING:  "ATTENTION",
    ServerStatus.CRITICAL: "DANGER",
}

_TYPE_FILTER_MAP: dict[BackupServerType, str] = {
    BackupServerType.DP:  "DP",
    BackupServerType.NAS: "NAS",
}

_TieringPlanCache = dict[str, TieringPlan]


class BackupServerCollection:
    """Collection interface for managing backup servers in the APM cluster.

    Accessed via APMClient.backup_servers; should not be instantiated directly.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        name_contains: str | None = None,
        status_filter: list[ServerStatus] | None = None,
        type_filter: list[BackupServerType] | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> ListResult[BackupServer]:
        """List all backup servers in the cluster.

        Tiering plan name and destination are resolved concurrently for all servers
        in the page that have a tiering plan assigned.

        Args:
            name_contains:  Name keyword search (fuzzy match).
            status_filter:  Filter by ServerStatus. Accepts multiple values; None means no filter.
            type_filter:    Filter by BackupServerType (DP or NAS). Accepts multiple values; None means no filter.
            limit:          Maximum records to return (default 500).
            offset:         Pagination start offset (default 0).

        Returns:
            (list of BackupServer, total count matching the filter)
        """
        params: list[tuple[str, str | int]] = [("offset", offset), ("limit", limit)]
        if name_contains:
            params.append(("keyword", name_contains))
        if status_filter:
            for s in dict.fromkeys(status_filter):  # deduplicate while preserving order
                if s == ServerStatus.DISCONNECTED:
                    params.append(("syncStatus", "DISCONNECTED"))
                    params.append(("syncStatus", "JOINING_DISCONNECTED"))
                elif s == ServerStatus.SYNCING:
                    params.append(("syncStatus", "JOINING"))
                else:
                    params.append(("status", _STATUS_FILTER_MAP[s]))
        if type_filter:
            # deduplicate while preserving order
            params.extend(("type", _TYPE_FILTER_MAP[t]) for t in dict.fromkeys(type_filter))
        raw = await self._session.get("/api/v1/infra/backup_server", params=params)
        servers_raw = raw.get("backupServers") or []
        tiering_cache = await _build_tiering_plan_cache(self._session, servers_raw)
        return ListResult([_parse_backup_server(s, tiering_cache) for s in servers_raw], raw.get("total"))

    async def get_by_name(self, name: str) -> BackupServer:
        """Fetch a backup server by name (keyword search + exact match).

        Searches using name as keyword; matches each result in order:
        case-insensitive name → case-insensitive hostname.
        Returns immediately on the first match without fetching further pages.

        Args:
            name: Server display name or hostname.

        Raises:
            ResourceNotFoundError: No backup server with an exact match was found.
        """
        q = name.lower()

        async def fetch(offset: int, limit: int) -> tuple[list[BackupServer], int | None]:
            return await self.list(name_contains=name, limit=limit, offset=offset)

        async for s in _paginate(fetch):
            if s.name.lower() == q or s.hostname.lower() == q:
                return s
        raise ResourceNotFoundError(
            f"BackupServer '{name}' not found.",
            resource_type="BackupServer",
            resource_id=name,
        )

    async def _find_management_servers(self) -> tuple[BackupServer | None, BackupServer | None]:
        """Scan all backup servers and return the (primary, secondary) Management Servers.

        Scans raw pages by role first and parses (resolving tiering info for)
        only the matched servers, so the scan cost does not grow with the number
        of tiering plans assigned to unrelated servers.
        """
        primary_raw: dict[str, Any] | None = None
        secondary_raw: dict[str, Any] | None = None

        async def fetch(offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
            raw = await self._session.get(
                "/api/v1/infra/backup_server",
                params=[("offset", offset), ("limit", limit)],
            )
            return raw.get("backupServers") or [], raw.get("total")

        async for s in _paginate(fetch, page_size=500):
            role = _ROLE_MAP.get(s.get("role") or "")
            if role == BackupServerRole.PRIMARY and primary_raw is None:
                primary_raw = s
            elif role == BackupServerRole.SECONDARY and secondary_raw is None:
                secondary_raw = s
            if primary_raw is not None and secondary_raw is not None:
                break

        matched = [s for s in (primary_raw, secondary_raw) if s is not None]
        cache = await _build_tiering_plan_cache(self._session, matched)
        primary = _parse_backup_server(primary_raw, cache) if primary_raw is not None else None
        secondary = _parse_backup_server(secondary_raw, cache) if secondary_raw is not None else None
        return primary, secondary

    async def change_tiering_plan(self, server: BackupServer, plan: TieringPlan | None) -> None:
        """Apply or remove a Tiering Plan on a Backup Server.

        Tiering is supported only for DP-type backup servers.

        Args:
            server: BackupServer object (obtained via get() or get_by_name()).
            plan:   TieringPlan to apply, or None to remove the current plan.

        Raises:
            InvalidOperationError: The backup server is not a DP-type server.
        """
        if server.server_type != BackupServerType.DP:
            raise InvalidOperationError(
                f"Tiering plans are only supported for DP-type backup servers ('{server.name}' is not DP-type).",
                resource_type="BackupServer",
                resource_id=server.backup_server_id,
            )
        body: dict[str, Any] = {
            "nsUidPairs": [{"namespace": server.namespace, "uid": server.backup_server_id}],
        }
        if plan is not None:
            body["tieringPlanId"] = plan.plan_id
        await self._session.put("/api/v1/infra/backup_server/tiering_plan", json=body)

    async def _get_me(self) -> BackupServer:
        """Fetch the backup server that is serving the current API session.

        Uses GET /api/v1/infra/backup_server/me. Called by APMClient.connect() to verify
        the connected host is the primary management server.

        Raises:
            ResourceNotFoundError: The endpoint does not exist — host is not an APM server.
        """
        raw = await self._session.get("/api/v1/infra/backup_server/me")
        server_raw = raw.get("backupServer") or raw
        return _parse_backup_server(server_raw, {})

    async def get(self, backup_server_id: str) -> BackupServer:
        """Fetch a backup server by ID.

        Raises:
            ResourceNotFoundError: The specified backup server does not exist.
        """
        with _not_found_as("BackupServer", backup_server_id):
            raw = await self._session.get(f"/api/v1/infra/backup_server/{backup_server_id}")
            server_raw = (
                raw.get("backupServer") or raw
                if not raw.get("backupServers")
                else raw["backupServers"][0]
            )
            if not server_raw.get("id"):
                raise ResourceNotFoundError("empty response", resource_type="unknown", resource_id="")
        tiering_cache = await _build_tiering_plan_cache(self._session, [server_raw])
        return _parse_backup_server(server_raw, tiering_cache)


def _tiering_plan_ref_uid(spec: dict[str, Any]) -> str:
    """Extract spec.tieringPlanRef.uid, tolerating an absent or null ref/uid."""
    return (spec.get("tieringPlanRef") or {}).get("uid") or ""


async def _build_tiering_plan_cache(
    session: WebAPISession,
    servers_raw: list[dict[str, Any]],
) -> _TieringPlanCache:
    """Collect unique tiering plan UIDs from a server list, fetch them in one
    concurrent batch (plan bodies first, then all destinations together), and
    return {plan_uid: TieringPlan}."""
    unique: list[str] = []
    seen: set[str] = set()
    for s in servers_raw:
        uid = _tiering_plan_ref_uid(s.get("spec") or {})
        if uid and uid not in seen:
            unique.append(uid)
            seen.add(uid)
    if not unique:
        return {}
    return await _get_plans_bulk(session, unique)


def _parse_backup_server(raw: dict[str, Any], tiering_cache: _TieringPlanCache) -> BackupServer:
    """Convert a backupServer object from an API response to the SDK BackupServer model."""
    status: dict[str, Any] = raw.get("status") or {}
    spec: dict[str, Any] = raw.get("spec") or {}
    stat: dict[str, Any] = status.get("storageStatistic") or {}
    server_type = BackupServerType.NAS if spec.get("type") == "NAS" else BackupServerType.DP
    is_nas = server_type == BackupServerType.NAS

    spec_sync = spec.get("syncStatus") or ""
    if spec_sync == "JOINING":
        server_status = ServerStatus.SYNCING
    elif spec_sync in _SYNC_DISCONNECTED:
        server_status = ServerStatus.DISCONNECTED
    else:
        server_status = _SERVER_STATUS_MAP.get(status.get("status") or "", ServerStatus.DISCONNECTED)
    system_version = None if is_nas else status.get("firmwareVer") or ""

    upgrade: dict[str, Any] = status.get("upgrade") or {}
    upgrade_status = upgrade.get("upgradeStatus") or ""
    is_updating = upgrade_status in {"PRECHECK", "BUILTIN_UPDATING", "DSM_UPDATING", "REBOOTING"}

    storage_total: int | None
    storage_used: int | None
    if is_nas:
        nas_list: list[dict[str, Any]] = status.get("nasStorage") or []
        if nas_list:
            storage_total = sum(int(s.get("totalBytes") or 0) for s in nas_list)
            storage_used = sum(int(s.get("usedBytes") or 0) for s in nas_list)
        else:
            storage_total = None
            storage_used = None
    else:
        dp: dict[str, Any] = status.get("dpStorage") or {}
        if dp:
            storage_total = int(dp.get("totalBytes") or 0)
            storage_used = int(dp.get("backupBytes") or 0) + int(dp.get("systemBytes") or 0)
        else:
            storage_total = None
            storage_used = None

    logical_bytes: int | None
    physical_bytes: int | None
    if stat:
        logical_bytes  = int(stat.get("transferBytes") or 0)
        physical_bytes = int(stat.get("usageBytes") or 0)
    else:
        logical_bytes  = None
        physical_bytes = None

    plan_uid = _tiering_plan_ref_uid(spec)
    tiering_plan = tiering_cache.get(plan_uid)
    tiering_plan_name        = tiering_plan.name        if tiering_plan else None
    tiering_plan_destination = tiering_plan.destination if tiering_plan else None
    tiering_status = _parse_tiering_status(raw.get("tieringInfo") or {})

    return BackupServer(
        backup_server_id=raw.get("id") or "",
        namespace=raw.get("namespace") or "",
        server_type=server_type,
        name=status.get("hostName") or "",
        hostname=spec.get("addr") or "",
        model=status.get("model") or "",
        system_version=system_version,
        status=server_status,
        is_updating=is_updating,
        serial=status.get("serial") or "",
        storage_total_bytes=storage_total,
        storage_used_bytes=storage_used,
        logical_backup_data_bytes=logical_bytes,
        physical_backup_data_bytes=physical_bytes,
        role=_ROLE_MAP.get(raw.get("role") or ""),
        description=spec.get("description") or "",
        tiering_plan_name=tiering_plan_name,
        tiering_plan_destination=tiering_plan_destination,
        tiering_status=tiering_status,
    )
