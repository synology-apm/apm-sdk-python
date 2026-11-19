"""Tests for the machine sandbox tools in tools/machine.py."""
from __future__ import annotations

import base64
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ImageContent

from synology_apm.mcp.tools.machine import _SandboxAdapterParam, _SandboxDiskParam, _SandboxSettingsParam
from synology_apm.sdk import (
    InvalidOperationError,
    SandboxDisk,
    SandboxDiskController,
    SandboxKey,
    SandboxMachineType,
    SandboxNetworkAdapter,
    SandboxSettings,
)
from tests.unit.mcp.conftest import audit_entries, call_tool, make_machine_workload, make_workload_version
from tests.unit.sandbox_builders import (
    LOC_A,
    WL_ID,
    WL_NS,
    make_sandbox_activity,
    make_sandbox_resources,
    make_sandbox_settings,
)

_VER = "123e4567-e89b-12d3-a456-426614174003"
_ACT_ID = "rst-uid-001"
_CONSOLE_URL = "https://192.0.2.2/sharing/AbCdEf123"
_SETTINGS = make_sandbox_settings()
_SANDBOX_IDS = {"workload_id": WL_ID, "namespace": WL_NS}


def _resolved(mock_apm: MagicMock) -> tuple[Any, Any]:
    wl = make_machine_workload(name="prod-server-01")
    version = make_workload_version(version_id=_VER)
    mock_apm.machine.workloads.get.return_value = wl
    mock_apm.machine.workloads.get_version.return_value = version
    mock_apm.machine.sandboxes.get_latest_version.return_value = version
    mock_apm.machine.sandboxes.get_default_settings.return_value = _SETTINGS
    return wl, version


class TestGetMachineSandboxResources:
    @pytest.mark.asyncio
    async def test_returns_version_and_resources(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        wl, version = _resolved(mock_apm)
        resources = make_sandbox_resources()
        mock_apm.machine.sandboxes.get_resources.return_value = resources

        result = await call_tool(
            admin_server, "get_machine_sandbox_resources", mock_ctx, **_SANDBOX_IDS, version_id=_VER,
        )

        mock_apm.machine.workloads.get.assert_called_once_with(WL_ID, WL_NS)
        mock_apm.machine.workloads.get_version.assert_called_once_with(wl, _VER)
        mock_apm.machine.sandboxes.get_resources.assert_called_once_with(wl, version)
        assert result == {"version_id": _VER, **resources.to_dict()}

    @pytest.mark.asyncio
    async def test_omitted_version_uses_the_latest_sandbox_version(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        wl, version = _resolved(mock_apm)
        mock_apm.machine.sandboxes.get_resources.return_value = make_sandbox_resources()

        result = await call_tool(admin_server, "get_machine_sandbox_resources", mock_ctx, **_SANDBOX_IDS)

        mock_apm.machine.sandboxes.get_latest_version.assert_called_once_with(wl)
        mock_apm.machine.workloads.get_version.assert_not_called()
        mock_apm.machine.sandboxes.get_resources.assert_called_once_with(wl, version)
        assert result["version_id"] == _VER


class TestStartMachineSandbox:
    @pytest.mark.asyncio
    async def test_defaults_only_starts_with_the_default_settings_and_returns_them(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        wl, version = _resolved(mock_apm)

        result = await call_tool(admin_server, "start_machine_sandbox", mock_ctx, **_SANDBOX_IDS)

        mock_apm.machine.workloads.get.assert_called_once_with(WL_ID, WL_NS)
        mock_apm.machine.sandboxes.get_latest_version.assert_called_once_with(wl)
        mock_apm.machine.sandboxes.get_default_settings.assert_called_once_with(wl, version, location_id=None)
        mock_apm.machine.sandboxes.start.assert_called_once_with(wl, version, _SETTINGS)
        mock_apm.machine.sandboxes.validate_settings.assert_not_called()
        assert result == {"version_id": _VER, "settings": _SETTINGS.to_dict()}

    @pytest.mark.asyncio
    async def test_settings_replace_the_defaults_of_the_given_location(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        wl, version = _resolved(mock_apm)

        result = await call_tool(
            admin_server, "start_machine_sandbox", mock_ctx, **_SANDBOX_IDS, version_id=_VER, settings={
                "location_id": LOC_A, "memory_mb": 2048, "cpu_compatibility_mode": True,
                "hyperv_enlightenments": False, "machine_type": "pc",
                "disks": [{"controller": "ide", "space_reclamation": True}],
                "network_adapters": [],
            },
        )

        expected = dataclasses.replace(
            _SETTINGS,
            memory_mb=2048,
            cpu_compatibility_mode=True,
            hyperv_enlightenments=False,
            machine_type=SandboxMachineType.PC,
            disks=(SandboxDisk(SandboxDiskController.IDE, True),),
            network_adapters=(),
        )
        mock_apm.machine.workloads.get_version.assert_called_once_with(wl, _VER)
        mock_apm.machine.sandboxes.get_default_settings.assert_called_once_with(wl, version, location_id=LOC_A)
        mock_apm.machine.sandboxes.start.assert_called_once_with(wl, version, expected)
        assert result == {"version_id": _VER, "settings": expected.to_dict()}

    @pytest.mark.asyncio
    async def test_returned_settings_round_trip_into_the_same_sdk_settings(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        """Settings a start returns, passed back unchanged, become the same SandboxSettings."""
        wl, version = _resolved(mock_apm)
        mock_apm.machine.sandboxes.get_default_settings.return_value = make_sandbox_settings(
            name="other", cpu_count=1, network_adapters=(),
        )

        await call_tool(admin_server, "start_machine_sandbox", mock_ctx, **_SANDBOX_IDS, settings=_SETTINGS.to_dict())

        mock_apm.machine.sandboxes.start.assert_called_once_with(wl, version, _SETTINGS)

    @pytest.mark.asyncio
    async def test_dry_run_validates_without_starting_or_auditing(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, audit_log: Path,
    ) -> None:
        wl, version = _resolved(mock_apm)

        result = await call_tool(
            admin_server, "start_machine_sandbox", mock_ctx, **_SANDBOX_IDS, settings={"cpu_count": 2}, dry_run=True,
        )

        expected = dataclasses.replace(_SETTINGS, cpu_count=2)
        mock_apm.machine.sandboxes.validate_settings.assert_called_once_with(wl, version, expected)
        mock_apm.machine.sandboxes.start.assert_not_called()
        assert result == {"version_id": _VER, "settings": expected.to_dict()}
        assert not audit_log.exists()

    @pytest.mark.asyncio
    async def test_invalid_setting_does_not_start(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        _resolved(mock_apm)

        with pytest.raises(ToolError):
            await call_tool(admin_server, "start_machine_sandbox", mock_ctx, **_SANDBOX_IDS, settings={"name": "   "})

        mock_apm.machine.sandboxes.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_audit_log_records_identifying_params(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, audit_log: Path,
    ) -> None:
        _resolved(mock_apm)

        await call_tool(
            admin_server, "start_machine_sandbox", mock_ctx, **_SANDBOX_IDS, version_id=_VER, settings={"memory_mb": 2048},
        )

        [entry] = audit_entries(audit_log)
        assert entry["tool"] == "start_machine_sandbox"
        assert entry["params"] == {**_SANDBOX_IDS, "version_id": _VER}
        assert entry["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_failed_start_is_audited_as_error(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, audit_log: Path,
    ) -> None:
        _resolved(mock_apm)
        mock_apm.machine.sandboxes.start.side_effect = InvalidOperationError(
            "Not enough free memory on the appliance for a 4096 MB sandbox.",
            resource_type="Workload", resource_id=WL_ID,
        )

        with pytest.raises(ToolError):
            await call_tool(admin_server, "start_machine_sandbox", mock_ctx, **_SANDBOX_IDS)

        [entry] = audit_entries(audit_log)
        assert entry["outcome"] == "error: InvalidOperationError"


@pytest.mark.parametrize(("param", "model"), [
    (_SandboxSettingsParam, SandboxSettings),
    (_SandboxAdapterParam, SandboxNetworkAdapter),
    (_SandboxDiskParam, SandboxDisk),
], ids=["settings", "network_adapter", "disk"])
def test_settings_param_keys_match_the_sdk_model_fields(param: Any, model: type) -> None:
    """The settings a start returns can be passed back unchanged: same keys, nothing missing."""
    assert param.__required_keys__ | param.__optional_keys__ == {f.name for f in dataclasses.fields(model)}


class TestGetMachineSandboxConsoleLink:
    @pytest.mark.asyncio
    async def test_gets_activity_and_returns_the_link(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        act = make_sandbox_activity()
        mock_apm.activities.restore.get.return_value = act
        mock_apm.machine.sandboxes.get_console_link.return_value = _CONSOLE_URL

        result = await call_tool(admin_server, "get_machine_sandbox_console_link", mock_ctx, activity_id=_ACT_ID)

        mock_apm.activities.restore.get.assert_called_once_with(_ACT_ID, include_logs=False)
        mock_apm.machine.sandboxes.get_console_link.assert_called_once_with(act)
        assert result == {"url": _CONSOLE_URL}

    @pytest.mark.asyncio
    async def test_audit_log_never_contains_the_url(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, audit_log: Path,
    ) -> None:
        mock_apm.activities.restore.get.return_value = make_sandbox_activity()
        mock_apm.machine.sandboxes.get_console_link.return_value = _CONSOLE_URL

        await call_tool(admin_server, "get_machine_sandbox_console_link", mock_ctx, activity_id=_ACT_ID)

        assert "AbCdEf123" not in audit_log.read_text()
        [entry] = audit_entries(audit_log)
        assert entry["tool"] == "get_machine_sandbox_console_link"
        assert entry["params"] == {"activity_id": _ACT_ID}
        assert entry["outcome"] == "ok"


class TestGetMachineSandboxScreenshot:
    _PNG = b"\x89PNG\r\n\x1a\nfake-image"

    def _arrange(self, mock_apm: MagicMock) -> Any:
        act = make_sandbox_activity()
        mock_apm.activities.restore.get.return_value = act
        mock_apm.machine.sandboxes.get_screenshot.return_value = self._PNG
        return act

    @pytest.mark.asyncio
    async def test_looks_up_activity_and_returns_png_image(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        act = self._arrange(mock_apm)

        result: Any = await call_tool(admin_server, "get_machine_sandbox_screenshot", mock_ctx, activity_id=_ACT_ID)

        mock_apm.activities.restore.get.assert_called_once_with(_ACT_ID, include_logs=False)
        mock_apm.machine.sandboxes.get_screenshot.assert_called_once_with(act, keys=[], delay_seconds=None)
        content = result.to_image_content()
        assert (content.type, content.mime_type) == ("image", "image/png")
        assert base64.b64decode(content.data) == self._PNG

    @pytest.mark.asyncio
    async def test_keys_and_delay_are_passed(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP,
    ) -> None:
        act = self._arrange(mock_apm)

        await call_tool(
            admin_server, "get_machine_sandbox_screenshot", mock_ctx, activity_id=_ACT_ID,
            keys=["shift", "ctrl_alt_delete"], delay_seconds=5,
        )

        mock_apm.machine.sandboxes.get_screenshot.assert_called_once_with(
            act, keys=[SandboxKey.SHIFT, SandboxKey.CTRL_ALT_DELETE], delay_seconds=5,
        )

    @pytest.mark.asyncio
    async def test_client_receives_image_content(
        self, mock_apm: MagicMock, client_server: Callable[[str], FastMCP],
    ) -> None:
        """Through a real client the tool answers with one image content block, not JSON text."""
        self._arrange(mock_apm)

        async with Client(client_server("operator")) as client:
            result = await client.call_tool("get_machine_sandbox_screenshot", {"activity_id": _ACT_ID})

        [block] = result.content
        assert isinstance(block, ImageContent)
        assert block.mime_type == "image/png"
        assert base64.b64decode(block.data) == self._PNG

    @pytest.mark.asyncio
    async def test_audit_log_has_ids_only(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, audit_log: Path,
    ) -> None:
        self._arrange(mock_apm)

        await call_tool(
            admin_server, "get_machine_sandbox_screenshot", mock_ctx, activity_id=_ACT_ID,
            keys=["enter"], delay_seconds=5,
        )

        [entry] = audit_entries(audit_log)
        assert entry == {**entry, "tool": "get_machine_sandbox_screenshot", "outcome": "ok"}
        assert entry["params"] == {"activity_id": _ACT_ID}
