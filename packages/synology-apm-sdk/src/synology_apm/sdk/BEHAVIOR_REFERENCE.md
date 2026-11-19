# APM Python SDK — Behavior Reference

> Corresponding product: Synology ActiveProtect Manager

**Purpose of this document**: companion reference to [`README.md`](README.md) (the SDK design
contract) — enum ↔ API string mappings, type-system edge cases, non-obvious per-collection
behavior rules, and the collection access-path map. Read `README.md` first for conventions and
the "Adding a New SDK Method or Field" recipe, which points here only for the specific sections
a change actually touches. This is reference material, not a document meant to be read cover to
cover.

---

## Table of Contents

- [Enum Definitions and API String Mapping](#enum-definitions-and-api-string-mapping)
- [Type System Notes](#type-system-notes)
- [Collection Behavior Rules](#collection-behavior-rules)
   - [MachineWorkloadCollection](#machineworkloadcollection)
   - [MachineSandboxCollection](#machinesandboxcollection)
      - [A sandbox is a restore activity](#a-sandbox-is-a-restore-activity)
      - [Requests](#requests)
      - [Locations](#locations)
      - [Defaults](#defaults)
      - [Values that are not settings](#values-that-are-not-settings)
      - [Validation and SandboxSettingsError](#validation-and-sandboxsettingserror)
   - [Sandbox console access](#sandbox-console-access)
      - [Console link](#console-link)
      - [Opening an access](#opening-an-access)
      - [Console transport](#console-transport)
      - [Access reuse and replacement](#access-reuse-and-replacement)
      - [Screen](#screen)
      - [VNC relay](#vnc-relay)
   - [M365WorkloadCollection](#m365workloadcollection)
   - [M365AutoBackupRuleCollection](#m365autobackuprulecollection)
   - [GWSWorkloadCollection](#gwsworkloadcollection)
   - [GWSAutoBackupRuleCollection](#gwsautobackuprulecollection)
   - [BackupActivityCollection / RestoreActivityCollection](#backupactivitycollection--restoreactivitycollection)
   - [LogCollection](#logcollection)
   - [ExchangeExportCollection / GroupExportCollection](#exchangeexportcollection--groupexportcollection)
   - [BackupServerCollection](#backupservercollection)
   - [Common Patterns Across Plan Collections](#common-patterns-across-plan-collections)
   - [ProtectionPlanCollection](#protectionplancollection)
   - [RetirementPlanCollection](#retirementplancollection)
   - [TieringPlanCollection](#tieringplancollection)
   - [RemoteStorageCollection](#remotestoragecollection)
   - [SaasCollection](#saascollection)
   - [APMClient.get_site_info()](#apmclientget_site_info)
- [Collection Map](#collection-map)

---

## Enum Definitions and API String Mapping

> **Convention**: enum values are SDK semantic values (snake_case). Complete raw-API ↔ SDK
> value tables are **not** restated here — they live as mapping dicts next to the parser code
> that uses them (see the index below), which is the single source of truth. This section
> records only mappings whose *semantics* are non-obvious: values computed from multiple
> fields, one-to-many filters, magic values, and naming decisions. For every other enum, see
> its docstring (→ Sphinx API docs); the CLI display mapping table is maintained independently
> by the CLI layer.

### Where the mapping dicts live

| Enum family | Mapping dict(s) |
|---|---|
| `ServerStatus`, `BackupServerRole`, server status/type filters | `collections/backup_servers.py` — `_SERVER_STATUS_MAP`, `_SYNC_DISCONNECTED`, `_ROLE_MAP`, `_STATUS_FILTER_MAP`, `_TYPE_FILTER_MAP` |
| `RemoteStorageStatus` / `RemoteStorageType` (API `connectionStatus` / `storageType`) | `collections/remote_storages.py` — `_REMOTE_STORAGE_STATUS_MAP`, `_REMOTE_STORAGE_TYPE_MAP` |
| `BackupActivityStatus` / `RestoreActivityStatus` (parse and filter directions) | `collections/_activity_parsers.py` — `_BACKUP_STATUS_MAP`, `_RESTORE_STATUS_MAP`, `_BACKUP_STATUS_TO_API`, `_RESTORE_STATUS_TO_API`, `_restore_type_filter` |
| `RestoreType` (parse and `restore_types` filter directions), `ActivityWorkloadType`, cancel type strings | `collections/_activity_parsers.py` — `_RESTORE_TYPE_MAP` / `_RESTORE_TYPE_TO_API`, `_RAW_TO_SUBTYPE`, `_SUBTYPE_TO_CANCEL_TYPE` |
| `MachineWorkloadType` / `M365WorkloadType` / `GWSWorkloadType` → `BackupActivityCollection.list()`'s `categoryService`/`saasServiceType` filter params | `collections/_activity_parsers.py` — `_MACHINE_TYPE_TO_CATEGORY_SERVICE`, `_M365_TYPE_TO_SAAS_SERVICE`, `_GWS_TYPE_TO_SAAS_SERVICE` |
| `M365WorkloadType` | `collections/m365.py` — `_TYPE_TO_API_TYPE` / `_API_TYPE_TO_TYPE` |
| `GWSWorkloadType` | `collections/gws.py` — `_TYPE_TO_API_TYPE` / `_API_TYPE_TO_TYPE` |
| `MachineWorkloadType`, `VersionStatus` | `collections/_shared.py` — `_MACHINE_WORKLOAD_TYPE_MAP`, `_VERSION_STATUS_MAP`; the `statuses` filter of `list_versions()`/`get_latest_version()` → `_VERSION_STATUS_TO_API`, keyed by the public `LISTED_VERSION_STATUSES` (`enums.py`; versions are only ever listed as COMPLETED/PARTIAL/FAILED/CANCELED, which the SDK requests when `statuses` is omitted) |
| `VerifyStatus` (parse and filter directions) | `enums.py` — `_VERIFY_STATUS_MAP`; `collections/machine.py` — `_VERIFY_STATUS_TO_API` |
| `WorkloadStatus` filter direction (`list()` `status` parameter; parse direction is branching logic — see "WorkloadStatus" below) | `collections/machine.py` — `_STATUS_TO_JOB_STATUS`, `_STATUS_TO_LVR`; `collections/m365.py` / `collections/gws.py` — `_STATUS_TO_API_BACKUP_STATUS` |
| `VersionCopyStatus`, `CopyReason` | `enums.py` — `_VERSION_COPY_STATUS_MAP`; `collections/_shared.py` — `_COPY_REASON_MAP`, `_COPY_REASON_SKIPPED_MAP`, `_COPY_ERROR_STATUS_MAP` |
| Plan task/db enums (`MachineOsType`, `MachineTaskScope`, `DbActionOnError`, `MssqlLogSetting`, `OracleLogSetting`) | `collections/_protection_plan_parsers.py` — `_OS_TYPE_MAP`, `_SOURCE_TYPE_MAP`, `_DB_ACTION_MAP`, `_MSSQL_LOG_MAP`, `_ORACLE_LOG_MAP` |
| `RemoteStorageType` → plan/tiering `destinationType` | `collections/_shared.py` — `_STORAGE_TYPE_TO_DEST_TYPE` |
| `HypervisorType` | `collections/hypervisors.py` — `_HOST_TYPE_MAP` |
| `SandboxDiskController` (`vdiskMode`), `SandboxAdapterModel` (`vnicType`), `SandboxVideoCard` | `collections/machine_sandbox.py` — `_VDISK_MODE` / `_VDISK_MODE_TO_CONTROLLER`, `_VNIC_TYPE`, `_VIDEO_CARDS` |
| `SandboxKey` (→ X11 keysyms sent as RFB key events) | `_vnc.py` — `_KEYSYMS` |
| `M365ExportStatus` | `collections/m365_mail_export.py` — `_EXPORT_STATUS_MAP` |
| `LogLevel` (activity logs) | `collections/_activity_parsers.py` — `_LOG_LEVEL_MAP` |
| `LogLevel` (backup-server logs) | `collections/logs.py` — `_LEVEL_API` / `_LEVEL_PARSE` |
| `APMActivityLogType` | `collections/logs.py` — `_TYPE_API` / `_TYPE_PARSE` |
| `WorkloadStatType` | `collections/system.py` — `_WORKLOAD_STAT_TYPE_MAP` |
| `FileServerType` | `collections/machine.py` — `_FS_OS_TYPE_MAP` |
| `BackupScope` | `collections/_activity_parsers.py` — `_BACKUP_SCOPE_MAP` |

### Naming decision: GWS

`GWS` is the SDK's chosen name for the Google Workspace category; the raw APM API uses
`GW` / `gw` / `APPLICATION_GW` / `GW_*` for this category (e.g. `GW_DRIVE`, `GW_MAIL`) —
this is an established SDK convention of preferring official product terminology over raw API
abbreviations or legacy tokens (applies to `WorkloadCategory.GWS`, `ActivityWorkloadType.GWS`,
`WorkloadStatType.GWS`).

The same principle applies to `GWSWorkloadType.SHARED_DRIVE`: the raw API calls this subtype
`TEAM_DRIVE` (legacy Google terminology), but the product name is "Shared Drive" — so the SDK
enum member is `SHARED_DRIVE`, and `TEAM_DRIVE` only appears in the raw-string mapping dicts
(`collections/gws.py`'s `_TYPE_TO_API_TYPE`, `collections/_activity_parsers.py`'s
`_GWS_TYPE_TO_SAAS_SERVICE`), never in public SDK surface.

### Naming decision: SaaS application hierarchy

`SaasCollection.list()` returns a mix of M365 tenants and GWS domains from a single call, but
the two categories do not share one model with a forced-neutral vocabulary. Instead,
`models/saas.py` mirrors the `Workload` / `M365Workload` / `GWSWorkload` pattern already used
for workloads: a `SaasApplication` base class holds only the fields genuinely identical across
categories (`name`, `category`, `protected_data_bytes`), while `M365TenantInfo` (`tenant_id`,
`domain`) and `GWSDomainInfo` (`domain`, `domain_admin`) each add their own category-specific
fields using each provider's own vocabulary. `M365TenantInfo.domain` holds the tenant's domain
(not a contact email, despite the raw API field name `tenantMail`);
`GWSDomainInfo.domain` doubles as the unique identifier — unlike `M365TenantInfo.domain`, which
is display metadata only, separate from the opaque `tenant_id` — since GWS has no separate
opaque domain ID. A future third SaaS provider should follow the same pattern —
`ThirdProviderInfo(SaasApplication)` — rather than forcing its fields onto the existing two
categories' vocabulary.

### Enums Requiring Parser Computation / Conversion Logic

#### ServerStatus

Computed by the collection parser from **two** API fields, in this precedence order:
1. `spec.syncStatus == "JOINING"` → `SYNCING`; `spec.syncStatus` in `_SYNC_DISCONNECTED` → `DISCONNECTED`.
2. Otherwise `status.status` is looked up in `_SERVER_STATUS_MAP` (see the dict index above);
   an unrecognized value falls back to `DISCONNECTED`.

#### WorkloadStatus

Derived from the API `jobStatus` (Machine) / `backupStatus` (M365, GWS) fields:
- When `BACKING_UP`: PC/PS/VM have a `backup_progress` percentage; FS/M365/GWS have an `items_backed_up` count.
- M365/GWS's API `backupStatus` value of `"WARNING"` maps to `PARTIAL`.
- `RETIRED`: workload is under a Retirement Plan; new backups will no longer be created. `DELETING` takes precedence if the workload is concurrently being deleted.
- `DELETING`: workload deletion is in progress; the workload will disappear from `list()` shortly.

#### VerifyStatus

PS/VM-specific; for PC/FS, `verify_status` is always `None`.
The API `VERIFY_NONE` and the non-PS/VM `VERIFY_NOT_ENABLED` are mapped by the parser to `None` and do not enter this enum.
The full API raw → SDK enum mapping table is in `enums._VERIFY_STATUS_MAP`; `NOT_ENABLED` represents "PS/VM is enabled but verification has not yet been configured".

#### Plan task/db enums — magic-value notes

Value tables live in `collections/_protection_plan_parsers.py` (see the dict index above).
Non-obvious semantics:

- `DbActionOnError` — `None` (DB backup disabled) corresponds to
  `disableDbBackup: true, logsProcessing: "DISABLED"` in the API. The SDK sends this
  combination when `MachinePlanCreateRequest.db_config` is `None`.
- `MachineOsType.NONE` — VM and FS task entries always use this value.
- `MachineTaskScope` — VM and FS task entries have no `agentScope` in the API;
  `MachineTaskConfig.scope` is `None` for these entries.

#### VersionCopyStatus

Parsed from the version's outer `copyStatus` field (`COPY_STATUS_*` prefix API enum) via
`enums._VERSION_COPY_STATUS_MAP`. Non-obvious semantics:

- The API value `"COPY_STATUS_NONE"` maps to `COMPLETED` (not to "no status").
- An unrecognized `copyStatus` string maps to `None` (graceful fallback for future API additions).
- `ProtectionPlan.backup_copy_status`, `TieringPlan.tiering_status`, and
  `BackupServer.tiering_status` are computed from a different field using a **separate**
  mapping — see [Shared Backup-Copy / Tiering Status Computation](#shared-backup-copy--tiering-status-computation) below.

#### Shared Backup-Copy / Tiering Status Computation

`ProtectionPlan.backup_copy_status`, `TieringPlan.tiering_status`, and `BackupServer.tiering_status`
are all computed by the same `_parse_copy_status_core()` helper in `collections/_shared.py`, from
a different input field per caller:

| Caller | Input field | `None` when |
|---|---|---|
| `ProtectionPlan.backup_copy_status` | `backupCopyStatus.copyStatus` | `backupCopyStatus` absent |
| `TieringPlan.tiering_status` / `BackupServer.tiering_status` | `tieringInfo.tieringStatus` | `tieringInfo` absent or its `tieringStatus` is empty |

Mapping (identical for all three callers, with one tiering-specific addition noted in the table):

| Input value | Computed `.status` | Notes |
|---|---|---|
| `"NOT_ENABLED"` | `NOT_ENABLED` | |
| `"SKIPPED_WORKLOAD"` | `SKIPPED` | `skipped_workload_count` from `skippedWorkloadCount`; `reason` from `_resolve_copy_reason("SKIPPED_WORKLOAD", statusReason)` |
| `"DOING"` | `IN_PROGRESS` | `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` |
| `"NO_VERSIONS_TO_COPY"` | `COMPLETED` | `reason = CopyReason.NO_VERSIONS_TO_COPY` |
| `"COMPLETED"` + `pendingVersionCount > 0` | `WAITING` | `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` (string `"0"` or empty → `None`) |
| `"COMPLETED"` + `pendingVersionCount == 0` | `COMPLETED` | `reason = None` |
| RETRY-class values (`_COPY_ERROR_STATUS_MAP`: `"DESTINATION_DISCONNECTED"`, `"UNDER_MAINTENANCE"`, `"AUTHENTICATION_FAIL"`, `"OUT_OF_STORAGE"`, `"OUT_OF_LICENSE_QUOTA"`, `"SOURCE_INCOMPATIBLE"`, `"DESTINATION_INCOMPATIBLE"`, `"SSL_VERIFY_FAILED"`) | `RETRY` | `reason` from `_resolve_copy_reason(copyStatus)`; `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` |
| FAILED-class values (`_COPY_ERROR_STATUS_MAP`: `"INFRASTRUCTURE_ERROR"`, `"VAULT_NOT_MOUNTED"`, `"DESTINATION_DATA_CORRUPTED"`, `"DESTINATION_NOT_EXIST"`, `"MISSING_LINK_KEY"`, `"FS_READONLY"`) | `FAILED` | `reason` from `_resolve_copy_reason(copyStatus)`; `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` |
| `"NONE"` (tiering input only) | `COMPLETED` | `reason = None` — this extra input value has no equivalent in the backup-copy input |

`remainingBytes` of `"0"` or empty string maps to `remaining_bytes = None` (unknown/not applicable), not `0`.

#### CopyReason

A single semantic enum that merges two API inner fields (`BackupCopyStatusCopyStatus` +
`BackupCopyStatusStatusReason`) into one value. The SDK resolves
`(status.copyStatus, status.copyStatusReason)` internally via `_COPY_REASON_MAP` /
`_COPY_REASON_SKIPPED_MAP` in `collections/_shared.py` (`SKIPPED_WORKLOAD` statuses resolve
through the *reason* field; all other statuses resolve directly); the CLI never sees raw API
strings.

`CopyReason` is set only when the outer `VersionCopyStatus` is `SKIPPED`, `RETRY`, or
`FAILED`; it is `None` for all other outer statuses. `NO_VERSIONS_TO_COPY` is the one
non-error value: the outer status is `COMPLETED`, but this reason distinguishes "no versions
eligible for copy" from a true completion. The neutral name suits both backup copy and
future tiering contexts.

---

## Type System Notes

### Ambiguity of the Workload ID Field Name

`workload_id` corresponds to different field names depending on the API context; the SDK consistently exposes it externally as `workload_id`, with the mapping handled by the collection parser:

| Context | API field name |
|------|-----------|
| Machine workload response | `id` |
| M365/GWS workload response | `uid` |
| Request body of all write operations (backup_now, cancel_backup, change_plan, retire) | `uid` |
| Path parameter of the version list path | `id` |

### API String for M365 Workload Type

The top-level `workloadType` field received by the M365WorkloadCollection parser carries the
service subtype directly (`"USER_EXCHANGE"`, `"USER_DRIVE"`, `"USER_CHAT"`, `"SITE"`,
`"TEAMS"`, `"GROUP_EXCHANGE"` — mapped via `_API_TYPE_TO_TYPE` in `collections/m365.py`).
The value `"APPLICATION_M365"` appears only in the **activity** API's `spec.workloadType`
(parsed by `collections/_activity_parsers.py`), where it identifies the M365 category as a whole.

### M365Info Union Type

```python
M365Info = M365UserInfo | M365SiteInfo | M365TeamInfo | M365GroupInfo
```

Each subtype has a `.label: str` property that returns the identifier string most suitable for display:

| Type | `.label` return value |
|------|----------------|
| `M365UserInfo` | `user_principal_name` |
| `M365SiteInfo` | `site_url` |
| `M365TeamInfo` | `web_url` |
| `M365GroupInfo` | `mail` |

### ActivityLogEntry vs APMActivityLog

These two have similar names but belong to different subsystems:

| Type | Purpose | How to obtain |
|------|------|---------|
| `ActivityLogEntry` | Execution log of a backup/restore activity (`Activity.log_entries` field) | Populated by the single-activity lookups (`get()`, `get_by_version()`, …); `None` after `list()` or with `include_logs=False` |
| `APMActivityLog` | System activity log of a backup server (an independent resource) | `LogCollection.list_activity()` |

### Field Differences Between RetirementPlan and ProtectionPlan

Both are "plans", but their structures differ and they are not interchangeable:

| Characteristic | `ProtectionPlan` | `RetirementPlan` |
|------|-----------------|-----------------|
| Domain | Specified via the `category` field | Domain-agnostic (shared across all categories) |
| Schedule | Yes (`policy.schedule`) | No |
| Backup Copy | Yes (`backup_copy_policy`) | No |
| Count fields | `workload_count`, `successful_workload_count`, `unsuccessful_workload_count` | `workload_count` |
| Retention type | `ProtectionRetentionPolicy` (supports GFS, keep days/versions/all) | `RetirementRetentionPolicy` (`days`, `keep_latest_version`) |

### `Workload.plan` Is a Lightweight Plan Reference

`MachineWorkload.plan` / `M365Workload.plan` / `GWSWorkload.plan` (type
`ProtectionPlan | RetirementPlan`) is built directly from the workload's own response, without
an extra request to the plans collections. Only `plan_id`, `name`, and (for `ProtectionPlan`)
`category` are guaranteed to be set; all other fields — `ProtectionPlan.policy`/`workload_count`
and `RetirementPlan.retention`/`workload_count` — default to `None` on this lightweight
reference. To obtain a fully-populated plan, fetch it separately via `apm.machine.plans`,
`apm.m365.plans`, `apm.gws.plans`, or `apm.retirement_plans` using `wl.plan.plan_id`.

`Workload.is_retired` and the `ProtectionPlan`/`RetirementPlan` discrimination on `wl.plan` are
derived from the same underlying signal in `MachineWorkloadCollection`, `M365WorkloadCollection`,
and `GWSWorkloadCollection` parsers (a workload is retired exactly when its assigned plan is a
retirement plan) — the three can never disagree.

### `MachineWorkload.fs_config` — FS Connection Details

`MachineWorkload.fs_config` (`FileServerConfig | None`) is populated for FS workloads only; `None` for PC/PS/VM.
It carries the same connection info returned by `list()` — no extra API call is needed.
`FileServerConfig.login_user` is set from the stored login user, but `login_password` is never returned by the API — `FileServerConfig` has no `login_password` field.

`FileServerConfig.selectors` (`tuple[FileServerPathSelector, ...]`) contains at least one entry:
- `FileServerPathSelector(path="")` with empty `excluded_paths`: whole machine, no exclusions
- `FileServerPathSelector(path="")` with non-empty `excluded_paths`: whole machine, with sub-paths excluded
- Non-empty `path`: a specific folder; `excluded_paths` lists sub-paths within it to skip

When the API returns an empty `remoteSessionList` (`"[]"`), the parser defaults to `(FileServerPathSelector(path=""),)` (whole-machine selector).
When `spec.configFs` is absent, `fs_config` is `None` (not a default `FileServerConfig`).

`FileServerConfig.server_type` is `FileServerType.UNKNOWN` when APM reports a server type not yet recognised by this SDK version.

### `MachineWorkload.inventory_type` — VM Hypervisor Link

`MachineWorkload.inventory_type` (`HypervisorType | None`) is populated for VM workloads with a
hypervisor inventory link only, via the same `_HOST_TYPE_MAP` used by `Hypervisor.host_type`
(`collections/hypervisors.py`'s `_parse_optional_host_type()` helper) — `None` for PC/PS/FS, and
also `None` (not `HypervisorType.UNKNOWN`) for a VM workload with no inventory link at all,
whether the API reports that as an absent field or its `"NONE"` sentinel string; a VM workload
whose raw inventory type string is present but unrecognized by this SDK version still parses as
`HypervisorType.UNKNOWN`, distinguishing "no inventory" from "unrecognized inventory type".

### Special Fields of WorkloadVersion

- `portal_version_id` (API `spec.versionId`): used by the M365 export/restore API paths
- `snapshot_id` (API `spec.snapshotId`): used by the portal entries:download API
- `execution_id`: the corresponding Activity's executionId, passed to `BackupActivityCollection.get_by_version()`
- `locations` (`list[VersionLocation]`): `lock_version()` / `unlock_version()` use its `namespace` and `location_id`; download/export operations use its `namespace`, `location_id`, and `connection_id` (selected via the `location_id` parameter)
- `copy_status` (`VersionCopyStatus | None`): outer backup copy status, from the version's top-level `copyStatus` API field — see [VersionCopyStatus](#versioncopystatus) above for the mapping
- `copy_reason` (`CopyReason | None`): see [CopyReason](#copyreason) above for when it's set

### Parsing Rules for RemoteStorage's usedSpace / remainingSpace

The API fields are of string type; parser rules:
- `""` or field missing → `None` (data unavailable)
- `"0"` → integer `0` (semantically "no space used", different from "data unavailable"; must not be converted to `None`)

---

## Collection Behavior Rules

> All `list()` methods return a `ListResult[T]` (a `NamedTuple` with `items` and `total` fields); callers (including tests) can unpack it positionally like a plain `(items, total)` tuple: `items, _ = await collection.list()`. `total` is `None` when the underlying data source cannot report a reliable count (see each collection method's docstring for which case applies). The two exceptions are `M365AutoBackupRuleCollection.list()` and `GWSAutoBackupRuleCollection.list()`, which return an `M365AutoBackupRuleListResult` / `GWSAutoBackupRuleListResult` (see their sections below).

> **Namespace filter resolution (shared by `M365WorkloadCollection.list()` and
> `GWSWorkloadCollection.list()`):** `namespace: list[str] | None` is repeatable (OR logic).
> Each given namespace is resolved to its internal backup server ID via
> `_resolve_namespaces_to_server_ids()` (`collections/_shared.py`) and the resolved IDs are
> sent as `filter.backupServerUids`; one shared backup-server scan covers every namespace
> given, regardless of count, and is issued whenever `namespace` is non-empty — including
> when no namespace ends up resolving, since resolution is the scan's own outcome. A namespace
> with no matching backup server contributes no IDs rather than raising (matching every other
> repeatable filter's "a value that matches nothing narrows the result to zero"). If none of
> the given namespaces resolve, `list()` returns an empty result without issuing the workload
> API request. This differs from
> `MachineWorkloadCollection.list()`'s `namespace` parameter, which is sent directly as
> `filter.namespace` with no backup-server resolution step (see below).

### MachineWorkloadCollection

**get() / get_by_name():**

| Method | Number of API calls | Notes |
|------|-----------|------|
| `get(workload_id, namespace)` | 1 (`GET /{id}?namespace={ns}`) | Queries directly by the `(namespace, workload_id)` primary key |
| `get_by_name(name, is_retired=False)` | N (keyword search + exact name match) | `is_retired` determines `filter.protectStatus` |

**lock_version() / unlock_version()** (M365WorkloadCollection shares the same `_VersionMixin`
logic in `collections/_shared.py`) — see `_post_version_lock()`'s own docstring for the
request-body shape.

**Special behavior of add_file_server() / update_file_server():**
- `add_file_server()`'s `DuplicateWorkloadError` corresponds to POST `errors[]` errorCode 7001; see the method's own docstring for the validation and error conditions it covers.
- `update_file_server()` fetches the current spec via GET, merges updated `configFs` fields, then PUTs the full spec (no opcode); unlike the POST endpoint's `errors[]` array, the PUT endpoint reports failure as a nested `error.errorCode` (see `_http.py`'s `_check_api_error` docstring), converted to `DuplicateWorkloadError` on errorCode 7001.
- `remoteSessionList` field mapping and the parse-direction default for an empty list: see "`MachineWorkload.fs_config`" in [Type System Notes](#type-system-notes).

**backup_now() / cancel_backup()** send a `workloadRefs: [{uid, namespace}]` body (see the
request-building code in `machine.py`).

**change_plan() / retire() dispatch:** both share the same `_put_plan_change()` request; their
opposite preconditions are documented on each method's own docstring.

**Batch response of `_put_plan_change()` (retire() / change_plan()) and delete():**
The underlying endpoints are batch operations that answer HTTP 200 even when the workload was
rejected, reporting the rejection per entry in the response body (`failed.entries` for Machine,
`errors` for M365/GWS). All three collections parse that list and raise `InvalidOperationError`
(with `resource_type="Workload"`, the workload's ID, and the entry's `errorCode` — e.g. 7018 when
the workload is still initializing) for the first failed entry, so a silently-ignored partial
failure cannot occur.

**MachineWorkloadCollection.list() plan parameter:**
`plan: list[ProtectionPlan | RetirementPlan] | None` is repeatable (OR logic); the SDK
extracts each plan's `plan_id` and sends it as a separate `filter.planId` query param.

**MachineWorkloadCollection.list() workload_types parameter:**
`workload_types: list[MachineWorkloadType] | None` — passing `None` (or omitting the argument) returns workloads of all types. The SDK always includes a fixed query parameter alongside any type filters to match APM's expected request format.

**MachineWorkloadCollection.list() namespace parameter:**
`namespace: list[str] | None` is repeatable (OR logic) — each namespace is sent as its own
`filter.namespace` query param, same multi-value pattern as `workload_types`/`plan`/`status`.
`get(workload_id, namespace)` takes a single namespace (a workload always has exactly one),
so this arity difference from `list()` is deliberate, not an oversight.

**MachineWorkloadCollection.list() status / verify_status parameters:**
Both are repeatable (OR logic) reverse-mappings of the raw filter fields; see `list()`'s own
docstring for the filter contract, and `_STATUS_TO_JOB_STATUS`/`_STATUS_TO_LVR` (`machine.py`) /
`_VERIFY_STATUS_MAP` (`enums.py`) for the raw-field reversal.

---

### MachineSandboxCollection

Instant restore of a PS/VM version to a VM on the appliance's built-in hypervisor.
`_load()` parses the four `list_*` responses of one location into private frozen dataclasses
(`_VmSpec`, `_Repo`, `SandboxLocation`, the existing VM names) gathered in a `_Plan`. Raw keys
appear only in those parsers and in `_open_in_vm_body()`, which holds the field-level request
contract, including the fields that always take a fixed value. `get_default_settings()` runs the
checks, then `_load()`, then the pure `_default_settings()`.

#### A sandbox is a restore activity

Raw `restoreType=INSTANT_RESTORE_AEM` (→ `RestoreType.INSTANT_SANDBOX`), with
`restore_destination` = the sandbox's name. `open_in_vm` returns
nothing that identifies the sandbox, so `start()` returns `None`, like `backup_now()`, and the
activity is found with `RestoreActivityCollection.list(restore_types=[RestoreType.INSTANT_SANDBOX])`.
Status, logs and stopping (`cancel()`, which also deletes the VM) go through
`RestoreActivityCollection`; there is no separate sandbox list or delete call. Ready-status
parsing and filtering:
[BackupActivityCollection / RestoreActivityCollection](#backupactivitycollection--restoreactivitycollection).

#### Requests

Every call goes to `/api/v1/portal/vmm/<call>`. `x-syno-tunnel-route` is the namespace named in
"Routed by". Only `list_spec` (`versionIds`) and `open_in_vm` (`versionId`) carry the chosen
location's `location_id`.

| Call | Routed by | Sent by | Used for |
|---|---|---|---|
| `list_spec` | Version location | `get_default_settings()`, `validate_settings()`, `start()` | The backed-up machine's spec (CPU, memory, disks, video card, machine type) |
| `list_repo` | Version location | `get_default_settings()`, `validate_settings()`, `start()` | The repo the sandbox is stored on (see [Values that are not settings](#values-that-are-not-settings)) |
| `list_resource` | Version location | `get_resources()` (one per eligible location), `get_default_settings()`, `validate_settings()`, `start()` | CPU and memory limits (`maxRamSize` in KB) and networks; only `permitted` networks are usable |
| `list_basic` | Version location | `get_default_settings()`, `validate_settings()`, `start()` | Existing VM names, for the name rule; a new sandbox's name is listed as soon as `open_in_vm` returns |
| `gen_mac` | Version location | `start()`, once per network adapter | One MAC address per adapter; an empty or duplicate result raises `APIError` before `open_in_vm` |
| `open_in_vm` | Version location | `start()` | Starts the sandbox |
| `connect_to_vm` | Activity (`RestoreActivity.namespace`) | `get_console_link()`; `get_screenshot()` and `open_vnc_bridge()` when they open a console access | Powers the VM on if needed and creates a console link (see [Console link](#console-link)) |

`get_resources()` sends only `list_resource`. `get_default_settings()`, `validate_settings()` and
`start()` send the four `list_*` calls of the chosen location concurrently.

`get_latest_version()` sends no `vmm` call: it is `MachineWorkloadCollection.get_latest_version()`
with `statuses=[SUCCESS, PARTIAL]` (`_RESTORABLE_STATUSES`, which the other methods' version
check also uses). When there is no such version it raises `InvalidOperationError`, not
`ResourceNotFoundError`: the workload exists, it only lacks a restorable version.

#### Locations

Only non-remote entries of `version.locations` are eligible: a sandbox runs on an appliance,
not on remote storage. A version without one raises `InvalidOperationError`. They are ordered
with the backup itself (`location_id == version.version_id`) first, then by server name; the
first is the default. A `location_id` that is not eligible (the argument of
`get_default_settings()`, or `SandboxSettings.location_id`) raises
`SandboxSettingsError(field="location_id")`.

#### Defaults

`get_default_settings()`'s docstring lists the defaults. Rules not obvious from that list:

- Memory rounds up to 128, 256, 512 or 768 MB, or above 768 MB to whole GiB, and is then capped
  at the location maximum. A whole-GiB value above the maximum becomes the maximum rounded down
  to whole GiB, but not below 128 MB.
- Hyper-V enlightenments default to on, whatever the backed-up machine had.
- The name's timestamp is the version time in the local time zone of the host that runs the SDK
  (`default_sandbox_name()` in `models/sandbox.py`, next to the name rules it applies).

`memory_mb` stays in MB rather than a `*_bytes` field: the SDK's `*_bytes` fields are measured,
read-only sizes, while memory is a setting that the server expresses in MB.

#### Values that are not settings

- **Storage:** the sandbox is stored on the first `list_repo` repo sorted by name. At least one
  repo must be on the backup's volume (`volumePath` equal to `vdiskStruct[0].volumePath`);
  otherwise `get_default_settings()`, `validate_settings()` and `start()` raise
  `InvalidOperationError`.
- **Weights:** every `open_in_vm` body sends `cpuWeight` `"256"` and, on every disk, `devWeight`
  `"3"` (`_CPU_WEIGHT`, `_DEV_WEIGHT`).

#### Validation and SandboxSettingsError

Every settings rule raises `SandboxSettingsError`. It extends `ValueError`, not `APMError` (it
reports a bad argument), so it is not in `ERROR_CODES`, and it carries:

- `field`: the `SandboxSettings.to_dict()` key of the setting, so the CLI and MCP map it to a
  flag or parameter without parsing the message. For a key that `with_changes()` does not know,
  `field` is that key. An error inside one adapter or disk names the list (`network_adapters`,
  `disks`); the message names the inner key (for example `disks.controller`).
- `other_field`: the second setting of a conflicting pair, otherwise `None`. The pairs are
  `hyperv_enlightenments` / `cpu_compatibility_mode` and `disks` / `machine_type` (IDE with Q35).
- `to_dict()`: `message`, `field` and `other_field`.

| Rule | Checked by |
|---|---|
| Name format, and the two pairs above | `SandboxSettings.__post_init__` (so also every `with_changes()`) |
| Unknown key, wrong value type, value that is not one of the enum's values | `SandboxSettings.with_changes()` |
| `location_id` is an eligible location | `_find_location()` |
| Name already used by another VM, CPU and memory limits, networks and SR-IOV, disk count | `_validate()` |

`validate_settings()` and `start()` run the location checks through `_prepare()`. `start()`
validates because the server does not: it accepts an out-of-range CPU count, an unknown network,
or memory below 128 MB and creates a VM that never boots. Free memory is the one condition only
the server checks: detail error 7026 → `InvalidOperationError`.

---

### Sandbox console access

`get_console_link()`, `get_screenshot()` and `open_vnc_bridge()` of
[MachineSandboxCollection](#machinesandboxcollection). The screen is reached through a console
access, never the login session (implementation: `_console.py`).

#### Console link

`connect_to_vm {guestId, workload}` powers the VM on first if needed (detail error 932 =
power-on failed → `InvalidOperationError`) and writes one activity-log entry per call. The
backup server that runs the VM creates a single-use DSM sharing link on its own host and returns
it as a relative `/sharing/<id>` path. `get_console_link()` returns the absolute URL, prefixed
with that server's origin (`BackupServerCollection._origin_for_namespace()`): the session's
`base_url` for the primary, otherwise `https://<spec.addr>` on the default port, with an IPv6
address in brackets. The origin is looked up before `connect_to_vm`, so a failed lookup
(`ResourceNotFoundError`) neither powers the VM on nor uses up a link.

The link needs no sign-in. `url` and `sharing_id` are in `_SENSITIVE_EXACT_KEYS`, and the
link's GET shows only `/sharing/***` in debug output.

#### Opening an access

1. A link is created as for `get_console_link()`.
2. One GET of the link sets a `sharing_sid` cookie. The link is then used up: a second GET
   answers 200 without a cookie.
3. A WebSocket to `/synovirtualization/ws/<sandbox_vm_id>?app_id=<random UUID>&sharing_id=<link
   id>` on the link's host. It needs both the cookie and `sharing_id`; either alone gets 400.

A link that is not a `/sharing/<id>` URL, or whose GET does not answer 200 with a cookie, raises
`APIError`.

#### Console transport

The session owns the console traffic: the link GET and every WebSocket go through
`WebAPISession.fetch_cookies()` / `ws_connect()`, on one `aiohttp.ClientSession` with a
`DummyCookieJar`, created on first use with the login session's TLS setting and timeout. The
cookies are read from the responses (redirects included) and sent as a `Cookie` header, so they
never enter the login jar. `WebAPISession.disconnect()` (and so `APMClient.disconnect()`) first
closes every WebSocket `ws_connect()` opened, since closing the client session alone leaves
established WebSockets open, then closes the client: every console connection and every VNC
bridge connection ends.

#### Access reuse and replacement

- One access opens any number of connections and keeps working while idle. The client
  (`SandboxConsoles`) keeps one console per sandbox VM, keyed by
  `(RestoreActivity.namespace, sandbox_vm_id)`, for its 16 most recently used VMs
  (`_DEFAULT_MAX_CONSOLES`). A console that is minting an access is not dropped, so the cache can
  briefly hold more. Dropping a console only forgets its access; open connections are not
  affected. `MachineSandboxCollection(consoles=...)` takes another cache (tests).
- An access is replaced only when a handshake answers 400/401, at most once per connection; a
  second rejection raises `APIError`. A 403 raises `PermissionDeniedError`; any other status
  raises `APIError`. Each replacement is another `connect_to_vm`, so another activity-log entry.
  Concurrent callers rejected on the same access share one replacement.
- A powered-off guest rejects the kept access, so the replacement's `connect_to_vm` powers it on
  again.
- A failed link creation or link GET leaves no access, so the next connection starts over.
- A connection that closes while the RFB session starts is reopened once on the same access.
  Nothing is retried after the session has started, because keys may already have been pressed.

#### Screen

The RFB client and the PNG encoding are in `_vnc.py`; its module docstring lists the protocol
subset. Rules not stated there:

- ClientInit sets the shared flag, so a viewer already connected to the console is not
  disconnected.
- Each connecting step (WebSocket handshake, RFB start) has 30 s (`_DEFAULT_TIMEOUT_S`); a
  capture has 30 s plus `delay_seconds`. A timeout raises `ConnectionTimeoutError`.
- `connect_to_vm` and the link's GET have no console timeout; only the session's request timeout
  applies.
- A screen wider or taller than 4096 px (`_MAX_SIDE`) raises `APIError`.

#### VNC relay

`open_vnc_bridge()` is `SandboxConsole.serve_vnc()`: it opens and closes one console connection
before it listens, so an unreachable console fails the call. The relay (`_relay.py`) then
listens on `127.0.0.1`:

- Each VNC client gets its own console connection on the kept access.
- A connection that cannot be opened disconnects that VNC client, without an error; with the
  client's `debug=True` it prints one line on stderr (the exception type, and the message only
  for SDK errors, since other errors can carry the link).
- When the bridge exits, every connection closes; a VNC client accepted during that exit is
  disconnected without opening one.

---

### M365WorkloadCollection

**get() / get_by_name():**

| Method | Notes |
|------|------|
| `get(workload_id, namespace, tenant_id, workload_type)` | Queries by the `(namespace, workload_id)` primary key; `tenant_id` and `workload_type` are both required |
| `get_by_name(name, tenant_id, workload_type, is_retired=False)` | `tenant_id` and `workload_type` are both required; matches against display name / UPN / group email (case-insensitive) |

**namespace parameter:** see [Namespace filter resolution](#collection-behavior-rules) above
(shared with `GWSWorkloadCollection`); arity matches `BackupActivityCollection.list()`'s
existing pattern.

**Number of API calls for list():**
- Always 1 API request (`workload_type` is required, queries only a single service subtype),
  except when `namespace` is given and none of it resolves — then 0, see above.
- When `namespace` is not None: 1 additional backup-server API call (shared scan across all
  given namespaces — see the Namespace filter resolution note above).
- `workload_type` has no "all subtypes" wildcard value to collapse the 6 per-subtype calls into
  one: the underlying filter's zero-value enum member returns the same single subtype's
  workloads as one of the 6 named values (not the union of all 6), so
  `list()` must still be called once per `M365WorkloadType` to enumerate every M365 workload for
  a tenant.

**backup_now() / cancel_backup()** use `nsUidPairs: [{namespace, uid}]`; `backup_now()`
additionally sends a top-level `tenantId`, `cancel_backup()` does not.

**change_plan() dispatch (shares the same `_put_plan_change()` request with retire()):**
the request carries a `planType` of `"ARCHIVE"` for a `RetirementPlan` or `"BACKUP"` for a
`ProtectionPlan`.

**M365WorkloadCollection.list() plan parameter:**
`plan: list[ProtectionPlan | RetirementPlan] | None` is repeatable (OR logic); the extracted
`plan_id` values are collected into the `planUids` array field of the request body filter.

**M365WorkloadCollection.list() status parameter:**
Repeatable (OR logic) reverse-mapping of a single raw field via `_STATUS_TO_API_BACKUP_STATUS`;
see `list()`'s own docstring for the filter contract. M365 workloads have no verification
concept, so there is no `verify_status` parameter here.

**Special behavior of M365WorkloadCollection.delete():**
- Error detection uses the `errors` array in the response body.
- A non-existent workload responds with an empty `errors` array (raw `success: true`); see `delete()`'s own docstring for the resulting behavior.
- `tenantId` in the request body is not validated server-side; matching is by namespace + uid only.

---

### M365AutoBackupRuleCollection

See `packages/synology-apm-sdk/README.md`'s "M365 Auto-Backup Rules" section for the User
Services / Collaboration Services category split. Mechanics not covered there:

**User Services rules:** `create()`'s request body wraps `tenantId`/`backupPlanId` in a
`ruleSpec` object; `update()`'s body has `backupPlanId` at the top level instead — the two
request shapes are not symmetric (see `m365_auto_backup_rule.py` for the exact bodies).

**Collaboration Services settings:** all four service types are sent together on every
`update_collab_settings()` call (see the method's own docstring). The API field name for
SharePoint Sites is `generalSiteSetting`, not a name matching "SharePoint".

---

### GWSWorkloadCollection

**get() / get_by_name():**

| Method | Notes |
|------|------|
| `get(workload_id, namespace, domain, workload_type)` | Queries by the `(namespace, workload_id)` primary key; `domain` and `workload_type` are both required |
| `get_by_name(name, domain, workload_type, is_retired=False)` | `domain` and `workload_type` are both required; matches against display name, email (Mail/Drive/Contact/Calendar), or drive name (Shared Drive) |

**namespace parameter:** see [Namespace filter resolution](#collection-behavior-rules) above
(identical mechanics to `M365WorkloadCollection`).

**Number of API calls for list():** `workload_type` is required and has no "all subtypes"
wildcard, so enumerating every GWS workload for a domain requires one `list()` call per
`GWSWorkloadType` member. The request's filter carries the domain string in `primKey`, not a
`domainId` field. Add 1 more call whenever `namespace` is given, regardless of whether it
resolves (see above).

**backup_now() / cancel_backup()** send `{"nsUidPairs": [...]}` only; neither call adds a
top-level domain field.

**change_plan() / retire()** (`_put_plan_change()`): the request body carries the domain under
a `"domain"` field.

**delete()**: the request body carries `primKey` (the domain string) plus
`isFromUnmanagedWorkload` and `nsUidPairs`, mirroring the batch-delete shape used by
`backup_now()`/`cancel_backup()`. If the workload no longer exists, the call succeeds silently
(empty `errors` array on the response); see `delete()`'s own docstring for the resulting
behavior.

**`GWSWorkload.backup_user` / `.is_anomaly`**: parsed from `backupUserInfo`/`isAnomaly`, which
are siblings of `entityMeta` on the raw workload item, not nested inside it. Both are set only
for `SHARED_DRIVE` workloads (`None`/`False` for the other four subtypes).

---

### GWSAutoBackupRuleCollection

See `packages/synology-apm-sdk/README.md`'s "GWS Auto-Backup Rules" section for the User
Services / Collaboration Services category split. Mechanics not covered there:

**User Services rules:** group-ID bindings are per-subtype (`mailGroupIds` /
`calendarGroupIds` / `contactGroupIds` / `driveGroupIds`). `create()`'s request body wraps
`domain`/`backupPlanId` in a `ruleSpec` object; `update()`'s body has `backupPlanId` at the
top level instead.

**Collaboration Services settings:** `update_collab_settings()` sends a single
`teamDriveSetting` object — only one service category.

**`update_protected_account_types()`**: the domain's "include unlicensed
accounts" / "include archived accounts" filter lives in `globalConfig.autoAddUnlicensed` /
`globalConfig.autoAddArchived`, part of the same `globalConfig` object the domain edit endpoint
replaces wholesale. Since the API treats a partial `globalConfig` as an explicit reset of every
field it omits (proto3 has no "unset" vs. "false"/"empty" distinction on this endpoint), this
method reads the domain's current `globalConfig` first, merges in the two target fields, and
writes the merged object back — a partial-object `PUT` would silently clear unrelated settings
(e.g. `selfServiceMyDrive`, `defaultArchivePlanId`).

---

### BackupActivityCollection / RestoreActivityCollection

**The history parameter of list():**
- `history=False` (default): in-progress tasks
- `history=True`: completed historical records

**The status parameter of list() is a one-to-many filter:**
A single SDK status enum value can expand to multiple API filter values (OR logic) — see
`_BACKUP_STATUS_TO_API` / `_RESTORE_STATUS_TO_API` in `collections/_activity_parsers.py`
(also indexed in [Enum Definitions](#enum-definitions-and-api-string-mapping) above). The two
restore ready statuses have their own rule; see the status and restore_types paragraph below.

**workload parameter (both collections):**
`workload: Workload | None` restricts results to a single workload's activities. Both
identifying params are required together — supplying either one alone has no filtering
effect, so the SDK only ever sends them as a pair. See `RestoreActivityCollection.list()`'s
own docstring for the no-matching-workload behavior, and `_RESTORE_WORKLOAD_NOT_FOUND_CODE`'s
comment (`activities.py`) for the underlying error-code handling.

**Sandbox-ready status depends on the restore type:** raw `restoreStatus=READY_FOR_MIGRATE` is
reported both by sandboxes (`restoreType=INSTANT_RESTORE_AEM` → `RestoreType.INSTANT_SANDBOX`)
and by other instant restores (VMware instant restore when migration is possible; after a
canceled migration). `_parse_restore_activity` maps it to `RestoreActivityStatus.SANDBOX_READY`
on sandbox activities and to `READY_FOR_MIGRATE` on every other type; `_RESTORE_STATUS_MAP`
itself stays type-agnostic. Sandbox rules: [MachineSandboxCollection](#machinesandboxcollection).

**RestoreActivityCollection.list() status and restore_types filters** (`_restore_type_filter`):
`status` → repeated `restoreStatus`, `restore_types` → repeated `restoreType` (through
`_RESTORE_TYPE_TO_API`, the reverse of `_RESTORE_TYPE_MAP`); the server ANDs the two, so an
activity must match both. `RestoreType.UNKNOWN` has no raw value and raises `ValueError`.

A sandbox is itself ready for migration, so `READY_FOR_MIGRATE` is the wider status filter and
matches sandboxes too. `SANDBOX_READY` without `READY_FOR_MIGRATE` needs the sandbox type in the
same `restoreType` parameter, which then narrows every requested status to sandboxes:

| `restore_types` | `SANDBOX_READY` without `READY_FOR_MIGRATE` |
|---|---|
| omitted | Allowed only as the only status; `restoreType=INSTANT_RESTORE_AEM` is added |
| `[INSTANT_SANDBOX]` | Allowed with any other status: the caller already asked for sandboxes only |
| anything else | `ValueError` |

Filtering a page client-side instead would break `total` and paging. Excluding sandboxes from a
`READY_FOR_MIGRATE` filter is not offered: the server has no `restoreType` exclusion, and
listing every other type would silently drop types the SDK doesn't know.

**get(activity_id, include_logs=True) (both collections):** pages through the listed
activities (ongoing, then history) and compares each raw `uid` before parsing, so only the
match is parsed (`_find_by_id`). `_with_details()` then completes it: the detail log for a
restore activity; the activity detail (data sizes, backup scope) and the detail log for a backup
activity. `include_logs=False` skips the log request; a backup activity still gets its detail.

**RestoreActivityCollection.list() filter support:**
`/api/v2/activity/restore/activities` supports `status`,
`workload`, `restore_types`, `since`/`until`, `keyword`, `history`, `limit`, and `offset` — unlike
`BackupActivityCollection.list()`, it does not accept `machine_types`, `m365_types`,
`gws_types`, or a backup-server `namespace` list.

**BackupActivityCollection.list()'s `machine_types`/`m365_types`/`gws_types` filters:**
map to two different raw API query params — `machine_types` → repeated `categoryService`
values (`_MACHINE_TYPE_TO_CATEGORY_SERVICE`); `m365_types`/`gws_types` → repeated
`saasServiceType` values (`_M365_TYPE_TO_SAAS_SERVICE`/`_GWS_TYPE_TO_SAAS_SERVICE`) — M365 and
GWS share the one `saasServiceType` param since both are SaaS-category filters. All three
dicts live in `collections/_activity_parsers.py`.

**BackupActivityCollection.cancel() / RestoreActivityCollection.cancel()** send differently-shaped
request bodies keyed on different identifiers — see each method's own docstring and inline
comments in `activities.py` for the exact shapes. `RestoreActivityCollection`'s body converts
`ActivityWorkloadType` via `_SUBTYPE_TO_CANCEL_TYPE` (`collections/_activity_parsers.py`).

**RestoreActivity detail fields parsed from spec:**
- `sandbox_vm_id`: `spec.machineInfo.vmmGuestId`, sandbox activities only. Internal: used by the
  sandbox console methods and left out of `to_dict()`, like `execution_id`.
- `restore_from_info`: `LocationInfo` built from `spec.restoreFromInfo` — see
  `_parse_restore_from_info`'s own docstring (`collections/_activity_parsers.py`) for the
  field mapping and the None-vs-empty condition.
- `destination_inventory`: field-mapping details (which `additionalInfo` keys populate which
  `Hypervisor` fields) are in `_parse_destination_inventory`'s own docstring
  (`collections/_activity_parsers.py`); the mutual-exclusivity note with `destination_path` is
  on `RestoreActivity`'s own class docstring.

---

### LogCollection

All methods require passing a `BackupServer` object; the SDK takes its `namespace` and adds it to the `x-syno-tunnel-route` header,
routing the request to the specified backup server via the gateway tunnel (see `LogCollection`'s own class docstring for the appliance-only constraint).

---

### ExchangeExportCollection / GroupExportCollection

**Internal steps of start():** fetches the mailbox root folder ID via `_fetch_root_folder_id()`
(see its own docstring), then submits the export request.

The `ready_to_download`/download-flow branching and the `PREPARING`-state check are documented
on `start()` and `get_download_url_by_activity()` respectively — see their docstrings.

**`M365ExportActivity.version_timestamp`**: the timestamp of the backup version used for the
export (`spec.versionTimestamp`); `None` if not provided by the API.

---

### BackupServerCollection

**Matching logic of get_by_name():**

See `get_by_name()`'s own docstring for the matching order.

**Query behavior for tiering_plan_name / tiering_plan_destination:**

`BackupServer.tiering_plan_name` and `tiering_plan_destination` (`LocationInfo | None`) are
resolved via the internal bulk-fetch helper `_get_plans_bulk()` (`collections/tiering_plans.py`)
for `list()`, or an on-demand query for `get()` (see that method's own docstring) — see
`_get_plans_bulk()`'s own docstring for why it bypasses the public `TieringPlanCollection.get()`.

**Computation of `BackupServer.tiering_status`:**

See [Shared Backup-Copy / Tiering Status Computation](#shared-backup-copy--tiering-status-computation) in Enum Definitions.

---

### Common Patterns Across Plan Collections

`ProtectionPlanCollection`, `RetirementPlanCollection`, and `TieringPlanCollection` share three
behaviors, documented once here rather than repeated in each section below:

- **`create()` / `update()` always return via `get()`:** POST/PUT responses return a minimal
  body (just the plan ID, not the full plan); both methods call `get(plan_id)` afterward to
  return a fully-populated model.
- **`delete()` maps an in-use errorCode to `PlanInUseError`:** each collection recognizes its
  own errorCode(s) and sets the corresponding flag; any other API error is re-raised unchanged.

  | Collection | errorCode | Resulting flag |
  |---|---|---|
  | `ProtectionPlanCollection` | `4019` (assigned to workloads) | `has_workloads=True` |
  | `ProtectionPlanCollection` | `4017` (referenced by a server template) | `has_server_template=True` |
  | `RetirementPlanCollection` | `4019` (assigned to workloads) | `has_workloads=True` |
  | `TieringPlanCollection` | `4029` (assigned to backup servers) | `has_backup_servers=True` |

  The two `ProtectionPlanCollection` codes are independent and may both be present at once,
  setting both flags.
- **`run_schedule_by_controller_time` field:** `True` when the API response's `spec` object
  contains a `controllerUtcOffset` key (regardless of its value), `False` when absent. The field
  name and the API field name are unrelated — the key's presence is the only signal.

---

### ProtectionPlanCollection

See `ProtectionPlanCollection`'s own class docstring for how it's accessed across the four
facades (`APMClient.plans`, `.machine.plans`, `.m365.plans`, `.gws.plans`).

**Resolution behavior of `backup_copy_policy`:**

`ProtectionPlan.backup_copy_policy` (`BackupCopyPolicy | None`) is populated when Backup Copy is enabled and the destination can be resolved; otherwise `None`. The resolution method differs depending on the destination type:

- **APPLIANCE (backup server)**: issues a single backup server list request to retrieve all servers, matching by **namespace**. If the namespace is not found in the list, `backup_copy_policy` is set to `None` for that plan; a failed backup server list query propagates as an exception.
  > **Note:** `spec.backupCopy.destination` returned by the plan API is the backup server's **namespace**, not its `backup_server_id`, so it cannot be used to query a single server directly.
- **Remote Storage (external storage)**: resolved via `_fetch_remote_storage_location()` / `_build_remote_location_cache()` in `collections/_shared.py` — the same helpers [TieringPlanCollection](#tieringplancollection)'s `destination` field uses (see below). A dangling destination reference or an empty `displayName` resolves to `None`; any other query failure propagates as an exception.

`list()` performs a batch query for all unique destinations on the page; `get(plan_id)` / `get_by_name(name)` resolves the destination only for the single matched plan.

**DEVICE 6-task array (MachinePlanCreateRequest.tasks):**

A DEVICE protection plan always has exactly 6 mandatory `(workload_type, os_type)` pairs in its task array:
`(PC, WINDOWS)`, `(PC, MAC)`, `(PS, WINDOWS)`, `(PS, LINUX)`, `(FS, NONE)`, `(VM, NONE)`.
When `MachinePlanCreateRequest.tasks` is `None`, `create()` and `update()` auto-generate these 6 default entries with `ENTIRE_MACHINE` scope and `use_main_schedule=True`; a custom `tasks` tuple is validated against the rules listed under "Request object validation" below.

**MANUAL schedule encoding:**

`ScheduleFrequency.MANUAL` maps to `scheduleType: "NONE"` in both `mainSchedule` and each
task's `schedule` dict. For an explicit custom task schedule (`use_main_schedule=False`)
with no `event_trigger`, the `logOff`/`screenLock`/`startup` flags are `false` — but a PC
task inheriting the main schedule (`use_main_schedule=True`, including the 6 auto-generated
default tasks) always sends these three flags as `true`, regardless of the main schedule's
frequency (see the "inherit main schedule" comment in `_build_task_schedule_dict()`,
`_protection_plan_builders.py`).

**PC task backup mode:** the four `MachineTaskSchedule` combinations are documented on its own
class docstring. `event_trigger` is only valid for PC tasks (see "Request object validation"
below for the full validated-conditions list).

**Request object validation (`MachinePlanCreateRequest`, `M365PlanCreateRequest`, `GWSPlanCreateRequest`):**

All three request types validate field invariants in `__post_init__`, so `ValueError` is raised at construction time — before any API call. The full set of validated conditions is listed in `MachinePlanCreateRequest`'s `Raises:` docstring; `M365PlanCreateRequest`/`GWSPlanCreateRequest` share the same non-`tasks` validations (no `vm_config`/`pc_config`/etc. to validate). When `tasks is None`, APM generates default tasks and all of the `tasks`-specific cross-task validations are skipped.

**`get()` vs `list()` — config fields:**

`get(plan_id)` returns the full plan spec including `configDevice` (tasks, vm_config, pc_config, ps_config, db_config, backup_window). `list()` does not include `configDevice` in its response; the config fields are always `None` on plans returned by `list()`. Always call `get(plan_id)` when config field values are needed.

**Computation of `ProtectionPlan.backup_copy_status`:**

`backup_copy_status` (`PlanBackupCopyStatus | None`) is parsed from `backupCopyStatus` in the plan API response — see [Shared Backup-Copy / Tiering Status Computation](#shared-backup-copy--tiering-status-computation) in Enum Definitions for the full mapping.

---

### RetirementPlanCollection

**Retention encoding for `RetirementPlanCreateRequest`:**

- `retention_days=None` → `keepAll: true` in the request body
- `retention_days=N` → `keepAll: false, keepDays: N`, with `keepVersions: 1` when `keep_latest_version=True`, else `keepVersions: 0`
- `retention_days=None` → `keepVersions` is always `0`, regardless of `keep_latest_version` (with `keepAll: true` every version is kept anyway)

---

### TieringPlanCollection

**Destination query behavior:**

`TieringPlan.destination` (`LocationInfo | None`) is resolved via the same `_fetch_remote_storage_location()` / `_build_remote_location_cache()` helpers as [ProtectionPlanCollection](#protectionplancollection)'s Remote Storage `backup_copy_policy` resolution (see above): `list()` batches all unique destination UUIDs on the page in parallel; `get(plan_id)` / `get_by_name(name)` resolve just the one destination per call.

**Computation of `TieringPlan.tiering_status` (and `BackupServer.tiering_status`):**

See [Shared Backup-Copy / Tiering Status Computation](#shared-backup-copy--tiering-status-computation) in Enum Definitions.

**`destinationType` mapping for `TieringPlanCreateRequest.destination`:**

The `RemoteStorage.storage_type` is converted to the API `destinationType` string via `_STORAGE_TYPE_TO_DEST_TYPE` in `collections/_shared.py` (shared with `_protection_plan_builders.py`). Note the many-to-one merges: the China variants map to the same `destinationType` as their global counterparts (`AMAZON_S3` / `AMAZON_S3_CHINA` → `"AWS_S3"`, `AZURE_BLOB` / `AZURE_BLOB_CHINA` → `"AZURE_BLOB"`).

---

### RemoteStorageCollection

`add()`'s type-specific request routing, catalog check/relink flow, and `trust_self_signed`
behavior are documented in full on `add()`'s own docstring; `update()`'s immutable fields and
`trust_self_signed` scope are on `update()`'s own docstring. Both re-fetch via `get()` after the
write to return the refreshed model state.

**update() — minimal body per type:** see `_build_update_body()`'s inline comments
(`collections/remote_storages.py`) for the per-type body shape.

**Azure Blob Storage (`AZURE_BLOB` / `AZURE_BLOB_CHINA`) — existing application only:** `add()`
and `update()` support only registering/re-authenticating with the credentials of an *existing*
Microsoft Entra application (service principal) — `AzureBlobStorageAddRequest`/
`AzureBlobChinaStorageAddRequest` and `AzureBlobStorageUpdateRequest`. Creating a new application
(the backend's device-login/OAuth flow under `/api/v1/external_storage/azure/*`) is not modeled by
this SDK. The create/update request bodies nest Azure-specific fields under a single `azureInfo`
object (`accountName`, `containerName`, `tenantId`, `clientId`, `secret`) instead of the flat
`accessKey`/`secretKey` used by every other type — `create`'s top-level `accessKey`/`secretKey` are
still sent as empty strings for schema consistency, but there is no `endpoint` key at all (unlike
other endpoint-free types, which send `endpoint: ""`). The catalog pre-flight
(`POST /api/v1/storage_connection/remote`) also uses a distinct shape for Azure —
`{storageType, vaultName, supportVirtualHost: true, azureInfo}` — rather than the generic
`accessKey`/`secretKey`/`endpoint`/`certificate` shape used by other types. `update()` always
resends the immutable `accountName`/`containerName` (read from the existing `RemoteStorage`, not
the caller's request) alongside the caller-supplied `tenantId`/`clientId`/`secret`. The read model
exposes the storage account name and application ID as `RemoteStorage.account_name` /
`RemoteStorage.client_id` (parsed from `azureAccountName`/`azureEntraAppId`), populated only for
Azure storage types.

---

### SaasCollection

**M365 vs. GWS parsing asymmetry:** `list()`'s raw response nests each category under a
different key with different field names — M365 entries nest under `tenant` and use
`tenantId`/`tenantName`/`tenantMail`; GWS entries nest under `gwDomain` and use
`domain`/`domainName`/`adminEmail` (`saas.py`). `_parse_m365_tenant()` returns `M365TenantInfo`;
`_parse_gws_domain()` returns `GWSDomainInfo` — see "Naming decision: SaaS application
hierarchy" above for why these are two distinct types rather than one shared model.
`get_m365_tenant()` always sets `protected_data_bytes=0` (see its own docstring for why).
`get_gws_domain()` sets `protected_data_bytes` from `dataUsageInfo.dataUsage`, present on the
domain-detail response.

**`list()`'s `total` field:** see the comment next to the coercion in `saas.py`.

---

### APMClient.get_site_info()

**Scan condition:** see `_find_first()`'s docstring (`collections/backup_servers.py`), which
`_find_management_servers()` uses.

---

## Collection Map

How each collection hangs off `APMClient`. Method signatures are deliberately **not** listed
here — they live in the source docstrings and the Sphinx API docs, which are always current.

| Access path | Collection | Purpose |
|---|---|---|
| `apm.machine` | `MachineCollection` | Machine domain entry point (`.workloads`, `.plans`, `.sandboxes`) |
| `apm.machine.workloads` | `MachineWorkloadCollection` | PC/PS/VM/FS workloads: listing, versions, backup/cancel, retire/change-plan, file-server registration |
| `apm.machine.plans` | `MachinePlanCollection` | Machine protection plan CRUD |
| `apm.machine.sandboxes` | `MachineSandboxCollection` | PS/VM instant restore to the built-in sandbox: default settings, resources, validation, start, screenshot, console link, VNC bridge |
| `apm.m365` | `M365Collection` | M365 domain entry point (`.workloads`, `.plans`, `.exchange_export`, `.group_export`, `.auto_backup_rules`) |
| `apm.m365.workloads` | `M365WorkloadCollection` | M365 workloads per service subtype: listing, versions, backup/cancel, retire/change-plan, delete |
| `apm.m365.plans` | `M365PlanCollection` | M365 protection plan CRUD |
| `apm.m365.exchange_export` / `apm.m365.group_export` | `ExchangeExportCollection` / `GroupExportCollection` | Mailbox PST export: start, poll, download URL, cancel |
| `apm.m365.auto_backup_rules` | `M365AutoBackupRuleCollection` | Per-tenant auto-backup rules and collaboration-service settings |
| `apm.gws` | `GWSCollection` | GWS domain entry point (`.workloads`, `.plans`, `.auto_backup_rules`; no export) |
| `apm.gws.workloads` | `GWSWorkloadCollection` | GWS workloads per service subtype: listing, versions, backup/cancel, retire/change-plan, delete |
| `apm.gws.plans` | `GWSPlanCollection` | GWS protection plan CRUD |
| `apm.gws.auto_backup_rules` | `GWSAutoBackupRuleCollection` | Per-domain auto-backup rules, collaboration-service settings, and protected-account-type filter |
| `apm.plans` | `ProtectionPlanCollection` | Cross-category protection plan reads (+ create/delete) |
| `apm.retirement_plans` | `RetirementPlanCollection` | Retirement plan CRUD |
| `apm.tiering_plans` | `TieringPlanCollection` | Tiering plan CRUD |
| `apm.activities` | `ActivityCollection` | Activity entry point (`.backup`, `.restore`) |
| `apm.activities.backup` / `apm.activities.restore` | `BackupActivityCollection` / `RestoreActivityCollection` | Activity listing, detail, cancel |
| `apm.backup_servers` | `BackupServerCollection` | Backup server listing/lookup, tiering-plan assignment |
| `apm.remote_storages` | `RemoteStorageCollection` | Remote storage (external vault) CRUD |
| `apm.hypervisors` | `HypervisorCollection` | Hypervisor inventory servers |
| `apm.logs` | `LogCollection` | Server-scoped activity/drive/connection/system logs |
| `apm.saas` | `SaasCollection` | SaaS application listing, M365 tenant / GWS domain lookup |
| `apm.get_site_info()` / `apm.download_file()` / `apm.my_server` | `APMClient` directly | Site overview, authenticated file download, connected server |

---

*For detailed API documentation, see the Sphinx API docs (`make docs`).*
