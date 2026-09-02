"""Tests for tools/plans/gws.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from synology_apm.sdk import WeekDay, WorkloadCategory
from tests.unit.mcp.conftest import call_tool, make_protection_plan, make_remote_storage


class TestCreateGwsProtectionPlan:
    @pytest.mark.asyncio
    async def test_run_schedule_by_controller_time_and_backup_copy_reach_request(
        self, mock_apm: MagicMock, mock_ctx: MagicMock
    ) -> None:
        from synology_apm.mcp._server import create_server

        storage = make_remote_storage(storage_id="stor-003")
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.gws.plans.create.return_value = make_protection_plan(category=WorkloadCategory.GWS)

        server = create_server(mode="admin")
        await call_tool(
            server, "create_gws_protection_plan", mock_ctx,
            name="GWS Plan",
            run_schedule_by_controller_time=True,
            backup_copy_destination_type="remote_storage",
            backup_copy_destination_id="stor-003",
            backup_copy_retention_type="keep_days",
            backup_copy_retention_days=60,
            backup_copy_schedule_frequency="daily",
            backup_copy_schedule_time="23:00",
        )

        (request,), _ = mock_apm.gws.plans.create.call_args
        assert request.run_schedule_by_controller_time is True
        assert request.backup_copy.destination is storage
        assert request.backup_copy.retention.days == 60

    @pytest.mark.asyncio
    async def test_audit_log_records_name(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, tmp_path: Path
    ) -> None:
        import os
        from unittest.mock import patch

        mock_apm.gws.plans.create.return_value = make_protection_plan(category=WorkloadCategory.GWS)

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(admin_server, "create_gws_protection_plan", mock_ctx, name="GWS Plan")

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "create_gws_protection_plan"
        assert entry["params"] == {"name": "GWS Plan"}
        assert entry["outcome"] == "ok"


class TestUpdateGwsProtectionPlan:
    @pytest.mark.asyncio
    async def test_sends_base_fields_and_resets_backup_copy_when_unset(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP
    ) -> None:
        mock_apm.gws.plans.update.return_value = make_protection_plan(category=WorkloadCategory.GWS)

        await call_tool(
            admin_server, "update_gws_protection_plan", mock_ctx,
            plan_id="plan-001",
            name="Renamed GWS Plan",
            retention_type="keep_days",
            retention_days=45,
            retention_versions=None,
            schedule_frequency="weekly",
            schedule_time="03:00",
            weekdays=["mon", "wed"],
            description="updated",
            is_immutable=False,
        )

        plan_id, request = mock_apm.gws.plans.update.call_args[0]
        assert plan_id == "plan-001"
        assert request.name == "Renamed GWS Plan"
        assert request.retention.days == 45
        assert request.schedule.weekdays == (WeekDay.MONDAY, WeekDay.WEDNESDAY)
        assert request.backup_copy is None

    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        with pytest.raises(TypeError):
            await call_tool(admin_server, "update_gws_protection_plan", mock_ctx, plan_id="plan-001", name="Renamed")

    @pytest.mark.asyncio
    async def test_audit_log_records_plan_id(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, tmp_path: Path
    ) -> None:
        import os
        from unittest.mock import patch

        mock_apm.gws.plans.update.return_value = make_protection_plan(category=WorkloadCategory.GWS)

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(
                admin_server, "update_gws_protection_plan", mock_ctx,
                plan_id="plan-001", name="Renamed GWS Plan", retention_type="keep_days",
                retention_days=45, retention_versions=None, schedule_frequency="weekly",
                schedule_time="03:00", weekdays=["mon", "wed"], description="updated", is_immutable=False,
            )

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "update_gws_protection_plan"
        assert entry["params"] == {"plan_id": "plan-001"}
        assert entry["outcome"] == "ok"
