"""Unit tests for backup_catchup.py: pure functions, poll loop, and run()/main() behavior."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import backup_catchup
import pytest
from backup_catchup import (
    _NEEDS_RETRY,
    TERMINAL_STATUSES,
    _is_stale,
    _poll_all,
    _poll_one,
    _reason,
    run,
)

from synology_apm.sdk import APMError, BackupActivityStatus, M365WorkloadType, WorkloadStatus
from tests.unit.examples._fixtures import (
    make_backup_activity,
    make_fake_apm,
    make_m365_workload,
    make_machine_workload,
    make_saas_tenant,
    patch_make_client,
)

_NOW = datetime.now(UTC)
_RECENT = _NOW - timedelta(hours=1)
_OLD = _NOW - timedelta(days=3)
_CUTOFF = _NOW - timedelta(days=1)


# ── Status-set partitions ─────────────────────────────────────────────────────


def test_terminal_statuses_are_the_completed_outcomes() -> None:
    assert TERMINAL_STATUSES == {
        BackupActivityStatus.SUCCESS,
        BackupActivityStatus.FAILED,
        BackupActivityStatus.PARTIAL,
        BackupActivityStatus.CANCELED,
    }


def test_non_terminal_statuses_are_the_in_progress_states() -> None:
    assert set(BackupActivityStatus) - TERMINAL_STATUSES == {
        BackupActivityStatus.QUEUING,
        BackupActivityStatus.BACKING_UP,
        BackupActivityStatus.CANCELING,
    }


def test_needs_retry_contains_failed_partial_canceled() -> None:
    assert WorkloadStatus.FAILED in _NEEDS_RETRY
    assert WorkloadStatus.PARTIAL in _NEEDS_RETRY
    assert WorkloadStatus.CANCELED in _NEEDS_RETRY


def test_needs_retry_excludes_success_and_non_terminal() -> None:
    assert WorkloadStatus.SUCCESS not in _NEEDS_RETRY
    assert WorkloadStatus.QUEUING not in _NEEDS_RETRY
    assert WorkloadStatus.BACKING_UP not in _NEEDS_RETRY
    assert WorkloadStatus.NO_BACKUPS not in _NEEDS_RETRY


# ── _reason ───────────────────────────────────────────────────────────────────


def test_reason_never_backed_up_returns_fixed_string() -> None:
    wl = make_machine_workload(last_backup_at=None, status=WorkloadStatus.NO_BACKUPS)
    assert _reason(wl, never_backed_up_only=False) == "never backed up"
    assert _reason(wl, never_backed_up_only=True) == "never backed up"


def test_reason_needs_retry_status_returns_status_value() -> None:
    for status in _NEEDS_RETRY:
        wl = make_machine_workload(last_backup_at=_RECENT, status=status)
        result = _reason(wl, never_backed_up_only=False)
        assert result == f"last backup {status.value}", (
            f"Expected 'last backup {status.value}' for status {status!r}, got {result!r}"
        )


def test_reason_never_backed_up_only_true_skips_retry_branch() -> None:
    # Workload has a recent backup but status is FAILED; never_backed_up_only=True means
    # the retry branch is bypassed — result is "overdue", not "last backup failed".
    wl = make_machine_workload(last_backup_at=_RECENT, status=WorkloadStatus.FAILED)
    assert _reason(wl, never_backed_up_only=True) == "overdue"


def test_reason_overdue_healthy_status() -> None:
    wl = make_machine_workload(last_backup_at=_OLD, status=WorkloadStatus.SUCCESS)
    assert _reason(wl, never_backed_up_only=False) == "overdue"


def test_reason_overdue_healthy_status_never_backed_up_only_true() -> None:
    wl = make_machine_workload(last_backup_at=_OLD, status=WorkloadStatus.SUCCESS)
    assert _reason(wl, never_backed_up_only=True) == "overdue"


def test_reason_m365_workload_never_backed_up() -> None:
    wl = make_m365_workload(last_backup_at=None, status=WorkloadStatus.NO_BACKUPS)
    assert _reason(wl, never_backed_up_only=False) == "never backed up"


def test_reason_m365_workload_needs_retry() -> None:
    wl = make_m365_workload(last_backup_at=_OLD, status=WorkloadStatus.PARTIAL)
    assert _reason(wl, never_backed_up_only=False) == f"last backup {WorkloadStatus.PARTIAL.value}"


# ── _is_stale ─────────────────────────────────────────────────────────────────


def test_is_stale_never_backed_up_always_true() -> None:
    wl = make_machine_workload(last_backup_at=None, status=WorkloadStatus.NO_BACKUPS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is True
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=True) is True


def test_is_stale_recent_backup_success_is_not_stale() -> None:
    wl = make_machine_workload(last_backup_at=_RECENT, status=WorkloadStatus.SUCCESS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is False


def test_is_stale_old_backup_success_overdue() -> None:
    wl = make_machine_workload(last_backup_at=_OLD, status=WorkloadStatus.SUCCESS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is True


def test_is_stale_needs_retry_recent_backup_still_stale() -> None:
    # Status in _NEEDS_RETRY marks it stale regardless of last_backup_at recency.
    for status in _NEEDS_RETRY:
        wl = make_machine_workload(last_backup_at=_RECENT, status=status)
        assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is True, (
            f"Expected stale=True for status {status!r} with recent backup"
        )


def test_is_stale_never_backed_up_only_true_skips_retry_and_overdue() -> None:
    # never_backed_up_only=True: only last_backup_at is None returns True.
    # A workload with a recent backup and FAILED status is not stale.
    wl = make_machine_workload(last_backup_at=_RECENT, status=WorkloadStatus.FAILED)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=True) is False


def test_is_stale_never_backed_up_only_true_old_backup_success_not_stale() -> None:
    # Overdue (old backup, healthy status) is not counted when never_backed_up_only=True.
    wl = make_machine_workload(last_backup_at=_OLD, status=WorkloadStatus.SUCCESS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=True) is False


def test_is_stale_never_backed_up_only_true_none_backup_is_stale() -> None:
    wl = make_machine_workload(last_backup_at=None, status=WorkloadStatus.NO_BACKUPS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=True) is True


@pytest.mark.parametrize("status", list(_NEEDS_RETRY))
def test_is_stale_never_backed_up_only_true_needs_retry_with_backup_not_stale(
    status: WorkloadStatus,
) -> None:
    wl = make_machine_workload(last_backup_at=_RECENT, status=status)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=True) is False


def test_is_stale_backup_exactly_at_cutoff_not_stale() -> None:
    # Boundary: last_backup_at == cutoff is not overdue (< cutoff required).
    wl = make_machine_workload(last_backup_at=_CUTOFF, status=WorkloadStatus.SUCCESS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is False


def test_is_stale_backup_one_microsecond_before_cutoff_is_stale() -> None:
    just_before = _CUTOFF - timedelta(microseconds=1)
    wl = make_machine_workload(last_backup_at=just_before, status=WorkloadStatus.SUCCESS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is True


def test_is_stale_m365_workload_never_backed_up() -> None:
    wl = make_m365_workload(last_backup_at=None, status=WorkloadStatus.NO_BACKUPS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is True


def test_is_stale_m365_workload_recent_backup_success() -> None:
    wl = make_m365_workload(last_backup_at=_RECENT, status=WorkloadStatus.SUCCESS)
    assert _is_stale(wl, _CUTOFF, never_backed_up_only=False) is False


# ── _poll_one ─────────────────────────────────────────────────────────────────


async def test_poll_one_returns_first_terminal_status() -> None:
    apm = make_fake_apm()
    wl = make_machine_workload()
    acts = [
        make_backup_activity(status=BackupActivityStatus.BACKING_UP),
        make_backup_activity(status=BackupActivityStatus.FAILED),
        make_backup_activity(status=BackupActivityStatus.SUCCESS),
    ]
    apm.activities.backup.list.return_value = (acts, 3)
    triggered_at = datetime.now(UTC)

    status = await _poll_one(apm, wl, triggered_at, asyncio.Semaphore(1))

    assert status is BackupActivityStatus.FAILED  # first terminal activity in list order
    assert apm.activities.backup.list.call_args.kwargs == {
        "workload": wl,
        "since": triggered_at,
        "history": True,
    }


async def test_poll_one_returns_none_when_only_non_terminal_activities() -> None:
    apm = make_fake_apm()
    acts = [
        make_backup_activity(status=BackupActivityStatus.QUEUING),
        make_backup_activity(status=BackupActivityStatus.BACKING_UP),
    ]
    apm.activities.backup.list.return_value = (acts, 2)

    status = await _poll_one(
        apm, make_machine_workload(), datetime.now(UTC), asyncio.Semaphore(1)
    )

    assert status is None


async def test_poll_one_returns_none_when_no_activities() -> None:
    apm = make_fake_apm()

    status = await _poll_one(
        apm, make_machine_workload(), datetime.now(UTC), asyncio.Semaphore(1)
    )

    assert status is None


# ── _poll_all ─────────────────────────────────────────────────────────────────


async def test_poll_all_zero_timeout_marks_all_timed_out() -> None:
    apm = make_fake_apm()
    wl = make_machine_workload(name="vm-web-01")

    results = await _poll_all(
        apm, [(wl, datetime.now(UTC))], 0, asyncio.Event(), asyncio.Semaphore(1)
    )

    assert results == {"vm-web-01": None}
    apm.activities.backup.list.assert_not_called()


async def test_poll_all_preset_interrupt_skips_polling() -> None:
    apm = make_fake_apm()
    wl = make_machine_workload(name="vm-web-01")
    interrupt = asyncio.Event()
    interrupt.set()

    results = await _poll_all(
        apm, [(wl, datetime.now(UTC))], 3600, interrupt, asyncio.Semaphore(1)
    )

    assert results == {"vm-web-01": None}
    apm.activities.backup.list.assert_not_called()


async def test_poll_all_interrupt_during_sleep_marks_pending_timed_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(backup_catchup, "interruptible_sleep", AsyncMock(return_value=True))
    apm = make_fake_apm()
    wl = make_machine_workload(name="vm-web-01")

    results = await _poll_all(
        apm, [(wl, datetime.now(UTC))], 3600, asyncio.Event(), asyncio.Semaphore(1)
    )

    assert results == {"vm-web-01": None}
    apm.activities.backup.list.assert_not_called()  # break happens before the poll round


async def test_poll_all_removes_completed_and_repolls_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(backup_catchup, "interruptible_sleep", AsyncMock(return_value=False))
    apm = make_fake_apm()
    wl_a = make_machine_workload(name="vm-web-01")
    wl_b = make_machine_workload(name="vm-app-01")
    polled_names: list[str] = []

    def _list(**kwargs: Any) -> tuple[list[Any], int]:
        name = kwargs["workload"].name
        polled_names.append(name)
        if name == "vm-web-01":
            return ([make_backup_activity(status=BackupActivityStatus.SUCCESS)], 1)
        # vm-app-01 is still running on the first round, then fails.
        if polled_names.count("vm-app-01") == 1:
            return ([], 0)
        return ([make_backup_activity(status=BackupActivityStatus.FAILED)], 1)

    apm.activities.backup.list.side_effect = _list
    triggered_at = datetime.now(UTC)

    results = await _poll_all(
        apm,
        [(wl_a, triggered_at), (wl_b, triggered_at)],
        3600,
        asyncio.Event(),
        asyncio.Semaphore(2),
    )

    assert results == {
        "vm-web-01": BackupActivityStatus.SUCCESS,
        "vm-app-01": BackupActivityStatus.FAILED,
    }
    # Completed workloads leave the pending set and are not polled again.
    assert polled_names.count("vm-web-01") == 1
    assert polled_names.count("vm-app-01") == 2


# ── run() ─────────────────────────────────────────────────────────────────────


def _make_stale_machine_workload(name: str = "CORP-PC-001") -> Any:
    return make_machine_workload(name=name, last_backup_at=None, status=WorkloadStatus.NO_BACKUPS)


def _patch_no_poll_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real poll-interval sleep inside _poll_all."""
    monkeypatch.setattr(backup_catchup, "interruptible_sleep", AsyncMock(return_value=False))


async def test_run_no_stale_workloads_returns_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    fresh = make_machine_workload(last_backup_at=_RECENT, status=WorkloadStatus.SUCCESS)
    apm.machine.workloads.list.return_value = ([fresh], 1)
    patch_make_client(monkeypatch, backup_catchup, apm)

    rc = await run(1, False, True, 0, False, "machine", None, "table")

    assert rc == 0
    assert "No workloads" in capsys.readouterr().err


async def test_run_dry_run_lists_candidates_without_triggering(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    stale = _make_stale_machine_workload()
    apm.machine.workloads.list.return_value = ([stale], 1)
    apm.machine.workloads.backup_now = AsyncMock()
    patch_make_client(monkeypatch, backup_catchup, apm)

    rc = await run(1, True, False, 0, False, "machine", None, "table")

    assert rc == 0
    err = capsys.readouterr().err
    assert "[dry-run] No backups triggered." in err
    candidate_line = next(ln for ln in err.splitlines() if "CORP-PC-001" in ln)
    assert "never backed up" in candidate_line
    apm.machine.workloads.backup_now.assert_not_called()


async def test_run_declined_confirmation_cancels_without_triggering(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([_make_stale_machine_workload()], 1)
    apm.machine.workloads.backup_now = AsyncMock()
    patch_make_client(monkeypatch, backup_catchup, apm)
    monkeypatch.setattr(backup_catchup, "prompt_yes_no", AsyncMock(return_value=False))

    rc = await run(1, False, False, 0, False, "machine", None, "table")

    assert rc == 0
    assert "Cancelled." in capsys.readouterr().err
    apm.machine.workloads.backup_now.assert_not_called()


async def test_run_success_flow_csv_and_exit_code_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    stale = _make_stale_machine_workload()
    apm.machine.workloads.list.return_value = ([stale], 1)
    apm.machine.workloads.backup_now = AsyncMock()
    apm.activities.backup.list.return_value = (
        [make_backup_activity(status=BackupActivityStatus.SUCCESS)], 1
    )
    patch_make_client(monkeypatch, backup_catchup, apm)
    _patch_no_poll_wait(monkeypatch)

    rc = await run(1, False, True, 600, False, "machine", None, "csv")

    assert rc == 0
    apm.machine.workloads.backup_now.assert_called_once_with(stale)
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == ["name", "type", "result"]
    assert rows[1] == ["CORP-PC-001", "PC", "success"]


async def test_run_all_category_csv_includes_category_column(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    stale = _make_stale_machine_workload()
    apm.machine.workloads.list.return_value = ([stale], 1)
    apm.machine.workloads.backup_now = AsyncMock()
    apm.activities.backup.list.return_value = (
        [make_backup_activity(status=BackupActivityStatus.SUCCESS)], 1
    )
    patch_make_client(monkeypatch, backup_catchup, apm)
    _patch_no_poll_wait(monkeypatch)

    rc = await run(1, False, True, 600, False, "all", None, "csv")

    assert rc == 0
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == ["name", "category", "type", "result"]
    assert rows[1] == ["CORP-PC-001", "Machine", "PC", "success"]


async def test_run_m365_workload_dispatches_to_m365_backup_now(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    tenant = make_saas_tenant()
    stale = make_m365_workload(
        name="alice@contoso.com",
        tenant_id=tenant.tenant_id,
        last_backup_at=None,
        status=WorkloadStatus.NO_BACKUPS,
    )
    apm.saas.list.return_value = ([tenant], 1)
    apm.m365.workloads.list.return_value = ([stale], 1)
    apm.m365.workloads.backup_now = AsyncMock()
    apm.machine.workloads.backup_now = AsyncMock()
    apm.activities.backup.list.return_value = (
        [make_backup_activity(status=BackupActivityStatus.SUCCESS)], 1
    )
    patch_make_client(monkeypatch, backup_catchup, apm)
    _patch_no_poll_wait(monkeypatch)

    rc = await run(1, False, True, 600, False, "m365", [M365WorkloadType.EXCHANGE], "table")

    assert rc == 0
    apm.m365.workloads.backup_now.assert_called_once_with(stale)
    apm.machine.workloads.backup_now.assert_not_called()


async def test_run_trigger_error_does_not_abort_batch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One failed trigger is reported, the rest of the batch still runs."""
    apm = make_fake_apm()
    wl_a = _make_stale_machine_workload("vm-web-01")
    wl_b = _make_stale_machine_workload("vm-app-01")
    apm.machine.workloads.list.return_value = ([wl_a, wl_b], 2)
    apm.machine.workloads.backup_now = AsyncMock(side_effect=[APMError("boom"), None])
    patch_make_client(monkeypatch, backup_catchup, apm)

    rc = await run(1, False, True, 0, False, "machine", None, "json")

    assert rc == 1  # the surviving workload times out with timeout_sec=0
    assert apm.machine.workloads.backup_now.call_count == 2
    captured = capsys.readouterr()
    failed_line = next(
        ln for ln in captured.err.splitlines() if "vm-web-01" in ln and "[!!]" in ln
    )
    assert "boom" in failed_line
    data = json.loads(captured.out)
    assert [r["name"] for r in data["results"]] == ["vm-app-01"]
    assert data["results"][0]["result"] == "timed_out"


async def test_run_all_triggers_failed_returns_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([_make_stale_machine_workload()], 1)
    apm.machine.workloads.backup_now = AsyncMock(side_effect=APMError("boom"))
    patch_make_client(monkeypatch, backup_catchup, apm)

    rc = await run(1, False, True, 0, False, "machine", None, "table")

    assert rc == 0
    assert "All triggers failed." in capsys.readouterr().err


async def test_run_json_output_maps_timeout_to_timed_out_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([_make_stale_machine_workload()], 1)
    apm.machine.workloads.backup_now = AsyncMock()
    patch_make_client(monkeypatch, backup_catchup, apm)

    rc = await run(1, False, True, 0, False, "machine", None, "json")

    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["results"] == [
        {"name": "CORP-PC-001", "category": "Machine", "type": "PC", "result": "timed_out"},
    ]
    assert data["summary"] == {"success": 0, "failed": 0, "timed_out": 1}


async def test_run_failed_backup_table_output_and_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([_make_stale_machine_workload()], 1)
    apm.machine.workloads.backup_now = AsyncMock()
    apm.activities.backup.list.return_value = (
        [make_backup_activity(status=BackupActivityStatus.FAILED)], 1
    )
    patch_make_client(monkeypatch, backup_catchup, apm)
    _patch_no_poll_wait(monkeypatch)

    rc = await run(1, False, True, 600, False, "machine", None, "table")

    assert rc == 1
    out_lines = capsys.readouterr().out.splitlines()
    result_line = next(ln for ln in out_lines if "CORP-PC-001" in ln)
    assert "failed (failed)" in result_line
    summary_line = next(ln for ln in out_lines if "Success:" in ln)
    assert "Failed: 1" in summary_line
    assert "Timed out: 0" in summary_line


# ── main(): argparse wiring ───────────────────────────────────────────────────


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    run_mock = MagicMock(name="run", return_value=None)
    run_main_mock = MagicMock(name="run_main")
    monkeypatch.setattr(backup_catchup, "run", run_mock)
    monkeypatch.setattr(backup_catchup, "run_main", run_main_mock)
    return run_mock, run_main_mock


def test_main_parses_flags_and_wires_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, run_main_mock = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "backup_catchup.py",
        "--category", "machine", "--max-age", "3", "--dry-run", "-y",
        "--timeout", "600", "--never-backed-up", "-o", "json",
    ])

    backup_catchup.main()

    run_mock.assert_called_once_with(3, True, True, 600, True, "machine", None, "json")
    run_main_mock.assert_called_once_with(run_mock.return_value)


def test_main_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, _ = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["backup_catchup.py", "--category", "machine"])

    backup_catchup.main()

    run_mock.assert_called_once_with(1, False, False, 1800, False, "machine", None, "table")
