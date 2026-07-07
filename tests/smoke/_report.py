"""Shared report-directory creation and index.md rendering for the smoke-test tools.

Each tool's _report.py wraps write_index() with its own env type and wording;
the directory layout, stats table, checklist, and file-pointer sections are
rendered here so the report format has a single source of truth.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ._context import DomainStats, StepResult, step_slug


def make_report_dir(base_dir: Path) -> Path:
    """Create and return a new ``<base_dir>/<UTC timestamp>/`` report directory."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_dir = base_dir / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def write_index(
    report_dir: Path,
    *,
    title: str,
    host: str,
    username: str,
    group: str,
    m365_scopes: Sequence[str],
    started_at: datetime,
    finished_at: datetime,
    domains: Sequence[str],
    stats: dict[str, DomainStats],
    step_results: dict[str, list[StepResult]],
    detail_header: str,
    include_checks: bool,
    trace_note: list[str],
    manual_note: str,
) -> None:
    """Write ``index.md``: run metadata, per-domain stats and checklist, file pointers."""
    lines: list[str] = [
        f"# {title}",
        "",
        f"- host: {host}",
        f"- username: {username}",
        f"- group: {group}",
        f"- m365 scopes: {', '.join(m365_scopes)}",
        f"- started: {started_at.isoformat()}",
        f"- finished: {finished_at.isoformat()}",
        "",
        "## Per-domain results",
        "",
    ]
    if include_checks:
        lines += [
            "| Domain | Ran | Skipped | N/A | Failed | Checks passed | Checks failed |",
            "|---|---|---|---|---|---|---|",
        ]
        for domain in domains:
            s = stats[domain]
            lines.append(
                f"| {domain} | {s.ran} | {s.skipped} | {s.na} | {s.unexpected} | "
                f"{s.checks_passed} | {s.checks_failed} |"
            )
    else:
        lines += ["| Domain | Ran | Skipped | Failed |", "|---|---|---|---|"]
        for domain in domains:
            s = stats[domain]
            lines.append(f"| {domain} | {s.ran} | {s.skipped} | {s.unexpected} |")

    lines += ["", "## Checklist", ""]
    for domain in domains:
        results = step_results[domain]
        if not results:
            continue
        lines.append(f"### {domain}")
        lines.append("")
        lines.append(f"| | Step | Result | {detail_header} |")
        lines.append("|---|---|---|---|")
        for r in results:
            icon = "✓" if (r.ok and not r.skipped) else ("−" if r.skipped else "✗")
            result_cell = r.label + (f" — {r.note}" if r.note else "")
            detail_cell = f"[→]({domain}.md#{step_slug(r.step)})" if r.has_detail else ""
            lines.append(f"| {icon} | `{r.step}` | {result_cell} | {detail_cell} |")
        lines.append("")

    lines += ["## Files", ""]
    domains_with_detail = [d for d in domains if any(r.has_detail for r in step_results[d])]
    for domain in domains_with_detail:
        lines.append(f"- [{domain}.md]({domain}.md)")
    lines += [
        *trace_note,
        "",
        "## Test data",
        "",
        "If many steps above show `skipped`, the APM test environment may be missing"
        " prerequisite data — see [TEST_DATA.md](../../TEST_DATA.md) for the checklist.",
        "",
        "## Manual tests",
        "",
        manual_note,
        "",
    ]

    (report_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")
