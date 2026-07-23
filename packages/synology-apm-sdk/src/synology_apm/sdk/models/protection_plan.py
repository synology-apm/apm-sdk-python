"""ProtectionPlan and all associated policy, config, task, and request models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta
from typing import Any

from ..enums import (
    CopyReason,
    DbActionOnError,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    RetentionType,
    ScheduleFrequency,
    VersionCopyStatus,
    WeekDay,
    WorkloadCategory,
)
from ._shared import auto_to_dict
from .backup_server import BackupServer
from .location import LocationInfo
from .remote_storage import RemoteStorage

# ── Schedule and retention ────────────────────────────────────────────────


@dataclass(frozen=True)
class ProtectionSchedule:
    """Backup schedule configuration.

    Attributes:
        frequency:  Schedule frequency (MANUAL / HOURLY / DAILY / WEEKLY / AFTER_BACKUP).
        start_time: Execution time. None for MANUAL / AFTER_BACKUP; time(0, run_minute) for HOURLY
                    (only minute is meaningful — runs at that minute of every hour);
                    full HH:MM for DAILY / WEEKLY.
        weekdays:   Days of the week to run (only meaningful for WEEKLY; at least one weekday
                    is required for WEEKLY; always empty for other frequencies).
    """
    frequency: ScheduleFrequency
    start_time: time | None
    weekdays: tuple[WeekDay, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(
            self,
            exclude=frozenset({"weekdays", "start_time"}),
            extra={
                "weekdays": [d.name.lower() for d in sorted(self.weekdays, key=lambda w: w.value)],
                "start_time": (
                    f"{self.start_time.hour:02d}:{self.start_time.minute:02d}" if self.start_time else None
                ),
            },
        )


@dataclass(frozen=True)
class GFSRetention:
    """GFS (Grandfather-Father-Son) rotation retention policy details."""
    daily_versions: int
    weekly_versions: int
    monthly_versions: int
    yearly_versions: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class ProtectionRetentionPolicy:
    """Version retention policy for a Protection Plan."""
    retention_type: RetentionType
    days: int | None = None
    versions: int | None = None
    gfs: GFSRetention | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class ProtectionPlanPolicy:
    """Combined schedule and retention policy for a backup or Backup Copy.

    Attributes:
        retention: Version retention policy.
        schedule:  Schedule settings; None for list() results.
    """
    retention: ProtectionRetentionPolicy
    schedule: ProtectionSchedule | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


# ── Backup Copy status (read-only) ───────────────────────────────────────


@dataclass(frozen=True)
class PlanBackupCopyStatus:
    """Backup copy status summary for a Protection Plan.

    Attributes:
        status:                 Overall backup copy status for this plan; parallels WorkloadVersion.copy_status.
        reason:                 Detail reason when status is SKIPPED, RETRY, or FAILED;
                                NO_VERSIONS_TO_COPY when the plan has no versions eligible for copy;
                                None otherwise.
        pending_version_count:  Number of versions waiting to be copied (meaningful when WAITING,
                                IN_PROGRESS, RETRY, or FAILED).
        remaining_bytes:        Estimated bytes remaining for the pending copy; None when unavailable.
        skipped_workload_count: Number of workloads skipped in the last copy run (meaningful when SKIPPED).
    """
    status: VersionCopyStatus
    reason: CopyReason | None
    pending_version_count: int = 0
    remaining_bytes: int | None = None
    skipped_workload_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


# ── Backup Copy config (read / write) ───────────────────────────────────


@dataclass(frozen=True)
class BackupCopyPolicy:
    """Backup Copy configuration as read from a Protection Plan (read-only).

    To modify Backup Copy settings, use BackupCopyConfig in create()/update() requests.
    To look up the full destination object for a new request, resolve
    destination.identifier via apm.backup_servers or apm.remote_storages.

    Attributes:
        destination: Backup Copy destination location.
        retention:   Retention policy applied to copied versions.
        schedule:    Copy trigger — AFTER_BACKUP or a fixed daily/weekly time.
    """
    destination: LocationInfo
    retention: ProtectionRetentionPolicy
    schedule: ProtectionSchedule

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class BackupCopyConfig:
    """Backup Copy configuration for create()/update() requests.

    Attributes:
        destination: Backup Copy destination: a BackupServer (ActiveProtect appliance) or
                     RemoteStorage. Determines the storage endpoint type used for copy.
        retention:   Retention policy applied to copied versions.
        schedule:    Copy trigger: AFTER_BACKUP runs after each backup; DAILY/WEEKLY
                     runs at the specified time.
    """
    destination: BackupServer | RemoteStorage
    retention: ProtectionRetentionPolicy
    schedule: ProtectionSchedule


# ── Device plan config sections ──────────────────────────────────────────


@dataclass(frozen=True)
class MachineVmConfig:
    """VM workload advanced settings.

    Attributes:
        enable_app_aware_bkp:                Application-consistent backup (quiesces VSS/VMware Tools).
        enable_verification:                  Run backup verification after each backup.
        verification_video_duration_seconds:  Length of the verification recording in seconds.
        enable_datastore_usage_detection:     Monitor datastore usage during backup.
        datastore_min_free_space_percent:     Minimum free space (%) the datastore must maintain.
    """
    enable_app_aware_bkp: bool = True
    enable_verification: bool = False
    verification_video_duration_seconds: int = 120
    enable_datastore_usage_detection: bool = False
    datastore_min_free_space_percent: int = 10

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class MachinePcConfig:
    """PC workload advanced settings (Windows and macOS).

    Attributes:
        shutdown_after_backup:       Shut down the device after backup completes.
        wake_for_backup:             Wake the device from sleep before backup starts.
        prevent_sleep_during_backup: Prevent the device from entering sleep during backup (Windows only).
    """
    shutdown_after_backup: bool = False
    wake_for_backup: bool = False
    prevent_sleep_during_backup: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class MachinePsConfig:
    """Physical server workload advanced settings.

    Attributes:
        enable_app_aware_bkp:                Application-consistent backup (quiesces VSS).
        enable_verification:                  Run backup verification after each backup.
        verification_video_duration_seconds:  Length of the verification recording in seconds.
        shutdown_after_backup:               Shut down the server after backup completes.
        wake_for_backup:                     Wake the server from sleep before backup starts.
        prevent_sleep_during_backup:          Prevent the server from entering sleep during backup (Windows only).
    """
    enable_app_aware_bkp: bool = True
    enable_verification: bool = False
    verification_video_duration_seconds: int = 120
    shutdown_after_backup: bool = False
    wake_for_backup: bool = False
    prevent_sleep_during_backup: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class MachineDbConfig:
    """Database processing configuration (applies when DB backup is enabled).

    Attributes:
        action_on_error:    Action when a database processing error occurs during backup.
        mssql_log_setting:  Transaction log handling for Microsoft SQL Server databases.
        oracle_log_setting: Archived log handling for Oracle databases.
    """
    action_on_error: DbActionOnError = DbActionOnError.CONTINUE
    mssql_log_setting: MssqlLogSetting = MssqlLogSetting.DO_NOT_TRUNCATE
    oracle_log_setting: OracleLogSetting = OracleLogSetting.DO_NOT_DELETE

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class MachineBackupWindow:
    """Allowed time slots for backup execution.

    Attributes:
        enabled:       Whether the backup window restriction is active.
                       When False, backup can run at any time.
        allowed_hours: Weekday → set of allowed hours (0–23). Absent weekdays
                       are fully blocked. Empty dict with enabled=True blocks all backup.
                       Ignored when enabled is False.

    Raises:
        ValueError: An allowed hour is outside 0–23 (checked only when enabled is True;
                    allowed_hours is ignored when enabled is False).
    """
    enabled: bool
    allowed_hours: dict[WeekDay, frozenset[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        for day, hours in self.allowed_hours.items():
            for h in hours:
                if not 0 <= h <= 23:
                    raise ValueError(
                        f"Backup window hour {h} for {day.name} is out of range 0-23."
                    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(
            self,
            exclude=frozenset({"allowed_hours"}),
            extra={
                "allowed_hours": {
                    day.name.lower(): sorted(hours)
                    for day, hours in sorted(self.allowed_hours.items(), key=lambda x: x[0].value)
                },
            },
        )


# ── Per-task config ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventTriggerConfig:
    """Event-triggered backup settings for PC/Mac tasks.

    Attributes:
        on_sign_out:  Back up when the user signs out.
        on_lock:      Back up when the screen locks.
        on_startup:   Back up on system startup.
        min_interval: Minimum time between consecutive event-triggered backups.
                      Sub-minute precision is not supported; values are rounded down
                      to the nearest minute.

    Raises:
        ValueError: At least one of on_sign_out, on_lock, on_startup must be True.
        ValueError: min_interval must be a positive duration.
    """
    on_sign_out: bool = False
    on_lock: bool = False
    on_startup: bool = False
    min_interval: timedelta = field(default_factory=lambda: timedelta(hours=1))

    def __post_init__(self) -> None:
        if not (self.on_sign_out or self.on_lock or self.on_startup):
            raise ValueError(
                "At least one event trigger (on_sign_out, on_lock, on_startup) must be enabled."
            )
        if self.min_interval.total_seconds() <= 0:
            raise ValueError("min_interval must be a positive duration.")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(
            self,
            exclude=frozenset({"min_interval"}),
            extra={"min_interval_seconds": int(self.min_interval.total_seconds())},
        )


@dataclass(frozen=True)
class MachineTaskSchedule:
    """Per-task backup schedule for an individual task entry in a DEVICE plan.

    Attributes:
        time_schedule:  Time-based schedule component; None when there is no time-based schedule
                        (event-triggered-only or on-demand backup).
        event_trigger:  Event-triggered backup settings; None when event-triggered backup is
                        disabled. Only applicable to PC (Windows and Mac) tasks.
    """
    time_schedule: ProtectionSchedule | None = None
    event_trigger: EventTriggerConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


def _validate_plan_schedule_and_retention(
    schedule: ProtectionSchedule, retention: ProtectionRetentionPolicy, is_immutable: bool
) -> None:
    if schedule.frequency == ScheduleFrequency.AFTER_BACKUP:
        raise ValueError("AFTER_BACKUP is only valid for Backup Copy schedules, not main plan schedules.")
    if schedule.frequency == ScheduleFrequency.WEEKLY and not schedule.weekdays:
        raise ValueError("WEEKLY schedule requires at least one weekday.")
    if is_immutable and retention.retention_type != RetentionType.KEEP_DAYS:
        raise ValueError("Immutable plans require KEEP_DAYS retention.")


def _validate_backup_copy(backup_copy: BackupCopyConfig | None) -> None:
    """Validate a Backup Copy schedule. Unlike the main schedule, AFTER_BACKUP is allowed here;
    only the WEEKLY-requires-a-weekday rule applies."""
    if backup_copy is None:
        return
    schedule = backup_copy.schedule
    if schedule.frequency == ScheduleFrequency.WEEKLY and not schedule.weekdays:
        raise ValueError("WEEKLY Backup Copy schedule requires at least one weekday.")


_MANDATORY_TASK_PAIRS: frozenset[tuple[MachineWorkloadType, MachineOsType]] = frozenset({
    (MachineWorkloadType.PC, MachineOsType.WINDOWS),
    (MachineWorkloadType.PC, MachineOsType.MAC),
    (MachineWorkloadType.PS, MachineOsType.WINDOWS),
    (MachineWorkloadType.PS, MachineOsType.LINUX),
    (MachineWorkloadType.FS, MachineOsType.NONE),
    (MachineWorkloadType.VM, MachineOsType.NONE),
})

_FIXED_COUNT_PAIRS: frozenset[tuple[MachineWorkloadType, MachineOsType]] = frozenset({
    (MachineWorkloadType.FS, MachineOsType.NONE),
    (MachineWorkloadType.VM, MachineOsType.NONE),
})

_VALID_OS_FOR_WORKLOAD: dict[MachineWorkloadType, frozenset[MachineOsType]] = {
    MachineWorkloadType.PC: frozenset({MachineOsType.WINDOWS, MachineOsType.MAC}),
    MachineWorkloadType.PS: frozenset({MachineOsType.WINDOWS, MachineOsType.LINUX}),
    MachineWorkloadType.FS: frozenset({MachineOsType.NONE}),
    MachineWorkloadType.VM: frozenset({MachineOsType.NONE}),
}


@dataclass(frozen=True)
class MachineTaskConfig:
    """One backup scope/schedule entry in a DEVICE plan's task array.

    A plan always contains at least 6 entries — one for each mandatory
    (workload_type, os_type) pair. PC and PS pairs may have additional
    entries to back up different scopes independently. VM and FS are
    always exactly one entry each and do not support scope customization.

    Attributes:
        workload_type:           PC, PS, VM, or FS.
        os_type:                 OS for this entry. Must match the workload type:
                                 PC → WINDOWS or MAC; PS → WINDOWS or LINUX;
                                 VM/FS → NONE.
        scope:                   Backup scope; not applicable for VM and FS workloads.
        custom_volumes:          Volume specifiers when scope=CUSTOM_VOLUME.
                                 Windows: drive letters ("C:") or volume labels ("Volume_1").
                                 macOS: volume labels or "$System_Volume".
                                 Linux PS: mount paths ("/my/mount/point").
        include_external_drives: Include external drives when scope=ENTIRE_MACHINE.
                                 Ignored for SYSTEM_VOLUME and CUSTOM_VOLUME.
        include_boot_partition:  Include boot partition when scope=CUSTOM_VOLUME and the
                                 selection contains the system partition.
        use_main_schedule:       Follow the plan's main schedule. When True, schedule is ignored.
        schedule:                Per-task schedule; only used when use_main_schedule=False.
    """
    workload_type: MachineWorkloadType
    os_type: MachineOsType
    scope: MachineTaskScope | None = None
    custom_volumes: tuple[str, ...] = ()
    include_external_drives: bool = False
    include_boot_partition: bool = True
    use_main_schedule: bool = True
    schedule: MachineTaskSchedule | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


# ── ProtectionPlan (read model) ──────────────────────────────────────────


@dataclass(frozen=True)
class ProtectionPlan:
    """Core configuration unit for a backup task.

    Attributes:
        plan_id:                       Unique plan identifier.
        name:                          Plan display name.
        category:                      Workload category this plan belongs to.
        policy:                        Schedule and retention policy for the main backup.
        workload_count:                Total number of workloads this plan is applied to.
        description:                   Plan description.
        successful_workload_count:     Number of workloads with successful backups.
        unsuccessful_workload_count:   Number of workloads with failed backups.
        is_immutable:                  Whether immutable backups are enabled.
        backup_copy_policy:            Backup Copy configuration; None when Backup Copy is not enabled.
        backup_copy_status:            Current backup copy status; None when Backup Copy is not configured.
        run_schedule_by_controller_time: Whether schedules run on the APM controller's clock rather
                                       than each backup server's local clock.
        vm_config:                     VM workload settings; None for M365 plans or for list() results.
        pc_config:                     PC workload settings; None for M365 plans or for list() results.
        ps_config:                     Physical server settings; None for M365 plans or for list() results.
        db_config:                     Database backup settings; None when DB backup is disabled
                                       or for list() results.
        backup_window:                 Allowed backup time window; None for list() results.
        tasks:                         Per-workload-type task entries; None for M365 plans or for list() results.
    """
    plan_id: str
    name: str
    category: WorkloadCategory
    policy: ProtectionPlanPolicy | None = None
    workload_count: int | None = None
    description: str = ""
    successful_workload_count: int = 0
    unsuccessful_workload_count: int = 0
    is_immutable: bool = False
    backup_copy_policy: BackupCopyPolicy | None = None
    backup_copy_status: PlanBackupCopyStatus | None = None
    run_schedule_by_controller_time: bool = False
    vm_config: MachineVmConfig | None = None
    pc_config: MachinePcConfig | None = None
    ps_config: MachinePsConfig | None = None
    db_config: MachineDbConfig | None = None
    backup_window: MachineBackupWindow | None = None
    tasks: tuple[MachineTaskConfig, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


# ── Request dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True)
class MachinePlanCreateRequest:
    """Parameters for creating a Machine (DEVICE) Protection Plan.

    Attributes:
        name:                           Plan display name.
        retention:                      Version retention policy.
        schedule:                       Main backup schedule.
        description:                    Plan description.
        is_immutable:                   Enable immutable backups (requires KEEP_DAYS retention).
        vm_config:                      VM workload settings; None applies default settings.
        pc_config:                      PC workload settings; None applies default settings.
        ps_config:                      Physical server settings; None applies default settings.
        db_config:                      Database processing settings; None disables DB backup.
        backup_window:                  Allowed backup time window; None imposes no restriction.
        tasks:                          Per-workload-type task entries. None generates the 6 default
                                        entries (one per mandatory (workload_type, os_type) pair).
                                        All 6 mandatory pairs must be covered when provided.
        backup_copy:                    Backup Copy configuration; None disables copy.
        run_schedule_by_controller_time: Use APM controller's clock for scheduling.

    Raises:
        ValueError: schedule frequency cannot be AFTER_BACKUP.
        ValueError: WEEKLY schedule requires at least one weekday.
        ValueError: WEEKLY Backup Copy schedule requires at least one weekday.
        ValueError: Immutable plans require KEEP_DAYS retention.
        ValueError: tasks is missing a mandatory (workload_type, os_type) pair.
        ValueError: tasks contains more than one FS or VM entry.
        ValueError: tasks contains a duplicate MachineTaskConfig.
        ValueError: A task's os_type is invalid for its workload_type.
        ValueError: A VM or FS task has a non-None scope.
        ValueError: custom_volumes is non-empty when scope != CUSTOM_VOLUME.
        ValueError: A task with use_main_schedule=False has no schedule.
        ValueError: A non-PC task's schedule has event_trigger set.
        ValueError: A task schedule uses AFTER_BACKUP frequency.
        ValueError: A task WEEKLY schedule has no weekdays.
    """
    name: str
    retention: ProtectionRetentionPolicy
    schedule: ProtectionSchedule
    description: str = ""
    is_immutable: bool = False
    vm_config: MachineVmConfig | None = None
    pc_config: MachinePcConfig | None = None
    ps_config: MachinePsConfig | None = None
    db_config: MachineDbConfig | None = None
    backup_window: MachineBackupWindow | None = None
    tasks: tuple[MachineTaskConfig, ...] | None = None
    backup_copy: BackupCopyConfig | None = None
    run_schedule_by_controller_time: bool = False

    def __post_init__(self) -> None:
        _validate_plan_schedule_and_retention(self.schedule, self.retention, self.is_immutable)
        _validate_backup_copy(self.backup_copy)
        if self.tasks is not None:
            pair_counts: dict[tuple[MachineWorkloadType, MachineOsType], int] = {}
            for task in self.tasks:
                valid_os = _VALID_OS_FOR_WORKLOAD.get(task.workload_type, frozenset())
                if task.os_type not in valid_os:
                    raise ValueError(
                        f"os_type={task.os_type.name!r} is not valid for "
                        f"workload_type={task.workload_type.name!r}."
                    )
                pair = (task.workload_type, task.os_type)
                pair_counts[pair] = pair_counts.get(pair, 0) + 1
            for pair in _MANDATORY_TASK_PAIRS:
                if pair_counts.get(pair, 0) < 1:
                    raise ValueError(
                        f"Task list must include at least one entry for "
                        f"(workload_type={pair[0].name}, os_type={pair[1].name})."
                    )
            for pair in _FIXED_COUNT_PAIRS:
                if pair_counts.get(pair, 0) > 1:
                    raise ValueError(
                        f"Tasks with (workload_type={pair[0].name}, os_type={pair[1].name}) "
                        f"must have exactly 1 entry."
                    )
            seen_tasks: set[MachineTaskConfig] = set()
            for task in self.tasks:
                if task.workload_type in (MachineWorkloadType.VM, MachineWorkloadType.FS) and task.scope is not None:
                    raise ValueError(
                        f"Tasks with workload_type={task.workload_type.name} must have scope=None."
                    )
                if task.scope != MachineTaskScope.CUSTOM_VOLUME and task.custom_volumes:
                    raise ValueError("custom_volumes must be empty when scope != CUSTOM_VOLUME.")
                if not task.use_main_schedule and task.schedule is None:
                    raise ValueError(
                        f"Tasks with use_main_schedule=False must provide a schedule "
                        f"(workload_type={task.workload_type.name}, os_type={task.os_type.name})."
                    )
                if not task.use_main_schedule and task.schedule is not None:
                    ts = task.schedule
                    if ts.event_trigger is not None and task.workload_type != MachineWorkloadType.PC:
                        raise ValueError(
                            f"event_trigger is only valid for PC tasks "
                            f"(workload_type={task.workload_type.name}, os_type={task.os_type.name})."
                        )
                    inner = ts.time_schedule
                    if inner is not None:
                        if inner.frequency == ScheduleFrequency.AFTER_BACKUP:
                            raise ValueError(
                                f"AFTER_BACKUP is not valid for task schedules "
                                f"(workload_type={task.workload_type.name}, os_type={task.os_type.name})."
                            )
                        if inner.frequency == ScheduleFrequency.WEEKLY and not inner.weekdays:
                            raise ValueError(
                                f"WEEKLY task schedule requires at least one weekday "
                                f"(workload_type={task.workload_type.name}, os_type={task.os_type.name})."
                            )
                if task in seen_tasks:
                    raise ValueError(
                        f"Duplicate task: identical MachineTaskConfig submitted more than once "
                        f"(workload_type={task.workload_type.name}, os_type={task.os_type.name})."
                    )
                seen_tasks.add(task)


@dataclass(frozen=True)
class M365PlanCreateRequest:
    """Parameters for creating an M365 Protection Plan.

    Attributes:
        name:                           Plan display name.
        retention:                      Version retention policy.
        schedule:                       Backup schedule.
        description:                    Plan description.
        is_immutable:                   Enable immutable backups (requires KEEP_DAYS retention).
        backup_copy:                    Backup Copy configuration; None disables copy.
        run_schedule_by_controller_time: Use APM controller's clock for scheduling.

    Raises:
        ValueError: schedule frequency cannot be AFTER_BACKUP.
        ValueError: WEEKLY schedule requires at least one weekday.
        ValueError: WEEKLY Backup Copy schedule requires at least one weekday.
        ValueError: Immutable plans require KEEP_DAYS retention.
    """
    name: str
    retention: ProtectionRetentionPolicy
    schedule: ProtectionSchedule
    description: str = ""
    is_immutable: bool = False
    backup_copy: BackupCopyConfig | None = None
    run_schedule_by_controller_time: bool = False

    def __post_init__(self) -> None:
        _validate_plan_schedule_and_retention(self.schedule, self.retention, self.is_immutable)
        _validate_backup_copy(self.backup_copy)
