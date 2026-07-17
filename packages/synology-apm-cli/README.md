# APM CLI — Command Reference

Command-line interface for [Synology ActiveProtect Manager (APM)](https://www.synology.com/products/ActiveProtectAppliance).

## Installation

Requires Python 3.11 or later.

```bash
pip install synology-apm-cli
```

## Authentication

Every command needs a host (hostname or IP, e.g. `apm.corp.com` or `apm.corp.com:10443`), username, and password. `https://` is always used — do not include the scheme. There are three ways to supply connection settings, applied in this priority order (highest first):

### Config file — recommended

Run once, then forget:

```bash
synology-apm config set --host apm.corp.com --username admin
```

For self-signed certificates (lab / dev), answer `y` when the wizard asks `Skip SSL verification?` — or set `APM_NO_VERIFY_SSL=true` / pass the global `--no-verify-ssl` flag per invocation.

By default the password is **not stored** — you will be prompted each time you run a command (or supply it via `APM_PASSWORD`). To store it, add `--save-password plaintext` (config file) or `--save-password keyring` (OS credential store — macOS Keychain / Windows Credential Manager / Linux Secret Service):

```bash
synology-apm config set --host apm.corp.com --username admin --save-password plaintext
synology-apm config set --host apm.corp.com --username admin --save-password keyring
```

> **Warning:** `--save-password plaintext` saves the password in **plain text** in `~/.config/synology-apm/config.toml`. Only use it on a trusted machine; prefer `--save-password keyring` or the `APM_PASSWORD` environment variable on shared/server machines.

Multiple profiles are supported:

```bash
synology-apm config set --host apm.corp.com --username admin --profile lab
synology-apm config set --host apm2.corp.com --username dr-admin --profile dr
```

View or clear config:

```bash
synology-apm config show                  # show default profile + list all profiles
synology-apm config show --profile lab    # show specific profile
synology-apm config clear                 # clear default profile
synology-apm config clear --profile lab   # clear specific profile
synology-apm config clear --all           # clear all profiles
```

Config is stored in `~/.config/synology-apm/config.toml`.

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
synology-apm --host apm.corp.com \
    --username admin \
    --password yourpassword \
    --no-verify-ssl \
    machine list
```

Use `--no-input` to disable all interactive prompts (suitable for scripts and CI). If a required value such as the password is missing, the command exits immediately with code 1 instead of hanging:

```bash
APM_HOST=apm.corp.com APM_USERNAME=admin APM_PASSWORD=secret \
  synology-apm --no-input machine list -o json
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
synology-apm machine list -o json | jq '.[].name'
synology-apm infra server list -o yaml
synology-apm machine list -o csv > machines.csv
synology-apm activity backup list --since 24h -o csv
```

---

## Debugging

Use `--debug` (before the subcommand) to print every API request and response to stderr. Useful when diagnosing unexpected errors or exploring the raw API.

```bash
synology-apm --debug machine list
synology-apm --debug infra server get --id <server-id>
synology-apm --debug m365 exchange list -t $TENANT
```

Output goes to **stderr** so it does not interfere with `--output json` pipelines:

```bash
synology-apm --debug machine list -o json 2>debug.log | jq '.[].name'
```

---

## Commands

### `synology-apm machine`

Manages device backup workloads: PC, Physical Server, VM, and File Server.

#### `synology-apm machine list`

```bash
# List all machine workloads (no --type = all types)
synology-apm machine list

# Filter by type (values: pc / ps / vm / fs; --type is repeatable)
synology-apm machine list --type vm                    # Virtual Machines only
synology-apm machine list --type vm --type fs          # VMs and File Servers

# Additional filters (default shows protected workloads only)
synology-apm machine list --retired                    # only retired workloads
synology-apm machine list --search "prod"              # name keyword search
synology-apm machine list --type vm --retired          # combine type + retired filter
synology-apm machine list --verbose                    # add IP Address / Workload ID / Namespace columns

# Filter by backup status / verification status (both repeatable)
synology-apm machine list --status failed --status partial
synology-apm machine list --verify-status not_enabled   # PS/VM only
```

#### `synology-apm machine get`

Two modes (mutually exclusive):
- **Search mode** — find by name (keyword search, default: protected workloads only)
- **Direct mode** — exact lookup, requires both `--id` and `--namespace`

```bash
# Search by name
synology-apm machine get "CORP-PC-001"
synology-apm machine get "old" --retired        # only among retired workloads
synology-apm machine get "CORP-PC-001" -o json

# Direct lookup
synology-apm machine get --id <workload-id> --namespace <namespace>
synology-apm machine get --id <workload-id> --namespace <namespace> -o json
```

#### `synology-apm machine backup`

Two modes (mutually exclusive):

```bash
# Search mode — find by name, then backup
synology-apm machine backup "CORP-PC-001"
synology-apm machine backup "CORP-PC-001" --quiet    # no output (scripts)

# Direct mode — exact lookup by ID + namespace
synology-apm machine backup --id <workload-id> --namespace <namespace>
```

#### `synology-apm machine cancel`

Two modes (mutually exclusive):

```bash
# Search mode (requires confirmation)
synology-apm machine cancel "CORP-PC-001"
synology-apm machine cancel "CORP-PC-001" --yes          # skip confirmation
synology-apm machine cancel "CORP-PC-001" --yes --quiet  # skip confirmation + no output

# Direct mode
synology-apm machine cancel --id <workload-id> --namespace <namespace>
synology-apm machine cancel --id <workload-id> --namespace <namespace> --yes
```

#### `synology-apm machine retire`

Two modes (mutually exclusive). `--plan` is required — get the ID from `synology-apm plan retirement list --verbose`.

```bash
# Search mode (irreversible — requires confirmation)
synology-apm machine retire "CORP-PC-001" --plan <retirement-plan-id>
synology-apm machine retire "CORP-PC-001" --plan <retirement-plan-id> --yes
synology-apm machine retire "CORP-PC-001" --plan <retirement-plan-id> --yes --quiet  # no output

# Direct mode
synology-apm machine retire --id <workload-id> --namespace <namespace> --plan <retirement-plan-id>
synology-apm machine retire --id <workload-id> --namespace <namespace> --plan <retirement-plan-id> --yes
```

#### `synology-apm machine change-plan`

Two modes (mutually exclusive). `--plan` accepts a plan name or UUID. The plan type it is resolved
against is auto-detected from the workload's current state: a Protection Plan for an active
Workload, a Retirement Plan for an already-retired one (add `--retired` in search mode to look up
a retired Workload by name).

```bash
# Change the Protection Plan of an active Workload (search mode)
synology-apm machine change-plan "CORP-PC-001" --plan "Daily Backup"

# Re-assign the Retirement Plan of an already-retired Workload
synology-apm machine change-plan "old-laptop" --retired --plan "Compliance Retention"

# Direct mode
synology-apm machine change-plan --id <workload-id> --namespace <namespace> --plan <plan-id> --yes
```

#### `synology-apm machine version list`

Lists backup versions. Table columns: #, Created, Status, Locked, Verification (PS/VM only), Changed Size, Copy Status, Locations, Version ID.
Footer shows pagination info (e.g. `Showing 1 of 42`). Use `--verbose` to show Workload ID + Namespace in the header.
Default search mode finds protected workloads; use `--retired` for retired workloads.

```bash
# Search mode
synology-apm machine version list "CORP-PC-001"
synology-apm machine version list "CORP-PC-001" --limit 25 --offset 25   # page 2
synology-apm machine version list "CORP-PC-001" --since 7d       # 30m | 1h | 24h | 7d | ISO 8601
synology-apm machine version list "CORP-PC-001" --since 2026-04-01T00:00:00
synology-apm machine version list "CORP-PC-001" --until 2026-04-20T23:59:59
synology-apm machine version list "old-laptop" --retired

# Direct mode
synology-apm machine version list --id <workload-id> --namespace <namespace>
synology-apm machine version list --id <workload-id> --namespace <namespace> --since 7d
```

#### `synology-apm machine version get`

Shows version info (Version ID, Workload ID, Namespace, storage Locations) followed by activity detail (status, timing, Data Change / Transferred / Actual Capacity Used metrics, logs). M365 activities additionally show `Processed items: N succeeded, N warning, N error`.
`--id` is the Version ID (from `version list`); omit to get the latest version automatically. Default search mode finds protected workloads; use `--retired` for retired workloads.

```bash
# Search mode (omit --id to get the latest version)
synology-apm machine version get "CORP-PC-001"
synology-apm machine version get "CORP-PC-001" --id <version-id>
synology-apm machine version get "old-laptop" --id <version-id> --retired

# Direct mode (skips workload lookup — faster)
synology-apm machine version get --workload-id <workload-id> --namespace <namespace>
synology-apm machine version get --workload-id <workload-id> --namespace <namespace> --id <version-id>
```

#### `synology-apm machine version lock` / `unlock`

Locks a backup version to prevent automatic deletion by retention policies. `--id` (Version ID) is required and comes from `version list`.

```bash
# Search mode
synology-apm machine version lock "CORP-PC-001" --id <version-id>
synology-apm machine version lock "CORP-PC-001" --id <version-id> --quiet  # no output
synology-apm machine version unlock "CORP-PC-001" --id <version-id>

# Direct mode
synology-apm machine version lock --workload-id <workload-id> --namespace <namespace> --id <version-id>
synology-apm machine version unlock --workload-id <workload-id> --namespace <namespace> --id <version-id>
```

---

### `synology-apm saas`

Lists connected SaaS tenants (Microsoft 365 and Google Workspace).

```bash
# List all connected SaaS tenants (M365 + GWS)
# Output includes: Category, Name, Email/Domain, Protected Size, Tenant ID
synology-apm saas list
```

---

### `synology-apm m365`

Manages Microsoft 365 backup workloads grouped by service type.

Service types: `exchange` | `onedrive` | `chat` | `group` | `sharepoint` | `teams`

The `--tenant-id` / `-t` option selects the M365 tenant. For `list`, and for `get`/`backup`/`cancel`/`retire` in search mode, it is optional — if omitted, the first M365 tenant from `synology-apm saas list` is used automatically. For direct mode (`--id + --namespace`), it is not needed.

The examples below use `exchange`; every other service type has the identical interface (substitute the scope name).

```bash
TENANT="123e4567-e89b-12d3-a456-426614174005"

# List M365 workloads by service type
# In table mode, tenant name and domain are displayed above the workload table.
synology-apm m365 exchange   list                       # auto-resolve tenant
synology-apm m365 exchange   list -t $TENANT            # scopes: exchange / onedrive / chat / group / sharepoint / teams
synology-apm m365 exchange   list --retired             # only retired workloads (default: protected)
synology-apm m365 exchange   list --search "alice"      # name/email keyword search
synology-apm m365 exchange   list --verbose             # add Workload ID / Namespace columns
synology-apm m365 exchange   list -o json               # JSON output (no tenant header)
synology-apm m365 exchange   list --status failed --status partial  # repeatable

# Inspect a single M365 workload — two modes:
# Search mode (find by name/email/URL — auto-resolves tenant if -t omitted; add --retired
# to search among retired workloads):
synology-apm m365 exchange get "alice@contoso.com"
# Direct mode (requires workload uid + namespace — no --tenant-id needed):
synology-apm m365 exchange get --id <workload-uid> --namespace <ns>

# Trigger / cancel a manual backup (cancel requires confirmation; --yes skips it)
synology-apm m365 exchange backup "alice@contoso.com"
synology-apm m365 exchange cancel "alice@contoso.com"
synology-apm m365 exchange cancel --id <workload-uid> --namespace <ns> --yes

# Retire an M365 workload (irreversible — use synology-apm plan retirement list --verbose to get plan ID)
synology-apm m365 exchange retire "alice@contoso.com" --plan <retirement-plan-id>

# Change the plan assigned to an M365 workload (--plan accepts plan name or UUID; the plan type
# it is resolved against is auto-detected from the workload's current state)
synology-apm m365 exchange change-plan "alice@contoso.com" --plan "Daily Backup"
synology-apm m365 exchange change-plan "bob@contoso.com" --retired --plan "Compliance Retention"

# List backup versions (Table columns: #, Created, Status, Locked, Changed Size, Copy Status, Locations, Version ID)
synology-apm m365 exchange version list "alice@contoso.com"
synology-apm m365 exchange version list "alice@contoso.com" --limit 25 --offset 25   # page 2
synology-apm m365 exchange version list --id <workload-uid> --namespace <ns> --since 7d  # direct mode

# Show version info and activity detail (omit --id to get the latest version)
synology-apm m365 exchange version get "alice@contoso.com"
synology-apm m365 exchange version get --workload-id <workload-uid> --namespace <ns> --id <version-id>

# Lock / unlock a version (--id is required)
synology-apm m365 exchange version lock "alice@contoso.com" --id <version-id>
synology-apm m365 exchange version unlock "alice@contoso.com" --id <version-id>

# Export mailbox to PST — list export tasks
synology-apm m365 exchange export list "alice@contoso.com"

# Export mailbox to PST — start export and download (auto-start mode)
# Starts a new export for the latest version and downloads when ready
synology-apm m365 exchange export download "alice@contoso.com"
synology-apm m365 exchange export download "alice@contoso.com" --archive-mailbox      # export archive mailbox instead
synology-apm m365 exchange export download "alice@contoso.com" --version-id <vid>     # specify a backup version
synology-apm m365 exchange export download "alice@contoso.com" --no-wait              # print Activity ID if available, exit immediately
synology-apm m365 exchange export download "alice@contoso.com" --filename mailbox.pst # custom local filename

# Export mailbox to PST — download a previously started export (direct download mode)
synology-apm m365 exchange export download "alice@contoso.com" --id <activity-id>

# Export mailbox to PST — cancel an in-progress export task
synology-apm m365 exchange export cancel "alice@contoso.com" --id <activity-id>

# Group mailbox export: same interface via `m365 group export`, without --archive-mailbox
synology-apm m365 group export download "marketing@contoso.com"
```

---

### `synology-apm plan`

Manages Protection Plans, Retirement Plans, and Tiering Plans.

#### `synology-apm plan protection`

Lists and inspects backup protection plans. To apply a plan to a workload, use
`synology-apm machine change-plan` / `synology-apm m365 <scope> change-plan`.

```bash
# List all protection plans (machine + M365 in a single API call)
synology-apm plan protection list
synology-apm plan protection list --category machine   # Machine plans only
synology-apm plan protection list --category m365      # M365 plans only
synology-apm plan protection list --search "Daily"
synology-apm plan protection list -v               # show Description and Plan ID columns

# Inspect a plan (search by name or direct by ID)
synology-apm plan protection get "Daily Backup"              # search by name (machine + M365)
synology-apm plan protection get --id <plan-id>              # direct by UUID
synology-apm plan protection get --id <plan-id> -o json
```

#### `synology-apm plan retirement`

Manages retirement plans used when retiring workloads. To re-assign a retirement plan
to an already-retired workload, use `synology-apm machine change-plan` /
`synology-apm m365 <scope> change-plan`.

```bash
# List all retirement plans (-v shows Plan ID)
synology-apm plan retirement list
synology-apm plan retirement list --search "30-Day"
synology-apm plan retirement list -v           # show Plan ID column

# Inspect a retirement plan (search by name or direct by ID)
synology-apm plan retirement get "Compliance Retention"      # search by name
synology-apm plan retirement get --id <plan-id>              # direct by UUID
synology-apm plan retirement get --id <plan-id> -o json
```

#### `synology-apm plan tiering`

Lists and inspects tiering plans (version tiering to remote storage).

```bash
# List all tiering plans (-v shows Plan ID)
synology-apm plan tiering list
synology-apm plan tiering list --search "30-Day"
synology-apm plan tiering list -v                  # show Plan ID column

# Inspect a tiering plan (search by name or direct by ID)
synology-apm plan tiering get "30-Day Tiering"               # search by name
synology-apm plan tiering get --id <plan-id>                 # direct by UUID
synology-apm plan tiering get --id <plan-id> -o json
```

---

### `synology-apm activity backup`

```bash
# List ongoing backup activities (default)
synology-apm activity backup list
synology-apm activity backup list --limit 100
synology-apm activity backup list --verbose              # add Transferred, Workload ID, and Workload Namespace columns (Activity ID shown by default)

# View completed history
synology-apm activity backup list --history
synology-apm activity backup list --history --limit 25 --offset 25         # page 2

# Filter options
synology-apm activity backup list --status backing_up                      # single status filter
synology-apm activity backup list --status failed --status partial          # multiple statuses (OR)
synology-apm activity backup list --machine-type vm                        # Machine sub-type filter
synology-apm activity backup list --machine-type pc --machine-type vm      # multiple sub-types (OR)
synology-apm activity backup list --m365-type exchange --m365-type teams   # M365 service type filter
synology-apm activity backup list --since 24h           # 30m | 1h | 24h | 7d | ISO 8601
synology-apm activity backup list --until 7d           # relative time also supported for --until
synology-apm activity backup list --until 2026-04-20T23:59:59

# Inspect a single activity (includes log entries)
# Search mode — latest activity for a workload by name
synology-apm activity backup get "CORP-PC-001"
synology-apm activity backup get "Corp Share" -o json
# Direct mode — exact lookup by Activity ID
synology-apm activity backup get --id <activity-id>
synology-apm activity backup get --id <activity-id> -o json

# Cancel a running backup activity (requires confirmation)
synology-apm activity backup cancel --id <activity-id>
synology-apm activity backup cancel --id <activity-id> --yes          # skip confirmation
synology-apm activity backup cancel --id <activity-id> --yes --quiet  # no output
```

> **Tip:** Activity ID is shown by default in `synology-apm activity backup list`. Use it with `synology-apm activity backup get` and `synology-apm activity backup cancel`.

---

### `synology-apm activity restore`

```bash
# List ongoing restore activities (default)
synology-apm activity restore list
synology-apm activity restore list --limit 50
synology-apm activity restore list --verbose                          # add Transferred, Workload ID, and Workload Namespace columns

# View completed history
synology-apm activity restore list --history
synology-apm activity restore list --history --limit 25 --offset 25         # page 2

# Filter options (restore list has no --machine-type / --m365-type filters)
synology-apm activity restore list --status restoring                       # single status filter
synology-apm activity restore list --status success --status failed         # multiple statuses (OR)
synology-apm activity restore list --since 24h
synology-apm activity restore list --until 7d           # relative time supported
synology-apm activity restore list --until 2026-04-20T23:59:59

# Inspect a single restore activity (includes log entries)
# Search mode — latest restore activity for a workload by name
synology-apm activity restore get "CORP-PC-001"
# Direct mode — exact lookup by Activity ID
synology-apm activity restore get --id <activity-id>
synology-apm activity restore get --id <activity-id> -o json

# Cancel a running restore activity (requires confirmation)
synology-apm activity restore cancel --id <activity-id>
synology-apm activity restore cancel --id <activity-id> --yes          # skip confirmation
synology-apm activity restore cancel --id <activity-id> --yes --quiet  # no output
```

> **Tip:** Activity ID is shown in `synology-apm activity restore list`. The cancel command automatically looks up the activity details needed to call the API.

---

### `synology-apm infra`

Infrastructure information: Management Server details and backup server management.

```bash
# Show Management Server info, storage statistics, and workload usage summary
# (like all get/info commands, supports -o json / -o yaml)
synology-apm infra info

# List all backup servers in the cluster
synology-apm infra server list
synology-apm infra server list --search "Lab"           # keyword search
synology-apm infra server list --status disconnected    # filter by status
synology-apm infra server list --status healthy --status warning  # multiple values allowed
synology-apm infra server list --type dp                # filter by server type (dp or nas)
synology-apm infra server list --type dp --type nas     # multiple values allowed
synology-apm infra server list --verbose                # add Description, Server ID, Namespace columns

# Inspect a single backup server — two modes:
synology-apm infra server get "apm-server-01"       # Search mode (keyword search)
synology-apm infra server get --id <server-id>      # Direct mode (exact lookup by Server ID)

# List all remote storages (External Vaults)
# Usage column: "442.8 KB (341.8 GB left)" / "442.8 KB" / "-"
synology-apm infra storage list
synology-apm infra storage list --verbose               # add Remote Storage ID column

# Inspect a single remote storage — two modes:
synology-apm infra storage get "DSM-Storage"        # Search mode (display name or endpoint)
synology-apm infra storage get --id <storage-id>    # Direct mode (exact lookup by Remote Storage UUID)

# List all hypervisor inventory servers
synology-apm infra hypervisor list
synology-apm infra hypervisor list --verbose               # add Hypervisor ID column

# Inspect a single hypervisor — two modes:
synology-apm infra hypervisor get "esxi1.example.com"    # Search mode (hostname or address)
synology-apm infra hypervisor get --id <hypervisor-id>   # Direct mode (exact lookup by Hypervisor UUID)
```

---

### `synology-apm log`

Query server-scoped logs from a specific DP (ActiveProtect Appliance) backup server. All log commands require identifying the target backup server (by name or server ID). NAS servers are not supported.

Get the Server ID with: `synology-apm infra server list --verbose`

```bash
# Activity logs — search mode (server name keyword)
synology-apm log activity list "apm-server-01"
synology-apm log activity list "apm-server-01" --level warning --level error
synology-apm log activity list "apm-server-01" --type protection --since 24h
synology-apm log activity list "apm-server-01" --search "copy destination"

# Activity logs — direct mode (server ID from synology-apm infra server list --verbose)
synology-apm log activity list --id <server-id>
synology-apm log activity list --id <server-id> --since 7d --limit 100 -o json

# Drive information logs
synology-apm log drive list "apm-server-01"
synology-apm log drive list "apm-server-01" --level error --since 30d
synology-apm log drive list --id <server-id> --location "Slot 1"
synology-apm log drive list --id <server-id> -o csv

# Connection logs
synology-apm log connection list "apm-server-01"
synology-apm log connection list "apm-server-01" --search "signed in" --since 24h
synology-apm log connection list --id <server-id> --level warning --level error

# Advanced system logs
synology-apm log system list "apm-server-01"
synology-apm log system list --id <server-id> --since 7d -o json

# Pagination
synology-apm log activity list --id <server-id> --limit 25 --offset 25
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error (API error, invalid argument) |
| `2` | Authentication failure (bad credentials, session expired) |
| `3` | Connection failure (host unreachable, TLS error) |
| `4` | Cancelled by user (confirmation prompt answered no) |
| `5` | Feature not supported on this APM version |

---

## Examples

### Daily backup health check

```bash
#!/bin/bash
# Print any workloads that failed backup in the last 24 hours
# (--history: failed runs are in the completed history, not the ongoing list)
synology-apm activity backup list --history --status failed --since 24h -o json \
  | jq -r '.[].workload_name'
```

### Apply a plan to all retired VMs

```bash
synology-apm machine list --type vm --retired -o json \
  | jq -r '.[] | "\(.workload_id) \(.namespace)"' \
  | while read -r id ns; do
      synology-apm machine change-plan --id "$id" --namespace "$ns" --plan <plan-id> --yes
    done
```

### Pipe backup server storage stats to another tool

```bash
synology-apm infra server list -o json \
  | jq '[.[] | {name, used: .storage_used_bytes, total: .storage_total_bytes}]'
```

### Use a specific profile

```bash
# Run a command against a non-default profile
synology-apm --profile lab machine list
synology-apm --profile dr infra server list -o json
```
