#!/usr/bin/env python3
"""
Backup catch-up — find workloads without a recent successful backup
(overdue, last backup failed/partial/canceled, or never backed up),
trigger on-demand backups, and wait for the results.

Usage:
    python backup_catchup.py --category machine
    python backup_catchup.py --category machine --max-age 3
    python backup_catchup.py --category machine --max-age 3 --dry-run
    python backup_catchup.py --category m365 --m365-service exchange
    python backup_catchup.py --category m365 --m365-service exchange --m365-service onedrive
    python backup_catchup.py --category all
    python backup_catchup.py --category all --m365-service exchange
    python backup_catchup.py --category machine --timeout 600
    python backup_catchup.py --category machine --never-backed-up

All progress messages go to stderr; use -o csv or -o json to send only the final results to stdout:
    python backup_catchup.py --category machine -o csv > results.csv
    python backup_catchup.py --category machine -o json > results.json

Environment variables (see .env.example and examples/README.md):
    APM_HOST          hostname or IP (supports host:port)
    APM_USERNAME      account
    APM_PASSWORD      password
    APM_NO_VERIFY_SSL set to true to skip SSL verification (self-signed certificate environments)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime, timedelta

from _common import (
    add_category_args,
    add_output_arg,
    add_profile_arg,
    category_label,
    collect_workloads,
    fmt_dt,
    interruptible_sleep,
    make_client,
    prompt_yes_no,
    register_interrupt,
    resolve_m365_services,
    run_main,
    unregister_interrupt,
    workload_type_label,
)

from synology_apm.sdk import (
    APMClient,
    APMError,
    BackupActivityStatus,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    WorkloadStatus,
)

TERMINAL_STATUSES = {
    BackupActivityStatus.SUCCESS,
    BackupActivityStatus.FAILED,
    BackupActivityStatus.PARTIAL,
    BackupActivityStatus.CANCELED,
}
_NEEDS_RETRY = {WorkloadStatus.FAILED, WorkloadStatus.PARTIAL, WorkloadStatus.CANCELED}
POLL_INTERVAL_SEC = 15
_MAX_CONCURRENT_POLL_REQUESTS = 10


def _reason(wl: MachineWorkload | M365Workload, never_backed_up_only: bool) -> str:
    if wl.last_backup_at is None:
        return "never backed up"
    if not never_backed_up_only and wl.status in _NEEDS_RETRY:
        return f"last backup {wl.status.value}"
    return "overdue"


def _is_stale(
    wl: MachineWorkload | M365Workload,
    cutoff: datetime,
    never_backed_up_only: bool,
) -> bool:
    if wl.last_backup_at is None:
        return True
    return not never_backed_up_only and (wl.status in _NEEDS_RETRY or wl.last_backup_at < cutoff)


async def _poll_one(
    apm: APMClient,
    wl: MachineWorkload | M365Workload,
    triggered_at: datetime,
    sem: asyncio.Semaphore,
) -> BackupActivityStatus | None:
    async with sem:
        acts, _ = await apm.activities.backup.list(
            workload=wl,
            since=triggered_at,
            history=True,
        )
    terminal = next((a for a in acts if a.status in TERMINAL_STATUSES), None)
    return terminal.status if terminal else None


async def _poll_all(
    apm: APMClient,
    triggered: list[tuple[MachineWorkload | M365Workload, datetime]],
    timeout_sec: int,
    interrupt: asyncio.Event,
    sem: asyncio.Semaphore,
) -> dict[str, BackupActivityStatus | None]:
    """Poll all triggered workloads until all complete, timeout, or interrupt."""
    pending = list(triggered)
    results: dict[str, BackupActivityStatus | None] = {}
    deadline = datetime.now(UTC) + timedelta(seconds=timeout_sec)
    iteration = 0

    while pending and datetime.now(UTC) < deadline and not interrupt.is_set():
        iteration += 1
        remaining = int((deadline - datetime.now(UTC)).total_seconds())
        print(
            f"  [{iteration:02d}] Waiting for {len(pending)} workload(s)... {remaining}s remaining",
            file=sys.stderr,
        )
        if await interruptible_sleep(POLL_INTERVAL_SEC, interrupt):
            break

        statuses: list[BackupActivityStatus | None] = list(
            await asyncio.gather(*(_poll_one(apm, wl, t, sem) for wl, t in pending))
        )
        still_pending: list[tuple[MachineWorkload | M365Workload, datetime]] = []
        for (wl, triggered_at), status in zip(pending, statuses, strict=True):
            if status is not None:
                results[wl.name] = status
            else:
                still_pending.append((wl, triggered_at))
        pending = still_pending

    for wl, _ in pending:
        results[wl.name] = None

    return results


async def run(
    threshold_days: int,
    dry_run: bool,
    yes: bool,
    timeout_sec: int,
    never_backed_up_only: bool,
    category: str,
    m365_services: list[M365WorkloadType] | None,
    output_format: str,
    profile: str | None = None,
) -> int:
    now    = datetime.now(UTC)
    cutoff = now - timedelta(days=threshold_days)
    criterion = (
        "never backed up"
        if never_backed_up_only
        else f"not backed up successfully in > {threshold_days} days"
    )

    results: dict[str, BackupActivityStatus | None] = {}
    wl_info: dict[str, tuple[str, str]] = {}  # name → (cat_label, wtype_label)

    print("Fetching workloads...", file=sys.stderr)
    async with make_client(profile=profile) as apm:
        workloads, total = await collect_workloads(apm, category, m365_services, is_retired=False)

        stale = [wl for wl in workloads if _is_stale(wl, cutoff, never_backed_up_only)]

        if not stale:
            print(f"No workloads {criterion} (queried {total} total).", file=sys.stderr)
            return 0

        # ── Candidate list ──────────────────────────────────────────
        col_w = max(len(wl.name) for wl in stale)
        reasons = {wl.name: _reason(wl, never_backed_up_only) for wl in stale}
        reason_w = max(len(r) for r in reasons.values())
        sep_w = col_w + 35 + reason_w  # type(10) + last backup(19) + reason + separators
        print(f"Found {len(stale)} workload(s) {criterion} ({total} total):", file=sys.stderr)
        print(file=sys.stderr)
        print(f"  {'Name':<{col_w}}  {'Type':<10}  {'Last Backup':<19}  Reason", file=sys.stderr)
        print(f"  {'-' * sep_w}", file=sys.stderr)
        for wl in stale:
            last_backup = fmt_dt(wl.last_backup_at, default="never")
            print(
                f"  {wl.name:<{col_w}}  {workload_type_label(wl):<10}  {last_backup:<19}  {reasons[wl.name]}",
                file=sys.stderr,
            )

        if dry_run:
            print("\n[dry-run] No backups triggered.", file=sys.stderr)
            return 0

        if not yes and not await prompt_yes_no(f"\nAbout to trigger {len(stale)} backup(s). Continue? [y/N] "):
            print("Cancelled.", file=sys.stderr)
            return 0

        # ── Trigger backups ─────────────────────────────────────────
        print("\nTriggering backups...", file=sys.stderr)
        triggered: list[tuple[MachineWorkload | M365Workload, datetime]] = []
        for wl in stale:
            try:
                t = datetime.now(UTC)
                if isinstance(wl, M365Workload):
                    await apm.m365.workloads.backup_now(wl)
                else:
                    await apm.machine.workloads.backup_now(wl)
                triggered.append((wl, t))
                wl_info[wl.name] = (category_label(wl), workload_type_label(wl))
                print(f"  [OK]  {wl.name}", file=sys.stderr)
            except APMError as e:
                print(f"  [!!]  {wl.name}: {e}", file=sys.stderr)

        if not triggered:
            print("All triggers failed. Exiting.", file=sys.stderr)
            return 0

        # ── Poll for progress ───────────────────────────────────────
        print(
            f"\nWaiting for {len(triggered)} backup(s) to complete "
            f"(poll interval: {POLL_INTERVAL_SEC}s, timeout: {timeout_sec}s)",
            file=sys.stderr,
        )
        poll_sem = asyncio.Semaphore(_MAX_CONCURRENT_POLL_REQUESTS)
        interrupt = asyncio.Event()
        loop = asyncio.get_running_loop()
        register_interrupt(loop, interrupt)
        try:
            results = await _poll_all(apm, triggered, timeout_sec, interrupt, poll_sem)
        finally:
            unregister_interrupt(loop)

    # ── Final results ─────────────────────────────────────────────────
    success_count = sum(1 for s in results.values() if s == BackupActivityStatus.SUCCESS)
    failed_count  = sum(1 for s in results.values() if s is not None and s != BackupActivityStatus.SUCCESS)
    timeout_count = sum(1 for s in results.values() if s is None)

    if output_format == "csv":
        csv_headers: list[str] = ["name"]
        if category == "all":
            csv_headers.append("category")
        csv_headers.extend(["type", "result"])
        writer = csv.writer(sys.stdout)
        writer.writerow(csv_headers)
        for name, status in results.items():
            cat_label, wtype_label = wl_info.get(name, ("", ""))
            result_str = "timed_out" if status is None else status.value
            row: list[str] = [name]
            if category == "all":
                row.append(cat_label)
            row.extend([wtype_label, result_str])
            writer.writerow(row)
    elif output_format == "json":
        json_rows = [
            {
                "name":     name,
                "category": wl_info[name][0] if name in wl_info else None,
                "type":     wl_info[name][1] if name in wl_info else None,
                "result":   "timed_out" if status is None else status.value,
            }
            for name, status in results.items()
        ]
        print(json.dumps({
            "results": json_rows,
            "summary": {
                "success":   success_count,
                "failed":    failed_count,
                "timed_out": timeout_count,
            },
        }, indent=2))
    else:
        result_col_w = max(len(name) for name in results) if results else 20
        print(f"\n{'─' * (result_col_w + 20)}")
        for name, status in results.items():
            if status is None:
                marker, label = "?", "timed out (still running)"
            elif status == BackupActivityStatus.SUCCESS:
                marker, label = "v", "success"
            else:
                marker, label = "!", f"failed ({status.value})"
            print(f"  [{marker}]  {name:<{result_col_w}}  {label}")
        print(f"{'─' * (result_col_w + 20)}")
        print(f"  Success: {success_count}  Failed: {failed_count}  Timed out: {timeout_count}")

    return 1 if failed_count or timeout_count else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_category_args(parser, verb="process")
    parser.add_argument(
        "--max-age", type=int, default=1, metavar="DAYS",
        help="Max age of last successful backup in days; trigger overdue workloads beyond this threshold (default: 1)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List matching workloads only; do not trigger or prompt",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt and trigger backups immediately",
    )
    parser.add_argument(
        "--timeout", type=int, default=1800, metavar="SEC",
        help="Maximum seconds to wait for completion (default: 1800)",
    )
    parser.add_argument(
        "--never-backed-up", action="store_true",
        help="Only process workloads that have never been backed up (ignores --max-age)",
    )
    add_output_arg(parser)
    add_profile_arg(parser)
    args = parser.parse_args()

    m365_services = resolve_m365_services(parser, args)

    run_main(run(
        args.max_age, args.dry_run, args.yes, args.timeout, args.never_backed_up,
        args.category, m365_services, args.output,
        profile=args.profile,
    ))


if __name__ == "__main__":
    main()
