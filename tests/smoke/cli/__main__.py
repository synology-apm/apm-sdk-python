"""Entry point: ``uv run python -m tests.smoke.cli [options]``.

Drives the real ``synology-apm-cli`` CLI against the ``.env``-configured live APM, running each
phase in dependency order and writing Markdown reports + ``api_trace.jsonl`` to
``tests/smoke/cli/reports/<UTC timestamp>/``.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from .._driver import build_argparser
from ._cli_runner import CliRunner, load_cli_env
from ._context import M365_SCOPES, SmokeContext
from ._report import make_report_dir, write_index
from .phases import _activity, _config, _infra, _log, _machine, _plan, _saas_m365

# Dependency order: plan populates protection/retirement plan data used by machine's
# change-plan round trips; machine populates workload/plan data used by m365 and activity;
# infra populates the DP server list used by log.
_ORDER = ("config", "infra", "plan", "machine", "m365", "activity", "log")

_PHASES = {
    "config": _config,
    "infra": _infra,
    "machine": _machine,
    "m365": _saas_m365,
    "saas": _saas_m365,
    "activity": _activity,
    "plan": _plan,
    "log": _log,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_argparser(
        prog="python -m tests.smoke.cli",
        description="Run the synology-apm-cli CLI live smoke test against the .env-configured APM.",
        group_choices=("all", "saas", *_ORDER),
        default_scopes=M365_SCOPES,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cli_env = load_cli_env()
    runner = CliRunner(cli_env)

    report_dir = make_report_dir(Path(__file__).resolve().parent / "reports")
    m365_scopes = [s.strip() for s in args.m365_scopes.split(",") if s.strip()]

    ctx = SmokeContext(runner, cli_env, report_dir, m365_scopes=m365_scopes)

    phases = _ORDER if args.group == "all" else (("m365",) if args.group == "saas" else (args.group,))

    started_at = datetime.now(UTC)
    try:
        for phase in phases:
            print(f"[smoke.cli] running phase: {phase}")
            _PHASES[phase].run(ctx)
    finally:
        finished_at = datetime.now(UTC)
        write_index(
            ctx, cli_env=cli_env, group=args.group, m365_scopes=m365_scopes,
            started_at=started_at, finished_at=finished_at,
        )
        ctx.close()

    print(f"[smoke.cli] report written to {report_dir}")
    failed = sum(s.unexpected for s in ctx.stats.values())
    if failed:
        print(f"[smoke.cli] FAILED: {failed} command(s) exited with unexpected codes")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
