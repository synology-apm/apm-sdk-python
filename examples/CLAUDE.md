# CLAUDE.md — APM Examples Development Guide

This file provides context for Claude Code when helping write scripts that use the APM Python SDK (`synology_apm.sdk`).

---

## Script Skeleton

Every script follows this shape: build the client with `make_client()` — which reads
`APM_HOST` / `APM_USERNAME` / `APM_PASSWORD` / `APM_NO_VERIFY_SSL` from the environment /
`.env` (see `.env.example` at the repository root) — and hand an async entry coroutine to
`run_main()`. Never hardcode credentials.

```python
import argparse
import sys

from _common import add_output_arg, make_client, run_main


async def run(output_format: str) -> int | None:
    print("Collecting data...", file=sys.stderr)
    async with make_client() as apm:
        servers, total = await apm.backup_servers.list()
    ...
    return 0  # None also means success; run_main() maps APMError to exit code 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_output_arg(parser)
    args = parser.parse_args()
    run_main(run(args.output))


if __name__ == "__main__":
    main()
```

> **Note:** `APMClient` takes a scheme-less `host[:port]` — the SDK prepends `https://`
> internally.

---

## Shared helpers (`_common.py`)

Example scripts must reuse `_common.py` instead of re-implementing boilerplate; read the file directly to see what's available. Add new helpers there rather than copying into individual scripts.

`list()` returns a single page of `(items, total)`; the default page size varies by collection. Use `paginate()` or the `collect_*` helpers from `_common.py` to retrieve all records.

---

## Finding SDK Entry Points and Types

- For the access-path overview (`apm.machine.workloads`, `apm.activities.backup`, ...), see
  the **Collection Map** in `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` —
  the authoritative list.
- For field names, method signatures, or enum values, look them up in the installed source
  rather than guessing — find the path, then use the Read tool on any `.py` file
  (`enums.py`, `models/*.py`, `collections/*.py`):

```bash
python -c "import synology_apm.sdk, os; print(os.path.dirname(synology_apm.sdk.__file__))"
```

---

## Script Design Guidelines

Read the existing scripts as concrete examples; the guidelines below describe the patterns they all follow.

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

## Unit Tests

Example scripts are unit-tested in `tests/unit/examples/` (one `test_<module>*.py` family per
script). Test layering:

- A script's module-level `_`-prefixed helpers are its **internal contract** and may be unit
  tested directly (the same precedent as the CLI's `cli/_display.py` public-named functions).
  A pure re-decomposition of a script is expected to update these tests.
- Every script must also have **`run()`-level behavior tests as the primary layer**: inject a
  fake client with `make_fake_apm()` + `patch_make_client()` from
  `tests/unit/examples/_fixtures.py`, then assert output formats (parse csv/json; match table
  label+value on the same line), exit codes, and per-item error resilience.
- Build SDK model values with the `make_*` builders in `tests/unit/examples/_fixtures.py`
  rather than hand-constructing dataclasses; test data follows the placeholder table in the
  repository CLAUDE.md.
- Tests must not hit the network, sleep for real, or read the developer's `.env` (note
  `_common.py` calls `load_dotenv()` at import time — environment-dependent tests must
  monkeypatch every variable they read).

> **Note:** `tests/unit/examples/conftest.py` inserts `examples/` into `sys.path`, so test
> modules import scripts flat (`from backup_catchup import ...`); shared test helpers use the
> full package path (`from tests.unit.examples._fixtures import ...`).

---

## Pre-commit Checks

Every example change must pass all three before committing:

```bash
uv run pytest tests/unit/examples/ -q
ruff check .
mypy .
```

`mypy` runs under default settings (`--strict` is not required), but generic types must carry full type arguments (e.g. `dict[str, Any]`, not bare `dict`) — `[type-arg]` errors are not acceptable.

---

## Adding a New Script

- Add it to the appropriate category table in `examples/README.md`.
- Add its `tests/unit/examples/test_<module>.py` — `examples/` is included in the unit-test
  coverage gate (`make test`).
- Dependencies are limited to the public SDK API and the existing dev dependencies
  (`python-dotenv`, `pyyaml`, `openpyxl`); do not introduce new third-party packages.
