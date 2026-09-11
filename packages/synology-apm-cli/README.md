# APM CLI — Command Reference

Command-line interface for [Synology ActiveProtect Manager (APM)](https://www.synology.com/products/ActiveProtectAppliance).

## Installation

Requires Python 3.11 or later — provisioned automatically if you use `uv`/`uvx` below, otherwise required on your own interpreter for `pip install`.

Run directly without installing, via [`uv`](https://docs.astral.sh/uv/getting-started/installation/)'s `uvx` launcher:

```bash
uvx synology-apm-cli --help
```

Or install with [`pip`](https://pip.pypa.io/en/stable/installation/):

```bash
pip install synology-apm-cli
```

## Authentication

Every command needs a host (hostname or IP, e.g. `apm.corp.com` or `apm.corp.com:10443`), username, and password. `https://` is always used — do not include the scheme. There are three ways to supply connection settings, applied in this priority order (highest first):

### Config file — recommended

Run once, then forget:

```bash
synology-apm-cli config set --host apm.corp.com --username admin
```

For self-signed certificates (lab / dev), answer `y` when the wizard asks `Skip SSL verification?` — or set `APM_NO_VERIFY_SSL=true` / pass the global `--no-verify-ssl` flag per invocation.

By default the password is **not stored** — you will be prompted each time you run a command (or supply it via `APM_PASSWORD`). To store it, add `--save-password plaintext` (config file) or `--save-password keyring` (OS credential store — macOS Keychain / Windows Credential Manager / Linux Secret Service):

```bash
synology-apm-cli config set --host apm.corp.com --username admin --save-password plaintext
synology-apm-cli config set --host apm.corp.com --username admin --save-password keyring
```

> **Warning:** `--save-password plaintext` saves the password in **plain text**. Only use it on
> a trusted machine; prefer `--save-password keyring` or the `APM_PASSWORD` environment variable
> on shared/server machines.

Multiple profiles are supported:

```bash
synology-apm-cli config set --host apm.corp.com --username admin --profile lab
synology-apm-cli config set --host apm2.corp.com --username dr-admin --profile dr
```

View or clear config:

```bash
synology-apm-cli config show                  # show default profile + list all profiles
synology-apm-cli config show --profile lab    # show specific profile
synology-apm-cli config clear                 # clear default profile
synology-apm-cli config clear --profile lab   # clear specific profile
synology-apm-cli config clear --all           # clear all profiles
synology-apm-cli config clear --yes --quiet   # skip confirmation and success output (scripting)
```

Config is stored in `~/.config/synology-apm/config.toml`.

### Two-factor authentication (TOTP)

If the account has two-factor authentication enabled, `config set` also attempts a real
connection (whenever a password is available) and, on first use, prompts once for a
verification code and registers this device as trusted — so future connections (from this CLI
and from `synology-apm-mcp`, if it shares the same profile) skip the code entirely:

```bash
synology-apm-cli config set --host apm.corp.com --username admin --save-password keyring
# Two-factor authentication code: ******
# ✓ Registered a trusted device for two-factor authentication.
```

This is the *only* place a verification code is ever requested — every other command fails
with an actionable message (pointing back at `config set`) if no trusted device is on file, or
the registered one is no longer valid. `config show` reports whether a profile has one
registered; to force re-verification (e.g. after revoking trusted devices in DSM) without
touching the rest of the profile:

```bash
synology-apm-cli config clear --forget-device --profile lab
```

### Environment variables

```bash
export APM_HOST=apm.corp.com
export APM_USERNAME=admin
export APM_PASSWORD=yourpassword   # optional — avoids the interactive prompt
export APM_NO_VERIFY_SSL=true
export APM_PROFILE=lab             # selects a config file profile
```

### Global flags (before the subcommand)

```bash
synology-apm-cli --host apm.corp.com \
    --username admin \
    --password yourpassword \
    --no-verify-ssl \
    machine list
```

Use `--no-input` to disable all interactive prompts (suitable for scripts and CI). If a required value such as the password is missing, the command exits immediately with code 1 instead of hanging:

```bash
APM_HOST=apm.corp.com APM_USERNAME=admin APM_PASSWORD=secret \
  synology-apm-cli --no-input machine list -o json
```

Flags override environment variables, which override the config file.

---

## Output formats

List, get, and info commands support `--output` / `-o` (action commands such as `backup`, `cancel`, and `config set` have no output option):

| Value | Description | Availability |
|-------|-------------|--------------|
| `table` | Default — human-readable Rich table | All commands |
| `json` | JSON — curated fields (nested structure), suitable for piping to `jq` | All commands |
| `yaml` | YAML format | All commands |
| `csv` | Flat CSV — table-aligned columns, raw values (bytes as int, datetime as local-timezone ISO 8601) | `list` commands only |

```bash
synology-apm-cli machine list -o json | jq '.[].name'
synology-apm-cli infra server list -o yaml
synology-apm-cli machine list -o csv > machines.csv
synology-apm-cli activity backup list --since 24h -o csv
```

---

## Debugging

Use `--debug` (before the subcommand) to print every API request and response to stderr. Useful when diagnosing unexpected errors or exploring the raw API.

```bash
synology-apm-cli --debug machine list
```

Output goes to **stderr** so it does not interfere with `--output json` pipelines:

```bash
synology-apm-cli --debug machine list -o json 2>debug.log | jq '.[].name'
```

---

## Commands

### `synology-apm-cli machine`

Manages device backup workloads: PC, Physical Server, VM, and File Server.

Every subcommand below that operates on a single workload accepts it one of two mutually
exclusive ways: **search mode** (a name/keyword positional argument) or **direct mode** (an
exact ID lookup + `--namespace`). Flag support differs per subcommand:

| Subcommand | Direct-mode ID flag | `--retired` | `--yes` | `--quiet` |
|---|---|---|---|---|
| `get` | `--id` | yes | – | – |
| `backup` | `--id` | – | – | yes |
| `cancel` | `--id` | – | yes | yes |
| `retire` | `--id` | – | yes | yes |
| `change-plan` | `--id` | yes | yes | yes |
| `version list` | `--workload-id` | yes | – | – |
| `version get` | `--workload-id` | yes | – | – |
| `version lock` / `unlock` | `--workload-id` | yes | – | yes |

Every `version` subcommand uses `--workload-id` for the workload — `--id` there instead
addresses the Version (`version get`/`lock`/`unlock`'s `--id`) or has no meaning at all
(`version list`, which lists every version of the workload, so there's no single Version to
address).

#### `synology-apm-cli machine list`

```bash
# List all machine workloads (no --type = all types)
synology-apm-cli machine list

# Filter by type (values: pc / ps / vm / fs; --type is repeatable), retirement, or keyword
synology-apm-cli machine list --type vm --type fs --retired --search "prod"
synology-apm-cli machine list --verbose   # add IP Address / Workload ID / Namespace / Plan ID columns

# Other filters (all repeatable except --hypervisor): --namespace <namespace> (backup server),
# --hypervisor <id> (VMs only, single value), --plan <name-or-id>, --status <status>,
# --verify-status <status> (PS/VM only)
synology-apm-cli machine list --status failed --status partial --verify-status not_enabled
```

#### `synology-apm-cli machine get`

```bash
synology-apm-cli machine get "CORP-PC-001"
synology-apm-cli machine get "old" --retired        # only among retired workloads
synology-apm-cli machine get "CORP-PC-001" -o json
synology-apm-cli machine get --id <workload-id> --namespace <namespace>
```

#### `synology-apm-cli machine backup` / `cancel`

`cancel` requires confirmation in both modes, unless `--yes` is passed.

```bash
synology-apm-cli machine backup "CORP-PC-001"
synology-apm-cli machine backup --id <workload-id> --namespace <namespace>

synology-apm-cli machine cancel "CORP-PC-001"
synology-apm-cli machine cancel --id <workload-id> --namespace <namespace> --yes
```

#### `synology-apm-cli machine retire`

Irreversible; requires confirmation in both modes, unless `--yes` is passed. `--plan` is
required — get the ID from `synology-apm-cli plan retirement list --verbose`.

```bash
synology-apm-cli machine retire "CORP-PC-001" --plan <retirement-plan-id>
synology-apm-cli machine retire --id <workload-id> --namespace <namespace> --plan <retirement-plan-id> --yes
```

#### `synology-apm-cli machine change-plan`

`--plan` accepts a plan name or UUID. The plan type it is resolved
against is auto-detected from the workload's current state: a Protection Plan for an active
Workload, a Retirement Plan for an already-retired one (add `--retired` in search mode to look up
a retired Workload by name).

```bash
# Change the Protection Plan of an active Workload (search mode)
synology-apm-cli machine change-plan "CORP-PC-001" --plan "Daily Backup"

# Re-assign the Retirement Plan of an already-retired Workload
synology-apm-cli machine change-plan "old-laptop" --retired --plan "Compliance Retention"

# Direct mode
synology-apm-cli machine change-plan --id <workload-id> --namespace <namespace> --plan <plan-id> --yes
```

#### `synology-apm-cli machine version list`

Lists backup versions. Table columns: #, Created, Status, Locked, Verification (PS/VM only), Changed Size, Copy Status, Locations, Version ID.
Footer shows pagination info (e.g. `Showing 1 of 42`). Use `--verbose` to show Workload ID + Namespace in the header.
Default search mode finds protected workloads; use `--retired` for retired workloads.

```bash
# Search mode
synology-apm-cli machine version list "CORP-PC-001"
synology-apm-cli machine version list "CORP-PC-001" --limit 25 --offset 25   # page 2
synology-apm-cli machine version list "CORP-PC-001" --since 7d --until 2026-04-20T23:59:59  # 30m|1h|24h|7d|ISO 8601
synology-apm-cli machine version list "old-laptop" --retired

# Direct mode
synology-apm-cli machine version list --workload-id <workload-id> --namespace <namespace> --since 7d
```

#### `synology-apm-cli machine version get`

Shows version info (Version ID, Workload ID, Namespace, storage Locations) followed by activity detail (status, timing, Data Change / Transferred / Actual Capacity Used metrics, logs). File Server (FS) activities additionally show `Processed items: N succeeded, N warning, N error`.
`--id` is the Version ID (from `version list`); omit to get the latest version automatically. Default search mode finds protected workloads; use `--retired` for retired workloads.

```bash
# Search mode (omit --id to get the latest version)
synology-apm-cli machine version get "CORP-PC-001"
synology-apm-cli machine version get "old-laptop" --id <version-id> --retired

# Direct mode (skips workload lookup — faster)
synology-apm-cli machine version get --workload-id <workload-id> --namespace <namespace> --id <version-id>
```

#### `synology-apm-cli machine version lock` / `unlock`

Locks a backup version to prevent automatic deletion by retention policies. `--id` (Version ID) is required and comes from `version list`.

```bash
synology-apm-cli machine version lock "CORP-PC-001" --id <version-id>   # unlock: same syntax
synology-apm-cli machine version lock --workload-id <workload-id> --namespace <namespace> --id <version-id>
```

---

### `synology-apm-cli saas`

Lists connected SaaS applications (Microsoft 365 tenants and Google Workspace domains).

```bash
# List all connected SaaS applications (M365 + GWS)
# Output includes: Category, Name, Tenant / Domain, Protected Size, ID
synology-apm-cli saas list

synology-apm-cli saas list --search contoso   # keyword search by name
synology-apm-cli saas list -v                 # also show Domain Admin (GWS only)
```

---

### `synology-apm-cli m365`

Manages Microsoft 365 backup workloads grouped by service type.

Service types: `exchange` | `onedrive` | `chat` | `group` | `sharepoint` | `teams`

The `--tenant-id` / `-t` option selects the M365 tenant: optional in search mode (auto-resolves
to the first M365 tenant from `synology-apm-cli saas list` when omitted), not needed in direct
mode.

The examples below use `exchange`; every other service type has the identical interface (substitute the scope name).

```bash
TENANT="123e4567-e89b-12d3-a456-426614174005"

# List M365 workloads by service type (scopes: exchange / onedrive / chat / group / sharepoint / teams)
# In table mode, tenant name and domain are displayed above the workload table.
synology-apm-cli m365 exchange list                                                  # auto-resolve tenant
synology-apm-cli m365 exchange list -t $TENANT --retired --search "alice" --verbose   # combine filters
synology-apm-cli m365 exchange list --status failed --status partial --namespace <ns1> --namespace <ns2>  # repeatable
synology-apm-cli m365 exchange list -o json                                          # JSON output (no tenant header)

# Inspect a single M365 workload — search mode auto-resolves tenant if -t omitted (add --retired
# to search among retired workloads); direct mode needs no --tenant-id:
synology-apm-cli m365 exchange get "alice@contoso.com"
synology-apm-cli m365 exchange get --id <workload-uid> --namespace <ns>

# Trigger / cancel a manual backup (cancel requires confirmation; --yes skips it)
synology-apm-cli m365 exchange backup "alice@contoso.com"
synology-apm-cli m365 exchange cancel --id <workload-uid> --namespace <ns> --yes

# Retire (irreversible — get the plan ID from `synology-apm-cli plan retirement list --verbose`)
synology-apm-cli m365 exchange retire "alice@contoso.com" --plan <retirement-plan-id>

# Change plan (--plan accepts name or UUID; type auto-detected from the workload's current state)
synology-apm-cli m365 exchange change-plan "alice@contoso.com" --plan "Daily Backup"
synology-apm-cli m365 exchange change-plan "bob@contoso.com" --retired --plan "Compliance Retention"

# List backup versions (Table columns: #, Created, Status, Locked, Changed Size, Copy Status, Locations, Version ID)
synology-apm-cli m365 exchange version list "alice@contoso.com" --limit 25 --offset 25              # page 2
synology-apm-cli m365 exchange version list --workload-id <workload-uid> --namespace <ns> --since 7d  # direct mode

# Show version info + activity detail (omit --id for the latest version); M365 activities
# additionally show "Processed items: N succeeded, N warning, N error"
synology-apm-cli m365 exchange version get "alice@contoso.com"
synology-apm-cli m365 exchange version get --workload-id <workload-uid> --namespace <ns> --id <version-id>

# Lock / unlock a version (--id is required; unlock uses the same syntax)
synology-apm-cli m365 exchange version lock "alice@contoso.com" --id <version-id>

# Export mailbox to PST — list tasks, auto-start + download (waits by default), or act on a
# previously started export by --id
synology-apm-cli m365 exchange export list "alice@contoso.com"
synology-apm-cli m365 exchange export download "alice@contoso.com" --archive-mailbox --version-id <vid> --filename mailbox.pst

# --no-wait returns immediately after starting the export instead of downloading it; re-run
# with --id (and --filename again, if desired) once it's ready
synology-apm-cli m365 exchange export download "alice@contoso.com" --archive-mailbox --version-id <vid> --no-wait
synology-apm-cli m365 exchange export download "alice@contoso.com" --id <activity-id> --filename mailbox.pst --quiet
synology-apm-cli m365 exchange export cancel "alice@contoso.com" --id <activity-id> --yes --quiet

# Group mailbox export: same interface via `m365 group export`, without --archive-mailbox
synology-apm-cli m365 group export download "marketing@contoso.com"
```

---

### `synology-apm-cli gws`

Manages Google Workspace backup workloads grouped by service type.

Service types: `mail` | `calendar` | `contact` | `drive` | `shared-drive`

The `--domain` / `-d` option selects the GWS domain, working the same way `--tenant-id` does
for [`m365`](#synology-apm-cli-m365) above (auto-resolves to the first GWS domain in search
mode when omitted; not needed in direct mode). Command syntax is otherwise identical to
`m365` — substitute the scope name, `-t`/`--tenant-id` for `-d`/`--domain`, and the tenant /
mailbox identifiers for a GWS domain / email address. Two differences:

- No `export` sub-app exists for any scope — GWS backups have no mailbox-export operation.
- `shared-drive` is identified by drive name instead of email.

```bash
DOMAIN="gwsdemo.example.com"

synology-apm-cli gws mail   list                                        # auto-resolve domain
synology-apm-cli gws mail   get "alice@gwsdemo.example.com"
synology-apm-cli gws mail   backup "alice@gwsdemo.example.com"
synology-apm-cli gws mail   change-plan "alice@gwsdemo.example.com" --plan "Daily Backup"
synology-apm-cli gws mail   version list "alice@gwsdemo.example.com"

# Shared Drive: identified by drive name instead of email
synology-apm-cli gws shared-drive get "Marketing Drive"
```

---

### `synology-apm-cli plan`

Manages Protection Plans, Retirement Plans, and Tiering Plans.

#### `synology-apm-cli plan protection`

Lists and inspects backup protection plans. To apply a plan to a workload, use
`synology-apm-cli machine change-plan` / `synology-apm-cli m365 <scope> change-plan` /
`synology-apm-cli gws <scope> change-plan`.

```bash
# List all protection plans (machine, M365, and GWS plans together)
synology-apm-cli plan protection list
synology-apm-cli plan protection list --category machine --search "Daily" -v   # category: machine|m365|gws

# Inspect a plan (search by name or direct by ID)
synology-apm-cli plan protection get "Daily Backup"     # search by name (machine + M365 + GWS)
synology-apm-cli plan protection get --id <plan-id> -o json
```

#### `synology-apm-cli plan retirement`

Manages retirement plans used when retiring workloads. To re-assign a retirement plan
to an already-retired workload, use `synology-apm-cli machine change-plan` /
`synology-apm-cli m365 <scope> change-plan` / `synology-apm-cli gws <scope> change-plan`.

```bash
# List all retirement plans (-v shows Plan ID)
synology-apm-cli plan retirement list --search "30-Day" -v

# Inspect a retirement plan (search by name or direct by ID)
synology-apm-cli plan retirement get "Compliance Retention"
synology-apm-cli plan retirement get --id <plan-id> -o json
```

#### `synology-apm-cli plan tiering`

Lists and inspects tiering plans (version tiering to remote storage).

```bash
# List all tiering plans (-v shows Plan ID)
synology-apm-cli plan tiering list --search "30-Day" -v

# Inspect a tiering plan (search by name or direct by ID)
synology-apm-cli plan tiering get "30-Day Tiering"
synology-apm-cli plan tiering get --id <plan-id> -o json
```

---

### `synology-apm-cli activity backup`

```bash
# List ongoing backup activities (default); --history switches to completed records
synology-apm-cli activity backup list
synology-apm-cli activity backup list --verbose --limit 100          # Transferred/Workload ID/Namespace columns
synology-apm-cli activity backup list --history --limit 25 --offset 25   # page 2 of history

# Filter options (status/machine-type/m365-type/gws-type/namespace are repeatable/OR;
# --since/--until are single-value, not repeatable)
synology-apm-cli activity backup list --status failed --status partial
synology-apm-cli activity backup list --machine-type pc --machine-type vm       # Machine sub-type
synology-apm-cli activity backup list --m365-type exchange --m365-type teams    # M365 service type
synology-apm-cli activity backup list --gws-type mail --gws-type drive         # GWS service type
synology-apm-cli activity backup list --namespace <namespace> --since 7d --until 24h  # since/until: 30m|1h|24h|7d|ISO 8601

# Inspect a single activity (includes log entries) — search mode (latest by workload name) or direct (Activity ID)
synology-apm-cli activity backup get "CORP-PC-001"
synology-apm-cli activity backup get --id <activity-id> -o json

# Cancel a running backup activity (requires confirmation; --yes skips it, --quiet suppresses output)
synology-apm-cli activity backup cancel --id <activity-id> --yes --quiet
```

> **Tip:** Activity ID is shown by default in `synology-apm-cli activity backup list`. Use it with `synology-apm-cli activity backup get` and `synology-apm-cli activity backup cancel`.

---

### `synology-apm-cli activity restore`

```bash
# List ongoing restore activities (default); --history switches to completed records
# (no --machine-type / --m365-type / --gws-type filters here, unlike activity backup)
synology-apm-cli activity restore list
synology-apm-cli activity restore list --verbose --history --limit 25 --offset 25   # page 2 of history

# Filter options (--status is repeatable/OR; --since/--until are single-value, not repeatable)
synology-apm-cli activity restore list --status success --status failed --since 7d --until 24h

# Inspect a single restore activity (includes log entries) — search mode or direct (Activity ID)
synology-apm-cli activity restore get "CORP-PC-001"
synology-apm-cli activity restore get --id <activity-id> -o json

# Cancel a running restore activity (requires confirmation; --yes skips it, --quiet suppresses output)
synology-apm-cli activity restore cancel --id <activity-id> --yes --quiet
```

> **Tip:** Activity ID is shown in `synology-apm-cli activity restore list`. The cancel command automatically looks up the activity details needed to call the API.

---

### `synology-apm-cli infra`

Infrastructure information: Management Server details and backup server management.

```bash
# Show Management Server info, storage statistics, and workload usage summary
# (like all get/info commands, supports -o json / -o yaml)
synology-apm-cli infra info

# List / inspect backup servers — filters: --search, --status (repeatable), --type dp|nas (repeatable), --verbose
synology-apm-cli infra server list --status healthy --status warning --type dp --verbose
synology-apm-cli infra server get "apm-server-01"       # Search mode (keyword search)
synology-apm-cli infra server get --id <server-id>      # Direct mode (exact lookup by Server ID)

# List / inspect remote storages (External Vaults) — Usage column: "442.8 KB (341.8 GB left)" / "442.8 KB" / "-"
synology-apm-cli infra storage list --verbose               # add Remote Storage ID column
synology-apm-cli infra storage get "DSM-Storage"        # Search mode (display name or endpoint)
synology-apm-cli infra storage get --id <storage-id>    # Direct mode (exact lookup by Remote Storage UUID)

# List / inspect hypervisor inventory servers
synology-apm-cli infra hypervisor list --verbose               # add Hypervisor ID column
synology-apm-cli infra hypervisor get "esxi1.example.com"    # Search mode (hostname or address)
synology-apm-cli infra hypervisor get --id <hypervisor-id>   # Direct mode (exact lookup by Hypervisor UUID)
```

---

### `synology-apm-cli log`

Query server-scoped logs from a specific DP (ActiveProtect Appliance) backup server. All log commands require identifying the target backup server (by name or server ID). NAS servers are not supported.

Get the Server ID with: `synology-apm-cli infra server list --verbose`

```bash
# Activity logs — search mode (server name keyword) or direct mode (server ID, from
# `synology-apm-cli infra server list --verbose`)
synology-apm-cli log activity list "apm-server-01" --level warning --level error --type protection --since 24h
synology-apm-cli log activity list --id <server-id> --limit 100 -o json     # direct mode, JSON output
synology-apm-cli log activity list --id <server-id> --limit 100 -v         # -v adds Server ID/Namespace columns (table output only)

# Drive information logs
synology-apm-cli log drive list "apm-server-01" --level error --since 30d
synology-apm-cli log drive list --id <server-id> --location "Slot 1" -o csv

# Connection logs
synology-apm-cli log connection list "apm-server-01" --search "signed in" --since 24h --level warning --level error

# Advanced system logs
synology-apm-cli log system list --id <server-id> --since 7d -o json

# Pagination (same --limit/--offset pattern as other list commands)
synology-apm-cli log activity list --id <server-id> --limit 25 --offset 25
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error (API error, invalid argument) |
| `2` | Authentication failure (bad credentials, session expired) |
| `3` | Connection failure (host unreachable, TLS error) |
| `4` | Cancelled by user (declined a confirmation prompt, Ctrl+C while one is shown, or Ctrl+C during an interruptible wait such as `export download` polling) |
| `5` | Feature not supported on this APM version |

---

## Examples

### Daily backup health check

```bash
#!/bin/bash
# Print any workloads that failed backup in the last 24 hours
# (--history: failed runs are in the completed history, not the ongoing list)
synology-apm-cli activity backup list --history --status failed --since 24h -o json \
  | jq -r '.[].workload_name'
```

### Apply a plan to all retired VMs

```bash
synology-apm-cli machine list --type vm --retired -o json \
  | jq -r '.[] | "\(.workload_id) \(.namespace)"' \
  | while read -r id ns; do
      synology-apm-cli machine change-plan --id "$id" --namespace "$ns" --plan <plan-id> --yes
    done
```

### Pipe backup server storage stats to another tool

```bash
synology-apm-cli infra server list -o json \
  | jq '[.[] | {name, used: .storage_used_bytes, total: .storage_total_bytes}]'
```

### Use a specific profile

```bash
# Run a command against a non-default profile
synology-apm-cli --profile lab machine list
synology-apm-cli --profile dr infra server list -o json
```
