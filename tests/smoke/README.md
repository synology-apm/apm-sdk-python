# Live Smoke-Test Tools — Shared Conventions

Two independent live smoke-test tools live under this directory: `tests/smoke/cli/` (drives the
real `synology-apm-cli` binary via subprocess) and `tests/smoke/sdk/` (drives `synology_apm.sdk`
directly, in-process). Both are structured the same way — phases, a `SmokeContext`, a `ctx.data`
registry, and a `reports/<UTC timestamp>/` output — and share the conventions below. Read this
file first; each tool's own `README.md` documents only what's specific to it (its `SmokeContext`
API, its `ctx.data` registry table, its phase list, and its exact report fields).

A third subdirectory, `tests/smoke/mcp/`, holds only `PROMPT.md` — it is not a third automated
tool built on the shared conventions below; see the note at the end of the next section for
what it is instead.

---

## Purpose and relationship to other test layers

| Layer | Drives | Data source | Offline? |
|---|---|---|---|
| `tests/unit/sdk/` | Methods directly, mocked HTTP (`aiointercept`/`patch`) | fixtures | yes |
| `tests/integration/` | SDK methods directly (`apm.machine.workloads.list()`) | `tests/cassettes/` | yes (replay) |
| `tests/smoke/cli/` | The real `synology-apm-cli` binary, via subprocess | live, `.env`-configured APM | no |
| `tests/smoke/sdk/` | `synology_apm.sdk`'s public async API, in-process | live, `.env`-configured APM | no |

`tests/unit/cli/` already exercises argv parsing and table/json rendering in-process, via
Typer's `CliRunner` against a mocked APM client — but neither it nor `tests/integration/` ever
talks to the actual APM server. The two smoke tools fill that gap: `tests/smoke/cli/` runs the
real `synology-apm-cli` binary as a subprocess against a live server; `tests/smoke/sdk/`
exercises the SDK's public API in-process against a live server, additionally making
**in-process correctness checks** (`ctx.check`) neither of the other two layers can (e.g. does
a filter actually filter server-side? does a bogus reference behave the way
`BEHAVIOR_REFERENCE.md`'s Collection Behavior Rules document?).

Invoke either with `uv run python -m tests.smoke.cli [--group ...]` /
`uv run python -m tests.smoke.sdk [--group ...]` (see the `Makefile`'s `smoke-test` target).
Both must be run from the repo root — each is a package under `tests/`, invoked via `python -m`.

`tests/unit/mcp/` mocks the APM client the same way `tests/unit/cli/` does, so it has the same
live-server gap called out above — but unlike CLI, there is no automated `tests/smoke/mcp/`
counterpart to fill it. `tests/smoke/mcp/PROMPT.md` is the current substitute: a natural-language
checklist run by hand against a connected MCP client, not a `SmokeContext`-based harness, because
the MCP server's value is in letting an agent choose the right tool for a described task rather
than in a scripted call sequence (see `packages/synology-apm-mcp/README.md`).

> **Note:** Before running against a new APM test instance, see each tool's own `TEST_DATA.md`
> for the resources (backup servers, workloads, versions, plans, tenants/domains, ...) that
> should already exist so as much of the surface as possible is exercised rather than skipped.

> **Note:** Each tool's `reports/` is gitignored, with the same trust model as
> `tests/cassettes/` — real hostnames, workload names, and IDs from your `.env`-configured APM
> are fine to appear there, but nothing under either `reports/` is ever committed. Every other
> committed file under `tests/smoke/` (`README.md`s, `TEST_DATA.md`s, `MANUAL_TESTS.md`, source
> files) must follow the root `CLAUDE.md`'s Language Policy and Documentation Style, and
> `CONTRIBUTING.md`'s Example Data Conventions (English, no decorative emoji, no real
> hostnames/IDs in code comments or docstrings).

---

## Shared conventions

- **File naming**: `phases/_<domain>.py`, and every other internal module in either tool, is
  named with a leading underscore so pytest's default `test_*.py` collection ignores them —
  same precedent as `tests/cassette_lib.py`.
- **`ctx.data` registry**: phases write discovered objects into `ctx.data` so later phases
  (which run after them in `_ORDER`) can reuse them instead of re-querying. This is a **living
  registry** — when you add a new `ctx.data[...] = ...` assignment in either tool, add a row to
  that tool's own registry table.
- **`ctx.skip(domain, step, reason)`**: records a conditional skip, never a hard failure,
  whenever a step's (or check's) prerequisite data may legitimately be absent on a given APM
  (empty workload lists, no DP-type server, no retired workload, no in-progress restore, ...) —
  a fresh/empty APM should still produce a clean `index.md` with zero unexpected results
  everywhere.
- **`--group` dispatch**: `--group all` (default) runs every phase in that tool's `_ORDER`;
  `--group <domain>` runs exactly one. Running a single `--group <domain>` is useful for fast
  iteration, but any step depending on a `ctx.data` key from an earlier phase will find it
  empty and `ctx.skip(...)` gracefully — use `--group all` to exercise the full cross-phase
  data flow.
- **`--m365-scopes`** (default: all scopes) limits which M365 scopes a phase loops over, in
  both tools. GWS has no equivalent `--gws-scopes` flag in either tool: `GWSWorkloadType` has
  only 5 fixed sub-types with no export feature to branch on, so the GWS phase always iterates
  every member unconditionally.

## How to extend (either tool)

1. **Add a step to an existing phase** — add the relevant context call (and any checks) in the
   matching `_run_<subcommand>` helper, following that tool's `step` naming convention. If the
   result is needed by a later phase, add it to that tool's `ctx.data` registry table.
2. **Add a new phase** — create `phases/_<domain>.py` following the existing pattern; register
   `"<domain>"` in `DOMAINS` (`_context.py`); wire it into `_PHASES`/`_ORDER` in `__main__.py`,
   positioned according to its `ctx.data` dependencies; add its new `ctx.data` keys to the
   registry table.
3. **New prerequisite data** — if a new or changed step's `ctx.skip(...)` depends on a kind of
   APM resource not yet covered by that tool's `TEST_DATA.md`, add it there.
4. **Irreversible operations on real, pre-existing data** (anything with no undo, like
   `retire()` on a workload that isn't disposable test data) — never automate here. If the CLI
   exposes the operation, document the manual procedure in `tests/smoke/cli/MANUAL_TESTS.md`
   (the CLI drives the same SDK method); otherwise rely on
   unit-test coverage. **Exception:** an irreversible operation is fine to automate when it runs
   only against synthetic, disposable data the test itself creates and tears down in the same
   run — e.g. `tests/smoke/sdk`'s File Server CRUD lifecycle (`add` → `retire` → `delete`) and
   the `m365_rule`/`gws_rule` phases' best-effort `delete()` of their own disposable test plans.

## After any change (lint/type-check gate)

Beyond re-running the tool you changed (see that tool's own "After any change" note for the
exact invocation): `uv run ruff check packages/synology-apm-sdk/src packages/synology-apm-cli/src
packages/synology-apm-mcp/src tests examples scripts` and `uv run mypy examples/ scripts/ tests/`
must both pass — see the root `CLAUDE.md`'s Post-change Checklist. Neither smoke tool is run by
`make test` (a separate `make smoke-test` target), so a clean `make test` will not catch a
regression here.
