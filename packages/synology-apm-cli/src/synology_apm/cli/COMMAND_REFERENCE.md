# APM CLI — Command Reference

> Corresponding product: Synology ActiveProtect Manager

**Purpose of this document**: companion reference to [`README.md`](README.md) (the CLI design
contract) — the canonical output-format rendering, the per-command behavior contract (what
`--help` and those renders can't show), status/color conventions, and the SDK exception → CLI
error mapping. Read `README.md` first for conventions and the "Adding a New Command" recipe,
which points here only for the specific command a change actually touches. This is reference
material, not a document meant to be read cover to cover.

---

## Table of Contents

- [Output Formats](#output-formats)
- [Detailed Command Specifications](#detailed-command-specifications)
   - [config — Configuration Management](#config--configuration-management)
   - [machine — Device Workload Management](#machine--device-workload-management)
   - [saas — SaaS Application Overview](#saas--saas-application-overview)
   - [m365 — M365 Workload Management](#m365--m365-workload-management)
   - [m365 exchange export / m365 group export — Mailbox PST Export](#m365-exchange-export--m365-group-export--mailbox-pst-export)
   - [gws — GWS Workload Management](#gws--gws-workload-management)
   - [plan protection / retirement / tiering — Plan Management](#plan-protection--retirement--tiering--plan-management)
   - [activity — Activity Log Queries](#activity--activity-log-queries)
   - [infra — Infrastructure Information](#infra--infrastructure-information)
   - [synology-apm-cli log — Backup Server Logs](#synology-apm-cli-log--backup-server-logs)
- [Status and Color Conventions](#status-and-color-conventions)
- [Error Handling](#error-handling)

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

- **Search mode** — positional `<NAME>`: keyword search, then case-insensitive exact match. Machine workloads match on name; M365 workloads match on display name / UPN / group email and are scoped by `-t/--tenant-id` (auto-resolves to the first M365 tenant when omitted, reported on stderr as `(Using tenant: <id>)`); GWS workloads match on display name / email (Shared Drive: drive name) and are scoped by `-d/--domain` (auto-resolves to the first GWS domain when omitted, reported on stderr as `(Using domain: <id>)`). Plan / server / storage / hypervisor commands match on name (or endpoint/address where noted per command).
- **Direct mode** — `--id <ID>`: direct ID lookup. Workload commands additionally require `--namespace <NS>`; every `version` subcommand and the `export` subcommands use `--workload-id` instead — for `version get`/`lock`/`unlock` and `export cancel`/`download`, because `--id` there addresses the version/activity; for `version list`, because it lists every version of the workload, so there's no single version for `--id` to address.

### Get detail block

Canonical example (`synology-apm-cli machine get`, table mode). Workload get commands (`machine get` / `m365 <scope> get` / `gws <scope> get`) share this two-block layout; plan / infra get commands use a similar `Header: <name>` + `─` rule + `Label: value` section layout whose fields are listed per command.

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
> - The workload type label in parentheses is machine-only; M365 and GWS workloads show `<name> (ID: <workload-id>)` without a type label.
> - Simple cancel confirmations (`machine cancel` / `m365 <scope> cancel` / `gws <scope> cancel`) use a shorter variant: header `⚠ Confirm cancel backup?`, `  Workload:  <name> (<type label>)` (no type label for M365/GWS), prompt `  Confirm? [y/N]:`, success line `✓ Backup cancelled: <name>`.

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

> The M365/GWS variant is identical except the Workload line has no type label. The `config set` interactive wizard transcript lives in the [config section](#config--configuration-management).

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

`config set` is an interactive wizard (host → username → password → SSL verify); see its own
docstring for the `--no-input` behavior.

```
$ synology-apm-cli config set

APM host (e.g. apm.corp.com or apm.corp.com:10443): apm.corp.com
Username: admin
Password (leave blank to prompt each time, not saved):
Skip SSL verification? (choose y for self-signed certificates) [y/N]: y

✓ Settings saved to ~/.config/synology-apm/config.toml (profile: default)
```

`config show` never displays the password itself; see its own docstring, and `config clear`'s,
for their keyring-interaction behavior.

**OS Keyring Storage**: a profile's password is stored under a stable, documented
`service`/`username` pair — `synology-apm-cli:<profile>` / `<profile's APM account>` — which
lets a credential be pre-seeded directly (`keyring set synology-apm-cli:lab admin`) without
the interactive wizard; the config file still needs `password_storage = "keyring"` recorded
for the profile.

> **Warning:** if the OS keyring backend is unavailable (e.g. a headless Linux host with no
> Secret Service running), commands needing the password fail with a hint to use
> `APM_PASSWORD` instead.

**Two-factor authentication (TOTP)**: whenever a password is available to test with (and
`--no-input` is not given), `config set` also attempts a real connection; on success it prints
`✓ Connection verified.`. If the account requires a two-factor code, it prompts once (retrying
on an incorrect code, up to 3 attempts) and, on success, registers this device as trusted:

```
$ synology-apm-cli config set --host apm.corp.com --username admin --save-password keyring

...
Two-factor authentication code: 123456
✓ Registered a trusted device for two-factor authentication.

✓ Settings saved to ~/.config/synology-apm/config.toml (profile: default)
✓ Password stored in the OS keyring.
```

`config show` reports the trusted-device status as an additional `2FA device:` line
(`registered` or `(not registered)`). `config clear --forget-device [--profile <name>]`
clears only the registered device (host/username/password untouched, no confirmation prompt) —
use it to force re-verification, e.g. after revoking trusted devices in DSM. This is the *only*
place synology-apm-cli ever handles a two-factor code; every other command that finds no valid
trusted device on file fails with a message pointing back at `config set`, rather than
prompting.

---

### machine — Device Workload Management

Manages device backup Workloads (PC, Physical Server, VM, File Server).

Subcommands that support search mode (`get` / `version list` / `version get` / `version lock` / `version unlock`) by default search only protected Workloads; adding `--retired` searches retired Workloads instead. In direct mode (`--id`/`--workload-id` + `--namespace`), `--retired` has no effect.

`retire`: in search mode, when the name is not found among protected workloads, the CLI
probes retired workloads; if the workload is already retired, the error is `Workload
'<name>' is already retired.` (exit 1) instead of a not-found error.

`change-plan`: see its own docstring for how the target plan type is auto-detected; in search
mode, add `--retired` to look up an already-retired Workload by name.

`version get`: `--id` (Version ID) is optional; the latest version is fetched automatically
if omitted (reported on stderr as `(Using version: <id>, created at <time>)`). Its detail
view reuses the same `Activity Detail` body as `synology-apm-cli activity backup get` (see
[activity backup get](#synology-apm-cli-activity-backup-get)).

---

### saas — SaaS Application Overview

`synology-apm-cli saas list` lists all connected SaaS applications (M365 tenants + GWS
domains). Columns are shared across both categories: `ID` shows the M365 tenant UUID or the
GWS domain (GWS has no separate opaque ID); `Tenant / Domain` shows the M365 tenant's domain
or the GWS domain itself. `--search` filters by name keyword; `--verbose` adds a
`Domain Admin` column (GWS only — blank for M365 rows, which have no equivalent field).

---

### m365 — M365 Workload Management

Manages Microsoft 365 SaaS backup Workloads, divided into six subcommand groups by service
type. Each group supports [Search / Direct mode](#search--direct-mode) addressing, the
standard [Action confirmation flow](#action-confirmation-flow) for destructive operations,
and `version` subcommands for browsing backup history.

| Subcommand | Service Type | Search-mode `<NAME>` matches |
|--------|---------|---------|
| `exchange` | Mailbox (Exchange) | UPN |
| `onedrive` | OneDrive | UPN |
| `chat` | Teams Chat | UPN |
| `group` | Group Exchange | Group mailbox email |
| `sharepoint` | SharePoint Sites | Site name |
| `teams` | Teams Channels | Team name |

Tenant ID auto-resolution (`-t`/`--tenant-id`; see [Search / Direct mode](#search--direct-mode)
and the option's own help) is not required in direct mode. Only `exchange` and `group` support
`export` (mailbox PST export; see the next section).

M365 workloads have no verification concept: `--verify-status` doesn't exist, and `get`/`version`
detail views never show a Verification line.

---

### m365 exchange export / m365 group export — Mailbox PST Export

Applies to the `exchange` and `group` subcommand groups, which share one implementation. Differences:

| Item | exchange | group |
|------|---------|-------|
| Identifier | UPN | Group email |
| `--archive-mailbox` | Supported | Hidden and silently ignored (no archive-mailbox concept for a group mailbox) |

`download`'s two modes (auto-start vs. direct) are described in its own `--help`; the internal
flow once auto-start begins:

1. Resolve the backup version (latest unless `--version-id`) → start the export.
2. If immediately downloadable, download it.
3. Otherwise: with `--no-wait`, print the Activity ID (or a hint to run `export list`) and
   suggest re-running with `--id` to download (exit 0). Without `--no-wait`, poll until
   downloadable; on Ctrl+C, ask whether to cancel the server-side task, then **exit 4 either
   way**. If the export reaches a non-downloadable terminal state (FAILED / CANCELED /
   EXPIRED / DOWNLOADED), print the status and exit 1.
4. Stream the file with a progress bar (stderr); if the local destination file already
   exists, prompt to overwrite (declined → exit 4; `--yes` skips).

Local filenames are auto-generated when `--filename`/`-f` is omitted — see
`_auto_download_filename()` / `_auto_download_filename_by_id()` for the exact templates.

`cancel` requires confirmation unless `--yes` is given (same confirm-then-cancel flow as
`activity backup/restore cancel` — see [Action confirmation flow](#action-confirmation-flow)).
Both `cancel` and `download` support `--quiet`/`-q` to suppress success output (and, for
`download`, the progress bar) for scripting.

---

### gws — GWS Workload Management

Manages Google Workspace SaaS backup Workloads, divided into five subcommand groups by service
type — same addressing, action-confirmation, and `version`-history support as
[m365](#m365--m365-workload-management) above, and the same "no verification concept" rule
(`--verify-status` doesn't exist; `get`/`version` detail views never show a Verification line).
Substitute `-d`/`--domain` for `-t`/`--tenant-id` (domain auto-resolution instead of tenant
auto-resolution, not required in direct mode).

| Subcommand | Service Type | Search-mode `<NAME>` matches |
|--------|---------|---------|
| `mail` | Mail | Email |
| `calendar` | Calendar | Email |
| `contact` | Contact | Email |
| `drive` | Drive | Email |
| `shared-drive` | Shared Drive | Shared Drive name |

Two differences from M365: no `export` sub-app exists for any scope (GWS backups have no
mailbox-export operation), and `shared-drive`'s list/detail identifier column is "Backup User"
(the account currently used to back up the shared drive) instead of "Email".

---

### plan protection / retirement / tiering — Plan Management

Manages Protection Plans, Retirement Plans, and Tiering Plans. `plan protection get` and
`plan retirement get`/`plan tiering get` support the standard [Search / Direct
mode](#search--direct-mode); see `plan protection get`'s own docstring for its cross-category
search scope.

`plan retirement` is the source for the `--plan` parameter of `machine`/`m365 <scope>`/`gws
<scope> retire` and `change-plan` (on an already-retired Workload); `plan tiering` is the
source for `infra server change-plan`. Every plan ID needed for those commands can be listed
with `--verbose` here.

`plan protection get`'s detail view is a section-based text block (Backup Copy Policy /
Backup Policy / Backup Window / Custom Scopes & Schedules); the exact formatting rules for
retention text, the weekly Backup Window grid, and per-task workload-type/OS/scope labels
are defined next to the rendering code in `plan.py` / `_display.py` — read those functions
rather than a hand-transcribed copy here.

---

### activity — Activity Log Queries

Queries backup/restore activity records. `backup list` / `restore list` by default show only in-progress tasks (Ongoing); adding `--history` switches to showing completed historical records.

#### `synology-apm-cli activity backup list`

Filters (repeatable with OR logic within each option, except `--search` which takes a single
keyword; combining different options narrows further): `--status`, `--namespace`, `--search`
(keyword), `--machine-type` (`pc`/`ps`/`vm`/`fs`), `--m365-type` (`exchange`/`onedrive`/`chat`/
`sharepoint`/`teams`/`group`), `--gws-type` (`mail`/`calendar`/`contact`/`drive`/`shared_drive`).
`--machine-type`, `--m365-type`, and `--gws-type` filter server-side, matching only activities
for workloads of that category; `restore list` has no equivalent sub-type filters (the
underlying API doesn't support them — see the SDK design contract's `BEHAVIOR_REFERENCE.md`'s
`RestoreActivityCollection.list()` note).

#### `synology-apm-cli activity backup get`

Standard [Search / Direct mode](#search--direct-mode) dispatch — search mode gets the latest
entry by workload name, direct mode (`--id`) gets by Activity ID. This detail body's shared use across `machine version get` / `m365 <scope> version get` /
`gws <scope> version get` is documented on `print_activity_detail()`'s own docstring
(`_display.py`). Its field set and
each field's conditional-visibility rule are defined next to that function and
`activity.py` — the fields that only appear for certain workload categories (e.g. `Backup
Scope` for machine workloads, `Processed items` for FS/M365) are exactly the kind of detail
worth reading there rather than duplicating here.

#### `synology-apm-cli activity backup cancel` / `activity restore cancel`

Exit code 1 if the activity is not found (or already completed); exit code 4 if the user
declines the confirmation prompt.

#### `synology-apm-cli activity restore get`

Same search/direct dispatch as `activity backup get`. Its detail body additionally shows
Restore Type / Version / Restore from / Destination / Destination path / Destination
hypervisor / Operator — see `RestoreActivity`'s own docstring (SDK) for why the latter two
are never both set.

---

### infra — Infrastructure Information

Manages basic APM Management Server information, the backup server cluster, and remote storage devices.

`infra info` shows site identity, Management Center/Recovery Portal URLs, Primary/Secondary
Management Server health, site-wide storage statistics, and per-workload-type usage.

`infra server change-plan` applies or removes a Tiering Plan on a (DP-only) backup server;
exactly one of `--plan` or `--remove` is required. Follows the standard [action confirmation
flow](#action-confirmation-flow); `--remove` prints an extra warning paragraph about tiering
being stopped (ongoing operations continue; immutable-workload lock durations are adjusted).

`infra storage list` / `infra hypervisor list` return all results in one API call — no
`--limit`/`--offset`/`--page-all`.

---

### `synology-apm-cli log` — Backup Server Logs

Queries the system logs of a specified backup server; see `_run_log_list()`'s own docstring
(`log.py`) for the DP-only server requirement, shared by all four `log * list` commands.
Column sets and the Level/Type → display-string mappings are defined in `log.py` / `_display.py`.
`--verbose` adds the resolved server's raw `Server ID` and `Namespace` as trailing columns
(constant across every row of one invocation, but useful for feeding into `--id`/direct-mode
lookups elsewhere).

---

## Status and Color Conventions

### Status Icons

| Status | Icon | Color | Description |
|------|------|------|------|
| **Workload Backup Result** | | | |
| Success | `✓` | Green | The most recent backup succeeded |
| Failed | `✗` | Red | The most recent backup failed |
| Partial | `⚠` | Yellow | The most recent backup partially succeeded (including M365/GWS WARNING) |
| Canceled | `⊘` | Dim | The most recent backup was canceled |
| No Backups | `—` | Dim | No backup has ever completed (first backup not yet run) |
| Retired | `—` | Dim | Workload is under a Retirement Plan; new backups will no longer be created |
| Waiting for Backup | `⠸` (spinner) | Blue | Backup is queued, waiting to run |
| Backing up (n%) | `⠸` (spinner) | Blue | PC/PS/VM backup in progress (block level) |
| Backing up (n items) | `⠸` (spinner) | Blue | FS/M365/GWS backup in progress (file/item level; n = success + warning + error) |
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
Note: the `(n items)` variant of "Backing up" only applies to a GWS *Workload's* own status; a
GWS backup/restore *Activity* in progress always shows `(n%)` instead (FS and M365 activities
do get item counts).

### Exit Codes

See `packages/synology-apm-cli/README.md`'s "Exit codes" section for the canonical table
(`5` maps from `NotSupportedError`).

---

## Error Handling

### Error Message Format

Errors are printed to stderr; the exit code is returned to the shell (it is not printed):

```
✗ <Short description>
  <Detailed description or suggested action>     ← optional second line
```

An invalid value for any enum-filter option (`--status`, `--type`, `--level`,
`--verify-status`, `--category`, etc.) is validated by the CLI itself, before any SDK call —
`✗ Unsupported <option> value: <value> (available: <comma-separated valid values>)`, exit
code `1`.

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
