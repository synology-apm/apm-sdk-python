"""Unit tests for examples/export_m365_mailbox.py — job polling, download, and per-workload orchestration."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import export_m365_mailbox as em
import pytest
from _common import Progress

from synology_apm.sdk import (
    APMError,
    M365ExportActivity,
    M365ExportStartResult,
    M365ExportStatus,
    M365Workload,
    ResourceNotFoundError,
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


def _make_progress() -> Progress:
    return Progress(total=1, noun="user")


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


# ── _poll_jobs ────────────────────────────────────────────────────────────────


async def test_poll_jobs_terminal_failure_marks_job(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    activity = _make_activity(status=M365ExportStatus.FAILED)
    export.list = AsyncMock(return_value=([activity], 1))
    job = _make_job(wl)
    domain = em._build_exchange_domain("both")

    tasks = await em._poll_jobs(
        export, [job], apm, domain, asyncio.Event(), _make_progress(), asyncio.Semaphore(5)
    )

    assert tasks == []
    assert job.outcome == "failed"
    assert job.outcome_msg == "failed"
    assert job.status == M365ExportStatus.FAILED
    assert job.activity is activity
    out = capsys.readouterr().out
    fail_line = next(line for line in out.splitlines() if "[Fail]" in line)
    assert "alice@contoso.com (mailbox)" in fail_line
    assert "export failed after" in fail_line

@pytest.mark.parametrize(
    "ready_status",
    [M365ExportStatus.READY_TO_DOWNLOAD, M365ExportStatus.DOWNLOADED],
)
async def test_poll_jobs_ready_status_fires_download_task(
    monkeypatch: pytest.MonkeyPatch,
    ready_status: M365ExportStatus,
) -> None:
    downloaded: list[em.MailExportJob] = []

    async def fake_download_job(
        apm_: object, domain_: object, job_: em.MailExportJob, sem_: object, progress_: object
    ) -> None:
        downloaded.append(job_)

    monkeypatch.setattr(em, "download_job", fake_download_job)
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    export.list = AsyncMock(return_value=([_make_activity(status=ready_status)], 1))
    job = _make_job(wl)
    domain = em._build_exchange_domain("both")

    tasks = await em._poll_jobs(
        export, [job], apm, domain, asyncio.Event(), _make_progress(), asyncio.Semaphore(5)
    )

    assert len(tasks) == 1
    await asyncio.gather(*tasks)
    assert downloaded == [job]
    assert job.status == ready_status
    assert job.outcome == ""  # download outcome is set by download_job, not the poll loop

async def test_poll_jobs_waits_until_preparing_job_becomes_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(secs: float, event: asyncio.Event) -> bool:
        sleep_calls.append(secs)
        return False

    async def fake_download_job(*args: object) -> None:
        return None

    monkeypatch.setattr(em, "interruptible_sleep", fake_sleep)
    monkeypatch.setattr(em, "download_job", fake_download_job)
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    export.list = AsyncMock(
        side_effect=[
            ([_make_activity(status=M365ExportStatus.PREPARING)], 1),
            ([_make_activity(status=M365ExportStatus.READY_TO_DOWNLOAD)], 1),
        ]
    )
    job = _make_job(wl)
    domain = em._build_exchange_domain("both")

    tasks = await em._poll_jobs(
        export, [job], apm, domain, asyncio.Event(), _make_progress(), asyncio.Semaphore(5)
    )

    assert export.list.await_count == 2
    assert sleep_calls == [em.POLL_INTERVAL_SEC]
    assert len(tasks) == 1
    await asyncio.gather(*tasks)
    assert job.status == M365ExportStatus.READY_TO_DOWNLOAD

async def test_poll_jobs_survives_poll_error_and_retries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_sleep(secs: float, event: asyncio.Event) -> bool:
        return False

    async def fake_download_job(*args: object) -> None:
        return None

    monkeypatch.setattr(em, "interruptible_sleep", fake_sleep)
    monkeypatch.setattr(em, "download_job", fake_download_job)
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    export.list = AsyncMock(
        side_effect=[
            APMError("temporarily unavailable"),
            ([_make_activity(status=M365ExportStatus.READY_TO_DOWNLOAD)], 1),
        ]
    )
    job = _make_job(wl)
    domain = em._build_exchange_domain("both")

    tasks = await em._poll_jobs(
        export, [job], apm, domain, asyncio.Event(), _make_progress(), asyncio.Semaphore(5)
    )

    assert len(tasks) == 1
    await asyncio.gather(*tasks)
    assert job.status == M365ExportStatus.READY_TO_DOWNLOAD
    out = capsys.readouterr().out
    assert "  [!!] Poll error for alice@contoso.com (mailbox): temporarily unavailable" in out

async def test_poll_jobs_stops_when_interrupt_fires_during_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(secs: float, event: asyncio.Event) -> bool:
        return True  # interrupt fired while sleeping

    monkeypatch.setattr(em, "interruptible_sleep", fake_sleep)
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    # No matching activity yet — the job stays pending.
    export.list = AsyncMock(return_value=([], 0))
    job = _make_job(wl)
    domain = em._build_exchange_domain("both")

    tasks = await em._poll_jobs(
        export, [job], apm, domain, asyncio.Event(), _make_progress(), asyncio.Semaphore(5)
    )

    assert tasks == []
    assert job.outcome == ""  # left pending for the interrupt handler to resolve
    assert export.list.await_count == 1

async def test_poll_jobs_returns_immediately_when_interrupt_preset() -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export
    interrupt = asyncio.Event()
    interrupt.set()
    job = _make_job(wl)
    domain = em._build_exchange_domain("both")

    tasks = await em._poll_jobs(
        export, [job], apm, domain, interrupt, _make_progress(), asyncio.Semaphore(5)
    )

    assert tasks == []
    export.list.assert_not_awaited()


# ── download_job ──────────────────────────────────────────────────────────────


async def test_download_job_ready_result_url_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"x" * 4096)

    apm.download_file = AsyncMock(side_effect=fake_download)
    job = _make_job(
        wl,
        ready=True,
        dest_path=str(tmp_path / "contoso.com" / "alice@contoso.com" / "mailbox.pst"),
        status=M365ExportStatus.READY_TO_DOWNLOAD,
    )
    domain = em._build_exchange_domain("both")
    progress = _make_progress()

    await em.download_job(apm, domain, job, asyncio.Semaphore(1), progress)

    assert job.outcome == "ok"
    assert job.bytes_saved == 4096
    assert Path(job.dest_path).exists()
    export.get_download_url_by_ready_result.assert_awaited_once_with(job.start_result)
    export.get_download_url_by_activity.assert_not_awaited()
    assert progress.downloading == 0
    out = capsys.readouterr().out
    done_line = next(line for line in out.splitlines() if "[Done]" in line)
    assert "alice@contoso.com (mailbox)" in done_line
    assert "4.0 KB" in done_line

async def test_download_job_pending_result_uses_activity_url(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    export = apm.m365.exchange_export

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"x")

    apm.download_file = AsyncMock(side_effect=fake_download)
    job = _make_job(wl, ready=False, dest_path=str(tmp_path / "mailbox.pst"))
    activity = _make_activity(status=M365ExportStatus.READY_TO_DOWNLOAD)
    job.activity = activity
    domain = em._build_exchange_domain("both")

    await em.download_job(apm, domain, job, asyncio.Semaphore(1), _make_progress())

    assert job.outcome == "ok"
    export.get_download_url_by_activity.assert_awaited_once_with(activity)
    export.get_download_url_by_ready_result.assert_not_awaited()

async def test_download_job_apm_error_marks_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.download_file = AsyncMock(side_effect=APMError("connection reset"))
    job = _make_job(wl, ready=True, dest_path=str(tmp_path / "mailbox.pst"))
    domain = em._build_exchange_domain("both")

    await em.download_job(apm, domain, job, asyncio.Semaphore(1), _make_progress())

    assert job.outcome == "failed"
    assert job.outcome_msg == "download error: connection reset"
    out = capsys.readouterr().out
    assert "  [!!] alice@contoso.com (mailbox): download error: connection reset" in out

async def test_download_job_os_error_removes_partial_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"partial")
        raise OSError("disk full")

    apm.download_file = AsyncMock(side_effect=fake_download)
    job = _make_job(wl, ready=True, dest_path=str(tmp_path / "mailbox.pst"))
    domain = em._build_exchange_domain("both")

    await em.download_job(apm, domain, job, asyncio.Semaphore(1), _make_progress())

    assert job.outcome == "failed"
    assert job.outcome_msg == "local I/O error: disk full"
    assert not Path(job.dest_path).exists()
    out = capsys.readouterr().out
    assert "  [!!] alice@contoso.com (mailbox): local I/O error: disk full" in out

async def test_download_job_cancelled_removes_partial_file_and_reraises(
    tmp_path: Path,
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"partial")
        raise asyncio.CancelledError

    apm.download_file = AsyncMock(side_effect=fake_download)
    job = _make_job(wl, ready=True, dest_path=str(tmp_path / "mailbox.pst"))
    domain = em._build_exchange_domain("both")
    progress = _make_progress()

    with pytest.raises(asyncio.CancelledError):
        await em.download_job(apm, domain, job, asyncio.Semaphore(1), progress)

    assert job.outcome == "interrupted"
    assert job.outcome_msg == "download interrupted"
    assert not Path(job.dest_path).exists()
    assert progress.downloading == 0  # finally-block still decrements


# ── _process_one ──────────────────────────────────────────────────────────────


async def test_process_one_returns_early_when_interrupted(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    interrupt = asyncio.Event()
    interrupt.set()
    progress = _make_progress()
    domain = em._build_exchange_domain("both")

    await em._process_one(
        apm, domain, wl, str(tmp_path), asyncio.Semaphore(1), asyncio.Semaphore(1),
        interrupt, progress, [], [], None,
    )

    assert progress.done == 1
    apm.m365.workloads.get_latest_version.assert_not_awaited()

async def test_process_one_with_only_failures_collects_them(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(
        side_effect=ResourceNotFoundError("not found", "WorkloadVersion", "vid")
    )
    progress = _make_progress()
    domain = em._build_exchange_domain("both")
    all_jobs: list[em.MailExportJob] = []
    all_failures: list[em.MailExportFailure] = []

    await em._process_one(
        apm, domain, wl, str(tmp_path), asyncio.Semaphore(1), asyncio.Semaphore(1),
        asyncio.Event(), progress, all_jobs, all_failures, None,
    )

    assert all_jobs == []
    assert [f.error for f in all_failures] == [
        "no backup version found", "no backup version found",
    ]
    assert progress.done == 1
    assert progress.exporting == 0
