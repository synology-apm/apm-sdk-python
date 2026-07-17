"""Protection, retirement, and tiering plan tools."""
from __future__ import annotations

from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp.tools.plans import common, m365, machine, retirement, tiering


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register all plan tools onto server."""
    common.register(registrar)
    machine.register(registrar)
    m365.register(registrar)
    retirement.register(registrar)
    tiering.register(registrar)
