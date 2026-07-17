"""Tests for tools/activity.py."""
from __future__ import annotations

import json

import pytest

from synology_apm.sdk import BackupActivityStatus
from tests.unit.mcp.conftest import (
    make_backup_activity,
    make_machine_workload,
    make_restore_activity,
)

_WL_ID = "123e4567-e89b-12d3-a456-426614174001"


class TestListBackupActivities:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        act = make_backup_activity()
        mock_apm.activities.backup.list.return_value = ([act], 1)

        result = await list_result(
            mock_apm.activities.backup.list(limit=100),
            lambda x: x.to_dict(),
        )
        assert result["total"] == 1
        assert result["items"][0]["activity_id"] == "act-001"
        assert result["items"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_empty_list(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        mock_apm.activities.backup.list.return_value = ([], 0)

        result = await list_result(mock_apm.activities.backup.list(), lambda x: x.to_dict())
        assert result["total"] == 0
        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_namespaces_forwarded_as_list(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.activities.backup.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_backup_activities")
        await tool.fn(ctx=mock_ctx, namespaces=["default", "secondary"])

        _, kwargs = mock_apm.activities.backup.list.call_args
        assert kwargs["namespace"] == ["default", "secondary"]
        assert kwargs["workload"] is None

    @pytest.mark.asyncio
    async def test_machine_types_and_m365_types_mutually_exclusive(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("list_backup_activities")

        with pytest.raises(ValueError, match="mutually exclusive"):
            await tool.fn(ctx=mock_ctx, machine_types=["vm"], m365_types=["exchange"])

    @pytest.mark.asyncio
    async def test_machine_workload_scoping_resolves_and_forwards_workload(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.activities.backup.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_backup_activities")
        await tool.fn(ctx=mock_ctx, workload_id=_WL_ID, workload_namespace="default")

        mock_apm.machine.workloads.get.assert_called_once_with(_WL_ID, "default")
        _, kwargs = mock_apm.activities.backup.list.call_args
        assert kwargs["workload"] is wl

    @pytest.mark.asyncio
    async def test_m365_workload_scoping_requires_workload_type(self, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("list_backup_activities")
        with pytest.raises(ValueError, match="workload_type is required"):
            await tool.fn(ctx=mock_ctx, workload_id="wl-001", workload_namespace="default", tenant_id="tenant-001")

    @pytest.mark.asyncio
    async def test_workload_id_without_namespace_raises(self, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("list_backup_activities")
        with pytest.raises(ValueError, match="workload_namespace is required"):
            await tool.fn(ctx=mock_ctx, workload_id="wl-001")

    @pytest.mark.asyncio
    async def test_m365_workload_scoping_resolves_and_forwards_workload(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from tests.unit.mcp.conftest import make_m365_workload

        m365_wl = make_m365_workload()
        mock_apm.m365.workloads.get.return_value = m365_wl
        mock_apm.activities.backup.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_backup_activities")
        await tool.fn(
            ctx=mock_ctx,
            workload_id=m365_wl.workload_id,
            workload_namespace="default",
            tenant_id="tenant-001",
            workload_type="exchange",
        )

        mock_apm.m365.workloads.get.assert_called_once_with(
            m365_wl.workload_id, "default", tenant_id="tenant-001", workload_type=m365_wl.workload_type
        )
        _, kwargs = mock_apm.activities.backup.list.call_args
        assert kwargs["workload"] is m365_wl


class TestGetBackupActivity:
    @pytest.mark.asyncio
    async def test_returns_activity_dict(self, mock_apm, mock_ctx, admin_server):
        from tests.unit.mcp.conftest import call_tool

        act = make_backup_activity()
        mock_apm.activities.backup.get.return_value = act

        result = json.loads(await call_tool(admin_server, "get_backup_activity", mock_ctx, activity_id="act-001"))
        assert result["activity_id"] == "act-001"
        assert result["workload_name"] == "vm-web-01"
        mock_apm.activities.backup.get.assert_called_once_with("act-001")


class TestCancelBackupActivity:
    @pytest.mark.asyncio
    async def test_calls_cancel_and_returns_ok(self, mock_apm, mock_ctx, admin_server):
        from tests.unit.mcp.conftest import call_tool

        act = make_backup_activity(status=BackupActivityStatus.BACKING_UP, finished_at=None)
        mock_apm.activities.backup.get.return_value = act
        mock_apm.activities.backup.cancel.return_value = None

        result = json.loads(
            await call_tool(admin_server, "cancel_backup_activity", mock_ctx, activity_id="act-001")
        )
        assert result["ok"] is True
        mock_apm.activities.backup.get.assert_called_once_with("act-001")
        mock_apm.activities.backup.cancel.assert_called_once_with(act)


class TestListRestoreActivities:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        act = make_restore_activity()
        mock_apm.activities.restore.list.return_value = ([act], 1)

        result = await list_result(
            mock_apm.activities.restore.list(limit=100),
            lambda x: x.to_dict(),
        )
        assert result["total"] == 1
        assert result["items"][0]["activity_id"] == "rst-001"
        assert result["items"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_workload_scoping_resolves_and_forwards_workload(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.activities.restore.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_restore_activities")
        await tool.fn(ctx=mock_ctx, workload_id=_WL_ID, workload_namespace="default")

        mock_apm.machine.workloads.get.assert_called_once_with(_WL_ID, "default")
        _, kwargs = mock_apm.activities.restore.list.call_args
        assert kwargs["workload"] is wl

    @pytest.mark.asyncio
    async def test_workload_scoping_omitted_forwards_none(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.activities.restore.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_restore_activities")
        await tool.fn(ctx=mock_ctx)

        mock_apm.machine.workloads.get.assert_not_called()
        _, kwargs = mock_apm.activities.restore.list.call_args
        assert kwargs["workload"] is None


class TestGetRestoreActivity:
    @pytest.mark.asyncio
    async def test_returns_activity_dict(self, mock_apm, mock_ctx, admin_server):
        from tests.unit.mcp.conftest import call_tool

        act = make_restore_activity()
        mock_apm.activities.restore.get.return_value = act

        result = json.loads(await call_tool(admin_server, "get_restore_activity", mock_ctx, activity_id="rst-001"))
        assert result["activity_id"] == "rst-001"
        assert result["workload_name"] == "vm-web-01"
        mock_apm.activities.restore.get.assert_called_once_with("rst-001")


class TestCancelRestoreActivity:
    @pytest.mark.asyncio
    async def test_calls_cancel_and_returns_ok(self, mock_apm, mock_ctx, admin_server):
        from tests.unit.mcp.conftest import call_tool

        act = make_restore_activity()
        mock_apm.activities.restore.get.return_value = act
        mock_apm.activities.restore.cancel.return_value = None

        result = json.loads(
            await call_tool(admin_server, "cancel_restore_activity", mock_ctx, activity_id="rst-001")
        )
        assert result["ok"] is True
        mock_apm.activities.restore.get.assert_called_once_with("rst-001")
        mock_apm.activities.restore.cancel.assert_called_once_with(act)
