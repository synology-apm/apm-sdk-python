"""activity phase — ``activity backup``/``activity restore`` query commands.

Search-mode steps take their workload name from the activity history itself, so they are
conditional on history already containing records. ``activity restore cancel`` is covered
by the SDK smoke test and is intentionally excluded here.
"""
from __future__ import annotations

from .._context import SmokeContext, parse_json

DOMAIN = "activity"


def run(ctx: SmokeContext) -> None:
    _run_backup(ctx)
    _run_restore(ctx)


def _run_backup(ctx: SmokeContext) -> None:
    ctx.run_both(DOMAIN, "activity.backup.list[ongoing]", ["activity", "backup", "list"])

    _, history_json = ctx.run_both(
        DOMAIN, "activity.backup.list[history]", ["activity", "backup", "list", "--history"],
    )
    activities = parse_json(history_json) or []
    ctx.data["backup_activities"] = activities

    ctx.run_both(
        DOMAIN, "activity.backup.list[status]",
        ["activity", "backup", "list", "--history", "--status", "success"],
    )
    ctx.run_both(
        DOMAIN, "activity.backup.list[since]",
        ["activity", "backup", "list", "--history", "--since", "24h"],
    )
    ctx.run_both(
        DOMAIN, "activity.backup.list[machine-type]",
        ["activity", "backup", "list", "--history", "--machine-type", "vm"],
    )
    ctx.run_both(
        DOMAIN, "activity.backup.list[m365-type]",
        ["activity", "backup", "list", "--history", "--m365-type", "exchange"],
    )
    ctx.run(
        DOMAIN, "activity.backup.list[page-all]", ["activity", "backup", "list", "--history", "--page-all"],
        output_format="json", note="Exercises NDJSON streaming for --page-all.",
    )
    ctx.run(DOMAIN, "activity.backup.list[csv]", ["activity", "backup", "list", "--history"], output_format="csv")
    ctx.run(DOMAIN, "activity.backup.list[yaml]", ["activity", "backup", "list", "--history"], output_format="yaml")

    if not activities:
        ctx.skip(
            DOMAIN, "activity.backup.list[search] / activity.backup.get",
            "No backup activity history found.",
        )
        return

    first = activities[0]
    name = first["workload_name"]
    ctx.run_both(
        DOMAIN, "activity.backup.list[search]",
        ["activity", "backup", "list", "--history", "--search", name],
    )
    ctx.run_both(DOMAIN, "activity.backup.get[search]", ["activity", "backup", "get", name])
    ctx.run_both(DOMAIN, "activity.backup.get[direct]", ["activity", "backup", "get", "--id", first["activity_id"]])


def _run_restore(ctx: SmokeContext) -> None:
    ctx.run_both(DOMAIN, "activity.restore.list[ongoing]", ["activity", "restore", "list"])

    _, history_json = ctx.run_both(
        DOMAIN, "activity.restore.list[history]", ["activity", "restore", "list", "--history"],
    )
    activities = parse_json(history_json) or []
    ctx.data["restore_activities"] = activities

    ctx.run_both(
        DOMAIN, "activity.restore.list[status]",
        ["activity", "restore", "list", "--history", "--status", "success"],
    )
    ctx.run_both(
        DOMAIN, "activity.restore.list[since]",
        ["activity", "restore", "list", "--history", "--since", "24h"],
    )

    if not activities:
        ctx.skip(
            DOMAIN, "activity.restore.list[search] / activity.restore.get",
            "No restore activity history found.",
        )
        return

    first = activities[0]
    name = first["workload_name"]
    ctx.run_both(
        DOMAIN, "activity.restore.list[search]",
        ["activity", "restore", "list", "--history", "--search", name],
    )
    ctx.run_both(DOMAIN, "activity.restore.get[search]", ["activity", "restore", "get", name])
    ctx.run_both(DOMAIN, "activity.restore.get[direct]", ["activity", "restore", "get", "--id", first["activity_id"]])
