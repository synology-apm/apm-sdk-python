"""MCP server factory and entry point."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from synology_apm.mcp import resources
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp.tools import activity, infra, log, m365, machine, plans
from synology_apm.sdk import APMClient, APMError


class _FailedConnectionClient:
    """Placeholder for ctx.lifespan_context["apm"] when the initial APM connection failed.

    Every attribute access returns itself, and calling it returns a coroutine that raises
    the original connect exception when awaited. Every tool/resource body does
    `apm.<collection>.<method>(...)` unchanged, so this lets the existing
    run_tool/run_resource/run_audited_tool/destructive_tool error-handling wrappers
    convert the connection failure into a structured JSON error, instead of the whole
    server process crashing before any MCP session is established.
    """

    def __init__(self, exc: APMError) -> None:
        self._exc = exc

    def __getattr__(self, _name: str) -> _FailedConnectionClient:
        return self

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise self._exc


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


def build_lifespan(
    host: str, username: str, password: str, *, verify_ssl: bool, debug: bool, config_error: APMError | None = None
) -> Callable[[FastMCP], Any]:
    """Build the lifespan callable used by run(): a persistent APMClient connection, or --
    if config_error is already known (no usable credentials were found, so a real connect
    would only fail) or the initial connect fails (bad credentials, unreachable host, SSL
    error, or the host is not the primary APM management server) -- a placeholder that turns
    every subsequent tool/resource call into a structured JSON error instead of crashing the
    server at startup.
    """

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        if config_error is not None:
            yield {"apm": _FailedConnectionClient(config_error)}
            return
        try:
            async with APMClient(host, username, password, verify_ssl=verify_ssl, debug=debug) as apm:
                yield {"apm": apm}
        except APMError as exc:
            yield {"apm": _FailedConnectionClient(exc)}

    return lifespan


def run(  # pragma: no cover
    host: str,
    username: str,
    password: str,
    *,
    verify_ssl: bool,
    debug: bool,
    mode: str,
    config_error: APMError | None = None,
) -> None:
    """Create a server with a persistent APMClient lifespan and run it (stdio transport)."""
    lifespan = build_lifespan(
        host, username, password, verify_ssl=verify_ssl, debug=debug, config_error=config_error
    )
    server = create_server(mode, lifespan=lifespan)
    server.run()
