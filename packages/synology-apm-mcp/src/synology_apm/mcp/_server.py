"""MCP server factory and entry point."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from synology_apm.mcp import resources
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp.tools import activity, infra, log, m365, machine, plans
from synology_apm.sdk import APMClient


def _register_tools(registrar: ToolRegistrar) -> None:
    infra.register(registrar)
    machine.register(registrar)
    m365.register(registrar)
    plans.register(registrar)
    activity.register(registrar)
    log.register(registrar)


def _register_resources(server: FastMCP) -> None:
    resources.register(server)


def create_server(mode: str = "operator", *, lifespan: Callable[[FastMCP], Any] | None = None) -> FastMCP:
    """Create a FastMCP server with every tool/resource registered.

    Without lifespan (the default), used by the coverage check script and unit
    tests — ctx.lifespan_context is mocked in tests, so no real APMClient is
    created. run() passes its own lifespan to attach a persistent APMClient.
    """
    server = FastMCP("synology-apm", lifespan=lifespan)
    _register_tools(ToolRegistrar(server, mode))
    _register_resources(server)
    return server


def tool_required_modes() -> dict[str, str]:
    """Return {tool_name: required_mode} for every tool the server can register.

    A single source to look up a tool's required mode without grepping source
    for its registration call site — every tool is recorded regardless of
    whether the given mode would actually register it (see ToolRegistrar).
    """
    registrar = ToolRegistrar(FastMCP("synology-apm"), "admin")
    _register_tools(registrar)
    return dict(registrar.required_modes)


def run(  # pragma: no cover
    host: str,
    username: str,
    password: str,
    *,
    verify_ssl: bool,
    debug: bool,
    mode: str,
) -> None:
    """Create a server with a persistent APMClient lifespan and run it (stdio transport)."""

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        async with APMClient(host, username, password, verify_ssl=verify_ssl, debug=debug) as apm:
            yield {"apm": apm}

    server = create_server(mode, lifespan=lifespan)
    server.run()
