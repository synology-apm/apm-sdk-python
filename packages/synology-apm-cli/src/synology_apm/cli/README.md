# APM CLI — Design Contract

> Corresponding product: Synology ActiveProtect Manager

**Purpose of this document**: A design contract for CLI implementers (human developers or AI
sessions) — command structure, output format conventions, color/status rules, and the
CLI → SDK call mapping. It is a guide for maintaining consistency, not a copy of `--help`
output: exact option lists live in each command's Typer decorators (`--help` shows them);
exact display strings and table-column definitions live next to the code that renders them
(`_display.py`, `_serializers.py`, `commands/*.py`) — see the note at the top of
[`COMMAND_REFERENCE.md`'s Detailed Command Specifications](COMMAND_REFERENCE.md#detailed-command-specifications).

---

## Table of Contents

- [Purpose and Design Principles](#purpose-and-design-principles)
- [Adding a New Command](#adding-a-new-command)
   - [Renaming or Moving a Command](#renaming-or-moving-a-command)
   - [Renaming or Changing an Option](#renaming-or-changing-an-option)
- [Package Structure](#package-structure)
- [Authentication Configuration](#authentication-configuration)
- [Global Options](#global-options)
- [Command Overview](#command-overview)

For output-format rendering, the per-command behavior contract, status/color conventions, and
error handling, see the companion [`COMMAND_REFERENCE.md`](COMMAND_REFERENCE.md) — read it
per-command as needed, not cover to cover.

---

## Purpose and Design Principles

### Purpose

See the root `CLAUDE.md`'s Project Background for what `synology-apm-cli` is. The data flow
below is the detail that matters for implementers: the CLI depends solely on the SDK and
never calls the REST API directly.

```
End user            →  apm CLI (synology_apm.cli)  →  apm SDK (synology_apm.sdk)  →  APM REST API
Program integration →                                  apm SDK (synology_apm.sdk)  →  APM REST API
```

### Design Principles

- **Domain-oriented command structure**: `synology-apm-cli machine` manages device backups, `synology-apm-cli saas` shows the SaaS application overview, `synology-apm-cli m365` manages M365 backups — aligned with the SDK's `apm.machine` / `apm.m365` object model
- **Resource-oriented addressing**: every resource-addressed command accepts either a positional `<NAME>` (search) or `--id` (+ `--namespace` for workloads) for direct lookup — see [Search / Direct mode](COMMAND_REFERENCE.md#search--direct-mode)
- **Progressive disclosure**: the most commonly used fields are shown by default; `--verbose` shows full information
- **Scriptable**: `--output json` outputs JSON for use with jq; the exit code reflects success/failure
- **Friendly terminal UX**: Rich colorized output, progress bars, clear error messages
- **Consistency**: options with the same semantics keep the same name across all commands (`--output`, `--since`)

### Development Conventions

When adding or modifying commands, follow the conventions of existing commands (actual signatures and details are in each module's docstrings and existing command examples):

- **Error handling / session setup**: SDK calls run inside `async with apm_session(ctx) as apm:` (`synology_apm.cli._helpers`), which stacks `apm_error_handler()` → optional `api_spinner` → `get_client()` in one context manager. List commands pass `spinner="Fetching ..."`; destructive commands pass `abortable=True` so a declined confirmation exits cleanly with `EXIT_CANCEL`. Do not hand-nest `apm_error_handler()` / `api_spinner()` / `get_client()` in commands.
- **Output dispatch**: non-table output is dispatched via `dispatch_list_output()` / `dispatch_output()` (`synology_apm.cli.output`); returning `True` ends early; `to_csv_row` fields must align with the table columns (not the JSON fields).
- **Workload resolution and argument validation**: when the search (`<NAME>`) and direct (`--id` / `--namespace`) modes are mutually exclusive, use `validate_*()` from `synology_apm.cli._validate` along with `WorkloadRef.resolve_machine()` / `.resolve_m365()` / `.resolve_gws()` — do not hand-roll the dispatch (see their docstrings for the resolution details). Any value the CLI auto-fills on the user's behalf (the M365 tenant when `--tenant-id` is omitted; the GWS domain when `--domain` is omitted; the version when `--id`/`--version-id` is omitted) must be reported back to the user via `print_resolved_saas_tenant()` (pass `label="domain"` for GWS) / `print_resolved_version()`.
- **Plan resolution (`--plan`)**: any `--plan <id or name>` option is resolved via the shared `_resolve_plan()` helper in `synology_apm.cli._validate` (see its docstring for the id-vs-name dispatch) — do not reimplement this inline. Which plan type it targets follows the operation, not user input: always a Retirement Plan for `retire` (retiring a workload always assigns one, regardless of its current state), the resolved workload's current state for `change-plan`, and the command's `--retired` flag for list filters. The repeatable `--plan` filter on `machine list` / `m365 <scope> list` / `gws <scope> list` maps the same helper over every value via `_resolve_plans()`. **Exception**: `infra server change-plan --plan` resolves against Tiering Plans through the separate `_resolve_tiering_plan()` helper, not `_resolve_plan()` — always use the domain-appropriate helper.
- **Stderr**: always use `err_console` (`synology_apm.cli.errors`); do not create a separate `Console(stderr=True)`.
- **Missing required arguments**: declare as `Optional` at the Typer layer; the function checks for `None` internally and prints `ctx.get_help()`, then exits with 0.
- **Global connection options that feed `resolve_connection()`'s priority cascade** (`--host`/`--username`/`--password`/`--profile`/`--no-verify-ssl`): declare with a `None` default, never a concrete value — a `None` means "not given" and lets the environment-variable/config-file fallback take effect, while an explicit value at any tier (CLI flag, env var, or config file) always wins over a lower-priority tier regardless of direction. Defaulting one of these options to a concrete value (e.g. `False`) instead of `None` silently breaks the cascade for that option, since the CLI would then always "explicitly" pass that default and the option could never fall through to the env var or config file.
- **Shared option constants**: the recurring pagination (`--limit`/`--offset`/`--page-all`), output (`--output`), and time-filter (`--since`/`--until`) options are declared once in `synology_apm.cli._options` and referenced as parameter defaults (e.g. `limit: int = LIMIT_OPTION`); `--since`/`--until` values are parsed with `parse_time_range(since, until)` from `synology_apm.cli._validate`. Only declare an option inline when its default or help text genuinely differs.
- **Destructive operations** (`retire`, overwrite-style `change-plan`): require interactive confirmation unless `--yes` is given; the summary message is always printed.
- **Enum display text**: all enum → display-string mapping tables live in `_display.py` (e.g. `_SERVER_STATUS_DISPLAY`, `_FILE_SERVER_TYPE_DISPLAY`, `_RESTORE_TYPE_DISPLAY`); command modules import and use them — they do not define their own. Each table is accessed through a public `fmt_*` wrapper (e.g. `fmt_server_status`, `fmt_export_status`) that owns the fallback for unmapped values, and the wrapper is what commands call and unit tests exercise. SDK enums contain only semantic values, so adding or adjusting display text requires changes in `_display.py` only. Display maps must always contain final display strings — no intermediate empty-string sentinels requiring call-site post-processing.
- **Datetime precision**: use `fmt_datetime()` for table/text output and `fmt_datetime_iso()` / `to_local_iso()` for JSON/CSV — see [`COMMAND_REFERENCE.md`'s Output Formats](COMMAND_REFERENCE.md#output-formats) for the resulting precision/format per mode. **Exception**: schedule time-of-day fields (`schedule.start_time`, `daily_check_time`) are always `HH:MM` (no seconds, no timezone) in every output format, since APM schedules only support minute granularity.
- **External non-SDK dependencies** (e.g. the OS keyring): wrap calls with a narrow `try/except` and the SDK-defined `KeyringUnavailableError` (re-exported via `synology_apm.sdk`), not `apm_error_handler()` — that helper converts `APMError` to structured messages and also converts `ValueError` to a plain `EXIT_ERROR` message; it re-raises everything else.
- **Two-factor authentication (TOTP)**: `config set` (`commands/config.py`) is the *only* place this CLI ever handles a two-factor code — its private `_verify_connection_and_register_device()` helper attempts a real connection and, on `OTPRequiredError`, prompts once (retrying on `OTPIncorrectError`) to register a trusted device, persisting the returned `device_id` into the profile. Every other command's `get_client()` (`_helpers.py`) only ever supplies that stored `device_id` and, on `OTPRequiredError`/`OTPIncorrectError`, reports an actionable "run `config set`" message and exits — it never prompts. Do not add interactive OTP handling anywhere else; there is deliberately no `--otp-code` global flag.
- **Shared backup/cancel/retire/change-plan action bodies**: `machine`, `m365`, and `gws` implement the same four destructive/state-changing commands (`backup`, `cancel`, `retire`, `change-plan`) with identical resolve → confirm → invoke → print-success flow. The domain-agnostic body of each lives once in `commands/_actions.py` (`_do_backup` / `_do_cancel` / `_do_retire` / `_do_change_plan`); each command module passes in closures for workload resolution and a `label_fn` callable (`_machine_type_label` for `machine`, `lambda wl: None` for `m365`/`gws`) to absorb the only real per-domain differences — see [`COMMAND_REFERENCE.md`'s Action confirmation flow](COMMAND_REFERENCE.md#action-confirmation-flow) for the resulting output difference.

### Flag Arity Reference

Two flag families are deliberately reused across many commands with more than one meaning
or arity. Both are correctly-applied conventions, not inconsistencies — read the specific
command's `--help` when in doubt.

**`--namespace`** — arity depends on context:

| Context | Arity | Notes |
|---|---|---|
| List-filter — `machine`/`m365`/`gws list --namespace`, `activity backup list --namespace` | Repeatable (`list[str] \| None`) | Mirrors the underlying SDK method's own arity: `MachineWorkloadCollection.list()` / `M365WorkloadCollection.list()` / `GWSWorkloadCollection.list()` / `BackupActivityCollection.list()` all take `list[str] \| None` (OR logic) |
| Direct-mode companion to `--id`/`--workload-id` — `get`/`backup`/`cancel`/`retire`/`change-plan`/`version *`/`export *` on `machine`/`m365`/`gws` | Scalar (`str \| None`) | A workload always has exactly one namespace; never co-occurs with the list-filter usage above in the same command |
| `log * list --id` | No `--namespace` at all | `--id` there names a *server* ID (a different resource than workload commands' `--id`) — a server's own namespace has no bearing on which server owns its logs |

**`--id`** — always names the current command's headline resource, with a resource-specific
companion flag used only when a second resource needs addressing in the same invocation:

| Commands | `--id` names | Companion flag |
|---|---|---|
| Workload `get`/`backup`/`cancel`/`retire`/`change-plan`; `plan */get`; `infra server/storage/hypervisor get`; `activity */get/cancel`; `log */list` | That command's one resource (workload, plan, server, storage, hypervisor, activity, or log server) | — |
| `version get`/`lock`/`unlock`; `export cancel` | The version/activity (the headline resource) | `--workload-id` names the owning workload |
| `version list`; `export list` | Not used — lists every version/export of one workload, so there's no single one for `--id` to address | `--workload-id` names the workload |
| `export download`, `--id` given (direct-download mode) | The activity | `--workload-id` names the owning workload; `--version-id` is accepted but ignored in this mode |
| `export download`, `--id` omitted (auto-start mode) | Not used — providing it switches to direct-download mode above | `--workload-id` (workload) + `--version-id` (a *third* distinct meaning: the backup version to export from) |

### Serialization Convention

When a resource needs no CLI-specific transform before output, command modules call the SDK
model's `to_dict()` directly (or pass the unbound method, e.g. `Hypervisor.to_dict`, as a
`dispatch_output`/`dispatch_list_output` callback) — do not add a zero-transform wrapper to
`_serializers.py` just to route through it. When CLI-specific work *is* needed (local-time
conversion, field renaming/flattening such as `plan_name`/`plan_id`, computed labels such as
`schedule_label`/`info_label`), add a named function in `_serializers.py` (e.g.
`workload_to_dict`, `protection_plan_to_dict`) and have command modules import and call it.
Command modules must not define their own `*_to_dict` / `*_to_csv_row` helpers (auditable via
`grep -r "def .*_to_dict" commands/` — expected empty). Do not call `print_json()` or
`dataclasses.asdict()` directly on an SDK model.

`_serializers.py`'s `*_to_dict` functions build on `d = obj.to_dict()` and mutate only the
deltas (`d[key] = ...` for an override/addition, `d[new] = d.pop(old)` for a rename, `del
d[key]` for a deliberate omission with a comment explaining why) — never manually reconstruct
the full dict field-by-field, since that silently drops any field added to the SDK model
later. `*_to_csv_row` functions build their flat, string-safe row independently — they are not
required to source from `to_dict()`.

### Technology Choices

| Package | Purpose |
|------|------|
| **Typer** | CLI framework; type hints auto-generate parameters and `--help` |
| **Rich** | Colorized tables, progress bars, tree structures, status icons |
| **synology-apm-sdk** | The sole dependency for APM operations; does not call the REST API directly |

---

## Adding a New Command

1. Implement it in `commands/<module>` — SDK calls only, never raw HTTP (see "Development Conventions" above).
2. Add a command-level unit test in `tests/unit/cli/commands/` (SDK wiring, exit codes, output dispatch — see `tests/CLAUDE.md` for the assertion conventions).
3. Update this README's [Command Overview](#command-overview) and `COMMAND_REFERENCE.md`'s [Detailed Command Specifications](COMMAND_REFERENCE.md#detailed-command-specifications) if the command surface changed.
4. Update `packages/synology-apm-cli/README.md` example blocks if user-facing usage changed.
5. Consider a matching invocation in `tests/smoke/cli/phases/_<domain>.py`.
6. Run `make test`.

### Renaming or Moving a Command

- Command path changed (e.g. `synology-apm-cli infra backup-server` → `synology-apm-cli infra server`): update all `runner.invoke(app, [...])` paths in its tests, and this README's Command Overview and `COMMAND_REFERENCE.md`'s Detailed Command Specifications.
- Command moved to a different module (e.g. `backup_server.py` → `infra.py`): update all `patch("synology_apm.cli.commands.<old_module>.get_client", ...)` call sites to the new module path.
- Either case: update `packages/synology-apm-cli/README.md` example blocks if the change is user-facing, and run `make test`.

### Renaming or Changing an Option

Applies when a flag's name or arity changes (e.g. `--id` → `--workload-id`, or a scalar
option becoming repeatable) — distinct from the command-level renames above:

- Update the command's own help text/examples (docstring and `--help` output).
- Update this README's Command Overview and `COMMAND_REFERENCE.md`'s Detailed Command
  Specifications, and `packages/synology-apm-cli/README.md`'s example blocks.
- Update the literal flag strings in every `runner.invoke(app, [...])` test call that
  exercises the old flag.
- Update the matching invocation in `tests/smoke/cli/phases/_<domain>.py`, if one exists —
  `make test` does not run the smoke suite (a separate `make smoke-test` target), so a clean
  `make test` will not catch a stale flag string there.
- Run `make test`.

---

## Package Structure

Each `commands/<name>.py` file implements the `synology-apm-cli <name> ...` top-level command
group named for the file; only files spanning multiple subcommand groups or with a
non-command role are annotated below.

Keep this tree in sync when a source file is added, renamed, or removed under
`synology_apm/cli/` — add an inline comment only when the file doesn't follow the naming
convention above.

```
synology_apm/cli/
├── __init__.py
├── main.py              # Typer app root entry point; registers all sub-apps
├── output.py            # Formatted output (table/json/yaml/csv); shared console instance
├── errors.py            # SDK Exception → CLI error message mapping; err_console
├── _async.py            # asyncio.run() wrapper (Typer has no native async support)
├── _helpers.py          # get_client(), apm_session(), api_spinner, enable_debug / is_debug
├── _options.py          # Shared typer.Option constants: LIMIT/VERSION_LIMIT, OFFSET, PAGE_ALL, LIST_OUTPUT/OUTPUT, SINCE/UNTIL
├── _display.py          # All enum → display-string mapping tables and formatting functions (fmt_*); print_list_footer / render_log_table / print_version_detail / print_workload_detail / render_version_table
├── _serializers.py      # All model → dict serializers (`*_to_dict` / `*_to_csv_row`) for every resource the CLI outputs
├── _validate.py         # validate_resolve_args, validate_version_workload_args, validate_version_lock_args, validate_activity_args, validate_name_or_id_args, parse_time_filter / parse_time_range, require_or_help, _resolve_saas_tenant_id, _resolve_plan, _resolve_plans, _resolve_tiering_plan; WorkloadRef.resolve_machine() / .resolve_m365() / .resolve_gws()
└── commands/
    ├── __init__.py
    ├── _actions.py      # Shared backup/cancel/retire/change-plan resolve-confirm-invoke-print bodies; consumed by machine.py, m365.py, and gws.py
    ├── config.py
    ├── machine.py
    ├── saas.py
    ├── m365.py
    ├── m365_export.py   # Shared M365 export infrastructure (_TENANT_ID_OPTION, _make_export_app, etc.); consumed by m365.py
    ├── gws.py
    ├── plan.py          # synology-apm-cli plan protection / synology-apm-cli plan retirement / synology-apm-cli plan tiering
    ├── activity.py
    ├── infra.py         # synology-apm-cli infra info / synology-apm-cli infra server ... / synology-apm-cli infra storage ... / synology-apm-cli infra hypervisor ...
    └── log.py           # synology-apm-cli log activity|drive|connection|system list
```

---

## Authentication Configuration

Connection settings are resolved by `synology_apm.sdk.resolve_connection()`. See
`packages/synology-apm-cli/README.md`'s "Authentication" section for the full flag/env-var list
and priority order; a config file profile's password may itself be stored in plaintext or looked
up from the OS keyring — see [`COMMAND_REFERENCE.md`'s config — Configuration Management](COMMAND_REFERENCE.md#config--configuration-management).

> **Note:** The config directory follows the XDG Base Directory Specification:
> `$XDG_CONFIG_HOME/synology-apm` when set to a non-empty absolute path, otherwise
> `~/.config/synology-apm` (the default shown throughout this document).

### Configuration File Format

```toml
# ~/.config/synology-apm/config.toml

[default]
host     = "apm.corp.com"
username = "admin"
# password / password_storage are only written by `config set --save-password`

[lab]
host     = "apm2.corp.com:10443"
username = "admin"
password_storage = "keyring"   # password itself lives in the OS keyring, not this file
device_id = "8nC0nhJjgi..."    # trusted-device token, registered by `config set` for a
                                # two-factor-enabled account — see "Development Conventions" above
```

See `packages/synology-apm-cli/README.md`'s "Config file — recommended" section for the
plaintext-vs-keyring tradeoff and its user-facing warning.

---

## Global Options

Full behavior, priority order, and examples for each of these are in
`packages/synology-apm-cli/README.md`'s "Authentication" and "Debugging" sections; do not
restate that explanation here when it changes, just keep this list in sync: `--host`,
`--username`/`-u`, `--password`/`-p`, `--profile`, `--no-verify-ssl`, `--no-input`, `--debug`,
`--help`/`-h`.

The following options are **per-command**, not global — they are declared only on the commands that support them, but keep the same name, short flag, and semantics everywhere they appear. Run `synology-apm-cli <command> --help` to see exactly which options a given command accepts.

| Option | Short | Where | Description |
|------|--------|------|------|
| `--output` | `-o` | list / get / info commands | Output format: `table` (default), `json`, `yaml`; list commands also support `csv` |
| `--verbose` | `-v` | list commands (and select others) | Show additional columns / fields |
| `--quiet` | `-q` | action commands (backup / cancel / retire / change-plan / lock / unlock / ...) | Suppress success messages (suitable for scripts) |
| `--yes` | `-y` | destructive commands | Skip the confirmation prompt (summaries are still printed) |

---

## Command Overview

This tree shows command **structure** only — the group hierarchy and each command's
addressing mode (positional `<NAME>` search vs. `--id`/`--namespace` direct lookup, per
[Search / Direct mode](COMMAND_REFERENCE.md#search--direct-mode)). It intentionally omits filter/option flags;
run `synology-apm-cli <command> --help` for the full, authoritative option list.

```
synology-apm-cli
├── config
│   ├── set       Configure connection settings (interactive wizard)
│   ├── show      Show current configuration
│   └── clear     Clear configuration
│
├── machine                                 # Device Workloads (PC / Physical Server / VM / File Server)
│   ├── list         List device Workloads
│   ├── get          <NAME> | --id + --namespace          View Workload details
│   ├── backup       <NAME> | --id + --namespace          Trigger a manual backup
│   ├── cancel       <NAME> | --id + --namespace          Cancel an in-progress backup
│   ├── retire       <NAME> | --id + --namespace  --plan <PLAN>   Retire a Workload (irreversible)
│   ├── change-plan  <NAME> | --id + --namespace  --plan <PLAN>   Change the assigned Protection/Retirement Plan
│   └── version
│       ├── list    List backup version history
│       ├── get     [--id <VERSION_ID>]   Version + activity detail (omit --id for the latest)
│       ├── lock    --id <VERSION_ID>     Lock a version (protects it from retention deletion)
│       └── unlock  --id <VERSION_ID>     Unlock a version
│
├── saas                                    # SaaS Application Overview (M365 / GWS)
│   └── list         List all connected SaaS applications
│
├── m365                                    # Microsoft 365 Workload Management, grouped by service type:
│   │                                       #   exchange / onedrive / chat / group / sharepoint / teams
│   └── <scope>      # Each scope has the same subcommand set as `machine`, plus:
│       └── export   # exchange / group only — Mailbox PST export
│           ├── list      List export tasks for a Workload
│           ├── cancel    --id <ACTIVITY_ID>   Cancel an in-progress export
│           └── download  [--id <ACTIVITY_ID>]   Start a new export and download it (no --id), or download an existing one
│
├── gws                                      # Google Workspace Workload Management, grouped by service type:
│   │                                       #   mail / calendar / contact / drive / shared-drive
│   └── <scope>      # Each scope has the same subcommand set as `machine` (no `export`)
│
├── plan                                    # Protection, Retirement, and Tiering Plan Management
│   ├── protection   list / get    Backup protection plans
│   ├── retirement   list / get    Retirement plans (used by machine/m365/gws `retire`)
│   └── tiering      list / get    Tiering plans (applied to backup servers)
│
├── activity
│   ├── backup    list / get / cancel    Backup activity records
│   └── restore   list / get / cancel    Restore activity records
│
├── infra                                   # Infrastructure Information
│   ├── info         Management Server info + cluster storage statistics
│   ├── server       list / get / change-plan   Backup servers; change-plan applies/removes a Tiering Plan
│   ├── storage      list / get                 Remote storage devices (External Vault)
│   └── hypervisor   list / get                 Hypervisor Inventory servers
│
└── log                                      # Backup server logs (DP servers only)
    ├── activity     list   Activity logs (protection / system / data_access)
    ├── drive        list   Drive information logs
    ├── connection   list   Connection logs
    └── system       list   Advanced system logs
```

The `m365` scopes share one command implementation; see `COMMAND_REFERENCE.md`'s
[m365 — M365 Workload Management](COMMAND_REFERENCE.md#m365--m365-workload-management) for the per-scope
differences (identifier field, tenant auto-resolution, which scopes support `export`).

### Known Gaps

SDK capabilities with no CLI command at all, deliberately out of scope for the
parameter-consistency refactor — tracked here rather than left silently absent:

- **Plan CRUD** — `plan protection/retirement/tiering` are list/get only; the SDK also
  supports create/update/delete for all three plan types (plus per-domain create/update
  for Machine/M365/GWS Protection Plans), with no CLI command surfacing any of it.
- **Workload delete** — only `retire` is exposed for Machine/M365/GWS workloads; the SDK's
  `.delete()` methods have no CLI command (retire is plausibly the deliberately-safer
  operation; delete's absence has not been confirmed as intentional).
- **File Server registration** — `machine.workloads.add_file_server()` /
  `.update_file_server()` have no CLI command; File Server workloads can be listed/backed
  up/retired via `machine`, but not registered or reconfigured.
- **Backup-verification video retrieval** — `machine.workloads.get_verification_video_url()`
  has no CLI command.
- **Remote Storage add/update/delete** — `infra storage` is list/get only; the SDK also
  supports registering, updating, and removing an external vault.
- **M365/GWS Auto Backup Rules** — `apm.m365.auto_backup_rules` / `apm.gws.auto_backup_rules`
  (list/create/update/delete/update_collab_settings, plus
  `update_protected_account_types` for GWS) have no CLI command group at all — the largest
  gap in this list.

---

*For per-command behavior, output rendering, and error handling, see
[`COMMAND_REFERENCE.md`](COMMAND_REFERENCE.md).*
