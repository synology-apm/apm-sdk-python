"""Unit tests for scripts/generate_skills.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_skills import (  # noqa: E402
    COMMON_FLAG_SOURCES,
    COMMON_FLAGS,
    caution_block,
    find_command,
    get_root_command,
    render_common_flags_table,
)


@pytest.mark.parametrize("path", ["machine retire", "m365 exchange retire"])
def test_caution_block_irreversible_commands_get_caution(path: str) -> None:
    """Commands documented as irreversible must render a CAUTION-level callout.

    caution_block() decides this by matching "irreversible" in the command's
    docstring; this test guards against a future docstring rewording silently
    downgrading the generated skill's warning.
    """
    root = get_root_command()
    cmd = find_command(root, path)
    block = caution_block(cmd)
    assert block is not None
    assert "CAUTION" in block
    assert "irreversible" in block


@pytest.mark.parametrize("path", ["machine cancel", "m365 exchange cancel"])
def test_caution_block_reversible_commands_get_generic_note(path: str) -> None:
    """Commands with --yes but not documented as irreversible get a plain note."""
    root = get_root_command()
    cmd = find_command(root, path)
    block = caution_block(cmd)
    assert block is not None
    assert "CAUTION" not in block


def test_caution_block_no_yes_flag_returns_none() -> None:
    """Commands without --yes don't get any confirmation callout."""
    root = get_root_command()
    cmd = find_command(root, "machine list")
    assert caution_block(cmd) is None


def test_find_command_unknown_segment_raises_system_exit() -> None:
    """A typo'd command path raises SystemExit, not a raw KeyError."""
    root = get_root_command()
    with pytest.raises(SystemExit, match="machine bogus"):
        find_command(root, "machine bogus")


def test_common_flag_sources_cover_all_common_flags() -> None:
    """Every flag stripped from per-command tables has a row source in apm-shared.

    If COMMON_FLAGS gains an entry without a matching COMMON_FLAG_SOURCES entry,
    that flag would silently disappear from both the per-command tables (stripped)
    and apm-shared's "Common Flags" table (no source to render it from).
    """
    assert set(COMMON_FLAG_SOURCES) == COMMON_FLAGS


def test_render_common_flags_table_includes_all_common_flags() -> None:
    """The rendered apm-shared table has one row per COMMON_FLAGS entry."""
    root = get_root_command()
    table = render_common_flags_table(root)
    for flag in COMMON_FLAGS:
        assert f"`{flag}" in table
