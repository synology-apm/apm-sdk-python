#!/usr/bin/env python3
"""Generate skills/apm-*/SKILL.md files from synology-apm CLI introspection + TOML sidecars.

Each skill combines:
  - Auto-extracted content from the live Typer/Click command tree (usage lines,
    flag tables, docstring examples) so the docs cannot drift from the CLI.
  - Hand-written content from scripts/skills_data/<group>.toml (skill triggers,
    intros, tips, cross-references) that cannot be derived from the CLI alone.

Usage:
    python scripts/generate_skills.py                  # regenerate all skills
    python scripts/generate_skills.py --group machine  # regenerate one skill
    python scripts/generate_skills.py --check          # verify skills/ is up to date (CI)
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

import click

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DATA_DIR = REPO_ROOT / "scripts" / "skills_data"
SKILLS_DIR = REPO_ROOT / "skills"

from typer.main import get_command  # noqa: E402

from synology_apm.cli.main import app  # noqa: E402

# Flags documented once in apm-shared and omitted from per-command tables.
COMMON_FLAGS = {"--output", "--verbose", "--limit", "--offset", "--page-all", "--quiet"}

# For each COMMON_FLAGS entry, the command path whose definition is used to render
# its row in apm-shared's "Common Flags" table (chosen for representative help text).
COMMON_FLAG_SOURCES = {
    "--limit": "machine list",
    "--offset": "machine list",
    "--page-all": "machine list",
    "--output": "machine list",
    "--verbose": "machine list",
    "--quiet": "machine backup",
}

# Typer/Click built-ins that are not part of the documented synology-apm CLI surface.
ROOT_OPTION_EXCLUDE = {"install_completion", "show_completion", "debug"}


def get_version() -> str:
    """Return the installed synology-apm-cli version, or a dev placeholder."""
    try:
        return metadata.version("synology-apm-cli")
    except metadata.PackageNotFoundError:
        return "0.0.0+dev"


def get_root_command() -> click.Group:
    cmd = get_command(app)
    assert isinstance(cmd, click.Group)
    return cmd


def find_command(root: click.Group, path: str) -> click.Command:
    """Resolve a space-separated command path (e.g. 'machine version list')."""
    node: click.Command = root
    for part in path.split():
        if not isinstance(node, click.Group):
            raise SystemExit(f"command path {path!r}: {part!r} is not a group")
        if part not in node.commands:
            raise SystemExit(
                f"command path {path!r}: {part!r} not found; available: {sorted(node.commands)}"
            )
        node = node.commands[part]
    return node


def clean_help(text: str | None) -> str:
    """Strip Click's no-rewrap markers and surrounding whitespace."""
    return (text or "").replace("\x08", "").strip()


def render_help_blocks(text: str | None) -> str:
    """Render a docstring as markdown: prose paragraphs plus fenced ``synology-apm ...`` examples.

    Blank-line-separated blocks are split into runs of prose lines followed by
    indented ``synology-apm ...`` example lines (a block may contain several such runs,
    e.g. alternating "Search mode:" / "Direct mode:" labels each with their own
    example line). A lone prose line ending in ":" immediately followed by an
    example run is rendered in bold as a sub-heading for that run.
    """
    cleaned = clean_help(text)
    if not cleaned:
        return ""

    def is_cmd_line(line: str) -> bool:
        return line[:1].isspace() and line.strip().startswith("synology-apm ")

    rendered = []
    for block in re.split(r"\n{2,}", cleaned):
        lines = block.split("\n")
        i = 0
        while i < len(lines):
            prose_run: list[str] = []
            while i < len(lines) and not is_cmd_line(lines[i]):
                prose_run.append(lines[i].rstrip())
                i += 1
            cmd_run: list[str] = []
            while i < len(lines) and is_cmd_line(lines[i]):
                cmd_run.append(lines[i].strip())
                i += 1
            prose = "\n".join(prose_run).strip()
            if prose:
                if cmd_run and len(prose_run) == 1 and prose.endswith(":"):
                    prose = f"**{prose}**"
                rendered.append(prose)
            if cmd_run:
                rendered.append("```sh\n" + "\n".join(cmd_run) + "\n```")
    return "\n\n".join(rendered)


def render_root_help(root: click.Group) -> str:
    """Render the root app's help text: summary line + a preformatted reference block."""
    cleaned = clean_help(root.help)
    blocks = re.split(r"\n{2,}", cleaned)
    summary, rest = blocks[0], blocks[1:]
    out = [summary, "", "```text", "\n\n".join(rest), "```"]
    return "\n".join(out)


def format_flag(param: click.Parameter, ctx: click.Context) -> str:
    """Render a single parameter as a markdown-formatted flag/argument label."""
    if isinstance(param, click.Argument):
        return f"`{param.make_metavar(ctx)}`"
    opts = ", ".join(param.opts)
    if getattr(param, "is_flag", False):
        return f"`{opts}`"
    if isinstance(param.type, click.Choice):
        # "/" (not "|") so the rendered code span doesn't introduce raw pipe
        # characters into the enclosing markdown table cell.
        metavar = "/".join(param.type.choices)
    else:
        metavar = (param.name or "value").upper()
    suffix = ", ..." if getattr(param, "multiple", False) else ""
    return f"`{opts} <{metavar}{suffix}>`"


def render_flag_table(rows: list[tuple[str, str]]) -> str:
    """Render (flag label, description) pairs as a markdown flags table."""
    lines = ["| Flag | Description |", "|------|-------------|"]
    lines.extend(f"| {flag} | {desc} |" for flag, desc in rows)
    return "\n".join(lines)


def caution_block(cmd: click.Command) -> str | None:
    """Return a CAUTION/confirmation callout for commands with --yes, or None."""
    has_yes = any(isinstance(p, click.Option) and "--yes" in p.opts for p in cmd.params)
    if not has_yes:
        return None
    if "irreversible" in clean_help(cmd.help).lower():
        return (
            "> **CAUTION:** This action is **irreversible** and normally prompts for "
            "confirmation. Confirm with the user before running it, and pass `--yes` "
            "only once they have explicitly agreed."
        )
    return (
        "> This action prompts for confirmation. Confirm with the user before running "
        "it, and pass `--yes` only once they have explicitly agreed."
    )


def render_command(root: click.Group, path: str) -> str:
    """Render one '### `synology-apm ...`' section: help text, usage, flags table, caution."""
    cmd = find_command(root, path)
    full_path = f"synology-apm {path}"
    ctx = click.Context(cmd, info_name=full_path)

    lines = [f"### `{full_path}`", ""]

    body = render_help_blocks(cmd.help)
    if body:
        lines.append(body)
        lines.append("")

    lines.append("```")
    lines.append(cmd.get_usage(ctx))
    lines.append("```")
    lines.append("")

    rows = []
    for param in cmd.params:
        if isinstance(param, click.Option) and any(o in COMMON_FLAGS for o in param.opts):
            continue
        help_text = clean_help(getattr(param, "help", None))
        if not help_text:
            continue
        rows.append((format_flag(param, ctx), help_text))

    if rows:
        lines.append(render_flag_table(rows))
        lines.append("")

    caution = caution_block(cmd)
    if caution:
        lines.append(caution)
        lines.append("")

    return "\n".join(lines).strip()


def render_common_flags_table(root: click.Group) -> str:
    """Render a flags table for COMMON_FLAGS, pulled from COMMON_FLAG_SOURCES commands.

    These flags are stripped from per-command tables (see COMMON_FLAGS) and
    documented once here instead. Sourcing each row from a real command keeps
    the table in sync with the CLI's actual help text.
    """
    rows = []
    for flag, cmd_path in COMMON_FLAG_SOURCES.items():
        cmd = find_command(root, cmd_path)
        ctx = click.Context(cmd, info_name=f"synology-apm {cmd_path}")
        for param in cmd.params:
            if isinstance(param, click.Option) and flag in param.opts:
                rows.append((format_flag(param, ctx), clean_help(getattr(param, "help", None))))
                break
        else:
            raise SystemExit(f"flag {flag!r} not found on reference command 'synology-apm {cmd_path}'")
    return render_flag_table(rows)


def render_global_options(root: click.Group) -> str:
    """Render the root command's connection/global options as a flags table."""
    ctx = click.Context(root, info_name="synology-apm")
    rows = []
    for param in root.params:
        if param.name in ROOT_OPTION_EXCLUDE or getattr(param, "hidden", False):
            continue
        help_text = clean_help(getattr(param, "help", None))
        if not help_text:
            continue
        rows.append((format_flag(param, ctx), help_text))
    return render_flag_table(rows)


def assert_m365_scopes(root: click.Group, data: dict[str, Any]) -> None:
    """Fail loudly if scripts/skills_data/m365.toml is out of sync with the CLI.

    This only checks the set of m365 scopes and each scope's has_export flag.
    It does not check that a scope's commands expose the same flags as the
    canonical scope shown in the generated skill — apm-m365/SKILL.md assumes
    list/get/backup/cancel/retire/version commands are identical in shape
    across all six workload types.
    """
    m365_group = find_command(root, "m365")
    assert isinstance(m365_group, click.Group)
    actual_scopes = set(m365_group.commands)
    declared_scopes = set(data["scopes"])
    if actual_scopes != declared_scopes:
        raise SystemExit(
            "m365.toml scope mismatch: CLI has m365 subcommands "
            f"{sorted(actual_scopes)}, but scripts/skills_data/m365.toml declares "
            f"{sorted(declared_scopes)}. Update the [scopes.*] tables."
        )
    for scope, info in data["scopes"].items():
        scope_group = m365_group.commands[scope]
        actual_has_export = isinstance(scope_group, click.Group) and "export" in scope_group.commands
        if actual_has_export != info["has_export"]:
            raise SystemExit(
                f"m365.toml scope {scope!r}: has_export={info['has_export']!r} but the "
                f"CLI's export subcommand presence is {actual_has_export!r}. "
                "Update scripts/skills_data/m365.toml."
            )


def render_m365_scope_table(data: dict[str, Any]) -> str:
    """Render the 'Other M365 Workload Types' adaptation table from [scopes.*]."""
    canonical = data["canonical_scope"]
    lines = [
        "## Other M365 Workload Types",
        "",
        f"The commands above use `{canonical}` as the workload type. The same "
        "`list` / `get` / `backup` / `cancel` / `retire` / `version ...` commands "
        f"work for every M365 workload type below — replace `{canonical}` with the "
        "desired type and adjust the NAME argument:",
        "",
        "| Workload type | NAME identifier | Export support |",
        "|---|---|---|",
    ]
    for scope, info in data["scopes"].items():
        export = "Yes (`export list` / `export cancel` / `export download`)" if info["has_export"] else "No"
        marker = " (canonical, shown above)" if scope == canonical else ""
        lines.append(f"| `{scope}`{marker} | `{info['example_name']}` ({info['identifier']}) | {export} |")
    return "\n".join(lines)


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_extra_section(root: click.Group, section: dict[str, Any]) -> str:
    parts = [f"## {section['heading']}", section["body"].strip()]
    parts.extend(render_command(root, cmd_path) for cmd_path in section.get("commands", []))
    return "\n\n".join(parts)


def render_skill(group: str, data: dict[str, Any], root: click.Group, version: str) -> str:
    """Assemble a SKILL.md as top-level blocks joined by single blank lines."""
    blocks = [
        "\n".join(
            [
                "---",
                f"name: {data['name']}",
                f"description: {yaml_quote(data['description'])}",
                "---",
            ]
        ),
        "\n".join(
            [
                "<!-- AUTO-GENERATED FILE. DO NOT EDIT BY HAND.",
                f"     Generated by scripts/generate_skills.py from scripts/skills_data/{group}.toml",
                f"     synology-apm CLI version: {version} -->",
            ]
        ),
        f"# {data['title']}",
    ]

    intro = data.get("intro", "").strip()
    if intro:
        blocks.append(intro)

    if group == "shared":
        blocks.append("## Connection, Output & Exit Codes\n\n" + render_root_help(root))
        blocks.append("## Global Options\n\n" + render_global_options(root))

        common_flags_parts = ["## Common Flags"]
        common_flags_intro = data.get("common_flags_intro", "").strip()
        if common_flags_intro:
            common_flags_parts.append(common_flags_intro)
        common_flags_parts.append(render_common_flags_table(root))
        common_flags_notes = data.get("common_flags_notes", "").strip()
        if common_flags_notes:
            common_flags_parts.append(common_flags_notes)
        blocks.append("\n\n".join(common_flags_parts))

        security_rules = data.get("security_rules", "").strip()
        if security_rules:
            blocks.append("## Security Rules\n\n" + security_rules)

    blocks.extend(render_extra_section(root, section) for section in data.get("extra_sections", []))

    command_blocks = ["## Commands"]
    command_blocks.extend(render_command(root, cmd_path) for cmd_path in data.get("commands", []))
    blocks.append("\n\n".join(command_blocks))

    if group == "m365":
        blocks.append(render_m365_scope_table(data))

    tips = data.get("tips", "").strip()
    if tips:
        blocks.append("## Tips\n\n" + tips)

    see_also = data.get("see_also", "").strip()
    if see_also:
        blocks.append("## See also\n\n" + see_also)

    return "\n\n".join(blocks) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify skills/ is up to date; write nothing")
    parser.add_argument("--group", help="Regenerate only the given sidecar group (e.g. 'machine')")
    args = parser.parse_args(argv)

    root = get_root_command()
    version = get_version()

    toml_files = sorted(SKILLS_DATA_DIR.glob("*.toml"))
    if args.group:
        toml_files = [f for f in toml_files if f.stem == args.group]
        if not toml_files:
            raise SystemExit(f"No sidecar found for group {args.group!r} in {SKILLS_DATA_DIR}")

    out_of_date = []
    for toml_file in toml_files:
        data = tomllib.loads(toml_file.read_text())
        if toml_file.stem == "m365":
            assert_m365_scopes(root, data)

        content = render_skill(toml_file.stem, data, root, version)
        out_path = SKILLS_DIR / data["name"] / "SKILL.md"

        if args.check:
            existing = out_path.read_text() if out_path.exists() else None
            if existing != content:
                out_of_date.append(out_path)
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content)
        print(f"wrote {out_path.relative_to(REPO_ROOT)}")

    if args.check:
        if out_of_date:
            for path in out_of_date:
                print(f"out of date: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            raise SystemExit(1)
        print("skills/ is up to date")


if __name__ == "__main__":
    main()
