"""Unit tests for backup_activity_report.py: pure functions plus run()/main() behavior."""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import backup_activity_report
import pytest
from backup_activity_report import (
    _FAILED,
    _ONGOING,
    _SUCCESS,
    _merge_activities,
    _tally,
    run,
)

from synology_apm.sdk import BackupActivityStatus, M365WorkloadType, MachineWorkloadType
from tests.unit.examples._fixtures import (
    make_backup_activity,
    make_fake_apm,
    make_machine_workload,
    patch_make_client,
)

# ── Partition contract ─────────────────────────────────────────────────────────


def test_status_sets_are_pairwise_disjoint() -> None:
    """_SUCCESS, _FAILED, and _ONGOING share no member."""
    assert _SUCCESS & _FAILED == set()
    assert _SUCCESS & _ONGOING == set()
    assert _FAILED & _ONGOING == set()


def test_status_sets_cover_all_statuses() -> None:
    """Union of _SUCCESS, _FAILED, and _ONGOING equals the full BackupActivityStatus enum."""
    assert _SUCCESS | _FAILED | _ONGOING == set(BackupActivityStatus)


# ── _tally: status routing ─────────────────────────────────────────────────────


@pytest.mark.parametrize("status", list(BackupActivityStatus))
def test_tally_routes_every_status_to_correct_bucket(status: BackupActivityStatus) -> None:
    """Success maps to 'success'; every other status keeps its own status string."""
    wl = make_machine_workload()
    act = make_backup_activity(status=status, workload_id=wl.workload_id)
    row: dict[str, Any] = _tally(wl, act)

    if status in _SUCCESS:
        assert row["result"] == "success"
    else:  # _FAILED and _ONGOING both surface the raw status value
        assert row["result"] == status.value


def test_tally_base_fields_copied_from_workload() -> None:
    """Every row carries the workload's identity, labels, and last-backup timestamp."""
    last_backup = datetime(2026, 5, 6, 22, 30, 0, tzinfo=UTC)
    wl = make_machine_workload(
        workload_id="123e4567-e89b-12d3-a456-426614174001",
        name="vm-web-01",
        workload_type=MachineWorkloadType.VM,
        last_backup_at=last_backup,
    )
    row: dict[str, Any] = _tally(wl, None)

    assert row["workload_id"] == "123e4567-e89b-12d3-a456-426614174001"
    assert row["name"] == "vm-web-01"
    assert row["category"] == "Machine"
    assert row["type"] == "VM"
    assert row["last_backup_at"] == last_backup


# ── _tally: edge cases ────────────────────────────────────────────────────────


def test_tally_none_activity_returns_no_activity_bucket() -> None:
    """When wl_act is None the result is 'no_activity' and every metric field is None."""
    wl = make_machine_workload()
    row: dict[str, Any] = _tally(wl, None)

    assert row["result"] == "no_activity"
    assert row["duration_seconds"] is None
    assert row["transferred_bytes"] is None
    assert row["progress"] is None


def test_tally_success_with_none_data_transferred_yields_zero() -> None:
    """For a successful activity whose data_transferred_bytes is None, transferred_bytes is 0."""
    wl = make_machine_workload()
    act = make_backup_activity(
        status=BackupActivityStatus.SUCCESS,
        workload_id=wl.workload_id,
        data_transferred_bytes=None,
    )
    row: dict[str, Any] = _tally(wl, act)

    assert row["result"] == "success"
    assert row["transferred_bytes"] == 0


def test_tally_failed_result_preserves_status_value_as_string() -> None:
    """For a failed activity, the result field holds the raw status string, not a generic label."""
    wl = make_machine_workload()
    act = make_backup_activity(
        status=BackupActivityStatus.FAILED,
        workload_id=wl.workload_id,
    )
    row: dict[str, Any] = _tally(wl, act)

    assert row["result"] == BackupActivityStatus.FAILED.value


# ── _merge_activities ─────────────────────────────────────────────────────────

_WL_A = "123e4567-e89b-12d3-a456-426614174001"
_WL_B = "123e4567-e89b-12d3-a456-426614174002"


def test_merge_activities_duplicate_completed_first_entry_wins() -> None:
    """For duplicate workload_ids in completed, setdefault retains the first (most-recent) entry."""
    act_first = make_backup_activity(
        workload_id=_WL_A,
        activity_id="123e4567-e89b-12d3-a456-426614174011",
        status=BackupActivityStatus.SUCCESS,
    )
    act_second = make_backup_activity(
        workload_id=_WL_A,
        activity_id="123e4567-e89b-12d3-a456-426614174012",
        status=BackupActivityStatus.FAILED,
    )

    result = _merge_activities([act_first, act_second], [])

    assert result[_WL_A] is act_first


def test_merge_activities_ongoing_overrides_completed() -> None:
    """When a workload appears in both completed and ongoing, the ongoing activity wins."""
    completed = make_backup_activity(
        workload_id=_WL_A,
        status=BackupActivityStatus.SUCCESS,
    )
    ongoing = make_backup_activity(
        workload_id=_WL_A,
        status=BackupActivityStatus.BACKING_UP,
    )

    result = _merge_activities([completed], [ongoing])

    assert result[_WL_A] is ongoing


def test_merge_activities_disjoint_sets_produce_union() -> None:
    """When completed and ongoing cover different workloads, all appear in the merged result."""
    act_completed = make_backup_activity(
        workload_id=_WL_A,
        status=BackupActivityStatus.SUCCESS,
    )
    act_ongoing = make_backup_activity(
        workload_id=_WL_B,
        status=BackupActivityStatus.BACKING_UP,
    )

    result = _merge_activities([act_completed], [act_ongoing])

    assert len(result) == 2
    assert result[_WL_A] is act_completed
    assert result[_WL_B] is act_ongoing


# ── run(): output formats ─────────────────────────────────────────────────────

_WL_C = "123e4567-e89b-12d3-a456-426614174003"
_WL_D = "123e4567-e89b-12d3-a456-426614174004"

_REPORT_DATE = date(2026, 5, 7)


def _wire_activities(
    apm: MagicMock,
    completed: list[Any],
    ongoing: list[Any],
) -> None:
    """Answer history=True with *completed* and history=False with *ongoing*."""

    def _list(**kwargs: Any) -> tuple[list[Any], int]:
        items = completed if kwargs["history"] else ongoing
        return (items if kwargs["offset"] == 0 else [], len(items))

    apm.activities.backup.list.side_effect = _list


async def test_run_csv_machine_category_emits_exact_header_and_row(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CSV output has the machine-category field order; None cells render empty."""
    apm = make_fake_apm()
    wl = make_machine_workload(workload_id=_WL_A, name="CORP-PC-001", last_backup_at=None)
    apm.machine.workloads.list.return_value = ([wl], 1)
    act = make_backup_activity(
        workload_id=_WL_A,
        status=BackupActivityStatus.SUCCESS,
        duration_seconds=300,
        data_transferred_bytes=2048,
    )
    _wire_activities(apm, [act], [])
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "machine", None, "csv")

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == [
        "workload_id", "name", "type", "result",
        "duration_seconds", "transferred_bytes", "last_backup_at",
    ]
    assert rows[1] == [_WL_A, "CORP-PC-001", "PC", "success", "300", "2048", ""]


async def test_run_csv_all_category_adds_category_column(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    wl = make_machine_workload(workload_id=_WL_A, name="CORP-PC-001", last_backup_at=None)
    apm.machine.workloads.list.return_value = ([wl], 1)
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "all", None, "csv")

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0][:3] == ["workload_id", "name", "category"]
    assert rows[1][1:4] == ["CORP-PC-001", "Machine", "PC"]


async def test_run_csv_serializes_last_backup_at_as_local_isoformat(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    last_backup = datetime(2026, 5, 6, 22, 30, 0, tzinfo=UTC)
    wl = make_machine_workload(workload_id=_WL_A, last_backup_at=last_backup)
    apm.machine.workloads.list.return_value = ([wl], 1)
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "machine", None, "csv")

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[1][-1] == last_backup.astimezone().isoformat()


async def test_run_json_buckets_and_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each workload lands in exactly one of the four JSON buckets."""
    apm = make_fake_apm()
    wl_ok = make_machine_workload(workload_id=_WL_A, name="vm-web-01")
    wl_fail = make_machine_workload(workload_id=_WL_B, name="vm-app-01")
    wl_prog = make_machine_workload(workload_id=_WL_C, name="vm-db-01")
    wl_idle = make_machine_workload(workload_id=_WL_D, name="CORP-PC-001")
    apm.machine.workloads.list.return_value = ([wl_ok, wl_fail, wl_prog, wl_idle], 4)
    completed = [
        make_backup_activity(
            workload_id=_WL_A,
            status=BackupActivityStatus.SUCCESS,
            duration_seconds=60,
            data_transferred_bytes=1024,
        ),
        make_backup_activity(workload_id=_WL_B, status=BackupActivityStatus.FAILED),
    ]
    ongoing = [make_backup_activity(workload_id=_WL_C, status=BackupActivityStatus.BACKING_UP)]
    _wire_activities(apm, completed, ongoing)
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "machine", None, "json")

    data = json.loads(capsys.readouterr().out)
    assert data["date"] == "2026-05-07"
    assert data["total_workloads"] == 4
    assert [r["workload_id"] for r in data["successes"]] == [_WL_A]
    assert data["successes"][0]["transferred_bytes"] == 1024
    assert data["successes"][0]["duration_seconds"] == 60
    assert [r["result"] for r in data["failures"]] == ["failed"]
    assert [r["workload_id"] for r in data["in_progress"]] == [_WL_C]
    assert data["in_progress"][0]["result"] == "backing_up"
    assert [r["workload_id"] for r in data["no_activity"]] == [_WL_D]


async def test_run_table_pairs_name_with_status_and_counts_sections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    wl_fail = make_machine_workload(workload_id=_WL_B, name="vm-app-01")
    apm.machine.workloads.list.return_value = ([wl_fail], 1)
    completed = [make_backup_activity(workload_id=_WL_B, status=BackupActivityStatus.FAILED)]
    _wire_activities(apm, completed, [])
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "machine", None, "table")

    out_lines = capsys.readouterr().out.splitlines()
    title_line = next(ln for ln in out_lines if "Backup Report:" in ln)
    assert "2026-05-07" in title_line
    assert "1 workloads total" in title_line
    summary_line = next(ln for ln in out_lines if "Success:" in ln)
    assert "Failed/Partial: 1" in summary_line
    fail_line = next(ln for ln in out_lines if "vm-app-01" in ln)
    assert "failed" in fail_line


async def test_run_table_renders_success_in_progress_and_no_activity_sections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    wl_ok = make_machine_workload(workload_id=_WL_A, name="vm-web-01")
    wl_prog = make_machine_workload(workload_id=_WL_C, name="vm-db-01")
    wl_idle = make_machine_workload(workload_id=_WL_D, name="CORP-PC-001", last_backup_at=None)
    apm.machine.workloads.list.return_value = ([wl_ok, wl_prog, wl_idle], 3)
    completed = [
        make_backup_activity(
            workload_id=_WL_A,
            status=BackupActivityStatus.SUCCESS,
            duration_seconds=60,
            data_transferred_bytes=1024,
        ),
    ]
    ongoing = [
        make_backup_activity(
            workload_id=_WL_C, status=BackupActivityStatus.BACKING_UP, progress=42
        ),
    ]
    _wire_activities(apm, completed, ongoing)
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "machine", None, "table")

    out_lines = capsys.readouterr().out.splitlines()
    progress_line = next(ln for ln in out_lines if "vm-db-01" in ln)
    assert "backing_up" in progress_line
    assert "42%" in progress_line
    idle_line = next(ln for ln in out_lines if "CORP-PC-001" in ln)
    assert "never" in idle_line
    success_line = next(ln for ln in out_lines if "vm-web-01" in ln)
    assert "00:01:00" in success_line
    assert "1.0 KB" in success_line


# ── run(): fetch contract ─────────────────────────────────────────────────────


async def test_run_fetches_completed_then_ongoing_with_day_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "machine", None, "csv")

    calls = apm.activities.backup.list.call_args_list
    assert [c.kwargs["history"] for c in calls] == [True, False]
    day_start = datetime(2026, 5, 7).astimezone()
    for call in calls:
        assert call.kwargs["since"] == day_start
        assert call.kwargs["until"] == day_start + timedelta(days=1)


async def test_run_machine_category_queries_machine_types_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "machine", None, "csv")

    kwargs = apm.activities.backup.list.call_args_list[0].kwargs
    assert kwargs["machine_types"] == [
        MachineWorkloadType.PC, MachineWorkloadType.PS,
        MachineWorkloadType.VM, MachineWorkloadType.FS,
    ]
    assert kwargs["m365_types"] is None


async def test_run_m365_category_queries_selected_m365_types_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "m365", [M365WorkloadType.EXCHANGE], "csv")

    kwargs = apm.activities.backup.list.call_args_list[0].kwargs
    assert kwargs["machine_types"] is None
    assert kwargs["m365_types"] == [M365WorkloadType.EXCHANGE]


async def test_run_m365_none_services_queries_all_m365_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, False, "m365", None, "csv")

    kwargs = apm.activities.backup.list.call_args_list[0].kwargs
    assert kwargs["m365_types"] == list(M365WorkloadType)


async def test_run_retired_only_forwards_is_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, backup_activity_report, apm)

    await run(_REPORT_DATE, True, "machine", None, "csv")

    assert apm.machine.workloads.list.call_args.kwargs["is_retired"] is True


# ── main(): argparse wiring ───────────────────────────────────────────────────


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    run_mock = MagicMock(name="run", return_value=None)
    run_main_mock = MagicMock(name="run_main")
    monkeypatch.setattr(backup_activity_report, "run", run_mock)
    monkeypatch.setattr(backup_activity_report, "run_main", run_main_mock)
    return run_mock, run_main_mock


def test_main_parses_flags_and_wires_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, run_main_mock = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "backup_activity_report.py",
        "--date", "2026-05-07", "--retired", "--category", "machine", "-o", "csv",
    ])

    backup_activity_report.main()

    run_mock.assert_called_once_with(date(2026, 5, 7), True, "machine", None, "csv")
    run_main_mock.assert_called_once_with(run_mock.return_value)


def test_main_defaults_to_yesterday_all_category_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_mock, _ = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["backup_activity_report.py"])

    backup_activity_report.main()

    assert run_mock.call_args.args == (
        date.today() - timedelta(days=1), False, "all", None, "table",
    )


def test_main_m365_services_convert_to_enum_list(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, _ = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "backup_activity_report.py",
        "--category", "m365", "--m365-service", "exchange", "--m365-service", "onedrive",
    ])

    backup_activity_report.main()

    assert run_mock.call_args.args[3] == [M365WorkloadType.EXCHANGE, M365WorkloadType.ONEDRIVE]
