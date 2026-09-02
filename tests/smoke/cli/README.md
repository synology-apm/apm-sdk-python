# CLI Live Smoke-Test Tool — Maintainer Guide

This is the design contract for the CLI live smoke-test tool: how it is structured, and how to
extend it when `synology-apm-cli` gains new commands or options. See `tests/smoke/README.md`
first for conventions shared with the sibling SDK smoke tool (the layer-comparison table,
`reports/` trust model, `ctx.data`/`ctx.skip`/`--group` semantics, and the generic extend/
after-any-change recipe) — this file covers only what's specific to driving the CLI binary.

---

## Purpose and relationship to other test layers

Neither `tests/unit/` nor `tests/integration/` exercises the actual CLI binary's argv parsing,
`table`/`json` rendering, or exit codes against a live server (see the layer table in
`tests/smoke/README.md`). This tool fills that gap: it runs every reversible command in
dependency order, for both `-o table` and `-o json`, and side-records the raw API traffic (via
`--debug`) for AI/human review.

Invoke it with `uv run python -m tests.smoke.cli [--group ...]` (see the `Makefile`'s
`smoke-test` target); see `TEST_DATA.md` for the prerequisite APM resources.

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
│   ├── _gws.py                   ← saas list + gws <scope> list/get/version for all 5 scopes
│   ├── _activity.py                ← activity backup/restore list/get
│   └── _log.py                      ← log activity/drive/connection/system (DP servers only)
└── reports/               ← gitignored: <UTC timestamp>/{index,<domain>}.md + api_trace.jsonl
```

Each `phases/_<domain>.py` corresponds to one entry in `DOMAINS` (`_context.py`) and one
entry in `_PHASES`/`_ORDER` (`__main__.py`). See `tests/smoke/README.md`'s "Shared
conventions" for the leading-underscore file naming rule.

---

## `SmokeContext` API (`_context.py`)

Every phase's `run(ctx: SmokeContext) -> None` interacts with the live APM exclusively
through these methods:

- **`ctx.run(domain, step, args, *, output_format=None, expect_codes=(0,), env_overrides=None,
  stdin=None, timeout=None, note="") -> CliResult`**
  Runs `uv run synology-apm-cli --no-input --debug <args> [-o <output_format>]` as a subprocess,
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
  `ctx.stats[domain].skipped` — see `tests/smoke/README.md`'s "Shared conventions" for when to
  use this instead of a hard failure.

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
| `gws_workloads[scope]` | `dict[str, list[dict]]` | gws: `gws.<scope>.list` | — (available) |
| `retired_gws_workloads[scope]` | `dict[str, list[dict]]` | gws: `gws.<scope>.list[retired]` | — (available) |
| `gws_versions[scope]` | `dict[str, list[dict]]` | gws: `gws.<scope>.version.list[direct]` | — (available) |
| `backup_activities` | `list[dict]` | activity: `activity.backup.list[history]` | — (available) |
| `restore_activities` | `list[dict]` | activity: `activity.restore.list[history]` | — (available) |
| `protection_plans` | `list[dict]` | plan: `plan.protection.list[all]` | — (available) |
| `retirement_plans` | `list[dict]` | plan: `plan.retirement.list` | — (available) |
| `tiering_plans` | `list[dict]` | plan: `plan.tiering.list` | — (available) |

The per-scope dicts (`m365_workloads`, `retired_m365_workloads`, `m365_versions`; likewise
`gws_workloads`, `retired_gws_workloads`, `gws_versions`) are built with
`ctx.data.setdefault("<key>", {})[scope] = ...` since the m365/gws phases each loop over their
own scopes (`ctx.m365_scopes` for m365; a fixed `GWS_SCOPES` tuple for gws — GWS has no
`--gws-scopes`-style flag since it has only 5 fixed sub-types with no export branching to
subset).

Dict values are the parsed `-o json` array elements — the same fields documented as CLI JSON
output in `packages/synology-apm-cli/src/synology_apm/cli/COMMAND_REFERENCE.md` (e.g. `name`,
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
   in-progress restore) are `ctx.skip(...)` per `tests/smoke/README.md`'s shared convention.

---

## Command categories

All commands this tool can run fall into two categories:

| Category | Behavior | Examples |
|---|---|---|
| **Read-only** | Always run | all `list`/`get`/`show`/`info` across every domain |
| **Excluded — manual only** | Irreversible, never automated, see `MANUAL_TESTS.md` | `machine retire`, `m365 <scope> retire`, `gws <scope> retire` |

State-mutating commands (`version lock`/`unlock`, `backup`/`cancel`, `change-plan`,
`activity restore cancel`, `infra server change-plan`) are not run by this tool — the SDK
smoke test (`tests/smoke/sdk`) covers those code paths against the live server, and the
CLI's own wiring for those commands is covered by the unit tests
(`tests/unit/cli/commands/`). See `TEST_DATA.md` for the data that makes each step exercised
rather than `ctx.skip(...)`-ed.

---

## `--group` dispatch (`__main__.py`)

```python
_ORDER = ("config", "infra", "plan", "machine", "m365", "gws", "activity", "log")
_PHASES = {"config": _config, "infra": _infra, "machine": _machine,
           "m365": _saas_m365, "saas": _saas_m365,  # "saas" is an alias for "m365"
           "gws": _gws,
           "activity": _activity, "plan": _plan, "log": _log}
```

See `tests/smoke/README.md`'s "Shared conventions" for the general `--group`/`--m365-scopes`
semantics. The order above reflects this tool's own `ctx.data` dependencies: `infra`
populates `dp_servers` before `log`. `phases/_gws.py` always iterates its fixed 5-scope
`GWS_SCOPES` tuple unconditionally (no `--gws-scopes` flag).

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
  {"step": "machine.list[all][json]", "command": ["uv","run","synology-apm-cli",...],
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

Follow `tests/smoke/README.md`'s generic "How to extend" recipe. The one CLI-specific detail:
adding a step means adding a `ctx.run(...)`/`ctx.run_both(...)` call in the relevant
`_run_<subcommand>` helper, following the `step` naming convention above; an irreversible
command goes in `MANUAL_TESTS.md` instead (search-mode + direct-mode syntax, prerequisites,
what to verify).

**After any change** — `uv run python -m tests.smoke.cli --group <domain>` against your
`.env` test machine; review the regenerated `reports/<ts>/<domain>.md` and `index.md` for
`unexpected` exit codes (also run `--group all` if the change touches `ctx.data` threading
across phases). Then follow `tests/smoke/README.md`'s lint/type-check gate.

---

## Relationship to `TEST_DATA.md` and `MANUAL_TESTS.md`

`TEST_DATA.md` documents the prerequisite APM resources (see `tests/smoke/README.md`'s note
above); `MANUAL_TESTS.md` documents the 12 irreversible `retire` invocations (1 machine + 6
m365 scopes + 5 gws scopes) that this tool deliberately never runs, to be checked **after** a
run by hand against a disposable workload. `index.md` links to it on every run as a reminder.
