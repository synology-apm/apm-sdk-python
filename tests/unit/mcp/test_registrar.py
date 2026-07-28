"""Tests for _registrar.py: ToolRegistrar."""
from __future__ import annotations

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError


class TestToolRegistrar:
    @pytest.mark.asyncio
    async def test_registers_when_mode_allows(self) -> None:
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
    async def test_skips_registration_when_mode_insufficient(self) -> None:
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

    def test_defaults_to_readonly(self) -> None:
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "readonly")

        @registrar.tool(description="A readonly tool.")
        async def list_things() -> str:
            return "[]"

        assert registrar.required_modes["list_things"] == "readonly"

    @pytest.mark.asyncio
    async def test_decorator_form_uses_function_name(self) -> None:
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        @registrar.tool("operator", description="...")
        async def backup_machine_workload() -> str:
            return "ok"

        tool = await server.get_tool("backup_machine_workload")
        assert tool is not None
        assert tool.name == "backup_machine_workload"

    @pytest.mark.asyncio
    async def test_direct_call_form_registers_under_given_name(self) -> None:
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
    async def test_required_modes_populated_for_registered_and_skipped(self) -> None:
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


class TestToolAnnotations:
    @pytest.mark.asyncio
    async def test_readonly_tool_gets_read_only_hint(self) -> None:
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "readonly")

        @registrar.tool(description="A readonly tool.")
        async def list_things() -> str:
            return "[]"

        tool = await server.get_tool("list_things")
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True

    @pytest.mark.asyncio
    async def test_delete_tool_gets_destructive_hint(self) -> None:
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        @registrar.tool("admin", description="A delete tool.")
        async def delete_thing() -> str:
            return "deleted"

        tool = await server.get_tool("delete_thing")
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is True
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.idempotentHint is False

    @pytest.mark.asyncio
    async def test_retire_tool_gets_destructive_hint(self) -> None:
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        @registrar.tool("admin", description="A retire tool.")
        async def retire_thing() -> str:
            return "retired"

        tool = await server.get_tool("retire_thing")
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is True

    @pytest.mark.asyncio
    async def test_update_tool_gets_idempotent_hint(self) -> None:
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        @registrar.tool("admin", description="An update tool.")
        async def update_thing() -> str:
            return "updated"

        tool = await server.get_tool("update_thing")
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.readOnlyHint is False

    @pytest.mark.asyncio
    async def test_plain_mutation_tool_gets_no_hints(self) -> None:
        """A tool that is neither list/get, delete_*/retire_*, nor update_* (e.g. an
        operator-mode action like backup_*) should get all hints False -- not left
        unset -- since "not read-only, not destructive, not idempotent" is itself a
        meaningful, accurate signal for a plain triggered action."""
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "operator")

        @registrar.tool("operator", description="A plain action tool.")
        async def backup_thing() -> str:
            return "ok"

        tool = await server.get_tool("backup_thing")
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is False

    @pytest.mark.asyncio
    async def test_direct_call_form_derives_hints_from_given_name(self) -> None:
        """The name= override (used by dynamically-named factory tools) must drive
        the annotation heuristics too, not just the underlying function's __name__."""
        from synology_apm.mcp._registrar import ToolRegistrar

        server = FastMCP("test")
        registrar = ToolRegistrar(server, "admin")

        async def _delete(plan_id: str) -> str:
            return plan_id

        registrar.tool("admin", name="delete_tiering_plan", description="...")(_delete)

        tool = await server.get_tool("delete_tiering_plan")
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is True


class TestToolRequiredModes:
    def test_returns_complete_table(self) -> None:
        """tool_required_modes() enumerates every tool the server can register,
        regardless of mode — the centralized {tool_name: required_mode} lookup."""
        from synology_apm.mcp._server import tool_required_modes

        table = tool_required_modes()

        assert table["list_machine_workloads"] == "readonly"
        assert table["backup_machine_workload"] == "operator"
        assert table["lock_machine_version"] == "admin"
        assert table["delete_machine_workload"] == "admin"

    @pytest.mark.asyncio
    async def test_matches_admin_mode_registered_tools(self) -> None:
        """The table's key set must equal what create_server(mode="admin") actually
        registers — every tool considered ends up registered at the top mode."""
        from synology_apm.mcp._server import create_server, tool_required_modes

        table = tool_required_modes()
        admin_server = create_server(mode="admin")
        admin_names = {t.name for t in await admin_server.list_tools()}

        assert set(table) == admin_names
