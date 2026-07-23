# APM MCP Server — Design Contract

> Corresponding product: Synology ActiveProtect Manager 1.2

**Purpose of this document**: the conventions and decisions a contribution to this package must follow —
not a restatement of what each function does. Mechanism-level rationale (why a helper is shaped the way it
is) lives in that function's own docstring/comments; this document only records the cross-cutting rule and
points at where to apply it. For end-user installation, client configuration, and environment variables,
see `packages/synology-apm-mcp/README.md`.

---

## Table of Contents

- [Purpose and Design Principles](#purpose-and-design-principles)
- [Package Structure](#package-structure)
- [Tool and Resource Conventions](#tool-and-resource-conventions)
- [Shared Code Patterns](#shared-code-patterns)
- [SDK ↔ MCP Coverage Manifest](#sdk--mcp-coverage-manifest)
- [Testing Conventions](#testing-conventions)
- [Relationship to Workflow Skills](#relationship-to-workflow-skills)

---

## Purpose and Design Principles

`synology-apm-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io/) server, built on
[FastMCP](https://gofastmcp.com/), that exposes the APM Python SDK as tools and resources for LLM agents.
The server depends solely on the SDK and does not call the REST API directly.

```
LLM agent  →  MCP server (synology_apm.mcp)  →  APM SDK (synology_apm.sdk)  →  APM REST API
```

- **SDK-only, machine-facing output**: per CLAUDE.md's "Three-Layer Responsibility Separation", MCP consumes
  the SDK model's `to_dict()` output directly, with none of the CLI's presentation-only transforms. Every
  tool/resource returns a JSON string.
- **Domain-oriented naming**: mirrors the CLI's command structure and the SDK's `apm.machine` / `apm.m365`
  object model.
- **Progressive permission model**: `APM_MCP_MODE` gates which tools are *registered* at startup, not which
  are merely rejected at call time.
- **Preview-then-confirm for irreversible actions**, and an **audit trail** for every mutation.
- **Resilient startup**: the server always starts and registers its tools, even with missing/invalid
  credentials.

Each principle above is a decision a contribution must respect; see "Tool and Resource Conventions" for the
concrete rule and where it's implemented.

---

## Package Structure

Two naming conventions let most files go uncommented below: `tools/<domain>.py` implements the domain's
tool set (one `register(registrar)` entry point per file), and `tools/plans/<category>.py` implements the
plan-category's create/update tools. Only private helpers, entry points, and multi-role files are annotated.

```
synology_apm/mcp/
├── __init__.py
├── __main__.py    # CLI entry point: arg parsing, delegates to _config + _server.run()
├── _config.py     # Credential/mode resolution
├── _enums.py      # Literal type aliases mirroring SDK enums
├── _errors.py     # SDK exception → structured JSON error
├── _helpers.py    # Pagination, workload/version resolution, JSON-list coercion
├── _registrar.py  # ToolRegistrar: mode-gated tool registration
├── _security.py   # Mode levels, audit log, destructive preview/confirm
├── _server.py     # Server factory, lifespan, startup resilience
├── resources.py   # MCP resources (apm://...)
└── tools/
    ├── __init__.py
    ├── _workload.py        # Shared machine/M365 workload tool registration (FastMCP signature layer)
    ├── _workload_logic.py  # Shared machine/M365 resolve/mutation logic (plain, unit-testable functions)
    ├── activity.py         # Backup/restore activity tools
    ├── infra.py            # Site info, backup server, remote storage, hypervisor tools
    ├── log.py              # DP-server-only log tools
    ├── m365.py             # M365 workload tools + exports + auto-backup rules + tenant lookup
    ├── machine.py          # Machine workload tools + file server add/update
    └── plans/
        ├── __init__.py
        ├── _builders_common.py   # Retention/schedule/backup-copy builders shared by machine + M365
        ├── _builders_machine.py  # Machine-specific plan request builder
        ├── common.py             # Cross-category tools: list/get/delete protection plans
        ├── m365.py               # M365 protection plan create/update tools
        ├── machine.py            # Machine protection plan create/update tools
        ├── retirement.py         # Retirement plan tools
        └── tiering.py            # Tiering plan tools
```

---

## Tool and Resource Conventions

Every function/constant named below has its own docstring documenting its behavior in full — this section
only states the rule a new tool must follow and where that rule lives.

### Naming and return shape

- Tool names follow `{verb}_{domain}_{noun}` (`list_machine_workloads`, `backup_machine_workload`). Machine
  and M365 share the same verb/noun with a differing prefix; cross-category plan operations that behave
  identically for both categories (list/get/delete) carry no category prefix — only `create_*`/`update_*`
  are split per category, since their request shapes diverge.
- Every tool/resource returns a JSON `str`, never a raw object — built via `run_tool()` / `run_resource()`
  (`_errors.py`), `run_audited_tool()` / `destructive_tool()` (`_security.py`), or `list_tool()` / `get_tool()`
  (`_helpers.py`). A tool body must not catch exceptions itself — let them propagate to these wrappers.
- List tools return `{items, total, truncated?}` via `list_result()` / `list_tool()`; every list tool's
  `description=` must end with `LIST_RESULT_SUFFIX` or `LIST_RESULT_SUFFIX_UNRELIABLE_TOTAL` (`_helpers.py`)
  so the documented shape can't drift from what the tool returns. get tools return a single item via
  `get_tool()` — no `{items, total}` envelope.

### Parameter conventions

- Repeatable/list-typed parameters: `Annotated[list[X], JSON_LIST_VALIDATOR]` (`_helpers.py`), never a bare
  `list[X] | None` — see `coerce_json_encoded_list()`'s docstring for why.
- Enum-typed parameters: a `Literal[...]` alias from `_enums.py`, never the SDK enum class directly.
  `_enums.py` is the single source of truth for each value set; convert to the real enum only at the SDK
  call site (`to_enum_list()` for repeatable filters, a direct `EnumType(value)` otherwise).
- Treat explicit `null` in a caller-supplied JSON field (e.g. `tasks_json`) as "no value" and fall through
  to that field's default — opposite of the SDK's API-response convention (see the SDK README's "Null vs.
  Absent JSON Field Handling"). Keep required fields failing loudly when missing.

### Mode gating

- Every tool is registered via `@registrar.tool(required_mode=..., description=...)` (`_registrar.py`);
  omitting the mode defaults to `readonly`. Modes are cumulative: `readonly < operator < admin`.
  Gating happens only at registration — never re-check the mode inside a tool body.
- **Choosing a mode for a new tool**: `readonly` for list/get queries; `operator` for triggering a routine,
  already-anticipated action (backup, cancel, export); `admin` for version lock/unlock, and anything that
  creates/updates/deletes a persistent configuration object, or permanently changes a workload's lifecycle
  (retire, delete, change_plan).

### Destructive actions

- Any `delete_*`/`retire_*` tool takes `confirm: bool = False` and goes through the shared
  `destructive_tool()` helper (`_security.py`) — never hand-roll the preview/execute branching. Its
  `description=` must end with the shared `DESTRUCTIVE_PREVIEW_SUFFIX` constant.

### Audit logging

- Every mutating tool (destructive or not) is wrapped in `run_audited_tool()` (`_security.py`). `params`
  should hold only identifying arguments, not every parameter — see `mutation_params()`
  (`tools/_workload_logic.py`) for the shared-workload-tool convention. Read-only tools are never
  audit-logged.

### Resources vs tools

- Small, bounded, stable-shape reference data is an MCP *resource* (`apm://...`, `resources.py`), not a
  tool. Resources are not mode-gated — they carry no mutation risk. Anything needing filtering, pagination
  beyond `MAX_LIST_LIMIT`, or a mutation must be a tool instead.

### Description text

- A tool/resource's `description=` is the only reference the calling agent sees. It follows the same rule
  as CLI `help=` text under CLAUDE.md's "API Abstraction in User-Facing Text": no REST paths, HTTP
  methods/status codes, raw API field names, or raw error codes — describe SDK/domain-level behavior only.

---

## Shared Code Patterns

- **Machine/M365 shared workload tools**: ~11 tool shapes are shared between machine and M365 workloads via
  `register_workload_tools()` (`tools/_workload.py`) and `WorkloadCategory` (`tools/_workload_logic.py`) —
  see that module's docstring for the split rationale. When adding a tool shared by both categories, add its
  logic to `_workload_logic.py` and its FastMCP signature pair to `register_workload_tools()`; do not
  duplicate the logic inside `machine.py` / `m365.py`.
- **Plan request builders**: shared retention/schedule/backup-copy construction lives in
  `tools/plans/_builders_common.py`; the machine-specific superset in `tools/plans/_builders_machine.py`.
  Neither registers tools itself. `register_delete_plan_tool()` (`tools/plans/common.py`) is the shared
  factory behind every plan-family delete tool.
- **Update tools are full-replace**: for `update_*` tools whose SDK request type has no partial-update
  semantics (protection/retirement/tiering plans, file server config), every field is a required parameter
  with no default. The `description=` must say so explicitly, so the calling agent never assumes an omitted
  field is preserved.
- **Startup resilience**: the server always starts and registers every tool for its mode, even with
  missing/invalid credentials — see `_server.py`'s `_FailedConnectionClient` / `build_lifespan()`
  docstrings. The one case that still exits immediately is an unrecognized `APM_MCP_MODE`
  (`_config.py::resolve_mode()`).
- **Error dict shape**: `sdk_error_to_dict()` (`_errors.py`) is the single place that converts any exception
  to `{"error": <code>, ...}`; codes needing user remediation get an additional `"hint"` field (see
  `_RECONFIGURE_CODES`) — this must not duplicate or diverge from the CLI's own error-message mapping
  (`cli/errors.py`).

---

## SDK ↔ MCP Coverage Manifest

`scripts/mcp_coverage.toml` maps every SDK public async method to either a `[[mapping]]` entry (exposed as
a tool, at a mode) or a `[[not_exposed]]` entry (excluded, with a reason). `scripts/check_mcp_coverage.py`
(run by `make test`) enforces that the manifest, the real SDK surface, and the registered tools all agree —
see the script's own docstring for the full pass list.

- **New SDK method added**: add exactly one manifest entry — `make test` fails on an unmapped method.
- **New MCP tool added**: add its `[[mapping]]` entry with the mode matching its `@registrar.tool(...)` call.
- **Tool renamed, removed, or its mode changed**: update the corresponding manifest entry in the same change.

---

## Testing Conventions

Tests live under `tests/unit/mcp/` (mirrors `mcp/`) and `tests/unit/mcp/tools/` (mirrors `mcp/tools/`).
Shared fixtures in `tests/unit/mcp/conftest.py` — see each fixture's own docstring for why it's shaped the
way it is:

| Fixture | Purpose |
|---|---|
| `mock_apm` | Deeply-mocked `APMClient`, every collection method an `AsyncMock()` |
| `mock_ctx` | `Context` with `lifespan_context = {"apm": mock_apm}` |
| `admin_server` | Real server with every tool registered (`mode="admin"`) |
| `resource_server` | Real server with only resources registered, plus a real lifespan yielding `mock_apm` |
| `call_tool(server, name, ctx, **kwargs)` | Invoke a registered tool through its real FastMCP wiring |
| `assert_destructive_preview_then_execute(...)` | Asserts the shared preview-then-execute contract |
| `make_*` | Model factories — keyword defaults plus `.update(kwargs)` |

- Test what a tool does — the right SDK method called with the right arguments, the right JSON result
  fields — not FastMCP's own dispatch/validation machinery (that's the library's own test suite).
- Shared machine/M365 tool logic is tested once in `test_workload.py`, parametrized over
  `(kind, workload_factory, ...)` — category-specific tools (file server management, M365 exports/auto-backup
  rules) stay in their own category's test file.
- The coverage check (`scripts/check_mcp_coverage.py`) is a separate `make test` step, not a pytest test.

---

## Relationship to Workflow Skills

`skills/` (repo root) contains hand-authored `SKILL.md` workflow skills that compose multiple MCP tools into
a task. They are bundled with the MCP server only when installed via the Claude Desktop (Cowork) / ChatGPT
Desktop (Work) plugin — not with a manually-configured server — see `packages/synology-apm-mcp/README.md`.
Skill content itself (plain markdown, not Python) is out of scope for this document; see
`skills/apm-mcp-conventions/SKILL.md` for the shared conventions the other skills point to, several of which
mirror conventions recorded above (destructive preview/confirm, list result shape, mode gating).
