#!/usr/bin/env python3
"""
Bulk backup verification video export — downloads the verification video for the latest
backup version of every Physical Server (PS) and Virtual Machine (VM) workload that
passed backup verification.

Output layout:
    {output_dir}/
        {workload_name}_{workload_id_prefix}_{version_date_utc}.mp4
        download_report_{timestamp}.csv   ← written unless --dry-run or no workloads found

Usage:
    python export_verification_videos.py
    python export_verification_videos.py --yes
    python export_verification_videos.py --dry-run
    python export_verification_videos.py --workload-type vm
    python export_verification_videos.py --workload-type ps --keyword web-server
    python export_verification_videos.py --namespace apm-server-01
    python export_verification_videos.py --output-dir /mnt/videos
    python export_verification_videos.py --concurrency 5
    python export_verification_videos.py --csv /path/to/report.csv

Environment variables (can be set in .env):
    APM_HOST          hostname or IP (supports host:port)
    APM_USERNAME      account
    APM_PASSWORD      password
    APM_NO_VERIFY_SSL set to true to skip SSL verification (self-signed certificate environments)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime

from _common import (
    Progress,
    _remove_quietly,
    fmt_bytes,
    fmt_dt,
    fmt_speed,
    make_client,
    prompt_yes_no,
    register_interrupt,
    run_main,
    safe_path,
    unregister_interrupt,
    workload_type_label,
)

from synology_apm.sdk import (
    APMClient,
    APMError,
    MachineWorkload,
    MachineWorkloadType,
    ResourceNotFoundError,
    VerifyStatus,
    WorkloadVersion,
)

_PS = MachineWorkloadType.PS
_VM = MachineWorkloadType.VM

# Bound the per-workload latest-version lookups during classification. These are
# light API calls, so this is independent of --concurrency (which tunes downloads).
_MAX_CONCURRENT_VERSION_REQUESTS = 10

_CSV_FIELDS = [
    "workload_name", "workload_type", "version_date",
    "status", "size_bytes", "note", "dest_path",
]


def _dest_filename(wl: MachineWorkload, version: WorkloadVersion) -> str:
    safe_name = safe_path(wl.name)
    short_id  = wl.workload_id[:8]
    date_str  = version.created_at.strftime("%Y%m%d_%H%M%S")
    return f"{safe_name}_{short_id}_{date_str}.mp4"


@dataclass
class _DownloadJob:
    workload: MachineWorkload
    version: WorkloadVersion
    dest_path: str
    outcome: str = ""
    outcome_msg: str = ""
    bytes_saved: int | None = None
    started_at: datetime | None = None


@dataclass
class _SkippedWorkload:
    workload: MachineWorkload
    version: WorkloadVersion | None
    reason: str
    csv_status: str


# ── Phase 1: list workloads ──────────────────────────────────────────────────

async def _list_all_workloads(
    apm: APMClient,
    workload_types: list[MachineWorkloadType],
    keyword: str | None,
    namespace: str | None,
) -> list[MachineWorkload]:
    results: list[MachineWorkload] = []
    offset = 0
    while True:
        page, total = await apm.machine.workloads.list(
            workload_types=workload_types,
            is_retired=False,
            name_contains=keyword,
            namespace=namespace,
            limit=500,
            offset=offset,
        )
        results.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return results


# ── Phase 2: classify workload (skip or download) ────────────────────────────

async def _classify_workload(
    apm: APMClient,
    wl: MachineWorkload,
    output_dir: str,
    sem: asyncio.Semaphore,
) -> tuple[_DownloadJob | None, _SkippedWorkload | None]:
    """Fetch the latest version and decide whether to download or skip."""
    try:
        async with sem:
            version = await apm.machine.workloads.get_latest_version(wl)
    except ResourceNotFoundError:
        return None, _SkippedWorkload(
            workload=wl, version=None,
            reason="no backup version found",
            csv_status="skipped_no_version",
        )
    except APMError as e:
        return None, _SkippedWorkload(
            workload=wl, version=None,
            reason=f"version lookup failed: {e.message}",
            csv_status="error",
        )

    vs = version.verify_status
    if vs is None:
        return None, None  # verification not enabled — silent skip

    if vs == VerifyStatus.SUCCESS:
        dest_path = os.path.join(output_dir, _dest_filename(wl, version))
        return _DownloadJob(workload=wl, version=version, dest_path=dest_path), None

    if vs == VerifyStatus.NOT_ENABLED:
        return None, None  # verification not configured — silent skip

    reason_map: dict[VerifyStatus, tuple[str, str]] = {
        VerifyStatus.FAILED:        ("verify failed",              "skipped_verify_failed"),
        VerifyStatus.VERIFYING:     ("verifying (in progress)",    "skipped_verifying"),
        VerifyStatus.WAITING:       ("verification waiting",       "skipped_verifying"),
        VerifyStatus.CANCELED:      ("verification canceled",      "skipped_verify_canceled"),
        VerifyStatus.NOT_SUPPORTED: ("verification not supported", "skipped_not_supported"),
        VerifyStatus.PARTIAL:       ("partial verification",       "skipped_verify_partial"),
    }
    reason, csv_status = reason_map.get(vs, (f"verify_status={vs.value}", "skipped_unknown"))
    return None, _SkippedWorkload(workload=wl, version=version, reason=reason, csv_status=csv_status)


# ── Phase 3: download ────────────────────────────────────────────────────────

async def _download_one(
    apm: APMClient,
    job: _DownloadJob,
    dl_sem: asyncio.Semaphore,
    progress: Progress,
    interrupt: asyncio.Event,
) -> None:
    async with dl_sem:
        if interrupt.is_set():
            job.outcome     = "interrupted"
            job.outcome_msg = "interrupted before download started"
            progress.done += 1
            progress.print_progress()
            return

        progress.downloading += 1
        job.started_at = datetime.now()
        wl_label = f"{job.workload.name:<22}"

        try:
            url = await apm.machine.workloads.get_verification_video_url(
                job.workload, job.version
            )
            os.makedirs(os.path.dirname(job.dest_path), exist_ok=True)
            await apm.download_file(url, job.dest_path)

            dl_secs         = (datetime.now() - job.started_at).total_seconds()
            job.bytes_saved = os.path.getsize(job.dest_path)
            job.outcome     = "ok"
            progress.clear_progress()
            version_date = fmt_dt(job.version.created_at)
            print(
                f"  [Done]    {wl_label}  {version_date}"
                f"   {fmt_bytes(job.bytes_saved)}"
                f"  ({fmt_speed(job.bytes_saved, dl_secs)})",
                file=sys.stderr,
            )
        except (APMError, OSError) as e:
            job.outcome     = "failed"
            job.outcome_msg = (
                f"download error: {e.message}" if isinstance(e, APMError) else f"local I/O error: {e}"
            )
            _remove_quietly(job.dest_path)
            progress.clear_progress()
            print(f"  [!!] {job.workload.name}: {job.outcome_msg}", file=sys.stderr)
        except asyncio.CancelledError:
            _remove_quietly(job.dest_path)
            job.outcome     = "interrupted"
            job.outcome_msg = "download interrupted"
            raise
        finally:
            progress.downloading -= 1
            progress.done += 1
            progress.print_progress()


# ── CSV output ────────────────────────────────────────────────────────────────

def _write_csv(
    path: str,
    jobs: list[_DownloadJob],
    skipped: list[_SkippedWorkload],
) -> None:
    rows = []
    for j in jobs:
        rows.append({
            "workload_name": j.workload.name,
            "workload_type": workload_type_label(j.workload),
            "version_date":  fmt_dt(j.version.created_at),
            "status":        "downloaded" if j.outcome == "ok" else j.outcome,
            "size_bytes":    j.bytes_saved if j.bytes_saved is not None else "",
            "note":          j.outcome_msg if j.outcome != "ok" else "",
            "dest_path":     j.dest_path if j.outcome == "ok" else "",
        })
    for s in skipped:
        rows.append({
            "workload_name": s.workload.name,
            "workload_type": workload_type_label(s.workload),
            "version_date":  fmt_dt(s.version.created_at) if s.version else "",
            "status":        s.csv_status,
            "size_bytes":    "",
            "note":          s.reason,
            "dest_path":     "",
        })
    rows.sort(key=lambda r: r["workload_name"])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


# ── Dry-run display ───────────────────────────────────────────────────────────

def _print_dry_run(jobs: list[_DownloadJob], skipped: list[_SkippedWorkload]) -> None:
    col = max((len(j.workload.name) for j in jobs), default=20)
    print("\nWorkloads with downloadable verification videos (SUCCESS):\n", file=sys.stderr)
    print(f"  {'Workload':<{col}}  Type  Version Date           Version ID", file=sys.stderr)
    print(f"  {'-'*col}  ----  ----------------------  ----------", file=sys.stderr)
    for j in sorted(jobs, key=lambda j: j.workload.name):
        date_str = fmt_dt(j.version.created_at)
        print(
            f"  {j.workload.name:<{col}}"
            f"  {workload_type_label(j.workload):<4}"
            f"  {date_str}  "
            f"  {j.version.version_id[:8]}",
            file=sys.stderr,
        )
    print(f"\n  {len(jobs)} video(s) would be downloaded.", file=sys.stderr)
    if skipped:
        print(f"  {len(skipped)} workload(s) would be skipped.", file=sys.stderr)
    print("\n[dry-run] No files written.", file=sys.stderr)


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(
    workload_type_filter: str,
    output_dir: str,
    keyword: str | None,
    namespace: str | None,
    dry_run: bool,
    yes: bool,
    concurrency: int,
    csv_path: str | None,
) -> int:
    wl_types: list[MachineWorkloadType]
    if workload_type_filter == "ps":
        wl_types = [_PS]
    elif workload_type_filter == "vm":
        wl_types = [_VM]
    else:
        wl_types = [_PS, _VM]

    if not dry_run and csv_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(output_dir, f"download_report_{ts}.csv")

    all_jobs:         list[_DownloadJob]    = []
    all_skipped:      list[_SkippedWorkload] = []
    silent_skip_count = 0

    try:
        async with make_client() as apm:

            # ── 1. List workloads ─────────────────────────────────────────
            type_label = workload_type_filter.upper() if workload_type_filter != "all" else "PS/VM"
            kw_note    = f" (keyword={keyword!r})" if keyword else ""
            print(f"Scanning {type_label} workloads{kw_note}...", file=sys.stderr)
            workloads = await _list_all_workloads(apm, wl_types, keyword, namespace)
            if not workloads:
                print("No workloads found.", file=sys.stderr)
                return 0
            print(f"  Found {len(workloads)} workload(s).", file=sys.stderr)

            # ── 2. Classify: resolve latest version + verify_status ───────
            print("Checking latest backup versions...", file=sys.stderr)
            classify_sem = asyncio.Semaphore(_MAX_CONCURRENT_VERSION_REQUESTS)
            classify_tasks = [
                _classify_workload(apm, wl, output_dir, classify_sem) for wl in workloads
            ]
            results = await asyncio.gather(*classify_tasks)

            for job, skipped in results:
                if job is not None:
                    all_jobs.append(job)
                elif skipped is not None:
                    all_skipped.append(skipped)
                else:
                    silent_skip_count += 1

            parts = []
            if all_jobs:
                parts.append(f"{len(all_jobs)} ready")
            if all_skipped:
                parts.append(f"{len(all_skipped)} skipped")
            if silent_skip_count:
                parts.append(f"{silent_skip_count} without verification")
            print(f"  {', '.join(parts)}.", file=sys.stderr)

            if not all_jobs:
                print("No workloads with SUCCESS verification videos found.", file=sys.stderr)
                return 0

            # ── 3. Dry-run ─────────────────────────────────────────────────
            if dry_run:
                _print_dry_run(all_jobs, all_skipped)
                return 0

            # ── 4. Download ────────────────────────────────────────────────
            assert csv_path is not None
            print(f"\nOutput dir : {os.path.abspath(output_dir)}", file=sys.stderr)
            print(f"CSV report : {os.path.abspath(csv_path)}", file=sys.stderr)

            if not yes:
                if not await prompt_yes_no(f"\nAbout to download {len(all_jobs)} video(s). Continue? [y/N] "):
                    print("Cancelled.", file=sys.stderr)
                    return 0

            os.makedirs(output_dir, exist_ok=True)
            print(f"\nDownloading {len(all_jobs)} video(s):", file=sys.stderr)
            print("  (press Ctrl+C to interrupt)\n", file=sys.stderr)

            dl_sem    = asyncio.Semaphore(concurrency)
            progress  = Progress(total=len(all_jobs), noun="video", show_exporting=False)
            interrupt = asyncio.Event()
            loop      = asyncio.get_running_loop()
            register_interrupt(loop, interrupt)

            stop_ticker = asyncio.Event()

            async def _ticker() -> None:
                while not stop_ticker.is_set():
                    if not interrupt.is_set():
                        progress.print_progress()
                    try:
                        await asyncio.wait_for(stop_ticker.wait(), timeout=1.0)
                    except TimeoutError:
                        pass

            ticker_task = asyncio.create_task(_ticker())
            dl_tasks = [
                asyncio.create_task(
                    _download_one(apm, job, dl_sem, progress, interrupt)
                )
                for job in all_jobs
            ]
            interrupt_wait = asyncio.create_task(interrupt.wait())
            try:
                done, _ = await asyncio.wait(
                    {*dl_tasks, interrupt_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if interrupt_wait in done:
                    for t in dl_tasks:
                        t.cancel()
                    interrupt_wait.cancel()
                else:
                    interrupt_wait.cancel()
                    await asyncio.gather(*dl_tasks, interrupt_wait, return_exceptions=True)
            finally:
                stop_ticker.set()
                if interrupt.is_set():
                    ticker_task.cancel()
                else:
                    await ticker_task
                unregister_interrupt(loop)
                progress.clear_progress()

            if interrupt.is_set():
                await asyncio.gather(*dl_tasks, ticker_task, return_exceptions=True)
                print("\nInterrupted.", file=sys.stderr)

    except KeyboardInterrupt:
        print("\n\nForce-interrupted.", file=sys.stderr)
        for job in all_jobs:
            if not job.outcome:
                job.outcome     = "interrupted"
                job.outcome_msg = "force-interrupted by user"

    # ── Write CSV ─────────────────────────────────────────────────────────────
    if not all_jobs and not all_skipped:
        return 1

    assert csv_path is not None  # resolved above; dry-run / no-workload paths returned earlier
    os.makedirs(output_dir, exist_ok=True)
    _write_csv(csv_path, all_jobs, all_skipped)

    # ── Summary ───────────────────────────────────────────────────────────────
    ok          = [j for j in all_jobs if j.outcome == "ok"]
    failed      = [j for j in all_jobs if j.outcome == "failed"]
    interrupted = [j for j in all_jobs if j.outcome == "interrupted"]
    total       = len(all_jobs) + len(all_skipped) + silent_skip_count

    print(f"\n{'='*64}", file=sys.stderr)
    print(f"  Verification Video Export Summary  ({total} workload(s))", file=sys.stderr)
    print(f"{'='*64}", file=sys.stderr)
    counts = f"  Downloaded: {len(ok)}   Failed: {len(failed)}"
    if all_skipped:
        counts += f"   Skipped: {len(all_skipped)}"
    if silent_skip_count:
        counts += f"   No verification: {silent_skip_count}"
    if interrupted:
        counts += f"   Interrupted: {len(interrupted)}"
    print(counts, file=sys.stderr)
    print(f"  Report: {csv_path}", file=sys.stderr)
    print(file=sys.stderr)
    return 1 if failed or interrupted else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--workload-type", metavar="TYPE", default="all",
        choices=["ps", "vm", "all"],
        help="Workload type filter: ps, vm, or all (default: all)",
    )
    parser.add_argument(
        "--output-dir", metavar="DIR", default="./verification_videos",
        help="Output directory for video files (default: ./verification_videos)",
    )
    parser.add_argument(
        "--keyword", metavar="KW", default=None,
        help="Filter workloads by name keyword",
    )
    parser.add_argument(
        "--namespace", metavar="NS", default=None,
        help="Filter workloads by backup server namespace",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List workloads with downloadable videos; do not download",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt and start downloading immediately",
    )
    parser.add_argument(
        "--concurrency", type=int, default=3, metavar="N",
        help="Max concurrent downloads (default: 3)",
    )
    parser.add_argument(
        "--csv", metavar="FILE", default=None,
        help="CSV report path (default: {output_dir}/download_report_{timestamp}.csv)",
    )
    args = parser.parse_args()

    run_main(run(
        workload_type_filter=args.workload_type,
        output_dir=args.output_dir,
        keyword=args.keyword,
        namespace=args.namespace,
        dry_run=args.dry_run,
        yes=args.yes,
        concurrency=args.concurrency,
        csv_path=args.csv,
    ))


if __name__ == "__main__":
    main()
