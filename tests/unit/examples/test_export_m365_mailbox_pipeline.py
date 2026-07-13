"""Unit tests for examples/export_m365_mailbox.py — async pipeline, orchestration, and CLI."""
from __future__ import annotations

import asyncio
import csv
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import export_m365_mailbox as em
import pytest
from _common import Progress, interruptible_sleep

from synology_apm.sdk import (
    APMError,
    M365ExportActivity,
    M365ExportStartResult,
    M365ExportStatus,
    M365Workload,
    M365WorkloadType,
    ResourceNotFoundError,
)
from tests.unit.examples._fixtures import (
    make_fake_apm,
    make_m365_group_info,
    make_m365_user_info,
    make_m365_workload,
    make_version_location,
    make_workload_version,
    patch_make_client,
)

_TENANT_ID = "123e4567-e89b-12d3-a456-426614174000"
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


# ── list_workloads ────────────────────────────────────────────────────────────


async def test_list_workloads_total_zero_returns_early() -> None:
    apm = make_fake_apm()
    domain = em._build_exchange_domain("both")

    items, total = await em.list_workloads(apm, domain, _TENANT_ID, None)

    assert items == []
    assert total == 0
    apm.m365.workloads.list.assert_awaited_once_with(
        tenant_id=_TENANT_ID,
        workload_type=M365WorkloadType.EXCHANGE,
        is_retired=False,
        keyword=None,
        limit=1,
        offset=0,
    )


async def test_list_workloads_quick_count_then_paginates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl1 = _user_workload("alice@contoso.com")
    wl2 = _user_workload("bob@contoso.com")
    wl3 = _user_workload("carol@contoso.com")
    apm = make_fake_apm()
    apm.m365.workloads.list.side_effect = [
        ([wl1], 3),        # quick count (limit=1)
        ([wl1, wl2], 3),   # page 1
        ([wl3], 3),        # page 2
    ]
    domain = em._build_exchange_domain("both")

    items, total = await em.list_workloads(apm, domain, _TENANT_ID, "contoso")

    assert [em._upn(w) for w in items] == [
        "alice@contoso.com", "bob@contoso.com", "carol@contoso.com",
    ]
    assert total == 3
    quick, page1, page2 = apm.m365.workloads.list.await_args_list
    assert quick.kwargs["limit"] == 1
    assert page1.kwargs == {
        "tenant_id": _TENANT_ID,
        "workload_type": M365WorkloadType.EXCHANGE,
        "is_retired": False,
        "keyword": "contoso",
        "limit": 500,
        "offset": 0,
    }
    assert page2.kwargs["offset"] == 2
    assert "Found 3 Exchange user(s)." in capsys.readouterr().out


async def test_list_workloads_reports_fetch_progress_beyond_one_page(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    apm = make_fake_apm()
    apm.m365.workloads.list.side_effect = [
        ([wl], 501),
        ([wl] * 500, 501),
        ([wl], 501),
    ]
    domain = em._build_exchange_domain("both")

    items, total = await em.list_workloads(apm, domain, _TENANT_ID, None)

    assert len(items) == 501
    assert total == 501
    assert "Fetching... 500/501" in capsys.readouterr().out


# ── _start_workload ───────────────────────────────────────────────────────────


async def test_start_workload_splits_immediate_and_pending(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    version = make_workload_version()
    apm.m365.workloads.get_latest_version = AsyncMock(return_value=version)
    ready_result = _make_start_result(wl, execution_id=_EXEC_ID, ready=True)
    pending_result = _make_start_result(wl, execution_id=_EXEC_ID_2, ready=False)
    apm.m365.exchange_export.start = AsyncMock(side_effect=[ready_result, pending_result])
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert failures == []
    assert [j.unit_label for j in immediate] == ["mailbox"]
    assert immediate[0].status == M365ExportStatus.READY_TO_DOWNLOAD
    assert immediate[0].identity == "alice@contoso.com"
    assert [j.unit_label for j in pending] == ["archive mailbox"]
    assert pending[0].status == M365ExportStatus.PREPARING
    assert pending[0].dest_path.endswith("_archive_mailbox.pst")
    first, second = apm.m365.exchange_export.start.await_args_list
    assert first.args == (wl, version)
    assert first.kwargs["archive_mailbox"] is False
    assert first.kwargs["export_name"].endswith("_mailbox.pst")
    assert second.kwargs["archive_mailbox"] is True
    assert second.kwargs["export_name"].endswith("_archive_mailbox.pst")


async def test_start_workload_resume_skips_downloaded_pairs(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(
        return_value=_make_start_result(wl, ready=False)
    )
    domain = em._build_exchange_domain("both")
    skip_pairs = {("alice@contoso.com", "mailbox")}

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), skip_pairs
    )

    assert failures == []
    assert immediate == []
    assert [j.unit_label for j in pending] == ["archive mailbox"]
    apm.m365.exchange_export.start.assert_awaited_once()
    assert apm.m365.exchange_export.start.await_args.kwargs["archive_mailbox"] is True


async def test_start_workload_version_not_found_fails_all_units(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(
        side_effect=ResourceNotFoundError("not found", "WorkloadVersion", "vid")
    )
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert pending == []
    assert immediate == []
    assert [(f.identity, f.unit_label, f.error) for f in failures] == [
        ("alice@contoso.com", "mailbox", "no backup version found"),
        ("alice@contoso.com", "archive mailbox", "no backup version found"),
    ]
    apm.m365.exchange_export.start.assert_not_awaited()


async def test_start_workload_version_lookup_error_fans_out_to_all_units(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(
        side_effect=APMError("temporarily unavailable")
    )
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert pending == []
    assert immediate == []
    assert [f.error for f in failures] == [
        "version lookup failed: temporarily unavailable",
        "version lookup failed: temporarily unavailable",
    ]
    out = capsys.readouterr().out
    assert (
        "  [!!] alice@contoso.com: failed to get latest version: temporarily unavailable" in out
    )


async def test_start_workload_unit_not_found_keeps_other_units(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(
        side_effect=[
            ResourceNotFoundError("not found", "M365Workload", "wid"),
            _make_start_result(wl, execution_id=_EXEC_ID_2, ready=False),
        ]
    )
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert [(f.unit_label, f.error) for f in failures] == [("mailbox", "resource not found")]
    assert [j.unit_label for j in pending] == ["archive mailbox"]
    assert immediate == []


async def test_start_workload_unit_apm_error_records_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(side_effect=APMError("permission denied"))
    domain = em._build_exchange_domain("primary")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert pending == []
    assert immediate == []
    assert [(f.unit_label, f.error) for f in failures] == [
        ("mailbox", "start failed: permission denied"),
    ]
    out = capsys.readouterr().out
    assert "  [!!] alice@contoso.com (mailbox): failed to start export: permission denied" in out


async def test_start_workload_group_domain_starts_single_unit(tmp_path: Path) -> None:
    wl = make_m365_workload(
        name="Marketing",
        workload_type=M365WorkloadType.GROUP,
        info=make_m365_group_info(mail="marketing@contoso.com"),
    )
    version = make_workload_version()
    apm = make_fake_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(return_value=version)
    apm.m365.group_export.start = AsyncMock(
        return_value=_make_start_result(wl, ready=False)
    )
    domain = em._build_group_domain()

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert failures == []
    assert immediate == []
    assert [(j.identity, j.unit_label) for j in pending] == [("marketing@contoso.com", "")]
    apm.m365.group_export.start.assert_awaited_once()
    assert apm.m365.group_export.start.await_args is not None
    assert apm.m365.group_export.start.await_args.args == (wl, version)
    export_name = apm.m365.group_export.start.await_args.kwargs["export_name"]
    assert export_name.startswith("marketing@contoso.com_")
    assert export_name.endswith(".pst")


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


# ── run() ─────────────────────────────────────────────────────────────────────


def _run_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "tenant_id": _TENANT_ID,
        "output_dir": "./exports",
        "keyword": None,
        "cancel": False,
        "dry_run": False,
        "yes": True,
        "concurrency": 2,
        "download_concurrency": 2,
        "csv_path": None,
        "resume_csv": None,
    }
    kwargs.update(overrides)
    return kwargs


async def test_run_missing_resume_csv_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_make_client(monkeypatch, em, make_fake_apm())
    domain = em._build_exchange_domain("both")
    missing = str(tmp_path / "missing_report.csv")

    rc = await em.run(domain, **_run_kwargs(resume_csv=missing))  # type: ignore[arg-type]

    assert rc == 1
    assert f"Error: resume CSV not found: {missing}" in capsys.readouterr().err


async def test_run_no_workloads_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs())  # type: ignore[arg-type]

    assert rc == 0
    assert "No Exchange workloads found." in capsys.readouterr().out


async def test_run_dry_run_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    wl = _user_workload()
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([wl], 1)))
    run_export_mock = AsyncMock()
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs(dry_run=True))  # type: ignore[arg-type]

    assert rc == 0
    assert "[dry-run] No exports started." in capsys.readouterr().out
    run_export_mock.assert_not_awaited()


async def test_run_cancel_mode_dispatches_to_cancel_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    wl = _user_workload()
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([wl], 1)))
    cancel_all_mock = AsyncMock(return_value=5)
    monkeypatch.setattr(em, "cancel_all", cancel_all_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs(cancel=True, dry_run=True))  # type: ignore[arg-type]

    assert rc == 5
    cancel_all_mock.assert_awaited_once()
    assert cancel_all_mock.await_args is not None
    args = cancel_all_mock.await_args.args
    assert args[0] is apm
    assert args[2] == [wl]
    assert args[3] is True  # dry_run forwarded


async def test_run_auto_names_csv_and_forwards_to_run_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    wl = _user_workload()
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([wl], 1)))
    run_export_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")
    output_dir = str(tmp_path / "exports")

    rc = await em.run(domain, **_run_kwargs(output_dir=output_dir))  # type: ignore[arg-type]

    assert rc == 0
    assert run_export_mock.await_args is not None
    args = run_export_mock.await_args.args
    assert args[2] == [wl]           # items
    assert args[3] == output_dir
    csv_arg = args[7]
    assert re.fullmatch(
        re.escape(output_dir) + r"/export_report_\d{8}_\d{6}\.csv", csv_arg
    )
    assert args[8] is None           # skip_pairs
    assert args[9] == []             # carried_rows


async def test_run_resume_filters_items_and_ignores_keyword(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    resume_csv = tmp_path / "previous_report.csv"
    with open(resume_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=em._EXCHANGE_CSV_FIELDS)
        writer.writeheader()
        writer.writerow({
            "upn": "alice@contoso.com", "domain": "contoso.com",
            "mailbox_type": "mailbox", "execution_id": _EXEC_ID,
            "status": "downloaded", "size_bytes": "1024", "error": "",
            "dest_path": "/exports/contoso.com/alice@contoso.com/mailbox.pst",
        })
        writer.writerow({
            "upn": "bob@contoso.com", "domain": "contoso.com",
            "mailbox_type": "mailbox", "execution_id": _EXEC_ID_2,
            "status": "failed", "size_bytes": "", "error": "download error",
            "dest_path": "",
        })

    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    alice = _user_workload("alice@contoso.com")
    bob = _user_workload("bob@contoso.com")
    list_mock = AsyncMock(return_value=([alice, bob], 2))
    monkeypatch.setattr(em, "list_workloads", list_mock)
    run_export_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(
        domain,
        **_run_kwargs(keyword="contoso", resume_csv=str(resume_csv)),  # type: ignore[arg-type]
    )

    assert rc == 0
    # --keyword is ignored in resume mode
    assert list_mock.await_args is not None
    assert list_mock.await_args.args[3] is None
    assert "Note: --keyword is ignored in resume mode" in capsys.readouterr().err
    assert run_export_mock.await_args is not None
    args = run_export_mock.await_args.args
    assert args[2] == [bob]                                        # only pending identities
    assert args[8] == {("alice@contoso.com", "mailbox")}           # skip_pairs
    assert len(args[9]) == 1                                       # carried rows
    assert args[9][0]["upn"] == "alice@contoso.com"


async def test_run_resume_all_downloaded_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    resume_csv = tmp_path / "previous_report.csv"
    with open(resume_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=em._EXCHANGE_CSV_FIELDS)
        writer.writeheader()
        writer.writerow({
            "upn": "alice@contoso.com", "domain": "contoso.com",
            "mailbox_type": "mailbox", "execution_id": _EXEC_ID,
            "status": "downloaded", "size_bytes": "1024", "error": "",
            "dest_path": "/exports/contoso.com/alice@contoso.com/mailbox.pst",
        })

    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    alice = _user_workload("alice@contoso.com")
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([alice], 1)))
    run_export_mock = AsyncMock()
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs(resume_csv=str(resume_csv)))  # type: ignore[arg-type]

    assert rc == 0
    assert "All users already downloaded — nothing to do." in capsys.readouterr().out
    run_export_mock.assert_not_awaited()


# ── main() / _add_common_args ─────────────────────────────────────────────────


def _capture_main_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch run/run_main so main() only records what it would pass."""
    captured: dict[str, object] = {}

    def fake_run(domain: em.MailExportDomain, **kwargs: object) -> object:
        captured["domain"] = domain
        captured.update(kwargs)

        async def _noop() -> int:
            return 0

        return _noop()

    monkeypatch.setattr(em, "run", fake_run)
    monkeypatch.setattr(em, "run_main", lambda coro: coro.close())
    return captured


def test_main_requires_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(sys, "argv", ["export_m365_mailbox.py"]):
        with pytest.raises(SystemExit) as exc:
            em.main()
    assert exc.value.code == 2
    assert "the following arguments are required: {exchange,group}" in capsys.readouterr().err


def test_main_requires_tenant_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture_main_run(monkeypatch)
    with patch.object(sys, "argv", ["export_m365_mailbox.py", "exchange"]):
        with pytest.raises(SystemExit) as exc:
            em.main()
    assert exc.value.code == 1
    assert "Error: --tenant-id is required" in capsys.readouterr().err


def test_main_rejects_primary_only_with_archive_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [
        "export_m365_mailbox.py", "exchange", "--tenant-id", _TENANT_ID,
        "--primary-only", "--archive-only",
    ]
    with patch.object(sys, "argv", argv):
        with pytest.raises(SystemExit) as exc:
            em.main()
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_main_exchange_passes_arguments_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_main_run(monkeypatch)
    argv = [
        "export_m365_mailbox.py", "exchange",
        "--tenant-id", _TENANT_ID,
        "--output-dir", "/exports",
        "--keyword", "alice",
        "--csv", "/exports/report.csv",
        "--resume", "/exports/previous_report.csv",
        "--cancel",
        "--dry-run",
        "--yes",
        "--concurrency", "4",
        "--download-concurrency", "8",
    ]
    with patch.object(sys, "argv", argv):
        em.main()

    domain = captured.pop("domain")
    assert isinstance(domain, em.MailExportDomain)
    assert domain.type_label == "Exchange"
    assert domain.extra_note == " — primary + archive"
    assert captured == {
        "tenant_id": _TENANT_ID,
        "output_dir": "/exports",
        "keyword": "alice",
        "cancel": True,
        "dry_run": True,
        "yes": True,
        "concurrency": 4,
        "download_concurrency": 8,
        "csv_path": "/exports/report.csv",
        "resume_csv": "/exports/previous_report.csv",
    }


@pytest.mark.parametrize(
    "scope_flag,expected_note",
    [
        ("--primary-only", " — primary only"),
        ("--archive-only", " — archive only"),
    ],
)
def test_main_exchange_scope_flags_select_domain_scope(
    monkeypatch: pytest.MonkeyPatch, scope_flag: str, expected_note: str
) -> None:
    captured = _capture_main_run(monkeypatch)
    argv = ["export_m365_mailbox.py", "exchange", "--tenant-id", _TENANT_ID, scope_flag]
    with patch.object(sys, "argv", argv):
        em.main()

    domain = captured["domain"]
    assert isinstance(domain, em.MailExportDomain)
    assert domain.extra_note == expected_note


def test_main_group_builds_group_domain_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_main_run(monkeypatch)
    argv = ["export_m365_mailbox.py", "group", "--tenant-id", _TENANT_ID]
    with patch.object(sys, "argv", argv):
        em.main()

    domain = captured.pop("domain")
    assert isinstance(domain, em.MailExportDomain)
    assert domain.type_label == "Group"
    assert domain.id_field == "group_mail"
    assert captured == {
        "tenant_id": _TENANT_ID,
        "output_dir": "./exports",
        "keyword": None,
        "cancel": False,
        "dry_run": False,
        "yes": False,
        "concurrency": 3,
        "download_concurrency": 5,
        "csv_path": None,
        "resume_csv": None,
    }
