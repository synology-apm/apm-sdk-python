#!/usr/bin/env python3
"""
Import and export APM infrastructure configuration as YAML.

Export writes all resources to a YAML file. Import creates or updates resources
on the target APM server; use --type to limit to a specific resource type.

YAML schema version: 1. Top-level keys:
  backup_servers, remote_storages, protection_plans, retirement_plans,
  tiering_plans, file_servers, saas_tenants, m365_auto_backup_rules.

backup_servers is a reference table (ref_key → backup server) used by plans and
file servers. remote_storages is a full config table — entries store connection
fields (endpoint, storage_type, vault_name, etc.) in addition to ref_key/name_or_id.
saas_tenants is a reference table (ref_key → tenant_id) for M365 tenants,
referenced by tenant_ref in m365_auto_backup_rules.

Each reference table entry has:
  ref_key    — stable in-YAML alias (backup_server_ref / destination_ref /
               tenant_ref throughout the file); rename freely.
  name_or_id — display name or UUID. UUID: exact match; not found is an error.
               Name: case-insensitive match; not found is an error.

Informational comments before each entry are not read on import. To redirect
all plans from one server to another, change name_or_id in the single
ref-table entry; all backup_server_ref references remain unchanged.

On import, plans (protection, retirement, tiering) are matched by name_or_id:
UUID not found is an error; name not found triggers creation. File Servers are
matched by (host_ip, namespace, plan_ref). Plans with unresolvable backup copy
or tiering destinations are skipped with an error.

File Server credentials are not stored in this file. Import auto-discovers
<stem>.fs-credentials.csv (override: --fs-credentials). Required for new
workloads; existing workloads are updated without a password change if absent.

Remote storage credentials are not stored in this file. Import auto-discovers
<stem>.storage-credentials.csv (override: --storage-credentials). Required to
create or update remote storages; skipped if absent. endpoint is empty for
endpoint-free types (amazon_s3, amazon_s3_china, c2_object_storage, wasabi).

Export generates pre-populated CSV credential templates by default;
suppress with --no-credentials-template.

Environment variables (see .env.example and examples/README.md):
    APM_HOST          hostname or IP (supports host:port)
    APM_USERNAME      account
    APM_PASSWORD      password
    APM_NO_VERIFY_SSL set to true to skip SSL verification
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import stat
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
from typing import Any, TextIO, TypeVar

import yaml
from _common import (
    add_profile_arg,
    fmt_compact_duration,
    list_m365_tenants,
    make_client,
    paginate,
    parse_compact_duration,
    prompt_yes_no,
    register_interrupt,
    run_main,
    unregister_interrupt,
)

from synology_apm.sdk import (
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APMClient,
    APMError,
    APVStorageAddRequest,
    BackupCopyConfig,
    BackupServer,
    C2ObjectStorageAddRequest,
    DbActionOnError,
    DuplicateWorkloadError,
    EventTriggerConfig,
    FileServerAddRequest,
    FileServerPathSelector,
    FileServerType,
    FileServerUpdateRequest,
    GenericS3StorageAddRequest,
    GFSRetention,
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
    M365PlanCreateRequest,
    MachineBackupWindow,
    MachineDbConfig,
    MachineOsType,
    MachinePcConfig,
    MachinePlanCreateRequest,
    MachinePsConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineTaskScope,
    MachineVmConfig,
    MachineWorkload,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    PlanNameConflictError,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorage,
    RemoteStorageConflictError,
    RemoteStorageInUseError,
    RemoteStorageType,
    RemoteStorageUnmanagedCatalogError,
    RemoteStorageUpdateRequest,
    ResourceNotFoundError,
    RetentionType,
    RetirementPlan,
    RetirementPlanCreateRequest,
    SaasTenant,
    ScheduleFrequency,
    TieringPlan,
    TieringPlanCreateRequest,
    WasabiCloudStorageAddRequest,
    WeekDay,
    WorkloadCategory,
)

# Types that use a caller-supplied endpoint; all other importable types connect to fixed
# service endpoints and do not accept an endpoint field.
_ENDPOINT_REQUIRED_TYPES: set[RemoteStorageType] = {
    RemoteStorageType.ACTIVE_PROTECT_VAULT,
    RemoteStorageType.S3_COMPATIBLE,
}
_IMPORTABLE_RS_TYPES: set[RemoteStorageType] = {
    RemoteStorageType.ACTIVE_PROTECT_VAULT,
    RemoteStorageType.S3_COMPATIBLE,
    RemoteStorageType.AMAZON_S3,
    RemoteStorageType.AMAZON_S3_CHINA,
    RemoteStorageType.C2_OBJECT_STORAGE,
    RemoteStorageType.WASABI,
}

_WEEKDAY_NAMES: dict[WeekDay, str] = {
    WeekDay.SUNDAY: "sunday", WeekDay.MONDAY: "monday",
    WeekDay.TUESDAY: "tuesday", WeekDay.WEDNESDAY: "wednesday",
    WeekDay.THURSDAY: "thursday", WeekDay.FRIDAY: "friday",
    WeekDay.SATURDAY: "saturday",
}
_NAME_TO_WEEKDAY: dict[str, WeekDay] = {v: k for k, v in _WEEKDAY_NAMES.items()}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s))


_T = TypeVar("_T")


def _dedupe_by_key(
    items: list[_T],
    key_of: Callable[[_T], Any],
    warn_of: Callable[[_T], str],
) -> list[_T]:
    """Keep the first occurrence per key; print warn_of(item) to stderr for each dropped duplicate."""
    seen: set[Any] = set()
    result: list[_T] = []
    for item in items:
        k = key_of(item)
        if k in seen:
            print(warn_of(item), file=sys.stderr)
        else:
            seen.add(k)
            result.append(item)
    return result


def _filter_to_keys(
    items: list[_T],
    key_of: Callable[[_T], Any],
    allowed_keys: Iterable[Any],
) -> list[_T]:
    """Keep items whose key is in allowed_keys, consuming each key once.

    Mirrors an earlier _dedupe_by_key pass: a re-parsed list is filtered back down to
    the exact set of keys the first pass kept, so duplicate YAML entries do not both
    re-appear.
    """
    remaining: dict[Any, None] = dict.fromkeys(allowed_keys)
    result: list[_T] = []
    for item in items:
        k = key_of(item)
        if k in remaining:
            result.append(item)
            del remaining[k]
    return result


# ── YAML write helpers ────────────────────────────────────────────────────────


def _yaml_scalar(value: str) -> str:
    """Serialize a string as a YAML scalar, adding quotes when needed."""
    # yaml.dump appends "...\n" (document-end marker) even for plain scalars;
    # strip it before embedding the value inline.
    return yaml.dump(value, default_flow_style=True).removesuffix("...\n").strip()


def _write_ref_section(
    fh: TextIO,
    section_name: str,
    entries: list[dict[str, Any]],
) -> None:
    """Write a ref-table section (backup_servers or remote_storages) with comment blocks.

    Each entry must have ref_key, name_or_id, and _comment (informational,
    written as a YAML comment — not a field). The _comment key is not written
    to the YAML output.
    """
    fh.write(f"{section_name}:\n")
    for entry in entries:
        comment = entry.get("_comment", "")
        if comment:
            fh.write(f"  # {comment}\n")
        fh.write(f"  - ref_key: {_yaml_scalar(entry['ref_key'])}\n")
        fh.write(f"    name_or_id: {_yaml_scalar(entry['name_or_id'])}\n")


def _write_saas_tenants_section(fh: TextIO, entries: list[dict[str, Any]]) -> None:
    if not entries:
        fh.write("saas_tenants: []\n")
        return
    fh.write("saas_tenants:\n")
    for entry in entries:
        comment = entry.get("_comment", "")
        if comment:
            fh.write(f"  # {comment}\n")
        fh.write(f"  - ref_key: {_yaml_scalar(entry['ref_key'])}\n")
        fh.write(f"    tenant_id: {_yaml_scalar(entry['tenant_id'])}\n")


def _write_section_comment(fh: TextIO, lines: list[str]) -> None:
    for line in lines:
        fh.write(f"# {line}\n")


def _write_commented_section(fh: TextIO, section_name: str, entries: list[dict[str, Any]]) -> None:
    """Write a YAML list section where each entry may carry a leading comment.

    Entries may include a ``_comment`` key whose value is written as a ``# ...``
    line immediately before the entry. The key itself is not written to YAML output.
    Entries are separated by a blank line for readability.
    """
    if not entries:
        fh.write(f"{section_name}: []\n")
        return
    fh.write(f"{section_name}:\n")
    for i, entry in enumerate(entries):
        if i > 0:
            fh.write("\n")
        comment = entry.get("_comment", "")
        filtered = {k: v for k, v in entry.items() if k != "_comment"}
        if comment:
            fh.write(f"  # {comment}\n")
        for line in yaml.dump([filtered], **_YAML_DUMP_OPTS).splitlines():
            fh.write(f"  {line}\n")


_COMMENT_BACKUP_SERVERS: list[str] = [
    "Backup server reference table — one entry per backup server.",
    "  ref_key    — stable in-YAML alias; referenced by backup_server_ref and destination_ref",
    "               throughout this file. Assign any unique name; rename freely.",
    "  name_or_id — server display name or UUID.",
    "               If a UUID is given, only an exact UUID match is used; UUID not found is an error.",
    "               If a display name is given, a case-insensitive name match is used.",
    "",
    "Informational comment per entry (name_or_id UUID, name, hostname, type, description)",
    "is not read on import.",
]

_COMMENT_REMOTE_STORAGES: list[str] = [
    "Remote storage reference table — one entry per remote storage target.",
    "  ref_key        — stable in-YAML alias; referenced by destination_ref in tiering plans",
    "                   and backup copy configurations. Assign any unique name; rename freely.",
    "  name_or_id     — storage display name or UUID.",
    "                   If a UUID is given, only an exact UUID match is used; UUID not found is an error.",
    "                   If a display name is given, a case-insensitive name match is used.",
    "  endpoint       — connection address. Present for active_protect_vault (host:port)",
    "                   and s3_compatible (https://... URL with port).",
    "                   Not included for endpoint-free types (amazon_s3, amazon_s3_china,",
    "                   c2_object_storage, wasabi) — see endpoint in per-entry comment.",
    "  storage_type   — storage type value (active_protect_vault, s3_compatible, etc.).",
    "  encryption_enabled — true if client-side encryption is enabled.",
    "  vault_name     — bucket name (S3) or vault name (APV; exported as-is, informational — the APV",
    "                   server provides the authoritative value at import time).",
    "  trust_self_signed  — true to auto-fetch and trust the endpoint's self-signed certificate.",
    "                       Present for active_protect_vault and s3_compatible only.",
    "                       Endpoint-free types (amazon_s3, etc.) use CA-signed public endpoints.",
    "",
    "Informational comment per entry (name_or_id UUID, endpoint, type) is not read on import.",
    "",
    "Import: active_protect_vault, s3_compatible, amazon_s3, amazon_s3_china, c2_object_storage,",
    "  and wasabi types are supported (all imported natively).",
    "  amazon_s3 / amazon_s3_china / c2_object_storage / wasabi: imported via native endpoint-free path",
    "  (no endpoint or trust_self_signed needed); endpoint in this file is informational only (in comment).",
    "  Supply credentials via --storage-credentials (CSV columns: storage_type, endpoint, vault_name,",
    "  access_key, secret_key, relink_encryption_key).",
    "  Credential lookup key is (storage_type, endpoint, vault_name) — portable across APM instances.",
    "  storage_type disambiguates entries with the same vault name across different services",
    "  (e.g. amazon_s3 vs wasabi with the same bucket name).",
    "  For amazon_s3 / amazon_s3_china / c2_object_storage / wasabi, endpoint column must be '' (empty)",
    "  in the CSV — matches the absent endpoint field in the YAML.",
    "  relink_encryption_key: blank for first-time registration. After a successful create of an",
    "  encrypted vault, the import tool renames the CSV to <file>.<timestamp>.bak, then overwrites",
    "  relink_encryption_key with the newly issued key so the CSV is ready for the next re-add.",
    "  If unmanaged backup catalogs are found in the vault during import, the entry is reported",
    "  as an error — re-add manually via the SDK passing unmanaged_retirement_plan.",
]

_COMMENT_PROTECTION_PLANS: list[str] = [
    "Protection plans — machine workloads (VM, PC, PS, FS, database) and Microsoft 365.",
    "  ref_key                         — stable in-YAML alias; referenced by plan_ref in file_servers",
    "                                      below. Assign any unique name; rename freely.",
    "  name_or_id                      — plan display name or UUID.",
    "                                      If a UUID is given, only an exact UUID match is used;",
    "                                      UUID not found on this server is an error.",
    "                                      If a display name is given, a case-insensitive name match",
    "                                      is used; no match triggers plan creation.",
    '  type                            — "machine" | "m365".',
    "  description                     — optional description.",
    "  is_immutable                    — true to prevent manual deletion of backup data.",
    "  retention                       — retention policy:",
    "                                      type: keep_all | keep_days | keep_versions | keep_advanced",
    "                                      days / versions / gfs depending on type.",
    "  schedule                        — backup frequency: frequency, start_time (HH:MM), weekdays list.",
    "  run_schedule_by_controller_time — true to use the management server's local clock.",
    "  backup_copy                     — optional secondary copy config; null to disable.",
    "                                      destination_type: appliance | remote_storage",
    "                                      destination_ref: ref_key from the ref table above.",
    "  vm_config / pc_config / ps_config / db_config",
    "                                  — workload-type-specific settings (machine only; null if unused).",
    "  backup_window                   — optional allowed-hours window (machine only).",
    "  tasks                           — per-workload-type task list with scope and optional schedule",
    "                                      (machine only).",
]

_COMMENT_RETIREMENT_PLANS: list[str] = [
    "Retirement plans — compliance / regulatory retention for decommissioned workloads.",
    "  name_or_id                      — plan display name or UUID.",
    "                                      If a UUID is given, only an exact UUID match is used;",
    "                                      UUID not found on this server is an error.",
    "                                      If a display name is given, a case-insensitive name match",
    "                                      is used; no match triggers plan creation.",
    "  description                     — optional description.",
    "  retention_days                  — days to retain data; null to retain indefinitely.",
    "  keep_latest_version             — true to always keep at least one version.",
    "  run_schedule_by_controller_time — true to use the management server's local clock.",
]

_COMMENT_TIERING_PLANS: list[str] = [
    "Tiering plans — move older backup versions to remote storage.",
    "  name_or_id                      — plan display name or UUID.",
    "                                      If a UUID is given, only an exact UUID match is used;",
    "                                      UUID not found on this server is an error.",
    "                                      If a display name is given, a case-insensitive name match",
    "                                      is used; no match triggers plan creation.",
    "  description                     — optional description.",
    "  tiering_after_days              — move versions older than this many days to the destination.",
    "  destination_ref                 — ref_key of the remote storage target (remote_storages above).",
    "                                      Plans with a missing destination are skipped on import.",
    "  daily_check_time                — daily time (HH:MM) to check for versions eligible for tiering.",
    "  run_schedule_by_controller_time — true to use the management server's local clock.",
]

_COMMENT_FILE_SERVERS: list[str] = [
    "File Server workloads (SMB/NFS shares backed up as file-server type workloads).",
    "  host_ip                    — IP address or hostname of the file server.",
    "  host_port                  — port number (default 445 for SMB).",
    '  server_type                — share protocol, e.g. "smb".',
    "  backup_server_ref          — ref_key of the backup server (backup_servers above);",
    "                               or the raw namespace string if no backup server is assigned.",
    "  plan_ref                   — ref_key of the protection plan (protection_plans above).",
    "  enable_vss                 — true to use VSS snapshots for application-consistent backups.",
    "  connection_timeout_seconds — connection timeout in seconds.",
    "  trigger_backup             — true to start an immediate backup after import (always false on export).",
    "  selectors                  — list of path selectors: {path, excluded_paths}.",
    "",
    "Passwords are not stored in this file. Supply them at import time via --fs-credentials:",
    "  apm_import_export.py import config.yaml --fs-credentials fs_creds.csv",
    "The credential file is a CSV with columns: endpoint, login_user, password.",
    "  endpoint = host_ip; login_user must match the login_user field in this YAML.",
    "  Lookup key is (endpoint, login_user) — supports multiple accounts at the same host IP.",
    "Required when creating new workloads; omit to keep the existing stored credentials on update.",
]

_COMMENT_SAAS_TENANTS: list[str] = [
    "SaaS tenant reference table — one entry per M365 tenant.",
    "  ref_key   — stable in-YAML alias; referenced by tenant_ref in m365_auto_backup_rules.",
    "              Assign any unique name; rename freely.",
    "  tenant_id — Azure AD tenant UUID.",
    "",
    "Informational comment per entry (name, email) is not read on import.",
]

_COMMENT_M365_AUTO_BACKUP_RULES: list[str] = [
    "M365 auto-backup rules — one entry per M365 tenant.",
    "  tenant_ref         — ref_key from the saas_tenants table above.",
    "                       For backward compatibility, tenant_id may be used directly when tenant_ref is absent.",
    "  user_rules         — per-plan rules for Exchange / OneDrive / Chat workloads.",
    "    backup_server_ref  — ref_key of the backup server (backup_servers above).",
    "    plan_ref           — ref_key of the M365 protection plan (protection_plans above).",
    "    exchange_groups    — Azure AD group IDs whose Exchange members are auto-protected.",
    "    onedrive_groups    — Azure AD group IDs whose OneDrive members are auto-protected.",
    "    chat_groups        — Azure AD group IDs whose Chat members are auto-protected.",
    "  collab_services    — tenant-wide settings for Collaboration service types.",
    "    group_exchange     — Microsoft 365 Groups auto-backup (backup_server_ref + plan_ref).",
    "    mysite             — SharePoint Personal Sites auto-backup.",
    "    sharepoint         — SharePoint Sites auto-backup.",
    "    teams              — Teams auto-backup.",
    "    Omit a type or set to null to disable it; only enabled types are written on export.",
    "",
    "On import, each user rule is matched by (backup_server_ref, plan_ref) within the tenant.",
    "  No match → create; match + on-conflict=overwrite → update; match + skip → skip.",
    "Collab services are always written as a single block per tenant (overwrite or skip).",
    "Tenant IDs not found on the target APM are skipped with an error.",
]

_YAML_DUMP_OPTS: dict[str, Any] = {
    "default_flow_style": False,
    "allow_unicode": True,
    "sort_keys": False,
}


# ── Serialization (export side) ──────────────────────────────────────────────


def _ser_backup_server(bs: BackupServer, ref_key: str) -> dict[str, Any]:
    parts = [
        f"name_or_id: {bs.backup_server_id}",
        f"name: {bs.name}",
        f"hostname: {bs.hostname}",
        f"type: {bs.server_type.value}",
    ]
    if bs.description:
        parts.append(f"description: {bs.description}")
    return {
        "ref_key": ref_key,
        "name_or_id": bs.name,
        "_comment": " | ".join(parts),
    }


def _ser_saas_tenant(tenant: SaasTenant, ref_key: str) -> dict[str, Any]:
    return {
        "ref_key": ref_key,
        "tenant_id": tenant.tenant_id,
        "_comment": f"name: {tenant.tenant_name} | email: {tenant.tenant_email}",
    }


def _ser_remote_storage(rs: RemoteStorage, ref_key: str) -> dict[str, Any]:
    original_type = rs.storage_type
    comment_base = (
        f"name_or_id: {rs.storage_id} | name: {rs.name} | "
        f"endpoint: {rs.endpoint} | type: {original_type.value}"
    )
    if original_type not in _IMPORTABLE_RS_TYPES:
        # Azure / unknown — export for ref resolution only; import not supported.
        return {
            "ref_key": ref_key,
            "name_or_id": rs.name,
            "endpoint": rs.endpoint,
            "storage_type": original_type.value,
            "encryption_enabled": rs.encryption_enabled,
            "vault_name": rs.vault_name,
            "trust_self_signed": False,
            "_comment": comment_base + " | import: not supported for this type",
        }
    if original_type not in _ENDPOINT_REQUIRED_TYPES:
        # Endpoint-free importable types (Amazon S3, Amazon S3 China, C2, Wasabi).
        return {
            "ref_key": ref_key,
            "name_or_id": rs.name,
            "storage_type": original_type.value,
            "encryption_enabled": rs.encryption_enabled,
            "vault_name": rs.vault_name,
            "_comment": comment_base + " | import: native endpoint-free path (no endpoint or trust_self_signed needed)",
        }
    # Endpoint-required importable types (ActiveProtect Vault, S3 Compatible).
    return {
        "ref_key": ref_key,
        "name_or_id": rs.name,
        "endpoint": rs.endpoint,
        "storage_type": original_type.value,
        "encryption_enabled": rs.encryption_enabled,
        "vault_name": rs.vault_name,
        "trust_self_signed": True,
        "_comment": comment_base,
    }


def _ser_time(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t is not None else None


def _ser_retention(r: ProtectionRetentionPolicy) -> dict[str, Any]:
    d: dict[str, Any] = {"type": r.retention_type.value}
    if r.retention_type == RetentionType.KEEP_DAYS and r.days is not None:
        d["days"] = r.days
    elif r.retention_type == RetentionType.KEEP_VERSIONS and r.versions is not None:
        d["versions"] = r.versions
    elif r.retention_type == RetentionType.KEEP_ADVANCED and r.gfs is not None:
        d["days"] = r.days
        d["versions"] = r.versions
        d["gfs"] = {
            "daily_versions": r.gfs.daily_versions,
            "weekly_versions": r.gfs.weekly_versions,
            "monthly_versions": r.gfs.monthly_versions,
            "yearly_versions": r.gfs.yearly_versions,
        }
    return d


def _ser_schedule(s: ProtectionSchedule) -> dict[str, Any]:
    return {
        "frequency": s.frequency.value,
        "start_time": _ser_time(s.start_time),
        "weekdays": [_WEEKDAY_NAMES[w] for w in s.weekdays],
    }


def _ser_task_schedule(s: MachineTaskSchedule) -> dict[str, Any]:
    d: dict[str, Any] = {
        "time_schedule": _ser_schedule(s.time_schedule) if s.time_schedule is not None else None,
        "event_trigger": {
            "on_sign_out": s.event_trigger.on_sign_out,
            "on_lock": s.event_trigger.on_lock,
            "on_startup": s.event_trigger.on_startup,
            "min_interval": fmt_compact_duration(s.event_trigger.min_interval),
        } if s.event_trigger is not None else None,
    }
    return d


def _ser_task(t: MachineTaskConfig) -> dict[str, Any]:
    return {
        "workload_type": t.workload_type.value,
        "os_type": t.os_type.value,
        "scope": t.scope.value if t.scope is not None else None,
        "custom_volumes": list(t.custom_volumes),
        "include_external_drives": t.include_external_drives,
        "include_boot_partition": t.include_boot_partition,
        "use_main_schedule": t.use_main_schedule,
        "schedule": _ser_task_schedule(t.schedule) if t.schedule is not None else None,
    }


# ·· Protection plan


def _ser_protection_plan(
    plan: ProtectionPlan,
    bs_ref_keys: dict[str, str],
    rs_ref_keys: dict[str, str],
    ref_key: str,
) -> dict[str, Any]:
    plan_type = "m365" if plan.category == WorkloadCategory.M365 else "machine"
    policy = plan.policy
    if policy is not None:
        retention_d = _ser_retention(policy.retention)
        schedule_d = (
            _ser_schedule(policy.schedule) if policy.schedule is not None
            else {"frequency": ScheduleFrequency.MANUAL.value, "start_time": None, "weekdays": []}
        )
    else:
        retention_d = {"type": RetentionType.KEEP_ALL.value}
        schedule_d = {"frequency": ScheduleFrequency.MANUAL.value, "start_time": None, "weekdays": []}

    bcp = plan.backup_copy_policy
    backup_copy_d: dict[str, Any] | None = None
    if bcp is not None:
        loc = bcp.destination
        dest_ref = rs_ref_keys.get(loc.name, loc.name) if loc.is_remote_storage else bs_ref_keys.get(loc.name, loc.name)
        backup_copy_d = {
            "destination_type": "remote_storage" if loc.is_remote_storage else "appliance",
            "destination_ref": dest_ref,
            "retention": _ser_retention(bcp.retention),
            "schedule": _ser_schedule(bcp.schedule),
        }

    d: dict[str, Any] = {
        "ref_key": ref_key,
        "name_or_id": plan.name,
        "type": plan_type,
        "description": plan.description,
        "is_immutable": plan.is_immutable,
        "retention": retention_d,
        "schedule": schedule_d,
        "run_schedule_by_controller_time": plan.run_schedule_by_controller_time,
        "backup_copy": backup_copy_d,
    }

    if plan_type == "machine":
        d["vm_config"] = {
            "enable_app_aware_bkp": plan.vm_config.enable_app_aware_bkp,
            "enable_verification": plan.vm_config.enable_verification,
            "verification_video_duration_seconds": plan.vm_config.verification_video_duration_seconds,
            "enable_datastore_usage_detection": plan.vm_config.enable_datastore_usage_detection,
            "datastore_min_free_space_percent": plan.vm_config.datastore_min_free_space_percent,
        } if plan.vm_config is not None else None
        d["pc_config"] = {
            "shutdown_after_backup": plan.pc_config.shutdown_after_backup,
            "wake_for_backup": plan.pc_config.wake_for_backup,
            "prevent_sleep_during_backup": plan.pc_config.prevent_sleep_during_backup,
        } if plan.pc_config is not None else None
        d["ps_config"] = {
            "enable_app_aware_bkp": plan.ps_config.enable_app_aware_bkp,
            "enable_verification": plan.ps_config.enable_verification,
            "verification_video_duration_seconds": plan.ps_config.verification_video_duration_seconds,
            "shutdown_after_backup": plan.ps_config.shutdown_after_backup,
            "wake_for_backup": plan.ps_config.wake_for_backup,
            "prevent_sleep_during_backup": plan.ps_config.prevent_sleep_during_backup,
        } if plan.ps_config is not None else None
        d["db_config"] = {
            "action_on_error": plan.db_config.action_on_error.value,
            "mssql_log_setting": plan.db_config.mssql_log_setting.value,
            "oracle_log_setting": plan.db_config.oracle_log_setting.value,
        } if plan.db_config is not None else None
        bw = plan.backup_window
        d["backup_window"] = {
            "enabled": bw.enabled,
            "allowed_hours": {
                _WEEKDAY_NAMES[wd]: sorted(hours)
                for wd, hours in bw.allowed_hours.items()
            },
        } if bw is not None else None
        d["tasks"] = [_ser_task(t) for t in plan.tasks] if plan.tasks else None

    d["_comment"] = f"name_or_id: {plan.plan_id}"
    return d


# ·· Retirement plan


def _ser_retirement_plan(plan: RetirementPlan) -> dict[str, Any]:
    return {
        "_comment": f"name_or_id: {plan.plan_id}",
        "name_or_id": plan.name,
        "description": plan.description,
        "retention_days": plan.retention.days if plan.retention is not None else None,
        "keep_latest_version": plan.retention.keep_latest_version if plan.retention is not None else True,
        "run_schedule_by_controller_time": plan.run_schedule_by_controller_time,
    }


# ·· Tiering plan and file server


def _ser_tiering_plan(plan: TieringPlan, rs_ref_keys: dict[str, str]) -> dict[str, Any]:
    dest_ref: str | None = None
    if plan.destination is not None:
        dest_ref = rs_ref_keys.get(plan.destination.name, plan.destination.name)
    return {
        "_comment": f"name_or_id: {plan.plan_id}",
        "name_or_id": plan.name,
        "description": plan.description,
        "tiering_after_days": plan.tiering_after_days,
        "destination_ref": dest_ref,
        "daily_check_time": plan.daily_check_time.strftime("%H:%M"),
        "run_schedule_by_controller_time": plan.run_schedule_by_controller_time,
    }


def _ser_file_server(
    wl: MachineWorkload,
    bs_ref_keys: dict[str, str],
    plan_ref_by_id: dict[str, str],
) -> dict[str, Any]:
    if wl.is_retired:
        return {}
    cfg = wl.fs_config
    if cfg is None:
        return {}
    if wl.backup_server is not None:
        bs_ref = bs_ref_keys.get(wl.backup_server.name, wl.backup_server.name)
    else:
        bs_ref = wl.namespace
    return {
        "host_ip": cfg.host_ip,
        "host_port": cfg.host_port,
        "login_user": cfg.login_user,
        "server_type": cfg.server_type.value,
        "backup_server_ref": bs_ref,
        "plan_ref": plan_ref_by_id.get(wl.plan.plan_id, wl.plan.plan_id),
        "enable_vss": cfg.enable_vss,
        "connection_timeout_seconds": cfg.connection_timeout_seconds,
        "trigger_backup": False,
        "selectors": [
            {"path": s.path, "excluded_paths": list(s.excluded_paths)}
            for s in cfg.selectors
        ],
    }


# ·· M365 auto-backup rules


def _ser_m365_auto_backup_rules_block(
    tenant: SaasTenant,
    result: M365AutoBackupRuleListResult,
    plan_id_to_ref: dict[str, str],
    bs_ns_to_ref: dict[str, str],
    tenant_ref: str,
) -> dict[str, Any] | None:
    """Serialize one tenant's M365 auto-backup rules. Returns None if nothing configured."""
    has_collab = any(
        s.enabled for s in [result.group_exchange, result.mysite, result.sharepoint, result.teams]
    )
    if not result.rules and not has_collab:
        return None

    user_rules: list[dict[str, Any]] = [
        {
            "backup_server_ref": bs_ns_to_ref.get(rule.namespace, rule.namespace),
            "plan_ref": plan_id_to_ref.get(rule.plan_id, rule.plan_id),
            "exchange_groups": list(rule.exchange_group_ids),
            "onedrive_groups": list(rule.onedrive_group_ids),
            "chat_groups": list(rule.chat_group_ids),
        }
        for rule in result.rules
    ]

    def _ser_collab(s: M365CollabServiceSetting) -> dict[str, str] | None:
        if not s.enabled:
            return None
        return {
            "backup_server_ref": bs_ns_to_ref.get(s.namespace, s.namespace),
            "plan_ref": plan_id_to_ref.get(s.plan_id, s.plan_id),
        }

    collab_services: dict[str, Any] = {}
    for key, setting in [
        ("group_exchange", result.group_exchange),
        ("mysite", result.mysite),
        ("sharepoint", result.sharepoint),
        ("teams", result.teams),
    ]:
        v = _ser_collab(setting)
        if v is not None:
            collab_services[key] = v

    return {
        "tenant_ref": tenant_ref,
        "user_rules": user_rules,
        "collab_services": collab_services,
    }


# ── Deserialization (import side) ────────────────────────────────────────────


def _parse_time_str(s: str | None) -> time | None:
    if not s:
        return None
    return datetime.strptime(str(s), "%H:%M").time()


def _parse_retention(d: dict[str, Any]) -> ProtectionRetentionPolicy:
    rtype = RetentionType(d["type"])
    if rtype == RetentionType.KEEP_DAYS:
        return ProtectionRetentionPolicy(rtype, days=int(d.get("days", 30)))
    if rtype == RetentionType.KEEP_VERSIONS:
        return ProtectionRetentionPolicy(rtype, versions=int(d.get("versions", 5)))
    if rtype == RetentionType.KEEP_ADVANCED:
        gfs_d = d.get("gfs") or {}
        return ProtectionRetentionPolicy(
            rtype,
            days=int(d["days"]) if d.get("days") is not None else None,
            versions=int(d["versions"]) if d.get("versions") is not None else None,
            gfs=GFSRetention(
                daily_versions=int(gfs_d.get("daily_versions", 7)),
                weekly_versions=int(gfs_d.get("weekly_versions", 4)),
                monthly_versions=int(gfs_d.get("monthly_versions", 12)),
                yearly_versions=int(gfs_d.get("yearly_versions", 3)),
            ),
        )
    return ProtectionRetentionPolicy(rtype)


def _parse_schedule(d: dict[str, Any]) -> ProtectionSchedule:
    return ProtectionSchedule(
        frequency=ScheduleFrequency(d["frequency"]),
        start_time=_parse_time_str(d.get("start_time")),
        weekdays=tuple(_NAME_TO_WEEKDAY[w] for w in (d.get("weekdays") or [])),
    )


def _parse_task_schedule_dict(d: dict[str, Any]) -> MachineTaskSchedule:
    ts_d = d.get("time_schedule")
    time_schedule = _parse_schedule(ts_d) if ts_d is not None else None
    et_d = d.get("event_trigger")
    event_trigger: EventTriggerConfig | None = None
    if et_d is not None:
        event_trigger = EventTriggerConfig(
            on_sign_out=bool(et_d["on_sign_out"]),
            on_lock=bool(et_d["on_lock"]),
            on_startup=bool(et_d["on_startup"]),
            min_interval=parse_compact_duration(str(et_d.get("min_interval", "1h"))),
        )
    return MachineTaskSchedule(time_schedule=time_schedule, event_trigger=event_trigger)


# ·· Ref-table resolution


def _build_ref_map(
    section: str,
    yaml_entries: list[dict[str, Any]],
    items: list[_T],
    id_of: Callable[[_T], str],
    name_of: Callable[[_T], str],
) -> tuple[dict[str, _T], list[str]]:
    """Resolve a YAML ref-table section against a live inventory list.

    Resolution for each entry's name_or_id:
      UUID value  — exact id_of() match only; not found is an error.
      Name value  — case-insensitive name_of() match only; not found is an error.

    Returns ({ref_key: item}, [error_messages]).
    """
    ref_map: dict[str, _T] = {}
    errors: list[str] = []
    seen: set[str] = set()
    for entry in yaml_entries:
        if not isinstance(entry, dict):
            errors.append(f"{section} entry is not a mapping: {entry!r}")
            continue
        ref_key = str(entry.get("ref_key", ""))
        name_or_id = str(entry.get("name_or_id", ""))
        if not ref_key:
            errors.append(f"{section} entry missing 'ref_key'")
            continue
        if ref_key in seen:
            errors.append(f"duplicate {section} ref_key={ref_key!r}")
            continue
        seen.add(ref_key)
        matched: _T | None = None
        if _is_uuid(name_or_id):
            matched = next((item for item in items if id_of(item) == name_or_id), None)
            if matched is None:
                errors.append(f"{section} ref_key={ref_key!r} UUID {name_or_id!r} not found")
        else:
            q = name_or_id.lower()
            matched = next((item for item in items if name_of(item).lower() == q), None)
            if matched is None:
                errors.append(f"{section} ref_key={ref_key!r} not found (name_or_id={name_or_id!r})")
        if matched is not None:
            ref_map[ref_key] = matched
    return ref_map, errors


def _build_saas_tenant_ref_map(
    yaml_entries: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Build {ref_key: tenant_id} from the saas_tenants YAML section."""
    ref_map: dict[str, str] = {}
    errors: list[str] = []
    seen: set[str] = set()
    for entry in yaml_entries:
        if not isinstance(entry, dict):
            continue
        ref_key = str(entry.get("ref_key", ""))
        tenant_id = str(entry.get("tenant_id", ""))
        if not ref_key:
            errors.append("saas_tenants entry missing 'ref_key'")
            continue
        if ref_key in seen:
            errors.append(f"duplicate saas_tenants ref_key={ref_key!r}")
            continue
        if not tenant_id:
            errors.append(f"saas_tenants ref_key={ref_key!r} missing 'tenant_id'")
            continue
        seen.add(ref_key)
        ref_map[ref_key] = tenant_id
    return ref_map, errors


def _resolve_backup_copy(
    bc_d: dict[str, Any],
    backup_servers_by_ref: dict[str, BackupServer],
    remote_storages_by_ref: dict[str, RemoteStorage],
    plan_name: str,
) -> BackupCopyConfig:
    dest_type = str(bc_d.get("destination_type", ""))
    dest_ref = str(bc_d.get("destination_ref") or "")
    dest: BackupServer | RemoteStorage
    if dest_type == "appliance":
        server = backup_servers_by_ref.get(dest_ref)
        if server is None:
            raise ValueError(
                f"backup_copy destination not found for plan {plan_name!r} "
                f"(appliance ref={dest_ref!r})"
            )
        dest = server
    elif dest_type == "remote_storage":
        storage = remote_storages_by_ref.get(dest_ref)
        if storage is None:
            raise ValueError(
                f"backup_copy destination not found for plan {plan_name!r} "
                f"(remote_storage ref={dest_ref!r})"
            )
        dest = storage
    else:
        raise ValueError(
            f"unknown backup_copy destination_type {dest_type!r} for plan {plan_name!r}"
        )
    return BackupCopyConfig(
        destination=dest,
        retention=_parse_retention(bc_d["retention"]),
        schedule=_parse_schedule(bc_d["schedule"]),
    )


# ·· Protection plan


def _parse_protection_request(
    d: dict[str, Any],
    backup_servers_by_ref: dict[str, BackupServer],
    remote_storages_by_ref: dict[str, RemoteStorage],
) -> MachinePlanCreateRequest | M365PlanCreateRequest:
    name = str(d["name_or_id"])
    plan_type = str(d.get("type", "machine")).lower()
    if plan_type not in ("machine", "m365"):
        raise ValueError(f"Unknown protection plan type {plan_type!r}; expected 'machine' or 'm365'.")
    retention = _parse_retention(d["retention"])
    schedule = _parse_schedule(d["schedule"])
    description = str(d.get("description", ""))
    is_immutable = bool(d.get("is_immutable", False))
    run_ctrl = bool(d.get("run_schedule_by_controller_time", False))

    bc_d = d.get("backup_copy")
    backup_copy = (
        _resolve_backup_copy(bc_d, backup_servers_by_ref, remote_storages_by_ref, name)
        if bc_d else None
    )

    if plan_type == "m365":
        return M365PlanCreateRequest(
            name=name, retention=retention, schedule=schedule,
            description=description, is_immutable=is_immutable,
            backup_copy=backup_copy, run_schedule_by_controller_time=run_ctrl,
        )

    vm_d = d.get("vm_config")
    vm_config = MachineVmConfig(
        enable_app_aware_bkp=bool(vm_d.get("enable_app_aware_bkp", True)),
        enable_verification=bool(vm_d.get("enable_verification", False)),
        verification_video_duration_seconds=int(vm_d.get("verification_video_duration_seconds", 120)),
        enable_datastore_usage_detection=bool(vm_d.get("enable_datastore_usage_detection", False)),
        datastore_min_free_space_percent=int(vm_d.get("datastore_min_free_space_percent", 10)),
    ) if vm_d is not None else None

    pc_d = d.get("pc_config")
    pc_config = MachinePcConfig(
        shutdown_after_backup=bool(pc_d.get("shutdown_after_backup", False)),
        wake_for_backup=bool(pc_d.get("wake_for_backup", False)),
        prevent_sleep_during_backup=bool(pc_d.get("prevent_sleep_during_backup", False)),
    ) if pc_d is not None else None

    ps_d = d.get("ps_config")
    ps_config = MachinePsConfig(
        enable_app_aware_bkp=bool(ps_d.get("enable_app_aware_bkp", True)),
        enable_verification=bool(ps_d.get("enable_verification", False)),
        verification_video_duration_seconds=int(ps_d.get("verification_video_duration_seconds", 120)),
        shutdown_after_backup=bool(ps_d.get("shutdown_after_backup", False)),
        wake_for_backup=bool(ps_d.get("wake_for_backup", False)),
        prevent_sleep_during_backup=bool(ps_d.get("prevent_sleep_during_backup", False)),
    ) if ps_d is not None else None

    db_d = d.get("db_config")
    db_config = MachineDbConfig(
        action_on_error=DbActionOnError(db_d.get("action_on_error", "continue")),
        mssql_log_setting=MssqlLogSetting(db_d.get("mssql_log_setting", "do_not_truncate")),
        oracle_log_setting=OracleLogSetting(db_d.get("oracle_log_setting", "do_not_delete")),
    ) if db_d is not None else None

    bw_d = d.get("backup_window")
    backup_window = MachineBackupWindow(
        enabled=bool(bw_d.get("enabled", True)),
        allowed_hours={
            _NAME_TO_WEEKDAY[wd]: frozenset(int(h) for h in hrs)
            for wd, hrs in (bw_d.get("allowed_hours") or {}).items()
        },
    ) if bw_d is not None else None

    tasks_list: list[dict[str, Any]] | None = d.get("tasks")
    tasks: tuple[MachineTaskConfig, ...] | None = None
    if tasks_list is not None:
        tasks = tuple(
            MachineTaskConfig(
                workload_type=MachineWorkloadType(t["workload_type"]),
                os_type=MachineOsType(t["os_type"]),
                scope=MachineTaskScope(t["scope"]) if t.get("scope") is not None else None,
                custom_volumes=tuple(str(v) for v in (t.get("custom_volumes") or [])),
                include_external_drives=bool(t.get("include_external_drives", False)),
                include_boot_partition=bool(t.get("include_boot_partition", True)),
                use_main_schedule=bool(t.get("use_main_schedule", True)),
                schedule=_parse_task_schedule_dict(t["schedule"]) if t.get("schedule") is not None else None,
            )
            for t in tasks_list
        )

    return MachinePlanCreateRequest(
        name=name, retention=retention, schedule=schedule,
        description=description, is_immutable=is_immutable,
        vm_config=vm_config, pc_config=pc_config, ps_config=ps_config,
        db_config=db_config, backup_window=backup_window, tasks=tasks,
        backup_copy=backup_copy, run_schedule_by_controller_time=run_ctrl,
    )


# ·· Retirement and tiering plan


def _parse_retirement_request(d: dict[str, Any]) -> RetirementPlanCreateRequest:
    raw_days = d.get("retention_days")
    return RetirementPlanCreateRequest(
        name=str(d.get("name_or_id") or d.get("name", "")),
        retention_days=int(raw_days) if raw_days is not None else None,
        description=str(d.get("description", "")),
        keep_latest_version=bool(d.get("keep_latest_version", True)),
        run_schedule_by_controller_time=bool(d.get("run_schedule_by_controller_time", False)),
    )


def _parse_tiering_request(
    d: dict[str, Any],
    remote_storages_by_ref: dict[str, RemoteStorage],
) -> TieringPlanCreateRequest:
    name = str(d.get("name_or_id") or d.get("name", ""))
    dest_ref = str(d.get("destination_ref") or "")
    dest = remote_storages_by_ref.get(dest_ref)
    if dest is None:
        raise ValueError(f"destination not found (remote_storage ref={dest_ref!r})")
    check_time = _parse_time_str(d.get("daily_check_time")) or time(20, 0)
    return TieringPlanCreateRequest(
        name=name,
        tiering_after_days=int(d["tiering_after_days"]),
        destination=dest,
        daily_check_time=check_time,
        description=str(d.get("description", "")),
        run_schedule_by_controller_time=bool(d.get("run_schedule_by_controller_time", False)),
    )


# ·· File server


def _parse_fs_selectors(raw_list: list[dict[str, Any]]) -> tuple[FileServerPathSelector, ...]:
    if not raw_list:
        return (FileServerPathSelector(path=""),)
    return tuple(
        FileServerPathSelector(
            path=str(e.get("path", "")),
            excluded_paths=tuple(str(p) for p in (e.get("excluded_paths") or [])),
        )
        for e in raw_list
    )


def _build_fs_add_request(
    raw: dict[str, Any], plan_id: str, namespace: str, password: str, login_user: str = ""
) -> FileServerAddRequest:
    return FileServerAddRequest(
        namespace=namespace,
        host_ip=str(raw["host_ip"]),
        server_type=FileServerType(str(raw.get("server_type", "smb"))),
        plan_id=plan_id,
        login_user=login_user,
        login_password=password,
        host_port=int(raw.get("host_port", 445)),
        enable_vss=bool(raw.get("enable_vss", False)),
        trigger_backup=bool(raw.get("trigger_backup", False)),
        connection_timeout_seconds=int(raw.get("connection_timeout_seconds", 180)),
        selectors=_parse_fs_selectors(raw.get("selectors") or []),
    )


def _build_fs_update_request(
    raw: dict[str, Any], password: str | None, login_user: str = ""
) -> FileServerUpdateRequest:
    return FileServerUpdateRequest(
        host_ip=str(raw["host_ip"]),
        login_user=login_user,
        login_password=password,
        host_port=int(raw.get("host_port", 445)),
        enable_vss=bool(raw.get("enable_vss", False)),
        connection_timeout_seconds=int(raw.get("connection_timeout_seconds", 180)),
        selectors=_parse_fs_selectors(raw.get("selectors") or []),
    )


# ·· Shared result types


@dataclass
class _FsEntry:
    host_ip: str
    backup_server_ref: str    # ref_key value from YAML (for display and error messages)
    resolved_namespace: str   # namespace resolved from the ref-table (for API calls and key lookup)
    plan_name: str
    raw: dict[str, Any]
    parse_error: str | None
    login_user: str = ""
    request: FileServerAddRequest | FileServerUpdateRequest | None = None


@dataclass
class _FsResult:
    entry: _FsEntry
    action: str
    result: str
    error_msg: str


@dataclass
class _RsEntry:
    name_or_id: str
    ref_key: str
    endpoint: str
    vault_name: str
    storage_type_str: str
    raw: dict[str, Any]
    parse_error: str | None
    request: (
        GenericS3StorageAddRequest
        | APVStorageAddRequest
        | AmazonS3StorageAddRequest
        | AmazonS3ChinaStorageAddRequest
        | C2ObjectStorageAddRequest
        | WasabiCloudStorageAddRequest
        | RemoteStorageUpdateRequest
        | None
    ) = None


def _rs_key(rse: _RsEntry) -> str:
    return f"{rse.storage_type_str}:{rse.endpoint}:{rse.vault_name}"


@dataclass
class _RsResult:
    entry: _RsEntry
    action: str
    result: str
    error_msg: str
    issued_encryption_key: str | None = None
    created_storage: RemoteStorage | None = None


@dataclass
class _M365RuleEntry:
    tenant_id: str
    kind: str        # "user_rule" | "collab_services"
    backup_server_ref: str
    resolved_namespace: str
    plan_ref: str
    resolved_plan_id: str
    exchange_groups: list[str]
    onedrive_groups: list[str]
    chat_groups: list[str]
    raw: dict[str, Any]
    parse_error: str | None


@dataclass
class _M365CollabEntry:
    tenant_id: str
    group_exchange: M365CollabServiceSetting | None
    mysite: M365CollabServiceSetting | None
    sharepoint: M365CollabServiceSetting | None
    teams: M365CollabServiceSetting | None
    parse_error: str | None


@dataclass
class _M365RuleResult:
    label: str
    kind: str
    action: str
    result: str
    error_msg: str


# ── Export ───────────────────────────────────────────────────────────────────


async def _fetch_protection_details(
    apm: APMClient,
    stubs: list[ProtectionPlan],
    sem: asyncio.Semaphore,
    is_machine: bool,
) -> list[ProtectionPlan]:
    """Fetch full plan details concurrently; drop plans whose fetch fails."""
    async def _get_one(plan: ProtectionPlan) -> ProtectionPlan | None:
        try:
            async with sem:
                if is_machine:
                    return await apm.machine.plans.get(plan.plan_id)
                return await apm.m365.plans.get(plan.plan_id)
        except APMError as e:
            print(f"  Warning: failed to fetch details for plan {plan.name!r}: {e}", file=sys.stderr)
            return None

    results = await asyncio.gather(*[_get_one(p) for p in stubs])
    return [r for r in results if r is not None]


def _rs_cred_endpoint(rs: RemoteStorage) -> str:
    """Return the endpoint string used as the credential CSV lookup key for this storage."""
    if rs.storage_type not in _ENDPOINT_REQUIRED_TYPES:
        return ""
    return rs.endpoint


def _write_export_yaml(
    output: str,
    *,
    bs_data: list[dict[str, Any]],
    rs_data: list[dict[str, Any]],
    protection_data: list[dict[str, Any]],
    retirement_data: list[dict[str, Any]],
    tiering_data: list[dict[str, Any]],
    fs_data: list[dict[str, Any]],
    saas_data: list[dict[str, Any]],
    m365_auto_bkp_data: list[dict[str, Any]],
) -> None:
    with open(output, "w", encoding="utf-8") as fh:
        fh.write("version: 1\n\n")

        _write_section_comment(fh, _COMMENT_BACKUP_SERVERS)
        _write_ref_section(fh, "backup_servers", bs_data)
        fh.write("\n")

        _write_section_comment(fh, _COMMENT_REMOTE_STORAGES)
        _write_commented_section(fh, "remote_storages", rs_data)
        fh.write("\n")

        _write_section_comment(fh, _COMMENT_PROTECTION_PLANS)
        _write_commented_section(fh, "protection_plans", protection_data)
        fh.write("\n")

        _write_section_comment(fh, _COMMENT_RETIREMENT_PLANS)
        _write_commented_section(fh, "retirement_plans", retirement_data)
        fh.write("\n")

        _write_section_comment(fh, _COMMENT_TIERING_PLANS)
        _write_commented_section(fh, "tiering_plans", tiering_data)
        fh.write("\n")

        _write_section_comment(fh, _COMMENT_FILE_SERVERS)
        yaml.dump({"file_servers": fs_data}, fh, **_YAML_DUMP_OPTS)
        fh.write("\n")

        _write_section_comment(fh, _COMMENT_SAAS_TENANTS)
        _write_saas_tenants_section(fh, saas_data)
        fh.write("\n")

        _write_section_comment(fh, _COMMENT_M365_AUTO_BACKUP_RULES)
        _write_commented_section(fh, "m365_auto_backup_rules", m365_auto_bkp_data)


def _write_fs_credentials_csv(path: str, rows: list[tuple[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["endpoint", "login_user", "password"])
        w.writeheader()
        for host_ip, login_user in rows:
            w.writerow({"endpoint": host_ip, "login_user": login_user, "password": ""})


def _write_storage_credentials_csv(path: str, rows: list[tuple[str, str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w2 = csv.DictWriter(
            fh,
            fieldnames=[
                "storage_type", "endpoint", "vault_name",
                "access_key", "secret_key", "relink_encryption_key",
            ],
        )
        w2.writeheader()
        for storage_type, endpoint, vault_name in rows:
            w2.writerow({
                "storage_type": storage_type, "endpoint": endpoint, "vault_name": vault_name,
                "access_key": "", "secret_key": "", "relink_encryption_key": "",
            })


async def run_export(
    output: str,
    concurrency: int,
    write_credentials_template: bool = True,
    yes: bool = False,
    profile: str | None = None,
) -> int | None:
    files_to_check = [output]
    if write_credentials_template:
        stem = os.path.splitext(output)[0]
        files_to_check += [f"{stem}.fs-credentials.csv", f"{stem}.storage-credentials.csv"]
    existing_files = await asyncio.to_thread(lambda: [f for f in files_to_check if os.path.exists(f)])
    if existing_files:
        print("The following file(s) already exist and will be overwritten:", file=sys.stderr)
        for f in existing_files:
            print(f"  {f}", file=sys.stderr)
        if not yes:
            confirmed = await prompt_yes_no("Overwrite? [y/N] ")
            if not confirmed:
                print("Aborted.", file=sys.stderr)
                return 1

    sem = asyncio.Semaphore(concurrency)
    protection_data: list[dict[str, Any]] = []
    retirement_data: list[dict[str, Any]] = []
    tiering_data: list[dict[str, Any]] = []
    fs_data: list[dict[str, Any]] = []
    bs_data: list[dict[str, Any]] = []
    rs_data: list[dict[str, Any]] = []

    async with make_client(profile=profile) as apm:
        print("Fetching backup server and remote storage registry...", file=sys.stderr)
        bs_list, _ = await paginate(
            lambda limit, offset: apm.backup_servers.list(limit=limit, offset=offset)
        )
        rs_list, _ = await apm.remote_storages.list()

        bs_ref_keys: dict[str, str] = {}
        for idx, bs in enumerate(bs_list, 1):
            rk = f"server-{idx}"
            bs_ref_keys[bs.name] = rk
            bs_data.append(_ser_backup_server(bs, rk))

        rs_ref_keys: dict[str, str] = {}
        for idx, rs in enumerate(rs_list, 1):
            rk = f"storage-{idx}"
            rs_ref_keys[rs.name] = rk
            rs_data.append(_ser_remote_storage(rs, rk))

        print("Fetching machine protection plans...", file=sys.stderr)
        machine_stubs, _ = await paginate(
            lambda limit, offset: apm.machine.plans.list(limit=limit, offset=offset)
        )
        print(f"  Fetching details for {len(machine_stubs)} machine plan(s)...", file=sys.stderr)
        machine_plans = await _fetch_protection_details(apm, machine_stubs, sem, is_machine=True)

        print("Fetching M365 protection plans...", file=sys.stderr)
        m365_stubs, _ = await paginate(
            lambda limit, offset: apm.m365.plans.list(limit=limit, offset=offset)
        )
        print(f"  Fetching details for {len(m365_stubs)} M365 plan(s)...", file=sys.stderr)
        m365_plans = await _fetch_protection_details(apm, m365_stubs, sem, is_machine=False)

        all_plans = machine_plans + m365_plans
        # Key by plan_id to guarantee a unique ref per plan even when names collide.
        plan_ref_by_id: dict[str, str] = {p.plan_id: f"plan-{idx}" for idx, p in enumerate(all_plans, 1)}

        protection_data = [
            _ser_protection_plan(p, bs_ref_keys, rs_ref_keys, plan_ref_by_id[p.plan_id])
            for p in all_plans
        ]

        print("Fetching retirement plans...", file=sys.stderr)
        ret_plans, _ = await paginate(
            lambda limit, offset: apm.retirement_plans.list(limit=limit, offset=offset)
        )
        retirement_data = [_ser_retirement_plan(p) for p in ret_plans]

        print("Fetching tiering plans...", file=sys.stderr)
        tier_plans, _ = await paginate(
            lambda limit, offset: apm.tiering_plans.list(limit=limit, offset=offset)
        )
        tiering_data = [_ser_tiering_plan(p, rs_ref_keys) for p in tier_plans]

        print("Fetching File Server workloads...", file=sys.stderr)
        fs_workloads, _ = await paginate(
            lambda limit, offset: apm.machine.workloads.list(
                workload_types=[MachineWorkloadType.FS], limit=limit, offset=offset
            )
        )
        fs_data = [d for wl in fs_workloads if (d := _ser_file_server(wl, bs_ref_keys, plan_ref_by_id))]
        print(f"  {len(fs_data)} File Server workload(s) found.", file=sys.stderr)

        print("Fetching M365 auto-backup rules...", file=sys.stderr)
        m365_tenants = await list_m365_tenants(apm)
        saas_data: list[dict[str, Any]] = []
        saas_tenant_ref_keys: dict[str, str] = {}
        for idx, t in enumerate(m365_tenants, 1):
            rk = f"tenant-{idx}"
            saas_tenant_ref_keys[t.tenant_id] = rk
            saas_data.append(_ser_saas_tenant(t, rk))
        plan_id_to_ref: dict[str, str] = dict(plan_ref_by_id)
        bs_ns_to_ref: dict[str, str] = {bs.namespace: bs_ref_keys[bs.name] for bs in bs_list}

        async def _get_tenant_rules(t: SaasTenant) -> tuple[SaasTenant, M365AutoBackupRuleListResult] | None:
            try:
                async with sem:
                    res = await apm.m365.auto_backup_rules.list(t.tenant_id)
                return (t, res)
            except APMError as e:
                print(f"  Warning: failed to fetch auto-backup rules for {t.tenant_name!r}: {e}", file=sys.stderr)
                return None

        tenant_rule_pairs: list[tuple[SaasTenant, M365AutoBackupRuleListResult]] = [
            r for r in await asyncio.gather(*[_get_tenant_rules(t) for t in m365_tenants])
            if r is not None
        ]
        m365_auto_bkp_data: list[dict[str, Any]] = []
        for t, res in tenant_rule_pairs:
            block = _ser_m365_auto_backup_rules_block(
                t, res, plan_id_to_ref, bs_ns_to_ref,
                saas_tenant_ref_keys.get(t.tenant_id, t.tenant_id),
            )
            if block is not None:
                m365_auto_bkp_data.append(block)
        print(f"  {len(saas_data)} SaaS tenant(s), {len(m365_auto_bkp_data)} with auto-backup rules.", file=sys.stderr)

    await asyncio.to_thread(
        _write_export_yaml,
        output,
        bs_data=bs_data,
        rs_data=rs_data,
        protection_data=protection_data,
        retirement_data=retirement_data,
        tiering_data=tiering_data,
        fs_data=fs_data,
        saas_data=saas_data,
        m365_auto_bkp_data=m365_auto_bkp_data,
    )

    print(
        f"Exported {len(protection_data)} protection, "
        f"{len(retirement_data)} retirement, "
        f"{len(tiering_data)} tiering plan(s), "
        f"{len(fs_data)} File Server workload(s), "
        f"{len(bs_data)} backup server(s), "
        f"{len(rs_data)} remote storage(s), "
        f"{len(saas_data)} SaaS tenant(s), "
        f"{len(m365_auto_bkp_data)} M365 auto-backup tenant(s) → {output}",
        file=sys.stderr,
    )

    if write_credentials_template:
        fs_creds_path = f"{stem}.fs-credentials.csv"
        fs_cred_rows: list[tuple[str, str]] = sorted({
            (wl.fs_config.host_ip, wl.fs_config.login_user)
            for wl in fs_workloads
            if wl.fs_config is not None and not wl.is_retired
        })
        await asyncio.to_thread(_write_fs_credentials_csv, fs_creds_path, fs_cred_rows)
        os.chmod(fs_creds_path, 0o600)
        print(
            f"Wrote FS credentials template: {fs_creds_path}  ({len(fs_cred_rows)} entries)",
            file=sys.stderr,
        )

        storage_creds_path = f"{stem}.storage-credentials.csv"  # stem bound at existence-check block above
        storage_cred_rows: list[tuple[str, str, str]] = sorted({
            (rs.storage_type.value, _rs_cred_endpoint(rs), rs.vault_name)
            for rs in rs_list
            if rs.storage_type in _IMPORTABLE_RS_TYPES and rs.vault_name
        })
        await asyncio.to_thread(_write_storage_credentials_csv, storage_creds_path, storage_cred_rows)
        os.chmod(storage_creds_path, 0o600)
        print(
            f"Wrote storage credentials template: {storage_creds_path}  ({len(storage_cred_rows)} entries)",
            file=sys.stderr,
        )

    return 0


# ── Import ───────────────────────────────────────────────────────────────────


@dataclass
class _ImportEntry:
    name: str
    kind: str     # "protection-plan" | "retirement-plan" | "tiering-plan"
    subtype: str  # "machine" | "m365" | "" (retirement/tiering)
    raw: dict[str, Any]
    request: (
        MachinePlanCreateRequest
        | M365PlanCreateRequest
        | RetirementPlanCreateRequest
        | TieringPlanCreateRequest
        | None
    )
    parse_error: str | None
    resolved_name: str | None = None  # real display name when name is a UUID


@dataclass
class _ImportResult:
    entry: _ImportEntry
    action: str   # "create" | "overwrite" | "skip" | "error"
    result: str   # "ok" | "skipped" | "failed"
    error_msg: str


# ·· Loaders


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError("YAML file must contain a mapping at the top level.")
    if raw.get("version") != 1:
        raise ValueError(f"Unsupported YAML schema version: {raw.get('version')!r} (expected 1).")
    return raw


def _warn_if_world_readable(path: str, label: str) -> None:
    """Warn on stderr if the credential file is readable by group or others."""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(
            f"WARNING: {label} file {path!r} is readable by group or others "
            f"(mode {oct(mode)}). Consider: chmod 600 {path}",
            file=sys.stderr,
        )


def _read_credential_rows(
    path: str, required_cols: set[str], header_error: str
) -> list[dict[str, str]]:
    """Read a credential CSV, skipping blank/comment lines and validating the header.

    Raises ValueError(header_error) if the header is missing or lacks a required column.
    Returns the data rows; the caller extracts and validates per-row fields.
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames is None or not required_cols.issubset(reader.fieldnames):
        raise ValueError(header_error)
    return list(reader)


def _load_fs_credentials(path: str) -> dict[tuple[str, str], str]:
    """Load a file server credential CSV.

    Returns {(endpoint, login_user): password}.
    Expected columns: endpoint, login_user, password (extra columns ignored).
    Blank lines and lines starting with '#' are skipped.
    """
    result: dict[tuple[str, str], str] = {}
    rows = _read_credential_rows(
        path, {"endpoint", "login_user", "password"},
        "fs-credentials file must have a header row with endpoint, login_user, password columns",
    )
    for i, row in enumerate(rows, start=2):
        endpoint   = (row.get("endpoint") or "").strip()
        login_user = (row.get("login_user") or "").strip()
        pw         = (row.get("password") or "").strip()
        if not endpoint:
            raise ValueError(f"fs-credentials file row {i}: endpoint must not be empty")
        if not login_user:
            raise ValueError(f"fs-credentials file row {i}: login_user must not be empty")
        result[(endpoint, login_user)] = pw
    _warn_if_world_readable(path, "fs-credentials")
    return result


def _load_rs_credentials(path: str) -> dict[tuple[str, str, str], dict[str, str]]:
    """Load a remote storage credential CSV.

    Returns {(storage_type, endpoint, vault_name): {access_key, secret_key, relink_encryption_key}}.
    Required columns: storage_type, endpoint, vault_name, access_key, secret_key.
    Optional column: relink_encryption_key (defaults to "" if missing or blank).
    """
    result: dict[tuple[str, str, str], dict[str, str]] = {}
    rows = _read_credential_rows(
        path, {"storage_type", "endpoint", "vault_name", "access_key", "secret_key"},
        "storage-credentials file must have a header row with "
        "storage_type, endpoint, vault_name, access_key, secret_key columns",
    )
    for i, row in enumerate(rows, start=2):
        storage_type = (row.get("storage_type") or "").strip()
        endpoint     = (row.get("endpoint") or "").strip()
        vault_name   = (row.get("vault_name") or "").strip()
        access_key   = (row.get("access_key") or "").strip()
        secret_key   = (row.get("secret_key") or "").strip()
        relink_key   = (row.get("relink_encryption_key") or "").strip()
        if not vault_name:
            raise ValueError(f"storage-credentials file row {i}: vault_name must not be empty")
        result[(storage_type, endpoint, vault_name)] = {
            "access_key": access_key,
            "secret_key": secret_key,
            "relink_encryption_key": relink_key,
        }
    _warn_if_world_readable(path, "storage-credentials")
    return result


def _write_rs_credentials(
    path: str,
    creds: dict[tuple[str, str, str], dict[str, str]],
    backup_suffix: str,
) -> None:
    """Atomically rewrite the credential CSV with updated creds.

    Writes to a .tmp file first, then renames the original to .bak, then atomically
    replaces the original with the tmp file. If the write fails before replacement,
    the original is preserved. If it fails after the .bak rename, the .tmp file
    retains the new encryption keys for manual recovery.
    """
    tmp_path = f"{path}.tmp"
    bak_path = f"{path}.{backup_suffix}.bak"
    # Create the tmp file owner-only (0600) so the secrets it holds are never
    # world-readable, and so the final os.replace() preserves that mode.
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=[
                "storage_type", "endpoint", "vault_name",
                "access_key", "secret_key", "relink_encryption_key",
            ]
        )
        writer.writeheader()
        for (storage_type, endpoint, vault_name), vals in creds.items():
            writer.writerow({
                "storage_type":          storage_type,
                "endpoint":              endpoint,
                "vault_name":            vault_name,
                "access_key":            vals.get("access_key", ""),
                "secret_key":            vals.get("secret_key", ""),
                "relink_encryption_key": vals.get("relink_encryption_key", ""),
            })
    os.replace(path, bak_path)
    os.replace(tmp_path, path)


# ·· Plan import


def _parse_all_entries(
    data: dict[str, Any],
    backup_servers_by_ref: dict[str, BackupServer],
    remote_storages_by_ref: dict[str, RemoteStorage],
    rs_pending_refs: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[_ImportEntry]:
    entries: list[_ImportEntry] = []

    for raw in (data.get("protection_plans") or []):
        name = str(raw.get("name_or_id", ""))
        subtype = str(raw.get("type", "machine")).lower()
        try:
            req = _parse_protection_request(raw, backup_servers_by_ref, remote_storages_by_ref)
            entries.append(_ImportEntry(
                name=name, kind="protection-plan", subtype=subtype, raw=raw, request=req, parse_error=None,
            ))
        except (KeyError, ValueError) as e:
            # If the parse error is due to a missing RS ref that is pending creation, defer.
            bc_d = raw.get("backup_copy") if isinstance(raw, dict) else None
            dest_ref = str((bc_d or {}).get("destination_ref") or "") if isinstance(bc_d, dict) else ""
            dest_type = str((bc_d or {}).get("destination_type") or "") if isinstance(bc_d, dict) else ""
            if dest_type == "remote_storage" and dest_ref in rs_pending_refs:
                entries.append(_ImportEntry(
                    name=name, kind="protection-plan", subtype=subtype, raw=raw, request=None, parse_error=None,
                ))
            else:
                entries.append(_ImportEntry(
                    name=name, kind="protection-plan", subtype=subtype, raw=raw, request=None, parse_error=str(e),
                ))

    for raw in (data.get("retirement_plans") or []):
        name = str(raw.get("name_or_id") or raw.get("name", ""))
        try:
            ret_req = _parse_retirement_request(raw)
            entries.append(_ImportEntry(
                name=name, kind="retirement-plan", subtype="", raw=raw, request=ret_req, parse_error=None,
            ))
        except (KeyError, ValueError) as e:
            entries.append(_ImportEntry(
                name=name, kind="retirement-plan", subtype="", raw=raw, request=None, parse_error=str(e),
            ))

    for raw in (data.get("tiering_plans") or []):
        name = str(raw.get("name_or_id") or raw.get("name", ""))
        try:
            tier_req = _parse_tiering_request(raw, remote_storages_by_ref)
            entries.append(_ImportEntry(
                name=name, kind="tiering-plan", subtype="", raw=raw, request=tier_req, parse_error=None,
            ))
        except (KeyError, ValueError) as e:
            dest_ref = str(raw.get("destination_ref") or "") if isinstance(raw, dict) else ""
            if dest_ref in rs_pending_refs:
                entries.append(_ImportEntry(
                    name=name, kind="tiering-plan", subtype="", raw=raw, request=None, parse_error=None,
                ))
            else:
                entries.append(_ImportEntry(
                    name=name, kind="tiering-plan", subtype="", raw=raw, request=None, parse_error=str(e),
                ))

    return entries


async def _check_conflicts(
    apm: APMClient,
    entries: list[_ImportEntry],
    plan_stubs: list[ProtectionPlan],
) -> dict[str, str | None]:
    """Return {kind:name → existing plan_id} for every entry; None means not found."""
    existing: dict[str, str | None] = {}

    def _find_protection_stub(name_or_id: str) -> ProtectionPlan | None:
        if _is_uuid(name_or_id):
            return next((s for s in plan_stubs if s.plan_id == name_or_id), None)
        q = name_or_id.lower()
        return next((s for s in plan_stubs if s.name.lower() == q), None)

    async def _check_one(entry: _ImportEntry) -> None:
        key = f"{entry.kind}:{entry.name}"
        try:
            if entry.kind == "protection-plan":
                stub = _find_protection_stub(entry.name)
                if stub is not None:
                    if _is_uuid(entry.name):
                        # Resolve the UUID to the real display name so overwrite uses
                        # the correct plan name (not the UUID).
                        entry.resolved_name = stub.name
                    existing[key] = stub.plan_id
                    if entry.parse_error is None:
                        expected_cat = WorkloadCategory.M365 if entry.subtype == "m365" else WorkloadCategory.MACHINE
                        if stub.category != expected_cat:
                            existing_type = "m365" if stub.category == WorkloadCategory.M365 else "machine"
                            entry.parse_error = (
                                f"type conflict: YAML declares type={entry.subtype!r} "
                                f"but the existing plan is type={existing_type!r}"
                            )
                        elif (
                            entry.parse_error is None
                            and entry.raw.get("is_immutable", False) != stub.is_immutable
                        ):
                            yaml_immutable = entry.raw.get("is_immutable", False)
                            entry.parse_error = (
                                f"immutability conflict: YAML declares is_immutable={yaml_immutable} "
                                f"but the existing plan has is_immutable={stub.is_immutable}"
                            )
                else:
                    if _is_uuid(entry.name) and entry.parse_error is None:
                        entry.parse_error = (
                            f"protection plan UUID {entry.name!r} not found on this server"
                        )
                    existing[key] = None
            elif entry.kind == "retirement-plan":
                if _is_uuid(entry.name):
                    ret = await apm.retirement_plans.get(entry.name)
                    entry.resolved_name = ret.name
                else:
                    ret = await apm.retirement_plans.get_by_name(entry.name)
                existing[key] = ret.plan_id
            else:
                if _is_uuid(entry.name):
                    tier = await apm.tiering_plans.get(entry.name)
                    entry.resolved_name = tier.name
                else:
                    tier = await apm.tiering_plans.get_by_name(entry.name)
                existing[key] = tier.plan_id
        except ResourceNotFoundError:
            existing[key] = None
            if (
                entry.kind in ("retirement-plan", "tiering-plan")
                and _is_uuid(entry.name)
                and entry.parse_error is None
            ):
                entry.parse_error = f"{entry.kind} UUID {entry.name!r} not found on this server"
        except APMError as e:
            if entry.parse_error is None:
                entry.parse_error = f"conflict check failed: {e}"
            print(f"  Warning: could not check {entry.name!r}: {e}", file=sys.stderr)

    await asyncio.gather(*[_check_one(e) for e in entries])
    return existing


def _determine_action(entry: _ImportEntry, existing_id: str | None, on_conflict: str) -> str:
    if entry.parse_error is not None:
        return "error"
    if existing_id is not None:
        return "overwrite" if on_conflict == "overwrite" else "skip"
    return "create"


def _build_plan_requests(
    entries: list[_ImportEntry],
    backup_servers_by_ref: dict[str, BackupServer],
    remote_storages_by_ref: dict[str, RemoteStorage],
) -> None:
    """Build request objects for plan entries whose request is still pending (request=None, parse_error=None).

    Called after RS creation so the ref map includes newly created storages.
    Entries with an already-built request or a parse error are skipped.
    Sets entry.parse_error on failure.
    """
    for entry in entries:
        if entry.parse_error is not None or entry.request is not None:
            continue
        try:
            if entry.kind == "protection-plan":
                entry.request = _parse_protection_request(
                    entry.raw, backup_servers_by_ref, remote_storages_by_ref
                )
            elif entry.kind == "retirement-plan":
                entry.request = _parse_retirement_request(entry.raw)
            else:
                entry.request = _parse_tiering_request(entry.raw, remote_storages_by_ref)
        except (KeyError, ValueError) as e:
            entry.parse_error = str(e)


def _build_fs_requests(
    fs_entries: list[_FsEntry],
    fs_creds: dict[tuple[str, str], str] | None,
    fs_actions: dict[str, str],
    plans_by_name: dict[str, str],
) -> None:
    """Build FS request objects for entries with create or overwrite actions.

    For creates: a missing or empty password is a parse error that sets action to error.
    For overwrites: a missing or empty credential keeps the existing stored password.
    Modifies fs_entries and fs_actions in place.
    """
    for fse in fs_entries:
        fse_key = f"{fse.host_ip}:{fse.resolved_namespace}:{fse.plan_name}"
        action = fs_actions.get(fse_key, "error")
        if action in ("skip", "error"):
            continue
        fse.login_user = str(fse.raw.get("login_user", ""))
        pw: str | None = fs_creds.get((fse.host_ip, fse.login_user)) if fs_creds is not None else None
        if action == "create":
            if not pw:
                key_str = f"endpoint={fse.host_ip!r}, login_user={fse.login_user!r}"
                fse.parse_error = (
                    "no fs-credentials file provided — use --fs-credentials to supply a password"
                    if fs_creds is None
                    else (
                        f"password is empty for {key_str} in fs-credentials file"
                        if pw is not None
                        else f"credential not found for {key_str} in fs-credentials file"
                    )
                )
                fs_actions[fse_key] = "error"
                continue
            try:
                fse.request = _build_fs_add_request(
                    fse.raw,
                    plans_by_name.get(fse.plan_name, ""),
                    fse.resolved_namespace,
                    pw,
                    fse.login_user,
                )
            except (KeyError, ValueError) as e:
                fse.parse_error = str(e)
                fs_actions[fse_key] = "error"
        else:  # overwrite
            if not pw and fs_creds is not None:
                print(
                    f"  WARNING: no credential found for "
                    f"endpoint={fse.host_ip!r}, login_user={fse.login_user!r} "
                    "in fs-credentials file — keeping existing stored password.",
                    file=sys.stderr,
                )
            try:
                fse.request = _build_fs_update_request(fse.raw, pw if pw else None, fse.login_user)
            except (KeyError, ValueError) as e:
                fse.parse_error = str(e)
                fs_actions[fse_key] = "error"


async def _execute_one(
    apm: APMClient,
    entry: _ImportEntry,
    action: str,
    existing_id: str | None,
) -> _ImportResult:
    if action == "error":
        return _ImportResult(entry=entry, action=action, result="failed", error_msg=entry.parse_error or "parse error")
    if action == "skip":
        return _ImportResult(entry=entry, action=action, result="skipped", error_msg="")

    req = entry.request
    if req is None:
        return _ImportResult(entry=entry, action=action, result="failed", error_msg="no request")

    # When name_or_id was a UUID, resolved_name holds the plan's real display name.
    # Use it instead of the UUID so the overwrite call does not rename the plan.
    if entry.resolved_name is not None and isinstance(
        req, (MachinePlanCreateRequest, M365PlanCreateRequest,
              RetirementPlanCreateRequest, TieringPlanCreateRequest)
    ):
        req = replace(req, name=entry.resolved_name)

    try:
        if action == "create":
            if isinstance(req, MachinePlanCreateRequest):
                await apm.machine.plans.create(req)
            elif isinstance(req, M365PlanCreateRequest):
                await apm.m365.plans.create(req)
            elif isinstance(req, RetirementPlanCreateRequest):
                await apm.retirement_plans.create(req)
            else:
                await apm.tiering_plans.create(req)
        else:
            plan_id = existing_id or ""
            if isinstance(req, MachinePlanCreateRequest):
                await apm.machine.plans.update(plan_id, req)
            elif isinstance(req, M365PlanCreateRequest):
                await apm.m365.plans.update(plan_id, req)
            elif isinstance(req, RetirementPlanCreateRequest):
                await apm.retirement_plans.update(plan_id, req)
            else:
                await apm.tiering_plans.update(plan_id, req)
        return _ImportResult(entry=entry, action=action, result="ok", error_msg="")
    except PlanNameConflictError as e:
        return _ImportResult(entry=entry, action=action, result="failed", error_msg=f"name conflict: {e}")
    except APMError as e:
        return _ImportResult(entry=entry, action=action, result="failed", error_msg=str(e))
    except ValueError as e:
        return _ImportResult(entry=entry, action=action, result="failed", error_msg=str(e))


def _status_line(result: str, error_msg: str, *, ok_warning: bool = False) -> str:
    """Format the inline per-item status shown after '  [action] name... '.

    With ok_warning=True, a non-empty error_msg on an 'ok' result is rendered as a warning
    suffix (used for remote storages, which can succeed with a relink warning).
    """
    if result == "ok":
        return f"ok (warning: {error_msg})" if ok_warning and error_msg else "ok"
    if result == "failed":
        return f"failed: {error_msg}"
    return "skipped"


def _result_cell(result: str, error_msg: str, *, ok_warning: bool = False) -> str:
    """Format the Result column of the final summary table.

    With ok_warning=True, a non-empty error_msg on an 'ok' result is appended as a warning.
    """
    if ok_warning and result == "ok" and error_msg:
        return f"{result} (warning: {error_msg})"
    if result == "failed" and error_msg:
        return f"{result}: {error_msg}"
    return result


def _print_table(rows: Sequence[tuple[str, ...]], headers: tuple[str, ...]) -> None:
    all_rows: list[tuple[str, ...]] = [headers, *rows]
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    sep = "  ".join("-" * w for w in widths)
    for i, row in enumerate(all_rows):
        print("  ".join(row[j].ljust(widths[j]) for j in range(len(headers))))
        if i == 0:
            print(sep)


def _build_final_rows(
    results: list[_ImportResult],
    fs_results: list[_FsResult],
    rs_results: list[_RsResult],
    m365_results: list[_M365RuleResult],
) -> list[tuple[str, str, str, str]]:
    """Build the rows of the final summary table across all four result categories."""
    return [
        (ir.entry.name, ir.entry.subtype or ir.entry.kind, ir.action,
         _result_cell(ir.result, ir.error_msg))
        for ir in results
    ] + [
        (fsr.entry.host_ip, "file_server", fsr.action,
         _result_cell(fsr.result, fsr.error_msg))
        for fsr in fs_results
    ] + [
        (rsr.entry.name_or_id, "remote_storage", rsr.action,
         _result_cell(rsr.result, rsr.error_msg, ok_warning=True))
        for rsr in rs_results
    ] + [
        (mr.label, mr.kind, mr.action, _result_cell(mr.result, mr.error_msg))
        for mr in m365_results
    ]


def _summarize_results(
    results: list[_ImportResult],
    fs_results: list[_FsResult],
    rs_results: list[_RsResult],
    m365_results: list[_M365RuleResult],
) -> tuple[int, int]:
    """Return (succeeded, failed) counts across all result categories."""
    all_results: list[_ImportResult | _FsResult | _RsResult | _M365RuleResult] = [
        *results, *fs_results, *rs_results, *m365_results,
    ]
    n_ok = sum(1 for r in all_results if r.result == "ok")
    n_failed = sum(1 for r in all_results if r.result == "failed")
    return n_ok, n_failed


# ·· File server import


def _parse_fs_entries(
    data: dict[str, Any],
    plans_by_name: dict[str, str],
    plan_name_by_ref: dict[str, str],
    backup_servers_by_ref: dict[str, BackupServer],
) -> list[_FsEntry]:
    entries: list[_FsEntry] = []
    for raw in (data.get("file_servers") or []):
        host_ip = str(raw.get("host_ip", ""))
        bs_ref = str(raw.get("backup_server_ref") or "")
        plan_ref = str(raw.get("plan_ref", ""))
        plan_name = plan_name_by_ref.get(plan_ref, "")
        parse_error: str | None = None
        resolved_namespace = ""

        if not host_ip:
            parse_error = "host_ip is required"
        elif not bs_ref:
            parse_error = "backup_server_ref is required"
        elif not plan_ref:
            parse_error = "plan_ref is required"
        elif not plan_name:
            parse_error = f"plan_ref {plan_ref!r} not found in protection_plans section"
        elif plan_name not in plans_by_name:
            parse_error = f"plan {plan_name!r} (plan_ref={plan_ref!r}) not found on this server"
        else:
            bs = backup_servers_by_ref.get(bs_ref)
            if bs is None:
                parse_error = (
                    f"backup_server_ref {bs_ref!r} not found "
                    f"(check reference resolution errors above)"
                )
            else:
                resolved_namespace = bs.namespace

        entries.append(_FsEntry(
            host_ip=host_ip,
            backup_server_ref=bs_ref,
            resolved_namespace=resolved_namespace,
            plan_name=plan_name,
            raw=raw,
            parse_error=parse_error,
        ))
    return entries


async def _execute_one_fs(
    apm: APMClient,
    entry: _FsEntry,
    action: str,
    existing_wl: MachineWorkload | None,
) -> _FsResult:
    if action == "error":
        return _FsResult(entry=entry, action=action, result="failed", error_msg=entry.parse_error or "parse error")
    if action == "skip":
        return _FsResult(entry=entry, action=action, result="skipped", error_msg="")
    try:
        if action == "create":
            if not isinstance(entry.request, FileServerAddRequest):
                return _FsResult(entry=entry, action=action, result="failed",
                                 error_msg="internal error: add request not built")
            await apm.machine.workloads.add_file_server(entry.request)
        else:
            if existing_wl is None:
                return _FsResult(entry=entry, action=action, result="failed", error_msg="existing workload not found")
            if not isinstance(entry.request, FileServerUpdateRequest):
                return _FsResult(entry=entry, action=action, result="failed",
                                 error_msg="internal error: update request not built")
            await apm.machine.workloads.update_file_server(existing_wl, entry.request)
        return _FsResult(entry=entry, action=action, result="ok", error_msg="")
    except DuplicateWorkloadError as e:
        return _FsResult(entry=entry, action=action, result="failed", error_msg=f"duplicate: {e.message}")
    except APMError as e:
        return _FsResult(entry=entry, action=action, result="failed", error_msg=str(e))


# ·· Remote storage import


def _build_rs_add_request(
    storage_type: RemoteStorageType,
    *,
    vault_name: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    relink_key: str,
    encryption_enabled: bool,
    trust_self_signed: bool,
) -> (
    GenericS3StorageAddRequest
    | APVStorageAddRequest
    | AmazonS3StorageAddRequest
    | AmazonS3ChinaStorageAddRequest
    | C2ObjectStorageAddRequest
    | WasabiCloudStorageAddRequest
):
    """Build the add request for an importable remote storage type.

    ActiveProtect Vault and S3-compatible take a caller-supplied endpoint and
    trust_self_signed; the endpoint-free types (Amazon S3 / S3 China / C2 / Wasabi) do not.
    """
    if storage_type == RemoteStorageType.ACTIVE_PROTECT_VAULT:
        return APVStorageAddRequest(
            access_key=access_key, secret_key=secret_key,
            endpoint=endpoint, encryption_enabled=encryption_enabled,
            relink_encryption_key=relink_key,
            trust_self_signed=trust_self_signed,
        )
    if storage_type == RemoteStorageType.S3_COMPATIBLE:
        return GenericS3StorageAddRequest(
            access_key=access_key, secret_key=secret_key,
            vault_name=vault_name, endpoint=endpoint,
            encryption_enabled=encryption_enabled,
            relink_encryption_key=relink_key,
            trust_self_signed=trust_self_signed,
        )
    if storage_type == RemoteStorageType.AMAZON_S3:
        return AmazonS3StorageAddRequest(
            access_key=access_key, secret_key=secret_key,
            vault_name=vault_name,
            encryption_enabled=encryption_enabled,
            relink_encryption_key=relink_key,
        )
    if storage_type == RemoteStorageType.AMAZON_S3_CHINA:
        return AmazonS3ChinaStorageAddRequest(
            access_key=access_key, secret_key=secret_key,
            vault_name=vault_name,
            encryption_enabled=encryption_enabled,
            relink_encryption_key=relink_key,
        )
    if storage_type == RemoteStorageType.C2_OBJECT_STORAGE:
        return C2ObjectStorageAddRequest(
            access_key=access_key, secret_key=secret_key,
            vault_name=vault_name,
            encryption_enabled=encryption_enabled,
            relink_encryption_key=relink_key,
        )
    return WasabiCloudStorageAddRequest(
        access_key=access_key, secret_key=secret_key,
        vault_name=vault_name,
        encryption_enabled=encryption_enabled,
        relink_encryption_key=relink_key,
    )


def _parse_rs_entries(
    data: dict[str, Any],
    rs_creds: dict[tuple[str, str, str], dict[str, str]],
) -> list[_RsEntry]:
    entries: list[_RsEntry] = []
    for raw in (data.get("remote_storages") or []):
        name_or_id       = str(raw.get("name_or_id", ""))
        ref_key          = str(raw.get("ref_key", ""))
        endpoint         = str(raw.get("endpoint", ""))
        vault_name       = str(raw.get("vault_name", ""))
        storage_type_str = str(raw.get("storage_type", ""))
        cred = rs_creds.get((storage_type_str, endpoint, vault_name))
        if cred is None:
            print(
                f"  Warning: remote storage {name_or_id!r} "
                f"(type={storage_type_str!r}, endpoint={endpoint!r}, vault={vault_name!r})"
                " has no matching row in storage-credentials file — skipping.",
                file=sys.stderr,
            )
            continue
        try:
            storage_type = RemoteStorageType(storage_type_str)
        except ValueError:
            entries.append(_RsEntry(
                name_or_id=name_or_id, ref_key=ref_key,
                endpoint=endpoint, vault_name=vault_name,
                storage_type_str=storage_type_str, raw=raw,
                parse_error=f"unrecognized storage_type {storage_type_str!r}",
            ))
            continue
        if storage_type not in _IMPORTABLE_RS_TYPES:
            print(
                f"  Warning: remote storage {name_or_id!r} has type {storage_type_str!r} "
                "— not supported for import, skipping.",
                file=sys.stderr,
            )
            continue
        entries.append(_RsEntry(
            name_or_id=name_or_id, ref_key=ref_key,
            endpoint=endpoint, vault_name=vault_name,
            storage_type_str=storage_type_str, raw=raw,
            parse_error=None,
        ))
    return entries


async def _execute_one_rs(
    apm: APMClient,
    entry: _RsEntry,
    action: str,
    existing_storage: RemoteStorage | None,
) -> _RsResult:
    if action == "error":
        return _RsResult(entry=entry, action=action, result="failed", error_msg=entry.parse_error or "parse error")
    if action == "skip":
        return _RsResult(entry=entry, action=action, result="skipped", error_msg="")
    req = entry.request
    if req is None:
        return _RsResult(entry=entry, action=action, result="failed", error_msg="no request built")
    try:
        if action == "create":
            if isinstance(req, RemoteStorageUpdateRequest):
                return _RsResult(entry=entry, action=action, result="failed",
                                 error_msg="internal error: wrong request type for create")
            add_result = await apm.remote_storages.add(req)
            return _RsResult(
                entry=entry, action=action, result="ok",
                error_msg=add_result.relink_warning or "",
                issued_encryption_key=add_result.encryption_key,
                created_storage=add_result.storage,
            )
        else:
            if existing_storage is None:
                return _RsResult(entry=entry, action=action, result="failed",
                                 error_msg="existing storage not found")
            if not isinstance(req, RemoteStorageUpdateRequest):
                return _RsResult(entry=entry, action=action, result="failed",
                                 error_msg="internal error: wrong request type for update")
            await apm.remote_storages.update(existing_storage, req)
            return _RsResult(entry=entry, action=action, result="ok", error_msg="")
    except RemoteStorageConflictError as e:
        return _RsResult(entry=entry, action=action, result="failed", error_msg=f"conflict: {e}")
    except RemoteStorageInUseError as e:
        return _RsResult(entry=entry, action=action, result="failed", error_msg=f"in use: {e}")
    except RemoteStorageUnmanagedCatalogError as e:
        return _RsResult(entry=entry, action=action, result="failed",
                         error_msg=(
                             f"unmanaged catalogs ({e.catalog_count}) in vault {e.vault_name!r}; "
                             "re-add manually via the SDK and pass unmanaged_retirement_plan"
                         ))
    except APMError as e:
        return _RsResult(entry=entry, action=action, result="failed", error_msg=str(e))


def _select_rs_actions(
    rs_entries: list[_RsEntry],
    rs_creds: dict[tuple[str, str, str], dict[str, str]] | None,
    on_conflict: str,
    existing_rs: dict[str, RemoteStorage],
    existing_rs_by_name: dict[str, RemoteStorage],
) -> dict[str, str]:
    """Select create/overwrite/skip/error per remote storage entry and build its request.

    Mutates each entry's ``request`` (and ``parse_error`` on a build failure).
    Returns {rs_key: action}.
    """
    rs_actions: dict[str, str] = {}
    for rse in rs_entries:
        if rse.parse_error:
            rs_actions[_rs_key(rse)] = "error"
            continue
        storage_type = RemoteStorageType(rse.storage_type_str)  # validated in _parse_rs_entries
        rs_cred = (rs_creds or {}).get((rse.storage_type_str, rse.endpoint, rse.vault_name), {})
        access_key = rs_cred.get("access_key", "")
        secret_key = rs_cred.get("secret_key", "")
        relink_key = rs_cred.get("relink_encryption_key", "")
        encryption_enabled = bool(rse.raw.get("encryption_enabled", False))
        trust_self_signed = bool(rse.raw.get("trust_self_signed", False))
        # Match an existing storage by UUID or name.
        if _is_uuid(rse.name_or_id):
            match_rs = existing_rs.get(rse.name_or_id)
        else:
            match_rs = existing_rs_by_name.get(rse.name_or_id.lower())
        if match_rs is not None:
            action = "overwrite" if on_conflict == "overwrite" else "skip"
            rs_actions[_rs_key(rse)] = action
            if action == "overwrite":
                rse.request = RemoteStorageUpdateRequest(
                    access_key=access_key,
                    secret_key=secret_key,
                    endpoint=rse.endpoint,
                    trust_self_signed=trust_self_signed,
                )
        else:
            rs_actions[_rs_key(rse)] = "create"
            try:
                rse.request = _build_rs_add_request(
                    storage_type,
                    vault_name=rse.vault_name,
                    endpoint=rse.endpoint,
                    access_key=access_key,
                    secret_key=secret_key,
                    relink_key=relink_key,
                    encryption_enabled=encryption_enabled,
                    trust_self_signed=trust_self_signed,
                )
            except (KeyError, ValueError) as e:
                rse.parse_error = str(e)
                rs_actions[_rs_key(rse)] = "error"
    return rs_actions


# ·· M365 auto-backup rule import


def _parse_m365_rule_entries(
    data: dict[str, Any],
    backup_servers_by_ref: dict[str, BackupServer],
    m365_plans_by_name: dict[str, str],
    plan_name_by_ref: dict[str, str],
    saas_tenants_by_ref: dict[str, str],
) -> tuple[list[_M365RuleEntry], list[_M365CollabEntry]]:
    """Parse the m365_auto_backup_rules YAML section into rule and collab entries.

    Returns (user_rule_entries, collab_entries).  Errors are captured in entry.parse_error.
    """
    rule_entries: list[_M365RuleEntry] = []
    collab_entries: list[_M365CollabEntry] = []

    def _resolve_plan(ref: str, label: str) -> tuple[str, str | None]:
        """Return (plan_name, error) from a plan_ref."""
        plan_name = plan_name_by_ref.get(ref, "")
        if not plan_name:
            return "", f"plan_ref {ref!r} not found in protection_plans section"
        if plan_name not in m365_plans_by_name:
            return plan_name, f"plan {plan_name!r} ({label}) not found on this server"
        return plan_name, None

    def _resolve_bs(ref: str, label: str) -> tuple[str, str | None]:
        """Return (namespace, error) from a backup_server_ref."""
        bs = backup_servers_by_ref.get(ref)
        if bs is None:
            return "", f"backup_server_ref {ref!r} not found ({label})"
        return bs.namespace, None

    def _resolve_collab_setting(
        raw_d: dict[str, Any] | None,
        label: str,
    ) -> tuple[M365CollabServiceSetting | None, str | None]:
        if raw_d is None:
            return None, None
        bs_ref = raw_d.get("backup_server_ref") or ""
        plan_ref = raw_d.get("plan_ref") or ""
        if not bs_ref:
            return None, "backup_server_ref is required"
        if not plan_ref:
            return None, "plan_ref is required"
        ns, err = _resolve_bs(bs_ref, label)
        if err:
            return None, err
        plan_name, err2 = _resolve_plan(plan_ref, label)
        if err2:
            return None, err2
        plan_id = m365_plans_by_name[plan_name]
        return M365CollabServiceSetting(plan_id=plan_id, namespace=ns), None

    for raw_tenant in (data.get("m365_auto_backup_rules") or []):
        if not isinstance(raw_tenant, dict):
            continue
        raw_ref = str(raw_tenant.get("tenant_ref", ""))
        raw_tid = str(raw_tenant.get("tenant_id", ""))
        if raw_ref:
            tenant_id = saas_tenants_by_ref.get(raw_ref, "")
            if not tenant_id:
                print(
                    f"  Warning: tenant_ref {raw_ref!r} not found in saas_tenants section — skipping.",
                    file=sys.stderr,
                )
                continue
        else:
            tenant_id = raw_tid
        if not tenant_id:
            continue

        # User rules
        for raw_rule in (raw_tenant.get("user_rules") or []):
            if not isinstance(raw_rule, dict):
                continue
            bs_ref = raw_rule.get("backup_server_ref") or ""
            plan_ref = raw_rule.get("plan_ref") or ""
            parse_error: str | None = None
            ns = ""
            plan_id = ""
            if not bs_ref:
                parse_error = "backup_server_ref is required"
            elif not plan_ref:
                parse_error = "plan_ref is required"
            else:
                ns, err = _resolve_bs(bs_ref, f"tenant {tenant_id!r}")
                if err:
                    parse_error = err
                else:
                    plan_name, err2 = _resolve_plan(plan_ref, f"tenant {tenant_id!r}")
                    if err2:
                        parse_error = err2
                    else:
                        plan_id = m365_plans_by_name[plan_name]
            rule_entries.append(_M365RuleEntry(
                tenant_id=tenant_id,
                kind="m365_user_rule",
                backup_server_ref=bs_ref,
                resolved_namespace=ns,
                plan_ref=plan_ref,
                resolved_plan_id=plan_id,
                exchange_groups=list(raw_rule.get("exchange_groups") or []),
                onedrive_groups=list(raw_rule.get("onedrive_groups") or []),
                chat_groups=list(raw_rule.get("chat_groups") or []),
                raw=raw_rule,
                parse_error=parse_error,
            ))

        # Collab services
        collab_d = raw_tenant.get("collab_services")
        if isinstance(collab_d, dict) and collab_d:
            errors: list[str] = []
            ge, err_ge = _resolve_collab_setting(collab_d.get("group_exchange"), f"tenant {tenant_id!r} group_exchange")
            if err_ge:
                errors.append(err_ge)
            ms, err_ms = _resolve_collab_setting(collab_d.get("mysite"), f"tenant {tenant_id!r} mysite")
            if err_ms:
                errors.append(err_ms)
            sp, err_sp = _resolve_collab_setting(collab_d.get("sharepoint"), f"tenant {tenant_id!r} sharepoint")
            if err_sp:
                errors.append(err_sp)
            te, err_te = _resolve_collab_setting(collab_d.get("teams"), f"tenant {tenant_id!r} teams")
            if err_te:
                errors.append(err_te)
            _succeeded = [n for n, v in [
                ("group_exchange", ge), ("mysite", ms), ("sharepoint", sp), ("teams", te),
            ] if v is not None]
            _parse_err: str | None = None
            if errors:
                _parse_err = "; ".join(errors)
                if _succeeded:
                    _parse_err += f" (succeeded but not applied: {', '.join(_succeeded)})"
            collab_entries.append(_M365CollabEntry(
                tenant_id=tenant_id,
                group_exchange=ge,
                mysite=ms,
                sharepoint=sp,
                teams=te,
                parse_error=_parse_err,
            ))

    return rule_entries, collab_entries


async def _execute_m365_rules(
    apm: APMClient,
    tenant_id: str,
    rule_entries: list[_M365RuleEntry],
    collab_entries: list[_M365CollabEntry],
    on_conflict: str,
    sem: asyncio.Semaphore,
    interrupted: asyncio.Event,
) -> list[_M365RuleResult]:
    """Fetch current rules for a tenant, then create/update/skip rules and collab settings."""
    results: list[_M365RuleResult] = []

    try:
        async with sem:
            current = await apm.m365.auto_backup_rules.list(tenant_id)
    except APMError as e:
        results.extend(
            _M365RuleResult(
                label=f"{tenant_id}:{re_.backup_server_ref}",
                kind="m365_user_rule",
                action="error",
                result="failed",
                error_msg=f"failed to fetch current rules: {e}",
            )
            for re_ in rule_entries
        )
        results.extend(
            _M365RuleResult(
                label=tenant_id,
                kind="m365_collab_services",
                action="error",
                result="failed",
                error_msg=f"failed to fetch current rules: {e}",
            )
            for ce in collab_entries
            if ce.tenant_id == tenant_id
        )
        return results

    existing_by_ns_plan: dict[tuple[str, str], M365AutoBackupRule] = {
        (r.namespace, r.plan_id): r for r in current.rules
    }

    for re_ in rule_entries:
        if interrupted.is_set():
            results.append(_M365RuleResult(
                label=f"{tenant_id}:{re_.backup_server_ref}",
                kind="m365_user_rule", action="skip", result="skipped", error_msg="",
            ))
            continue
        if re_.parse_error:
            results.append(_M365RuleResult(
                label=f"{tenant_id}:{re_.backup_server_ref}",
                kind="m365_user_rule", action="error", result="failed", error_msg=re_.parse_error,
            ))
            continue
        existing = existing_by_ns_plan.get((re_.resolved_namespace, re_.resolved_plan_id))
        if existing is not None:
            if on_conflict == "skip":
                results.append(_M365RuleResult(
                    label=f"{tenant_id}:{re_.backup_server_ref}",
                    kind="m365_user_rule", action="skip", result="skipped", error_msg="",
                ))
                continue
            try:
                async with sem:
                    await apm.m365.auto_backup_rules.update(
                        existing,
                        plan_id=re_.resolved_plan_id,
                        exchange_group_ids=re_.exchange_groups,
                        onedrive_group_ids=re_.onedrive_groups,
                        chat_group_ids=re_.chat_groups,
                    )
                results.append(_M365RuleResult(
                    label=f"{tenant_id}:{re_.backup_server_ref}",
                    kind="m365_user_rule", action="overwrite", result="ok", error_msg="",
                ))
            except APMError as e:
                results.append(_M365RuleResult(
                    label=f"{tenant_id}:{re_.backup_server_ref}",
                    kind="m365_user_rule", action="overwrite", result="failed", error_msg=str(e),
                ))
        else:
            try:
                async with sem:
                    await apm.m365.auto_backup_rules.create(
                        tenant_id=tenant_id,
                        namespace=re_.resolved_namespace,
                        plan_id=re_.resolved_plan_id,
                        exchange_group_ids=re_.exchange_groups,
                        onedrive_group_ids=re_.onedrive_groups,
                        chat_group_ids=re_.chat_groups,
                    )
                results.append(_M365RuleResult(
                    label=f"{tenant_id}:{re_.backup_server_ref}",
                    kind="m365_user_rule", action="create", result="ok", error_msg="",
                ))
            except APMError as e:
                results.append(_M365RuleResult(
                    label=f"{tenant_id}:{re_.backup_server_ref}",
                    kind="m365_user_rule", action="create", result="failed", error_msg=str(e),
                ))

    for ce in collab_entries:
        if interrupted.is_set():
            results.append(_M365RuleResult(
                label=tenant_id, kind="m365_collab_services", action="skip", result="skipped", error_msg="",
            ))
            continue
        if ce.parse_error:
            results.append(_M365RuleResult(
                label=tenant_id, kind="m365_collab_services", action="error",
                result="failed", error_msg=ce.parse_error,
            ))
            continue
        if on_conflict == "skip":
            _collab_active = any(
                s.enabled for s in [
                    current.group_exchange, current.mysite, current.sharepoint, current.teams,
                ]
            )
            if _collab_active:
                results.append(_M365RuleResult(
                    label=tenant_id, kind="m365_collab_services", action="skip", result="skipped", error_msg="",
                ))
                continue
            # No existing collab config — fall through to apply even with on_conflict=skip.
        try:
            async with sem:
                await apm.m365.auto_backup_rules.update_collab_settings(
                    tenant_id=tenant_id,
                    group_exchange=ce.group_exchange,
                    mysite=ce.mysite,
                    sharepoint=ce.sharepoint,
                    teams=ce.teams,
                )
            results.append(_M365RuleResult(
                label=tenant_id, kind="m365_collab_services", action="overwrite", result="ok", error_msg="",
            ))
        except APMError as e:
            results.append(_M365RuleResult(
                label=tenant_id, kind="m365_collab_services", action="overwrite", result="failed", error_msg=str(e),
            ))

    return results


# ·· Main orchestrator


def _fs_key(host_ip: str, namespace: str, plan_name: str) -> str:
    return f"{host_ip}:{namespace}:{plan_name}"


def _m365_rule_key(e: _M365RuleEntry) -> tuple[str, str, str]:
    """Dedup/match key for an M365 user rule (YAML-identity refs, not resolved IDs)."""
    return (e.tenant_id, e.backup_server_ref, e.plan_ref)


def _m365_collab_key(e: _M365CollabEntry) -> str:
    """Dedup/match key for an M365 collab-services block (one per tenant)."""
    return e.tenant_id


def _autodetect_and_load_credentials(
    input_path: str,
    fs_credentials_path: str | None,
    storage_credentials_path: str | None,
) -> tuple[
    dict[tuple[str, str], str] | None,
    dict[tuple[str, str, str], dict[str, str]] | None,
    str | None,
] | None:
    """Resolve and load the FS + remote-storage credential CSVs.

    Auto-detects <stem>.fs-credentials.csv / <stem>.storage-credentials.csv next to the input
    YAML when not given explicitly. Returns (fs_creds, rs_creds, storage_credentials_path) — the
    resolved storage path is needed later to write back issued encryption keys — or None if a
    credential file failed to load (error already printed to stderr).
    """
    stem = os.path.splitext(input_path)[0]
    if fs_credentials_path is None:
        candidate = f"{stem}.fs-credentials.csv"
        if os.path.exists(candidate):
            print(f"Using auto-detected FS credentials: {candidate}", file=sys.stderr)
            fs_credentials_path = candidate
    if storage_credentials_path is None:
        candidate = f"{stem}.storage-credentials.csv"
        if os.path.exists(candidate):
            print(f"Using auto-detected storage credentials: {candidate}", file=sys.stderr)
            storage_credentials_path = candidate

    fs_creds: dict[tuple[str, str], str] | None = None
    if fs_credentials_path is not None:
        try:
            fs_creds = _load_fs_credentials(fs_credentials_path)
        except (OSError, ValueError) as e:
            print(f"Error loading fs-credentials file {fs_credentials_path!r}: {e}", file=sys.stderr)
            return None

    rs_creds: dict[tuple[str, str, str], dict[str, str]] | None = None
    if storage_credentials_path is not None:
        try:
            rs_creds = _load_rs_credentials(storage_credentials_path)
        except (OSError, ValueError) as e:
            print(f"Error loading storage-credentials file {storage_credentials_path!r}: {e}", file=sys.stderr)
            return None

    return fs_creds, rs_creds, storage_credentials_path


async def _fetch_import_index(
    apm: APMClient,
) -> tuple[
    list[BackupServer], list[RemoteStorage],
    list[ProtectionPlan], list[ProtectionPlan], list[MachineWorkload],
]:
    """Fetch all read-only index data needed for an import, in parallel.

    Returns (backup_servers, remote_storages, machine_plan_stubs, m365_plan_stubs, fs_workloads).
    """
    print("Fetching index...", file=sys.stderr)
    (bs_list, _), (rs_list, _), (machine_plan_stubs, _), (m365_plan_stubs, _), (fs_wls, _) = (
        await asyncio.gather(
            paginate(lambda limit, offset: apm.backup_servers.list(limit=limit, offset=offset)),
            apm.remote_storages.list(),
            paginate(lambda limit, offset: apm.machine.plans.list(limit=limit, offset=offset)),
            paginate(lambda limit, offset: apm.m365.plans.list(limit=limit, offset=offset)),
            paginate(lambda limit, offset: apm.machine.workloads.list(
                workload_types=[MachineWorkloadType.FS], limit=limit, offset=offset
            )),
        )
    )
    return bs_list, rs_list, machine_plan_stubs, m365_plan_stubs, fs_wls


def _build_plan_name_by_ref(
    data: dict[str, Any], all_plan_stubs: list[ProtectionPlan]
) -> dict[str, str]:
    """Map each protection_plans ref_key to the plan's display name.

    A name_or_id given as a UUID is resolved to the matching stub's name; a UUID with no match
    is omitted (so a referencing file server later gets a parse error). A name value is used as-is.
    """
    plans_by_uuid = {p.plan_id: p for p in all_plan_stubs}
    plan_name_by_ref: dict[str, str] = {}
    for r in (data.get("protection_plans") or []):
        rk = str(r.get("ref_key", ""))
        noi = str(r.get("name_or_id", ""))
        if not rk or not noi:
            continue
        if _is_uuid(noi):
            stub = plans_by_uuid.get(noi)
            if stub is not None:
                plan_name_by_ref[rk] = stub.name
        else:
            plan_name_by_ref[rk] = noi
    return plan_name_by_ref


def _report_parse_errors(
    plan_errors: list[_ImportEntry],
    fs_errors: list[_FsEntry],
    rs_errors: list[_RsEntry],
    m365_rule_errors: list[_M365RuleEntry],
    m365_collab_errors: list[_M365CollabEntry],
) -> None:
    """Print all per-section parse errors to stderr, separated by blank lines."""
    if plan_errors or fs_errors or rs_errors or m365_rule_errors or m365_collab_errors:
        print(file=sys.stderr)
    if plan_errors:
        n = len(plan_errors)
        print(f"{n} plan parse error{'s' if n != 1 else ''}:", file=sys.stderr)
        for pe in plan_errors:
            print(f"  [{pe.kind}] {pe.name!r}: {pe.parse_error}", file=sys.stderr)
    if fs_errors:
        if plan_errors:
            print(file=sys.stderr)
        n = len(fs_errors)
        print(f"{n} file server parse error{'s' if n != 1 else ''}:", file=sys.stderr)
        for fpe in fs_errors:
            print(f"  [file_server] {fpe.host_ip!r} ({fpe.backup_server_ref}): {fpe.parse_error}", file=sys.stderr)
    if rs_errors:
        if plan_errors or fs_errors:
            print(file=sys.stderr)
        n = len(rs_errors)
        print(f"{n} remote storage parse error{'s' if n != 1 else ''}:", file=sys.stderr)
        for rpe in rs_errors:
            print(
                f"  [remote_storage] {rpe.name_or_id!r} (vault={rpe.vault_name!r}): {rpe.parse_error}",
                file=sys.stderr,
            )
    if m365_rule_errors or m365_collab_errors:
        if plan_errors or fs_errors or rs_errors:
            print(file=sys.stderr)
        for mre in m365_rule_errors:
            print(
                f"  [m365_user_rule] {mre.tenant_id!r} ({mre.backup_server_ref}): {mre.parse_error}",
                file=sys.stderr,
            )
        for mce in m365_collab_errors:
            print(f"  [m365_collab] {mce.tenant_id!r}: {mce.parse_error}", file=sys.stderr)


def _compute_m365_dry_actions(
    m365_rule_entries: list[_M365RuleEntry],
    m365_collab_entries: list[_M365CollabEntry],
    m365_existing_by_tenant: dict[str, M365AutoBackupRuleListResult],
    on_conflict: str,
) -> list[tuple[str, str, str]]:
    """Resolve M365 rule/collab dry-run actions against current APM state (for accurate counts)."""
    dry_actions: list[tuple[str, str, str]] = []
    for mre in m365_rule_entries:
        label = f"{mre.tenant_id}:{mre.backup_server_ref}"
        if mre.parse_error:
            dry_actions.append((label, "m365_user_rule", "error"))
        elif mre.tenant_id not in m365_existing_by_tenant:
            dry_actions.append((label, "m365_user_rule", "unknown"))
        else:
            rule_exists = any(
                r.namespace == mre.resolved_namespace and r.plan_id == mre.resolved_plan_id
                for r in m365_existing_by_tenant[mre.tenant_id].rules
            )
            action = ("overwrite" if on_conflict == "overwrite" else "skip") if rule_exists else "create"
            dry_actions.append((label, "m365_user_rule", action))
    for mce in m365_collab_entries:
        if mce.parse_error:
            dry_actions.append((mce.tenant_id, "m365_collab_services", "error"))
        elif mce.tenant_id not in m365_existing_by_tenant:
            dry_actions.append((mce.tenant_id, "m365_collab_services", "unknown"))
        else:
            current = m365_existing_by_tenant[mce.tenant_id]
            collab_active = any(
                s.enabled for s in [
                    current.group_exchange, current.mysite, current.sharepoint, current.teams,
                ]
            )
            # else branch (collab_active is False): no existing collab config, so settings
            # are applied even under on_conflict=skip.
            action = ("overwrite" if on_conflict == "overwrite" else "skip") if collab_active else "overwrite"
            dry_actions.append((mce.tenant_id, "m365_collab_services", action))
    return dry_actions


def _print_dry_run_plan(
    entries: list[_ImportEntry],
    fs_entries: list[_FsEntry],
    rs_entries: list[_RsEntry],
    plan_actions: dict[str, tuple[str, str | None]],
    fs_actions: dict[str, str],
    rs_actions: dict[str, str],
    m365_rule_dry_actions: list[tuple[str, str, str]],
) -> tuple[int, int, int]:
    """Print the planned-actions table and summary line. Returns (n_create, n_overwrite, n_error)."""
    def _count(a_val: str) -> int:
        return (
            sum(1 for a, _ in plan_actions.values() if a == a_val)
            + sum(1 for a in fs_actions.values() if a == a_val)
            + sum(1 for a in rs_actions.values() if a == a_val)
            + sum(1 for _, _, a in m365_rule_dry_actions if a == a_val)
        )
    n_create    = _count("create")
    n_overwrite = _count("overwrite")
    n_skip      = _count("skip")
    n_error     = _count("error") + _count("unknown")

    dry_rows: list[tuple[str, str, str]] = [
        (e.name, e.subtype or e.kind, plan_actions[f"{e.kind}:{e.name}"][0])
        for e in entries
    ] + [
        (fse.host_ip, "file_server", fs_actions[_fs_key(fse.host_ip, fse.resolved_namespace, fse.plan_name)])
        for fse in fs_entries
    ] + [
        (rse.name_or_id, "remote_storage", rs_actions[_rs_key(rse)])
        for rse in rs_entries
    ] + m365_rule_dry_actions
    print(file=sys.stderr)
    sys.stderr.flush()
    _print_table(dry_rows, ("Name", "Type", "Action"))
    n_err_s = "error" if n_error == 1 else "errors"
    print(
        f"\n{n_create} to create, {n_overwrite} to overwrite, "
        f"{n_skip} to skip, {n_error} {n_err_s}.",
        file=sys.stderr,
    )
    return n_create, n_overwrite, n_error


async def run_import(
    input_path: str,
    on_conflict: str,
    dry_run: bool,
    yes: bool,
    concurrency: int = 5,
    fs_credentials_path: str | None = None,
    storage_credentials_path: str | None = None,
    import_types: set[str] | None = None,
    profile: str | None = None,
) -> int | None:
    _types = import_types if import_types is not None else {
        "remote-storage", "protection-plan", "retirement-plan", "tiering-plan",
        "file-server", "m365-auto-backup-rule",
    }

    # Phase 1: load YAML, auto-detect credentials, parse RS entries early.
    try:
        data = _load_yaml(input_path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"Error loading {input_path}: {e}", file=sys.stderr)
        return 1

    # Auto-detect and load credential files (storage path is retained for key write-back).
    creds = _autodetect_and_load_credentials(input_path, fs_credentials_path, storage_credentials_path)
    if creds is None:
        return 1
    fs_creds, rs_creds, storage_credentials_path = creds

    yaml_bs: list[dict[str, Any]] = data.get("backup_servers") or []
    yaml_rs: list[dict[str, Any]] = data.get("remote_storages") or []
    yaml_saas: list[dict[str, Any]] = data.get("saas_tenants") or []
    saas_tenants_by_ref, saas_ref_errors = _build_saas_tenant_ref_map(yaml_saas)
    for err in saas_ref_errors:
        print(f"  Warning: saas_tenants: {err}", file=sys.stderr)

    # Parse RS entries early (before ref map build) to know which ref_keys will be
    # created/updated via --storage-credentials; those entries must not abort the import
    # if they are not yet present on the target APM.
    rs_entries: list[_RsEntry] = (
        _parse_rs_entries(data, rs_creds)
        if rs_creds is not None and "remote-storage" in _types
        else []
    )

    async with make_client(profile=profile) as apm:
        # Phase 2: parallel fetch of all read-only index data.
        bs_list, rs_list, machine_plan_stubs, m365_plan_stubs, fs_wls = await _fetch_import_index(apm)

        backup_servers_by_ref, bs_errors = _build_ref_map(
            "backup server", yaml_bs, bs_list,
            id_of=lambda bs: bs.backup_server_id, name_of=lambda bs: bs.name,
        )
        remote_storages_by_ref, rs_errors_raw = _build_ref_map(
            "remote storage", yaml_rs, rs_list,
            id_of=lambda rs: rs.storage_id, name_of=lambda rs: rs.name,
        )
        # Backup server ref errors are checked early; RS ref errors are checked after RS
        # action-selection (below) so that the suppression set reflects final parse state.
        if bs_errors and _types & {"file-server", "protection-plan", "m365-auto-backup-rule"}:
            print(f"\n{len(bs_errors)} reference resolution error(s):", file=sys.stderr)
            for err in bs_errors:
                print(f"  {err}", file=sys.stderr)
            print("Aborting import due to unresolved references.", file=sys.stderr)
            return 1

        all_plan_stubs: list[ProtectionPlan] = machine_plan_stubs + m365_plan_stubs
        plans_by_name: dict[str, str] = {p.name: p.plan_id for p in machine_plan_stubs}
        m365_plans_by_name: dict[str, str] = {p.name: p.plan_id for p in m365_plan_stubs}

        plan_name_by_ref = _build_plan_name_by_ref(data, all_plan_stubs)

        existing_fs: dict[str, MachineWorkload] = {}
        for wl in fs_wls:
            if wl.fs_config and not wl.is_retired:
                existing_fs[_fs_key(wl.fs_config.host_ip, wl.namespace, wl.plan.name)] = wl

        existing_rs: dict[str, RemoteStorage] = {rs.storage_id: rs for rs in rs_list}
        existing_rs_by_name: dict[str, RemoteStorage] = {}
        for rs in rs_list:
            existing_rs_by_name.setdefault(rs.name.lower(), rs)

        # Phase 3: parse all entries.
        rs_pending_refs: set[str] = {rse.ref_key for rse in rs_entries if not rse.parse_error}
        entries = _parse_all_entries(
            data, backup_servers_by_ref, remote_storages_by_ref, rs_pending_refs
        )
        entries = [e for e in entries if e.kind in _types]
        fs_entries = (
            _parse_fs_entries(data, plans_by_name, plan_name_by_ref, backup_servers_by_ref)
            if "file-server" in _types
            else []
        )
        m365_rule_entries: list[_M365RuleEntry] = []
        m365_collab_entries: list[_M365CollabEntry] = []
        if "m365-auto-backup-rule" in _types:
            m365_rule_entries, m365_collab_entries = _parse_m365_rule_entries(
                data, backup_servers_by_ref, m365_plans_by_name, plan_name_by_ref,
                saas_tenants_by_ref,
            )

        # Deduplicate each entry list, keeping the first occurrence. All keys use YAML-identity
        # refs (e.g. backup_server_ref, not resolved_namespace): unresolved entries share
        # resolved_namespace="" and would otherwise incorrectly collapse into one "duplicate".
        fs_entries = _dedupe_by_key(
            fs_entries,
            lambda fse: f"{fse.host_ip}:{fse.backup_server_ref}:{fse.plan_name}",
            lambda fse: (
                f"\nWARNING: duplicate file_server entry {fse.host_ip!r} "
                f"({fse.backup_server_ref}, plan_ref → {fse.plan_name!r}) — extra copy skipped."
            ),
        )
        m365_rule_entries = _dedupe_by_key(
            m365_rule_entries,
            _m365_rule_key,
            lambda mre: (
                f"\nWARNING: duplicate m365_user_rule entry for tenant {mre.tenant_id!r} "
                f"(backup_server_ref={mre.backup_server_ref!r}, plan_ref={mre.plan_ref!r})"
                " — extra copy skipped."
            ),
        )
        m365_collab_entries = _dedupe_by_key(
            m365_collab_entries,
            _m365_collab_key,
            lambda mce: (
                f"\nWARNING: duplicate m365 collab_services entry for tenant {mce.tenant_id!r}"
                " — extra copy skipped."
            ),
        )
        rs_entries = _dedupe_by_key(
            rs_entries,
            _rs_key,
            lambda rse: (
                f"\nWARNING: duplicate remote_storage entry {rse.name_or_id!r} "
                f"(endpoint={rse.endpoint!r}, vault={rse.vault_name!r}) — extra copy skipped."
            ),
        )
        # Key sets reused by the Phase 7 re-dedup (see _filter_to_keys below).
        _seen_m365_rule_keys = {_m365_rule_key(e) for e in m365_rule_entries}
        _seen_m365_collab_keys = {_m365_collab_key(e) for e in m365_collab_entries}

        rs_actions = _select_rs_actions(
            rs_entries, rs_creds, on_conflict, existing_rs, existing_rs_by_name
        )

        # RS ref errors are checked here, after action-selection, so the suppression set
        # only covers entries that successfully built a request (not ones that errored out).
        rs_handled_refs: set[str] = {
            rse.ref_key for rse in rs_entries
            if not rse.parse_error and rs_actions.get(_rs_key(rse)) not in ("error", None)
        }
        rs_errors = [
            e for e in rs_errors_raw
            if not any(f"ref_key={rk!r}" in e for rk in rs_handled_refs)
        ]
        if rs_errors:
            print(f"\n{len(rs_errors)} unresolved remote storage reference(s):", file=sys.stderr)
            for err in rs_errors:
                print(f"  {err}", file=sys.stderr)
            if "remote-storage" in _types:
                print("Aborting import due to unresolved references.", file=sys.stderr)
                return 1

        has_work = bool(entries or fs_entries or rs_entries or m365_rule_entries or m365_collab_entries)
        if not has_work:
            print("No matching entries found in YAML file for the selected type(s).", file=sys.stderr)
            return 0

        # Phase 3c: determine actions (conflict check + action selection, no request building yet).
        print("Checking for existing plans...", file=sys.stderr)
        existing = await _check_conflicts(apm, entries, all_plan_stubs)

        plan_actions: dict[str, tuple[str, str | None]] = {}
        for entry in entries:
            key = f"{entry.kind}:{entry.name}"
            existing_id = existing.get(key)
            plan_actions[key] = (_determine_action(entry, existing_id, on_conflict), existing_id)

        # FS action selection: determine create/overwrite/skip/error — NO request building yet.
        fs_actions: dict[str, str] = {}
        for fse in fs_entries:
            fs_key = _fs_key(fse.host_ip, fse.resolved_namespace, fse.plan_name)
            if fse.parse_error:
                fs_actions[fs_key] = "error"
            elif fs_key in existing_fs:
                fs_actions[fs_key] = "overwrite" if on_conflict == "overwrite" else "skip"
            else:
                fs_actions[fs_key] = "create"

        # Report all parse errors together once all fetching and action selection is done.
        _report_parse_errors(
            [e for e in entries if e.parse_error],
            [e for e in fs_entries if e.parse_error],
            [e for e in rs_entries if e.parse_error],
            [e for e in m365_rule_entries if e.parse_error],
            [e for e in m365_collab_entries if e.parse_error],
        )

        # Pre-fetch existing M365 rules so dry-run can show real create/overwrite/skip actions.
        m365_existing_by_tenant: dict[str, M365AutoBackupRuleListResult] = {}
        if m365_rule_entries or m365_collab_entries:
            print("Checking existing M365 auto-backup rules...", file=sys.stderr)
            _m365_tenant_ids = list(dict.fromkeys(
                [e.tenant_id for e in m365_rule_entries]
                + [ce.tenant_id for ce in m365_collab_entries]
            ))
            _m365_check_sem = asyncio.Semaphore(concurrency)

            async def _fetch_m365_current(tid: str) -> tuple[str, M365AutoBackupRuleListResult] | None:
                try:
                    async with _m365_check_sem:
                        return (tid, await apm.m365.auto_backup_rules.list(tid))
                except APMError as e:
                    print(
                        f"  Warning: could not fetch existing rules for tenant {tid!r}: {e}",
                        file=sys.stderr,
                    )
                    return None

            for _r in await asyncio.gather(*[_fetch_m365_current(tid) for tid in _m365_tenant_ids]):
                if _r is not None:
                    m365_existing_by_tenant[_r[0]] = _r[1]

        # M365 rule dry-run actions — resolved against current APM state for accurate counts.
        m365_rule_dry_actions = _compute_m365_dry_actions(
            m365_rule_entries, m365_collab_entries, m365_existing_by_tenant, on_conflict,
        )

        n_create, n_overwrite, n_error = _print_dry_run_plan(
            entries, fs_entries, rs_entries,
            plan_actions, fs_actions, rs_actions, m365_rule_dry_actions,
        )

        if dry_run:
            return 0

        if not (n_create + n_overwrite):
            print("Nothing to do.", file=sys.stderr)
            return 1 if n_error else 0

        ms = apm.my_server
        print(f"\nTarget: {ms.name} ({ms.hostname}) — {ms.system_version}", file=sys.stderr)
        if not yes:
            confirmed = await prompt_yes_no("Proceed? [y/N] ")
            if not confirmed:
                print("Aborted.", file=sys.stderr)
                return 0

        # Phase 4: execute RS first so newly created storages are available for plan
        # ref resolution before plan creation begins.
        loop = asyncio.get_running_loop()
        interrupted = asyncio.Event()
        register_interrupt(loop, interrupted)
        m365_all_results: list[_M365RuleResult] = []
        try:
            rs_sem = asyncio.Semaphore(concurrency)

            async def _run_rs(rse: _RsEntry) -> _RsResult:
                async with rs_sem:
                    action = rs_actions[_rs_key(rse)]
                    if interrupted.is_set():
                        return _RsResult(entry=rse, action=action, result="skipped", error_msg="")
                    if _is_uuid(rse.name_or_id):
                        existing_storage = existing_rs.get(rse.name_or_id)
                    else:
                        existing_storage = existing_rs_by_name.get(rse.name_or_id.lower())
                    return await _execute_one_rs(apm, rse, action, existing_storage)

            rs_results: list[_RsResult] = list(
                await asyncio.gather(*[_run_rs(rse) for rse in rs_entries])
            )
            for rsr in rs_results:
                status = _status_line(rsr.result, rsr.error_msg, ok_warning=True)
                print(f"  [{rsr.action}] RS {rsr.entry.name_or_id!r}... {status}", file=sys.stderr)

            # Write back newly issued encryption keys to the credentials CSV.
            if storage_credentials_path is not None and rs_creds is not None:
                keys_issued = [r for r in rs_results if r.issued_encryption_key is not None]
                if keys_issued:
                    backup_suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                    for rsr in keys_issued:
                        cred_key = (rsr.entry.storage_type_str, rsr.entry.endpoint, rsr.entry.vault_name)
                        if cred_key in rs_creds:
                            rs_creds[cred_key]["relink_encryption_key"] = rsr.issued_encryption_key or ""
                            print(
                                f"  Encryption key for vault {rsr.entry.vault_name!r} saved to credentials file.",
                                file=sys.stderr,
                            )
                    _write_rs_credentials(storage_credentials_path, rs_creds, backup_suffix)
                    print(
                        f"  Credentials file updated (old copy: {storage_credentials_path}.{backup_suffix}.bak).",
                        file=sys.stderr,
                    )

            # After RS creation, populate the ref map so that plans referencing newly created
            # storages resolve correctly.
            created_rs = [rse for rse in rs_entries if rs_actions.get(_rs_key(rse)) == "create"]
            if created_rs:
                # Fast path: populate directly from add() results — no extra round-trip.
                for rsr in rs_results:
                    if (
                        rsr.result == "ok"
                        and rsr.created_storage is not None
                        and rsr.entry.ref_key not in remote_storages_by_ref
                    ):
                        remote_storages_by_ref[rsr.entry.ref_key] = rsr.created_storage

                # Fallback: if any create entry is still unresolved (e.g. add() failed with a
                # conflict because another client registered the storage concurrently), fetch
                # the full storage list to locate it by name.
                unresolved = [rse for rse in created_rs if rse.ref_key not in remote_storages_by_ref]
                if unresolved:
                    rs_list_refreshed, _ = await apm.remote_storages.list()
                    rs_by_name = {rs.name.lower(): rs for rs in rs_list_refreshed}
                    rs_by_id = {rs.storage_id: rs for rs in rs_list_refreshed}
                    for rse in unresolved:
                        rs_obj = (
                            rs_by_id.get(rse.name_or_id)
                            or rs_by_name.get(rse.vault_name.lower())
                            or rs_by_name.get(rse.name_or_id.lower())
                        )
                        if rs_obj:
                            remote_storages_by_ref[rse.ref_key] = rs_obj

            # Phase 5a: build plan requests now that the RS ref map is complete.
            _build_plan_requests(entries, backup_servers_by_ref, remote_storages_by_ref)
            for entry in entries:
                key = f"{entry.kind}:{entry.name}"
                if entry.parse_error is not None and plan_actions[key][0] not in ("error", "skip"):
                    plan_actions[key] = ("error", plan_actions[key][1])

            # Phase 5b: build FS requests now that we know we're proceeding past dry-run.
            _build_fs_requests(fs_entries, fs_creds, fs_actions, plans_by_name)

            # Phase 6: execute plans and FS concurrently.
            plan_sem = asyncio.Semaphore(concurrency)
            fs_sem = asyncio.Semaphore(concurrency)

            async def _run_plan(entry: _ImportEntry) -> _ImportResult:
                async with plan_sem:
                    key = f"{entry.kind}:{entry.name}"
                    action, existing_id = plan_actions[key]
                    if interrupted.is_set():
                        return _ImportResult(entry=entry, action=action, result="skipped", error_msg="")
                    return await _execute_one(apm, entry, action, existing_id)

            async def _run_fs(fse: _FsEntry) -> _FsResult:
                async with fs_sem:
                    key = _fs_key(fse.host_ip, fse.resolved_namespace, fse.plan_name)
                    action = fs_actions[key]
                    if interrupted.is_set():
                        return _FsResult(entry=fse, action=action, result="skipped", error_msg="")
                    return await _execute_one_fs(apm, fse, action, existing_fs.get(key))

            (plan_raw, fs_raw) = await asyncio.gather(
                asyncio.gather(*[_run_plan(e) for e in entries]),
                asyncio.gather(*[_run_fs(fse) for fse in fs_entries]),
            )
            results: list[_ImportResult] = list(plan_raw)
            fs_results: list[_FsResult] = list(fs_raw)

            for ir in results:
                status = _status_line(ir.result, ir.error_msg)
                print(f"  [{ir.action}] {ir.entry.name!r}... {status}", file=sys.stderr)
            for fsr in fs_results:
                status = _status_line(fsr.result, fsr.error_msg)
                print(f"  [{fsr.action}] FS {fsr.entry.host_ip!r}... {status}", file=sys.stderr)

            # Phase 7: M365 auto-backup rules — after plans exist.
            if m365_rule_entries or m365_collab_entries:
                # Re-fetch M365 plan stubs to include plans created in Phase 6.
                m365_stubs_fresh, _ = await paginate(
                    lambda limit, offset: apm.m365.plans.list(limit=limit, offset=offset)
                )
                fresh_plans_by_name: dict[str, str] = {p.name: p.plan_id for p in m365_stubs_fresh}
                # Re-parse with fresh plan IDs so newly created plans resolve correctly.
                m365_rule_entries_fresh, m365_collab_entries_fresh = _parse_m365_rule_entries(
                    data, backup_servers_by_ref, fresh_plans_by_name, plan_name_by_ref,
                    saas_tenants_by_ref,
                )
                # Filter the re-parsed lists back down to the exact keys Phase 3 kept, so the
                # fresh list matches what the dry-run was computed from (consume each key once
                # to prevent duplicate YAML entries from both passing).
                m365_rule_entries_fresh = _filter_to_keys(
                    m365_rule_entries_fresh, _m365_rule_key, _seen_m365_rule_keys,
                )
                m365_collab_entries_fresh = _filter_to_keys(
                    m365_collab_entries_fresh, _m365_collab_key, _seen_m365_collab_keys,
                )
                # Group by tenant_id for concurrent execution.
                tenant_ids: list[str] = list(dict.fromkeys(
                    [e.tenant_id for e in m365_rule_entries_fresh]
                    + [ce.tenant_id for ce in m365_collab_entries_fresh]
                ))
                m365_sem = asyncio.Semaphore(concurrency)

                async def _run_tenant_m365(tid: str) -> list[_M365RuleResult]:
                    tenant_rules = [e for e in m365_rule_entries_fresh if e.tenant_id == tid]
                    tenant_collabs = [e for e in m365_collab_entries_fresh if e.tenant_id == tid]
                    return await _execute_m365_rules(
                        apm, tid, tenant_rules, tenant_collabs, on_conflict, m365_sem, interrupted,
                    )

                tenant_result_lists: list[list[_M365RuleResult]] = list(
                    await asyncio.gather(*[_run_tenant_m365(tid) for tid in tenant_ids])
                )
                for sublist in tenant_result_lists:
                    m365_all_results.extend(sublist)

                for mr in m365_all_results:
                    status_str = _status_line(mr.result, mr.error_msg)
                    print(f"  [{mr.action}] M365 {mr.kind} {mr.label!r}... {status_str}", file=sys.stderr)
        finally:
            unregister_interrupt(loop)

    final_rows = _build_final_rows(results, fs_results, rs_results, m365_all_results)
    print()
    _print_table(final_rows, ("Name", "Type", "Action", "Result"))
    n_ok, n_failed = _summarize_results(results, fs_results, rs_results, m365_all_results)
    print(f"\n  {n_ok} succeeded, {n_failed} failed.", file=sys.stderr)
    return 1 if n_failed else 0


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("export", help="Export plans to a YAML file")
    ex.add_argument("output", metavar="FILE", help="Output YAML file path")
    ex.add_argument(
        "--concurrency", type=int, default=5, metavar="N",
        help="Max concurrent detail-fetch requests per collection (default: 5)",
    )
    ex.add_argument(
        "--no-credentials-template", action="store_false", dest="write_credentials_template",
        help=(
            "Skip writing credential template CSV files alongside the output YAML. "
            "By default, export writes <stem>.fs-credentials.csv and "
            "<stem>.storage-credentials.csv with endpoint/vault fields pre-populated "
            "and credential columns blank; fill them in before running import."
        ),
    )
    ex.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt when output files already exist",
    )
    add_profile_arg(ex)

    im = sub.add_parser("import", help="Import plans from a YAML file")
    im.add_argument("input", metavar="FILE", help="Input YAML file path")
    im.add_argument(
        "--type", dest="import_type", default="all",
        choices=[
            "all", "remote-storage", "protection-plan", "retirement-plan",
            "tiering-plan", "file-server", "m365-auto-backup-rule",
        ],
        help="Which type(s) to import from the YAML file (default: all)",
    )
    im.add_argument(
        "--on-conflict", dest="on_conflict", default="skip",
        choices=["skip", "overwrite"],
        help="Action when a plan with the same name already exists (default: skip)",
    )
    im.add_argument(
        "--dry-run", action="store_true",
        help="Show planned actions without executing",
    )
    im.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt",
    )
    im.add_argument(
        "--concurrency", type=int, default=5, metavar="N",
        help="Max concurrent File Server create/update requests (default: 5)",
    )
    im.add_argument(
        "--fs-credentials", "-c", metavar="FILE",
        help=(
            "CSV file (columns: endpoint, login_user, password) with credentials for File Server "
            "workloads. endpoint = host_ip. Defaults to <stem>.fs-credentials.csv alongside the "
            "input YAML if that file exists; use this flag to override. Required when creating new "
            "workloads; optional for updates (omit to keep the existing stored credentials)."
        ),
    )
    im.add_argument(
        "--storage-credentials", metavar="FILE",
        help=(
            "CSV file (columns: storage_type, endpoint, vault_name, access_key, secret_key, "
            "relink_encryption_key) with credentials for remote storage entries. "
            "Defaults to <stem>.storage-credentials.csv alongside the input YAML if that file exists; "
            "use this flag to override. "
            "endpoint is empty for storage types with no configurable endpoint "
            "(e.g. Amazon S3, Wasabi, C2 Object Storage). "
            "Required when creating or updating remote storages; omit to skip storage import. "
            "relink_encryption_key is updated in-place after a successful create for encrypted vaults "
            "(the old CSV is renamed to <file>.<timestamp>.bak before the update)."
        ),
    )
    add_profile_arg(im)

    args = parser.parse_args()

    if args.command == "export":
        run_main(run_export(
            args.output, args.concurrency, args.write_credentials_template, args.yes,
            profile=args.profile,
        ))
    else:
        import_types = (
            {
                "remote-storage", "protection-plan", "retirement-plan",
                "tiering-plan", "file-server", "m365-auto-backup-rule",
            }
            if args.import_type == "all"
            else {args.import_type}
        )
        run_main(run_import(
            args.input, args.on_conflict, args.dry_run, args.yes, args.concurrency,
            args.fs_credentials,
            args.storage_credentials,
            import_types,
            profile=args.profile,
        ))


if __name__ == "__main__":
    main()
