"""Tests for _registrar.py: ToolRegistrar."""
from __future__ import annotations

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError


class TestToolRegistrar:
    @pytest.mark.asyncio
    async def test_registers_when_mode_allows(self):
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        @registrar.tool("admin", description="An admin tool.")
        async def delete_thing() -> str:
            return "deleted"

        tool = await server.get_tool("delete_thing")
        assert tool is not None
        assert registrar.required_modes["delete_thing"] == "admin"

    @pytest.mark.asyncio
    async def test_skips_registration_when_mode_insufficient(self):
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "readonly")

        @registrar.tool("admin", description="An admin tool.")
        async def delete_thing() -> str:
            return "deleted"

        with pytest.raises(NotFoundError):
            await server.call_tool("delete_thing", {})
        # still recorded, even though not registered at this mode
        assert registrar.required_modes["delete_thing"] == "admin"

    def test_defaults_to_readonly(self):
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "readonly")

        @registrar.tool(description="A readonly tool.")
        async def list_things() -> str:
            return "[]"

        assert registrar.required_modes["list_things"] == "readonly"

    @pytest.mark.asyncio
    async def test_decorator_form_uses_function_name(self):
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        @registrar.tool("operator", description="...")
        async def backup_machine_workload() -> str:
            return "ok"

        tool = await server.get_tool("backup_machine_workload")
        assert tool is not None

    @pytest.mark.asyncio
    async def test_direct_call_form_registers_under_given_name(self):
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        async def _delete(plan_id: str) -> str:
            return plan_id

        registrar.tool("admin", name="delete_tiering_plan", description="...")(_delete)

        tool = await server.get_tool("delete_tiering_plan")
        assert tool is not None
        assert _delete.__name__ == "delete_tiering_plan"
        assert registrar.required_modes["delete_tiering_plan"] == "admin"

    @pytest.mark.asyncio
    async def test_required_modes_populated_for_registered_and_skipped(self):
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "operator")

        @registrar.tool(description="readonly tool")
        async def list_things() -> str:
            return "[]"

        @registrar.tool("operator", description="operator tool")
        async def backup_thing() -> str:
            return "ok"

        @registrar.tool("admin", description="admin tool")
        async def delete_thing() -> str:
            return "deleted"

        assert registrar.required_modes == {
            "list_things": "readonly",
            "backup_thing": "operator",
            "delete_thing": "admin",
        }
        assert await server.get_tool("list_things") is not None
        assert await server.get_tool("backup_thing") is not None
        with pytest.raises(NotFoundError):
            await server.call_tool("delete_thing", {})


class TestToolRequiredModes:
    def test_returns_complete_table(self):
        """tool_required_modes() enumerates every tool the server can register,
        regardless of mode — the centralized {tool_name: required_mode} lookup."""
        from synology_apm.mcp._server import tool_required_modes

        table = tool_required_modes()

        assert table["list_machine_workloads"] == "readonly"
        assert table["backup_machine_workload"] == "operator"
        assert table["lock_machine_version"] == "manager"
        assert table["delete_machine_workload"] == "admin"

    @pytest.mark.asyncio
    async def test_matches_admin_mode_registered_tools(self):
        """The table's key set must equal what create_server(mode="admin") actually
        registers — every tool considered ends up registered at the top mode."""
        from synology_apm.mcp._server import create_server, tool_required_modes

        table = tool_required_modes()
        admin_server = create_server(mode="admin")
        admin_names = {t.name for t in await admin_server.list_tools()}

        assert set(table) == admin_names
