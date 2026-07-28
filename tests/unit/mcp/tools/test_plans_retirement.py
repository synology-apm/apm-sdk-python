"""Tests for tools/plans/retirement.py (create/update; list/get/delete are in test_plans_common.py)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from tests.unit.mcp.conftest import call_tool, make_retirement_plan


class TestCreateRetirementPlan:
    @pytest.mark.asyncio
    async def test_run_schedule_by_controller_time_reaches_request(self, mock_apm: MagicMock, mock_ctx: MagicMock) -> None:
        from synology_apm.mcp._server import create_server

        mock_apm.retirement_plans.create.return_value = make_retirement_plan()

        server = create_server(mode="admin")
        await call_tool(server, "create_retirement_plan", mock_ctx, name="Retention Plan", run_schedule_by_controller_time=True)

        (request,), _ = mock_apm.retirement_plans.create.call_args
        assert request.run_schedule_by_controller_time is True


class TestUpdateRetirementPlan:
    @pytest.mark.asyncio
    async def test_sends_full_request_no_merge(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        from synology_apm.sdk import RetirementRetentionPolicy

        mock_apm.retirement_plans.update.return_value = make_retirement_plan(
            retention=RetirementRetentionPolicy(days=90, keep_latest_version=False)
        )

        result = await call_tool(
            admin_server, "update_retirement_plan", mock_ctx,
            plan_id="ret-001",
            name="Renamed Retention Plan",
            retention_days=90,
            keep_latest_version=False,
            description="updated",
        )

        assert result["name"] == "Compliance Retention"
        plan_id, request = mock_apm.retirement_plans.update.call_args[0]
        assert plan_id == "ret-001"
        assert request.name == "Renamed Retention Plan"
        assert request.retention_days == 90
        assert request.keep_latest_version is False
        assert request.description == "updated"
        mock_apm.retirement_plans.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        with pytest.raises(TypeError):
            await call_tool(admin_server, "update_retirement_plan", mock_ctx, plan_id="ret-001", name="Renamed")
