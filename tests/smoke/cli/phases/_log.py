"""log phase — ``log activity|drive|connection|system list`` (DP-type backup servers only).

The whole phase is skipped if the infra phase's ``infra server list --type dp`` found no
DP-type (ActiveProtect Appliance) backup server — ``log`` commands only work on DP servers.
"""
from __future__ import annotations

from .._context import SmokeContext

DOMAIN = "log"


def run(ctx: SmokeContext) -> None:
    dp_servers = ctx.data.get("dp_servers") or []
    if not dp_servers:
        ctx.skip(DOMAIN, "log.*", "No DP-type backup server found (see infra.server.list[dp]) — log phase skipped.")
        return

    server = dp_servers[0]
    name = server["name"]
    server_id = server["backup_server_id"]

    _run_activity(ctx, name, server_id)
    _run_drive(ctx, name, server_id)
    _run_connection(ctx, name, server_id)
    _run_system(ctx, name, server_id)


def _run_activity(ctx: SmokeContext, name: str, server_id: str) -> None:
    ctx.run_both(DOMAIN, "log.activity.list[search]", ["log", "activity", "list", name])
    ctx.run_both(DOMAIN, "log.activity.list[direct]", ["log", "activity", "list", "--id", server_id])
    ctx.run_both(
        DOMAIN, "log.activity.list[filters]",
        ["log", "activity", "list", name, "--level", "warning", "--level", "error",
         "--type", "protection", "--since", "24h"],
    )
    ctx.run(
        DOMAIN, "log.activity.list[page-all]",
        ["log", "activity", "list", name, "--page-all", "--since", "24h"],
        output_format="json",
        note="Exercises NDJSON streaming for --page-all; bounded to 24h so full pagination"
        " fits the runner timeout on log-heavy servers.",
    )
    ctx.run(DOMAIN, "log.activity.list[csv]", ["log", "activity", "list", name], output_format="csv")
    ctx.run(DOMAIN, "log.activity.list[yaml]", ["log", "activity", "list", name], output_format="yaml")


def _run_drive(ctx: SmokeContext, name: str, server_id: str) -> None:
    ctx.run_both(DOMAIN, "log.drive.list[search]", ["log", "drive", "list", name])
    ctx.run_both(DOMAIN, "log.drive.list[direct]", ["log", "drive", "list", "--id", server_id])
    ctx.run_both(
        DOMAIN, "log.drive.list[filters]",
        ["log", "drive", "list", name, "--level", "warning", "--since", "24h", "--location", "front"],
    )


def _run_connection(ctx: SmokeContext, name: str, server_id: str) -> None:
    ctx.run_both(DOMAIN, "log.connection.list[search]", ["log", "connection", "list", name])
    ctx.run_both(DOMAIN, "log.connection.list[direct]", ["log", "connection", "list", "--id", server_id])
    ctx.run_both(
        DOMAIN, "log.connection.list[filters]",
        ["log", "connection", "list", name, "--level", "warning", "--since", "24h"],
    )


def _run_system(ctx: SmokeContext, name: str, server_id: str) -> None:
    ctx.run_both(DOMAIN, "log.system.list[search]", ["log", "system", "list", name])
    ctx.run_both(DOMAIN, "log.system.list[direct]", ["log", "system", "list", "--id", server_id])
    ctx.run_both(
        DOMAIN, "log.system.list[filters]",
        ["log", "system", "list", name, "--level", "warning", "--since", "24h"],
    )
