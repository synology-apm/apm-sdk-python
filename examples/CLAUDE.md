# CLAUDE.md — APM Examples Development Guide

This file provides context for Claude Code when helping write scripts that use the APM Python SDK (`synology_apm.sdk`).

---

## APMClient Setup

```python
from synology_apm.sdk import APMClient

# host is hostname or IP only — no https:// prefix; SDK prepends it internally
# supports host:port format
async with APMClient("apm.corp.com", "admin", "password", verify_ssl=False) as apm:
    ...
```

Read credentials from environment / `.env`:

```python
from dotenv import load_dotenv
import os

load_dotenv()
host     = os.environ["APM_HOST"]
username = os.environ["APM_USERNAME"]
password = os.environ["APM_PASSWORD"]
no_ssl   = os.environ.get("APM_NO_VERIFY_SSL", "").lower() == "true"

async with APMClient(host, username, password, verify_ssl=not no_ssl) as apm:
    ...
```

`.env` format:
```ini
APM_HOST=apm.corp.com
APM_USERNAME=admin
APM_PASSWORD=yourpassword
APM_NO_VERIFY_SSL=true
```

---

## Shared helpers (`_common.py`)

Example scripts must reuse `_common.py` instead of re-implementing boilerplate; read the file directly to see what's available. Add new helpers there rather than copying into individual scripts.

Raw `list()` returns only one page (default 500 items); use `paginate()` or the `collect_*` helpers from `_common.py` to retrieve all records.

---

## Collection Map

| Attribute | Type | Description |
|-----------|------|-------------|
| `apm.machine.workloads` | `MachineWorkloadCollection` | PC / PS / VM / FS workloads |
| `apm.machine.plans` | `MachinePlanCollection` | Protection plans for machine workloads |
| `apm.m365.workloads` | `M365WorkloadCollection` | Microsoft 365 workloads |
| `apm.m365.plans` | `M365PlanCollection` | Protection plans for M365 workloads |
| `apm.activities.backup` | `BackupActivityCollection` | Backup activity records |
| `apm.activities.restore` | `RestoreActivityCollection` | Restore activity records |
| `apm.backup_servers` | `BackupServerCollection` | Backup server list |
| `apm.hypervisors` | `HypervisorCollection` | Hypervisor inventory servers |
| `apm.remote_storages` | `RemoteStorageCollection` | Remote storage targets |
| `apm.retirement_plans` | `RetirementPlanCollection` | Archive / retirement plans |
| `apm.tiering_plans` | `TieringPlanCollection` | Tiering plans (version tiering to remote storage) |
| `apm.saas` | `SaasCollection` | SaaS tenant list |
| `apm.plans` | `ProtectionPlanCollection` | Cross-category read queries (all categories) |
| `apm.get_site_info()` | direct method | Site info + management server info + storage stats |

---

## SDK Source Access

For field names, method signatures, enum values, or any other SDK details, look them up from the installed source rather than guessing.

**Find the installed source path** (then use the Read tool on any `.py` file):

```bash
python -c "import synology_apm.sdk, os; print(os.path.dirname(synology_apm.sdk.__file__))"
```

Key files: `enums.py`, `models/workload.py`, `models/activity.py`, `models/backup_server.py`, `models/version.py`, `models/protection_plan.py`, `models/retirement_plan.py`, `models/tiering_plan.py`, `models/saas.py`, `models/remote_storage.py`.

**List all public names** (classes, enums, models, exceptions):

```bash
python -c "import synology_apm.sdk; print('\n'.join(synology_apm.sdk.__all__))"
```

**Inspect a specific class:**

```bash
python -c "import inspect, synology_apm.sdk; print(inspect.getsource(synology_apm.sdk.MachineWorkload))"
```

---

## Script Design Guidelines

Read the existing scripts as concrete examples; the guidelines below describe the patterns they all follow.

### Entry point

Every script uses `run_main(run(...))` from `_common.py`. The async `run()` function takes all parsed arguments and returns `int | None` (0 = success, non-zero = error). `make_client()` builds the `APMClient` from environment variables; never hardcode credentials.

### stdout / stderr discipline

Progress messages, status lines, prompts, and dry-run notices go to `stderr`. Machine-readable output (table / csv / json) goes to `stdout`. This lets operators redirect output without losing visibility: `script -o csv > out.csv` shows progress in the terminal while capturing data cleanly.

### Output formats

Use `add_output_arg(parser)` from `_common.py` to add the standard `-o table|csv|json` flag. CSV columns must match table columns in the same order. JSON includes `null` for optional fields rather than omitting them. Table output uses dynamic column widths calculated from data.

### Confirmation before mutating state

Operations that trigger actions on the backend must support `--dry-run` and `--yes / -y`:

- `--dry-run` — display the planned scope and return 0 without executing anything.
- Without `--yes` — display the planned scope, then prompt for confirmation with `prompt_yes_no()` from `_common.py`.
- `--yes` — skip the prompt; intended for automated or scripted contexts.

### Per-item error handling

One item failing must not abort the batch. Wrap per-item work in `try/except APMError` and record the error against that item. Use `asyncio.gather(*tasks)` to collect all results regardless of individual failures. Report per-item errors in the final summary or output CSV.

### Concurrency

Use `asyncio.Semaphore` to bound concurrent API calls; pass the semaphore into each task and acquire it at the start of work. Expose a `--concurrency` flag so operators can tune throughput. A default of 3–5 is typical. For pipelines with distinct phases (e.g. start export then download), use separate semaphores for each phase.

### Progress feedback for long-running operations

Use the `Progress` dataclass from `_common.py` together with a 1-second ticker task to print live status to `stderr`. Clear the progress line (`progress.clear_progress()`) before printing a per-item status message so output is not garbled. Polling loops print each iteration's status and remaining time to `stderr`.

### Graceful interruption

Before starting tasks, register a SIGINT handler with `register_interrupt()` from `_common.py`, which converts Ctrl+C into an `asyncio.Event` rather than raising `KeyboardInterrupt`. On interrupt: stop starting new work, offer to cancel any in-progress remote jobs on the APM server, then drain all asyncio tasks to completion before the APM session closes. Always write the final report even when interrupted.

### CSV report as recovery checkpoint

Large batch operations should write a CSV report recording the outcome of every item. Where feasible, support `--resume <report.csv>` to retry items in a resumable state, merging carried rows from the previous run into the new report to preserve history.

---

## Quality Standards

Every example script must pass both checks before committing:

```bash
ruff check .
mypy .
```

Rules:
- `ruff check` must produce no warnings or errors (unused imports, undefined names, etc.)
- `mypy` must produce no errors under default settings — generic types must carry full type arguments (e.g. `dict[str, Any]`, not bare `dict`; `list[str]`, not bare `list`)
- `--strict` is not required, but `[type-arg]` errors are not acceptable

