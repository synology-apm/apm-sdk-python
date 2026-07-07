#!/usr/bin/env python3
"""
Bulk M365 mailbox PST export — exports Exchange user mailboxes or M365 Group
mailboxes for a Microsoft 365 tenant. Pick the kind with a subcommand:

    exchange   every user's primary and archive mailbox (--primary-only / --archive-only
               to limit scope; the two flags are mutually exclusive)
    group      every M365 Group mailbox (single mailbox; no archive)

Output layout (exchange):
    {output_dir}/
        contoso.com/
            alice@contoso.com/
                alice@contoso.com_20260514_mailbox.pst
                alice@contoso.com_20260514_archive_mailbox.pst
        export_report_20260514_143022.csv   ← always written

Output layout (group):
    {output_dir}/
        contoso.com/
            marketing@contoso.com_20260514.pst
        export_report_20260514_143022.csv

Usage:
    python export_m365_mailbox.py exchange --tenant-id <TENANT_ID>
    python export_m365_mailbox.py exchange --tenant-id <TENANT_ID> --primary-only
    python export_m365_mailbox.py exchange --tenant-id <TENANT_ID> --archive-only
    python export_m365_mailbox.py exchange --tenant-id <TENANT_ID> --resume export_report_20260514_143022.csv
    python export_m365_mailbox.py exchange --tenant-id <TENANT_ID> --cancel
    python export_m365_mailbox.py group --tenant-id <TENANT_ID>
    python export_m365_mailbox.py group --tenant-id <TENANT_ID> --cancel --dry-run

Common options (both subcommands):
    --output-dir DIR              root output directory (default: ./exports)
    --keyword KW                  filter by name/email keyword
    --csv FILE                    CSV report path (default: {output_dir}/export_report_{timestamp}.csv)
    --cancel                      cancel all in-progress (Preparing) export tasks instead of exporting
    --resume CSV                  retry mailboxes without 'downloaded' status in a previous report
    --dry-run                     list only; do not start exports or prompt
    --yes / -y                    skip the confirmation prompt
    --concurrency N               max workloads in the export pipeline at once (default: 3)
    --download-concurrency M      max simultaneous downloads (default: 5)

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from _common import (
    Progress,
    fmt_bytes,
    fmt_duration,
    fmt_speed,
    interruptible_sleep,
    make_client,
    prompt_yes_no,
    register_interrupt,
    run_main,
    safe_path,
    unregister_interrupt,
)

from synology_apm.sdk import (
    APMClient,
    APMError,
    ExchangeExportCollection,
    GroupExportCollection,
    M365ExportActivity,
    M365ExportStartResult,
    M365ExportStatus,
    M365GroupInfo,
    M365UserInfo,
    M365Workload,
    M365WorkloadType,
    ResourceNotFoundError,
    WorkloadVersion,
)

POLL_INTERVAL_SEC = 5

ExportCollection = ExchangeExportCollection | GroupExportCollection


# ════════════════════════════════════════════════════════════════════════════
# Engine — domain-agnostic start/poll/download/cancel/resume pipeline.
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class PlannedUnit:
    """One mailbox to export for a workload (a user has up to two; a group, one)."""
    archive: bool
    unit_label: str    # display label: "mailbox" / "archive mailbox" / "" (group)
    dest_path: str


@dataclass
class MailExportJob:
    """A started export task tracked through polling and download."""
    start_result: M365ExportStartResult
    identity: str            # UPN or group mail
    unit_label: str          # "" for a group (single mailbox)
    dest_path: str
    activity: M365ExportActivity | None = None   # set by poll loop; used for cancel and download
    status: M365ExportStatus = M365ExportStatus.PREPARING
    outcome: str = ""        # "" | "ok" | "failed" | "canceled" | "interrupted"
    outcome_msg: str = ""
    bytes_saved:    int      | None = None
    started_at:    datetime | None = None
    ready_at:      datetime | None = None
    dl_started_at: datetime | None = None

    @property
    def log_label(self) -> str:
        return f"{self.identity} ({self.unit_label})" if self.unit_label else self.identity

    @property
    def final_status(self) -> str:
        if self.outcome == "ok":
            return "downloaded"
        if self.outcome in ("failed", "canceled", "interrupted"):
            return self.outcome
        return "unknown"


@dataclass
class MailExportFailure:
    """A mailbox that could not be started — has no execution_id."""
    identity: str
    unit_label: str   # "" for a group
    error: str


@dataclass
class MailExportDomain:
    """Per-kind differences between Exchange and Group export."""
    noun: str                                # "user" / "group"
    type_label: str                          # "Exchange" / "Group" (for messages)
    workload_type: M365WorkloadType
    id_field: str                            # CSV column holding the identity
    csv_fields: list[str]
    export_collection: Callable[[APMClient], ExportCollection]
    identity_of: Callable[[M365Workload], str]
    plan_units: Callable[[M365Workload, str, str], list[PlannedUnit]]   # (wl, identity, out_dir)
    start_unit: Callable[[Any, M365Workload, WorkloadVersion, PlannedUnit], Awaitable[M365ExportStartResult]]
    job_to_row: Callable[[MailExportJob], dict[str, str]]
    failure_to_row: Callable[[MailExportFailure], dict[str, str]]
    listing_label: str                       # "Exchange users" / "M365 Groups"
    pst_layout: Callable[[str], str]         # output_dir → human-readable layout string
    summary_noun: str = ""                   # noun for the summary total (defaults to noun)
    unit_field: str | None = None            # CSV column distinguishing units (Exchange only)
    extra_note: str = ""                     # appended to confirm + "Processing" lines (Exchange scope)

    def __post_init__(self) -> None:
        if not self.summary_noun:
            self.summary_noun = self.noun


# ── Phase 1: list workloads ───────────────────────────────────────────────────

async def list_workloads(
    apm: APMClient,
    domain: MailExportDomain,
    tenant_id: str,
    keyword: str | None,
) -> tuple[list[M365Workload], int]:
    """Quick-count first, then paginate through all workloads of the domain type.

    Prints the server-reported total immediately so the operator sees the scope
    before all pages are fetched. Returns (workloads, server_total).
    """
    _, total = await apm.m365.workloads.list(
        tenant_id=tenant_id, workload_type=domain.workload_type,
        is_retired=False, keyword=keyword, limit=1, offset=0,
    )
    if total == 0:
        return [], 0

    print(f"  Found {total} {domain.type_label} {domain.noun}(s).", flush=True)

    results: list[M365Workload] = []
    offset = 0
    while True:
        page, _ = await apm.m365.workloads.list(
            tenant_id=tenant_id, workload_type=domain.workload_type,
            is_retired=False, keyword=keyword, limit=500, offset=offset,
        )
        results.extend(page)
        offset += len(page)
        if total > 500 and offset < total:
            print(f"\r  Fetching... {offset}/{total}", end="", flush=True)
        if not page or offset >= total:
            break
    if total > 500:
        print("\r\033[K", end="", flush=True)

    return results, total


# ── Phase 2: start exports ─────────────────────────────────────────────────--

async def _start_workload(
    apm: APMClient,
    domain: MailExportDomain,
    workload: M365Workload,
    output_dir: str,
    progress: Progress,
    skip_pairs: set[tuple[str, str]] | None,
) -> tuple[list[MailExportJob], list[MailExportJob], list[MailExportFailure]]:
    """Start exports for one workload's mailboxes.

    Returns (pending_jobs, immediate_jobs, failures). immediate_jobs are
    ready_to_download from start(); pending_jobs need polling.
    """
    export = domain.export_collection(apm)
    identity = domain.identity_of(workload)
    units = domain.plan_units(workload, identity, output_dir)

    pending:   list[MailExportJob] = []
    immediate: list[MailExportJob] = []
    failures:  list[MailExportFailure] = []

    try:
        version = await apm.m365.workloads.get_latest_version(workload)
    except ResourceNotFoundError:
        return [], [], [MailExportFailure(identity, u.unit_label, "no backup version found") for u in units]
    except APMError as e:
        progress.clear_progress()
        print(f"  [!!] {identity}: failed to get latest version: {e.message}")
        return [], [], [
            MailExportFailure(identity, u.unit_label, f"version lookup failed: {e.message}") for u in units
        ]

    for unit in units:
        if skip_pairs and (identity, unit.unit_label) in skip_pairs:
            continue
        try:
            result = await domain.start_unit(export, workload, version, unit)
            job = MailExportJob(
                start_result=result,
                identity=identity,
                unit_label=unit.unit_label,
                dest_path=unit.dest_path,
                status=(
                    M365ExportStatus.READY_TO_DOWNLOAD if result.ready_to_download
                    else M365ExportStatus.PREPARING
                ),
                started_at=datetime.now(),
            )
            (immediate if result.ready_to_download else pending).append(job)
        except ResourceNotFoundError:
            failures.append(MailExportFailure(identity, unit.unit_label, "resource not found"))
        except APMError as e:
            progress.clear_progress()
            label = f"{identity} ({unit.unit_label})" if unit.unit_label else identity
            print(f"  [!!] {label}: failed to start export: {e.message}")
            failures.append(MailExportFailure(identity, unit.unit_label, f"start failed: {e.message}"))

    return pending, immediate, failures


# ── Phase 3: poll ─────────────────────────────────────────────────────────--

async def _poll_jobs(
    export: ExportCollection,
    jobs: list[MailExportJob],
    apm: APMClient,
    domain: MailExportDomain,
    interrupt: asyncio.Event,
    progress: Progress,
    dl_sem: asyncio.Semaphore,
) -> list[asyncio.Task[None]]:
    """Poll one workload's pending jobs until terminal or interrupt fires.

    Fires a download task as soon as a job becomes ready. Returns the created tasks.
    All jobs belong to the same workload, so a single list() call covers them.
    """
    pending = list(jobs)
    dl_tasks: list[asyncio.Task[None]] = []

    while pending and not interrupt.is_set():
        try:
            activities, _ = await export.list(pending[0].start_result.workload, limit=200)
            activity_map = {(a.namespace, a.execution_id): a for a in activities}
            still_pending: list[MailExportJob] = []
            for job in pending:
                act = activity_map.get(
                    (job.start_result.location.namespace, job.start_result.execution_id)
                )
                if act is not None:
                    job.activity = act
                s = act.status if act is not None else None
                if s is None or s == M365ExportStatus.PREPARING:
                    still_pending.append(job)
                    continue
                job.status   = s
                job.ready_at = datetime.now()
                if s in {M365ExportStatus.READY_TO_DOWNLOAD, M365ExportStatus.DOWNLOADED}:
                    dl_tasks.append(asyncio.create_task(download_job(apm, domain, job, dl_sem, progress)))
                else:
                    job.outcome     = "failed"
                    job.outcome_msg = s.value
                    exp_secs = (job.ready_at - job.started_at).total_seconds() if job.started_at else 0
                    progress.clear_progress()
                    print(f"  [Fail]  {job.log_label}  export {s.value} after {fmt_duration(exp_secs)}")
            pending = still_pending
        except APMError as e:
            progress.clear_progress()
            print(f"  [!!] Poll error for {pending[0].log_label}: {e.message}")

        if not pending:
            break
        if await interruptible_sleep(POLL_INTERVAL_SEC, interrupt):
            break

    return dl_tasks


# ── Phase 4: download ─────────────────────────────────────────────────────--

async def download_job(
    apm: APMClient,
    domain: MailExportDomain,
    job: MailExportJob,
    dl_sem: asyncio.Semaphore,
    progress: Progress,
) -> None:
    export = domain.export_collection(apm)
    async with dl_sem:
        progress.downloading += 1
        job.dl_started_at = datetime.now()
        try:
            if job.start_result.ready_to_download:
                url = await export.get_download_url_by_ready_result(job.start_result)
            else:
                assert job.activity is not None, "activity must be set by poll loop before download"
                url = await export.get_download_url_by_activity(job.activity)
            os.makedirs(os.path.dirname(job.dest_path), exist_ok=True)
            await apm.download_file(url, job.dest_path)
            dl_end          = datetime.now()
            job.bytes_saved = os.path.getsize(job.dest_path)
            job.outcome     = "ok"
            exp_secs = (job.ready_at - job.started_at).total_seconds() if job.ready_at and job.started_at else 0
            dl_secs  = (dl_end - job.dl_started_at).total_seconds()
            progress.clear_progress()
            print(
                f"  [Done]  {job.log_label}"
                f"  export {fmt_duration(exp_secs)},"
                f" download {fmt_duration(dl_secs)}"
                f" for {fmt_bytes(job.bytes_saved)} ({fmt_speed(job.bytes_saved, dl_secs)})"
            )
        except APMError as e:
            job.outcome     = "failed"
            job.outcome_msg = f"download error: {e.message}"
            progress.clear_progress()
            print(f"  [!!] {job.log_label}: {job.outcome_msg}")
        except OSError as e:
            job.outcome     = "failed"
            job.outcome_msg = f"local I/O error: {e}"
            _remove_quietly(job.dest_path)
            progress.clear_progress()
            print(f"  [!!] {job.log_label}: {job.outcome_msg}")
        except asyncio.CancelledError:
            _remove_quietly(job.dest_path)
            job.outcome     = "interrupted"
            job.outcome_msg = "download interrupted"
            raise
        finally:
            progress.downloading -= 1


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ── Pipeline: one workload ────────────────────────────────────────────────--

async def _process_one(
    apm: APMClient,
    domain: MailExportDomain,
    workload: M365Workload,
    output_dir: str,
    export_sem: asyncio.Semaphore,
    dl_sem: asyncio.Semaphore,
    interrupt: asyncio.Event,
    progress: Progress,
    all_jobs: list[MailExportJob],
    all_failures: list[MailExportFailure],
    skip_pairs: set[tuple[str, str]] | None,
) -> None:
    """Full pipeline for one workload: start → poll (fire downloads) → await downloads.

    The export semaphore is released as soon as polling completes so the next
    workload can start while this one's downloads continue.
    """
    async with export_sem:
        if interrupt.is_set():
            progress.done += 1
            progress.print_progress()
            return
        progress.exporting += 1

        pending, immediate, failures = await _start_workload(
            apm, domain, workload, output_dir, progress, skip_pairs
        )
        all_failures.extend(failures)
        jobs_this = pending + immediate
        if not jobs_this:
            progress.exporting -= 1
            progress.done += 1
            progress.print_progress()
            return

        all_jobs.extend(jobs_this)
        progress.print_progress()

        export = domain.export_collection(apm)
        immediate_dl = [
            asyncio.create_task(download_job(apm, domain, job, dl_sem, progress))
            for job in immediate
        ]
        poll_dl = await _poll_jobs(export, pending, apm, domain, interrupt, progress, dl_sem)

        progress.exporting -= 1
        progress.print_progress()

    dl_tasks = immediate_dl + poll_dl
    if dl_tasks:
        await asyncio.gather(*dl_tasks)

    progress.done += 1
    progress.print_progress()


# ── Interrupt-triggered cancel ─────────────────────────────────────────────--

async def _cancel_tracked_jobs(
    export: ExportCollection,
    jobs: list[MailExportJob],
    sem: asyncio.Semaphore,
) -> None:
    """Cancel jobs started by this run that are still cancellable; update outcomes in place."""
    targets = [j for j in jobs if j.status == M365ExportStatus.PREPARING]
    if not targets:
        print("  (no cancellable tasks in current tracking)")
        return

    async def _do(job: MailExportJob) -> None:
        async with sem:
            try:
                if job.activity is not None:
                    await export.cancel(job.activity)
                job.outcome     = "canceled"
                job.outcome_msg = "cancelled on user interrupt"
                print(f"  [OK] {job.log_label}: cancelled")
            except APMError as e:
                job.outcome     = "failed"
                job.outcome_msg = f"cancel error: {e.message}"
                print(f"  [!!] {job.log_label}: {e.message}")

    await asyncio.gather(*(_do(j) for j in targets))

    for job in jobs:
        if not job.outcome:
            job.outcome     = "canceled"
            job.outcome_msg = "cancelled on user interrupt"


# ── Cancel mode ───────────────────────────────────────────────────────────--

async def cancel_all(
    apm: APMClient,
    domain: MailExportDomain,
    workloads: list[M365Workload],
    dry_run: bool,
    sem: asyncio.Semaphore,
) -> int:
    """Cancel all in-progress (Preparing) export tasks across the given workloads."""
    export = domain.export_collection(apm)
    print("Scanning export tasks...")

    async def _list_cancellable(wl: M365Workload) -> list[tuple[M365Workload, Any]]:
        try:
            activities, _ = await export.list(wl, limit=200)
            return [(wl, a) for a in activities if a.status == M365ExportStatus.PREPARING]
        except APMError as e:
            print(f"  [!!] {domain.identity_of(wl)}: failed to list exports: {e.message}")
            return []

    list_results = await asyncio.gather(*[_list_cancellable(wl) for wl in workloads])
    targets = [pair for pairs in list_results for pair in pairs]

    if not targets:
        print("No cancellable export tasks found.")
        return 0

    col_w = max(len(domain.identity_of(wl)) for wl, _ in targets)
    print(f"\nFound {len(targets)} cancellable task(s):\n")
    for wl, act in sorted(targets, key=lambda t: (domain.identity_of(t[0]), t[1].is_archive_mail)):
        label = "archive mailbox" if act.is_archive_mail else "mailbox"
        print(f"  {domain.identity_of(wl):<{col_w}}  {label:<15}  {act.status.value:<20}  id={act.execution_id[:8]}...")

    if dry_run:
        print("\n[dry-run] No tasks cancelled.")
        return 0

    print(f"\nCancelling {len(targets)} task(s)...")

    async def _do_cancel(wl: M365Workload, act: Any) -> bool:
        label = "archive mailbox" if act.is_archive_mail else "mailbox"
        async with sem:
            try:
                await export.cancel(act)
                print(f"  [OK] {domain.identity_of(wl)} ({label}): cancelled")
                return True
            except APMError as e:
                print(f"  [!!] {domain.identity_of(wl)} ({label}): {e.message}")
                return False

    results = await asyncio.gather(*[_do_cancel(wl, act) for wl, act in targets])
    cancelled = sum(1 for r in results if r)
    failed    = len(results) - cancelled

    print(f"\n{'='*64}")
    print(f"  Cancel Summary  ({len(targets)} task(s))")
    print(f"{'='*64}")
    print(f"  Cancelled: {cancelled}  Failed: {failed}")
    print()
    return 1 if failed else 0


# ── Resume / report CSV ─────────────────────────────────────────────────────--

@dataclass
class ResumeState:
    downloaded_pairs: set[tuple[str, str]] = field(default_factory=set)
    pending_identities: set[str] = field(default_factory=set)
    carried_rows: list[dict[str, str]] = field(default_factory=list)


def load_resume_csv(path: str, domain: MailExportDomain) -> ResumeState:
    """Read a previous report. Downloaded rows are carried forward and skipped;
    identities with any non-downloaded row are queued for retry."""
    state = ResumeState()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            identity = row[domain.id_field]
            unit = row.get(domain.unit_field, "") if domain.unit_field else ""
            if row.get("status") == "downloaded":
                state.downloaded_pairs.add((identity, unit))
                state.carried_rows.append(dict(row))
            else:
                state.pending_identities.add(identity)
    return state


def write_report(
    path: str,
    domain: MailExportDomain,
    jobs: list[MailExportJob],
    failures: list[MailExportFailure],
    carried_rows: list[dict[str, str]] | None,
) -> None:
    rows: list[dict[str, str]] = list(carried_rows) if carried_rows else []
    rows.extend(
        domain.job_to_row(j)
        for j in sorted(jobs, key=lambda j: (j.identity, j.unit_label == "archive mailbox"))
    )
    rows.extend(
        domain.failure_to_row(f)
        for f in sorted(failures, key=lambda f: (f.identity, f.unit_label == "archive mailbox"))
    )
    rows.sort(key=lambda r: (r[domain.id_field], r.get(domain.unit_field or "", "") == "archive mailbox"))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=domain.csv_fields)
        writer.writeheader()
        writer.writerows(rows)


# ── Pipeline orchestration ──────────────────────────────────────────────────--

async def run_export(
    apm: APMClient,
    domain: MailExportDomain,
    workloads: list[M365Workload],
    output_dir: str,
    yes: bool,
    concurrency: int,
    download_concurrency: int,
    csv_path: str,
    skip_pairs: set[tuple[str, str]] | None,
    carried_rows: list[dict[str, str]],
) -> int:
    """Drive the export pipeline for *workloads* with Ctrl+C-safe interrupt handling.

    Returns the process exit code. The caller is responsible for listing workloads,
    handling cancel/dry-run/resume filtering, and resolving csv_path beforehand.
    """
    export_sem = asyncio.Semaphore(concurrency)
    dl_sem     = asyncio.Semaphore(download_concurrency)
    all_jobs:     list[MailExportJob]     = []
    all_failures: list[MailExportFailure] = []

    print(f"PST output : {domain.pst_layout(output_dir)}")
    print(f"CSV report : {os.path.abspath(csv_path)}")

    confirm = f"\nAbout to export {len(workloads)} {domain.noun}(s){domain.extra_note}. Continue? [y/N] "
    if not yes and not await prompt_yes_no(confirm):
        print("Cancelled.")
        return 0

    print(f"\nProcessing {len(workloads)} {domain.noun}(s){domain.extra_note}:")
    print("  (press Ctrl+C to interrupt and optionally cancel pending tasks)")

    progress  = Progress(total=len(workloads), noun=domain.noun)
    interrupt = asyncio.Event()
    loop      = asyncio.get_running_loop()
    register_interrupt(loop, interrupt)

    # Background ticker keeps the progress line live during long downloads.
    stop_ticker = asyncio.Event()

    async def _ticker() -> None:
        while not stop_ticker.is_set():
            if interrupt.is_set():
                remaining = progress.total - progress.done
                line = f"  Interrupted — {progress.downloading} downloading, {remaining} remaining..."
                print(f"\r\033[K{line}", end="", flush=True)
                progress.on_line = True
            else:
                progress.print_progress()
            await interruptible_sleep(1.0, stop_ticker)

    ticker_task = asyncio.create_task(_ticker())
    pipeline_tasks = [
        asyncio.create_task(_process_one(
            apm, domain, wl, output_dir, export_sem, dl_sem,
            interrupt, progress, all_jobs, all_failures, skip_pairs,
        ))
        for wl in workloads
    ]
    interrupt_wait = asyncio.create_task(interrupt.wait())
    try:
        done, _ = await asyncio.wait({*pipeline_tasks, interrupt_wait}, return_when=asyncio.FIRST_COMPLETED)
        if interrupt_wait in done:
            for t in pipeline_tasks:
                t.cancel()
            interrupt_wait.cancel()
            # Tasks are cancelling in the background; do NOT await them here so the
            # prompt appears immediately.
        else:
            interrupt_wait.cancel()
            await asyncio.gather(*pipeline_tasks, interrupt_wait, return_exceptions=True)
    finally:
        stop_ticker.set()
        if interrupt.is_set():
            ticker_task.cancel()
        else:
            await ticker_task
        unregister_interrupt(loop)
        progress.clear_progress()

    # ── Handle graceful interrupt ──────────────────────────────────────────
    if interrupt.is_set():
        pending_jobs = [j for j in all_jobs if not j.outcome]
        if pending_jobs:
            print(f"\n\nInterrupted — {len(pending_jobs)} job(s) still in progress.")
            if await prompt_yes_no("Cancel pending tasks on APM? [y/N] "):
                print(f"\nCancelling {len(pending_jobs)} task(s)...")
                await _cancel_tracked_jobs(domain.export_collection(apm), pending_jobs, export_sem)
                for job in all_jobs:
                    if not job.outcome:
                        job.outcome     = "canceled"
                        job.outcome_msg = "cancelled on user interrupt"
            else:
                for job in all_jobs:
                    if not job.outcome:
                        job.outcome     = "interrupted"
                        job.outcome_msg = "interrupted by user; task may still run on APM"
        # Drain cancelled tasks (including the ticker) before the session closes.
        await asyncio.gather(*pipeline_tasks, ticker_task, return_exceptions=True)

    return _finish(domain, all_jobs, all_failures, carried_rows, output_dir, csv_path)


def _finish(
    domain: MailExportDomain,
    all_jobs: list[MailExportJob],
    all_failures: list[MailExportFailure],
    carried_rows: list[dict[str, str]],
    output_dir: str,
    csv_path: str,
) -> int:
    """Write the report and print the summary. Returns the exit code."""
    if not all_jobs and not all_failures and not carried_rows:
        return 1

    os.makedirs(output_dir, exist_ok=True)
    write_report(csv_path, domain, all_jobs, all_failures, carried_rows or None)

    ok          = [j for j in all_jobs if j.outcome == "ok"]
    failed      = [j for j in all_jobs if j.outcome == "failed"]
    interrupted = [j for j in all_jobs if j.outcome == "interrupted"]
    canceled    = [j for j in all_jobs if j.outcome == "canceled"]
    total       = len(all_jobs) + len(all_failures) + len(carried_rows)

    print(f"\n{'='*64}")
    print(f"  {domain.type_label} Export Summary  ({total} {domain.summary_noun}(s))")
    print(f"{'='*64}")
    counts = f"  Downloaded: {len(ok)}  Failed: {len(failed)}"
    if all_failures:
        counts += f"  Skipped: {len(all_failures)}"
    if interrupted:
        counts += f"  Interrupted: {len(interrupted)}"
    if canceled:
        counts += f"  Cancelled: {len(canceled)}"
    if carried_rows:
        counts += f"  Carried: {len(carried_rows)}"
    print(counts)
    print(f"  Report: {csv_path}")
    print()
    return 1 if failed or interrupted else 0


# ════════════════════════════════════════════════════════════════════════════
# Domains — the Exchange / Group specifics.
# ════════════════════════════════════════════════════════════════════════════

def _mail_domain(addr: str) -> str:
    return addr.rsplit("@", 1)[1] if "@" in addr else "unknown_domain"


# ── Exchange ─────────────────────────────────────────────────────────────────

_EXCHANGE_CSV_FIELDS = [
    "upn", "domain", "mailbox_type", "execution_id", "status", "size_bytes", "error", "dest_path",
]
_SCOPE_ARCHIVES: dict[str, list[bool]] = {"both": [False, True], "primary": [False], "archive": [True]}
_SCOPE_LABEL: dict[str, str] = {
    "both": "primary + archive", "primary": "primary only", "archive": "archive only",
}


def _upn(workload: M365Workload) -> str:
    if isinstance(workload.info, M365UserInfo):
        return workload.info.user_principal_name or workload.name
    return workload.name


def _exchange_job_row(j: MailExportJob) -> dict[str, str]:
    return {
        "upn":          j.identity,
        "domain":       _mail_domain(j.identity),
        "mailbox_type": j.unit_label,
        "execution_id": j.start_result.execution_id,
        "status":       j.final_status,
        "size_bytes":   str(j.bytes_saved) if j.bytes_saved is not None else "",
        "error":        j.outcome_msg if j.outcome != "ok" else "",
        "dest_path":    j.dest_path,
    }


def _exchange_failure_row(f: MailExportFailure) -> dict[str, str]:
    return {
        "upn":          f.identity,
        "domain":       _mail_domain(f.identity),
        "mailbox_type": f.unit_label,
        "execution_id": "",
        "status":       "skipped",
        "size_bytes":   "",
        "error":        f.error,
        "dest_path":    "",
    }


def _build_exchange_domain(mailbox_scope: str) -> MailExportDomain:
    def plan_units(workload: M365Workload, identity: str, output_dir: str) -> list[PlannedUnit]:
        user_dir = os.path.join(output_dir, safe_path(_mail_domain(identity)), safe_path(identity))
        today = date.today().strftime("%Y%m%d")
        units: list[PlannedUnit] = []
        for archive in _SCOPE_ARCHIVES[mailbox_scope]:
            file_label = "archive_mailbox" if archive else "mailbox"
            display    = "archive mailbox" if archive else "mailbox"
            dest_path  = os.path.join(user_dir, f"{identity}_{today}_{file_label}.pst")
            units.append(PlannedUnit(archive=archive, unit_label=display, dest_path=dest_path))
        return units

    async def start_unit(
        export: Any, workload: M365Workload, version: WorkloadVersion, unit: PlannedUnit
    ) -> M365ExportStartResult:
        result: M365ExportStartResult = await export.start(
            workload, version,
            archive_mailbox=unit.archive,
            export_name=os.path.basename(unit.dest_path),
        )
        return result

    return MailExportDomain(
        noun="user",
        summary_noun="mailbox",
        type_label="Exchange",
        workload_type=M365WorkloadType.EXCHANGE,
        id_field="upn",
        unit_field="mailbox_type",
        csv_fields=_EXCHANGE_CSV_FIELDS,
        export_collection=lambda apm: apm.m365.exchange_export,
        identity_of=_upn,
        plan_units=plan_units,
        start_unit=start_unit,
        job_to_row=_exchange_job_row,
        failure_to_row=_exchange_failure_row,
        listing_label="Exchange users",
        pst_layout=lambda out: os.path.join(os.path.abspath(out), "{domain}", "{upn}") + "/",
        extra_note=f" — {_SCOPE_LABEL[mailbox_scope]}",
    )


# ── Group ──────────────────────────────────────────────────────────────────--

_GROUP_CSV_FIELDS = ["group_mail", "domain", "execution_id", "status", "size_bytes", "error", "dest_path"]


def _group_mail(workload: M365Workload) -> str:
    if isinstance(workload.info, M365GroupInfo):
        return workload.info.mail or workload.name
    return workload.name


def _group_job_row(j: MailExportJob) -> dict[str, str]:
    return {
        "group_mail":   j.identity,
        "domain":       _mail_domain(j.identity),
        "execution_id": j.start_result.execution_id,
        "status":       j.final_status,
        "size_bytes":   str(j.bytes_saved) if j.bytes_saved is not None else "",
        "error":        j.outcome_msg if j.outcome != "ok" else "",
        "dest_path":    j.dest_path,
    }


def _group_failure_row(f: MailExportFailure) -> dict[str, str]:
    return {
        "group_mail":   f.identity,
        "domain":       _mail_domain(f.identity),
        "execution_id": "",
        "status":       "skipped",
        "size_bytes":   "",
        "error":        f.error,
        "dest_path":    "",
    }


def _group_plan_units(workload: M365Workload, identity: str, output_dir: str) -> list[PlannedUnit]:
    today = date.today().strftime("%Y%m%d")
    dest_path = os.path.join(output_dir, safe_path(_mail_domain(identity)), f"{safe_path(identity)}_{today}.pst")
    # Groups have a single mailbox (no archive); unit_label is empty.
    return [PlannedUnit(archive=False, unit_label="", dest_path=dest_path)]


async def _group_start_unit(
    export: Any, workload: M365Workload, version: WorkloadVersion, unit: PlannedUnit
) -> M365ExportStartResult:
    result: M365ExportStartResult = await export.start(
        workload, version, export_name=os.path.basename(unit.dest_path)
    )
    return result


def _build_group_domain() -> MailExportDomain:
    return MailExportDomain(
        noun="group",
        type_label="Group",
        workload_type=M365WorkloadType.GROUP,
        id_field="group_mail",
        csv_fields=_GROUP_CSV_FIELDS,
        export_collection=lambda apm: apm.m365.group_export,
        identity_of=_group_mail,
        plan_units=_group_plan_units,
        start_unit=_group_start_unit,
        job_to_row=_group_job_row,
        failure_to_row=_group_failure_row,
        listing_label="M365 Groups",
        pst_layout=lambda out: os.path.join(os.path.abspath(out), "{domain}", "{group_mail}_{date}.pst"),
    )


# ════════════════════════════════════════════════════════════════════════════
# Entry point — shared run() + subcommand CLI.
# ════════════════════════════════════════════════════════════════════════════

async def run(
    domain: MailExportDomain,
    tenant_id: str,
    output_dir: str,
    keyword: str | None,
    cancel: bool,
    dry_run: bool,
    yes: bool,
    concurrency: int,
    download_concurrency: int,
    csv_path: str | None,
    resume_csv: str | None,
) -> int:
    skip_pairs: set[tuple[str, str]] | None = None
    pending_identities: set[str] = set()
    carried_rows: list[dict[str, str]] = []
    if resume_csv:
        try:
            state = load_resume_csv(resume_csv, domain)
        except FileNotFoundError:
            print(f"Error: resume CSV not found: {resume_csv}", file=sys.stderr)
            return 1
        skip_pairs = state.downloaded_pairs or None
        pending_identities = state.pending_identities
        carried_rows = state.carried_rows
        if keyword:
            print("Note: --keyword is ignored in resume mode; scope is determined by the resume CSV.",
                  file=sys.stderr)

    if not cancel and not dry_run and csv_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(output_dir, f"export_report_{ts}.csv")

    async with make_client() as apm:
        mode_note = " (resume mode)" if resume_csv else (f" (keyword={keyword!r})" if keyword else "")
        print(f"Listing {domain.listing_label} for tenant {tenant_id}{mode_note}...")
        items, _ = await list_workloads(apm, domain, tenant_id, None if resume_csv else keyword)

        if not items:
            print(f"No {domain.type_label} workloads found.")
            return 0

        if cancel:
            return await cancel_all(apm, domain, items, dry_run, asyncio.Semaphore(concurrency))

        if resume_csv:
            items = [wl for wl in items if domain.identity_of(wl) in pending_identities]
            if carried_rows:
                print(f"  Resuming {len(items)} {domain.noun}(s) ({len(carried_rows)} already downloaded).")
            if not items:
                print(f"All {domain.noun}s already downloaded — nothing to do.")
                return 0

        if dry_run:
            print("\n[dry-run] No exports started.")
            return 0

        assert csv_path is not None
        return await run_export(
            apm, domain, items, output_dir, yes, concurrency, download_concurrency, csv_path,
            skip_pairs, carried_rows,
        )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tenant-id", metavar="ID", default="",
        help="Azure AD tenant ID",
    )
    parser.add_argument(
        "--output-dir", metavar="DIR", default="./exports",
        help="Root output directory (default: ./exports)",
    )
    parser.add_argument("--keyword", metavar="KW", default=None, help="Filter by name/email keyword")
    parser.add_argument(
        "--csv", metavar="FILE", default=None,
        help="CSV report path (default: {output_dir}/export_report_{timestamp}.csv)",
    )
    parser.add_argument(
        "--cancel", action="store_true",
        help="Cancel all in-progress (Preparing) export tasks instead of starting new ones",
    )
    parser.add_argument(
        "--resume", metavar="CSV", default=None,
        help="Resume from a previous export report; only processes mailboxes without 'downloaded' status",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List only; do not start any exports or prompt"
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompt and start exports immediately"
    )
    parser.add_argument(
        "--concurrency", type=int, default=3, metavar="N",
        help="Max workloads in export pipeline simultaneously (default: 3)",
    )
    parser.add_argument(
        "--download-concurrency", type=int, default=5, metavar="M",
        help="Max simultaneous downloads (default: 5)",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="kind", required=True, metavar="{exchange,group}")

    ex = sub.add_parser("exchange", help="Exchange user mailboxes (primary + archive)")
    _add_common_args(ex)
    scope_group = ex.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--primary-only", action="store_true", help="Export primary mailbox only; skip archive mailboxes"
    )
    scope_group.add_argument(
        "--archive-only", action="store_true", help="Export archive mailbox only; skip primary mailboxes"
    )

    grp = sub.add_parser("group", help="M365 Group mailboxes (single mailbox, no archive)")
    _add_common_args(grp)

    args = parser.parse_args()

    if not args.tenant_id:
        print("Error: --tenant-id is required", file=sys.stderr)
        sys.exit(1)

    if args.kind == "exchange":
        scope = "archive" if args.archive_only else ("primary" if args.primary_only else "both")
        domain = _build_exchange_domain(scope)
    else:
        domain = _build_group_domain()

    run_main(run(
        domain,
        tenant_id=args.tenant_id,
        output_dir=args.output_dir,
        keyword=args.keyword,
        cancel=args.cancel,
        dry_run=args.dry_run,
        yes=args.yes,
        concurrency=args.concurrency,
        download_concurrency=args.download_concurrency,
        csv_path=args.csv,
        resume_csv=args.resume,
    ))


if __name__ == "__main__":
    main()
