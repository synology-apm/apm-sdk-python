# Synology ActiveProtect Manager (APM) — Python SDK, CLI & MCP Server

[![CI](https://github.com/synology-apm/apm-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/synology-apm/apm-sdk-python/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://synology-apm.github.io/apm-sdk-python/)
[![PyPI - synology-apm-sdk](https://img.shields.io/pypi/v/synology-apm-sdk?label=synology-apm-sdk)](https://pypi.org/project/synology-apm-sdk/)
[![PyPI - synology-apm-cli](https://img.shields.io/pypi/v/synology-apm-cli?label=synology-apm-cli)](https://pypi.org/project/synology-apm-cli/)
[![PyPI - synology-apm-mcp](https://img.shields.io/pypi/v/synology-apm-mcp?label=synology-apm-mcp)](https://pypi.org/project/synology-apm-mcp/)

Python SDK, command-line tool, and MCP server for [Synology ActiveProtect Manager (APM)](https://www.synology.com/products/ActiveProtectAppliance).

- **synology-apm-sdk** (`synology_apm.sdk`) — async-native, fully typed Python interface to the APM REST API.
- **synology-apm-cli** (`synology_apm.cli`) — a CLI front-end, built on `synology-apm-sdk`, for operating APM from the terminal (`synology-apm-cli` command).
- **synology-apm-mcp** (`synology_apm.mcp`) — an MCP server, built on `synology-apm-sdk`, exposing APM operations as tools for AI agents (Claude Desktop, ChatGPT Desktop, and other MCP-compatible clients).

This page is a starting point. For product background, usage guides, and command references, see [Documentation](#documentation) below.

---

## What's in this repo

This repo is a [uv](https://docs.astral.sh/uv/) workspace that publishes **three** PyPI packages from shared source:

| Path | Description |
|------|-------------|
| `packages/synology-apm-sdk/` | The `synology-apm-sdk` PyPI package (`synology_apm.sdk` import) |
| `packages/synology-apm-cli/` | The `synology-apm-cli` PyPI package (`synology_apm.cli` import; depends on `synology-apm-sdk`) |
| `packages/synology-apm-mcp/` | The `synology-apm-mcp` PyPI package — MCP server for AI agent integration (depends on `synology-apm-sdk`) |
| `skills/` | Workflow skills for the MCP server — teach AI agents how to accomplish domain goals using MCP tools |
| `examples/` | Example automation scripts built on `synology_apm.sdk` |
| `docs/` | Sphinx API reference source — published to [GitHub Pages](https://synology-apm.github.io/apm-sdk-python/) (see [API Reference](#api-reference)) |

---

## Documentation

| Document | Description |
|----------|-------------|
| [`APM_PRODUCT_OVERVIEW.md`](APM_PRODUCT_OVERVIEW.md) | APM product/domain knowledge: workload categories, protection plans, backup copy, and other core concepts |
| [`packages/synology-apm-sdk/README.md`](packages/synology-apm-sdk/README.md) | SDK developer guide — quick start and usage examples for every module |
| [API Reference](https://synology-apm.github.io/apm-sdk-python/) | Full SDK API reference — every public class, method, and type signature (Sphinx, hosted on GitHub Pages) |
| [`packages/synology-apm-cli/README.md`](packages/synology-apm-cli/README.md) | CLI command reference — authentication, output formats, full command list |
| [`packages/synology-apm-mcp/README.md`](packages/synology-apm-mcp/README.md) | MCP server guide — Claude Desktop / ChatGPT Desktop setup, operation modes, available tools and workflow skills |
| [`examples/README.md`](examples/README.md) | Example automation scripts (inventory, reports, bulk import/export, …) |
| [`CLAUDE.md`](CLAUDE.md) | Development guide — testing standards, conventions, and common commands |

### API Reference

The full SDK API reference (every public class, method, and type signature) is generated from source with Sphinx and published at:

**https://synology-apm.github.io/apm-sdk-python/**

To build it locally instead (e.g. to preview docstring changes):

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
pip install synology-apm-cli   # CLI (depends on synology-apm-sdk), provides the `synology-apm-cli` command
```

### Option B — Install from source (development)

This repo is a uv workspace with three members: `packages/synology-apm-sdk`, `packages/synology-apm-cli`, and `packages/synology-apm-mcp`.

```bash
git clone https://github.com/synology-apm/apm-sdk-python.git
cd apm-sdk-python

# Install both packages editable, plus dev tools (pytest, mypy, ruff, etc.)
uv sync

# Run the CLI
uv run synology-apm-cli config set --host apm.corp.com --username admin
```

### Option C — Build and install wheels

```bash
make whl   # → dist/synology-apm-sdk/*.whl + *.tar.gz, dist/synology-apm-cli/*.whl + *.tar.gz, dist/synology-apm-mcp/*.whl + *.tar.gz

pip install dist/synology-apm-sdk/synology_apm_sdk-<version>-py3-none-any.whl
pip install dist/synology-apm-cli/synology_apm_cli-<version>-py3-none-any.whl
```

Replace `<version>` with the actual version number in the filenames (e.g. `0.1.0`).

All three options install the `synology_apm.sdk` package and, when `synology-apm-cli` is installed, the `synology-apm-cli` command (defined in `packages/synology-apm-cli/pyproject.toml` → `[project.scripts]`).

`make whl` also builds `dist/synology-apm-mcp/*.whl`. The recommended way to run the MCP server is `uvx synology-apm-mcp` or the Claude/ChatGPT Desktop plugin (see [AI agent integration](#ai-agent-integration) below) rather than a manual `pip install`, but `pip install dist/synology-apm-mcp/synology_apm_mcp-<version>-py3-none-any.whl` also works if you want the `synology-apm-mcp` command directly.

### AI agent integration

`packages/synology-apm-mcp/` is an MCP server that exposes the full SDK as MCP tools for AI agents (Claude Desktop, ChatGPT Desktop, and other MCP-compatible clients), plus workflow skills that teach agents domain goals — daily backup reports, storage capacity analysis, failure investigation, and more.

See [`packages/synology-apm-mcp/README.md`](packages/synology-apm-mcp/README.md) for installation (Claude Desktop, ChatGPT Desktop) and the full setup guide.

---

## Quick start

> For MCP server setup (AI agent integration), see [AI agent integration](#ai-agent-integration) above.

### CLI

```bash
synology-apm-cli config set --host apm.corp.com --username admin
synology-apm-cli infra info
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
