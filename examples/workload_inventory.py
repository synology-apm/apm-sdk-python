#!/usr/bin/env python3
"""
Workload inventory — lists name, type, backup server, last backup date,
backup status, and version count for workloads as a table, CSV, or JSON.
Use --no-versions to skip version count fetching for faster output.

Usage:
    python workload_inventory.py --category machine
    python workload_inventory.py --category machine -o csv
    python workload_inventory.py --category machine --retired --no-versions
    python workload_inventory.py --category m365 --m365-service exchange
    python workload_inventory.py --category m365 --m365-service exchange --m365-service onedrive
    python workload_inventory.py --category all
    python workload_inventory.py --category all --m365-service sharepoint -o csv

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

from _common import (
    add_category_args,
    add_output_arg,
    add_profile_arg,
    collect_m365_workloads,
    collect_machine_workloads,
    fmt_dt,
    list_m365_tenants,
    make_client,
    resolve_m365_services,
    run_main,
    workload_type_label,
)

from synology_apm.sdk import APMClient, M365Workload, M365WorkloadType, MachineWorkload

_DEFAULT_CONCURRENCY = 10


def _print_table(headers: list[str], rows: list[list[str | int | None]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len("" if cell is None else str(cell)))
    row_fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(row_fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(row_fmt.format(*[("" if c is None else str(c)) for c in row]))


async def _get_version_count(
    apm: APMClient,
    wl: MachineWorkload | M365Workload,
    sem: asyncio.Semaphore,
) -> int:
    async with sem:
        if isinstance(wl, M365Workload):
            _, total = await apm.m365.workloads.list_versions(wl, limit=1)
        else:
            _, total = await apm.machine.workloads.list_versions(wl, limit=1)
        assert total is not None  # list_versions() always reports a real total
        return total


def _build_inventory(
    workloads: list[MachineWorkload | M365Workload],
    version_counts: list[int | None],
    tenant_names: dict[str, str],
    category: str,
    include_versions: bool,
) -> tuple[list[str], list[list[str | int | None]]]:
    include_category_col = category == "all"
    include_tenant_col   = category in ("m365", "all")

    headers: list[str] = ["name"]
    if include_category_col:
        headers.append("category")
    headers.append("type")
    if include_tenant_col:
        headers.append("tenant")
    headers.extend(["plan_name", "backup_server", "last_backup_at", "backup_status"])
    if include_versions:
        headers.append("version_count")

    rows: list[list[str | int | None]] = []
    for wl, vc in zip(workloads, version_counts, strict=True):
        if isinstance(wl, M365Workload):
            cat_label  = "M365"
            tenant_col = tenant_names.get(wl.tenant_id, wl.tenant_id)
        else:
            cat_label  = "Machine"
            tenant_col = ""
        server = wl.backup_server.name if wl.backup_server else ""
        status = wl.status.value
        row: list[str | int | None] = [wl.name]
        if include_category_col:
            row.append(cat_label)
        row.append(workload_type_label(wl))
        if include_tenant_col:
            row.append(tenant_col)
        row.extend([wl.plan.name, server, fmt_dt(wl.last_backup_at), status])
        if include_versions:
            row.append(vc)
        rows.append(row)

    return headers, rows


async def run(
    retired_only: bool,
    include_versions: bool,
    concurrency: int,
    category: str,
    m365_services: list[M365WorkloadType] | None,
    output_format: str,
    profile: str | None = None,
) -> None:
    print("Fetching workloads...", file=sys.stderr)
    async with make_client(profile=profile) as apm:
        workloads: list[MachineWorkload | M365Workload] = []
        tenant_names: dict[str, str] = {}  # tenant_id → display name (M365 only)

        if category in ("machine", "all"):
            machine, _ = await collect_machine_workloads(apm, is_retired=retired_only)
            workloads.extend(machine)

        if category in ("m365", "all"):
            services = m365_services if m365_services is not None else list(M365WorkloadType)
            tenants = await list_m365_tenants(apm)
            tenant_names = {t.tenant_id: t.tenant_name for t in tenants}
            m365, _ = await collect_m365_workloads(apm, services, is_retired=retired_only, tenants=tenants)
            workloads.extend(m365)

        # Fetch version counts in parallel, bounded by semaphore.
        version_counts: list[int | None]
        if include_versions:
            print(f"Fetching version counts for {len(workloads)} workload(s)...", file=sys.stderr)
            sem = asyncio.Semaphore(concurrency)
            version_counts = list(
                await asyncio.gather(*[_get_version_count(apm, wl, sem) for wl in workloads])
            )
        else:
            version_counts = [None] * len(workloads)

    # Build rows and render.
    headers, rows = _build_inventory(workloads, version_counts, tenant_names, category, include_versions)

    if output_format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([("" if c is None else c) for c in row])
    elif output_format == "json":
        json_rows = [dict(zip(headers, row, strict=True)) for row in rows]
        print(json.dumps(json_rows, indent=2, ensure_ascii=False))
    else:
        _print_table(headers, rows)

    print(f"[{len(workloads)} workloads exported]", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_category_args(parser, verb="export")
    parser.add_argument(
        "--retired", dest="retired_only", action="store_true",
        help="Show only retired workloads (instead of protected)",
    )
    parser.add_argument(
        "--no-versions", dest="no_versions", action="store_true",
        help="Skip version count lookup (faster; omits the Versions column)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=_DEFAULT_CONCURRENCY, metavar="N",
        help=f"Max concurrent version-count requests (default: {_DEFAULT_CONCURRENCY})",
    )
    add_output_arg(parser)
    add_profile_arg(parser)
    args = parser.parse_args()

    m365_services = resolve_m365_services(parser, args)

    run_main(run(
        retired_only=args.retired_only,
        include_versions=not args.no_versions,
        concurrency=args.concurrency,
        category=args.category,
        m365_services=m365_services,
        output_format=args.output,
        profile=args.profile,
    ))


if __name__ == "__main__":
    main()
