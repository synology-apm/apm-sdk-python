"""synology-apm-cli m365 — Microsoft 365 backup resource management commands.

Command structure:
  synology-apm-cli m365 (exchange|onedrive|chat|group|sharepoint|teams)
    list/get/backup/cancel/retire/change-plan/version (all types)
    export list/cancel/download (exchange and group only)

get / backup / cancel / retire / change-plan / version / export support two modes:
  Search mode: <identifier> -t <tid>       (UPN / group email / site or team name)
  Direct mode: --id <uid> --namespace <ns> (exact lookup; version/export subcommands
               use --workload-id because --id refers to the version/activity there)
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    _M365_WORKLOAD_TYPE_DISPLAY,
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
    SINCE_OPTION,
    UNTIL_OPTION,
    VERSION_LIMIT_OPTION,
)
from synology_apm.cli._serializers import (
    m365_workload_to_csv_row,
    m365_workload_to_dict,
)
from synology_apm.cli._validate import (
    WORKLOAD_STATUS_ARGS,
    WorkloadRef,
    _resolve_plans,
    _resolve_tenant,
    parse_enum_list,
    parse_time_range,
    print_resolved_tenant,
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
from synology_apm.cli.commands.m365_export import (
    _M365_TYPE_MAP as _TYPE_MAP,
)
from synology_apm.cli.commands.m365_export import (
    _TENANT_ID_OPTION,
    _make_export_app,
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
    M365Workload,
    M365WorkloadType,
    SaasTenant,
)

app = typer.Typer(
    help="Manage Microsoft 365 backup resources.",
    no_args_is_help=True,
)

_TYPE_LABELS: dict[str, str] = {
    "exchange":   "Mailbox (Exchange)",
    "onedrive":   "OneDrive",
    "chat":       "Teams Chat",
    "group":      "Group Exchange",
    "sharepoint": "SharePoint Sites",
    "teams":      "Teams Channels",
}

# <name> positional argument description for get / backup / cancel / retire / version
_TYPE_SEARCH_ARG: dict[str, str] = {
    "exchange":   "UPN (search mode, e.g. alice@contoso.com)",
    "onedrive":   "UPN (search mode, e.g. alice@contoso.com)",
    "chat":       "UPN (search mode, e.g. alice@contoso.com)",
    "group":      "Group email (search mode, e.g. marketing@contoso.com)",
    "sharepoint": "Site name (search mode)",
    "teams":      "Team name (search mode)",
}

# Example values for each workload_type in search mode (used in --help text)
_TYPE_EXAMPLE: dict[str, str] = {
    "exchange":   '"alice@contoso.com"',
    "onedrive":   '"alice@contoso.com"',
    "chat":       '"alice@contoso.com"',
    "group":      '"marketing@contoso.com"',
    "sharepoint": '"HR Site"',
    "teams":      '"Engineering"',
}

_INFO_COL_LABELS: dict[M365WorkloadType, str] = {
    M365WorkloadType.EXCHANGE:   "UPN",
    M365WorkloadType.ONEDRIVE:   "UPN",
    M365WorkloadType.CHAT:       "UPN",
    M365WorkloadType.GROUP:      "Email",
    M365WorkloadType.SHAREPOINT: "URL",
    M365WorkloadType.TEAMS:      "URL",
}


def _make_type_app(type_name: str, type_val: M365WorkloadType) -> typer.Typer:
    """Build a Typer sub-app for the given M365 service sub-type, with list/get/backup/cancel/retire commands."""
    label = _TYPE_LABELS[type_name]
    info_col = _INFO_COL_LABELS[type_val]
    search_arg_help = _TYPE_SEARCH_ARG[type_name]
    example = _TYPE_EXAMPLE[type_name]

    type_app = typer.Typer(
        help=f"Manage M365 {label} workloads.",
        no_args_is_help=True,
    )

    async def _get_workload(
        apm: APMClient, ref: WorkloadRef, tenant_id: str | None, is_retired: bool
    ) -> M365Workload:
        """Resolve the workload via get() (--id/--namespace) or get_by_name() (name)."""
        wl = await ref.resolve_m365(apm, tenant_id, type_val, is_retired=is_retired)
        print_resolved_tenant(tenant_id, wl.tenant_id)
        return wl

    # ── list ──────────────────────────────────────────────────────────────

    @type_app.command("list")
    @run_async
    async def _list(
        ctx: typer.Context,
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = typer.Option(
            False, "--retired", help="Show only retired workloads (default: show protected only)"
        ),
        search: str | None = typer.Option(None, "--search", help="Keyword search"),
        namespace: str | None = typer.Option(
            None, "--namespace", "-n",
            help=(
                "Show only workloads on the specified backup server "
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
        """List M365 Workloads of this type."""
        status_enums = parse_enum_list(
            status, WORKLOAD_STATUS_ARGS, "status",
            "queuing / backing_up / success / failed / partial / canceled / no_backups / deleting",
        )
        async with apm_session(ctx) as apm:
            tid = await _resolve_tenant(apm, tenant_id)
            print_resolved_tenant(tenant_id, tid)
            resolved_plans = await _resolve_plans(apm, plan, is_retired=retired)
            with api_spinner("Fetching workloads..."):
                list_coro = dispatch_paginated_list(
                    lambda off, lim: apm.m365.workloads.list(
                        tid, workload_type=type_val, keyword=search,
                        namespace=namespace, is_retired=retired, plan=resolved_plans,
                        status=status_enums,
                        limit=lim, offset=off,
                    ),
                    limit=limit, offset=offset, page_all=page_all, output=output,
                    to_dict=m365_workload_to_dict, to_csv_row=m365_workload_to_csv_row,
                )
                if output == ListOutputFormat.TABLE:
                    tenant_info, result = await asyncio.gather(
                        apm.saas.get_m365_tenant(tid), list_coro,
                    )
                else:
                    tenant_info = None
                    result = await list_coro

        if result is None:
            return
        workloads, total = result
        _print_tenant_header(tenant_info)
        _print_workload_table(workloads, verbose=verbose, info_col=info_col, retired=retired)
        print_list_footer(console, len(workloads), total, offset)

    # ── get ───────────────────────────────────────────────────────────────

    @type_app.command("get", help=(
        f"Show details for an M365 Workload.\n\n"
        f"\b\nSearch mode (searches protected workloads by default):\n"
        f"  synology-apm-cli m365 {type_name} get {example}\n"
        f"  synology-apm-cli m365 {type_name} get {example} --retired\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} get --id <uid> --namespace <ns>"
    ))
    @run_async
    async def _get(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode; requires --namespace)"),
        namespace: str | None = typer.Option(
            None, "--namespace", "-n", help="Backup server namespace (direct mode; requires --id)"
        ),
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        output: OutputFormat = OUTPUT_OPTION,
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        async with apm_session(ctx) as apm:
            wl = await _get_workload(apm, ref, tenant_id, is_retired=retired)

        if dispatch_output(wl, output, m365_workload_to_dict):
            return
        _print_workload_detail(wl)

    # ── backup ────────────────────────────────────────────────────────────

    @type_app.command("backup", help=(
        f"Trigger an on-demand backup for an M365 Workload.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli m365 {type_name} backup {example}\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} backup --id <uid> --namespace <ns>"
    ))
    @run_async
    async def _backup(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        async with apm_session(ctx) as apm:
            await _do_backup(
                lambda: _get_workload(apm, ref, tenant_id, is_retired=False),
                apm.m365.workloads.backup_now,
                quiet=quiet,
            )

    # ── cancel ────────────────────────────────────────────────────────────

    @type_app.command("cancel", help=(
        f"Cancel the running backup for an M365 Workload.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli m365 {type_name} cancel {example}\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} cancel --id <uid> --namespace <ns>"
    ))
    @run_async
    async def _cancel(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        async with apm_session(ctx, abortable=True) as apm:
            await _do_cancel(
                lambda: _get_workload(apm, ref, tenant_id, is_retired=False),
                apm.m365.workloads.cancel_backup,
                lambda wl: None,
                yes=yes,
                quiet=quiet,
            )

    # ── retire ────────────────────────────────────────────────────────────

    @type_app.command("retire", help=(
        f"Retire an M365 Workload (irreversible; requires confirmation).\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli m365 {type_name} retire {example} --plan \"Compliance Retention\"\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} retire --id <uid> --namespace <ns> --plan <plan-id>"
    ))
    @run_async
    async def _retire(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
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
            tid = await _resolve_tenant(apm, tenant_id)
            print_resolved_tenant(tenant_id, tid)
            await _do_retire(
                lambda: ref.resolve_m365(apm, tid, type_val, is_retired=False),
                lambda: apm.m365.workloads.get_by_name(
                    ref.identifier, tid, workload_type=type_val, is_retired=True
                ),
                apm.m365.workloads.retire,
                lambda wl: None,
                apm=apm,
                is_direct=ref.is_direct,
                plan_arg=plan,
                resource_type="M365Workload",
                yes=yes,
                quiet=quiet,
            )

    # ── change-plan ──────────────────────────────────────────────────────

    @type_app.command("change-plan", help=(
        f"Change the Protection Plan or Retirement Plan assigned to an M365 Workload.\n\n"
        "The plan type --plan is resolved against is auto-detected from the workload's current "
        "state: a Protection Plan for an active workload, a Retirement Plan for an "
        "already-retired one.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli m365 {type_name} change-plan {example} --plan \"Daily Backup\"\n"
        f"  synology-apm-cli m365 {type_name} change-plan {example} --retired --plan \"Compliance Retention\"\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} change-plan --id <uid> --namespace <ns> --plan <plan-id>"
    ))
    @run_async
    async def _change_plan(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
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
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        plan = require_or_help(ctx, plan)
        async with apm_session(ctx, abortable=True) as apm:
            await _do_change_plan(
                lambda: _get_workload(apm, ref, tenant_id, is_retired=retired),
                apm.m365.workloads.change_plan,
                lambda wl: None,
                apm=apm,
                plan_arg=plan,
                yes=yes,
                quiet=quiet,
            )

    # ── version ───────────────────────────────────────────────────────────

    version_app = typer.Typer(help=f"Manage M365 {label} backup versions.", no_args_is_help=True)
    type_app.add_typer(version_app, name="version")

    @version_app.command("list", help=(
        f"List backup version history for an M365 Workload.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli m365 {type_name} version list {example}\n"
        f"  synology-apm-cli m365 {type_name} version list {example} --retired\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} version list --id <workload-id> --namespace <ns>"
    ))
    @run_async
    async def _version_list(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        limit: int = VERSION_LIMIT_OPTION,
        offset: int = OFFSET_OPTION,
        page_all: bool = PAGE_ALL_OPTION,
        since: str | None = SINCE_OPTION,
        until: str | None = UNTIL_OPTION,
        output: ListOutputFormat = LIST_OUTPUT_OPTION,
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace)
        since_dt, until_dt = parse_time_range(since, until)
        async with apm_session(ctx) as apm:
            await _do_version_list(
                lambda: _get_workload(apm, ref, tenant_id, is_retired=retired),
                apm.m365.workloads.list_versions,
                None,
                limit=limit, offset=offset, page_all=page_all, since=since_dt, until=until_dt,
                output=output, verbose=verbose,
            )

    @version_app.command("get", help=(
        f"Show activity details and logs for a backup version (omit --id to get the latest).\n\n"
        f"\b\nSearch mode (by workload name; omit --id for latest):\n"
        f"  synology-apm-cli m365 {type_name} version get {example}\n"
        f"  synology-apm-cli m365 {type_name} version get {example} --id <version-id>\n"
        f"  synology-apm-cli m365 {type_name} version get {example} --id <version-id> --retired\n\n"
        f"\b\nDirect mode (--tenant-id not required; omit --id for latest):\n"
        f"  synology-apm-cli m365 {type_name} version get --workload-id <wl-id> --namespace <ns>\n"
        f"  synology-apm-cli m365 {type_name} version get --workload-id <wl-id> --namespace <ns> --id <version-id>"
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
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        output: OutputFormat = OUTPUT_OPTION,
    ) -> None:
        ref = validate_version_workload_args(ctx, name, workload_id, namespace)

        async with apm_session(ctx) as apm:
            await _do_version_get(
                lambda: _get_workload(apm, ref, tenant_id, is_retired=retired),
                apm.m365.workloads.get_version,
                apm.m365.workloads.get_latest_version,
                apm=apm,
                version_id=version_id,
                output=output,
            )

    @version_app.command("lock", help=(
        f"Lock a backup version to prevent deletion by retention rules.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli m365 {type_name} version lock {example} --id <ver-id>\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} version lock --workload-id <wl-id> --namespace <ns> --id <ver-id>"
    ))
    @run_async
    async def _version_lock(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        version_id: str | None = typer.Option(None, "--id", help="Version ID (from version list)"),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref, version_id = validate_version_lock_args(ctx, name, workload_id, namespace, version_id)
        async with apm_session(ctx) as apm:
            await _do_version_lock_unlock(
                lambda: _get_workload(apm, ref, tenant_id, is_retired=retired),
                apm.m365.workloads.get_version,
                apm.m365.workloads.lock_version,
                apm.m365.workloads.unlock_version,
                version_id=version_id,
                lock=True,
            )
        if not quiet:
            console.print(f"[green]✓[/green] Version locked: {version_id}")

    @version_app.command("unlock", help=(
        f"Unlock a backup version, allowing retention rules to delete it.\n\n"
        f"\b\nSearch mode:\n"
        f"  synology-apm-cli m365 {type_name} version unlock {example} --id <ver-id>\n\n"
        f"\b\nDirect mode (--tenant-id not required):\n"
        f"  synology-apm-cli m365 {type_name} version unlock --workload-id <wl-id> --namespace <ns> --id <ver-id>"
    ))
    @run_async
    async def _version_unlock(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        version_id: str | None = typer.Option(None, "--id", help="Version ID (from version list)"),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = typer.Option(False, "--retired", help="Search in retired workloads (search mode)"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref, version_id = validate_version_lock_args(ctx, name, workload_id, namespace, version_id)
        async with apm_session(ctx) as apm:
            await _do_version_lock_unlock(
                lambda: _get_workload(apm, ref, tenant_id, is_retired=retired),
                apm.m365.workloads.get_version,
                apm.m365.workloads.lock_version,
                apm.m365.workloads.unlock_version,
                version_id=version_id,
                lock=False,
            )
        if not quiet:
            console.print(f"[green]✓[/green] Version unlocked: {version_id}")

    # ── mail export (exchange and group only) ─────────────────────────────
    if type_name in ("exchange", "group"):
        type_app.add_typer(_make_export_app(type_name, search_arg_help), name="export")

    return type_app


# ── Register all scope sub-apps ───────────────────────────────────────────

for _type_name, _type_val in _TYPE_MAP.items():
    app.add_typer(_make_type_app(_type_name, _type_val), name=_type_name)


# ── Formatting helpers ────────────────────────────────────────────────────

def _print_tenant_header(tenant: SaasTenant | None) -> None:
    if tenant is None:
        return
    name = tenant.tenant_name or "-"
    domain = tenant.tenant_email or "-"
    console.print(f"Tenant: [bold]{name}[/bold] ({domain})")
    console.print()


def _print_workload_table(
    workloads: Sequence[M365Workload], verbose: bool = False, info_col: str = "UPN", retired: bool = False
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
        row = [cell(wl.name), cell(wl.info.label if wl.info else "-")]
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


def _print_workload_detail(wl: M365Workload) -> None:
    info_col = _INFO_COL_LABELS.get(wl.workload_type, "Info")
    info_label = wl.info.label if wl.info else "-"
    type_display = _M365_WORKLOAD_TYPE_DISPLAY.get(wl.workload_type, wl.workload_type.value)
    print_workload_detail(
        console, wl,
        type_label=f"M365 / {type_display}",
        info_rows=[(info_col, info_label), ("Tenant ID", wl.tenant_id)],
    )
