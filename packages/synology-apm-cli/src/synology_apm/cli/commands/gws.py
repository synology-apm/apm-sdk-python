"""synology-apm-cli gws — Google Workspace backup resource management commands.

Command structure:
  synology-apm-cli gws (mail|calendar|contact|drive|shared-drive)
    list/get/backup/cancel/retire/change-plan/version (all types)

get / backup / cancel / retire / change-plan / version support two modes:
  Search mode: <identifier> -d <domain>     (email, or Shared Drive name for shared-drive)
  Direct mode: --id <uid> --namespace <ns>  (exact lookup; version subcommands
               use --workload-id because --id refers to the version there)

No `export` sub-app exists here — GWS backups have no mailbox-export operation.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    _GWS_INFO_COL_LABELS,
    _GWS_WORKLOAD_TYPE_DISPLAY,
    fmt_backup_copy,
    fmt_backup_server,
    fmt_bytes,
    fmt_datetime,
    fmt_workload_status,
    print_list_footer,
    print_workload_detail,
)
from synology_apm.cli._helpers import api_spinner, apm_session
from synology_apm.cli._options import (
    LIMIT_OPTION,
    LIST_OUTPUT_OPTION,
    OFFSET_OPTION,
    OUTPUT_OPTION,
    PAGE_ALL_OPTION,
    SEARCH_OPTION,
    SINCE_OPTION,
    UNTIL_OPTION,
    VERSION_LIMIT_OPTION,
)
from synology_apm.cli._serializers import (
    gws_workload_to_csv_row,
    gws_workload_to_dict,
)
from synology_apm.cli._validate import (
    WORKLOAD_STATUS_ARGS,
    WorkloadRef,
    _resolve_plans,
    _resolve_saas_tenant_id,
    parse_enum_list,
    parse_time_range,
    print_resolved_saas_tenant,
    require_or_help,
    validate_resolve_args,
    validate_version_lock_args,
    validate_version_workload_args,
)
from synology_apm.cli.commands._actions import (
    _do_backup,
    _do_cancel,
    _do_change_plan,
    _do_retire,
    _do_version_get,
    _do_version_list,
    _do_version_lock_unlock,
)
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
    APMClient,
    GWSDomainInfo,
    GWSWorkload,
    GWSWorkloadType,
    WorkloadCategory,
)

app = typer.Typer(
    help="Manage Google Workspace backup resources.",
    no_args_is_help=True,
)

_TYPE_LABELS: dict[str, str] = {
    "mail":         "Mail",
    "calendar":     "Calendar",
    "contact":      "Contact",
    "drive":        "Drive",
    "shared-drive": "Shared Drive",
}

_TYPE_MAP: dict[str, GWSWorkloadType] = {
    "mail":         GWSWorkloadType.MAIL,
    "calendar":     GWSWorkloadType.CALENDAR,
    "contact":      GWSWorkloadType.CONTACT,
    "drive":        GWSWorkloadType.DRIVE,
    "shared-drive": GWSWorkloadType.SHARED_DRIVE,
}

# <name> positional argument description for get / backup / cancel / retire / version
_TYPE_SEARCH_ARG: dict[str, str] = {
    "mail":         "Email (search mode, e.g. alice@gwsdemo.example.com)",
    "calendar":     "Email (search mode, e.g. alice@gwsdemo.example.com)",
    "contact":      "Email (search mode, e.g. alice@gwsdemo.example.com)",
    "drive":        "Email (search mode, e.g. alice@gwsdemo.example.com)",
    "shared-drive": "Shared Drive name (search mode)",
}

# Example values for each workload_type in search mode (used in --help text)
_TYPE_EXAMPLE: dict[str, str] = {
    "mail":         '"alice@gwsdemo.example.com"',
    "calendar":     '"alice@gwsdemo.example.com"',
    "contact":      '"alice@gwsdemo.example.com"',
    "drive":        '"alice@gwsdemo.example.com"',
    "shared-drive": '"Marketing Drive"',
}

_DOMAIN_OPTION = typer.Option(
    None, "--domain", "-d",
    help="GWS domain (from synology-apm-cli saas list; omit to auto-use the first GWS domain)",
)


def _info_value(wl: GWSWorkload) -> str:
    """Return the identifying value shown in the info column: backup_user for Shared Drive,
    the info label (email) for every other sub-type."""
    if wl.workload_type == GWSWorkloadType.SHARED_DRIVE:
        return wl.backup_user or "-"
    return wl.info.label if wl.info else "-"


def _make_type_app(type_name: str, type_val: GWSWorkloadType) -> typer.Typer:
    """Build a Typer sub-app for the given GWS service sub-type, with list/get/backup/cancel/retire commands."""
    label = _TYPE_LABELS[type_name]
    info_col = _GWS_INFO_COL_LABELS[type_val]
    search_arg_help = _TYPE_SEARCH_ARG[type_name]
    example = _TYPE_EXAMPLE[type_name]

    type_app = typer.Typer(
        help=f"Manage GWS {label} workloads.",
        no_args_is_help=True,
    )

    async def _get_workload(
        apm: APMClient, ref: WorkloadRef, domain: str | None, is_retired: bool
    ) -> GWSWorkload:
        """Resolve the workload via get() (--id/--namespace) or get_by_name() (name)."""
        wl = await ref.resolve_gws(apm, domain, type_val, is_retired=is_retired)
        print_resolved_saas_tenant(domain, wl.domain, label="domain")
        return wl

    # ── list ──────────────────────────────────────────────────────────────

    @type_app.command("list")
    @run_async
    async def _list(
        ctx: typer.Context,
        domain: str | None = _DOMAIN_OPTION,
        retired: bool = typer.Option(
            False, "--retired", help="Show only retired workloads (default: show protected only)"
        ),
        search: str | None = SEARCH_OPTION,
        namespace: list[str] | None = typer.Option(
            None, "--namespace", "-n",
            help=(
                "Show only workloads on the given backup server(s), repeatable "
                "(get namespace from synology-apm-cli infra server list --verbose)"
            ),
        ),
        plan: list[str] | None = typer.Option(
            None, "--plan",
            help=(
                "Plan name or ID (repeatable). Resolved against Protection Plans, or Retirement "
                "Plans if --retired is set."
            ),
        ),
        status: list[str] | None = typer.Option(
            None, "--status",
            help=(
                "Backup status filter, repeatable: queuing / backing_up / success / failed / "
                "partial / canceled / no_backups / deleting"
            ),
        ),
        limit: int = LIMIT_OPTION,
        offset: int = OFFSET_OPTION,
        page_all: bool = PAGE_ALL_OPTION,
        output: ListOutputFormat = LIST_OUTPUT_OPTION,
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
    ) -> None:
        """List GWS Workloads of this type."""
        status_enums = parse_enum_list(status, WORKLOAD_STATUS_ARGS, "status")
        async with apm_session(ctx) as apm:
            did = await _resolve_saas_tenant_id(apm, domain, category=WorkloadCategory.GWS)
            print_resolved_saas_tenant(domain, did, label="domain")
            resolved_plans = await _resolve_plans(apm, plan, is_retired=retired)
            with api_spinner("Fetching workloads..."):
                list_coro = dispatch_paginated_list(
                    lambda off, lim: apm.gws.workloads.list(
                        did, workload_type=type_val, keyword=search,
                        namespace=namespace, is_retired=retired, plan=resolved_plans,
                        status=status_enums,
                        limit=lim, offset=off,
                    ),
                    limit=limit, offset=offset, page_all=page_all, output=output,
                    to_dict=gws_workload_to_dict, to_csv_row=gws_workload_to_csv_row,
                )
                if output == ListOutputFormat.TABLE:
                    domain_info, result = await asyncio.gather(
                        apm.saas.get_gws_domain(did), list_coro,
                    )
                else:
                    domain_info = None
                    result = await list_coro

        if result is None:
            return
        workloads, total = result
        _print_domain_header(domain_info)
        _print_workload_table(workloads, verbose=verbose, info_col=info_col, retired=retired)
        print_list_footer(console, len(workloads), total, offset)

    # ── get ───────────────────────────────────────────────────────────────

    @type_app.command("get", help=(
        f"Show details for a GWS Workload.\n\n"
        f"\b\nSearch mode (searches protected workloads by default):\n"
        f"  synology-apm-cli gws {type_name} get {example}\n"
        f"  synology-apm-cli gws {type_name} get {example} --retired\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} get --id <uid> --namespace <ns>"
    ))
    @run_async
    async def _get(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode; requires --namespace)"),
        namespace: str | None = typer.Option(
            None, "--namespace", "-n", help="Backup server namespace (direct mode; requires --id)"
        ),
        domain: str | None = _DOMAIN_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        output: OutputFormat = OUTPUT_OPTION,
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        async with apm_session(ctx) as apm:
            wl = await _get_workload(apm, ref, domain, is_retired=retired)

        if dispatch_output(wl, output, gws_workload_to_dict):
            return
        _print_workload_detail(wl)

    # ── backup ────────────────────────────────────────────────────────────

    @type_app.command("backup", help=(
        f"Trigger an on-demand backup for a GWS Workload.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli gws {type_name} backup {example}\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} backup --id <uid> --namespace <ns>"
    ))
    @run_async
    async def _backup(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        async with apm_session(ctx) as apm:
            await _do_backup(
                lambda: _get_workload(apm, ref, domain, is_retired=False),
                apm.gws.workloads.backup_now,
                quiet=quiet,
            )

    # ── cancel ────────────────────────────────────────────────────────────

    @type_app.command("cancel", help=(
        f"Cancel the running backup for a GWS Workload.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli gws {type_name} cancel {example}\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} cancel --id <uid> --namespace <ns>"
    ))
    @run_async
    async def _cancel(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        async with apm_session(ctx, abortable=True) as apm:
            await _do_cancel(
                lambda: _get_workload(apm, ref, domain, is_retired=False),
                apm.gws.workloads.cancel_backup,
                lambda wl: None,
                yes=yes,
                quiet=quiet,
            )

    # ── retire ────────────────────────────────────────────────────────────

    @type_app.command("retire", help=(
        f"Retire a GWS Workload (irreversible; requires confirmation).\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli gws {type_name} retire {example} --plan \"Compliance Retention\"\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} retire --id <uid> --namespace <ns> --plan <plan-id>"
    ))
    @run_async
    async def _retire(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        plan: str | None = typer.Option(
            None,
            "--plan",
            help="Retirement Plan name or ID (required). Resolved against Retirement Plans.",
        ),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt (destructive, irreversible)"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        plan = require_or_help(ctx, plan)
        async with apm_session(ctx, abortable=True) as apm:
            did = await _resolve_saas_tenant_id(apm, domain, category=WorkloadCategory.GWS)
            print_resolved_saas_tenant(domain, did, label="domain")
            await _do_retire(
                lambda: ref.resolve_gws(apm, did, type_val, is_retired=False),
                lambda: apm.gws.workloads.get_by_name(
                    ref.identifier, did, workload_type=type_val, is_retired=True
                ),
                apm.gws.workloads.retire,
                lambda wl: None,
                apm=apm,
                is_direct=ref.is_direct,
                plan_arg=plan,
                resource_type="GWSWorkload",
                yes=yes,
                quiet=quiet,
            )

    # ── change-plan ──────────────────────────────────────────────────────

    @type_app.command("change-plan", help=(
        f"Change the Protection Plan or Retirement Plan assigned to a GWS Workload.\n\n"
        "The plan type --plan is resolved against is auto-detected from the workload's current "
        "state: a Protection Plan for an active workload, a Retirement Plan for an "
        "already-retired one.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli gws {type_name} change-plan {example} --plan \"Daily Backup\"\n"
        f"  synology-apm-cli gws {type_name} change-plan {example} --retired --plan \"Compliance Retention\"\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} change-plan --id <uid> --namespace <ns> --plan <plan-id>"
    ))
    @run_async
    async def _change_plan(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        plan: str | None = typer.Option(
            None, "--plan",
            help="Plan name or ID (required); see the command description for how the plan type is resolved.",
        ),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        plan = require_or_help(ctx, plan)
        async with apm_session(ctx, abortable=True) as apm:
            await _do_change_plan(
                lambda: _get_workload(apm, ref, domain, is_retired=retired),
                apm.gws.workloads.change_plan,
                lambda wl: None,
                apm=apm,
                plan_arg=plan,
                yes=yes,
                quiet=quiet,
            )

    # ── version ───────────────────────────────────────────────────────────

    version_app = typer.Typer(help=f"Manage GWS {label} backup versions.", no_args_is_help=True)
    type_app.add_typer(version_app, name="version")

    @version_app.command("list", help=(
        f"List backup version history for a GWS Workload.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli gws {type_name} version list {example}\n"
        f"  synology-apm-cli gws {type_name} version list {example} --retired\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} version list --workload-id <workload-id> --namespace <ns>"
    ))
    @run_async
    async def _version_list(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        limit: int = VERSION_LIMIT_OPTION,
        offset: int = OFFSET_OPTION,
        page_all: bool = PAGE_ALL_OPTION,
        since: str | None = SINCE_OPTION,
        until: str | None = UNTIL_OPTION,
        output: ListOutputFormat = LIST_OUTPUT_OPTION,
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace, id_flag="--workload-id")
        since_dt, until_dt = parse_time_range(since, until)
        async with apm_session(ctx) as apm:
            await _do_version_list(
                lambda: _get_workload(apm, ref, domain, is_retired=retired),
                apm.gws.workloads.list_versions,
                None,
                limit=limit, offset=offset, page_all=page_all, since=since_dt, until=until_dt,
                output=output, verbose=verbose,
            )

    @version_app.command("get", help=(
        f"Show activity details and logs for a backup version (omit --id to get the latest).\n\n"
        f"\b\nSearch mode (by workload name; omit --id for latest):\n"
        f"  synology-apm-cli gws {type_name} version get {example}\n"
        f"  synology-apm-cli gws {type_name} version get {example} --id <version-id>\n"
        f"  synology-apm-cli gws {type_name} version get {example} --id <version-id> --retired\n\n"
        f"\b\nDirect mode (--domain not required; omit --id for latest):\n"
        f"  synology-apm-cli gws {type_name} version get --workload-id <wl-id> --namespace <ns>\n"
        f"  synology-apm-cli gws {type_name} version get --workload-id <wl-id> --namespace <ns> --id <version-id>"
    ))
    @run_async
    async def _version_get(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        version_id: str | None = typer.Option(
            None, "--id", help="Version ID (from version list; omit to get the latest)"
        ),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        output: OutputFormat = OUTPUT_OPTION,
    ) -> None:
        ref = validate_version_workload_args(ctx, name, workload_id, namespace)

        async with apm_session(ctx) as apm:
            await _do_version_get(
                lambda: _get_workload(apm, ref, domain, is_retired=retired),
                apm.gws.workloads.get_version,
                apm.gws.workloads.get_latest_version,
                apm=apm,
                version_id=version_id,
                output=output,
            )

    @version_app.command("lock", help=(
        f"Lock a backup version to prevent deletion by retention rules.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli gws {type_name} version lock {example} --id <ver-id>\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} version lock --workload-id <wl-id> --namespace <ns> --id <ver-id>"
    ))
    @run_async
    async def _version_lock(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        version_id: str | None = typer.Option(None, "--id", help="Version ID (from version list)"),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref, version_id = validate_version_lock_args(ctx, name, workload_id, namespace, version_id)
        async with apm_session(ctx) as apm:
            await _do_version_lock_unlock(
                lambda: _get_workload(apm, ref, domain, is_retired=retired),
                apm.gws.workloads.get_version,
                apm.gws.workloads.lock_version,
                apm.gws.workloads.unlock_version,
                version_id=version_id,
                lock=True,
            )
        if not quiet:
            console.print(f"[green]✓[/green] Version locked: {version_id}")

    @version_app.command("unlock", help=(
        f"Unlock a backup version, allowing retention rules to delete it.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli gws {type_name} version unlock {example} --id <ver-id>\n\n"
        f"\b\nDirect mode (--domain not required):\n"
        f"  synology-apm-cli gws {type_name} version unlock --workload-id <wl-id> --namespace <ns> --id <ver-id>"
    ))
    @run_async
    async def _version_unlock(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        version_id: str | None = typer.Option(None, "--id", help="Version ID (from version list)"),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        domain: str | None = _DOMAIN_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref, version_id = validate_version_lock_args(ctx, name, workload_id, namespace, version_id)
        async with apm_session(ctx) as apm:
            await _do_version_lock_unlock(
                lambda: _get_workload(apm, ref, domain, is_retired=retired),
                apm.gws.workloads.get_version,
                apm.gws.workloads.lock_version,
                apm.gws.workloads.unlock_version,
                version_id=version_id,
                lock=False,
            )
        if not quiet:
            console.print(f"[green]✓[/green] Version unlocked: {version_id}")

    return type_app


# ── Register all scope sub-apps ───────────────────────────────────────────

for _type_name, _type_val in _TYPE_MAP.items():
    app.add_typer(_make_type_app(_type_name, _type_val), name=_type_name)


# ── Formatting helpers ────────────────────────────────────────────────────

def _print_domain_header(domain_info: GWSDomainInfo | None) -> None:
    if domain_info is None:
        return
    name = domain_info.name or "-"
    domain = domain_info.domain or "-"
    console.print(f"Domain: [bold]{name}[/bold] ({domain})")
    console.print()


def _print_workload_table(
    workloads: Sequence[GWSWorkload], verbose: bool = False, info_col: str = "Email", retired: bool = False
) -> None:
    t = new_table()
    t.add_column("Name", min_width=16)
    t.add_column(info_col, min_width=20)
    if not retired:
        t.add_column("Status", min_width=12)
    t.add_column("Last Backup", min_width=19)
    t.add_column("Protected Size", min_width=14)
    t.add_column("Copy Size", min_width=9)
    t.add_column("Protection Plan", min_width=14)
    t.add_column("Backup Server", min_width=12)
    t.add_column("Copy Destination", min_width=16)
    if verbose:
        t.add_column("Workload ID", min_width=36)
        t.add_column("Namespace", min_width=36)
        t.add_column("Plan ID", min_width=36)

    for wl in workloads:
        row = [cell(wl.name), cell(_info_value(wl))]
        if not retired:
            row.append(cell(fmt_workload_status(wl), styled=True))
        row += [
            cell(fmt_datetime(wl.last_backup_at)),
            cell(fmt_bytes(wl.protected_data_bytes)),
            cell(fmt_bytes(wl.backup_copy_data_bytes) if wl.backup_copy_data_bytes else "-"),
            cell(wl.plan.name),
            cell(fmt_backup_server(wl)),
            cell(fmt_backup_copy(wl)),
        ]
        if verbose:
            row.append(cell(wl.workload_id))
            row.append(cell(wl.namespace))
            row.append(cell(wl.plan.plan_id))
        t.add_row(*row)

    console.print(t)


def _print_workload_detail(wl: GWSWorkload) -> None:
    info_col = _GWS_INFO_COL_LABELS.get(wl.workload_type, "Info")
    type_display = _GWS_WORKLOAD_TYPE_DISPLAY.get(wl.workload_type, wl.workload_type.value)
    print_workload_detail(
        console, wl,
        type_label=f"GWS / {type_display}",
        info_rows=[(info_col, _info_value(wl)), ("Domain", wl.domain)],
    )
