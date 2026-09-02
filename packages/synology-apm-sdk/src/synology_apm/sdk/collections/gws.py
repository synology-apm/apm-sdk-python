"""GWSCollection — entry collection for Google Workspace SaaS backup resources."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..enums import GWSWorkloadType, WorkloadCategory, WorkloadStatus
from ..exceptions import ResourceNotFoundError
from ..models.protection_plan import ProtectionPlan
from ..models.retirement_plan import RetirementPlan
from ..models.workload import GWSInfo, GWSSharedDriveInfo, GWSUserInfo, GWSWorkload
from ._shared import (
    _SAAS_STATUS_TO_API_BACKUP_STATUS,
    ListResult,
    _build_location_info,
    _build_workload_plan_ref,
    _check_active_for_write,
    _check_change_plan_preconditions,
    _check_not_retired,
    _not_found_as,
    _paginate,
    _parse_ts_optional,
    _plan_type_filter,
    _raise_first_batch_error,
    _resolve_namespaces_to_server_ids,
    _resolve_saas_workload_status,
    _VersionMixin,
)
from .gws_auto_backup_rule import GWSAutoBackupRuleCollection
from .protection_plans import GWSPlanCollection

_TYPE_TO_API_TYPE: dict[GWSWorkloadType, str] = {
    GWSWorkloadType.DRIVE:        "DRIVE",
    GWSWorkloadType.MAIL:         "MAIL",
    GWSWorkloadType.CONTACT:      "CONTACT",
    GWSWorkloadType.CALENDAR:     "CALENDAR",
    GWSWorkloadType.SHARED_DRIVE: "TEAM_DRIVE",
}
_API_TYPE_TO_TYPE: dict[str, GWSWorkloadType] = {v: k for k, v in _TYPE_TO_API_TYPE.items()}


def _parse_gws_workload(raw: dict[str, Any]) -> GWSWorkload | None:
    """Convert a single object from POST /api/v1/workload/gw_workload/list to the SDK model."""
    api_type: str = raw.get("workloadType") or ""
    workload_type = _API_TYPE_TO_TYPE.get(api_type)
    if workload_type is None:
        return None

    entity_meta: dict[str, Any] = raw.get("entityMeta") or {}
    spec: dict[str, Any] = entity_meta.get("spec") or {}

    workload_id: str = raw.get("uid") or ""
    namespace: str = raw.get("namespace") or ""
    domain: str = raw.get("domain") or ""
    plan_id: str = raw.get("planId") or ""
    plan_name: str = raw.get("planName") or ""
    plan_type: str = raw.get("planType") or ""
    is_retired: bool = plan_type == "ARCHIVE"
    plan = _build_workload_plan_ref(
        plan_id, plan_name, is_archive=is_retired, category=WorkloadCategory.GWS
    )

    last_backup_at = _parse_ts_optional(raw.get("lastBackupTime"))

    protected_data_bytes = int(raw.get("backupUsage") or 0)
    backup_copy_data_bytes = int(raw.get("copyUsage") or 0)

    backup_progress: int | None = None
    workload_status, items_backed_up = _resolve_saas_workload_status(raw, is_retired=is_retired)

    info: GWSInfo
    backup_user: str | None
    is_anomaly: bool
    if workload_type == GWSWorkloadType.SHARED_DRIVE:
        team_drive_info: dict[str, Any] = spec.get("teamDriveInfo") or {}
        info = GWSSharedDriveInfo(
            drive_id=team_drive_info.get("id") or "",
            drive_name=team_drive_info.get("name") or "",
        )
        name = team_drive_info.get("name") or ""
        backup_user_info: dict[str, Any] = raw.get("backupUserInfo") or {}
        backup_user = backup_user_info.get("mail") or None
        is_anomaly = bool(raw.get("isAnomaly") or False)
    else:
        user_info: dict[str, Any] = spec.get("userInfo") or {}
        info = GWSUserInfo(email=user_info.get("email") or "")
        name = user_info.get("name") or user_info.get("email") or ""
        backup_user = None
        is_anomaly = False

    server_info: dict[str, Any] = raw.get("backupServerInfo") or {}
    copy_info: dict[str, Any] = raw.get("backupCopyServerInfo") or {}
    backup_server = _build_location_info(server_info)
    backup_copy_destination = _build_location_info(copy_info)

    return GWSWorkload(
        workload_id=workload_id,
        name=name,
        category=WorkloadCategory.GWS,
        namespace=namespace,
        last_backup_at=last_backup_at,
        is_retired=is_retired,
        protected_data_bytes=protected_data_bytes,
        status=workload_status,
        plan=plan,
        backup_progress=backup_progress,
        items_backed_up=items_backed_up,
        backup_server=backup_server,
        backup_copy_destination=backup_copy_destination,
        backup_copy_data_bytes=backup_copy_data_bytes,
        workload_type=workload_type,
        domain=domain,
        info=info,
        backup_user=backup_user,
        is_anomaly=is_anomaly,
    )


def _label_matches(wl: GWSWorkload, query: str) -> bool:
    """Return True if the workload's display name or resource identifier exactly matches the query
    (case-insensitive)."""
    q = query.lower()
    if wl.name.lower() == q:
        return True
    info = wl.info
    if isinstance(info, GWSUserInfo):
        return info.email.lower() == q
    if isinstance(info, GWSSharedDriveInfo):
        return info.drive_name.lower() == q
    return False  # pragma: no cover


class GWSWorkloadCollection(_VersionMixin):
    """Collection interface for managing Google Workspace SaaS backup Workloads.

    Accessed via APMClient.gws.workloads; should not be instantiated directly.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        domain: str,
        workload_type: GWSWorkloadType,
        namespace: list[str] | None = None,
        plan: list[ProtectionPlan | RetirementPlan] | None = None,
        keyword: str | None = None,
        is_retired: bool = False,
        status: list[WorkloadStatus] | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> ListResult[GWSWorkload]:
        """List GWS Workloads of a given service sub-type for a domain.

        Args:
            domain:        Google Workspace domain (required).
            workload_type: Service sub-type to list (MAIL / DRIVE / CONTACT / CALENDAR / SHARED_DRIVE).
            namespace:     Return only workloads on one or more backup servers (OR logic; =
                           workload.namespace). The SDK resolves each namespace to an internal
                           backup server reference automatically; a namespace with no matching
                           backup server contributes no matches rather than raising.
            plan:          Restrict results to workloads assigned to one of the given plans (OR logic).
            keyword:       Name keyword (partial match).
            is_retired:    True = retired only; False = protected workloads only (default).
            status:        Filter by one or more backup statuses (OR logic); None returns all statuses.
                           WorkloadStatus.RETIRED is not accepted here — use is_retired=True instead.
            limit:         Maximum records to return (default 500).
            offset:        Pagination start offset (default 0).

        Returns:
            (list of GWSWorkload, total count).

        Raises:
            ValueError: WorkloadStatus.RETIRED was passed in `status`.
        """
        if status and WorkloadStatus.RETIRED in status:
            raise ValueError(
                "WorkloadStatus.RETIRED cannot be used as a status filter; use is_retired=True instead."
            )

        # Resolve namespace(s) → backup_server_ids; a namespace filter that matches no
        # backup server at all short-circuits to an empty result instead of falling
        # through to an unfiltered lookup.
        backup_server_ids: list[str] = []
        if namespace:
            backup_server_ids = await _resolve_namespaces_to_server_ids(self._session, namespace)
            if not backup_server_ids:
                return ListResult([], 0)

        plan_type = _plan_type_filter(is_retired)

        filter_body: dict[str, Any] = {
            "primKey": domain,
            "gwWorkloadFilter": {"gwWorkloadType": _TYPE_TO_API_TYPE[workload_type]},
            "limit": limit,
            "offset": offset,
        }
        if keyword:
            filter_body["keyword"] = keyword
        filter_body["planType"] = plan_type
        if backup_server_ids:
            filter_body["backupServerUids"] = backup_server_ids
        if plan:
            filter_body["planUids"] = [p.plan_id for p in plan]
        if status:
            filter_body["backupStatus"] = [_SAAS_STATUS_TO_API_BACKUP_STATUS[s] for s in status]
        raw = await self._session.post(
            "/api/v1/workload/gw_workload/list",
            json={"filter": filter_body},
        )

        workloads: list[GWSWorkload] = []
        for w in raw.get("gwWorkloads") or []:
            parsed = _parse_gws_workload(w)
            if parsed is not None:
                workloads.append(parsed)

        return ListResult(workloads, raw.get("total"))

    async def get(
        self,
        workload_id: str,
        namespace: str,
        domain: str,
        workload_type: GWSWorkloadType,
    ) -> GWSWorkload:
        """Fetch a GWS Workload by workload ID and backup server namespace.

        Args:
            workload_id:   Workload ID.
            namespace:     Backup server namespace.
            domain:        Google Workspace domain.
            workload_type: Service sub-type (MAIL / DRIVE / CONTACT / CALENDAR / SHARED_DRIVE).

        Raises:
            ResourceNotFoundError: No workload matches the given workload_id + namespace.
        """
        filter_body: dict[str, Any] = {
            "primKey": domain,
            "nsUidPair": {"namespace": namespace, "uid": workload_id},
            "gwWorkloadFilter": {"gwWorkloadType": _TYPE_TO_API_TYPE[workload_type]},
            "limit": 1,
            "offset": 0,
        }
        msg = f"GWSWorkload not found (namespace={namespace!r}, uid={workload_id!r})."
        with _not_found_as("GWSWorkload", workload_id, message=msg):
            raw = await self._session.post(
                "/api/v1/workload/gw_workload/list",
                json={"filter": filter_body},
            )
            for w in raw.get("gwWorkloads") or []:
                parsed = _parse_gws_workload(w)
                if parsed is not None:
                    return parsed
            raise ResourceNotFoundError("no matching workload", resource_type="unknown", resource_id="")

    async def get_by_name(
        self,
        name: str,
        domain: str,
        workload_type: GWSWorkloadType,
        is_retired: bool = False,
    ) -> GWSWorkload:
        """Fetch a GWS Workload by name or email (keyword search + exact match).

        Returns the first workload whose display name, email, or shared drive name matches
        exactly (case-insensitive), without fetching further pages.

        Args:
            name:          Display name or email (exact match, case-insensitive).
            domain:        Google Workspace domain; required to scope the search.
            workload_type: Service sub-type (MAIL / DRIVE / CONTACT / CALENDAR / SHARED_DRIVE).
            is_retired:    True=retired only, False=protected workloads only (default).

        Raises:
            ResourceNotFoundError: No workload with an exact match was found.
        """
        plan_type = _plan_type_filter(is_retired)

        async def fetch(offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
            filter_body: dict[str, Any] = {
                "primKey": domain,
                "keyword": name,
                "gwWorkloadFilter": {"gwWorkloadType": _TYPE_TO_API_TYPE[workload_type]},
                "limit": limit,
                "offset": offset,
            }
            filter_body["planType"] = plan_type
            raw = await self._session.post(
                "/api/v1/workload/gw_workload/list",
                json={"filter": filter_body},
            )
            return raw.get("gwWorkloads") or [], raw.get("total")

        async for w in _paginate(fetch):
            parsed = _parse_gws_workload(w)
            if parsed is None:
                continue
            if _label_matches(parsed, name):
                return parsed
        raise ResourceNotFoundError(
            f"GWSWorkload '{name}' not found.",
            resource_type="GWSWorkload",
            resource_id=name,
        )

    async def backup_now(self, workload: GWSWorkload) -> None:
        """Trigger an on-demand backup for a GWS Workload.

        Args:
            workload: GWSWorkload object (obtained via get()).

        Raises:
            InvalidOperationError: The workload is already retired.
            APIError: APM rejected the backup request.
        """
        _check_active_for_write(workload, "cannot be backed up")
        await self._session.post(
            "/api/v1/workload/gw_workload/batch/backup",
            json={"nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}]},
        )

    async def cancel_backup(self, workload: GWSWorkload) -> None:
        """Cancel the running backup for a GWS Workload.

        Args:
            workload: GWSWorkload object (obtained via get()).

        Raises:
            InvalidOperationError: The workload is already retired.
            APIError: No backup in progress, or APM rejected the cancel request.
        """
        _check_active_for_write(workload, "has no active backup to cancel")
        await self._session.post(
            "/api/v1/workload/gw_workload/batch/cancel",
            json={"nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}]},
        )

    async def retire(
        self,
        workload: GWSWorkload,
        plan: RetirementPlan,
    ) -> None:
        """Retire a GWS Workload (apply a retirement policy; irreversible).

        Args:
            workload: GWSWorkload object (obtained via get(); must not be already retired).
            plan:     RetirementPlan object (obtained via apm.retirement_plans.get() or get_by_name()).

        Raises:
            InvalidOperationError: The workload is already retired, or APM rejected the
                retirement because the workload is in a state that does not allow it
                (e.g., still initializing).
        """
        _check_not_retired(workload)
        await self._put_plan_change(workload, plan.plan_id, "ARCHIVE")

    async def change_plan(self, workload: GWSWorkload, plan: ProtectionPlan | RetirementPlan) -> None:
        """Change the Protection Plan or Retirement Plan assigned to a GWS Workload.

        Args:
            workload: GWSWorkload object (obtained via get() or get_by_name()).
            plan:     ProtectionPlan (workload must not be retired, and its category must match
                      the workload's category) or RetirementPlan (workload must already be retired).

        Raises:
            InvalidOperationError: The plan type does not match the workload's retirement state,
                the plan's category does not match the workload's category, or APM rejected
                the change because the workload is in a state that does not allow it
                (e.g., still initializing).
        """
        _check_change_plan_preconditions(workload, plan)
        plan_type = "ARCHIVE" if isinstance(plan, RetirementPlan) else "BACKUP"
        await self._put_plan_change(workload, plan.plan_id, plan_type)

    async def delete(self, workload: GWSWorkload) -> None:
        """Delete a GWS Workload from APM.

        Args:
            workload: GWSWorkload to delete. Active and retired workloads are both supported.
                      If the workload no longer exists, the call succeeds silently.

        Raises:
            InvalidOperationError: APM rejected the delete request.
        """
        resp = await self._session.delete(
            "/api/v1/workload/gw_workload/batch",
            json={
                "primKey": workload.domain,
                "isFromUnmanagedWorkload": False,
                "nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}],
            },
        )
        _raise_first_batch_error(
            (resp or {}).get("errors") or [],
            workload,
            default_message="Workload delete failed",
            response_body=resp,
        )

    async def _put_plan_change(self, workload: GWSWorkload, plan_id: str, plan_type: str) -> None:
        resp = await self._session.put(
            "/api/v1/workload/gw_workload/batch/change_plan",
            json={
                "domain": workload.domain,
                "planId": plan_id,
                "planType": plan_type,
                "nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}],
                "isFromUnmanagedWorkload": False,
            },
        )
        _raise_first_batch_error(
            (resp or {}).get("errors") or [],
            workload,
            default_message="Workload plan change failed",
            response_body=resp,
        )


class GWSCollection:
    """Entry collection for Google Workspace SaaS backup resources.

    Accessed via APMClient.gws; should not be instantiated directly.
    Provides workloads, plans, and auto_backup_rules sub-collections.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._workloads = GWSWorkloadCollection(session)
        self._plans = GWSPlanCollection(session)
        self._auto_backup_rules = GWSAutoBackupRuleCollection(session)

    @property
    def workloads(self) -> GWSWorkloadCollection:
        """Access the GWSWorkloadCollection."""
        return self._workloads

    @property
    def plans(self) -> GWSPlanCollection:
        """Access the GWSPlanCollection."""
        return self._plans

    @property
    def auto_backup_rules(self) -> GWSAutoBackupRuleCollection:
        """Access the GWSAutoBackupRuleCollection for managing auto-backup rules."""
        return self._auto_backup_rules
