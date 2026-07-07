"""Shared helpers for CLI unit tests.

Import these in command test files to avoid repeating boilerplate:

    from tests.unit.cli.conftest import BASE_ARGS, invoke_cli

``invoke_cli`` wraps the ``fake_get_client`` + ``patch`` + ``runner.invoke`` pattern
that every CLI test repeats:

    result = invoke_cli(mock_apm, ["machine", "list", "--type", "vm"])
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import typer
from click.testing import Result
from typer.testing import CliRunner

# Must be set before importing any synology_apm.cli module so that every Rich
# Console — including the module-level Console in output.py and the temporary
# consoles Typer creates for help-text rendering — is initialized with
# color_system=None and produces no ANSI output.
#
# TERM=dumb is the only flag that suppresses ALL ANSI codes (colors AND
# bold/dim formatting). NO_COLOR=1 alone still allows bold/dim, which causes
# Typer to render option flags like --id as split sequences
# (\x1b[2m-\x1b[0m\x1b[1;2m-id\x1b[0m), breaking simple substring assertions.
# We override unconditionally (not setdefault) so CI environments that set
# FORCE_COLOR don't escape this guard.
os.environ["TERM"] = "dumb"

from synology_apm.cli.main import app  # noqa: E402

runner = CliRunner()

BASE_ARGS: list[str] = ["--host", "https://x", "--username", "u", "--password", "p"]


def invoke_cli(
    mock_apm: AsyncMock,
    args: list[str],
    **runner_kwargs: Any,
) -> Result:
    """Invoke a CLI command with a mocked APM client.

    Args:
        mock_apm: Pre-configured AsyncMock to inject as the APM client
                  (patched in as ``_helpers.get_client``, which every command
                  reaches through ``apm_session``).
        args:     CLI args (without the global connection flags — those are prepended
                  automatically via BASE_ARGS).
        **runner_kwargs: Passed through to ``CliRunner.invoke``.

    Returns:
        The ``typer.testing.Result`` from the CLI invocation.
    """
    @asynccontextmanager
    async def fake_get_client(ctx: typer.Context) -> AsyncIterator[AsyncMock]:
        yield mock_apm

    with patch("synology_apm.cli._helpers.get_client", fake_get_client):
        return runner.invoke(app, BASE_ARGS + args, **runner_kwargs)


@pytest.fixture
def mock_apm() -> AsyncMock:
    """A blank AsyncMock APM client for configuring per-test SDK return values."""
    return AsyncMock()
