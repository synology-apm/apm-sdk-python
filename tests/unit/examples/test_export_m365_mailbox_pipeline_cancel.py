"""Unit tests for examples/export_m365_mailbox.py — task cancellation and export-run reporting."""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import export_m365_mailbox as em
import pytest
from _common import interruptible_sleep

from synology_apm.sdk import (
    APMError,
    M365ExportActivity,
    M365ExportStartResult,
    M365ExportStatus,
    M365Workload,
)
from tests.unit.examples._fixtures import (
    make_fake_apm,
    make_m365_user_info,
    make_m365_workload,
    make_version_location,
    make_workload_version,
)

_NAMESPACE = "ns-apm-server-01"
_EXEC_ID = "123e4567-e89b-12d3-a456-426614174091"
_EXEC_ID_2 = "123e4567-e89b-12d3-a456-426614174092"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _user_workload(upn: str = "alice@contoso.com") -> M365Workload:
    return make_m365_workload(name=upn, info=make_m365_user_info(user_principal_name=upn))


def _make_start_result(
    workload: M365Workload,
    *,
    execution_id: str = _EXEC_ID,
    ready: bool = False,
    export_name: str = "alice@contoso.com_20260514_mailbox.pst",
) -> M365ExportStartResult:
    return M365ExportStartResult(
        execution_id=execution_id,
        ready_to_download=ready,
        export_name=export_name,
        location=make_version_location(),
        workload=workload,
        version=make_workload_version(),
    )


def _make_activity(
    *,
    execution_id: str = _EXEC_ID,
    status: M365ExportStatus = M365ExportStatus.PREPARING,
    is_archive_mail: bool = False,
) -> M365ExportActivity:
    return M365ExportActivity(
        activity_id="123e4567-e89b-12d3-a456-426614174090",
        execution_id=execution_id,
        namespace=_NAMESPACE,
        workload_id="123e4567-e89b-12d3-a456-426614174002",
        workload_namespace=_NAMESPACE,
        source_name="mailbox",
        is_archive_mail=is_archive_mail,
        status=status,
        started_at=None,
        finished_at=None,
    )


def _make_job(
    workload: M365Workload,
    *,
    identity: str = "alice@contoso.com",
    unit_label: str = "mailbox",
    dest_path: str = "/exports/contoso.com/alice@contoso.com/mailbox.pst",
    execution_id: str = _EXEC_ID,
    ready: bool = False,
    status: M365ExportStatus = M365ExportStatus.PREPARING,
) -> em.MailExportJob:
    return em.MailExportJob(
        start_result=_make_start_result(workload, execution_id=execution_id, ready=ready),
        identity=identity,
        unit_label=unit_label,
        dest_path=dest_path,
        status=status,
    )


def _exchange_apm() -> MagicMock:
    """Fake APM client with the exchange-export collection methods wired."""
    apm = make_fake_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(return_value=make_workload_version())
    export = apm.m365.exchange_export
    export.start = AsyncMock()
    export.list = AsyncMock(return_value=([], 0))
    export.cancel = AsyncMock()
    export.get_download_url_by_ready_result = AsyncMock(
        return_value="https://apm.corp.com/download/export"
    )
    export.get_download_url_by_activity = AsyncMock(
        return_value="https://apm.corp.com/download/export"
    )
    return apm


# ── _cancel_tracked_jobs ──────────────────────────────────────────────────────


async def test_cancel_tracked_jobs_without_targets_prints_notice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    export = MagicMock()
    export.cancel = AsyncMock()
    job = _make_job(wl, status=M365ExportStatus.READY_TO_DOWNLOAD)

    await em._cancel_tracked_jobs(export, [job], asyncio.Semaphore(1))

    assert "(no cancellable tasks in current tracking)" in capsys.readouterr().out
    assert job.outcome == ""
    export.cancel.assert_not_awaited()

async def test_cancel_tracked_jobs_cancels_preparing_and_sweeps_rest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    export = MagicMock()
    export.cancel = AsyncMock()
    preparing = _make_job(wl, status=M365ExportStatus.PREPARING)
    activity = _make_activity()
    preparing.activity = activity
    ready = _make_job(
        wl, unit_label="archive mailbox", execution_id=_EXEC_ID_2,
        status=M365ExportStatus.READY_TO_DOWNLOAD,
    )

    await em._cancel_tracked_jobs(export, [preparing, ready], asyncio.Semaphore(1))

    export.cancel.assert_awaited_once_with(activity)
    assert preparing.outcome == "canceled"
    assert preparing.outcome_msg == "cancelled on user interrupt"
    # jobs without an outcome are swept to canceled too
    assert ready.outcome == "canceled"
    assert "  [OK] alice@contoso.com (mailbox): cancelled" in capsys.readouterr().out

async def test_cancel_tracked_jobs_skips_cancel_call_without_activity() -> None:
    wl = _user_workload()
    export = MagicMock()
    export.cancel = AsyncMock()
    job = _make_job(wl, status=M365ExportStatus.PREPARING)  # activity is None

    await em._cancel_tracked_jobs(export, [job], asyncio.Semaphore(1))

    export.cancel.assert_not_awaited()
    assert job.outcome == "canceled"

async def test_cancel_tracked_jobs_cancel_error_marks_failed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    export = MagicMock()
    export.cancel = AsyncMock(side_effect=APMError("task already finished"))
    job = _make_job(wl, status=M365ExportStatus.PREPARING)
    job.activity = _make_activity()

    await em._cancel_tracked_jobs(export, [job], asyncio.Semaphore(1))

    assert job.outcome == "failed"
    assert job.outcome_msg == "cancel error: task already finished"
    assert "  [!!] alice@contoso.com (mailbox): task already finished" in capsys.readouterr().out


# ── cancel_all ────────────────────────────────────────────────────────────────


async def test_cancel_all_without_preparing_tasks_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    export.list = AsyncMock(
        return_value=([_make_activity(status=M365ExportStatus.DOWNLOADED)], 1)
    )
    domain = em._build_exchange_domain("both")

    rc = await em.cancel_all(apm, domain, [wl], False, asyncio.Semaphore(1))

    assert rc == 0
    assert "No cancellable export tasks found." in capsys.readouterr().out
    export.cancel.assert_not_awaited()

async def test_cancel_all_dry_run_lists_without_cancelling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    export.list = AsyncMock(
        return_value=(
            [
                _make_activity(execution_id=_EXEC_ID, status=M365ExportStatus.PREPARING),
                _make_activity(
                    execution_id=_EXEC_ID_2,
                    status=M365ExportStatus.PREPARING,
                    is_archive_mail=True,
                ),
            ],
            2,
        )
    )
    domain = em._build_exchange_domain("both")

    rc = await em.cancel_all(apm, domain, [wl], True, asyncio.Semaphore(1))

    assert rc == 0
    out = capsys.readouterr().out
    assert "Found 2 cancellable task(s):" in out
    listing_line = next(
        line for line in out.splitlines()
        if "alice@contoso.com" in line and "archive mailbox" in line
    )
    assert "preparing" in listing_line
    assert f"id={_EXEC_ID_2[:8]}..." in listing_line
    assert "[dry-run] No tasks cancelled." in out
    export.cancel.assert_not_awaited()

async def test_cancel_all_reports_mixed_results(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    export.list = AsyncMock(
        return_value=(
            [
                _make_activity(execution_id=_EXEC_ID, status=M365ExportStatus.PREPARING),
                _make_activity(
                    execution_id=_EXEC_ID_2,
                    status=M365ExportStatus.PREPARING,
                    is_archive_mail=True,
                ),
            ],
            2,
        )
    )
    export.cancel = AsyncMock(side_effect=[None, APMError("task already finished")])
    domain = em._build_exchange_domain("both")

    rc = await em.cancel_all(apm, domain, [wl], False, asyncio.Semaphore(1))

    assert rc == 1
    out = capsys.readouterr().out
    assert "  [OK] alice@contoso.com (mailbox): cancelled" in out
    assert "  [!!] alice@contoso.com (archive mailbox): task already finished" in out
    counts_line = next(line for line in out.splitlines() if "Cancelled:" in line)
    assert "Cancelled: 1" in counts_line
    assert "Failed: 1" in counts_line

async def test_cancel_all_list_error_skips_that_workload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl1 = _user_workload("alice@contoso.com")
    wl2 = _user_workload("bob@contoso.com")
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    export.list = AsyncMock(
        side_effect=[
            APMError("temporarily unavailable"),
            ([_make_activity(status=M365ExportStatus.PREPARING)], 1),
        ]
    )
    export.cancel = AsyncMock()
    domain = em._build_exchange_domain("both")

    rc = await em.cancel_all(apm, domain, [wl1, wl2], False, asyncio.Semaphore(1))

    assert rc == 0
    out = capsys.readouterr().out
    assert "  [!!] alice@contoso.com: failed to list exports: temporarily unavailable" in out
    export.cancel.assert_awaited_once()


# ── run_export / _finish ──────────────────────────────────────────────────────


def _patch_interrupt_hooks(monkeypatch: pytest.MonkeyPatch) -> dict[str, asyncio.Event]:
    """Neutralize signal-handler registration and expose the interrupt event."""
    holder: dict[str, asyncio.Event] = {}

    def fake_register(loop: object, event: asyncio.Event) -> None:
        holder["event"] = event

    monkeypatch.setattr(em, "register_interrupt", fake_register)
    monkeypatch.setattr(em, "unregister_interrupt", lambda loop: None)
    return holder


async def test_run_export_happy_path_downloads_and_writes_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_interrupt_hooks(monkeypatch)
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(
        return_value=_make_start_result(wl, ready=True)
    )

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"x" * 1024)

    apm.download_file = AsyncMock(side_effect=fake_download)
    domain = em._build_exchange_domain("primary")
    output_dir = tmp_path / "exports"
    csv_path = tmp_path / "report.csv"

    rc = await em.run_export(
        apm, domain, [wl], str(output_dir), yes=True, concurrency=2,
        download_concurrency=2, csv_path=str(csv_path), skip_pairs=None, carried_rows=[],
    )

    assert rc == 0
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["upn"] == "alice@contoso.com"
    assert rows[0]["mailbox_type"] == "mailbox"
    assert rows[0]["status"] == "downloaded"
    assert rows[0]["size_bytes"] == "1024"
    out = capsys.readouterr().out
    assert "Exchange Export Summary  (1 mailbox(s))" in out
    counts_line = next(line for line in out.splitlines() if "Downloaded:" in line)
    assert "Downloaded: 1" in counts_line
    assert "Failed: 0" in counts_line

async def test_run_export_declined_confirmation_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    monkeypatch.setattr(em, "prompt_yes_no", AsyncMock(return_value=False))
    domain = em._build_exchange_domain("primary")
    csv_path = tmp_path / "report.csv"

    rc = await em.run_export(
        apm, domain, [wl], str(tmp_path), yes=False, concurrency=2,
        download_concurrency=2, csv_path=str(csv_path), skip_pairs=None, carried_rows=[],
    )

    assert rc == 0
    assert "Cancelled." in capsys.readouterr().out
    apm.m365.exchange_export.start.assert_not_awaited()
    assert not csv_path.exists()

async def test_run_export_interrupt_cancels_pending_jobs_and_writes_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    holder = _patch_interrupt_hooks(monkeypatch)
    monkeypatch.setattr(em, "prompt_yes_no", AsyncMock(return_value=True))

    async def fake_sleep(secs: float, event: asyncio.Event) -> bool:
        if secs == em.POLL_INTERVAL_SEC:
            # Simulate Ctrl+C arriving during the poll-loop sleep.
            holder["event"].set()
            return True
        return await interruptible_sleep(secs, event)

    monkeypatch.setattr(em, "interruptible_sleep", fake_sleep)
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(
        return_value=_make_start_result(wl, ready=False)
    )
    apm.m365.exchange_export.list = AsyncMock(
        return_value=([_make_activity(status=M365ExportStatus.PREPARING)], 1)
    )
    domain = em._build_exchange_domain("primary")
    csv_path = tmp_path / "report.csv"

    rc = await em.run_export(
        apm, domain, [wl], str(tmp_path), yes=True, concurrency=1,
        download_concurrency=1, csv_path=str(csv_path), skip_pairs=None, carried_rows=[],
    )

    assert rc == 0  # cancelled jobs are neither failed nor interrupted
    apm.m365.exchange_export.cancel.assert_awaited_once()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["status"] == "canceled"
    assert rows[0]["error"] == "cancelled on user interrupt"
    out = capsys.readouterr().out
    assert "Cancelling 1 task(s)..." in out
    counts_line = next(line for line in out.splitlines() if "Downloaded:" in line)
    assert "Cancelled: 1" in counts_line

async def test_run_export_interrupt_with_declined_cancel_marks_interrupted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    holder = _patch_interrupt_hooks(monkeypatch)
    monkeypatch.setattr(em, "prompt_yes_no", AsyncMock(return_value=False))

    async def fake_sleep(secs: float, event: asyncio.Event) -> bool:
        if secs == em.POLL_INTERVAL_SEC:
            holder["event"].set()
            return True
        return await interruptible_sleep(secs, event)

    monkeypatch.setattr(em, "interruptible_sleep", fake_sleep)
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(
        return_value=_make_start_result(wl, ready=False)
    )
    apm.m365.exchange_export.list = AsyncMock(
        return_value=([_make_activity(status=M365ExportStatus.PREPARING)], 1)
    )
    domain = em._build_exchange_domain("primary")
    csv_path = tmp_path / "report.csv"

    rc = await em.run_export(
        apm, domain, [wl], str(tmp_path), yes=True, concurrency=1,
        download_concurrency=1, csv_path=str(csv_path), skip_pairs=None, carried_rows=[],
    )

    assert rc == 1  # interrupted jobs force a non-zero exit
    apm.m365.exchange_export.cancel.assert_not_awaited()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["status"] == "interrupted"
    assert rows[0]["error"] == "interrupted by user; task may still run on APM"
    out = capsys.readouterr().out
    counts_line = next(line for line in out.splitlines() if "Downloaded:" in line)
    assert "Interrupted: 1" in counts_line

def test_finish_with_nothing_to_report_returns_one(tmp_path: Path) -> None:
    domain = em._build_exchange_domain("both")
    csv_path = tmp_path / "report.csv"

    rc = em._finish(domain, [], [], [], str(tmp_path), str(csv_path))

    assert rc == 1
    assert not csv_path.exists()

def test_finish_counts_all_outcome_buckets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    domain = em._build_exchange_domain("both")
    ok_job = _make_job(wl)
    ok_job.outcome = "ok"
    ok_job.bytes_saved = 1024
    failed_job = _make_job(wl, unit_label="archive mailbox", execution_id=_EXEC_ID_2)
    failed_job.outcome = "failed"
    failed_job.outcome_msg = "download error: connection reset"
    failure = em.MailExportFailure("bob@contoso.com", "mailbox", "no backup version found")
    carried = [
        {
            "upn": "carol@contoso.com",
            "domain": "contoso.com",
            "mailbox_type": "mailbox",
            "execution_id": _EXEC_ID,
            "status": "downloaded",
            "size_bytes": "2048",
            "error": "",
            "dest_path": "/exports/contoso.com/carol@contoso.com/mailbox.pst",
        }
    ]
    csv_path = tmp_path / "report.csv"

    rc = em._finish(domain, [ok_job, failed_job], [failure], carried, str(tmp_path), str(csv_path))

    assert rc == 1  # a failed job forces a non-zero exit
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["upn"], r["status"]) for r in rows] == [
        ("alice@contoso.com", "downloaded"),
        ("alice@contoso.com", "failed"),
        ("bob@contoso.com", "skipped"),
        ("carol@contoso.com", "downloaded"),
    ]
    out = capsys.readouterr().out
    assert "Exchange Export Summary  (4 mailbox(s))" in out
    counts_line = next(line for line in out.splitlines() if "Downloaded:" in line)
    assert "Downloaded: 1" in counts_line
    assert "Failed: 1" in counts_line
    assert "Skipped: 1" in counts_line
    assert "Carried: 1" in counts_line
    report_line = next(line for line in out.splitlines() if "Report:" in line)
    assert str(csv_path) in report_line

def test_finish_all_downloaded_returns_zero(tmp_path: Path) -> None:
    wl = _user_workload()
    domain = em._build_exchange_domain("both")
    ok_job = _make_job(wl)
    ok_job.outcome = "ok"
    csv_path = tmp_path / "report.csv"

    rc = em._finish(domain, [ok_job], [], [], str(tmp_path), str(csv_path))

    assert rc == 0
    assert csv_path.exists()
