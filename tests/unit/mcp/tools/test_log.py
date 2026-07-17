"""Tests for tools/log.py: _resolve_dp_server and log list tools."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from synology_apm.sdk import APMActivityLogType, BackupServerType, LogLevel
from tests.unit.mcp.conftest import call_tool, make_backup_server


def _make_drive_log(**kwargs):
    from datetime import UTC, datetime

    from synology_apm.sdk import DriveLog, LogLevel

    defaults = dict(
        level=LogLevel.INFO,
        timestamp=datetime(2026, 7, 14, 2, 30, tzinfo=UTC),
        description="Drive healthy",
        server_name="apm-server-01",
        model="-",
        location="-",
        serial="-",
    )
    defaults.update(kwargs)
    return DriveLog(**defaults)


class TestResolveDpServer:
    @pytest.mark.asyncio
    async def test_accepts_dp_server(self, mock_apm):
        from synology_apm.mcp.tools.log import _resolve_dp_server

        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs

        result = await _resolve_dp_server(mock_apm, "srv-001")
        assert result.name == "apm-server-01"

    @pytest.mark.asyncio
    async def test_rejects_nas_server(self, mock_apm):
        from synology_apm.mcp.tools.log import _resolve_dp_server

        nas = make_backup_server(name="nas-server-01", server_type=BackupServerType.NAS)
        mock_apm.backup_servers.get.return_value = nas

        with pytest.raises(ValueError, match="NAS-type server"):
            await _resolve_dp_server(mock_apm, "srv-nas")


class TestNasServerRejectedByAllLogTools:
    """_resolve_dp_server (shared by all 4 log tools) rejects NAS-type backup servers
    before any log SDK call is made — verified once per tool via a shared table
    rather than four hand-copied test bodies."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name,list_mock_attr", [
        ("list_activity_logs", "list_activity"),
        ("list_drive_logs", "list_drive"),
        ("list_connection_logs", "list_connection"),
        ("list_system_logs", "list_system"),
    ])
    async def test_nas_server_raises_before_list(self, mock_apm, mock_ctx, admin_server, tool_name, list_mock_attr):
        nas = make_backup_server(name="nas-server-01", server_type=BackupServerType.NAS)
        mock_apm.backup_servers.get.return_value = nas

        raw = await call_tool(admin_server, tool_name, mock_ctx, server_id="srv-nas")
        parsed = json.loads(raw)

        assert parsed["error"] == "invalid_argument"
        assert "NAS-type" in parsed["message"]
        getattr(mock_apm.logs, list_mock_attr).assert_not_called()


class TestLogListFilterForwarding:
    """levels/since/until/keyword must reach the SDK call unchanged for every log tool —
    previously only the presence of a call was checked, never the filter values themselves."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name,list_mock_attr", [
        ("list_activity_logs", "list_activity"),
        ("list_drive_logs", "list_drive"),
        ("list_connection_logs", "list_connection"),
        ("list_system_logs", "list_system"),
    ])
    async def test_forwards_levels_since_until_keyword(
        self, mock_apm, mock_ctx, admin_server, tool_name, list_mock_attr
    ):
        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs
        getattr(mock_apm.logs, list_mock_attr).return_value = ([], 0)

        await call_tool(
            admin_server, tool_name, mock_ctx,
            server_id="srv-001",
            levels=["warning", "error"],
            since="2026-07-01T00:00:00",
            until="2026-07-14T00:00:00",
            keyword="backup",
        )

        _, kwargs = getattr(mock_apm.logs, list_mock_attr).call_args
        assert kwargs["levels"] == [LogLevel.WARNING, LogLevel.ERROR]
        assert kwargs["since"] == datetime(2026, 7, 1, 0, 0, 0)
        assert kwargs["until"] == datetime(2026, 7, 14, 0, 0, 0)
        assert kwargs["keyword"] == "backup"

    @pytest.mark.asyncio
    async def test_activity_forwards_log_type(self, mock_apm, mock_ctx, admin_server):
        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs
        mock_apm.logs.list_activity.return_value = ([], 0)

        await call_tool(admin_server, "list_activity_logs", mock_ctx, server_id="srv-001", log_type="system")

        _, kwargs = mock_apm.logs.list_activity.call_args
        assert kwargs["log_type"] == APMActivityLogType.SYSTEM

    @pytest.mark.asyncio
    async def test_drive_forwards_location(self, mock_apm, mock_ctx, admin_server):
        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs
        mock_apm.logs.list_drive.return_value = ([], 0)

        await call_tool(admin_server, "list_drive_logs", mock_ctx, server_id="srv-001", location="Slot 1")

        _, kwargs = mock_apm.logs.list_drive.call_args
        assert kwargs["location"] == "Slot 1"


class TestListActivityLogs:
    @pytest.mark.asyncio
    async def test_calls_sdk_and_returns_result(self, mock_apm, mock_ctx, admin_server):
        from datetime import UTC, datetime

        from synology_apm.sdk import APMActivityLog, APMActivityLogType, LogLevel

        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs

        entry = APMActivityLog(
            level=LogLevel.INFO,
            log_type=APMActivityLogType.PROTECTION,
            timestamp=datetime(2026, 7, 14, 2, 30, tzinfo=UTC),
            username="admin",
            description="Backup completed",
        )
        mock_apm.logs.list_activity.return_value = ([entry], 0)

        raw = await call_tool(admin_server, "list_activity_logs", mock_ctx, server_id="srv-001")
        result = json.loads(raw)

        assert result["items"][0]["level"] == "info"
        assert "Backup completed" in result["items"][0]["description"]
        assert result["total"] is None


class TestListDriveLogs:
    @pytest.mark.asyncio
    async def test_reports_reliable_total(self, mock_apm, mock_ctx, admin_server):
        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs
        mock_apm.logs.list_drive.return_value = ([_make_drive_log()], 5)

        raw = await call_tool(admin_server, "list_drive_logs", mock_ctx, server_id="srv-001")
        result = json.loads(raw)

        assert result["total"] == 5
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_no_truncated_flag_on_last_page_with_offset(self, mock_apm, mock_ctx, admin_server):
        """Regression test: offset + len(items) == total on the true last page must not
        be flagged truncated."""
        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs
        mock_apm.logs.list_drive.return_value = ([_make_drive_log()] * 5, 95)

        raw = await call_tool(admin_server, "list_drive_logs", mock_ctx, server_id="srv-001", limit=10, offset=90)
        result = json.loads(raw)

        assert result["total"] == 95
        assert "truncated" not in result


class TestListConnectionLogs:
    @pytest.mark.asyncio
    async def test_calls_sdk_and_returns_result(self, mock_apm, mock_ctx, admin_server):
        from datetime import UTC, datetime

        from synology_apm.sdk import ConnectionLog, LogLevel

        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs
        entry = ConnectionLog(
            level=LogLevel.WARNING,
            timestamp=datetime(2026, 7, 14, 2, 30, tzinfo=UTC),
            username="admin",
            description="Login failed",
        )
        mock_apm.logs.list_connection.return_value = ([entry], 0)

        raw = await call_tool(admin_server, "list_connection_logs", mock_ctx, server_id="srv-001")
        result = json.loads(raw)

        assert result["items"][0]["level"] == "warning"
        assert "Login failed" in result["items"][0]["description"]
        assert result["total"] is None
        mock_apm.logs.list_connection.assert_called_once()


class TestListSystemLogs:
    @pytest.mark.asyncio
    async def test_calls_sdk_and_returns_result(self, mock_apm, mock_ctx, admin_server):
        from datetime import UTC, datetime

        from synology_apm.sdk import LogLevel, SystemLog

        bs = make_backup_server(server_type=BackupServerType.DP)
        mock_apm.backup_servers.get.return_value = bs
        entry = SystemLog(
            level=LogLevel.ERROR,
            timestamp=datetime(2026, 7, 14, 2, 30, tzinfo=UTC),
            username="admin",
            description="Disk failure detected",
        )
        mock_apm.logs.list_system.return_value = ([entry], 0)

        raw = await call_tool(admin_server, "list_system_logs", mock_ctx, server_id="srv-001")
        result = json.loads(raw)

        assert result["items"][0]["level"] == "error"
        assert "Disk failure detected" in result["items"][0]["description"]
        assert result["total"] is None
        mock_apm.logs.list_system.assert_called_once()
