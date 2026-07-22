"""synology-apm-cli infra — infrastructure information commands (Management Server + Backup Server)."""
from __future__ import annotations

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    _COPY_PROGRESS_STATUSES,
    _WORKLOAD_STAT_LABEL,
    fmt_bytes,
    fmt_copy_reason,
    fmt_copy_status,
    fmt_encryption_enabled,
    fmt_hypervisor_type,
    fmt_management_url,
    fmt_remote_storage_status,
    fmt_remote_storage_type,
    fmt_remote_storage_usage,
    fmt_server_status,
    fmt_storage_usage,
    fmt_usage_pct,
    print_list_footer,
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
    hypervisor_to_csv_row,
    server_to_csv_row,
)
from synology_apm.cli._validate import _resolve_tiering_plan, resolve_by_name_or_id, validate_name_or_id_args
from synology_apm.cli.errors import EXIT_ERROR, err_console
from synology_apm.cli.output import (
    ListOutputFormat,
    OutputFormat,
    cell,
    console,
    dispatch_list_output,
    dispatch_output,
    dispatch_paginated_list,
    new_table,
)
from synology_apm.sdk import (
    BackupServer,
    BackupServerRole,
    BackupServerType,
    Hypervisor,
    RemoteStorage,
    ServerStatus,
    SiteInfo,
    VersionCopyStatus,
    WorkloadStatType,
)

app = typer.Typer(
    help="Show APM infrastructure information (Management Server and Backup Server).",
    no_args_is_help=True,
)

_server_app = typer.Typer(help="Manage backup servers.", no_args_is_help=True)
app.add_typer(_server_app, name="server")

_remote_storage_app = typer.Typer(help="Manage remote storage devices.", no_args_is_help=True)
app.add_typer(_remote_storage_app, name="storage")

_hypervisor_app = typer.Typer(help="Manage hypervisor inventory servers.", no_args_is_help=True)
app.add_typer(_hypervisor_app, name="hypervisor")


_WORKLOAD_STAT_ORDER = [
    WorkloadStatType.MACHINE_PC,
    WorkloadStatType.MACHINE_PS,
    WorkloadStatType.MACHINE_VM,
    WorkloadStatType.MACHINE_FS,
    WorkloadStatType.M365,
    WorkloadStatType.GWS,
]

# ── synology-apm-cli infra info ────────────────────────────────────────────────────────

@app.command("info")
@run_async
async def infra_info(
    ctx: typer.Context,
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show APM site information, Management Server details, and site storage statistics."""
    async with apm_session(ctx) as apm:
        site = await apm.get_site_info()

    if dispatch_output(site, output, SiteInfo.to_dict):
        return

    mgmt = site.primary_management_server
    sec = site.secondary_management_server
    storage = site.site_storage
    base_url = fmt_management_url(site.external_address, site.port)

    console.print("[bold]Site Information[/bold]")
    console.print("─" * 38)
    console.print(f"UUID:               {site.site_uuid}")
    if base_url:
        console.print(f"Management Center:  {base_url}")
        console.print(f"Recovery Portal:    {base_url}/portal")
    else:
        console.print("Management Center:  -")
        console.print("Recovery Portal:    -")
    console.print()

    console.print("[bold]Primary Management Server[/bold]")
    console.print("─" * 38)
    if mgmt is None:
        console.print("[dim]Not available[/dim]")
    else:
        mgmt_status = fmt_server_status(mgmt.status)
        version_str = "Updating..." if mgmt.is_updating else (mgmt.system_version or "-")
        console.print(f"Name:            {mgmt.name}")
        console.print(f"Model:           {mgmt.model}")
        console.print(f"IP:              {mgmt.hostname}")
        console.print(f"System Version:  {version_str}")
        console.print(f"Serial:          {mgmt.serial}")
        console.print(f"Status:          {mgmt_status}")
    console.print()

    console.print("[bold]Secondary Management Server[/bold]")
    console.print("─" * 38)
    if sec is None:
        console.print("[dim]Not configured[/dim]")
    else:
        sec_status = fmt_server_status(sec.status)
        sec_version = "Updating..." if sec.is_updating else (sec.system_version or "-")
        console.print(f"Name:            {sec.name}")
        console.print(f"Model:           {sec.model}")
        console.print(f"IP:              {sec.hostname}")
        console.print(f"System Version:  {sec_version}")
        console.print(f"Serial:          {sec.serial}")
        console.print(f"Status:          {sec_status}")
    console.print()

    logical_str     = fmt_bytes(storage.logical_backup_data_bytes)
    physical_str    = fmt_bytes(storage.physical_backup_data_bytes)
    reduced_str     = fmt_bytes(storage.backup_data_reduction_bytes)
    reduction_ratio = storage.backup_data_reduction_ratio

    console.print("[bold]Data Reduction Summary[/bold]")
    console.print("─" * 38)
    console.print(f"Total Logical Backup Data:   {logical_str}")
    console.print(f"Total Physical Backup Data:  {physical_str}")
    console.print(f"Data Reduced:                {reduced_str} ([green]{reduction_ratio:.1f}%[/green])")
    console.print()

    usage = site.workload_usage
    by_type = {s.workload_type: s for s in usage.by_type}

    console.print("[bold]Workload Usage Summary[/bold]")
    console.print("─" * 38)
    console.print(f"{'Type':<6}  {'Workloads':>9}  {'Data Size':>14}")
    console.print("─" * 38)
    for wtype in _WORKLOAD_STAT_ORDER:
        stat = by_type.get(wtype)
        if stat is None:
            continue
        label = _WORKLOAD_STAT_LABEL[wtype]
        size = fmt_bytes(stat.protected_data_bytes) if stat.protected_data_bytes > 0 else "-"
        console.print(f"{label:<6}  {stat.total_count:>9}  {size:>14}")
    console.print("─" * 38)
    total_size = usage.total_protected_data_bytes
    total_size_str = fmt_bytes(total_size) if total_size > 0 else "-"
    console.print(f"{'Total':<6}  {usage.total_count:>9}  {total_size_str:>14}")


# ── synology-apm-cli infra server list ─────────────────────────────────────────────────

@_server_app.command("list")
@run_async
async def server_list(
    ctx: typer.Context,
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    status: list[ServerStatus] | None = typer.Option(None, "--status", help="Filter by status; repeatable"),
    type_filter: list[BackupServerType] | None = typer.Option(None, "--type", help="Filter by server type; repeatable"),
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Verbose mode (show Description, Server ID, Namespace)"
    ),
) -> None:
    """List all backup servers in the cluster."""
    async with apm_session(ctx, spinner="Fetching backup servers...") as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.backup_servers.list(
                name_contains=search,
                status_filter=status or None,
                type_filter=type_filter or None,
                limit=lim,
                offset=off,
            ),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=BackupServer.to_dict, to_csv_row=server_to_csv_row,
        )

    if result is None:
        return

    servers, total = result

    t = new_table()
    t.add_column("Name", min_width=20)
    t.add_column("Serial Number", min_width=12)
    t.add_column("IP Address", min_width=14)
    t.add_column("Model", min_width=8)
    t.add_column("System Version", min_width=16)
    t.add_column("Status", min_width=14, no_wrap=True)
    t.add_column("Usage", min_width=26)
    t.add_column("Tiering Plan", min_width=16)
    t.add_column("Tiering Status", min_width=16)
    if verbose:
        t.add_column("Description", min_width=16)
        t.add_column("Server ID", min_width=36)
        t.add_column("Namespace", min_width=36)

    for s in servers:
        status_label = fmt_server_status(s.status)
        if s.role == BackupServerRole.PRIMARY:
            name_label = f"{s.name} [green dim](Primary)[/green dim]"
        elif s.role == BackupServerRole.SECONDARY:
            name_label = f"{s.name} [cyan dim](Secondary)[/cyan dim]"
        else:
            name_label = s.name
        used = fmt_bytes(s.storage_used_bytes)
        storage_total = fmt_bytes(s.storage_total_bytes)
        usage = fmt_storage_usage(
            used, storage_total,
            s.storage_usage_pct if s.storage_total_bytes is not None else None,
        )
        ts = s.tiering_status
        tiering_status_str = (
            fmt_copy_status(ts)
            if ts and ts.status != VersionCopyStatus.NOT_ENABLED
            else "-"
        )
        row = [
            cell(name_label, styled=True), cell(s.serial),
            cell(s.hostname), cell(s.model),
            cell("Updating..." if s.is_updating else (s.system_version or "-")),
            cell(status_label, styled=True),
            usage,
            cell(s.tiering_plan_name or "-"),
            cell(tiering_status_str, styled=True),
        ]
        if verbose:
            row += [cell(s.description or "-"), cell(s.backup_server_id), cell(s.namespace)]
        t.add_row(*row)

    console.print(t)
    print_list_footer(console, len(servers), total, offset)


# ── synology-apm-cli infra server get ──────────────────────────────────────────────────

@_server_app.command("get")
@run_async
async def server_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Server name keyword (search mode)"),
    server_id: str | None = typer.Option(
        None, "--id", help="Backup Server ID (direct mode; from synology-apm-cli infra server list --verbose)"
    ),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show details for a backup server.

    \b
    Search mode (name keyword):
      synology-apm-cli infra server get "apm-server-01"

    \b
    Direct mode:
      synology-apm-cli infra server get --id <server-id>
    """
    validate_name_or_id_args(ctx, name, server_id, exclusive_msg="<name> cannot be used with --id")
    async with apm_session(ctx) as apm:
        server = await resolve_by_name_or_id(
            name, server_id, apm.backup_servers.get, apm.backup_servers.get_by_name,
        )

    if dispatch_output(server, output, BackupServer.to_dict):
        return

    status = fmt_server_status(server.status)
    total = fmt_bytes(server.storage_total_bytes)
    used = fmt_bytes(server.storage_used_bytes)
    pct = server.storage_usage_pct if server.storage_total_bytes is not None else None
    used_str = f"{used} ({fmt_usage_pct(pct, fixed_width=False)})" if pct is not None else used

    logical_str     = fmt_bytes(server.logical_backup_data_bytes)
    physical_str    = fmt_bytes(server.physical_backup_data_bytes)
    reduction_bytes = server.backup_data_reduction_bytes
    reduced_str     = fmt_bytes(reduction_bytes)

    console.print(f"Backup Server: [bold]{server.name}[/bold]")
    console.print("─" * 38)
    console.print(f"ID:             {server.backup_server_id}")
    console.print(f"Namespace:      {server.namespace}")
    console.print(f"Model:          {server.model}")
    console.print(f"IP:             {server.hostname}")
    console.print(f"Serial:         {server.serial}")
    version_str = "Updating..." if server.is_updating else (server.system_version or "-")
    console.print(f"System Version: {version_str}")
    console.print(f"Description:    {server.description or '-'}")
    console.print(f"Status:         {status}")
    ts = server.tiering_status
    if ts and ts.status != VersionCopyStatus.NOT_ENABLED:
        console.print()
        console.print(f"Tiering Status: {fmt_copy_status(ts)}", markup=True)
        if ts.status in _COPY_PROGRESS_STATUSES and ts.pending_version_count > 0:
            remaining = f", {fmt_bytes(ts.remaining_bytes)} remaining" if ts.remaining_bytes else ""
            console.print(f"                {ts.pending_version_count} version(s) pending{remaining}")
        reason_str = fmt_copy_reason(ts.reason)
        if reason_str:
            console.print(f"                {reason_str}")
    console.print()
    console.print("Storage Usage:")
    console.print(f"  Total:  {total}")
    console.print(f"  Used:   {used_str}")
    console.print()
    console.print("Data Reduction Summary:")
    console.print(f"  Logical Backup Data:   {logical_str}")
    console.print(f"  Physical Backup Data:  {physical_str}")
    if reduction_bytes is not None:
        reduction_ratio = server.backup_data_reduction_ratio
        console.print(f"  Data Reduced:          {reduced_str} ([green]{reduction_ratio:.1f}%[/green])")
    else:
        console.print(f"  Data Reduced:          {reduced_str}")
    console.print()
    console.print("Tiering Plan:")
    if server.tiering_plan_name:
        console.print(f"  Plan:         {server.tiering_plan_name}")
        dest = server.tiering_plan_destination
        if dest:
            console.print(f"  Destination:  {dest.name}")
            console.print(f"  Endpoint:     {dest.endpoint}")
            if dest.vault:
                console.print(f"  Vault:        {dest.vault}")
    else:
        console.print("  [dim]Not configured[/dim]")


@_server_app.command("change-plan")
@run_async
async def server_change_plan(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Server name keyword (search mode)"),
    server_id: str | None = typer.Option(
        None, "--id", help="Backup Server ID (direct mode; from synology-apm-cli infra server list --verbose)"
    ),
    plan: str | None = typer.Option(None, "--plan", help="Tiering plan name or ID to apply"),
    remove: bool = typer.Option(False, "--remove", help="Remove the current tiering plan"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Apply or remove a Tiering Plan on a backup server.

    \b
    Apply a tiering plan (search mode):
      synology-apm-cli infra server change-plan "apm-server-01" --plan "30-Day Tiering"

    \b
    Apply a tiering plan (direct mode):
      synology-apm-cli infra server change-plan --id <server-id> --plan <plan-id>

    \b
    Remove the current tiering plan:
      synology-apm-cli infra server change-plan "apm-server-01" --remove
    """
    validate_name_or_id_args(ctx, name, server_id, exclusive_msg="<name> cannot be used with --id")
    if plan and remove:
        err_console.print("[red]✗[/red] --plan and --remove are mutually exclusive")
        raise typer.Exit(code=EXIT_ERROR)
    if not plan and not remove:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    async with apm_session(ctx, abortable=True) as apm:
        server = await resolve_by_name_or_id(
            name, server_id, apm.backup_servers.get, apm.backup_servers.get_by_name,
        )
        resolved_plan = await _resolve_tiering_plan(apm, plan) if plan else None

        current = server.tiering_plan_name or "None"
        new_plan_name = resolved_plan.name if resolved_plan else "None"
        err_console.print("Changing tiering plan:")
        err_console.print(f"  Server:   {server.name} (ID: {server.backup_server_id})")
        err_console.print(f"\n[yellow]⚠[/yellow] Current plan: {current} -> {new_plan_name}")

        if resolved_plan is None:
            err_console.print(
                "\n[yellow]⚠[/yellow] Removing the tiering plan from a server will stop any new data "
                "from being tiered, but ongoing operations will continue. To ensure full protection, "
                "the lock duration for immutable workloads on backup servers will also be adjusted accordingly."
            )

        if not yes:
            typer.confirm("\nConfirm change plan?", abort=True)

        await apm.backup_servers.change_tiering_plan(server, resolved_plan)

    if not quiet:
        console.print(f"[green]✓[/green] Tiering plan updated: {server.name}")


# ── Serialization helpers ─────────────────────────────────────────────────


# ── synology-apm-cli infra storage list ────────────────────────────────────────────────

@_remote_storage_app.command("list")
@run_async
async def remote_storage_list(
    ctx: typer.Context,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode (show Remote Storage ID)"),
) -> None:
    """List all remote storage devices."""
    async with apm_session(ctx, spinner="Fetching remote storages...") as apm:
        remote_storages, total = await apm.remote_storages.list()

    if dispatch_list_output(remote_storages, output, RemoteStorage.to_dict):
        return

    t = new_table()
    t.add_column("Name", min_width=12)
    t.add_column("Endpoint", min_width=16)
    t.add_column("Type", min_width=20)
    t.add_column("Client-Side Encryption", min_width=22, no_wrap=True)
    t.add_column("Status", min_width=22, no_wrap=True)
    t.add_column("Usage", min_width=16)
    if verbose:
        t.add_column("Remote Storage ID", min_width=36)

    for s in remote_storages:
        status_label = fmt_remote_storage_status(s.status)
        usage = fmt_remote_storage_usage(s.used_bytes, s.remaining_bytes)
        type_label = cell(fmt_remote_storage_type(s.storage_type, s.device_model))
        encryption_label = fmt_encryption_enabled(s.encryption_enabled)
        row = [
            cell(s.name), cell(s.endpoint), type_label,
            cell(encryption_label, styled=True), cell(status_label, styled=True), cell(usage),
        ]
        if verbose:
            row.append(cell(s.storage_id))
        t.add_row(*row)

    console.print(t)
    print_list_footer(console, len(remote_storages), total)


# ── synology-apm-cli infra storage get ─────────────────────────────────────────────────

@_remote_storage_app.command("get")
@run_async
async def remote_storage_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Remote Storage display name or endpoint (search mode)"),
    storage_id: str | None = typer.Option(
        None, "--id", help="Remote Storage UUID (direct mode; from synology-apm-cli infra storage list --verbose)"
    ),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show details for a remote storage device.

    \b
    Search mode (name or endpoint):
      synology-apm-cli infra storage get "DSM-Storage"

    \b
    Direct mode:
      synology-apm-cli infra storage get --id <storage-id>
    """
    validate_name_or_id_args(ctx, name, storage_id, exclusive_msg="<name> cannot be used with --id")
    async with apm_session(ctx) as apm:
        remote_storage = await resolve_by_name_or_id(
            name, storage_id, apm.remote_storages.get, apm.remote_storages.get_by_name,
        )

    if dispatch_output(remote_storage, output, RemoteStorage.to_dict):
        return

    status = fmt_remote_storage_status(remote_storage.status)
    used = fmt_bytes(remote_storage.used_bytes)
    remaining = fmt_bytes(remote_storage.remaining_bytes)
    type_label = fmt_remote_storage_type(remote_storage.storage_type, remote_storage.device_model)

    console.print(f"Remote Storage: [bold]{remote_storage.name}[/bold]")
    console.print("─" * 38)
    console.print(f"ID:                       {remote_storage.storage_id}")
    console.print(f"Type:                     {type_label}")
    console.print(f"Endpoint:                 {remote_storage.endpoint}")
    console.print(f"Client-Side Encryption:   {fmt_encryption_enabled(remote_storage.encryption_enabled)}")
    console.print(f"Status:                   {status}")
    console.print()
    console.print("Storage Usage:")
    console.print(f"  Used:      {used}")
    console.print(f"  Remaining: {remaining}")


# ── synology-apm-cli infra hypervisor list ─────────────────────────────────────────────

@_hypervisor_app.command("list")
@run_async
async def hypervisor_list(
    ctx: typer.Context,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode (show Hypervisor ID)"),
) -> None:
    """List all registered hypervisor inventory servers."""
    async with apm_session(ctx, spinner="Fetching hypervisors...") as apm:
        hypervisors, total = await apm.hypervisors.list()

    if dispatch_list_output(hypervisors, output, Hypervisor.to_dict, hypervisor_to_csv_row):
        return

    t = new_table()
    t.add_column("Hostname", min_width=16)
    t.add_column("Address", min_width=14)
    t.add_column("Type", min_width=28)
    t.add_column("Account", min_width=10)
    t.add_column("Description", min_width=12)
    if verbose:
        t.add_column("Hypervisor ID", min_width=36)

    for h in hypervisors:
        row = [
            cell(h.hostname), cell(h.address),
            cell(fmt_hypervisor_type(h.host_type)), cell(h.account), cell(h.description),
        ]
        if verbose:
            row.append(cell(h.hypervisor_id))
        t.add_row(*row)

    console.print(t)
    print_list_footer(console, len(hypervisors), total)


# ── synology-apm-cli infra hypervisor get ──────────────────────────────────────────────

@_hypervisor_app.command("get")
@run_async
async def hypervisor_get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Hypervisor hostname or address (search mode)"),
    hypervisor_id: str | None = typer.Option(
        None, "--id", help="Hypervisor UUID (direct mode; from synology-apm-cli infra hypervisor list --verbose)"
    ),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show details for a hypervisor inventory server.

    \b
    Search mode (hostname or address):
      synology-apm-cli infra hypervisor get "esxi1.example.com"

    \b
    Direct mode:
      synology-apm-cli infra hypervisor get --id <hypervisor-id>
    """
    validate_name_or_id_args(ctx, name, hypervisor_id, exclusive_msg="<name> cannot be used with --id")
    async with apm_session(ctx) as apm:
        hypervisor = await resolve_by_name_or_id(
            name, hypervisor_id, apm.hypervisors.get, apm.hypervisors.get_by_name,
        )

    if dispatch_output(hypervisor, output, Hypervisor.to_dict):
        return

    type_label = fmt_hypervisor_type(hypervisor.host_type)
    console.print(f"Hypervisor: [bold]{hypervisor.hostname}[/bold]")
    console.print("─" * 38)
    console.print(f"ID:          {hypervisor.hypervisor_id}")
    console.print(f"Type:        {type_label}")
    console.print(f"Address:     {hypervisor.address}")
    console.print(f"Port:        {hypervisor.port}")
    console.print(f"Account:     {hypervisor.account}")
    console.print(f"Version:     {hypervisor.version or '-'}")
    console.print(f"Description: {hypervisor.description or '-'}")


