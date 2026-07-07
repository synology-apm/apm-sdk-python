"""WorkloadVersion and related location models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..enums import CopyReason, VerifyStatus, VersionCopyStatus, VersionStatus
from .location import LocationInfo


@dataclass(frozen=True)
class VersionLocation:
    """A single lockable version copy within a WorkloadVersion, at a specific storage location.

    Each instance represents one (namespace, id) pair used for lock/unlock and download/export
    operations. A WorkloadVersion may have multiple VersionLocation entries when copies exist
    across different storage locations.

    Attributes:
        namespace:      Backup server namespace; used for lock/unlock API requests.
        location_info:  Display information for the storage location (name, endpoint, vault).
        location_id:    Identifier for this location copy; used for lock/unlock and download/export.
        connection_id:  Connection identifier for download/export operations; None for appliance locations.
    """
    namespace: str
    location_info: LocationInfo
    location_id: str
    connection_id: str | None = None


@dataclass(frozen=True)
class WorkloadVersion:
    """A complete backup version of a Workload at a specific point in time.

    Attributes:
        version_id:         Unique version identifier.
        workload_id:        ID of the Workload this version belongs to.
        namespace:          Namespace of the backup server that holds this version.
        created_at:         Version creation time.
        status:             Backup status of this version.
        execution_id:       Identifier of the backup activity associated with this version.
        locked:             Whether the version is manually locked (prevents deletion by retention rules).
        changed_size_bytes: Amount of changed data in this version (bytes).
        portal_version_id:  Version identifier for M365 export and restore operations; empty when not applicable.
        snapshot_id:        Snapshot identifier for download operations; empty when not applicable.
        verify_status:      Backup verification result for this version (PS/VM only); None for PC/FS.
        locations:          List of all physical storage locations.
        copy_status:        Backup copy status for this version; None when Backup Copy is not configured.
        copy_reason:        Detail reason when copy_status is SKIPPED, RETRY, or FAILED; None otherwise.
    """
    version_id: str
    workload_id: str
    namespace: str
    created_at: datetime
    status: VersionStatus
    execution_id: str
    locked: bool
    changed_size_bytes: int
    portal_version_id: str = ""  # spec.versionId; used in portal export/restore API paths
    snapshot_id: str = ""        # spec.snapshotId; used in portal download API
    verify_status: VerifyStatus | None = field(default=None, kw_only=True)
    locations: list[VersionLocation] = field(default_factory=list)
    copy_status: VersionCopyStatus | None = field(default=None, kw_only=True)
    copy_reason: CopyReason | None = field(default=None, kw_only=True)
