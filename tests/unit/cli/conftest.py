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

# Must be set before importing any synology_apm.cli module. output.py creates a
# module-level Console() whose _color_system is fixed at construction time based
# on sys.stdout.isatty(). In a real terminal isatty()=True → _color_system=TRUECOLOR
# → Style.render() emits ANSI codes (bold, dim, …) even through CliRunner's
# StringIO. TTY_COMPATIBLE=0 makes Rich's is_terminal() return False
# unconditionally (takes priority over isatty and FORCE_COLOR), so
# _color_system=None and all output is plain text.
os.environ.setdefault("TTY_COMPATIBLE", "0")

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
