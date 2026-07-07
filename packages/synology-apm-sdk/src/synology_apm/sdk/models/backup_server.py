"""BackupServer data model."""
from __future__ import annotations

from dataclasses import dataclass

from ..enums import BackupServerRole, BackupServerType, ServerStatus
from .location import LocationInfo
from .tiering_plan import TieringStatus


@dataclass(frozen=True)
class BackupServer:
    """A backup server in the APM cluster (ActiveProtect Appliance or NAS).

    Attributes:
        backup_server_id: Unique backup server identifier.
        namespace: Namespace of this server (matches workload.namespace).
        server_type: Hardware type — DP (ActiveProtect Appliance) or NAS.
        name: Display name.
        hostname: IP address or hostname.
        model: Device model (e.g. "DP320").
        system_version: System version string for DP servers (e.g. "APM 1.2-71845"). None for NAS servers.
        status: Server health status.
        is_updating: True when a firmware update is in progress.
        serial: Serial number.
        storage_total_bytes: Total storage capacity in bytes. None when data is unavailable.
        storage_used_bytes: Total disk space used in bytes. None when data is unavailable.
        logical_backup_data_bytes: Logical backup data size in bytes (before dedup/compression).
            None when data is unavailable.
        physical_backup_data_bytes: Physical storage occupied by backup data in bytes (after dedup/compression).
            None when data is unavailable.
        role: Cluster role of this server. PRIMARY = Primary Management Server;
            SECONDARY = Secondary Management Server; None = regular backup server.
        description: Administrator-supplied description for the server. Empty string when not set.
        tiering_plan_name: Name of the tiering plan applied to this server. None when no plan is assigned.
        tiering_plan_destination: Remote storage destination of the tiering plan. None when no plan is
            assigned or destination details are unavailable.
        tiering_status: Current tiering operation status for this server. None when no plan is assigned
            or status is unavailable.
    """
    backup_server_id: str
    namespace: str
    server_type: BackupServerType
    name: str
    hostname: str
    model: str
    system_version: str | None
    status: ServerStatus
    is_updating: bool
    serial: str
    storage_total_bytes: int | None
    storage_used_bytes: int | None
    logical_backup_data_bytes: int | None
    physical_backup_data_bytes: int | None
    role: BackupServerRole | None = None
    description: str = ""
    tiering_plan_name: str | None = None
    tiering_plan_destination: LocationInfo | None = None
    tiering_status: TieringStatus | None = None

    @property
    def storage_usage_pct(self) -> float:
        """Storage usage percentage (float 0–100). Returns 0.0 when storage data is unavailable or total is 0."""
        if self.storage_total_bytes is None or self.storage_used_bytes is None:
            return 0.0
        if self.storage_total_bytes == 0:
            return 0.0
        return self.storage_used_bytes / self.storage_total_bytes * 100

    @property
    def backup_data_reduction_bytes(self) -> int | None:
        """Data reduction achieved by dedup/compression in bytes. Returns None when unavailable; minimum is 0."""
        if self.logical_backup_data_bytes is None or self.physical_backup_data_bytes is None:
            return None
        return max(0, self.logical_backup_data_bytes - self.physical_backup_data_bytes)

    @property
    def backup_data_reduction_ratio(self) -> float:
        """Data reduction ratio as a percentage (float 0–100). Returns 0.0 when unavailable or logical size is 0."""
        reduction = self.backup_data_reduction_bytes
        if reduction is None or self.logical_backup_data_bytes is None or self.logical_backup_data_bytes == 0:
            return 0.0
        return reduction / self.logical_backup_data_bytes * 100
