"""ToolRegistrar: single declarative call-site for mode-gated tool registration."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastmcp import FastMCP

from synology_apm.mcp._security import mode_allows

_F = TypeVar("_F", bound=Callable[..., Any])


class ToolRegistrar:
    """Wraps a FastMCP server + the current APM_MCP_MODE for tool registration.

    `tool()` is used as `@registrar.tool(required_mode, description=...)` above
    a tool function, or called directly with `name=` for a dynamically-named
    function — either way the required mode is declared right next to the
    tool it gates.

    required_modes records every tool considered — registered or not — before
    the mode_allows() check runs, so a single register() pass yields a complete
    {tool_name: required_mode} table (see _server.py::tool_required_modes()).
    """

    def __init__(self, server: FastMCP, mode: str) -> None:
        self._server = server
        self._mode = mode
        self.required_modes: dict[str, str] = {}

    def tool(self, required_mode: str = "readonly", *, name: str | None = None, description: str) -> Callable[[_F], _F]:
        def decorate(fn: _F) -> _F:
            tool_name = name if name is not None else fn.__name__
            self.required_modes[tool_name] = required_mode
            if mode_allows(required_mode, self._mode):
                if name is not None:
                    fn.__name__ = name
                self._server.tool(description=description)(fn)
            return fn

        return decorate
