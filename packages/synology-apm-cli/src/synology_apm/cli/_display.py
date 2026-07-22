"""CLI display helpers — formatters, table renderers, and shared display tables."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from rich.console import Console

from synology_apm.cli.output import cell, new_table, to_local_iso
from synology_apm.sdk import (
    ActivityLogEntry,
    APMActivityLogType,
    BackupActivity,
    BackupActivityStatus,
    BackupScope,
    CopyReason,
    FileServerType,
    HypervisorType,
    LocationInfo,
    LogLevel,
    M365ExportStatus,
    M365WorkloadType,
    MachineBackupWindow,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    PlanBackupCopyStatus,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorageStatus,
    RemoteStorageType,
    RestoreActivity,
    RestoreActivityStatus,
    RestoreType,
    RetentionType,
    RetirementRetentionPolicy,
    ScheduleFrequency,
    ServerStatus,
    TieringStatus,
    VerifyStatus,
    VersionCopyStatus,
    VersionLocation,
    VersionStatus,
    WeekDay,
    Workload,
    WorkloadCategory,
    WorkloadStatType,
    WorkloadStatus,
    WorkloadVersion,
)

_COPY_PROGRESS_STATUSES: frozenset[VersionCopyStatus] = frozenset({
    VersionCopyStatus.WAITING, VersionCopyStatus.SCHEDULED,
    VersionCopyStatus.IN_PROGRESS, VersionCopyStatus.RETRY, VersionCopyStatus.FAILED,
})

BACKUP_SCOPE_LABELS: dict[BackupScope, str] = {
    BackupScope.ENTIRE_DEVICE_WITH_EXT_DRIVES: "Entire Device with External Drives",
    BackupScope.ENTIRE_DEVICE:                 "Entire Device",
    BackupScope.VOLUME:                        "Volume",
    BackupScope.FILE:                          "File / Folder",
}

_VERSION_STATUS_DISPLAY: dict[VersionStatus, str] = {
    VersionStatus.SUCCESS:       "[green]✓ Success[/green]",
    VersionStatus.FAILED:        "[red]✗ Failed[/red]",
    VersionStatus.PARTIAL:       "[yellow]⚠ Partial[/yellow]",
    VersionStatus.BACKING_UP:    "[blue]⠸ Backing up[/blue]",
    VersionStatus.CANCELED:      "[bright_black]⊘ Canceled[/bright_black]",
    VersionStatus.PAUSED:        "[bright_black]‖ Paused[/bright_black]",
    VersionStatus.DELETING:      "[bright_black]Deleting[/bright_black]",
    VersionStatus.DELETE_FAILED: "[red]Delete Failed[/red]",
    VersionStatus.NO_BACKUPS:    "-",
}

_VERIFY_STATUS_DISPLAY: dict[VerifyStatus, str] = {
    VerifyStatus.SUCCESS:       "[green]✓ Success[/green]",
    VerifyStatus.FAILED:        "[red]✗ Failed[/red]",
    VerifyStatus.PARTIAL:       "[yellow]⚠ Partial[/yellow]",
    VerifyStatus.CANCELED:      "[bright_black]⊘ Canceled[/bright_black]",
    VerifyStatus.VERIFYING:     "[blue]⠸ Verifying[/blue]",
    VerifyStatus.WAITING:       "[blue]⠸ Waiting[/blue]",
    VerifyStatus.NOT_SUPPORTED: "[bright_black]Unable to perform[/bright_black]",
    VerifyStatus.NOT_ENABLED:   "[bright_black]Not enabled[/bright_black]",
}

_VERSION_COPY_STATUS_DISPLAY: dict[VersionCopyStatus, str] = {
    VersionCopyStatus.COMPLETED:   "[green]Completed[/green]",
    VersionCopyStatus.NOT_ENABLED: "[bright_black]Not enabled[/bright_black]",
    VersionCopyStatus.WAITING:     "[blue]⠸ Waiting[/blue]",
    VersionCopyStatus.SCHEDULED:   "[blue]⠸ Waiting for schedule[/blue]",
    VersionCopyStatus.IN_PROGRESS: "[blue]⠸ Copying[/blue]",
    VersionCopyStatus.SKIPPED:     "[yellow]⊘ Skipped[/yellow]",
    VersionCopyStatus.RETRY:       "[yellow]⠸ Waiting for retry[/yellow]",
    VersionCopyStatus.FAILED:      "[red]✗ Unable to perform[/red]",
}

_WORKLOAD_STATUS_DISPLAY: dict[WorkloadStatus, str] = {
    WorkloadStatus.QUEUING:   "[blue]⠸ Waiting for Backup[/blue]",
    WorkloadStatus.BACKING_UP: "[blue]⠸ Backing up[/blue]",
    WorkloadStatus.SUCCESS:   "[green]✓ Success[/green]",
    WorkloadStatus.FAILED:    "[red]✗ Failed[/red]",
    WorkloadStatus.PARTIAL:   "[yellow]⚠ Partial[/yellow]",
    WorkloadStatus.CANCELED:  "[bright_black]⊘ Canceled[/bright_black]",
    WorkloadStatus.DELETING:  "[bright_black]⟳ Deleting[/bright_black]",
    WorkloadStatus.RETIRED:   "[bright_black]— Retired[/bright_black]",
    WorkloadStatus.NO_BACKUPS: "[bright_black]— No Backups[/bright_black]",
}

_BACKUP_ACTIVITY_STATUS_DISPLAY: dict[BackupActivityStatus, str] = {
    BackupActivityStatus.QUEUING:   "[blue]⠸ Waiting for Backup[/blue]",
    BackupActivityStatus.BACKING_UP: "[blue]⠸ Backing up[/blue]",  # base; enriched at runtime (kept for set-coverage)
    BackupActivityStatus.CANCELING: "[yellow]⊗ Canceling[/yellow]",
    BackupActivityStatus.SUCCESS:   "[green]✓ Success[/green]",
    BackupActivityStatus.FAILED:    "[red]✗ Failed[/red]",
    BackupActivityStatus.PARTIAL:   "[yellow]⚠ Partial[/yellow]",
    BackupActivityStatus.CANCELED:  "[bright_black]⊘ Canceled[/bright_black]",
}

_RESTORE_ACTIVITY_STATUS_DISPLAY: dict[RestoreActivityStatus, str] = {
    RestoreActivityStatus.PREPARING:           "[blue]⠸ Preparing[/blue]",
    # base; enriched at runtime (kept for set-coverage)
    RestoreActivityStatus.RESTORING:           "[blue]⠸ Restoring[/blue]",
    RestoreActivityStatus.CANCELING:           "[yellow]⊗ Canceling[/yellow]",
    RestoreActivityStatus.READY_FOR_MIGRATE:   "[blue]⠸ Ready to migrate[/blue]",
    RestoreActivityStatus.MIGRATE_VM_MANUALLY: "[yellow]⚠ Manual migration required[/yellow]",
    RestoreActivityStatus.MIGRATING:           "[blue]⠸ Migrating[/blue]",
    RestoreActivityStatus.SUCCESS:             "[green]✓ Success[/green]",
    RestoreActivityStatus.FAILED:              "[red]✗ Failed[/red]",
    RestoreActivityStatus.PARTIAL:             "[yellow]⚠ Partial[/yellow]",
    RestoreActivityStatus.CANCELED:            "[bright_black]⊘ Canceled[/bright_black]",
}

_LOG_LEVEL_DISPLAY: dict[LogLevel, str] = {
    LogLevel.INFO:    "[bright_black]INFO   [/bright_black]",
    LogLevel.WARNING: "[yellow]WARNING[/yellow]",
    LogLevel.ERROR:   "[red]ERROR  [/red]",
}

# Word-style LogLevel labels for the `log <kind> list` tables — intentionally
# distinct from the padded _LOG_LEVEL_DISPLAY style used by render_log_table's
# activity-log entries; do not merge the two.
_SERVER_LOG_LEVEL_DISPLAY: dict[LogLevel, str] = {
    LogLevel.INFO:    "Information",
    LogLevel.WARNING: "[yellow]Warning[/yellow]",
    LogLevel.ERROR:   "[red]Error[/red]",
}

_ACTIVITY_LOG_TYPE_DISPLAY: dict[APMActivityLogType, str] = {
    APMActivityLogType.PROTECTION:  "Data protection",
    APMActivityLogType.SYSTEM:      "System management",
    APMActivityLogType.DATA_ACCESS: "Data access",
}


def fmt_server_log_level(level: LogLevel) -> str:
    """Word-style severity label for the server log list tables."""
    return _SERVER_LOG_LEVEL_DISPLAY.get(level, level.value)


def fmt_activity_log_type(log_type: APMActivityLogType | None) -> str:
    """Display label for an activity log type; '-' when absent."""
    if log_type is None:
        return "-"
    return _ACTIVITY_LOG_TYPE_DISPLAY.get(log_type, "-")

_COPY_REASON_MESSAGES: dict[CopyReason, str] = {
    CopyReason.DESTINATION_DISCONNECTED: "Destination is disconnected.",
    CopyReason.VERSION_INCOMPATIBLE:     "Backup server version is incompatible.",
    CopyReason.DESTINATION_UPDATING:     "Destination is updating.",
    CopyReason.AUTH_FAILED:              "Authentication error occurred.",
    CopyReason.STORAGE_FULL:             "Storage is full.",
    CopyReason.QUOTA_EXCEEDED:           "Server count exceeds the limit, or there are connection issues.",
    CopyReason.INFRASTRUCTURE_ERROR:     (
        "Issue detected. The system will retry every 24 hours. "
        "This error status may persist for a while, even after the issue is resolved."
    ),
    CopyReason.VAULT_SETUP_ISSUE:        (
        "Improper vault setup. This may be caused by a missing shared folder or incorrect vault settings."
    ),
    CopyReason.DATA_CORRUPTED:           "Data on the destination is corrupted.",
    CopyReason.DESTINATION_MISSING:      "Destination is missing.",
    CopyReason.VOLUME_READONLY:          "Destination volume is read-only.",
    CopyReason.CERT_AUTH_FAILED:         "Certificate authentication failed.",
    CopyReason.NO_DESTINATION:           "Destination is not set.",
    CopyReason.SKIPPED_DB_OUTDATED:      (
        "The backup copy destination is running an outdated APM version. "
        "Update the system before performing backup copies."
    ),
    CopyReason.SKIPPED_NAS_ENCRYPTED:    (
        "The backup location is encrypted, preventing the execution of backup copies."
    ),
    CopyReason.SKIPPED_NAS_TO_EXTERNAL:  (
        "Backup data on a Synology NAS cannot be copied to remote storage."
    ),
    CopyReason.SKIPPED_SOURCE_EQ_DEST:   (
        "The backup copy destination is the same as the assigned backup server, "
        "so no backup copies will be created."
    ),
}

# ── Command-level enum display tables (migrated from commands/*.py) ───────────────

_SERVER_STATUS_DISPLAY: dict[ServerStatus, str] = {
    ServerStatus.HEALTHY:      "[green]● Healthy[/green]",
    ServerStatus.WARNING:      "[yellow]⚠ Warning[/yellow]",
    ServerStatus.CRITICAL:     "[red]✗ Critical[/red]",
    ServerStatus.DISCONNECTED: "[dim]○ Disconnected[/dim]",
    ServerStatus.SYNCING:      "[cyan]⟳ Syncing...[/cyan]",
}

_REMOTE_STORAGE_STATUS_DISPLAY: dict[RemoteStorageStatus, str] = {
    RemoteStorageStatus.CONNECTED:         "[green]● Connected[/green]",
    RemoteStorageStatus.AUTH_FAILED:       "[red]✗ Authentication Failed[/red]",
    RemoteStorageStatus.DISCONNECTED:      "[dim]○ Disconnected[/dim]",
    RemoteStorageStatus.UNKNOWN:           "[dim]? Unknown[/dim]",
    RemoteStorageStatus.VAULT_NOT_MOUNTED: "[yellow]⚠ Vault Not Mounted[/yellow]",
    RemoteStorageStatus.DATA_CORRUPTED:    "[red]✗ Vault Missing[/red]",
    RemoteStorageStatus.UNMANAGED_CATALOG: "[yellow]⚠ Unmanaged Catalog[/yellow]",
}

_REMOTE_STORAGE_TYPE_DISPLAY: dict[RemoteStorageType, str] = {
    RemoteStorageType.ACTIVE_PROTECT_VAULT: "ActiveProtect Vault",
    RemoteStorageType.C2_OBJECT_STORAGE:    "Synology C2 Object Storage",
    RemoteStorageType.AMAZON_S3:            "Amazon S3",
    RemoteStorageType.AMAZON_S3_CHINA:      "Amazon S3 (China)",
    RemoteStorageType.WASABI:               "Wasabi Cloud Object Storage",
    RemoteStorageType.AZURE_BLOB:           "Azure Blob Storage",
    RemoteStorageType.AZURE_BLOB_CHINA:     "Azure Blob Storage (China)",
    RemoteStorageType.S3_COMPATIBLE:        "S3 Compatible",
    RemoteStorageType.UNKNOWN:              "Unknown",
}

_HYPERVISOR_TYPE_DISPLAY: dict[HypervisorType, str] = {
    HypervisorType.VSPHERE_ESXI:            "VMware vSphere (ESXi)",
    HypervisorType.VSPHERE_VCENTER:         "VMware vSphere (vCenter)",
    HypervisorType.HYPERV_STANDALONE:       "Microsoft Hyper-V (Standalone)",
    HypervisorType.HYPERV_SCVMM:           "Microsoft Hyper-V (SCVMM)",
    HypervisorType.HYPERV_FAILOVER_CLUSTER: "Microsoft Hyper-V (Failover Cluster)",
    HypervisorType.UNKNOWN:                 "Unknown",
}

_WORKLOAD_STAT_LABEL: dict[WorkloadStatType, str] = {
    WorkloadStatType.MACHINE_PC: "PC",
    WorkloadStatType.MACHINE_PS: "PS",
    WorkloadStatType.MACHINE_VM: "VM",
    WorkloadStatType.MACHINE_FS: "FS",
    WorkloadStatType.M365:       "M365",
    WorkloadStatType.GWS:        "GWS",
}

_WORKLOAD_TYPE_DISPLAY: dict[MachineWorkloadType, str] = {
    MachineWorkloadType.PC: "PC/Mac",
    MachineWorkloadType.PS: "Physical Server",
    MachineWorkloadType.FS: "File Server",
    MachineWorkloadType.VM: "Virtual Machine",
}

_OS_TYPE_DISPLAY: dict[MachineOsType, str] = {
    MachineOsType.WINDOWS: "Windows",
    MachineOsType.MAC:     "Mac",
    MachineOsType.LINUX:   "Linux",
    MachineOsType.NONE:    "-",
}

_SCOPE_DISPLAY: dict[MachineTaskScope, str] = {
    MachineTaskScope.ENTIRE_MACHINE: "Entire Machine",
    MachineTaskScope.SYSTEM_VOLUME:  "System Volume",
    MachineTaskScope.CUSTOM_VOLUME:  "Custom Volume",
}

_WEEKDAY_LABELS: dict[WeekDay, str] = {
    WeekDay.SUNDAY:    "Sun.",
    WeekDay.MONDAY:    "Mon.",
    WeekDay.TUESDAY:   "Tue.",
    WeekDay.WEDNESDAY: "Wed.",
    WeekDay.THURSDAY:  "Thu.",
    WeekDay.FRIDAY:    "Fri.",
    WeekDay.SATURDAY:  "Sat.",
}

_SCHEDULE_FREQUENCY_LABELS: dict[ScheduleFrequency, str] = {
    ScheduleFrequency.MANUAL:       "Manual Backup",
    ScheduleFrequency.HOURLY:       "Hourly Backup",
    ScheduleFrequency.DAILY:        "Daily Backup",
    ScheduleFrequency.WEEKLY:       "Weekly Backup",
    ScheduleFrequency.AFTER_BACKUP: "After Backup",
}


def fmt_schedule_frequency(freq: ScheduleFrequency) -> str:
    """Convert a ScheduleFrequency to a short human-readable schedule label."""
    return _SCHEDULE_FREQUENCY_LABELS.get(freq, str(freq.value))


def fmt_schedule_label(policy: ProtectionPlanPolicy) -> str | None:
    """Short schedule-type label for a plan policy; None when no schedule is set."""
    if policy.schedule is None:
        return None
    return _SCHEDULE_FREQUENCY_LABELS.get(policy.schedule.frequency, str(policy.schedule.frequency.value))

_RESTORE_TYPE_DISPLAY: dict[RestoreType, str] = {
    RestoreType.FILE_LEVEL:        "File Level Restore",
    RestoreType.FULL:              "Full Restore",
    RestoreType.SYSTEM_VOLUME:     "System Volume Restore",
    RestoreType.CUSTOMIZED_VOLUME: "Volume Restore",
    RestoreType.VM_FULL:           "VM Full Restore",
    RestoreType.INSTANT_AEM:       "Instant Restore (AEM)",
    RestoreType.INSTANT_VMWARE:    "Instant Restore (VMware)",
    RestoreType.INSTANT_HYPERV:    "Instant Restore (Hyper-V)",
    RestoreType.ORACLE_DATABASE:   "Oracle DB Restore",
    RestoreType.MSSQL_DATABASE:    "MSSQL DB Restore",
    RestoreType.INSTANT_NUTANIX:   "Instant Restore (Nutanix)",
    RestoreType.INSTANT_PROXMOX:   "Instant Restore (Proxmox)",
    RestoreType.UNKNOWN:           "-",
}


def fmt_restore_type(restore_type: RestoreType | None) -> str:
    """Format a RestoreType as a short human-readable string, or '-' when absent."""
    if restore_type is None:
        return "-"
    return _RESTORE_TYPE_DISPLAY.get(restore_type, restore_type.value)

_FILE_SERVER_TYPE_DISPLAY: dict[FileServerType, str] = {
    FileServerType.SMB:           "SMB",
    FileServerType.SYNOLOGY_NAS:  "Synology NAS",
    FileServerType.NUTANIX_FILES: "Nutanix",
    FileServerType.NETAPP_ONTAP:  "NetApp",
    FileServerType.UNKNOWN:       "Unknown",
}

_M365_WORKLOAD_TYPE_DISPLAY: dict[M365WorkloadType, str] = {
    M365WorkloadType.EXCHANGE:   "Exchange",
    M365WorkloadType.ONEDRIVE:   "OneDrive",
    M365WorkloadType.CHAT:       "Chat",
    M365WorkloadType.GROUP:      "Group",
    M365WorkloadType.SHAREPOINT: "SharePoint",
    M365WorkloadType.TEAMS:      "Teams",
}

_PLAN_CATEGORY_DISPLAY: dict[WorkloadCategory, str] = {
    WorkloadCategory.MACHINE: "Machine",
    WorkloadCategory.M365:    "M365",
    WorkloadCategory.GWS:     "GWS",
}


def fmt_category(cat: WorkloadCategory) -> str:
    """Convert a WorkloadCategory to its display label."""
    return _PLAN_CATEGORY_DISPLAY.get(cat, cat.value)


def fmt_bytes(n: int | None) -> str:
    """Convert bytes to a human-readable string (e.g. '1.2 GB'). Returns '-' when n is None."""
    if n is None:
        return "-"
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0  # type: ignore[assignment]
    return f"{n:.1f} PB"


def fmt_datetime(dt: datetime | None) -> str:
    """Format a datetime as a local time string; returns '-' when None."""
    if dt is None:
        return "-"
    local = dt.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S")


def fmt_datetime_iso(dt: datetime | None) -> str | None:
    """Format a datetime as a local-timezone ISO 8601 string; returns None when None."""
    return to_local_iso(dt)


def fmt_duration(seconds: int | None) -> str:
    """Format seconds as H:MM:SS; returns '-' when None or negative."""
    if seconds is None or seconds < 0:
        return "-"
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_version_status(status: VersionStatus) -> str:
    """Convert a VersionStatus to a color-coded display string."""
    return _VERSION_STATUS_DISPLAY.get(status, str(status.value))


def fmt_backup_activity_status(act: BackupActivity) -> str:
    """Convert a BackupActivity's status to a color-coded display string."""
    if act.status == BackupActivityStatus.BACKING_UP:
        if act.items_processed is not None:
            return f"[blue]⠸ Backing up ({act.items_processed} items)[/blue]"
        return f"[blue]⠸ Backing up ({act.progress}%)[/blue]"
    return _BACKUP_ACTIVITY_STATUS_DISPLAY.get(act.status, str(act.status.value))


def fmt_restore_activity_status(act: RestoreActivity) -> str:
    """Convert a RestoreActivity's status to a color-coded display string."""
    if act.status == RestoreActivityStatus.RESTORING:
        if act.items_processed is not None:
            return f"[blue]⠸ Restoring ({act.items_processed} items)[/blue]"
        return f"[blue]⠸ Restoring ({act.progress}%)[/blue]"
    return _RESTORE_ACTIVITY_STATUS_DISPLAY.get(act.status, str(act.status.value))


def fmt_verify_status(vs: VerifyStatus | None) -> str:
    """Convert a VerifyStatus to a color-coded display string; returns '-' when None."""
    if vs is None:
        return "-"
    return _VERIFY_STATUS_DISPLAY.get(vs, str(vs.value))


def fmt_version_copy_status(status: VersionCopyStatus | None) -> str:
    """Convert a VersionCopyStatus to a color-coded display string; returns '-' when None."""
    if status is None:
        return "-"
    return _VERSION_COPY_STATUS_DISPLAY.get(status, str(status.value))


def fmt_copy_reason(reason: CopyReason | None) -> str | None:
    """Return a human-readable detail string for a CopyReason; returns None when no message applies.

    Shared helper for version copy status, plan backup copy status, and tiering status detail.
    Returns None for None and NO_VERSIONS_TO_COPY (handled separately by the caller).
    """
    if reason is None or reason == CopyReason.NO_VERSIONS_TO_COPY:
        return None
    return _COPY_REASON_MESSAGES.get(reason)


def fmt_copy_status(info: TieringStatus | PlanBackupCopyStatus | None) -> str:
    """Format a TieringStatus or PlanBackupCopyStatus for display; handles NO_VERSIONS_TO_COPY override."""
    if info is None:
        return "-"
    if info.status == VersionCopyStatus.COMPLETED and info.reason == CopyReason.NO_VERSIONS_TO_COPY:
        return "No versions to copy"
    return fmt_version_copy_status(info.status)


def fmt_retention(retention: ProtectionRetentionPolicy) -> str:
    """Format a ProtectionRetentionPolicy as a short human-readable string (e.g. '30 days', '10 versions')."""
    r = retention
    if r.retention_type == RetentionType.KEEP_ALL:
        return "Keep all"
    if r.retention_type == RetentionType.KEEP_DAYS and r.days:
        return f"{r.days} {'day' if r.days == 1 else 'days'}"
    if r.retention_type == RetentionType.KEEP_VERSIONS and r.versions:
        return f"{r.versions} versions"
    if r.retention_type == RetentionType.KEEP_ADVANCED:
        return "Advanced rules"
    return "-"


def fmt_retirement_retention(r: RetirementRetentionPolicy) -> str:
    """Format a RetirementRetentionPolicy as a short human-readable string."""
    if r.days is not None and r.keep_latest_version:
        return f"{r.days} {'day' if r.days == 1 else 'days'} + latest"
    if r.days is not None:
        return f"{r.days} {'day' if r.days == 1 else 'days'}"
    if r.keep_latest_version:
        return "Latest version"
    return "Keep all"


def fmt_location_info(info: LocationInfo) -> str:
    """Format a LocationInfo as a display string: name or name (vault)."""
    return f"{info.name} ({info.vault})" if info.vault else info.name


def fmt_location_name(loc: VersionLocation) -> str:
    """Format a VersionLocation as a display string: name or name (vault)."""
    return fmt_location_info(loc.location_info)


def fmt_backup_server(wl: Workload) -> str:
    """Return the backup_server display name for a Workload; returns '-' when unknown."""
    loc = wl.backup_server
    return loc.name if loc else "-"


def fmt_backup_copy(wl: Workload) -> str:
    """Return the backup_copy_destination display string for a Workload; returns '-' when not configured."""
    loc = wl.backup_copy_destination
    if not loc:
        return "-"
    return f"{loc.name} ({loc.vault})" if loc.vault else loc.name


def fmt_workload_status(wl: Workload) -> str:
    """Convert a Workload's current backup status to a color-coded display string."""
    if wl.status == WorkloadStatus.BACKING_UP:
        if wl.items_backed_up is not None:
            return f"[blue]⠸ Backing up ({wl.items_backed_up} items)[/blue]"
        if wl.backup_progress is not None:
            return f"[blue]⠸ Backing up ({wl.backup_progress}%)[/blue]"
    return _WORKLOAD_STATUS_DISPLAY.get(wl.status, str(wl.status.value))


def print_list_footer(console: Console, n: int, total: int | None, offset: int = 0) -> None:
    """Display a pagination summary after a table.

    total of None (or negative) means the API does not report a total count; in that
    case only the shown count is printed (offset range and "of N" are omitted).
    """
    if total is None or total < 0:
        console.print(f"[dim]Showing {n}[/dim]")
        return
    if offset > 0:
        console.print(f"[dim]Showing {offset + 1}–{offset + n} of {total}[/dim]")
    else:
        console.print(f"[dim]Showing {n} of {total}[/dim]")


def render_log_table(console: Console, log_entries: tuple[ActivityLogEntry, ...] | None) -> None:
    """Render activity log entries as a Rich table; no-op when log_entries is empty or None."""
    if not log_entries:
        return
    console.print()
    console.print("[bold]── Logs[/bold]")
    t = new_table()
    t.add_column("Time", min_width=19)
    t.add_column("Level", width=8)
    t.add_column("Message")
    for entry in log_entries:
        level_label: str = _LOG_LEVEL_DISPLAY.get(entry.level, entry.level.value)
        t.add_row(
            cell(fmt_datetime(entry.timestamp)),
            cell(level_label, styled=True),
            cell(entry.message),
        )
    console.print(t)


def render_version_table(
    console: Console,
    versions: Sequence[WorkloadVersion],
    offset: int,
    wl: Workload,
    verbose: bool,
    *,
    show_verify: bool = False,
) -> None:
    """Render the version list header + table (without footer) for both Machine and M365 commands."""
    console.print(f"Versions: [bold]{wl.name}[/bold]")
    if verbose:
        console.print(f"Workload ID: {wl.workload_id}")
        console.print(f"Namespace:   {wl.namespace}")
    console.print()
    t = new_table()
    t.add_column("#", style="bright_black", width=4)
    t.add_column("Created", min_width=19)
    t.add_column("Status", min_width=12)
    t.add_column("Locked", min_width=6)
    if show_verify:
        t.add_column("Verification", min_width=12)
    t.add_column("Changed Size", min_width=12)
    t.add_column("Copy Status", min_width=16)
    t.add_column("Locations", min_width=16)
    t.add_column("Version ID", min_width=36)
    for i, v in enumerate(versions, offset + 1):
        row = [
            cell(str(i)),
            cell(fmt_datetime(v.created_at)),
            cell(fmt_version_status(v.status), styled=True),
            cell("🔒" if v.locked else "", ""),
        ]
        if show_verify:
            row.append(cell(fmt_verify_status(v.verify_status), styled=True))
        loc_names = ", ".join(fmt_location_name(loc) for loc in v.locations) or "-"
        row += [
            cell(fmt_bytes(v.changed_size_bytes)),
            cell(fmt_version_copy_status(v.copy_status), styled=True),
            cell(loc_names),
            cell(v.version_id),
        ]
        t.add_row(*row)
    console.print(t)


def print_activity_detail(console: Console, act: BackupActivity, *, show_workload: bool = False) -> None:
    """Render a backup activity's Status/Plan/timing/data/log body.

    Shared by print_version_detail() (as the "Activity Detail" section of `machine
    version get` / `m365 version get`) and `activity backup get`, which prints its own
    header before calling this and passes show_workload=True for its extra Workload: line
    (print_version_detail's own header already names the workload, so it omits that line).
    """
    status_label = fmt_backup_activity_status(act)
    console.print(f"Status:          {status_label}")
    if show_workload:
        console.print(f"Workload:        {act.workload_name}")
    console.print(f"Plan:            {act.plan_name or '-'}")
    if act.backup_scope:
        scope_label = BACKUP_SCOPE_LABELS.get(act.backup_scope, act.backup_scope.value)
        console.print(f"Backup Scope:    {scope_label}")
    console.print()
    console.print(f"Start:           {fmt_datetime(act.started_at)}")
    console.print(f"End:             {fmt_datetime(act.finished_at)}")
    console.print(f"Duration:        {fmt_duration(act.duration_seconds)}")
    console.print()
    change_str  = fmt_bytes(act.data_change_bytes)      if act.data_change_bytes      is not None else "-"
    xfr_str     = fmt_bytes(act.data_transferred_bytes)
    deduped_str = fmt_bytes(act.data_deduped_bytes)     if act.data_deduped_bytes     is not None else "-"
    console.print(f"Data Change:     {change_str}")
    console.print(f"Transferred:     {xfr_str}")
    console.print(f"Actual Capacity Used: {deduped_str}")
    if act.processed_success_count is not None:
        console.print(
            f"Processed items: {act.processed_success_count} succeeded, "
            f"{act.processed_warning_count} warning, "
            f"{act.processed_error_count} error"
        )
    render_log_table(console, act.log_entries)


def print_version_detail(console: Console, v: WorkloadVersion, act: BackupActivity) -> None:
    """Render the version + activity detail view for TABLE output mode."""
    console.print("[bold]── Version[/bold]")
    console.print(f"Version ID:      {v.version_id}")
    console.print(f"Workload ID:     {v.workload_id}")
    console.print(f"Namespace:       {v.namespace}")
    if v.locked:
        console.print("Locked:          🔒")
    if v.locations:
        loc_lines = [fmt_location_name(loc) for loc in v.locations]
        console.print(f"Locations:       {loc_lines[0]}")
        for line in loc_lines[1:]:
            console.print(f"                 {line}")
    if v.copy_status is not None:
        console.print(f"Copy Status:     {fmt_version_copy_status(v.copy_status)}", markup=True)
        reason_str = fmt_copy_reason(v.copy_reason)
        if reason_str:
            console.print(f"                 {reason_str}")
    console.print()
    console.print(f"[bold]── Activity Detail — {act.workload_name}[/bold]")
    print_activity_detail(console, act)


# ── M365 export status ────────────────────────────────────────────────────────

_EXPORT_STATUS_DISPLAY: dict[M365ExportStatus, str] = {
    M365ExportStatus.READY_TO_DOWNLOAD: "[green]Ready to download[/green]",
    M365ExportStatus.DOWNLOADED:        "[bright_black]Downloaded[/bright_black]",
    M365ExportStatus.CANCELED:          "[bright_black]Canceled[/bright_black]",
    M365ExportStatus.PREPARING:         "[blue]Preparing[/blue]",
    M365ExportStatus.EXPIRED:           "[yellow]Expired[/yellow]",
    M365ExportStatus.FAILED:            "[red]Failed[/red]",
    M365ExportStatus.WARNING:           "[yellow]Warning[/yellow]",
    M365ExportStatus.UNKNOWN:           "[bright_black]Unknown[/bright_black]",
}


def fmt_export_status(status: M365ExportStatus) -> str:
    """Return a Rich-formatted label for an M365ExportStatus value."""
    return _EXPORT_STATUS_DISPLAY.get(status, "[bright_black]Unknown[/bright_black]")


# ── Infrastructure formatters ─────────────────────────────────────────────────


def fmt_server_status(status: ServerStatus) -> str:
    """Rich-formatted label for a backup server status."""
    return _SERVER_STATUS_DISPLAY.get(status, f"[dim]{status.value}[/dim]")


def fmt_usage_pct(pct: float, *, fixed_width: bool = True) -> str:
    """Color-coded percentage label; yellow >= 80%, red >= 90%."""
    s = f"{pct:>3.0f}%" if fixed_width else f"{pct:.0f}%"
    if pct >= 90:
        return f"[red]{s}[/red]"
    if pct >= 80:
        return f"[yellow]{s}[/yellow]"
    return s


def fmt_storage_usage(used: str, total: str, pct: float | None) -> str:
    """Format combined storage usage cell: 'Z% (X / Y)' with color-coded percentage."""
    if pct is None:
        return "-"
    return f"{fmt_usage_pct(pct)} ({used} / {total})"


def fmt_management_url(address: str, port: str) -> str:
    """Build the base management URL. Omits port when empty or when it is the standard HTTPS port 443."""
    if not address:
        return ""
    if port and port != "443":
        return f"https://{address}:{port}"
    return f"https://{address}"


def fmt_remote_storage_status(status: RemoteStorageStatus) -> str:
    """Rich-formatted label for a remote storage status."""
    return _REMOTE_STORAGE_STATUS_DISPLAY.get(status, f"[dim]{status.value}[/dim]")


def fmt_encryption_enabled(enabled: bool) -> str:
    """Rich-formatted label for the remote storage encryption flag."""
    return "[green]✓ Enabled[/green]" if enabled else "[dim]✗ Disabled[/dim]"


def fmt_remote_storage_type(storage_type: RemoteStorageType, device_model: str) -> str:
    """Display label for a remote storage type; APV includes the device model."""
    label = _REMOTE_STORAGE_TYPE_DISPLAY.get(storage_type, "Unknown")
    if storage_type == RemoteStorageType.ACTIVE_PROTECT_VAULT and device_model:
        return f"{label} ({device_model})"
    return label


def fmt_hypervisor_type(host_type: HypervisorType) -> str:
    """Display label for a hypervisor host type."""
    return _HYPERVISOR_TYPE_DISPLAY.get(host_type, f"[dim]{host_type.value}[/dim]")


def fmt_remote_storage_usage(used_bytes: int | None, remaining_bytes: int | None) -> str:
    """Format the Remote Storage usage column.

    Both used and remaining available → "442.8 KB (341.8 GB left)"
    Only used available               → "442.8 KB"
    used unavailable                  → "-"
    """
    if used_bytes is None:
        return "-"
    used = fmt_bytes(used_bytes)
    if remaining_bytes is not None:
        return f"{used} ({fmt_bytes(remaining_bytes)} left)"
    return used


# ── Plan schedule / retention detail formatters ───────────────────────────────

_WEEKDAY_DISPLAY_ORDER = [
    WeekDay.MONDAY, WeekDay.TUESDAY, WeekDay.WEDNESDAY, WeekDay.THURSDAY,
    WeekDay.FRIDAY, WeekDay.SATURDAY, WeekDay.SUNDAY,
]


def fmt_schedule_str(sch: ProtectionSchedule) -> str:
    """Render a ProtectionSchedule to a human-readable string."""
    if sch.frequency == ScheduleFrequency.AFTER_BACKUP:
        return "After Backup"
    if sch.frequency == ScheduleFrequency.MANUAL:
        return "Manual"
    t = sch.start_time
    if sch.frequency == ScheduleFrequency.HOURLY:
        return f"Hourly at :{t.minute:02d}" if t else "Hourly"
    if sch.frequency == ScheduleFrequency.DAILY:
        return f"Daily, {t.hour:02d}:{t.minute:02d}" if t else "Daily"
    days = ", ".join(
        _WEEKDAY_LABELS[d] for d in sorted(sch.weekdays, key=lambda w: w.value)
    ) if sch.weekdays else "-"
    return f"Weekly on {days}, {t.hour:02d}:{t.minute:02d}" if t else f"Weekly on {days}"


def fmt_hour_ranges(hours: frozenset[int]) -> str:
    """Collapse a set of allowed hours into 'HH:00–HH:00' range strings."""
    if len(hours) == 24:
        return "unrestricted"
    sorted_hours = sorted(hours)
    ranges: list[str] = []
    start = end = sorted_hours[0]
    for h in sorted_hours[1:]:
        if h == end + 1:
            end = h
        else:
            ranges.append(f"{start:02d}:00–{end + 1:02d}:00")
            start = end = h
    ranges.append(f"{start:02d}:00–{end + 1:02d}:00")
    return ", ".join(ranges)


def fmt_advanced_rules(retention: ProtectionRetentionPolicy) -> list[str]:
    """Return individual rule lines for KEEP_ADVANCED retention in detail view."""
    r = retention

    def _d(n: int, unit: str) -> str:
        return f"{n} {unit}" if n == 1 else f"{n} {unit}s"

    lines: list[str] = []
    if r.days:
        lines.append(f"Keep all versions for {_d(r.days, 'day')}")
    if r.gfs:
        if r.gfs.daily_versions:
            lines.append(f"Keep the latest version of the day for {_d(r.gfs.daily_versions, 'day')}")
        if r.gfs.weekly_versions:
            lines.append(f"Keep the latest version of the week for {_d(r.gfs.weekly_versions, 'week')}")
        if r.gfs.monthly_versions:
            lines.append(f"Keep the latest version of the month for {_d(r.gfs.monthly_versions, 'month')}")
        if r.gfs.yearly_versions:
            lines.append(f"Keep the latest version of the year for {_d(r.gfs.yearly_versions, 'year')}")
    if r.versions:
        lines.append(f"Number of latest version to keep: {_d(r.versions, 'version')}")
    return lines


def print_retention_detail(console: Console, label: str, retention: ProtectionRetentionPolicy) -> None:
    """Print a retention field in the get detail view, with multi-line output for KEEP_ADVANCED."""
    if retention.retention_type == RetentionType.KEEP_ADVANCED:
        console.print(f"  {label}")
        for rule in fmt_advanced_rules(retention):
            console.print(f"    {rule}")
    else:
        console.print(f"  {label} {fmt_retention(retention)}")


def print_backup_window(console: Console, window: MachineBackupWindow) -> None:
    """Print the plan backup window section: per-weekday allowed hour ranges."""
    if not window.enabled:
        console.print("  Backup Window:    No restriction")
        return
    console.print("  Backup Window:")
    for day in _WEEKDAY_DISPLAY_ORDER:
        hours = window.allowed_hours.get(day, frozenset())
        value = fmt_hour_ranges(hours) if hours else "blocked"
        console.print(f"    {_WEEKDAY_LABELS[day]}  {value}")


def print_workload_detail(
    console: Console,
    wl: Workload,
    *,
    type_label: str,
    info_rows: Sequence[tuple[str, str]] = (),
    status_rows: Sequence[tuple[str, str]] = (),
) -> None:
    """Render the workload detail view shared by the machine and m365 get commands.

    Args:
        type_label:  Value of the ``Type:`` row (e.g. "Machine / PC/Mac").
        info_rows:   Extra (label, value) rows appended to the Workload Information block.
        status_rows: Extra (label, value) rows inserted after the Status row in the
                     Backup Status block.
    """
    def row(label: str, value: str) -> None:
        console.print(f"  {label}:{' ' * (15 - len(label))}{value}")

    console.print(f"Workload: [bold]{wl.name}[/bold]")
    console.print("[bold]── Workload Information[/bold]")
    row("ID", wl.workload_id)
    row("Namespace", wl.namespace)
    row("Type", type_label)
    for label, value in info_rows:
        row(label, value)
    console.print()
    console.print("[bold]── Backup Status[/bold]")
    if not wl.is_retired:
        row("Status", fmt_workload_status(wl))
    for label, value in status_rows:
        row(label, value)
    row("Plan", wl.plan.name)
    row("Plan ID", wl.plan.plan_id)
    row("Last Backup", fmt_datetime(wl.last_backup_at))
    row("Protected Size", fmt_bytes(wl.protected_data_bytes))
    if wl.backup_copy_data_bytes:
        row("Copy Size", fmt_bytes(wl.backup_copy_data_bytes))
    row("Backup Server", fmt_backup_server(wl))
    row("Copy Dest", fmt_backup_copy(wl))
