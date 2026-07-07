# Synology ActiveProtect Manager (APM) — Python SDK & CLI

Python SDK and command-line tool for [Synology ActiveProtect Manager (APM)](https://www.synology.com/products/ActiveProtectAppliance).

- **synology-apm-sdk** (`synology_apm.sdk`) — async-native, fully typed Python interface to the APM REST API.
- **synology-apm-cli** (`synology_apm.cli`) — a CLI front-end, built on `synology-apm-sdk`, for operating APM from the terminal (`synology-apm` command).

This page is a starting point. For product background, usage guides, and command references, see [Documentation](#documentation) below.

---

## What's in this repo

This repo is a [uv](https://docs.astral.sh/uv/) workspace that publishes **two** PyPI packages from shared source:

| Path | Description |
|------|-------------|
| `packages/synology-apm-sdk/` | The `synology-apm-sdk` PyPI package (`synology_apm.sdk` import) |
| `packages/synology-apm-cli/` | The `synology-apm-cli` PyPI package (`synology_apm.cli` import; depends on `synology-apm-sdk`) |
| `skills/` | Claude Code skills that teach AI agents to use the `synology-apm` CLI |
| `examples/` | Example automation scripts built on `synology_apm.sdk` |
| `docs/` | Sphinx API reference — config is checked in, generated output is not (see [API Reference](#api-reference)) |

---

## Documentation

| Document | Description |
|----------|-------------|
| [`APM_PRODUCT_OVERVIEW.md`](APM_PRODUCT_OVERVIEW.md) | APM product/domain knowledge: workload categories, protection plans, backup copy, and other core concepts |
| [`packages/synology-apm-sdk/README.md`](packages/synology-apm-sdk/README.md) | SDK developer guide — quick start and usage examples for every module |
| [`packages/synology-apm-cli/README.md`](packages/synology-apm-cli/README.md) | CLI command reference — authentication, output formats, full command list |
| [`examples/README.md`](examples/README.md) | Example automation scripts (inventory, reports, bulk import/export, …) |
| [`CLAUDE.md`](CLAUDE.md) | Development guide — testing standards, conventions, and common commands |

### API Reference

The full SDK API reference (every public class, method, and type signature) is generated from source with Sphinx and is not checked into the repository. Build it locally:

```bash
uv sync --group docs   # first time only
make docs
```

Then open `docs/_build/html/index.html` in your browser.

---

## Requirements

- Python 3.11 or later

---

## Installation

### Option A — Install from PyPI

```bash
pip install synology-apm-sdk   # library only
pip install synology-apm-cli   # CLI (depends on synology-apm-sdk), provides the `synology-apm` command
```

### Option B — Install from source (development)

This repo is a uv workspace with two members, `packages/synology-apm-sdk` and `packages/synology-apm-cli`.

```bash
git clone https://github.com/synology-apm/apm-sdk-python.git
cd apm-sdk-python

# Install both packages editable, plus dev tools (pytest, mypy, ruff, etc.)
uv sync

# Run the CLI
uv run synology-apm config set --host apm.corp.com --username admin
```

### Option C — Build and install wheels

```bash
make whl   # → dist/synology-apm-sdk/*.whl + *.tar.gz, dist/synology-apm-cli/*.whl + *.tar.gz

pip install dist/synology-apm-sdk/synology_apm_sdk-<version>-py3-none-any.whl
pip install dist/synology-apm-cli/synology_apm_cli-<version>-py3-none-any.whl
```

Replace `<version>` with the actual version number in the filenames (e.g. `0.1.0`).

All three options install the `synology_apm.sdk` package and, when `synology-apm-cli` is installed, the `synology-apm` command (defined in `packages/synology-apm-cli/pyproject.toml` → `[project.scripts]`).

### Claude Code skills

`skills/apm-*` teach Claude Code how to use the `synology-apm` CLI. Copy or symlink them into your skills directory:

```bash
mkdir -p ~/.claude/skills
cp -r skills/apm-* ~/.claude/skills/
```

---

## Quick start

### CLI

```bash
synology-apm config set --host apm.corp.com --username admin
synology-apm infra info
```

See [`packages/synology-apm-cli/README.md`](packages/synology-apm-cli/README.md) for authentication options, output formats, and the full command reference.

### SDK

```python
import asyncio
from synology_apm.sdk import APMClient

async def main():
    async with APMClient("apm.corp.com", "admin", "password") as apm:
        workloads, _ = await apm.machine.workloads.list()
        for wl in workloads:
            print(f"{wl.name}  last backup: {wl.last_backup_at}")

asyncio.run(main())
```

See [`packages/synology-apm-sdk/README.md`](packages/synology-apm-sdk/README.md) for the full data model and usage examples.

---

## Development

See [`CLAUDE.md`](CLAUDE.md) for the development guide: testing standards, code conventions, and common commands (tests, linting, type checking, docs, skills generation).
