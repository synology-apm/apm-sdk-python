"""CLI argument validation and workload resolution helpers."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TypeVar

import typer

from synology_apm.cli._display import fmt_datetime
from synology_apm.cli.errors import EXIT_ERROR, err_console
from synology_apm.sdk import (
    APMActivityLogType,
    APMClient,
    BackupActivityStatus,
    BackupServerType,
    GWSDomainInfo,
    GWSWorkload,
    GWSWorkloadType,
    LogLevel,
    M365TenantInfo,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    MachineWorkloadType,
    ProtectionPlan,
    RestoreActivityStatus,
    RetirementPlan,
    SaasApplication,
    ServerStatus,
    TieringPlan,
    VerifyStatus,
    WorkloadCategory,
    WorkloadStatus,
    WorkloadVersion,
)

_T = TypeVar("_T")
_E = TypeVar("_E", bound=Enum)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _enum_args(enum_cls: type[_E], *, exclude: frozenset[_E] = frozenset()) -> dict[str, _E]:
    """Build a CLI arg-name -> enum-member dict from every member's .value, skipping `exclude`."""
    return {e.value: e for e in enum_cls if e not in exclude}


MACHINE_TYPE_ARGS: dict[str, MachineWorkloadType] = _enum_args(MachineWorkloadType)

# Shared by `m365 <scope> list`'s sub-app registration and `activity backup list --m365-type`.
M365_TYPE_ARGS: dict[str, M365WorkloadType] = _enum_args(M365WorkloadType)

# `activity backup list --gws-type`. Not shared with `gws.py`'s own sub-app registration —
# that keeps a separate, hyphenated _TYPE_MAP ("shared-drive", the CLI command-path spelling)
# distinct from this dict's underscored enum-value spelling ("shared_drive").
GWS_TYPE_ARGS: dict[str, GWSWorkloadType] = _enum_args(GWSWorkloadType)

# Shared by `machine list --status` and `m365 <scope> list --status`.
# RETIRED is excluded — already governed by the --retired flag, not a filterable status value.
WORKLOAD_STATUS_ARGS: dict[str, WorkloadStatus] = _enum_args(
    WorkloadStatus, exclude=frozenset({WorkloadStatus.RETIRED})
)

# `machine list --verify-status` (backup verification status is a Machine-specific concept).
VERIFY_STATUS_ARGS: dict[str, VerifyStatus] = _enum_args(VerifyStatus)

# `activity backup list --status`.
BACKUP_ACTIVITY_STATUS_ARGS: dict[str, BackupActivityStatus] = _enum_args(BackupActivityStatus)

# `activity restore list --status`.
RESTORE_ACTIVITY_STATUS_ARGS: dict[str, RestoreActivityStatus] = _enum_args(RestoreActivityStatus)

# `infra server list --status`.
SERVER_STATUS_ARGS: dict[str, ServerStatus] = _enum_args(ServerStatus)

# `infra server list --type`.
BACKUP_SERVER_TYPE_ARGS: dict[str, BackupServerType] = _enum_args(BackupServerType)

# `log * list --level`.
LOG_LEVEL_ARGS: dict[str, LogLevel] = _enum_args(LogLevel)

# `log activity list --type`.
APM_ACTIVITY_LOG_TYPE_ARGS: dict[str, APMActivityLogType] = _enum_args(APMActivityLogType)

# `plan protection list --category`.
WORKLOAD_CATEGORY_ARGS: dict[str, WorkloadCategory] = _enum_args(WorkloadCategory)


_NO_SAAS_TENANT_MESSAGE: dict[WorkloadCategory, str] = {
    WorkloadCategory.M365: "No M365 tenant found. Add a tenant in the APM UI or specify --tenant-id.",
    WorkloadCategory.GWS:  "No GWS domain found. Add a domain in the APM UI or specify --domain.",
}


def saas_application_id(application: SaasApplication) -> str:
    """Return the identifier: tenant_id for M365, domain for GWS (GWS has no separate ID)."""
    if isinstance(application, M365TenantInfo):
        return application.tenant_id
    assert isinstance(application, GWSDomainInfo)
    return application.domain


async def _resolve_saas_tenant_id(
    apm: APMClient, tenant_id: str | None, category: WorkloadCategory = WorkloadCategory.M365
) -> str:
    """Return a valid tenant_id (M365) or domain (GWS); if not provided, take the first
    matching application from saas.list()."""
    if tenant_id is not None:
        return tenant_id
    apps, _ = await apm.saas.list()
    matched = [a for a in apps if a.category == category]
    if not matched:
        err_console.print(f"[red]✗[/red] {_NO_SAAS_TENANT_MESSAGE[category]}")
        raise typer.Exit(code=EXIT_ERROR)
    return saas_application_id(matched[0])


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


def print_resolved_saas_tenant(cli_tenant_id: str | None, resolved_tenant_id: str, label: str = "tenant") -> None:
    """Inform the user which tenant/domain was auto-selected when its ID option was not given.

    ``label`` names the resolved scope in the printed message ("tenant" for M365,
    "domain" for GWS).
    """
    if cli_tenant_id is None:
        err_console.print(f"[bright_black](Using {label}: {resolved_tenant_id})[/bright_black]")


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
        """Resolve to a MachineWorkload via get() (direct mode) or get_by_name() (search mode).

        is_retired only applies in search mode; direct mode looks up the workload by ID
        regardless of its retirement state.
        """
        if self.namespace is not None:
            return await apm.machine.workloads.get(self.identifier, namespace=self.namespace)
        return await apm.machine.workloads.get_by_name(self.identifier, is_retired=is_retired)

    async def resolve_m365(
        self, apm: APMClient, tenant_id: str | None, workload_type: M365WorkloadType, is_retired: bool = False
    ) -> M365Workload:
        """Resolve to an M365Workload via get() (direct mode) or get_by_name() (search mode).

        ``tenant_id`` is resolved automatically (falling back to the first M365 tenant) if not
        provided. is_retired only applies in search mode; direct mode looks up the workload by
        ID regardless of its retirement state.
        """
        tid = await _resolve_saas_tenant_id(apm, tenant_id)
        if self.namespace is not None:
            return await apm.m365.workloads.get(
                self.identifier, self.namespace, tenant_id=tid, workload_type=workload_type
            )
        return await apm.m365.workloads.get_by_name(
            self.identifier, tid, workload_type=workload_type, is_retired=is_retired
        )

    async def resolve_gws(
        self, apm: APMClient, domain: str | None, workload_type: GWSWorkloadType, is_retired: bool = False
    ) -> GWSWorkload:
        """Resolve to a GWSWorkload via get() (direct mode) or get_by_name() (search mode).

        ``domain`` is resolved automatically (falling back to the first GWS domain) if not
        provided. is_retired only applies in search mode; direct mode looks up the workload by
        ID regardless of its retirement state.
        """
        did = await _resolve_saas_tenant_id(apm, domain, category=WorkloadCategory.GWS)
        if self.namespace is not None:
            return await apm.gws.workloads.get(
                self.identifier, self.namespace, domain=did, workload_type=workload_type
            )
        return await apm.gws.workloads.get_by_name(
            self.identifier, did, workload_type=workload_type, is_retired=is_retired
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
            raise typer.Exit(code=EXIT_ERROR)
        if name is not None:
            err_console.print(f"[red]✗[/red] <name> cannot be used with {id_flag} / --namespace")
            raise typer.Exit(code=EXIT_ERROR)
    else:
        if namespace is not None:
            err_console.print(f"[red]✗[/red] --namespace requires {id_flag}")
            raise typer.Exit(code=EXIT_ERROR)
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
            raise typer.Exit(code=EXIT_ERROR)
    elif workload_id is not None:
        if namespace is None:
            err_console.print("[red]✗[/red] --workload-id requires --namespace")
            raise typer.Exit(code=EXIT_ERROR)
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
        raise typer.Exit(code=EXIT_ERROR)
    if name is None and resource_id is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


async def resolve_by_name_or_id(
    name: str | None,
    resource_id: str | None,
    get_by_id: Callable[[str], Awaitable[_T]],
    get_by_name: Callable[[str], Awaitable[_T]],
) -> _T:
    """Resolve to a resource via get_by_id() (--id) or get_by_name() (name).

    Call only after validate_name_or_id_args() has confirmed that exactly one of
    ``name`` or ``resource_id`` is set.
    """
    if resource_id is not None:
        return await get_by_id(resource_id)
    assert name is not None, "resolve_by_name_or_id requires name or resource_id (validate args first)"
    return await get_by_name(name)


def parse_enum_list(
    values: list[str] | None,
    mapping: dict[str, _T],
    option_name: str,
) -> list[_T] | None:
    """Validate and convert a list of CLI string values to enum instances.

    Returns None when values is empty or None; exits with code 1 on unknown value. The
    "available" hint shown on an unsupported value is derived from ``mapping``'s keys.
    """
    if not values:
        return None
    result: list[_T] = []
    for v in values:
        enum_val = mapping.get(v.lower())
        if enum_val is None:
            hint = ", ".join(mapping)
            err_console.print(f"[red]✗[/red] Unsupported {option_name} value: {v} (available: {hint})")
            raise typer.Exit(code=EXIT_ERROR)
        result.append(enum_val)
    return result


def parse_enum_scalar(
    value: str | None,
    mapping: dict[str, _T],
    option_name: str,
) -> _T | None:
    """Scalar counterpart to parse_enum_list(); same validation and error UX for a
    single-value enum option. Returns None when value is None."""
    result = parse_enum_list([value] if value is not None else None, mapping, option_name)
    return result[0] if result else None


def parse_time_filter(value: str) -> datetime:
    """Parse a --since / --until time-filter argument; supports ISO 8601 and relative times (30m, 1h, 24h, 7d)."""
    now = datetime.now(tz=UTC)
    value = value.strip()
    _bad_format = typer.BadParameter(
        f"Cannot parse time format: {value!r}. "
        "Supported: ISO 8601 (e.g. 2026-04-01) or relative (e.g. 30m, 1h, 24h, 7d)."
    )
    unit_to_field = {"h": "hours", "d": "days", "m": "minutes"}
    if value and value[-1] in unit_to_field:
        try:
            amount = float(value[:-1])
        except ValueError:
            raise _bad_format from None
        return now - timedelta(**{unit_to_field[value[-1]]: amount})
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        raise _bad_format from None


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
