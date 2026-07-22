"""M365 mail export — Exchange mailbox and Group mailbox PST export operations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .._http import WebAPISession
from ..enums import M365ExportStatus
from ..exceptions import APIError, ResourceNotFoundError, ResourceNotReadyError
from ..models._shared import auto_to_dict
from ..models.activity import M365ExportActivity
from ..models.version import VersionLocation, WorkloadVersion
from ..models.workload import M365GroupInfo, M365UserInfo, M365Workload
from ._shared import ListResult, _parse_ts_optional, _tunnel_headers


@dataclass(frozen=True)
class M365ExportStartResult:
    """Return value of ExchangeExportCollection.start() and GroupExportCollection.start().

    Attributes:
        execution_id:      Internal export task execution identifier; used by SDK methods only.
        ready_to_download: If True, the export is immediately available; call
                           get_download_url_by_ready_result() directly. If False, poll
                           get_activity_by_result() until status is READY_TO_DOWNLOAD,
                           then call get_download_url_by_activity().
        export_name:       PST filename used for this export.
        location:          Storage location from which this export was started.
        workload:          The M365Workload passed to start().
        version:           The WorkloadVersion passed to start().
    """
    execution_id: str
    ready_to_download: bool
    export_name: str
    location: VersionLocation
    workload: M365Workload
    version: WorkloadVersion

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(
            self,
            exclude=frozenset({"execution_id", "location", "workload", "version"}),
            extra={"workload_id": self.workload.workload_id, "version_id": self.version.version_id},
        )


def _resolve_version_location(
    version: WorkloadVersion,
    location_id: str | None,
) -> VersionLocation:
    """Return the VersionLocation for download/export operations.

    If location_id is None, returns the first location in version.locations.
    Raises APIError when the version has no locations.
    Raises ResourceNotFoundError when location_id is not found.
    """
    if not version.locations:
        raise APIError(
            f"Version '{version.version_id}' has no location data; cannot determine download target.",
            response_body=None,
        )
    if location_id is None:
        return version.locations[0]
    match = next((loc for loc in version.locations if loc.location_id == location_id), None)
    if match is None:
        raise ResourceNotFoundError(
            f"Location '{location_id}' not found in version '{version.version_id}'.",
            resource_type="VersionLocation",
            resource_id=location_id,
        )
    return match


_EXPORT_STATUS_MAP: dict[str, M365ExportStatus] = {
    "READY_TO_DOWNLOAD": M365ExportStatus.READY_TO_DOWNLOAD,
    "SUCCESS":           M365ExportStatus.DOWNLOADED,
    "CANCELED":          M365ExportStatus.CANCELED,
    "PREPARING":         M365ExportStatus.PREPARING,
    "EXPIRED":           M365ExportStatus.EXPIRED,
    "FAILED":            M365ExportStatus.FAILED,
    "WARNING":           M365ExportStatus.WARNING,
}


def _parse_export_activity(raw: dict[str, Any]) -> M365ExportActivity:
    """Convert a single object from the export activities list API to the SDK model."""
    spec: dict[str, Any] = raw.get("spec", {})
    status_obj: dict[str, Any] = raw.get("status", {})
    workload_ref: dict[str, Any] = spec.get("workload", {})

    status_raw = status_obj.get("exportStatus", "")
    status = _EXPORT_STATUS_MAP.get(status_raw, M365ExportStatus.UNKNOWN)

    started_at = _parse_ts_optional(status_obj.get("startTime"))
    finished_at = (
        None if status == M365ExportStatus.PREPARING
        else _parse_ts_optional(status_obj.get("endTime"))
    )

    return M365ExportActivity(
        activity_id=raw.get("uid", ""),
        execution_id=spec.get("executionId", ""),
        namespace=raw.get("namespace", ""),
        workload_id=workload_ref.get("uid", ""),
        workload_namespace=workload_ref.get("namespace", ""),
        source_name=(
            "Entire archive mailbox" if spec.get("isRootFolder") and spec.get("isArchiveMail")
            else "Entire mailbox" if spec.get("isRootFolder")
            else spec.get("sourceName", "")
        ),
        is_archive_mail=bool(spec.get("isArchiveMail", False)),
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        version_timestamp=_parse_ts_optional(spec.get("versionTimestamp")),
    )


class _BaseM365ExportCollection:
    """Shared implementation for Exchange and Group mailbox PST export.

    Not part of the public SDK API; use ExchangeExportCollection or GroupExportCollection.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(
        self,
        workload: M365Workload,
        limit: int = 500,
        offset: int = 0,
    ) -> ListResult[M365ExportActivity]:
        """List export tasks for a workload.

        Args:
            workload: M365Workload (Exchange or Group, from apm.m365.workloads.get() or get_by_name()).
            limit:    Maximum records to return (default 500).
            offset:   Pagination start offset (default 0).

        Returns:
            (list of M365ExportActivity, total count)
        """
        raw = await self._session.get(
            "/portal/api/v1/portal/activity/export/activities",
            params={
                "offset": offset,
                "limit": limit,
                "workload.namespace": workload.namespace,
                "workload.uid": workload.workload_id,
            },
            headers=_tunnel_headers(workload.namespace),
        )
        activities = [_parse_export_activity(a) for a in raw.get("activities", [])]
        return ListResult(activities, raw.get("total"))

    async def cancel(self, activity: M365ExportActivity) -> None:
        """Cancel an in-progress export task.

        Args:
            activity: M365ExportActivity obtained from list().

        Raises:
            APIError: APM rejected the cancel request.
        """
        await self._session.post(
            "/portal/api/v1/portal/microsoft365/cancel/export",
            json={
                "workload": {
                    "uid": activity.workload_id,
                    "namespace": activity.workload_namespace,
                },
                "executionId": activity.execution_id,
            },
            headers=_tunnel_headers(activity.namespace),
        )

    async def get_download_url_by_activity(
        self,
        activity: M365ExportActivity,
    ) -> str:
        """Retrieve the time-limited download URL for a completed export.

        Use this when downloading a previously completed export (status READY_TO_DOWNLOAD).
        For exports started in the current session, prefer get_download_url_by_ready_result().

        Args:
            activity: M365ExportActivity obtained from list() with status READY_TO_DOWNLOAD.

        Returns:
            Full HTTPS download URL for the PST file.

        Raises:
            ResourceNotReadyError: Activity status is PREPARING; export is not yet available.
            APIError: URL generation failed.
        """
        if activity.status == M365ExportStatus.PREPARING:
            raise ResourceNotReadyError(
                "Export is still being prepared; poll list() until status changes from PREPARING."
            )
        result = await self._session.post(
            "/portal/api/v1/portal/entries:download",
            json={
                "workload": {
                    "uid": activity.workload_id,
                    "namespace": activity.workload_namespace,
                },
                "abmParams": {
                    "type": "MAIL",
                    "mailParam": {
                        "type": "MAIL",
                        "downloadMailParam": {"taskExecutionId": activity.execution_id},
                    },
                },
            },
            headers=_tunnel_headers(activity.namespace),
        )
        return str(result.get("url", ""))

    async def get_download_url_by_ready_result(
        self,
        result: M365ExportStartResult,
    ) -> str:
        """Retrieve the time-limited download URL for an immediately ready export.

        Use this immediately after start() when M365ExportStartResult.ready_to_download is True.
        If ready_to_download is False, poll get_activity_by_result() until status is
        READY_TO_DOWNLOAD, then call get_download_url_by_activity() instead.

        Args:
            result: M365ExportStartResult from start() with ready_to_download=True.

        Returns:
            Full HTTPS download URL for the PST file.

        Raises:
            ResourceNotReadyError: result.ready_to_download is False.
            APIError: URL generation failed.
        """
        if not result.ready_to_download:
            raise ResourceNotReadyError(
                "Export is not ready to download; poll get_activity_by_result() until "
                "status is READY_TO_DOWNLOAD, then call get_download_url_by_activity()."
            )
        location = result.location
        abm_params: dict[str, Any] = {
            "type": "MAIL",
            "snapshotId": result.version.snapshot_id,
            "versionId": result.version.portal_version_id,
            "exportName": result.export_name,
            "mailParam": {
                "type": "MAIL",
                "downloadMailParam": {"taskExecutionId": result.execution_id},
            },
        }
        if location.connection_id:
            abm_params["connectionId"] = location.connection_id
        raw = await self._session.post(
            "/portal/api/v1/portal/entries:download",
            json={
                "id": location.location_id,
                "workload": {
                    "uid": result.workload.workload_id,
                    "namespace": result.workload.namespace,
                },
                "abmParams": abm_params,
            },
            headers=_tunnel_headers(location.namespace),
        )
        return str(raw.get("url", ""))

    async def get_activity_by_result(
        self,
        result: M365ExportStartResult,
    ) -> M365ExportActivity | None:
        """Find the M365ExportActivity corresponding to a started export.

        Searches the activity list by matching both the backup server namespace and
        the internal execution ID. Returns None when the activity is not yet visible
        in the list (may occur immediately after start()).

        Args:
            result: M365ExportStartResult returned by start().

        Returns:
            The matching M365ExportActivity, or None if not yet available.
        """
        activities, _ = await self.list(result.workload)
        return next(
            (
                a for a in activities
                if a.namespace == result.location.namespace
                and a.execution_id == result.execution_id
            ),
            None,
        )

    async def _fetch_root_folder_id(
        self,
        workload: M365Workload,
        version: WorkloadVersion,
        location: VersionLocation,
        *,
        is_group: bool,
        is_archive: bool,
    ) -> str:
        """Fetch the root mailbox folder ID for the given version."""
        folders_raw = await self._session.get(
            f"/portal/api/v1/portal/microsoft365/workloads"
            f"/{workload.namespace}/{workload.workload_id}"
            f"/exchange/mail/versions/{version.portal_version_id}/folders",
            params={
                "connectionId": location.connection_id or "",
                "isArchive": "true" if is_archive else "false",
                "isGroup": "true" if is_group else "false",
            },
            headers=_tunnel_headers(location.namespace),
        )
        folder_list = folders_raw.get("folderList", [])
        if not folder_list:
            raise ResourceNotFoundError(
                "No mailbox folders found for this version.",
                resource_type="MailboxFolder",
                resource_id=version.version_id,
            )
        return str(folder_list[0].get("id", ""))


class ExchangeExportCollection(_BaseM365ExportCollection):
    """Collection interface for Exchange mailbox PST export operations.

    Accessed via APMClient.m365.exchange_export; should not be instantiated directly.

    All methods operate on Exchange workloads only. The workload must be obtained
    via APMClient.m365.workloads.get() before calling methods on this collection.
    """

    async def start(
        self,
        workload: M365Workload,
        version: WorkloadVersion,
        *,
        archive_mailbox: bool = False,
        export_name: str | None = None,
        location_id: str | None = None,
    ) -> M365ExportStartResult:
        """Start an Exchange mailbox PST export.

        Args:
            workload:        Exchange M365Workload (from apm.m365.workloads.get() or get_by_name()).
            version:         Backup version to export (from list_versions / get_latest_version).
            archive_mailbox: If True, export the archive mailbox instead of the primary mailbox.
            export_name:     Filename shown in the browser download dialog (e.g. "alice_20260514.pst").
                             Auto-generated from the workload UPN and today's date if omitted.
            location_id:     VersionLocation.location_id of the specific copy to export from; defaults to the
                             first available location. The selected location is embedded in the returned
                             M365ExportStartResult.

        Returns:
            M365ExportStartResult with execution_id, ready_to_download, and export_name.
            If ready_to_download is True, call get_download_url_by_ready_result() immediately.
            If False, poll get_activity_by_result() until status is READY_TO_DOWNLOAD,
            then call get_download_url_by_activity().

        Raises:
            ResourceNotFoundError: No mailbox folders found for this version, or location_id not found.
            APIError: Version has no location data, or APM rejected the export request.
        """
        if not version.portal_version_id:
            raise ResourceNotFoundError(
                "Version has no portal_version_id; cannot determine export path.",
                resource_type="WorkloadVersion",
                resource_id=version.version_id,
            )

        location = _resolve_version_location(version, location_id)
        root_folder_id = await self._fetch_root_folder_id(
            workload, version, location, is_group=False, is_archive=archive_mailbox,
        )

        if export_name is None:
            upn = ""
            if isinstance(workload.info, M365UserInfo):
                upn = workload.info.user_principal_name
            if not upn:
                upn = workload.name
            date_str = date.today().strftime("%Y%m%d")
            mailbox_label = "archive mailbox" if archive_mailbox else "mailbox"
            export_name = f"{upn}'s {mailbox_label}_{date_str}.pst"

        mail_export_option = "ARCHIVE_USER" if archive_mailbox else "USER"
        body: dict[str, Any] = {
            "exportName": export_name,
            "mailFolderList": [{"id": root_folder_id}],
            "filter": {},
            "mailFolderIdToPathMap": {},
            "mailExportOption": mail_export_option,
            "mailExportType": "PST",
            "isArchive": archive_mailbox,
            "isGroup": False,
        }
        if location.connection_id:
            body["connectionId"] = location.connection_id
        raw = await self._session.post(
            f"/portal/api/v1/portal/microsoft365/workloads"
            f"/{workload.namespace}/{workload.workload_id}"
            f"/exchange/mail/versions/{version.portal_version_id}/start_export",
            json=body,
            headers=_tunnel_headers(location.namespace),
        )
        return M365ExportStartResult(
            execution_id=str(raw.get("taskExecutionId", "")),
            ready_to_download=not bool(raw.get("provideLink", True)),
            export_name=export_name,
            location=location,
            workload=workload,
            version=version,
        )


class GroupExportCollection(_BaseM365ExportCollection):
    """Collection interface for Group mailbox PST export operations.

    Accessed via APMClient.m365.group_export; should not be instantiated directly.

    All methods operate on Group workloads only. The workload must be obtained
    via APMClient.m365.workloads.get() before calling methods on this collection.
    Group mailboxes do not have an archive mailbox.
    """

    async def start(
        self,
        workload: M365Workload,
        version: WorkloadVersion,
        *,
        export_name: str | None = None,
        location_id: str | None = None,
    ) -> M365ExportStartResult:
        """Start a Group mailbox PST export.

        Args:
            workload:    Group M365Workload (from apm.m365.workloads.get() or get_by_name()).
            version:     Backup version to export (from list_versions / get_latest_version).
            export_name: Filename shown in the browser download dialog (e.g. "group_20260514.pst").
                         Auto-generated from the workload name and today's date if omitted.
            location_id: VersionLocation.location_id of the specific copy to export from; defaults to the
                         first available location. The selected location is embedded in the
                         returned M365ExportStartResult.

        Returns:
            M365ExportStartResult with execution_id, ready_to_download, and export_name.
            If ready_to_download is True, call get_download_url_by_ready_result() immediately.
            If False, poll get_activity_by_result() until status is READY_TO_DOWNLOAD,
            then call get_download_url_by_activity().

        Raises:
            ResourceNotFoundError: No mailbox folders found for this version, or location_id not found.
            APIError: Version has no location data, or APM rejected the export request.
        """
        if not version.portal_version_id:
            raise ResourceNotFoundError(
                "Version has no portal_version_id; cannot determine export path.",
                resource_type="WorkloadVersion",
                resource_id=version.version_id,
            )

        location = _resolve_version_location(version, location_id)
        root_folder_id = await self._fetch_root_folder_id(
            workload, version, location, is_group=True, is_archive=False,
        )

        if export_name is None:
            mail = ""
            if isinstance(workload.info, M365GroupInfo):
                mail = workload.info.mail
            if not mail:
                mail = workload.name
            date_str = date.today().strftime("%Y%m%d")
            export_name = f"{mail}'s group_mailbox_{date_str}.pst"

        body: dict[str, Any] = {
            "exportName": export_name,
            "mailFolderList": [{"id": root_folder_id}],
            "filter": {},
            "mailFolderIdToPathMap": {},
            "mailExportOption": "USER",
            "mailExportType": "PST",
            "isArchive": False,
            "isGroup": True,
        }
        if location.connection_id:
            body["connectionId"] = location.connection_id
        raw = await self._session.post(
            f"/portal/api/v1/portal/microsoft365/workloads"
            f"/{workload.namespace}/{workload.workload_id}"
            f"/exchange/mail/versions/{version.portal_version_id}/start_export",
            json=body,
            headers=_tunnel_headers(location.namespace),
        )
        return M365ExportStartResult(
            execution_id=str(raw.get("taskExecutionId", "")),
            ready_to_download=not bool(raw.get("provideLink", True)),
            export_name=export_name,
            location=location,
            workload=workload,
            version=version,
        )
