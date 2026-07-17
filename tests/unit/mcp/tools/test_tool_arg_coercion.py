"""End-to-end regression tests for the JSON-encoded-list-argument coercion fix.

Some MCP clients (e.g. Claude Desktop) encode list-typed tool arguments as a JSON
string (e.g. '["mon","wed"]') instead of a native JSON array. fastmcp's tool-calling
path validates the whole incoming arguments dict via pydantic with no string-to-JSON
fallback for tools (unlike prompts, see fastmcp.prompts.function_prompt), so this
would otherwise be rejected with a pydantic validation error.

These tests must go through the real fastmcp.Client call path (not the direct
`tool.fn(ctx=..., **kwargs)` shortcut `call_tool()` in conftest.py uses elsewhere) —
`.fn()` bypasses pydantic argument validation entirely, so it cannot exercise or
prove this fix. See resource_server's docstring in conftest.py for the same
reasoning applied to resources.

Exhaustive edge cases for the underlying coerce_json_encoded_list() helper itself
live in tests/unit/mcp/test_helpers.py — these two tests exist only to prove the
wiring (Annotated[..., JSON_LIST_VALIDATOR]) works end-to-end for a couple of
representative shapes (a plain string list, and a list-of-dict).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastmcp import Client, FastMCP

from synology_apm.sdk import FileServerPathSelector, MachineWorkloadType
from tests.unit.mcp.conftest import make_machine_workload


@pytest.fixture
def tool_server(mock_apm):
    """A real FastMCP server (mode=admin, every tool registered) with a lifespan
    yielding mock_apm, so tool calls made through an in-memory Client go through
    fastmcp's actual pydantic argument-validation path (see module docstring)."""
    from synology_apm.mcp._server import create_server

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        yield {"apm": mock_apm}

    return create_server(mode="admin", lifespan=lifespan)


class TestUpdateMachineFileServerArgCoercion:
    @pytest.mark.asyncio
    async def test_json_encoded_selectors_string_is_parsed_and_flows_through(self, mock_apm, tool_server):
        """Exact real-world payload from the live bug report: `selectors` arrives
        as a JSON-encoded string instead of a native array."""
        workload = make_machine_workload(workload_id="539a8ae5-5bbd-468c-ab0f-0d0b5101a978")
        mock_apm.machine.workloads.get.return_value = workload
        mock_apm.machine.workloads.update_file_server.return_value = None

        arguments = {
            "workload_id": "539a8ae5-5bbd-468c-ab0f-0d0b5101a978",
            "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
            "login_user": "smoke-test-user",
            "login_password": "smoke-test-password",
            "host_ip": "203.0.113.50",
            "host_port": 445,
            "connection_timeout_seconds": 180,
            "enable_vss": False,
            "selectors": (
                '[{"path": "", "excluded_paths": []}, '
                '{"path": "/smoke-test-share2", "excluded_paths": []}]'
            ),
        }

        async with Client(tool_server) as client:
            # No exception here proves validation accepted the JSON-encoded string;
            # without the fix, this raises a fastmcp ToolError wrapping a pydantic
            # ValidationError before update_machine_file_server's body ever runs.
            await client.call_tool("update_machine_file_server", arguments)

        mock_apm.machine.workloads.update_file_server.assert_called_once()
        call_args = mock_apm.machine.workloads.update_file_server.call_args
        request = call_args.args[1]
        assert request.selectors == (
            FileServerPathSelector(path="", excluded_paths=()),
            FileServerPathSelector(path="/smoke-test-share2", excluded_paths=()),
        )


class TestListMachineWorkloadsArgCoercion:
    @pytest.mark.asyncio
    async def test_json_encoded_workload_types_string_is_parsed(self, mock_apm, tool_server):
        """A second affected file/shape: workload_types arrives as a JSON-encoded
        string of plain strings rather than a native array of enum literals."""
        mock_apm.machine.workloads.list.return_value = ([], 0)

        async with Client(tool_server) as client:
            await client.call_tool("list_machine_workloads", {"workload_types": '["vm","fs"]'})

        mock_apm.machine.workloads.list.assert_called_once()
        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["workload_types"] == [MachineWorkloadType.VM, MachineWorkloadType.FS]
