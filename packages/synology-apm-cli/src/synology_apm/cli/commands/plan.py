"""synology-apm plan — Protection Plan and Retirement Plan management commands."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import typer
from rich.padding import Padding

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    _COPY_PROGRESS_STATUSES,
    _OS_TYPE_DISPLAY,
    _SCOPE_DISPLAY,
    _WORKLOAD_TYPE_DISPLAY,
    fmt_bytes,
    fmt_category,
    fmt_copy_reason,
    fmt_copy_status,
    fmt_location_info,
    fmt_retention,
    fmt_schedule_frequency,
    fmt_schedule_label,
    fmt_schedule_str,
    print_backup_window,
    print_list_footer,
    print_retention_detail,
)
from synology_apm.cli._helpers import apm_session
from synology_apm.cli._options import (
    LIMIT_OPTION,
    LIST_OUTPUT_OPTION,
    OFFSET_OPTION,
    OUTPUT_OPTION,
    PAGE_ALL_OPTION,
)
from synology_apm.cli._serializers import (
    protection_plan_to_csv_row,
    protection_plan_to_dict,
    retirement_plan_to_csv_row,
    retirement_plan_to_dict,
    tiering_plan_to_csv_row,
)
from synology_apm.cli._validate import validate_name_or_id_args
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
    EventTriggerConfig,
    MachineTaskConfig,
    MachineTaskScope,
    ProtectionPlanPolicy,
    TieringPlan,
    VersionCopyStatus,
    WorkloadCategory,
)

app = typer.Typer(help="Manage Protection Plans, Retirement Plans, and Tiering Plans.", no_args_is_help=True)
_protection_app = typer.Typer(help="Manage Protection Plans.", no_args_is_help=True)
_retirement_app = typer.Typer(help="Manage Retirement Plans.", no_args_is_help=True)
_tiering_app = typer.Typer(help="Manage Tiering Plans.", no_args_is_help=True)
app.add_typer(_protection_app, name="protection")
app.add_typer(_retirement_app, name="retirement")
app.add_typer(_tiering_app, name="tiering")

_CATEGORY_MAP = {"machine": WorkloadCategory.MACHINE, "m365": WorkloadCategory.M365}


# ═══════════════════════════════════════════════════════════════════════════
# synology-apm plan protection
# ═══════════════════════════════════════════════════════════════════════════

@_protection_app.command("list")
@run_async
async def protection_list(
    ctx: typer.Context,
    category: str | None = typer.Option(
        None, "--category", "-c", help="Workload category filter: machine / m365 (omit for all)"
    ),
    search: str | None = typer.Option(None, "--search", "-s", help="Name keyword search"),
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List Protection Plans, optionally filtered by workload category.

    \b
    Examples:
      synology-apm plan protection list                   # list all (machine + m365)
      synology-apm plan protection list --category machine  # Machine Plans only
      synology-apm plan protection list --category m365     # M365 Plans only
      synology-apm plan protection list -v                # show Description column
    """
    if category is not None and category.lower() not in _CATEGORY_MAP:
        err_console.print(f"[red]✗[/red] Invalid category: {category!r} (expected: machine / m365)")
        raise typer.Exit(code=1)

    async with apm_session(ctx, spinner="Fetching protection plans...") as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.plans.list(
                category=_CATEGORY_MAP.get(category.lower()) if category else None,
                name_contains=search,
                limit=lim,
                offset=off,
            ),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=protection_plan_to_dict, to_csv_row=protection_plan_to_csv_row,
        )

    if result is None:
        return

    plans, total = result
    _print_protection_plan_table(plans, show_type=(category is None), verbose=verbose)
    print_list_footer(console, len(plans), total, offset)


@_protection_app.command("get")
@run_async
async def protection_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, metavar="NAME", help="Plan name (search mode)"),
    plan_id: str | None = typer.Option(None, "--id", help="Plan ID (direct mode)"),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show details for a Protection Plan.

    \b
    Examples:
      synology-apm plan protection get "Daily Backup"                           # name search
      synology-apm plan protection get --id 0c8f033b-fb57-4f46-9a9d-85e9d21c08ab  # exact lookup
    """
    validate_name_or_id_args(ctx, name, plan_id)
    async with apm_session(ctx) as apm:
        if plan_id is not None:
            plan = await apm.plans.get(plan_id)
        else:
            assert name is not None
            plan = await apm.plans.get_by_name(name)

    if dispatch_output(plan, output, protection_plan_to_dict):
        return

    console.print(f"Protection Plan: [bold]{plan.name}[/bold]")
    console.print("─" * 38)
    console.print(f"ID:           {plan.plan_id}")
    console.print(f"Category:     {fmt_category(plan.category)}")
    console.print(f"Description:  {plan.description or '-'}")
    console.print(f"Immutable:    {'Yes' if plan.is_immutable else 'No'}")
    console.print()
    console.print(f"Successful:   {plan.successful_workload_count} workloads")
    console.print(f"Unsuccessful: {plan.unsuccessful_workload_count} workloads")
    if plan.backup_copy_status and plan.backup_copy_status.status != VersionCopyStatus.NOT_ENABLED:
        bcs = plan.backup_copy_status
        console.print(f"Copy Status:  {fmt_copy_status(bcs)}", markup=True)
        if bcs.status in _COPY_PROGRESS_STATUSES and bcs.pending_version_count > 0:
            remaining = f", {fmt_bytes(bcs.remaining_bytes)} remaining" if bcs.remaining_bytes else ""
            console.print(f"              {bcs.pending_version_count} version(s) pending{remaining}")
        elif bcs.status == VersionCopyStatus.SKIPPED:
            console.print(f"              {bcs.skipped_workload_count} workload(s) skipped.")
        reason_str = fmt_copy_reason(bcs.reason)
        if reason_str:
            console.print(f"              {reason_str}")
    console.print()
    console.print("[bold]Backup Copy Policy[/bold]")
    if plan.backup_copy_policy:
        bcd = plan.backup_copy_policy
        console.print(f"  Destination: {fmt_location_info(bcd.destination)}")
        print_retention_detail(console, "Retention:  ", bcd.retention)
        console.print(f"  Schedule:    {fmt_schedule_str(bcd.schedule)}")
    else:
        console.print("  No Backup Copy enabled.")
    console.print()
    console.print("[bold]Backup Policy[/bold]")
    # always populated for a plan fetched via the plans collection
    assert plan.policy is not None
    print_retention_detail(console, "Retention:       ", plan.policy.retention)
    console.print(f"  Default Schedule: {_fmt_schedule_detail(plan.policy)}")
    if plan.backup_window is not None:
        print_backup_window(console, plan.backup_window)

    if plan.tasks:
        console.print()
        _print_tasks_section(plan.tasks)


# ═══════════════════════════════════════════════════════════════════════════
# synology-apm plan retirement
# ═══════════════════════════════════════════════════════════════════════════

@_retirement_app.command("list")
@run_async
async def retirement_list(
    ctx: typer.Context,
    search: str | None = typer.Option(None, "--search", "-s", help="Name keyword search"),
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode (shows Plan ID)"),
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List all Retirement Plans."""
    async with apm_session(ctx, spinner="Fetching retirement plans...") as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.retirement_plans.list(name_contains=search, limit=lim, offset=off),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=retirement_plan_to_dict, to_csv_row=retirement_plan_to_csv_row,
        )

    if result is None:
        return

    plans, total = result
    _print_retirement_plan_table(plans, verbose=verbose)
    print_list_footer(console, len(plans), total, offset)


@_retirement_app.command("get")
@run_async
async def retirement_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, metavar="NAME", help="Plan name (search mode)"),
    plan_id: str | None = typer.Option(None, "--id", help="Plan ID (direct mode)"),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show details for a Retirement Plan.

    \b
    Examples:
      synology-apm plan retirement get "Compliance Retention"                 # name search
      synology-apm plan retirement get --id cc39711f-deb9-40fa-b6c4-27ca82958d3c  # exact lookup
    """
    validate_name_or_id_args(ctx, name, plan_id)
    async with apm_session(ctx) as apm:
        if plan_id is not None:
            plan = await apm.retirement_plans.get(plan_id)
        else:
            assert name is not None
            plan = await apm.retirement_plans.get_by_name(name)

    if dispatch_output(plan, output, retirement_plan_to_dict):
        return

    # always populated for a plan fetched via the retirement_plans collection
    assert plan.retention is not None and plan.workload_count is not None
    r = plan.retention
    console.print(f"Retirement Plan: [bold]{plan.name}[/bold]")
    console.print("─" * 38)
    console.print(f"ID:                       {plan.plan_id}")
    console.print(f"Description:              {plan.description or '-'}")
    console.print(f"Version Retention (Days): {r.days if r.days is not None else '-'}")
    console.print(f"Keep Latest Version:      {'Yes' if r.keep_latest_version else 'No'}")
    console.print(f"Included Workloads:       {plan.workload_count}")


# ═══════════════════════════════════════════════════════════════════════════
# synology-apm plan tiering
# ═══════════════════════════════════════════════════════════════════════════

@_tiering_app.command("list")
@run_async
async def tiering_list(
    ctx: typer.Context,
    search: str | None = typer.Option(None, "--search", "-s", help="Name keyword search"),
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode (shows Plan ID)"),
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List all Tiering Plans."""
    async with apm_session(ctx, spinner="Fetching tiering plans...") as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.tiering_plans.list(name_contains=search, limit=lim, offset=off),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=TieringPlan.to_dict, to_csv_row=tiering_plan_to_csv_row,
        )

    if result is None:
        return

    plans, total = result
    _print_tiering_plan_table(plans, verbose=verbose)
    print_list_footer(console, len(plans), total, offset)


@_tiering_app.command("get")
@run_async
async def tiering_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, metavar="NAME", help="Plan name (search mode)"),
    plan_id: str | None = typer.Option(None, "--id", help="Plan ID (direct mode)"),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show details for a Tiering Plan.

    \b
    Examples:
      synology-apm plan tiering get "My Tiering Plan"                          # name search
      synology-apm plan tiering get --id f56f8969-a831-47a6-9de0-279696dafea6  # exact lookup
    """
    validate_name_or_id_args(ctx, name, plan_id)
    async with apm_session(ctx) as apm:
        if plan_id is not None:
            plan = await apm.tiering_plans.get(plan_id)
        else:
            assert name is not None
            plan = await apm.tiering_plans.get_by_name(name)

    if dispatch_output(plan, output, TieringPlan.to_dict):
        return

    dest_str = fmt_location_info(plan.destination) if plan.destination else "-"
    console.print(f"Tiering Plan: [bold]{plan.name}[/bold]")
    console.print("─" * 38)
    console.print(f"ID:               {plan.plan_id}")
    console.print(f"Description:      {plan.description or '-'}")
    ts = plan.tiering_status
    if ts and ts.status != VersionCopyStatus.NOT_ENABLED:
        console.print()
        console.print(f"Tiering Status:   {fmt_copy_status(ts)}", markup=True)
        if ts.status in _COPY_PROGRESS_STATUSES and ts.pending_version_count > 0:
            remaining = f", {fmt_bytes(ts.remaining_bytes)} remaining" if ts.remaining_bytes else ""
            console.print(f"                  {ts.pending_version_count} version(s) pending{remaining}")
        reason_str = fmt_copy_reason(ts.reason)
        if reason_str:
            console.print(f"                  {reason_str}")
        console.print()
    console.print(f"Tier After:       {plan.tiering_after_days} days")
    console.print(f"Destination:      {dest_str}")
    t = plan.daily_check_time
    console.print(f"Daily Check Time: {t.hour:02d}:{t.minute:02d}")
    console.print(f"Included Servers: {plan.server_count}")


# ── Task display helpers ──────────────────────────────────────────────────

def _fmt_min_interval(td: timedelta) -> str:
    total_secs = int(td.total_seconds())
    hours = total_secs // 3600
    minutes = (total_secs % 3600) // 60
    if hours and minutes:
        return f"{hours}h {minutes} min."
    if hours:
        return f"{hours}h"
    return f"{minutes} min."


def _fmt_event_trigger_line(et: EventTriggerConfig) -> str:
    events = []
    if et.on_sign_out:
        events.append("Sign-out")
    if et.on_lock:
        events.append("Screen lock")
    if et.on_startup:
        events.append("Startup")
    events_str = ", ".join(events) if events else "none"
    return f"Events: {events_str} (min. {_fmt_min_interval(et.min_interval)})"


def _fmt_task_scope(task: MachineTaskConfig) -> str:
    if task.scope is None:
        return "-"
    label = _SCOPE_DISPLAY.get(task.scope, task.scope.value)
    if task.scope == MachineTaskScope.CUSTOM_VOLUME and task.custom_volumes:
        label += f": {', '.join(task.custom_volumes)}"
    qualifiers = []
    if task.include_external_drives and task.scope == MachineTaskScope.ENTIRE_MACHINE:
        qualifiers.append("Include external drives")
    if task.scope == MachineTaskScope.CUSTOM_VOLUME and task.include_boot_partition:
        qualifiers.append("Include boot partition")
    if qualifiers:
        label += f" ({', '.join(qualifiers)})"
    return label


def _fmt_task_schedule(task: MachineTaskConfig) -> str:
    if task.use_main_schedule:
        return "Follow the default schedule"
    sched = task.schedule
    if sched is None:
        return "-"  # pragma: no cover - parsed plans always carry a schedule when use_main_schedule=False
    if sched.time_schedule is None and sched.event_trigger is not None:
        return _fmt_event_trigger_line(sched.event_trigger)
    sched_str = fmt_schedule_str(sched.time_schedule) if sched.time_schedule else "-"
    if sched.event_trigger is not None:
        sched_str += f"\n{_fmt_event_trigger_line(sched.event_trigger)}"
    return sched_str


def _print_tasks_section(tasks: tuple[MachineTaskConfig, ...]) -> None:
    console.print("[bold]Custom Scopes & Schedules[/bold]")
    t = new_table()
    t.add_column("Type", min_width=15)
    t.add_column("OS", min_width=7)
    t.add_column("Backup Scope", min_width=14)
    t.add_column("Schedule", min_width=27)
    for task in tasks:
        t.add_row(
            _WORKLOAD_TYPE_DISPLAY.get(task.workload_type, task.workload_type.value),
            _OS_TYPE_DISPLAY.get(task.os_type, "-"),
            _fmt_task_scope(task),
            _fmt_task_schedule(task),
        )
    console.print(Padding(t, (0, 0, 0, 2)))



def _print_protection_plan_table(
    plans: Sequence[Any], show_type: bool = True, verbose: bool = False
) -> None:
    t = new_table()
    t.add_column("Name", min_width=20)
    if show_type:
        t.add_column("Type", width=8)
    if verbose:
        t.add_column("Description", min_width=20)
    t.add_column("Immutable", width=9)
    t.add_column("Retention", min_width=15)
    t.add_column("Schedule", min_width=14)
    t.add_column("Copy Destination", min_width=16)
    t.add_column("Copy Retention", min_width=15)
    t.add_column("Copy Schedule", min_width=12)
    t.add_column("Copy Status", min_width=16)
    t.add_column("✓", width=4)
    t.add_column("✗", width=4)
    if verbose:
        t.add_column("Plan ID", min_width=36)

    for p in plans:
        # always populated for plans fetched via the plans/machine.plans/m365.plans collection
        assert p.policy is not None
        bcd = p.backup_copy_policy
        copy_ret = fmt_retention(bcd.retention) if bcd else None
        copy_sched = fmt_schedule_frequency(bcd.schedule.frequency) if bcd else None
        copy_dest = fmt_location_info(bcd.destination) if bcd else None

        row = [cell(p.name)]
        if show_type:
            row.append(cell(fmt_category(p.category)))
        if verbose:
            row.append(cell(p.description))
        row.append(cell("Yes" if p.is_immutable else "No"))
        row.append(cell(fmt_retention(p.policy.retention)))
        row.append(cell(fmt_schedule_label(p.policy)))
        row.append(cell(copy_dest))
        row.append(cell(copy_ret))
        row.append(cell(copy_sched))
        row.append(cell(fmt_copy_status(p.backup_copy_status), styled=True))
        row.append(cell(str(p.successful_workload_count)))
        row.append(cell(str(p.unsuccessful_workload_count)))
        if verbose:
            row.append(cell(p.plan_id))
        t.add_row(*row)

    console.print(t)


def _print_retirement_plan_table(plans: Sequence[Any], verbose: bool = False) -> None:
    t = new_table()
    t.add_column("Name", min_width=20)
    t.add_column("Description", min_width=16)
    t.add_column("Version Retention (Days)", min_width=10)
    t.add_column("Keep Latest Version", min_width=8)
    t.add_column("Included Workloads", width=10)
    if verbose:
        t.add_column("Plan ID", min_width=36)

    for p in plans:
        # always populated for plans fetched via the retirement_plans collection
        assert p.retention is not None and p.workload_count is not None
        r = p.retention
        row = [
            cell(p.name),
            cell(p.description or "-"),
            cell(str(r.days) if r.days is not None else "-"),
            cell("Yes" if r.keep_latest_version else "No"),
            cell(str(p.workload_count)),
        ]
        if verbose:
            row.append(cell(p.plan_id))
        t.add_row(*row)

    console.print(t)


def _fmt_schedule_detail(policy: ProtectionPlanPolicy) -> str:
    if policy.schedule is None:
        return "-"
    return fmt_schedule_str(policy.schedule)


def _print_tiering_plan_table(plans: Sequence[Any], verbose: bool = False) -> None:
    t = new_table()
    t.add_column("Name", min_width=20)
    t.add_column("Description", min_width=16)
    t.add_column("Tier After", width=12)
    t.add_column("Destination", min_width=16)
    t.add_column("Daily Check Time", width=17)
    t.add_column("Included Servers", width=10)
    t.add_column("Tiering Status", min_width=16)
    if verbose:
        t.add_column("Plan ID", min_width=36)

    for p in plans:
        dest_str = fmt_location_info(p.destination) if p.destination else None
        ts = p.tiering_status
        status_str = (
            fmt_copy_status(ts)
            if ts and ts.status != VersionCopyStatus.NOT_ENABLED
            else "-"
        )
        row = [
            cell(p.name),
            cell(p.description or "-"),
            cell(f"{p.tiering_after_days} days"),
            cell(dest_str),
            cell(f"{p.daily_check_time.hour:02d}:{p.daily_check_time.minute:02d}"),
            cell(str(p.server_count)),
            cell(status_str, styled=True),
        ]
        if verbose:
            row.append(cell(p.plan_id))
        t.add_row(*row)

    console.print(t)


