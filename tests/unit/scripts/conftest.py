"""Bootstrap sys.path so test modules can import generate_skills from scripts/ directly."""
from __future__ import annotations

import sys
from pathlib import Path

import click
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))


@pytest.fixture(scope="module")
def root() -> click.Group:
    """The live synology-apm Click command tree, built once per test module."""
    from generate_skills import get_root_command

    return get_root_command()
