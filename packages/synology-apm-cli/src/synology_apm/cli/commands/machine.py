"""synology-apm machine — device Workload management commands.

get / backup / cancel / retire / change-plan / version list / version get support two modes:
  Search mode: <name>  (name keyword search)
  Direct mode: --id <id> --namespace <ns>
"""
from __future__ import annotations

from collections.abc import Sequence

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    _FILE_SERVER_TYPE_DISPLAY,
    _WORKLOAD_TYPE_DISPLAY,
    fmt_backup_copy,
    fmt_backup_server,
    fmt_bytes,
    fmt_datetime,
    fmt_verify_status,
    fmt_workload_status,
    print_list_footer,
    print_version_detail,
    print_workload_detail,
    render_version_table,
)
from synology_apm.cli._helpers import apm_session
from synology_apm.cli._options import (
    LIMIT_OPTION,
    LIST_OUTPUT_OPTION,
    OFFSET_OPTION,
    OUTPUT_OPTION,
    PAGE_ALL_OPTION,
    SINCE_OPTION,
    UNTIL_OPTION,
    VERSION_LIMIT_OPTION,
)
from synology_apm.cli._serializers import (
    version_detail_to_dict,
    version_to_csv_row,
    version_to_dict,
    workload_to_csv_row,
    workload_to_dict,
)
from synology_apm.cli._validate import (
    MACHINE_TYPE_ARGS,
    WorkloadRef,
    _resolve_plans,
    parse_time_range,
    print_resolved_version,
    require_or_help,
    validate_resolve_args,
    validate_version_lock_args,
    validate_version_workload_args,
)
from synology_apm.cli.commands._actions import _do_backup, _do_cancel, _do_change_plan, _do_retire
from synology_apm.cli.errors import err_console
from synology_apm.cli.output import (
    ListOutputFormat,
    OutputFormat,
    cell,
    console,
    dispatch_output,
    dispatch_paginated_list,
    new_table,
)
from synology_apm.sdk import (
    MachineWorkload,
    MachineWorkloadType,
)

app = typer.Typer(help="Manage Machine Workloads (PC / PS / VM / FS).", no_args_is_help=True)
version_app = typer.Typer(help="Manage backup versions.", no_args_is_help=True)
app.add_typer(version_app, name="version")




# ── synology-apm machine list --type [pc|ps|vm|fs] ───────────────────────────────────────

@app.command("list")
@run_async
async def machine_list(
    ctx: typer.Context,
    type_filter: list[str] | None = typer.Option(
        None, "--type", metavar="[pc|ps|vm|fs]", help="Workload type filter, repeatable (default: all types)"
    ),
    retired: bool = typer.Option(False, "--retired", help="Show only retired workloads (default: show protected only)"),
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    namespace: str | None = typer.Option(
        None, "--namespace", "-n",
        help=(
            "Show only workloads on the specified backup server "
            "(get namespace from synology-apm infra server list --verbose)"
        ),
    ),
    hypervisor: str | None = typer.Option(
        None, "--hypervisor",
        help="Filter VM workloads by Hypervisor ID (get ID from synology-apm infra hypervisor list --verbose)",
    ),
    plan: list[str] | None = typer.Option(
        None, "--plan",
        help=(
            "Plan name or ID (repeatable). Resolved against Protection Plans, or Retirement "
            "Plans if --retired is set."
        ),
    ),
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
) -> None:
    """List Machine Workloads.

    \b
    Examples:
      synology-apm machine list                          # list all protected workloads
      synology-apm machine list --type vm                # list protected VMs only
      synology-apm machine list --type vm --type fs      # list VMs and File Servers
      synology-apm machine list --type ps --retired
      synology-apm machine list --search corp-pc
      synology-apm machine list --namespace <ns>
      synology-apm machine list --plan "Daily Backup"
    """
    workload_types: list[MachineWorkloadType] | None = None
    if type_filter:
        invalid = [t for t in type_filter if t.lower() not in MACHINE_TYPE_ARGS]
        if invalid:
            err_console.print(f"[red]✗[/red] Invalid type: {invalid[0]!r} (expected: pc / ps / vm / fs)")
            raise typer.Exit(code=1)
        workload_types = [MACHINE_TYPE_ARGS[t.lower()] for t in type_filter]
    await _do_list(
        ctx=ctx,
        workload_types=workload_types,
        retired=retired, search=search, namespace=namespace, hypervisor_id=hypervisor, plan=plan,
        limit=limit, offset=offset, page_all=page_all, output=output, verbose=verbose,
    )


async def _do_list(
    ctx: typer.Context,
    workload_types: list[MachineWorkloadType] | None,
    retired: bool,
    search: str | None,
    namespace: str | None,
    hypervisor_id: str | None,
    plan: list[str] | None,
    limit: int,
    offset: int,
    page_all: bool,
    output: ListOutputFormat,
    verbose: bool,
) -> None:
    async with apm_session(ctx, spinner="Fetching workloads...") as apm:
        resolved_plans = await _resolve_plans(apm, plan, is_retired=retired)
        result = await dispatch_paginated_list(
            lambda off, lim: apm.machine.workloads.list(
                workload_types=workload_types,
                is_retired=retired,
                name_contains=search,
                namespace=namespace,
                hypervisor_id=hypervisor_id,
                plan=resolved_plans,
                limit=lim,
                offset=off,
            ),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=workload_to_dict, to_csv_row=workload_to_csv_row,
        )

    if result is None:
        return
    workloads, total = result
    _print_workload_table(workloads, verbose=verbose, retired=retired)
    print_list_footer(console, len(workloads), total, offset)


# ── synology-apm machine get ───────────────────────────────────────────────────────

@app.command("get")
@run_async
async def machine_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode; requires --namespace)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
    output: OutputFormat = OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
) -> None:
    """Show details for a Machine Workload.

    \b
    Search mode (name lookup; searches protected workloads by default):
      synology-apm machine get "CORP-PC-001"
      synology-apm machine get "CORP-PC-001" --retired

    \b
    Direct mode:
      synology-apm machine get --id <id> --namespace <ns>
    """
    ref = validate_resolve_args(ctx, name, workload_id, namespace)
    async with apm_session(ctx) as apm:
        wl = await ref.resolve_machine(apm, is_retired=retired)

    if dispatch_output(wl, output, workload_to_dict):
        return
    _print_workload_detail(wl)


# ── synology-apm machine backup ────────────────────────────────────────────────────

@app.command("backup")
@run_async
async def machine_backup(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Trigger an on-demand backup for a Machine Workload.

    \b
    Search mode (name lookup):
      synology-apm machine backup "CORP-PC-001"

    \b
    Direct mode:
      synology-apm machine backup --id <id> --namespace <ns>
    """
    ref = validate_resolve_args(ctx, name, workload_id, namespace)
    async with apm_session(ctx) as apm:
        await _do_backup(
            lambda: ref.resolve_machine(apm),
            apm.machine.workloads.backup_now,
            quiet=quiet,
        )


# ── synology-apm machine cancel ────────────────────────────────────────────────────

@app.command("cancel")
@run_async
async def machine_cancel(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Cancel the running backup for a Machine Workload.

    \b
    Search mode (name lookup):
      synology-apm machine cancel "CORP-PC-001"

    \b
    Direct mode:
      synology-apm machine cancel --id <id> --namespace <ns>
    """
    ref = validate_resolve_args(ctx, name, workload_id, namespace)
    async with apm_session(ctx, abortable=True) as apm:
        await _do_cancel(
            lambda: ref.resolve_machine(apm),
            apm.machine.workloads.cancel_backup,
            _machine_type_label,
            yes=yes,
            quiet=quiet,
        )


# ── synology-apm machine retire ────────────────────────────────────────────────────

@app.command("retire")
@run_async
async def machine_retire(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    plan: str | None = typer.Option(
        None, "--plan", help="Retirement Plan name or ID (required). Resolved against Retirement Plans."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt (destructive, irreversible)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Retire a Machine Workload (irreversible; requires confirmation).

    \b
    Search mode (name lookup):
      synology-apm machine retire "CORP-PC-001" --plan "Compliance Retention"

    \b
    Direct mode:
      synology-apm machine retire --id <id> --namespace <ns> --plan <plan-id>
    """
    ref = validate_resolve_args(ctx, name, workload_id, namespace)
    plan = require_or_help(ctx, plan)
    async with apm_session(ctx, abortable=True) as apm:
        await _do_retire(
            lambda: ref.resolve_machine(apm),
            lambda: apm.machine.workloads.get_by_name(ref.identifier, is_retired=True),
            apm.machine.workloads.retire,
            _machine_type_label,
            apm=apm,
            is_direct=ref.is_direct,
            plan_arg=plan,
            resource_type="Workload",
            yes=yes,
            quiet=quiet,
        )


# ── synology-apm machine change-plan ───────────────────────────────────────────────

@app.command("change-plan")
@run_async
async def machine_change_plan(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
    plan: str | None = typer.Option(
        None, "--plan",
        help=(
            "Plan name or ID (required). Resolved against Protection Plans if the workload is "
            "active, or Retirement Plans if it is already retired."
        ),
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Change the Protection Plan or Retirement Plan assigned to a Machine Workload.

    The plan type --plan is resolved against is auto-detected from the workload's current
    state: a Protection Plan for an active workload, a Retirement Plan for an already-retired one.

    \b
    Search mode (name lookup):
      synology-apm machine change-plan "CORP-PC-001" --plan "Daily Backup"
      synology-apm machine change-plan "CORP-PC-001" --retired --plan "Compliance Retention"

    \b
    Direct mode:
      synology-apm machine change-plan --id <id> --namespace <ns> --plan <plan-id>
    """
    ref = validate_resolve_args(ctx, name, workload_id, namespace)
    plan = require_or_help(ctx, plan)
    async with apm_session(ctx, abortable=True) as apm:
        await _do_change_plan(
            lambda: ref.resolve_machine(apm, is_retired=retired),
            apm.machine.workloads.change_plan,
            _machine_type_label,
            apm=apm,
            plan_arg=plan,
            yes=yes,
            quiet=quiet,
        )


# ── synology-apm machine version list / get ────────────────────────────────────────

@version_app.command("list")
@run_async
async def machine_version_list(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    limit: int = VERSION_LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
) -> None:
    """List backup version history for a Machine Workload.

    \b
    Search mode (name lookup; searches protected workloads by default):
      synology-apm machine version list "CORP-PC-001"
      synology-apm machine version list "CORP-PC-001" --retired

    \b
    Direct mode:
      synology-apm machine version list --id <id> --namespace <ns>
    """
    ref = validate_resolve_args(ctx, name, workload_id, namespace)
    since_dt, until_dt = parse_time_range(since, until)

    async with apm_session(ctx) as apm:
        wl = await ref.resolve_machine(apm, is_retired=retired)
        result = await dispatch_paginated_list(
            lambda off, lim: apm.machine.workloads.list_versions(
                wl, limit=lim, offset=off, since=since_dt, until=until_dt,
            ),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=version_to_dict, to_csv_row=version_to_csv_row,
        )

    if result is None:
        return

    versions, total = result
    show_verify = wl.workload_type in (MachineWorkloadType.PS, MachineWorkloadType.VM)
    render_version_table(console, versions, offset, wl, verbose=verbose, show_verify=show_verify)
    print_list_footer(console, len(versions), total, offset)


@version_app.command("get")
@run_async
async def machine_version_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    version_id: str | None = typer.Option(None, "--id", help="Version ID (from version list; omit to get the latest)"),
    workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show activity details and logs for a backup version (omit --id to get the latest).

    \b
    Search mode (by workload name; searches protected workloads by default):
      synology-apm machine version get "CORP-PC-001"               (latest version)
      synology-apm machine version get "CORP-PC-001" --id <ver-id>
      synology-apm machine version get "CORP-PC-001" --id <ver-id> --retired

    \b
    Direct mode (Workload ID + Namespace):
      synology-apm machine version get --workload-id <wl-id> --namespace <ns>
      synology-apm machine version get --workload-id <wl-id> --namespace <ns> --id <ver-id>
    """
    ref = validate_version_workload_args(ctx, name, workload_id, namespace)

    async with apm_session(ctx) as apm:
        wl = await ref.resolve_machine(apm, is_retired=retired)

        if version_id is not None:
            v = await apm.machine.workloads.get_version(wl, version_id)
        else:
            v = await apm.machine.workloads.get_latest_version(wl)
        print_resolved_version(version_id, v)

        act = await apm.activities.backup.get_by_version(v)

    if dispatch_output(None, output, lambda _: version_detail_to_dict(v, act)):
        return

    print_version_detail(console, v, act)


# ── synology-apm machine version lock / unlock ─────────────────────────────────────


async def _exec_version_lock(
    ctx: typer.Context,
    ref: WorkloadRef,
    retired: bool,
    version_id: str,
    *,
    lock: bool,
) -> None:
    async with apm_session(ctx) as apm:
        wl = await ref.resolve_machine(apm, is_retired=retired)
        version = await apm.machine.workloads.get_version(wl, version_id)
        if lock:
            await apm.machine.workloads.lock_version(version)
        else:
            await apm.machine.workloads.unlock_version(version)


@version_app.command("lock")
@run_async
async def machine_version_lock(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    version_id: str | None = typer.Option(None, "--id", help="Version ID (from version list)"),
    workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Lock a backup version to prevent deletion by retention rules.

    \b
    Search mode:
      synology-apm machine version lock "CORP-PC-001" --id <ver-id>

    \b
    Direct mode:
      synology-apm machine version lock --workload-id <wl-id> --namespace <ns> --id <ver-id>
    """
    ref, version_id = validate_version_lock_args(ctx, name, workload_id, namespace, version_id)
    await _exec_version_lock(ctx, ref, retired, version_id, lock=True)
    if not quiet:
        console.print(f"[green]✓[/green] Version locked: {version_id}")


@version_app.command("unlock")
@run_async
async def machine_version_unlock(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode)"),
    version_id: str | None = typer.Option(None, "--id", help="Version ID (from version list)"),
    workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
    namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
    retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Unlock a backup version, allowing retention rules to delete it.

    \b
    Search mode:
      synology-apm machine version unlock "CORP-PC-001" --id <ver-id>

    \b
    Direct mode:
      synology-apm machine version unlock --workload-id <wl-id> --namespace <ns> --id <ver-id>
    """
    ref, version_id = validate_version_lock_args(ctx, name, workload_id, namespace, version_id)
    await _exec_version_lock(ctx, ref, retired, version_id, lock=False)
    if not quiet:
        console.print(f"[green]✓[/green] Version unlocked: {version_id}")


# ── Formatting helpers ────────────────────────────────────────────────────


def _machine_type_label(wl: MachineWorkload) -> str:
    base = _WORKLOAD_TYPE_DISPLAY.get(wl.workload_type, wl.workload_type.value)
    if wl.workload_type == MachineWorkloadType.FS and wl.fs_config is not None:
        sub = _FILE_SERVER_TYPE_DISPLAY.get(wl.fs_config.server_type, "Unknown")
        return f"{base} / {sub}"
    return base



def _print_workload_table(
    workloads: Sequence[MachineWorkload], verbose: bool = False, retired: bool = False
) -> None:
    t = new_table()
    t.add_column("Name", min_width=16)
    t.add_column("Type", min_width=15)
    if not retired:
        t.add_column("Status", min_width=12)
    t.add_column("Verification", min_width=12)
    t.add_column("Last Backup", min_width=19)
    t.add_column("Protected Size", min_width=14)
    t.add_column("Copy Size", min_width=9)
    t.add_column("Protection Plan", min_width=14)
    t.add_column("Backup Server", min_width=14)
    t.add_column("Copy Destination", min_width=16)
    if verbose:
        t.add_column("IP Address", min_width=14)
        t.add_column("Workload ID", min_width=36)
        t.add_column("Namespace", min_width=36)
        t.add_column("Plan ID", min_width=36)

    for wl in workloads:
        row = [cell(wl.name), cell(_machine_type_label(wl))]
        if not retired:
            row.append(cell(fmt_workload_status(wl), styled=True))
        row.append(cell(fmt_verify_status(wl.verify_status), styled=True))
        row += [
            cell(fmt_datetime(wl.last_backup_at)),
            cell(fmt_bytes(wl.protected_data_bytes)),
            cell(fmt_bytes(wl.backup_copy_data_bytes) if wl.backup_copy_data_bytes else "-"),
            cell(wl.plan.name),
            cell(fmt_backup_server(wl)),
            cell(fmt_backup_copy(wl)),
        ]
        if verbose:
            row += [
                cell(wl.ip_address),
                cell(wl.workload_id),
                cell(wl.namespace),
                cell(wl.plan.plan_id),
            ]
        t.add_row(*row)

    console.print(t)


def _print_workload_detail(wl: MachineWorkload) -> None:
    info_rows: list[tuple[str, str]] = []
    if wl.inventory_name:
        inv_type = f" ({wl.inventory_type})" if wl.inventory_type else ""
        info_rows.append(("Host", f"{wl.inventory_name}{inv_type}"))
    if wl.device_uuid:
        info_rows.append(("Device UUID", wl.device_uuid))
    if wl.agent_version:
        info_rows.append(("Agent", wl.agent_version))
    if wl.ip_address:
        info_rows.append(("IP", wl.ip_address))
    status_rows: list[tuple[str, str]] = []
    if wl.verify_status is not None:
        status_rows.append(("Verification", fmt_verify_status(wl.verify_status)))
    print_workload_detail(
        console, wl,
        type_label=f"Machine / {_machine_type_label(wl)}",
        info_rows=info_rows,
        status_rows=status_rows,
    )
