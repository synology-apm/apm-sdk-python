"""synology-apm activity — backup and restore activity record query commands."""
from __future__ import annotations

from typing import TypeVar

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    BACKUP_SCOPE_LABELS,
    fmt_backup_activity_status,
    fmt_bytes,
    fmt_datetime,
    fmt_duration,
    fmt_location_info,
    fmt_restore_activity_status,
    fmt_restore_type,
    fmt_verify_status,
    print_list_footer,
    render_log_table,
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
)
from synology_apm.cli._serializers import activity_to_dict, backup_activity_to_csv_row, restore_activity_to_csv_row
from synology_apm.cli._validate import MACHINE_TYPE_ARGS, parse_time_range, require_or_help, validate_activity_args
from synology_apm.cli.commands._actions import _cancel_activity
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
    BackupActivityStatus,
    M365WorkloadType,
    RestoreActivityStatus,
)

app = typer.Typer(help="Query activity records.", no_args_is_help=True)
_backup_app  = typer.Typer(help="Query backup activity records.", no_args_is_help=True)
_restore_app = typer.Typer(help="Query restore activity records.", no_args_is_help=True)
app.add_typer(_backup_app,  name="backup")
app.add_typer(_restore_app, name="restore")

_T = TypeVar("_T")

_BACKUP_STATUS_MAP = {
    "queuing":    BackupActivityStatus.QUEUING,
    "backing_up": BackupActivityStatus.BACKING_UP,
    "canceling":  BackupActivityStatus.CANCELING,
    "success":    BackupActivityStatus.SUCCESS,
    "failed":     BackupActivityStatus.FAILED,
    "partial":    BackupActivityStatus.PARTIAL,
    "canceled":   BackupActivityStatus.CANCELED,
}

_RESTORE_STATUS_MAP = {
    "preparing":            RestoreActivityStatus.PREPARING,
    "restoring":            RestoreActivityStatus.RESTORING,
    "canceling":            RestoreActivityStatus.CANCELING,
    "ready_for_migrate":    RestoreActivityStatus.READY_FOR_MIGRATE,
    "migrate_vm_manually":  RestoreActivityStatus.MIGRATE_VM_MANUALLY,
    "migrating":            RestoreActivityStatus.MIGRATING,
    "success":              RestoreActivityStatus.SUCCESS,
    "failed":               RestoreActivityStatus.FAILED,
    "partial":              RestoreActivityStatus.PARTIAL,
    "canceled":             RestoreActivityStatus.CANCELED,
}


_M365_TYPE_MAP: dict[str, M365WorkloadType] = {
    "exchange":   M365WorkloadType.EXCHANGE,
    "onedrive":   M365WorkloadType.ONEDRIVE,
    "chat":       M365WorkloadType.CHAT,
    "sharepoint": M365WorkloadType.SHAREPOINT,
    "teams":      M365WorkloadType.TEAMS,
    "group":      M365WorkloadType.GROUP,
}


def _parse_enum_list(
    values: list[str] | None,
    mapping: dict[str, _T],
    option_name: str,
    available: str | None = None,
) -> list[_T] | None:
    """Validate and convert a list of CLI string values to enum instances.

    Returns None when values is empty or None; exits with code 1 on unknown value.
    """
    if not values:
        return None
    result: list[_T] = []
    for v in values:
        enum_val = mapping.get(v.lower())
        if enum_val is None:
            if available:
                err_console.print(f"[red]✗[/red] Unsupported {option_name} value: {v} (available: {available})")
            else:
                err_console.print(f"[red]✗[/red] Unsupported {option_name} value: {v}")
            raise typer.Exit(code=1)
        result.append(enum_val)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# synology-apm activity backup
# ═══════════════════════════════════════════════════════════════════════════

@_backup_app.command("list")
@run_async
async def backup_list(
    ctx: typer.Context,
    status: list[str] | None = typer.Option(
        None, "--status",
        help="Repeatable: queuing / backing_up / canceling / success / failed / partial / canceled",
    ),
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    machine_type: list[str] | None = typer.Option(
        None, "--machine-type",
        help="Machine sub-type filter, repeatable: pc / ps / vm / fs",
    ),
    m365_type: list[str] | None = typer.Option(
        None, "--m365-type",
        help="M365 service type filter, repeatable: exchange / onedrive / chat / sharepoint / teams / group",
    ),
    namespace: list[str] | None = typer.Option(
        None, "--namespace", "-n",
        help=(
            "Repeatable. Show only activities on the specified backup server(s) "
            "(get namespace from synology-apm infra server list --verbose)"
        ),
    ),
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    history: bool = typer.Option(False, "--history", help="Show completed activities instead of ongoing activities"),
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
) -> None:
    """List backup activity records."""
    status_enums = _parse_enum_list(status, _BACKUP_STATUS_MAP, "status")
    machine_type_enums = _parse_enum_list(machine_type, MACHINE_TYPE_ARGS, "machine-type", "pc / ps / vm / fs")
    m365_type_enums = _parse_enum_list(
        m365_type, _M365_TYPE_MAP, "m365-type",
        "exchange / onedrive / chat / sharepoint / teams / group",
    )
    since_dt, until_dt = parse_time_range(since, until)

    async with apm_session(ctx, spinner="Fetching backup activities...") as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.activities.backup.list(
                status=status_enums,
                keyword=search,
                machine_types=machine_type_enums,
                m365_types=m365_type_enums,
                namespace=namespace,
                since=since_dt,
                until=until_dt,
                history=history,
                limit=lim,
                offset=off,
            ),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=activity_to_dict, to_csv_row=backup_activity_to_csv_row,
        )

    if result is None:
        return

    activities, total = result

    if not activities and not history:
        console.print("No ongoing backup tasks.")
        console.print("Use --history to view completed activities.")
        return

    t = new_table()
    t.add_column("Workload", min_width=20)
    t.add_column("Status", min_width=20)
    t.add_column("Verification", min_width=12)
    t.add_column("Started", min_width=19)
    t.add_column("Duration", width=10)
    t.add_column("Activity ID", min_width=36)
    if verbose:
        t.add_column("Transferred", width=12)
        t.add_column("Workload ID", min_width=36)
        t.add_column("Workload Namespace", min_width=20)

    for act in activities:
        status_label = fmt_backup_activity_status(act)
        verify_label = fmt_verify_status(act.verify_status)
        row = [
            cell(act.workload_name),
            cell(status_label, styled=True),
            cell(verify_label, styled=True),
            cell(fmt_datetime(act.started_at)),
            cell(fmt_duration(act.duration_seconds)),
            cell(act.activity_id),
        ]
        if verbose:
            row.append(cell(fmt_bytes(act.data_transferred_bytes)))
            row.append(cell(act.workload_id))
            row.append(cell(act.workload_namespace))
        t.add_row(*row)

    console.print(t)
    print_list_footer(console, len(activities), total, offset)


@_backup_app.command("get")
@run_async
async def backup_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode; returns the latest)"),
    activity_id: str | None = typer.Option(None, "--id", help="Activity ID (direct mode)"),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show backup activity details and logs.

    \b
    Search mode (by workload name, returns the latest):
      synology-apm activity backup get <WORKLOAD_NAME>
    Direct mode (by Activity ID):
      synology-apm activity backup get --id <activity-id>
    """
    validate_activity_args(ctx, name, activity_id)
    async with apm_session(ctx) as apm:
        if activity_id is not None:
            act = await apm.activities.backup.get(activity_id)
        else:
            assert name is not None
            act = await apm.activities.backup.get_latest_by_workload_name(name)

    if dispatch_output(act, output, activity_to_dict):
        return

    status_label = fmt_backup_activity_status(act)

    console.print(f"[bold]Activity Detail — {act.workload_name}[/bold]")
    console.print("─" * 44)
    console.print(f"Status:          {status_label}")
    console.print(f"Workload:        {act.workload_name}")
    console.print(f"Plan:            {act.plan_name or '-'}")
    if act.backup_scope:
        scope_label = BACKUP_SCOPE_LABELS.get(act.backup_scope, act.backup_scope.value)
        console.print(f"Backup Scope:    {scope_label}")
    console.print()
    console.print(f"Start:           {fmt_datetime(act.started_at)}")
    console.print(f"End:             {fmt_datetime(act.finished_at)}")
    console.print(f"Duration:        {fmt_duration(act.duration_seconds)}")
    console.print()
    change_str  = fmt_bytes(act.data_change_bytes)  if act.data_change_bytes  is not None else "-"
    deduped_str = fmt_bytes(act.data_deduped_bytes)
    console.print(f"Data Change:     {change_str}")
    console.print(f"Transferred:     {fmt_bytes(act.data_transferred_bytes)}")
    console.print(f"Actual Capacity Used: {deduped_str}")
    if act.processed_success_count is not None:
        console.print(
            f"Processed items: {act.processed_success_count} succeeded, "
            f"{act.processed_warning_count} warning, "
            f"{act.processed_error_count} error"
        )
    render_log_table(console, act.log_entries)


@_backup_app.command("cancel")
@run_async
async def backup_cancel(
    ctx: typer.Context,
    activity_id: str | None = typer.Option(None, "--id", help="Activity ID (from synology-apm activity backup list)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Cancel a running backup activity (requires confirmation).

    \b
    Example:
      synology-apm activity backup cancel --id <activity-id>
    """
    activity_id = require_or_help(ctx, activity_id)
    async with apm_session(ctx, abortable=True) as apm:
        await _cancel_activity(apm.activities.backup, activity_id, "backup", yes=yes, quiet=quiet)


# ═══════════════════════════════════════════════════════════════════════════
# synology-apm activity restore
# ═══════════════════════════════════════════════════════════════════════════

@_restore_app.command("list")
@run_async
async def restore_list(
    ctx: typer.Context,
    status: list[str] | None = typer.Option(
        None, "--status",
        help=(
            "Repeatable: preparing / restoring / canceling / ready_for_migrate / migrate_vm_manually "
            "/ migrating / success / failed / partial / canceled"
        ),
    ),
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    history: bool = typer.Option(False, "--history", help="Show completed activities instead of ongoing activities"),
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
) -> None:
    """List restore activity records."""
    status_enums = _parse_enum_list(status, _RESTORE_STATUS_MAP, "status")
    since_dt, until_dt = parse_time_range(since, until)

    async with apm_session(ctx, spinner="Fetching restore activities...") as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.activities.restore.list(
                status=status_enums,
                keyword=search,
                since=since_dt,
                until=until_dt,
                history=history,
                limit=lim,
                offset=off,
            ),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=activity_to_dict, to_csv_row=restore_activity_to_csv_row,
        )

    if result is None:
        return

    activities, total = result

    if not activities and not history:
        console.print("No ongoing restore tasks.")
        console.print("Use --history to view completed activities.")
        return

    t = new_table()
    t.add_column("Workload", min_width=20)
    t.add_column("Restore Type", min_width=18)
    t.add_column("Status", min_width=20)
    t.add_column("Started", min_width=19)
    t.add_column("Duration", width=10)
    t.add_column("Operator", min_width=12)
    t.add_column("Activity ID", min_width=36)
    if verbose:
        t.add_column("Transferred", width=12)
        t.add_column("Workload ID", min_width=36)
        t.add_column("Workload Namespace", min_width=20)

    for act in activities:
        status_label = fmt_restore_activity_status(act)
        restore_type_label = fmt_restore_type(act.restore_type)
        row = [
            cell(act.workload_name),
            cell(restore_type_label),
            cell(status_label, styled=True),
            cell(fmt_datetime(act.started_at)),
            cell(fmt_duration(act.duration_seconds)),
            cell(act.operator),
            cell(act.activity_id),
        ]
        if verbose:
            row.append(cell(fmt_bytes(act.data_transferred_bytes)))
            row.append(cell(act.workload_id))
            row.append(cell(act.workload_namespace))
        t.add_row(*row)

    console.print(t)
    print_list_footer(console, len(activities), total, offset)


@_restore_app.command("get")
@run_async
async def restore_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Workload name (search mode; returns the latest)"),
    activity_id: str | None = typer.Option(None, "--id", help="Activity ID (direct mode)"),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show restore activity details and logs.

    \b
    Search mode (by workload name, returns the latest):
      synology-apm activity restore get <WORKLOAD_NAME>
    Direct mode (by Activity ID):
      synology-apm activity restore get --id <activity-id>
    """
    validate_activity_args(ctx, name, activity_id)
    async with apm_session(ctx) as apm:
        if activity_id is not None:
            act = await apm.activities.restore.get(activity_id)
        else:
            assert name is not None
            act = await apm.activities.restore.get_latest_by_workload_name(name)

    if dispatch_output(act, output, activity_to_dict):
        return

    status_label = fmt_restore_activity_status(act)

    console.print(f"[bold]Restore Activity Detail — {act.workload_name}[/bold]")
    console.print("─" * 44)
    console.print(f"{'Status:':<24}{status_label}")
    console.print(f"{'Workload:':<24}{act.workload_name}")
    if act.restore_type:
        console.print(f"{'Restore Type:':<24}{fmt_restore_type(act.restore_type)}")
    if act.version_timestamp:
        console.print(f"{'Version:':<24}{fmt_datetime(act.version_timestamp)}")
    if act.restore_from_info:
        console.print(f"{'Restore from:':<24}{fmt_location_info(act.restore_from_info)}")
    if act.restore_destination:
        console.print(f"{'Destination:':<24}{act.restore_destination}")
    if act.destination_path:
        console.print(f"{'Destination path:':<24}{act.destination_path}")
    if act.destination_inventory:
        hv = act.destination_inventory
        hv_label = f"{hv.hostname} ({hv.address})" if hv.address else hv.hostname
        console.print(f"{'Destination hypervisor:':<24}{hv_label}")
    if act.operator:
        console.print(f"{'Operator:':<24}{act.operator}")
    console.print()
    console.print(f"{'Start:':<24}{fmt_datetime(act.started_at)}")
    console.print(f"{'End:':<24}{fmt_datetime(act.finished_at)}")
    console.print(f"{'Duration:':<24}{fmt_duration(act.duration_seconds)}")
    console.print()
    xfr_str = fmt_bytes(act.data_transferred_bytes)
    console.print(f"{'Transferred:':<24}{xfr_str}")
    if act.processed_success_count is not None:
        console.print(
            f"{'Processed items:':<24}{act.processed_success_count} succeeded, "
            f"{act.processed_warning_count} warning, "
            f"{act.processed_error_count} error"
        )
    render_log_table(console, act.log_entries)


@_restore_app.command("cancel")
@run_async
async def restore_cancel(
    ctx: typer.Context,
    activity_id: str | None = typer.Option(None, "--id", help="Activity ID (from synology-apm activity restore list)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Cancel a running restore activity (requires confirmation).

    \b
    Example:
      synology-apm activity restore cancel --id <activity-id>
    """
    activity_id = require_or_help(ctx, activity_id)

    async with apm_session(ctx, abortable=True) as apm:
        await _cancel_activity(apm.activities.restore, activity_id, "restore", yes=yes, quiet=quiet)
