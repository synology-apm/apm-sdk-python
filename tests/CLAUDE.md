# CLAUDE.md — Testing Standards

This file provides testing conventions for Claude Code when writing or reviewing tests under `tests/`. It is the detailed companion to the root `CLAUDE.md`'s Post-change Checklist.

One theme recurs throughout: an assertion must be capable of failing on a real defect, not just
raise the coverage percentage (see root `CLAUDE.md`'s Post-change Checklist note on the shared
coverage floor). The sections below (Output assertion conventions, `list()`, `create()`/`update()`/`delete()`)
each spell out what "meaningful" means for that specific case.

---

## Test scope: behavior vs. implementation

Tests must verify **observable behavior**, not internal implementation. The rule of thumb: a pure internal refactor that does not change public behavior or API contracts must not break any test.

| What to test | How |
|---|---|
| SDK → REST API request contract (URL, method, body, params, headers) | `aiointercept` URL interception; or `patch.object(session, "get/post")` + `call_args` inspection |
| API response → SDK model field parsing | Provide a complete raw API response fixture; assert model attribute values |
| Error handling | Provide an error response; assert the exception type and attributes (e.g. `.resource_id`) |
| Observable side effects | e.g. verify the logout endpoint was called after disconnect: `assert ("GET", URL(logout_url)) in m.requests` |

Do not test: `_private` **symbols** (leading-underscore functions, constants, attributes), internal call counts or arguments between methods of the same object, or private state fields. This applies to the SDK, the CLI, and the MCP server.

> **Note:** isinstance-only assertions are acceptable solely for facade property wiring tests
> (e.g. asserting `client.machine` returns `MachineCollection`) — there the returned collection
> type is the property's entire contract. Everywhere else, isinstance is not a meaningful
> assertion on its own.

> **Note:** When a test's own fake constructs and raises the exception, re-asserting the
> exception's fields is tautological and not required — the `assert_resource_error` rule below
> applies to exceptions raised by SDK code paths, not to pass-through fakes.

> **Note:** A leading underscore on a *module* marks it as not-for-end-users, not as
> untestable — the **public-named** functions inside these shared modules are that layer's
> internal contract and should have direct unit tests. The `_`-prefixed display dicts
> themselves are tested only two ways: through their public `fmt_*` wrapper, and via
> set-equality enum-exhaustiveness checks in `test_display.py`.

| Layer | Module | Public contract | Test file |
|---|---|---|---|
| CLI | `cli/_display.py` | `fmt_*` | `test_display.py` |
| CLI | `cli/_serializers.py` | `*_to_dict` / `*_to_csv_row` | `test_serializers.py` |
| CLI | `cli/_validate.py` | validators | `test_arg_validation.py` |
| MCP | `mcp/_helpers.py` | `list_result` / `get_tool` / `run_tool` | `test_helpers.py` |
| MCP | `mcp/_security.py` | `mode_allows` / `destructive_tool` | `test_security.py` |
| MCP | `mcp/_errors.py` | `sdk_error_to_dict` | `test_errors.py` |
| MCP | `mcp/_registrar.py` | registration helpers | `test_registrar.py` |

(CLI paths under `tests/unit/cli/`, MCP paths under `tests/unit/mcp/`.)

## Testing purpose by layer

Each layer verifies a different contract, and must not re-verify a layer beneath it:

- **SDK tests** verify the REST request/response contract itself — this is the primary subject
  of the table above (request shape, response field parsing, error mapping, observable side
  effects). This is the foundation layer and already has the fullest coverage in the codebase.
- **CLI tests** verify only that the command *wires into* the SDK correctly (right method,
  right arguments) and *presents/dispatches* the result correctly (exit code, output format) —
  see "CLI test layering" below. They must not re-assert SDK-level behavior (request bodies,
  field parsing) already covered by SDK tests.
- **MCP tests** verify the same wiring contract as CLI (right SDK method, right arguments), plus
  the tool's JSON result shape, mode gating, and the destructive preview/confirm contract — see
  "MCP test layering" below. They must not re-assert SDK-level behavior, and must not assert
  FastMCP's own dispatch/validation machinery (that's the library's own test suite, not this
  project's).

## CLI test layering

CLI tests split responsibilities across two layers — do not duplicate one layer's checks in the other:

- **Command-level tests** (`tests/unit/cli/commands/`, via `invoke_cli` + a mocked APM client) verify that the command is wired to the SDK correctly: the right SDK method is called with the right arguments, exit codes are correct, and output dispatch/confirmation flow behaves as specified.
- **Shared-module tests** (`test_display.py`, `test_serializers.py`, `test_arg_validation.py`) verify display formatting and dict serialization field contracts directly. Command-level tests should not re-assert per-field formatting/serialization details that a shared-module test already covers.

## MCP test layering

MCP tool tests split responsibilities the same way — do not duplicate one layer's checks in the other:

- **Tool-level tests** (`tests/unit/mcp/tools/`, via `call_tool()` + a mocked APM client) verify
  that the tool is wired to the SDK correctly: the right SDK collection method is called with the
  right resolved arguments, and the JSON result contains the right fields — not just that the call
  succeeds.
- **Shared-module tests** (`test_helpers.py`, `test_security.py`, `test_errors.py`,
  `test_registrar.py`) verify pagination, error-dict, and mode-gating contracts directly.
  Tool-level tests should not re-assert those shared-module contracts.
- Shared machine/M365/GWS tool logic (`register_workload_tools()`) is tested once in
  `test_workload.py`, parametrized over `(kind, workload_factory, ...)` — do not hand-duplicate
  the same assertions per category across
  `test_machine.py`/`test_m365.py`/`test_gws.py`/`test_workload.py`.

## Output assertion conventions (CLI)

- Output assertions must anchor a value to its label/context — assert the label+value pair or
  the full rendered line (e.g. assert that the line containing `Pending versions:` also contains
  `5`). Standalone single-character, single-digit, or common-word substring assertions
  (`assert "5" in result.output`, `assert "-" in result.output`) are not meaningful: they pass
  on virtually any output.
- Output-dispatch tests (`--output json` / `yaml` / `csv`) must assert at least one field of the
  emitted document (parse it and check a value, or `== []` for an empty listing), not just
  `exit_code == 0`.

## `list()` test conventions

A `list()` test that verifies field parsing (e.g., `test_list_parses_backup_server_fields`) is
sufficient — do not add a separate `test_list_returns_X` that only checks the return type and
count. If you want to verify `total`, add `assert total == N` to the parse test, not a standalone
test. `isinstance` checks and bare `len` comparisons are not meaningful assertions on their own.

`assert X is not None` is permitted as a **type-narrowing guard** when it is immediately followed
by field-value assertions on `X` (e.g. `assert X.field == value`). Standalone `assert X is not
None` with no follow-up assertions on `X` is not a meaningful test on its own. Do not use truthy
guards (`assert X`) in place of `assert X is not None` — use the explicit form.

## `create()` / `update()` / `delete()` test conventions

`create()`, `update()`, and `delete()` tests must always assert:

1. **The full request body** — assert every field the request object populates (e.g.
   `body["plan"]["serviceType"]`, `body["plan"]["retention"]["keepDays"]`).
2. **Key fields on the returned model** — for `create()`/`update()`, assert at least the
   `*_id` field and one identifying field (e.g. `plan.plan_id`, `plan.name`); `isinstance`
   alone is not sufficient. (`delete()` methods that return `None` are exempt from this
   sub-point.)

## Exception attribute conventions

For exceptions that extend `_ResourceError` (i.e., expose `.resource_type` and `.resource_id`
— covers `ResourceNotFoundError`, `InvalidOperationError`, `PlanNameConflictError`,
`PlanInUseError`, and similar), always capture `exc_info` and assert both `.resource_type` and
`.resource_id` using the shared `assert_resource_error` helper from `tests/unit/sdk/conftest.py`.
This applies to **integration tests as well as unit tests** — a bare `pytest.raises(...)` passes
even when the exception carries placeholder resource fields, which is exactly the defect class
cassette replay would otherwise catch for free.

When a unit test mocks an error response, base the fixture on the **observed API behavior**
(cassettes under `tests/cassettes/`, or a live run) rather than an assumed one — e.g. lookups
for a missing resource answer HTTP 404 with an empty body, not 200 with an empty JSON object.
Keep a separate test for any defensive code path that handles a different response shape.

> **Note:** `ResourceNotReadyError` extends bare `APMError`, not `_ResourceError` — it has no
> `.resource_type` / `.resource_id`, so only the bare `pytest.raises` check is needed for it.

## Test File Organization

- One `test_<module>.py` per source module by default; split into `test_<module>_<subtopic>.py` when a
  file would exceed ~1000-1500 lines — e.g. `collections/machine.py` →
  `test_machine_workloads.py` / `test_machine_versions.py` / `test_machine_file_server.py`.
- When a source module is tested at two layers (collection class vs. client facade), use
  `test_<module>.py` (collection layer) and `test_<module>_facade.py` (facade layer) — e.g.
  `test_protection_plans.py` / `test_protection_plans_facade.py` for `collections/protection_plans.py`.
- CLI command modules with multiple Typer sub-app groups split by group, named for the subcommand —
  e.g. `cli/commands/infra.py` → `test_infra_backup_server.py`, `test_infra_remote_storage.py`,
  `test_infra_hypervisor.py`, `test_infra_system.py`.
- `tests/unit/` subdirectories use empty `__init__.py`; `tests/integration/` subdirectories do not
  (uses `--import-mode=importlib`).

## Integration Tests (custom cassette system)

Integration tests use the custom cassette system in `tests/cassette_lib.py` (not vcrpy/pytest-recording).
Use `--import-mode=importlib` to correctly resolve the `tests.cassette_lib` module path.
See the Makefile for cassette recording and replay commands (`make record-integration-cassettes`
records missing cassettes only; `uv run pytest tests/integration/ --record-mode=all
--import-mode=importlib -v` force-re-records everything).

> **Note:** `tests/cassettes/` is gitignored — cassettes are recorded locally by each developer against their own `.env`-configured APM (`--record-mode=new_episodes`) and replayed offline thereafter (`--record-mode=none`, the default). They are not committed or shared; on a fresh clone with no cassettes, `--record-mode=none` skips every integration test (`pytest.skip`) rather than failing.

> **Note:** Renaming or removing an integration test is local-only and has no commit impact —
> delete its corresponding cassette from `tests/cassettes/`. If a major SDK refactor changes the
> request/response format, re-record everything with `--record-mode=all` rather than editing
> cassettes by hand.

> **Note:** SDK collection `list()` methods return `(items, total)` tuples (see `packages/synology-apm-sdk/src/synology_apm/sdk/BEHAVIOR_REFERENCE.md` "Collection Behavior Rules") — unpack both in integration tests.
