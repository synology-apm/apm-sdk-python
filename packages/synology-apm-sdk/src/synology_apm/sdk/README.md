# APM Python SDK — Design Contract

> Corresponding product: Synology ActiveProtect Manager

**Purpose of this document**: A design contract for implementers (human developers or AI
sessions) — see the root `CLAUDE.md`'s Key Documents table for what it covers. For the SDK's
full public interface (signatures, Attributes, Args/Returns/Raises), see the Sphinx API docs
(`make docs`).

---

## Table of Contents

- [Design Conventions](#design-conventions)
- [Adding a New SDK Method or Field](#adding-a-new-sdk-method-or-field)
- [APM Version Compatibility](#apm-version-compatibility)
- [Package Structure](#package-structure)
- [Exception Hierarchy](#exception-hierarchy)
- [Authentication Flow](#authentication-flow)

For enum ↔ API string mappings, type-system edge cases, non-obvious per-collection behavior
rules, and the collection access-path map, see the companion
[`BEHAVIOR_REFERENCE.md`](BEHAVIOR_REFERENCE.md) — read it per-section as a task touches that
area, not cover to cover.

---

## Design Conventions

- Public methods must have type annotations and docstrings; use `async/await` (no synchronous blocking calls allowed); connections are managed via `async with APMClient(...) as apm:`; attribute access uses `@property` rather than getters.
- Exceptions must always use the custom hierarchy defined in `exceptions.py` (see "Exception Hierarchy"); never raise a generic `Exception`.
- Single-resource lookups (`get()` and equivalents) wrap their primary API call in `_shared._not_found_as(resource_type, resource_id, ...)` so every not-found error carries the caller's resource identity, regardless of which response shape signaled it (HTTP 404, an error detail code via `detail_code=`, or an empty 200 body — for the last, raise a placeholder `ResourceNotFoundError` inside the block and the context manager rewrites it). Wrap only the primary lookup, never nested lookups such as location-cache building.
- Docstrings fall under the root CLAUDE.md's "API Abstraction in User-Facing Text" rule; `_http.py` and private helpers prefixed with an underscore are exempt (per that rule) and may reference raw API details in code/comments where needed.
- When adding a new public type (class / enum / dataclass), remember to also add it to `__all__` in `synology_apm/sdk/__init__.py`.
- All model dataclasses (`models/*.py`) are `@dataclass(frozen=True)`: API responses are parsed into immutable value objects, never mutated in place.
- When an API field name differs from the SDK dataclass field name, perform the conversion inside the collection parser (`_parse_*` functions) without changing the SDK's public interface. Magic values (such as `"-1"` / `"0"` / `""`) are always converted to `None` via the relevant `_parse_*` helper (e.g. `_parse_data_sizes()`, `_parse_int_or_none()`).
- **Null vs. Absent JSON Field Handling:** in a parser, use `raw.get(key) or T` instead of `raw.get(key, T)` for nested optional config blocks and for scalar fields whose default equals that type's falsy value; avoid `or T` for any other default (e.g. `True`, non-empty sentinels) without concrete evidence the field can be `null`. Applies to REST API response parsing only, not TOML config or environment variables; parenthesize `or` expressions inside comparisons or chained calls.
- The `host` parameter of the `APMClient` constructor only accepts a hostname or `host:port` (without scheme); the SDK automatically prepends `https://` internally.
- Write operations (`backup_now`, `cancel_backup`, `change_plan`, `retire`, etc.) return `None`, not a pollable Job object — this matches how the APM API itself models long-running operations (fire-and-forget); progress and history are queried separately via `apm.activities`.

### Serialization Convention (`to_dict()`)

All response model dataclasses expose a `to_dict()` method returning a JSON-safe dict. Most fields and properties are formulaic (enum → `.value`, datetime/date/time → ISO 8601, nested `to_dict()`-bearing objects → recursive call, list/tuple → element-wise) and should call `models/_shared.py`'s `auto_to_dict(self, exclude=..., extra=...)` rather than listing every field by hand: `auto_to_dict()` automatically serializes every dataclass field and every public (non-underscore-prefixed) `@property` on the instance's type, including inherited ones — a new `@property` needs no manual step to appear in `to_dict()` output; name a property with a leading underscore to keep it internal. `exclude` drops a field or property being replaced; `extra` supplies a non-formulaic conversion or renamed/restructured output, merged on top last. `dataclasses.fields()` resolves against an instance's actual runtime type, so a base class's `to_dict()` (e.g. `auto_to_dict(self)`) already serializes a subclass instance's full field and property set — a dataclass subclass adding only plain fields/properties (no extra `exclude`/`extra` of its own) needs **no** `to_dict()` override at all; it inherits the base method as-is. Only define an override when the subclass needs its own `exclude`/`extra` beyond the base class's, and in that case call `auto_to_dict(self, ...)` once with the *combined* `exclude`/`extra` — never `{**super().to_dict(), **auto_to_dict(self, ...)}`, which redundantly re-serializes every subclass field (and re-invokes nested `to_dict()` calls) a second time. `*Request` input types (and other write-only helpers such as `BackupCopyConfig`) are exempt — they are never returned by the API, so there is nothing to serialize. This is the single source of truth for semantic JSON serialization; CLI and MCP both build their output from it rather than each maintaining a separate field-mapping (see "Three-Layer Responsibility Separation" in the repository `CLAUDE.md`).

Every `APMError` subclass also exposes a `to_dict()` method returning a JSON-safe dict of its semantic fields (this is the same "SDK owns semantic serialization" principle as response models above, extended to exceptions). Exceptions are not dataclasses (`Exception.__init__` isn't compatible with a dataclass-generated `__init__`), so `to_dict()` is hand-written per class rather than routed through `auto_to_dict()`: each override calls `{**super().to_dict(), ...}` to layer its own fields on top of the base class's, mirroring how `_ResourceError.__init__` layers its constructor args on top of `APMError.__init__`. A subclass adding no fields (e.g. most `_ResourceError` subclasses) needs no override, same as with model dataclasses. CLI and MCP each still own their own exception → user-facing error code/message mapping (which fields to expose as which label is presentation, not SDK data); only the field *contents* come from `to_dict()`.

---

## Adding a New SDK Method or Field

### Add a collection method

1. Implement it in the relevant `collections/` module (see "Design Conventions" above).
2. If it introduces a new public symbol (enum, model, collection), export it via `sdk/__init__.py` + `__all__`, then run `grep -r "from synology_apm\.sdk\." packages/synology-apm-cli/src examples/` and confirm no output — CLI and examples must consume it via the top-level `synology_apm.sdk` package, never a submodule import.
3. Add exactly one `[[mapping]]` or `[[not_exposed]]` entry to `scripts/mcp_coverage.toml` — enforced by `make test`.
4. Add a unit test in `tests/unit/sdk/collections/` (see `tests/CLAUDE.md` for the request-contract + response-parsing conventions).
5. Add an integration test and record its cassette (`make record-integration-cassettes`).
6. Update `BEHAVIOR_REFERENCE.md` only for non-obvious behavior ("Collection Behavior Rules") or a new collection/access path ("Collection Map").
7. Update `packages/synology-apm-sdk/README.md`'s Quick Start / Developer Guide if user-facing usage changed.
8. Consider a matching call in `tests/smoke/sdk/phases/_<domain>.py`.
9. Run `make test` and `make docs`.

### Add an enum or model field

1. Add/extend the mapping dict next to the collection's `_parse_*` parser — it is the source of truth.
2. Add/extend the model dataclass with an Attributes docstring entry; export new types via `__all__`.
3. If the value is displayed, add the enum → display-string mapping in the CLI layer (never in the SDK).
4. Update every test that uses the changed dataclass as a fixture.
5. Update `BEHAVIOR_REFERENCE.md` ("Enum Definitions and API String Mapping") only if the mapping semantics are non-obvious, or to add a new mapping dict to its location index.
6. Update `packages/synology-apm-sdk/README.md`'s Quick Start / Developer Guide if user-facing usage changed.
7. Run `make test` and `make docs`.

---

## APM Version Compatibility

This SDK targets both APM 1.2 and APM 2.0. Cross-version differences are handled with two
techniques, in order of preference:

1. **Dual-key emission** — for a confirmed field rename, send both the old and new key with
   the same value; APM ignores unrecognized keys. See `M365WorkloadCollection.delete()`.
2. **Reactive `NotSupportedError` fallback** — for a change dual-key emission can't express
   (a restructured body, a removed endpoint). Add only when a real HTTP 501 is reported
   against a supported version, never preemptively: catch `NotSupportedError` locally around
   the one affected call and fall back to the older request/response shape.

No generic version-detection framework exists, and none should be added speculatively.
`BackupServer.system_version` is a per-appliance field, not a manager API version, and is not
used for branching.

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

Keep this tree in sync when a source file is added, renamed, or removed under
`synology_apm/sdk/` — add an inline comment only when the file doesn't follow one of the two
naming conventions above.

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
│   ├── workload.py          # Workload base, MachineWorkload, M365Workload + M365*Info, GWSWorkload + GWS*Info, FileServer* config/request models
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
│   ├── gws_auto_backup_rule.py   # GWSAutoBackupRule, GWSSharedDriveSetting, GWSAutoBackupRuleListResult
│   ├── saas.py               # SaasApplication (base) + M365TenantInfo, GWSDomainInfo
│   └── system.py            # SiteInfo, SiteStorageStats, WorkloadTypeStat, WorkloadUsageSummary
└── collections/
    ├── _shared.py           # Shared collection helpers (private): pagination, timestamp/status parsing, version mixin; also defines the public ListResult pagination envelope
    ├── machine.py           # MachineCollection (entry point) + MachineWorkloadCollection
    ├── m365.py              # M365Collection (entry point) + M365WorkloadCollection
    ├── m365_auto_backup_rule.py
    ├── m365_mail_export.py  # ExchangeExportCollection, GroupExportCollection, M365ExportStartResult
    ├── gws.py               # GWSCollection (entry point) + GWSWorkloadCollection
    ├── gws_auto_backup_rule.py
    ├── protection_plans.py  # ProtectionPlanCollection, MachinePlanCollection, M365PlanCollection, GWSPlanCollection
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
  fields via the shared `assert_resource_error` helper (see `tests/CLAUDE.md` "Exception
  attribute conventions").
- `ResourceNotReadyError` and `RemoteStorageUnmanagedCatalogError` extend bare `APMError` —
  they have **no** `.resource_type` / `.resource_id` (`RemoteStorageUnmanagedCatalogError`
  carries `vault_name` / `catalog_count` instead).
- `KeyringUnavailableError` extends `RuntimeError` directly, **not** `APMError` — it signals
  a local OS-keyring failure (raised by `config.py`'s keyring helpers / `resolve_connection()`),
  not a REST API error, and carries no `error_code` / `response_body`.
- `OTPRequiredError` (two-factor authentication code required) and `OTPIncorrectError` (the
  supplied code was rejected) extend `APMError` directly — **not** `AuthenticationError`, even
  though both are conceptually authentication failures. `ERROR_CODES` keys must have no
  subclass relationships with each other (`classify_error()` does an exact `type()` lookup,
  not an `isinstance` walk), so a new auth-adjacent exception type is always a sibling of
  `AuthenticationError`, never a subclass. Only synology-apm-cli's `config set` command ever
  supplies an `otp_code`/handles these interactively; every other caller (including MCP) only
  ever consumes an already-registered trusted device and treats these as a hard failure.
- API errorCode → exception mappings are operation-specific and documented per collection in
  [`BEHAVIOR_REFERENCE.md`'s Collection Behavior Rules](BEHAVIOR_REFERENCE.md#collection-behavior-rules) (e.g. 4013 → `PlanNameConflictError`,
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

The SDK authenticates through the legacy Synology WebAPI login endpoint (`/webapi/entry.cgi`).

1. `connect()` calls `GET /webapi/entry.cgi?api=SYNO.API.Auth&version=6&method=login&client=browser&session=webui&enable_syno_token=yes`, which sets the `id` HttpOnly session cookie.
   > **Warning:** Must use **GET + `version=6` + `client=browser`**. `POST + version=7 + format=cookie` returns an empty `id=` value, causing every subsequent business API call to respond with `HTTP 401`.
2. `connect()` then calls `GET /api/v1/infra/backup_server/me` to confirm the host is an APM appliance and to resolve `my_server` (see "Trigger Conditions for NotManagementServerError" above).
3. All business API requests rely on the `id` cookie. When the session expires, APM responds with `HTTP 401` (`{"message": "auth cookie failed"}`); the SDK re-authenticates once via step 1 and retries, raising `AuthenticationError` only if that also fails.
4. `disconnect()` calls `GET /api/v1/preference/logout`.

### Two-Factor Authentication (TOTP) / Trusted Devices

`WebAPISession`/`APMClient` accept optional `otp_code`/`device_id` constructor args, added to
the login request built in `_http.py`'s `_login_params()`:

- `otp_code` (+ `enable_device_token=yes`) is included only when supplied — a fresh,
  OTP-verified login that registers (or re-confirms) a trusted device. It is **one-shot**:
  `_do_login()` clears it in a `finally` block after the first attempt, so it is never resent
  on a later automatic 401 re-auth (a TOTP code can't be replayed, and resending it would risk
  a spurious `OTPIncorrectError` on a re-auth nobody is watching).
- `device_id` is included whenever known — **not** one-shot, resent on every login attempt
  including automatic re-auth. This is what lets 401 re-auth keep working for a two-factor
  account with no human present: the device token substitutes for the code.
- A successful `otp_code`-bearing login's response `data.did` is captured and exposed via the
  `device_id` property on both `WebAPISession` and `APMClient`.
- Codes `403` (two-factor code required) and `404` (code incorrect) raise `OTPRequiredError`/
  `OTPIncorrectError` respectively (via the shared `_auth_exception_for_code()` helper, used by
  both `_do_login()` and the generic `_raise_for_error_code()` path); every other auth code
  (119/400/401/402/406/407/430) is unaffected and still raises plain `AuthenticationError`.

The SDK itself never solicits an `otp_code` interactively — that's synology-apm-cli's `config
set` command's job (the only place in this project that handles a two-factor prompt). Every
other consumer, including synology-apm-mcp, only ever supplies a previously-registered
`device_id` and treats `OTPRequiredError`/`OTPIncorrectError` as a hard failure.

---

*For detailed API documentation, see the Sphinx API docs (`make docs`).*
