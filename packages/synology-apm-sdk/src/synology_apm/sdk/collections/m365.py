"""M365Collection — entry collection for M365 SaaS backup resources."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..enums import M365WorkloadType, WorkloadCategory, WorkloadStatus
from ..exceptions import InvalidOperationError, ResourceNotFoundError
from ..models.protection_plan import ProtectionPlan
from ..models.retirement_plan import RetirementPlan
from ..models.workload import (
    M365GroupInfo,
    M365SiteInfo,
    M365TeamInfo,
    M365UserInfo,
    M365Workload,
)
from ._shared import (
    _build_location_info,
    _build_workload_plan_ref,
    _check_active_for_write,
    _check_change_plan_preconditions,
    _check_not_retired,
    _m365_plan_type,
    _not_found_as,
    _paginate,
    _parse_ts_optional,
    _resolve_namespace_to_server_id,
    _VersionMixin,
)
from .m365_auto_backup_rule import M365AutoBackupRuleCollection
from .m365_mail_export import ExchangeExportCollection, GroupExportCollection
from .protection_plans import M365PlanCollection

_M365_STATUS_MAP: dict[str, WorkloadStatus] = {
    "SUCCESS":           WorkloadStatus.SUCCESS,
    "WARNING":           WorkloadStatus.PARTIAL,
    "ERROR":             WorkloadStatus.FAILED,
    "CANCELED":          WorkloadStatus.CANCELED,
    "NOT_BACKED_UP_YET": WorkloadStatus.NO_BACKUPS,
}


_TYPE_TO_API_TYPE: dict[M365WorkloadType, str] = {
    M365WorkloadType.EXCHANGE:   "USER_EXCHANGE",
    M365WorkloadType.ONEDRIVE:   "USER_DRIVE",
    M365WorkloadType.CHAT:       "USER_CHAT",
    M365WorkloadType.SHAREPOINT: "SITE",
    M365WorkloadType.TEAMS:      "TEAMS",
    M365WorkloadType.GROUP:      "GROUP_EXCHANGE",
}
_API_TYPE_TO_TYPE: dict[str, M365WorkloadType] = {v: k for k, v in _TYPE_TO_API_TYPE.items()}


def _parse_m365_workload(raw: dict[str, Any]) -> M365Workload | None:
    """Convert a single object from POST /api/v1/workload/m365_workload to the SDK model."""
    api_type: str = raw.get("workloadType", "")
    workload_type = _API_TYPE_TO_TYPE.get(api_type)
    if workload_type is None:
        return None

    entity_meta: dict[str, Any] = raw.get("entityMeta", {})
    spec: dict[str, Any] = entity_meta.get("spec", {})

    workload_id: str = raw.get("uid", "")
    namespace: str = raw.get("namespace", "")
    tenant_id: str = spec.get("tenantId", "")
    plan_id: str = raw.get("planId", "")
    plan_name: str = raw.get("planName", "")
    plan_type: str = raw.get("planType", "")
    is_retired: bool = plan_type == "ARCHIVE"
    plan = _build_workload_plan_ref(
        plan_id, plan_name, is_archive=is_retired, category=WorkloadCategory.M365
    )

    last_backup_at = _parse_ts_optional(raw.get("lastBackupTime"))

    usage_raw = raw.get("backupUsage", "0")
    protected_data_bytes = int(usage_raw) if usage_raw else 0

    copy_usage_raw = raw.get("copyUsage", "0")
    backup_copy_data_bytes = int(copy_usage_raw) if copy_usage_raw else 0

    _backup_status = raw.get("backupStatus", "")
    backup_progress: int | None = None
    items_backed_up: int | None
    workload_status: WorkloadStatus
    if _backup_status == "DELETING":
        items_backed_up = None
        workload_status = WorkloadStatus.DELETING
    elif is_retired:
        items_backed_up = None
        workload_status = WorkloadStatus.RETIRED
    elif _backup_status == "BACKUPING":
        workload_status = WorkloadStatus.BACKING_UP
        raw_items = raw.get("processItemCount")
        items_backed_up = int(raw_items) if raw_items is not None else None
    elif _backup_status == "QUEUING":
        items_backed_up = None
        workload_status = WorkloadStatus.QUEUING
    else:
        items_backed_up = None
        workload_status = _M365_STATUS_MAP.get(_backup_status, WorkloadStatus.NO_BACKUPS)

    info: M365UserInfo | M365SiteInfo | M365TeamInfo | M365GroupInfo
    if workload_type in (M365WorkloadType.EXCHANGE, M365WorkloadType.ONEDRIVE, M365WorkloadType.CHAT):
        user_info: dict[str, Any] = spec.get("userInfo") or {}
        info = M365UserInfo(user_principal_name=user_info.get("email", ""))
        name = user_info.get("userName", "") or user_info.get("email", "")
    elif workload_type == M365WorkloadType.SHAREPOINT:
        site_info: dict[str, Any] = spec.get("siteInfo") or {}
        info = M365SiteInfo(
            site_url=site_info.get("url", ""),
            site_name=site_info.get("siteName", ""),
        )
        name = site_info.get("siteName", "")
    elif workload_type == M365WorkloadType.TEAMS:
        team_info: dict[str, Any] = spec.get("teamInfo") or {}
        info = M365TeamInfo(
            team_id=team_info.get("id", ""),
            team_name=team_info.get("name", ""),
            web_url=team_info.get("webUrl", ""),
        )
        name = team_info.get("name", "")
    else:  # GROUP_EXCHANGE
        group_info: dict[str, Any] = spec.get("groupInfo") or {}
        info = M365GroupInfo(
            group_id=group_info.get("id", ""),
            display_name=group_info.get("displayName", ""),
            mail=group_info.get("mail", ""),
        )
        name = group_info.get("displayName", "")

    server_info: dict[str, Any] = raw.get("backupServerInfo", {}) or {}
    copy_info: dict[str, Any] = raw.get("backupCopyServerInfo", {}) or {}
    backup_server = _build_location_info(server_info)
    backup_copy_destination = _build_location_info(copy_info)

    return M365Workload(
        workload_id=workload_id,
        name=name,
        category=WorkloadCategory.M365,
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
        tenant_id=tenant_id,
        info=info,
    )


def _label_matches(wl: M365Workload, query: str) -> bool:
    """Return True if the workload's display name or resource identifier exactly matches the query
    (case-insensitive)."""
    q = query.lower()
    if wl.name.lower() == q:
        return True
    info = wl.info
    if isinstance(info, M365UserInfo):
        return info.user_principal_name.lower() == q
    if isinstance(info, M365GroupInfo):
        return bool(info.mail) and info.mail.lower() == q
    return False  # pragma: no cover


class M365WorkloadCollection(_VersionMixin):
    """Collection interface for managing M365 SaaS backup Workloads.

    Accessed via APMClient.m365.workloads; should not be instantiated directly.

    get() fetches a single Workload by workload_id + namespace; get_by_name() looks up a
    Workload by name, UPN, email, or URL via keyword search and exact match. Neither
    performs a full list-all scan.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        tenant_id: str,
        workload_type: M365WorkloadType,
        namespace: str | None = None,
        plan: list[ProtectionPlan | RetirementPlan] | None = None,
        keyword: str | None = None,
        is_retired: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[M365Workload], int]:
        """List M365 Workloads of a given service sub-type for a tenant.

        Args:
            tenant_id:     Azure AD tenant ID (required).
            workload_type: Service sub-type to list (EXCHANGE / ONEDRIVE / etc.).
            namespace:     Return only workloads on a specific backup server (= workload.namespace).
                           The SDK resolves the namespace to an internal backup server reference automatically.
            plan:          Restrict results to workloads assigned to one of the given plans (OR logic).
            keyword:       Name keyword (partial match).
            is_retired:    True = retired only; False = protected workloads only (default).
            limit:         Maximum records to return (default 500).
            offset:        Pagination start offset (default 0).

        Returns:
            (list of M365Workload, total count).

        Raises:
            ResourceNotFoundError: No backup server matching the specified namespace exists.
        """
        # Resolve namespace → backup_server_id
        backup_server_id: str | None = None
        if namespace:
            backup_server_id = await _resolve_namespace_to_server_id(self._session, namespace)

        plan_type = _m365_plan_type(is_retired)

        filter_body: dict[str, Any] = {
            "primKey": tenant_id,
            "m365WorkloadFilter": {"m365WorkloadType": _TYPE_TO_API_TYPE[workload_type]},
            "limit": limit,
            "offset": offset,
        }
        if keyword:
            filter_body["keyword"] = keyword
        filter_body["planType"] = plan_type
        if backup_server_id:
            filter_body["backupServerUids"] = [backup_server_id]
        if plan:
            filter_body["planUids"] = [p.plan_id for p in plan]
        raw = await self._session.post(
            "/api/v1/workload/m365_workload",
            json={"filter": filter_body},
        )

        workloads: list[M365Workload] = []
        for w in raw.get("m365Workloads", []):
            parsed = _parse_m365_workload(w)
            if parsed is not None:
                workloads.append(parsed)

        return workloads, raw.get("total", 0)

    async def get(
        self,
        workload_id: str,
        namespace: str,
        tenant_id: str,
        workload_type: M365WorkloadType,
    ) -> M365Workload:
        """Fetch an M365 Workload by ID (lookup via nsUidPair).

        Args:
            workload_id:   Workload ID.
            namespace:     Backup server namespace.
            tenant_id:     Azure AD tenant ID.
            workload_type: Service sub-type (EXCHANGE / ONEDRIVE / etc.).

        Raises:
            ResourceNotFoundError: No workload matches the given workload_id + namespace.
        """
        filter_body: dict[str, Any] = {
            "primKey": tenant_id,
            "nsUidPair": {"namespace": namespace, "uid": workload_id},
            "m365WorkloadFilter": {"m365WorkloadType": _TYPE_TO_API_TYPE[workload_type]},
            "limit": 1,
            "offset": 0,
        }
        msg = f"M365Workload not found (namespace={namespace!r}, uid={workload_id!r})."
        with _not_found_as("M365Workload", workload_id, message=msg):
            raw = await self._session.post(
                "/api/v1/workload/m365_workload",
                json={"filter": filter_body},
            )
            for w in raw.get("m365Workloads", []):
                parsed = _parse_m365_workload(w)
                if parsed is not None:
                    return parsed
            raise ResourceNotFoundError("no matching workload", resource_type="unknown", resource_id="")

    async def get_by_name(
        self,
        name: str,
        tenant_id: str,
        workload_type: M365WorkloadType,
        is_retired: bool = False,
    ) -> M365Workload:
        """Fetch an M365 Workload by name, UPN, email, or URL (keyword search + exact match).

        Args:
            name:          Display name, UPN, email, or URL (exact match, case-insensitive).
            tenant_id:     Azure AD tenant ID; required to scope the search.
            workload_type: Service sub-type (EXCHANGE / ONEDRIVE / etc.).
            is_retired:    True=retired only, False=protected workloads only (default).

        Raises:
            ResourceNotFoundError: Not found, or name is ambiguous (multiple matches).
        """
        plan_type = _m365_plan_type(is_retired)

        async def fetch(offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
            filter_body: dict[str, Any] = {
                "primKey": tenant_id,
                "keyword": name,
                "m365WorkloadFilter": {"m365WorkloadType": _TYPE_TO_API_TYPE[workload_type]},
                "limit": limit,
                "offset": offset,
            }
            filter_body["planType"] = plan_type
            raw = await self._session.post(
                "/api/v1/workload/m365_workload",
                json={"filter": filter_body},
            )
            return raw.get("m365Workloads", []), None

        async for w in _paginate(fetch):
            parsed = _parse_m365_workload(w)
            if parsed is None:
                continue
            if parsed.workload_id == name or _label_matches(parsed, name):
                return parsed
        raise ResourceNotFoundError(
            f"M365Workload '{name}' not found.",
            resource_type="M365Workload",
            resource_id=name,
        )

    async def backup_now(self, workload: M365Workload) -> None:
        """Trigger an on-demand backup for an M365 Workload.

        Args:
            workload: M365Workload object (obtained via get()).

        Raises:
            InvalidOperationError: The workload is already retired.
            APIError: APM rejected the backup request.
        """
        _check_active_for_write(workload, "cannot be backed up")
        await self._session.post(
            "/api/v1/workload/m365_workload/batch/backup",
            json={
                "tenantId": workload.tenant_id,
                "nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}],
            },
        )

    async def cancel_backup(self, workload: M365Workload) -> None:
        """Cancel the running backup for an M365 Workload.

        Args:
            workload: M365Workload object (obtained via get()).

        Raises:
            InvalidOperationError: The workload is already retired.
            APIError: No backup in progress, or APM rejected the cancel request.
        """
        _check_active_for_write(workload, "has no active backup to cancel")
        await self._session.post(
            "/api/v1/workload/m365_workload/batch/cancel",
            json={"nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}]},
        )

    async def retire(
        self,
        workload: M365Workload,
        plan: RetirementPlan,
    ) -> None:
        """Retire an M365 Workload (apply a retirement policy; irreversible).

        Args:
            workload: M365Workload object (obtained via get(); must not be already retired).
            plan:     RetirementPlan object (obtained via apm.retirement_plans.get() or get_by_name()).

        Raises:
            InvalidOperationError: The workload is already retired, or APM rejected the
                retirement because the workload is in a state that does not allow it
                (e.g., still initializing).
            PermissionDeniedError: Insufficient permission to retire workloads.
        """
        _check_not_retired(workload)
        await self._put_plan_change(workload, plan.plan_id, "ARCHIVE")

    async def change_plan(self, workload: M365Workload, plan: ProtectionPlan | RetirementPlan) -> None:
        """Change the Protection Plan or Retirement Plan assigned to an M365 Workload.

        Args:
            workload: M365Workload object (obtained via get() or get_by_name()).
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

    async def delete(self, workload: M365Workload) -> None:
        """Delete an M365 Workload from APM.

        Args:
            workload: M365Workload to delete. Active and retired workloads are both supported.
                      If the workload no longer exists, the call succeeds silently.

        Raises:
            InvalidOperationError: APM rejected the delete request.
            AuthenticationError:   Session expired.
            APIError:              Unexpected error from APM.
        """
        resp = await self._session.delete(
            "/api/v1/workload/m365_workload/batch",
            json={
                "tenantId": workload.tenant_id,
                "isFromUnmanagedWorkload": False,
                "nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}],
            },
        )
        errors = (resp or {}).get("errors") or []
        if errors:
            err = errors[0]
            raise InvalidOperationError(
                err.get("message", "Workload delete failed"),
                resource_type="Workload",
                resource_id=workload.workload_id,
                error_code=err.get("errorCode"),
                response_body=resp,
            )

    async def _put_plan_change(self, workload: M365Workload, plan_id: str, plan_type: str) -> None:
        resp = await self._session.put(
            "/api/v1/workload/m365_workload/batch/change_plan",
            json={
                "tenantId": workload.tenant_id,
                "planId": plan_id,
                "planType": plan_type,
                "nsUidPairs": [{"namespace": workload.namespace, "uid": workload.workload_id}],
                "isFromUnmanagedWorkload": False,
            },
        )
        errors = (resp or {}).get("errors") or []
        if errors:
            err = errors[0]
            raise InvalidOperationError(
                err.get("message", "Workload plan change failed"),
                resource_type="Workload",
                resource_id=workload.workload_id,
                error_code=err.get("errorCode"),
                response_body=resp,
            )


class M365Collection:
    """Entry collection for M365 SaaS backup resources.

    Accessed via APMClient.m365; should not be instantiated directly.
    Provides workloads, plans, exchange_export, and group_export sub-collections.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._workloads = M365WorkloadCollection(session)
        self._plans = M365PlanCollection(session)
        self._exchange_export = ExchangeExportCollection(session)
        self._group_export = GroupExportCollection(session)
        self._auto_backup_rules = M365AutoBackupRuleCollection(session)

    @property
    def workloads(self) -> M365WorkloadCollection:
        """Access the M365WorkloadCollection."""
        return self._workloads

    @property
    def plans(self) -> M365PlanCollection:
        """Access the M365PlanCollection."""
        return self._plans

    @property
    def exchange_export(self) -> ExchangeExportCollection:
        """Access the ExchangeExportCollection for Exchange mailbox PST export."""
        return self._exchange_export

    @property
    def group_export(self) -> GroupExportCollection:
        """Access the GroupExportCollection for Group mailbox PST export."""
        return self._group_export

    @property
    def auto_backup_rules(self) -> M365AutoBackupRuleCollection:
        """Access the M365AutoBackupRuleCollection for managing auto-backup rules."""
        return self._auto_backup_rules
