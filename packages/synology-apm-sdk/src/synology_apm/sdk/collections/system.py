"""SystemCollection — site-wide information queries."""
from __future__ import annotations

import asyncio
from typing import Any

from .._http import WebAPISession
from ..enums import WorkloadStatType
from ..models.system import (
    SiteInfo,
    SiteStorageStats,
    WorkloadTypeStat,
    WorkloadUsageSummary,
)
from .backup_servers import BackupServerCollection

_WORKLOAD_STAT_TYPE_MAP: dict[str, WorkloadStatType] = {
    "MACHINE_PC":       WorkloadStatType.MACHINE_PC,
    "MACHINE_PS":       WorkloadStatType.MACHINE_PS,
    "MACHINE_VM":       WorkloadStatType.MACHINE_VM,
    "MACHINE_FS":       WorkloadStatType.MACHINE_FS,
    "APPLICATION_M365": WorkloadStatType.M365,
    "APPLICATION_GW":   WorkloadStatType.GWS,
}


class SystemCollection:
    """Collection for site-wide system information.

    Accessed via APMClient.get_site_info(); should not be instantiated directly.
    """

    def __init__(
        self,
        session: WebAPISession,
        backup_servers: BackupServerCollection,
    ) -> None:
        self._session = session
        self._backup_servers = backup_servers

    async def get_site_info(self) -> SiteInfo:
        """Fetch complete APM site information.

        Makes concurrent calls to retrieve license info, cluster info, storage statistics,
        and workload statistics; also scans all backup servers to locate the Primary and
        Secondary Management Servers.

        Returns:
            SiteInfo object containing site_uuid, external_address, port,
            primary_management_server (BackupServer or None), secondary_management_server (BackupServer
            or None), site_storage (SiteStorageStats), and workload_usage (WorkloadUsageSummary).

        Raises:
            AuthenticationError: Session has expired.
            PermissionDeniedError: Insufficient system administration permissions.
        """
        license_raw, site_raw, storage_raw, workload_raw, management_servers = await asyncio.gather(
            self._session.get("/api/v1/license/info"),
            self._session.get("/api/v1/cluster/site_info"),
            self._session.get("/api/v1/infra/backup_server/storage_statistics"),
            self._session.get("/api/v1/dashboard/get_workload_statistics"),
            self._backup_servers._find_management_servers(),
        )
        primary_management_server, secondary_management_server = management_servers

        return SiteInfo(
            site_uuid=license_raw.get("uuid") or "",
            external_address=site_raw.get("externalAddress") or "",
            port=site_raw.get("port") or "",
            primary_management_server=primary_management_server,
            secondary_management_server=secondary_management_server,
            site_storage=_parse_site_storage_stats(storage_raw),
            workload_usage=_parse_workload_usage_summary(workload_raw),
        )


def _parse_site_storage_stats(raw: dict[str, Any]) -> SiteStorageStats:
    return SiteStorageStats(
        logical_backup_data_bytes=int(raw.get("transferBytes") or 0),
        physical_backup_data_bytes=int(raw.get("backupServerUsageBytes") or 0),
    )


def _parse_workload_usage_summary(raw: dict[str, Any]) -> WorkloadUsageSummary:
    stats: list[WorkloadTypeStat] = []
    for item in raw.get("workloadStatistics") or []:
        wtype = _WORKLOAD_STAT_TYPE_MAP.get(item.get("workloadType") or "")
        if wtype is None:
            continue
        total = (
            int(item.get("successCount") or 0)
            + int(item.get("warningCount") or 0)
            + int(item.get("errorCount") or 0)
            + int(item.get("noBackupCount") or 0)
        )
        stats.append(WorkloadTypeStat(
            workload_type=wtype,
            total_count=total,
            protected_data_bytes=int(item.get("dataUsage") or 0),
        ))
    return WorkloadUsageSummary(by_type=tuple(stats))
