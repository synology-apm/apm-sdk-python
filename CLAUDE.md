# CLAUDE.md — APM Python SDK Development Guide

> Read this file in full at the start of every new Claude Code session.

---

## Project Background

This project develops a Python SDK and CLI tool for **Synology ActiveProtect Manager (APM)**.

- **synology-apm-sdk**: Wraps the APM REST API so developers can interact with APM using a Pythonic interface, without dealing with raw HTTP details.
- **synology-apm-cli**: A CLI front-end that uses the SDK as its sole dependency, allowing end users to operate APM from the terminal.

---

## Key Documents

| Document | Description | Priority |
|----------|-------------|----------|
| `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` | **SDK design contract**: enum ↔ API string mappings, non-obvious collection behavior rules, type system notes. Full public API signatures/docstrings live in source code (→ Sphinx). | required |
| `packages/synology-apm-cli/src/synology_apm/cli/README.md` | **CLI command spec**: command structure, output format, color/status rules. Per-command SDK wiring is not duplicated here — read the command's module under `cli/commands/`. | implementation reference for CLI |
| `packages/synology-apm-mcp/src/synology_apm/mcp/README.md` | **MCP design contract**: tool/resource registration conventions, mode gating, destructive-action preview/confirm pattern, audit logging, the SDK ↔ MCP coverage manifest, testing conventions. | implementation reference for MCP |
| `APM_PRODUCT_OVERVIEW.md` | APM product domain knowledge — backup/recovery model, workload categories, key concepts (not the SDK itself — see `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` for design rationale and interface) | background reference |

New details belong in the closest document, not here: implementation rationale goes in
source docstrings/comments, SDK behavior contracts in the SDK design-contract README, CLI
specs in the CLI README. This file holds only cross-cutting rules, workflow, and pointers —
keep it from growing into a second copy of those documents.

---

## APM Test Environment

> **Warning:** APM connection credentials (host, username, password) **must never be written into any commit**. Read them from `.env` (already in `.gitignore`):

`.env.example` is the canonical template — copy it to `.env` and fill in real values:

```bash
# .env (format — see .env.example for the full commented template)
APM_HOST=apm.corp.com        # hostname or IP, no scheme; supports host:port; SDK prepends https:// automatically
APM_USERNAME=<username>
APM_PASSWORD=<password>
APM_NO_VERIFY_SSL=true       # set to true for self-signed certificate environments
# APM_PROFILE=default        # optional CLI config profile
```

> **Note:** The SDK/CLI contract is a scheme-less `host[:port]` value. As a test-only
> convenience, the integration-test conftest strips an `https://` prefix from `APM_HOST`
> if one is present.

> SSL: If your APM uses a self-signed certificate, set `APM_NO_VERIFY_SSL=true` (or CLI `--no-verify-ssl`) when connecting.

---

## Cross-Cutting Design Principles

### Language Policy

All written artifacts in this repository must be written in **English** — no exemptions. This covers docstrings, inline comments, CLI `help=` strings, and user-visible output strings (console messages, error messages, confirmation prompts).

### Example Data Conventions

Artifacts covered by the Language Policy above (docstrings, inline comments, CLI `help=`
strings, user-visible output strings) — plus README examples and test fixtures — must never
contain real data captured from a live APM/test environment: real hostnames, IPs, account
names, tenant names, serial numbers, or version strings. This applies even when the value was
obtained by running the CLI/SDK against a real test server to verify behavior — substitute it
with a placeholder from the table below before committing.

Reuse these values consistently so examples form a coherent, recognizable "sample environment."

| Category | Canonical value(s) | Notes |
|----------|--------------------|-------|
| CLI connection target / `APM_HOST` / `SiteInfo.external_address` | `apm.corp.com` (primary), `apm2.corp.com` (secondary/DR site) | `.env`, `config set --host`, `APMClient(...)`, error messages |
| Primary backup server (`BackupServer.name` / `.hostname`) | `apm-server-01` / `192.0.2.1` | "the sample backup server" |
| Secondary backup server (HA) | `apm-server-02` / `192.0.2.2` | |
| Additional backup servers | `apm-server-03`, ... / `192.0.2.3`, ... | extend the pattern |
| Backup server in a non-default state | `apm-server-<state>` / `192.0.2.N` | e.g. `apm-server-dr`, `apm-server-updating`, `apm-server-tiering` |
| NAS-type backup server | `nas-server-01` / `10.0.0.10` | extend as `nas-server-NN` / `10.0.0.1N` |
| ESXi / hypervisor | `esxi1.example.com` / `192.0.2.40` | established |
| Hypervisor account | `root` (local) / `administrator@vsphere.local` (vCenter) | |
| VM workload (primary) | `vm-web-01` (restore dest: `vm-web-01-restored`) | |
| Additional VM workloads | `vm-app-01`, `vm-db-01` (same `-restored` pattern) | distinguish by tier |
| PC / device workload | `CORP-PC-001` | |
| Other device/workload examples | `old-laptop`, `prod-server-01`, `MyPC` | varied names are fine, no single canonical value required |
| File server / share workload | `Corp Share` | |
| M365 user mailbox | `alice@contoso.com` (secondary: `bob@contoso.com`) | |
| M365 group / shared mailbox | `marketing@contoso.com` | |
| M365 / SaaS tenant | `Contoso` (name) / `admin@contoso.com` (email) | |
| SharePoint site | `Marketing` (`https://contoso.sharepoint.com/sites/Marketing`) | |
| Primary remote storage (DSM-based) | `DSM-Storage` / `192.0.2.20:8444`, vault `MyVault` | |
| Tiering destination (S3-compatible) | `tiering-remote` / `https://s3.example.com:443` | |
| APV-based external vault | `APV Vault` / `apv.example.com`, vault `my-bucket` | distinct storage type from `DSM-Storage` |
| Appliance model | `DP320` | |
| Reference NAS model | `DS720+` | |
| Serial number | `SN001` (pattern: `SN` + digits) | |
| APM software version | `APM 1.2-71845` | build number must stay fictional — never copy a version string from a live system |
| Protection / retirement plan name | `Daily Backup` / `Compliance Retention` | |
| Admin username | `admin` | |
| Resource UUIDs (workload/plan/namespace/tenant/version IDs) | `123e4567-e89b-12d3-a456-4266141740NN` (increment `NN` per distinct resource in an example) | based on the RFC 4122 example UUID; truncated form `123e4567-...` is fine |
| IP addresses (not covered above) | RFC 5737: `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`; or `10.0.0.0/24` | |
| Hostnames (not covered above) | RFC 2606: `example.com` / `example.org` | |

### Documentation Style

- No decorative emoji in documentation prose (this file, package READMEs, `docs/`). Use text-labeled admonitions instead: `> **Note:** ...` / `> **Warning:** ...`.
- Exception: the status-icon table in `packages/synology-apm-cli/src/synology_apm/cli/README.md` (✓ ✗ ⚠ ⊘ — ⠸ ● ○ ? 🔒 ⟳) documents the CLI's actual terminal output and is part of the design spec, not decoration — do not strip these.
- Admonitions use `> **Note:**` / `> **Warning:**` blockquotes consistently across CLAUDE.md and both package READMEs.

### Three-Layer Responsibility Separation (API string → SDK Enum → CLI display)

- **Parser** (collection `_parse_*`): maps raw API strings to semantic enums — API strings must not leak past this layer.
- **SDK** (model / enum): exposes only semantic enum values (snake_case), and owns semantic JSON serialization — every response model dataclass exposes a `to_dict()` method (enum fields via `.value`, datetime fields as ISO 8601 strings, nested models via their own `to_dict()`) — see `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` "Design Conventions" and "Enum Definitions and API String Mapping" for the conventions.
- **CLI** (commands): owns all enum → display-string mapping tables, plus any other presentation-only transform (local-time conversion, flattened/derived fields, CSV row shaping) — see `packages/synology-apm-cli/src/synology_apm/cli/README.md` "Development Conventions" for the convention. CLI's JSON/YAML output sources its semantic field values from the SDK model's `to_dict()` rather than re-deriving them.
- **MCP** (`packages/synology-apm-mcp`): consumes the SDK model's `to_dict()` output directly, without CLI's presentation-only transforms (local-time conversion, flattened/derived fields) — MCP output is the raw semantic shape for machine consumption, while CLI's `--output json` applies the same presentation transforms it uses for table/CSV output. This is an intentional divergence between the two surfaces, not a shared contract. MCP tool output is always machine-readable JSON with no table/display formatting, so MCP code must not contain enum → display-string mapping tables — that concern belongs to CLI only.

No layer may cross into another's domain: raw API strings must not appear in SDK models, CLI, or MCP code; display strings must not appear in SDK enum values or in MCP output; and CLI/MCP must not hand-roll a second copy of the semantic dict-building logic already provided by SDK model `to_dict()` methods.

### API Abstraction in User-Facing Text

Docstrings (module/class/method/function — published via Sphinx), CLI `help=` strings,
user-visible output strings, and the three PyPI READMEs (`packages/synology-apm-sdk/README.md`,
`packages/synology-apm-cli/README.md`, `packages/synology-apm-mcp/README.md`) describe
**domain/SDK-level behavior only**. They must not mention:

- REST API paths (e.g. `/api/v1/system/resource`), HTTP methods, or HTTP status codes
  (e.g. "returns 404", "HTTP 401").
- Raw API field/query-param names (e.g. `categoryService`, `workload.uid`, `jobStatus`) or
  raw API error codes/messages (e.g. `errorCode 1002`, `database_query_failed`).
- A *specific* underlying-API quirk or mechanism (e.g. "the API ignores X if Y is missing",
  "the API returns an empty list / raises an error for Z") — describe the resulting
  SDK/CLI behavior directly (what the method returns or raises) instead.
- **Internal SDK processing**: how the SDK derives or transforms raw data (e.g. "computed from", "merged from", "parsed from raw API data"). Describe what a field means and when it is set, not how it is produced — e.g. `reason: Detail reason when FAILED; None otherwise.`, not `reason: Resolved detail reason when FAILED.`

Generic, non-specific references (e.g. "not supported by the API", "not currently exposed
by APM") are fine — the rule targets concrete implementation details, not the word "API"
itself.

> **Note:** `_http.py` and private helpers/modules prefixed with `_` are exempt and may
> reference raw API details in code/comments where needed for implementation clarity. The
> package design-contract READMEs
> (`packages/synology-apm-sdk/src/synology_apm/sdk/README.md`,
> `packages/synology-apm-cli/src/synology_apm/cli/README.md`,
> `packages/synology-apm-mcp/src/synology_apm/mcp/README.md`) are also exempt — their "Enum
> Definitions and API String Mapping", "Collection Behavior Rules", and "SDK ↔ MCP Coverage
> Manifest" sections exist specifically to document these mappings for maintainers.

### Implementation Conventions

- The CLI interacts with APM only through the SDK (never raw HTTP); its command structure, output formats, and exit codes are specified in `packages/synology-apm-cli/src/synology_apm/cli/README.md` — command additions/removals must be reflected there (see Post-change Checklist).
- The MCP server interacts with APM only through the SDK (never raw HTTP); its tool/resource registration conventions, mode gating, and the SDK ↔ MCP coverage manifest are specified in `packages/synology-apm-mcp/src/synology_apm/mcp/README.md` — tool additions/removals must be reflected there and in `scripts/mcp_coverage.toml` (see Post-change Checklist).
- SDK-level conventions (code style, `__all__` exports, field-name mapping, `APMClient` constructor, domain separation between `apm.machine` / `apm.m365`) live in `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` → "Design Conventions".
- CLI-level conventions (error handling, output dispatch, workload resolution, argument validation, serialization) live in `packages/synology-apm-cli/src/synology_apm/cli/README.md` → "Development Conventions".
- MCP-level conventions (tool naming, mode gating, destructive preview/confirm, audit logging, list result shape) live in `packages/synology-apm-mcp/src/synology_apm/mcp/README.md` → "Tool and Resource Conventions"; error dict shape and other cross-tool patterns live in that README's "Shared Code Patterns".

All three READMEs are part of the design contract — read them before implementing, not just when something breaks.

---

## Directory Structure

```
synology-apm-sdk-python/
├── .claude-plugin/      ← plugin marketplace manifests for /plugin install
├── .codex-plugin/       ← Codex plugin manifest (Codex equivalent of .claude-plugin/)
├── .github/workflows/   ← CI, docs, and release pipelines
├── packages/
│   ├── synology-apm-sdk/  ← PyPI project "synology-apm-sdk"; source in src/synology_apm/sdk/ (see its README.md)
│   ├── synology-apm-cli/  ← PyPI project "synology-apm-cli"; source in src/synology_apm/cli/ (see its README.md)
│   └── synology-apm-mcp/  ← PyPI project "synology-apm-mcp"; source in src/synology_apm/mcp/ (see its README.md)
├── tests/
│   ├── unit/            ← unit tests; mirrors sdk/collections/, cli/commands/, and mcp/tools/ (see "Test File Organization"); also covers examples/ and scripts/check_mcp_coverage.py
│   ├── integration/     ← cassette-backed real-server tests; mirrors sdk/collections/
│   ├── cassettes/       ← one JSON cassette per integration test; gitignored, local-only
│   └── smoke/           ← live smoke-test tools; smoke/cli/ drives the CLI binary, smoke/sdk/ the SDK (see their README.md)
├── skills/              ← MCP workflow skills (SKILL.md per task), hand-authored; see packages/synology-apm-mcp/README.md
├── examples/            ← runnable SDK usage examples; see examples/README.md and examples/CLAUDE.md
├── docs/                ← Sphinx API reference
└── scripts/             ← build, release, and consistency-check scripts
```

---

## Testing Standards

### Test scope: behavior vs. implementation

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

> **Note:** A leading underscore on a *module* (e.g. `cli/_display.py`, `cli/_serializers.py`, `cli/_validate.py`; or, for MCP, `mcp/_helpers.py`, `mcp/_security.py`, `mcp/_errors.py`, `mcp/_registrar.py`) marks it as not-for-end-users, not as untestable — the **public-named** functions inside these shared modules (`fmt_*`, `*_to_dict` / `*_to_csv_row`, validators for the CLI; `list_result`/`get_tool`/`run_tool`/`sdk_error_to_dict`/`mode_allows`/`destructive_tool`/etc. for MCP) are that layer's internal contract and should have direct unit tests (`tests/unit/cli/test_display.py`, `test_serializers.py`, `test_arg_validation.py`; `tests/unit/mcp/test_helpers.py`, `test_security.py`, `test_errors.py`, `test_registrar.py`). The `_`-prefixed display dicts themselves are tested only two ways: through their public `fmt_*` wrapper, and via set-equality enum-exhaustiveness checks in `test_display.py`.

### Testing purpose by layer

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

### CLI test layering

CLI tests split responsibilities across two layers — do not duplicate one layer's checks in the other:

- **Command-level tests** (`tests/unit/cli/commands/`, via `invoke_cli` + a mocked APM client) verify that the command is wired to the SDK correctly: the right SDK method is called with the right arguments, exit codes are correct, and output dispatch/confirmation flow behaves as specified.
- **Shared-module tests** (`test_display.py`, `test_serializers.py`, `test_arg_validation.py`) verify display formatting and dict serialization field contracts directly. Command-level tests should not re-assert per-field formatting/serialization details that a shared-module test already covers.

### MCP test layering

MCP tool tests split responsibilities the same way — do not duplicate one layer's checks in the other:

- **Tool-level tests** (`tests/unit/mcp/tools/`, via `call_tool()` + a mocked APM client) verify
  that the tool is wired to the SDK correctly: the right SDK collection method is called with the
  right resolved arguments, and the JSON result contains the right fields — not just that the call
  succeeds.
- **Shared-module tests** (`test_helpers.py`, `test_security.py`, `test_errors.py`,
  `test_registrar.py`) verify pagination, error-dict, and mode-gating contracts directly.
  Tool-level tests should not re-assert those shared-module contracts.
- Shared machine/M365 tool logic (`register_workload_tools()`) is tested once in
  `test_workload.py`, parametrized over `(kind, workload_factory, ...)` — do not hand-duplicate
  the same assertions per category across `test_machine.py`/`test_m365.py`/`test_workload.py`.

### Output assertion conventions (CLI)

- Output assertions must anchor a value to its label/context — assert the label+value pair or
  the full rendered line (e.g. assert that the line containing `Pending versions:` also contains
  `5`). Standalone single-character, single-digit, or common-word substring assertions
  (`assert "5" in result.output`, `assert "-" in result.output`) are not meaningful: they pass
  on virtually any output.
- Output-dispatch tests (`--output json` / `yaml` / `csv`) must assert at least one field of the
  emitted document (parse it and check a value, or `== []` for an empty listing), not just
  `exit_code == 0`.

### `list()` test conventions

A `list()` test that verifies field parsing (e.g., `test_list_parses_backup_server_fields`) is
sufficient — do not add a separate `test_list_returns_X` that only checks the return type and
count. If you want to verify `total`, add `assert total == N` to the parse test, not a standalone
test. `isinstance` checks and bare `len` comparisons are not meaningful assertions on their own.

`assert X is not None` is permitted as a **type-narrowing guard** when it is immediately followed
by field-value assertions on `X` (e.g. `assert X.field == value`). Standalone `assert X is not
None` with no follow-up assertions on `X` is not a meaningful test on its own. Do not use truthy
guards (`assert X`) in place of `assert X is not None` — use the explicit form.

### `create()` / `update()` test conventions

`create()` and `update()` tests must always assert:

1. **The full request body** — assert every field the request object populates (e.g.
   `body["plan"]["serviceType"]`, `body["plan"]["retention"]["keepDays"]`).
2. **Key fields on the returned model** — assert at least the `*_id` field and one identifying
   field (e.g. `plan.plan_id`, `plan.name`); `isinstance` alone is not sufficient.

### Exception attribute conventions

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

### Test File Organization

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

### Integration Tests (custom cassette system)

Integration tests use the custom cassette system in `tests/cassette_lib.py` (not vcrpy/pytest-recording).
Use `--import-mode=importlib` to correctly resolve the `tests.cassette_lib` module path.
See **Common Commands** for cassette recording and replay commands.

> **Note:** `tests/cassettes/` is gitignored — cassettes are recorded locally by each developer against their own `.env`-configured APM (`--record-mode=new_episodes`) and replayed offline thereafter (`--record-mode=none`, the default). They are not committed or shared; on a fresh clone with no cassettes, `--record-mode=none` skips every integration test (`pytest.skip`) rather than failing.

> **Note:** SDK collection `list()` methods return `(items, total)` tuples (see `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` "Collection Behavior Rules") — unpack both in integration tests.

---

## Post-change Checklist (all required before every commit)

After any feature addition, refactor, or deletion, **complete all four categories of sync before committing** — all required.

### Tests

**Update tests when:**

| Scenario | Required action |
|----------|----------------|
| New SDK method added | Add a corresponding unit test in `tests/unit/sdk/collections/<module>.py` (or the relevant split file); add exactly one entry (`[[mapping]]` or `[[not_exposed]]`) to `scripts/mcp_coverage.toml` — enforced by `make test` (see `packages/synology-apm-mcp/src/synology_apm/mcp/README.md` "SDK ↔ MCP Coverage Manifest") |
| New SDK enum, model, or collection class added | Add it to `packages/synology-apm-sdk/src/synology_apm/sdk/__init__.py` and `__all__`; run `grep -r "from synology_apm\.sdk\." packages/synology-apm-cli/src examples/` and confirm no output |
| New CLI command added | Add a command-level unit test in `tests/unit/cli/commands/` per "CLI test layering" — assert SDK wiring (method + arguments), exit code, and output dispatch, not just that the command runs |
| CLI command path changed (e.g. `synology-apm-cli infra backup-server` → `synology-apm-cli infra server`) | Update all test `runner.invoke(app, [...])` paths |
| Command moved to a different module (e.g. `backup_server.py` → `infra.py`) | Update all `patch("synology_apm.cli.commands.<old_module>.get_client", ...)` to the new module path |
| New MCP tool or resource added | Add a tool-level unit test in `tests/unit/mcp/tools/<module>.py` (or `tests/unit/mcp/` for a non-tool module) per "MCP test layering" — assert SDK wiring and the JSON result's fields, not just that the call succeeds; add/update its `[[mapping]]` entry in `scripts/mcp_coverage.toml` with the matching mode |
| MCP tool renamed, removed, or its required mode changed | Update all `call_tool(server, "<tool_name>", ...)` call sites in `tests/unit/mcp/tools/`; update the corresponding `scripts/mcp_coverage.toml` entry (mode mismatches and unregistered/unmapped tools both fail `make test`) |
| Dataclass field changed (added/deleted/renamed) | Update all tests that use that dataclass as a fixture |
| Package version bumped (see "Release / Version Bump") | Run `make check-version-consistency` — the `version` field in all three `packages/*/pyproject.toml` files, and the `synology-apm-sdk==X.Y.Z` dependency pin in `synology-apm-cli`'s and `synology-apm-mcp`'s `dependencies`, must all match; enforced by `make test` |
| Integration test renamed or removed | Local-only, no commit impact (`tests/cassettes/` is gitignored): delete the corresponding cassette from `tests/cassettes/`; use `--record-mode=all` to re-record if the underlying API response changed |
| Major SDK refactor changes request/response format | Local-only, no commit impact: re-record your local cassettes: `pytest tests/integration/ --record-mode=all --import-mode=importlib` |

**Run before every commit:** `make test` (see "Common Commands" for what it runs — same gate as CI).

> **Note:** The `fail_under = 95` floor (`pyproject.toml`) is a shared aggregate backstop across
> SDK/CLI/MCP/examples, not a definition of "sufficiently tested" — a small new command/tool with
> zero direct tests can still pass it if the rest of the codebase absorbs the drop. Meet this
> section's per-layer assertion conventions first (SDK request/response contract; CLI/MCP wiring +
> presentation contract); let the percentage rise as a byproduct of meaningful tests, never write
> assertion-free tests solely to move the number.

### Documentation

| Change | Documents to sync |
|--------|------------------|
| CLI command path or structure adjusted | `packages/synology-apm-cli/src/synology_apm/cli/README.md`: command overview, command detailed spec |
| CLI-level convention changed (error handling, output dispatch, workload resolution, argument validation, serialization, enum display mapping) | `packages/synology-apm-cli/src/synology_apm/cli/README.md`: Development Conventions section |
| New SDK public method added | No README update needed (docstrings publish via Sphinx). Update `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` only if the method has non-obvious behavior (Collection Behavior Rules) or introduces a new collection/access path (Collection Map) |
| New enum added, or enum value / API string mapping changed | The mapping dict next to the parser is the source of truth. Update `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` (Enum Definitions and API String Mapping) only when the mapping's *semantics* are non-obvious (computed from multiple fields, one-to-many, magic values), or to add a new dict to its location index |
| Non-obvious collection behavior added or changed (e.g. multi-call logic, request body format, mode dispatch) | `packages/synology-apm-sdk/src/synology_apm/sdk/README.md`: Collection Behavior Rules section |
| New SDK public type added (class, enum, dataclass) | Source docstring (Attributes / class-level description); `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` only if the type has non-obvious behavior or mapping that cannot go in a docstring |
| SDK-level convention changed (code style, `__all__` exports, field-name mapping, `APMClient` constructor, domain separation) | `packages/synology-apm-sdk/src/synology_apm/sdk/README.md`: Design Conventions section |
| CLI command/option/flag/help text added, removed, or changed | No additional sync beyond the CLI README rows above |
| MCP tool or resource added, removed, or its required mode changed | `scripts/mcp_coverage.toml` (see Tests table above); a new/removed source file is tracked separately (see the CLAUDE.md sync table below) |
| MCP-level convention changed (tool naming, mode gating, destructive preview/confirm, audit logging, list result shape, error dict shape) | `packages/synology-apm-mcp/src/synology_apm/mcp/README.md`: Tool and Resource Conventions / Shared Code Patterns sections |
| Any change | Run `make docs` and fix any warnings or errors before committing (`docs/api/` RST stubs are regenerated automatically via `sphinx-apidoc -f`, but `docs/index.rst` toctree entries must be added manually for new modules) |

> **Note:** When adding/changing CLI commands or options, consider adding/updating the
> corresponding invocation in `tests/smoke/cli/phases/_<domain>.py` (see
> `tests/smoke/cli/README.md` for the phase-file pattern and the `ctx.data`
> registry). This is recommended but not enforced by `make test` (requires a live,
> `.env`-configured APM) — verify on your next `--group <domain>` run.

> **Note:** When adding/changing SDK collection methods, consider adding/updating the
> corresponding call in `tests/smoke/sdk/phases/_<domain>.py` (see
> `tests/smoke/sdk/README.md` for the phase-file pattern, the `ctx.data` registry,
> and the `ctx.check` verification conventions). This is recommended but not enforced by
> `make test` (requires a live, `.env`-configured APM) — verify on your next `--group
> <domain>` run.

### README

| Change | Documents to sync |
|--------|------------------|
| CLI command path or usage changed | `packages/synology-apm-cli/README.md`: corresponding command example blocks |
| SDK Quick Start or Developer Guide content is outdated | `packages/synology-apm-sdk/README.md` |
| MCP installation, client config, environment variables, or operation modes changed | `packages/synology-apm-mcp/README.md` |
| MCP tool/resource domain list changed (once feature is complete) | `packages/synology-apm-mcp/README.md`: "Available Tools" domain overview |

### CLAUDE.md

| Change | Sync required |
|--------|--------------|
| New source file added, or existing source file renamed/moved/deleted under `packages/synology-apm-sdk/src/synology_apm/sdk/`, `packages/synology-apm-cli/src/synology_apm/cli/`, or `packages/synology-apm-mcp/src/synology_apm/mcp/` | Update the package README instead: `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` Package Structure for SDK files; `packages/synology-apm-cli/src/synology_apm/cli/README.md` Package Structure for CLI files; `packages/synology-apm-mcp/src/synology_apm/mcp/README.md` Package Structure for MCP files (test file additions do **not** require this update). Always add the file to the tree; add an inline comment only if it doesn't follow that directory's stated naming convention (see each README's Package Structure intro) — a multi-type file, private helper, or entry point still needs one, but a file matching the convention (e.g. `collections/<name>.py` → `<Noun>Collection`, `commands/<name>.py` → `synology-apm-cli <name> ...`, `tools/<domain>.py` → one `register(registrar)` entry point) does not |
| New CLI command added (once feature is complete) | No CLAUDE.md index update needed — see the Documentation table above for the CLI README sections to update |
| New MCP tool added (once feature is complete) | Add its `[[mapping]]` entry to `scripts/mcp_coverage.toml` (see the Tests table above); no separate CLAUDE.md index update needed |

---

## Git Commit Convention

```
feat:  new feature        feat: implement WorkloadCollection.list()
fix:   bug fix            fix: handle 401 re-auth in _http.py
docs:  documentation      docs: map backup_now to REST API
test:  tests              test: add unit tests for MachineWorkload
chore: configuration      chore: add pytest-recording to dev deps
```

A commit message describes only what the diff actually contains — its end state and
motivation — not the development history behind it. If a bug was introduced and fixed
entirely within the same uncommitted working tree before this commit, don't narrate that
discovery/fix in the message: there is no prior commit showing the buggy state, so the
"fix" has no corresponding change visible in the diff. Just describe the resulting
behavior/feature.

---

## GitHub Actions Security Conventions

Every `uses:` in `.github/workflows/*.yml` must reference a full commit SHA with a trailing
`# vX.Y.Z` comment (e.g. `uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 #
v7`), never a mutable version tag or branch. Pin a new action to its commit SHA in the same
commit that adds it — resolve the SHA via `git ls-remote <repo-url> refs/tags/<tag>` (for an
annotated tag, use the dereferenced `<tag>^{}` commit SHA, not the tag object SHA). A local
reusable-workflow reference (`uses: ./.github/workflows/ci.yml`) is not an external action and
is exempt from this rule.

There is no `.github/dependabot.yml` in this repo — pins are kept current only reactively, by
a Dependabot security-update PR (enabled via repo Settings → Code security: Dependabot alerts +
Dependabot security updates; no config file involved) when a CVE/advisory is filed against the
currently-pinned version. There is no proactive/scheduled freshness pass; a maintainer wanting
one can run `make bump-external-versions` manually (rewrites outdated GH Actions pins and
upgrades `uv.lock` within existing constraints — review `git diff` before committing), or add
`dependabot.yml` version-updates later. This is not scheduled or proactive; a human still
decides when to run it.

> **Warning:** `make github-act-simulation` (see "Common Commands") runs `docs.yml`'s
> `build` job, then `release.yml`'s `verify-dist` job (which pulls in `test` — the
> reusable call into `ci.yml` — and `build` as `needs:` dependencies, so all three run).
> Never point it at `docs.yml`'s `deploy` job or `release.yml`'s
> `publish-sdk`/`publish-cli`/`publish-mcp`/`github-release` — those publish to GitHub
> Pages / PyPI via OIDC (`pages: write` / `id-token: write`, PyPI Trusted Publishing) or
> create a real GitHub Release (`contents: write`), which a local `act` run can not and
> should not exercise.
>
> `.github/workflows/dependabot-auto-merge.yml` has no local `act` simulation target —
> its entire logic is a one-line author check gating a real `gh pr merge --auto` call
> against a real pull request, which `act` cannot meaningfully fabricate.

`.github/workflows/dependabot-auto-merge.yml` auto-merges every PR authored by `dependabot[bot]`
(via `gh pr merge --auto --squash`, which only enables GitHub's native auto-merge — the actual
merge still waits on the `test` required status check from `ci.yml`). Gating on the author
alone (no label check) is sufficient because this repo has no `dependabot.yml`: with no
version-updates configured for any ecosystem, "Dependabot security updates" is the only
mechanism that can make `dependabot[bot]` open a PR here, so every such PR is a security
update. This applies uniformly regardless of semver bump size, including to
`pypa/gh-action-pypi-publish` and `softprops/action-gh-release`.

> **Warning:** `pypa/gh-action-pypi-publish` and `softprops/action-gh-release` are exercised
> only by `release.yml` on a tag push, never by a PR — so the `test` check passing does not
> mean a security bump to either of these two has actually been run with its new pin. That gap
> is accepted for simplicity rather than special-cased.

> **Warning:** If `dependabot.yml` version-updates are ever added for any ecosystem, this
> workflow must be revisited (e.g. reintroduce a security-only filter) — otherwise it will
> start auto-merging routine, non-security dependency bumps too.

This depends on manual, non-committable repo Settings: Code security (Dependabot alerts +
security updates), General → Pull Requests (Allow auto-merge, Allow squash merging), and a
branch protection rule on `main` requiring the `test` status check.

---

## Release / Version Bump

All three packages (`synology-apm-sdk`, `synology-apm-cli`, `synology-apm-mcp`) share a single **lockstep version number** — bump them together, never independently. Releases are cut by the maintainers and published to PyPI and GitHub automatically via `.github/workflows/release.yml`.

Consistency across the three packages is checked at two points: `make check-version-consistency`
(part of `make test`, so it runs on every commit/PR) statically compares the `version` field of
all three `packages/*/pyproject.toml` files and the `synology-apm-sdk==X.Y.Z` dependency pin in
`synology-apm-cli`/`synology-apm-mcp`; `.github/workflows/release.yml`'s `verify-dist` job
separately confirms the built wheel filenames match the pushed tag before publishing to PyPI.

External contributors do not need to manage version numbers — include your changes in a PR
and the maintainers will handle versioning and publishing.

---

## Common Commands

```bash
# First-time setup
uv sync

# Run unit tests during development
uv run pytest tests/unit/ -v

# Run unit tests in parallel across CPU cores (pytest-xdist) — same command `make test` uses
uv run pytest tests/unit/ -n auto -v

# Record new integration cassettes against a live APM (only records missing ones)
uv run pytest tests/integration/ --record-mode=new_episodes --import-mode=importlib -v
# (= make record-integration-cassettes)

# Force re-record all cassettes (after major SDK refactors)
uv run pytest tests/integration/ --record-mode=all --import-mode=importlib -v

# Scope smoke tests to one domain group (full run of both: make smoke-test)
uv run python -m tests.smoke.cli --group infra
uv run python -m tests.smoke.sdk --group machine

# Build API reference docs (docs/Makefile syncs the "docs" dependency group itself)
make docs

# Makefile shortcuts (make test = unit + integration tests, lint, mypy, coverage, MCP coverage check, version consistency check)
make test
make test-unit                         # unit tests only, no coverage/lint/mypy/integration — same command CI's older-Python matrix uses
make record-integration-cassettes
make smoke-test                        # runs both CLI and SDK smoke tests
make build                             # wheel + sdist (runs make test first)
make whl                               # wheel + sdist without running tests

# Maintenance — none of these run as part of `make test`; each needs a human to run and review
make bump-external-versions            # rewrite outdated GH Actions pins + upgrade uv.lock (needs network; review git diff, then make test)
make github-act-simulation             # locally test docs.yml's build, then release.yml's test+build+verify-dist, via act (needs act + Docker)
```

---

## Common Tasks

End-to-end recipes for the most frequent changes. Each step points at the detailed rules
(Testing Standards, Post-change Checklist, the three design-contract READMEs) — follow those
for the specifics; the recipe only fixes the order.

### Add an SDK collection method

1. Implement it in the relevant `sdk/collections/` module (see the SDK README "Design Conventions").
2. If it introduces a new public symbol (enum, model, collection), export it via `sdk/__init__.py` + `__all__`.
3. Add a unit test in `tests/unit/sdk/collections/` following Testing Standards (request contract + response parsing).
4. Add an integration test and record its cassette (`make record-integration-cassettes`).
5. Update the SDK README only for non-obvious behavior (Collection Behavior Rules) or a new collection/access path.
6. Consider a matching call in `tests/smoke/sdk/phases/_<domain>.py`.
7. Run `make test` and `make docs`.

### Add a CLI command

1. Implement it in `cli/commands/<module>` — SDK calls only, never raw HTTP (see the CLI README "Development Conventions").
2. Add a command-level unit test in `tests/unit/cli/commands/` (SDK wiring, exit codes, output dispatch).
3. Update the CLI README command spec (command overview + detailed spec) if the command surface changed.
4. Update `packages/synology-apm-cli/README.md` example blocks if user-facing usage changed.
5. Consider a matching invocation in `tests/smoke/cli/phases/_<domain>.py`.
6. Run `make test`.

### Add an MCP tool

1. Implement it in the relevant `mcp/tools/<domain>.py` — SDK calls only, never raw HTTP (see the MCP README "Tool and Resource Conventions" for naming, mode gating, list result shape, and the destructive preview/confirm pattern).
2. Add exactly one `[[mapping]]` entry to `scripts/mcp_coverage.toml` with the matching mode (`make test` fails until this is done).
3. Add a tool-level unit test in `tests/unit/mcp/tools/` (SDK wiring, mode gating, JSON result shape).
4. Update `packages/synology-apm-mcp/README.md`'s "Available Tools" domain overview if the change is user-visible.
5. Run `make test`.

### Add an enum or model field

1. Add/extend the mapping dict next to the collection's `_parse_*` parser — it is the source of truth.
2. Add/extend the model dataclass with an Attributes docstring entry; export new types via `__all__`.
3. If the value is displayed, add the enum → display-string mapping in the CLI layer (never in the SDK).
4. Update every test that uses the changed dataclass as a fixture.
5. Update the SDK README (Enum Definitions and API String Mapping) only if the mapping semantics are non-obvious.
6. Run `make test` and `make docs`.

---

*For detailed change history, see git log.*

