"""Shared Typer option declarations reused across command modules.

Each constant is a typer.Option metadata object; commands reference it as the
parameter default (e.g. ``limit: int = LIMIT_OPTION``) so option names, defaults,
and help text stay identical everywhere they appear.
"""
from __future__ import annotations

import typer

from synology_apm.cli.output import ListOutputFormat, OutputFormat

LIMIT_OPTION = typer.Option(25, "--limit", help="Maximum records to show")
VERSION_LIMIT_OPTION = typer.Option(25, "--limit", help="Maximum versions to show")
OFFSET_OPTION = typer.Option(0, "--offset", help="Pagination start offset (default 0)")
PAGE_ALL_OPTION = typer.Option(
    False, "--page-all",
    help="Fetch all pages automatically, starting from --offset, using --limit as the page size",
)
LIST_OUTPUT_OPTION = typer.Option(ListOutputFormat.TABLE, "--output", "-o", help="Output format")
OUTPUT_OPTION = typer.Option(OutputFormat.TABLE, "--output", "-o", help="Output format")
SINCE_OPTION = typer.Option(
    None, "--since",
    help="Start time: 1h / 24h / 7d or ISO 8601 (e.g. 2026-04-01T00:00:00)",
)
UNTIL_OPTION = typer.Option(
    None, "--until",
    help="End time: 1h / 24h / 7d or ISO 8601 (e.g. 2026-04-20T23:59:59)",
)
