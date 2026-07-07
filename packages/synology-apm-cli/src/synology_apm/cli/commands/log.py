"""synology-apm log — server-scoped log commands."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    fmt_activity_log_type,
    fmt_datetime,
    fmt_server_log_level,
    print_list_footer,
)
from synology_apm.cli._helpers import apm_session
from synology_apm.cli._options import (
    LIMIT_OPTION,
    LIST_OUTPUT_OPTION,
    OFFSET_OPTION,
    PAGE_ALL_OPTION,
    SINCE_OPTION,
    UNTIL_OPTION,
)
from synology_apm.cli._serializers import (
    activity_log_to_dict,
    connection_log_to_dict,
    drive_log_to_dict,
    system_log_to_dict,
)
from synology_apm.cli._validate import parse_time_range, validate_name_or_id_args
from synology_apm.cli.errors import err_console
from synology_apm.cli.output import ListOutputFormat, cell, console, dispatch_paginated_list, new_table
from synology_apm.sdk import (
    APMActivityLog,
    APMActivityLogType,
    APMClient,
    BackupServer,
    BackupServerType,
    ConnectionLog,
    DriveLog,
    LogLevel,
    SystemLog,
)

app = typer.Typer(
    name="log",
    help="Query backup server logs.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

_activity_app   = typer.Typer(name="activity",   help="Activity logs.",           no_args_is_help=True)
_drive_app      = typer.Typer(name="drive",      help="Drive information logs.",  no_args_is_help=True)
_conn_app       = typer.Typer(name="connection", help="Connection logs.",         no_args_is_help=True)
_system_app     = typer.Typer(name="system",     help="Advanced system logs.",    no_args_is_help=True)

app.add_typer(_activity_app,   name="activity")
app.add_typer(_drive_app,      name="drive")
app.add_typer(_conn_app,       name="connection")
app.add_typer(_system_app,     name="system")


# ── Argument validation ───────────────────────────────────────────────────────

async def _resolve_server(apm: APMClient, name: str | None, server_id: str | None) -> BackupServer:
    """Resolve server search or direct ID to a BackupServer, and enforce DP-only requirement."""
    if name is not None:
        server = await apm.backup_servers.get_by_name(name)
    else:
        assert server_id is not None
        server = await apm.backup_servers.get(server_id)
    if server.server_type != BackupServerType.DP:
        err_console.print(
            f"[red]✗[/red] '{server.name}' is a NAS server — "
            "log commands only work on DP (ActiveProtect Appliance) servers."
        )
        raise typer.Exit(code=1)
    return server


# ── Shared list runner ────────────────────────────────────────────────────────

# (header, rich Table.add_column kwargs) pairs describing one log table layout.
_ColumnSpec = tuple[str, dict[str, Any]]

_USER_EVENT_COLUMNS: list[_ColumnSpec] = [
    ("Level", {"width": 11, "no_wrap": True}),
    ("Time",  {"width": 19, "no_wrap": True}),
    ("User",  {"min_width": 8, "no_wrap": True}),
    ("Event", {"ratio": 1}),
]


def _user_event_row(e: ConnectionLog | SystemLog) -> list[str]:
    return [
        cell(fmt_server_log_level(e.level), styled=True),
        cell(fmt_datetime(e.timestamp)),
        cell(e.username),
        cell(e.description),
    ]


async def _run_log_list(
    ctx: typer.Context,
    *,
    name: str | None,
    server_id: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    offset: int,
    page_all: bool,
    output: ListOutputFormat,
    spinner: str,
    list_fn: Callable[
        [APMClient, BackupServer, datetime | None, datetime | None, int, int],
        Awaitable[tuple[Sequence[Any], int]],
    ],
    to_dict: Callable[[Any], dict[str, Any]],
    columns: list[_ColumnSpec],
    row_fn: Callable[[Any], list[str]],
) -> None:
    """Shared body for the four `log <kind> list` commands.

    Validates the server argument, opens the session, pages via list_fn, and
    renders either the dispatched output format or the given table layout.
    """
    validate_name_or_id_args(ctx, name, server_id, exclusive_msg="<server> cannot be used with --id")
    since_dt, until_dt = parse_time_range(since, until)

    async with apm_session(ctx, spinner=spinner) as apm:
        server = await _resolve_server(apm, name, server_id)
        result = await dispatch_paginated_list(
            lambda off, lim: list_fn(apm, server, since_dt, until_dt, off, lim),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=to_dict,
        )

    if result is None:
        return

    logs, total = result

    t = new_table(expand=True)
    for header, kwargs in columns:
        t.add_column(header, **kwargs)
    for e in logs:
        t.add_row(*row_fn(e))
    console.print(t)
    print_list_footer(console, len(logs), total or None)


# ═══════════════════════════════════════════════════════════════════════════════
# synology-apm log activity list
# ═══════════════════════════════════════════════════════════════════════════════

@_activity_app.command("list")
@run_async
async def activity_list(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Server name keyword (search mode)"),
    server_id: str | None = typer.Option(
        None, "--id",
        help="Backup Server ID (direct mode; from synology-apm infra server list --verbose)",
    ),
    level: list[LogLevel] | None = typer.Option(
        None, "--level",
        help="Severity filter, repeatable: information / warning / error",
    ),
    log_type: APMActivityLogType | None = typer.Option(
        None, "--type",
        help="Log type filter: protection / system / data_access",
    ),
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    offset: int = OFFSET_OPTION,
    limit: int = LIMIT_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List activity logs for a backup server.

    \b
    Search mode (server name keyword):
      synology-apm log activity list "apm-server-01"

    \b
    Direct mode (server ID from synology-apm infra server list --verbose):
      synology-apm log activity list --id <server-id>
    """
    def _row(e: APMActivityLog) -> list[str]:
        return [
            cell(fmt_server_log_level(e.level), styled=True),
            cell(fmt_activity_log_type(e.log_type)),
            cell(fmt_datetime(e.timestamp)),
            cell(e.username),
            cell(e.description),
        ]

    await _run_log_list(
        ctx, name=name, server_id=server_id, since=since, until=until,
        limit=limit, offset=offset, page_all=page_all, output=output,
        spinner="Fetching activity logs...",
        list_fn=lambda apm, server, s, u, off, lim: apm.logs.list_activity(
            server, levels=level or None, log_type=log_type,
            since=s, until=u, keyword=search, limit=lim, offset=off,
        ),
        to_dict=activity_log_to_dict,
        columns=[
            ("Level", {"width": 11, "no_wrap": True}),
            ("Type",  {"width": 17, "no_wrap": True}),
            ("Time",  {"width": 19, "no_wrap": True}),
            ("User",  {"min_width": 8, "no_wrap": True}),
            ("Event", {"ratio": 1}),
        ],
        row_fn=_row,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# synology-apm log drive list
# ═══════════════════════════════════════════════════════════════════════════════

@_drive_app.command("list")
@run_async
async def drive_list(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Server name keyword (search mode)"),
    server_id: str | None = typer.Option(
        None, "--id",
        help="Backup Server ID (direct mode; from synology-apm infra server list --verbose)",
    ),
    level: list[LogLevel] | None = typer.Option(
        None, "--level",
        help="Severity filter, repeatable: information / warning / error",
    ),
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    location: str | None = typer.Option(None, "--location", help="Drive location filter"),
    offset: int = OFFSET_OPTION,
    limit: int = LIMIT_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List drive information logs for a backup server.

    \b
    Search mode (server name keyword):
      synology-apm log drive list "apm-server-01"

    \b
    Direct mode (server ID from synology-apm infra server list --verbose):
      synology-apm log drive list --id <server-id>
    """
    def _row(e: DriveLog) -> list[str]:
        return [
            cell(fmt_server_log_level(e.level), styled=True),
            cell(fmt_datetime(e.timestamp)),
            cell(e.model),
            cell(e.serial),
            cell(e.server_name),
            cell(e.location),
            cell(e.description),
        ]

    await _run_log_list(
        ctx, name=name, server_id=server_id, since=since, until=until,
        limit=limit, offset=offset, page_all=page_all, output=output,
        spinner="Fetching drive logs...",
        list_fn=lambda apm, server, s, u, off, lim: apm.logs.list_drive(
            server, levels=level or None, since=s, until=u,
            keyword=search, location=location, limit=lim, offset=off,
        ),
        to_dict=drive_log_to_dict,
        columns=[
            ("Level",         {"width": 11, "no_wrap": True}),
            ("Time",          {"width": 19, "no_wrap": True}),
            ("Model",         {"min_width": 10, "no_wrap": True}),
            ("Serial Number", {"min_width": 12, "no_wrap": True}),
            ("Server Name",   {"min_width": 10, "no_wrap": True}),
            ("Location",      {"min_width": 8, "no_wrap": True}),
            ("Event",         {"ratio": 1}),
        ],
        row_fn=_row,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# synology-apm log connection list
# ═══════════════════════════════════════════════════════════════════════════════

@_conn_app.command("list")
@run_async
async def connection_list(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Server name keyword (search mode)"),
    server_id: str | None = typer.Option(
        None, "--id",
        help="Backup Server ID (direct mode; from synology-apm infra server list --verbose)",
    ),
    level: list[LogLevel] | None = typer.Option(
        None, "--level",
        help="Severity filter, repeatable: information / warning / error",
    ),
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    offset: int = OFFSET_OPTION,
    limit: int = LIMIT_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List connection logs for a backup server.

    \b
    Search mode (server name keyword):
      synology-apm log connection list "apm-server-01"

    \b
    Direct mode (server ID from synology-apm infra server list --verbose):
      synology-apm log connection list --id <server-id>
    """
    await _run_log_list(
        ctx, name=name, server_id=server_id, since=since, until=until,
        limit=limit, offset=offset, page_all=page_all, output=output,
        spinner="Fetching connection logs...",
        list_fn=lambda apm, server, s, u, off, lim: apm.logs.list_connection(
            server, levels=level or None, since=s, until=u,
            keyword=search, limit=lim, offset=off,
        ),
        to_dict=connection_log_to_dict,
        columns=_USER_EVENT_COLUMNS,
        row_fn=_user_event_row,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# synology-apm log system list
# ═══════════════════════════════════════════════════════════════════════════════

@_system_app.command("list")
@run_async
async def system_list(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Server name keyword (search mode)"),
    server_id: str | None = typer.Option(
        None, "--id",
        help="Backup Server ID (direct mode; from synology-apm infra server list --verbose)",
    ),
    level: list[LogLevel] | None = typer.Option(
        None, "--level",
        help="Severity filter, repeatable: information / warning / error",
    ),
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    search: str | None = typer.Option(None, "--search", help="Keyword search"),
    offset: int = OFFSET_OPTION,
    limit: int = LIMIT_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List advanced system logs for a backup server.

    \b
    Search mode (server name keyword):
      synology-apm log system list "apm-server-01"

    \b
    Direct mode (server ID from synology-apm infra server list --verbose):
      synology-apm log system list --id <server-id>
    """
    await _run_log_list(
        ctx, name=name, server_id=server_id, since=since, until=until,
        limit=limit, offset=offset, page_all=page_all, output=output,
        spinner="Fetching system logs...",
        list_fn=lambda apm, server, s, u, off, lim: apm.logs.list_system(
            server, levels=level or None, since=s, until=u,
            keyword=search, limit=lim, offset=off,
        ),
        to_dict=system_log_to_dict,
        columns=_USER_EVENT_COLUMNS,
        row_fn=_user_event_row,
    )
