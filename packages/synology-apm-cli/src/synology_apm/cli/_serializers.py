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
    M365ExportActivity,
    M365Workload,
    MachineTaskSchedule,
    MachineWorkload,
    ProtectionPlan,
    ProtectionPlanPolicy,
    RestoreActivity,
    RetirementPlan,
    SystemLog,
    TieringPlan,
    WorkloadVersion,
)


def workload_to_dict(wl: MachineWorkload) -> dict[str, Any]:
    """Serialize a MachineWorkload to a JSON/YAML-safe dict (local time, flattened plan)."""
    d = wl.to_dict()
    plan = d.pop("plan")
    d["plan_name"] = plan["name"]
    d["plan_id"] = plan["plan_id"]
    d["last_backup_at"] = _iso(wl.last_backup_at)
    return d


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
    if sched is None or (sched.time_schedule is None and sched.event_trigger is None):
        return None
    return sched.to_dict()


def _policy_to_dict(policy: ProtectionPlanPolicy, d: dict[str, Any]) -> dict[str, Any]:
    """Mutate a ProtectionPlanPolicy's already-serialized dict: rename retention_type, add schedule_label."""
    d["retention"]["type"] = d["retention"].pop("retention_type")
    d["schedule_label"] = fmt_schedule_label(policy)
    return d


def _backup_copy_policy_to_dict(bcd: BackupCopyPolicy, d: dict[str, Any]) -> dict[str, Any]:
    """Mutate a BackupCopyPolicy's already-serialized dict: rename retention_type, add schedule_label."""
    d["retention"]["type"] = d["retention"].pop("retention_type")
    d["schedule_label"] = fmt_schedule_frequency(bcd.schedule.frequency)
    return d


def protection_plan_to_dict(plan: ProtectionPlan) -> dict[str, Any]:
    """Serialize a ProtectionPlan to a JSON/YAML-safe dict."""
    # always populated for plans fetched via the plans/machine.plans/m365.plans collection
    assert plan.policy is not None and plan.workload_count is not None
    d = plan.to_dict()
    d["policy"] = _policy_to_dict(plan.policy, d["policy"])
    d["backup_copy_policy"] = (
        _backup_copy_policy_to_dict(plan.backup_copy_policy, d["backup_copy_policy"])
        if plan.backup_copy_policy else None
    )
    d["tasks"] = (
        [{**td, "schedule": _task_schedule_to_dict(t.schedule)} for td, t in zip(d["tasks"], plan.tasks)]
        if plan.tasks is not None else None
    )
    return d


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
    return plan.to_dict()


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


# ── Version / Activity serializers ─────────────────────────────────────────

def version_to_dict(v: WorkloadVersion) -> dict[str, Any]:
    """Serialize a WorkloadVersion to a JSON-safe dict."""
    d = v.to_dict()
    d["created_at"] = _iso(v.created_at)
    locations = []
    for loc in d["locations"]:
        location_info = loc.pop("location_info")
        locations.append({**loc, **location_info})
    d["locations"] = locations
    return d


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
    return {**version_to_dict(v), "activity": activity_to_dict(act)}


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
    """Serialize a BackupActivity or RestoreActivity to a JSON-safe dict.

    Local time; fields not applicable to this activity's type, or left unset, are omitted
    rather than shown as null (e.g. a BackupActivity never has a restore_type key).
    """
    d = act.to_dict()
    d["activity_type"] = "backup" if isinstance(act, BackupActivity) else "restore"
    d["started_at"] = _iso(act.started_at)
    d["finished_at"] = _iso(act.finished_at)

    if isinstance(act, BackupActivity) and act.backup_scope is None:
        del d["backup_scope"]

    if act.processed_success_count is None:
        del d["processed_success_count"]
        del d["processed_warning_count"]
        del d["processed_error_count"]

    if act.log_entries is None:
        del d["log_entries"]
    else:
        d["log_entries"] = [
            {**entry, "timestamp": _iso(e.timestamp)}
            for entry, e in zip(d["log_entries"], act.log_entries)
        ]

    if isinstance(act, BackupActivity) and act.verify_status is None:
        del d["verify_status"]

    if isinstance(act, RestoreActivity):
        if act.version_timestamp is None:
            del d["version_timestamp"]
        else:
            d["version_timestamp"] = _iso(act.version_timestamp)
        for key in ("restore_type", "restore_destination", "operator", "restore_from_info", "destination_path"):
            if getattr(act, key) is None:
                del d[key]
        if act.destination_inventory is None:
            del d["destination_inventory"]
        else:
            d["destination_inventory"] = {
                k: d["destination_inventory"][k] for k in ("hostname", "address", "host_type")
            }
    return d


def activity_log_to_dict(e: APMActivityLog) -> dict[str, Any]:
    """Serialize an APMActivityLog entry to a JSON-safe dict."""
    d = e.to_dict()
    d["type"] = d.pop("log_type")
    d["timestamp"] = fmt_datetime_iso(e.timestamp)
    return d


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
    d = e.to_dict()
    d["timestamp"] = fmt_datetime_iso(e.timestamp)
    return d


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
    d = e.to_dict()
    d["timestamp"] = fmt_datetime_iso(e.timestamp)
    return d


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
    d = e.to_dict()
    d["timestamp"] = fmt_datetime_iso(e.timestamp)
    return d


def system_log_to_csv_row(e: SystemLog) -> dict[str, Any]:
    """Serialize a SystemLog entry to a CSV-safe flat dict (table columns, raw values)."""
    return {
        "level":       e.level.value,
        "timestamp":   fmt_datetime_iso(e.timestamp) or "",
        "username":    e.username,
        "description": e.description,
    }


def m365_workload_to_dict(wl: M365Workload) -> dict[str, Any]:
    """Serialize an M365Workload to a JSON/YAML-safe dict (local time, flattened plan, computed info_label)."""
    d = wl.to_dict()
    plan = d.pop("plan")
    d["plan_name"] = plan["name"]
    d["plan_id"] = plan["plan_id"]
    d["last_backup_at"] = fmt_datetime_iso(wl.last_backup_at)
    d["info_label"] = wl.info.label if wl.info else None
    return d


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


def m365_export_activity_to_dict(a: M365ExportActivity) -> dict[str, Any]:
    """Serialize an M365ExportActivity to a JSON-safe dict (local time; source_name renamed to item)."""
    d = a.to_dict()
    d["item"] = d.pop("source_name")
    d["started_at"] = fmt_datetime_iso(a.started_at)
    d["finished_at"] = fmt_datetime_iso(a.finished_at)
    d["version_timestamp"] = fmt_datetime_iso(a.version_timestamp)
    return d


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
