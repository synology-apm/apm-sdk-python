"""Shared resolve/confirm/invoke/print-success bodies for backup/cancel/retire/change-plan.

Domain-specific differences (workload resolution, presence of a type label, the
``resource_type`` string passed to ``InvalidOperationError``) are absorbed via callables
passed in by each call site in ``commands/machine.py`` and ``commands/m365.py``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import typer

from synology_apm.cli._display import fmt_datetime, fmt_retention, fmt_retirement_retention, fmt_schedule_label
from synology_apm.cli._validate import _resolve_plan
from synology_apm.cli.errors import err_console
from synology_apm.cli.output import console
from synology_apm.sdk import (
    APMClient,
    BackupActivityCollection,
    InvalidOperationError,
    ProtectionPlan,
    ResourceNotFoundError,
    RestoreActivityCollection,
    RetirementPlan,
    Workload,
)

W = TypeVar("W", bound=Workload)


async def _do_backup(
    resolve: Callable[[], Awaitable[W]],
    backup_now: Callable[[W], Awaitable[None]],
    *,
    quiet: bool,
) -> None:
    wl = await resolve()
    await backup_now(wl)
    if not quiet:
        console.print("[green]✓[/green] Backup triggered.")
        console.print(f"  Workload: {wl.name}")
        console.print("\n  Use `synology-apm-cli activity backup list` to check progress.")


async def _do_cancel(
    resolve: Callable[[], Awaitable[W]],
    cancel_backup: Callable[[W], Awaitable[None]],
    label_fn: Callable[[W], str | None],
    *,
    yes: bool,
    quiet: bool,
) -> None:
    wl = await resolve()
    if not yes:
        label = label_fn(wl)
        type_part = f" ({label})" if label else ""
        err_console.print("[yellow]⚠[/yellow] Confirm cancel backup?")
        err_console.print(f"\n  Workload:  {wl.name}{type_part}\n")
        typer.confirm("  Confirm?", abort=True)
    await cancel_backup(wl)
    if not quiet:
        console.print(f"[green]✓[/green] Backup cancelled: {wl.name}")


async def _do_retire(
    resolve: Callable[[], Awaitable[W]],
    probe_retired: Callable[[], Awaitable[W]],
    retire_fn: Callable[[W, RetirementPlan], Awaitable[None]],
    label_fn: Callable[[W], str | None],
    *,
    apm: APMClient,
    is_direct: bool,
    plan_arg: str,
    resource_type: str,
    yes: bool,
    quiet: bool,
) -> None:
    try:
        wl = await resolve()
    except ResourceNotFoundError:
        if not is_direct:
            try:
                existing = await probe_retired()
                raise InvalidOperationError(
                    f"Workload '{existing.name}' is already retired.",
                    resource_type=resource_type,
                    resource_id=existing.workload_id,
                )
            except ResourceNotFoundError:
                pass
        raise
    resolved_plan = await _resolve_plan(apm, plan_arg, is_retired=True)
    assert isinstance(resolved_plan, RetirementPlan)
    assert resolved_plan.retention is not None
    label = label_fn(wl)
    type_part = f" ({label})" if label else ""
    err_console.print("[yellow]⚠[/yellow] Warning: this action is irreversible!")
    err_console.print(f"\n  Workload:     {wl.name}{type_part}")
    err_console.print(f"  Retirement Plan: {resolved_plan.name} ({resolved_plan.plan_id})")
    err_console.print(f"  Retention:    {fmt_retirement_retention(resolved_plan.retention)}")
    err_console.print("  The workload will be retired and no longer backed up.")
    err_console.print("  Existing backup versions will not be deleted immediately.\n")
    if not yes:
        typer.confirm("  Confirm retire?", abort=True)
    await retire_fn(wl, resolved_plan)
    if not quiet:
        console.print(f"[green]✓[/green] Workload retired: {wl.name}")


async def _do_change_plan(
    resolve: Callable[[], Awaitable[W]],
    change_plan_fn: Callable[[W, ProtectionPlan | RetirementPlan], Awaitable[None]],
    label_fn: Callable[[W], str | None],
    *,
    apm: APMClient,
    plan_arg: str,
    yes: bool,
    quiet: bool,
) -> None:
    wl = await resolve()

    resolved_plan = await _resolve_plan(apm, plan_arg, is_retired=wl.is_retired)
    if isinstance(resolved_plan, RetirementPlan):
        # always populated for a plan resolved via the plans/retirement_plans collection
        assert resolved_plan.retention is not None
        err_console.print("Updating retirement plan:")
        err_console.print(f"  Plan:      {resolved_plan.name} ({resolved_plan.plan_id})")
        err_console.print(f"  Retention: {fmt_retirement_retention(resolved_plan.retention)}")
    else:
        # always populated for a plan resolved via the plans/retirement_plans collection
        assert resolved_plan.policy is not None
        err_console.print("Applying protection plan:")
        err_console.print(f"  Plan:      {resolved_plan.name} ({resolved_plan.plan_id})")
        err_console.print(f"  Retention: {fmt_retention(resolved_plan.policy.retention)}")
        schedule_label = fmt_schedule_label(resolved_plan.policy)
        if schedule_label:
            err_console.print(f"  Schedule:  {schedule_label}")

    label = label_fn(wl)
    type_part = f"{label}, " if label else ""
    err_console.print(f"  Workload:  {wl.name} ({type_part}ID: {wl.workload_id})")
    err_console.print(f"\n[yellow]⚠[/yellow] Current plan: {wl.plan.name} -> {resolved_plan.name}")

    if not yes:
        typer.confirm("\nConfirm change plan?", abort=True)

    await change_plan_fn(wl, resolved_plan)
    if not quiet:
        console.print(f"[green]✓[/green] Plan changed: {wl.name}")


async def _cancel_activity(
    collection: BackupActivityCollection | RestoreActivityCollection,
    activity_id: str,
    noun: str,
    *,
    yes: bool,
    quiet: bool,
) -> None:
    """Shared body of `activity backup cancel` / `activity restore cancel`.

    Locates the running activity by ID, confirms (unless --yes), cancels it,
    and prints the success line (unless --quiet).
    """
    activities, _ = await collection.list(limit=500)
    target = next((a for a in activities if a.activity_id == activity_id), None)

    if not target:
        err_console.print("[red]✗[/red] Running activity not found, or it has already completed.")
        raise typer.Exit(code=1)

    if not yes:
        err_console.print(f"[yellow]⚠[/yellow] Confirm cancel {noun} activity?")
        err_console.print(f"  Activity:  {activity_id}")
        err_console.print(f"  Workload:  {target.workload_name}")
        err_console.print(f"  Started:   {fmt_datetime(target.started_at)}")
        if target.items_processed is not None:
            err_console.print(f"  Progress:  {target.items_processed} items")
        else:
            err_console.print(f"  Progress:  {target.progress}%")
        typer.confirm("\n  Confirm?", abort=True)

    await collection.cancel(target)  # type: ignore[arg-type]
    if not quiet:
        console.print(f"[green]✓[/green] {noun.capitalize()} cancelled.")
