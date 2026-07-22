# APM Python SDK — Design Contract

> Corresponding product: Synology ActiveProtect Manager 1.2

**Purpose of this document**: A design contract document for implementers (human developers or AI sessions).
It records information that docstrings are not allowed to contain: API string mappings, non-obvious behavior rules, design rationale.
For the SDK's full public interface (signatures, Attributes, Args/Returns/Raises), see the Sphinx API docs (`make docs`).

---

## Table of Contents

- [Design Conventions](#design-conventions)
- [Package Structure](#package-structure)
- [Exception Hierarchy](#exception-hierarchy)
- [Authentication Flow](#authentication-flow)
- [Enum Definitions and API String Mapping](#enum-definitions-and-api-string-mapping)
- [Type System Notes](#type-system-notes)
- [Collection Behavior Rules](#collection-behavior-rules)
   - [MachineWorkloadCollection](#machineworkloadcollection)
   - [M365WorkloadCollection](#m365workloadcollection)
   - [M365AutoBackupRuleCollection](#m365autobackuprulecollection)
   - [BackupActivityCollection / RestoreActivityCollection](#backupactivitycollection--restoreactivitycollection)
   - [LogCollection](#logcollection)
   - [ExchangeExportCollection / GroupExportCollection](#exchangeexportcollection--groupexportcollection)
   - [BackupServerCollection](#backupservercollection)
   - [ProtectionPlanCollection](#protectionplancollection)
   - [RetirementPlanCollection](#retirementplancollection)
   - [TieringPlanCollection](#tieringplancollection)
   - [RemoteStorageCollection](#remotestoragecollection)
   - [SaasCollection](#saascollection)
   - [APMClient.get_site_info()](#apmclientget_site_info)
- [Collection Map](#collection-map)

---

## Design Conventions

- Public methods must have type annotations and docstrings; use `async/await` (no synchronous blocking calls allowed); connections are managed via `async with APMClient(...) as apm:`; attribute access uses `@property` rather than getters.
- Exceptions must always use the custom hierarchy defined in `exceptions.py` (see "Exception Hierarchy"); never raise a generic `Exception`.
- Single-resource lookups (`get()` and equivalents) wrap their primary API call in `_shared._not_found_as(resource_type, resource_id, ...)` so every not-found error carries the caller's resource identity, regardless of which response shape signaled it (HTTP 404, an error detail code via `detail_code=`, or an empty 200 body — for the last, raise a placeholder `ResourceNotFoundError` inside the block and the context manager rewrites it). Wrap only the primary lookup, never nested lookups such as location-cache building.
- Docstrings must follow CLAUDE.md's "API Abstraction in User-Facing Text" convention (no REST API paths, HTTP methods/status codes, raw API field names, or descriptions of specific underlying-API mechanisms); `_http.py` and private helpers prefixed with an underscore are exempt and may reference raw API details in code/comments where needed.
- When adding a new public type (class / enum / dataclass), remember to also add it to `__all__` in `synology_apm/sdk/__init__.py`.
- All model dataclasses (`models/*.py`) are `@dataclass(frozen=True)`: API responses are parsed into immutable value objects, never mutated in place.
- All response model dataclasses expose a `to_dict()` method returning a JSON-safe dict. Most fields are formulaic (enum → `.value`, datetime/date/time → ISO 8601, nested `to_dict()`-bearing objects → recursive call, list/tuple → element-wise) and should call `models/_shared.py`'s `auto_to_dict(self, exclude=..., extra=...)` rather than listing every field by hand: `exclude` drops a field that's being replaced, `extra` supplies computed `@property` values or restructured/renamed output (e.g. a field needing a non-formulaic conversion, such as a `timedelta` reduced to whole seconds). `dataclasses.fields()` resolves against an instance's actual runtime type, so a base class's `to_dict()` (e.g. `auto_to_dict(self)`) already serializes a subclass instance's full field set — a dataclass subclass adding only plain fields (no extra `exclude`/`extra` of its own) needs **no** `to_dict()` override at all; it inherits the base method as-is. Only define an override when the subclass needs its own `exclude`/`extra` beyond the base class's, and in that case call `auto_to_dict(self, ...)` once with the *combined* `exclude`/`extra` — never `{**super().to_dict(), **auto_to_dict(self, ...)}`, which redundantly re-serializes every subclass field (and re-invokes nested `to_dict()` calls) a second time. `*Request` input types (and other write-only helpers such as `BackupCopyConfig`) are exempt — they are never returned by the API, so there is nothing to serialize. This is the single source of truth for semantic JSON serialization; CLI and MCP both build their output from it rather than each maintaining a separate field-mapping (see "Three-Layer Responsibility Separation" in the repository `CLAUDE.md`).
- Every `APMError` subclass also exposes a `to_dict()` method returning a JSON-safe dict of its semantic fields (this is the same "SDK owns semantic serialization" principle as response models above, extended to exceptions). Exceptions are not dataclasses (`Exception.__init__` isn't compatible with a dataclass-generated `__init__`), so `to_dict()` is hand-written per class rather than routed through `auto_to_dict()`: each override calls `{**super().to_dict(), ...}` to layer its own fields on top of the base class's, mirroring how `_ResourceError.__init__` layers its constructor args on top of `APMError.__init__`. A subclass adding no fields (e.g. most `_ResourceError` subclasses) needs no override, same as with model dataclasses. CLI and MCP each still own their own exception → user-facing error code/message mapping (which fields to expose as which label is presentation, not SDK data); only the field *contents* come from `to_dict()`.
- When an API field name differs from the SDK dataclass field name, perform the conversion inside the collection parser (`_parse_*` functions) without changing the SDK's public interface. Magic values (such as `"-1"` / `"0"`) are always converted to `None` via `_parse_data_sizes()`.
- The `host` parameter of the `APMClient` constructor only accepts a hostname or `host:port` (without scheme); the SDK automatically prepends `https://` internally.
- Write operations (`backup_now`, `cancel_backup`, `change_plan`, `retire`, etc.) return `None`, not a pollable Job object — this matches how the APM API itself models long-running operations (fire-and-forget); progress and history are queried separately via `apm.activities`.

---

## Package Structure

The object model follows a bounded `domain → collection → workload → version → location`
hierarchy (4 levels below `APMClient`) — deep enough to mirror APM's actual resource
relationships (a Workload has Versions, a Version has Locations), shallow enough to stay
easy to navigate.

Two naming conventions let most files go uncommented below: every `collections/*.py` file
(except the private/entry-point ones called out explicitly) exports exactly one
`<Noun>Collection` class named for the file, e.g. `hypervisors.py` → `HypervisorCollection`;
a handful of `models/*.py` files each define a single, file-obvious model class with nothing
further to say (open the file to confirm). Only multi-type files, private helpers, entry
points, and exceptions to these conventions are annotated.

```
synology_apm/sdk/
├── __init__.py              # Public API: APMClient, exceptions, enums, models, collections
├── client.py                # APMClient main entry point
├── exceptions.py            # All custom exception classes
├── _http.py                 # Low-level HTTP wrapper (private)
├── enums.py                 # All Enum definitions
├── config.py                # Config file read/write, keyring credential storage, resolve_connection() (shared with CLI/MCP)
├── models/
│   ├── _shared.py           # Shared model serialization helpers (private): auto_to_dict()
│   ├── workload.py          # Workload base, MachineWorkload, M365Workload + M365*Info, FileServer* config/request models
│   ├── location.py
│   ├── version.py           # WorkloadVersion, VersionLocation
│   ├── protection_plan.py   # ProtectionPlan + its policy/schedule/retention/backup-copy/task-config models and create requests
│   ├── retirement_plan.py   # RetirementPlan + retention policy and create request
│   ├── tiering_plan.py      # TieringPlan, TieringStatus + create request
│   ├── activity.py          # Activity, BackupActivity, RestoreActivity, ActivityLogEntry, M365ExportActivity
│   ├── backup_server.py
│   ├── hypervisor.py
│   ├── log.py               # APMActivityLog, DriveLog, ConnectionLog, SystemLog
│   ├── remote_storage.py    # RemoteStorage + per-type *StorageAddRequest/update/add-result models
│   ├── m365_auto_backup_rule.py  # M365AutoBackupRule, M365CollabServiceSetting, M365AutoBackupRuleListResult
│   ├── saas.py
│   └── system.py            # SiteInfo, SiteStorageStats, WorkloadTypeStat, WorkloadUsageSummary
└── collections/
    ├── _shared.py           # Shared collection helpers (private): pagination, timestamp/status parsing, version mixin; also defines the public ListResult pagination envelope
    ├── machine.py           # MachineCollection (entry point) + MachineWorkloadCollection
    ├── m365.py              # M365Collection (entry point) + M365WorkloadCollection
    ├── m365_auto_backup_rule.py
    ├── m365_mail_export.py  # ExchangeExportCollection, GroupExportCollection, M365ExportStartResult
    ├── protection_plans.py  # ProtectionPlanCollection, MachinePlanCollection, M365PlanCollection
    ├── _protection_plan_builders.py  # Protection Plan request-body builders (private)
    ├── _protection_plan_parsers.py   # Protection Plan response parsers + API string maps (private)
    ├── retirement_plans.py
    ├── tiering_plans.py
    ├── saas.py
    ├── activities.py        # ActivityCollection, BackupActivityCollection, RestoreActivityCollection
    ├── _activity_parsers.py # Activity response parsers + API string maps (private)
    ├── backup_servers.py
    ├── hypervisors.py
    ├── logs.py
    ├── system.py            # SystemCollection — internal helper behind get_site_info(); not exported
    └── remote_storages.py
```

The per-file comments name the primary types only; the authoritative list of public types is
`__all__` in `synology_apm/sdk/__init__.py` — every SDK-public type is exported there.
Consumers must always use `from synology_apm.sdk import ...` and must not import private submodule paths directly (e.g. `synology_apm.sdk.enums`, `synology_apm.sdk.models.workload`).

---

## Exception Hierarchy

The hierarchy is defined in `exceptions.py` — every class carries a docstring with its
attributes and trigger conditions (→ Sphinx API docs). This section records only what the
class list itself does not convey:

- `_ResourceError` is the shared base for every exception that carries `.resource_type` /
  `.resource_id`: `ResourceNotFoundError`, `InvalidOperationError`, `DuplicateWorkloadError`,
  `PlanNameConflictError`, `PlanInUseError`, `RemoteStorageConflictError`,
  `RemoteStorageInUseError`, `RemoteStorageEncryptionMismatchError`. Tests must assert both
  fields via the shared `assert_resource_error` helper (see CLAUDE.md "Exception attribute
  conventions").
- `ResourceNotReadyError` and `RemoteStorageUnmanagedCatalogError` extend bare `APMError` —
  they have **no** `.resource_type` / `.resource_id` (`RemoteStorageUnmanagedCatalogError`
  carries `vault_name` / `catalog_count` instead).
- `KeyringUnavailableError` extends `RuntimeError` directly, **not** `APMError` — it signals
  a local OS-keyring failure (raised by `config.py`'s keyring helpers / `resolve_connection()`),
  not a REST API error, and carries no `error_code` / `response_body`.
- API errorCode → exception mappings are operation-specific and documented per collection in
  [Collection Behavior Rules](#collection-behavior-rules) (e.g. 4013 → `PlanNameConflictError`,
  4017/4019/4029 → `PlanInUseError`, 3004/3014 → the RemoteStorage conflict/in-use errors,
  3006 → `RemoteStorageEncryptionMismatchError`, 7001 → `DuplicateWorkloadError`).
- `str(exc)` automatically appends formatted JSON when `response_body` has a value;
  `exc.message` always contains only a brief description and is unaffected by `response_body`.

### Trigger Conditions for NotManagementServerError

Automatically validated by the SDK during connect(), under two trigger conditions:
- `GET /api/v1/infra/backup_server/me` returns 404 → the host is not an APM appliance
- The host is an APM backup server or Secondary Management Server, not the Primary Management Server

---

## Authentication Flow

The SDK authenticates through the legacy Synology WebAPI login endpoint (`/webapi/entry.cgi`); as of APM 1.2, there is no independent login endpoint of its own.

1. `connect()` calls `GET /webapi/entry.cgi?api=SYNO.API.Auth&version=6&method=login&client=browser&session=webui&enable_syno_token=yes`, which sets the `id` HttpOnly session cookie.
   > **Warning:** Must use **GET + `version=6` + `client=browser`**. `POST + version=7 + format=cookie` returns an empty `id=` value, causing every subsequent business API call to respond with `HTTP 401`.
2. `connect()` then calls `GET /api/v1/infra/backup_server/me` to confirm the host is an APM appliance and to resolve `my_server` (see "Trigger Conditions for NotManagementServerError" above).
3. All business API requests rely on the `id` cookie. When the session expires, APM responds with `HTTP 401` (`{"message": "auth cookie failed"}`); the SDK re-authenticates once via step 1 and retries, raising `AuthenticationError` only if that also fails.
4. `disconnect()` calls `GET /api/v1/preference/logout`.

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
| `BackupActivityStatus` / `RestoreActivityStatus` (parse and filter directions) | `collections/_activity_parsers.py` — `_BACKUP_STATUS_MAP`, `_RESTORE_STATUS_MAP`, `_BACKUP_STATUS_TO_API`, `_RESTORE_STATUS_TO_API` |
| `RestoreType`, `ActivityWorkloadType`, cancel type strings | `collections/_activity_parsers.py` — `_RESTORE_TYPE_MAP`, `_RAW_TO_SUBTYPE`, `_SUBTYPE_TO_CANCEL_TYPE` |
| `M365WorkloadType` | `collections/m365.py` — `_TYPE_TO_API_TYPE` / `_API_TYPE_TO_TYPE` |
| `MachineWorkloadType`, `VersionStatus` | `collections/_shared.py` — `_MACHINE_WORKLOAD_TYPE_MAP`, `_VERSION_STATUS_MAP` |
| `VerifyStatus` (parse and filter directions) | `enums.py` — `_VERIFY_STATUS_MAP`; `collections/machine.py` — `_VERIFY_STATUS_TO_API` |
| `WorkloadStatus` filter direction (`list()` `status` parameter; parse direction is branching logic — see "WorkloadStatus" below) | `collections/machine.py` — `_STATUS_TO_JOB_STATUS`, `_STATUS_TO_LVR`; `collections/m365.py` — `_STATUS_TO_API_BACKUP_STATUS` |
| `VersionCopyStatus`, `CopyReason` | `enums.py` — `_VERSION_COPY_STATUS_MAP`; `collections/_shared.py` — `_COPY_REASON_MAP`, `_COPY_REASON_SKIPPED_MAP`, `_COPY_ERROR_STATUS_MAP` |
| Plan task/db enums (`MachineOsType`, `MachineTaskScope`, `DbActionOnError`, `MssqlLogSetting`, `OracleLogSetting`) | `collections/_protection_plan_parsers.py` — `_OS_TYPE_MAP`, `_SOURCE_TYPE_MAP`, `_DB_ACTION_MAP`, `_MSSQL_LOG_MAP`, `_ORACLE_LOG_MAP` |
| `RemoteStorageType` → plan/tiering `destinationType` | `collections/_shared.py` — `_STORAGE_TYPE_TO_DEST_TYPE` |
| `HypervisorType` | `collections/hypervisors.py` — `_HOST_TYPE_MAP` |
| `M365ExportStatus` | `collections/m365_mail_export.py` — `_EXPORT_STATUS_MAP` |
| `LogLevel` | `collections/_activity_parsers.py` — `_LOG_LEVEL_MAP` |
| `FileServerType` | `collections/machine.py` — `_FS_OS_TYPE_MAP` |
| `BackupScope` | `collections/_activity_parsers.py` — `_BACKUP_SCOPE_MAP` |

### Naming decision: GWS

`GWS` is the SDK's chosen name for the Google Workspace category; the raw APM API uses
`GW` / `gw` / `APPLICATION_GW` / `GW_*` for this category (e.g. `GW_DRIVE`, `GW_MAIL`) —
this is currently the only enum family where the SDK value diverges from the API's own
abbreviation (applies to `WorkloadCategory.GWS`, `ActivityWorkloadType.GWS`,
`WorkloadStatType.GWS`).

### Enums Requiring Parser Computation / Conversion Logic

#### ServerStatus

Computed by the collection parser from **two** API fields, in this precedence order:
1. `spec.syncStatus == "JOINING"` → `SYNCING`; `spec.syncStatus ∈ _SYNC_DISCONNECTED`
   (`"DISCONNECTED"`, `"JOINING_DISCONNECTED"`) → `DISCONNECTED`.
2. Otherwise `status.status` is looked up in `_SERVER_STATUS_MAP` (`"NORMAL"` → `HEALTHY`,
   `"ATTENTION"` → `WARNING`, `"DANGER"` → `CRITICAL`, `"NOTINITIALIZED"` / `"INCOMPATIBLE"` →
   `DISCONNECTED`); an unrecognized `status.status` falls back to `DISCONNECTED`.

#### WorkloadStatus

Derived from the API `jobStatus` (Machine) / `backupStatus` (M365) fields:
- When `BACKING_UP`: PC/PS/VM have a `backup_progress` percentage; FS/M365 have an `items_backed_up` count.
- M365's API `backupStatus` value of `"WARNING"` maps to `PARTIAL`.
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
- For `ProtectionPlan.backup_copy_status`, the outer `VersionCopyStatus` value is computed
  from the plan API's `backupCopyStatus.copyStatus` field using a **separate** mapping — see
  [ProtectionPlanCollection behavior rules](#protectionplancollection) below.

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
| M365 workload response | `uid` |
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
| `ActivityLogEntry` | Execution log of a backup/restore activity (`Activity.log_entries` field) | Populated after `get()` or `get_by_version()`; `None` after `list()` |
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

`MachineWorkload.plan` / `M365Workload.plan` (type `ProtectionPlan | RetirementPlan`) is built
directly from the workload's own response, without an extra request to the plans collections.
Only `plan_id`, `name`, and (for `ProtectionPlan`) `category` are guaranteed to be set; all other
fields — `ProtectionPlan.policy`/`workload_count` and `RetirementPlan.retention`/`workload_count`
— default to `None` on this lightweight reference. To obtain a fully-populated plan, fetch it
separately via `apm.machine.plans`, `apm.m365.plans`, or `apm.retirement_plans` using
`wl.plan.plan_id`.

`Workload.is_retired` and the `ProtectionPlan`/`RetirementPlan` discrimination on `wl.plan` are
derived from the same underlying signal in both `MachineWorkloadCollection` and
`M365WorkloadCollection` parsers (a workload is retired exactly when its assigned plan is a
retirement plan) — the two can never disagree.

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

### Special Fields of WorkloadVersion

- `portal_version_id` (API `spec.versionId`): used by the M365 export/restore API paths
- `snapshot_id` (API `spec.snapshotId`): used by the portal entries:download API
- `execution_id`: the corresponding Activity's executionId, passed to `BackupActivityCollection.get_by_version()`
- `locations` (`list[VersionLocation]`): `lock_version()` / `unlock_version()` use its `namespace` and `location_id`; download/export operations use its `namespace`, `location_id`, and `connection_id` (selected via the `location_id` parameter)
- `copy_status` (`VersionCopyStatus | None`): outer backup copy status (from the version's top-level `copyStatus` API field); `None` for unrecognized values
- `copy_reason` (`CopyReason | None`): resolved detail reason for `SKIPPED`/`RETRY`/`FAILED` states; always `None` when `copy_status` is `COMPLETED`/`NOT_ENABLED`/`WAITING`/`SCHEDULED`/`IN_PROGRESS`

### Parsing Rules for RemoteStorage's usedSpace / remainingSpace

The API fields are of string type; parser rules:
- `""` or field missing → `None` (data unavailable)
- `"0"` → integer `0` (semantically "no space used", different from "data unavailable"; must not be converted to `None`)

---

## Collection Behavior Rules

> All `list()` methods return a `ListResult[T]` (a `NamedTuple` with `items` and `total` fields); callers (including tests) can unpack it positionally like a plain `(items, total)` tuple: `items, _ = await collection.list()`. `total` is `None` when the underlying data source cannot report a reliable count (see each collection method's docstring for which case applies). The one exception is `M365AutoBackupRuleCollection.list()`, which returns an `M365AutoBackupRuleListResult` (see its section below).

### MachineWorkloadCollection

**get() / get_by_name():**

| Method | Number of API calls | Notes |
|------|-----------|------|
| `get(workload_id, namespace)` | 1 (`GET /{id}?namespace={ns}`) | Queries directly by the `(namespace, workload_id)` primary key |
| `get_by_name(name, is_retired=False)` | N (keyword search + exact name match) | `is_retired` determines `filter.protectStatus` |

**Internal flow of lock_version() / unlock_version() (M365WorkloadCollection shares the same `_VersionMixin` logic):**
- Builds the batch lock/unlock request body directly from the passed-in `WorkloadVersion.locations` — `nsUidPairs` is composed of the `namespace` and `location_id` of each `VersionLocation` (the parser has already expanded the API's `versionUids[]` into one `VersionLocation` per UID during parsing)
- Before calling, a `WorkloadVersion` with complete `locations` must first be obtained via `list_versions()` / `get_latest_version()` / `get_version()`

**Special behavior of add_file_server():**
- An empty `login_password` raises `ValueError` at `FileServerAddRequest` construction time (`__post_init__`), before any API call — the API accepts `""` but produces a broken workload that cannot back up due to auth failure
- Raises `DuplicateWorkloadError` when the POST response `errors[]` contains errorCode 7001 (same IP already registered under that plan/namespace)
- Raises `APIError` for other non-zero errorCodes in `errors[]`
- Returns `None` on success

**Special behavior of update_file_server():**
- `login_password == ""` raises `ValueError` at `FileServerUpdateRequest` construction time (`__post_init__`), before any API call (empty string is never a valid stored password)
- `login_password: None` in `FileServerUpdateRequest` keeps the existing stored password — sends `""` to the API, which the API treats as "preserve the stored value" (the GET response always returns `""`, so the only way to round-trip without changing the password is to send `""`)
- Fetches the current spec via GET, merges updated `configFs` fields, then PUTs the full spec (no opcode)
- Unlike the POST endpoint's `errors[]` array, the PUT endpoint reports failure as a nested `error.errorCode` (handled by `_http.py`'s `_check_api_error`, which raises `APIError`); `update_file_server()` converts errorCode 7001 to `DuplicateWorkloadError` and re-raises everything else

**`remoteSessionList` serialization (add_file_server / update_file_server):**
- `FileServerPathSelector.path`: `""` = backup whole root; non-empty = specific folder path
- `FileServerPathSelector.excluded_paths`: sub-paths excluded within `path`; maps to `filtered_paths` in JSON
- Parse-direction default for an empty `remoteSessionList`: see "`MachineWorkload.fs_config`" in [Type System Notes](#type-system-notes)

**backup_now() / cancel_backup()** send a `workloadRefs: [{uid, namespace}]` body (see the
request-building code in `machine.py`).

**change_plan() dispatch (shares the same `_put_plan_change()` request with retire()):**
`change_plan(workload, plan)` dispatches on `isinstance(plan,
RetirementPlan)`: a `RetirementPlan` requires `workload.is_retired` to already be `True` (else
`InvalidOperationError`) — re-assigning the retirement policy of an already-retired workload;
a `ProtectionPlan` requires `workload.is_retired` to be `False` (else `InvalidOperationError`).
This is the opposite precondition from `retire()`, which transitions an active (not yet
retired) workload into the retired state. Additionally, for a `ProtectionPlan`,
`plan.category` must match `workload.category` (else `InvalidOperationError`); `RetirementPlan`
is domain-agnostic and skips this check.

**Batch response of `_put_plan_change()` (retire() / change_plan()) and delete():**
The underlying endpoints are batch operations that answer HTTP 200 even when the workload was
rejected, reporting the rejection per entry in the response body (`failed.entries` for Machine,
`errors` for M365). Both collections parse that list and raise `InvalidOperationError` (with
`resource_type="Workload"`, the workload's ID, and the entry's `errorCode` — e.g. 7018 when the
workload is still initializing) for the first failed entry, so a silently-ignored partial
failure cannot occur.

**MachineWorkloadCollection.list() plan parameter:**
`plan: list[ProtectionPlan | RetirementPlan] | None` is repeatable (OR logic); the SDK
extracts each plan's `plan_id` and sends it as a separate `filter.planId` query param.

**MachineWorkloadCollection.list() workload_types parameter:**
`workload_types: list[MachineWorkloadType] | None` — passing `None` (or omitting the argument) returns workloads of all types. The SDK always includes a fixed query parameter alongside any type filters to match APM's expected request format; callers do not need to supply this parameter.

**MachineWorkloadCollection.list() status parameter:**
`status: list[WorkloadStatus] | None` is repeatable (OR logic); reverses `WorkloadStatus`'s
two-field derivation (see "WorkloadStatus" above) — `QUEUING`/`BACKING_UP`/`DELETING` map to
`jobStatus`, the remaining members map to `latestVersionResult` (dicts in the index above).
`WorkloadStatus.RETIRED` is not a valid filter value — it's governed by `is_retired`, not a raw
status field — and raises `ValueError` if included.

**MachineWorkloadCollection.list() verify_status parameter:**
`verify_status: list[VerifyStatus] | None` is repeatable (OR logic); reverses
`_VERIFY_STATUS_MAP` directly (single raw field, no per-field split like `status` above).
Verified against a live APM: the raw field is not exclusively PS/VM-scoped, so
`verify_status=[NOT_ENABLED]` can also match PC/FS workloads — those still display
`verify_status=None` (see "VerifyStatus" above), not the value they were filtered on.

---

### M365WorkloadCollection

**get() / get_by_name():**

| Method | Notes |
|------|------|
| `get(workload_id, namespace, tenant_id, workload_type)` | Queries by the `(namespace, workload_id)` primary key; `tenant_id` and `workload_type` are both required |
| `get_by_name(name, tenant_id, workload_type, is_retired=False)` | `tenant_id` and `workload_type` are both required; matches against display name / UPN / group email (case-insensitive) |

**Number of API calls for list():**
- Always 1 API request (`workload_type` is required, queries only a single service subtype)
- When `namespace` is not None: 1 additional backup_server API call to resolve namespace →
  backup_server_id (paginated 500 servers at a time; more than one call only when the target
  namespace isn't in the first page)
- `workload_type` has no "all subtypes" wildcard value to collapse the 6 per-subtype calls into
  one: verified against a live APM that the underlying filter's zero-value enum member returns
  the same single subtype's workloads as one of the 6 named values (not the union of all 6), so
  `list()` must still be called once per `M365WorkloadType` to enumerate every M365 workload for
  a tenant.

**backup_now() / cancel_backup()** use `nsUidPairs: [{namespace, uid}]` (differs from
Machine's `workloadRefs`); `backup_now()` additionally sends a top-level `tenantId`,
`cancel_backup()` does not.

**change_plan() dispatch (shares the same `_put_plan_change()` request with retire()):**
Same dispatch semantics as `MachineWorkloadCollection.change_plan()` (see above), with the
request additionally carrying a `planType` of `"ARCHIVE"` for a `RetirementPlan` or `"BACKUP"`
for a `ProtectionPlan`.

**M365WorkloadCollection.list() plan parameter:**
`plan: list[ProtectionPlan | RetirementPlan] | None` is repeatable (OR logic); the SDK
extracts each plan's `plan_id` and collects them into the `planUids` array field of the
request body filter.

**M365WorkloadCollection.list() status parameter:**
`status: list[WorkloadStatus] | None` is repeatable (OR logic); reverses `_M365_STATUS_MAP`
directly (single raw field, unlike Machine's two-field split above). `WorkloadStatus.RETIRED`
is not a valid filter value (governed by `is_retired`) and raises `ValueError` if included.
M365 workloads have no verification concept, so there is no `verify_status` parameter here.

**Special behavior of M365WorkloadCollection.delete():**
- Error detection uses the `errors` array in the response body (not `failed.entries` as in `MachineWorkloadCollection`).
- Non-existent workloads return `success: true` with an empty `errors` array — the call succeeds silently; no `ResourceNotFoundError` is raised.
- `tenantId` in the request body is not validated server-side; matching is by namespace + uid only.

---

### M365AutoBackupRuleCollection

Accessed via `APMClient.m365.auto_backup_rules`.

**API path prefix:** `/api/v1/application/m365/tenant/auto_backup_rule`

**Two independently managed sections per tenant:**

**User Services rules (Exchange / OneDrive / Chat):**
- `list(tenant_id)` — GET `/{tenant_id}`.
- `create(tenant_id, namespace, plan_id, ...)` — POST with body `{ namespace, ruleSpec: { tenantId, backupPlanId }, exchangeGroupIds, onedriveGroupIds, chatGroupIds }`.
- `update(rule, ...)` — PUT `/{uid}` with body `{ namespace, backupPlanId, exchangeGroupIds, onedriveGroupIds, chatGroupIds }`. Note: the PUT body uses `backupPlanId` at top level (no `ruleSpec` wrapper), unlike the POST.
- `delete(rule)` — DELETE `/{uid}?namespace={namespace}`.

**Collaboration Services settings (M365 Groups / SharePoint Personal Sites / SharePoint Sites / Teams):**
- `update_collab_settings(tenant_id, group_exchange, mysite, sharepoint, teams)` — PUT `/collab_service`. All four service types are sent together. Disabled types (or `None`) serialize to `{ "planId": "", "namespace": "" }`.
- API field names: `groupExchangeSetting`, `mySiteSetting`, `generalSiteSetting` (SharePoint Sites), `teamsSetting`.

**`list()` excludes rules pending deletion** — after `delete()` is called, the rule may still appear in the raw API response for up to ~2 minutes while the finalizer runs. `list()` filters these out so the result reflects only active rules.

---

### BackupActivityCollection / RestoreActivityCollection

**The history parameter of list():**
- `history=False` (default): in-progress tasks
- `history=True`: completed historical records

**The status parameter of list() is a one-to-many filter:**
A single SDK status enum value can expand to multiple API filter values (OR logic),
per `_BACKUP_STATUS_TO_API` / `_RESTORE_STATUS_TO_API` in `collections/_activity_parsers.py`.
For example, `BackupActivityStatus.FAILED` sends `backupStatus=ERROR&backupStatus=UNKNOWN`,
and `RestoreActivityStatus.FAILED` sends
`restoreStatus=FAILED&restoreStatus=DEVICE_MISSING&restoreStatus=MIGRATE_FAILED`.

**BackupActivityCollection.list() namespace parameter:**
`namespace: list[str] | None` is repeatable (OR logic); each value is sent as a separate
`namespace` query param, restricting results to activities on the given backup server
namespace(s). This is independent of the `workload` parameter below (server-level vs.
workload-level scoping) and the two compose via AND logic.

**workload parameter (both collections):**
`workload: Workload | None` restricts results to a single workload's activities, sent as
the query param pair `workload.uid=<workload.workload_id>&workload.namespace=<workload.namespace>`.
Confirmed against the real API: both params are required together — supplying either one
alone has no filtering effect, so the SDK only ever sends them as a pair. If no workload
matching this reference exists on the server, both `BackupActivityCollection.list()` and
`RestoreActivityCollection.list()` return `([], 0)` — the underlying restore-activities
endpoint responds with an HTTP 404 carrying `error.details[0].errorCode: 1002`
(`errorString.key: "database_query_failed"`, confirmed against a live APM) in that case
(unlike the backup-activities endpoint, which responds with an empty page), so
`RestoreActivityCollection.list()` catches a 404 carrying that specific detail code and
returns an empty result to match its sibling's behavior; a 404 without that code (or any
other error) still propagates normally.

**RestoreActivityCollection.list() filter support:**
Confirmed against the real `/api/v2/activity/restore/activities` API: supports `status`,
`workload`, `since`/`until`, `keyword`, `history`, `limit`, and `offset` — unlike
`BackupActivityCollection.list()`, it does not accept `machine_types`, `m365_types`, or a
backup-server `namespace` list.

**BackupActivityCollection.cancel()** sends `deviceNsUidPairs`/`m365NsUidPairs`/`gwNsUidPairs`,
populating only the one matching `activity.category` (the other two empty) with
`{namespace, uid: activity.activity_id}`.

**RestoreActivityCollection.cancel()** sends a differently-shaped body keyed on the
activity's workload rather than its own ID: `activities: [{workload: {uid, namespace},
executionId, namespace, workloadType}]`, with `workloadType` converted from
`ActivityWorkloadType` via `_SUBTYPE_TO_CANCEL_TYPE` (`collections/_activity_parsers.py`).

**Endpoint used by get_by_version():**
Calls `GET /api/v1/activity/backup/activity?executionId=...&workloadUid=...&namespace=...` (v1, not v2) with `version.execution_id`.

**RestoreActivity detail fields parsed from spec:**
- `version_timestamp`: from `spec.versionTimestamp`; same parsing as
  `M365ExportActivity.version_timestamp`. `None` if not provided.
- `restore_from_info`: from `spec.restoreFromInfo`, mapped to `LocationInfo`:
  `is_remote_storage = destinationType != "APPLIANCE"`, `identifier = ""`,
  `name = hostname`, `endpoint = address`, `vault = containerName or None`.
  `None` when `spec.restoreFromInfo` is missing or empty.
- `destination_path`: from `spec.destinationPath`; empty string -> `None`.
- `destination_inventory`: a `Hypervisor` built from
  `spec.machineInfo.additionalInfo` (a JSON-encoded string). Only `hostname`
  (`inventory_name`), `address` (`inventory_addr`), and `host_type`
  (`inventory_type`, mapped via the existing `_HOST_TYPE_MAP` in
  `collections/hypervisors.py`, e.g. `"ESXi" -> HypervisorType.VSPHERE_ESXI`)
  are populated; `hypervisor_id`, `account`, `description`, `port`, `version`
  have no equivalent in `additionalInfo` and are left as empty/zero
  placeholders -- this is a partial snapshot, not a full inventory lookup.
  `None` when `machineInfo`/`additionalInfo` is missing, not valid JSON, or
  `inventory_name` is empty.
- In practice, `destination_path` and `destination_inventory` are mutually
  exclusive: `destination_path` is populated for file-level restores (FS/M365
  workloads), while `destination_inventory` is populated for machine-level VM
  restores. The SDK does not enforce this -- both fields are independently
  optional based on what `spec` contains.

---

### LogCollection

All methods require passing a `BackupServer` object; the SDK takes its `namespace` and adds it to the `x-syno-tunnel-route` header,
routing the request to the specified backup server via the gateway tunnel. **Only `BackupServerType.DP` servers are supported** — an API-level constraint of the log endpoints; the SDK does not pre-validate the server type.

**Returned total values:**

| Method | total |
|------|-------|
| `list_drive()` | The actual total returned by the API |
| `list_activity()` | Always `None` (not returned by the API) |
| `list_connection()` | Always `None` |
| `list_system()` | Always `None` |

---

### ExchangeExportCollection / GroupExportCollection

**Internal steps of start():**
1. `GET …/folders?isGroup={bool}` — get the mailbox root folder ID
2. `POST …/start_export?isGroup={bool}` — submit the export request

The `ready_to_download`/download-flow branching and the `PREPARING`-state check are documented
on `start()` and `get_download_url_by_activity()` respectively — see their docstrings.

**Special field behavior of M365ExportActivity:**
- `finished_at`: always `None` when status is `PREPARING` (regardless of the API's returned value).
- `version_timestamp`: the timestamp of the backup version used for the export (`spec.versionTimestamp`); `None` if not provided by the API.

**Group mailboxes have no archive mailbox**: `GroupExportCollection.start()` has no `archive_mailbox` parameter.

---

### BackupServerCollection

**Matching logic of get_by_name():**

Iterates through the search results, applying the following conditions (OR) to each server in order, and returns the first match:
1. `name` case-insensitive match
2. `hostname` case-insensitive match

**Query behavior for tiering_plan_name / tiering_plan_destination:**

`BackupServer.tiering_plan_name` and `tiering_plan_destination` (`LocationInfo | None`) are resolved when the server has a tiering plan applied, via an internal bulk-fetch helper (`_get_plans_bulk()` in `collections/tiering_plans.py`) — **not** the public `TieringPlanCollection.get()`, so it doesn't get that method's detail-code-based 404 mapping; it relies on the SDK's generic 404 handling instead, with the same net effect (dangling reference → `None`; any other query failure propagates).

- `list()`: runs `asyncio.gather` to query all unique tiering plans on the page in parallel
- `get(backup_server_id)`: performs an on-demand query only for that server's tiering plan

**Computation of `BackupServer.tiering_status`:**

`tiering_status` (`TieringStatus | None`) is parsed from `tieringInfo` in the backup server API response. The field is `None` when `tieringInfo` is absent or its `tieringStatus` field is empty. The mapping follows the same rules as `TieringPlan.tiering_status`; see the `TieringPlanCollection` section below.

---

### ProtectionPlanCollection

Accessed via `APMClient.plans` (cross-category facade), `APMClient.machine.plans` (`MachinePlanCollection`), and `APMClient.m365.plans` (`M365PlanCollection`).
To change the plan assigned to a workload, use `APMClient.machine.workloads.change_plan()` or
`APMClient.m365.workloads.change_plan()`.

**Resolution behavior of `backup_copy_policy`:**

`ProtectionPlan.backup_copy_policy` (`BackupCopyPolicy | None`) is populated when Backup Copy is enabled and the destination can be resolved; otherwise `None`. The resolution method differs depending on the destination type:

- **APPLIANCE (backup server)**: issues a single backup server list request to retrieve all servers, matching by **namespace**. If the namespace is not found in the list, `backup_copy_policy` is set to `None` for that plan; a failed backup server list query propagates as an exception.
  > Note: `spec.backupCopy.destination` returned by the plan API is the backup server's **namespace**, not its `backup_server_id`, so it cannot be used to query a single server directly.
- **Remote Storage (external storage)**: issues one query per unique destination ID, executed in parallel. If the destination no longer exists (dangling reference) or `displayName` is empty, set to `None`; any other query failure propagates as an exception.

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

**PC task backup mode (`MachineTaskSchedule`):**

`MachineTaskSchedule` supports three modes for `MachineWorkloadType.PC` tasks, determined by the combination of `time_schedule` and `event_trigger`:

| `time_schedule`              | `event_trigger`            | Backup mode        |
|------------------------------|----------------------------|--------------------|
| `None`                       | `None`                     | Manual (on-demand) |
| `None`                       | `EventTriggerConfig(...)`  | EVENT only         |
| `ProtectionSchedule(...)`    | `None`                     | SCHEDULE only      |
| `ProtectionSchedule(...)`    | `EventTriggerConfig(...)`  | SCHEDULE+EVENT     |

`event_trigger` is only valid for PC tasks; setting it on PS/VM/FS tasks raises `ValueError` at `MachinePlanCreateRequest` construction time. `EventTriggerConfig` validates at construction time that at least one of `on_sign_out`, `on_lock`, `on_startup` is `True`, and that `min_interval` is positive.

**Request object validation (`MachinePlanCreateRequest`, `M365PlanCreateRequest`):**

`MachinePlanCreateRequest` and `M365PlanCreateRequest` validate field invariants in `__post_init__`, so `ValueError` is raised at construction time — before any API call. The full set of validated conditions is listed in `MachinePlanCreateRequest`'s `Raises:` docstring; when `tasks is None`, APM generates default tasks and all of the `tasks`-specific cross-task validations are skipped.

**`create()` / `update()` always return via `get()`:**

POST and PUT responses for protection plans return a minimal body (just the plan ID, not the full plan). Both `create()` and `update()` automatically call `get(plan_id)` after the mutating request to return a fully-populated `ProtectionPlan` (with `tasks`, `vm_config`, `pc_config`, `ps_config`, `db_config`, `backup_window`, and `backup_copy_policy` all populated when applicable).

**`get()` vs `list()` — config fields:**

`get(plan_id)` returns the full plan spec including `configDevice` (tasks, vm_config, pc_config, ps_config, db_config, backup_window). `list()` does not include `configDevice` in its response; the config fields are always `None` on plans returned by `list()`. Always call `get(plan_id)` when config field values are needed.

**`delete()` error codes:**

errorCode `4019` (plan assigned to workloads) or `4017` (plan referenced by a server template) → `PlanInUseError`; either code triggers the error, and the flags are derived independently: `has_workloads` = 4019 present, `has_server_template` = 4017 present (both may be `True` at once). Other API errors are re-raised unchanged.

**`run_schedule_by_controller_time` field (shared by `ProtectionPlan` / `RetirementPlan` / `TieringPlan`):**

The field is `True` when the API response's `spec` object contains a `controllerUtcOffset` key (regardless of its value), and `False` when that key is absent. The field name and the API field name are unrelated — the presence of `controllerUtcOffset` is the only signal that controller-time scheduling is active. The same rule applies to all three plan models; it is not repeated in the sections below.

**Computation of `ProtectionPlan.backup_copy_status`:**

`backup_copy_status` (`PlanBackupCopyStatus | None`) is parsed from `backupCopyStatus` in the plan API response. The field is `None` when `backupCopyStatus` is absent. When present, the outer `VersionCopyStatus` is computed from `backupCopyStatus.copyStatus` using a separate mapping (distinct from the `COPY_STATUS_*` enum used for versions). The table below documents the shared `_parse_copy_status_core()` helper in `_shared.py`, which also resolves `TieringPlan.tiering_status` / `BackupServer.tiering_status` (see [TieringPlanCollection](#tieringplancollection) for the tiering-specific differences):

| `backupCopyStatus.copyStatus` | Computed `PlanBackupCopyStatus.status` | Notes |
|---|---|---|
| `"NOT_ENABLED"` | `NOT_ENABLED` | |
| `"SKIPPED_WORKLOAD"` | `SKIPPED` | `skipped_workload_count` from `skippedWorkloadCount`; `reason` from `_resolve_copy_reason("SKIPPED_WORKLOAD", statusReason)` |
| `"DOING"` | `IN_PROGRESS` | `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` |
| `"NO_VERSIONS_TO_COPY"` | `COMPLETED` | `reason = CopyReason.NO_VERSIONS_TO_COPY` |
| `"COMPLETED"` + `pendingVersionCount > 0` | `WAITING` | `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` (string `"0"` or empty → `None`) |
| `"COMPLETED"` + `pendingVersionCount == 0` | `COMPLETED` | `reason = None` |
| RETRY-class values (see `_COPY_ERROR_STATUS_MAP` in `_shared.py`: `"DESTINATION_DISCONNECTED"`, `"UNDER_MAINTENANCE"`, `"AUTHENTICATION_FAIL"`, `"OUT_OF_STORAGE"`, `"OUT_OF_LICENSE_QUOTA"`, `"SOURCE_INCOMPATIBLE"`, `"DESTINATION_INCOMPATIBLE"`, `"SSL_VERIFY_FAILED"`) | `RETRY` | `reason` from `_resolve_copy_reason(copyStatus)`; `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` |
| FAILED-class values (see `_COPY_ERROR_STATUS_MAP` in `_shared.py`: `"INFRASTRUCTURE_ERROR"`, `"VAULT_NOT_MOUNTED"`, `"DESTINATION_DATA_CORRUPTED"`, `"DESTINATION_NOT_EXIST"`, `"MISSING_LINK_KEY"`, `"FS_READONLY"`) | `FAILED` | `reason` from `_resolve_copy_reason(copyStatus)`; `pending_version_count` from `pendingVersionCount`; `remaining_bytes` from `remainingBytes` |

`remainingBytes` of `"0"` or empty string maps to `remaining_bytes = None` (unknown/not applicable), not `0`.

---

### RetirementPlanCollection

**`create()` / `update()` always return via `get()`:**

Same pattern as `ProtectionPlanCollection`: POST/PUT return a minimal body; both methods call `get(plan_id)` to return a fully-populated `RetirementPlan`.

**Retention encoding for `RetirementPlanCreateRequest`:**

- `retention_days=None` → `keepAll: true` in the request body
- `retention_days=N` → `keepAll: false, keepDays: N`, with `keepVersions: 1` when `keep_latest_version=True`, else `keepVersions: 0`
- `retention_days=None` → `keepVersions` is always `0`, regardless of `keep_latest_version` (with `keepAll: true` every version is kept anyway)

**`delete()` error codes:**

- errorCode `4019` → `PlanInUseError(has_workloads=True)` (plan has assigned workloads)

---

### TieringPlanCollection

**Destination query behavior of list() / get() / get_by_name():**

`TieringPlan.destination` (`LocationInfo | None`) is obtained via `GET /api/v1/external_storage/{spec.destination}`. If the destination no longer exists (dangling reference), set to `None`; any other query failure propagates as an exception.

- `list()`: runs `asyncio.gather` to query all unique destination UUIDs on the page in parallel
- `get(plan_id)`: queries the destination for that plan
- `get_by_name(name)`: performs the destination query only for the plan whose name exactly matches; does not query the destinations of other plans

**Computation of `TieringPlan.tiering_status` (and `BackupServer.tiering_status`):**

`tiering_status` (`TieringStatus | None`) is parsed from `tieringInfo` in the plan (or backup server) API response. The field is `None` when `tieringInfo` is absent or its `tieringStatus` field is empty. When present, `TieringStatus.status` is computed from `tieringInfo.tieringStatus` by the same shared `_parse_copy_status_core()` semantics documented in the [`ProtectionPlan.backup_copy_status` table](#protectionplancollection), with two tiering-specific differences:

- The input field is `tieringInfo.tieringStatus` (not `backupCopyStatus.copyStatus`).
- The additional input value `"NONE"` also maps to `COMPLETED` (`reason = None`).

**`create()` / `update()` always return via `get()`:**

Same pattern as `ProtectionPlanCollection`: POST/PUT return a minimal body; both methods call `get(plan_id)` to return a fully-populated `TieringPlan` (with `destination` resolved).

**`destinationType` mapping for `TieringPlanCreateRequest.destination`:**

The `RemoteStorage.storage_type` is converted to the API `destinationType` string via `_STORAGE_TYPE_TO_DEST_TYPE` in `collections/_shared.py` (shared with `_protection_plan_builders.py`). Note the many-to-one merges: the China variants map to the same `destinationType` as their global counterparts (`AMAZON_S3` / `AMAZON_S3_CHINA` → `"AWS_S3"`, `AZURE_BLOB` / `AZURE_BLOB_CHINA` → `"AZURE_BLOB"`).

**`delete()` error codes:**

- errorCode `4029` → `PlanInUseError(has_backup_servers=True)` (plan has assigned backup servers)

---

### RemoteStorageCollection

**add() — type-specific request routing:**

`add()` accepts one of six request types; the caller selects the type appropriate for the storage:

- `GenericS3StorageAddRequest` — S3 Compatible: endpoint and `trust_self_signed` required. Region and virtual-host support are auto-detected from the endpoint before creation.
- `APVStorageAddRequest` — ActiveProtect Vault: endpoint and `trust_self_signed` required. Vault name and display name are fetched from the APV server; `APVStorageAddRequest` has no `vault_name` field.
- `AmazonS3StorageAddRequest` / `AmazonS3ChinaStorageAddRequest` — Amazon S3: vault name (bucket) and credentials required; no endpoint or region needed. APM derives endpoint and region server-side.
- `C2ObjectStorageAddRequest` — Synology C2 Object Storage: same endpoint-free pattern as Amazon S3.
- `WasabiCloudStorageAddRequest` — Wasabi Cloud Storage: same endpoint-free pattern as Amazon S3.

**add() — catalog check and batch relink:**

Before creating any storage, `add()` calls the catalog check endpoint for all types (including endpoint-free types, which use `endpoint:""`). If unmanaged backup catalogs are found:
- `unmanaged_retirement_plan=None` → `RemoteStorageUnmanagedCatalogError` is raised; no storage is created.
- `unmanaged_retirement_plan=<plan>` → storage is created first, then the catalogs are relinked to that plan via `batch_relink`.

**add() — trust_self_signed:**

`trust_self_signed=True` causes the SDK to auto-fetch the remote endpoint's self-signed TLS certificate and include it in the create/update request. Applies to `GenericS3StorageAddRequest`, `APVStorageAddRequest`, and `RemoteStorageUpdateRequest` only. Endpoint-free request types do not expose this field — their public endpoints use CA-signed certificates.

**update() — minimal body per type:**

- `S3_COMPATIBLE` and `ACTIVE_PROTECT_VAULT`: body is `{id, accessKey, secretKey, endpoint}` plus `certificate` if `trust_self_signed=True` and the endpoint has a self-signed cert.
- Endpoint-free types (`AMAZON_S3`, `AMAZON_S3_CHINA`, `C2_OBJECT_STORAGE`, `WASABI`): body is `{id, accessKey, secretKey}` only. `endpoint` and `trust_self_signed` are not sent for these types.
- Display name, storage type, vault name, and encryption settings are immutable — the update endpoint silently ignores them.

**add() and update() — encryption key:**

`RemoteStorageAddResult.encryption_key` is `None` when encryption is not enabled; non-None (the APM-issued key) when enabled. Store the key securely — it cannot be retrieved later. When re-adding an encrypted vault that was previously registered, pass the old key in `relink_encryption_key`; the old key remains valid and a new key is also issued. Re-adding a previously encrypted vault **without** a valid `relink_encryption_key` raises `RemoteStorageEncryptionMismatchError` (`resource_id` = vault name).

**delete() — in-use guard:**

`delete()` raises `RemoteStorageInUseError` when the storage is still referenced by active plans.

**vault_name on RemoteStorage:**

`RemoteStorage.vault_name` contains the bucket name (S3 types) or vault name (APV); empty string when not reported by the API.

**add() and update() — GET after write:**

Both `add()` and `update()` issue a `get()` after the write to return the refreshed model state.

---

### SaasCollection

Accessed via `APMClient.saas`.

**M365 vs. GWS tenant parsing asymmetry:** `list()`'s raw response uses different field names
per tenant category — M365 entries use `tenantId`/`tenantName`/`tenantMail`; GWS entries use
`domainId`/`domainName`/`domain`, falling back to the M365 field names when present (`saas.py`).
`get_m365_tenant()` only ever parses the M365 shape (M365-only lookup) and always sets
`protected_data_bytes=0` (already documented in its own docstring — usage data isn't available
for this specific lookup).

**`list()`'s `total` field:** the underlying `cloudapp` endpoint returns `total` as a string
(a server-side quirk); the SDK coerces it to `int` (see the comment in `saas.py`).

---

### APMClient.get_site_info()

Calls four endpoints in parallel, then performs a paginated scan of the backup server list:

```
GET /api/v1/license/info                           → site_uuid
GET /api/v1/cluster/site_info                      → external_address, port
GET /api/v1/infra/backup_server/storage_statistics → site_storage
GET /api/v1/dashboard/get_workload_statistics      → workload_usage
GET /api/v1/infra/backup_server (paginated scan)   → primary_management_server, secondary_management_server
```

Scan condition: stops as soon as both PRIMARY and SECONDARY roles are found; otherwise scans all pages.

---

## Collection Map

How each collection hangs off `APMClient`. Method signatures are deliberately **not** listed
here — they live in the source docstrings and the Sphinx API docs, which are always current.

| Access path | Collection | Purpose |
|---|---|---|
| `apm.machine` | `MachineCollection` | Machine domain entry point (`.workloads`, `.plans`) |
| `apm.machine.workloads` | `MachineWorkloadCollection` | PC/PS/VM/FS workloads: listing, versions, backup/cancel, retire/change-plan, file-server registration |
| `apm.machine.plans` | `MachinePlanCollection` | Machine protection plan CRUD |
| `apm.m365` | `M365Collection` | M365 domain entry point (`.workloads`, `.plans`, `.exchange_export`, `.group_export`, `.auto_backup_rules`) |
| `apm.m365.workloads` | `M365WorkloadCollection` | M365 workloads per service subtype: listing, versions, backup/cancel, retire/change-plan, delete |
| `apm.m365.plans` | `M365PlanCollection` | M365 protection plan CRUD |
| `apm.m365.exchange_export` / `apm.m365.group_export` | `ExchangeExportCollection` / `GroupExportCollection` | Mailbox PST export: start, poll, download URL, cancel |
| `apm.m365.auto_backup_rules` | `M365AutoBackupRuleCollection` | Per-tenant auto-backup rules and collaboration-service settings |
| `apm.plans` | `ProtectionPlanCollection` | Cross-category protection plan reads (+ create/delete) |
| `apm.retirement_plans` | `RetirementPlanCollection` | Retirement plan CRUD |
| `apm.tiering_plans` | `TieringPlanCollection` | Tiering plan CRUD |
| `apm.activities` | `ActivityCollection` | Activity entry point (`.backup`, `.restore`) |
| `apm.activities.backup` / `apm.activities.restore` | `BackupActivityCollection` / `RestoreActivityCollection` | Activity listing, detail, cancel |
| `apm.backup_servers` | `BackupServerCollection` | Backup server listing/lookup, tiering-plan assignment |
| `apm.remote_storages` | `RemoteStorageCollection` | Remote storage (external vault) CRUD |
| `apm.hypervisors` | `HypervisorCollection` | Hypervisor inventory servers |
| `apm.logs` | `LogCollection` | Server-scoped activity/drive/connection/system logs |
| `apm.saas` | `SaasCollection` | SaaS tenant listing, M365 tenant lookup |
| `apm.get_site_info()` / `apm.download_file()` / `apm.my_server` | `APMClient` directly | Site overview, authenticated file download, connected server |

---

*For detailed API documentation, see the Sphinx API docs (`make docs`).*
