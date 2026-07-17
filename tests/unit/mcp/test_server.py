"""Tests for _server.py: create_server factory."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastmcp import FastMCP

_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "scripts" / "mcp_coverage.toml"


def _load_expected_tool_modes() -> dict[str, str]:
    """Ground truth for every tool's minimum required mode, derived from the single
    declared source of intent (mcp_coverage.toml's [[mapping]] `mode` field) rather
    than a hand-typed mirror — otherwise this dict and the manifest, and each tool's
    own inline mode_allows() gate, are three independently-maintained copies of the
    same fact. This only checks the manifest agrees with what create_server() actually
    registers (via the tests below); scripts/check_mcp_coverage.py separately checks the
    manifest against the inline mode_allows() gates themselves.
    """
    with open(_MANIFEST_PATH, "rb") as f:
        data = tomllib.load(f)
    return {m["mcp_tool"]: m["mode"] for m in data.get("mapping", [])}


_EXPECTED_TOOL_MODES = _load_expected_tool_modes()


class TestCreateServer:
    def test_returns_fastmcp_instance(self):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        assert isinstance(server, FastMCP)
        assert server.name == "synology-apm"

    @pytest.mark.asyncio
    async def test_readonly_mode_registers_fewer_tools(self):
        from synology_apm.mcp._server import create_server

        readonly = create_server(mode="readonly")
        admin = create_server(mode="admin")
        readonly_names = {t.name for t in (await readonly.list_tools())}
        admin_names = {t.name for t in (await admin.list_tools())}

        assert len(readonly_names) < len(admin_names)
        assert "delete_machine_workload" not in readonly_names
        assert "delete_machine_workload" in admin_names
        assert "list_machine_workloads" in readonly_names
        assert "list_machine_workloads" in admin_names

    @pytest.mark.asyncio
    async def test_hidden_tool_raises_not_found_when_called_directly(self):
        """Mode gating works by never registering the tool, not by filtering
        tools/list after the fact — calling a hidden tool's name directly
        (as a client that learned it elsewhere would) must fail at lookup."""
        from fastmcp.exceptions import NotFoundError

        from synology_apm.mcp._server import create_server

        server = create_server(mode="readonly")
        with pytest.raises(NotFoundError, match="delete_machine_workload"):
            await server.call_tool("delete_machine_workload", {})


class TestPerToolModeGating:
    """Every tool's mode gate, checked individually (not just one representative
    tool per level) — a tool accidentally registered inside the wrong
    mode_allows() block, or outside any block, changes its computed mode here
    without touching _EXPECTED_TOOL_MODES, so the mismatch surfaces immediately."""

    @pytest.mark.asyncio
    async def test_table_covers_every_registered_tool(self):
        """Guards the guard: a newly added tool with no entry in
        _EXPECTED_TOOL_MODES must fail loudly here rather than silently
        skipping mode-gate coverage."""
        from synology_apm.mcp._server import create_server

        admin = create_server(mode="admin")
        admin_names = {t.name for t in (await admin.list_tools())}

        assert admin_names == set(_EXPECTED_TOOL_MODES)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["readonly", "operator", "manager", "admin"])
    async def test_registered_tools_match_expected_modes(self, mode):
        from synology_apm.mcp._security import mode_allows
        from synology_apm.mcp._server import create_server

        server = create_server(mode=mode)
        actual_names = {t.name for t in (await server.list_tools())}
        expected_names = {
            name for name, required in _EXPECTED_TOOL_MODES.items()
            if mode_allows(required, mode)
        }

        assert actual_names == expected_names
