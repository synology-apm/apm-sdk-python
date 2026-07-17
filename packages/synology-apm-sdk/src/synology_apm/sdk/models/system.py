"""SiteInfo, SiteStorageStats, WorkloadTypeStat, and WorkloadUsageSummary data models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..enums import WorkloadStatType
from ._shared import auto_to_dict
from .backup_server import BackupServer


@dataclass(frozen=True)
class SiteStorageStats:
    """Site-wide backup storage statistics.

    Attributes:
        logical_backup_data_bytes: Total logical backup data size (before dedup/compression).
        physical_backup_data_bytes: Physical storage occupied by backup data (after dedup/compression).
    """
    logical_backup_data_bytes: int
    physical_backup_data_bytes: int

    @property
    def backup_data_reduction_bytes(self) -> int:
        """Data reduction achieved by dedup/compression in bytes. Minimum is 0."""
        return max(0, self.logical_backup_data_bytes - self.physical_backup_data_bytes)

    @property
    def backup_data_reduction_ratio(self) -> float:
        """Data reduction ratio as a percentage (float 0–100). Returns 0.0 when logical size is 0."""
        if self.logical_backup_data_bytes == 0:
            return 0.0
        return self.backup_data_reduction_bytes / self.logical_backup_data_bytes * 100

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(
            self,
            extra={
                "backup_data_reduction_bytes": self.backup_data_reduction_bytes,
                "backup_data_reduction_ratio": round(self.backup_data_reduction_ratio, 1),
            },
        )


@dataclass(frozen=True)
class WorkloadTypeStat:
    """Per-type workload count and data usage.

    Attributes:
        workload_type:       Workload type.
        total_count:         Total number of workloads (success + warning + error + no_backup).
        protected_data_bytes: Cumulative protected data size in bytes.
    """
    workload_type: WorkloadStatType
    total_count: int
    protected_data_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class WorkloadUsageSummary:
    """Aggregated workload count and data usage across all workload types.

    Attributes:
        by_type: Per-type statistics tuple.
    """
    by_type: tuple[WorkloadTypeStat, ...]

    @property
    def total_count(self) -> int:
        """Total number of workloads across all types."""
        return sum(s.total_count for s in self.by_type)

    @property
    def total_protected_data_bytes(self) -> int:
        """Total protected data size in bytes across all types."""
        return sum(s.protected_data_bytes for s in self.by_type)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(
            self,
            extra={
                "total_count": self.total_count,
                "total_protected_data_bytes": self.total_protected_data_bytes,
            },
        )


@dataclass(frozen=True)
class SiteInfo:
    """Complete APM site information.

    Attributes:
        site_uuid:                  Site UUID.
        external_address:           External access address.
        port:                       External access port.
        primary_management_server:  Primary Management Server (BackupServerRole.PRIMARY).
                                    None when no management server is found.
        secondary_management_server: Secondary Management Server (BackupServerRole.SECONDARY).
                                    None when not configured.
        site_storage:               Site-wide storage statistics.
        workload_usage:             Workload counts and data usage by type.
    """
    site_uuid: str
    external_address: str
    port: str
    primary_management_server: BackupServer | None
    secondary_management_server: BackupServer | None
    site_storage: SiteStorageStats
    workload_usage: WorkloadUsageSummary

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)
