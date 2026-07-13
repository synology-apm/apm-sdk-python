"""M365 mailbox export commands — shared export infrastructure and app factory.

Consumed by m365.py: import _TENANT_ID_OPTION, _M365_TYPE_MAP, _make_export_app.
"""
from __future__ import annotations

import asyncio
import re
import signal
from datetime import date
from pathlib import Path
from types import FrameType
from typing import cast

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import fmt_datetime, fmt_export_status, print_list_footer
from synology_apm.cli._helpers import api_spinner, apm_session
from synology_apm.cli._options import (
    LIST_OUTPUT_OPTION,
    OFFSET_OPTION,
    PAGE_ALL_OPTION,
)
from synology_apm.cli._serializers import (
    m365_export_activity_to_csv_row,
    m365_export_activity_to_dict,
)
from synology_apm.cli._validate import (
    WorkloadRef,
    print_resolved_tenant,
    print_resolved_version,
    require_or_help,
    validate_resolve_args,
)
from synology_apm.cli.errors import EXIT_ERROR, err_console
from synology_apm.cli.output import ListOutputFormat, cell, console, dispatch_paginated_list, new_table
from synology_apm.sdk import (
    APMClient,
    ExchangeExportCollection,
    GroupExportCollection,
    M365ExportActivity,
    M365ExportStartResult,
    M365ExportStatus,
    M365GroupInfo,
    M365UserInfo,
    M365Workload,
    M365WorkloadType,
)

_TENANT_ID_OPTION = typer.Option(
    None, "--tenant-id", "-t",
    help="Tenant ID (from synology-apm saas list; omit to auto-use the first M365 tenant)",
)

_RETIRED_OPTION = typer.Option(False, "--retired", help="Search in retired workloads (search mode)")

_M365_TYPE_MAP: dict[str, M365WorkloadType] = {
    "exchange":   M365WorkloadType.EXCHANGE,
    "onedrive":   M365WorkloadType.ONEDRIVE,
    "chat":       M365WorkloadType.CHAT,
    "group":      M365WorkloadType.GROUP,
    "sharepoint": M365WorkloadType.SHAREPOINT,
    "teams":      M365WorkloadType.TEAMS,
}


def _auto_download_filename(wl_name: str, archive_mailbox: bool, *, suffix: str = "") -> str:
    """Generate a safe PST filename: {wl_name}_{today}_{suffix}.pst

    suffix overrides the default "mailbox" / "archive_mailbox" label.
    """
    label = suffix if suffix else ("archive_mailbox" if archive_mailbox else "mailbox")
    return f"{_safe_export_name(wl_name)}_{date.today().strftime('%Y%m%d')}_{label}.pst"


def _auto_download_filename_by_id(wl_name: str, activity_id: str) -> str:
    """Generate a safe PST filename from workload name and activity ID (--id path)."""
    return f"{_safe_export_name(wl_name)}_{activity_id[:8]}.pst"


def _safe_export_name(wl_name: str) -> str:
    """Filesystem-safe stem derived from the workload display name."""
    return re.sub(r"[^\w.-]", "_", wl_name).strip("_") or "export"


def _confirm_overwrite(dest_path: str, yes: bool) -> None:
    """Prompt before overwriting an existing download target; Exit(4) when declined."""
    if not yes and Path(dest_path).exists():
        err_console.print(f"[yellow]![/yellow] File already exists: {dest_path}")
        if not typer.confirm("Overwrite?", default=False):
            raise typer.Exit(4)


_EXPORT_DOWNLOADABLE: frozenset[M365ExportStatus] = frozenset({
    M365ExportStatus.READY_TO_DOWNLOAD,
    M365ExportStatus.WARNING,
})
_EXPORT_TERMINAL: frozenset[M365ExportStatus] = frozenset({
    M365ExportStatus.READY_TO_DOWNLOAD,
    M365ExportStatus.WARNING,
    M365ExportStatus.DOWNLOADED,
    M365ExportStatus.FAILED,
    M365ExportStatus.CANCELED,
    M365ExportStatus.EXPIRED,
})


def _register_export_interrupt(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
    try:
        loop.add_signal_handler(signal.SIGINT, event.set)
    except (NotImplementedError, AttributeError):  # pragma: no cover
        def _win_handler(sig: int, frame: FrameType | None) -> None:  # pragma: no cover
            loop.call_soon_threadsafe(event.set)
        signal.signal(signal.SIGINT, _win_handler)  # pragma: no cover


def _unregister_export_interrupt(loop: asyncio.AbstractEventLoop) -> None:
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except (NotImplementedError, AttributeError):  # pragma: no cover
        signal.signal(signal.SIGINT, signal.default_int_handler)  # pragma: no cover


async def _poll_export_until_ready(
    collection: ExchangeExportCollection | GroupExportCollection,
    start_result: M365ExportStartResult,
    poll_interval: int = 5,
) -> tuple[M365ExportStatus | None, bool, M365ExportActivity | None]:
    """Poll export activity until a terminal status is reached or Ctrl+C fires.

    Returns (status, interrupted, last_activity):
      status is None when interrupted before any terminal state.
      last_activity is the last matched activity seen during polling (None if never found).
    """
    interrupt = asyncio.Event()
    loop = asyncio.get_running_loop()
    _register_export_interrupt(loop, interrupt)
    found_status: M365ExportStatus | None = None
    last_activity: M365ExportActivity | None = None
    try:
        while not interrupt.is_set():
            activity = await collection.get_activity_by_result(start_result)
            if activity is not None:
                last_activity = activity
            if activity is not None and activity.status in _EXPORT_TERMINAL:
                found_status = activity.status
                break
            try:  # pragma: no cover
                await asyncio.wait_for(interrupt.wait(), timeout=poll_interval)
            except TimeoutError:  # pragma: no cover
                pass
    finally:
        _unregister_export_interrupt(loop)

    interrupted = found_status is None
    return found_status, interrupted, last_activity


async def _wait_until_downloadable(
    col: ExchangeExportCollection | GroupExportCollection,
    start_result: M365ExportStartResult,
    identifier: str,
    cmd_prefix: str,
) -> None:
    """Poll until the export becomes downloadable; handle Ctrl+C and failed end states."""
    status, interrupted, last_activity = await _poll_export_until_ready(col, start_result)

    if interrupted:  # pragma: no cover
        console.print()
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, input, "Cancel export task on APM? [y/N] "
        )
        if raw.strip().lower() in ("y", "yes"):
            if last_activity is not None:
                await col.cancel(last_activity)
            console.print("[green]✓[/green] Export task canceled.")
        else:
            act_id_str = (
                f"\n  Activity ID: {last_activity.activity_id}" if last_activity else ""
            )
            console.print(
                f"  Export task still running on APM.{act_id_str}\n"
                f"  Run [bold]{cmd_prefix} list {identifier}[/bold] to check status."
            )
        raise typer.Exit(4)

    if status not in _EXPORT_DOWNLOADABLE:
        assert status is not None
        err_console.print(
            f"[red]✗[/red] Export ended with status: {fmt_export_status(status)}"
        )
        raise typer.Exit(1)


async def _start_export_and_resolve_url(
    apm: APMClient,
    col: ExchangeExportCollection | GroupExportCollection,
    wl: M365Workload,
    *,
    is_group: bool,
    mailbox_label: str,
    cmd_prefix: str,
    version_id: str | None,
    archive_mailbox: bool,
    export_name: str | None,
    filename: str | None,
    no_wait: bool,
    yes: bool,
) -> tuple[str, str]:
    """Auto-start mode: start a new export, wait until downloadable, return (url, dest_path)."""
    effective_archive = False if is_group else archive_mailbox
    effective_mailbox_label = mailbox_label if not effective_archive else "archive mailbox"

    with api_spinner("Fetching version..."):
        if version_id is not None:
            version = await apm.m365.workloads.get_version(wl, version_id)
        else:
            version = await apm.m365.workloads.get_latest_version(wl)
    print_resolved_version(version_id, version)

    dest_path = filename if filename is not None else _auto_download_filename(
        wl.name, effective_archive,
        suffix="group_mailbox" if is_group else "",
    )
    effective_export_name = export_name or Path(dest_path).name

    _confirm_overwrite(dest_path, yes)

    with api_spinner("Starting export..."):
        if is_group:
            start_result = await apm.m365.group_export.start(
                wl, version, export_name=effective_export_name,
            )
        else:
            start_result = await apm.m365.exchange_export.start(
                wl, version,
                archive_mailbox=effective_archive,
                export_name=effective_export_name,
            )

    if is_group:
        identifier = cast(M365GroupInfo, wl.info).mail
    else:
        identifier = cast(M365UserInfo, wl.info).user_principal_name

    if not start_result.ready_to_download:
        if no_wait:
            activity = await col.get_activity_by_result(start_result)
            if activity is not None:
                activity_hint = f"  Activity ID: {activity.activity_id}"
            else:
                activity_hint = (
                    f"  Run [bold]{cmd_prefix} list {identifier}[/bold]"
                    f" to get the Activity ID"
                )
            console.print(
                f"[green]✓[/green] Export started for [bold]{wl.name}[/bold]"
                f" ({effective_mailbox_label})\n"
                f"{activity_hint}\n"
                f"  Re-run with [bold]--id <activity-id>[/bold] to download."
            )
            raise typer.Exit(0)

        console.print(
            f"Export started for [bold]{wl.name}[/bold] ({effective_mailbox_label})\n"
            f"Waiting for APM to finish exporting...  [dim](Ctrl+C to interrupt)[/dim]"
        )
        await _wait_until_downloadable(col, start_result, identifier, cmd_prefix)

    url = await col.get_download_url_by_ready_result(start_result)
    return url, dest_path


async def _resolve_existing_export_url(
    col: ExchangeExportCollection | GroupExportCollection,
    wl: M365Workload,
    *,
    activity_id: str,
    filename: str | None,
    yes: bool,
) -> tuple[str, str]:
    """Direct mode (--id): look up a previously started export, return (url, dest_path)."""
    dest_path = filename if filename is not None else _auto_download_filename_by_id(
        wl.name, activity_id
    )
    _confirm_overwrite(dest_path, yes)

    activities, _ = await col.list(wl, limit=500)
    activity = next((a for a in activities if a.activity_id == activity_id), None)
    if activity is None:
        err_console.print(f"[red]✗[/red] Activity '{activity_id}' not found.")
        raise typer.Exit(EXIT_ERROR)
    url = await col.get_download_url_by_activity(activity)
    return url, dest_path


async def _download_with_progress(apm: APMClient, url: str, dest_path: str) -> None:
    """Stream the export file to dest_path with a transient progress bar on stderr."""
    from rich.console import Console as _Console
    from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=_Console(stderr=True),
        transient=True,
    ) as progress:
        task_id = progress.add_task(f"Downloading {dest_path}...", total=None)

        def on_progress(downloaded: int, total: int | None) -> None:
            progress.update(task_id, completed=downloaded, total=total)

        await apm.download_file(url, dest_path, on_progress=on_progress)


def _make_export_app(type_name: str, search_arg_help: str) -> typer.Typer:
    """Build the 'synology-apm m365 (exchange|group) export' sub-app (list / cancel / download).

    download auto-starts a new export when --id is omitted, or downloads an
    existing export when --id is provided.
    """
    is_group = type_name == "group"
    mailbox_label = "group mailbox" if is_group else "mailbox"
    cmd_prefix = f"synology-apm m365 {type_name} export"
    export_app = typer.Typer(
        help="Export Group mailbox to PST." if is_group else "Export Exchange mailbox to PST.",
        no_args_is_help=True,
    )

    wl_type = M365WorkloadType.GROUP if is_group else M365WorkloadType.EXCHANGE

    async def _get_workload(
        apm: APMClient, ref: WorkloadRef, tenant_id: str | None, is_retired: bool
    ) -> M365Workload:
        """Resolve the workload via get() (--workload-id/--namespace) or get_by_name() (name)."""
        wl = await ref.resolve_m365(apm, tenant_id, wl_type, is_retired=is_retired)
        print_resolved_tenant(tenant_id, wl.tenant_id)
        return wl

    # ── export list ───────────────────────────────────────────────────────

    @export_app.command("list")
    @run_async
    async def _export_list(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = _RETIRED_OPTION,
        limit: int = typer.Option(50, "--limit", help="Maximum records to show"),
        offset: int = OFFSET_OPTION,
        page_all: bool = PAGE_ALL_OPTION,
        output: ListOutputFormat = LIST_OUTPUT_OPTION,
    ) -> None:
        """List export tasks for a workload."""
        ref = validate_resolve_args(ctx, name, workload_id, namespace, id_flag="--workload-id")
        async with apm_session(ctx) as apm:
            wl = await _get_workload(apm, ref, tenant_id, is_retired=retired)
            col = apm.m365.group_export if is_group else apm.m365.exchange_export
            result = await dispatch_paginated_list(
                lambda off, lim: col.list(wl, limit=lim, offset=off),
                limit=limit, offset=offset, page_all=page_all, output=output,
                to_dict=m365_export_activity_to_dict, to_csv_row=m365_export_activity_to_csv_row,
            )

        if result is None:
            return

        activities, total = result

        if not activities:
            console.print("[dim]No export tasks found.[/dim]")
            return

        t = new_table()
        t.add_column("Item", min_width=24)
        t.add_column("Version", min_width=19)
        t.add_column("Status", min_width=20)
        t.add_column("Started", min_width=19)
        t.add_column("Finished", min_width=19)
        t.add_column("Activity ID", min_width=36)
        for a in activities:
            t.add_row(
                cell(a.source_name),
                cell(fmt_datetime(a.version_timestamp)),
                cell(fmt_export_status(a.status), styled=True),
                cell(fmt_datetime(a.started_at)),
                cell(fmt_datetime(a.finished_at)),
                cell(a.activity_id),
            )
        console.print(t)
        print_list_footer(console, len(activities), total, offset)

    # ── export cancel ─────────────────────────────────────────────────────

    _cancel_help = (
        "Cancel an in-progress export task.\n\n"
        "\b\n"
        "Search mode:\n"
        f'  {cmd_prefix} cancel "alice@contoso.com" --id <activity-id>\n\n'
        "\b\n"
        "Direct mode:\n"
        f"  {cmd_prefix} cancel --workload-id <wl-id> --namespace <ns> --id <activity-id>"
    )

    @export_app.command("cancel", help=_cancel_help)
    @run_async
    async def _export_cancel(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        activity_id: str | None = typer.Option(None, "--id", help="Activity ID (from export list)"),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = _RETIRED_OPTION,
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace, id_flag="--workload-id")
        activity_id = require_or_help(ctx, activity_id)
        async with apm_session(ctx) as apm:
            wl = await _get_workload(apm, ref, tenant_id, is_retired=retired)
            col = apm.m365.group_export if is_group else apm.m365.exchange_export
            activities, _ = await col.list(wl, limit=500)
            activity = next((a for a in activities if a.activity_id == activity_id), None)
            if activity is None:
                err_console.print(f"[red]✗[/red] Activity '{activity_id}' not found.")
                raise typer.Exit(EXIT_ERROR)
            await col.cancel(activity)

        if not quiet:
            console.print(f"[green]✓[/green] Export task {activity_id} canceled.")

    # ── export download ───────────────────────────────────────────────────

    _download_help = (
        "Start an export and download the PST file, or download an existing export.\n\n"
        "\b\n"
        "Auto-start mode (no --id): starts a new export then downloads when ready.\n"
        f'  {cmd_prefix} download "alice@contoso.com"\n'
        f'  {cmd_prefix} download "alice@contoso.com" --version-id <vid>\n'
        f'  {cmd_prefix} download "alice@contoso.com" --no-wait\n\n'
        "\b\n"
        "Direct download mode (--id): downloads a previously started export.\n"
        f'  {cmd_prefix} download "alice@contoso.com" --id <activity-id>\n'
        f"  {cmd_prefix} download --workload-id <wl-id> --namespace <ns> --id <activity-id>"
    )

    @export_app.command("download", help=_download_help)
    @run_async
    async def _export_download(
        ctx: typer.Context,
        name: str | None = typer.Argument(None, help=search_arg_help),
        activity_id: str | None = typer.Option(None, "--id", help="Activity ID (from export list)"),
        filename: str | None = typer.Option(
            None, "--filename", "-f",
            help="Output file path (e.g. mailbox.pst); auto-generated from the workload name if omitted",
        ),
        version_id: str | None = typer.Option(
            None, "--version-id", help="Version ID for auto-start (omit for latest version)"
        ),
        archive_mailbox: bool = typer.Option(
            False, "--archive-mailbox",
            help="Export archive mailbox instead of primary (auto-start only; Exchange only)",
            hidden=is_group,
        ),
        export_name: str | None = typer.Option(
            None, "--export-name",
            help='PST filename (auto-generated if omitted; auto-start only)',
        ),
        no_wait: bool = typer.Option(
            False, "--no-wait",
            help="Auto-start only: exit immediately if export is not ready; use --id <activity-id> to download later",
        ),
        workload_id: str | None = typer.Option(None, "--workload-id", help="Workload ID (direct mode)"),
        namespace: str | None = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)"),
        tenant_id: str | None = _TENANT_ID_OPTION,
        retired: bool = _RETIRED_OPTION,
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip overwrite confirmation prompt"),
    ) -> None:
        ref = validate_resolve_args(ctx, name, workload_id, namespace, id_flag="--workload-id")

        dest_path: str | None = None
        try:
            async with apm_session(ctx) as apm:
                wl = await _get_workload(apm, ref, tenant_id, is_retired=retired)

                col = apm.m365.group_export if is_group else apm.m365.exchange_export

                if activity_id is None:
                    url, dest_path = await _start_export_and_resolve_url(
                        apm, col, wl,
                        is_group=is_group,
                        mailbox_label=mailbox_label,
                        cmd_prefix=cmd_prefix,
                        version_id=version_id,
                        archive_mailbox=archive_mailbox,
                        export_name=export_name,
                        filename=filename,
                        no_wait=no_wait,
                        yes=yes,
                    )
                else:
                    url, dest_path = await _resolve_existing_export_url(
                        col, wl, activity_id=activity_id, filename=filename, yes=yes,
                    )

                await _download_with_progress(apm, url, dest_path)

        except OSError as exc:
            if dest_path is not None:
                try:
                    Path(dest_path).unlink(missing_ok=True)
                except OSError:
                    pass
            err_console.print(f"[red]✗[/red] Download failed: {exc}")
            raise typer.Exit(EXIT_ERROR)

        assert dest_path is not None
        console.print(f"[green]✓[/green] Saved to [bold]{dest_path}[/bold]")

    return export_app
