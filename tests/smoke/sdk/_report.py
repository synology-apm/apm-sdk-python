"""Report directory creation and index.md run-summary writer (SDK tool wording)."""
from __future__ import annotations

from datetime import datetime

from .._report import make_report_dir
from .._report import write_index as _write_index
from ._client_env import SdkEnv
from ._context import DOMAINS, SmokeContext

__all__ = ["make_report_dir", "write_index"]


def write_index(
    ctx: SmokeContext,
    *,
    env: SdkEnv,
    group: str,
    m365_scopes: tuple[str, ...],
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Write ``index.md``: run metadata, per-domain checklist, and file pointers."""
    _write_index(
        ctx.report_dir,
        title="SDK smoke test run summary",
        host=env.host,
        username=env.username,
        group=group,
        m365_scopes=m365_scopes,
        started_at=started_at,
        finished_at=finished_at,
        domains=DOMAINS,
        stats=ctx.stats,
        step_results=ctx.step_results,
        detail_header="Detail",
        include_checks=True,
        trace_note=[
            "- [api_trace.jsonl](api_trace.jsonl) — one JSON object per SDK-level API call, "
            "full untruncated request/response, referenced by `seq` from the domain files above",
            "",
            "> **Note:** No HTTP headers or cookies are captured, so session tokens never "
            "appear in api_trace.jsonl.",
        ],
        manual_note=(
            "Irreversible M365 operations (`retire()`, `delete()`) are excluded from this run — "
            "`retire()` is covered manually through the CLI, see "
            "[tests/smoke/cli/MANUAL_TESTS.md](../../../cli/MANUAL_TESTS.md)."
        ),
    )
