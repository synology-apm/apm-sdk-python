# APM SDK — Developer Guide

Python SDK for [Synology ActiveProtect Manager (APM)](https://www.synology.com/products/ActiveProtectAppliance).

Async-native, fully typed Python interface to the APM REST API — no raw HTTP required.

## Installation

Requires Python 3.11 or later.

```bash
pip install synology-apm-sdk
```

## Quick start

```python
import asyncio
from synology_apm.sdk import APMClient

async def main():
    async with APMClient("apm.corp.com", "admin", "password") as apm:
        workloads, _ = await apm.machine.workloads.list()
        for wl in workloads:
            print(f"{wl.name}  last backup: {wl.last_backup_at}")

asyncio.run(main())
```

For self-signed certificates (common in lab environments):

```python
async with APMClient("apm.corp.com", "admin", "password", verify_ssl=False) as apm:
    ...
```

---

## APMClient

`APMClient` is the single entry point. Always use it as an async context manager so the session is properly authenticated and cleaned up:

```python
async with APMClient(host, username, password, verify_ssl=True, timeout=300.0) as apm:
    apm.machine          # MachineCollection  → .workloads / .plans
    apm.m365             # M365Collection     → .workloads / .plans / .auto_backup_rules / .exchange_export / .group_export
    apm.saas             # SaasCollection           — SaaS (M365) tenants
    apm.activities       # ActivityCollection → .backup / .restore
    apm.backup_servers   # BackupServerCollection   — cluster servers, tiering-plan assignment
    apm.remote_storages  # RemoteStorageCollection  — external vaults (S3-compatible / APV / DSM)
    apm.hypervisors      # HypervisorCollection     — hypervisor inventory servers
    apm.logs             # LogCollection            — activity / drive / connection / system logs
    apm.plans            # ProtectionPlanCollection — cross-category read + create / delete
    apm.retirement_plans # RetirementPlanCollection — retention plans for retired workloads
    apm.tiering_plans    # TieringPlanCollection    — tier old versions to remote storage
    await apm.get_site_info()  # site UUID, management server, storage stats, workload usage
```

Each collection's methods are shown in its section below; full signatures and every model
field are documented in the docstrings and the
[Sphinx API reference](https://synology-apm.github.io/apm-sdk-python/).

Manual lifecycle (if context manager is not suitable):

```python
apm = APMClient(...)
await apm.connect()
try:
    ...
finally:
    await apm.disconnect()
```

---

## Machine Workloads

Manages device backup workloads: PC, Physical Server, VM, and File Server.

```python
from synology_apm.sdk import MachineWorkloadType, VerifyStatus, WorkloadStatus

# List all machine workloads
workloads, total = await apm.machine.workloads.list()

# Filter by type, retirement status, or name (workload_types is a repeatable list)
vms,     _ = await apm.machine.workloads.list(workload_types=[MachineWorkloadType.VM])
fs,      _ = await apm.machine.workloads.list(workload_types=[MachineWorkloadType.FS])
retired, _ = await apm.machine.workloads.list(is_retired=True)
results, _ = await apm.machine.workloads.list(name_contains="prod")

# Filter by backup status or verification status (both repeatable; verify_status is PS/VM only)
failed,       _ = await apm.machine.workloads.list(status=[WorkloadStatus.FAILED, WorkloadStatus.PARTIAL])
not_verified, _ = await apm.machine.workloads.list(verify_status=[VerifyStatus.NOT_ENABLED])

# Get a single workload by ID (namespace comes from list() results)
wl = await apm.machine.workloads.get("123e4567-e89b-12d3-a456-426614174000", namespace="123e4567-e89b-12d3-a456-426614174001")

# Type-specific fields
from synology_apm.sdk import MachineWorkload
if isinstance(wl, MachineWorkload):
    print(wl.workload_type, wl.agent_version, wl.device_uuid, wl.ip_address)
```

### Trigger a backup

Pass the `Workload` object directly. Returns `None` — use `activities.backup.list()` to track progress.

```python
wl = await apm.machine.workloads.get("123e4567-e89b-12d3-a456-426614174000", namespace="123e4567-e89b-12d3-a456-426614174001")
await apm.machine.workloads.backup_now(wl)
print("Backup triggered — use apm.activities.backup.list() to track progress")
```

### Cancel a running backup

```python
await apm.machine.workloads.cancel_backup(wl)
```

### Backup version history

```python
from datetime import datetime, timezone, timedelta

versions, total = await apm.machine.workloads.list_versions(wl)

# Filter by time range
since = datetime.now(timezone.utc) - timedelta(days=7)
recent, _ = await apm.machine.workloads.list_versions(wl, since=since, limit=10)

for v in recent:
    print(f"{v.created_at}  changed={v.changed_size_bytes}  locked={v.locked}")
```

### Retire a workload

```python
# Irreversible — resolve the retirement plan first
plan = await apm.retirement_plans.get_by_name("Compliance Retention")
await apm.machine.workloads.retire(wl, plan)
```

### Delete a workload

```python
await apm.machine.workloads.delete(wl)
```

### Register and update a File Server

```python
from synology_apm.sdk import (
    FileServerAddRequest, FileServerUpdateRequest,
    FileServerType, FileServerPathSelector,
    DuplicateWorkloadError,
)

server = await apm.backup_servers.get_by_name("apm-server-01")
plan   = await apm.machine.plans.get_by_name("Daily Backup")

req = FileServerAddRequest(
    namespace=server.namespace,
    host_ip="192.0.2.50",
    server_type=FileServerType.SMB,
    plan_id=plan.plan_id,
    login_user="corp\\admin",
    login_password="s3cret",
    selectors=(FileServerPathSelector(path=""),),  # whole root; customise as needed
)
try:
    await apm.machine.workloads.add_file_server(req)
except DuplicateWorkloadError as e:
    print(f"Already registered: {e.resource_id}")

# Update an existing file server workload
fs_wl = await apm.machine.workloads.get_by_name("Corp Share")
upd = FileServerUpdateRequest(
    host_ip="192.0.2.50",
    login_user="corp\\admin",
    login_password="newpass",   # pass None to keep the existing stored password
)
await apm.machine.workloads.update_file_server(fs_wl, upd)
```

### Lock and unlock versions

```python
versions, _ = await apm.machine.workloads.list_versions(wl)
v = versions[0]
await apm.machine.workloads.lock_version(v)    # prevent retention-policy deletion
await apm.machine.workloads.unlock_version(v)  # restore normal retention behaviour
```

### Backup verification video (PS/VM)

```python
from synology_apm.sdk import VerifyStatus

# Only PS/VM workloads produce verification videos, and only for verified versions
if v.verify_status == VerifyStatus.SUCCESS:
    url = await apm.machine.workloads.get_verification_video_url(wl, v)  # time-limited URL
    await apm.download_file(url, "verification.mp4")
```

---

## Protection Plans

`apm.plans` is a cross-category read-only collection; `apm.machine.plans` / `apm.m365.plans` provide domain-specific CRUD.

```python
from synology_apm.sdk import WorkloadCategory

# Cross-category — single API call (machine + M365 combined)
plans, total = await apm.plans.list()
plans, total = await apm.plans.list(category=WorkloadCategory.MACHINE)
plans, total = await apm.plans.list(category=WorkloadCategory.M365, name_contains="Daily")

# Category-agnostic lookup
plan = await apm.plans.get_by_name("Daily Backup")                       # exact name match, case-insensitive
plan = await apm.plans.get("123e4567-e89b-12d3-a456-426614174002")        # direct UUID

# Domain-specific collections
plans, total = await apm.machine.plans.list()
plans, total = await apm.m365.plans.list()
plan  = await apm.machine.plans.get_by_name("Daily Backup")
plan  = await apm.machine.plans.get("123e4567-e89b-12d3-a456-426614174002")

# change_plan() takes the resolved workload and Plan object directly
await apm.machine.workloads.change_plan(wl, plan)
await apm.m365.workloads.change_plan(m365_wl, plan)

# Create a new machine protection plan
from datetime import time
from synology_apm.sdk import ScheduleFrequency, RetentionType
from synology_apm.sdk import ProtectionSchedule, ProtectionRetentionPolicy, MachinePlanCreateRequest

schedule  = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(2, 0))
retention = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=30)
plan = await apm.machine.plans.create(MachinePlanCreateRequest(
    name="Daily Backup",
    schedule=schedule,
    retention=retention,
))
```

Update or delete a plan:

```python
from synology_apm.sdk import PlanNameConflictError, PlanInUseError

# Update — pass the plan_id and a new request object
try:
    plan = await apm.machine.plans.update(plan.plan_id, MachinePlanCreateRequest(
        name="Daily Backup",
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0)),
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=60),
    ))
except PlanNameConflictError as e:
    print(f"Name taken: {e.resource_id}")

# Delete — raises PlanInUseError when workloads are still assigned
try:
    await apm.machine.plans.delete(plan)
except PlanInUseError as e:
    print(f"Still in use: workloads={e.has_workloads}  template={e.has_server_template}")

# Same signatures for M365 plans (use apm.m365.plans.update / .delete and M365PlanCreateRequest)
```

Inspect schedule and retention:

```python
if plan.policy.schedule:
    sch = plan.policy.schedule
    print(f"Frequency: {sch.frequency.value}")          # "manual" / "hourly" / "daily" / "weekly"
    if sch.start_time:                                   # None for manual/after_backup plans
        print(f"Time:    {sch.start_time}")
    if sch.weekdays:                                     # non-empty only for weekly
        print(f"Days:    {[d.name for d in sch.weekdays]}")
r = plan.policy.retention
print(f"Retention: {r.retention_type.value}  days={r.days}  versions={r.versions}")
if plan.backup_copy_policy:
    if plan.backup_copy_policy.schedule:
        print(f"Copy schedule:   {plan.backup_copy_policy.schedule.frequency.value}")
    print(f"Copy retention:  {plan.backup_copy_policy.retention.days} days")
    print(f"Copy destination: {plan.backup_copy_policy.destination.name}")
```

## Retirement Plans

Retirement plans live in `RetirementPlanCollection` (`synology_apm.sdk.collections.retirement_plans`).

```python
from synology_apm.sdk import RetirementPlanCreateRequest

# List all retirement plans
plans, _ = await apm.retirement_plans.list()
plan = await apm.retirement_plans.get_by_name("Compliance Retention")   # name search
plan = await apm.retirement_plans.get("123e4567-e89b-12d3-a456-426614174003")  # direct UUID

print(f"{plan.name}  workloads={plan.workload_count}")
print(f"  days={plan.retention.days}  keep_latest={plan.retention.keep_latest_version}")

# Create
plan = await apm.retirement_plans.create(RetirementPlanCreateRequest(
    name="Compliance Retention",
    retention_days=365,
    keep_latest_version=True,
))

# Update — pass the plan_id and a new request
plan = await apm.retirement_plans.update(plan.plan_id, RetirementPlanCreateRequest(
    name="Compliance Retention",
    retention_days=730,
    keep_latest_version=True,
))

# Delete
await apm.retirement_plans.delete(plan)
```

---

## Tiering Plans

Tiering plans live in `TieringPlanCollection` (`synology_apm.sdk.collections.tiering_plans`).
Destination details are resolved automatically from the remote storage registry.

```python
from datetime import time
from synology_apm.sdk import TieringPlanCreateRequest

# List all tiering plans
plans, _ = await apm.tiering_plans.list()
plan = await apm.tiering_plans.get_by_name("30-Day Tiering")    # name search
plan = await apm.tiering_plans.get("123e4567-e89b-12d3-a456-426614174004")  # direct UUID

print(f"{plan.name}  after={plan.tiering_after_days} days  check={plan.daily_check_time}")
if plan.destination:
    print(f"  destination={plan.destination.name}  endpoint={plan.destination.endpoint}")
print(f"  servers={plan.server_count}")

# Create
storage = await apm.remote_storages.get_by_name("tiering-remote")
plan = await apm.tiering_plans.create(TieringPlanCreateRequest(
    name="30-Day Tiering",
    tiering_after_days=30,
    destination=storage,
    daily_check_time=time(20, 0),
))

# Update — pass the plan_id and a new request
plan = await apm.tiering_plans.update(plan.plan_id, TieringPlanCreateRequest(
    name="30-Day Tiering",
    tiering_after_days=45,
    destination=storage,
))

# Delete
await apm.tiering_plans.delete(plan)
```

---

## M365 Workloads

Manages Microsoft 365 SaaS backup workloads (Mailbox, OneDrive, SharePoint, Teams, etc.).
`tenant_id` is required for all M365 workload operations.

```python
from synology_apm.sdk import M365WorkloadType, WorkloadStatus

# tenant_id comes from apm.saas.list()
tenants, _ = await apm.saas.list()
TENANT = tenants[0].tenant_id
tenant = await apm.saas.get_m365_tenant(TENANT)   # single-tenant detail lookup

# List M365 workloads of a given service sub-type for a tenant
mailboxes, total = await apm.m365.workloads.list(TENANT, workload_type=M365WorkloadType.EXCHANGE)

# Filter by backup status (repeatable; M365 has no verification concept)
failed, _ = await apm.m365.workloads.list(TENANT, workload_type=M365WorkloadType.EXCHANGE, status=[WorkloadStatus.FAILED])

# Get a single workload by name — tenant_id required
wl = await apm.m365.workloads.get_by_name("alice@contoso.com", TENANT, workload_type=M365WorkloadType.EXCHANGE)
print(wl.workload_type, wl.tenant_id, wl.info)

# Retire an M365 workload (irreversible)
plan = await apm.retirement_plans.get_by_name("Compliance Retention")
await apm.m365.workloads.retire(wl, plan)
```

---

## M365 Plans

```python
# List all M365 protection plans
plans, _ = await apm.m365.plans.list()

# Get by name or UUID
plan = await apm.m365.plans.get_by_name("M365 Daily Backup")    # name search
plan = await apm.m365.plans.get("m365-plan-uuid")                # direct UUID

# Apply a plan to an M365 workload
await apm.m365.workloads.change_plan(wl, plan)

# Create a new M365 plan
from synology_apm.sdk import M365PlanCreateRequest
plan = await apm.m365.plans.create(M365PlanCreateRequest(
    name="M365 Daily",
    schedule=schedule,
    retention=retention,
))

# Update or delete
plan = await apm.m365.plans.update(plan.plan_id, M365PlanCreateRequest(
    name="M365 Daily",
    schedule=schedule,
    retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=60),
))
await apm.m365.plans.delete(plan)
```

---

## M365 Auto-Backup Rules

Automatically protect new M365 items. Accessed via `apm.m365.auto_backup_rules`.
Two independently-managed sections: **User Services** rules (per-plan CRUD; Exchange /
OneDrive / Chat members of selected Azure AD groups) and **Collaboration Services**
settings (one per-tenant object; Microsoft 365 Groups, SharePoint Sites, Personal Sites,
Teams — all items of an enabled type are included).

```python
# Full auto-backup configuration for a tenant
result = await apm.m365.auto_backup_rules.list(TENANT)
for rule in result.rules:
    print(rule.plan_id, rule.exchange_group_ids)
print(f"SharePoint auto-backup enabled: {result.sharepoint.enabled}")

# Create a User Services rule: auto-protect Exchange members of an Azure AD group
server = await apm.backup_servers.get_by_name("apm-server-01")
plan = await apm.m365.plans.get_by_name("M365 Daily Backup")
await apm.m365.auto_backup_rules.create(
    TENANT, server.namespace, plan.plan_id,
    exchange_group_ids=["123e4567-e89b-12d3-a456-426614174010"],
)

# Update / delete an existing rule (obtained via list(); omitted fields keep current values)
await apm.m365.auto_backup_rules.update(rule, onedrive_group_ids=["123e4567-e89b-12d3-a456-426614174011"])
await apm.m365.auto_backup_rules.delete(rule)

# Replace Collaboration Services settings (types omitted or None are disabled)
await apm.m365.auto_backup_rules.update_collab_settings(
    TENANT,
    sharepoint=result.sharepoint,  # pass current settings to preserve a type
    teams=result.teams,
)
```

---

## M365 Export

Exchange mailbox and Group mailbox PST export. Accessed via `apm.m365.exchange_export` and
`apm.m365.group_export`.

```python
import asyncio
from synology_apm.sdk import M365WorkloadType, M365ExportStatus

TENANT = (await apm.saas.list())[0][0].tenant_id

# Resolve the workload and a version to export
wl = await apm.m365.workloads.get_by_name(
    "alice@contoso.com", TENANT, workload_type=M365WorkloadType.EXCHANGE
)
versions, _ = await apm.m365.workloads.list_versions(wl)
version = versions[0]

# Start an export
result = await apm.m365.exchange_export.start(wl, version)

if result.ready_to_download:
    # The PST was pre-built; download immediately
    url = await apm.m365.exchange_export.get_download_url_by_ready_result(result)
else:
    # Poll until the export finishes being prepared
    while True:
        activity = await apm.m365.exchange_export.get_activity_by_result(result)
        if activity.status == M365ExportStatus.READY_TO_DOWNLOAD:
            url = await apm.m365.exchange_export.get_download_url_by_activity(activity)
            break
        if activity.status in (M365ExportStatus.FAILED, M365ExportStatus.CANCELED):
            raise RuntimeError(f"Export ended with status {activity.status.value}")
        await asyncio.sleep(5)

# Download the PST
await apm.download_file(url, "/tmp/alice-mailbox.pst",
    on_progress=lambda done, total: print(f"{done}/{total} bytes"))

# Group mailbox export (no archive_mailbox option)
group_wl = await apm.m365.workloads.get_by_name(
    "marketing@contoso.com", TENANT, workload_type=M365WorkloadType.GROUP
)
result = await apm.m365.group_export.start(group_wl, version)

# Cancel an in-progress export
activity = await apm.m365.exchange_export.get_activity_by_result(result)
await apm.m365.exchange_export.cancel(activity)

# List active or recent exports
exports, _ = await apm.m365.exchange_export.list(wl)
```

---

## Activities

```python
from synology_apm.sdk import BackupActivityStatus, RestoreActivityStatus

# List recent backup activities (returns list[BackupActivity])
activities, total = await apm.activities.backup.list(limit=50)

# Filter by status, workload, or time window
running, _ = await apm.activities.backup.list(status=[BackupActivityStatus.BACKING_UP])
ns_acts, _ = await apm.activities.backup.list(namespace=["ns-uid-001"])
recent, _  = await apm.activities.backup.list(since=datetime.now(timezone.utc) - timedelta(hours=24))

for act in activities:
    dur = f"{act.duration_seconds}s" if act.duration_seconds is not None else "—"
    print(f"{act.workload_name}  {act.status.value}  {dur}")

# Get activity details + log entries
act = await apm.activities.backup.get(activity_id)
for entry in act.log_entries:
    print(f"[{entry.level}] {entry.message}")

# Cancel a running backup activity (pass the BackupActivity object)
await apm.activities.backup.cancel(act)

# List and cancel restore activities (returns list[RestoreActivity])
restore_acts, _ = await apm.activities.restore.list(limit=50)
restore_act = await apm.activities.restore.get(activity_id)
await apm.activities.restore.cancel(restore_act)  # pass the RestoreActivity object

# Latest activity for a workload by display name (full details, including log entries)
act = await apm.activities.backup.get_latest_by_workload_name("vm-web-01")
act = await apm.activities.restore.get_latest_by_workload_name("vm-web-01")
```

---

## Backup Servers

```python
# List all backup servers in the cluster
servers, _ = await apm.backup_servers.list()

# Filter by status
from synology_apm.sdk import ServerStatus
disconnected, _ = await apm.backup_servers.list(status_filter=[ServerStatus.DISCONNECTED])
online, _       = await apm.backup_servers.list(status_filter=[ServerStatus.HEALTHY, ServerStatus.WARNING, ServerStatus.CRITICAL])

# Filter by server type
from synology_apm.sdk import BackupServerType
dp_only,  _ = await apm.backup_servers.list(type_filter=[BackupServerType.DP])
nas_only, _ = await apm.backup_servers.list(type_filter=[BackupServerType.NAS])

# Get a single server
server = await apm.backup_servers.get(backup_server_id)
print(f"{server.name}  {server.model}  status={server.status.value}")
if server.storage_total_bytes is not None:
    print(f"Storage: {server.storage_used_bytes}/{server.storage_total_bytes} bytes ({server.storage_usage_pct:.1f}%)")
else:
    print("Storage: - (data unavailable)")
print(f"Data reduction: {server.backup_data_reduction_ratio:.1f}% saved")

# Apply or remove a tiering plan (DP-type servers only; pass None to remove)
tp = await apm.tiering_plans.get_by_name("30-Day Tiering")
await apm.backup_servers.change_tiering_plan(server, tp)
```

---

## Remote Storages

```python
from synology_apm.sdk import (
    GenericS3StorageAddRequest,
    AmazonS3StorageAddRequest,
    APVStorageAddRequest,
    RemoteStorageUpdateRequest,
    RemoteStorageConflictError,
    RemoteStorageInUseError,
    RemoteStorageUnmanagedCatalogError,
)

# List and look up
storages, _ = await apm.remote_storages.list()
storage = await apm.remote_storages.get(storage_id)
storage = await apm.remote_storages.get_by_name("DSM-Storage")

# Add S3 Compatible storage (endpoint required)
try:
    result = await apm.remote_storages.add(GenericS3StorageAddRequest(
        access_key="AKID…",
        secret_key="…",
        vault_name="MyVault",
        endpoint="https://s3.example.com:443",
        encryption_enabled=True,
    ))
    if result.encryption_key:
        print(f"Save this key — it cannot be retrieved later: {result.encryption_key}")
except RemoteStorageConflictError as e:
    print(f"Vault already registered: {e.resource_id}")
except RemoteStorageUnmanagedCatalogError as e:
    # Retry with a retirement plan to relink the existing catalogs
    rp = await apm.retirement_plans.get_by_name("Compliance Retention")
    result = await apm.remote_storages.add(GenericS3StorageAddRequest(
        access_key="AKID…", secret_key="…", vault_name="MyVault",
        endpoint="https://s3.example.com:443", unmanaged_retirement_plan=rp,
    ))

# Add Amazon S3 (APM derives the endpoint from the bucket and credentials)
result = await apm.remote_storages.add(AmazonS3StorageAddRequest(
    access_key="AKID…",
    secret_key="…",
    vault_name="my-bucket",
))

# Add ActiveProtect Vault
result = await apm.remote_storages.add(APVStorageAddRequest(
    access_key="key",
    secret_key="secret",
    endpoint="apv.example.com:5001",
    trust_self_signed=True,
))

# Update credentials
await apm.remote_storages.update(storage, RemoteStorageUpdateRequest(
    access_key="new-key",
    secret_key="new-secret",
))

# Delete
try:
    await apm.remote_storages.delete(storage)
except RemoteStorageInUseError as e:
    print(f"Still referenced by active plans: {e.resource_id}")
```

---

## Hypervisors

```python
# List all registered hypervisor inventory servers
hypervisors, _ = await apm.hypervisors.list()
hv = await apm.hypervisors.get(hypervisor_id)
hv = await apm.hypervisors.get_by_name("esxi1.example.com")

print(f"{hv.hostname}  type={hv.host_type.value}  version={hv.version}")
print(f"address={hv.address}  port={hv.port}  account={hv.account}")
```

---

## Logs

All log methods require a `BackupServer` to route the request. Only `BackupServerType.DP`
(ActiveProtect appliance) servers carry logs; pass a DP server or the call will fail.

```python
from synology_apm.sdk import BackupServerType, LogLevel, APMActivityLogType

server = await apm.backup_servers.get_by_name("apm-server-01")

# Activity log (PROTECTION / SYSTEM / DATA_ACCESS events)
# Note: total is always 0 for this log type — pagination must be managed by caller
activity_logs, _ = await apm.logs.list_activity(
    server,
    levels=[LogLevel.ERROR, LogLevel.WARNING],
    log_type=APMActivityLogType.PROTECTION,
    limit=50,
)
for entry in activity_logs:
    print(f"[{entry.level.value}] {entry.timestamp}  {entry.description}")

# Drive information log (total is the real count)
drive_logs, total = await apm.logs.list_drive(server, limit=100)

# Connection log (total always 0)
conn_logs, _ = await apm.logs.list_connection(server)

# Advanced system log (total always 0)
sys_logs, _ = await apm.logs.list_system(server)
```

---

## System Info

```python
# Complete site overview: UUID, external address, management server, storage stats, workload usage
site = await apm.get_site_info()
print(f"UUID:    {site.site_uuid}")
print(f"Address: {site.external_address}:{site.port}")

mgmt = site.primary_management_server
print(f"Host:     {mgmt.hostname}")
print(f"Model:    {mgmt.model}")
print(f"System Version: {mgmt.system_version}")
print(f"Status:   {mgmt.status.value}")

storage = site.site_storage
print(f"Logical backup data:  {storage.logical_backup_data_bytes:,} bytes")
print(f"Physical storage:     {storage.physical_backup_data_bytes:,} bytes")
print(f"Data reduction:       {storage.backup_data_reduction_ratio:.1f}%")

usage = site.workload_usage
print(f"Total workloads: {usage.total_count}")
print(f"Total data size: {usage.total_protected_data_bytes:,} bytes")
```

---

## Error handling

All SDK exceptions inherit from `APMError` and carry three common attributes:

| Attribute | Description |
|-----------|-------------|
| `.message` | Human-readable description |
| `.error_code` | Synology/APM numeric error code (`int \| None`) |
| `.response_body` | Full JSON body from APM, for debugging (`Any \| None`) |

Resource-oriented exceptions (`ResourceNotFoundError`, `InvalidOperationError`, `PlanNameConflictError`, `PlanInUseError`, `DuplicateWorkloadError`, and the `RemoteStorage*` conflict/in-use errors) additionally carry `.resource_type` and `.resource_id` identifying the resource involved. Operation-specific exceptions raised by each method — and their extra attributes, such as `PlanInUseError.has_workloads` or `RemoteStorageUnmanagedCatalogError.catalog_count` — are documented in the method's docstring and the [Sphinx API reference](https://synology-apm.github.io/apm-sdk-python/).

`str(exc)` automatically appends the full `response_body` as formatted JSON when present, making it easy to forward to the API provider.

```python
from synology_apm.sdk import APMError, AuthenticationError, ResourceNotFoundError

try:
    wl = await apm.machine.workloads.get_by_name(some_name)
except ResourceNotFoundError as e:
    print(f"{e.resource_type} not found: {e.resource_id}")
except AuthenticationError:
    print("Session expired — re-authenticate")
except APMError as e:
    # e.message    → short description
    # e.error_code → numeric APM error code
    # e.response_body → raw dict from APM, useful for bug reports
    print(f"APM error {e.error_code}: {e.message}")
    if e.response_body:
        import json; print(json.dumps(e.response_body, indent=2))
```

Session expiry is handled automatically: the SDK re-authenticates once before raising `AuthenticationError`.

---

## Data model

The main model classes returned by the collections above:

| Class | Purpose |
|-------|---------|
| `MachineWorkload` / `M365Workload` | A protected device / Microsoft 365 workload, including its backup status and assigned plan |
| `WorkloadVersion` | One backup version of a workload, with lock state and backup-copy status |
| `ProtectionPlan` | A backup plan: schedule, retention, and backup-copy policy |
| `RetirementPlan` | A retention-only plan applied to retired (no longer backed up) workloads |
| `TieringPlan` | A plan that tiers old versions to remote storage |
| `BackupActivity` / `RestoreActivity` | One backup / restore task record, in progress or historical |
| `BackupServer` | A backup server in the cluster, with storage and data-reduction statistics |
| `RemoteStorage` | A registered remote storage (external vault) target |
| `Hypervisor` | A registered hypervisor inventory server |
| `APMActivityLog` / `DriveLog` / `ConnectionLog` / `SystemLog` | Server-scoped log entries |
| `M365ExportActivity` | An Exchange/Group mailbox PST export task |
| `M365AutoBackupRule` / `M365AutoBackupRuleListResult` | Auto-backup rules and per-tenant collaboration-service settings |
| `SiteInfo` | Site-wide overview: management servers, storage stats, workload usage |

Every field of every model — names, types, and when each is `None` — is documented in the class docstrings and the [Sphinx API reference](https://synology-apm.github.io/apm-sdk-python/).

All model objects are **frozen dataclasses** (immutable after creation).
