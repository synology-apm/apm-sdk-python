"""Tests for tools/plans/common.py, plus the shared list/get/delete contract that
applies identically to protection (common.py), retirement, and tiering plans."""
from __future__ import annotations

import json

import pytest

from tests.unit.mcp.conftest import (
    assert_destructive_preview_then_execute,
    call_tool,
    make_protection_plan,
    make_retirement_plan,
    make_tiering_plan,
)

_PLAN_LIST_GET_CASES = [
    # (kind, collection_attr, resource_factory, plan_id, expected_name)
    ("protection", "plans", make_protection_plan, "plan-001", "Daily Backup"),
    ("retirement", "retirement_plans", make_retirement_plan, "ret-001", "Compliance Retention"),
    ("tiering", "tiering_plans", make_tiering_plan, "tier-001", "30-Day Tiering"),
]


class TestListPlans:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kind,collection_attr,resource_factory,plan_id,expected_name",
        _PLAN_LIST_GET_CASES, ids=[c[0] for c in _PLAN_LIST_GET_CASES],
    )
    async def test_returns_items_and_total(
        self, mock_apm, mock_ctx, admin_server, kind, collection_attr, resource_factory, plan_id, expected_name,
    ):
        plan = resource_factory()
        getattr(mock_apm, collection_attr).list.return_value = ([plan], 1)

        raw = await call_tool(admin_server, f"list_{kind}_plans", mock_ctx)
        result = json.loads(raw)

        assert result["total"] == 1
        assert result["items"][0]["name"] == expected_name


class TestGetPlan:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kind,collection_attr,resource_factory,plan_id,expected_name",
        _PLAN_LIST_GET_CASES, ids=[c[0] for c in _PLAN_LIST_GET_CASES],
    )
    async def test_resolves_by_id_and_returns_dict(
        self, mock_apm, mock_ctx, admin_server, kind, collection_attr, resource_factory, plan_id, expected_name,
    ):
        plan = resource_factory(plan_id=plan_id)
        getattr(mock_apm, collection_attr).get.return_value = plan

        raw = await call_tool(admin_server, f"get_{kind}_plan", mock_ctx, plan_id=plan_id)
        result = json.loads(raw)

        assert result["plan_id"] == plan_id
        assert result["name"] == expected_name
        getattr(mock_apm, collection_attr).get.assert_called_once_with(plan_id)


class TestDeletePlan:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kind,collection_attr,resource_factory,plan_id,expected_name",
        _PLAN_LIST_GET_CASES, ids=[c[0] for c in _PLAN_LIST_GET_CASES],
    )
    async def test_preview_then_execute(
        self, mock_apm, mock_ctx, admin_server, kind, collection_attr, resource_factory, plan_id, expected_name,
    ):
        collection = getattr(mock_apm, collection_attr)
        plan = resource_factory(plan_id=plan_id)
        collection.get.return_value = plan
        collection.delete.return_value = None

        await assert_destructive_preview_then_execute(
            admin_server,
            mock_ctx,
            f"delete_{kind}_plan",
            {"plan_id": plan_id},
            collection.delete,
            expected_target={"name": plan.name, "plan_id": plan.plan_id},
        )

        collection.delete.assert_called_once_with(plan)
