# APM CLI — Design Contract

> Corresponding product: Synology ActiveProtect Manager 1.2

**Purpose of this document**: A design contract for CLI implementers (human developers or AI
sessions) — command structure, output format conventions, color/status rules, and the
CLI → SDK call mapping. It is a guide for maintaining consistency, not a copy of `--help`
output: exact option lists live in each command's Typer decorators (`--help` shows them);
exact display strings and table-column definitions live next to the code that renders them
(`_display.py`, `_serializers.py`, `commands/*.py`) — see the note at the top of
[Detailed Command Specifications](#detailed-command-specifications).

---

## Table of Contents

- [Purpose and Design Principles](#purpose-and-design-principles)
- [Package Structure](#package-structure)
- [Authentication Configuration](#authentication-configuration)
- [Global Options](#global-options)
- [Command Overview](#command-overview)
- [Output Formats](#output-formats)
- [Detailed Command Specifications](#detailed-command-specifications)
   - [config — Configuration Management](#config--configuration-management)
   - [machine — Device Workload Management](#machine--device-workload-management)
   - [saas — SaaS Tenant Overview](#saas--saas-tenant-overview)
   - [m365 — M365 Workload Management](#m365--m365-workload-management)
   - [m365 exchange export / m365 group export — Mailbox PST Export](#m365-exchange-export--m365-group-export--mailbox-pst-export)
   - [plan protection / retirement / tiering — Plan Management](#plan-protection--retirement--tiering--plan-management)
   - [activity — Activity Log Queries](#activity--activity-log-queries)
   - [infra — Infrastructure Information](#infra--infrastructure-information)
   - [synology-apm-cli log — Backup Server Logs](#synology-apm-cli-log--backup-server-logs)
- [Status and Color Conventions](#status-and-color-conventions)
- [Error Handling](#error-handling)

---

## Purpose and Design Principles

### Purpose

`synology-apm-cli` is the official CLI front-end for the APM Python SDK, providing a complete experience for end users to operate APM from the terminal.
The CLI depends solely on the SDK and does not call the REST API directly.

```
End user            →  apm CLI (synology_apm.cli)  →  apm SDK (synology_apm.sdk)  →  APM REST API
Program integration →                                  apm SDK (synology_apm.sdk)  →  APM REST API
```

### Design Principles

- **Domain-oriented command structure**: `synology-apm-cli machine` manages device backups, `synology-apm-cli saas` shows the SaaS tenant overview, `synology-apm-cli m365` manages M365 backups — aligned with the SDK's `apm.machine` / `apm.m365` object model
- **Resource-oriented addressing**: every resource-addressed command accepts either a positional `<NAME>` (search) or `--id` (+ `--namespace` for workloads) for direct lookup — see [Search / Direct mode](#search--direct-mode)
- **Progressive disclosure**: the most commonly used fields are shown by default; `--verbose` shows full information
- **Scriptable**: `--output json` outputs JSON for use with jq; the exit code reflects success/failure
- **Friendly terminal UX**: Rich colorized output, progress bars, clear error messages
- **Consistency**: options with the same semantics keep the same name across all commands (`--output`, `--since`)

### Development Conventions

When adding or modifying commands, follow the conventions of existing commands (actual signatures and details are in each module's docstrings and existing command examples):

- **Error handling / session setup**: SDK calls run inside `async with apm_session(ctx) as apm:` (`synology_apm.cli._helpers`), which stacks `apm_error_handler()` → optional `api_spinner` → `get_client()` in one context manager. List commands pass `spinner="Fetching ..."`; destructive commands pass `abortable=True` so a declined confirmation exits cleanly with `EXIT_CANCEL`. Do not hand-nest `apm_error_handler()` / `api_spinner()` / `get_client()` in commands.
- **Output dispatch**: non-table output is dispatched via `dispatch_list_output()` / `dispatch_output()` (`synology_apm.cli.output`); returning `True` ends early; `to_csv_row` fields must align with the table columns (not the JSON fields).
- **Workload resolution and argument validation**: when the search (`<NAME>`) and direct (`--id` / `--namespace`) modes are mutually exclusive, use `validate_*()` from `synology_apm.cli._validate` along with `WorkloadRef.resolve_machine()` / `.resolve_m365()` — do not hand-roll the dispatch (see their docstrings for the resolution details). Any value the CLI auto-fills on the user's behalf (the M365 tenant when `--tenant-id` is omitted; the version when `--id`/`--version-id` is omitted) must be reported back to the user via `print_resolved_tenant()` / `print_resolved_version()`.
- **Plan resolution (`--plan`)**: any `--plan <id or name>` option is resolved via the shared `_resolve_plan()` helper in `synology_apm.cli._validate` (see its docstring for the id-vs-name dispatch) — do not reimplement this inline. Which plan type it targets follows the operation, not user input: always a Retirement Plan for `retire` (retiring a workload always assigns one, regardless of its current state), the resolved workload's current state for `change-plan`, and the command's `--retired` flag for list filters. The repeatable `--plan` filter on `machine list` / `m365 <scope> list` maps the same helper over every value via `_resolve_plans()`. **Exception**: `infra server change-plan --plan` resolves against Tiering Plans through the separate `_resolve_tiering_plan()` helper, not `_resolve_plan()` — always use the domain-appropriate helper.
- **Stderr**: always use `err_console` (`synology_apm.cli.errors`); do not create a separate `Console(stderr=True)`.
- **Missing required arguments**: declare as `Optional` at the Typer layer; the function checks for `None` internally and prints `ctx.get_help()`, then exits with 0.
- **Global connection options that feed `resolve_connection()`'s priority cascade** (`--host`/`--username`/`--password`/`--profile`/`--no-verify-ssl`): declare with a `None` default, never a concrete value — a `None` means "not given" and lets the environment-variable/config-file fallback take effect, while an explicit value at any tier (CLI flag, env var, or config file) always wins over a lower-priority tier regardless of direction. Defaulting one of these options to a concrete value (e.g. `False`) instead of `None` silently breaks the cascade for that option, since the CLI would then always "explicitly" pass that default and the option could never fall through to the env var or config file.
- **Shared option constants**: the recurring pagination (`--limit`/`--offset`/`--page-all`), output (`--output`), and time-filter (`--since`/`--until`) options are declared once in `synology_apm.cli._options` and referenced as parameter defaults (e.g. `limit: int = LIMIT_OPTION`); `--since`/`--until` values are parsed with `parse_time_range(since, until)` from `synology_apm.cli._validate`. Only declare an option inline when its default or help text genuinely differs.
- **Destructive operations** (`retire`, overwrite-style `change-plan`): require interactive confirmation unless `--yes` is given; the summary message is always printed.
- **Serialization**: when a resource needs no CLI-specific transform before output, command modules call the SDK model's `to_dict()` directly (or pass the unbound method, e.g. `Hypervisor.to_dict`, as a `dispatch_output`/`dispatch_list_output` callback) — do not add a zero-transform wrapper to `_serializers.py` just to route through it. When CLI-specific work *is* needed (local-time conversion, field renaming/flattening such as `plan_name`/`plan_id`, computed labels such as `schedule_label`/`info_label`), add a named function in `_serializers.py` (e.g. `workload_to_dict`, `protection_plan_to_dict`) and have command modules import and call it. Command modules must not define their own `*_to_dict` / `*_to_csv_row` helpers (auditable via `grep -r "def .*_to_dict" commands/` — expected empty). Do not call `print_json()` or `dataclasses.asdict()` directly on an SDK model. `_serializers.py`'s `*_to_dict` functions build on `d = obj.to_dict()` and mutate only the deltas (`d[key] = ...` for an override/addition, `d[new] = d.pop(old)` for a rename, `del d[key]` for a deliberate omission with a comment explaining why) — never manually reconstruct the full dict field-by-field, since that silently drops any field added to the SDK model later. `*_to_csv_row` functions build their flat, string-safe row independently — they are not required to source from `to_dict()`.
- **Enum display text**: all enum → display-string mapping tables live in `_display.py` (e.g. `_SERVER_STATUS_DISPLAY`, `_FILE_SERVER_TYPE_DISPLAY`, `_RESTORE_TYPE_DISPLAY`); command modules import and use them — they do not define their own. Each table is accessed through a public `fmt_*` wrapper (e.g. `fmt_server_status`, `fmt_export_status`) that owns the fallback for unmapped values, and the wrapper is what commands call and unit tests exercise. SDK enums contain only semantic values, so adding or adjusting display text requires changes in `_display.py` only. Display maps must always contain final display strings — no intermediate empty-string sentinels requiring call-site post-processing.
- **Datetime precision**: table/text output shows local time at second precision (`YYYY-MM-DD HH:MM:SS` via `fmt_datetime()`); JSON/CSV output uses local-timezone ISO 8601 (via `fmt_datetime_iso()` / `to_local_iso()`). **Exception**: schedule time-of-day fields (`schedule.start_time`, `daily_check_time`) are always `HH:MM` (no seconds, no timezone) in every output format, since APM schedules only support minute granularity.
- **External non-SDK dependencies** (e.g. the OS keyring): wrap calls with a narrow `try/except` and the SDK-defined `KeyringUnavailableError` (re-exported via `synology_apm.sdk`), not `apm_error_handler()` — that helper converts `APMError` to structured messages and also converts `ValueError` to a plain `EXIT_ERROR` message; it re-raises everything else.
- **Shared backup/cancel/retire/change-plan action bodies**: `machine` and `m365` implement the same four destructive/state-changing commands (`backup`, `cancel`, `retire`, `change-plan`) with identical resolve → confirm → invoke → print-success flow. The domain-agnostic body of each lives once in `commands/_actions.py` (`_do_backup` / `_do_cancel` / `_do_retire` / `_do_change_plan`); each command module passes in closures for workload resolution and a `label_fn` callable (`_machine_type_label` for `machine`, `lambda wl: None` for `m365`, since M365 workloads have no type label in this output) to absorb the only real per-domain differences.

### Technology Choices

| Package | Purpose |
|------|------|
| **Typer** | CLI framework; type hints auto-generate parameters and `--help` |
| **Rich** | Colorized tables, progress bars, tree structures, status icons |
| **synology-apm-sdk** | The sole dependency for APM operations; does not call the REST API directly |

---

## Package Structure

Each `commands/<name>.py` file implements the `synology-apm-cli <name> ...` top-level command
group named for the file; only files spanning multiple subcommand groups or with a
non-command role are annotated below.

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
├── _validate.py         # validate_resolve_args, validate_version_workload_args, validate_version_lock_args, validate_activity_args, validate_name_or_id_args, parse_time_filter / parse_time_range, require_or_help, _resolve_tenant, _resolve_plan, _resolve_plans, _resolve_tiering_plan; WorkloadRef.resolve_machine() / .resolve_m365()
└── commands/
    ├── __init__.py
    ├── _actions.py      # Shared backup/cancel/retire/change-plan resolve-confirm-invoke-print bodies; consumed by machine.py and m365.py
    ├── config.py
    ├── machine.py
    ├── saas.py
    ├── m365.py
    ├── m365_export.py   # Shared M365 export infrastructure (_TENANT_ID_OPTION, _make_export_app, etc.); consumed by m365.py
    ├── plan.py          # synology-apm-cli plan protection / synology-apm-cli plan retirement / synology-apm-cli plan tiering
    ├── activity.py
    ├── infra.py         # synology-apm-cli infra info / synology-apm-cli infra server ... / synology-apm-cli infra storage ... / synology-apm-cli infra hypervisor ...
    └── log.py           # synology-apm-cli log activity|drive|connection|system list
```

---

## Authentication Configuration

Connection settings are resolved by `synology_apm.sdk.resolve_connection()`, priority high → low:

1. CLI flags: `--host` / `--username` / `--password` / `--profile` / `--no-verify-ssl`
2. Environment variables: `APM_HOST` / `APM_USERNAME` / `APM_PASSWORD` / `APM_PROFILE` / `APM_NO_VERIFY_SSL`
3. Config file profile (`~/.config/synology-apm/config.toml`, selected via `--profile` / `APM_PROFILE`) — a profile's password may itself be stored in plaintext in this file or looked up from the OS keyring; see [config — Configuration Management](#config--configuration-management).

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
```

> **Warning:** `password_storage = "plaintext"` stores the password in plaintext in this
> file. Recommended only for trusted local environments; prefer `password_storage =
> "keyring"` (OS-native credential store) or the `APM_PASSWORD` environment variable instead.

### Environment Variables

```bash
APM_PROFILE=lab          # Select a profile in config.toml

# Or set connection details directly instead of using a profile:
APM_HOST=apm.corp.com
APM_USERNAME=admin
APM_PASSWORD=secret
APM_NO_VERIFY_SSL=true   # Skip SSL verification (self-signed certificates)
```

---

## Global Options

The following options are defined on the root command and accepted by every subcommand; for the configuration source priority order of `--host`, `--username`, `--password`, `--profile`, and `--no-verify-ssl`, see [Authentication Configuration](#authentication-configuration).

| Option | Short | Description |
|------|--------|------|
| `--host` | | APM hostname or IP, supports host:port (https:// is prepended automatically) |
| `--username` | `-u` | APM account |
| `--password` | `-p` | APM password; prompted interactively if missing |
| `--profile` | | Use the specified config profile (default `default`) |
| `--no-verify-ssl` | | Skip SSL verification |
| `--no-input` | | Disable all interactive prompts; if required input is missing, immediately error with exit 1 (suitable for scripts / CI environments) |
| `--debug` | | Print every API request and response to stderr (hidden, not shown in `--help`) |
| `--help` | `-h` | Show help |

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
[Search / Direct mode](#search--direct-mode)). It intentionally omits filter/option flags;
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
├── saas                                    # SaaS Tenant Overview (M365 / GWS)
│   └── list         List all connected SaaS tenants
│
├── m365                                    # Microsoft 365 Workload Management, grouped by service type:
│   │                                       #   exchange / onedrive / chat / group / sharepoint / teams
│   └── <scope>      # Each scope has the same subcommand set as `machine`, plus:
│       └── export   # exchange / group only — Mailbox PST export
│           ├── list      List export tasks for a Workload
│           ├── cancel    --id <ACTIVITY_ID>   Cancel an in-progress export
│           └── download  [--id <ACTIVITY_ID>]   Start a new export and download it (no --id), or download an existing one
│
├── plan                                    # Protection, Retirement, and Tiering Plan Management
│   ├── protection   list / get    Backup protection plans
│   ├── retirement   list / get    Retirement plans (used by machine/m365 `retire`)
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

The `m365` scopes share one command implementation; see
[m365 — M365 Workload Management](#m365--m365-workload-management) for the per-scope
differences (identifier field, tenant auto-resolution, which scopes support `export`).

---

## Output Formats

This section is the canonical example library: each interaction pattern (list table, get detail block, action confirmation, irreversible warning) is rendered **once** here, using `machine` commands as the subject. Command sections in [Detailed Command Specifications](#detailed-command-specifications) do not repeat rendered output — they only note how their output differs from these patterns.

### Table (default)

Rendered with Rich, with status colors and icons. Canonical list example (`synology-apm-cli machine list` default columns):

```
$ synology-apm-cli machine list

 Name            Type             Status         Verification  Last Backup          Protected Size  Copy Size  Protection Plan  Backup Server  Copy Destination
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
 CORP-PC-001     PC/Mac           ✓ Success      -             2026-04-21 09:23:00  145.3 GB        -          Daily Backup     apm-server-01  -
 prod-server-01  Physical Server  ✓ Success      ✓ Success     2026-04-21 09:15:00  80.2 GB         12.4 GB    Daily Backup     apm-server-01  DSM-Storage (MyVault)
 vm-web-01       Virtual Machine  — No Backups   -             -                    0 B             -          Daily Backup     apm-server-01  -
 old-laptop      PC/Mac           ✗ Failed       -             2026-03-10 14:00:00  22.1 GB         -          Daily Backup     apm-server-01  -
Showing 4 of 4
```

> **Note (pagination summary):** table list output is followed by `Showing N of M` (number of results / total matching count). When `--offset` is used, it shows `Showing X–Y of M`. When the endpoint does not report a total count, only `Showing N` is printed.

Every list command's exact column set (and which extra columns `--verbose` adds) is defined
next to that command's table-rendering function in `commands/*.py` / `_display.py` — treat
that code as the source of truth rather than a hand-maintained column list here.

### JSON (`--output json`)

Outputs a curated set of fields (nested structure, matching the SDK model's `to_dict()`), suitable for jq / script processing. datetime fields are output in local-timezone ISO 8601 (e.g. `2026-05-16T16:54:20+08:00`):

```bash
$ synology-apm-cli machine list --output json | jq '.[].name'
```

```json
[
  {
    "workload_id": "123e4567-e89b-12d3-a456-426614174000",
    "name": "vm-web-01",
    "workload_type": "virtual_machine",
    "status": "success",
    "last_backup_at": "2026-04-21T09:23:00+08:00",
    "protected_data_bytes": 155917238272,
    "backup_server": { "name": "apm-server-01", "endpoint": "192.0.2.1", "..." : "..." },
    "plan_name": "Daily Backup",
    "plan_id": "123e4567-e89b-12d3-a456-426614174002"
  }
]
```

> **Note:** the full field set is the workload model's `to_dict()` output; see the SDK model
> docstring for every field and when each is populated (e.g. `backup_progress` only while a
> backup is in progress, `fs_config` only for File Server workloads).

### YAML (`--output yaml`)

Same fields as JSON, rendered as YAML — e.g. `synology-apm-cli machine get "CORP-PC-001" --output yaml`.

### CSV (`--output csv`)

**Supported only by list commands** (get commands do not offer this option). Outputs a flattened set of fields, suitable for importing into spreadsheets or pipeline processing:

```bash
$ synology-apm-cli machine list --output csv > machines.csv
```

Field policy:
- The field set aligns with table mode (not the full set of dataclass fields)
- Values are machine-readable raw values (datetime → local-timezone ISO 8601; bytes → integer; enum → semantic string)
- Nested objects are flattened into separate fields (e.g. `backup_server_name`, `retention_type`)
- Empty values are output as an empty string

### Auto-pagination (`--page-all`)

All list commands that support `--limit` / `--offset` provide a `--page-all` flag: starting from `--offset`, using `--limit`
as the page size, it automatically fetches page by page until all data is retrieved (with a fixed internal delay between fetches).

| `--page-all` + `--output` | Actual output |
| --- | --- |
| `--page-all --output table` (default) | First fetches all pages, then renders a **single** merged table and footer, in the same format as a single-page output without `--page-all` |
| `--page-all --output json` | NDJSON: each record is output as one line of compact JSON, streamed page by page |
| `--page-all --output csv` | The first page outputs the header + data rows; subsequent pages output only data rows (same field order) |
| `--page-all --output yaml` | Each page is prefixed with `---`, forming a YAML multi-document stream |

`synology-apm-cli infra storage list` and `synology-apm-cli infra hypervisor list` do not support `--limit` / `--offset` (the API returns all data in one call), so `--page-all` is not provided.

### Search / Direct mode

Resource-addressed commands accept two mutually exclusive addressing modes; combining them is an argument error, and omitting both prints the command help:

- **Search mode** — positional `<NAME>`: keyword search, then case-insensitive exact match. Machine workloads match on name; M365 workloads match on display name / UPN / group email and are scoped by `-t/--tenant-id` (auto-resolves to the first M365 tenant when omitted, reported on stderr as `(Using tenant: <id>)`). Plan / server / storage / hypervisor commands match on name (or endpoint/address where noted per command).
- **Direct mode** — `--id <ID>`: direct ID lookup. Workload commands additionally require `--namespace <NS>`; `version` and `export` subcommands use `--workload-id` instead, because `--id` there addresses the version/activity.

### Get detail block

Canonical example (`synology-apm-cli machine get`, table mode). Workload get commands (`machine get` / `m365 <scope> get`) share this two-block layout; plan / infra get commands use a similar `Header: <name>` + `─` rule + `Label: value` section layout whose fields are listed per command.

```
$ synology-apm-cli machine get "CORP-PC-001"

Workload: CORP-PC-001
── Workload Information
  ID:             123e4567-e89b-12d3-a456-426614174000
  Namespace:      123e4567-e89b-12d3-a456-426614174001
  Type:           Machine / PC/Mac
  Device UUID:    123e4567-e89b-12d3-a456-426614174006
  Agent:          1.2.0-71845
  IP:             192.0.2.30

── Backup Status
  Status:         ✓ Success
  Plan:           Daily Backup
  Plan ID:        123e4567-e89b-12d3-a456-426614174002
  Last Backup:    2026-04-21 09:23:00
  Protected Size: 145.3 GB
  Backup Server:  apm-server-01
  Copy Dest:      -
```

> - The Workload Information block always starts with ID / Namespace / Type; the rows after Type vary by workload type (see each get command).
> - The Backup Status block ends with Plan / Plan ID / Last Backup / Protected Size / Backup Server / Copy Dest; a `Copy Size` row is inserted before Backup Server only when the workload has backup copy data (non-zero).
> - Retired workload: the Backup Status block does not show the Status line.

### Copy status detail lines

Wherever a backup-copy / tiering status is rendered in a detail view (`plan protection get` Copy Status, `plan tiering get` and `infra server get` Tiering Status — all via `fmt_copy_status` / `fmt_copy_reason`), the status line appears only when the status is set and not NOT_ENABLED, and is followed by up to two indented detail lines:

```
<formatted status>
<N version(s) pending, X remaining>   ← WAITING/SCHEDULED/IN_PROGRESS/RETRY/FAILED, when pending_version_count > 0;
                                         the ", X remaining" suffix is omitted when remaining_bytes is unavailable
<N workload(s) skipped.>              ← SKIPPED: skipped workload count (plan protection get only)
<error detail message>                ← RETRY/FAILED/SKIPPED: reason string (fmt_copy_reason)
```

### Action confirmation flow

Canonical example (`synology-apm-cli machine change-plan`). The plan and workload summary is always printed (to stderr) even with `--yes`; `--yes` skips only the prompt. A declined confirmation prints `Cancelled.` and exits 4. Note the ASCII `->` arrow in the `Current plan:` line.

```
$ synology-apm-cli machine change-plan "CORP-PC-001" --plan "Daily Backup"

Applying protection plan:
  Plan:      Daily Backup (123e4567-e89b-12d3-a456-426614174002)
  Retention: 30 days
  Schedule:  Daily Backup
  Workload:  CORP-PC-001 (PC/Mac, ID: 123e4567-e89b-12d3-a456-426614174000)

⚠ Current plan: Old Plan -> Daily Backup

Confirm change plan? [y/N]: y
✓ Plan changed: CORP-PC-001
```

> - When the resolved plan is a Retirement Plan (retired workload), the header is `Updating retirement plan:` and the summary shows Plan / Retention only (no Schedule line).
> - The workload type label in parentheses is machine-only; M365 workloads show `<name> (ID: <workload-id>)` without a type label.
> - Simple cancel confirmations (`machine cancel` / `m365 <scope> cancel`) use a shorter variant: header `⚠ Confirm cancel backup?`, `  Workload:  <name> (<type label>)` (no type label for M365), prompt `  Confirm? [y/N]:`, success line `✓ Backup cancelled: <name>`.

### Irreversible-warning flow

Canonical example (`synology-apm-cli machine retire`). Even with `--yes`, the warning summary is still printed (this action is irreversible — the summary must be reviewable):

```
$ synology-apm-cli machine retire "old-laptop" --plan "Compliance Retention"

⚠ Warning: this action is irreversible!

  Workload:     old-laptop (PC/Mac)
  Retirement Plan: Compliance Retention (123e4567-e89b-12d3-a456-426614174003)
  Retention:    90 days
  The workload will be retired and no longer backed up.
  Existing backup versions will not be deleted immediately.

  Confirm retire? [y/N]: y
✓ Workload retired: old-laptop
```

> The M365 variant is identical except the Workload line has no type label. The `config set` interactive wizard transcript lives in the [config section](#config--configuration-management).

---

## Detailed Command Specifications

> This section records only what `--help` and the canonical [Output Formats](#output-formats)
> examples above can't show: Search/Direct mode deviations, cross-command behavioral rules,
> and non-obvious flow logic (e.g. how a value is auto-resolved, what triggers a specific
> error, an action's exact terminal states). It deliberately does **not** enumerate table
> columns, enum → display-string mappings, or full JSON shapes — those live as code next to
> their implementation (`_display.py`'s `*_DISPLAY` dicts and `fmt_*` functions,
> `_serializers.py`'s `*_to_dict` functions, each command's table-rendering function) and
> are visible by reading that source or running the command.

### config — Configuration Management

`config set` is an interactive wizard (host → username → password → SSL verify); `--host`/`--username` pre-fill their prompts, `--save-password {plaintext|keyring}` forces a confirmed password prompt and saves it, and `--no-input` requires `--host`/`--username` up front, rejects `--save-password`, and leaves the password unsaved.

```
$ synology-apm-cli config set

APM host (e.g. apm.corp.com or apm.corp.com:10443): apm.corp.com
Username: admin
Password (leave blank to prompt each time, not saved):
Skip SSL verification? (choose y for self-signed certificates) [y/N]: y

✓ Settings saved to ~/.config/synology-apm/config.toml (profile: default)
```

`config show` reports each field's status without ever displaying the password, and without
querying the OS keyring on a plain `show` (to avoid triggering an unexpected Keychain/Secret-Service
unlock prompt on a read-only command) — it only reports whether the profile is *configured*
to use keyring storage. `config clear` removes a profile (or `--all`, with confirmation
unless `--yes`) and its OS keyring entry if it has one; clearing a nonexistent profile warns
instead of failing.

**OS Keyring Storage**: a profile's password is stored under a stable, documented
`service`/`username` pair — `synology-apm-cli:<profile>` / `<profile's APM account>` — which
lets a credential be pre-seeded directly (`keyring set synology-apm-cli:lab admin`) without
the interactive wizard; the config file still needs `password_storage = "keyring"` recorded
for the profile.

> **Warning:** if the OS keyring backend is unavailable (e.g. a headless Linux host with no
> Secret Service running), commands needing the password fail with a hint to use
> `APM_PASSWORD` instead.

---

### machine — Device Workload Management

Manages device backup Workloads (PC, Physical Server, VM, File Server).

Subcommands that support search mode (`get` / `version list` / `version get` / `version lock` / `version unlock`) by default search only protected Workloads; adding `--retired` searches retired Workloads instead. In direct mode (`--id`/`--workload-id` + `--namespace`), `--retired` has no effect.

`retire`: in search mode, when the name is not found among protected workloads, the CLI
probes retired workloads; if the workload is already retired, the error is `Workload
'<name>' is already retired.` (exit 1) instead of a not-found error.

`change-plan`: which plan type `--plan` resolves against follows the [Plan
resolution](#development-conventions) rule (based on the workload's current state); in search
mode, add `--retired` to look up an already-retired Workload by name.

`version get`: `--id` (Version ID) is optional; the latest version is fetched automatically
if omitted (reported on stderr as `(Using version: <id>, created at <time>)`). Its detail
view reuses the same `Activity Detail` body as `synology-apm-cli activity backup get` (see
[activity backup get](#synology-apm-cli-activity-backup-get)).

---

### saas — SaaS Tenant Overview

`synology-apm-cli saas list` lists all connected SaaS tenants (M365 + GWS).

---

### m365 — M365 Workload Management

Manages Microsoft 365 SaaS backup Workloads, divided into six subcommand groups by service
type — each behaves like the corresponding `machine` command (same search/direct modes,
confirmation flows, and version subcommands), with the differences below.

| Subcommand | Service Type | Search-mode `<NAME>` matches |
|--------|---------|---------|
| `exchange` | Mailbox (Exchange) | UPN |
| `onedrive` | OneDrive | UPN |
| `chat` | Teams Chat | UPN |
| `group` | Group Exchange | Group mailbox email |
| `sharepoint` | SharePoint Sites | Site name |
| `teams` | Teams Channels | Team name |

The Tenant ID (`-t`/`--tenant-id`) can be obtained via `synology-apm-cli saas list`; when
omitted, the first M365 tenant is used automatically (reported on stderr as `(Using tenant:
<tenant-id>)`) — not required in direct mode. Only `exchange` and `group` support `export`
(mailbox PST export; see the next section).

Differences from `machine`:
- M365 workloads have no verification concept (`--verify-status` doesn't exist; `get`/`version`
  detail views never show a Verification line).
- Confirmation summaries show the workload without a type label (`alice@contoso.com`, not
  `CORP-PC-001 (PC/Mac)`).
- Backups are triggered directly by the API with no Job ID returned; use `synology-apm-cli
  activity backup list` to check progress.
- `retire` requires at least one retirement plan already created in the APM UI.

---

### m365 exchange export / m365 group export — Mailbox PST Export

Applies to the `exchange` and `group` subcommand groups, which share one implementation. Differences:

| Item | exchange | group |
|------|---------|-------|
| Identifier | UPN | Group email |
| `--archive-mailbox` | Supported | Hidden and silently ignored (no archive-mailbox concept for a group mailbox) |

`download` starts a new export and waits for it to become downloadable (no `--id`), or
downloads an already-started one directly (`--id`):

1. Resolve the backup version (latest unless `--version-id`) → start the export.
2. If immediately downloadable, download it.
3. Otherwise: with `--no-wait`, print the Activity ID (or a hint to run `export list`) and
   suggest re-running with `--id` to download (exit 0). Without `--no-wait`, poll until
   downloadable; on Ctrl+C, ask whether to cancel the server-side task, then **exit 4 either
   way**. If the export reaches a non-downloadable terminal state (FAILED / CANCELED /
   EXPIRED / DOWNLOADED), print the status and exit 1.
4. Stream the file with a progress bar (stderr); if the local destination file already
   exists, prompt to overwrite (declined → exit 4; `--yes` skips).

Local filenames are auto-generated when `--filename`/`-f` is omitted (auto-start:
`{name}_{date}_{mailbox|archive_mailbox|group_mailbox}.pst`; direct download:
`{name}_{first-8-chars-of-activity-id}.pst`); unsafe filesystem characters are replaced with `_`.

---

### plan protection / retirement / tiering — Plan Management

Manages Protection Plans, Retirement Plans, and Tiering Plans. `plan protection get` and
`plan retirement get`/`plan tiering get` support the standard [Search / Direct
mode](#search--direct-mode) (`plan protection get` searches across both `machine`/`m365`
categories regardless of `--category`).

`plan retirement` is the source for the `--plan` parameter of `machine`/`m365 <scope>
retire` and `change-plan` (on an already-retired Workload); `plan tiering` is the source for
`infra server change-plan`. Every plan ID needed for those commands can be listed with
`--verbose` here.

`plan protection get`'s detail view is a section-based text block (Backup Copy Policy /
Backup Policy / Backup Window / Custom Scopes & Schedules); the exact formatting rules for
retention text, the weekly Backup Window grid, and per-task workload-type/OS/scope labels
are defined next to the rendering code in `plan.py` / `_display.py` — read those functions
rather than a hand-transcribed copy here.

---

### activity — Activity Log Queries

Queries backup/restore activity records. `backup list` / `restore list` by default show only in-progress tasks (Ongoing); adding `--history` switches to showing completed historical records; if there are no in-progress tasks and `--history` is not given, a hint is printed instead of an empty table.

#### `synology-apm-cli activity backup get`

```bash
synology-apm-cli activity backup get WORKLOAD_NAME       # Search mode (gets the latest entry by workload name)
synology-apm-cli activity backup get --id ACTIVITY_ID    # Direct mode (gets directly by Activity ID)
```

This detail body (`Activity Detail — <workload name>` header) is reused as the Activity
Detail section of `machine version get` / `m365 <scope> version get`. Its field set (Status
/ Workload / Plan / Backup Scope / Start / End / Duration / Data Change / Transferred /
Actual Capacity Used / Processed items / Logs) and each field's conditional-visibility rule
are defined in `activity.py` / `_display.py` — the fields that only appear for certain
workload categories (e.g. `Backup Scope` for machine workloads, `Processed items` for
FS/M365) are exactly the kind of detail worth reading there rather than duplicating here.

#### `synology-apm-cli activity backup cancel` / `activity restore cancel`

`--yes` skips the confirmation prompt; exit code 1 if the activity is not found (or already
completed); exit code 4 if the user declines.

#### `synology-apm-cli activity restore get`

Same search/direct dispatch as `activity backup get`. Its detail body additionally shows
Restore Type / Version / Restore from / Destination / Destination path (file-level restores)
/ Destination hypervisor (VM restores) / Operator — the latter two never appear on the same
activity.

---

### infra — Infrastructure Information

Manages basic APM Management Server information, the backup server cluster, and remote storage devices.

`infra info` shows site identity, Management Center/Recovery Portal URLs, Primary/Secondary
Management Server health, site-wide storage statistics, and per-workload-type usage;
`primary_management_server.system_version` shows `Updating...` while an update is in
progress.

`infra server change-plan` applies or removes a Tiering Plan on a (DP-only) backup server;
exactly one of `--plan` or `--remove` is required. Follows the standard [action confirmation
flow](#action-confirmation-flow); `--remove` prints an extra warning paragraph about tiering
being stopped (ongoing operations continue; immutable-workload lock durations are adjusted).

`infra storage list` / `infra hypervisor list` return all results in one API call — no
`--limit`/`--offset`/`--page-all`.

---

### `synology-apm-cli log` — Backup Server Logs

Queries the system logs of a specified backup server; `<SERVER>` (search) or `--id` (direct)
selects it, obtained via `synology-apm-cli infra server list --verbose`.

> **Warning:** only DP (ActiveProtect) backup servers are supported — specifying a NAS server exits 1.

All four `log * list` commands share `--level` / `--since` / `--until` / `--search` plus
standard pagination; `log activity list` additionally has `--type`, and `log drive list` has
`--location`. Column sets and the Level/Type → display-string mappings are defined in
`log.py` / `_display.py`.

---

## Status and Color Conventions

### Status Icons

| Status | Icon | Color | Description |
|------|------|------|------|
| **Workload Backup Result** | | | |
| Success | `✓` | Green | The most recent backup succeeded |
| Failed | `✗` | Red | The most recent backup failed |
| Partial | `⚠` | Yellow | The most recent backup partially succeeded (including M365 WARNING) |
| Canceled | `⊘` | Dim | The most recent backup was canceled |
| No Backups | `—` | Dim | No backup has ever completed (first backup not yet run) |
| Retired | `—` | Dim | Workload is under a Retirement Plan; new backups will no longer be created |
| Waiting for Backup | `⠸` (spinner) | Blue | Backup is queued, waiting to run |
| Backing up (n%) | `⠸` (spinner) | Blue | PC/PS/VM backup in progress (block level) |
| Backing up (n items) | `⠸` (spinner) | Blue | FS/M365 backup in progress (file/item level; n = success + warning + error) |
| Deleting | `⟳` | Dim | Workload deletion is in progress (transient) |
| **Activity Status** (backup/restore activities, in addition to the result icons above) | | | |
| Canceling | `⊗` | Yellow | An in-progress backup/restore activity is being canceled |
| **Version Status** (in addition to Success / Failed / Partial / Canceled above) | | | |
| Paused | `‖` | Dim | The backup producing this version is paused |
| Delete Failed | — | Red | Deletion of this version failed |
| Deleting | — | Dim | This version is being deleted (no icon at version level) |
| **Backup Server Status** | | | |
| Healthy | `●` | Green | `NORMAL`: operating normally |
| Warning | `⚠` | Yellow | `ATTENTION`: there is a warning that needs attention |
| Critical | `✗` | Red | `DANGER`: a serious issue |
| Syncing... | `⟳` | Cyan | `spec.syncStatus=JOINING`: joining the cluster |
| Disconnected | `○` | Dim | `DISCONNECTED` / `JOINING_DISCONNECTED` / `NOTINITIALIZED` / `INCOMPATIBLE` |
| **Remote Storage Status** | | | |
| Connected | `●` | Green | `Connection`: connection normal |
| Authentication Failed | `✗` | Red | `AuthFailed`: authentication failed |
| Disconnected | `○` | Dim | `Disconnect`: disconnected |
| Unknown | `?` | Dim | `Unknown`: status unknown |
| Vault Not Mounted | `⚠` | Yellow | `VaultNotMounted`: vault not mounted |
| Vault Missing | `✗` | Red | `DataCorrupted`: vault data corrupted or missing |
| Unmanaged Catalog | `⚠` | Yellow | `SomeUnmanaged`: vault contains pre-existing catalogs not linked to any plan |
| **Backup Verification Status** | | | |
| ✓ Success | `✓` | Green | Backup verification succeeded |
| ✗ Failed | `✗` | Red | Backup verification failed |
| ⚠ Partial | `⚠` | Yellow | Backup verification partially succeeded |
| ⊘ Canceled | `⊘` | Dim | Backup verification was canceled |
| ⠸ Verifying | `⠸` | Blue | Backup verification in progress |
| ⠸ Waiting | `⠸` | Blue | Waiting for backup verification |
| Unable to perform | — | Dim | This workload does not support backup verification |
| Not enabled | — | Dim | Backup verification is not enabled |
| **Other** | | | |
| Success | `✓` | Green | Activity / Version completed successfully |
| Locked | `🔒` | Dim | The version is locked |

The concrete raw-status → icon/color mapping for each row above lives in `_display.py`'s
`*_DISPLAY` dicts — this table is the cross-cutting rendering *convention* new mappings must
follow (which icon/color means what across the whole CLI), not a duplicate of those dicts.

### Exit Codes

| Code | Description |
|------|------|
| `0` | Success |
| `1` | General error (API error, resource not found, etc.) |
| `2` | Authentication failed |
| `3` | Connection failed |
| `4` | User canceled the operation (Ctrl+C or answered N to a confirmation prompt) |
| `5` | Operation not supported (NotSupportedError) |

---

## Error Handling

### Error Message Format

Errors are printed to stderr; the exit code is returned to the shell (it is not printed):

```
✗ <Short description>
  <Detailed description or suggested action>     ← optional second line
```

### SDK Exception → CLI Error Mapping

| SDK Exception | Exit Code | Output |
|---------------|-----------|---------|
| `AuthenticationError` | 2 | `✗ Authentication failed: <message>` |
| `NotManagementServerError` | 3 | `✗ <message>` |
| `BackupServerDisconnectedError` | 3 | `✗ Unable to perform this operation because the designated backup server is disconnected` |
| `ConnectionTimeoutError` | 3 | `✗ Connection timed out` + detail line with the SDK message |
| `ResourceNotFoundError` | 1 | `✗ <ResourceType> not found: <resource-id>` (falls back to the raw message when the resource type is unknown) |
| `InvalidOperationError` / `ResourceNotReadyError` / `PlanNameConflictError` / `PlanInUseError` / `DuplicateWorkloadError` / `RemoteStorageConflictError` / `RemoteStorageEncryptionMismatchError` / `RemoteStorageInUseError` / `RemoteStorageUnmanagedCatalogError` | 1 | `✗ <message>` |
| `PermissionDeniedError` | 1 | `✗ Permission denied: <message>` |
| `NotSupportedError` | 5 | `✗ Not supported: <message>` |
| `APIError` (message indicates an SSL certificate verification failure) | 3 | `✗ SSL certificate verification failed` + hint suggesting `--no-verify-ssl` or skipping SSL verification in `config set` |
| `APIError` (message indicates a connection problem, e.g. contains "connect"/"connection") | 3 | `✗ <message>` |
| Any other `APMError` | 1 | `✗ API error: <message>` |

Additionally (non-`APMError` paths): a `ValueError` raised inside a command is printed as `✗ <message>` with exit 1; `KeyringUnavailableError` (OS keyring backend unavailable) is printed with exit 1; a declined confirmation prompt prints `Cancelled.` and exits 4.

### Hints for Common Usage Issues

When connection settings can't be resolved (`missing_config_hint()` in `errors.py`), the
hint names the profile that was checked, points at the interactive wizard and the relevant
environment variables, and — when other profiles are already configured — lists them as a
faster fix:

```
✗ Connection settings not configured for profile 'default'

  Configured profiles found: prod
  Select one with --profile <name> or APM_PROFILE=<name>, or configure this one:

  Run first (interactive wizard):
    synology-apm-cli config set

  Or set environment variables:
    export APM_HOST=apm.corp.com
    export APM_USERNAME=admin
    export APM_PASSWORD=...
    export APM_NO_VERIFY_SSL=true   # only needed for self-signed certificates
```

The "Configured profiles found" block and the `--profile <name>` flag on the `config set`
line only appear when relevant (no other profiles exist / a non-default profile was
requested); otherwise the message is the same, without that block.
