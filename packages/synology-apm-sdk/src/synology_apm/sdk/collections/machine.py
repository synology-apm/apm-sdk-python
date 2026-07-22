"""Machine category collections: MachineCollection / MachineWorkloadCollection."""
from __future__ import annotations

import json
from typing import Any

from .._http import WebAPISession
from ..enums import (
    _VERIFY_STATUS_MAP,
    FileServerType,
    MachineWorkloadType,
    VerifyStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from ..exceptions import APIError, DuplicateWorkloadError, InvalidOperationError, ResourceNotFoundError
from ..models.protection_plan import ProtectionPlan
from ..models.retirement_plan import RetirementPlan
from ..models.version import WorkloadVersion
from ..models.workload import (
    FileServerAddRequest,
    FileServerConfig,
    FileServerPathSelector,
    FileServerUpdateRequest,
    MachineWorkload,
    Workload,
)
from ._shared import (
    _MACHINE_WORKLOAD_TYPE_MAP,
    ListResult,
    _build_location_info,
    _build_workload_plan_ref,
    _check_active_for_write,
    _check_change_plan_preconditions,
    _check_not_retired,
    _machine_protect_status,
    _not_found_as,
    _paginate,
    _parse_ts_optional,
    _parse_verify_status,
    _raise_first_batch_error,
    _tunnel_headers,
    _VersionMixin,
)
from .protection_plans import MachinePlanCollection

_LVR_TO_STATUS: dict[str, WorkloadStatus] = {
    "VERSION_RESULT_SUCCESS":  WorkloadStatus.SUCCESS,
    "VERSION_RESULT_FAILED":   WorkloadStatus.FAILED,
    "VERSION_RESULT_PARTIAL":  WorkloadStatus.PARTIAL,
    "VERSION_RESULT_CANCELED": WorkloadStatus.CANCELED,
}

# WorkloadStatus filter reverse maps: each filterable status is governed by exactly one of
# these two raw fields (see _parse_workload() below for the equivalent forward derivation).
# RETIRED is intentionally absent — it's controlled by the is_retired parameter, not a raw
# status field, and is rejected by list() if requested via `status`.
_STATUS_TO_JOB_STATUS: dict[WorkloadStatus, str] = {
    WorkloadStatus.QUEUING:    "WAITING_TASK",
    WorkloadStatus.BACKING_UP: "RUNNING",
    WorkloadStatus.DELETING:   "DELETING",
}
_STATUS_TO_LVR: dict[WorkloadStatus, str] = {
    **{v: k for k, v in _LVR_TO_STATUS.items()},
    WorkloadStatus.NO_BACKUPS: "VERSION_RESULT_NONE",  # no forward-parse counterpart; see _parse_workload()
}
_VERIFY_STATUS_TO_API: dict[VerifyStatus, str] = {v: k for k, v in _VERIFY_STATUS_MAP.items()}


_FS_DUPLICATE_ERROR_CODE = 7001  # workload already exists in the same plan on the same server

# osName API string → FileServerType (safe lookup; unrecognised values fall back to UNKNOWN)
_FS_OS_TYPE_MAP: dict[str, FileServerType] = {t.value: t for t in FileServerType}


def _raise_fs_duplicate(resource_id: str, error_code: int, response_body: object) -> None:
    raise DuplicateWorkloadError(
        f"The file server at {resource_id} is already enrolled in the same plan on the same backup server.",
        resource_type="file_server",
        resource_id=resource_id,
        error_code=error_code,
        response_body=response_body,
    )


def _batch_errors_from_failed(resp: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten the device-workload batch response's {"failed": {"entries": [{"error": {...}}]}} shape."""
    entries = ((resp or {}).get("failed") or {}).get("entries", [])
    return [e.get("error", {}) for e in entries]




# ── MachineCollection ─────────────────────────────────────────────────────


class MachineCollection:
    """Entry collection for Machine category backup resources.

    Accessed via APMClient.machine; should not be instantiated directly.
    Provides workloads and plans sub-collections.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._workloads = MachineWorkloadCollection(session)
        self._plans = MachinePlanCollection(session)

    @property
    def workloads(self) -> MachineWorkloadCollection:
        """Access the MachineWorkloadCollection."""
        return self._workloads

    @property
    def plans(self) -> MachinePlanCollection:
        """Access the MachinePlanCollection."""
        return self._plans


# ── MachineWorkloadCollection ─────────────────────────────────────────────


class MachineWorkloadCollection(_VersionMixin):
    """Collection interface for managing device backup Workloads (PC/PS/VM/FS).

    Accessed via APMClient.machine.workloads; should not be instantiated directly.

    get() fetches a single Workload by workload_id + namespace; get_by_name() looks up a
    Workload by display name via keyword search and exact match. Neither performs a
    full list-all scan.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    # ── Public API ─────────────────────────────────────────────────────────

    async def list(
        self,
        workload_types: list[MachineWorkloadType] | None = None,
        namespace: str | None = None,
        plan: list[ProtectionPlan | RetirementPlan] | None = None,
        is_retired: bool = False,
        name_contains: str | None = None,
        hypervisor_id: str | None = None,
        status: list[WorkloadStatus] | None = None,
        verify_status: list[VerifyStatus] | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> ListResult[MachineWorkload]:
        """List device Workloads with optional filtering.

        Args:
            workload_types: Filter by one or more sub-types (PC / PS / VM / FS); None returns all sub-types.
            namespace:    Return only workloads on a specific backup server (matches workload.namespace).
            plan:         Restrict results to workloads assigned to one of the given plans (OR logic).
            is_retired:   Retirement filter:
                          True  → retired workloads only.
                          False → protected workloads only (default).
            name_contains: Name keyword (partial match, case-insensitive).
            hypervisor_id: Filter VM workloads by hypervisor inventory UUID. Only meaningful for VM workloads.
            status:        Filter by one or more backup statuses (OR logic); None returns all statuses.
                           WorkloadStatus.RETIRED is not accepted here — use is_retired=True instead.
            verify_status: Filter by one or more backup verification statuses (OR logic); None returns
                           all. Only meaningful for PS/VM workloads. PC/FS workloads may still be
                           included in results (verification is not tracked for them at all, so they
                           are not excluded by this filter), but always report verify_status=None.
            limit:         Maximum records to return (default 500).
            offset:        Pagination start offset (default 0).

        Returns:
            (list of Workload, total count matching the filter)

        Raises:
            ValueError: WorkloadStatus.RETIRED was passed in `status`.
        """
        if status and WorkloadStatus.RETIRED in status:
            raise ValueError(
                "WorkloadStatus.RETIRED cannot be used as a status filter; use is_retired=True instead."
            )

        # Build params as list-of-tuples to support multi-value workloadType
        param_pairs: list[tuple[str, str | int]] = [
            ("filter.limit", limit),
            ("filter.offset", offset),
            ("filter.isFilterBasedOnNonWorkloadType", "true"),
        ]
        param_pairs.extend(("filter.workloadType", wt.name) for wt in workload_types or ())
        if name_contains:
            param_pairs.append(("filter.keyword", name_contains))
        param_pairs.append(("filter.protectStatus", _machine_protect_status(is_retired)))
        if namespace:
            param_pairs.append(("filter.namespace", namespace))
        param_pairs.extend(("filter.planId", p.plan_id) for p in plan or ())
        if hypervisor_id:
            param_pairs.append(("filter.filterVm.inventoryId", hypervisor_id))
        for s in status or ():
            if s in _STATUS_TO_JOB_STATUS:
                param_pairs.append(("filter.jobStatus", _STATUS_TO_JOB_STATUS[s]))
            else:
                param_pairs.append(("filter.latestVersionResult", _STATUS_TO_LVR[s]))
        param_pairs.extend(("filter.verifyStatus", _VERIFY_STATUS_TO_API[vs]) for vs in verify_status or ())

        raw = await self._session.get(
            "/api/v2/workload/device_workload", params=param_pairs
        )
        workloads: list[MachineWorkload] = [
            _parse_workload(w) for w in raw.get("workloads", [])
        ]
        return ListResult(workloads, raw.get("total"))

    async def get(self, workload_id: str, namespace: str) -> MachineWorkload:
        """Fetch a device Workload by ID (direct lookup, no list scan).

        Args:
            workload_id: Workload ID.
            namespace:   Backup server namespace.

        Raises:
            ResourceNotFoundError: No workload matches the given workload_id + namespace.
        """
        msg = f"Workload not found (namespace={namespace!r}, id={workload_id!r})."
        with _not_found_as("Workload", workload_id, message=msg):
            raw = await self._session.get(
                f"/api/v1/workload/device_workload/{workload_id}",
                params={"namespace": namespace},
            )
            if not raw or "id" not in raw:
                raise ResourceNotFoundError("empty response", resource_type="unknown", resource_id="")
        return _parse_workload(raw)

    async def get_by_name(self, name: str, is_retired: bool = False) -> MachineWorkload:
        """Fetch a device Workload by display name (keyword search + exact match).

        Returns the first workload whose display name matches exactly
        (case-insensitive), without fetching further pages.

        Args:
            name:       Workload display name (exact match, case-insensitive).
            is_retired: True=retired only, False=protected workloads only (default).

        Raises:
            ResourceNotFoundError: No workload with an exact match was found.
        """
        q = name.lower()

        async def fetch(offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
            param_pairs: list[tuple[str, str | int]] = [
                ("filter.isFilterBasedOnNonWorkloadType", "true"),
                ("filter.keyword", name),
                ("filter.limit", limit),
                ("filter.offset", offset),
                ("filter.protectStatus", _machine_protect_status(is_retired)),
            ]
            raw = await self._session.get("/api/v2/workload/device_workload", params=param_pairs)
            return raw.get("workloads", []), raw.get("total")

        async for raw_wl in _paginate(fetch):
            wl = _parse_workload(raw_wl)
            if wl.name.lower() == q:
                return wl
        raise ResourceNotFoundError(
            f"Workload '{name}' not found.",
            resource_type="Workload",
            resource_id=name,
        )

    def _version_wl_type(self, workload: Workload) -> MachineWorkloadType | None:
        """Pass the device sub-type to the version parser so PS/VM verify status is resolved."""
        return workload.workload_type if isinstance(workload, MachineWorkload) else None

    async def backup_now(self, workload: MachineWorkload) -> None:
        """Trigger an on-demand backup for a device Workload.

        Args:
            workload: MachineWorkload object (obtained via get()).

        Raises:
            InvalidOperationError: The workload is already retired.
        """
        _check_active_for_write(workload, "cannot be backed up")
        await self._session.post(
            "/api/v1/workload/device_workload/backup",
            json={"workloadRefs": [{"uid": workload.workload_id, "namespace": workload.namespace}]},
        )

    async def cancel_backup(self, workload: MachineWorkload) -> None:
        """Cancel the running backup for a device Workload.

        Args:
            workload: MachineWorkload object (obtained via get()).

        Raises:
            InvalidOperationError: The workload is already retired.
        """
        _check_active_for_write(workload, "has no active backup to cancel")
        await self._session.post(
            "/api/v1/workload/device_workload/cancel",
            json={"workloadRefs": [{"uid": workload.workload_id, "namespace": workload.namespace}]},
        )

    async def add_file_server(self, request: FileServerAddRequest) -> None:
        """Register a File Server workload in APM.

        Args:
            request: FileServerAddRequest describing the file server to register.

        Raises:
            DuplicateWorkloadError: The file server is already enrolled in the same plan on the same backup server.
            AuthenticationError:    Session expired.
            APIError:               Unexpected error from APM.
        """
        body = {
            "requests": [
                {
                    "namespace": request.namespace,
                    "spec": {
                        "workloadType": MachineWorkloadType.FS.name,
                        "workloadName": request.host_ip,
                        "configFs": {
                            "hostIp": request.host_ip,
                            "hostPort": request.host_port,
                            "osName": request.server_type.value,
                            "loginUser": request.login_user,
                            "loginPassword": request.login_password,
                            "remoteSessionList": _build_remote_session_list(request.selectors),
                            "agentlessEnableWindowsVss": request.enable_vss,
                            "connectionTimeout": request.connection_timeout_seconds,
                        },
                        "planRef": {"kind": "BackupPlan", "uid": request.plan_id},
                    },
                    "status": {"hostName": request.host_ip},
                    "triggerBackupAfterCreated": request.trigger_backup,
                }
            ]
        }

        resp = await self._session.post("/api/v1/workload/device_workload/batch", json=body)

        errors: list[dict[str, Any]] = resp.get("errors", []) if resp else []
        if not errors:
            return

        first_error = errors[0]
        error_code: int = first_error.get("errorCode", 0)
        error_message: str = first_error.get("message", "unknown error")

        if error_code == _FS_DUPLICATE_ERROR_CODE:
            _raise_fs_duplicate(request.host_ip, error_code, first_error)
        raise APIError(error_message, error_code=error_code, response_body=first_error)

    async def update_file_server(
        self,
        workload: MachineWorkload,
        request: FileServerUpdateRequest,
    ) -> None:
        """Update the connection settings and backup scope of an existing File Server workload.

        Server type cannot be changed after creation.

        Pass ``None`` for ``login_password`` to keep the existing stored password.

        Args:
            workload: MachineWorkload with workload_type == FS.
            request:  FileServerUpdateRequest describing the desired new state.

        Raises:
            InvalidOperationError:  workload is not an FS workload.
            DuplicateWorkloadError: The updated IP conflicts with another file server in the same plan.
            AuthenticationError:    Session expired.
            APIError:               Unexpected error from APM.
        """
        if workload.workload_type != MachineWorkloadType.FS:
            raise InvalidOperationError(
                "update_file_server() only applies to FS workloads",
                resource_type="Workload",
                resource_id=workload.workload_id,
            )
        raw = await self._session.get(
            f"/api/v1/workload/device_workload/{workload.workload_id}",
            params={"namespace": workload.namespace},
        )
        spec: dict[str, Any] = raw["spec"]
        spec["configFs"] = {
            **(spec.get("configFs") or {}),
            "hostIp": request.host_ip,
            "hostPort": request.host_port,
            "loginUser": request.login_user,
            # APM uses "" as the "keep existing password" sentinel; it does not support empty passwords,
            # so "" is unambiguous. None -> "" here implements the "keep existing" contract.
            "loginPassword": request.login_password if request.login_password is not None else "",
            "remoteSessionList": _build_remote_session_list(request.selectors),
            "agentlessEnableWindowsVss": request.enable_vss,
            "connectionTimeout": request.connection_timeout_seconds,
        }
        try:
            await self._session.put(
                f"/api/v1/workload/device_workload/{workload.workload_id}",
                json={"spec": spec, "namespace": workload.namespace},
            )
        except APIError as exc:
            if exc.error_code == _FS_DUPLICATE_ERROR_CODE:
                _raise_fs_duplicate(request.host_ip, exc.error_code, exc.response_body)
            raise

    async def get_verification_video_url(
        self,
        workload: MachineWorkload,
        version: WorkloadVersion,
    ) -> str:
        """Return a time-limited download URL for the backup verification video of a version.

        Only PS and VM workloads produce verification videos; call this only when
        version.verify_status == VerifyStatus.SUCCESS.

        Args:
            workload: MachineWorkload object (obtained via get()).
            version:  WorkloadVersion whose verify_status is SUCCESS.

        Returns:
            A time-limited HTTPS URL; pass directly to apm.download_file().

        Raises:
            APIError: APM rejected the request or no verification video exists for this version.
        """
        resp = await self._session.post(
            f"/api/v1/version/{version.version_id}/video:download",
            json={
                "workload": {
                    "uid": workload.workload_id,
                    "namespace": workload.namespace,
                },
                "abbParams": {},
            },
            headers=_tunnel_headers(version.namespace),
        )
        return str(resp["url"])

    async def retire(
        self,
        workload: MachineWorkload,
        plan: RetirementPlan,
    ) -> None:
        """Retire a Workload (apply a retirement policy; irreversible).

        Args:
            workload: MachineWorkload object (obtained via get(); must not be already retired).
            plan:     RetirementPlan object (obtained via apm.retirement_plans.get() or get_by_name()).

        Raises:
            InvalidOperationError: The workload is already retired, or APM rejected the
                retirement because the workload is in a state that does not allow it
                (e.g., still initializing).
        """
        _check_not_retired(workload)
        await self._put_plan_change(workload, plan.plan_id)

    async def delete(self, workload: MachineWorkload) -> None:
        """Delete a Machine Workload from APM.

        Args:
            workload: MachineWorkload to delete. Active and retired workloads are both supported.

        Raises:
            InvalidOperationError: APM rejected the delete because the workload is in a state
                that does not allow deletion (e.g., still initializing).
            AuthenticationError:   Session expired.
            APIError:              Unexpected error from APM.
        """
        resp = await self._session.delete(
            "/api/v1/workload/device_workload/batch",
            json={"workloadRefs": [{"uid": workload.workload_id, "namespace": workload.namespace}]},
        )
        _raise_first_batch_error(
            _batch_errors_from_failed(resp),
            workload,
            default_message="Workload delete failed",
            response_body=resp,
        )

    async def change_plan(self, workload: MachineWorkload, plan: ProtectionPlan | RetirementPlan) -> None:
        """Change the Protection Plan or Retirement Plan assigned to a Workload.

        Args:
            workload: MachineWorkload object (obtained via get() or get_by_name()).
            plan:     ProtectionPlan (workload must not be retired, and its category must match
                      the workload's category) or RetirementPlan (workload must already be retired).

        Raises:
            InvalidOperationError: The plan type does not match the workload's retirement state,
                the plan's category does not match the workload's category, or APM rejected
                the change because the workload is in a state that does not allow it
                (e.g., still initializing).
        """
        _check_change_plan_preconditions(workload, plan)
        await self._put_plan_change(workload, plan.plan_id)

    async def _put_plan_change(self, workload: MachineWorkload, plan_id: str) -> None:
        resp = await self._session.put(
            "/api/v1/workload/device_workloads/plan",
            json={
                "nsWorkloadMap": {workload.namespace: {"ids": [workload.workload_id]}},
                "planId": plan_id,
            },
        )
        _raise_first_batch_error(
            _batch_errors_from_failed(resp),
            workload,
            default_message="Workload plan change failed",
            response_body=resp,
        )


# ── FS helpers ────────────────────────────────────────────────────────────


def _parse_selectors(raw: str) -> tuple[FileServerPathSelector, ...]:
    entries: list[dict[str, Any]] = json.loads(raw or "[]")
    if not entries:
        return (FileServerPathSelector(path=""),)
    return tuple(
        FileServerPathSelector(
            path=e.get("selected_path", ""),
            excluded_paths=tuple(e.get("filtered_paths") or []),
        )
        for e in entries
    )


def _build_remote_session_list(selectors: tuple[FileServerPathSelector, ...]) -> str:
    return json.dumps(
        [{"selected_path": s.path, "filtered_paths": list(s.excluded_paths)} for s in selectors],
        separators=(",", ":"),
    )


# ── Response parsers ──────────────────────────────────────────────────────


def _parse_workload(raw: dict[str, Any]) -> MachineWorkload:
    """Convert a single workload object from an API response to the SDK model."""
    spec: dict[str, Any] = raw.get("spec", {})
    status: dict[str, Any] = raw.get("status", {})

    workload_id: str = raw["id"]
    name: str = spec.get("workloadName", "")
    namespace: str = raw.get("namespace", "")

    last_backup_at = _parse_ts_optional(status.get("lastBackupTime"))

    usage_raw = status.get("usage", "0")
    protected_data_bytes = int(usage_raw) if usage_raw else 0

    copy_usage_raw = raw.get("copyDataUsage", "0")
    backup_copy_data_bytes = int(copy_usage_raw) if copy_usage_raw else 0

    api_type: str = spec.get("workloadType", "")
    wl_type = _MACHINE_WORKLOAD_TYPE_MAP.get(api_type, MachineWorkloadType.PC)

    plan_ref: dict[str, Any] = spec.get("planRef") or {}
    plan_name: str = raw.get("planName", "")
    is_retired = plan_ref.get("kind") == "ArchivePlan"
    plan = _build_workload_plan_ref(
        plan_ref.get("uid", ""), plan_name, is_archive=is_retired, category=WorkloadCategory.MACHINE
    )

    server_info: dict[str, Any] = raw.get("backupServerInfo", {}) or {}
    copy_info: dict[str, Any] = raw.get("backupCopyServerInfo", {}) or {}
    backup_server = _build_location_info(server_info)
    backup_copy_destination = _build_location_info(copy_info)

    job_status: str = status.get("jobStatus", "")
    cache: dict[str, Any] = raw.get("cache") or {}
    backup_progress: int | None
    items_backed_up: int | None
    workload_status: WorkloadStatus
    if job_status == "DELETING":
        backup_progress = None
        items_backed_up = None
        workload_status = WorkloadStatus.DELETING
    elif is_retired:
        backup_progress = None
        items_backed_up = None
        workload_status = WorkloadStatus.RETIRED
    elif job_status == "RUNNING":
        workload_status = WorkloadStatus.BACKING_UP
        if api_type == "FS":
            backup_progress = None
            raw_items = cache.get("processedSuccessCount")
            items_backed_up = int(raw_items) if raw_items is not None else None
        else:
            raw_prog = cache.get("progress")
            backup_progress = int(float(raw_prog)) if raw_prog is not None else 0
            items_backed_up = None
    elif job_status == "WAITING_TASK":
        backup_progress = None
        items_backed_up = None
        workload_status = WorkloadStatus.QUEUING
    else:
        backup_progress = None
        items_backed_up = None
        workload_status = _LVR_TO_STATUS.get(
            status.get("latestVersionResult", ""), WorkloadStatus.NO_BACKUPS
        )

    # agent config (PC/PS only)
    config_pc: dict[str, Any] = status.get("configPc") or {}
    config_ps: dict[str, Any] = status.get("configPs") or {}

    # VM: deviceUuid is in spec.configVm
    spec_config_vm: dict[str, Any] = spec.get("configVm") or {}

    if api_type == "PC":
        agent_version: str | None = config_pc.get("versionNumber") or None
        ip_address: str | None = config_pc.get("publicIp") or None
        device_uuid: str | None = config_pc.get("deviceUuid") or None
    elif api_type == "PS":
        agent_version = config_ps.get("versionNumber") or None
        ip_address = config_ps.get("publicIp") or None
        device_uuid = config_ps.get("deviceUuid") or None
    elif api_type == "VM":
        agent_version = None
        ip_address = None
        device_uuid = spec_config_vm.get("deviceUuid") or None
    else:  # FS
        agent_version = None
        ip_address = None
        device_uuid = None

    inventory_name: str | None = raw.get("inventoryName") or None if api_type == "VM" else None
    inventory_type: str | None = raw.get("inventoryType") or None if api_type == "VM" else None
    verify_status = _parse_verify_status(status.get("verifyStatus"), wl_type)

    fs_config: FileServerConfig | None = None
    if api_type == "FS" and spec.get("configFs"):
        cfg: dict[str, Any] = spec["configFs"]
        fs_config = FileServerConfig(
            host_ip=cfg.get("hostIp", ""),
            host_port=int(cfg.get("hostPort", 445)),
            server_type=_FS_OS_TYPE_MAP.get(cfg.get("osName", ""), FileServerType.UNKNOWN),
            login_user=cfg.get("loginUser", ""),
            enable_vss=bool(cfg.get("agentlessEnableWindowsVss", False)),
            connection_timeout_seconds=int(cfg.get("connectionTimeout", 180)),
            selectors=_parse_selectors(cfg.get("remoteSessionList") or "[]"),
        )

    return MachineWorkload(
        workload_id=workload_id,
        name=name,
        category=WorkloadCategory.MACHINE,
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
        workload_type=wl_type,
        agent_version=agent_version,
        verify_status=verify_status,
        device_uuid=device_uuid,
        ip_address=ip_address,
        inventory_name=inventory_name,
        inventory_type=inventory_type,
        fs_config=fs_config,
    )
