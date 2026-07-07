"""Shared argparse construction for the smoke-test entry points."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path


def build_argparser(
    *,
    prog: str,
    description: str,
    group_choices: Sequence[str],
    default_scopes: Sequence[str],
) -> argparse.ArgumentParser:
    """Build the argument parser shared by the CLI and SDK smoke-test tools."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--group",
        choices=tuple(group_choices),
        default="all",
        help="Only run this phase, instead of all phases in dependency order (default: all).",
    )
    parser.add_argument(
        "--m365-scopes",
        default=",".join(default_scopes),
        help=f"Comma-separated M365 workload scopes to test (default: {','.join(default_scopes)}).",
    )
    parser.add_argument(
        "--creds",
        type=Path,
        default=None,
        metavar="FILE",
        help="TOML credential file for CRUD roundtrip tests (default: tests/smoke/smoke_creds.toml).",
    )
    parser.add_argument(
        "--output-creds-template",
        action="store_true",
        help="Print a smoke_creds.toml template to stdout and exit.",
    )
    return parser
