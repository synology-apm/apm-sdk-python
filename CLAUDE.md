# CLAUDE.md — APM Python SDK Development Guide

> Read this file in full at the start of every new Claude Code session.

---

## Project Background

This project develops a Python SDK, CLI, and MCP server for **Synology ActiveProtect Manager (APM)**.

- **synology-apm-sdk**: Wraps the APM REST API so developers can interact with APM using a Pythonic interface, without dealing with raw HTTP details.
- **synology-apm-cli**: A CLI front-end that uses the SDK as its sole dependency, allowing end users to operate APM from the terminal.
- **synology-apm-mcp**: An MCP server front-end that uses the SDK as its sole dependency, exposing APM as tools/resources for MCP-capable clients.

---

## Key Documents

| Document | Description | Priority |
|----------|-------------|----------|
| `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` (+ its companion `BEHAVIOR_REFERENCE.md`) | **SDK design contract**: conventions and the "Adding a New ..." recipe in `README.md`; enum ↔ API string mappings, non-obvious collection behavior rules, and type system notes in `BEHAVIOR_REFERENCE.md`. Full public API signatures/docstrings live in source code (→ Sphinx). | required |
| `packages/synology-apm-cli/src/synology_apm/cli/README.md` (+ its companion `COMMAND_REFERENCE.md`) | **CLI command spec**: command structure and conventions in `README.md`; output format rendering, per-command behavior, and color/status rules in `COMMAND_REFERENCE.md`. Per-command SDK wiring is not duplicated here — read the command's module under `cli/commands/`. | implementation reference for CLI |
| `packages/synology-apm-mcp/src/synology_apm/mcp/README.md` | **MCP design contract**: tool/resource registration conventions, mode gating, destructive-action preview/confirm pattern, audit logging, the SDK ↔ MCP coverage manifest, testing conventions. | implementation reference for MCP |
| `APM_PRODUCT_OVERVIEW.md` | APM product domain knowledge — backup/recovery model, workload categories, key concepts (not the SDK itself — see `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` for design rationale and interface) | background reference |
| `CONTRIBUTING.md` | Canonical placeholder values for example data (hostnames, IPs, account/tenant names, ...) — see "Example Data Conventions" below | background reference |

New details belong in the closest document, not here: implementation rationale goes in
source docstrings/comments, SDK behavior contracts in the SDK design-contract README, CLI
specs in the CLI README. This file holds only cross-cutting rules, workflow, and pointers —
keep it from growing into a second copy of those documents.

---

## Package Design Contracts

Each package's design-contract README above is authoritative for that layer's conventions —
read it before implementing, not just when something breaks. This file never restates their
content; it only routes to it:

| Package | Conventions | Adding something new |
|---|---|---|
| SDK | [Design Conventions](packages/synology-apm-sdk/src/synology_apm/sdk/README.md#design-conventions) | [Adding a New SDK Method or Field](packages/synology-apm-sdk/src/synology_apm/sdk/README.md#adding-a-new-sdk-method-or-field) |
| CLI | [Development Conventions](packages/synology-apm-cli/src/synology_apm/cli/README.md#development-conventions) | [Adding a New Command](packages/synology-apm-cli/src/synology_apm/cli/README.md#adding-a-new-command) |
| MCP | [Tool and Resource Conventions](packages/synology-apm-mcp/src/synology_apm/mcp/README.md#tool-and-resource-conventions) / [Shared Code Patterns](packages/synology-apm-mcp/src/synology_apm/mcp/README.md#shared-code-patterns) | [Adding a New Tool](packages/synology-apm-mcp/src/synology_apm/mcp/README.md#adding-a-new-tool) |

The CLI and MCP server interact with APM only through the SDK, never raw HTTP. Command/tool
additions, removals, or renames must be reflected in the owning README (and, for MCP, also
`scripts/mcp_coverage.toml`) — see the Post-change Checklist below.

---

## APM Test Environment

> **Warning:** APM connection credentials (host, username, password) **must never be written into any commit**. Read them from `.env` (already in `.gitignore`) — copy `.env.example`, the canonical template, and fill in real values.

> **Note:** For self-signed certificates, set `APM_NO_VERIFY_SSL=true` in `.env` (or pass `--no-verify-ssl` to the CLI).

---

## Cross-Cutting Design Principles

### Language Policy

All written artifacts in this repository must be written in **English** — no exemptions. This covers docstrings, inline comments, CLI `help=` strings, and user-visible output strings (console messages, error messages, confirmation prompts).

### Example Data Conventions

See [`CONTRIBUTING.md`](CONTRIBUTING.md#example-data-conventions) for the rule and the
canonical placeholder table.

### Documentation Style

- No decorative emoji in documentation prose (this file, package READMEs, `docs/`). Use text-labeled admonitions instead: `> **Note:** ...` / `> **Warning:** ...`.
- Exception: the status-icon table in `packages/synology-apm-cli/src/synology_apm/cli/COMMAND_REFERENCE.md` documents the CLI's actual terminal output and is part of the design spec, not decoration — do not strip those icons.
- Admonitions use `> **Note:**` / `> **Warning:**` blockquotes consistently across CLAUDE.md and both package READMEs.

### Three-Layer Responsibility Separation (API string → SDK Enum → CLI display)

- **Parser** (collection `_parse_*`): maps raw API strings to semantic enums — API strings must not leak past this layer.
- **SDK** (model / enum): exposes only semantic enum values (snake_case) and owns semantic JSON serialization via `to_dict()` — mechanics are defined in the SDK design contract's "Design Conventions" (`README.md`) and "Enum Definitions and API String Mapping" (`BEHAVIOR_REFERENCE.md`).
- **CLI** (commands): owns all enum → display-string mapping tables, plus any other presentation-only transform (local-time conversion, flattened/derived fields, CSV row shaping) — conventions in the CLI README's "Development Conventions". CLI's JSON/YAML output sources its semantic field values from the SDK model's `to_dict()` rather than re-deriving them.
- **MCP**: consumes the SDK model's `to_dict()` output directly, without CLI's presentation-only transforms — MCP output is always machine-readable JSON with no table/display formatting, so MCP code must not contain enum → display-string mapping tables.

No layer may cross into another's domain: raw API strings must not appear in SDK models, CLI, or MCP code; display strings must not appear in SDK enum values or in MCP output; and CLI/MCP must not hand-roll a second copy of the semantic dict-building logic already provided by SDK model `to_dict()` methods.

### API Abstraction in User-Facing Text

Docstrings (module/class/method/function — published via Sphinx), CLI `help=` strings,
user-visible output strings, and the three PyPI READMEs (`packages/synology-apm-sdk/README.md`,
`packages/synology-apm-cli/README.md`, `packages/synology-apm-mcp/README.md`) describe
**domain/SDK-level behavior only**. They must not mention:

- REST API paths, HTTP methods, or HTTP status codes (e.g. "returns 404").
- Raw API field/query-param names (e.g. `categoryService`) or raw API error codes/messages.
- A *specific* underlying-API quirk or mechanism (e.g. "the API ignores X if Y is missing") — describe the resulting SDK/CLI behavior directly (what the method returns or raises) instead.
- **Internal SDK processing**: how the SDK derives or transforms raw data (e.g. "computed from raw API data"). Describe what a field means and when it is set, not how it is produced — e.g. `reason: Detail reason when FAILED; None otherwise.`, not `reason: Resolved detail reason when FAILED.`

Generic, non-specific references (e.g. "not supported by the API", "not currently exposed
by APM") are fine — the rule targets concrete implementation details, not the word "API"
itself.

> **Note:** `_http.py` and private helpers/modules prefixed with `_` are exempt and may
> reference raw API details in code/comments where needed for implementation clarity. The
> package design-contract READMEs (including the SDK's companion `BEHAVIOR_REFERENCE.md`) are
> also exempt — their "Enum Definitions and API String Mapping", "Collection Behavior Rules",
> and "SDK ↔ MCP Coverage Manifest" sections exist specifically to document these mappings for
> maintainers.

---

## Testing Standards

See [`tests/CLAUDE.md`](tests/CLAUDE.md) for the full testing conventions — test scope
(behavior vs. implementation), per-layer test responsibilities (SDK/CLI/MCP), output/list/
create/update/delete assertion conventions, exception attribute conventions, test file
organization, and the custom cassette-based integration test system.

---

## Post-change Checklist (required before every commit)

After any feature addition, refactor, or deletion, sync tests and documentation before committing:

- **Adding** something new: follow the relevant README's "Adding a New ..." recipe in
  [Package Design Contracts](#package-design-contracts) above — each recipe covers its own
  test/doc/README sync steps.
- **Renaming or moving** a CLI command or MCP tool: see that package README's dedicated
  renaming/removal subsection. **Changing or removing a dataclass field**: update every test
  that uses it as a fixture (SDK README's "Add an enum or model field" recipe covers this) —
  see [`tests/CLAUDE.md`](tests/CLAUDE.md) for test-fixture and cassette conventions generally.
- **Changed a documented convention** (not a new/renamed symbol): update that package's own
  conventions section — see [Package Design Contracts](#package-design-contracts) above.
- **Any change**: run `make docs` and fix any warnings or errors before committing (`docs/api/`
  RST stubs regenerate automatically via `sphinx-apidoc -f`, but `docs/index.rst` toctree
  entries for new modules must be added manually).

**Run before every commit:** `make test` (see the `Makefile` for what it runs — same gate as CI).

> **Note:** The `fail_under = 95` floor (`pyproject.toml`) is a shared aggregate backstop across
> SDK/CLI/MCP/examples, not a definition of "sufficiently tested" — a small new command/tool with
> zero direct tests can still pass it if the rest of the codebase absorbs the drop. Meet each
> README's per-layer assertion conventions first (SDK request/response contract; CLI/MCP wiring +
> presentation contract); let the percentage rise as a byproduct of meaningful tests, never write
> assertion-free tests solely to move the number.

---

## Git Commit Convention

See [`CONTRIBUTING.md`](CONTRIBUTING.md#commit-convention) for the commit message format and
conventions.

---

## GitHub Actions Security Conventions

See [`.github/CLAUDE.md`](.github/CLAUDE.md) for GH Actions pinning conventions and the
Dependabot auto-merge workflow's design and preconditions.

---

## Release / Version Bump

See [`CONTRIBUTING.md`](CONTRIBUTING.md#version-bump) for the lockstep-versioning contract.
Releases are cut by the maintainers and published to PyPI and GitHub automatically via
`.github/workflows/release.yml`.

---

*For detailed change history, see git log.*

