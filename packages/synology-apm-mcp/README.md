# synology-apm-mcp

MCP server for [Synology ActiveProtect Manager (APM)](https://www.synology.com/en-global/dsm/feature/active-protect-manager), built on the `synology-apm-sdk`. Exposes APM backup and restore operations, protection plans, M365 workloads, infrastructure, activities, and logs as [Model Context Protocol](https://modelcontextprotocol.io/) tools and resources for LLM agents.

## Prerequisites

- uv (used as the `uvx` launcher) — see the
  [installation instructions](https://docs.astral.sh/uv/getting-started/installation/) for your platform
- A running APM instance with a valid account
- Python 3.11 or later (installed automatically by `uvx`)

## Configure APM Credentials

The MCP server does not collect or store APM credentials itself — it connects using a
`synology-apm-cli` config profile. Configure the `default` profile once, before installing
the MCP server in any client — the config file it writes persists on disk, so there is no
need to install the CLI permanently just for this:

```bash
uvx synology-apm-cli config set
```

This walks through host/username/password and writes them to
`~/.config/synology-apm/config.toml`, with the password optionally stored in the OS keyring.
To use a name other than `default`, pass `--profile <name>` and select it with the
`APM_PROFILE` environment variable (see Environment Variables below) or `synology-apm-mcp
--profile <name>`.

## Claude Desktop (Cowork)

**Prerequisite:** the Claude desktop app (Cowork).

1. Open **Customize → Plugins → Add marketplace → Add from a repository**, and enter the URL `synology-apm/apm-sdk-python`.
2. In **Plugins → Personal**, find **Synology APM MCP Server** and click **+** to install — this installs the MCP server and its workflow skills together.

This plugin connects using the `default` `synology-apm-cli` profile and `operator` mode.
Claude Desktop currently has no UI to change plugin settings after install — if you need a
different profile or mode, install manually instead (see Claude Desktop (manual config)
below).

## Claude Desktop (manual config)

Open **Settings → Developer**, click **Edit Config**, and add the following:

```json
{
  "mcpServers": {
    "synology-apm": {
      "command": "uvx",
      "args": ["synology-apm-mcp"],
      "env": {
        "APM_PROFILE": "default",
        "APM_MCP_MODE": "operator"
      }
    }
  }
}
```

## ChatGPT Desktop (Work)

**Prerequisite:** the ChatGPT desktop app (Work).

1. Open **Plugins → Add marketplace**, and enter `synology-apm/apm-sdk-python` as the source.
2. In **Plugins → Personal**, find **synology-apm-mcp** and click **Install**.

The plugin installs with a blank template and connects using the `default`
`synology-apm-cli` profile configured above. To use a different profile, open
`~/.codex/config.toml` (shared with the Codex CLI/IDE extension) after installing and set it
directly:

```toml
[mcp_servers.synology-apm.env]
APM_PROFILE = "default"
APM_MCP_MODE = "operator"
```

## Environment Variables

Reference for every variable the `env` block (see the manual config examples above)
accepts.

`APM_PROFILE`/`APM_HOST`/`APM_USERNAME`/`APM_PASSWORD`/`APM_NO_VERIFY_SSL` are the same
connection settings `synology-apm-cli` resolves:

```bash
APM_PROFILE=lab              # synology-apm-cli config profile to connect with (default: "default")

# Or set connection details directly instead of using a profile:
APM_HOST=apm.corp.com        # Override the profile's host directly (set together with USERNAME/PASSWORD)
APM_USERNAME=admin           # Override the profile's username directly
APM_PASSWORD=secret          # Override the profile's password directly
APM_NO_VERIFY_SSL=true       # Skip SSL verification, overriding the profile's own setting in
                             # either direction (also accepts "false" to force verification
                             # back on over a profile that has it disabled)
```

MCP-specific settings, with no `synology-apm-cli` equivalent:

| Variable | Purpose |
|----------|---------|
| `APM_MCP_MODE` | Controls which tools are registered: `readonly`, `operator` (default), `manager`, or `admin`. See Operation Modes below. |
| `APM_MCP_AUDIT_LOG` | Path to a JSON-lines audit log file recording mutating operations. See Audit Log below. |

## Operation Modes

Set `APM_MCP_MODE` (default: `operator`) to control which tools are available:

| Mode | Tools available |
|------|-----------------|
| `readonly` | Read-only queries: list/get workloads, plans, activities, logs, site info |
| `operator` | Above + trigger backups, cancel activities, manage M365 exports |
| `manager` | Above + lock/unlock backup versions |
| `admin` | Full access: create/update/delete plans, workloads, infrastructure |

Tools not available in the current mode are never registered on the server process: they
are hidden from the agent's `tools/list` response and cannot be invoked via `tools/call`,
regardless of how a client learns their name.

## Audit Log

To record a JSON audit trail of all mutating operations, set `APM_MCP_AUDIT_LOG` to a file
path:

```json
"env": {
  "APM_MCP_AUDIT_LOG": "/var/log/apm-mcp-audit.jsonl",
  ...
}
```

Each line is a JSON object:
```json
{"ts": "2026-07-15T10:30:00Z", "tool": "backup_machine_workload", "params": {"workload_id": "123e4567-e89b-12d3-a456-426614174001"}, "outcome": "ok"}
```

## Troubleshooting

The server always starts, whether or not it can actually reach APM: no connection settings
found at all, an invalid or expired password, an unreachable host, a self-signed
certificate without `APM_NO_VERIFY_SSL` set, or a target that is not the primary APM
management server all print a diagnostic line to stderr (visible when running
`synology-apm-mcp` directly, or in the host application's MCP server logs, e.g. Claude
Desktop's Developer settings) but do not stop the process. Every tool call then returns a
JSON error describing the failure together with a hint to reconfigure: re-run `uvx
synology-apm-cli config set`, fix the `APM_HOST`/`APM_USERNAME`/`APM_PASSWORD`/
`APM_NO_VERIFY_SSL` environment variables directly, or select a different configured profile
via `APM_PROFILE`, then restart the MCP server.

The one case that does exit immediately at startup is an unrecognized `APM_MCP_MODE` value
— that is a deployment misconfiguration, not a credentials problem, and there is no
sensible set of tools to register without knowing which mode was intended.

## Available Tools

The MCP server exposes tools across six domains:

- **Infrastructure**: site info, backup servers, remote storage, hypervisors
- **Machine workloads**: list, get, backup, cancel, versions, lock/unlock, file servers, retire, delete
- **M365 workloads**: list, get, backup, cancel, versions, lock/unlock, exports, auto backup rules, retire, delete
- **Plans**: protection, retirement, and tiering plans — list, get, create, update, delete
- **Activities**: backup and restore activities — list, get, cancel
- **Logs**: activity, drive, connection, and system logs (DP appliances only)

Plus six **MCP resources** for stable, small reference data: `apm://site` (site overview
including workload counts by type), `apm://servers`, `apm://plans/protection`,
`apm://plans/retirement`, `apm://plans/tiering`, `apm://tenants`, and the
`apm://server/{server_id}` resource template. For workload queries use the
`list_machine_workloads` and `list_m365_workloads` tools, which support filtering (including
by backup status, and for Machine workloads also verification status) and pagination. List
resources include `"truncated": true` when the result set exceeds 500 items, indicating that
further pages are available via the corresponding list tool.

## Workflow Skills

When installed via the Claude Desktop (Cowork) or ChatGPT Desktop (Work) plugin,
workflow skills are also available (a manually configured server does not get them):

- **daily-backup-report** — Status report for a time window
- **catch-up-overdue-backups** — Find and trigger catch-up for overdue/failed workloads
- **analyze-storage-capacity** — Capacity planning across all servers and remote storage
- **generate-billing-report** — Compute backup charges per plan, server, workload type, or tenant
- **analyze-restore-activities** — Summarize and investigate restore activity trends
- **workload-inventory** — Full inventory of protected workloads by type and status
- **export-apm-config** — Snapshot of all APM configuration objects
- **investigate-backup-failure** — Root cause analysis for backup failures
- **review-verification-videos** — Check backup-verification status and fetch video links for VM/PS workloads
- **export-m365-mailboxes** — Bulk PST export of Exchange or Group mailboxes
- **provision-apm-config** — Bulk-create or update APM infrastructure/plans from a config description
- **manage-apm-resource** — View, update, or remove a single existing resource
- **manage-running-jobs-and-version-locks** — Cancel a running backup/restore/export job, or lock/unlock a backup version
- **reassign-or-retire-workload** — Move a workload to a different plan, or retire/delete it
- **apm-mcp-conventions** — Shared reference (list-vs-get field completeness, pagination, permission modes, update semantics, destructive action preview pattern) the other skills point to; not a task on its own

For implementers (tool/resource conventions, mode gating, the SDK ↔ MCP coverage manifest, testing
conventions), see the design contract at
[`src/synology_apm/mcp/README.md`](src/synology_apm/mcp/README.md).
