"""CLI argument validation and workload resolution helpers."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import typer

from synology_apm.cli._display import fmt_datetime
from synology_apm.cli.errors import err_console
from synology_apm.sdk import (
    APMClient,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    MachineWorkloadType,
    ProtectionPlan,
    RetirementPlan,
    TieringPlan,
    WorkloadCategory,
    WorkloadVersion,
)

_T = TypeVar("_T")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

MACHINE_TYPE_ARGS: dict[str, MachineWorkloadType] = {
    "pc": MachineWorkloadType.PC,
    "ps": MachineWorkloadType.PS,
    "vm": MachineWorkloadType.VM,
    "fs": MachineWorkloadType.FS,
}


async def _resolve_tenant(apm: APMClient, tenant_id: str | None) -> str:
    """Return a valid tenant_id; if not provided, take the first M365 tenant from saas.list()."""
    if tenant_id is not None:
        return tenant_id
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if t.category == WorkloadCategory.M365]
    if not m365:
        err_console.print("[red]✗[/red] No M365 tenant found. Add a tenant in the APM UI or specify --tenant-id.")
        raise typer.Exit(code=1)
    return m365[0].tenant_id


async def _resolve_tiering_plan(apm: APMClient, plan_arg: str) -> TieringPlan:
    """Resolve a --plan argument (UUID or name) to a TieringPlan."""
    return (
        await apm.tiering_plans.get(plan_arg)
        if _UUID_RE.match(plan_arg)
        else await apm.tiering_plans.get_by_name(plan_arg)
    )


async def _resolve_plan(apm: APMClient, plan_arg: str, *, is_retired: bool) -> ProtectionPlan | RetirementPlan:
    """Resolve a --plan argument (UUID or name) to a plan object.

    Resolves against Retirement Plans when is_retired is True, Protection Plans otherwise.
    """
    if is_retired:
        return (
            await apm.retirement_plans.get(plan_arg)
            if _UUID_RE.match(plan_arg)
            else await apm.retirement_plans.get_by_name(plan_arg)
        )
    return (
        await apm.plans.get(plan_arg) if _UUID_RE.match(plan_arg) else await apm.plans.get_by_name(plan_arg)
    )


async def _resolve_plans(
    apm: APMClient, plan_args: list[str] | None, *, is_retired: bool
) -> list[ProtectionPlan | RetirementPlan] | None:
    """Resolve each --plan argument via _resolve_plan(); None if plan_args is None or empty."""
    if not plan_args:
        return None
    return await asyncio.gather(*(_resolve_plan(apm, p, is_retired=is_retired) for p in plan_args))


def print_resolved_tenant(cli_tenant_id: str | None, resolved_tenant_id: str) -> None:
    """Inform the user which tenant was auto-selected when --tenant-id was not given."""
    if cli_tenant_id is None:
        err_console.print(f"[bright_black](Using tenant: {resolved_tenant_id})[/bright_black]")


def print_resolved_version(cli_version_id: str | None, resolved_version: WorkloadVersion) -> None:
    """Inform the user which version was auto-selected when --id/--version-id was not given."""
    if cli_version_id is None:
        err_console.print(
            f"[bright_black](Using version: {resolved_version.version_id}, "
            f"created at {fmt_datetime(resolved_version.created_at)})[/bright_black]"
        )


@dataclass(frozen=True)
class WorkloadRef:
    """Resolved workload identification for search / direct-mode commands.

    ``identifier`` is the name (search mode) or the workload_id (direct mode) and is
    always set; ``namespace`` is populated only in direct mode.
    """

    identifier: str
    namespace: str | None
    is_direct: bool

    async def resolve_machine(self, apm: APMClient, is_retired: bool = False) -> MachineWorkload:
        """Resolve to a MachineWorkload via get() (direct mode) or get_by_name() (search mode)."""
        if self.namespace is not None:
            return await apm.machine.workloads.get(self.identifier, namespace=self.namespace)
        return await apm.machine.workloads.get_by_name(self.identifier, is_retired=is_retired)

    async def resolve_m365(
        self, apm: APMClient, tenant_id: str | None, workload_type: M365WorkloadType, is_retired: bool = False
    ) -> M365Workload:
        """Resolve to an M365Workload via get() (direct mode) or get_by_name() (search mode).

        ``tenant_id`` is resolved automatically (falling back to the first M365 tenant) if not provided.
        """
        tid = await _resolve_tenant(apm, tenant_id)
        if self.namespace is not None:
            return await apm.m365.workloads.get(
                self.identifier, self.namespace, tenant_id=tid, workload_type=workload_type
            )
        return await apm.m365.workloads.get_by_name(
            self.identifier, tid, workload_type=workload_type, is_retired=is_retired
        )


def workload_ref(name: str | None, workload_id: str | None, namespace: str | None) -> WorkloadRef:
    """Build a WorkloadRef from search / direct args.

    Call only after one of the ``validate_*`` helpers has confirmed that exactly one of
    ``name`` or ``workload_id`` is set (with ``namespace`` accompanying ``workload_id``).
    The assertion encodes that invariant so callers get a non-optional ``identifier``.
    """
    if workload_id is not None:
        return WorkloadRef(workload_id, namespace, is_direct=True)
    assert name is not None, "workload_ref requires name or workload_id (validate args first)"
    return WorkloadRef(name, None, is_direct=False)


def validate_resolve_args(
    ctx: typer.Context,
    name: str | None,
    workload_id: str | None,
    namespace: str | None,
    *,
    id_flag: str = "--id",
) -> WorkloadRef:
    """Validate search / direct mode args and return the resolved WorkloadRef.

    Prints an error and Exit(1) on invalid input, or help and Exit(0) when nothing is given.
    id_flag controls the option name used in error messages (e.g. ``--id`` or ``--workload-id``).
    """
    if workload_id is not None:
        if namespace is None:
            err_console.print(f"[red]✗[/red] {id_flag} requires --namespace")
            raise typer.Exit(code=1)
        if name is not None:
            err_console.print(f"[red]✗[/red] <name> cannot be used with {id_flag} / --namespace")
            raise typer.Exit(code=1)
    else:
        if namespace is not None:
            err_console.print(f"[red]✗[/red] --namespace requires {id_flag}")
            raise typer.Exit(code=1)
        if name is None:
            typer.echo(ctx.get_help())
            raise typer.Exit(0)
    return workload_ref(name, workload_id, namespace)


def validate_version_workload_args(
    ctx: typer.Context,
    name: str | None,
    workload_id: str | None,
    namespace: str | None,
) -> WorkloadRef:
    """Validate workload identification args for version commands and return the WorkloadRef.

    Prints help/error and Exit on invalid input.
    """
    if name is not None:
        if workload_id is not None or namespace is not None:
            err_console.print("[red]✗[/red] <name> cannot be used with --workload-id / --namespace")
            raise typer.Exit(code=1)
    elif workload_id is not None:
        if namespace is None:
            err_console.print("[red]✗[/red] --workload-id requires --namespace")
            raise typer.Exit(code=1)
    else:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    return workload_ref(name, workload_id, namespace)


def validate_version_lock_args(
    ctx: typer.Context,
    name: str | None,
    workload_id: str | None,
    namespace: str | None,
    version_id: str | None,
) -> tuple[WorkloadRef, str]:
    """Validate version lock/unlock args; return the WorkloadRef and the narrowed version_id."""
    ref = validate_version_workload_args(ctx, name, workload_id, namespace)
    if version_id is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    return ref, version_id


def validate_activity_args(
    ctx: typer.Context,
    name: str | None,
    activity_id: str | None,
) -> None:
    """Print help and exit when neither name nor activity_id is provided."""
    if name is None and activity_id is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


def validate_name_or_id_args(
    ctx: typer.Context,
    name: str | None,
    resource_id: str | None,
    *,
    exclusive_msg: str = "NAME and --id are mutually exclusive",
) -> None:
    """Validate mutually-exclusive NAME / --id args; print help/error and Exit on invalid input.

    exclusive_msg overrides the error text shown when both NAME and --id are given.
    """
    if name is not None and resource_id is not None:
        err_console.print(f"[red]✗[/red] {exclusive_msg}")
        raise typer.Exit(code=1)
    if name is None and resource_id is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


def parse_time_filter(value: str) -> datetime:
    """Parse a --since / --until time-filter argument; supports ISO 8601 and relative times (1h, 24h, 7d)."""
    now = datetime.now(tz=UTC)
    value = value.strip()
    if value.endswith("h"):
        return now - timedelta(hours=float(value[:-1]))
    if value.endswith("d"):
        return now - timedelta(days=float(value[:-1]))
    if value.endswith("m"):
        return now - timedelta(minutes=float(value[:-1]))
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        raise typer.BadParameter(
            f"Cannot parse time format: {value!r}. "
            "Supported: ISO 8601 (e.g. 2026-04-01) or relative (e.g. 1h, 24h, 7d)."
        )


def parse_time_range(
    since: str | None, until: str | None
) -> tuple[datetime | None, datetime | None]:
    """Parse optional --since / --until values via parse_time_filter; None passes through."""
    return (
        parse_time_filter(since) if since else None,
        parse_time_filter(until) if until else None,
    )


def require_or_help(ctx: typer.Context, value: _T | None) -> _T:
    """Return value, or print the command help and exit 0 when it is missing (None)."""
    if value is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    return value
