"""Response parsers and API string maps for backup/restore activity collections.

Private module backing collections/activities.py; converts raw activity API response
objects into SDK models. The API↔enum string maps defined here (status filters, workload
subtype, cancel-request type) are also the source of truth for the collection classes'
list()/cancel() request-body construction.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    BackupScope,
    HypervisorType,
    LogLevel,
    M365WorkloadType,
    MachineWorkloadType,
    RestoreActivityStatus,
    RestoreType,
    VerifyStatus,
    WorkloadCategory,
)
from ..models.activity import ActivityLogEntry, BackupActivity, RestoreActivity
from ..models.hypervisor import Hypervisor
from ..models.location import LocationInfo
from ._shared import _parse_int_or_none, _parse_ts_optional, _parse_ts_or_now, _parse_verify_status_core
from .hypervisors import _HOST_TYPE_MAP

_BACKUP_SCOPE_MAP: dict[str, BackupScope] = {
    "ENTIRE_DEVICE_WITH_EXT_DRIVES": BackupScope.ENTIRE_DEVICE_WITH_EXT_DRIVES,
    "ENTIRE_DEVICE":                 BackupScope.ENTIRE_DEVICE,
    "VOLUME":                        BackupScope.VOLUME,
    "FILE":                          BackupScope.FILE,
}

_BACKUP_STATUS_MAP: dict[str, BackupActivityStatus] = {
    "QUEUING":           BackupActivityStatus.QUEUING,
    "BACKUPING":         BackupActivityStatus.BACKING_UP,
    "CANCELING":         BackupActivityStatus.CANCELING,
    "SUCCESS":           BackupActivityStatus.SUCCESS,
    "FAILED":            BackupActivityStatus.FAILED,
    "ERROR":             BackupActivityStatus.FAILED,
    "UNKNOWN":           BackupActivityStatus.FAILED,
    "WARNING":           BackupActivityStatus.PARTIAL,
    "CANCELED":          BackupActivityStatus.CANCELED,
    "NOT_BACKED_UP_YET": BackupActivityStatus.QUEUING,
}

# SDK BackupActivityStatus → API backupStatus values (one-to-many)
_BACKUP_STATUS_TO_API: dict[BackupActivityStatus, list[str]] = {
    BackupActivityStatus.QUEUING:    ["QUEUING", "NOT_BACKED_UP_YET"],
    BackupActivityStatus.BACKING_UP: ["BACKUPING"],
    BackupActivityStatus.CANCELING:  ["CANCELING"],
    BackupActivityStatus.SUCCESS:    ["SUCCESS"],
    BackupActivityStatus.FAILED:     ["ERROR", "UNKNOWN"],
    BackupActivityStatus.PARTIAL:    ["WARNING"],
    BackupActivityStatus.CANCELED:   ["CANCELED"],
}

_RESTORE_STATUS_MAP: dict[str, RestoreActivityStatus] = {
    "PREPARING":           RestoreActivityStatus.PREPARING,
    "RESTORING":           RestoreActivityStatus.RESTORING,
    "CANCELING":           RestoreActivityStatus.CANCELING,
    "READY_FOR_MIGRATE":   RestoreActivityStatus.READY_FOR_MIGRATE,
    "MIGRATE_VM_MANUALLY": RestoreActivityStatus.MIGRATE_VM_MANUALLY,
    "MIGRATING":           RestoreActivityStatus.MIGRATING,
    "SUCCESS":             RestoreActivityStatus.SUCCESS,
    "FAILED":              RestoreActivityStatus.FAILED,
    "ERROR":               RestoreActivityStatus.FAILED,
    "DEVICE_MISSING":      RestoreActivityStatus.FAILED,
    "MIGRATE_FAILED":      RestoreActivityStatus.FAILED,
    "WARNING":             RestoreActivityStatus.PARTIAL,
    "PARTIAL_SUCCESS":     RestoreActivityStatus.PARTIAL,
    "CANCELED":            RestoreActivityStatus.CANCELED,
}

# SDK RestoreActivityStatus → API restoreStatus values (one-to-many)
_RESTORE_STATUS_TO_API: dict[RestoreActivityStatus, list[str]] = {
    RestoreActivityStatus.PREPARING:           ["PREPARING"],
    RestoreActivityStatus.RESTORING:           ["RESTORING"],
    RestoreActivityStatus.CANCELING:           ["CANCELING"],
    RestoreActivityStatus.READY_FOR_MIGRATE:   ["READY_FOR_MIGRATE"],
    RestoreActivityStatus.MIGRATE_VM_MANUALLY: ["MIGRATE_VM_MANUALLY"],
    RestoreActivityStatus.MIGRATING:           ["MIGRATING"],
    RestoreActivityStatus.SUCCESS:             ["SUCCESS"],
    RestoreActivityStatus.FAILED:              ["FAILED", "DEVICE_MISSING", "MIGRATE_FAILED"],
    RestoreActivityStatus.PARTIAL:             ["WARNING", "PARTIAL_SUCCESS"],
    RestoreActivityStatus.CANCELED:            ["CANCELED"],
}

_RESTORE_TYPE_MAP: dict[str, RestoreType] = {
    "FILE_LEVEL_RESTORE":         RestoreType.FILE_LEVEL,
    "FULL_RESTORE":               RestoreType.FULL,
    "RESTORE_SYSTEM_VOLUME":      RestoreType.SYSTEM_VOLUME,
    "RESTORE_CUSTOMIZED_VOLUME":  RestoreType.CUSTOMIZED_VOLUME,
    "VM_FULL_RESTORE":            RestoreType.VM_FULL,
    "INSTANT_RESTORE_AEM":        RestoreType.INSTANT_AEM,
    "INSTANT_RESTORE_VMWARE":     RestoreType.INSTANT_VMWARE,
    "INSTANT_RESTORE_HYPERV":     RestoreType.INSTANT_HYPERV,
    "ORACLE_DATABASE_RESTORE":    RestoreType.ORACLE_DATABASE,
    "MSSQL_DATABASE_RESTORE":     RestoreType.MSSQL_DATABASE,
    "INSTANT_RESTORE_NUTANIX":    RestoreType.INSTANT_NUTANIX,
    "INSTANT_RESTORE_PROXMOX":    RestoreType.INSTANT_PROXMOX,
}

_MACHINE_TYPE_TO_CATEGORY_SERVICE: dict[MachineWorkloadType, str] = {
    MachineWorkloadType.PC: "MACHINE_PC",
    MachineWorkloadType.PS: "MACHINE_PS",
    MachineWorkloadType.VM: "MACHINE_VM",
    MachineWorkloadType.FS: "MACHINE_FS",
}

_M365_TYPE_TO_SAAS_SERVICE: dict[M365WorkloadType, str] = {
    M365WorkloadType.EXCHANGE:   "M365_USER_EXCHANGE",
    M365WorkloadType.ONEDRIVE:   "M365_USER_DRIVE",
    M365WorkloadType.CHAT:       "M365_USER_CHAT",
    M365WorkloadType.SHAREPOINT: "M365_SITE",
    M365WorkloadType.TEAMS:      "M365_TEAMS",
    M365WorkloadType.GROUP:      "M365_GROUP_EXCHANGE",
}

# API workloadType raw string → ActivityWorkloadType enum
_RAW_TO_SUBTYPE: dict[str, ActivityWorkloadType] = {
    "MACHINE_PC":         ActivityWorkloadType.MACHINE_PC,
    "MACHINE_PS":         ActivityWorkloadType.MACHINE_PS,
    "MACHINE_VM":         ActivityWorkloadType.MACHINE_VM,
    "MACHINE_FS":         ActivityWorkloadType.MACHINE_FS,
    "MACHINE_CLOUDVM":    ActivityWorkloadType.MACHINE_CLOUDVM,
    "APPLICATION_M365":   ActivityWorkloadType.M365,
    "APPLICATION_GW":     ActivityWorkloadType.GWS,
    "APPLICATION_ORACLE": ActivityWorkloadType.ORACLE,
    "APPLICATION_MSSQL":  ActivityWorkloadType.MSSQL,
}

# ActivityWorkloadType → WorkloadCategory
_ACTIVITY_TYPE_TO_CATEGORY: dict[ActivityWorkloadType, WorkloadCategory] = {
    ActivityWorkloadType.MACHINE_PC:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_PS:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_VM:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_FS:      WorkloadCategory.MACHINE,
    ActivityWorkloadType.MACHINE_CLOUDVM: WorkloadCategory.MACHINE,
    ActivityWorkloadType.M365:            WorkloadCategory.M365,
    ActivityWorkloadType.GWS:             WorkloadCategory.GWS,
    ActivityWorkloadType.ORACLE:          WorkloadCategory.MACHINE,
    ActivityWorkloadType.MSSQL:           WorkloadCategory.MACHINE,
    ActivityWorkloadType.UNKNOWN:         WorkloadCategory.MACHINE,
}

# ActivityWorkloadType → API workloadType string for restore cancel requests
_SUBTYPE_TO_CANCEL_TYPE: dict[ActivityWorkloadType, str] = {
    ActivityWorkloadType.MACHINE_PC:      "MACHINE_PC",
    ActivityWorkloadType.MACHINE_PS:      "MACHINE_PS",
    ActivityWorkloadType.MACHINE_VM:      "MACHINE_VM",
    ActivityWorkloadType.MACHINE_FS:      "MACHINE_FS",
    ActivityWorkloadType.MACHINE_CLOUDVM: "MACHINE_CLOUDVM",
    ActivityWorkloadType.M365:            "APPLICATION_M365",
    ActivityWorkloadType.GWS:             "APPLICATION_GW",
    ActivityWorkloadType.ORACLE:          "APPLICATION_ORACLE",
    ActivityWorkloadType.MSSQL:           "APPLICATION_MSSQL",
}

# Subtypes whose activities track per-item processed counts (FS and M365)
_ITEM_BASED_SUBTYPES: frozenset[ActivityWorkloadType] = frozenset({
    ActivityWorkloadType.MACHINE_FS,
    ActivityWorkloadType.M365,
})

# Subtypes that support backup verification (PS and VM only)
_VERIFY_SUPPORTED_SUBTYPES: frozenset[ActivityWorkloadType] = frozenset({
    ActivityWorkloadType.MACHINE_PS,
    ActivityWorkloadType.MACHINE_VM,
})

_LOG_LEVEL_MAP: dict[str, LogLevel] = {
    "LEVEL_INFORMATION": LogLevel.INFO,
    "LEVEL_WARNING":     LogLevel.WARNING,
    "LEVEL_ERROR":       LogLevel.ERROR,
}


def _parse_data_sizes(status: dict[str, Any]) -> tuple[int | None, int | None]:
    """Parse changeDataSize / dedupedDataSize. "-1" becomes None; 0 and positive values are kept as-is."""
    return (
        _parse_int_or_none(status.get("changeDataSize")),
        _parse_int_or_none(status.get("dedupedDataSize")),
    )


def _parse_log_entries(raw_logs: list[dict[str, Any]]) -> tuple[ActivityLogEntry, ...]:
    entries = []
    for log in raw_logs:
        dt = _parse_ts_or_now(log.get("timestamp"))
        level = _LOG_LEVEL_MAP.get(log.get("level", ""), LogLevel.INFO)
        entries.append(ActivityLogEntry(timestamp=dt, level=level, message=log.get("description", "")))
    return tuple(entries)


def _parse_restore_from_info(raw: dict[str, Any] | None) -> LocationInfo | None:
    """Build a LocationInfo from spec.restoreFromInfo.

    Returns None when raw is missing or empty. `identifier` is always ""
    -- restoreFromInfo has no namespace/uid equivalent.
    """
    if not raw:
        return None
    return LocationInfo(
        is_remote_storage=raw.get("destinationType", "APPLIANCE") != "APPLIANCE",
        identifier="",
        name=raw.get("hostname", ""),
        endpoint=raw.get("address", ""),
        vault=raw.get("containerName") or None,
    )


def _parse_destination_inventory(machine_info: dict[str, Any] | None) -> Hypervisor | None:
    """Build a Hypervisor from spec.machineInfo.additionalInfo (a JSON-encoded string).

    Only hostname, address, and host_type are populated (from inventory_name,
    inventory_addr, inventory_type via the existing _HOST_TYPE_MAP); the
    remaining Hypervisor fields have no equivalent in additionalInfo and are
    left empty/zero. Returns None when machineInfo/additionalInfo is missing,
    not valid JSON, or inventory_name is empty.
    """
    if not machine_info:
        return None
    raw_info = machine_info.get("additionalInfo")
    if not raw_info:
        return None
    try:
        info = json.loads(raw_info)
    except (TypeError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    name = info.get("inventory_name") or ""
    if not name:
        return None
    return Hypervisor(
        hypervisor_id="",
        hostname=name,
        address=info.get("inventory_addr", ""),
        host_type=_HOST_TYPE_MAP.get(info.get("inventory_type", ""), HypervisorType.UNKNOWN),
        account="",
        description="",
        port=0,
        version="",
    )


def _parse_activity_common(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse the constructor fields shared by backup and restore activities."""
    spec: dict[str, Any] = raw.get("spec", {})
    status_raw: dict[str, Any] = raw.get("status", {})

    subtype = _RAW_TO_SUBTYPE.get(spec.get("workloadType", ""), ActivityWorkloadType.UNKNOWN)
    workload_ref = spec.get("workload", {})

    processed_success_count = processed_warning_count = processed_error_count = None
    if subtype in _ITEM_BASED_SUBTYPES:
        processed_success_count = int(status_raw.get("processedSuccessCount", 0))
        processed_warning_count = int(status_raw.get("processedWarningCount", 0))
        processed_error_count   = int(status_raw.get("processedErrorCount", 0))

    return {
        "activity_id": raw.get("uid", ""),
        "workload_id": workload_ref.get("uid", ""),
        "workload_name": spec.get("workloadName", ""),
        "category": _ACTIVITY_TYPE_TO_CATEGORY[subtype],
        "workload_type": subtype,
        "namespace": raw.get("namespace", ""),
        "plan_name": spec.get("planName", ""),
        "started_at": _parse_ts_or_now(status_raw.get("startTime")),
        "finished_at": _parse_ts_optional(status_raw.get("endTime")),
        "progress": int(status_raw.get("progress", 0)),
        "processed_success_count": processed_success_count,
        "processed_warning_count": processed_warning_count,
        "processed_error_count": processed_error_count,
        "workload_namespace": workload_ref.get("namespace", ""),
    }


def _parse_restore_activity(raw: dict[str, Any]) -> RestoreActivity:
    """Convert a restore activity object from an API response to the SDK RestoreActivity model."""
    spec: dict[str, Any] = raw.get("spec", {})
    status_raw: dict[str, Any] = raw.get("status", {})
    common = _parse_activity_common(raw)

    # Restore API does not return durationTime; compute from start/end if available.
    duration_raw = status_raw.get("durationTime")
    finished_at: datetime | None = common["finished_at"]
    if duration_raw is not None:
        _d = int(duration_raw)
        duration_seconds: int | None = _d if _d > 0 else None
    elif finished_at is not None:
        delta = int(finished_at.timestamp()) - int(common["started_at"].timestamp())
        duration_seconds = delta if delta > 0 else None
    else:
        duration_seconds = None

    restore_status = status_raw.get("restoreStatus", "")

    return RestoreActivity(
        **common,
        execution_id=spec.get("executionId", ""),
        status=_RESTORE_STATUS_MAP.get(restore_status, RestoreActivityStatus.RESTORING),
        duration_seconds=duration_seconds,
        data_transferred_bytes=_parse_int_or_none(status_raw.get("transferredSize")),
        restore_type=(
            _RESTORE_TYPE_MAP.get(spec.get("restoreType", ""), RestoreType.UNKNOWN)
            if spec.get("restoreType") else None
        ),
        restore_destination=spec.get("destination"),
        operator=spec.get("operator"),
        version_timestamp=_parse_ts_optional(spec.get("versionTimestamp")),
        restore_from_info=_parse_restore_from_info(spec.get("restoreFromInfo")),
        destination_path=spec.get("destinationPath") or None,
        destination_inventory=_parse_destination_inventory(spec.get("machineInfo")),
    )


def _parse_backup_activity(raw: dict[str, Any]) -> BackupActivity:
    """Convert an activity object from an API response to the SDK BackupActivity model."""
    status_raw: dict[str, Any] = raw.get("status", {})
    common = _parse_activity_common(raw)

    duration_raw = status_raw.get("durationTime", "0")
    _duration_int = int(duration_raw) if duration_raw else 0
    duration_seconds = _duration_int if _duration_int > 0 else None

    data_change, data_deduped = _parse_data_sizes(status_raw)

    backup_status = status_raw.get("backupStatus", "")

    machine_status_info: dict[str, Any] | None = status_raw.get("machineStatusInfo")
    verify_status: VerifyStatus | None = None
    if machine_status_info is not None:
        subtype: ActivityWorkloadType = common["workload_type"]
        verify_status = _parse_verify_status_core(
            machine_status_info.get("verifyStatus"), verify_supported=subtype in _VERIFY_SUPPORTED_SUBTYPES
        )

    return BackupActivity(
        **common,
        execution_id=status_raw.get("executionId", ""),
        status=_BACKUP_STATUS_MAP.get(backup_status, BackupActivityStatus.BACKING_UP),
        duration_seconds=duration_seconds,
        data_transferred_bytes=_parse_int_or_none(status_raw.get("transferredDataSize")),
        verify_status=verify_status,
        data_change_bytes=data_change,
        data_deduped_bytes=data_deduped,
    )
