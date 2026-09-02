# Synology ActiveProtect Manager (APM) — Python SDK, CLI & MCP Server

[![CI](https://github.com/synology-apm/apm-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/synology-apm/apm-sdk-python/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://synology-apm.github.io/apm-sdk-python/)
[![PyPI - synology-apm-sdk](https://img.shields.io/pypi/v/synology-apm-sdk?label=synology-apm-sdk)](https://pypi.org/project/synology-apm-sdk/)
[![PyPI - synology-apm-cli](https://img.shields.io/pypi/v/synology-apm-cli?label=synology-apm-cli)](https://pypi.org/project/synology-apm-cli/)
[![PyPI - synology-apm-mcp](https://img.shields.io/pypi/v/synology-apm-mcp?label=synology-apm-mcp)](https://pypi.org/project/synology-apm-mcp/)

Python SDK, command-line tool, and MCP server for [Synology ActiveProtect Manager (APM)](https://www.synology.com/products/ActiveProtectAppliance).

## Prerequisites

Pick whichever matches the install method you use below — you don't need both:

- `uv` — provisions Python automatically and powers the `uvx`/`uv tool`/`uv add` commands below; see the [installation instructions](https://docs.astral.sh/uv/getting-started/installation/) for macOS, Windows, and Linux
- `pip` — usually bundled with your Python installation; see the [installation instructions](https://pip.pypa.io/en/stable/installation/) if you need to install it separately
- Python 3.11 or later (provisioned automatically when using `uv`/`uvx`; required on your own interpreter for a plain `pip install`)

## Compatibility

This SDK release supports multiple APM versions — see the SDK design contract's
["APM Version Compatibility"](packages/synology-apm-sdk/src/synology_apm/sdk/README.md#apm-version-compatibility)
section for the supported versions and how cross-version differences are handled.

---

## AI Agent Integration (MCP Server)

`synology-apm-mcp` exposes APM operations — backups, restores, protection plans, M365 and GWS workloads, infrastructure, activities, and logs — as [Model Context Protocol](https://modelcontextprotocol.io/) tools for AI agents such as Claude Desktop and ChatGPT Desktop, plus workflow skills that teach agents domain goals (daily backup reports, storage capacity analysis, failure investigation, and more).

### Install the MCP Server

See [`packages/synology-apm-mcp/README.md`](packages/synology-apm-mcp/README.md) for the full setup guide — Claude Desktop, ChatGPT Desktop, environment variables, operation modes, and audit logging.

---

## CLI

`synology-apm-cli` operates APM directly from your terminal — protection plans, backup/restore operations, infrastructure, activities, and logs.

### Install the CLI

Run directly without installing:

```bash
uvx synology-apm-cli --help
```

Or install with pip:

```bash
pip install synology-apm-cli
synology-apm-cli --help
```

### CLI Quick Start

```bash
synology-apm-cli config set  # set up default credentials
synology-apm-cli infra info
```

### Full CLI Command Reference

See [`packages/synology-apm-cli/README.md`](packages/synology-apm-cli/README.md) for authentication options, output formats, and the full command reference.

---

## Developer Guide

Building your own automation on top of the SDK, or contributing to this repo? Start here.

### SDK

`synology-apm-sdk` is the async-native, fully typed Python interface to the APM REST API that both the CLI and MCP server are built on.

#### Install the SDK

```bash
uv add synology-apm-sdk        # inside a uv project
pip install synology-apm-sdk   # any other environment
```

See [`packages/synology-apm-sdk/README.md`](packages/synology-apm-sdk/README.md) for the Quick Start, full data model, and usage examples.

This repo is a [uv](https://docs.astral.sh/uv/) workspace publishing three PyPI packages
(`synology-apm-sdk`, `synology-apm-cli`, `synology-apm-mcp`) from shared source, plus `skills/`
(MCP workflow skills), `examples/`, and `docs/` (Sphinx source) — see [`CLAUDE.md`](CLAUDE.md)
for the dev workflow.

### Install From Source (Contributing)

```bash
git clone https://github.com/synology-apm/apm-sdk-python.git
cd apm-sdk-python

# Install all three packages editable, plus dev tools (pytest, mypy, ruff, etc.)
uv sync

# Run the CLI
uv run synology-apm-cli config set  # set up default credentials
```

See [`CLAUDE.md`](CLAUDE.md) for the development guide (code conventions, Post-change Checklist)
and [`CONTRIBUTING.md`](CONTRIBUTING.md) for example-data conventions; see the `Makefile` for
available commands (tests, linting, type checking, docs, skills generation).

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

## Documentation Index

| Document | Description |
|----------|-------------|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Example-data conventions (canonical placeholder values for docstrings, examples, and test fixtures) |
| [`APM_PRODUCT_OVERVIEW.md`](APM_PRODUCT_OVERVIEW.md) | APM product/domain knowledge: workload categories, protection plans, backup copy, and other core concepts |
| [`packages/synology-apm-cli/README.md`](packages/synology-apm-cli/README.md) | CLI command reference — authentication, output formats, full command list |
| [`packages/synology-apm-mcp/README.md`](packages/synology-apm-mcp/README.md) | MCP server guide — Claude Desktop / ChatGPT Desktop setup, operation modes, available tools and workflow skills |
| [`packages/synology-apm-sdk/README.md`](packages/synology-apm-sdk/README.md) | SDK developer guide — quick start and usage examples for every module |
| [API Reference](https://synology-apm.github.io/apm-sdk-python/) | Full SDK API reference — every public class, method, and type signature (Sphinx, hosted on GitHub Pages) |
| [`examples/README.md`](examples/README.md) | Example automation scripts (inventory, reports, bulk import/export, …) |
| [`CLAUDE.md`](CLAUDE.md) | Development guide — code conventions and the Post-change Checklist |
