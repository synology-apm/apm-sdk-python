# APM CLI — Design Contract

> Corresponding product: Synology ActiveProtect Manager 1.2

**Purpose of this document**: A design contract document for CLI implementers (human developers or AI sessions).
It records the command structure, option specifications, output format conventions, and the CLI → SDK call mapping.

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
   - [plan protection — Protection Plan Management](#plan-protection--protection-plan-management)
   - [plan retirement — Retirement Plan Management](#plan-retirement--retirement-plan-management)
   - [plan tiering — Tiering Plan Management](#plan-tiering--tiering-plan-management)
   - [activity — Activity Log Queries](#activity--activity-log-queries)
   - [infra — Infrastructure Information](#infra--infrastructure-information)
   - [synology-apm log — Backup Server Logs](#synology-apm-log--backup-server-logs)
- [Status and Color Conventions](#status-and-color-conventions)
- [Error Handling](#error-handling)
- [CLI → SDK Mapping Table](#cli--sdk-mapping-table)

---

## Purpose and Design Principles

### Purpose

`synology-apm` is the official CLI front-end for the APM Python SDK, providing a complete experience for end users to operate APM from the terminal.
The CLI depends solely on the SDK and does not call the REST API directly.

```
End user        →  apm CLI (synology_apm.cli)  →  apm SDK (synology_apm.sdk)  →  APM REST API
Program integration →                    apm SDK (synology_apm.sdk)  →  APM REST API
```

### Design Principles

- **Domain-oriented command structure**: `synology-apm machine` manages device backups, `synology-apm saas` shows the SaaS tenant overview, `synology-apm m365` manages M365 backups — aligned with the SDK's `apm.machine` / `apm.m365` object model
- **Type filter**: `synology-apm machine list --type vm` / `synology-apm machine list --type vm --type fs` — the type is a repeatable `--type` option (values: `pc`, `ps`, `vm`, `fs`); omitting it defaults to all types
- **Resource-oriented**: when operating by Workload ID, the type prefix is omitted: `synology-apm machine get <ID>`, `synology-apm machine backup <ID>`
- **Progressive disclosure**: the most commonly used fields are shown by default; `--verbose` shows full information
- **Scriptable**: `--output json` outputs JSON for use with jq; the exit code reflects success/failure
- **Friendly terminal UX**: Rich colorized output, progress bars, clear error messages
- **Consistency**: options with the same semantics keep the same name across all commands (`--output`, `--since`)

### Development Conventions

When adding or modifying commands, follow the conventions of existing commands (actual signatures and details are in each module's docstrings and existing command examples):

- **Error handling / session setup**: SDK calls run inside `async with apm_session(ctx) as apm:` (`synology_apm.cli._helpers`), which stacks `apm_error_handler()` → optional `api_spinner` → `get_client()` in one context manager. List commands pass `spinner="Fetching ..."`; destructive commands pass `abortable=True` so a declined confirmation exits cleanly with `EXIT_CANCEL`. Do not hand-nest `apm_error_handler()` / `api_spinner()` / `get_client()` in commands.
- **Output dispatch**: non-table output is dispatched via `dispatch_list_output()` / `dispatch_output()` (`synology_apm.cli.output`); returning `True` ends early; `to_csv_row` fields must align with the table columns (not the JSON fields).
- **Workload resolution and argument validation**: when the search (`<NAME>`) and direct (`--id` / `--namespace`) modes are mutually exclusive, use `validate_*()` from `synology_apm.cli._validate` along with `WorkloadRef.resolve_machine()` / `.resolve_m365()`; once the M365 tenant is automatically resolved, report it via `print_resolved_tenant()`. Similarly, when `--id`/`--version-id` is omitted and `get_latest_version()` is used to resolve the version, report it via `print_resolved_version()`.
- **Plan resolution (`--plan`)**: any `--plan <id or name>` option is resolved via the shared `_resolve_plan(apm, plan_arg, is_retired=...)` helper in `synology_apm.cli._validate` — UUID-shaped values are looked up with `.get()`, otherwise with `.get_by_name()`, dispatched to `RetirementPlanCollection` or `ProtectionPlanCollection` based on the `is_retired` flag (always `True` for `retire`, since retiring a workload always assigns a Retirement Plan regardless of the workload's state; the resolved workload's `is_retired` for `change-plan`; the command's `--retired` flag for the list filter). The repeatable `--plan` filter on `machine list` / `m365 <scope> list` resolves every value through `_resolve_plans(apm, plan_args, is_retired=...)`, which maps `_resolve_plan()` over the list (returning `None` when no `--plan` was given). **Exception**: `infra server change-plan --plan` resolves against Tiering Plans via the separate `_resolve_tiering_plan(apm, plan_arg)` helper (same UUID-or-name dispatch, targeting `TieringPlanCollection`), not the shared `_resolve_plan()`.
- **Stderr**: always use `err_console` (`synology_apm.cli.errors`); do not create a separate `Console(stderr=True)`.
- **Missing required arguments**: declare as `Optional` at the Typer layer; the function checks for `None` internally and prints `ctx.get_help()`, then exits with 0.
- **Shared option constants**: the recurring pagination (`--limit`/`--offset`/`--page-all`), output (`--output`), and time-filter (`--since`/`--until`) options are declared once in `synology_apm.cli._options` and referenced as parameter defaults (e.g. `limit: int = LIMIT_OPTION`); `--since`/`--until` values are parsed with `parse_time_range(since, until)` from `synology_apm.cli._validate`. Only declare an option inline when its default or help text genuinely differs.
- **Destructive operations** (`retire`, overwrite-style `change-plan`): require interactive confirmation unless `--yes` is given; the summary message is always printed.
- **Serialization**: before output, resource objects are converted to dicts via public functions in `_serializers.py` (e.g. `workload_to_dict`, `server_to_dict`, `protection_plan_to_dict`); command modules import and call these. This rule has no exemptions: command modules must not define their own `*_to_dict` / `*_to_csv_row` helpers (auditable via `grep -r "def .*_to_dict" commands/` — expected empty). Do not call `print_json()` or `dataclasses.asdict()` directly on an SDK model.
- **Enum display text**: all enum → display-string mapping tables live in `_display.py` (e.g. `_SERVER_STATUS_DISPLAY`, `_FILE_SERVER_TYPE_DISPLAY`, `_RESTORE_TYPE_DISPLAY`); command modules import and use them — they do not define their own. Each table is accessed through a public `fmt_*` wrapper (e.g. `fmt_server_status`, `fmt_export_status`) that owns the fallback for unmapped values, and the wrapper is what commands call and unit tests exercise. SDK enums contain only semantic values, so adding or adjusting display text requires changes in `_display.py` only (the concrete display strings — e.g. the FS protocol sub-labels and the `plan protection get` task-table labels — are defined there and in the per-command specs below). Display maps must always contain final display strings — no intermediate empty-string sentinels requiring call-site post-processing.
- **Datetime precision**: table/text output shows local time at second precision (`YYYY-MM-DD HH:MM:SS` via `fmt_datetime()`); JSON/CSV output uses local-timezone ISO 8601 (via `fmt_datetime_iso()` / `to_local_iso()`). **Exception**: schedule time-of-day fields (`schedule.start_time`, `daily_check_time`) are always `HH:MM` (no seconds, no timezone) in every output format, since APM schedules only support minute granularity.
- **External non-SDK dependencies** (e.g. the OS keyring): wrap calls with a narrow `try/except` and a dedicated CLI-defined exception type (e.g. `KeyringUnavailableError`), not `apm_error_handler()` — that helper converts `APMError` to structured messages and also converts `ValueError` to a plain `EXIT_ERROR` message; it re-raises everything else.
- **Shared backup/cancel/retire/change-plan action bodies**: `machine` and `m365` implement the same four destructive/state-changing commands (`backup`, `cancel`, `retire`, `change-plan`) with identical resolve → confirm → invoke → print-success flow. The domain-agnostic body of each lives once in `commands/_actions.py` (`_do_backup` / `_do_cancel` / `_do_retire` / `_do_change_plan`); each command module passes in closures for workload resolution and a `label_fn` callable (`_machine_type_label` for `machine`, `lambda wl: None` for `m365`, since M365 workloads have no type label in this output) to absorb the only real per-domain differences.

### Technology Choices

| Package | Purpose |
|------|------|
| **Typer** | CLI framework; type hints auto-generate parameters and `--help` |
| **Rich** | Colorized tables, progress bars, tree structures, status icons |
| **synology-apm** | The sole dependency for APM operations; does not call the REST API directly |

---

## Package Structure

```
synology_apm/cli/
├── __init__.py
├── main.py              # Typer app root entry point; registers all sub-apps
├── config.py            # Config file read/write (~/.config/synology-apm/config.toml)
├── output.py            # Formatted output (table/json/yaml/csv); shared console instance
├── errors.py            # SDK Exception → CLI error message mapping; err_console
├── _async.py            # asyncio.run() wrapper (Typer has no native async support)
├── _helpers.py          # get_client(), apm_session(), api_spinner, enable_debug / is_debug
├── _options.py          # Shared typer.Option constants: LIMIT/VERSION_LIMIT, OFFSET, PAGE_ALL, LIST_OUTPUT/OUTPUT, SINCE/UNTIL
├── _display.py          # All enum → display-string constants (e.g. _SERVER_STATUS_DISPLAY, _RESTORE_TYPE_DISPLAY) and formatting functions (fmt_bytes / datetime / duration / workload status / activity status); BACKUP_SCOPE_LABELS; print_list_footer / render_log_table / print_version_detail / print_workload_detail / render_version_table
├── _serializers.py      # All model → dict serializers (`*_to_dict` / `*_to_csv_row`) for every resource the CLI outputs: workloads (machine + M365), servers, plans (protection/retirement/tiering), versions, activities, M365 exports, site info, hypervisors, remote storages, tenants, log entries
├── _validate.py         # validate_resolve_args, validate_version_workload_args, validate_version_lock_args, validate_activity_args, validate_name_or_id_args, parse_time_filter / parse_time_range, require_or_help, _resolve_tenant, _resolve_plan, _resolve_plans, _resolve_tiering_plan; WorkloadRef.resolve_machine() / .resolve_m365() (get/get_by_name dispatch, automatic tenant_id resolution)
└── commands/
    ├── __init__.py
    ├── _actions.py      # Shared backup/cancel/retire/change-plan resolve-confirm-invoke-print bodies; consumed by machine.py and m365.py
    ├── config.py        # synology-apm config ...
    ├── machine.py       # synology-apm machine ... (device workloads)
    ├── saas.py          # synology-apm saas ... (SaaS tenant overview)
    ├── m365.py          # synology-apm m365 ... (M365 workloads)
    ├── m365_export.py   # Shared M365 export infrastructure (_TENANT_ID_OPTION, _make_export_app, etc.); consumed by m365.py
    ├── plan.py          # synology-apm plan protection / synology-apm plan retirement / synology-apm plan tiering
    ├── activity.py      # synology-apm activity ...
    ├── infra.py         # synology-apm infra info / synology-apm infra server ... / synology-apm infra storage ... / synology-apm infra hypervisor ...
    └── log.py           # synology-apm log activity|drive|connection|system list
```

---

## Authentication Configuration

### Priority Order (high → low)

```
1. Command-line options (--host, --username, --password)
2. Environment variables (APM_HOST, APM_USERNAME, APM_PASSWORD)
3. Configuration file (~/.config/synology-apm/config.toml) — a profile's password may itself be
   stored in plaintext in this file, or looked up from the OS keyring; see "OS Keyring Storage"
   under config — Configuration Management for the keyring-specific details.
```

### Configuration File Format

```toml
# ~/.config/synology-apm/config.toml

[default]
host     = "apm.corp.com"
username = "admin"
# password = "secret"            # Optional, stored in plaintext; only written when
                                  # --save-password plaintext is given
# password_storage = "plaintext" # Recorded alongside `password` once a password is saved

[lab]
host     = "apm2.corp.com:10443"
username = "admin"
password_storage = "keyring"     # Password stored via the OS keyring (Keychain / Credential
                                  # Manager / Secret Service), not in this file — set via
                                  # synology-apm config set --save-password keyring --profile lab

[prod]
host     = "apm.corp.com"
username = "svc-apm"
no_verify_ssl = false
```

> **Warning:** `password_storage = "plaintext"` stores the password in plaintext in this file. Recommended only for trusted local environments; prefer `password_storage = "keyring"` (OS-native credential store) or the `APM_PASSWORD` environment variable instead.

### Environment Variables

```bash
APM_HOST=apm.corp.com
APM_USERNAME=admin
APM_PASSWORD=secret
APM_PROFILE=lab          # Select a profile in config.toml
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

The following options are **per-command**, not global — they are declared only on the commands that support them, but keep the same name, short flag, and semantics everywhere they appear:

| Option | Short | Where | Description |
|------|--------|------|------|
| `--output` | `-o` | list / get / info commands | Output format: `table` (default), `json`, `yaml`; list commands also support `csv` |
| `--verbose` | `-v` | list commands (and select others) | Show additional columns / fields |
| `--quiet` | `-q` | action commands (backup / cancel / retire / change-plan / lock / unlock / ...) | Suppress success messages (suitable for scripts) |
| `--yes` | `-y` | destructive commands | Skip the confirmation prompt (summaries are still printed) |

---

## Command Overview

```
synology-apm
├── config
│   ├── set       Configure connection information
│   ├── show      Show current configuration
│   └── clear     Clear configuration
│
├── machine                                 # Device Workloads (PC / Physical Server / VM / File Server)
│   ├── list      [--type pc|ps|vm|fs]  List device Workloads (no --type = all types)
│   ├── get       <NAME>            View details of a specific Workload (search mode, defaults to protected; --retired searches retired)
│   │     or      --id <ID> --namespace <NS>  (direct mode)
│   ├── backup    <NAME or --id+--namespace>   Trigger a manual backup
│   ├── cancel    <NAME or --id+--namespace>   Cancel an in-progress backup
│   ├── retire    <NAME or --id+--namespace> --plan <PLAN>   Retire a Workload (irreversible)
│   ├── change-plan  <NAME or --id+--namespace> --plan <PLAN>   Change the Protection Plan (active Workload) or Retirement Plan (retired Workload) assigned to a Workload
│   └── version
│       ├── list    <NAME or --id+--namespace>   List backup version history (Status, Changed Size, Copy Status, Locations, Version ID)
│       ├── get     <NAME> [--id <VERSION_ID>]    Show version info (Version ID / Workload ID / Namespace / Locations / Copy Status) and activity detail (omit --id to get the latest version)
│       │     or    --workload-id <WL_ID> --namespace <NS> [--id <VERSION_ID>]
│       ├── lock    <NAME or --workload-id+--namespace> --id <VERSION_ID>   Lock a version (prevent deletion by retention rules)
│       └── unlock  <NAME or --workload-id+--namespace> --id <VERSION_ID>   Unlock a version
│
├── saas                                    # SaaS Tenant Overview (M365 / GWS)
│   └── list                                # List all SaaS providers / tenants
│
├── m365                                    # Microsoft 365 Workload Management (grouped by service type)
│   ├── exchange    # Mailbox (Exchange mailboxes)
│   │   ├── list     [-t <TID>]   List M365 Workloads of this type (omit -t to automatically use the first M365 tenant)
│   │   ├── get      <NAME> [-t <TID>]              (search mode, defaults to protected; --retired searches retired)
│   │   │     or     --id <UID> --namespace <NS>         (direct mode, -t not required)
│   │   ├── backup   <NAME or --id+--namespace> [-t <TID>]
│   │   ├── cancel   <NAME or --id+--namespace> [-t <TID>]
│   │   ├── retire   <NAME or --id+--namespace> [-t <TID>] --plan <PLAN>
│   │   ├── change-plan  <NAME or --id+--namespace> [-t <TID>] --plan <PLAN>   Change the Protection Plan (active) or Retirement Plan (retired)
│   │   ├── version
│   │   │   ├── list    <NAME or --id+--namespace> [-t <TID>]   List backup versions (Status, Changed Size, Copy Status, Locations, Version ID)
│   │   │   ├── get     <NAME or --workload-id+--namespace> [--id <VERSION_ID>] [-t <TID>]   Version info + activity detail (omit --id = latest)
│   │   │   ├── lock    <NAME or --workload-id+--namespace> --id <VERSION_ID> [-t <TID>]   Lock a version
│   │   │   └── unlock  <NAME or --workload-id+--namespace> --id <VERSION_ID> [-t <TID>]   Unlock a version
│   │   └── export      # Mailbox PST export
│   │       ├── list      <NAME or --workload-id+--namespace>   List export tasks for a Workload
│   │       ├── cancel    <NAME or --workload-id+--namespace> --id <ACTIVITY_ID>   Cancel an in-progress export
│   │       └── download  <NAME or --workload-id+--namespace> [--id <ACTIVITY_ID>]   Start a new export and download it (no --id), or download an existing one (--id)
│   ├── onedrive    # OneDrive (personal cloud) — same structure (list/get/backup/cancel/retire/change-plan + version); no export
│   ├── chat        # Teams Chat — same structure; no export
│   ├── group       # Group Exchange (group mailboxes) — same structure, including export (group mailbox PST)
│   ├── sharepoint  # SharePoint Sites — same structure; no export
│   └── teams       # Teams Channels — same structure; no export
│
├── plan                                    # Protection, Retirement, and Tiering Plan Management
│   ├── protection                          # Backup protection plans
│   │   ├── list   [--category machine|m365]   List plans (single API call; no --category = all)
│   │   └── get    <NAME> or --id <PLAN_ID>    View plan details (search mode is cross-category)
│   ├── retirement                          # Retirement plans
│   │   ├── list                            List all retirement plans (-v shows Plan ID)
│   │   └── get    <NAME> or --id <PLAN_ID>    View a specific retirement plan
│   └── tiering                             # Tiering plans (applied to backup servers)
│       ├── list                            List all tiering plans (-v shows Plan ID)
│       └── get    <NAME> or --id <PLAN_ID>    View a specific tiering plan
│
├── activity
│   ├── backup
│   │   ├── list      List backup activity records (--status / --search / --since / --history / --limit)
│   │   ├── get       <WORKLOAD_NAME> or --id <ACTIVITY_ID>   View backup activity details and logs
│   │   └── cancel    --id <ACTIVITY_ID>   Cancel an in-progress backup activity
│   └── restore
│       ├── list      List restore activity records (--status / --search / --since / --history / --limit)
│       ├── get       <WORKLOAD_NAME> or --id <ACTIVITY_ID>   View restore activity details
│       └── cancel    --id <ACTIVITY_ID>   Cancel an in-progress restore activity
│
├── infra                                   # Infrastructure Information
│   ├── info      Show Management Server information + cluster storage statistics
│   ├── server
│   │   ├── list         List all backup servers
│   │   ├── get          <NAME> or --id <SERVER_ID>   View details of a specific backup server
│   │   └── change-plan  <NAME or --id> --plan <PLAN>   Apply a Tiering Plan to a backup server
│   │                    <NAME or --id> --remove         Remove the current Tiering Plan
│   ├── storage
│   │   ├── list      List all remote storage devices
│   │   └── get       <NAME> or --id <STORAGE_ID>   View details of a specific remote storage device
│   └── hypervisor
│       ├── list      List all Hypervisor Inventory servers
│       └── get       <NAME> or --id <HV_ID>   View details of a specific Hypervisor (search by hostname or address)
│
└── log                                     # Backup server logs (DP servers only)
    ├── activity   list  [<SERVER> | --id <SERVER_ID>]   Activity logs (--type protection|system|data_access)
    ├── drive      list  [<SERVER> | --id <SERVER_ID>]   Drive information logs
    ├── connection list  [<SERVER> | --id <SERVER_ID>]   Connection logs
    └── system     list  [<SERVER> | --id <SERVER_ID>]   Advanced system logs
```

---

## Output Formats

This section is the canonical example library: each interaction pattern (list table, get detail block, action confirmation, irreversible warning) is rendered **once** here, using `machine` commands as the subject. Command sections in [Detailed Command Specifications](#detailed-command-specifications) do not repeat rendered output — they only note how their output differs from these patterns.

### Table (default)

Rendered with Rich, with status colors and icons. Canonical list example (`synology-apm machine list` default columns):

```
$ synology-apm machine list

 Name            Type             Status         Verification  Last Backup          Protected Size  Copy Size  Protection Plan  Backup Server  Copy Destination
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
 CORP-PC-001     PC/Mac           ✓ Success      -             2026-04-21 09:23:00  145.3 GB        -          Daily Backup     apm-server-01  -
 prod-server-01  Physical Server  ✓ Success      ✓ Success     2026-04-21 09:15:00  80.2 GB         12.4 GB    Daily Backup     apm-server-01  DSM-Storage (MyVault)
 vm-web-01       Virtual Machine  — No Backups   -             -                    0 B             -          Daily Backup     apm-server-01  -
 old-laptop      PC/Mac           ✗ Failed       -             2026-03-10 14:00:00  22.1 GB         -          Daily Backup     apm-server-01  -
Showing 4 of 4
```

> **Note (pagination summary):** table list output is followed by `Showing N of M` (number of results / total matching count). When `--offset` is used, it shows `Showing X–Y of M`. When the endpoint does not report a total count, only `Showing N` is printed. Activity list (`synology-apm activity backup list` / `synology-apm activity restore list`) similarly shows `Showing N of M`, where M is the total returned by the endpoint for the selected mode (Ongoing or History).

### JSON (`--output json`)

Outputs a curated set of fields (nested structure), suitable for jq / script processing. datetime fields are output in local-timezone ISO 8601 (e.g. `2026-05-16T16:54:20+08:00`):

```bash
$ synology-apm machine list --retired --output json | jq '.[].name'
```

```json
[
  {
    "workload_id": "123e4567-e89b-12d3-a456-426614174000",
    "name": "vm-web-01",
    "category": "machine",
    "workload_type": "virtual_machine",
    "namespace": "123e4567-e89b-12d3-a456-426614174001",
    "is_retired": true,
    "status": "retired",
    "plan_name": null,
    "plan_id": null,
    "last_backup_at": null,
    "protected_data_bytes": 0,
    "backup_copy_data_bytes": null,
    "backup_server": {
      "is_remote_storage": false,
      "identifier": "123e4567-e89b-12d3-a456-426614174001",
      "name": "apm-server-01",
      "endpoint": "192.0.2.1",
      "vault": null
    },
    "backup_copy_destination": null,
    "verify_status": null,
    "agent_version": null,
    "device_uuid": "123e4567-e89b-12d3-a456-426614174006",
    "ip_address": null,
    "inventory_name": "esxi1.example.com",
    "inventory_type": "ESXi"
  }
]
```

### YAML (`--output yaml`)

```bash
$ synology-apm machine get "CORP-PC-001" --output yaml
```

### CSV (`--output csv`)

**Supported only by list commands** (get commands do not offer this option). Outputs a flattened set of fields, suitable for importing into spreadsheets (Excel / Google Sheets) or pipeline processing:

```bash
$ synology-apm machine list --output csv > machines.csv
$ synology-apm activity backup list --since 24h --output csv
```

Field policy:
- The field set aligns with table mode (not the full set of dataclass fields)
- Values are machine-readable raw values (datetime → local-timezone ISO 8601, e.g. `2026-05-16T16:54:20+08:00`; bytes → integer; enum → semantic string)
- Nested objects are flattened into separate fields (e.g. `backup_server_name`, `retention_type`)
- Empty values are output as an empty string

### Auto-pagination (`--page-all`)

All list commands that support `--limit` / `--offset` provide a `--page-all` flag: starting from `--offset`, using `--limit`
as the page size, it automatically fetches page by page until all data is retrieved (with a fixed internal delay between fetches).

The combined behavior of `--page-all` and `--output`:

| `--page-all` + `--output` | Actual output |
| --- | --- |
| `--page-all --output table` (default) | First fetches all pages, then renders a **single** merged table and footer, in the same format as a single-page output without `--page-all` |
| `--page-all --output json` | NDJSON: each record is output as one line of compact JSON, streamed page by page |
| `--page-all --output csv` | The first page outputs the header + data rows; subsequent pages output only data rows (same field order) |
| `--page-all --output yaml` | Each page is prefixed with `---`, forming a YAML multi-document stream |

`synology-apm infra storage list` and `synology-apm infra hypervisor list` do not support `--limit` / `--offset` (the API returns all data in one call), so `--page-all` is not provided.

### Search / Direct mode

Resource-addressed commands accept two mutually exclusive addressing modes; combining them is an argument error, and omitting both prints the command help:

- **Search mode** — positional `<NAME>`: keyword search, then case-insensitive exact match. Machine workloads match on name; M365 workloads match on display name / UPN / group email and are scoped by `-t/--tenant-id` (auto-resolves to the first M365 tenant when omitted, reported on stderr as `(Using tenant: <id>)`). Plan / server / storage / hypervisor commands match on name (or endpoint/address where noted per command).
- **Direct mode** — `--id <ID>`: direct ID lookup. Workload commands additionally require `--namespace <NS>`; `version` and `export` subcommands use `--workload-id` instead, because `--id` there addresses the version/activity.

Per-command specs below show the two usage lines and note any deviations (optional `--id`, `--retired`, plan options).

### Get detail block

Canonical example (`synology-apm machine get`, table mode). Workload get commands (`machine get` / `m365 <scope> get`) share this two-block layout; plan / infra get commands use a similar `Header: <name>` + `─` rule + `Label: value` section layout whose fields are listed per command.

```
$ synology-apm machine get "CORP-PC-001"

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

Canonical example (`synology-apm machine change-plan`). The plan and workload summary is always printed (to stderr) even with `--yes`; `--yes` skips only the prompt. A declined confirmation prints `Cancelled.` and exits 4. Note the ASCII `->` arrow in the `Current plan:` line.

```
$ synology-apm machine change-plan "CORP-PC-001" --plan "Daily Backup"

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
> - The `Schedule:` line is omitted when the protection plan has no schedule.
> - The workload type label in parentheses is machine-only (`PC/Mac` / `Physical Server` / `Virtual Machine` / `File Server`, with FS protocol sub-label); M365 workloads show `<name> (ID: <workload-id>)` without a type label.
> - Simple cancel confirmations (`machine cancel` / `m365 <scope> cancel`) use a shorter variant: header `⚠ Confirm cancel backup?`, a blank line, `  Workload:  <name> (<type label>)` (no type label for M365), a blank line, prompt `  Confirm? [y/N]:`, success line `✓ Backup cancelled: <name>`.

### Irreversible-warning flow

Canonical example (`synology-apm machine retire`). Even with `--yes`, the warning summary is still printed (this action is irreversible — the summary must be reviewable):

```
$ synology-apm machine retire "old-laptop" --plan "Compliance Retention"

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

> For the complete option list and brief descriptions of each command, run `synology-apm <command> --help`; this section only records what `--help` cannot show: Search/Direct mode determination logic, output field definitions, and conditional display rules. Rendered example output lives in [Output Formats](#output-formats) — each command below only notes how its output differs from those canonical patterns.

### config — Configuration Management

#### `synology-apm config set`

An interactive wizard that asks for all connection settings step by step. `--host` / `--username` can be pre-filled to skip the corresponding prompts; `--save-password {plaintext|keyring}` forces a password prompt (with confirmation) and saves it; `--profile` selects the profile (default `default`).

```bash
synology-apm config set [--host HOST] [--username USERNAME] [--save-password {plaintext|keyring}] [--profile PROFILE]
```

**Interactive flow (`synology-apm config set`):**

```
APM host (e.g. apm.corp.com or apm.corp.com:10443): apm.corp.com
Username: admin
Password (leave blank to prompt each time, not saved):
Skip SSL verification? (choose y for self-signed certificates) [y/N]: y

✓ Settings saved to ~/.config/synology-apm/config.toml (profile: default)
```

> - When the profile already has a saved password, the password prompt hint changes to `Password (leave blank to keep the saved password)` (plaintext) or `Password (leave blank to keep the password stored in the OS keyring)` (keyring); leaving it blank keeps the stored password.
> - **With `--save-password`**, the password is always prompted and confirmed (`Password:` + `Repeat for confirmation:`; cannot be left blank). After the save-confirmation line, `plaintext` additionally prints `⚠ Password is stored in plaintext. Secure the file permissions (chmod 600).`; `keyring` prints `✓ Password stored in the OS keyring.`.
> - With `--no-input`, `--host` / `--username` become required (error + exit 1 when missing), `--save-password` is rejected (it requires interactive input), the password is left unsaved, and the SSL setting keeps its existing value.

##### OS Keyring Storage

A profile's password is stored under a stable, documented keyring `service`/`username` pair:

```
service  = synology-apm-cli:<profile>
username = <profile's APM login account>
```

This lets a power user pre-seed a credential directly with the `keyring` CLI tool (e.g. for scripted setup), without going through the interactive wizard:

```bash
keyring set synology-apm-cli:lab admin
```

After pre-seeding the keyring entry, the config file still needs `password_storage = "keyring"` recorded for the profile — either via `synology-apm config set --save-password keyring` or by hand-editing `config.toml`.

> **Note:** `synology-apm config show` never queries the OS keyring — it only reports whether a profile is *configured* to use keyring storage, to avoid triggering a Keychain/Secret-Service unlock prompt on a read-only command.

> **Warning:** if the OS keyring backend is unavailable (e.g. a headless Linux host with no Secret Service / kwallet running), commands that need the password will fail with a clear error suggesting the `APM_PASSWORD` environment variable as a fallback.

#### `synology-apm config show`

```bash
synology-apm config show [--profile PROFILE]
```

Prints the profile's settings as `Label: value` lines: `Profile` / `Host` / `User` / `Password` / `SSL`. The `Password` line shows one of three states: `(not saved)`, `(saved, plaintext)` (in yellow), or `(stored in OS keyring)` (in green); the actual password is never displayed. The `SSL` line shows `verify` or `skip verify`. Without `--profile`, the default profile is shown, followed by `All profiles: <name>, <name>, ...` when more than one profile exists. Unset host/user values show `(not set)`.

#### `synology-apm config clear`

```bash
synology-apm config clear [--profile PROFILE]   # Clear the specified profile (default: `default`)
synology-apm config clear --all                 # Clear all profiles
```

Asks for confirmation unless `--yes` is given; also deletes the profile's OS keyring entry when it has one. Clearing a nonexistent profile prints a warning (`⚠ Profile '<name>' does not exist.`) instead of failing.

---

### machine — Device Workload Management

Manages device backup Workloads (PC, Physical Server, VM, File Server).
Corresponds to the SDK's `apm.machine.workloads` (`MachineWorkloadCollection`).

Subcommands that support search mode (`get` / `version list` / `version get` / `version lock` / `version unlock`) by default search only protected Workloads; adding `--retired` searches retired Workloads instead. In direct mode (`--id`/`--workload-id` + `--namespace`), `--retired` has no effect.

#### `synology-apm machine list`

`--type [pc|ps|vm|fs]` is a repeatable type filter (default: all types); other filters: `--retired`, `--search`, `--namespace`, `--hypervisor`, `--plan` (repeatable, name or ID).

**Default table columns** (see the canonical list example in [Output Formats](#output-formats)):

| Column | Description |
|------|------|
| Name | Workload name |
| Type | `PC/Mac` / `Physical Server` / `Virtual Machine` / `File Server` (protocol unknown) / `File Server / SMB` / `File Server / Synology NAS` / `File Server / Nutanix` / `File Server / NetApp` / `File Server / Unknown` |
| Status | Backup status icon and color, see [Status Icons](#status-icons) "Workload Backup Result"; **this column is not shown with `--retired`** |
| Verification | Backup verification result icon and color, see [Status Icons](#status-icons) "Backup Verification Status"; **shows `-` for PC/FS and when no data is available** |
| Last Backup | Last backup time (`-` if none) |
| Protected Size | Storage space occupied by protected data (human-readable, e.g. 145.3 GB) |
| Copy Size | Storage space occupied by Backup Copy (backup_copy_data_bytes); shows `-` if unset or 0 |
| Protection Plan | Name of the applied Plan (`-` if none) |
| Backup Server | Backup server display name (backup_server.name); shows `-` if unknown |
| Copy Destination | Backup Copy destination (backup_copy_destination); format: name or name (vault); shows `-` if unset |

**Additional `--verbose` columns:**

| Column | Description |
|------|------|
| IP Address | Device IP (only for PC/PS; VM/FS show `-`) |
| Workload ID | workload_id |
| Namespace | namespace (corresponds to the backup server identifier) |
| Plan ID | plan.plan_id |

---

#### `synology-apm machine get`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm machine get <NAME> [--retired]                     # Search mode (keyword search, then exact name match)
synology-apm machine get --id WORKLOAD_ID --namespace NAMESPACE  # Direct mode (requires both --id and --namespace)
```

Table output follows the canonical [get detail block](#get-detail-block). Type row: `Machine / <type label>` (same type labels as the list Type column). Conditional rows:

> - PC / PS: shows Device UUID, Agent, IP.
> - VM: shows Host (`inventoryName (inventoryType)`), Device UUID; no Agent / IP.
> - FS: none of the three are shown.
> - Verification line (in the Backup Status block, after Status): shown only when PS/VM and verify_status has a value (not shown for PC/FS).
> - Retired workload (`--retired`): the Backup Status block does not show the Status line.

---

#### `synology-apm machine backup`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm machine backup <NAME>                                  # Search mode
synology-apm machine backup --id WORKLOAD_ID --namespace NAMESPACE  # Direct mode
```

**Success output** (suppressed by `--quiet`):

```
✓ Backup triggered.
  Workload: CORP-PC-001

  Use `synology-apm activity backup list` to check progress.
```

---

#### `synology-apm machine cancel`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm machine cancel <NAME> [--yes] [--quiet]                                  # Search mode
synology-apm machine cancel --id WORKLOAD_ID --namespace NAMESPACE [--yes] [--quiet]  # Direct mode
```

Follows the short cancel-confirmation variant of the canonical [action confirmation flow](#action-confirmation-flow); success line: `✓ Backup cancelled: <name>`.

---

#### `synology-apm machine retire`

[Search / Direct mode](#search--direct-mode). `--plan` is required and accepts a Retirement Plan name or UUID; if the value does not match the UUID format, name search is performed automatically. The Plan ID/name can be obtained from `synology-apm plan retirement list --verbose`.

```bash
synology-apm machine retire <NAME> --plan PLAN_NAME_OR_ID [--yes] [--quiet]                                  # Search mode
synology-apm machine retire --id WORKLOAD_ID --namespace NAMESPACE --plan PLAN_NAME_OR_ID [--yes] [--quiet]  # Direct mode
```

Output follows the canonical [irreversible-warning flow](#irreversible-warning-flow) exactly (the warning summary is printed even with `--yes`).

> **Note:** in search mode, when the name is not found among protected workloads, the CLI probes retired workloads; if the workload is already retired, the error is `Workload '<name>' is already retired.` (exit 1) instead of a not-found error.

---

#### `synology-apm machine change-plan`

[Search / Direct mode](#search--direct-mode). `--plan` is required and accepts a Plan name or UUID; if the value does not match the UUID format, name search is performed automatically. The plan type `--plan` is resolved against is auto-detected from the workload's current state: a Protection Plan for an active Workload, a Retirement Plan for an already-retired one (add `--retired` in search mode to look up a retired Workload by name). The Plan ID can be obtained from `synology-apm plan protection list --verbose` / `synology-apm plan retirement list --verbose`.

```bash
synology-apm machine change-plan <NAME> --plan PLAN_NAME_OR_ID [--retired] [--yes] [--quiet]                       # Search mode
synology-apm machine change-plan --id WORKLOAD_ID --namespace NAMESPACE --plan PLAN_NAME_OR_ID [--yes] [--quiet]   # Direct mode
```

Output follows the canonical [action confirmation flow](#action-confirmation-flow), including the `Updating retirement plan:` variant for retired workloads. Success line: `✓ Plan changed: <name>`.

---

#### `synology-apm machine version list`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm machine version list <NAME> [--retired]                       # Search mode
synology-apm machine version list --id WORKLOAD_ID --namespace NAMESPACE   # Direct mode
```

Table output: header line `Versions: <workload name>` followed by the version table and the standard pagination footer.

Column order: `#` / Created / Status / Locked / **Verification (PS/VM only)** / Changed Size / Copy Status / Locations / Version ID

> - The version list for PS/VM Workloads shows the Verification column; PC/FS do not show this column.
> - The **Locked** column shows `🔒` when the version is locked, blank otherwise.
> - The **Locations** column shows the actual backup location name(s) (`name` or `name (vault)`, comma-separated when multiple); `-` when empty.
> - The **Copy Status** column shows the backup copy status when set; `-` when not populated.
> - When `--verbose` is added, the header additionally shows `Workload ID:` and `Namespace:` lines (for use with direct-mode operations).
> - With `--offset 25 --limit 25`, row numbers start at #26, and the footer shows `Showing 26–50 of N`.

---

#### `synology-apm machine version get`

[Search / Direct mode](#search--direct-mode); `--id` (Version ID) is optional, and the latest version is fetched automatically if omitted (the auto-selected version is reported on stderr as `(Using version: <id>, created at <time>)`):

```bash
synology-apm machine version get <NAME> [--id VERSION_ID] [--retired]                                # Search mode
synology-apm machine version get --workload-id WORKLOAD_ID --namespace NAMESPACE [--id VERSION_ID]   # Direct mode
```

**Table output** is divided into two sections:

- `── Version` section rows:
  - `Version ID:` / `Workload ID:` / `Namespace:` — always shown.
  - `Locked:          🔒` — shown only when the version is locked.
  - `Locations:` — shown when the version has locations; one location per line (`name` or `name (vault)`), additional lines indent-aligned under the first.
  - `Copy Status:` — shown only when copy_status is set; followed by an indented detail line when the copy reason maps to a message (e.g. `Authentication error occurred.`).
- `── Activity Detail — <workload-name>` section: identical to the `synology-apm activity backup get` detail body (Status / Plan / Backup Scope when present / Start / End / Duration / Data Change / Transferred / Actual Capacity Used / Processed items / `── Logs` table — see [activity backup get](#synology-apm-activity-backup-get)).

**JSON/YAML output** merges the version and activity into a single object:

```json
{
  "version_id":         "...",
  "created_at":         "2026-04-21T09:23:00+08:00",
  "status":             "success",
  "locked":             false,
  "changed_size_bytes": 1234567,
  "verify_status":      null,
  "copy_status":        "retry",
  "copy_reason":        "auth_failed",
  "locations":          [ { "location_id": "...", "is_remote_storage": false, "identifier": "...", "name": "...", "endpoint": "...", "vault": null } ],
  "workload_id":        "...",
  "namespace":          "...",
  "activity":           { ... }
}
```

---

#### `synology-apm machine version lock`

Locks the specified backup version, preventing it from being automatically deleted by retention rules. The version ID is obtained from the Version ID column of `synology-apm machine version list`.

```bash
synology-apm machine version lock <NAME> --id VERSION_ID [--retired]                                # Search mode
synology-apm machine version lock --workload-id WORKLOAD_ID --namespace NAMESPACE --id VERSION_ID   # Direct mode
```

Success output: `✓ Version locked: <version-id>`

---

#### `synology-apm machine version unlock`

Removes the manual lock on a backup version, allowing it to be deleted by retention rules. Same modes as `version lock`.

Success output: `✓ Version unlocked: <version-id>`

---

### saas — SaaS Tenant Overview

Shows tenant overview information for all SaaS service providers (M365 / GWS).

#### `synology-apm saas list`

Lists all connected SaaS tenants (M365 + GWS). Corresponds to the SDK's `apm.saas.list()` (`SaasCollection`). Supports `--limit` / `--offset` / `--page-all` / `--output`.

**table columns:**

| Column | Description |
|------|------|
| Category | Business domain (M365 / GWS) |
| Name | Tenant display name (M365 organization name / GWS domain) |
| Email / Domain | Tenant's primary email or domain |
| Protected Size | Size of this tenant's backup data (`-` when 0 or unknown) |
| Tenant ID | Tenant unique identifier (Azure AD tenant UUID / GWS domain ID) |

---

### m365 — M365 Workload Management

Manages Microsoft 365 SaaS backup Workloads, divided into six subcommand groups by service type.
The Tenant ID can be obtained via `synology-apm saas list`; if `--tenant-id`/`-t` is omitted, the first M365 tenant is used automatically (the auto-selected tenant is reported on stderr as `(Using tenant: <tenant-id>)`). Corresponds to the SDK's `apm.m365.workloads` (`M365WorkloadCollection`).

Subcommands that support search mode (`get` / `version list` / `version get` / `version lock` / `version unlock`) by default search only protected Workloads; adding `--retired` searches retired Workloads instead. In direct mode (`--id`/`--workload-id` + `--namespace`), `--tenant-id` is not required, and `--retired` has no effect.

#### Subcommand Groups

| Subcommand | Service Type | Corresponding M365WorkloadType |
|--------|---------|---------------|
| `exchange` | Mailbox (Exchange mailboxes) | EXCHANGE |
| `onedrive` | OneDrive (personal cloud) | ONEDRIVE |
| `chat` | Teams Chat | CHAT |
| `group` | Group Exchange (group mailboxes) | GROUP |
| `sharepoint` | SharePoint Sites | SHAREPOINT |
| `teams` | Teams Channels | TEAMS |

The operation interface is identical for each subcommand group; `exchange` is used as the example. The search-mode `<NAME>` argument is the UPN for exchange/onedrive/chat, the group mailbox email for group, and the site or team name for sharepoint/teams.

---

#### `synology-apm m365 <SCOPE> list`

In table mode, tenant information is shown above the table (calls `SaasCollection.get_m365_tenant(tenant_id)`):

```
Tenant: Contoso (admin@contoso.com)
```

JSON / YAML / CSV output is a plain workload list (no tenant header), convenient for `jq` pipeline use.

**Default table columns:**

| Column | Description |
|------|------|
| Name | Workload name |
| UPN / Email / URL | Shows the corresponding field depending on workload_type: exchange/onedrive/chat → UPN; group → group mailbox (Email); sharepoint/teams → URL |
| Status | Backup status icon and color, see [Status Icons](#status-icons) "Workload Backup Result"; **this column is not shown with `--retired`** |
| Last Backup | Last backup time |
| Protected Size | Storage space occupied by protected data (human-readable, e.g. 145.3 GB) |
| Copy Size | Storage space occupied by Backup Copy (backup_copy_data_bytes); shows `-` if unset or 0 |
| Protection Plan | Name of the applied protection plan |
| Backup Server | Backup server display name (backup_server.name); shows `-` if unknown |
| Copy Destination | Backup Copy destination (backup_copy_destination); format: name or name (vault); shows `-` if unset |

**Additional `--verbose` columns:** Workload ID / Namespace / Plan ID (same semantics as `machine list`).

---

#### `synology-apm m365 <SCOPE> get`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm m365 exchange get <NAME> [-t <TID>] [--retired]     # Search mode (keyword search + exact match)
synology-apm m365 exchange get --id <UID> --namespace <NS>       # Direct mode (--tenant-id not required)
```

Table output follows the canonical [get detail block](#get-detail-block), with these differences:

> - Type row: `M365 / <Service>` (`Exchange` / `OneDrive` / `Chat` / `Group` / `SharePoint` / `Teams`).
> - Workload Information extra rows: the identifier row (`UPN:` / `Email:` / `URL:` by service type) and `Tenant ID:`; no Device UUID / Agent / IP / Host rows.
> - Backup Status block: no Verification line (M365 workloads have no verification status).

---

#### `synology-apm m365 <SCOPE> backup / cancel / retire / change-plan`

Same modes, options, confirmation flows, and success lines as the corresponding `machine` commands (shared action bodies), with these differences:

```bash
synology-apm m365 exchange backup <NAME> [-t <TID>] [--quiet]          # Search mode (direct mode: --id <UID> --namespace <NS>)
synology-apm m365 exchange cancel <NAME> [-t <TID>] [--yes] [--quiet]
synology-apm m365 exchange retire <NAME> --plan PLAN_NAME_OR_ID [-t <TID>] [--yes] [--quiet]
synology-apm m365 exchange change-plan <NAME> --plan PLAN_NAME_OR_ID [-t <TID>] [--retired] [--yes] [--quiet]
```

> - Confirmation summaries show the workload without a type label (`alice@contoso.com` instead of `CORP-PC-001 (PC/Mac)`); see the canonical-flow notes in [Output Formats](#output-formats).
> - M365 backups are triggered directly by the API, with no Job ID returned. Use `synology-apm activity backup list` to check progress.
> - `retire` requires at least one retirement plan already created in the APM UI. Use `synology-apm plan retirement list --verbose` to get the Plan ID.

---

#### `synology-apm m365 <SCOPE> version list / get / lock / unlock`

Same modes, options, output, and success lines as the corresponding `synology-apm machine version` commands (shared table renderer and detail body; M365 versions use the same version endpoint as machine versions), plus the `-t <TID>` option in search mode. Differences:

```bash
synology-apm m365 exchange version list <NAME> [-t <TID>]                          # Search mode (direct mode: --id <WL_ID> --namespace <NS>)
synology-apm m365 exchange version get <NAME> [--id VERSION_ID] [-t <TID>]         # Direct mode: --workload-id <WL_ID> --namespace <NS> [--id ...]
synology-apm m365 exchange version lock <NAME> --id VERSION_ID [-t <TID>]
synology-apm m365 exchange version unlock <NAME> --id VERSION_ID [-t <TID>]
```

> - The version table never shows the Verification column (machine PS/VM only); columns: `#` / Created / Status / Locked / Changed Size / Copy Status / Locations / Version ID.
> - `version get` output is identical to `machine version get` (Version section — including Copy Status — plus Activity Detail section). M365 activity details show `Processed items:` and have no Backup Scope; `Actual Capacity Used:` is always printed and shows `-` when unavailable.

---

### m365 exchange export / m365 group export — Mailbox PST Export

Applies to the two subcommand groups `exchange` and `group`, which share essentially the same API and behavior, with the differences below:

| Item | exchange | group |
|------|---------|-------|
| Identifier | UPN (`alice@contoso.com`) | Group email (`marketing@contoso.com`) |
| `--archive-mailbox` | Supported | Not supported (Groups have no archive mailbox) |
| SDK collection | `apm.m365.exchange_export` | `apm.m365.group_export` |
| Auto-start local filename suffix | `_mailbox.pst` / `_archive_mailbox.pst` | `_group_mailbox.pst` |

The interface is described below using `exchange` as the example; the `group` command structure is identical — simply replace `exchange` with `group` (the waiting/status hint messages also show `synology-apm m365 group export list <email>` accordingly).

#### `synology-apm m365 exchange export list`

Lists the export tasks for a specified workload.

```bash
synology-apm m365 exchange export list <NAME> [-t <TID>]                        # Search mode
synology-apm m365 exchange export list --workload-id <UID> --namespace <NS>    # Direct mode
```

**table columns:** Item, Version, Status, Started, Finished, Activity ID

The Item column shows `activity.source_name` (converted by the SDK into `Entire mailbox` / `Entire archive mailbox` / a folder name — archive mailbox information is embedded in the Item value; there is no separate Archive column). The Version column shows `activity.version_timestamp` (the backup version timestamp; shows `-` if no data). When there are no export tasks, prints `No export tasks found.` instead of an empty table.

#### `synology-apm m365 exchange export cancel`

Cancels an in-progress export task.

```bash
synology-apm m365 exchange export cancel <NAME> --id <ACTIVITY-ID> [-t <TID>]                       # Search mode
synology-apm m365 exchange export cancel --workload-id <UID> --namespace <NS> --id <ACTIVITY-ID>    # Direct mode
```

Success output: `✓ Export task <activity-id> canceled.`

#### `synology-apm m365 exchange export download`

Starts a new export and waits for it to download (no `--id`), or directly downloads an already-started export (with `--id`).

```bash
synology-apm m365 exchange export download <NAME> [-t <TID>] [OPTIONS]                              # Auto-start mode (search; direct: --workload-id + --namespace)
synology-apm m365 exchange export download <NAME> --id <ACTIVITY-ID> [-t <TID>]                     # Direct download mode
```

**Local filename** (`--filename`/`-f`; auto-generated when omitted): auto-start mode uses `{name}_{YYYYMMDD}_{mailbox|archive_mailbox|group_mailbox}.pst`; direct download mode (`--id`) uses `{name}_{first 8 chars of activity id}.pst`. Characters unsafe for filesystems are replaced with `_`. The server-side PST name (`--export-name`; auto-start only) defaults to the basename of the local filename.

**Auto-start flow:**
1. Resolve the backup version (latest unless `--version-id`; the auto-selected version is reported via `(Using version: ...)`) → start the export → obtain `M365ExportStartResult`
2. `ready_to_download=True` → download immediately
3. `ready_to_download=False` + `--no-wait` → print `✓ Export started ...` with the Activity ID when available (otherwise a hint to run `export list`), suggest re-running with `--id <activity-id>` to download, exit 0
4. `ready_to_download=False` + no `--no-wait` → print a waiting message and poll until the export becomes downloadable (READY_TO_DOWNLOAD **or WARNING**). On Ctrl+C, the CLI asks `Cancel export task on APM? [y/N]` — answering y cancels the server-side task, answering n leaves it running and prints the Activity ID / `export list` hint — and **exits 4 in both cases**. If polling ends in a non-downloadable terminal state (FAILED / CANCELED / EXPIRED / DOWNLOADED), prints `✗ Export ended with status: <status>` and exits 1.
5. Fetch the download URL from the start result and stream the file with a progress bar (on stderr).

**Overwrite behavior:** if the local target file already exists, the CLI prompts `Overwrite?` (declined → exit 4; `--yes` skips). Auto-start mode confirms overwrite before starting the export; Direct mode confirms before downloading.

**Success output:** `✓ Saved to <dest_path>`

#### `synology-apm m365 group export` Differences

The interface of `synology-apm m365 group export list / cancel / download` is identical to exchange, with the following differences:

- `<NAME>` is the Group email (e.g. `marketing@contoso.com`)
- `download` does not offer the `--archive-mailbox` option
- The Auto-start local filename suffix is `_group_mailbox.pst`
- The waiting prompt message shows `synology-apm m365 group export list <email>` instead of `exchange`

---

### plan protection — Protection Plan Management

Manages Protection Plans (backup protection plans). Both `get` / `get_by_name` are category-agnostic.

#### `synology-apm plan protection list`

`--category`/`-c` filters by workload category: `machine` / `m365` (omit for all); `--search`/`-s` is a name keyword search.

**table columns (in the order below; `--verbose` / `-v` additionally shows Description and Plan ID; Type is shown only when `--category` is omitted):**

| Column | Description |
|------|------|
| Name | Plan name |
| Type | Workload category (shown when `--category` is omitted) |
| Description | Plan description (shown only with `--verbose`, located after Type) |
| Immutable | Whether immutable backup is enabled (Yes / No) |
| Retention | Retention rule (Keep all / N day(s) / N versions / Advanced rules / -) |
| Schedule | Primary backup schedule type (Daily Backup / Hourly Backup / Weekly Backup / Manual Backup / After Backup) |
| Copy Destination | Backup Copy destination (name or name (vault)); shows `-` if not enabled |
| Copy Retention | Backup Copy retention rule; shows `-` if not enabled |
| Copy Schedule | Backup Copy schedule type; shows `-` if not enabled |
| Copy Status | Backup copy status (`PlanBackupCopyStatus`); shows `-` when `backup_copy_status` is `None`; shows "No versions to copy" when `reason == CopyReason.NO_VERSIONS_TO_COPY` |
| ✓ | Number of successfully backed-up Workloads (protectedWorkloadCount) |
| ✗ | Number of Workloads with failed backups (unprotectedWorkloadCount) |
| Plan ID | Plan ID (shown only with `--verbose`) |

---

#### `synology-apm plan protection get`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm plan protection get NAME       # Search mode (name search, exact match page by page, across categories)
synology-apm plan protection get --id PLAN_ID   # Direct mode (direct UUID lookup, category-agnostic)
```

**table detail format (section-based):**

```
Protection Plan: <name>
──────────────────────────────────────
ID:           <plan_id>
Category:     <workload category>
Description:  <description or `-`>
Immutable:    Yes / No

Successful:   <n> workloads
Unsuccessful: <n> workloads
Copy Status:  <formatted status + detail lines>   ← see "Copy status detail lines" above

Backup Copy Policy
  Destination: <name> or <name (vault)> or `-` (when the destination lookup fails)
  Retention:  <N day(s) / N versions / Keep all / Advanced rules / ->
  Schedule:   After Backup / Daily, 09:00 / ...
  ── or ──
  No Backup Copy enabled.             ← when backup copy is not enabled

Backup Policy
  Retention:       <N day(s) / N versions / Keep all / Advanced rules / ->
  ← When KEEP_ADVANCED, replaced by multi-line rules (items with no value are not shown):
    Keep all versions for N days
    Keep the latest version of the day for N days
    Keep the latest version of the week for N weeks
    Keep the latest version of the month for N months
    Keep the latest version of the year for N years
    Number of latest version to keep: N versions
  Default Schedule: <Detailed schedule: Daily, 09:00 / Hourly at :15 / Weekly on Mon., 09:00 / Manual>
  Backup Window:    No restriction    ← when plan.backup_window.enabled is False
  ── or ──
  Backup Window:                      ← when plan.backup_window.enabled is True (always 7 lines, Mon–Sun)
    Mon.  <hour ranges or "blocked">  ← range format: HH:MM–HH:MM (e.g. "00:00–08:00, 20:00–24:00")
    Tue.  <hour ranges or "blocked">  ← "unrestricted" when all 24 hours are allowed
    Wed.  <hour ranges or "blocked">  ← "blocked" when the day is absent from allowed_hours
    Thu.  <hour ranges or "blocked">
    Fri.  <hour ranges or "blocked">
    Sat.  <hour ranges or "blocked">
    Sun.  <hour ranges or "blocked">
  ← Backup Window omitted when plan.backup_window is None

Custom Scopes & Schedules            ← shown only when plan.tasks is a non-empty tuple; 2-space left indent
  Type             OS        Backup Scope      Schedule
  <workload type>  <os>      <scope detail>    <schedule>
```

Workload type display (`_WORKLOAD_TYPE_DISPLAY`): `PC/Mac` / `Physical Server` / `File Server` / `Virtual Machine`

OS type display (`_OS_TYPE_DISPLAY`): `Windows` / `Mac` / `Linux` / `-` (NONE — used for VM and FS tasks)

Backup scope display (`_SCOPE_DISPLAY` + qualifiers):
- `Entire Machine` — optionally `(Include external drives)` when `include_external_drives=True`
- `System Volume`
- `Custom Volume: C:, D:` — volume list appended after colon; optionally `(Include boot partition)` when `include_boot_partition=True`
- `-` — when `scope is None`

Schedule cell:
- `Follow the default schedule` — when `use_main_schedule=True`
- Time schedule string (e.g. `Daily, 14:30`) — when `use_main_schedule=False` and `time_schedule` is set
- `Events: Sign-out, Screen lock (min. 1h)` — when only `event_trigger` is set (enabled events only; interval via `_fmt_min_interval`)
- Both lines joined with newline — when both `time_schedule` and `event_trigger` are set
- `-` — when `use_main_schedule=False` and `schedule` is None or both fields absent

---

### plan retirement — Retirement Plan Management

Manages Retirement Plans.
Used as the source for the `--plan` parameter of `synology-apm machine retire` / `synology-apm m365 retire` (to retire a Workload) and `synology-apm machine change-plan` / `synology-apm m365 <SCOPE> change-plan` (to re-assign the retirement plan of an already-retired Workload). The Plan ID can be obtained from `synology-apm plan retirement list --verbose`.

#### `synology-apm plan retirement list`

**table columns:**

| Column | Description |
|------|------|
| Name | Plan name |
| Description | Description |
| Version Retention (Days) | Retention days (`-` means no time limit) |
| Keep Latest Version | Whether the latest version is always kept (Yes / No) |
| Included Workloads | Total number of workloads to which this plan is applied |
| Plan ID | Plan ID (shown only with `--verbose`) |

---

#### `synology-apm plan retirement get`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm plan retirement get NAME           # Search mode (name search, exact match page by page)
synology-apm plan retirement get --id PLAN_ID   # Direct mode (direct UUID lookup)
```

Table detail rows (`Retirement Plan: <name>` header + `─` rule): `ID` / `Description` (`-` when empty) / `Version Retention (Days)` (`-` means no time limit) / `Keep Latest Version` (Yes / No) / `Included Workloads`.

---

### plan tiering — Tiering Plan Management

#### `synology-apm plan tiering list`

Lists all Tiering Plans. `--verbose` additionally shows the Plan ID column.

| Column | Description |
|------|------|
| Name | Plan name |
| Description | Description |
| Tier After | Number of days before tiering (e.g. `30 days`) |
| Destination | Remote storage name (shown as `name (vault)` when a vault is present) |
| Daily Check Time | Daily execution time (HH:MM) |
| Included Servers | Number of backup servers to which this plan is applied |
| Tiering Status | Current tiering status; shows `-` when not set or `NOT_ENABLED` |

#### `synology-apm plan tiering get`

```bash
synology-apm plan tiering get NAME              # Search mode (keyword search + exact match)
synology-apm plan tiering get --id PLAN_ID      # Direct mode
```

`NAME` and `--id` are mutually exclusive: if neither is provided, help is shown; if both are provided, exit code 1.

**table detail format (section-based):**

```
Tiering Plan: <name>
──────────────────────────────────────
ID:               <plan_id>
Description:      <description or `-`>

Tiering Status:   <formatted status + detail lines>   ← shown before config fields; see "Copy status detail lines"

Tier After:       <N> days
Destination:      <name> or <name (vault)> or `-`
Daily Check Time: HH:MM
Included Servers: <N>
```

---

### activity — Activity Log Queries

Queries backup/restore activity records. `backup list` / `restore list` by default show only in-progress tasks (Ongoing); adding `--history` switches to showing completed historical records; if there are no in-progress tasks and `--history` is not given, a hint is printed instead of an empty table (`No ongoing backup tasks.` / `Use --history to view completed activities.`).

#### `synology-apm activity backup list`

Filters: `--status` (repeatable: queuing / backing_up / canceling / success / failed / partial / canceled), `--search`, `--machine-type` (repeatable: pc / ps / vm / fs), `--m365-type` (repeatable: exchange / onedrive / chat / sharepoint / teams / group), `--namespace` (repeatable), `--since` / `--until`, `--history`.

**Default table columns:**

| Column | Description |
|------|------|
| Workload | Workload name |
| Status | Backup status icon and color, see [Status Icons](#status-icons) "Workload Backup Result" (in-progress rows show `⠸ Backing up (n%)` or `⠸ Backing up (n items)`) |
| Verification | Backup verification result icon and color, see [Status Icons](#status-icons) "Backup Verification Status"; **shows `-` for PC/FS/M365 and when no data is available** |
| Started | Start time |
| Duration | Elapsed time |
| Activity ID | Unique activity identifier (used by `synology-apm activity backup get` / `cancel`) |

**Additional `--verbose` columns:** Transferred / Workload ID / Workload Namespace

---

#### `synology-apm activity backup get`

```bash
synology-apm activity backup get WORKLOAD_NAME       # Search mode (gets the latest entry by workload name)
synology-apm activity backup get --id ACTIVITY_ID    # Direct mode (gets directly by Activity ID)
```

Shows the detailed information and logs of a specified backup activity.

- **Search mode**: pass the workload name (positional argument). The CLI calls `BackupActivityCollection.get_latest_by_workload_name(name)`; the SDK internally searches with `keyword=name`, exact-matches `workload_name`, takes the latest entry, and fetches the detail/log. Exit code 1 if not found.
- **Direct mode**: pass `--id ACTIVITY_ID`, calling `BackupActivityCollection.get(activity_id)`. The Activity ID is obtained from the Activity ID column of `synology-apm activity backup list`.

**Detail body** (`Activity Detail — <workload name>` header + `─` rule; this same body is reused as the Activity Detail section of `machine version get` / `m365 <scope> version get`):

> - `Status:` — status icon and color.
> - `Workload:` / `Plan:` (`-` when no plan name).
> - `Backup Scope:` — shown only when the activity carries a scope (PC/PS/VM/FS; M365 activities have none); label from `BACKUP_SCOPE_LABELS`: `Entire Device with External Drives` / `Entire Device` / `Volume` / `File / Folder`.
> - `Start:` / `End:` / `Duration:` block.
> - `Data Change:` / `Transferred:` / `Actual Capacity Used:` block — all three lines are always printed; each shows `-` when the value is unavailable.
> - `Processed items: N succeeded, N warning, N error` — shown only when the activity reports processed-item counts (FS and M365 activities).
> - `── Logs` table (Time / Level / Message) — shown only when the activity has log entries.

---

#### `synology-apm activity backup cancel --id <ACTIVITY_ID>`

```bash
synology-apm activity backup cancel --id ACTIVITY_ID [--yes] [--quiet]
```

Confirmation summary (stderr) shows `⚠ Confirm cancel backup activity?` followed by `Activity:` / `Workload:` / `Started:` / `Progress:` lines (`Progress:` shows a percentage for PC/PS/VM, or `N items` for FS/M365), then the `  Confirm? [y/N]:` prompt. Success line: `✓ Backup cancelled.`

The CLI first calls `backup.list()` to obtain the Activity object, then calls `BackupActivityCollection.cancel(activity)`; the SDK automatically routes to the corresponding cancel interface based on `activity.category`. `--yes` skips the confirmation prompt; exit code 1 if the activity is not found (or already completed); exit code 4 if the user declines the confirmation.

---

#### `synology-apm activity restore list`

Filters: `--status` (repeatable: preparing / restoring / canceling / ready_for_migrate / migrate_vm_manually / migrating / success / failed / partial / canceled), `--search`, `--since` / `--until`, `--history`.

**Table columns:** Workload / Restore Type / Status / Started / Duration / Operator / Activity ID
**Additional `--verbose` columns:** Transferred / Workload ID / Workload Namespace

#### `synology-apm activity restore get`

```bash
synology-apm activity restore get WORKLOAD_NAME      # Search mode (gets the latest entry by workload name)
synology-apm activity restore get --id ACTIVITY_ID   # Direct mode (gets directly by Activity ID)
```

- **Search mode**: calls `RestoreActivityCollection.get_latest_by_workload_name(name)`, behaving the same as `synology-apm activity backup get` search mode.
- **Direct mode**: pass `--id ACTIVITY_ID`, calling `RestoreActivityCollection.get(activity_id)`.

Detail body (`Restore Activity Detail — <workload name>` header + `─` rule): Status, Workload, Restore Type, Version, Restore from (`name` or `name (vault)`), Destination, Destination path, Destination hypervisor (`hostname (address)`), Operator, Start/End/Duration, Transferred, Processed items (when counts are present), and the `── Logs` table. Restore Type, Version, Restore from, Destination, Destination path, Destination hypervisor, and Operator are each shown only when present in the response. In practice `Destination path` appears for file-level restores (FS/M365 workloads) and `Destination hypervisor` for machine-level VM restores — the two never appear on the same activity.

#### `synology-apm activity restore cancel --id <ACTIVITY_ID>`

```bash
synology-apm activity restore cancel --id ACTIVITY_ID [--yes] [--quiet]
```

Cancels an in-progress restore activity; same confirmation flow and exit codes as `activity backup cancel` (success line: `✓ Restore cancelled.`). The CLI first calls `list()` to obtain the Activity object, then calls `RestoreActivityCollection.cancel(activity)`; the SDK is responsible for constructing the full request.

---

### infra — Infrastructure Information

Manages basic APM Management Server information, the backup server cluster, and remote storage devices.

> **Note:** the backup server subcommand was renamed from `infra backup-server` to `infra server` (v0.4.1).

#### `synology-apm infra info`

```bash
synology-apm infra info [--output [table|json|yaml]]
```

Shows the site UUID, Management Center and Recovery Portal URLs, Primary / Secondary Management Server information (including health status), site-wide backup storage statistics, and the count and data usage of each Workload type. Calls `APMClient.get_site_info()`.

Table output sections (each with a bold header + `─` rule):

> - **Site Information**: `UUID:` / `Management Center:` / `Recovery Portal:`. The URL port is omitted when empty or 443; other port values are appended (`https://host:port`); both URL lines show `-` when no external address is configured.
> - **Primary Management Server**: `Name:` / `Model:` / `IP:` / `System Version:` (shows `Updating...` while updating; `-` when unknown) / `Serial:` / `Status:`; shows `Not available` when absent.
> - **Secondary Management Server**: same fields; shows `Not configured` when not set.
> - **Data Reduction Summary**: `Total Logical Backup Data:` / `Total Physical Backup Data:` / `Data Reduced: <bytes> (<ratio>%)`.
> - **Workload Usage Summary**: a Type / Workloads / Data Size text table in fixed row order PC, PS, VM, FS, M365, GWS (types absent from the response are omitted), ending with a `Total` row; Data Size shows `-` when 0.

`--output json` output structure:

```json
{
  "site_uuid": "123e4567-e89b-12d3-a456-426614174017",
  "external_address": "apm.corp.com",
  "port": "443",
  "primary_management_server": { "backup_server_id": "...", "name": "apm-server-01", "hostname": "192.0.2.1", "model": "DP320", "system_version": "APM 1.2-71845", "serial": "SN001", "status": "healthy", "role": "primary", "is_updating": false, ... },
  "secondary_management_server": null,
  "site_storage": { "logical_backup_data_bytes": 107374182400, "physical_backup_data_bytes": 42949672960, "backup_data_reduction_bytes": 64424509440, "backup_data_reduction_ratio": 60.0 },
  "workload_usage": {
    "total_count": 45,
    "total_protected_data_bytes": 411603673088,
    "by_type": [
      { "workload_type": "pc",              "total_count": 1,  "protected_data_bytes": 199096201216 },
      { "workload_type": "virtual_machine", "total_count": 6,  "protected_data_bytes": 95284154368 },
      { "workload_type": "file_server",     "total_count": 1,  "protected_data_bytes": 622608384 },
      { "workload_type": "m365",            "total_count": 37, "protected_data_bytes": 116600709120 }
    ]
  }
}
```

---

#### `synology-apm infra server list`

Filters: `--search`, `--status` (repeatable), `--type` (repeatable).

**Default table columns:**

| Column | Description |
|------|------|
| Name | Server name; Primary Management Server gets an additional `(Primary)` badge (green); Secondary Management Server gets an additional `(Secondary)` badge (cyan) |
| Serial Number | Serial number |
| IP Address | Device IP address or hostname |
| Model | Device model |
| System Version | System version (shows `Updating...` while updating; `-` when unknown) |
| Status | Connection status icon and color, see [Status Icons](#status-icons) "Backup Server Status" |
| Usage | Usage percentage and capacity; format `  4% (277.3 GB / 7.0 TB)`; yellow at ≥80%, red at ≥90%; shows `-` if no data |
| Tiering Plan | Tiering Plan name; shows `-` if not set |
| Tiering Status | Current tiering status; shows `-` when not set or `NOT_ENABLED` |

**Additional `--verbose` columns:**

| Column | Description |
|------|------|
| Description | Server description filled in by the administrator; shows `-` if not filled in |
| Server ID | Backup Server UUID |
| Namespace | Backup Server Namespace |

---

#### `synology-apm infra server get`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm infra server get "apm-server-01"       # Search mode (name keyword search)
synology-apm infra server get --id SERVER_ID        # Direct mode
```

**table detail format (section-based; sections appear in exactly this order):**

```
Backup Server: <name>
──────────────────────────────────
ID:             <backup_server_id>
Namespace:      <namespace>
Model:          <model>
IP:             <hostname>
Serial:         <serial>
System Version: <version or "Updating..." or `-`>
Description:    <description or `-`>
Status:         <status icon + label>

Tiering Status: <formatted status + detail lines>   ← see "Copy status detail lines"

Storage Usage:
  Total:  <total bytes>
  Used:   <used bytes> (<pct>%)                     ← percentage omitted when total is unknown

Data Reduction Summary:
  Logical Backup Data:   <bytes>
  Physical Backup Data:  <bytes>
  Data Reduced:          <bytes> (<ratio>%)          ← ratio omitted when reduction is unknown (`-`)

Tiering Plan:
  Plan:         <tiering plan name>
  Destination:  <destination name>
  Endpoint:     <destination endpoint>
  Vault:        <vault>                              ← only when the destination has a vault
  ── or ──
  Not configured                                     ← when no Tiering Plan is set
```

---

#### `synology-apm infra server change-plan`

Apply or remove a Tiering Plan on a backup server. Only DP-type backup servers support tiering plans.

The two server-identification modes are mutually exclusive, and exactly one of `--plan` or `--remove` is required (both → exit 1; neither → help):

```bash
synology-apm infra server change-plan "apm-server-01" --plan "30-Day Tiering" [--yes] [--quiet]   # Apply (search mode)
synology-apm infra server change-plan --id SERVER_ID --plan PLAN_NAME_OR_ID [--yes] [--quiet]     # Apply (direct mode)
synology-apm infra server change-plan "apm-server-01" --remove [--yes] [--quiet]                  # Remove the current plan
```

Follows the canonical [action confirmation flow](#action-confirmation-flow) with a server-specific summary — header `Changing tiering plan:`, one line `  Server:   <name> (ID: <server-id>)`, then `⚠ Current plan: <current or None> -> <new or None>` — and the success line `✓ Tiering plan updated: <name>`. With `--remove`, an additional warning paragraph is printed before the prompt:

```
⚠ Removing the tiering plan from a server will stop any new data from being tiered,
but ongoing operations will continue. To ensure full protection, the lock duration for
immutable workloads on backup servers will also be adjusted accordingly.
```

`--plan` accepts a Tiering Plan name or UUID (resolved via `_resolve_tiering_plan()`: UUID-shaped values with `.get()`, otherwise `.get_by_name()`). Use `synology-apm plan tiering list` to discover available tiering plans; the Server ID comes from `synology-apm infra server list --verbose`.

---

#### `synology-apm infra storage list`

Lists all configured remote storage devices (External Vault). The API does not support pagination and returns all results in one call (no `--limit` / `--offset` / `--page-all`).

**Default table columns:**

| Column | Description |
|------|------|
| Name | Display name (`displayName`) |
| Endpoint | Connection address (`host:port`) |
| Type | Storage device type (including model, e.g. `ActiveProtect Vault (DP320)`) |
| Client-Side Encryption | Client-side encryption status; shows `✓ Enabled` (green) when enabled, `✗ Disabled` (gray) when not |
| Status | Connection status icon and color, see [Status Icons](#status-icons) "Remote Storage Status" |
| Usage | Usage: `442.8 KB (341.8 GB left)` / when only used is available `442.8 KB` / `-` when data is unavailable |

**Additional `--verbose` columns:**

| Column | Description |
|------|------|
| Remote Storage ID | Remote storage device UUID |

`--output json` output structure:

```json
[
  {
    "storage_id": "123e4567-e89b-12d3-a456-426614174015",
    "name": "DSM-Storage",
    "storage_type": "active_protect_vault",
    "device_model": "DSM",
    "endpoint": "192.0.2.20:8444",
    "encryption_enabled": false,
    "status": "connected",
    "used_bytes": 453378,
    "remaining_bytes": 366960877568
  }
]
```

---

#### `synology-apm infra storage get`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm infra storage get "DSM-Storage"      # Search mode (display name or endpoint)
synology-apm infra storage get --id STORAGE_ID    # Direct mode
```

Table detail rows (`Remote Storage: <name>` header + `─` rule): `ID` / `Type` / `Endpoint` / `Client-Side Encryption` / `Status`, then a `Storage Usage:` section with `Used:` and `Remaining:` lines (each `-` when unavailable).

---

#### `synology-apm infra hypervisor list`

Lists all registered Hypervisor Inventory servers. The API does not support pagination and returns all results in one call (no `--limit` / `--offset` / `--page-all`).

**Default table columns:**

| Column | Description |
|------|------|
| Hostname | Hypervisor display name (`spec.hostName`) |
| Address | Connection IP or FQDN (`spec.hostAddr`) |
| Type | Hypervisor type display string (see the type mapping below) |
| Account | Authentication account (`spec.authUser`) |
| Description | Description notes (`spec.description`); shows `-` if empty |

**Additional `--verbose` columns:**

| Column | Description |
|------|------|
| Hypervisor ID | Hypervisor UUID |

**Hypervisor type mapping:**

| `HypervisorType` enum | CLI display string |
|----------------------|-------------|
| `VSPHERE_ESXI` | VMware vSphere (ESXi) |
| `VSPHERE_VCENTER` | VMware vSphere (vCenter) |
| `HYPERV_STANDALONE` | Microsoft Hyper-V (Standalone) |
| `HYPERV_SCVMM` | Microsoft Hyper-V (SCVMM) |
| `HYPERV_FAILOVER_CLUSTER` | Microsoft Hyper-V (Failover Cluster) |
| `UNKNOWN` | Unknown |

`--output json` output structure:

```json
[
  {
    "hypervisor_id": "123e4567-e89b-12d3-a456-426614174016",
    "hostname": "esxi1.example.com",
    "address": "192.0.2.40",
    "host_type": "vsphere_esxi",
    "account": "root",
    "description": "",
    "port": 443,
    "version": "6.5"
  }
]
```

---

#### `synology-apm infra hypervisor get`

[Search / Direct mode](#search--direct-mode):

```bash
synology-apm infra hypervisor get "esxi1.example.com"    # Search mode (hostname or address)
synology-apm infra hypervisor get --id HYPERVISOR_ID     # Direct mode
```

Table detail rows (`Hypervisor: <hostname>` header + `─` rule): `ID` / `Type` / `Address` / `Port` / `Account` / `Version` (`-` when unknown) / `Description` (`-` when empty).

---

### `synology-apm log` — Backup Server Logs

Queries the system logs of a specified backup server. All `synology-apm log * list` commands require specifying the target backup server: `<SERVER>` is a name keyword search (search mode), `--id` directly specifies the Backup Server ID (direct mode).

> **Warning:** only DP (ActiveProtect) backup servers are supported. If a NAS server is specified, the command shows an error and ends with exit code 1.

**How to obtain the Server ID:** `synology-apm infra server list --verbose` (verbose mode must be enabled to show the ID column)

**Shared filters:** all four `log * list` commands support `--level` (repeatable severity filter: information / warning / error), `--since` / `--until`, `--search`, and the standard pagination options. `log activity list` additionally supports `--type`, and `log drive list` supports `--location` (shown in their usage lines below).

---

#### `synology-apm log activity list`

```bash
synology-apm log activity list [<SERVER> | --id <SERVER_ID>] [OPTIONS] [--type [protection|system|data_access]]
```

**Displayed columns:** Level, Type, Time, User, Event

| Column | Description |
|------|------|
| Level | Severity |
| Type | Log category (Data protection / System management / Data access) |
| Time | Event time |
| User | Triggering user (SYSTEM for system events) |
| Event | Event description |

**Level / Type display mapping:**

| `LogLevel` enum | CLI display |
|----------------|---------|
| `INFO` | Information |
| `WARNING` | Warning (yellow) |
| `ERROR` | Error (red) |

| `APMActivityLogType` enum | CLI display |
|-----------------------|---------|
| `PROTECTION` | Data protection |
| `SYSTEM` | System management |
| `DATA_ACCESS` | Data access |

---

#### `synology-apm log drive list`

```bash
synology-apm log drive list [<SERVER> | --id <SERVER_ID>] [OPTIONS] [--location TEXT]
```

**Displayed columns:** Level, Time, Model, Serial Number, Server Name, Location, Event

| Column | Description |
|------|------|
| Server Name | Name of the backup server containing this drive (API field `deviceName`) |
| Model | Drive model; `-` means not applicable |
| Serial Number | Serial number; `-` means not applicable |
| Location | Physical location; `-` means not applicable |

> **Note:** the drive log table requires a terminal width of 120+ columns to fully display the Event column.

---

#### `synology-apm log connection list`

```bash
synology-apm log connection list [<SERVER> | --id <SERVER_ID>] [OPTIONS]
```

**Displayed columns:** Level, Time, User, Event

---

#### `synology-apm log system list`

```bash
synology-apm log system list [<SERVER> | --id <SERVER_ID>] [OPTIONS]
```

**Displayed columns:** Level, Time, User, Event

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
| `InvalidOperationError` / `ResourceNotReadyError` / `PlanNameConflictError` / `PlanInUseError` / `DuplicateWorkloadError` | 1 | `✗ <message>` |
| `PermissionDeniedError` | 1 | `✗ Permission denied: <message>` |
| `NotSupportedError` | 5 | `✗ Not supported: <message>` |
| `APIError` (message indicates an SSL certificate verification failure) | 3 | `✗ SSL certificate verification failed` + hint suggesting `--no-verify-ssl` or skipping SSL verification in `config set` |
| `APIError` (message indicates a connection problem, e.g. contains "connect"/"connection") | 3 | `✗ <message>` |
| Any other `APMError` | 1 | `✗ API error: <message>` |

Additionally (non-`APMError` paths): a `ValueError` raised inside a command is printed as `✗ <message>` with exit 1; `KeyringUnavailableError` (OS keyring backend unavailable) is printed with exit 1; a declined confirmation prompt prints `Cancelled.` and exits 4.

### Hints for Common Usage Issues

When authentication is not configured:
```
✗ Connection settings not configured

  Run first:
    synology-apm config set --host <APM_HOST> --username <USER>

  Or set environment variables:
    export APM_HOST=apm.corp.com
    export APM_USERNAME=admin
    export APM_PASSWORD=...
```

---

## CLI → SDK Mapping Table

Rows marked *plan resolution* resolve `--plan` (UUID → `.get()`, otherwise `.get_by_name()`) against `ProtectionPlanCollection` / `RetirementPlanCollection` as described in the [Development Conventions](#development-conventions) "Plan resolution" bullet — based on `--retired` for list filters, always a Retirement Plan for `retire`, and the workload's current state for `change-plan`. In the export rows, `<Export>` denotes `ExchangeExportCollection` (`exchange`) or `GroupExportCollection` (`group`).

| Command | Corresponding SDK |
|------|---------|
| `synology-apm config set/show/clear` | — |
| `synology-apm machine list --type [pc\|ps\|vm\|fs] --plan` | *plan resolution* → `MachineWorkloadCollection.list()` |
| `synology-apm machine get` | `MachineWorkloadCollection.get_by_name()` (search) / `MachineWorkloadCollection.get()` (direct) |
| `synology-apm machine backup` | `MachineWorkloadCollection.backup_now()` |
| `synology-apm machine cancel` | `MachineWorkloadCollection.cancel_backup()` |
| `synology-apm machine retire --plan` | *plan resolution* → `MachineWorkloadCollection.retire()` |
| `synology-apm machine change-plan --plan` | *plan resolution* → `MachineWorkloadCollection.change_plan()` |
| `synology-apm machine version list` | `MachineWorkloadCollection.list_versions()` |
| `synology-apm machine version get [--id]` | `MachineWorkloadCollection.get_version()` or `get_latest_version()` + `BackupActivityCollection.get_by_version()` |
| `synology-apm machine version lock --id` | `MachineWorkloadCollection.get_version()` → `lock_version(version)` |
| `synology-apm machine version unlock --id` | `MachineWorkloadCollection.get_version()` → `unlock_version(version)` |
| `synology-apm activity backup list/get/cancel` | `BackupActivityCollection` |
| `synology-apm activity restore list/get/cancel` | `RestoreActivityCollection` |
| `synology-apm infra server list` | `BackupServerCollection.list()` |
| `synology-apm infra server get` | `BackupServerCollection.get()` (direct) / `BackupServerCollection.get_by_name()` (search) |
| `synology-apm infra server change-plan` | `BackupServerCollection.get()` or `get_by_name()` + `TieringPlanCollection.get()` or `get_by_name()` + `BackupServerCollection.change_tiering_plan()` |
| `synology-apm infra storage list` | `RemoteStorageCollection.list()` |
| `synology-apm infra storage get` | `RemoteStorageCollection.get()` (direct) / `RemoteStorageCollection.get_by_name()` (search) |
| `synology-apm infra hypervisor list` | `HypervisorCollection.list()` |
| `synology-apm infra hypervisor get` | `HypervisorCollection.get()` (direct) / `HypervisorCollection.get_by_name()` (search) |
| `synology-apm infra info` | `APMClient.get_site_info()` |
| `synology-apm saas list` | `SaasCollection.list()` |
| `synology-apm m365 <scope> list [-t] --plan` | *plan resolution* → `M365WorkloadCollection.list()` + `SaasCollection.get_m365_tenant()` |
| `synology-apm m365 <scope> get/backup/cancel` | `M365WorkloadCollection` |
| `synology-apm m365 <scope> retire --plan` | *plan resolution* → `M365WorkloadCollection.retire()` |
| `synology-apm m365 <scope> change-plan --plan` | *plan resolution* → `M365WorkloadCollection.change_plan()` |
| `synology-apm m365 <scope> version list/get/lock/unlock` | same as the `machine version` rows above, on `M365WorkloadCollection` |
| `synology-apm m365 (exchange\|group) export list` | `<Export>.list(wl)` |
| `synology-apm m365 (exchange\|group) export cancel --id` | `<Export>.list(wl)` → `cancel(activity)` |
| `synology-apm m365 (exchange\|group) export download` (auto-start) | `<Export>.start()` → `ready_to_download=True`: `get_download_url_by_ready_result()`; `ready_to_download=False`: poll `get_activity_by_result()` until downloadable → `get_download_url_by_ready_result(start_result)`; then `APMClient.download_file()` |
| `synology-apm m365 (exchange\|group) export download --id` | `<Export>.list(wl)` → `get_download_url_by_activity()` + `APMClient.download_file()` |
| `synology-apm plan protection list [--category machine\|m365]` | `ProtectionPlanCollection.list(category=)` |
| `synology-apm plan protection get NAME` | `ProtectionPlanCollection.get_by_name(name)` (cross-category keyword search + exact match) |
| `synology-apm plan protection get --id` | `ProtectionPlanCollection.get(plan_id)` |
| `synology-apm plan retirement list` | `RetirementPlanCollection.list()` |
| `synology-apm plan retirement get NAME` | `RetirementPlanCollection.get_by_name()` (keyword search + exact match) |
| `synology-apm plan retirement get --id` | `RetirementPlanCollection.get()` |
| `synology-apm plan tiering list` | `TieringPlanCollection.list()` |
| `synology-apm plan tiering get NAME` | `TieringPlanCollection.get_by_name()` (keyword search + exact match) |
| `synology-apm plan tiering get --id` | `TieringPlanCollection.get()` |
| `synology-apm log activity list` | `LogCollection.list_activity()` (BackupServer resolved via search/direct mode; DP only) |
| `synology-apm log drive list` | `LogCollection.list_drive()` (BackupServer resolved via search/direct mode; DP only) |
| `synology-apm log connection list` | `LogCollection.list_connection()` (BackupServer resolved via search/direct mode; DP only) |
| `synology-apm log system list` | `LogCollection.list_system()` (BackupServer resolved via search/direct mode; DP only) |
