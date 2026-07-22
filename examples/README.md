# APM SDK — Examples

This directory contains example scripts demonstrating common APM automation patterns
built on the APM Python SDK (`synology_apm.sdk`). Each example is a self-contained,
runnable command-line tool.

## Prerequisites

Install the SDK first — see the [repository README](../README.md#install-the-sdk) for the
available install methods, or [Install From Source (Contributing)](../README.md#install-from-source-contributing)
for an editable install from source.

Some scripts need two more packages: `pyyaml` (YAML config import/export) and `openpyxl`
(XLSX output). Both are dev dependencies already installed by `uv sync`; otherwise:

```bash
pip install pyyaml openpyxl
```

**Create a `.env` file** with your APM credentials — copy `.env.example` at the repository
root and fill in the values (`APM_HOST` / `APM_USERNAME` / `APM_PASSWORD` /
`APM_NO_VERIFY_SSL`). Load it via `uv run`'s built-in `--env-file` support when running a
script (run from the repository root, so the relative `.env` path resolves):

```bash
uv run --env-file .env python examples/workload_inventory.py --category machine
```

Alternatively, if you've already configured a profile with `synology-apm-cli config set`
(`~/.config/synology-apm/config.toml`), the scripts fall back to it automatically when no
`.env`/environment variables are set — no separate setup needed for examples. A script's own
`--profile` flag selects which configured profile to fall back to (see `examples/CLAUDE.md`'s
Script Skeleton section for the full env-var/profile priority order if you're adapting a
script).

## Running an Example

Run a script with `uv run --env-file .env`, passing its path; `--help` shows its full option
list:

```bash
uv run --env-file .env python examples/workload_inventory.py --help
uv run --env-file .env python examples/workload_inventory.py --category machine
```

(Omit `--env-file .env` if you're supplying credentials via real exported environment
variables, a configured profile, or a script's own `--profile` flag instead.)

The tables below show only the script name for readability.

All scripts print progress to `stderr` and machine-readable output (`-o csv` / `-o json`,
where supported) to `stdout`, so `uv run --env-file .env python examples/<name>.py -o csv
> out.csv` captures data cleanly while still showing progress in the terminal.

## Examples by Category

### Workload Inventory & Reporting

| Example | Description |
|---------|-------------|
| [workload_inventory.py](workload_inventory.py) | Print a table/CSV/JSON inventory (name, type, backup server, last backup date, backup status, version count) for Machine and/or M365 workloads |
| [backup_activity_report.py](backup_activity_report.py) | Print a categorized daily backup result summary (succeeded / failed / in-progress / no activity) for Machine and/or M365 workloads |
| [restore_activity_report.py](restore_activity_report.py) | Print a categorized daily restore result summary (succeeded / failed / in-progress) across Machine and M365 workloads |
| [storage_usage_report.py](storage_usage_report.py) | Print a three-section storage usage report: workload usage, backup server usage, and remote storage usage |
| [billing_report.py](billing_report.py) | Print a billing report across three independent dimensions — groups, backup servers, and APM plans — with rates from a YAML pricing config (generate a starter with `--dump-config-template`); supports `--details` per-workload-type breakdowns and multi-sheet XLSX output |

### Backup Operations

| Example | Description |
|---------|-------------|
| [backup_catchup.py](backup_catchup.py) | Find workloads that are overdue for backup (or whose last backup failed, was partial, or was canceled), confirm, then trigger and poll backups to completion |
| [export_verification_videos.py](export_verification_videos.py) | Download backup verification videos for PS/VM workloads whose `verify_status` is `SUCCESS`, writing a CSV report |

### Bulk Import / Export

| Example | Description |
|---------|-------------|
| [apm_import_export.py](apm_import_export.py) | Export and import Protection Plans, Retirement Plans, Tiering Plans, and File Server workloads via YAML; supports `--on-conflict skip\|overwrite` for updates |
| [export_m365_mailbox.py](export_m365_mailbox.py) | Bulk-export M365 Exchange mailboxes or M365 Group mailboxes to PST files, with CSV-based resume support |

## Developing New Examples

`CLAUDE.md` in this directory is a development reference for Coding Agents such as Claude Code.
To write a new example, open a session in the `examples/` directory — `CLAUDE.md` is loaded
automatically and gives the agent the SDK context needed to build new scripts from scratch.
