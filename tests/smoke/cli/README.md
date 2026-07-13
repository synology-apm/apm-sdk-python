# CLI Live Smoke-Test Tool — Maintainer Guide

This is the design contract for the CLI live smoke-test tool: how it is structured, the
conventions every phase follows, and how to extend it when `synology-apm` gains new commands
or options. Read this before adding to or modifying anything under this directory.

---

## Purpose and relationship to other test layers

| Layer | Drives | Data source | Offline? |
|---|---|---|---|
| `tests/unit/` | In-process Typer `CliRunner` (mocked SDK) | fixtures | yes |
| `tests/integration/` | SDK methods directly (`apm.machine.workloads.list()`) | `tests/cassettes/` | yes (replay) |
| **This tool** | The real `synology-apm` binary, via subprocess | live, `.env`-configured APM | no |

Neither `tests/unit/` nor `tests/integration/` exercises the actual CLI binary's argv
parsing, `table`/`json` rendering, or exit codes against a live server. This tool fills that
gap: it runs every reversible command in dependency order, for both `-o table` and `-o json`,
and side-records the raw API traffic (via `--debug`) for AI/human review.

See root `CLAUDE.md` → "Common Commands" for how to invoke it
(`uv run python -m tests.smoke.cli [--group ...]`). It must be run from the repo
root (it is a package under `tests/`, invoked via `python -m`).

> **Note:** Before running against a new APM test instance, see `TEST_DATA.md` for the
> resources (backup servers, workloads, plans, ...) that should already exist so that as much
> of the CLI as possible is exercised rather than skipped.

> **Note:** `reports/` is gitignored, with the same trust model as
> `tests/cassettes/` — real hostnames, workload names, and IDs from your `.env`-configured
> APM are fine to appear there, but nothing under `reports/` is ever committed. This
> `README.md`, `MANUAL_TESTS.md`, and all source files in this directory **are** committed
> and must follow CLAUDE.md's Language Policy, Documentation Style, and Example Data
> Conventions (English, no decorative emoji, no real hostnames/IDs in code comments or
> docstrings).

---

## Package structure

```
smoke/cli/
├── __init__.py
├── __main__.py        ← entry point: argv parsing, --group/_ORDER/_PHASES dispatch
├── _context.py         ← SmokeContext, DOMAINS, M365_SCOPES, parse_json()
│                          (DomainStats/StepResult/step_slug come from smoke/_context.py)
├── _cli_runner.py       ← CliRunner/CliEnv/load_cli_env(): subprocess wrapper, .env credentials
├── _debug_trace.py       ← parse_debug_trace(): --debug stderr -> structured API call records
├── _report.py            ← CLI wording for write_index(); rendering lives in smoke/_report.py
├── MANUAL_TESTS.md        ← irreversible `retire` commands (never automated)
├── TEST_DATA.md            ← APM test data prerequisites checklist
├── README.md                ← this file
├── phases/
│   ├── __init__.py
│   ├── _config.py         ← config set/show/clear (sandboxed HOME)
│   ├── _infra.py           ← infra info/server/storage/hypervisor
│   ├── _plan.py             ← plan protection/retirement/tiering
│   ├── _machine.py           ← machine list/get/version list/version get
│   ├── _saas_m365.py           ← saas list + m365 <scope> list/get/version for all 6 scopes
│   ├── _activity.py             ← activity backup/restore list/get
│   └── _log.py                   ← log activity/drive/connection/system (DP servers only)
└── reports/               ← gitignored: <UTC timestamp>/{index,<domain>}.md + api_trace.jsonl
```

Each `phases/_<domain>.py` corresponds to one entry in `DOMAINS` (`_context.py`) and one
entry in `_PHASES`/`_ORDER` (`__main__.py`). Files are named with a leading underscore so
pytest's default `test_*.py` collection ignores them — same precedent as
`tests/cassette_lib.py`.

---

## `SmokeContext` API (`_context.py`)

Every phase's `run(ctx: SmokeContext) -> None` interacts with the live APM exclusively
through these methods:

- **`ctx.run(domain, step, args, *, output_format=None, expect_codes=(0,), env_overrides=None,
  stdin=None, timeout=None, note="") -> CliResult`**
  Runs `uv run synology-apm --no-input --debug <args> [-o <output_format>]` as a subprocess,
  records every captured API call to `reports/api_trace.jsonl`, and updates
  `ctx.stats[domain]`. A `### <step>` section is written to `reports/<domain>.md` only when
  the run captured API calls or the exit code was unexpected; the PASSED/FAILED result and
  `note=` land in `index.md`'s checklist (see "Reports" below). `expect_codes` controls
  whether a non-zero exit is flagged as "unexpected" — use it for commands whose exit code
  legitimately depends on live server state (e.g. `(0, 1)` for a cancel that may have nothing
  to cancel).

- **`ctx.run_both(domain, step, args, *, expect_codes=(0,), env_overrides=None, note=None) ->
  (CliResult, CliResult)`**
  Calls `ctx.run` twice — once with `-o table`, once with `-o json` — appending `[table]`/
  `[json]` to `step` (e.g. `step="infra.server.get[search]"` produces report steps
  `infra.server.get[search][table]` and `infra.server.get[search][json]`). Use this for every
  `list`/`get` invocation (including filter variants) so both renderers are exercised; use
  `ctx.run` directly only for single-format steps: `[page-all]` (json/NDJSON), `[csv]`,
  `[yaml]`, `[verbose]` (table-only feature), and the sandboxed `config` commands.

- **`ctx.run_python(domain, step, script_args, *, expect_codes=(0,), env_overrides=None,
  note="") -> CliResult`** — like `ctx.run` but executes a Python script as the subprocess
  instead of the CLI binary; recorded the same way.

- **`ctx.skip(domain, step, reason)`** — records a conditional skip (e.g. "no DP-type server
  found") as a `SKIPPED: <reason>` entry in `index.md`'s checklist and increments
  `ctx.stats[domain].skipped`. Use this, not a hard failure, whenever a step's prerequisite
  data may legitimately be absent on a given APM (empty workload lists, no retired workloads,
  no in-progress restore, etc.).

- **`pick_backed_up_workload(workloads) -> dict`** (module-level) — picks the `get`/`version`
  target from a parsed `list` result: prefers a backed-up workload with an unambiguous name
  (so version steps don't skip and search-mode steps stay deterministic); used by the machine
  and m365 phases.

- **`parse_json(result: CliResult) -> Any | None`** (module-level) — parses `result.stdout`
  as JSON, returning `None` if it isn't valid JSON (e.g. a `table`-format result, or a
  non-zero exit with no JSON body). Always call this on the `[json]` half of a
  `run_both(...)` pair before indexing into the result.

- **`ctx.data: dict[str, Any]`** — the cross-phase registry; see next section.

- **`ctx.stats: dict[str, DomainStats]`** — `ran`/`skipped`/`unexpected` counters per domain,
  written into `index.md`'s summary table by `_report.write_index`.

- **`DOMAINS`** and **`M365_SCOPES`** module constants — `DOMAINS` must stay in sync with
  `__main__.py`'s `_PHASES`/`_ORDER` keys (one entry per phase file); `M365_SCOPES` is the
  default for `--m365-scopes` and the scope loop in `phases/_saas_m365.py`.

---

## The `ctx.data` registry

Phases write discovered IDs/names/objects into `ctx.data` so later phases (which run after
them in `_ORDER`) can use them instead of re-querying. This is a **living registry** — when
you add a new `ctx.data[...] = ...` assignment, add a row to this table.

| Key | Type | Set by (`ctx.run` step) | Read by |
|---|---|---|---|
| `servers` | `list[dict]` | infra: `infra.server.list` | — (available for future use) |
| `dp_servers` | `list[dict]` | infra: `infra.server.list[dp]` | log — whole phase skipped if empty |
| `machine_workloads` | `list[dict]` | machine: `machine.list[all]` | — (available) |
| `saas_tenants` | `list[dict]` | m365: `saas.list` | — (available) |
| `m365_workloads[scope]` | `dict[str, list[dict]]` | m365: `m365.<scope>.list` | — (available) |
| `retired_m365_workloads[scope]` | `dict[str, list[dict]]` | m365: `m365.<scope>.list[retired]` | — (available) |
| `m365_versions[scope]` | `dict[str, list[dict]]` | m365: `m365.<scope>.version.list[direct]` | — (available) |
| `backup_activities` | `list[dict]` | activity: `activity.backup.list[history]` | — (available) |
| `restore_activities` | `list[dict]` | activity: `activity.restore.list[history]` | — (available) |
| `protection_plans` | `list[dict]` | plan: `plan.protection.list[all]` | — (available) |
| `retirement_plans` | `list[dict]` | plan: `plan.retirement.list` | — (available) |
| `tiering_plans` | `list[dict]` | plan: `plan.tiering.list` | — (available) |

The per-scope dicts (`m365_workloads`, `retired_m365_workloads`, `m365_versions`) are built
with `ctx.data.setdefault("<key>", {})[scope] = ...` since the m365 phase loops over
`ctx.m365_scopes`.

Dict values are the parsed `-o json` array elements — the same fields documented as CLI JSON
output in `packages/synology-apm-cli/src/synology_apm/cli/README.md` (e.g. `name`,
`workload_id`, `namespace`, `tenant_id`, `plan_id`, `plan_name`, `backup_server_id`,
`version_id`, `locked`, `activity_id`, `is_retired`).

---

## Phase-file pattern

Every `phases/_<domain>.py` follows this shape:

1. A module docstring describing the phase's scope and any whole-phase skip condition (e.g.
   `_log.py` is skipped entirely if `ctx.data["dp_servers"]` is empty).
2. `DOMAIN = "<name>"` — must match one entry in `DOMAINS`.
3. `def run(ctx: SmokeContext) -> None` — the entry point called from `__main__.py`,
   decomposed into `_run_<subcommand>(ctx, ...)` helpers for readability.
4. **`step` naming**: `<domain>.<resource>.<action>[<variant>]`, e.g.
   `machine.version.get[direct]`, `infra.server.list[page-all]`,
   `m365.exchange.version.list`. `run_both` appends `[table]`/`[json]` on top of whatever
   step string is passed.
5. **`--page-all` and csv/yaml**: exercise each once per domain, on a representative `list` —
   `--page-all` with `note="Exercises NDJSON streaming for --page-all."` and
   `output_format="json"`; plus one `[csv]` and one `[yaml]` run of the same base `list`.
6. **State-mutating commands are excluded**: lock/unlock, backup/cancel, change-plan, and
   restore cancel are covered by the SDK smoke test (`tests/smoke/sdk`), and the CLI's own
   wiring for those commands by the unit tests (`tests/unit/cli/commands/`) — CLI phases run
   only the `list`/`get` read paths.
7. **`note=`** is appended to the step's entry in `index.md`'s checklist for the human/AI
   reviewer reading the report — it is not passed to the CLI. Use it to explain *what the
   reviewer should expect to see* (e.g. `"Exercises NDJSON streaming for --page-all."`) or *why* a non-default
   `expect_codes` is acceptable.
8. Conditional prerequisites (empty `list`, no DP server, no retired workload, no
   in-progress restore) are `ctx.skip(...)`, never a hard failure — a fresh/empty APM should
   still produce a clean `index.md` with `unexpected: 0` everywhere.

---

## Command categories

All commands this tool can run fall into two categories:

| Category | Behavior | Examples |
|---|---|---|
| **Read-only** | Always run | all `list`/`get`/`show`/`info` across every domain |
| **Excluded — manual only** | Irreversible, never automated, see `MANUAL_TESTS.md` | `machine retire`, `m365 <scope> retire` |

State-mutating commands (`version lock`/`unlock`, `backup`/`cancel`, `change-plan`,
`activity restore cancel`, `infra server change-plan`) are not run by this tool — the SDK
smoke test (`tests/smoke/sdk`) covers those code paths against the live server, and the
CLI's own wiring for those commands is covered by the unit tests
(`tests/unit/cli/commands/`). Steps whose prerequisite data is not present on
the APM are recorded as `ctx.skip(...)` in the relevant `<domain>.md`, with a reason
describing what data was not found — this is expected on a sparsely-populated APM, not a
failure. See `TEST_DATA.md` for the data that makes each step exercised rather than
skipped.

---

## `--group` dispatch (`__main__.py`)

```python
_ORDER = ("config", "infra", "plan", "machine", "m365", "activity", "log")
_PHASES = {"config": _config, "infra": _infra, "machine": _machine,
           "m365": _saas_m365, "saas": _saas_m365,  # "saas" is an alias for "m365"
           "activity": _activity, "plan": _plan, "log": _log}
```

`--group all` (default) runs all phases in `_ORDER`; `--group <domain>` runs exactly one.
The order reflects `ctx.data` dependencies: `infra` populates `dp_servers` before `log`.

> **Note:** Running a single `--group <domain>` is useful for fast iteration, but any step
> that depends on a `ctx.data` key from an earlier phase will find it empty and
> `ctx.skip(...)` gracefully. To exercise the full cross-phase data flow (e.g. the `log`
> phase reading `dp_servers` from the infra phase), use `--group all`.

`--m365-scopes` (default: all of `M365_SCOPES`) limits which scopes `phases/_saas_m365.py`
loops over — useful for iterating on one scope.

The credential file (`tests/smoke/smoke_creds.toml`) is consumed only by the **SDK** smoke
tool's CRUD round trips (see `tests/smoke/sdk/README.md`) — the CLI phases are all
read-only and do not need it.

---

## Reports (`_report.py`, `_debug_trace.py`)

Each run creates `reports/<UTC timestamp>/`:

- **`index.md`** — host/username/group/flags, a per-domain `ran`/`skipped`/`unexpected`
  summary table, a per-step **checklist** (PASSED / `FAILED: exit N (expected ...)` /
  `SKIPPED: <reason>`, each with its optional `note=` and a link into the step's
  `<domain>.md` section when one exists), links to each `<domain>.md`, and a pointer to
  `MANUAL_TESTS.md`.
- **`<domain>.md`** — a `### \`<step>\`` section for each `ctx.run`/`ctx.run_both` call that
  captured API calls or exited unexpectedly (steps with neither produce no section — their
  result lives only in the `index.md` checklist). Each section lists the captured API calls
  (method + path + HTTP status, query params, request body, response — truncated responses
  are marked); `stdout` is included only when the exit code was unexpected.
- **`api_trace.jsonl`** — one JSON object per API call captured via `--debug`, in the order
  issued:
  ```json
  {"step": "machine.list[all][json]", "command": ["uv","run","synology-apm",...],
   "output_format": "json", "seq": 1, "method": "GET", "url": "...",
   "headers": {...}, "params": {...}, "body": null, "status": 200,
   "duration": 0.42, "response": {...}}
  ```
  A truncated response body (the CLI's `--debug` output truncates large bodies) is stored as
  a raw string with `"truncated": true` instead of a parsed `"response"`.

> **Note:** `--debug` does not print response headers/cookies, so session tokens are never
> captured in `api_trace.jsonl`. This is mentioned in `index.md` so a reviewer doesn't expect
> cookie/token data there.

---

## How to extend

**Add a command/option to an existing phase** — add a `ctx.run(...)`/`ctx.run_both(...)`
call in the relevant `_run_<subcommand>` helper (or a new helper called from `run`),
following the `step` naming convention above. If the command's output feeds a later phase,
add it to the `ctx.data` registry table above.

**Add a new phase** —
1. Create `phases/_<domain>.py` following the pattern above.
2. Add `"<domain>"` to `DOMAINS` in `_context.py`.
3. Import it and add it to `_PHASES`/`_ORDER` in `__main__.py`, positioned according to its
   `ctx.data` dependencies.
4. Add the new key(s) it produces/consumes to the `ctx.data` registry table above.

**New prerequisite data** — if a new or changed step's `ctx.skip(...)` depends on a kind of
APM resource (a new plan type, workload category, scope, version state, ...) not yet covered
by `TEST_DATA.md`, add it to the relevant section there so testers know to set it up on the
APM before running.

**Irreversible commands** (anything with no undo, like `retire`) — never automate. Add it to
`MANUAL_TESTS.md` instead, following its existing format (search-mode + direct-mode syntax,
prerequisites, what to verify).

**After any change** —
1. `uv run python -m tests.smoke.cli --group <domain>` against your `.env` test
   machine; review the regenerated `reports/<ts>/<domain>.md` and `index.md` for `unexpected`
   exit codes.
2. For changes that touch `ctx.data` threading across phases, also run `--group all`.
3. `uv run ruff check packages/synology-apm-sdk/src packages/synology-apm-cli/src tests
   examples scripts` and `uv run mypy examples/ scripts/ tests/` must both pass — see root
   `CLAUDE.md` Post-change Checklist.

---

## Relationship to `TEST_DATA.md` and `MANUAL_TESTS.md`

`TEST_DATA.md` documents what to set up **before** a run: the backup servers, workloads,
versions, tenants, and plans that should already exist on the APM so the phases above find
data to exercise instead of `ctx.skip(...)`-ing.

`MANUAL_TESTS.md` documents the 7 irreversible `retire` invocations (1 machine + 6 m365
scopes) that this tool deliberately never runs, to be checked **after** a run by hand against
a disposable workload. `index.md` links to it on every run as a reminder.
