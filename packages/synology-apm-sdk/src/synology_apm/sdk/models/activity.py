"""Activity data models (backup/restore activity records)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    BackupScope,
    LogLevel,
    M365ExportStatus,
    RestoreActivityStatus,
    RestoreType,
    VerifyStatus,
    WorkloadCategory,
)
from .hypervisor import Hypervisor
from .location import LocationInfo


@dataclass(frozen=True)
class ActivityLogEntry:
    """A single entry in an activity log."""
    timestamp: datetime
    level: LogLevel
    message: str


@dataclass(frozen=True, kw_only=True)
class Activity:
    """Base class for backup and restore activity records (read-only historical view).

    Attributes:
        activity_id:            Unique activity identifier.
        execution_id:           Execution identifier used by ActivityCollection.get().
        namespace:              Namespace of this activity (backup server namespace).
        category:               Business domain of the associated Workload (MACHINE / M365).
        workload_type:          Workload sub-type as recorded in the activity (e.g. MACHINE_VM,
                                MACHINE_FS, ORACLE, M365). Finer-grained than category.
        workload_id:            Associated Workload identifier.
        workload_namespace:     Namespace of the associated Workload; may differ from
                                activity namespace. Used in restore cancel requests.
        workload_name:          Associated Workload display name (snapshot value).
        plan_name:              Name of the applied Protection Plan.
        started_at:             Activity start time.
        finished_at:            Activity end time; None if still in progress.
        duration_seconds:       Duration in seconds; None if not yet completed.
        data_transferred_bytes: Amount of data transferred (bytes); None when not available.
        progress:               Progress percentage (0–100).
        log_entries:            Detailed log entries; populated only by get() / get_by_version().
        processed_success_count: Items backed up or restored successfully; M365 and FS activities only.
        processed_warning_count: Items processed with warnings; M365 and FS activities only.
        processed_error_count:   Items that failed processing; M365 and FS activities only.
    """
    activity_id: str
    execution_id: str
    namespace: str
    category: WorkloadCategory
    workload_type: ActivityWorkloadType
    workload_id: str
    workload_namespace: str
    workload_name: str
    plan_name: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: int | None
    data_transferred_bytes: int | None
    progress: int
    log_entries: tuple[ActivityLogEntry, ...] | None = None
    processed_success_count: int | None = None
    processed_warning_count: int | None = None
    processed_error_count: int | None = None

    @property
    def items_processed(self) -> int | None:
        """Total items processed (success + warning + error); None for machine byte-level activities."""
        if self.processed_success_count is None:
            return None
        return (
            (self.processed_success_count or 0)
            + (self.processed_warning_count or 0)
            + (self.processed_error_count or 0)
        )


@dataclass(frozen=True, kw_only=True)
class BackupActivity(Activity):
    """A backup activity record.

    Attributes:
        status:             Current backup activity status.
        verify_status:      Backup verification result (PS/VM only); None otherwise.
        data_change_bytes:  Changed data size (bytes); None when not available.
        data_deduped_bytes: Actual storage consumed after deduplication (bytes); None when not available.
        backup_scope:       Data scope of the backup; None for M365 activities.
    """
    status: BackupActivityStatus
    verify_status: VerifyStatus | None = None
    data_change_bytes: int | None = None
    data_deduped_bytes: int | None = None
    backup_scope: BackupScope | None = None


@dataclass(frozen=True, kw_only=True)
class RestoreActivity(Activity):
    """A restore activity record.

    Attributes:
        status:               Current restore activity status.
        restore_type:         Method used for this restore; None when unavailable.
        restore_destination:  Target restore destination path or label; None when not set.
        operator:             Username who initiated the restore; None when not recorded.
        version_timestamp:    Timestamp of the backup version used for this restore; None if unavailable.
        restore_from_info:    Source location this restore was performed from; None when not provided.
        destination_path:     Restore destination filesystem path; None when not set.
        destination_inventory: Destination hypervisor entry; None when not applicable.
            Only hostname, address, and host_type are populated (the
            remaining Hypervisor fields are empty/zero placeholders, not a
            full inventory record).
    """
    status: RestoreActivityStatus
    restore_type: RestoreType | None = None
    restore_destination: str | None = None
    operator: str | None = None
    version_timestamp: datetime | None = None
    restore_from_info: LocationInfo | None = None
    destination_path: str | None = None
    destination_inventory: Hypervisor | None = None


@dataclass(frozen=True)
class M365ExportActivity:
    """An M365 mailbox PST export task record (Exchange or Group mailbox).

    Attributes:
        activity_id:        Activity UUID (unique identifier for cancel and download operations).
        execution_id:       Internal execution identifier; used by SDK methods, not exposed to users.
        namespace:          Namespace of the backup server where this activity resides.
        workload_id:        ID of the associated M365 workload.
        workload_namespace: Namespace of the associated M365 workload.
        source_name:        Source folder or item name.
        is_archive_mail:    True if this is an archive mailbox export.
        status:             Current export task status.
        started_at:         Task start time; None if not yet started.
        finished_at:        Task end time; None if still in progress or status is PREPARING.
        version_timestamp:  Timestamp of the backup version being exported; None if unavailable.
    """
    activity_id: str
    execution_id: str
    namespace: str
    workload_id: str
    workload_namespace: str
    source_name: str
    is_archive_mail: bool
    status: M365ExportStatus
    started_at: datetime | None
    finished_at: datetime | None
    version_timestamp: datetime | None = None
