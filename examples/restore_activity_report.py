#!/usr/bin/env python3
"""
Restore activity report — groups restore activities into succeeded / failed (includes
partial and canceled) / in-progress for a given date (default: yesterday).

Usage:
    python restore_activity_report.py
    python restore_activity_report.py --category machine
    python restore_activity_report.py -o csv
    python restore_activity_report.py --date 2026-05-07
    python restore_activity_report.py --category m365 -o json

Environment variables (can be set in .env):
    APM_HOST          hostname or IP (supports host:port)
    APM_USERNAME      account
    APM_PASSWORD      password
    APM_NO_VERIFY_SSL set to true to skip SSL verification (self-signed certificate environments)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from typing import Any

from _common import (
    add_output_arg,
    fmt_dt,
    fmt_duration,
    make_client,
    paginate,
    run_main,
)

from synology_apm.sdk import (
    RestoreActivity,
    RestoreActivityStatus,
    WorkloadCategory,
)

_SUCCESS = {RestoreActivityStatus.SUCCESS}
_FAILED  = {
    RestoreActivityStatus.FAILED,
    RestoreActivityStatus.PARTIAL,
    RestoreActivityStatus.CANCELED,
}
_ONGOING = {
    RestoreActivityStatus.PREPARING,
    RestoreActivityStatus.RESTORING,
    RestoreActivityStatus.CANCELING,
    RestoreActivityStatus.READY_FOR_MIGRATE,
    RestoreActivityStatus.MIGRATE_VM_MANUALLY,
    RestoreActivityStatus.MIGRATING,
}
_FAILED_RESULTS  = {s.value for s in _FAILED}
_ONGOING_RESULTS = {s.value for s in _ONGOING}


def _merge_activities(
    completed: list[RestoreActivity],
    ongoing: list[RestoreActivity],
) -> dict[str, RestoreActivity]:
    _seen: dict[str, RestoreActivity] = {}
    for act in completed:
        _seen.setdefault(act.activity_id, act)
    for act in ongoing:
        _seen[act.activity_id] = act
    return _seen


def _build_row(act: RestoreActivity) -> dict[str, Any]:
    result = "success" if act.status in _SUCCESS else act.status.value
    return {
        "workload_id":      act.workload_id,
        "workload_name":    act.workload_name,
        "workload_type":    act.workload_type.value,
        "result":           result,
        "operator":         act.operator,
        "restore_type":     act.restore_type.value if act.restore_type else None,
        "destination":      act.restore_destination,
        "started_at":       act.started_at,
        "duration_seconds": act.duration_seconds,
    }


async def run(
    report_date: date,
    category: str,
    output_format: str,
) -> None:
    day_start = datetime(report_date.year, report_date.month, report_date.day).astimezone()
    day_end   = day_start + timedelta(days=1)

    print(f"Fetching restore activities for {report_date}...", file=sys.stderr)
    async with make_client() as apm:
        completed, _ = await paginate(
            lambda limit, offset: apm.activities.restore.list(
                since=day_start,
                until=day_end,
                history=True,
                limit=limit,
                offset=offset,
            )
        )
        ongoing, _ = await paginate(
            lambda limit, offset: apm.activities.restore.list(
                since=day_start,
                until=day_end,
                history=False,
                limit=limit,
                offset=offset,
            )
        )
        # Merge by activity_id: completed first, ongoing overrides (more current status).
        # The two sets are disjoint in practice; dedup handles edge cases.
        activities = list(_merge_activities(completed, ongoing).values())

    # Filter by category
    if category == "machine":
        activities = [a for a in activities if a.category == WorkloadCategory.MACHINE]
    elif category == "m365":
        activities = [a for a in activities if a.category == WorkloadCategory.M365]

    rows: list[dict[str, Any]] = [_build_row(act) for act in activities]

    output_fields: list[str] = [
        "workload_id", "workload_name", "workload_type",
        "result", "operator", "restore_type", "destination",
        "started_at", "duration_seconds",
    ]

    if output_format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                k: (
                    "" if row[k] is None
                    else (row[k].astimezone().isoformat() if isinstance(row[k], datetime) else row[k])
                )
                for k in output_fields
            })
        return

    if output_format == "json":
        def _json_row(r: dict[str, Any]) -> dict[str, Any]:
            return {
                k: (r[k].astimezone().isoformat() if isinstance(r[k], datetime) else r[k])
                for k in output_fields
            }
        success_rows    = [r for r in rows if r["result"] == "success"]
        failure_rows    = [r for r in rows if r["result"] in _FAILED_RESULTS]
        inprogress_rows = [r for r in rows if r["result"] in _ONGOING_RESULTS]
        print(json.dumps({
            "date":        str(report_date),
            "total":       len(rows),
            "successes":   [_json_row(r) for r in success_rows],
            "failures":    [_json_row(r) for r in failure_rows],
            "in_progress": [_json_row(r) for r in inprogress_rows],
        }, indent=2))
        return

    # Table output
    success_rows    = sorted([r for r in rows if r["result"] == "success"],
                             key=lambda r: r["workload_name"])
    failure_rows    = sorted([r for r in rows if r["result"] in _FAILED_RESULTS],
                             key=lambda r: r["workload_name"])
    inprogress_rows = [r for r in rows if r["result"] in _ONGOING_RESULTS]

    print(f"\n{'='*66}")
    print(f"  Restore Report: {report_date}  ({len(rows)} activities total)")
    print(f"{'='*66}")
    print(
        f"  Success: {len(success_rows)}"
        f"  Failed/Partial: {len(failure_rows)}"
        f"  In progress: {len(inprogress_rows)}"
    )

    if failure_rows:
        print(f"\n[!] Failed / Partial ({len(failure_rows)})")
        print(f"  {'Name':<30} {'Type':<14} {'Status':<10} {'Duration':>10}  {'Started'}")
        print(f"  {'-'*78}")
        for r in failure_rows:
            started = fmt_dt(r["started_at"], default="—")
            dur     = fmt_duration(r["duration_seconds"])
            print(f"  {r['workload_name']:<30} {r['workload_type']:<14} {r['result']:<10} {dur:>10}  {started}")

    if inprogress_rows:
        print(f"\n[~] In Progress ({len(inprogress_rows)})")
        for r in inprogress_rows:
            print(f"  {r['workload_name']:<30} {r['result']}")

    if success_rows:
        print(f"\n[v] Succeeded ({len(success_rows)})")
        print(f"  {'Name':<30} {'Type':<14} {'Operator':<20} {'Duration':>10}  {'Started'}")
        print(f"  {'-'*88}")
        for r in success_rows:
            started  = fmt_dt(r["started_at"], default="—")
            dur      = fmt_duration(r["duration_seconds"])
            operator = r["operator"] or "—"
            print(f"  {r['workload_name']:<30} {r['workload_type']:<14} {operator:<20} {dur:>10}  {started}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--category", choices=["machine", "m365", "all"], default="all",
        help="Workload category filter: machine, m365, or all (default: all)",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Report date (default: yesterday)",
    )
    add_output_arg(parser)
    args = parser.parse_args()

    report_date = (
        date.fromisoformat(args.date) if args.date
        else date.today() - timedelta(days=1)
    )

    run_main(run(report_date, args.category, args.output))


if __name__ == "__main__":
    main()
