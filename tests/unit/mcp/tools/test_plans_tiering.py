"""Tests for tools/plans/tiering.py (create/update; list/get/delete are in test_plans_common.py)."""
from __future__ import annotations

import pytest

from tests.unit.mcp.conftest import make_remote_storage, make_tiering_plan


class TestCreateTieringPlan:
    @pytest.mark.asyncio
    async def test_run_schedule_by_controller_time_reaches_request(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        storage = make_remote_storage(storage_id="stor-004")
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.tiering_plans.create.return_value = make_tiering_plan()

        server = create_server(mode="admin")
        tool = await server.get_tool("create_tiering_plan")
        await tool.fn(
            ctx=mock_ctx,
            name="Tiering Plan",
            tiering_after_days=30,
            destination_storage_id="stor-004",
            run_schedule_by_controller_time=True,
        )

        (request,), _ = mock_apm.tiering_plans.create.call_args
        assert request.run_schedule_by_controller_time is True


class TestUpdateTieringPlan:
    @pytest.mark.asyncio
    async def test_sends_only_explicitly_supplied_fields_no_merge(self, mock_apm, mock_ctx):
        """update_tiering_plan must not fetch the existing plan or merge any of its
        field values in — every field on the request comes straight from the caller."""
        from synology_apm.mcp._server import create_server

        storage = make_remote_storage(storage_id="stor-002", name="tiering-remote")
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.tiering_plans.update.return_value = make_tiering_plan(tiering_after_days=45)

        server = create_server(mode="admin")
        tool = await server.get_tool("update_tiering_plan")
        await tool.fn(
            ctx=mock_ctx,
            plan_id="tier-001",
            name="Renamed Plan",
            tiering_after_days=45,
            destination_storage_id="stor-002",
            daily_check_time="21:00",
            description="updated",
        )

        mock_apm.tiering_plans.get.assert_not_called()
        mock_apm.remote_storages.get.assert_called_once_with("stor-002")
        plan_id, request = mock_apm.tiering_plans.update.call_args[0]
        assert plan_id == "tier-001"
        assert request.name == "Renamed Plan"
        assert request.tiering_after_days == 45
        assert request.destination is storage
        assert request.description == "updated"
        assert request.daily_check_time.hour == 21

    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("update_tiering_plan")
        with pytest.raises(TypeError):
            await tool.fn(ctx=mock_ctx, plan_id="tier-001", name="Renamed Plan")
