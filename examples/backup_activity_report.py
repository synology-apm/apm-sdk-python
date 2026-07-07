#!/usr/bin/env python3
"""
Daily backup activity report — groups workloads into succeeded / failed (includes partial and
canceled) / in-progress / no-activity (no backup recorded that day) for a given date
(default: yesterday).

Usage:
    python backup_activity_report.py
    python backup_activity_report.py -o csv
    python backup_activity_report.py --date 2026-05-07
    python backup_activity_report.py --category machine
    python backup_activity_report.py --category machine --retired
    python backup_activity_report.py --category m365 --m365-service exchange
    python backup_activity_report.py --category m365 --m365-service exchange --m365-service onedrive

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
from operator import itemgetter
from typing import Any

from _common import (
    add_category_args,
    add_output_arg,
    category_label,
    collect_workloads,
    fmt_bytes,
    fmt_dt,
    fmt_duration,
    make_client,
    paginate,
    resolve_m365_services,
    run_main,
    workload_type_label,
)

from synology_apm.sdk import (
    BackupActivity,
    BackupActivityStatus,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    MachineWorkloadType,
)

_SUCCESS = {BackupActivityStatus.SUCCESS}
_FAILED  = {BackupActivityStatus.FAILED, BackupActivityStatus.PARTIAL, BackupActivityStatus.CANCELED}
_ONGOING = {BackupActivityStatus.BACKING_UP, BackupActivityStatus.QUEUING, BackupActivityStatus.CANCELING}


async def run(
    report_date: date,
    retired_only: bool,
    category: str,
    m365_services: list[M365WorkloadType] | None,
    output_format: str,
) -> None:
    day_start = datetime(report_date.year, report_date.month, report_date.day).astimezone()
    day_end   = day_start + timedelta(days=1)

    rows: list[dict[str, Any]] = []

    # Resolve which M365 types to query (None means all types).
    m365_types_to_query = m365_services if m365_services is not None else list(M365WorkloadType)

    print(f"Fetching backup activities for {report_date}...", file=sys.stderr)
    async with make_client() as apm:
        # 1. Fetch completed and ongoing activities for the day, then merge into a
        #    per-workload map.  list() returns DESC by started_at; setdefault on the
        #    completed set keeps the most recent completed activity per workload.
        #    Ongoing activities are then written with direct assignment so they take
        #    priority — if a workload is currently backing up, that status is shown.
        _machine_types = (
            [MachineWorkloadType.PC, MachineWorkloadType.PS,
             MachineWorkloadType.VM, MachineWorkloadType.FS]
            if category in ("machine", "all") else None
        )
        _m365_types = m365_types_to_query if category in ("m365", "all") else None
        completed, _ = await paginate(
            lambda limit, offset: apm.activities.backup.list(
                machine_types=_machine_types,
                m365_types=_m365_types,
                since=day_start,
                until=day_end,
                history=True,
                limit=limit,
                offset=offset,
            )
        )
        ongoing, _ = await paginate(
            lambda limit, offset: apm.activities.backup.list(
                machine_types=_machine_types,
                m365_types=_m365_types,
                since=day_start,
                until=day_end,
                history=False,
                limit=limit,
                offset=offset,
            )
        )
        act_map: dict[str, BackupActivity] = {}
        for act in completed:
            act_map.setdefault(act.workload_id, act)
        for act in ongoing:
            act_map[act.workload_id] = act  # ongoing overrides: more current status

        # 2. Collect workloads and tally into rows.
        workloads, total_workloads = await collect_workloads(
            apm, category, m365_services,
            is_retired=retired_only,
        )

    def _tally(wl: MachineWorkload | M365Workload) -> None:
        base   = {"workload_id": wl.workload_id, "name": wl.name,
                  "category": category_label(wl), "type": workload_type_label(wl),
                  "last_backup_at": wl.last_backup_at}
        wl_act = act_map.get(wl.workload_id)
        if wl_act is None or wl_act.status not in (_SUCCESS | _FAILED | _ONGOING):
            rows.append({**base, "result": "no_activity", "duration_seconds": None,
                         "transferred_bytes": None, "progress": None})
        elif wl_act.status in _SUCCESS:
            rows.append({**base, "result": "success",
                         "duration_seconds": wl_act.duration_seconds,
                         "transferred_bytes": wl_act.data_transferred_bytes or 0,
                         "progress": None})
        elif wl_act.status in _FAILED:
            rows.append({**base, "result": wl_act.status.value,
                         "duration_seconds": wl_act.duration_seconds,
                         "transferred_bytes": wl_act.data_transferred_bytes,
                         "progress": None})
        else:  # _ONGOING
            rows.append({**base, "result": wl_act.status.value, "duration_seconds": None,
                         "transferred_bytes": None, "progress": wl_act.progress or 0})

    for wl in workloads:
        _tally(wl)

    # ── Output ─────────────────────────────────────────────────────────
    include_category_col = category == "all"
    output_fields: list[str] = ["workload_id", "name"]
    if include_category_col:
        output_fields.append("category")
    output_fields.extend(["type", "result", "duration_seconds", "transferred_bytes", "last_backup_at"])

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
        print(json.dumps({
            "date":            str(report_date),
            "total_workloads": total_workloads,
            "successes":   [_json_row(r) for r in rows if r["result"] == "success"],
            "failures":    [_json_row(r) for r in rows if r["result"] in {"failed", "partial", "canceled"}],
            "in_progress": [_json_row(r) for r in rows if r["result"] in {"backing_up", "queuing", "canceling"}],
            "no_activity": [_json_row(r) for r in rows if r["result"] == "no_activity"],
        }, indent=2))
        return

    success_rows    = sorted([r for r in rows if r["result"] == "success"], key=itemgetter("name"))
    failure_rows    = sorted(
        [r for r in rows if r["result"] in {"failed", "partial", "canceled"}], key=itemgetter("name")
    )
    inprogress_rows = [r for r in rows if r["result"] in {"backing_up", "queuing", "canceling"}]
    noactivity_rows = sorted([r for r in rows if r["result"] == "no_activity"], key=itemgetter("name"))

    print(f"\n{'='*66}")
    print(f"  Backup Report: {report_date}  ({total_workloads} workloads total)")
    print(f"{'='*66}")
    print(
        f"  Success: {len(success_rows)}"
        f"  Failed/Partial: {len(failure_rows)}"
        f"  In progress: {len(inprogress_rows)}"
        f"  No activity: {len(noactivity_rows)}"
    )

    if failure_rows:
        print(f"\n[!] Failed / Partial ({len(failure_rows)})")
        print(f"  {'Name':<30} {'Type':<10} {'Status':<10} {'Duration':>10}  {'Last Backup'}")
        print(f"  {'-'*76}")
        for r in failure_rows:
            last = fmt_dt(r["last_backup_at"], default="never")
            dur  = fmt_duration(r["duration_seconds"])
            print(f"  {r['name']:<30} {r['type']:<10} {r['result']:<10} {dur:>10}  {last}")

    if inprogress_rows:
        print(f"\n[~] In Progress ({len(inprogress_rows)})")
        for r in inprogress_rows:
            print(f"  {r['name']:<30} {r['result']} {r['progress']}%")

    if noactivity_rows:
        print(f"\n[-] No backup activity today ({len(noactivity_rows)})")
        print(f"  {'Name':<30} {'Type':<10} {'Last Backup'}")
        print(f"  {'-'*53}")
        for r in noactivity_rows:
            last = fmt_dt(r["last_backup_at"], default="never")
            print(f"  {r['name']:<30} {r['type']:<10} {last}")

    if success_rows:
        print(f"\n[v] Succeeded ({len(success_rows)})")
        print(f"  {'Name':<30} {'Type':<10} {'Duration':>10}  {'Transferred':>10}  {'Last Backup'}")
        print(f"  {'-'*83}")
        for r in success_rows:
            last = fmt_dt(r["last_backup_at"], default="—")
            dur  = fmt_duration(r["duration_seconds"])
            xfr  = fmt_bytes(r["transferred_bytes"])
            print(f"  {r['name']:<30} {r['type']:<10} {dur:>10}  {xfr:>10}  {last}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_category_args(parser, verb="report", default="all")
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Report date (default: yesterday)",
    )
    parser.add_argument(
        "--retired", dest="retired_only", action="store_true",
        help="Show only retired workloads (instead of protected)",
    )
    add_output_arg(parser)
    args = parser.parse_args()

    m365_services = resolve_m365_services(parser, args)

    report_date = (
        date.fromisoformat(args.date) if args.date
        else date.today() - timedelta(days=1)
    )

    run_main(run(report_date, args.retired_only, args.category, m365_services, args.output))


if __name__ == "__main__":
    main()
