"""Report directory creation and ``index.md`` run-summary writer (CLI tool wording)."""
from __future__ import annotations

from datetime import datetime

from .._report import make_report_dir
from .._report import write_index as _write_index
from ._cli_runner import CliEnv
from ._context import DOMAINS, SmokeContext

__all__ = ["make_report_dir", "write_index"]


def write_index(
    ctx: SmokeContext,
    *,
    cli_env: CliEnv,
    group: str,
    m365_scopes: list[str],
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Write ``index.md``: run metadata, per-domain checklist, and file pointers."""
    _write_index(
        ctx.report_dir,
        title="CLI smoke test run summary",
        host=cli_env.host,
        username=cli_env.username,
        group=group,
        m365_scopes=m365_scopes,
        started_at=started_at,
        finished_at=finished_at,
        domains=DOMAINS,
        stats=ctx.stats,
        step_results=ctx.step_results,
        detail_header="API",
        include_checks=False,
        trace_note=[
            "- [api_trace.jsonl](api_trace.jsonl) — one JSON object per `--debug`-captured API call,"
            " referenced by `seq` from the checklist above",
            "",
            "> **Note:** `--debug` does not print response headers or cookies, so session tokens"
            " are never captured in `api_trace.jsonl`.",
        ],
        manual_note=(
            "Irreversible commands (`machine retire`, `m365 <scope> retire`) are excluded from"
            " this run — see [MANUAL_TESTS.md](../../MANUAL_TESTS.md) for the manual checklist."
        ),
    )
