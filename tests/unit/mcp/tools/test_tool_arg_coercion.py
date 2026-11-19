"""Tool-argument tests that need FastMCP's real argument validation.

They go through an in-memory fastmcp.Client: the `call_tool()` shortcut in conftest.py
calls `tool.fn()` directly and skips pydantic validation.

- JSON-encoded lists: some clients (e.g. Claude Desktop) send list arguments as a JSON
  string; `JSON_LIST_VALIDATOR` must accept them (edge cases: test_helpers.py).
- Enum lists: an unknown value in a list of enum literals is rejected.
- Strict booleans: list-of-object entries declare booleans as `StrictBool`, so a string
  such as "false" is rejected instead of being read as true.
"""
from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from synology_apm.sdk import FileServerPathSelector, MachineWorkloadType, SandboxKey
from tests.unit.mcp.conftest import make_machine_workload
from tests.unit.sandbox_builders import make_sandbox_activity


@pytest.fixture
def tool_server(client_server: Callable[[str], FastMCP]) -> FastMCP:
    """Every tool registered (mode=admin), called through fastmcp's real argument validation."""
    return client_server("admin")


class TestUpdateMachineFileServerArgCoercion:
    @pytest.mark.asyncio
    async def test_json_encoded_selectors_string_is_parsed_and_flows_through(
        self, mock_apm: MagicMock, tool_server: FastMCP
    ) -> None:
        """The live bug report's payload shape: `selectors` arrives as a JSON-encoded
        string instead of a native array."""
        workload = make_machine_workload(workload_id="123e4567-e89b-12d3-a456-426614174001")
        mock_apm.machine.workloads.get.return_value = workload
        mock_apm.machine.workloads.update_file_server.return_value = None

        arguments = {
            "workload_id": "123e4567-e89b-12d3-a456-426614174001",
            "namespace": "123e4567-e89b-12d3-a456-426614174002",
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
    async def test_json_encoded_workload_types_string_is_parsed(
        self, mock_apm: MagicMock, tool_server: FastMCP
    ) -> None:
        """A second affected file/shape: workload_types arrives as a JSON-encoded
        string of plain strings rather than a native array of enum literals."""
        mock_apm.machine.workloads.list.return_value = ([], 0)

        async with Client(tool_server) as client:
            await client.call_tool("list_machine_workloads", {"workload_types": '["vm","fs"]'})

        mock_apm.machine.workloads.list.assert_called_once()
        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["workload_types"] == [MachineWorkloadType.VM, MachineWorkloadType.FS]


class TestStartMachineSandboxStrictBool:
    @pytest.mark.asyncio
    async def test_string_boolean_in_adapter_is_rejected_before_starting(
        self, mock_apm: MagicMock, tool_server: FastMCP
    ) -> None:
        arguments = {
            "workload_id": "123e4567-e89b-12d3-a456-426614174001",
            "namespace": "123e4567-e89b-12d3-a456-426614174002",
            "version_id": "123e4567-e89b-12d3-a456-426614174010",
            "settings": {
                "location_id": "123e4567-e89b-12d3-a456-426614174010",
                "name": "drill vm",
                "cpu_count": 2,
                "memory_mb": 2048,
                "cpu_compatibility_mode": False,
                "hyperv_enlightenments": True,
                "video_card": "vmvga",
                "machine_type": "q35",
                "network_adapters": [{"network_id": "net-1", "model": "e1000", "sriov": "false"}],
                "disks": [{"controller": "sata", "space_reclamation": False}],
            },
        }

        async with Client(tool_server) as client:
            with pytest.raises(ToolError, match="sriov"):
                await client.call_tool("start_machine_sandbox", arguments)

        mock_apm.machine.sandboxes.start.assert_not_called()


class TestGetMachineSandboxScreenshotArgCoercion:
    @pytest.mark.asyncio
    async def test_json_encoded_keys_string_is_parsed(self, mock_apm: MagicMock, tool_server: FastMCP) -> None:
        mock_apm.activities.restore.get.return_value = make_sandbox_activity()
        mock_apm.machine.sandboxes.get_screenshot.return_value = b"\x89PNG"

        async with Client(tool_server) as client:
            await client.call_tool("get_machine_sandbox_screenshot", {"activity_id": "rst-uid-001", "keys": '["enter"]'})

        assert mock_apm.machine.sandboxes.get_screenshot.call_args.kwargs["keys"] == [SandboxKey.ENTER]

    @pytest.mark.asyncio
    async def test_unknown_key_is_rejected_before_capturing(self, mock_apm: MagicMock, tool_server: FastMCP) -> None:
        async with Client(tool_server) as client:
            with pytest.raises(ToolError, match="keys"):
                await client.call_tool("get_machine_sandbox_screenshot", {"activity_id": "rst-uid-001", "keys": ["a"]})

        mock_apm.machine.sandboxes.get_screenshot.assert_not_called()
