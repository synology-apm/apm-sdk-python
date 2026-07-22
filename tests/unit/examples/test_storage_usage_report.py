"""Unit tests for storage_usage_report.py: pure functions plus run()/main() behavior."""
from __future__ import annotations

import csv
import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest
import storage_usage_report
from storage_usage_report import (
    _none_add,
    _print_csv,
    _print_json,
    _print_table,
    _scan_remote_storage_usage,
    _scan_server_usage,
    _scan_workload_usage,
    _srv_totals,
    _SrvRow,
    _stor_total,
    _StorRow,
    _wl_totals,
    _WlRow,
    run,
)

from synology_apm.sdk import M365WorkloadType, MachineWorkloadType
from tests.unit.examples._fixtures import (
    make_backup_server,
    make_fake_apm,
    make_m365_workload,
    make_machine_workload,
    make_remote_storage,
    make_saas_tenant,
    patch_make_client,
)

# ── _none_add ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (None, None, None),
        (None, 5, 5),
        (3, None, 3),
        (3, 5, 8),
    ],
)
def test_none_add(a: int | None, b: int | None, expected: int | None) -> None:
    assert _none_add(a, b) == expected


# ── _WlRow ────────────────────────────────────────────────────────────────────


def test_wl_row_total_bytes_sums_protected_and_retired() -> None:
    row = _WlRow(
        type_label="VM",
        protected_bytes=100,
        retired_bytes=50,
        protected_copy_bytes=0,
        retired_copy_bytes=0,
    )
    assert row.total_bytes == 150


def test_wl_row_total_copy_bytes_sums_protected_and_retired_copy() -> None:
    row = _WlRow(
        type_label="Exchange",
        protected_bytes=0,
        retired_bytes=0,
        protected_copy_bytes=200,
        retired_copy_bytes=80,
    )
    assert row.total_copy_bytes == 280


# ── _wl_totals ────────────────────────────────────────────────────────────────


def test_wl_totals_empty_list() -> None:
    assert _wl_totals([]) == (0, 0, 0, 0)


def test_wl_totals_sums_two_rows() -> None:
    row_a = _WlRow(
        type_label="VM",
        protected_bytes=100,
        retired_bytes=20,
        protected_copy_bytes=10,
        retired_copy_bytes=5,
    )
    row_b = _WlRow(
        type_label="PC",
        protected_bytes=200,
        retired_bytes=30,
        protected_copy_bytes=40,
        retired_copy_bytes=15,
    )
    assert _wl_totals([row_a, row_b]) == (300, 50, 50, 20)


# ── _srv_totals ───────────────────────────────────────────────────────────────


def test_srv_totals_empty_list() -> None:
    assert _srv_totals([]) == (None, None, None)


def test_srv_totals_all_none_fields() -> None:
    row = _SrvRow(
        name="apm-server-01",
        logical_backup_data_bytes=None,
        physical_backup_data_bytes=None,
        backup_data_reduction_bytes=None,
        backup_data_reduction_ratio=0.0,
    )
    assert _srv_totals([row]) == (None, None, None)


def test_srv_totals_mixed_none_and_int() -> None:
    row_a = _SrvRow(
        name="apm-server-01",
        logical_backup_data_bytes=1000,
        physical_backup_data_bytes=None,
        backup_data_reduction_bytes=300,
        backup_data_reduction_ratio=30.0,
    )
    row_b = _SrvRow(
        name="apm-server-02",
        logical_backup_data_bytes=None,
        physical_backup_data_bytes=500,
        backup_data_reduction_bytes=None,
        backup_data_reduction_ratio=0.0,
    )
    logical, physical, reduced = _srv_totals([row_a, row_b])
    assert logical == 1000
    assert physical == 500
    assert reduced == 300


def test_srv_totals_all_int_fields() -> None:
    row_a = _SrvRow(
        name="apm-server-01",
        logical_backup_data_bytes=1000,
        physical_backup_data_bytes=800,
        backup_data_reduction_bytes=200,
        backup_data_reduction_ratio=20.0,
    )
    row_b = _SrvRow(
        name="apm-server-02",
        logical_backup_data_bytes=500,
        physical_backup_data_bytes=400,
        backup_data_reduction_bytes=100,
        backup_data_reduction_ratio=20.0,
    )
    assert _srv_totals([row_a, row_b]) == (1500, 1200, 300)


# ── _stor_total ───────────────────────────────────────────────────────────────


def test_stor_total_empty_list() -> None:
    assert _stor_total([]) is None


def test_stor_total_all_none_used_bytes() -> None:
    rows = [
        _StorRow(name="DSM-Storage", used_bytes=None),
        _StorRow(name="tiering-remote", used_bytes=None),
    ]
    assert _stor_total(rows) is None


def test_stor_total_sums_non_none_skips_none() -> None:
    rows = [
        _StorRow(name="DSM-Storage", used_bytes=1000),
        _StorRow(name="tiering-remote", used_bytes=None),
        _StorRow(name="APV Vault", used_bytes=500),
    ]
    assert _stor_total(rows) == 1500


# ── _print_json ───────────────────────────────────────────────────────────────


def test_print_json_structure_and_values(capsys: pytest.CaptureFixture[str]) -> None:
    wl_rows = [
        _WlRow(
            type_label="VM",
            protected_bytes=1024,
            retired_bytes=256,
            protected_copy_bytes=512,
            retired_copy_bytes=128,
        )
    ]
    srv_rows = [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=2000,
            physical_backup_data_bytes=1500,
            backup_data_reduction_bytes=500,
            backup_data_reduction_ratio=25.0,
        )
    ]
    stor_rows = [
        _StorRow(name="DSM-Storage", used_bytes=4096),
    ]

    _print_json(wl_rows, srv_rows, stor_rows)

    captured = capsys.readouterr()
    data: dict[str, Any] = json.loads(captured.out)

    assert "workload_usage" in data
    by_type: list[dict[str, Any]] = data["workload_usage"]["by_type"]
    assert len(by_type) == 1
    entry = by_type[0]
    assert entry["type"] == "VM"
    assert entry["protected_bytes"] == 1024
    assert entry["retired_bytes"] == 256
    assert entry["total_bytes"] == 1280
    assert entry["protected_copy_bytes"] == 512
    assert entry["retired_copy_bytes"] == 128
    assert entry["total_copy_bytes"] == 640

    wl_total: dict[str, Any] = data["workload_usage"]["total"]
    assert wl_total["protected_bytes"] == 1024
    assert wl_total["retired_bytes"] == 256
    assert wl_total["total_bytes"] == 1280

    assert "backup_server_usage" in data
    servers: list[dict[str, Any]] = data["backup_server_usage"]["servers"]
    assert len(servers) == 1
    srv_entry = servers[0]
    assert srv_entry["name"] == "apm-server-01"
    assert srv_entry["logical_backup_data_bytes"] == 2000
    assert srv_entry["backup_data_reduction_ratio_pct"] == 25.0

    assert "remote_storage_usage" in data
    storages: list[dict[str, Any]] = data["remote_storage_usage"]["storages"]
    assert len(storages) == 1
    assert storages[0]["name"] == "DSM-Storage"
    assert storages[0]["used_bytes"] == 4096
    assert data["remote_storage_usage"]["total"]["used_bytes"] == 4096


def test_print_json_none_fields_serialize_as_null(capsys: pytest.CaptureFixture[str]) -> None:
    wl_rows: list[_WlRow] = []
    srv_rows = [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=None,
            physical_backup_data_bytes=None,
            backup_data_reduction_bytes=None,
            backup_data_reduction_ratio=0.0,
        )
    ]
    stor_rows = [
        _StorRow(name="DSM-Storage", used_bytes=None),
    ]

    _print_json(wl_rows, srv_rows, stor_rows)

    captured = capsys.readouterr()
    data: dict[str, Any] = json.loads(captured.out)

    servers: list[dict[str, Any]] = data["backup_server_usage"]["servers"]
    assert servers[0]["logical_backup_data_bytes"] is None
    assert servers[0]["backup_data_reduction_bytes"] is None
    assert servers[0]["backup_data_reduction_ratio_pct"] is None

    storages: list[dict[str, Any]] = data["remote_storage_usage"]["storages"]
    assert storages[0]["used_bytes"] is None
    assert data["remote_storage_usage"]["total"]["used_bytes"] is None


# ── _print_csv ────────────────────────────────────────────────────────────────


def test_print_csv_section_headers_present(capsys: pytest.CaptureFixture[str]) -> None:
    _print_csv([], [], [])

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))
    first_cols = [r[0] for r in rows if r]
    assert "Workload Usage Summary" in first_cols
    assert "Backup Server Usage Summary" in first_cols
    assert "Remote Storage Usage Summary" in first_cols


def test_print_csv_wl_data_row_values(capsys: pytest.CaptureFixture[str]) -> None:
    wl_rows = [
        _WlRow(
            type_label="VM",
            protected_bytes=1024,
            retired_bytes=256,
            protected_copy_bytes=512,
            retired_copy_bytes=128,
        )
    ]

    _print_csv(wl_rows, [], [])

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))

    vm_row = next(r for r in rows if r and r[0] == "VM")
    assert vm_row[1] == "1024"   # protected_bytes
    assert vm_row[2] == "256"    # retired_bytes
    assert vm_row[3] == "1280"   # total_bytes
    assert vm_row[4] == "512"    # protected_copy_bytes
    assert vm_row[5] == "128"    # retired_copy_bytes
    assert vm_row[6] == "640"    # total_copy_bytes


def test_print_csv_wl_total_row(capsys: pytest.CaptureFixture[str]) -> None:
    wl_rows = [
        _WlRow(
            type_label="VM",
            protected_bytes=1000,
            retired_bytes=200,
            protected_copy_bytes=100,
            retired_copy_bytes=50,
        ),
        _WlRow(
            type_label="PC",
            protected_bytes=500,
            retired_bytes=100,
            protected_copy_bytes=60,
            retired_copy_bytes=30,
        ),
    ]

    _print_csv(wl_rows, [], [])

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))

    # WL total row has 7 columns; SRV and STOR totals have 5 and 2 respectively
    total_row = next(r for r in rows if r and r[0] == "Total" and len(r) == 7)
    assert total_row[1] == "1500"   # protected_bytes
    assert total_row[2] == "300"    # retired_bytes
    assert total_row[3] == "1800"   # total_bytes
    assert total_row[4] == "160"    # protected_copy_bytes
    assert total_row[5] == "80"     # retired_copy_bytes
    assert total_row[6] == "240"    # total_copy_bytes


def test_print_csv_srv_data_row_and_total(capsys: pytest.CaptureFixture[str]) -> None:
    srv_rows = [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=2000,
            physical_backup_data_bytes=1500,
            backup_data_reduction_bytes=500,
            backup_data_reduction_ratio=25.0,
        )
    ]

    _print_csv([], srv_rows, [])

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))

    srv_row = next(r for r in rows if r and r[0] == "apm-server-01")
    assert srv_row[1] == "2000"
    assert srv_row[2] == "1500"
    assert srv_row[3] == "500"
    assert srv_row[4] == "25.0"

    # SRV total row has 5 columns; distinguish from WL (7) and STOR (2) totals
    srv_total = next(r for r in rows if r and r[0] == "Total" and len(r) == 5)
    assert srv_total[1] == "2000"
    assert srv_total[2] == "1500"
    assert srv_total[3] == "500"


def test_print_csv_srv_none_fields_serialize_as_empty(capsys: pytest.CaptureFixture[str]) -> None:
    srv_rows = [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=None,
            physical_backup_data_bytes=None,
            backup_data_reduction_bytes=None,
            backup_data_reduction_ratio=0.0,
        )
    ]

    _print_csv([], srv_rows, [])

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))

    srv_row = next(r for r in rows if r and r[0] == "apm-server-01")
    assert srv_row[1] == ""   # logical_backup_data_bytes
    assert srv_row[2] == ""   # physical_backup_data_bytes
    assert srv_row[3] == ""   # backup_data_reduction_bytes
    assert srv_row[4] == ""   # backup_data_reduction_ratio_pct (empty when reduction_bytes is None)


def test_print_csv_stor_data_rows_and_total(capsys: pytest.CaptureFixture[str]) -> None:
    stor_rows = [
        _StorRow(name="DSM-Storage", used_bytes=4096),
        _StorRow(name="tiering-remote", used_bytes=None),
    ]

    _print_csv([], [], stor_rows)

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))

    dsm_row = next(r for r in rows if r and r[0] == "DSM-Storage")
    assert dsm_row[1] == "4096"

    tiering_row = next(r for r in rows if r and r[0] == "tiering-remote")
    assert tiering_row[1] == ""

    # STOR total row has 2 columns; only non-None used_bytes values are summed
    stor_total = next(r for r in rows if r and r[0] == "Total" and len(r) == 2)
    assert stor_total[1] == "4096"


# ── _print_table ──────────────────────────────────────────────────────────────


def test_print_table_workload_row_and_total_line(capsys: pytest.CaptureFixture[str]) -> None:
    wl_rows = [
        _WlRow(
            type_label="VM",
            protected_bytes=2048,
            retired_bytes=1024,
            protected_copy_bytes=512,
            retired_copy_bytes=0,
        )
    ]

    _print_table(wl_rows, [], [])

    lines = capsys.readouterr().out.splitlines()
    vm_line = next(ln for ln in lines if "VM" in ln)
    assert "2.0 KB" in vm_line   # protected
    assert "1.0 KB" in vm_line   # retired
    assert "3.0 KB" in vm_line   # total
    assert "512.0 B" in vm_line  # protected copy
    wl_total_line = [ln for ln in lines if ln.startswith("  Total")][0]
    assert "3.0 KB" in wl_total_line


def test_print_table_server_reduction_with_ratio(capsys: pytest.CaptureFixture[str]) -> None:
    srv_rows = [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=2048,
            physical_backup_data_bytes=1024,
            backup_data_reduction_bytes=1024,
            backup_data_reduction_ratio=50.0,
        )
    ]

    _print_table([], srv_rows, [])

    lines = capsys.readouterr().out.splitlines()
    srv_line = next(ln for ln in lines if "apm-server-01" in ln)
    assert "1.0 KB (50.0%)" in srv_line
    srv_total_line = [ln for ln in lines if ln.startswith("  Total")][1]  # 2nd Total row = server section
    assert "1.0 KB (50.0%)" in srv_total_line


def test_print_table_server_reduction_none_shows_dash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    srv_rows = [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=None,
            physical_backup_data_bytes=None,
            backup_data_reduction_bytes=None,
            backup_data_reduction_ratio=0.0,
        )
    ]

    _print_table([], srv_rows, [])

    lines = capsys.readouterr().out.splitlines()
    srv_line = next(ln for ln in lines if "apm-server-01" in ln)
    assert "—" in srv_line
    srv_total_line = [ln for ln in lines if ln.startswith("  Total")][1]
    assert "—" in srv_total_line


def test_print_table_server_total_reduction_without_logical_omits_ratio(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Reduction bytes known but logical total unavailable: the Total row shows the
    # byte amount without a percentage.
    srv_rows = [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=None,
            physical_backup_data_bytes=None,
            backup_data_reduction_bytes=512,
            backup_data_reduction_ratio=0.0,
        )
    ]

    _print_table([], srv_rows, [])

    lines = capsys.readouterr().out.splitlines()
    srv_total_line = [ln for ln in lines if ln.startswith("  Total")][1]
    assert "512.0 B" in srv_total_line
    assert "%" not in srv_total_line


def test_print_table_storage_rows_and_total(capsys: pytest.CaptureFixture[str]) -> None:
    stor_rows = [
        _StorRow(name="tiering-remote", used_bytes=1024),
        _StorRow(name="DSM-Storage", used_bytes=None),
    ]

    _print_table([], [], stor_rows)

    lines = capsys.readouterr().out.splitlines()
    tiering_line = next(ln for ln in lines if "tiering-remote" in ln)
    assert "1.0 KB" in tiering_line
    dsm_line = next(ln for ln in lines if "DSM-Storage" in ln)
    assert "—" in dsm_line
    stor_total_line = [ln for ln in lines if ln.startswith("  Total")][2]  # 3rd Total row = storage section
    assert "1.0 KB" in stor_total_line


# ── _scan_workload_usage ──────────────────────────────────────────────────────


async def test_scan_workload_usage_buckets_by_type_and_retired_state() -> None:
    """Protected vs retired bytes split per type; rows follow the canonical type order
    and types without workloads are omitted."""
    apm = make_fake_apm()
    tenant = make_saas_tenant()
    apm.saas.list.return_value = ([tenant], 1)

    vm_protected = make_machine_workload(
        workload_type=MachineWorkloadType.VM,
        protected_data_bytes=100,
        backup_copy_data_bytes=10,
        is_retired=False,
    )
    pc_protected = make_machine_workload(
        workload_type=MachineWorkloadType.PC,
        protected_data_bytes=7,
        backup_copy_data_bytes=0,
        is_retired=False,
    )
    vm_retired = make_machine_workload(
        workload_type=MachineWorkloadType.VM,
        protected_data_bytes=40,
        backup_copy_data_bytes=4,
        is_retired=True,
    )
    exchange_protected = make_m365_workload(
        workload_type=M365WorkloadType.EXCHANGE,
        tenant_id=tenant.tenant_id,
        protected_data_bytes=5,
        backup_copy_data_bytes=1,
        is_retired=False,
    )

    def _machine_list(*, is_retired: bool, limit: int, offset: int) -> tuple[list[Any], int]:
        items = [vm_retired] if is_retired else [vm_protected, pc_protected]
        return (items if offset == 0 else [], len(items))

    def _m365_list(
        *, tenant_id: str, workload_type: M365WorkloadType, is_retired: bool,
        limit: int, offset: int,
    ) -> tuple[list[Any], int]:
        if workload_type is M365WorkloadType.EXCHANGE and not is_retired and offset == 0:
            return ([exchange_protected], 1)
        return ([], 0)

    apm.machine.workloads.list.side_effect = _machine_list
    apm.m365.workloads.list.side_effect = _m365_list

    rows = await _scan_workload_usage(apm)

    # Canonical order is VM before PC before Exchange; unused types are omitted.
    assert [r.type_label for r in rows] == ["VM", "PC", "Exchange"]
    vm_row = rows[0]
    assert vm_row.protected_bytes == 100
    assert vm_row.retired_bytes == 40
    assert vm_row.protected_copy_bytes == 10
    assert vm_row.retired_copy_bytes == 4
    pc_row = rows[1]
    assert pc_row.protected_bytes == 7
    assert pc_row.retired_bytes == 0
    exchange_row = rows[2]
    assert exchange_row.protected_bytes == 5
    assert exchange_row.protected_copy_bytes == 1


# ── _scan_server_usage / _scan_remote_storage_usage ───────────────────────────


async def test_scan_server_usage_maps_backup_server_fields() -> None:
    apm = make_fake_apm()
    server = make_backup_server(
        name="apm-server-01",
        logical_backup_data_bytes=2000,
        physical_backup_data_bytes=1500,
    )
    apm.backup_servers.list.return_value = ([server], 1)

    rows = await _scan_server_usage(apm)

    assert rows == [
        _SrvRow(
            name="apm-server-01",
            logical_backup_data_bytes=2000,
            physical_backup_data_bytes=1500,
            backup_data_reduction_bytes=500,
            backup_data_reduction_ratio=25.0,
        )
    ]


async def test_scan_server_usage_unavailable_stats_map_to_none() -> None:
    apm = make_fake_apm()
    server = make_backup_server(
        name="apm-server-02",
        hostname="192.0.2.2",
        logical_backup_data_bytes=None,
        physical_backup_data_bytes=None,
    )
    apm.backup_servers.list.return_value = ([server], 1)

    rows = await _scan_server_usage(apm)

    assert rows == [
        _SrvRow(
            name="apm-server-02",
            logical_backup_data_bytes=None,
            physical_backup_data_bytes=None,
            backup_data_reduction_bytes=None,
            backup_data_reduction_ratio=0.0,
        )
    ]


async def test_scan_remote_storage_usage_maps_name_and_used_bytes() -> None:
    apm = make_fake_apm()
    storage = make_remote_storage(name="tiering-remote", used_bytes=1_073_741_824)
    apm.remote_storages.list.return_value = ([storage], 1)

    rows = await _scan_remote_storage_usage(apm)

    assert rows == [_StorRow(name="tiering-remote", used_bytes=1_073_741_824)]


# ── run() ─────────────────────────────────────────────────────────────────────


def _make_populated_apm() -> Any:
    apm = make_fake_apm()

    def _machine_list(*, is_retired: bool, limit: int, offset: int) -> tuple[list[Any], int]:
        if not is_retired and offset == 0:
            wl = make_machine_workload(
                workload_type=MachineWorkloadType.VM,
                protected_data_bytes=2048,
                backup_copy_data_bytes=512,
                is_retired=False,
            )
            return ([wl], 1)
        return ([], 0)

    apm.machine.workloads.list.side_effect = _machine_list
    apm.backup_servers.list.return_value = (
        [make_backup_server(logical_backup_data_bytes=2000, physical_backup_data_bytes=1500)], 1
    )
    apm.remote_storages.list.return_value = (
        [make_remote_storage(name="tiering-remote", used_bytes=4096)], 1
    )
    return apm


async def test_run_json_end_to_end(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_make_client(monkeypatch, storage_usage_report, _make_populated_apm())

    await run("json")

    captured = capsys.readouterr()
    assert "Collecting data..." in captured.err
    data = json.loads(captured.out)
    assert data["workload_usage"]["by_type"] == [
        {
            "type": "VM",
            "protected_bytes": 2048,
            "retired_bytes": 0,
            "total_bytes": 2048,
            "protected_copy_bytes": 512,
            "retired_copy_bytes": 0,
            "total_copy_bytes": 512,
        }
    ]
    server = data["backup_server_usage"]["servers"][0]
    assert server["name"] == "apm-server-01"
    assert server["backup_data_reduction_bytes"] == 500
    assert server["backup_data_reduction_ratio_pct"] == 25.0
    assert data["remote_storage_usage"]["storages"][0] == {
        "name": "tiering-remote",
        "used_bytes": 4096,
    }
    assert data["remote_storage_usage"]["total"]["used_bytes"] == 4096


async def test_run_csv_end_to_end(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_make_client(monkeypatch, storage_usage_report, _make_populated_apm())

    await run("csv")

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    vm_row = next(r for r in rows if r and r[0] == "VM")
    assert vm_row[1] == "2048"  # protected_bytes
    srv_row = next(r for r in rows if r and r[0] == "apm-server-01")
    assert srv_row[1] == "2000"  # logical_backup_data_bytes
    stor_row = next(r for r in rows if r and r[0] == "tiering-remote")
    assert stor_row[1] == "4096"  # used_bytes


async def test_run_table_end_to_end(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_make_client(monkeypatch, storage_usage_report, _make_populated_apm())

    await run("table")

    lines = capsys.readouterr().out.splitlines()
    vm_line = next(ln for ln in lines if "VM" in ln)
    assert "2.0 KB" in vm_line
    srv_line = next(ln for ln in lines if "apm-server-01" in ln)
    assert "500.0 B (25.0%)" in srv_line


# ── main(): argparse wiring ───────────────────────────────────────────────────


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    run_mock = MagicMock(name="run", return_value=None)
    run_main_mock = MagicMock(name="run_main")
    monkeypatch.setattr(storage_usage_report, "run", run_mock)
    monkeypatch.setattr(storage_usage_report, "run_main", run_main_mock)
    return run_mock, run_main_mock


def test_main_parses_output_flag_and_wires_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, run_main_mock = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["storage_usage_report.py", "-o", "csv", "--profile", "lab"])

    storage_usage_report.main()

    run_mock.assert_called_once_with("csv", profile="lab")
    run_main_mock.assert_called_once_with(run_mock.return_value)


def test_main_default_output_is_table(monkeypatch: pytest.MonkeyPatch) -> None:
    run_mock, _ = _patch_entry_points(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["storage_usage_report.py"])

    storage_usage_report.main()

    run_mock.assert_called_once_with("table", profile=None)
