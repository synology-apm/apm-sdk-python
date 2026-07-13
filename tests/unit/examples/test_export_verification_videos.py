"""Unit tests for export_verification_videos.py pure and async functions."""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import export_verification_videos as evv
import pytest
from _common import Progress
from export_verification_videos import (
    _classify_workload,
    _dest_filename,
    _download_one,
    _DownloadJob,
    _print_dry_run,
    _SkippedWorkload,
    _write_csv,
)

from synology_apm.sdk import APMError, MachineWorkloadType, ResourceNotFoundError, VerifyStatus
from tests.unit.examples._fixtures import (
    make_fake_apm,
    make_machine_workload,
    make_workload_version,
    patch_make_client,
)

# ── _dest_filename ────────────────────────────────────────────────────────────


def test_dest_filename_sanitizes_slash_in_name() -> None:
    wl = make_machine_workload(name="vm/web-01", workload_id="abcdef01-e89b-12d3-a456-426614174001")
    version = make_workload_version(created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    result = _dest_filename(wl, version)
    assert "/" not in result
    assert "vm_web-01" in result


def test_dest_filename_includes_workload_id_prefix() -> None:
    wl = make_machine_workload(workload_id="abcdef01-e89b-12d3-a456-426614174001")
    version = make_workload_version(created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    result = _dest_filename(wl, version)
    assert "abcdef01" in result


def test_dest_filename_includes_version_date() -> None:
    wl = make_machine_workload()
    version = make_workload_version(created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    result = _dest_filename(wl, version)
    assert "20260102" in result
    assert "030405" in result


def test_dest_filename_ends_with_mp4() -> None:
    wl = make_machine_workload()
    version = make_workload_version()
    assert _dest_filename(wl, version).endswith(".mp4")


# ── _write_csv ────────────────────────────────────────────────────────────────


def test_write_csv_downloaded_job_has_downloaded_status(tmp_path: Path) -> None:
    wl = make_machine_workload(name="vm-web-01")
    version = make_workload_version()
    job = _DownloadJob(
        workload=wl,
        version=version,
        dest_path=str(tmp_path / "vm-web-01.mp4"),
        outcome="ok",
        bytes_saved=1024,
    )
    path = str(tmp_path / "report.csv")
    _write_csv(path, [job], [])

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    assert rows[0]["workload_name"] == "vm-web-01"
    assert rows[0]["status"] == "downloaded"
    assert rows[0]["size_bytes"] == "1024"
    assert rows[0]["dest_path"] != ""


def test_write_csv_failed_job_uses_outcome_as_status(tmp_path: Path) -> None:
    wl = make_machine_workload(name="vm-web-01")
    version = make_workload_version()
    job = _DownloadJob(
        workload=wl,
        version=version,
        dest_path=str(tmp_path / "vm-web-01.mp4"),
        outcome="failed",
        outcome_msg="download error: connection reset",
    )
    path = str(tmp_path / "report.csv")
    _write_csv(path, [job], [])

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["status"] == "failed"
    assert rows[0]["note"] == "download error: connection reset"
    assert rows[0]["dest_path"] == ""


def test_write_csv_skipped_row_has_reason_and_csv_status(tmp_path: Path) -> None:
    wl = make_machine_workload(name="vm-app-01")
    version = make_workload_version()
    skipped = _SkippedWorkload(
        workload=wl,
        version=version,
        reason="verify failed",
        csv_status="skipped_verify_failed",
    )
    path = str(tmp_path / "report.csv")
    _write_csv(path, [], [skipped])

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    assert rows[0]["workload_name"] == "vm-app-01"
    assert rows[0]["status"] == "skipped_verify_failed"
    assert rows[0]["note"] == "verify failed"


def test_write_csv_skipped_row_with_no_version_leaves_version_date_empty(
    tmp_path: Path,
) -> None:
    wl = make_machine_workload(name="vm-db-01")
    skipped = _SkippedWorkload(
        workload=wl,
        version=None,
        reason="no backup version found",
        csv_status="skipped_no_version",
    )
    path = str(tmp_path / "report.csv")
    _write_csv(path, [], [skipped])

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["version_date"] == ""


def test_write_csv_rows_sorted_by_workload_name(tmp_path: Path) -> None:
    wl_b = make_machine_workload(name="vm-web-01")
    wl_a = make_machine_workload(name="vm-app-01", workload_id="123e4567-e89b-12d3-a456-426614174002")
    version = make_workload_version()
    job_b = _DownloadJob(workload=wl_b, version=version, dest_path="/tmp/b.mp4", outcome="ok")
    skipped_a = _SkippedWorkload(
        workload=wl_a,
        version=version,
        reason="verify failed",
        csv_status="skipped_verify_failed",
    )
    path = str(tmp_path / "report.csv")
    _write_csv(path, [job_b], [skipped_a])

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["workload_name"] == "vm-app-01"
    assert rows[1]["workload_name"] == "vm-web-01"


# ── _classify_workload ────────────────────────────────────────────────────────


def _fake_apm(
    *,
    latest_version: object = None,
    version_error: Exception | None = None,
) -> MagicMock:
    """Shared fake APM client with get_latest_version wired for classification tests."""
    apm = make_fake_apm()
    apm.machine.workloads.get_latest_version = AsyncMock(
        return_value=latest_version, side_effect=version_error
    )
    return apm


@pytest.mark.parametrize(
    "verify_status_value, expected_job, expected_csv_status, expected_reason",
    [
        (VerifyStatus.SUCCESS,       "job",  None,                     None),
        (None,                       None,   "silent",                 None),
        (VerifyStatus.NOT_ENABLED,   None,   "silent",                 None),
        (VerifyStatus.FAILED,        None,   "skipped_verify_failed",  "verify failed"),
        (VerifyStatus.VERIFYING,     None,   "skipped_verifying",      "verifying (in progress)"),
        (VerifyStatus.WAITING,       None,   "skipped_verifying",      "verification waiting"),
        (VerifyStatus.CANCELED,      None,   "skipped_verify_canceled","verification canceled"),
        (VerifyStatus.NOT_SUPPORTED, None,   "skipped_not_supported",  "verification not supported"),
        (VerifyStatus.PARTIAL,       None,   "skipped_verify_partial", "partial verification"),
    ],
)
async def test_classify_workload_verify_status_cases(
    tmp_path: Path,
    verify_status_value: VerifyStatus | None,
    expected_job: str | None,
    expected_csv_status: str | None,
    expected_reason: str | None,
) -> None:
    wl = make_machine_workload(workload_id="abcdef01-e89b-12d3-a456-426614174001")
    version = make_workload_version(verify_status=verify_status_value)
    apm = _fake_apm(latest_version=version)
    sem = asyncio.Semaphore(1)

    job, skipped = await _classify_workload(apm, wl, str(tmp_path), sem)

    if expected_job == "job":
        assert job is not None
        assert isinstance(job, _DownloadJob)
        assert job.workload is wl
        assert job.version is version
        assert job.dest_path.endswith(".mp4")
        assert skipped is None
    elif expected_csv_status == "silent":
        assert job is None
        assert skipped is None
    else:
        assert job is None
        assert skipped is not None
        assert skipped.csv_status == expected_csv_status
        assert skipped.reason == expected_reason
        assert skipped.workload is wl
        assert skipped.version is version


async def test_classify_workload_resource_not_found_returns_skipped_no_version(
    tmp_path: Path,
) -> None:
    wl = make_machine_workload()
    apm = _fake_apm(
        version_error=ResourceNotFoundError("not found", "WorkloadVersion", wl.workload_id)
    )
    sem = asyncio.Semaphore(1)

    job, skipped = await _classify_workload(apm, wl, str(tmp_path), sem)

    assert job is None
    assert skipped is not None
    assert skipped.csv_status == "skipped_no_version"
    assert skipped.reason == "no backup version found"
    assert skipped.version is None
    assert skipped.workload is wl


async def test_classify_workload_apm_error_returns_error_status(
    tmp_path: Path,
) -> None:
    wl = make_machine_workload()
    apm = _fake_apm(version_error=APMError("service unavailable", error_code=500))
    sem = asyncio.Semaphore(1)

    job, skipped = await _classify_workload(apm, wl, str(tmp_path), sem)

    assert job is None
    assert skipped is not None
    assert skipped.csv_status == "error"
    assert "service unavailable" in skipped.reason
    assert skipped.version is None
    assert skipped.workload is wl


async def test_classify_workload_success_dest_path_in_output_dir(
    tmp_path: Path,
) -> None:
    wl = make_machine_workload(workload_id="abcdef01-e89b-12d3-a456-426614174001")
    version = make_workload_version(
        verify_status=VerifyStatus.SUCCESS,
        created_at=datetime(2026, 3, 15, 10, 30, 0, tzinfo=UTC),
    )
    apm = _fake_apm(latest_version=version)
    sem = asyncio.Semaphore(1)

    job, skipped = await _classify_workload(apm, wl, str(tmp_path), sem)

    assert job is not None
    assert os.path.dirname(job.dest_path) == str(tmp_path)
    assert "abcdef01" in job.dest_path
    assert "20260315" in job.dest_path


async def test_classify_workload_bounds_concurrent_version_lookups(
    tmp_path: Path,
) -> None:
    """With Semaphore(2), at most two version lookups run at once even for four tasks."""
    active = 0
    max_active = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()

    async def gated_get_latest_version(wl: object) -> object:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            two_entered.set()
        await release.wait()
        active -= 1
        return make_workload_version(verify_status=VerifyStatus.SUCCESS)

    apm = make_fake_apm()
    apm.machine.workloads.get_latest_version = AsyncMock(side_effect=gated_get_latest_version)
    sem = asyncio.Semaphore(2)
    workloads = [
        make_machine_workload(
            name=f"vm-web-{i:02d}",
            workload_id=f"123e4567-e89b-12d3-a456-4266141740{i:02d}",
        )
        for i in range(1, 5)
    ]
    tasks = [
        asyncio.create_task(_classify_workload(apm, wl, str(tmp_path), sem))
        for wl in workloads
    ]

    # Wait until two lookups are held inside the gate, then let everything through.
    await asyncio.wait_for(two_entered.wait(), timeout=5)
    release.set()
    results = await asyncio.gather(*tasks)

    assert max_active == 2
    assert sum(1 for job, _ in results if job is not None) == 4


# ── _list_all_workloads ───────────────────────────────────────────────────────


async def test_list_all_workloads_paginates_two_pages_and_forwards_filters() -> None:
    wl1 = make_machine_workload(name="vm-web-01", workload_id="123e4567-e89b-12d3-a456-426614174001")
    wl2 = make_machine_workload(name="vm-app-01", workload_id="123e4567-e89b-12d3-a456-426614174002")
    wl3 = make_machine_workload(name="vm-db-01", workload_id="123e4567-e89b-12d3-a456-426614174003")
    apm = make_fake_apm()
    apm.machine.workloads.list.side_effect = [([wl1, wl2], 3), ([wl3], 3)]

    result = await evv._list_all_workloads(
        apm, [MachineWorkloadType.VM], "web", "ns-apm-server-01"
    )

    assert [wl.name for wl in result] == ["vm-web-01", "vm-app-01", "vm-db-01"]
    first, second = apm.machine.workloads.list.await_args_list
    assert first.kwargs == {
        "workload_types": [MachineWorkloadType.VM],
        "is_retired": False,
        "name_contains": "web",
        "namespace": "ns-apm-server-01",
        "limit": 500,
        "offset": 0,
    }
    assert second.kwargs["offset"] == 2
    assert second.kwargs["limit"] == 500


# ── _download_one ─────────────────────────────────────────────────────────────


def _make_dl_job(tmp_path: Path, name: str = "vm-web-01") -> _DownloadJob:
    wl = make_machine_workload(name=name, workload_type=MachineWorkloadType.VM)
    version = make_workload_version(verify_status=VerifyStatus.SUCCESS)
    return _DownloadJob(
        workload=wl,
        version=version,
        dest_path=str(tmp_path / "videos" / f"{name}.mp4"),
    )


async def test_download_one_interrupted_before_start_skips_download(tmp_path: Path) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.get_verification_video_url = AsyncMock()
    job = _make_dl_job(tmp_path)
    progress = Progress(total=1, noun="video", show_exporting=False)
    interrupt = asyncio.Event()
    interrupt.set()

    await _download_one(apm, job, asyncio.Semaphore(1), progress, interrupt)

    assert job.outcome == "interrupted"
    assert job.outcome_msg == "interrupted before download started"
    assert progress.done == 1
    apm.machine.workloads.get_verification_video_url.assert_not_awaited()


async def test_download_one_success_saves_file_and_updates_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"x" * 2048)

    apm.download_file = AsyncMock(side_effect=fake_download)
    job = _make_dl_job(tmp_path)
    progress = Progress(total=1, noun="video", show_exporting=False)

    await _download_one(apm, job, asyncio.Semaphore(1), progress, asyncio.Event())

    assert job.outcome == "ok"
    assert job.bytes_saved == 2048
    assert os.path.exists(job.dest_path)
    assert progress.done == 1
    assert progress.downloading == 0
    apm.machine.workloads.get_verification_video_url.assert_awaited_once_with(
        job.workload, job.version
    )
    err = capsys.readouterr().err
    done_line = next(line for line in err.splitlines() if "[Done]" in line)
    assert "vm-web-01" in done_line
    assert "2.0 KB" in done_line


async def test_download_one_apm_error_cleans_up_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )
    apm.download_file = AsyncMock(side_effect=APMError("connection reset"))
    job = _make_dl_job(tmp_path)
    os.makedirs(os.path.dirname(job.dest_path), exist_ok=True)
    Path(job.dest_path).write_bytes(b"partial")
    progress = Progress(total=1, noun="video", show_exporting=False)

    await _download_one(apm, job, asyncio.Semaphore(1), progress, asyncio.Event())

    assert job.outcome == "failed"
    assert job.outcome_msg == "download error: connection reset"
    assert not os.path.exists(job.dest_path)
    assert progress.done == 1
    assert progress.downloading == 0
    err = capsys.readouterr().err
    assert "  [!!] vm-web-01: download error: connection reset" in err


async def test_download_one_os_error_cleans_up_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )
    apm.download_file = AsyncMock(side_effect=OSError("disk full"))
    job = _make_dl_job(tmp_path)
    os.makedirs(os.path.dirname(job.dest_path), exist_ok=True)
    Path(job.dest_path).write_bytes(b"partial")
    progress = Progress(total=1, noun="video", show_exporting=False)

    await _download_one(apm, job, asyncio.Semaphore(1), progress, asyncio.Event())

    assert job.outcome == "failed"
    assert job.outcome_msg == "local I/O error: disk full"
    assert not os.path.exists(job.dest_path)
    err = capsys.readouterr().err
    assert "  [!!] vm-web-01: local I/O error: disk full" in err


async def test_download_one_cancelled_removes_partial_file_and_reraises(
    tmp_path: Path,
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"partial")
        raise asyncio.CancelledError

    apm.download_file = AsyncMock(side_effect=fake_download)
    job = _make_dl_job(tmp_path)
    progress = Progress(total=1, noun="video", show_exporting=False)

    with pytest.raises(asyncio.CancelledError):
        await _download_one(apm, job, asyncio.Semaphore(1), progress, asyncio.Event())

    assert job.outcome == "interrupted"
    assert job.outcome_msg == "download interrupted"
    assert not os.path.exists(job.dest_path)
    # finally-block still updates the counters
    assert progress.done == 1
    assert progress.downloading == 0


# ── _print_dry_run ────────────────────────────────────────────────────────────


def test_print_dry_run_lists_jobs_and_counts(capsys: pytest.CaptureFixture[str]) -> None:
    wl = make_machine_workload(name="vm-web-01", workload_type=MachineWorkloadType.VM)
    version = make_workload_version(version_id="123e4567-e89b-12d3-a456-426614174080")
    job = _DownloadJob(workload=wl, version=version, dest_path="/videos/vm-web-01.mp4")
    skipped = _SkippedWorkload(
        workload=make_machine_workload(name="vm-db-01"),
        version=None,
        reason="verify failed",
        csv_status="skipped_verify_failed",
    )

    _print_dry_run([job], [skipped])

    err = capsys.readouterr().err
    job_line = next(line for line in err.splitlines() if "vm-web-01" in line)
    assert "VM" in job_line
    assert "123e4567" in job_line  # version-ID prefix
    assert "1 video(s) would be downloaded." in err
    assert "1 workload(s) would be skipped." in err
    assert "[dry-run] No files written." in err


# ── run() ─────────────────────────────────────────────────────────────────────


def _patch_interrupt_hooks(monkeypatch: pytest.MonkeyPatch) -> dict[str, asyncio.Event]:
    """Neutralize signal-handler registration and expose the interrupt event."""
    holder: dict[str, asyncio.Event] = {}

    def fake_register(loop: object, event: asyncio.Event) -> None:
        holder["event"] = event

    monkeypatch.setattr(evv, "register_interrupt", fake_register)
    monkeypatch.setattr(evv, "unregister_interrupt", lambda loop: None)
    return holder


async def test_run_no_workloads_returns_zero_without_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, evv, apm)

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=False, yes=True, concurrency=2, csv_path=None,
    )

    assert rc == 0
    assert "No workloads found." in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "type_filter,expected_types",
    [
        ("ps", [MachineWorkloadType.PS]),
        ("vm", [MachineWorkloadType.VM]),
        ("all", [MachineWorkloadType.PS, MachineWorkloadType.VM]),
    ],
)
async def test_run_maps_workload_type_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    type_filter: str,
    expected_types: list[MachineWorkloadType],
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, evv, apm)

    rc = await evv.run(
        workload_type_filter=type_filter, output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=False, yes=True, concurrency=2, csv_path=None,
    )

    assert rc == 0
    assert apm.machine.workloads.list.await_args.kwargs["workload_types"] == expected_types


async def test_run_all_skipped_returns_zero_without_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = make_machine_workload(name="vm-web-01")
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([wl], 1)
    apm.machine.workloads.get_latest_version = AsyncMock(
        return_value=make_workload_version(verify_status=VerifyStatus.FAILED)
    )
    patch_make_client(monkeypatch, evv, apm)

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=False, yes=True, concurrency=2, csv_path=None,
    )

    assert rc == 0
    err = capsys.readouterr().err
    assert "No workloads with SUCCESS verification videos found." in err
    assert "1 skipped" in err
    assert list(tmp_path.iterdir()) == []


async def test_run_dry_run_downloads_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = make_machine_workload(name="vm-web-01")
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([wl], 1)
    apm.machine.workloads.get_latest_version = AsyncMock(
        return_value=make_workload_version(verify_status=VerifyStatus.SUCCESS)
    )
    apm.download_file = AsyncMock()
    patch_make_client(monkeypatch, evv, apm)

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=True, yes=False, concurrency=2, csv_path=None,
    )

    assert rc == 0
    assert "[dry-run] No files written." in capsys.readouterr().err
    apm.download_file.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []


async def test_run_declined_confirmation_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = make_machine_workload(name="vm-web-01")
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([wl], 1)
    apm.machine.workloads.get_latest_version = AsyncMock(
        return_value=make_workload_version(verify_status=VerifyStatus.SUCCESS)
    )
    apm.download_file = AsyncMock()
    patch_make_client(monkeypatch, evv, apm)
    monkeypatch.setattr(evv, "prompt_yes_no", AsyncMock(return_value=False))

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=False, yes=False, concurrency=2,
        csv_path=str(tmp_path / "report.csv"),
    )

    assert rc == 0
    assert "Cancelled." in capsys.readouterr().err
    apm.download_file.assert_not_awaited()
    assert not (tmp_path / "report.csv").exists()


async def test_run_happy_path_auto_names_csv_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = make_machine_workload(name="vm-web-01", workload_type=MachineWorkloadType.VM)
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([wl], 1)
    apm.machine.workloads.get_latest_version = AsyncMock(
        return_value=make_workload_version(verify_status=VerifyStatus.SUCCESS)
    )
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"x" * 1024)

    apm.download_file = AsyncMock(side_effect=fake_download)
    patch_make_client(monkeypatch, evv, apm)
    _patch_interrupt_hooks(monkeypatch)
    output_dir = tmp_path / "videos"

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(output_dir), keyword=None,
        namespace=None, dry_run=False, yes=True, concurrency=2, csv_path=None,
    )

    assert rc == 0
    reports = list(output_dir.glob("download_report_*.csv"))
    assert len(reports) == 1
    with open(reports[0], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["workload_name"] == "vm-web-01"
    assert rows[0]["status"] == "downloaded"
    assert rows[0]["size_bytes"] == "1024"
    err = capsys.readouterr().err
    counts_line = next(line for line in err.splitlines() if "Downloaded:" in line)
    assert "Downloaded: 1" in counts_line
    assert "Failed: 0" in counts_line


async def test_run_counts_workloads_without_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl_ok = make_machine_workload(
        name="vm-web-01", workload_id="123e4567-e89b-12d3-a456-426614174001"
    )
    wl_silent = make_machine_workload(
        name="vm-app-01", workload_id="123e4567-e89b-12d3-a456-426614174002"
    )
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([wl_ok, wl_silent], 2)
    apm.machine.workloads.get_latest_version = AsyncMock(
        side_effect=[
            make_workload_version(verify_status=VerifyStatus.SUCCESS),
            make_workload_version(verify_status=None),  # verification not enabled
        ]
    )
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )

    async def fake_download(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"x" * 1024)

    apm.download_file = AsyncMock(side_effect=fake_download)
    patch_make_client(monkeypatch, evv, apm)
    _patch_interrupt_hooks(monkeypatch)
    csv_path = tmp_path / "report.csv"

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=False, yes=True, concurrency=2, csv_path=str(csv_path),
    )

    assert rc == 0
    err = capsys.readouterr().err
    assert "1 ready, 1 without verification." in err
    counts_line = next(line for line in err.splitlines() if "Downloaded:" in line)
    assert "No verification: 1" in counts_line
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # silently skipped workloads do not appear in the CSV
    assert [r["workload_name"] for r in rows] == ["vm-web-01"]


async def test_run_failed_download_returns_one_and_writes_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = make_machine_workload(name="vm-web-01")
    wl_skipped = make_machine_workload(
        name="vm-db-01", workload_id="123e4567-e89b-12d3-a456-426614174003"
    )
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([wl, wl_skipped], 2)
    apm.machine.workloads.get_latest_version = AsyncMock(
        side_effect=[
            make_workload_version(verify_status=VerifyStatus.SUCCESS),
            make_workload_version(verify_status=VerifyStatus.FAILED),
        ]
    )
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )
    apm.download_file = AsyncMock(side_effect=APMError("connection reset"))
    patch_make_client(monkeypatch, evv, apm)
    _patch_interrupt_hooks(monkeypatch)
    csv_path = tmp_path / "report.csv"

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=False, yes=True, concurrency=2, csv_path=str(csv_path),
    )

    assert rc == 1
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["workload_name"], r["status"]) for r in rows] == [
        ("vm-db-01", "skipped_verify_failed"),
        ("vm-web-01", "failed"),
    ]
    assert rows[1]["note"] == "download error: connection reset"
    counts_line = next(
        line for line in capsys.readouterr().err.splitlines() if "Downloaded:" in line
    )
    assert "Failed: 1" in counts_line
    assert "Skipped: 1" in counts_line


async def test_run_interrupt_still_writes_csv_and_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = make_machine_workload(name="vm-web-01")
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([wl], 1)
    apm.machine.workloads.get_latest_version = AsyncMock(
        return_value=make_workload_version(verify_status=VerifyStatus.SUCCESS)
    )
    apm.machine.workloads.get_verification_video_url = AsyncMock(
        return_value="https://apm.corp.com/download/video"
    )
    patch_make_client(monkeypatch, evv, apm)
    holder = _patch_interrupt_hooks(monkeypatch)

    async def blocking_download(url: str, dest: str) -> None:
        # Simulate Ctrl+C arriving mid-download, then block until cancelled.
        holder["event"].set()
        await asyncio.Event().wait()

    apm.download_file = AsyncMock(side_effect=blocking_download)
    csv_path = tmp_path / "report.csv"

    rc = await evv.run(
        workload_type_filter="all", output_dir=str(tmp_path), keyword=None,
        namespace=None, dry_run=False, yes=True, concurrency=1, csv_path=str(csv_path),
    )

    assert rc == 1
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["status"] == "interrupted"
    assert rows[0]["note"] == "download interrupted"
    err = capsys.readouterr().err
    assert "Interrupted." in err
    counts_line = next(line for line in err.splitlines() if "Downloaded:" in line)
    assert "Interrupted: 1" in counts_line


# ── main() ────────────────────────────────────────────────────────────────────


def _capture_main_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch run/run_main so main() only records the kwargs it would pass."""
    captured: dict[str, object] = {}

    def fake_run(**kwargs: object) -> object:
        captured.update(kwargs)

        async def _noop() -> int:
            return 0

        return _noop()

    monkeypatch.setattr(evv, "run", fake_run)
    monkeypatch.setattr(evv, "run_main", lambda coro: coro.close())
    return captured


def test_main_passes_arguments_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_main_run(monkeypatch)
    argv = [
        "export_verification_videos.py",
        "--workload-type", "vm",
        "--output-dir", "/videos",
        "--keyword", "web",
        "--namespace", "ns-apm-server-01",
        "--yes",
        "--concurrency", "5",
        "--csv", "/videos/report.csv",
    ]
    with patch.object(sys, "argv", argv):
        evv.main()

    assert captured == {
        "workload_type_filter": "vm",
        "output_dir": "/videos",
        "keyword": "web",
        "namespace": "ns-apm-server-01",
        "dry_run": False,
        "yes": True,
        "concurrency": 5,
        "csv_path": "/videos/report.csv",
    }


def test_main_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_main_run(monkeypatch)
    with patch.object(sys, "argv", ["export_verification_videos.py"]):
        evv.main()

    assert captured == {
        "workload_type_filter": "all",
        "output_dir": "./verification_videos",
        "keyword": None,
        "namespace": None,
        "dry_run": False,
        "yes": False,
        "concurrency": 3,
        "csv_path": None,
    }


def test_main_rejects_invalid_workload_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture_main_run(monkeypatch)
    with patch.object(sys, "argv", ["export_verification_videos.py", "--workload-type", "pc"]):
        with pytest.raises(SystemExit) as exc:
            evv.main()
    assert exc.value.code == 2
    assert "invalid choice: 'pc'" in capsys.readouterr().err
