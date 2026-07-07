"""Log domain phase: apm.logs.* against DP-type backup servers.

Depends on ctx.data["dp_servers"], populated by the infra phase.
"""
from __future__ import annotations

from synology_apm.sdk import LogLevel

from .._context import SmokeContext

DOMAIN = "log"


async def run(ctx: SmokeContext) -> None:
    apm = ctx.apm
    dp_servers = ctx.data.get("dp_servers", [])

    if not dp_servers:
        for step in (
            "log.activity.list", "log.activity.list[filtered]", "log.drive.list",
            "log.connection.list", "log.system.list",
            "log.drive.check[real_total]", "log.activity.check[hardcoded_total_zero]",
            "log.connection.check[hardcoded_total_zero]", "log.system.check[hardcoded_total_zero]",
        ):
            ctx.skip(DOMAIN, step, "No DP-type backup server found")
        return

    server = dp_servers[0]

    activity_result = await ctx.call(DOMAIN, "log.activity.list", lambda: apm.logs.list_activity(server, limit=25))
    _activity_entries, activity_total = activity_result if activity_result is not None else ([], 0)

    await ctx.call(
        DOMAIN, "log.activity.list[filtered]",
        lambda: apm.logs.list_activity(server, levels=[LogLevel.ERROR, LogLevel.WARNING], limit=25),
    )

    drive_result = await ctx.call(DOMAIN, "log.drive.list", lambda: apm.logs.list_drive(server, limit=25))
    drive_entries, drive_total = drive_result if drive_result is not None else ([], 0)

    connection_result = await ctx.call(
        DOMAIN, "log.connection.list", lambda: apm.logs.list_connection(server, limit=25)
    )
    _connection_entries, connection_total = connection_result if connection_result is not None else ([], 0)

    system_result = await ctx.call(DOMAIN, "log.system.list", lambda: apm.logs.list_system(server, limit=25))
    _system_entries, system_total = system_result if system_result is not None else ([], 0)

    ctx.check(
        DOMAIN, "log.drive.check[real_total]",
        (drive_total == 0 and len(drive_entries) == 0) or drive_total > 0,
        note="list_drive() reports a real server-side total: either 0 with no entries, or > 0.",
    )
    ctx.check(
        DOMAIN, "log.activity.check[hardcoded_total_zero]", activity_total == 0,
        note="list_activity() always reports total == 0 (no server-side total for activity logs).",
    )
    ctx.check(
        DOMAIN, "log.connection.check[hardcoded_total_zero]", connection_total == 0,
        note="list_connection() always reports total == 0 (no server-side total).",
    )
    ctx.check(
        DOMAIN, "log.system.check[hardcoded_total_zero]", system_total == 0,
        note="list_system() always reports total == 0 (no server-side total).",
    )
