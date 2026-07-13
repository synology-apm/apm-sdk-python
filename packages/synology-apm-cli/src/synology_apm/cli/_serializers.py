"""CLI serializers — SDK model → JSON/CSV-safe dict conversions."""
from __future__ import annotations

from typing import Any

from synology_apm.cli._display import fmt_datetime_iso, fmt_schedule_frequency, fmt_schedule_label
from synology_apm.cli.output import to_local_iso as _iso
from synology_apm.sdk import (
    APMActivityLog,
    BackupActivity,
    BackupCopyPolicy,
    BackupServer,
    ConnectionLog,
    DriveLog,
    Hypervisor,
    LocationInfo,
    M365ExportActivity,
    M365Workload,
    MachineBackupWindow,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineWorkload,
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorage,
    RestoreActivity,
    RetirementPlan,
    SaasTenant,
    SiteInfo,
    SystemLog,
    TieringPlan,
    TieringStatus,
    WorkloadVersion,
)


def tiering_status_to_dict(ts: TieringStatus | None) -> dict[str, Any] | None:
    """Serialize a TieringStatus to a JSON-safe dict; returns None when ts is None."""
    if ts is None:
        return None
    return {
        "status":                ts.status.value,
        "reason":                ts.reason.value if ts.reason else None,
        "pending_version_count": ts.pending_version_count,
        "remaining_bytes":       ts.remaining_bytes,
    }


def location_info_to_dict(loc: LocationInfo) -> dict[str, Any]:
    """Serialize a LocationInfo to a dict with all five fields."""
    return {
        "is_remote_storage": loc.is_remote_storage,
        "identifier":        loc.identifier,
        "name":              loc.name,
        "endpoint":          loc.endpoint,
        "vault":             loc.vault,
    }


def workload_to_dict(wl: MachineWorkload) -> dict[str, Any]:
    """Serialize a MachineWorkload to a JSON/YAML-safe dict (selected fields, nested structure)."""
    bs = wl.backup_server
    bc = wl.backup_copy_destination
    return {
        "workload_id":    wl.workload_id,
        "name":           wl.name,
        "category":       wl.category.value,
        "workload_type":  wl.workload_type.value,
        "namespace":      wl.namespace,
        "is_retired":     wl.is_retired,
        "status":         wl.status.value,
        "plan_name":      wl.plan.name,
        "plan_id":        wl.plan.plan_id,
        "last_backup_at": _iso(wl.last_backup_at),
        "protected_data_bytes":   wl.protected_data_bytes,
        "backup_copy_data_bytes": wl.backup_copy_data_bytes,
        "backup_server":           location_info_to_dict(bs) if bs else None,
        "backup_copy_destination": location_info_to_dict(bc) if bc else None,
        "verify_status":  wl.verify_status.value if wl.verify_status else None,
        "agent_version":  wl.agent_version,
        "device_uuid":    wl.device_uuid,
        "ip_address":     wl.ip_address,
        "inventory_name": wl.inventory_name,
        "inventory_type": wl.inventory_type,
    }


def workload_to_csv_row(wl: MachineWorkload) -> dict[str, Any]:
    """Serialize a MachineWorkload to a CSV-safe flat dict (table columns, raw values)."""
    bs = wl.backup_server
    bc = wl.backup_copy_destination
    return {
        "name":                   wl.name,
        "workload_type":          wl.workload_type.value,
        "status":                 wl.status.value,
        "verify_status":          wl.verify_status.value if wl.verify_status else "",
        "last_backup_at":         _iso(wl.last_backup_at) or "",
        "protected_data_bytes":   wl.protected_data_bytes,
        "backup_copy_data_bytes": wl.backup_copy_data_bytes,
        "plan_name":              wl.plan.name,
        "plan_id":                wl.plan.plan_id,
        "backup_server_name":     bs.name if bs else "",
        "copy_destination_name":  bc.name        if bc else "",
        "copy_destination_vault": (bc.vault or "") if bc else "",
        "ip_address":             wl.ip_address or "",
        "workload_id":            wl.workload_id,
        "namespace":              wl.namespace,
    }


def server_to_dict(server: BackupServer) -> dict[str, Any]:
    """Serialize a BackupServer to a JSON/YAML-safe dict."""
    dest = server.tiering_plan_destination
    return {
        "backup_server_id":   server.backup_server_id,
        "namespace":          server.namespace,
        "name":               server.name,
        "hostname":           server.hostname,
        "model":              server.model,
        "system_version":     server.system_version,
        "description":        server.description,
        "status":             server.status.value,
        "is_updating":        server.is_updating,
        "serial":             server.serial,
        "role":               server.role.value if server.role is not None else None,
        "storage_total_bytes":           server.storage_total_bytes,
        "storage_used_bytes":            server.storage_used_bytes,
        "logical_backup_data_bytes":     server.logical_backup_data_bytes,
        "physical_backup_data_bytes":    server.physical_backup_data_bytes,
        "backup_data_reduction_bytes":   server.backup_data_reduction_bytes,
        "backup_data_reduction_ratio":   (
            round(server.backup_data_reduction_ratio, 1) if server.backup_data_reduction_bytes is not None else None
        ),
        "tiering_plan_name": server.tiering_plan_name,
        "tiering_plan_destination": location_info_to_dict(dest) if dest is not None else None,
        "tiering_status": tiering_status_to_dict(server.tiering_status),
    }


def server_to_csv_row(server: BackupServer) -> dict[str, Any]:
    """Serialize a BackupServer to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "name":                  server.name,
        "serial":                server.serial,
        "hostname":              server.hostname,
        "model":                 server.model,
        "system_version":        server.system_version or "",
        "status":                server.status.value,
        "storage_used_bytes":    server.storage_used_bytes if server.storage_used_bytes is not None else "",
        "storage_total_bytes":   server.storage_total_bytes if server.storage_total_bytes is not None else "",
        "tiering_plan_name":     server.tiering_plan_name or "",
        "tiering_status":        server.tiering_status.status.value if server.tiering_status else "",
        "description":           server.description,
        "backup_server_id":      server.backup_server_id,
        "namespace":             server.namespace,
    }


# ── Plan serializer helpers ───────────────────────────────────────────────────

def _task_schedule_to_dict(sched: MachineTaskSchedule | None) -> dict[str, Any] | None:
    if sched is None:
        return None
    ts = sched.time_schedule
    et = sched.event_trigger
    if ts is None and et is None:
        return None
    return {
        "time_schedule": {
            "frequency":  ts.frequency.value,
            "start_time": (
                f"{ts.start_time.hour:02d}:{ts.start_time.minute:02d}"
                if ts.start_time else None
            ),
            "weekdays": [d.name.lower() for d in sorted(ts.weekdays, key=lambda w: w.value)],
        } if ts else None,
        "event_trigger": {
            "on_sign_out":          et.on_sign_out,
            "on_lock":              et.on_lock,
            "on_startup":           et.on_startup,
            "min_interval_seconds": int(et.min_interval.total_seconds()),
        } if et else None,
    }


def _task_config_to_dict(task: MachineTaskConfig) -> dict[str, Any]:
    return {
        "workload_type":          task.workload_type.value,
        "os_type":                task.os_type.value,
        "scope":                  task.scope.value if task.scope else None,
        "custom_volumes":         list(task.custom_volumes),
        "include_external_drives": task.include_external_drives,
        "include_boot_partition":  task.include_boot_partition,
        "use_main_schedule":       task.use_main_schedule,
        "schedule":               _task_schedule_to_dict(task.schedule),
    }


def _backup_window_to_dict(window: MachineBackupWindow) -> dict[str, Any]:
    return {
        "enabled": window.enabled,
        "allowed_hours": {
            day.name.lower(): sorted(hours)
            for day, hours in sorted(window.allowed_hours.items(), key=lambda x: x[0].value)
        },
    }



def _retention_policy_to_dict(r: ProtectionRetentionPolicy) -> dict[str, Any]:
    return {
        "type": r.retention_type.value,
        "days": r.days,
        "versions": r.versions,
        "gfs": {
            "daily_versions":   r.gfs.daily_versions,
            "weekly_versions":  r.gfs.weekly_versions,
            "monthly_versions": r.gfs.monthly_versions,
            "yearly_versions":  r.gfs.yearly_versions,
        } if r.gfs else None,
    }


def _schedule_to_dict(sch: ProtectionSchedule) -> dict[str, Any]:
    return {
        "frequency":  sch.frequency.value,
        "start_time": (
            f"{sch.start_time.hour:02d}:{sch.start_time.minute:02d}"
            if sch.start_time else None
        ),
        "weekdays": [d.name.lower() for d in sorted(sch.weekdays, key=lambda w: w.value)],
    }


def _policy_to_dict(policy: ProtectionPlanPolicy) -> dict[str, Any]:
    return {
        "retention": _retention_policy_to_dict(policy.retention),
        "schedule_label": fmt_schedule_label(policy),
        "schedule": _schedule_to_dict(policy.schedule) if policy.schedule else None,
    }


def _backup_copy_policy_to_dict(bcd: BackupCopyPolicy) -> dict[str, Any]:
    return {
        "destination": location_info_to_dict(bcd.destination),
        "retention": _retention_policy_to_dict(bcd.retention),
        "schedule_label": fmt_schedule_frequency(bcd.schedule.frequency),
        "schedule": _schedule_to_dict(bcd.schedule),
    }


def protection_plan_to_dict(plan: ProtectionPlan) -> dict[str, Any]:
    """Serialize a ProtectionPlan to a JSON/YAML-safe dict."""
    # always populated for plans fetched via the plans/machine.plans/m365.plans collection
    assert plan.policy is not None and plan.workload_count is not None
    bcd = plan.backup_copy_policy
    bcs = plan.backup_copy_status
    return {
        "plan_id": plan.plan_id,
        "name": plan.name,
        "category": plan.category.value,
        "description": plan.description,
        "policy": _policy_to_dict(plan.policy),
        "workload_count": plan.workload_count,
        "successful_workload_count": plan.successful_workload_count,
        "unsuccessful_workload_count": plan.unsuccessful_workload_count,
        "is_immutable": plan.is_immutable,
        "backup_copy_policy": _backup_copy_policy_to_dict(bcd) if bcd else None,
        "backup_copy_status": {
            "status": bcs.status.value,
            "reason": bcs.reason.value if bcs.reason else None,
            "pending_version_count": bcs.pending_version_count,
            "remaining_bytes": bcs.remaining_bytes,
            "skipped_workload_count": bcs.skipped_workload_count,
        } if bcs else None,
        "tasks": [_task_config_to_dict(t) for t in plan.tasks] if plan.tasks is not None else None,
        "backup_window": _backup_window_to_dict(plan.backup_window) if plan.backup_window is not None else None,
    }


def protection_plan_to_csv_row(plan: ProtectionPlan) -> dict[str, Any]:
    """Serialize a ProtectionPlan to a CSV-safe flat dict (table columns, raw values)."""
    # always populated for plans fetched via the plans/machine.plans/m365.plans collection
    assert plan.policy is not None and plan.workload_count is not None
    r = plan.policy.retention
    bcd = plan.backup_copy_policy
    cr = bcd.retention if bcd else None
    bcs = plan.backup_copy_status
    return {
        "plan_id":                    plan.plan_id,
        "name":                       plan.name,
        "category":                   plan.category.value,
        "description":                plan.description or "",
        "is_immutable":               plan.is_immutable,
        "retention_type":             r.retention_type.value,
        "retention_days":             r.days if r.days is not None else "",
        "retention_versions":         r.versions if r.versions is not None else "",
        "schedule_label":             fmt_schedule_label(plan.policy) or "",
        "copy_destination_name":      bcd.destination.name          if bcd else "",
        "copy_destination_vault":     (bcd.destination.vault or "") if bcd else "",
        "copy_retention_type":        cr.retention_type.value if cr else "",
        "copy_retention_days":        cr.days if cr and cr.days is not None else "",
        "copy_retention_versions":    cr.versions if cr and cr.versions is not None else "",
        "copy_schedule_label":        (fmt_schedule_frequency(bcd.schedule.frequency) if bcd else ""),
        "copy_status":                bcs.status.value if bcs else "",
        "workload_count":             plan.workload_count,
        "successful_workload_count":   plan.successful_workload_count,
        "unsuccessful_workload_count": plan.unsuccessful_workload_count,
    }


def retirement_plan_to_dict(plan: RetirementPlan) -> dict[str, Any]:
    """Serialize a RetirementPlan to a JSON/YAML-safe dict."""
    # always populated for plans fetched via the retirement_plans collection
    assert plan.retention is not None and plan.workload_count is not None
    r = plan.retention
    return {
        "plan_id": plan.plan_id,
        "name": plan.name,
        "description": plan.description,
        "retention": {
            "days": r.days,
            "keep_latest_version": r.keep_latest_version,
        },
        "workload_count": plan.workload_count,
    }


def retirement_plan_to_csv_row(plan: RetirementPlan) -> dict[str, Any]:
    """Serialize a RetirementPlan to a CSV-safe flat dict (table columns, raw values)."""
    # always populated for plans fetched via the retirement_plans collection
    assert plan.retention is not None and plan.workload_count is not None
    r = plan.retention
    return {
        "plan_id":                  plan.plan_id,
        "name":                     plan.name,
        "description":              plan.description or "",
        "retention_days":           r.days if r.days is not None else "",
        "retention_keep_latest":    r.keep_latest_version,
        "workload_count":           plan.workload_count,
    }


def tiering_plan_to_dict(plan: TieringPlan) -> dict[str, Any]:
    """Serialize a TieringPlan to a JSON/YAML-safe dict."""
    dest = plan.destination
    ts = plan.tiering_status
    return {
        "plan_id":            plan.plan_id,
        "name":               plan.name,
        "description":        plan.description,
        "tiering_after_days": plan.tiering_after_days,
        "daily_check_time":   f"{plan.daily_check_time.hour:02d}:{plan.daily_check_time.minute:02d}",
        "destination": location_info_to_dict(dest) if dest else None,
        "server_count":       plan.server_count,
        "tiering_status": tiering_status_to_dict(ts),
    }


def tiering_plan_to_csv_row(plan: TieringPlan) -> dict[str, Any]:
    """Serialize a TieringPlan to a CSV-safe flat dict (table columns, raw values)."""
    dest = plan.destination
    ts = plan.tiering_status
    return {
        "plan_id":            plan.plan_id,
        "name":               plan.name,
        "description":        plan.description or "",
        "tiering_after_days": plan.tiering_after_days,
        "daily_check_time":   f"{plan.daily_check_time.hour:02d}:{plan.daily_check_time.minute:02d}",
        "destination_name":   dest.name if dest else "",
        "destination_vault":  dest.vault if dest else "",
        "server_count":       plan.server_count,
        "tiering_status":     ts.status.value if ts else "",
    }


# ── Version / Activity serializers (unchanged) ────────────────────────────────

def version_to_dict(v: WorkloadVersion) -> dict[str, Any]:
    """Serialize a WorkloadVersion to a JSON-safe dict, excluding internal fields."""
    return {
        "version_id":         v.version_id,
        "created_at":         _iso(v.created_at),
        "status":             v.status.value,
        "locked":             v.locked,
        "changed_size_bytes": v.changed_size_bytes,
        "verify_status":      v.verify_status.value if v.verify_status else None,
        "copy_status":        v.copy_status.value if v.copy_status else None,
        "copy_reason":        v.copy_reason.value if v.copy_reason else None,
        "locations": [
            {"location_id": loc.location_id, **location_info_to_dict(loc.location_info)}
            for loc in v.locations
        ],
    }


def version_to_csv_row(v: WorkloadVersion) -> dict[str, Any]:
    """Serialize a WorkloadVersion to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "version_id":         v.version_id,
        "created_at":         _iso(v.created_at),
        "status":             v.status.value,
        "locked":             v.locked,
        "changed_size_bytes": v.changed_size_bytes if v.changed_size_bytes is not None else "",
        "verify_status":      v.verify_status.value if v.verify_status else "",
        "copy_status":        v.copy_status.value if v.copy_status else "",
        "location_count":     len(v.locations),
    }


def version_detail_to_dict(v: WorkloadVersion, act: BackupActivity) -> dict[str, Any]:
    """Serialize a WorkloadVersion + its backup activity into a single JSON-safe dict."""
    return {
        **version_to_dict(v),
        "workload_id": v.workload_id,
        "namespace":   v.namespace,
        "activity":    activity_to_dict(act),
    }


def backup_activity_to_csv_row(act: BackupActivity) -> dict[str, Any]:
    """Serialize a BackupActivity to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "workload_name":          act.workload_name,
        "status":                 act.status.value,
        "verify_status":          act.verify_status.value if act.verify_status else "",
        "started_at":             _iso(act.started_at),
        "duration_seconds":       act.duration_seconds if act.duration_seconds is not None else "",
        "activity_id":            act.activity_id,
        "data_transferred_bytes": act.data_transferred_bytes if act.data_transferred_bytes is not None else "",
        "workload_id":            act.workload_id,
        "workload_namespace":     act.workload_namespace,
    }


def restore_activity_to_csv_row(act: RestoreActivity) -> dict[str, Any]:
    """Serialize a RestoreActivity to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "workload_name":          act.workload_name,
        "restore_type":           act.restore_type.value if act.restore_type else "",
        "status":                 act.status.value,
        "started_at":             _iso(act.started_at) or "",
        "duration_seconds":       act.duration_seconds if act.duration_seconds is not None else "",
        "operator":               act.operator or "",
        "activity_id":            act.activity_id,
        "data_transferred_bytes": act.data_transferred_bytes if act.data_transferred_bytes is not None else "",
        "workload_id":            act.workload_id,
        "workload_namespace":     act.workload_namespace,
    }


def activity_to_dict(act: BackupActivity | RestoreActivity) -> dict[str, Any]:
    """Serialize a BackupActivity or RestoreActivity to a JSON-safe dict (selected fields only)."""
    d: dict[str, Any] = {
        "activity_id":            act.activity_id,
        "workload_id":            act.workload_id,
        "workload_namespace":     act.workload_namespace,
        "workload_name":          act.workload_name,
        "category":               act.category.value,
        "namespace":              act.namespace,
        "plan_name":              act.plan_name,
        "activity_type":          "backup" if isinstance(act, BackupActivity) else "restore",
        "status":                 act.status.value,
        "started_at":             _iso(act.started_at),
        "finished_at":            _iso(act.finished_at),
        "duration_seconds":       act.duration_seconds,
        "data_transferred_bytes": act.data_transferred_bytes,
        "progress":               act.progress,
    }
    if isinstance(act, BackupActivity):
        if act.backup_scope is not None:
            d["backup_scope"] = act.backup_scope.value
        d["data_change_bytes"]  = act.data_change_bytes
        d["data_deduped_bytes"] = act.data_deduped_bytes
    if act.processed_success_count is not None:
        d["processed_success_count"] = act.processed_success_count
        d["processed_warning_count"]  = act.processed_warning_count
        d["processed_error_count"]    = act.processed_error_count
    if act.log_entries is not None:
        d["log_entries"] = [
            {"timestamp": _iso(e.timestamp), "level": e.level.value, "message": e.message}
            for e in act.log_entries
        ]
    if isinstance(act, BackupActivity):
        if act.verify_status is not None:
            d["verify_status"] = act.verify_status.value
    if isinstance(act, RestoreActivity):
        if act.restore_type is not None:
            d["restore_type"] = act.restore_type.value
        if act.restore_destination is not None:
            d["restore_destination"] = act.restore_destination
        if act.operator is not None:
            d["operator"] = act.operator
        if act.version_timestamp is not None:
            d["version_timestamp"] = _iso(act.version_timestamp)
        if act.restore_from_info is not None:
            d["restore_from_info"] = location_info_to_dict(act.restore_from_info)
        if act.destination_path is not None:
            d["destination_path"] = act.destination_path
        if act.destination_inventory is not None:
            d["destination_inventory"] = {
                "hostname":  act.destination_inventory.hostname,
                "address":   act.destination_inventory.address,
                "host_type": act.destination_inventory.host_type.value,
            }
    return d


def activity_log_to_dict(e: APMActivityLog) -> dict[str, Any]:
    """Serialize an APMActivityLog entry to a JSON-safe dict."""
    return {
        "level":        e.level.value,
        "type":         e.log_type.value if e.log_type else None,
        "timestamp":    fmt_datetime_iso(e.timestamp),
        "username":     e.username,
        "description":  e.description,
    }


def activity_log_to_csv_row(e: APMActivityLog) -> dict[str, Any]:
    """Serialize an APMActivityLog entry to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "level":       e.level.value,
        "type":        e.log_type.value if e.log_type else "",
        "timestamp":   fmt_datetime_iso(e.timestamp) or "",
        "username":    e.username,
        "description": e.description,
    }


def drive_log_to_dict(e: DriveLog) -> dict[str, Any]:
    """Serialize a DriveLog entry to a JSON-safe dict."""
    return {
        "level":       e.level.value,
        "timestamp":   fmt_datetime_iso(e.timestamp),
        "description": e.description,
        "server_name": e.server_name,
        "model":       e.model,
        "location":    e.location,
        "serial":      e.serial,
    }


def drive_log_to_csv_row(e: DriveLog) -> dict[str, Any]:
    """Serialize a DriveLog entry to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "level":       e.level.value,
        "timestamp":   fmt_datetime_iso(e.timestamp) or "",
        "model":       e.model,
        "serial":      e.serial,
        "server_name": e.server_name,
        "location":    e.location,
        "description": e.description,
    }


def connection_log_to_dict(e: ConnectionLog) -> dict[str, Any]:
    """Serialize a ConnectionLog entry to a JSON-safe dict."""
    return {
        "level":       e.level.value,
        "timestamp":   fmt_datetime_iso(e.timestamp),
        "username":    e.username,
        "description": e.description,
    }


def connection_log_to_csv_row(e: ConnectionLog) -> dict[str, Any]:
    """Serialize a ConnectionLog entry to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "level":       e.level.value,
        "timestamp":   fmt_datetime_iso(e.timestamp) or "",
        "username":    e.username,
        "description": e.description,
    }


def system_log_to_dict(e: SystemLog) -> dict[str, Any]:
    """Serialize a SystemLog entry to a JSON-safe dict."""
    return {
        "level":       e.level.value,
        "timestamp":   fmt_datetime_iso(e.timestamp),
        "username":    e.username,
        "description": e.description,
    }


def system_log_to_csv_row(e: SystemLog) -> dict[str, Any]:
    """Serialize a SystemLog entry to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "level":       e.level.value,
        "timestamp":   fmt_datetime_iso(e.timestamp) or "",
        "username":    e.username,
        "description": e.description,
    }


def m365_workload_to_dict(wl: M365Workload) -> dict[str, Any]:
    """Serialize an M365Workload to a JSON/YAML-safe dict (selected fields, nested structure)."""
    bs = wl.backup_server
    bc = wl.backup_copy_destination
    return {
        "workload_id":    wl.workload_id,
        "name":           wl.name,
        "category":       wl.category.value,
        "workload_type":  wl.workload_type.value,
        "namespace":      wl.namespace,
        "tenant_id":      wl.tenant_id,
        "is_retired":     wl.is_retired,
        "status":         wl.status.value,
        "plan_name":      wl.plan.name,
        "plan_id":        wl.plan.plan_id,
        "last_backup_at": fmt_datetime_iso(wl.last_backup_at),
        "protected_data_bytes":   wl.protected_data_bytes,
        "backup_copy_data_bytes": wl.backup_copy_data_bytes,
        "info_label":             wl.info.label if wl.info else None,
        "backup_server":           location_info_to_dict(bs) if bs else None,
        "backup_copy_destination": location_info_to_dict(bc) if bc else None,
    }


def m365_workload_to_csv_row(wl: M365Workload) -> dict[str, Any]:
    """Serialize an M365Workload to a CSV-safe flat dict (table columns, raw values)."""
    bs = wl.backup_server
    bc = wl.backup_copy_destination
    return {
        "name":                   wl.name,
        "info_label":             wl.info.label if wl.info else "",
        "status":                 wl.status.value,
        "last_backup_at":         fmt_datetime_iso(wl.last_backup_at) or "",
        "protected_data_bytes":   wl.protected_data_bytes,
        "backup_copy_data_bytes": wl.backup_copy_data_bytes,
        "plan_name":              wl.plan.name,
        "plan_id":                wl.plan.plan_id,
        "backup_server_name":     bs.name if bs else "",
        "copy_destination_name":  bc.name        if bc else "",
        "copy_destination_vault": (bc.vault or "") if bc else "",
        "workload_id":            wl.workload_id,
        "namespace":              wl.namespace,
    }


def tenant_to_dict(t: SaasTenant) -> dict[str, Any]:
    """Serialize a SaasTenant to a JSON-safe dict."""
    return {
        "tenant_id": t.tenant_id,
        "tenant_name": t.tenant_name,
        "tenant_email": t.tenant_email,
        "category": t.category.value,
        "protected_data_bytes": t.protected_data_bytes,
    }


def site_info_to_dict(site: SiteInfo) -> dict[str, Any]:
    """Serialize a SiteInfo (management servers, storage, and workload usage) to a JSON-safe dict."""
    storage = site.site_storage
    usage = site.workload_usage
    return {
        "site_uuid":        site.site_uuid,
        "external_address": site.external_address,
        "port":             site.port,
        "primary_management_server": (
            server_to_dict(site.primary_management_server) if site.primary_management_server is not None else None
        ),
        "secondary_management_server": (
            server_to_dict(site.secondary_management_server)
            if site.secondary_management_server is not None else None
        ),
        "site_storage": {
            "logical_backup_data_bytes":  storage.logical_backup_data_bytes,
            "physical_backup_data_bytes": storage.physical_backup_data_bytes,
            "backup_data_reduction_bytes": storage.backup_data_reduction_bytes,
            "backup_data_reduction_ratio": round(storage.backup_data_reduction_ratio, 1),
        },
        "workload_usage": {
            "total_count":               usage.total_count,
            "total_protected_data_bytes": usage.total_protected_data_bytes,
            "by_type": [
                {
                    "workload_type":      s.workload_type.value,
                    "total_count":        s.total_count,
                    "protected_data_bytes": s.protected_data_bytes,
                }
                for s in usage.by_type
            ],
        },
    }


def hypervisor_to_dict(hypervisor: Hypervisor) -> dict[str, Any]:
    """Serialize a Hypervisor to a JSON-safe dict."""
    return {
        "hypervisor_id": hypervisor.hypervisor_id,
        "hostname":      hypervisor.hostname,
        "address":       hypervisor.address,
        "host_type":     hypervisor.host_type.value,
        "account":       hypervisor.account,
        "description":   hypervisor.description,
        "port":          hypervisor.port,
        "version":       hypervisor.version,
    }


def hypervisor_to_csv_row(hypervisor: Hypervisor) -> dict[str, Any]:
    """Serialize a Hypervisor to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "hostname":      hypervisor.hostname,
        "address":       hypervisor.address,
        "host_type":     hypervisor.host_type.value,
        "account":       hypervisor.account,
        "description":   hypervisor.description,
        "hypervisor_id": hypervisor.hypervisor_id,
    }


def remote_storage_to_dict(remote_storage: RemoteStorage) -> dict[str, Any]:
    """Serialize a RemoteStorage to a JSON-safe dict."""
    return {
        "storage_id":         remote_storage.storage_id,
        "name":               remote_storage.name,
        "storage_type":       remote_storage.storage_type.value,
        "device_model":       remote_storage.device_model,
        "endpoint":           remote_storage.endpoint,
        "encryption_enabled": remote_storage.encryption_enabled,
        "status":             remote_storage.status.value,
        "used_bytes":         remote_storage.used_bytes,
        "remaining_bytes":    remote_storage.remaining_bytes,
    }


def m365_export_activity_to_dict(a: M365ExportActivity) -> dict[str, Any]:
    """Serialize an M365ExportActivity to a JSON-safe dict."""
    return {
        "activity_id":        a.activity_id,
        "namespace":          a.namespace,
        "workload_id":        a.workload_id,
        "workload_namespace": a.workload_namespace,
        "item":               a.source_name,
        "version_timestamp":  fmt_datetime_iso(a.version_timestamp),
        "status":             a.status.value,
        "started_at":         fmt_datetime_iso(a.started_at),
        "finished_at":        fmt_datetime_iso(a.finished_at),
    }


def m365_export_activity_to_csv_row(a: M365ExportActivity) -> dict[str, Any]:
    """Serialize an M365ExportActivity to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "item":              a.source_name,
        "version_timestamp": fmt_datetime_iso(a.version_timestamp) or "",
        "status":            a.status.value,
        "started_at":        fmt_datetime_iso(a.started_at) or "",
        "finished_at":       fmt_datetime_iso(a.finished_at) or "",
        "activity_id":       a.activity_id,
    }
