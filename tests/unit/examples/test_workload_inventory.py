"""Unit tests for workload_inventory.py: pure functions plus run()/main() behavior."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
import workload_inventory
from workload_inventory import _build_inventory, _get_version_count, _print_table, run

from synology_apm.sdk import M365WorkloadType, MachineWorkloadType, WorkloadStatus
from tests.unit.examples._fixtures import (
    make_fake_apm,
    make_location_info,
    make_m365_workload,
    make_machine_workload,
    make_protection_plan,
    make_saas_tenant,
    patch_make_client,
)

# ── helpers ──────────────────────────────────────────────────────────────────

_TENANT_ID = "123e4567-e89b-12d3-a456-426614174000"
_TENANT_NAME = "Contoso"
_TENANT_NAMES: dict[str, str] = {_TENANT_ID: _TENANT_NAME}

_NO_TENANT_NAMES: dict[str, str] = {}


# ── _build_inventory: category="all" ─────────────────────────────────────────


def test_build_inventory_all_includes_category_and_tenant_columns() -> None:
    machine_wl = make_machine_workload()
    headers, _ = _build_inventory([machine_wl], [None], _TENANT_NAMES, "all", False)
    assert "category" in headers
    assert "tenant" in headers


def test_build_inventory_all_machine_row_has_empty_tenant() -> None:
    machine_wl = make_machine_workload(name="CORP-PC-001")
    headers, rows = _build_inventory([machine_wl], [None], _TENANT_NAMES, "all", False)
    tenant_idx = headers.index("tenant")
    assert rows[0][tenant_idx] == ""


def test_build_inventory_all_m365_row_uses_tenant_name_from_map() -> None:
    m365_wl = make_m365_workload(tenant_id=_TENANT_ID)
    headers, rows = _build_inventory([m365_wl], [None], _TENANT_NAMES, "all", False)
    tenant_idx = headers.index("tenant")
    assert rows[0][tenant_idx] == _TENANT_NAME


def test_build_inventory_all_m365_row_falls_back_to_tenant_id_when_not_in_map() -> None:
    m365_wl = make_m365_workload(tenant_id=_TENANT_ID)
    headers, rows = _build_inventory([m365_wl], [None], _NO_TENANT_NAMES, "all", False)
    tenant_idx = headers.index("tenant")
    assert rows[0][tenant_idx] == _TENANT_ID


def test_build_inventory_all_machine_row_has_category_label_machine() -> None:
    machine_wl = make_machine_workload()
    headers, rows = _build_inventory([machine_wl], [None], _TENANT_NAMES, "all", False)
    cat_idx = headers.index("category")
    assert rows[0][cat_idx] == "Machine"


def test_build_inventory_all_m365_row_has_category_label_m365() -> None:
    m365_wl = make_m365_workload(tenant_id=_TENANT_ID)
    headers, rows = _build_inventory([m365_wl], [None], _TENANT_NAMES, "all", False)
    cat_idx = headers.index("category")
    assert rows[0][cat_idx] == "M365"


# ── _build_inventory: category="machine" ─────────────────────────────────────


def test_build_inventory_machine_omits_category_and_tenant_columns() -> None:
    machine_wl = make_machine_workload()
    headers, _ = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    assert "category" not in headers
    assert "tenant" not in headers


def test_build_inventory_machine_includes_core_columns() -> None:
    machine_wl = make_machine_workload()
    headers, _ = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    for col in ("name", "type", "plan_name", "backup_server", "last_backup_at", "backup_status"):
        assert col in headers


# ── _build_inventory: category="m365" ────────────────────────────────────────


def test_build_inventory_m365_includes_tenant_but_not_category() -> None:
    m365_wl = make_m365_workload(tenant_id=_TENANT_ID)
    headers, _ = _build_inventory([m365_wl], [None], _TENANT_NAMES, "m365", False)
    assert "tenant" in headers
    assert "category" not in headers


# ── _build_inventory: include_versions ───────────────────────────────────────


def test_build_inventory_include_versions_true_adds_version_count_column() -> None:
    machine_wl = make_machine_workload()
    headers, rows = _build_inventory([machine_wl], [42], _NO_TENANT_NAMES, "machine", True)
    assert "version_count" in headers
    vc_idx = headers.index("version_count")
    assert rows[0][vc_idx] == 42


def test_build_inventory_include_versions_false_omits_version_count_column() -> None:
    machine_wl = make_machine_workload()
    headers, _ = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    assert "version_count" not in headers


def test_build_inventory_include_versions_none_count_stored_as_none() -> None:
    machine_wl = make_machine_workload()
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", True)
    vc_idx = headers.index("version_count")
    assert rows[0][vc_idx] is None


# ── _build_inventory: row field values ───────────────────────────────────────


def test_build_inventory_machine_row_name() -> None:
    machine_wl = make_machine_workload(name="CORP-PC-001")
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    name_idx = headers.index("name")
    assert rows[0][name_idx] == "CORP-PC-001"


def test_build_inventory_machine_row_type_is_uppercase_enum_value() -> None:
    machine_wl = make_machine_workload(workload_type=MachineWorkloadType.PC)
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    type_idx = headers.index("type")
    assert rows[0][type_idx] == "PC"


def test_build_inventory_machine_row_type_vm_is_uppercase() -> None:
    machine_wl = make_machine_workload(workload_type=MachineWorkloadType.VM)
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    type_idx = headers.index("type")
    assert rows[0][type_idx] == "VM"


def test_build_inventory_m365_row_type_is_display_label() -> None:
    m365_wl = make_m365_workload(workload_type=M365WorkloadType.EXCHANGE, tenant_id=_TENANT_ID)
    headers, rows = _build_inventory([m365_wl], [None], _TENANT_NAMES, "m365", False)
    type_idx = headers.index("type")
    assert rows[0][type_idx] == "Exchange"


def test_build_inventory_machine_row_plan_name() -> None:
    plan = make_protection_plan(name="Daily Backup")
    machine_wl = make_machine_workload(plan=plan)
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    plan_idx = headers.index("plan_name")
    assert rows[0][plan_idx] == "Daily Backup"


def test_build_inventory_machine_row_backup_server_name() -> None:
    server = make_location_info(name="apm-server-01")
    machine_wl = make_machine_workload(backup_server=server)
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    server_idx = headers.index("backup_server")
    assert rows[0][server_idx] == "apm-server-01"


def test_build_inventory_machine_row_backup_server_empty_when_none() -> None:
    machine_wl = make_machine_workload(backup_server=None)
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    server_idx = headers.index("backup_server")
    assert rows[0][server_idx] == ""


def test_build_inventory_machine_row_backup_status_is_enum_value() -> None:
    machine_wl = make_machine_workload(status=WorkloadStatus.SUCCESS)
    headers, rows = _build_inventory([machine_wl], [None], _NO_TENANT_NAMES, "machine", False)
    status_idx = headers.index("backup_status")
    assert rows[0][status_idx] == WorkloadStatus.SUCCESS.value


# ── _build_inventory: mixed workloads in "all" ───────────────────────────────


def test_build_inventory_all_mixed_workloads_produce_two_rows() -> None:
    machine_wl = make_machine_workload(name="CORP-PC-001")
    m365_wl = make_m365_workload(name="alice@contoso.com", tenant_id=_TENANT_ID)
    headers, rows = _build_inventory(
        [machine_wl, m365_wl], [None, None], _TENANT_NAMES, "all", False
    )
    assert len(rows) == 2
    name_idx = headers.index("name")
    assert rows[0][name_idx] == "CORP-PC-001"
    assert rows[1][name_idx] == "alice@contoso.com"


def test_build_inventory_all_version_counts_assigned_per_workload() -> None:
    machine_wl = make_machine_workload()
    m365_wl = make_m365_workload(tenant_id=_TENANT_ID)
    headers, rows = _build_inventory(
        [machine_wl, m365_wl], [7, 3], _TENANT_NAMES, "all", True
    )
    vc_idx = headers.index("version_count")
    assert rows[0][vc_idx] == 7
    assert rows[1][vc_idx] == 3


# ── _build_inventory: empty workloads list ────────────────────────────────────


def test_build_inventory_empty_workloads_returns_empty_rows() -> None:
    headers, rows = _build_inventory([], [], _NO_TENANT_NAMES, "machine", False)
    assert rows == []
    assert "name" in headers


# ── _print_table ─────────────────────────────────────────────────────────────


def test_print_table_pairs_headers_and_row_values_on_their_own_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    headers = ["name", "backup_status"]
    rows: list[list[str | int | None]] = [["vm-web-01", "success"]]
    _print_table(headers, rows)
    lines = capsys.readouterr().out.splitlines()
    header_line = lines[0]
    assert "name" in header_line
    assert "backup_status" in header_line
    data_lines = [ln for ln in lines if "vm-web-01" in ln]
    assert data_lines, "expected a line containing the workload name"
    assert "success" in data_lines[0]


# ── _get_version_count ────────────────────────────────────────────────────────


async def test_get_version_count_machine_workload_uses_machine_collection() -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list_versions = AsyncMock(return_value=([], 5))
    apm.m365.workloads.list_versions = AsyncMock(return_value=([], 0))
    wl = make_machine_workload()

    count = await _get_version_count(apm, wl, asyncio.Semaphore(1))

    assert count == 5
    apm.machine.workloads.list_versions.assert_called_once_with(wl, limit=1)
    apm.m365.workloads.list_versions.assert_not_called()


async def test_get_version_count_m365_workload_uses_m365_collection() -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list_versions = AsyncMock(return_value=([], 0))
    apm.m365.workloads.list_versions = AsyncMock(return_value=([], 3))
    wl = make_m365_workload()

    count = await _get_version_count(apm, wl, asyncio.Semaphore(1))

    assert count == 3
    apm.m365.workloads.list_versions.assert_called_once_with(wl, limit=1)
    apm.machine.workloads.list_versions.assert_not_called()


# ── run() ─────────────────────────────────────────────────────────────────────


async def test_run_csv_machine_with_versions_emits_exact_header_and_row(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CSV columns match the machine-category layout; None dates render empty."""
    apm = make_fake_apm()
    wl = make_machine_workload(
        name="CORP-PC-001",
        last_backup_at=None,
        backup_server=make_location_info(name="apm-server-01"),
    )
    apm.machine.workloads.list.return_value = ([wl], 1)
    apm.machine.workloads.list_versions = AsyncMock(return_value=([], 7))
    patch_make_client(monkeypatch, workload_inventory, apm)

    await run(
        retired_only=False,
        include_versions=True,
        concurrency=2,
        category="machine",
        m365_services=None,
        output_format="csv",
    )

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == [
        "name", "type", "plan_name", "backup_server",
        "last_backup_at", "backup_status", "version_count",
    ]
    assert rows[1] == ["CORP-PC-001", "PC", "Daily Backup", "apm-server-01", "", "success", "7"]


async def test_run_no_versions_skips_lookup_and_omits_column(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([make_machine_workload()], 1)
    apm.machine.workloads.list_versions = AsyncMock()
    patch_make_client(monkeypatch, workload_inventory, apm)

    await run(
        retired_only=False,
        include_versions=False,
        concurrency=2,
        category="machine",
        m365_services=None,
        output_format="csv",
    )

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert "version_count" not in rows[0]
    apm.machine.workloads.list_versions.assert_not_called()


async def test_run_retired_only_forwards_is_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, workload_inventory, apm)

    await run(
        retired_only=True,
        include_versions=False,
        concurrency=2,
        category="machine",
        m365_services=None,
        output_format="csv",
    )

    assert apm.machine.workloads.list.call_args.kwargs["is_retired"] is True


async def test_run_json_m365_uses_tenant_display_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    tenant = make_saas_tenant()
    wl = make_m365_workload(
        name="alice@contoso.com",
        tenant_id=tenant.tenant_id,
        workload_type=M365WorkloadType.EXCHANGE,
    )
    apm.saas.list.return_value = ([tenant], 1)
    apm.m365.workloads.list.return_value = ([wl], 1)
    apm.m365.workloads.list_versions = AsyncMock(return_value=([], 2))
    patch_make_client(monkeypatch, workload_inventory, apm)

    await run(
        retired_only=False,
        include_versions=True,
        concurrency=2,
        category="m365",
        m365_services=[M365WorkloadType.EXCHANGE],
        output_format="json",
    )

    data = json.loads(capsys.readouterr().out)
    assert data[0]["name"] == "alice@contoso.com"
    assert data[0]["tenant"] == "Contoso"
    assert data[0]["type"] == "Exchange"
    assert data[0]["version_count"] == 2
    assert "category" not in data[0]


async def test_run_table_output_and_export_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    apm.machine.workloads.list.return_value = ([make_machine_workload(name="vm-web-01")], 1)
    patch_make_client(monkeypatch, workload_inventory, apm)

    await run(
        retired_only=False,
        include_versions=False,
        concurrency=2,
        category="machine",
        m365_services=None,
        output_format="table",
    )

    captured = capsys.readouterr()
    data_line = next(ln for ln in captured.out.splitlines() if "vm-web-01" in ln)
    assert "success" in data_line
    assert "[1 workloads exported]" in captured.err


# ── main(): argparse wiring ───────────────────────────────────────────────────


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    run_mock = MagicMock(name="run", return_value=None)
    run_main_mock = MagicMock(name="run_main")
    monkeypatch.setattr(workload_inventory, "run", run_mock)
    monkeypatch.setattr(workload_inventory, "run_main", run_main_mock)
    return run_mock, run_main_mock


def test_main_parses_flags_and_wires_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, run_main_mock = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "workload_inventory.py",
        "--category", "machine", "--retired", "--no-versions",
        "--concurrency", "5", "-o", "csv",
    ])

    workload_inventory.main()

    run_mock.assert_called_once_with(
        retired_only=True,
        include_versions=False,
        concurrency=5,
        category="machine",
        m365_services=None,
        output_format="csv",
    )
    run_main_mock.assert_called_once_with(run_mock.return_value)


def test_main_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, _ = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["workload_inventory.py", "--category", "all"])

    workload_inventory.main()

    run_mock.assert_called_once_with(
        retired_only=False,
        include_versions=True,
        concurrency=10,
        category="all",
        m365_services=None,
        output_format="table",
    )
