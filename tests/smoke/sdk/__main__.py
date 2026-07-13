"""Entry point: ``uv run python -m tests.smoke.sdk [options]``.

Drives the ``synology_apm.sdk`` public async API directly against the ``.env``-configured live
APM, running each phase in dependency order and writing Markdown reports + ``api_trace.jsonl``
to ``tests/smoke/sdk/reports/<UTC timestamp>/``.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from synology_apm.sdk import APMClient

from .._creds import load_smoke_creds
from .._driver import build_argparser
from ._client_env import load_sdk_env
from ._context import M365_SCOPES, SmokeContext
from ._report import make_report_dir, write_index
from .phases import _activity, _infra, _log, _m365, _m365_auto_backup_rule, _machine, _plan

_ORDER = ("infra", "plan", "machine", "m365", "m365_rule", "activity", "log")
_PHASES = {
    "infra": _infra,
    "plan": _plan,
    "machine": _machine,
    "m365": _m365,
    "m365_rule": _m365_auto_backup_rule,
    "activity": _activity,
    "log": _log,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_argparser(
        prog="python -m tests.smoke.sdk",
        description="Run the synology-apm-sdk live smoke test against the .env-configured APM.",
        group_choices=("all", *_ORDER),
        default_scopes=M365_SCOPES,
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    env = load_sdk_env()
    report_dir = make_report_dir(Path(__file__).resolve().parent / "reports")
    m365_scopes = tuple(s.strip() for s in args.m365_scopes.split(",") if s.strip())
    creds = load_smoke_creds()

    started_at = datetime.now(UTC)
    async with APMClient(env.host, env.username, env.password, verify_ssl=env.verify_ssl) as apm:
        ctx = SmokeContext(apm, report_dir, m365_scopes=m365_scopes)
        ctx.data["smoke_creds"] = creds
        phases = _ORDER if args.group == "all" else (args.group,)
        try:
            for phase in phases:
                print(f"[smoke.sdk] running phase: {phase}")
                await _PHASES[phase].run(ctx)
        finally:
            finished_at = datetime.now(UTC)
            write_index(
                ctx, env=env, group=args.group, m365_scopes=m365_scopes,
                started_at=started_at, finished_at=finished_at,
            )
            ctx.close()

    print(f"[smoke.sdk] report written to {report_dir}")
    failed = sum(s.unexpected for s in ctx.stats.values())
    checks_failed = sum(s.checks_failed for s in ctx.stats.values())
    if failed or checks_failed:
        print(f"[smoke.sdk] FAILED: {failed} unexpected call(s), {checks_failed} failed check(s)")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
