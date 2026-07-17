"""Tests for tools/plans/m365.py."""
from __future__ import annotations

import json

import pytest

from synology_apm.sdk import WeekDay, WorkloadCategory
from tests.unit.mcp.conftest import call_tool, make_protection_plan, make_remote_storage


class TestCreateM365ProtectionPlan:
    @pytest.mark.asyncio
    async def test_run_schedule_by_controller_time_and_backup_copy_reach_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        storage = make_remote_storage(storage_id="stor-003")
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.m365.plans.create.return_value = make_protection_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_m365_protection_plan")
        await tool.fn(
            ctx=mock_ctx,
            name="M365 Plan",
            run_schedule_by_controller_time=True,
            backup_copy_destination_type="remote_storage",
            backup_copy_destination_id="stor-003",
            backup_copy_retention_type="keep_days",
            backup_copy_retention_days=60,
            backup_copy_schedule_frequency="daily",
            backup_copy_schedule_time="23:00",
        )

        (request,), _ = mock_apm.m365.plans.create.call_args
        assert request.run_schedule_by_controller_time is True
        assert request.backup_copy.destination is storage
        assert request.backup_copy.retention.days == 60

    @pytest.mark.asyncio
    async def test_audit_log_records_name(self, mock_apm, mock_ctx, admin_server, tmp_path):
        import os
        from unittest.mock import patch

        mock_apm.m365.plans.create.return_value = make_protection_plan()

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(admin_server, "create_m365_protection_plan", mock_ctx, name="M365 Plan")

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "create_m365_protection_plan"
        assert entry["params"] == {"name": "M365 Plan"}
        assert entry["outcome"] == "ok"


class TestUpdateM365ProtectionPlan:
    @pytest.mark.asyncio
    async def test_sends_base_fields_and_resets_backup_copy_when_unset(self, mock_apm, mock_ctx, admin_server):
        mock_apm.m365.plans.update.return_value = make_protection_plan(category=WorkloadCategory.M365)

        await call_tool(
            admin_server, "update_m365_protection_plan", mock_ctx,
            plan_id="plan-001",
            name="Renamed M365 Plan",
            retention_type="keep_days",
            retention_days=45,
            retention_versions=None,
            schedule_frequency="weekly",
            schedule_time="03:00",
            weekdays=["mon", "wed"],
            description="updated",
            is_immutable=False,
        )

        plan_id, request = mock_apm.m365.plans.update.call_args[0]
        assert plan_id == "plan-001"
        assert request.name == "Renamed M365 Plan"
        assert request.retention.days == 45
        assert request.schedule.weekdays == (WeekDay.MONDAY, WeekDay.WEDNESDAY)
        assert request.backup_copy is None

    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self, mock_ctx, admin_server):
        with pytest.raises(TypeError):
            await call_tool(admin_server, "update_m365_protection_plan", mock_ctx, plan_id="plan-001", name="Renamed")

    @pytest.mark.asyncio
    async def test_audit_log_records_plan_id(self, mock_apm, mock_ctx, admin_server, tmp_path):
        import os
        from unittest.mock import patch

        mock_apm.m365.plans.update.return_value = make_protection_plan(category=WorkloadCategory.M365)

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(
                admin_server, "update_m365_protection_plan", mock_ctx,
                plan_id="plan-001", name="Renamed M365 Plan", retention_type="keep_days",
                retention_days=45, retention_versions=None, schedule_frequency="weekly",
                schedule_time="03:00", weekdays=["mon", "wed"], description="updated", is_immutable=False,
            )

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "update_m365_protection_plan"
        assert entry["params"] == {"plan_id": "plan-001"}
        assert entry["outcome"] == "ok"
