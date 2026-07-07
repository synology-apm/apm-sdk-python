# CLAUDE.md — APM Python SDK Development Guide

> Read this file in full at the start of every new Claude Code session.

---

## Project Background

This project develops a Python SDK and CLI tool for **Synology ActiveProtect Manager (APM)**.

- **synology-apm-sdk**: Wraps the APM REST API (`/api/v1/`, `/api/v2/`) so developers can interact with APM using a Pythonic interface, without dealing with raw HTTP details.
- **synology-apm-cli**: A CLI front-end that uses the SDK as its sole dependency, allowing end users to operate APM from the terminal.

---

## Key Documents

| Document | Description | Priority |
|----------|-------------|----------|
| `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` | **SDK design contract**: enum ↔ API string mappings, non-obvious collection behavior rules, type system notes. Full public API signatures/docstrings live in source code (→ Sphinx). | required |
| `packages/synology-apm-cli/src/synology_apm/cli/README.md` | **CLI command spec**: command structure, output format, color/status rules, SDK call mapping. Full CLI → SDK mapping table lives at the end of the file. | implementation reference for CLI |
| `APM_PRODUCT_OVERVIEW.md` | APM product domain knowledge — backup/recovery model, workload categories, key concepts (not the SDK itself — see `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` for design rationale and interface) | background reference |

---

## APM Test Environment

> **Warning:** Test machine credentials (IP, username, password) **must never be written into any commit**. Read them from `.env` (already in `.gitignore`):

```bash
# .env (format)
APM_HOST=apm.corp.com        # hostname or IP; supports host:port; SDK prepends https:// automatically
                              # also accepts full URL format https://192.0.2.1 (conftest strips the scheme automatically)
APM_USERNAME=<username>
APM_PASSWORD=<password>
APM_NO_VERIFY_SSL=true       # set to true for self-signed certificate environments
```

> SSL: The test machine uses a self-signed certificate. Set `APM_NO_VERIFY_SSL=true` (or CLI `--no-verify-ssl`) when connecting.

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
| APM software version | `APM 1.2-71845` | |
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
- **SDK** (model / enum): exposes only semantic enum values (snake_case) — see `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` "Enum Definitions and API String Mapping" for the convention.
- **CLI** (commands): owns all enum → display-string mapping tables — see `packages/synology-apm-cli/src/synology_apm/cli/README.md` "Development Conventions" for the convention.

No layer may cross into another's domain: raw API strings must not appear in SDK models or CLI code, and display strings must not appear in SDK enum values.

### API Abstraction in User-Facing Text

Docstrings (module/class/method/function — published via Sphinx), CLI `help=` strings,
user-visible output strings, and the two PyPI READMEs (`packages/synology-apm-sdk/README.md`,
`packages/synology-apm-cli/README.md`) describe **domain/SDK-level behavior only**. They
must not mention:

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
> `packages/synology-apm-cli/src/synology_apm/cli/README.md`) are also exempt — their "Enum
> Definitions and API String Mapping", "Collection Behavior Rules", and "CLI → SDK Mapping
> Table" sections exist specifically to document these mappings for maintainers.

### Implementation Conventions

- The CLI interacts with APM only through the SDK (never raw HTTP); its command structure, output formats, and exit codes are specified in `packages/synology-apm-cli/src/synology_apm/cli/README.md` — command additions/removals must be reflected there (see Post-change Checklist).
- SDK-level conventions (code style, `__all__` exports, field-name mapping, `APMClient` constructor, domain separation between `apm.machine` / `apm.m365`) live in `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` → "Design Conventions".
- CLI-level conventions (error handling, output dispatch, workload resolution, argument validation, serialization) live in `packages/synology-apm-cli/src/synology_apm/cli/README.md` → "Development Conventions".

Both READMEs are part of the design contract — read them before implementing, not just when something breaks.

---

## Directory Structure

```
synology-apm-sdk-python/
├── pyproject.toml           ← workspace root; shared pytest/mypy/ruff config
├── uv.lock                   ← committed lockfile
├── Makefile                  ← make test / docs / build / smoke-test shortcuts (see Common Commands)
├── LICENSE
├── .env.example              ← template for the .env file described above
├── .github/workflows/        ← CI, docs, and release pipelines
├── APM_PRODUCT_OVERVIEW.md  ← product domain knowledge; see Key Documents table above
├── packages/
│   ├── synology-apm-sdk/
│   │   ├── pyproject.toml   ← PyPI project "synology-apm-sdk"
│   │   ├── README.md        ← PyPI README
│   │   └── src/synology_apm/sdk/  ← SDK package; see its README.md for file-level details
│   └── synology-apm-cli/
│       ├── pyproject.toml   ← PyPI project "synology-apm-cli"
│       ├── README.md        ← PyPI README
│       └── src/synology_apm/cli/  ← CLI package; see its README.md for file-level details
├── tests/
│   ├── cassette_lib.py  ← custom cassette record/replay system
│   ├── unit/            ← unit tests; mirrors sdk/collections/ and cli/commands/ (see "Test File Organization")
│   ├── integration/     ← cassette-backed real-server tests; mirrors sdk/collections/
│   ├── cassettes/       ← one JSON cassette per integration test; gitignored, local-only
│   └── smoke/           ← live smoke-test tools; smoke/cli/ drives the CLI binary, smoke/sdk/ the SDK (see their README.md)
├── skills/              ← apm-*/SKILL.md — generated CLI skills; do not edit by hand
├── examples/            ← runnable SDK usage examples; see examples/README.md and examples/CLAUDE.md
├── docs/
│   ├── conf.py              ← Sphinx config (committed; holds version/release strings)
│   ├── index.rst            ← Sphinx toctree root (committed; new modules added manually)
│   ├── Makefile             ← Sphinx build entry (invoked by root `make docs`)
│   └── api/                 ← sphinx-apidoc RST stubs (gitignored, generated)
└── scripts/
    ├── generate_skills.py   ← generates skills/ from CLI introspection + skills_data/*.toml
    ├── skills_data/          ← hand-written TOML sidecars for generate_skills.py
    ├── build-cli-macos.sh
    └── build-cli-windows.ps1
```

---

## Testing Standards

### Test scope: behavior vs. implementation

Tests must verify **observable behavior**, not internal implementation. The rule of thumb: a pure internal refactor that does not change public behavior or API contracts must not break any test.

| What to test | How |
|---|---|
| SDK → REST API request contract (URL, method, body, params, headers) | `aioresponses` URL interception; or `patch.object(session, "get/post")` + `call_args` inspection |
| API response → SDK model field parsing | Provide a complete raw API response fixture; assert model attribute values |
| Error handling | Provide an error response; assert the exception type and attributes (e.g. `.resource_id`) |
| Observable side effects | e.g. verify the logout endpoint was called after disconnect: `assert ("GET", URL(logout_url)) in m.requests` |

Do not test: `_private` **symbols** (leading-underscore functions, constants, attributes), internal call counts or arguments between methods of the same object, or private state fields. This applies to both the SDK and the CLI.

> **Note:** isinstance-only assertions are acceptable solely for facade property wiring tests
> (e.g. asserting `client.machine` returns `MachineCollection`) — there the returned collection
> type is the property's entire contract. Everywhere else, isinstance is not a meaningful
> assertion on its own.

> **Note:** When a test's own fake constructs and raises the exception, re-asserting the
> exception's fields is tautological and not required — the `assert_resource_error` rule below
> applies to exceptions raised by SDK code paths, not to pass-through fakes.

> **Note:** A leading underscore on a *module* (e.g. `cli/_display.py`, `cli/_serializers.py`, `cli/_validate.py`) marks it as not-for-end-users, not as untestable — the **public-named** functions inside these shared CLI modules (`fmt_*`, `*_to_dict` / `*_to_csv_row`, validators) are the CLI's internal contract and should have direct unit tests (`tests/unit/cli/test_display.py`, `test_serializers.py`, `test_arg_validation.py`). The `_`-prefixed display dicts themselves are tested only two ways: through their public `fmt_*` wrapper, and via set-equality enum-exhaustiveness checks in `test_display.py`.

### CLI test layering

CLI tests split responsibilities across two layers — do not duplicate one layer's checks in the other:

- **Command-level tests** (`tests/unit/cli/commands/`, via `invoke_cli` + a mocked APM client) verify that the command is wired to the SDK correctly: the right SDK method is called with the right arguments, exit codes are correct, and output dispatch/confirmation flow behaves as specified.
- **Shared-module tests** (`test_display.py`, `test_serializers.py`, `test_arg_validation.py`) verify display formatting and dict serialization field contracts directly. Command-level tests should not re-assert per-field formatting/serialization details that a shared-module test already covers.

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
| New SDK method added | Add a corresponding unit test in `tests/unit/sdk/collections/<module>.py` (or the relevant split file) |
| New SDK enum, model, or collection class added | Add it to `packages/synology-apm-sdk/src/synology_apm/sdk/__init__.py` and `__all__`; run `grep -r "from synology_apm\.sdk\." packages/synology-apm-cli/src examples/` and confirm no output |
| CLI command path changed (e.g. `synology-apm infra backup-server` → `synology-apm infra server`) | Update all test `runner.invoke(app, [...])` paths |
| Command moved to a different module (e.g. `backup_server.py` → `infra.py`) | Update all `patch("synology_apm.cli.commands.<old_module>.get_client", ...)` to the new module path |
| Dataclass field changed (added/deleted/renamed) | Update all tests that use that dataclass as a fixture |
| Integration test renamed or removed | Local-only, no commit impact (`tests/cassettes/` is gitignored): delete the corresponding cassette from `tests/cassettes/`; use `--record-mode=all` to re-record if the underlying API response changed |
| Major SDK refactor changes request/response format | Local-only, no commit impact: re-record your local cassettes: `pytest tests/integration/ --record-mode=all --import-mode=importlib` |

**Run before every commit:** `make test` (unit + integration tests, lint, mypy, coverage, skills check — same gate as CI).

### Documentation

| Change | Documents to sync |
|--------|------------------|
| CLI command path or structure adjusted | `packages/synology-apm-cli/src/synology_apm/cli/README.md`: command overview, command detailed spec, CLI → SDK mapping table |
| CLI-level convention changed (error handling, output dispatch, workload resolution, argument validation, serialization, enum display mapping) | `packages/synology-apm-cli/src/synology_apm/cli/README.md`: Development Conventions section |
| New SDK public method added | No README update needed (docstrings publish via Sphinx). Update `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` only if the method has non-obvious behavior (Collection Behavior Rules) or introduces a new collection/access path (Collection Map) |
| New enum added, or enum value / API string mapping changed | The mapping dict next to the parser is the source of truth. Update `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` (Enum Definitions and API String Mapping) only when the mapping's *semantics* are non-obvious (computed from multiple fields, one-to-many, magic values), or to add a new dict to its location index |
| Non-obvious collection behavior added or changed (e.g. multi-call logic, request body format, mode dispatch) | `packages/synology-apm-sdk/src/synology_apm/sdk/README.md`: Collection Behavior Rules section |
| New SDK public type added (class, enum, dataclass) | Source docstring (Attributes / class-level description); `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` only if the type has non-obvious behavior or mapping that cannot go in a docstring |
| SDK-level convention changed (code style, `__all__` exports, field-name mapping, `APMClient` constructor, domain separation) | `packages/synology-apm-sdk/src/synology_apm/sdk/README.md`: Design Conventions section |
| CLI command/option/flag/help text added, removed, or changed | Regenerate `skills/apm-*/SKILL.md`: `uv run python scripts/generate_skills.py` (or `--group <name>` for one skill). For a **new** command, also add its path to the relevant `commands` (or `extra_sections`) list in `scripts/skills_data/<group>.toml` first |
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

### CLAUDE.md

| Change | Sync required |
|--------|--------------|
| New source file added, or existing source file renamed/moved/deleted under `packages/synology-apm-sdk/src/synology_apm/sdk/` or `packages/synology-apm-cli/src/synology_apm/cli/` | Update the package README instead: `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` Package Structure for SDK files; `packages/synology-apm-cli/src/synology_apm/cli/README.md` Package Structure for CLI files (test file additions do **not** require this update) |
| New CLI command added (once feature is complete) | Add to the "CLI → SDK Mapping Table" in `packages/synology-apm-cli/src/synology_apm/cli/README.md`. (New SDK methods need no index update — see the Documentation table above) |

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

## Release / Version Bump

Both packages share a single **lockstep version number** — bump them together, never
independently. Releases are cut by the maintainers and published to PyPI and GitHub
automatically via `.github/workflows/release.yml`.

External contributors do not need to manage version numbers — include your changes in a PR
and the maintainers will handle versioning and publishing.

> **Note:** Publishing requires a one-time PyPI Trusted Publisher registration for both the
> `synology-apm-sdk` and `synology-apm-cli` projects — see the prerequisite comment at the
> top of `.github/workflows/release.yml`.

---

## Common Commands

```bash
# First-time setup
uv sync

# Run unit tests during development
uv run pytest tests/unit/ -v

# Record new integration cassettes against a live APM (only records missing ones)
uv run pytest tests/integration/ --record-mode=new_episodes --import-mode=importlib -v
# (= make record-integration-cassettes)

# Force re-record all cassettes (after major SDK refactors)
uv run pytest tests/integration/ --record-mode=all --import-mode=importlib -v

# Scope smoke tests to one domain group (full runs: make smoke-test-cli / make smoke-test-sdk)
uv run python -m tests.smoke.cli --group infra
uv run python -m tests.smoke.sdk --group machine

# Regenerate skills (make test only checks; run this after adding or changing commands)
uv run python scripts/generate_skills.py
uv run python scripts/generate_skills.py --group machine   # single skill

# Build API reference docs (first-time: uv sync --group docs)
make docs

# Makefile shortcuts (make test = unit + integration tests, lint, mypy, coverage, skills check)
make test
make record-integration-cassettes
make smoke-test                        # runs both CLI and SDK smoke tests
make build                             # wheel + sdist (runs make test first)
make whl                               # wheel + sdist without running tests
```

---

*For detailed change history, see git log.*

