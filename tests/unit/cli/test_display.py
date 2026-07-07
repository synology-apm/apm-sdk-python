"""Enum exhaustiveness tests for synology_apm.cli._display module-level constants.

Each test asserts that the display dict covers exactly the set of values in the
corresponding enum — no missing keys, no extra keys.
"""
from __future__ import annotations

from datetime import time

from synology_apm.cli._display import (
    _ACTIVITY_LOG_TYPE_DISPLAY,
    _BACKUP_ACTIVITY_STATUS_DISPLAY,
    _COPY_REASON_MESSAGES,
    _EXPORT_STATUS_DISPLAY,
    _FILE_SERVER_TYPE_DISPLAY,
    _HYPERVISOR_TYPE_DISPLAY,
    _LOG_LEVEL_DISPLAY,
    _M365_WORKLOAD_TYPE_DISPLAY,
    _OS_TYPE_DISPLAY,
    _PLAN_CATEGORY_DISPLAY,
    _REMOTE_STORAGE_STATUS_DISPLAY,
    _REMOTE_STORAGE_TYPE_DISPLAY,
    _RESTORE_ACTIVITY_STATUS_DISPLAY,
    _RESTORE_TYPE_DISPLAY,
    _SCHEDULE_FREQUENCY_LABELS,
    _SCOPE_DISPLAY,
    _SERVER_LOG_LEVEL_DISPLAY,
    _SERVER_STATUS_DISPLAY,
    _VERIFY_STATUS_DISPLAY,
    _VERSION_COPY_STATUS_DISPLAY,
    _VERSION_STATUS_DISPLAY,
    _WEEKDAY_LABELS,
    _WORKLOAD_STAT_LABEL,
    _WORKLOAD_STATUS_DISPLAY,
    _WORKLOAD_TYPE_DISPLAY,
    BACKUP_SCOPE_LABELS,
    fmt_activity_log_type,
    fmt_advanced_rules,
    fmt_encryption_enabled,
    fmt_export_status,
    fmt_hour_ranges,
    fmt_hypervisor_type,
    fmt_management_url,
    fmt_remote_storage_type,
    fmt_remote_storage_usage,
    fmt_restore_type,
    fmt_retirement_retention,
    fmt_schedule_frequency,
    fmt_schedule_str,
    fmt_server_log_level,
    fmt_storage_usage,
    fmt_usage_pct,
)
from synology_apm.sdk import (
    APMActivityLogType,
    BackupActivityStatus,
    BackupScope,
    CopyReason,
    FileServerType,
    GFSRetention,
    HypervisorType,
    LogLevel,
    M365ExportStatus,
    M365WorkloadType,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorageStatus,
    RemoteStorageType,
    RestoreActivityStatus,
    RestoreType,
    RetentionType,
    ScheduleFrequency,
    ServerStatus,
    VerifyStatus,
    VersionCopyStatus,
    VersionStatus,
    WeekDay,
    WorkloadCategory,
    WorkloadStatType,
    WorkloadStatus,
)
from synology_apm.sdk.models.retirement_plan import RetirementRetentionPolicy


def test_version_status_display_covers_all_enum_values() -> None:
    assert set(VersionStatus) == set(_VERSION_STATUS_DISPLAY)


def test_verify_status_display_covers_all_enum_values() -> None:
    assert set(VerifyStatus) == set(_VERIFY_STATUS_DISPLAY)


def test_version_copy_status_display_covers_all_enum_values() -> None:
    assert set(VersionCopyStatus) == set(_VERSION_COPY_STATUS_DISPLAY)


def test_workload_status_display_covers_all_enum_values() -> None:
    assert set(WorkloadStatus) == set(_WORKLOAD_STATUS_DISPLAY)


def test_backup_activity_status_display_covers_all_enum_values() -> None:
    assert set(BackupActivityStatus) == set(_BACKUP_ACTIVITY_STATUS_DISPLAY)


def test_restore_activity_status_display_covers_all_enum_values() -> None:
    assert set(RestoreActivityStatus) == set(_RESTORE_ACTIVITY_STATUS_DISPLAY)


def test_log_level_display_covers_all_enum_values() -> None:
    assert set(LogLevel) == set(_LOG_LEVEL_DISPLAY)


def test_backup_scope_labels_covers_all_enum_values() -> None:
    assert set(BackupScope) == set(BACKUP_SCOPE_LABELS)


def test_copy_reason_messages_covers_all_enum_values() -> None:
    # NO_VERSIONS_TO_COPY is intentionally absent: fmt_copy_reason() returns None for it
    # (handled as an informational override in fmt_copy_status, not an error message).
    excluded = {CopyReason.NO_VERSIONS_TO_COPY}
    assert set(CopyReason) - excluded == set(_COPY_REASON_MESSAGES)


def test_server_status_display_covers_all_enum_values() -> None:
    assert set(ServerStatus) == set(_SERVER_STATUS_DISPLAY)


def test_remote_storage_status_display_covers_all_enum_values() -> None:
    assert set(RemoteStorageStatus) == set(_REMOTE_STORAGE_STATUS_DISPLAY)


def test_remote_storage_type_display_covers_all_enum_values() -> None:
    assert set(RemoteStorageType) == set(_REMOTE_STORAGE_TYPE_DISPLAY)


def test_hypervisor_type_display_covers_all_enum_values() -> None:
    assert set(HypervisorType) == set(_HYPERVISOR_TYPE_DISPLAY)


def test_workload_stat_label_covers_all_enum_values() -> None:
    assert set(WorkloadStatType) == set(_WORKLOAD_STAT_LABEL)


def test_machine_workload_type_display_covers_all_enum_values() -> None:
    assert set(MachineWorkloadType) == set(_WORKLOAD_TYPE_DISPLAY)


def test_machine_os_type_display_covers_all_enum_values() -> None:
    assert set(MachineOsType) == set(_OS_TYPE_DISPLAY)


def test_machine_task_scope_display_covers_all_enum_values() -> None:
    assert set(MachineTaskScope) == set(_SCOPE_DISPLAY)


def test_weekday_labels_covers_all_enum_values() -> None:
    assert set(WeekDay) == set(_WEEKDAY_LABELS)


def test_schedule_frequency_labels_covers_all_enum_values() -> None:
    assert set(ScheduleFrequency) == set(_SCHEDULE_FREQUENCY_LABELS)


def test_restore_type_display_covers_all_enum_values() -> None:
    assert set(RestoreType) == set(_RESTORE_TYPE_DISPLAY)


def test_file_server_type_display_covers_all_enum_values() -> None:
    assert set(FileServerType) == set(_FILE_SERVER_TYPE_DISPLAY)


def test_m365_workload_type_display_covers_all_enum_values() -> None:
    assert set(M365WorkloadType) == set(_M365_WORKLOAD_TYPE_DISPLAY)


def test_plan_category_display_covers_all_enum_values() -> None:
    assert set(WorkloadCategory) == set(_PLAN_CATEGORY_DISPLAY)


def test_server_log_level_display_covers_all_enum_values() -> None:
    assert set(LogLevel) == set(_SERVER_LOG_LEVEL_DISPLAY)


def test_activity_log_type_display_covers_all_enum_values() -> None:
    assert set(APMActivityLogType) == set(_ACTIVITY_LOG_TYPE_DISPLAY)


def test_fmt_server_log_level_values() -> None:
    assert "Information" in fmt_server_log_level(LogLevel.INFO)
    assert "Warning" in fmt_server_log_level(LogLevel.WARNING)
    assert "Error" in fmt_server_log_level(LogLevel.ERROR)


def test_fmt_activity_log_type_values() -> None:
    assert fmt_activity_log_type(APMActivityLogType.PROTECTION) == "Data protection"
    assert fmt_activity_log_type(APMActivityLogType.SYSTEM) == "System management"
    assert fmt_activity_log_type(APMActivityLogType.DATA_ACCESS) == "Data access"
    assert fmt_activity_log_type(None) == "-"


def test_export_status_display_covers_all_enum_values() -> None:
    assert set(M365ExportStatus) == set(_EXPORT_STATUS_DISPLAY)


def test_fmt_export_status_values() -> None:
    assert "Ready to download" in fmt_export_status(M365ExportStatus.READY_TO_DOWNLOAD)
    assert "Failed" in fmt_export_status(M365ExportStatus.FAILED)


def test_fmt_usage_pct_thresholds() -> None:
    assert fmt_usage_pct(50) == " 50%"
    assert fmt_usage_pct(85) == "[yellow] 85%[/yellow]"
    assert fmt_usage_pct(95) == "[red] 95%[/red]"
    assert fmt_usage_pct(95, fixed_width=False) == "[red]95%[/red]"


def test_fmt_storage_usage() -> None:
    assert fmt_storage_usage("1.0 GB", "2.0 GB", None) == "-"
    assert fmt_storage_usage("1.0 GB", "2.0 GB", 50.0) == " 50% (1.0 GB / 2.0 GB)"


def test_fmt_management_url() -> None:
    assert fmt_management_url("", "443") == ""
    assert fmt_management_url("apm.corp.com", "443") == "https://apm.corp.com"
    assert fmt_management_url("apm.corp.com", "") == "https://apm.corp.com"
    assert fmt_management_url("apm.corp.com", "8443") == "https://apm.corp.com:8443"


def test_fmt_encryption_enabled() -> None:
    assert "Enabled" in fmt_encryption_enabled(True)
    assert "Disabled" in fmt_encryption_enabled(False)


def test_fmt_schedule_str_variants() -> None:
    assert fmt_schedule_str(ProtectionSchedule(ScheduleFrequency.MANUAL, start_time=None)) == "Manual"
    assert fmt_schedule_str(ProtectionSchedule(ScheduleFrequency.AFTER_BACKUP, start_time=None)) == "After Backup"
    assert fmt_schedule_str(ProtectionSchedule(ScheduleFrequency.HOURLY, start_time=time(0, 30))) == "Hourly at :30"
    assert fmt_schedule_str(ProtectionSchedule(ScheduleFrequency.DAILY, start_time=time(2, 0))) == "Daily, 02:00"
    weekly = ProtectionSchedule(
        ScheduleFrequency.WEEKLY, start_time=time(3, 0), weekdays=(WeekDay.MONDAY, WeekDay.FRIDAY),
    )
    assert fmt_schedule_str(weekly) == "Weekly on Mon., Fri., 03:00"


def test_fmt_hour_ranges() -> None:
    assert fmt_hour_ranges(frozenset(range(24))) == "unrestricted"
    assert fmt_hour_ranges(frozenset({0, 1, 2})) == "00:00–03:00"
    assert fmt_hour_ranges(frozenset({1, 2, 22, 23})) == "01:00–03:00, 22:00–24:00"


def test_fmt_advanced_rules_lines() -> None:
    retention = ProtectionRetentionPolicy(
        retention_type=RetentionType.KEEP_ADVANCED,
        days=1,
        versions=5,
        gfs=GFSRetention(daily_versions=7, weekly_versions=0, monthly_versions=12, yearly_versions=0),
    )
    lines = fmt_advanced_rules(retention)
    assert "Keep all versions for 1 day" in lines
    assert "Keep the latest version of the day for 7 days" in lines
    assert "Keep the latest version of the month for 12 months" in lines
    assert "Number of latest version to keep: 5 versions" in lines
    assert not any("week" in line and "weeks" in line for line in lines if "week for" in line)


def test_fmt_hypervisor_type_all_values() -> None:
    assert fmt_hypervisor_type(HypervisorType.VSPHERE_ESXI)            == "VMware vSphere (ESXi)"
    assert fmt_hypervisor_type(HypervisorType.VSPHERE_VCENTER)         == "VMware vSphere (vCenter)"
    assert fmt_hypervisor_type(HypervisorType.HYPERV_STANDALONE)       == "Microsoft Hyper-V (Standalone)"
    assert fmt_hypervisor_type(HypervisorType.HYPERV_SCVMM)            == "Microsoft Hyper-V (SCVMM)"
    assert fmt_hypervisor_type(HypervisorType.HYPERV_FAILOVER_CLUSTER) == "Microsoft Hyper-V (Failover Cluster)"
    assert fmt_hypervisor_type(HypervisorType.UNKNOWN)                 == "Unknown"


def test_fmt_remote_storage_usage_both_values() -> None:
    result = fmt_remote_storage_usage(453378, 366960877568)
    assert result == "442.8 KB (341.8 GB left)"


def test_fmt_remote_storage_usage_only_used() -> None:
    result = fmt_remote_storage_usage(453378, None)
    assert result == "442.8 KB"


def test_fmt_remote_storage_usage_zero_used() -> None:
    assert fmt_remote_storage_usage(0, 1073741824) == "0 B (1.0 GB left)"


def test_fmt_remote_storage_usage_no_data() -> None:
    assert fmt_remote_storage_usage(None, None) == "-"
    assert fmt_remote_storage_usage(None, 1073741824) == "-"


def test_fmt_remote_storage_type_aev_with_model() -> None:
    assert fmt_remote_storage_type(RemoteStorageType.ACTIVE_PROTECT_VAULT, "SA6400") == "ActiveProtect Vault (SA6400)"


def test_fmt_remote_storage_type_aev_empty_model() -> None:
    assert fmt_remote_storage_type(RemoteStorageType.ACTIVE_PROTECT_VAULT, "") == "ActiveProtect Vault"


def test_fmt_remote_storage_type_cloud() -> None:
    assert fmt_remote_storage_type(RemoteStorageType.AMAZON_S3, "ignored") == "Amazon S3"
    assert fmt_remote_storage_type(RemoteStorageType.AZURE_BLOB, "") == "Azure Blob Storage"
    assert fmt_remote_storage_type(RemoteStorageType.WASABI, "") == "Wasabi Cloud Object Storage"


def test_fmt_remote_storage_type_unknown() -> None:
    assert fmt_remote_storage_type(RemoteStorageType.UNKNOWN, "") == "Unknown"
    assert fmt_remote_storage_type(RemoteStorageType.UNKNOWN, "some-model") == "Unknown"


def test_fmt_schedule_frequency_values() -> None:
    assert fmt_schedule_frequency(ScheduleFrequency.MANUAL) == "Manual Backup"
    assert fmt_schedule_frequency(ScheduleFrequency.DAILY) == "Daily Backup"
    assert fmt_schedule_frequency(ScheduleFrequency.AFTER_BACKUP) == "After Backup"


def test_fmt_restore_type_values() -> None:
    assert fmt_restore_type(None) == "-"
    assert fmt_restore_type(RestoreType.FULL) == "Full Restore"


def test_fmt_retirement_retention_variants() -> None:
    assert fmt_retirement_retention(RetirementRetentionPolicy(days=30, keep_latest_version=True)) == "30 days + latest"
    assert fmt_retirement_retention(RetirementRetentionPolicy(days=1, keep_latest_version=False)) == "1 day"
    assert fmt_retirement_retention(RetirementRetentionPolicy(days=None, keep_latest_version=True)) == "Latest version"
    assert fmt_retirement_retention(RetirementRetentionPolicy(days=None, keep_latest_version=False)) == "Keep all"


def test_print_workload_detail_renders_all_sections() -> None:
    from datetime import UTC, datetime

    from rich.console import Console

    from synology_apm.cli._display import print_workload_detail
    from synology_apm.sdk.models.location import LocationInfo
    from synology_apm.sdk.models.protection_plan import ProtectionPlan
    from synology_apm.sdk.models.workload import Workload

    wl = Workload(
        workload_id="wl-id-001",
        name="CORP-PC-001",
        category=WorkloadCategory.MACHINE,
        namespace="ns-001",
        last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        is_retired=False,
        protected_data_bytes=1024**3,
        status=WorkloadStatus.SUCCESS,
        plan=ProtectionPlan(plan_id="plan-id-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
        backup_copy_data_bytes=2 * 1024**3,
        backup_server=LocationInfo(
            is_remote_storage=False, identifier="ns-server-001",
            name="apm-server-01", endpoint="192.0.2.1", vault=None,
        ),
    )
    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        print_workload_detail(
            console, wl,
            type_label="Machine / PC/Mac",
            info_rows=[("Host", "esxi1.example.com (ESXi)")],
            status_rows=[("Verification", "OK")],
        )
    out = capture.get()

    assert "Workload: CORP-PC-001" in out
    assert "  Type:           Machine / PC/Mac" in out
    assert "  Host:           esxi1.example.com (ESXi)" in out  # info_rows land in Workload Information
    assert "  Verification:   OK" in out                        # status_rows land after Status
    assert "  Status:" in out
    assert "  Plan:           Daily Backup" in out
    assert "  Plan ID:        plan-id-001" in out
    assert "  Protected Size: 1.0 GB" in out
    assert "  Copy Size:      2.0 GB" in out
    assert "  Backup Server:  apm-server-01" in out


def test_print_workload_detail_hides_status_when_retired_and_copy_when_zero() -> None:
    from rich.console import Console

    from synology_apm.cli._display import print_workload_detail
    from synology_apm.sdk.models.retirement_plan import RetirementPlan
    from synology_apm.sdk.models.workload import Workload

    wl = Workload(
        workload_id="wl-id-002",
        name="old-laptop",
        category=WorkloadCategory.MACHINE,
        namespace="ns-001",
        last_backup_at=None,
        is_retired=True,
        protected_data_bytes=0,
        status=WorkloadStatus.RETIRED,
        plan=RetirementPlan(plan_id="retire-plan-001", name="Compliance Retention"),
    )
    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        print_workload_detail(console, wl, type_label="Machine / PC/Mac")
    out = capture.get()

    assert "Status:" not in out      # suppressed for retired workloads
    assert "Copy Size:" not in out   # suppressed when no copy data
    assert "  Plan:           Compliance Retention" in out
