#!/usr/bin/env python3
"""
Storage usage report — summarizes storage consumption across protected and retired
workloads (both machine and M365), backup server disk usage, and remote storage
allocation.

Output is split into three sections: Workload Usage (per workload type, primary data
and backup copy for protected vs. retired), Backup Server Usage (per-server logical
backup, physical backup, and data reduction), and Remote Storage Usage (per-target
used bytes).

Usage:
    python storage_usage_report.py
    python storage_usage_report.py -o csv
    python storage_usage_report.py -o json

Environment variables (see .env.example and examples/README.md):
    APM_HOST          hostname or IP (supports host:port)
    APM_USERNAME      account
    APM_PASSWORD      password
    APM_NO_VERIFY_SSL set to true to skip SSL verification (self-signed certificate environments)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import dataclass
from typing import Any

from _common import (
    WORKLOAD_TYPE_ORDER,
    add_output_arg,
    add_profile_arg,
    collect_m365_workloads,
    collect_machine_workloads,
    fmt_bytes,
    list_m365_tenants,
    make_client,
    paginate,
    run_main,
    workload_type_label,
)

from synology_apm.sdk import APMClient, M365Workload, M365WorkloadType, MachineWorkload, MachineWorkloadType

_M365_TYPES: list[M365WorkloadType] = [t for t in WORKLOAD_TYPE_ORDER if isinstance(t, M365WorkloadType)]


@dataclass
class _WlRow:
    type_label: str
    protected_bytes: int = 0
    retired_bytes: int = 0
    protected_copy_bytes: int = 0
    retired_copy_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return self.protected_bytes + self.retired_bytes

    @property
    def total_copy_bytes(self) -> int:
        return self.protected_copy_bytes + self.retired_copy_bytes


@dataclass
class _SrvRow:
    name: str
    logical_backup_data_bytes: int | None   # logical_backup_data_bytes: raw backup data before dedup/compression
    physical_backup_data_bytes: int | None  # physical_backup_data_bytes: actual disk usage after dedup/compression
    backup_data_reduction_bytes: int | None # backup_data_reduction_bytes property
    backup_data_reduction_ratio: float      # backup_data_reduction_ratio property (0.0 when data unavailable)


@dataclass
class _StorRow:
    name: str
    used_bytes: int | None


def _none_add(a: int | None, b: int | None) -> int | None:
    """Add two nullable ints; returns None only when both inputs are None."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


# ── Data collection ────────────────────────────────────────────────────────────

async def _scan_workload_usage(apm: APMClient) -> list[_WlRow]:
    buckets: dict[MachineWorkloadType | M365WorkloadType, _WlRow] = {}

    def _accumulate(
        key: MachineWorkloadType | M365WorkloadType,
        label: str,
        usage: int,
        copy_usage: int,
        is_retired: bool,
    ) -> None:
        if key not in buckets:
            buckets[key] = _WlRow(label)
        if is_retired:
            buckets[key].retired_bytes += usage
            buckets[key].retired_copy_bytes += copy_usage
        else:
            buckets[key].protected_bytes += usage
            buckets[key].protected_copy_bytes += copy_usage

    # Machine + M365 workloads (all service types) — fetch protected and retired separately,
    # then split locally by is_retired flag.
    # list_m365_tenants is a single fast call; run it first so tenants is ready for the gather.
    tenants = await list_m365_tenants(apm)
    (machine_p, _), (machine_r, _), (m365_p, _), (m365_r, _) = await asyncio.gather(
        collect_machine_workloads(apm, is_retired=False),
        collect_machine_workloads(apm, is_retired=True),
        collect_m365_workloads(apm, _M365_TYPES, is_retired=False, tenants=tenants),
        collect_m365_workloads(apm, _M365_TYPES, is_retired=True,  tenants=tenants),
    )
    workloads: list[MachineWorkload | M365Workload] = [*machine_p, *machine_r, *m365_p, *m365_r]
    for wl in workloads:
        _accumulate(
            wl.workload_type, workload_type_label(wl),
            wl.protected_data_bytes, wl.backup_copy_data_bytes, wl.is_retired,
        )

    # Return rows in canonical display order; omit types with no workloads
    return [buckets[t] for t in WORKLOAD_TYPE_ORDER if t in buckets]


async def _scan_server_usage(apm: APMClient) -> list[_SrvRow]:
    servers, _ = await paginate(
        lambda limit, offset: apm.backup_servers.list(limit=limit, offset=offset)
    )
    return [
        _SrvRow(
            name=srv.name,
            logical_backup_data_bytes=srv.logical_backup_data_bytes,
            physical_backup_data_bytes=srv.physical_backup_data_bytes,
            backup_data_reduction_bytes=srv.backup_data_reduction_bytes,
            backup_data_reduction_ratio=srv.backup_data_reduction_ratio,
        )
        for srv in servers
    ]


async def _scan_remote_storage_usage(apm: APMClient) -> list[_StorRow]:
    # RemoteStorageCollection.list() does not expose pagination parameters;
    # a single call returns all configured remote storage targets.
    storages, _ = await apm.remote_storages.list()
    return [_StorRow(name=s.name, used_bytes=s.used_bytes) for s in storages]


# ── Totals helpers ─────────────────────────────────────────────────────────────

def _wl_totals(rows: list[_WlRow]) -> tuple[int, int, int, int]:
    return (
        sum(r.protected_bytes for r in rows),
        sum(r.retired_bytes for r in rows),
        sum(r.protected_copy_bytes for r in rows),
        sum(r.retired_copy_bytes for r in rows),
    )


def _srv_totals(rows: list[_SrvRow]) -> tuple[int | None, int | None, int | None]:
    tot_logical: int | None = None
    tot_physical: int | None = None
    tot_reduced: int | None = None
    for r in rows:
        tot_logical  = _none_add(tot_logical,  r.logical_backup_data_bytes)
        tot_physical = _none_add(tot_physical, r.physical_backup_data_bytes)
        tot_reduced  = _none_add(tot_reduced,  r.backup_data_reduction_bytes)
    return tot_logical, tot_physical, tot_reduced


def _stor_total(rows: list[_StorRow]) -> int | None:
    total: int | None = None
    for r in rows:
        if r.used_bytes is not None:
            total = (total or 0) + r.used_bytes
    return total


# ── Output: table ──────────────────────────────────────────────────────────────

def _print_table(
    wl_rows: list[_WlRow],
    srv_rows: list[_SrvRow],
    stor_rows: list[_StorRow],
) -> None:
    # Section 1: Workload Usage
    print("\nWorkload Usage Summary")
    print("─" * 102)
    print(f"  {'':<14}  {'Primary Data':^40}    {'Backup Copy':^40}")
    print(
        f"  {'Type':<14}  {'Protected':>12}  {'Retired':>12}  {'Total':>12}"
        f"    {'Protected':>12}  {'Retired':>12}  {'Total':>12}"
    )
    print(f"  {'-'*14}  {'-'*12}  {'-'*12}  {'-'*12}    {'-'*12}  {'-'*12}  {'-'*12}")
    tot_prot, tot_ret, tot_prot_copy, tot_ret_copy = _wl_totals(wl_rows)
    for wl_r in wl_rows:
        print(
            f"  {wl_r.type_label:<14}  {fmt_bytes(wl_r.protected_bytes):>12}"
            f"  {fmt_bytes(wl_r.retired_bytes):>12}  {fmt_bytes(wl_r.total_bytes):>12}"
            f"    {fmt_bytes(wl_r.protected_copy_bytes):>12}"
            f"  {fmt_bytes(wl_r.retired_copy_bytes):>12}  {fmt_bytes(wl_r.total_copy_bytes):>12}"
        )
    print(f"  {'-'*14}  {'-'*12}  {'-'*12}  {'-'*12}    {'-'*12}  {'-'*12}  {'-'*12}")
    print(
        f"  {'Total':<14}  {fmt_bytes(tot_prot):>12}"
        f"  {fmt_bytes(tot_ret):>12}  {fmt_bytes(tot_prot + tot_ret):>12}"
        f"    {fmt_bytes(tot_prot_copy):>12}"
        f"  {fmt_bytes(tot_ret_copy):>12}  {fmt_bytes(tot_prot_copy + tot_ret_copy):>12}"
    )

    # Section 2: Backup Server Usage
    print("\nBackup Server Usage Summary")
    print("─" * 76)
    print(f"  {'Server':<26}  {'Logical Backup':>14}  {'Physical Backup':>15}  {'Data Reduced':>18}")
    print(f"  {'-'*26}  {'-'*14}  {'-'*15}  {'-'*18}")

    tot_logical_s, tot_physical_s, tot_reduced_s = _srv_totals(srv_rows)
    for srv_r in srv_rows:
        if srv_r.backup_data_reduction_bytes is not None:
            reduced = f"{fmt_bytes(srv_r.backup_data_reduction_bytes)} ({srv_r.backup_data_reduction_ratio:.1f}%)"
        else:
            reduced = "—"
        print(
            f"  {srv_r.name:<26}  {fmt_bytes(srv_r.logical_backup_data_bytes):>14}"
            f"  {fmt_bytes(srv_r.physical_backup_data_bytes):>15}  {reduced:>18}"
        )

    if tot_logical_s is not None and tot_logical_s > 0 and tot_reduced_s is not None:
        tot_reduced = f"{fmt_bytes(tot_reduced_s)} ({tot_reduced_s / tot_logical_s * 100:.1f}%)"
    elif tot_reduced_s is not None:
        tot_reduced = fmt_bytes(tot_reduced_s)
    else:
        tot_reduced = "—"
    print(f"  {'-'*26}  {'-'*14}  {'-'*15}  {'-'*18}")
    print(
        f"  {'Total':<26}  {fmt_bytes(tot_logical_s):>14}"
        f"  {fmt_bytes(tot_physical_s):>15}  {tot_reduced:>18}"
    )

    # Section 3: Remote Storage Usage
    print("\nRemote Storage Usage Summary")
    print("─" * 42)
    print(f"  {'Storage':<26}  {'Used':>12}")
    print(f"  {'-'*26}  {'-'*12}")
    tot_used_r = _stor_total(stor_rows)
    for stor_r in stor_rows:
        print(f"  {stor_r.name:<26}  {fmt_bytes(stor_r.used_bytes):>12}")
    print(f"  {'-'*26}  {'-'*12}")
    print(f"  {'Total':<26}  {fmt_bytes(tot_used_r):>12}")
    print()


# ── Output: CSV ────────────────────────────────────────────────────────────────

def _print_csv(
    wl_rows: list[_WlRow],
    srv_rows: list[_SrvRow],
    stor_rows: list[_StorRow],
) -> None:
    w = csv.writer(sys.stdout)

    def _i(n: int | None) -> str:
        return "" if n is None else str(n)

    # Section 1
    w.writerow(["Workload Usage Summary"])
    w.writerow([
        "type", "protected_bytes", "retired_bytes", "total_bytes",
        "protected_copy_bytes", "retired_copy_bytes", "total_copy_bytes",
    ])
    tot_prot, tot_ret, tot_prot_copy, tot_ret_copy = _wl_totals(wl_rows)
    for wl_r in wl_rows:
        w.writerow([
            wl_r.type_label, wl_r.protected_bytes, wl_r.retired_bytes, wl_r.total_bytes,
            wl_r.protected_copy_bytes, wl_r.retired_copy_bytes, wl_r.total_copy_bytes,
        ])
    w.writerow([
        "Total", tot_prot, tot_ret, tot_prot + tot_ret,
        tot_prot_copy, tot_ret_copy, tot_prot_copy + tot_ret_copy,
    ])
    w.writerow([])

    # Section 2
    w.writerow(["Backup Server Usage Summary"])
    headers = ["server", "logical_backup_data_bytes", "physical_backup_data_bytes",
               "backup_data_reduction_bytes", "backup_data_reduction_ratio_pct"]
    w.writerow(headers)
    tot_logical_s, tot_physical_s, tot_reduced_s = _srv_totals(srv_rows)
    for srv_r in srv_rows:
        ratio_str = "" if srv_r.backup_data_reduction_bytes is None else f"{srv_r.backup_data_reduction_ratio:.1f}"
        w.writerow([
            srv_r.name, _i(srv_r.logical_backup_data_bytes), _i(srv_r.physical_backup_data_bytes),
            _i(srv_r.backup_data_reduction_bytes), ratio_str,
        ])
    if tot_logical_s is not None and tot_logical_s > 0 and tot_reduced_s is not None:
        tot_ratio_str = f"{tot_reduced_s / tot_logical_s * 100:.1f}"
    else:
        tot_ratio_str = ""
    w.writerow(["Total", _i(tot_logical_s), _i(tot_physical_s), _i(tot_reduced_s), tot_ratio_str])
    w.writerow([])

    # Section 3
    w.writerow(["Remote Storage Usage Summary"])
    w.writerow(["storage", "used_bytes"])
    tot_used_r = _stor_total(stor_rows)
    for stor_r in stor_rows:
        w.writerow([stor_r.name, _i(stor_r.used_bytes)])
    w.writerow(["Total", _i(tot_used_r)])


# ── Output: JSON ───────────────────────────────────────────────────────────────

def _print_json(
    wl_rows: list[_WlRow],
    srv_rows: list[_SrvRow],
    stor_rows: list[_StorRow],
) -> None:
    tot_wl_prot, tot_wl_ret, tot_wl_prot_copy, tot_wl_ret_copy = _wl_totals(wl_rows)
    tot_srv_logical, tot_srv_physical, tot_srv_reduced = _srv_totals(srv_rows)
    tot_stor = _stor_total(stor_rows)

    srv_tot_ratio: float | None = None
    if tot_srv_logical is not None and tot_srv_logical > 0 and tot_srv_reduced is not None:
        srv_tot_ratio = round(tot_srv_reduced / tot_srv_logical * 100, 1)

    out: dict[str, Any] = {
        "workload_usage": {
            "by_type": [
                {
                    "type": r.type_label,
                    "protected_bytes": r.protected_bytes,
                    "retired_bytes": r.retired_bytes,
                    "total_bytes": r.total_bytes,
                    "protected_copy_bytes": r.protected_copy_bytes,
                    "retired_copy_bytes": r.retired_copy_bytes,
                    "total_copy_bytes": r.total_copy_bytes,
                }
                for r in wl_rows
            ],
            "total": {
                "protected_bytes": tot_wl_prot,
                "retired_bytes": tot_wl_ret,
                "total_bytes": tot_wl_prot + tot_wl_ret,
                "protected_copy_bytes": tot_wl_prot_copy,
                "retired_copy_bytes": tot_wl_ret_copy,
                "total_copy_bytes": tot_wl_prot_copy + tot_wl_ret_copy,
            },
        },
        "backup_server_usage": {
            "servers": [
                {
                    "name": r.name,
                    "logical_backup_data_bytes": r.logical_backup_data_bytes,
                    "physical_backup_data_bytes": r.physical_backup_data_bytes,
                    "backup_data_reduction_bytes": r.backup_data_reduction_bytes,
                    "backup_data_reduction_ratio_pct": (
                        round(r.backup_data_reduction_ratio, 1) if r.backup_data_reduction_bytes is not None else None
                    ),
                }
                for r in srv_rows
            ],
            "total": {
                "logical_backup_data_bytes": tot_srv_logical,
                "physical_backup_data_bytes": tot_srv_physical,
                "backup_data_reduction_bytes": tot_srv_reduced,
                "backup_data_reduction_ratio_pct": srv_tot_ratio,
            },
        },
        "remote_storage_usage": {
            "storages": [
                {"name": r.name, "used_bytes": r.used_bytes}
                for r in stor_rows
            ],
            "total": {"used_bytes": tot_stor},
        },
    }
    print(json.dumps(out, indent=2))


# ── Entry point ────────────────────────────────────────────────────────────────

async def run(output_format: str, profile: str | None = None) -> None:
    print("Collecting data...", file=sys.stderr)
    async with make_client(profile=profile) as apm:
        wl_rows, srv_rows, stor_rows = await asyncio.gather(
            _scan_workload_usage(apm),
            _scan_server_usage(apm),
            _scan_remote_storage_usage(apm),
        )

    if output_format == "csv":
        _print_csv(wl_rows, srv_rows, stor_rows)
    elif output_format == "json":
        _print_json(wl_rows, srv_rows, stor_rows)
    else:
        _print_table(wl_rows, srv_rows, stor_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_output_arg(parser)
    add_profile_arg(parser)
    args = parser.parse_args()
    run_main(run(args.output, profile=args.profile))


if __name__ == "__main__":
    main()
