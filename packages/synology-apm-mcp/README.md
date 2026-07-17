# synology-apm-mcp

MCP server for [Synology ActiveProtect Manager (APM)](https://www.synology.com/en-global/dsm/feature/active-protect-manager), built on the `synology-apm-sdk`. Exposes APM backup and restore operations, protection plans, M365 workloads, infrastructure, activities, and logs as [Model Context Protocol](https://modelcontextprotocol.io/) tools and resources for LLM agents.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (used as the `uvx` launcher)
- A running APM instance with a valid account
- Python 3.11 or later (installed automatically by `uvx`)

To install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Claude Desktop (Cowork)

**Prerequisite:** the Claude desktop app (Cowork).

1. Open **Customize → Plugins → Add marketplace → Add from a repository**, and enter the URL `synology-apm/apm-sdk-python`.
2. In **Plugins → Personal**, find **Synology APM MCP Server** and click **+** to install.
3. Fill in your APM host, username, password, SSL setting, and operation mode when prompted — Claude Desktop stores them securely and configures the server automatically.

## Claude Desktop (manual config)

Open **Settings → Developer**, click **Edit Config**, and add the following:

```json
{
  "mcpServers": {
    "synology-apm": {
      "command": "/Users/username/.local/bin/uvx",
      "args": ["synology-apm-mcp"],
      "env": {
        "APM_HOST": "apm.corp.com",
        "APM_USERNAME": "admin",
        "APM_PASSWORD": "your-password",
        "APM_MCP_MODE": "operator"
      }
    }
  }
}
```

> **Note:** Claude Desktop does not inherit your shell `PATH`. Use the full path to `uvx`
> (find it with `which uvx` on macOS/Linux). If you have not installed `uv`, see
> [the uv install instructions](https://docs.astral.sh/uv/getting-started/installation/).

For self-signed certificates, add `"APM_NO_VERIFY_SSL": "true"` to the `env` block.

### Using an existing CLI config profile

If you have already configured the `synology-apm` CLI (`synology-apm config set`), you can
reuse that profile instead of providing credentials directly:

```json
{
  "mcpServers": {
    "synology-apm": {
      "command": "/Users/username/.local/bin/uvx",
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

Unlike Claude Desktop, ChatGPT/Codex has no install-time credential prompt — the plugin
installs with a blank template and falls back to an existing CLI config profile
(`APM_PROFILE`). To use it, either:

- Run `synology-apm config set` beforehand so the `default` profile has credentials, or
- Open `~/.codex/config.toml` (shared with the Codex CLI/IDE extension) after installing
  and fill in `[mcp_servers.synology-apm.env]` directly:

```toml
[mcp_servers.synology-apm.env]
APM_HOST = "apm.corp.com"
APM_USERNAME = "admin"
APM_PASSWORD = "your-password"
APM_MCP_MODE = "operator"
```

For self-signed certificates, also set `APM_NO_VERIFY_SSL = "true"`.

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

To record a JSON audit trail of all mutating operations:

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

## Development

```bash
# Install dependencies
uv sync

# Run unit tests
uv run pytest tests/unit/mcp/ -v

# Check SDK ↔ MCP tool coverage: every SDK method resolves and is covered by a
# manifest entry, and every manifest entry resolves and matches a registered tool
uv run python scripts/check_mcp_coverage.py

# Run the server locally (requires .env with APM_HOST, APM_USERNAME, APM_PASSWORD)
source .env && synology-apm-mcp
```
