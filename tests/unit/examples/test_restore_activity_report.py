"""Unit tests for restore_activity_report.py: pure functions plus run()/main() behavior."""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
import restore_activity_report
from restore_activity_report import (
    _FAILED,
    _FAILED_RESULTS,
    _ONGOING,
    _ONGOING_RESULTS,
    _SUCCESS,
    _build_row,
    _merge_activities,
    run,
)

from synology_apm.sdk import (
    ActivityWorkloadType,
    RestoreActivityStatus,
    RestoreType,
    WorkloadCategory,
)
from tests.unit.examples._fixtures import (
    make_fake_apm,
    make_restore_activity,
    patch_make_client,
)

_ACT_A = "123e4567-e89b-12d3-a456-426614174050"
_ACT_B = "123e4567-e89b-12d3-a456-426614174051"
_WL_A = "123e4567-e89b-12d3-a456-426614174001"

# ── Partition contract ─────────────────────────────────────────────────────────


def test_status_sets_partition_all_statuses() -> None:
    assert set(RestoreActivityStatus) == _SUCCESS | _FAILED | _ONGOING


def test_status_sets_are_pairwise_disjoint() -> None:
    assert _SUCCESS.isdisjoint(_FAILED)
    assert _SUCCESS.isdisjoint(_ONGOING)
    assert _FAILED.isdisjoint(_ONGOING)


def test_failed_results_are_the_failed_status_strings() -> None:
    assert {"failed", "partial", "canceled"} == _FAILED_RESULTS


def test_ongoing_results_are_the_ongoing_status_strings() -> None:
    assert {
        "preparing",
        "restoring",
        "canceling",
        "ready_for_migrate",
        "migrate_vm_manually",
        "migrating",
    } == _ONGOING_RESULTS


# ── _build_row — result field and key set ──────────────────────────────────────


_EXPECTED_KEYS: frozenset[str] = frozenset({
    "workload_id",
    "workload_name",
    "workload_type",
    "result",
    "operator",
    "restore_type",
    "destination",
    "started_at",
    "duration_seconds",
})


@pytest.mark.parametrize("status", list(RestoreActivityStatus))
def test_build_row_result_field_and_key_set(status: RestoreActivityStatus) -> None:
    """Success maps to 'success', all else keeps its status string; keys are fixed."""
    act = make_restore_activity(status=status)
    row: dict[str, Any] = _build_row(act)
    if status in _SUCCESS:
        assert row["result"] == "success"
    else:
        assert row["result"] == status.value
    assert set(row.keys()) == _EXPECTED_KEYS


# ── _build_row — individual field contracts ────────────────────────────────────


def test_build_row_workload_id_field() -> None:
    act = make_restore_activity(
        workload_id=_WL_A,
        status=RestoreActivityStatus.SUCCESS,
    )
    row: dict[str, Any] = _build_row(act)
    assert row["workload_id"] == _WL_A


def test_build_row_workload_name_field() -> None:
    act = make_restore_activity(
        workload_name="CORP-PC-001",
        status=RestoreActivityStatus.SUCCESS,
    )
    row: dict[str, Any] = _build_row(act)
    assert row["workload_name"] == "CORP-PC-001"


def test_build_row_workload_type_uses_enum_value() -> None:
    act = make_restore_activity(
        workload_type=ActivityWorkloadType.MACHINE_PC,
        status=RestoreActivityStatus.SUCCESS,
    )
    row: dict[str, Any] = _build_row(act)
    assert row["workload_type"] == ActivityWorkloadType.MACHINE_PC.value


def test_build_row_restore_type_uses_enum_value_when_set() -> None:
    act = make_restore_activity(
        restore_type=RestoreType.FILE_LEVEL,
        status=RestoreActivityStatus.SUCCESS,
    )
    row: dict[str, Any] = _build_row(act)
    assert row["restore_type"] == RestoreType.FILE_LEVEL.value


def test_build_row_restore_type_is_none_when_not_set() -> None:
    act = make_restore_activity(restore_type=None, status=RestoreActivityStatus.SUCCESS)
    row: dict[str, Any] = _build_row(act)
    assert row["restore_type"] is None


def test_build_row_destination_maps_to_restore_destination() -> None:
    act = make_restore_activity(
        restore_destination="vm-web-01-restored",
        status=RestoreActivityStatus.SUCCESS,
    )
    row: dict[str, Any] = _build_row(act)
    assert row["destination"] == "vm-web-01-restored"


def test_build_row_destination_is_none_when_not_set() -> None:
    act = make_restore_activity(restore_destination=None, status=RestoreActivityStatus.SUCCESS)
    row: dict[str, Any] = _build_row(act)
    assert row["destination"] is None


def test_build_row_operator_field() -> None:
    act = make_restore_activity(operator="admin", status=RestoreActivityStatus.SUCCESS)
    row: dict[str, Any] = _build_row(act)
    assert row["operator"] == "admin"


def test_build_row_operator_is_none_when_not_set() -> None:
    act = make_restore_activity(operator=None, status=RestoreActivityStatus.SUCCESS)
    row: dict[str, Any] = _build_row(act)
    assert row["operator"] is None


def test_build_row_duration_seconds_field() -> None:
    act = make_restore_activity(duration_seconds=300, status=RestoreActivityStatus.SUCCESS)
    row: dict[str, Any] = _build_row(act)
    assert row["duration_seconds"] == 300


def test_build_row_duration_seconds_is_none_when_not_set() -> None:
    act = make_restore_activity(duration_seconds=None, status=RestoreActivityStatus.SUCCESS)
    row: dict[str, Any] = _build_row(act)
    assert row["duration_seconds"] is None


def test_build_row_started_at_field() -> None:
    dt = datetime(2026, 5, 7, 8, 0, 0, tzinfo=UTC)
    act = make_restore_activity(started_at=dt, status=RestoreActivityStatus.SUCCESS)
    row: dict[str, Any] = _build_row(act)
    assert row["started_at"] == dt


# ── _merge_activities ─────────────────────────────────────────────────────────


def test_merge_activities_empty_inputs() -> None:
    result = _merge_activities([], [])
    assert result == {}


def test_merge_activities_completed_only() -> None:
    act = make_restore_activity(activity_id=_ACT_A)
    result = _merge_activities([act], [])
    assert result == {_ACT_A: act}


def test_merge_activities_ongoing_only() -> None:
    act = make_restore_activity(
        activity_id=_ACT_A,
        status=RestoreActivityStatus.RESTORING,
    )
    result = _merge_activities([], [act])
    assert result == {_ACT_A: act}


def test_merge_activities_ongoing_overrides_completed_for_same_activity_id() -> None:
    completed_act = make_restore_activity(
        activity_id=_ACT_A,
        status=RestoreActivityStatus.SUCCESS,
    )
    ongoing_act = make_restore_activity(
        activity_id=_ACT_A,
        status=RestoreActivityStatus.RESTORING,
    )
    result = _merge_activities([completed_act], [ongoing_act])
    assert result[_ACT_A] is ongoing_act


def test_merge_activities_first_completed_wins_per_activity_id() -> None:
    first = make_restore_activity(
        activity_id=_ACT_A,
        status=RestoreActivityStatus.SUCCESS,
    )
    second = make_restore_activity(
        activity_id=_ACT_A,
        status=RestoreActivityStatus.FAILED,
    )
    result = _merge_activities([first, second], [])
    assert result[_ACT_A] is first


def test_merge_activities_distinct_activity_ids_all_included() -> None:
    act1 = make_restore_activity(
        activity_id=_ACT_A,
        status=RestoreActivityStatus.SUCCESS,
    )
    act2 = make_restore_activity(
        activity_id=_ACT_B,
        status=RestoreActivityStatus.RESTORING,
    )
    result = _merge_activities([act1], [act2])
    assert result[_ACT_A] is act1
    assert result[_ACT_B] is act2


def test_merge_activities_keyed_by_activity_id_not_workload_id() -> None:
    """Two activities sharing a workload_id but with distinct activity_ids are both retained."""
    act1 = make_restore_activity(
        activity_id=_ACT_A,
        workload_id=_WL_A,
        status=RestoreActivityStatus.SUCCESS,
    )
    act2 = make_restore_activity(
        activity_id=_ACT_B,
        workload_id=_WL_A,
        status=RestoreActivityStatus.SUCCESS,
    )
    result = _merge_activities([act1, act2], [])
    assert len(result) == 2
    assert result[_ACT_A] is act1
    assert result[_ACT_B] is act2


# ── run(): output formats and category filter ─────────────────────────────────

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

    apm.activities.restore.list.side_effect = _list


async def test_run_csv_emits_exact_header_and_row(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CSV output has the fixed field order; None cells render empty."""
    apm = make_fake_apm()
    started = datetime(2026, 5, 7, 8, 0, 0, tzinfo=UTC)
    act = make_restore_activity(
        activity_id=_ACT_A,
        workload_id=_WL_A,
        workload_name="CORP-PC-001",
        status=RestoreActivityStatus.SUCCESS,
        operator="admin",
        restore_type=RestoreType.FILE_LEVEL,
        restore_destination=None,
        started_at=started,
        duration_seconds=300,
    )
    _wire_activities(apm, [act], [])
    patch_make_client(monkeypatch, restore_activity_report, apm)

    await run(_REPORT_DATE, "all", "csv")

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == [
        "workload_id", "workload_name", "workload_type",
        "result", "operator", "restore_type", "destination",
        "started_at", "duration_seconds",
    ]
    assert rows[1] == [
        _WL_A, "CORP-PC-001", "machine_pc",
        "success", "admin", "file_level", "",
        started.astimezone().isoformat(), "300",
    ]


async def test_run_json_buckets_and_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    completed = [
        make_restore_activity(
            activity_id=_ACT_A,
            workload_name="vm-web-01",
            status=RestoreActivityStatus.FAILED,
        ),
    ]
    ongoing = [
        make_restore_activity(
            activity_id=_ACT_B,
            workload_name="vm-app-01",
            status=RestoreActivityStatus.RESTORING,
        ),
    ]
    _wire_activities(apm, completed, ongoing)
    patch_make_client(monkeypatch, restore_activity_report, apm)

    await run(_REPORT_DATE, "all", "json")

    data = json.loads(capsys.readouterr().out)
    assert data["date"] == "2026-05-07"
    assert data["total"] == 2
    assert data["successes"] == []
    assert [r["workload_name"] for r in data["failures"]] == ["vm-web-01"]
    assert data["failures"][0]["result"] == "failed"
    assert [r["workload_name"] for r in data["in_progress"]] == ["vm-app-01"]
    assert data["in_progress"][0]["result"] == "restoring"


@pytest.mark.parametrize(
    "category,expected_names",
    [
        ("machine", ["CORP-PC-001"]),
        ("m365", ["alice@contoso.com"]),
        ("all", ["CORP-PC-001", "alice@contoso.com"]),
    ],
)
async def test_run_filters_activities_by_category(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    category: str,
    expected_names: list[str],
) -> None:
    apm = make_fake_apm()
    machine_act = make_restore_activity(
        activity_id=_ACT_A,
        category=WorkloadCategory.MACHINE,
        workload_name="CORP-PC-001",
    )
    m365_act = make_restore_activity(
        activity_id=_ACT_B,
        category=WorkloadCategory.M365,
        workload_type=ActivityWorkloadType.M365,
        workload_name="alice@contoso.com",
    )
    _wire_activities(apm, [machine_act, m365_act], [])
    patch_make_client(monkeypatch, restore_activity_report, apm)

    await run(_REPORT_DATE, category, "json")

    data = json.loads(capsys.readouterr().out)
    assert data["total"] == len(expected_names)
    assert [r["workload_name"] for r in data["successes"]] == expected_names


async def test_run_table_pairs_name_with_status_and_counts_sections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    act = make_restore_activity(
        activity_id=_ACT_A,
        workload_name="vm-app-01",
        status=RestoreActivityStatus.PARTIAL,
    )
    _wire_activities(apm, [act], [])
    patch_make_client(monkeypatch, restore_activity_report, apm)

    await run(_REPORT_DATE, "all", "table")

    out_lines = capsys.readouterr().out.splitlines()
    title_line = next(ln for ln in out_lines if "Restore Report:" in ln)
    assert "2026-05-07" in title_line
    assert "1 activities total" in title_line
    summary_line = next(ln for ln in out_lines if "Success:" in ln)
    assert "Failed/Partial: 1" in summary_line
    fail_line = next(ln for ln in out_lines if "vm-app-01" in ln)
    assert "partial" in fail_line


async def test_run_table_renders_success_and_in_progress_sections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    completed = [
        make_restore_activity(
            activity_id=_ACT_A,
            workload_name="vm-web-01",
            status=RestoreActivityStatus.SUCCESS,
            operator="admin",
            duration_seconds=60,
        ),
    ]
    ongoing = [
        make_restore_activity(
            activity_id=_ACT_B,
            workload_name="vm-db-01",
            status=RestoreActivityStatus.RESTORING,
        ),
    ]
    _wire_activities(apm, completed, ongoing)
    patch_make_client(monkeypatch, restore_activity_report, apm)

    await run(_REPORT_DATE, "all", "table")

    out_lines = capsys.readouterr().out.splitlines()
    progress_line = next(ln for ln in out_lines if "vm-db-01" in ln)
    assert "restoring" in progress_line
    success_line = next(ln for ln in out_lines if "vm-web-01" in ln)
    assert "admin" in success_line
    assert "00:01:00" in success_line


async def test_run_fetches_completed_then_ongoing_with_day_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, restore_activity_report, apm)

    await run(_REPORT_DATE, "all", "csv")

    calls = apm.activities.restore.list.call_args_list
    assert [c.kwargs["history"] for c in calls] == [True, False]
    day_start = datetime(2026, 5, 7).astimezone()
    for call in calls:
        assert call.kwargs["since"] == day_start
        assert call.kwargs["until"] == day_start + timedelta(days=1)


# ── main(): argparse wiring ───────────────────────────────────────────────────


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    run_mock = MagicMock(name="run", return_value=None)
    run_main_mock = MagicMock(name="run_main")
    monkeypatch.setattr(restore_activity_report, "run", run_mock)
    monkeypatch.setattr(restore_activity_report, "run_main", run_main_mock)
    return run_mock, run_main_mock


def test_main_parses_flags_and_wires_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, run_main_mock = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "restore_activity_report.py",
        "--date", "2026-05-07", "--category", "m365", "-o", "json", "--profile", "lab",
    ])

    restore_activity_report.main()

    run_mock.assert_called_once_with(date(2026, 5, 7), "m365", "json", profile="lab")
    run_main_mock.assert_called_once_with(run_mock.return_value)


def test_main_defaults_to_yesterday_all_category_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_mock, _ = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["restore_activity_report.py"])

    restore_activity_report.main()

    assert run_mock.call_args.args == (date.today() - timedelta(days=1), "all", "table")
    assert run_mock.call_args.kwargs == {"profile": None}
