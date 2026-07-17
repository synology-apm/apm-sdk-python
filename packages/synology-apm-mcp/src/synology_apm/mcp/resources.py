"""MCP resources: URI-addressable reference entities agents read as context."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import Context, FastMCP

from synology_apm.mcp._errors import run_resource
from synology_apm.mcp._helpers import MAX_LIST_LIMIT, list_result
from synology_apm.sdk import APMClient

# (uri, description, collection accessor) — these 5 resources are otherwise
# byte-for-byte identical: fetch up to MAX_LIST_LIMIT items and wrap via list_result.
_LIST_RESOURCES: list[tuple[str, str, Callable[[APMClient], Any]]] = [
    ("apm://servers", "All backup servers with storage summary.", lambda apm: apm.backup_servers),
    ("apm://plans/protection", "All protection plans (machine and M365).", lambda apm: apm.plans),
    ("apm://plans/retirement", "All retirement plans.", lambda apm: apm.retirement_plans),
    ("apm://plans/tiering", "All tiering plans.", lambda apm: apm.tiering_plans),
    ("apm://tenants", "All M365/SaaS tenants registered in APM.", lambda apm: apm.saas),
]


def register(server: FastMCP) -> None:  # pragma: no cover
    """Register all MCP resources onto server."""

    @server.resource("apm://site", description="APM site overview: site UUID, external address, management servers, storage, and workload counts.")
    async def site_resource(ctx: Context) -> str:  # pragma: no cover
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_resource(apm.get_site_info(), lambda x: x.to_dict())

    def _make_list_resource(collection_fn: Callable[[APMClient], Any]) -> Callable[[Context], Any]:
        # A factory (rather than a plain loop-body closure) so each resource captures
        # its own collection_fn — a closure over the loop variable directly would have
        # every resource see whatever collection_fn the loop last landed on.
        async def _list_resource(ctx: Context) -> str:  # pragma: no cover
            apm: APMClient = ctx.lifespan_context["apm"]
            return await run_resource(
                list_result(collection_fn(apm).list(limit=MAX_LIST_LIMIT), lambda x: x.to_dict()),
                lambda x: x,
            )

        return _list_resource

    for uri, description, collection_fn in _LIST_RESOURCES:
        resource_fn = _make_list_resource(collection_fn)
        resource_fn.__name__ = f"resource_{uri.split('://')[1].replace('/', '_')}"
        server.resource(uri, description=description)(resource_fn)

    @server.resource("apm://server/{server_id}", description="A single backup server by ID.")
    async def server_by_id_resource(server_id: str, ctx: Context) -> str:  # pragma: no cover
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_resource(apm.backup_servers.get(server_id), lambda x: x.to_dict())
