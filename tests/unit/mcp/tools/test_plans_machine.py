"""Tests for tools/plans/machine.py."""
from __future__ import annotations

import json

import pytest

from tests.unit.mcp.conftest import call_tool, make_backup_server, make_protection_plan, make_remote_storage


class TestCreateMachineProtectionPlan:
    @pytest.mark.asyncio
    async def test_calls_sdk_and_returns_dict(self, mock_apm, mock_ctx, admin_server):
        plan = make_protection_plan(plan_id="plan-new", name="New Plan")
        mock_apm.machine.plans.create.return_value = plan

        raw = await call_tool(admin_server, "create_machine_protection_plan", mock_ctx, name="New Plan")
        parsed = json.loads(raw)

        assert parsed["plan_id"] == "plan-new"
        assert parsed["name"] == "New Plan"
        mock_apm.machine.plans.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_log_records_name(self, mock_apm, mock_ctx, admin_server, tmp_path):
        import os
        from unittest.mock import patch

        mock_apm.machine.plans.create.return_value = make_protection_plan(plan_id="plan-new", name="New Plan")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(admin_server, "create_machine_protection_plan", mock_ctx, name="New Plan")

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "create_machine_protection_plan"
        assert entry["params"] == {"name": "New Plan"}
        assert entry["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_advanced_fields_default_to_none(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        await tool.fn(ctx=mock_ctx, name="New Plan")

        (request,), _ = mock_apm.machine.plans.create.call_args
        assert request.run_schedule_by_controller_time is False
        assert request.vm_config is None
        assert request.pc_config is None
        assert request.ps_config is None
        assert request.db_config is None
        assert request.backup_window is None
        assert request.tasks is None
        assert request.backup_copy is None
        assert request.retention.gfs is None

    @pytest.mark.asyncio
    async def test_run_schedule_by_controller_time_reaches_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        await tool.fn(ctx=mock_ctx, name="New Plan", run_schedule_by_controller_time=True)

        (request,), _ = mock_apm.machine.plans.create.call_args
        assert request.run_schedule_by_controller_time is True

    @pytest.mark.asyncio
    async def test_gfs_retention_reaches_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        await tool.fn(
            ctx=mock_ctx,
            name="New Plan",
            retention_type="keep_advanced",
            gfs_daily_versions=7,
            gfs_weekly_versions=4,
            gfs_monthly_versions=12,
            gfs_yearly_versions=5,
        )

        (request,), _ = mock_apm.machine.plans.create.call_args
        assert request.retention.gfs.daily_versions == 7
        assert request.retention.gfs.weekly_versions == 4
        assert request.retention.gfs.monthly_versions == 12
        assert request.retention.gfs.yearly_versions == 5

    @pytest.mark.asyncio
    async def test_gfs_retention_missing_field_returns_invalid_argument_error(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        raw = await tool.fn(ctx=mock_ctx, name="New Plan", retention_type="keep_advanced", gfs_daily_versions=7)
        result = json.loads(raw)

        assert result["error"] == "invalid_argument"
        assert "keep_advanced requires" in result["message"]
        mock_apm.machine.plans.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_device_configs_reach_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        await tool.fn(
            ctx=mock_ctx,
            name="New Plan",
            vm_enable_verification=True,
            pc_shutdown_after_backup=True,
            ps_wake_for_backup=True,
            db_action_on_error="stop",
            db_mssql_log_setting="truncate",
        )

        (request,), _ = mock_apm.machine.plans.create.call_args
        assert request.vm_config.enable_verification is True
        assert request.vm_config.enable_app_aware_bkp is True  # untouched field keeps its dataclass default
        assert request.pc_config.shutdown_after_backup is True
        assert request.ps_config.wake_for_backup is True
        from synology_apm.sdk import DbActionOnError, MssqlLogSetting

        assert request.db_config.action_on_error == DbActionOnError.STOP
        assert request.db_config.mssql_log_setting == MssqlLogSetting.TRUNCATE

    @pytest.mark.asyncio
    async def test_backup_window_reaches_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        await tool.fn(
            ctx=mock_ctx,
            name="New Plan",
            backup_window_enabled=True,
            backup_window_allowed_hours="mon:0-8,13-18;tue:0-23",
        )

        (request,), _ = mock_apm.machine.plans.create.call_args
        from synology_apm.sdk import WeekDay

        assert request.backup_window.enabled is True
        assert request.backup_window.allowed_hours[WeekDay.MONDAY] == frozenset(range(0, 9)) | frozenset(range(13, 19))
        assert request.backup_window.allowed_hours[WeekDay.TUESDAY] == frozenset(range(0, 24))

    @pytest.mark.asyncio
    async def test_tasks_json_reaches_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        tasks_json = json.dumps([
            {"workload_type": "pc", "os_type": "windows"},
            {"workload_type": "pc", "os_type": "mac"},
            {"workload_type": "ps", "os_type": "windows"},
            {"workload_type": "ps", "os_type": "linux"},
            {"workload_type": "vm", "os_type": "none"},
            {"workload_type": "fs", "os_type": "none"},
        ])
        await tool.fn(ctx=mock_ctx, name="New Plan", tasks_json=tasks_json)

        (request,), _ = mock_apm.machine.plans.create.call_args
        assert len(request.tasks) == 6
        from synology_apm.sdk import MachineOsType, MachineWorkloadType

        assert request.tasks[0].workload_type == MachineWorkloadType.PC
        assert request.tasks[0].os_type == MachineOsType.WINDOWS

    @pytest.mark.asyncio
    async def test_backup_copy_with_backup_server_destination_reaches_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        server_dest = make_backup_server(backup_server_id="srv-002", name="apm-server-02")
        mock_apm.backup_servers.get.return_value = server_dest
        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        await tool.fn(
            ctx=mock_ctx,
            name="New Plan",
            backup_copy_destination_type="backup_server",
            backup_copy_destination_id="srv-002",
            backup_copy_retention_type="keep_days",
            backup_copy_retention_days=90,
            backup_copy_schedule_frequency="after_backup",
        )

        mock_apm.backup_servers.get.assert_called_once_with("srv-002")
        mock_apm.remote_storages.get.assert_not_called()
        (request,), _ = mock_apm.machine.plans.create.call_args
        assert request.backup_copy.destination is server_dest
        assert request.backup_copy.retention.days == 90
        from synology_apm.sdk import ScheduleFrequency

        assert request.backup_copy.schedule.frequency == ScheduleFrequency.AFTER_BACKUP

    @pytest.mark.asyncio
    async def test_backup_copy_with_remote_storage_destination_reaches_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        storage = make_remote_storage(storage_id="stor-002")
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.machine.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_machine_protection_plan")
        await tool.fn(
            ctx=mock_ctx,
            name="New Plan",
            backup_copy_destination_type="remote_storage",
            backup_copy_destination_id="stor-002",
            backup_copy_retention_type="keep_versions",
            backup_copy_retention_versions=10,
            backup_copy_schedule_frequency="weekly",
            backup_copy_weekdays=["sun"],
        )

        mock_apm.remote_storages.get.assert_called_once_with("stor-002")
        mock_apm.backup_servers.get.assert_not_called()
        (request,), _ = mock_apm.machine.plans.create.call_args
        assert request.backup_copy.destination is storage
        assert request.backup_copy.retention.versions == 10


class TestUpdateMachineProtectionPlan:
    @pytest.mark.asyncio
    async def test_advanced_fields_default_to_none_full_replace(self, mock_apm, mock_ctx):
        """update is a full replace: omitting an advanced field resets that feature."""
        from synology_apm.mcp._server import create_server

        mock_apm.machine.plans.update.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_protection_plan")
        await tool.fn(
            ctx=mock_ctx,
            plan_id="plan-001",
            name="Renamed",
            retention_type="keep_days",
            retention_days=30,
            retention_versions=None,
            schedule_frequency="daily",
            schedule_time="02:00",
            weekdays=None,
            description="",
            is_immutable=False,
        )

        plan_id, request = mock_apm.machine.plans.update.call_args[0]
        assert plan_id == "plan-001"
        assert request.vm_config is None
        assert request.backup_copy is None
        assert request.tasks is None
        assert request.backup_window is None

    @pytest.mark.asyncio
    async def test_audit_log_records_plan_id(self, mock_apm, mock_ctx, admin_server, tmp_path):
        import os
        from unittest.mock import patch

        mock_apm.machine.plans.update.return_value = make_protection_plan()

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(
                admin_server, "update_machine_protection_plan", mock_ctx,
                plan_id="plan-001", name="Renamed", retention_type="keep_days",
                retention_days=30, retention_versions=None, schedule_frequency="daily",
                schedule_time="02:00", weekdays=None, description="", is_immutable=False,
            )

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "update_machine_protection_plan"
        assert entry["params"] == {"plan_id": "plan-001"}
        assert entry["outcome"] == "ok"
