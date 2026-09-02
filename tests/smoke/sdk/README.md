# SDK Live Smoke-Test Tool — Maintainer Guide

This is the design contract for the SDK live smoke-test tool: how it is structured, and how to
extend it when `synology_apm.sdk` gains new collections or methods. See `tests/smoke/README.md`
first for conventions shared with the sibling CLI smoke tool (the layer-comparison table,
`reports/` trust model, `ctx.data`/`ctx.skip`/`--group` semantics, and the generic extend/
after-any-change recipe) — this file covers only what's specific to driving the SDK in-process.

---

## Purpose and relationship to other test layers

`tests/smoke/cli/` exercises the CLI's argv parsing and table/json rendering (see the layer
table in `tests/smoke/README.md`); this tool goes one layer down and exercises the SDK's
collections/methods directly, as "unit tests against a real machine" — including **in-process
correctness checks** (`ctx.check`) that the CLI smoke test cannot make (e.g. does
`apm.activities.backup.list(workload=w)` actually filter by `w`? does a bogus workload
reference behave the way `BEHAVIOR_REFERENCE.md`'s Collection Behavior Rules document?). It
also records full, untruncated API
request/response traffic for every call.

Invoke it with `uv run python -m tests.smoke.sdk [--group ...]` (see the `Makefile`'s
`smoke-test` target); see `TEST_DATA.md` for the prerequisite APM resources.

---

## Package structure

```
smoke/sdk/
├── __init__.py
├── __main__.py        ← entry point: argv parsing, --group/_ORDER/_PHASES dispatch
├── _client_env.py       ← load_sdk_env(): .env -> SdkEnv (mirrors smoke.cli._cli_runner.load_cli_env)
├── _context.py            ← SmokeContext, DOMAINS, M365_SCOPES,
│                              ctx.call / ctx.call_expect_error / ctx.check / ctx.skip / ctx.na
│                              (DomainStats/StepResult/step_slug come from smoke/_context.py)
├── _trace.py               ← install_trace(): instance-level _do_login/_request wrapping -> api_trace.jsonl
├── _serialize.py            ← to_jsonable(): dataclass/enum/datetime -> JSON-safe
├── _report.py                  ← SDK wording for write_index(); rendering lives in smoke/_report.py
├── TEST_DATA.md                   ← APM test data prerequisites checklist (SDK-shaped)
├── README.md                       ← this file
├── phases/
│   ├── __init__.py
│   ├── _shared.py            ← lock/unlock + backup/cancel roundtrip helpers, ZERO_UUID / SENTINEL_NAME
│   ├── _infra.py             ← apm.get_site_info(), apm.backup_servers, apm.remote_storages, apm.hypervisors
│   ├── _plan.py                ← apm.plans, apm.retirement_plans, apm.tiering_plans
│   ├── _machine.py               ← apm.machine.workloads (list/get/versions/lock/backup/change_plan round trips)
│   ├── _m365.py                    ← apm.m365.workloads per M365WorkloadType + exchange/group export + change_plan round trips
│   ├── _m365_auto_backup_rule.py     ← apm.m365.auto_backup_rules CRUD + collab settings roundtrip
│   ├── _gws.py                          ← apm.gws.workloads per GWSWorkloadType + change_plan round trips (no export feature)
│   ├── _gws_auto_backup_rule.py           ← apm.gws.auto_backup_rules CRUD + collab settings + protected-account-types roundtrips
│   ├── _activity.py                         ← apm.activities.backup / apm.activities.restore
│   └── _log.py                                ← apm.logs (DP-type servers only)
└── reports/                ← gitignored: <UTC timestamp>/{index,<domain>}.md + api_trace.jsonl
```

Each `phases/_<domain>.py` corresponds to one entry in `DOMAINS` (`_context.py`) and one entry
in `_PHASES`/`_ORDER` (`__main__.py`). See `tests/smoke/README.md`'s "Shared conventions" for
the leading-underscore file naming rule.

---

## `SmokeContext` API (`_context.py`)

Every phase's `run(ctx: SmokeContext) -> None` interacts with the live APM exclusively through
these methods:

- **`async ctx.call(domain, step, coro, *, expect_error=None, note=None) -> T | None`** — awaits
  `coro()` (a zero-arg callable returning an SDK coroutine, e.g. `lambda:
  apm.machine.workloads.list(limit=500)`), tags every API call made during its execution
  (including calls inside an internal `asyncio.gather()`) as `step` in `api_trace.jsonl`, and
  records the result into `reports/<domain>.md`. Returns the awaited result, or `None` if the
  call raised `expect_error` (recorded as expected) or any other `APMError` (recorded as
  `unexpected` in `ctx.stats`, but not re-raised — the phase continues).

- **`async ctx.call_expect_error(domain, step, coro, expect_error, *, note=None) ->
  APMError | None`** — for steps that are *expected* to raise. Returns the caught exception (so
  the phase can `ctx.check` its `.resource_type`/`.error_code`/etc.), or `None` if `coro()`
  raised nothing — labeled `FAILED: expected error not raised` in the `index.md` checklist.
  A no-raise does **not** count toward `unexpected`, so always pair the call with a
  `ctx.check` on the returned exception. A *different* `APMError` than `expect_error` is
  recorded as `unexpected`.

- **`ctx.check(domain, step, condition, *, note="") -> bool`** — records a PASS/FAIL assertion
  (no API call) into the `index.md` checklist and
  `ctx.stats[domain].checks_passed`/`checks_failed` (no `<domain>.md` section is written).
  Returns `condition` unchanged. Use this for every in-process correctness assertion — never a
  bare `assert` (an `AssertionError` would abort the whole run).

- **Composition helpers built on the above** — `ctx.check_exc_attr(domain, step, exc, attr,
  expected)` (check one attribute of a caught exception); `ctx.call_expect_not_found(...)`
  (a `call_expect_error(ResourceNotFoundError)` plus the two resource-field checks, with
  auto-derived step names); `ctx.guard_error(...)` (an expected-error call plus its two
  attribute checks, or a skip of all three steps when the precondition is absent);
  `ctx.call_expect_value_error(...)` (for client-side `ValueError` validation paths). See
  their docstrings in `_context.py` for signatures.

- **`ctx.skip(domain, step, reason)`** — records a conditional skip as a `SKIPPED: <reason>`
  entry in the `index.md` checklist and increments `ctx.stats[domain].skipped` — see
  `tests/smoke/README.md`'s "Shared conventions" for when to use this instead of a hard
  failure (here, a fresh/empty APM should produce `unexpected: 0` **and** `checks_failed: 0`).
  **`ctx.skip_remaining(domain, steps, reason=...)`** skips every step in `steps` not already
  emitted in this domain — use it when one missing prerequisite invalidates a whole block of
  planned steps.

- **`ctx.na(domain, step, reason)`** — records a step as not applicable (an `N/A: <reason>`
  checklist entry), and increments `ctx.stats[domain].na`. Use this when the step exists in
  the design but is inapplicable given the current state — e.g. a server-list call omitted
  because an earlier flow in the same phase already fetched the same data. Distinct from
  `ctx.skip`: `ctx.skip` signals that prerequisite data is absent; `ctx.na` signals that the
  step is intentionally not performed, not that data is missing.

- **`ctx.data: dict[str, Any]`** — the cross-phase registry; see next section.

- **`ctx.stats: dict[str, DomainStats]`** — `ran`/`skipped`/`na`/`unexpected`/`checks_passed`/
  `checks_failed` counters per domain, written into `index.md`'s summary table by
  `_report.write_index`.

- **`DOMAINS`** and **`M365_SCOPES`** module constants — `DOMAINS` must stay in sync with
  `__main__.py`'s `_PHASES`/`_ORDER` keys (one entry per phase file). `M365_SCOPES` is the
  **enum-definition order** of `M365WorkloadType` (`exchange, onedrive, chat, sharepoint, teams,
  group`) — note this **differs** from the CLI smoke tool's CLI-subcommand order (`group` is 4th
  there); this is an intentional deviation, not a bug.

> **Implementation note:** `__post_init__` accesses `self.apm._session` to install the trace
> recorder. This reaches past the SDK's public API — same accepted-exception pattern as
> `tests/integration/conftest.py`'s `install_replay(client._session, cassette)` and
> `tests/cassette_lib.py`.

---

## The `ctx.data` registry

Phases write discovered objects into `ctx.data` so later phases (which run after them in
`_ORDER`) can use them instead of re-querying. This is a **living registry** — when you add a
new `ctx.data[...] = ...` assignment, add a row to this table.

| Key | Type | Set by (`ctx.call` step) | Read by |
|---|---|---|---|
| `smoke_creds` | `SmokeCreds` | `__main__.py` (from `tests/smoke/smoke_creds.toml`, before any phase runs) | infra (remote-storage CRUD round trips) |
| `site_info` | `SiteInfo` | infra: `infra.site_info.get` | infra (same-phase checks) |
| `servers` | `list[BackupServer]` | infra: `infra.servers.list[all]` | infra (same-phase checks); m365_rule (backup server namespace; falls back to its own list call) |
| `dp_servers` | `list[BackupServer]` (filtered `server_type == BackupServerType.DP`) | infra (derived, no extra call) | log — whole phase skipped if empty |
| `remote_storages` | `list[RemoteStorage]` | infra: `infra.remote_storages.list` | plan (`plan.tiering.check[destination_resolution]`) |
| `hypervisors` | `list[Hypervisor]` | infra: `infra.hypervisors.list` | — (available) |
| `machine_workloads` | `list[MachineWorkload]` | machine: `machine.workloads.list[all]` | activity (workload-filter checks); same-phase `_run_change_plan_roundtrips` candidate search |
| `retired_machine_workloads` | `list[MachineWorkload]` | machine: `machine.workloads.list[retired]` | same-phase `machine.change_plan[retired_noop]` |
| `machine_versions` | `list[WorkloadVersion]` | machine: `machine.versions.list[search]` | activity (`activity.backup.get_by_version`) |
| `_fs_crud_server_result` | `tuple[list[BackupServer], int] \| None` | machine: `machine.fs_crud.active.backup_servers.list` (or N/A if infra phase ran) | machine: `_run_fs_crud_retired` (server reuse) |
| `m365_tenant` | `M365TenantInfo \| None` | m365: `m365.saas.list` (derived) | m365 (same-phase guard; always set, even if `None`); m365_rule (falls back to its own tenant lookup) |
| `m365_workloads` | `dict[str, list[M365Workload]]` | m365: `m365.<scope>.list[all]` | same-phase `_run_change_plan_roundtrips` candidate search |
| `m365_retired_workloads` | `dict[str, list[M365Workload]]` | m365: `m365.<scope>.list[retired]` | same-phase `m365.change_plan[retired_noop]` |
| `m365_exports` | `dict[str, M365ExportStartResult \| None]` | m365: `m365.<scope>.export.start` | — (available) |
| `gws_domain` | `GWSDomainInfo \| None` | gws: `gws.saas.list` (derived) | gws (same-phase guard; always set, even if `None`); gws_rule (falls back to its own domain lookup) |
| `gws_workloads` | `dict[str, list[GWSWorkload]]` | gws: `gws.<scope>.list[all]` | same-phase `_run_change_plan_roundtrips` candidate search |
| `gws_retired_workloads` | `dict[str, list[GWSWorkload]]` | gws: `gws.<scope>.list[retired]` | same-phase `gws.change_plan[retired_noop]` |
| `backup_activities` | `list[BackupActivity]` | activity: `activity.backup.list[history]` | — (available) |
| `restore_activities` | `list[RestoreActivity]` | activity: `activity.restore.list[history]` | — (available) |
| `protection_plans` | `list[ProtectionPlan]` | plan: `plan.protection.list[all]` | machine (`_run_change_plan_roundtrips`); m365 (`_run_change_plan_roundtrips`); gws (`_run_change_plan_roundtrips`) |
| `retirement_plans` | `list[RetirementPlan]` | plan: `plan.retirement.list` | machine (`_run_change_plan_roundtrips`); m365 (`_run_change_plan_roundtrips`); gws (`_run_change_plan_roundtrips`) |
| `tiering_plans` | `list[TieringPlan]` | plan: `plan.tiering.list` | plan (same-phase checks) |

Entries marked "available" are populated for future phases/checks to consume — `ctx.data` is a
free-form registry, not a fixed contract, but every existing key must stay documented here.

---

## Phase-file pattern

Every `phases/_<domain>.py` follows this shape:

1. A module docstring describing the phase's scope, which `ctx.data` keys it reads/writes, and
   any whole-phase skip condition (e.g. `_log.py` is skipped entirely if
   `ctx.data["dp_servers"]` is empty; `_m365.py` is skipped entirely if no tenant has
   `category == WorkloadCategory.M365`).
2. `DOMAIN = "<name>"` — must match one entry in `DOMAINS`.
3. `async def run(ctx: SmokeContext) -> None` — the entry point called from `__main__.py`,
   decomposed into `_run_<subcommand>(ctx, ...)` helpers for readability.
4. **`step` naming**: `<domain>.<resource>.<action>[<variant>]` for calls (e.g.
   `machine.versions.get_latest`, `infra.servers.get_by_name[search]`,
   `activity.backup.list[machine_workload_filter]`) and `<domain>.<resource>.check[<name>]` for
   `ctx.check` assertions (e.g. `machine.versions.check[lock_roundtrip]`,
   `plan.tiering.check[destination_resolution]`).
5. **Unpacking `list()` results**: every collection `list(...)` returns `(items, total)`.
   Always unpack via
   `items, total = result if result is not None else ([], 0)` before storing into `ctx.data` or
   indexing — `result` is `None` if the call raised (and was recorded as `unexpected`).
6. **Multi-call "roundtrip" steps**: when a single logical operation needs several SDK calls in
   sequence (e.g. lock → get → unlock → get), write a private `async def
   _foo_roundtrip(apm, ...) -> ...` helper and pass it as a single `lambda: _foo_roundtrip(...)`
   to one `ctx.call`, so every sub-call shares one `step` tag in `api_trace.jsonl`. See
   `lock_unlock_roundtrip` / `backup_cancel_roundtrip` in `phases/_shared.py` (used by both
   `_machine.py` and, per scope, `_m365.py`).
7. Conditional prerequisites (empty `list()`, no DP server, no `v0.locations`, no M365 tenant,
   `w0.is_retired`, ...) are `ctx.skip(...)` for the dependent steps/checks, never a hard
   failure.

---

## Round-trip operations

All operations this tool can run fall into three categories:

| Category | Behavior | Examples |
|---|---|---|
| **Read-only / round-trip** | Always run; round trips fully restore state | all `list`/`get`/`get_by_name`/`get_site_info`; `lock_version()`→`unlock_version()` (or vice versa, detecting current state first); `backup_now()`→`cancel_backup()`; `apm.machine.workloads.change_plan(...)` / `apm.m365.workloads.change_plan(...)` / `apm.gws.workloads.change_plan(...)` switch→restore; `apm.machine.workloads.change_plan(...)` / `apm.m365.workloads.change_plan(...)` / `apm.gws.workloads.change_plan(...)` no-op re-apply; m365 export `start`→`list`→`download_url.get`→`cancel`; remote storage `add`→`update`→`delete` (per `[[remote_storage]]` entry in `smoke_creds.toml`, see `TEST_DATA.md`) |
| **Consumes prepared test data** | Runs only when the tester prepared disposable data (see `TEST_DATA.md`); skipped otherwise | `activities.restore.cancel(...)` on an in-progress restore the tester started |
| **Excluded — never automated** | Irreversible on real data. M365/GWS `retire()` is checked manually through the CLI (`tests/smoke/cli/MANUAL_TESTS.md` — the CLI drives the same SDK method); M365/GWS `delete()` has unit-test coverage only (the `m365_rule`/`gws_rule` phases still call it defensively for best-effort cleanup of their own disposable test plans, not as a general round trip) | M365 `retire()`, `delete()`; GWS `retire()`, `delete()` |

There is no opt-in flag for any of these — every round trip above runs by default on every
invocation (missing prerequisite data is `ctx.skip(...)`-ed per the shared convention).

**File Server CRUD lifecycle** (`_run_fs_crud_active`/`_run_fs_crud_retired` in
`phases/_machine.py`, using a synthetic fake IP and disposable protection/retirement plans so
the round trip never touches real infrastructure): two full lifecycles run back to back —
Flow 1 (active) `add` → `update` → `backup_now`/`cancel_backup` → `change_plan` → `delete`;
Flow 2 (retirement) `add` → `retire` → `change_plan` (retirement) → `delete`. Both flows clean
up their disposable plans and the File Server workload itself before the phase returns.

**`machine.change_plan[switch]`/`[restore]`** (`_run_change_plan_roundtrips` in
`phases/_machine.py`, using `ctx.data["protection_plans"]` from the plan phase, which runs
first): iterates the workloads just listed and, for each one, tries to resolve both its current
Protection Plan (by `name == workload.plan.name`) and a different, non-`is_immutable`
MACHINE-category Protection Plan. The first workload for which both resolve is switched to the
alternative plan and back via `apm.machine.workloads.change_plan(...)`. If there are no Machine
Workloads, no MACHINE-category Protection Plans, or no such combination exists, the round trip
is skipped.

**`machine.change_plan[retired_noop]`** (same function, using `ctx.data["retirement_plans"]`):
looks for a retired Machine Workload among those just listed whose `plan.name` matches the
`name` of an existing Retirement Plan, and re-applies that plan via
`apm.machine.workloads.change_plan(...)` (a no-op state change). Skipped if no such workload/plan
pair exists.

**`m365.change_plan[switch]`/`[restore]`/`[retired_noop]`** (`_run_change_plan_roundtrips` in
`phases/_m365.py`, run once after all scopes have been listed): same shape as the machine-phase
round trips above, but candidates are searched across the combined `ctx.data["m365_workloads"]` /
`ctx.data["m365_retired_workloads"]` workloads from every scope in `ctx.m365_scopes`, and
M365-category Protection Plans are matched instead of MACHINE-category ones.
`apm.m365.workloads.change_plan(...)` is called in place of the machine collection's method.

**m365 export round trip** (`phases/_m365.py`, exchange/group scopes only): runs only if the
scope's first workload has a backup version with a non-empty `portal_version_id` — otherwise all
four export steps are skipped.
The `download_url.get` step polls up to 5 times (2s apart) for the export to leave
`M365ExportStatus.PREPARING`, using `expect_error=ResourceNotReadyError` if it never does.

**`gws.change_plan[switch]`/`[restore]`/`[retired_noop]`** (`_run_change_plan_roundtrips` in
`phases/_gws.py`, run once after all 5 `GWSWorkloadType` sub-types have been listed): same shape
as the machine/m365-phase round trips above, but candidates are searched across the combined
`ctx.data["gws_workloads"]`/`ctx.data["gws_retired_workloads"]` workloads from every sub-type, and
GWS-category Protection Plans are matched instead of MACHINE/M365-category ones.
`apm.gws.workloads.change_plan(...)` is called in place of the other collections' method. Unlike
M365, GWS has no export feature — there is no `gws export round trip` counterpart.

**Remote storage `add`→`update`→`delete`** (`phases/_infra.py`, one full roundtrip per
`[[remote_storage]]` entry in `smoke_creds.toml`; skipped entirely when that file is absent):
each entry runs `retirement_plan[name/create]` (a temporary 9999-day retirement plan, needed to
satisfy APM's catalog-relinking requirement) → `add[name]` → `add[name/duplicate]` (expects
`RemoteStorageConflictError`) → `get[name]` → `check[name/fields]` → `update[name]` →
`check[name/post_update]` → an in-use delete check via a temporary tiering plan (expects
`RemoteStorageInUseError`; skipped without a DP backup server) → `delete[name]` →
`get[name/post_delete]` (expects `ResourceNotFoundError`) → `retirement_plan[name/delete]`
(always runs, regardless of intermediate failures, to avoid leaking the temporary plan).

The unmanaged-catalog check runs for every entry: `add[name/unmanaged_raises]` first adds the
vault **without** a retirement plan. On a vault already holding catalogs from a previous
registration this must raise `RemoteStorageUnmanagedCatalogError` (`vault_name`/`catalog_count`
are checked), and the `add[name]` step that follows adopts those catalogs; on a vault with no
pre-existing catalogs the probe registration succeeds instead — it is rolled back and the three
steps are recorded as skipped. Deleting the storage at the end of the roundtrip leaves adopted
catalogs unmanaged again, so a vault used this way stays reusable across runs. When
`relink_encryption_key` is set on an entry, two more steps run: `add[name/no_key]` (expects
`RemoteStorageEncryptionMismatchError`) and `check[name/encryption_key]`.

See `TEST_DATA.md` for the data that makes each round trip exercised rather than skipped.

---

## `--group` dispatch (`__main__.py`)

```python
_ORDER = ("infra", "plan", "machine", "m365", "m365_rule", "gws", "gws_rule", "activity", "log")
_PHASES = {"infra": _infra, "plan": _plan, "machine": _machine, "m365": _m365,
           "m365_rule": _m365_auto_backup_rule, "gws": _gws,
           "gws_rule": _gws_auto_backup_rule, "activity": _activity, "log": _log}
```

See `tests/smoke/README.md`'s "Shared conventions" for the general `--group`/`--m365-scopes`
semantics. The order above reflects this tool's own `ctx.data` dependencies: `infra` populates
`dp_servers`/`remote_storages` before `log`/`plan`; `plan` populates
`protection_plans`/`retirement_plans` before `machine`'s, `m365`'s, and `gws`'s `change_plan`
round trips; `machine` populates `machine_workloads`/`retired_machine_workloads`/
`machine_versions` before `activity`; `m365_rule` follows `infra` (reads `ctx.data["servers"]`
for the backup server namespace) and `m365` (same tenant dependency, consistent ordering) and
creates its own test M365 plans rather than consuming from `ctx.data`; `gws`/`gws_rule` follow
the same pattern as `m365`/`m365_rule`, one category later.

`tests/smoke/smoke_creds.toml` is loaded automatically if it exists; when absent, all CRUD
steps needing endpoint credentials (remote-storage registration in the infra phase) are
skipped. Copy `tests/smoke/smoke_creds.toml.example` to get started. The loaded value is
exposed to phases as `ctx.data["smoke_creds"]`.

---

## Reports (`_report.py`, `_trace.py`)

Each run creates `reports/<UTC timestamp>/`:

- **`index.md`** — host/username/group/m365-scopes, a per-domain
  `ran`/`skipped`/`na`/`unexpected`/`checks_passed`/`checks_failed` summary table, a per-step
  **checklist** (call results, check PASS/FAIL lines, `SKIPPED:`/`N/A:` reasons — each with
  its optional `note=` and a link into `<domain>.md` when a detail section exists), links to
  each `<domain>.md` and `api_trace.jsonl`, and pointers to `TEST_DATA.md` and the CLI manual
  checklist (`tests/smoke/cli/MANUAL_TESTS.md`).
- **`<domain>.md`** — one section per `ctx.call`/`ctx.call_expect_error` call (checks, skips,
  and N/A entries appear only in the `index.md` checklist): the JSON-serialized result
  (`to_jsonable`; list results are truncated to the first 5 items, with a
  "…(N more items, M total)" marker) or an error summary
  (`<ExceptionClass>: <message> (expected)` / `(FAILED)` plus the raw error body).
- **`api_trace.jsonl`** — one JSON object per SDK-level API call, in the order issued, tagged
  with the `step` active when it was made (via `_trace.current_step`, a `contextvars.ContextVar`
  — correctly attributes calls made inside an internal `asyncio.gather()`, since Python copies
  the current `Context` into spawned tasks):
  ```json
  {"kind": "api", "method": "GET", "path": "/api/v1/workload/device_workload",
   "params": {"limit": 500, "offset": 0}, "body": null,
   "result": {"type": "data", "status": null, "data": { "...": "full untruncated body" }},
   "seq": 17, "step": "machine.workloads.list[all]", "timestamp": "..."}
  ```
  An error record has `"result": {"type": "error", "error_class": "...", "message": "...",
  "error_code": ..., "status": ..., "response_body": ..., "resource_type": ..., "resource_id":
  ...}` (the last two only for `_ResourceError` subclasses). Unlike the CLI smoke tool's `--debug`
  trace (truncated at 4096 bytes), this trace is **full and untruncated** — `result.data`/
  `result.response_body` are the raw (camelCase) API JSON, not SDK-parsed models.

  > **Note:** No HTTP headers or cookies are captured, so session tokens never appear in
  > `api_trace.jsonl`.

---

## How to extend

Follow `tests/smoke/README.md`'s generic "How to extend" recipe. The one SDK-specific detail:
adding a step means adding a `ctx.call(...)`/`ctx.call_expect_error(...)` call (and any
`ctx.check(...)`s on its result) in the relevant `_run_<subcommand>` helper, following the
`step` naming convention above. An irreversible operation with no CLI equivalent relies on
unit-test coverage instead, recorded in the "Round-trip operations" table above (one with a
CLI equivalent goes in `tests/smoke/cli/MANUAL_TESTS.md`, since the CLI drives the same SDK
method).

**After any change** — `uv run python -m tests.smoke.sdk --group <domain>` against your `.env`
test machine; review the regenerated `reports/<ts>/<domain>.md` and `index.md` for
`unexpected` calls or `checks_failed` (also run `--group all` if the change touches `ctx.data`
threading across phases). Then follow `tests/smoke/README.md`'s lint/type-check gate.

---

## Relationship to `TEST_DATA.md`

`TEST_DATA.md` documents the prerequisite APM resources (see `tests/smoke/README.md`'s note
above).

Irreversible M365/GWS operations (`retire()`, `delete()`) are deliberately never run by this
tool. `retire()` is checked by hand through the CLI against disposable workloads — see
`tests/smoke/cli/MANUAL_TESTS.md`; `index.md` links to that checklist on every run as a
reminder.
