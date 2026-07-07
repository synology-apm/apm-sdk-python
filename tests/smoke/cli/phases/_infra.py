"""infra phase — ``infra info`` / ``server`` / ``storage`` / ``hypervisor``.

Discovers backup servers (including DP-type appliances, needed by the ``log`` phase)
and exercises both search-mode and direct (``--id``) lookups for each infra resource.
"""
from __future__ import annotations

from .._context import SmokeContext, parse_json

DOMAIN = "infra"


def run(ctx: SmokeContext) -> None:
    ctx.run_both(DOMAIN, "infra.info", ["infra", "info"])

    ctx.run(
        DOMAIN, "infra.server.list[verbose]", ["infra", "server", "list", "--verbose"],
        output_format="table",
        note="--verbose only affects the table renderer (extra columns); json output is unchanged.",
    )
    _, server_json = ctx.run_both(DOMAIN, "infra.server.list", ["infra", "server", "list"])
    ctx.run(
        DOMAIN, "infra.server.list[page-all]", ["infra", "server", "list", "--page-all"],
        output_format="json", note="Exercises NDJSON streaming for --page-all.",
    )
    ctx.run(DOMAIN, "infra.server.list[csv]", ["infra", "server", "list"], output_format="csv")
    ctx.run(DOMAIN, "infra.server.list[yaml]", ["infra", "server", "list"], output_format="yaml")

    servers = parse_json(server_json) or []
    ctx.data["servers"] = servers

    _, dp_json = ctx.run_both(DOMAIN, "infra.server.list[dp]", ["infra", "server", "list", "--type", "dp"])
    dp_servers = parse_json(dp_json) or []
    ctx.data["dp_servers"] = dp_servers
    if not dp_servers:
        ctx.skip(
            DOMAIN, "infra.server.list[dp][data]",
            "No DP-type (ActiveProtect Appliance) backup servers found — the log phase will be skipped.",
        )

    if servers:
        first_server = servers[0]
        ctx.run_both(DOMAIN, "infra.server.get[search]", ["infra", "server", "get", first_server["name"]])
        ctx.run_both(
            DOMAIN, "infra.server.get[direct]",
            ["infra", "server", "get", "--id", first_server["backup_server_id"]],
        )
    else:
        ctx.skip(DOMAIN, "infra.server.get", "No backup servers found.")

    _, storage_json = ctx.run_both(DOMAIN, "infra.storage.list", ["infra", "storage", "list"])
    storages = parse_json(storage_json) or []
    if storages:
        first_storage = storages[0]
        ctx.run_both(
            DOMAIN, "infra.storage.get[search]",
            ["infra", "storage", "get", first_storage["name"]],
        )
        ctx.run_both(
            DOMAIN, "infra.storage.get[direct]",
            ["infra", "storage", "get", "--id", first_storage["storage_id"]],
        )
    else:
        ctx.skip(DOMAIN, "infra.storage.get", "No remote storage devices configured.")

    _, hypervisor_json = ctx.run_both(DOMAIN, "infra.hypervisor.list", ["infra", "hypervisor", "list"])
    hypervisors = parse_json(hypervisor_json) or []
    if hypervisors:
        first_hypervisor = hypervisors[0]
        ctx.run_both(
            DOMAIN, "infra.hypervisor.get[search]",
            ["infra", "hypervisor", "get", first_hypervisor["hostname"]],
        )
        ctx.run_both(
            DOMAIN, "infra.hypervisor.get[direct]",
            ["infra", "hypervisor", "get", "--id", first_hypervisor["hypervisor_id"]],
        )
    else:
        ctx.skip(DOMAIN, "infra.hypervisor.get", "No hypervisors registered.")
