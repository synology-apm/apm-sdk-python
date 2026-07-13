"""Unit tests for scripts/generate_skills.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import generate_skills
import pytest
from generate_skills import (
    COMMON_FLAG_SOURCES,
    COMMON_FLAGS,
    assert_m365_scopes,
    caution_block,
    clean_help,
    find_command,
    format_flag,
    render_command,
    render_common_flags_table,
    render_global_options,
    render_help_blocks,
    render_m365_scope_table,
    render_root_help,
    render_skill,
    yaml_quote,
)

# --- caution_block ------------------------------------------------------------


@pytest.mark.parametrize("path", ["machine retire", "m365 exchange retire"])
def test_caution_block_irreversible_commands_get_caution(root: click.Group, path: str) -> None:
    """Commands documented as irreversible must render a CAUTION-level callout.

    caution_block() decides this by matching "irreversible" in the command's
    docstring; this test guards against a future docstring rewording silently
    downgrading the generated skill's warning.
    """
    cmd = find_command(root, path)
    block = caution_block(cmd)
    assert block is not None
    assert "CAUTION" in block
    assert "irreversible" in block


@pytest.mark.parametrize("path", ["machine cancel", "m365 exchange cancel"])
def test_caution_block_reversible_commands_get_generic_note(root: click.Group, path: str) -> None:
    """Commands with --yes but not documented as irreversible get a plain note."""
    cmd = find_command(root, path)
    block = caution_block(cmd)
    assert block is not None
    assert "CAUTION" not in block
    assert "prompts for confirmation" in block


def test_caution_block_no_yes_flag_returns_none(root: click.Group) -> None:
    """Commands without --yes don't get any confirmation callout."""
    cmd = find_command(root, "machine list")
    assert caution_block(cmd) is None


def test_caution_block_matches_irreversible_case_insensitively() -> None:
    """The irreversible marker is detected regardless of docstring capitalization."""
    cmd = click.Command(
        "retire",
        params=[click.Option(["--yes"], is_flag=True)],
        help="Remove the workload. This action is Irreversible.",
    )
    block = caution_block(cmd)
    assert block is not None
    assert "CAUTION" in block


# --- find_command ------------------------------------------------------------


def test_find_command_unknown_segment_raises_system_exit(root: click.Group) -> None:
    """A typo'd command path raises SystemExit, not a raw KeyError."""
    with pytest.raises(SystemExit, match="machine bogus"):
        find_command(root, "machine bogus")


def test_find_command_segment_under_leaf_command_raises_system_exit(root: click.Group) -> None:
    """A path descending through a non-group command raises SystemExit, not AttributeError."""
    with pytest.raises(SystemExit, match="not a group"):
        find_command(root, "machine list bogus")


# --- common flags ------------------------------------------------------------


def test_common_flag_sources_cover_all_common_flags() -> None:
    """Every flag stripped from per-command tables has a row source in apm-shared.

    If COMMON_FLAGS gains an entry without a matching COMMON_FLAG_SOURCES entry,
    that flag would silently disappear from both the per-command tables (stripped)
    and apm-shared's "Common Flags" table (no source to render it from).
    """
    assert set(COMMON_FLAG_SOURCES) == COMMON_FLAGS


def test_render_common_flags_table_has_one_documented_row_per_flag(root: click.Group) -> None:
    """The rendered apm-shared table has exactly one row per COMMON_FLAGS entry,
    and every row pairs the flag with a non-empty description."""
    table = render_common_flags_table(root)
    lines = table.split("\n")
    assert lines[:2] == ["| Flag | Description |", "|------|-------------|"]
    rows = lines[2:]
    assert len(rows) == len(COMMON_FLAGS)
    assert "| `--limit <LIMIT>` | Maximum records to show |" in rows
    for flag in COMMON_FLAGS:
        row = next(r for r in rows if f"`{flag}" in r)
        assert row.split("|")[2].strip()


def test_render_common_flags_table_missing_flag_on_source_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A COMMON_FLAG_SOURCES entry pointing at a command lacking the flag fails loudly."""
    grp = click.Group("root", commands={"noop": click.Command("noop", params=[])})
    monkeypatch.setattr(generate_skills, "COMMON_FLAG_SOURCES", {"--output": "noop"})
    with pytest.raises(SystemExit, match="--output"):
        render_common_flags_table(grp)


# --- clean_help --------------------------------------------------------------


def test_clean_help_strips_no_rewrap_marker_and_whitespace() -> None:
    assert clean_help("\x08\nList workloads.  \n") == "List workloads."


def test_clean_help_none_returns_empty_string() -> None:
    assert clean_help(None) == ""


# --- render_help_blocks -----------------------------------------------------


def test_render_help_blocks_empty_input_returns_empty_string() -> None:
    assert render_help_blocks(None) == ""
    assert render_help_blocks("   \n  ") == ""


def test_render_help_blocks_prose_only_passes_through_as_paragraphs() -> None:
    text = "List all workloads.\n\nSupports filtering by status."
    assert render_help_blocks(text) == ("List all workloads.\n\nSupports filtering by status.")


def test_render_help_blocks_indented_examples_become_sh_fence() -> None:
    text = "List all workloads.\n\n    synology-apm machine list\n    synology-apm machine list --status protected"
    assert render_help_blocks(text) == (
        "List all workloads.\n\n```sh\nsynology-apm machine list\nsynology-apm machine list --status protected\n```"
    )


def test_render_help_blocks_single_colon_label_before_examples_is_bolded() -> None:
    """A lone 'Label:' line directly above an example run becomes a bold sub-heading."""
    text = (
        "Restore a workload.\n\n"
        "Search mode:\n"
        "    synology-apm machine restore vm-web-01\n"
        "Direct mode:\n"
        "    synology-apm machine restore --version-id 123e4567-..."
    )
    assert render_help_blocks(text) == (
        "Restore a workload.\n\n"
        "**Search mode:**\n\n"
        "```sh\nsynology-apm machine restore vm-web-01\n```\n\n"
        "**Direct mode:**\n\n"
        "```sh\nsynology-apm machine restore --version-id 123e4567-...\n```"
    )


def test_render_help_blocks_multiline_prose_ending_in_colon_is_not_bolded() -> None:
    """Only a single-line label gets the bold treatment, not a full paragraph."""
    text = "First line of prose.\nSecond line ends with a colon:\n    synology-apm machine list"
    rendered = render_help_blocks(text)
    assert "**" not in rendered
    assert rendered.endswith("```sh\nsynology-apm machine list\n```")


def test_render_help_blocks_unindented_command_line_is_prose() -> None:
    """Only indented 'synology-apm ...' lines count as examples."""
    text = "Run synology-apm config show to inspect the profile."
    rendered = render_help_blocks(text)
    assert "```" not in rendered
    assert rendered == text


# --- format_flag ------------------------------------------------------------


def _flag_ctx(param: click.Parameter) -> click.Context:
    cmd = click.Command("dummy", params=[param])
    return click.Context(cmd, info_name="synology-apm dummy")


def test_format_flag_argument_renders_bare_metavar() -> None:
    param = click.Argument(["workload"])
    assert format_flag(param, _flag_ctx(param)) == "`WORKLOAD`"


def test_format_flag_boolean_flag_renders_opts_only() -> None:
    param = click.Option(["--yes", "-y"], is_flag=True)
    assert format_flag(param, _flag_ctx(param)) == "`--yes, -y`"


def test_format_flag_choice_joins_values_with_slash() -> None:
    """Choice values use '/' so the code span adds no raw pipes to the table cell."""
    param = click.Option(["--output"], type=click.Choice(["table", "json", "yaml"]))
    rendered = format_flag(param, _flag_ctx(param))
    assert rendered == "`--output <table/json/yaml>`"
    assert "|" not in rendered


def test_format_flag_value_option_renders_uppercase_name_metavar() -> None:
    param = click.Option(["--plan-name"])
    assert format_flag(param, _flag_ctx(param)) == "`--plan-name <PLAN_NAME>`"


def test_format_flag_multiple_option_gets_ellipsis_suffix() -> None:
    param = click.Option(["--id"], multiple=True)
    assert format_flag(param, _flag_ctx(param)) == "`--id <ID, ...>`"


# --- render_command ----------------------------------------------------------


def test_render_command_renders_heading_usage_and_flag_rows(root: click.Group) -> None:
    out = render_command(root, "machine list")
    assert out.startswith("### `synology-apm machine list`\n")
    assert "```\nUsage: synology-apm machine list" in out
    assert "| `--search <SEARCH>` | Keyword search |" in out


def test_render_command_strips_common_flags_from_table(root: click.Group) -> None:
    """COMMON_FLAGS are documented once in apm-shared, never in per-command tables."""
    out = render_command(root, "machine list")
    for flag in COMMON_FLAGS:
        assert f"| `{flag}" not in out


def test_render_command_skips_params_without_help() -> None:
    cmd = click.Command(
        "dummy",
        params=[
            click.Option(["--documented"], help="Documented flag."),
            click.Option(["--undocumented"]),
        ],
        help="Do the thing.",
    )
    grp = click.Group("root", commands={"dummy": cmd})
    out = render_command(grp, "dummy")
    assert "| `--documented <DOCUMENTED>` | Documented flag. |" in out
    assert "--undocumented" not in out


def test_render_command_appends_caution_for_yes_command(root: click.Group) -> None:
    out = render_command(root, "machine retire")
    assert "> **CAUTION:**" in out
    assert out.endswith("only once they have explicitly agreed.")


# --- render_root_help --------------------------------------------------------


def test_render_root_help_summary_plus_text_fenced_reference() -> None:
    """The first help block stays prose; the rest is preserved verbatim in a text fence."""
    grp = click.Group(
        "root",
        help="Summary line.\n\nExit codes:\n  0  success\n\nProfiles are stored in config.",
    )
    assert render_root_help(grp) == (
        "Summary line.\n\n```text\nExit codes:\n  0  success\n\nProfiles are stored in config.\n```"
    )


# --- render_global_options ---------------------------------------------------


def test_render_global_options_excludes_builtins_and_hidden(root: click.Group) -> None:
    """Typer built-ins (ROOT_OPTION_EXCLUDE) and hidden options never reach the table."""
    table = render_global_options(root)
    assert "--install-completion" not in table
    assert "--show-completion" not in table
    assert "--debug" not in table


def test_render_global_options_pairs_each_flag_with_help(root: click.Group) -> None:
    table = render_global_options(root)
    lines = table.split("\n")
    assert lines[:2] == ["| Flag | Description |", "|------|-------------|"]
    rows = lines[2:]
    assert any(row.startswith("| `--host <HOST>` |") for row in rows)
    for row in rows:
        assert row.split("|")[2].strip()


# --- assert_m365_scopes -----------------------------------------------------


def _m365_scope_data(root: click.Group) -> dict[str, Any]:
    """Build a [scopes.*] dict that exactly matches the live CLI's m365 tree."""
    m365 = find_command(root, "m365")
    assert isinstance(m365, click.Group)
    scopes = {}
    for name, group in m365.commands.items():
        has_export = isinstance(group, click.Group) and "export" in group.commands
        scopes[name] = {"example_name": "x", "identifier": "x", "has_export": has_export}
    return {"scopes": scopes}


def test_assert_m365_scopes_accepts_matching_data(root: click.Group) -> None:
    assert_m365_scopes(root, _m365_scope_data(root))


def test_assert_m365_scopes_rejects_missing_scope(root: click.Group) -> None:
    """A CLI scope absent from m365.toml fails the sync check."""
    data = _m365_scope_data(root)
    removed = sorted(data["scopes"])[0]
    del data["scopes"][removed]
    with pytest.raises(SystemExit, match="scope mismatch"):
        assert_m365_scopes(root, data)


def test_assert_m365_scopes_rejects_unknown_scope(root: click.Group) -> None:
    """A m365.toml scope with no CLI counterpart fails the sync check."""
    data = _m365_scope_data(root)
    data["scopes"]["bogus"] = {"example_name": "x", "identifier": "x", "has_export": False}
    with pytest.raises(SystemExit, match="scope mismatch"):
        assert_m365_scopes(root, data)


def test_assert_m365_scopes_rejects_wrong_has_export(root: click.Group) -> None:
    """A has_export flag contradicting the CLI's export subcommand fails the sync check."""
    data = _m365_scope_data(root)
    flipped = sorted(data["scopes"])[0]
    data["scopes"][flipped]["has_export"] = not data["scopes"][flipped]["has_export"]
    with pytest.raises(SystemExit, match=flipped):
        assert_m365_scopes(root, data)


# --- render_m365_scope_table -------------------------------------------------


M365_TEST_DATA: dict[str, Any] = {
    "canonical_scope": "exchange",
    "scopes": {
        "exchange": {"example_name": "alice@contoso.com", "identifier": "mailbox address", "has_export": True},
        "teams": {"example_name": "Marketing", "identifier": "team name", "has_export": False},
    },
}


def test_render_m365_scope_table_renders_full_table() -> None:
    assert render_m365_scope_table(M365_TEST_DATA) == (
        "## Other M365 Workload Types\n"
        "\n"
        "The commands above use `exchange` as the workload type. The same "
        "`list` / `get` / `backup` / `cancel` / `retire` / `version ...` commands "
        "work for every M365 workload type below — replace `exchange` with the "
        "desired type and adjust the NAME argument:\n"
        "\n"
        "| Workload type | NAME identifier | Export support |\n"
        "|---|---|---|\n"
        "| `exchange` (canonical, shown above) | `alice@contoso.com` (mailbox address) "
        "| Yes (`export list` / `export cancel` / `export download`) |\n"
        "| `teams` | `Marketing` (team name) | No |"
    )


# --- yaml_quote -------------------------------------------------------------


def test_yaml_quote_wraps_plain_string() -> None:
    assert yaml_quote("List and back up workloads") == '"List and back up workloads"'


def test_yaml_quote_escapes_double_quotes() -> None:
    assert yaml_quote('use "quotes" here') == '"use \\"quotes\\" here"'


def test_yaml_quote_escapes_backslashes_before_quotes() -> None:
    r"""Backslashes are escaped first so \" in the input doesn't double-escape."""
    assert yaml_quote('a\\b"c') == '"a\\\\b\\"c"'


# --- render_skill ------------------------------------------------------------


def _skill_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "apm-test-skill",
        "description": "Manage test workloads",
        "title": "Test Skill",
        "commands": ["machine list"],
    }
    data.update(overrides)
    return data


def test_render_skill_frontmatter_marker_and_commands(root: click.Group) -> None:
    out = render_skill("testgroup", _skill_data(), root, "1.2.3")
    assert out.startswith('---\nname: apm-test-skill\ndescription: "Manage test workloads"\n---\n\n<!-- AUTO-GENERATED')
    assert "Generated by scripts/generate_skills.py from scripts/skills_data/testgroup.toml" in out
    assert "synology-apm CLI version: 1.2.3 -->" in out
    assert "# Test Skill" in out
    assert "## Commands\n\n### `synology-apm machine list`" in out


def test_render_skill_optional_sections_appear_only_when_set(root: click.Group) -> None:
    bare = render_skill("testgroup", _skill_data(), root, "1.2.3")
    assert "## Tips" not in bare
    assert "## See also" not in bare
    full = render_skill(
        "testgroup",
        _skill_data(intro="Intro paragraph.", tips="Tip body.", see_also="- apm-shared"),
        root,
        "1.2.3",
    )
    assert "Intro paragraph." in full
    assert "## Tips\n\nTip body." in full
    assert "## See also\n\n- apm-shared" in full


def test_render_skill_shared_group_includes_global_sections(root: click.Group) -> None:
    out = render_skill("shared", _skill_data(), root, "1.2.3")
    assert "## Connection, Output & Exit Codes" in out
    assert "## Global Options" in out
    assert "## Common Flags" in out


def test_render_skill_m365_group_appends_scope_table(root: click.Group) -> None:
    out = render_skill("m365", _skill_data(**M365_TEST_DATA), root, "1.2.3")
    assert "## Other M365 Workload Types" in out
    assert "| `teams` | `Marketing` (team name) | No |" in out


# --- main --------------------------------------------------------------------


MINIMAL_TOML = """\
name = "apm-test-skill"
description = "Manage test workloads"
title = "Test Skill"
commands = ["machine list"]
"""


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the generator's directory constants at a hermetic tmp tree with one sidecar."""
    data_dir = tmp_path / "skills_data"
    data_dir.mkdir()
    (data_dir / "testgroup.toml").write_text(MINIMAL_TOML)
    monkeypatch.setattr(generate_skills, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(generate_skills, "SKILLS_DATA_DIR", data_dir)
    monkeypatch.setattr(generate_skills, "SKILLS_DIR", tmp_path / "skills")
    return tmp_path


def test_main_write_mode_generates_skill_file(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    generate_skills.main([])
    content = (sandbox / "skills" / "apm-test-skill" / "SKILL.md").read_text()
    assert content.startswith('---\nname: apm-test-skill\ndescription: "Manage test workloads"\n---\n')
    assert "AUTO-GENERATED FILE. DO NOT EDIT BY HAND." in content
    assert "### `synology-apm machine list`" in content
    assert "wrote skills/apm-test-skill/SKILL.md" in capsys.readouterr().out


def test_main_check_mode_passes_when_up_to_date(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    generate_skills.main([])
    generate_skills.main(["--check"])
    assert "skills/ is up to date" in capsys.readouterr().out


def test_main_check_mode_fails_on_stale_skill(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--check exits 1 and names each stale file without rewriting it."""
    generate_skills.main([])
    skill_path = sandbox / "skills" / "apm-test-skill" / "SKILL.md"
    skill_path.write_text("stale content\n")
    with pytest.raises(SystemExit) as exc_info:
        generate_skills.main(["--check"])
    assert exc_info.value.code == 1
    assert "out of date: skills/apm-test-skill/SKILL.md" in capsys.readouterr().err
    assert skill_path.read_text() == "stale content\n"


def test_main_group_regenerates_only_that_sidecar(sandbox: Path) -> None:
    other_toml = MINIMAL_TOML.replace("apm-test-skill", "apm-other-skill")
    (sandbox / "skills_data" / "othergroup.toml").write_text(other_toml)
    generate_skills.main(["--group", "testgroup"])
    assert (sandbox / "skills" / "apm-test-skill" / "SKILL.md").exists()
    assert not (sandbox / "skills" / "apm-other-skill").exists()


def test_main_unknown_group_raises_system_exit(sandbox: Path) -> None:
    with pytest.raises(SystemExit, match="bogus"):
        generate_skills.main(["--group", "bogus"])
