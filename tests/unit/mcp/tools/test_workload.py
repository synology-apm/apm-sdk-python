"""Tests for tools/_workload.py: shared machine/M365 workload tool factory.

Both categories register list/get/backup/cancel_backup/versions/lock/unlock/
change_plan/retire/delete from the same closures in register_workload_tools().
Since the underlying logic is genuinely shared, tests that exercise that shared
logic are parametrized here over workload kind (machine vs m365) rather than
hand-duplicated per category across test_machine.py/test_m365.py/this file.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from synology_apm.sdk import M365WorkloadType
from tests.unit.mcp.conftest import (
    assert_destructive_preview_then_execute,
    call_tool,
    make_m365_workload,
    make_machine_workload,
    make_protection_plan,
    make_retirement_plan,
    make_workload_version,
)

_MACHINE_WL_ID = "123e4567-e89b-12d3-a456-426614174001"
_M365_WL_ID = "123e4567-e89b-12d3-a456-426614174002"

# (kind, workload_factory, workload_id, extra_kwargs_only_m365_needs)
_WORKLOAD_KIND_CASES = [
    ("machine", make_machine_workload, _MACHINE_WL_ID, {}),
    ("m365", make_m365_workload, _M365_WL_ID, {"tenant_id": "tenant-001", "workload_type": "exchange"}),
]


def _workload_collection(mock_apm, kind):
    return mock_apm.machine.workloads if kind == "machine" else mock_apm.m365.workloads


class TestCancelWorkloadBackup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_cancels_resolved_workload(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        collection.get.return_value = wl
        collection.cancel_backup.return_value = None

        await call_tool(
            admin_server, f"cancel_{kind}_backup", mock_ctx,
            workload_id=wl_id, namespace="default", **extra_kwargs,
        )

        get_kwargs = {"tenant_id": "tenant-001", "workload_type": M365WorkloadType.EXCHANGE} if kind == "m365" else {}
        collection.get.assert_called_once_with(wl_id, "default", **get_kwargs)
        collection.cancel_backup.assert_called_once_with(wl)


class TestLockUnlockWorkloadVersion:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_lock_version_calls_sdk(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        version = make_workload_version()
        collection.get.return_value = wl
        collection.get_version.return_value = version
        collection.lock_version.return_value = None

        await call_tool(
            admin_server, f"lock_{kind}_version", mock_ctx,
            version_id="ver-001", workload_id=wl_id, namespace="default", **extra_kwargs,
        )

        collection.get_version.assert_called_once_with(wl, "ver-001")
        collection.lock_version.assert_called_once_with(version)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_unlock_version_calls_sdk(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        version = make_workload_version()
        collection.get.return_value = wl
        collection.get_version.return_value = version
        collection.unlock_version.return_value = None

        await call_tool(
            admin_server, f"unlock_{kind}_version", mock_ctx,
            version_id="ver-001", workload_id=wl_id, namespace="default", **extra_kwargs,
        )

        collection.unlock_version.assert_called_once_with(version)


class TestChangeWorkloadPlan:
    """The plan type (protection vs retirement) is determined by workload.is_retired,
    not guessed from the plan id — verified identically for both workload categories
    since both go through the same register_workload_tools() closure."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_active_workload_uses_protection_plan_collection(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory(is_retired=False)
        plan = make_protection_plan()
        collection.get.return_value = wl
        mock_apm.plans.get.return_value = plan
        collection.change_plan.return_value = None

        await call_tool(
            admin_server, f"change_{kind}_workload_plan", mock_ctx,
            plan_id="plan-001", workload_id=wl_id, namespace="default", **extra_kwargs,
        )

        mock_apm.plans.get.assert_called_once_with("plan-001")
        mock_apm.retirement_plans.get.assert_not_called()
        collection.change_plan.assert_called_once_with(wl, plan)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_retired_workload_uses_retirement_plan_collection(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory(is_retired=True)
        plan = make_retirement_plan()
        collection.get.return_value = wl
        mock_apm.retirement_plans.get.return_value = plan
        collection.change_plan.return_value = None

        await call_tool(
            admin_server, f"change_{kind}_workload_plan", mock_ctx,
            plan_id="ret-001", workload_id=wl_id, namespace="default", **extra_kwargs,
        )

        mock_apm.retirement_plans.get.assert_called_once_with("ret-001")
        mock_apm.plans.get.assert_not_called()
        collection.change_plan.assert_called_once_with(wl, plan)

    @pytest.mark.asyncio
    async def test_plan_not_found_returns_structured_error_scoped_to_the_right_type(self, mock_apm, mock_ctx, admin_server):
        from synology_apm.sdk import ResourceNotFoundError

        wl = make_machine_workload(is_retired=False)
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.plans.get.side_effect = ResourceNotFoundError("not found", "ProtectionPlan", "plan-missing")

        raw = await call_tool(
            admin_server, "change_machine_workload_plan", mock_ctx,
            workload_id=_MACHINE_WL_ID, namespace="default", plan_id="plan-missing",
        )
        parsed = json.loads(raw)

        assert parsed["error"] == "not_found"
        assert parsed["resource_id"] == "plan-missing"
        mock_apm.retirement_plans.get.assert_not_called()
        mock_apm.machine.workloads.change_plan.assert_not_called()

    @pytest.mark.asyncio
    async def test_audit_log_records_workload_id_and_plan_id(self, mock_apm, mock_ctx, admin_server, tmp_path):
        import os
        from unittest.mock import patch

        wl = make_machine_workload(is_retired=False)
        plan = make_protection_plan()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.plans.get.return_value = plan
        mock_apm.machine.workloads.change_plan.return_value = None

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(
                admin_server, "change_machine_workload_plan", mock_ctx,
                workload_id=_MACHINE_WL_ID, namespace="default", plan_id="plan-001",
            )

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "change_machine_workload_plan"
        assert entry["params"] == {"workload_id": _MACHINE_WL_ID, "plan_id": "plan-001"}
        assert entry["outcome"] == "ok"


class TestListWorkloadVersions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_passes_since_until_to_sdk(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        collection.get.return_value = wl
        collection.list_versions.return_value = ([], 0)

        await call_tool(
            admin_server, f"list_{kind}_versions", mock_ctx,
            workload_id=wl_id, namespace="default",
            since="2026-07-01T00:00:00", until="2026-07-14T00:00:00",
            **extra_kwargs,
        )

        collection.list_versions.assert_called_once()
        _, kwargs = collection.list_versions.call_args
        assert kwargs["since"] == datetime(2026, 7, 1, 0, 0, 0)
        assert kwargs["until"] == datetime(2026, 7, 14, 0, 0, 0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_since_until_default_to_none(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        collection.get.return_value = wl
        collection.list_versions.return_value = ([], 0)

        await call_tool(admin_server, f"list_{kind}_versions", mock_ctx, workload_id=wl_id, namespace="default", **extra_kwargs)

        _, kwargs = collection.list_versions.call_args
        assert kwargs["since"] is None
        assert kwargs["until"] is None


class TestGetWorkloadVersion:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_gets_version_by_id(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        version = make_workload_version()
        collection.get.return_value = wl
        collection.get_version.return_value = version

        raw = await call_tool(
            admin_server, f"get_{kind}_version", mock_ctx,
            workload_id=wl_id, namespace="default", version_id="ver-001", **extra_kwargs,
        )
        result = json.loads(raw)

        assert result["version_id"] == "ver-001"
        collection.get_version.assert_called_once_with(wl, "ver-001")
        collection.get_latest_version.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_gets_latest_version_when_version_id_omitted(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        version = make_workload_version()
        collection.get.return_value = wl
        collection.get_latest_version.return_value = version

        raw = await call_tool(admin_server, f"get_{kind}_version", mock_ctx, workload_id=wl_id, namespace="default", **extra_kwargs)
        result = json.loads(raw)

        assert result["version_id"] == version.version_id
        collection.get_latest_version.assert_called_once_with(wl)
        collection.get_version.assert_not_called()


class TestRetireWorkload:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,wl_factory,wl_id,extra_kwargs", _WORKLOAD_KIND_CASES, ids=[c[0] for c in _WORKLOAD_KIND_CASES])
    async def test_preview_then_execute(self, mock_apm, mock_ctx, admin_server, kind, wl_factory, wl_id, extra_kwargs):
        collection = _workload_collection(mock_apm, kind)
        wl = wl_factory()
        plan = make_retirement_plan(plan_id="ret-001")
        collection.get.return_value = wl
        mock_apm.retirement_plans.get.return_value = plan
        collection.retire.return_value = None

        await assert_destructive_preview_then_execute(
            admin_server,
            mock_ctx,
            f"retire_{kind}_workload",
            {"retirement_plan_id": "ret-001", "workload_id": wl_id, "namespace": "default", **extra_kwargs},
            collection.retire,
            expected_target={"name": wl.name, "workload_id": wl.workload_id},
        )

        collection.retire.assert_called_once_with(wl, plan)
        mock_apm.retirement_plans.get.assert_called_once_with("ret-001")


def _make_category(kind: str):
    from synology_apm.mcp.tools._workload_logic import WorkloadCategory

    if kind == "machine":
        return WorkloadCategory(is_m365=False, name_prefix="machine", collection_fn=lambda apm: apm.machine.workloads, serializer=lambda w: w.to_dict())
    return WorkloadCategory(is_m365=True, name_prefix="m365", collection_fn=lambda apm: apm.m365.workloads, serializer=lambda w: w.to_dict())


class TestResolveWorkload:
    """Direct tests for the module-level resolve_workload helper — the M365
    ValueError paths were previously enforced by an assert (stripped under
    python -O) and were not meaningfully testable."""

    @pytest.mark.asyncio
    async def test_machine_resolves_via_machine_collection(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_workload

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl

        result = await resolve_workload(_make_category("machine"), mock_apm, workload_id=_MACHINE_WL_ID, namespace="default")

        assert result is wl
        mock_apm.machine.workloads.get.assert_called_once_with(_MACHINE_WL_ID, "default")

    @pytest.mark.asyncio
    async def test_m365_resolves_via_m365_collection(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_workload

        wl = make_m365_workload()
        mock_apm.m365.workloads.get.return_value = wl

        result = await resolve_workload(
            _make_category("m365"), mock_apm, workload_id=_M365_WL_ID, namespace="default",
            tenant_id="tenant-001", workload_type="exchange",
        )

        assert result is wl
        mock_apm.m365.workloads.get.assert_called_once_with(
            _M365_WL_ID, "default", tenant_id="tenant-001", workload_type=M365WorkloadType.EXCHANGE,
        )

    @pytest.mark.asyncio
    async def test_m365_missing_tenant_id_raises_value_error(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_workload

        with pytest.raises(ValueError, match="tenant_id is required"):
            await resolve_workload(_make_category("m365"), mock_apm, workload_id=_M365_WL_ID, namespace="default", workload_type="exchange")

    @pytest.mark.asyncio
    async def test_m365_missing_workload_type_raises_value_error(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_workload

        with pytest.raises(ValueError, match="workload_type is required"):
            await resolve_workload(_make_category("m365"), mock_apm, workload_id=_M365_WL_ID, namespace="default", tenant_id="tenant-001")


class TestResolveVersion:
    """Direct tests for the module-level resolve_version helper, covering both
    the machine/M365 dispatch and the M365 ValueError paths."""

    @pytest.mark.asyncio
    async def test_machine_dispatches_to_machine_collection(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_version

        wl = make_machine_workload()
        version = make_workload_version()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.machine.workloads.get_version.return_value = version

        result_wl, result_version = await resolve_version(
            _make_category("machine"), mock_apm, version_id="ver-001", workload_id=_MACHINE_WL_ID, namespace="default",
        )

        assert result_wl is wl
        assert result_version is version

    @pytest.mark.asyncio
    async def test_m365_dispatches_to_m365_collection(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_version

        wl = make_m365_workload()
        version = make_workload_version()
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.workloads.get_version.return_value = version

        result_wl, result_version = await resolve_version(
            _make_category("m365"), mock_apm, version_id="ver-001", workload_id=_M365_WL_ID, namespace="default",
            tenant_id="tenant-001", workload_type="exchange",
        )

        assert result_wl is wl
        assert result_version is version

    @pytest.mark.asyncio
    async def test_m365_missing_tenant_id_raises_value_error(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_version

        with pytest.raises(ValueError, match="tenant_id is required"):
            await resolve_version(
                _make_category("m365"), mock_apm, version_id=None, workload_id=_M365_WL_ID, namespace="default",
                workload_type="exchange",
            )

    @pytest.mark.asyncio
    async def test_m365_missing_workload_type_raises_value_error(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import resolve_version

        with pytest.raises(ValueError, match="workload_type is required"):
            await resolve_version(
                _make_category("m365"), mock_apm, version_id=None, workload_id=_M365_WL_ID, namespace="default",
                tenant_id="tenant-001",
            )


class TestMutationParams:
    def test_machine_omits_tenant_and_workload_type(self):
        from synology_apm.mcp.tools._workload_logic import mutation_params

        params = mutation_params(_make_category("machine"), "wl-001", None, None, plan_id="plan-001")

        assert params == {"workload_id": "wl-001", "plan_id": "plan-001"}

    def test_m365_includes_tenant_and_workload_type(self):
        from synology_apm.mcp.tools._workload_logic import mutation_params

        params = mutation_params(_make_category("m365"), "wl-001", "tenant-001", "exchange", plan_id="plan-001")

        assert params == {
            "workload_id": "wl-001",
            "plan_id": "plan-001",
            "tenant_id": "tenant-001",
            "workload_type": "exchange",
        }


class TestDestructiveWorkloadMutation:
    """Direct tests for the shared destructive-mutation helper unifying what were
    previously separate _retire_via/_delete_via closures."""

    @pytest.mark.asyncio
    async def test_preview_returns_without_executing(self, mock_apm):
        from unittest.mock import AsyncMock

        from synology_apm.mcp.tools._workload_logic import destructive_workload_mutation

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl
        execute = AsyncMock()

        raw = await destructive_workload_mutation(
            _make_category("machine"), mock_apm,
            action_verb="delete", warning="This is destructive.",
            workload_id=_MACHINE_WL_ID, namespace="default", confirm=False, execute_fn=execute,
        )
        result = json.loads(raw)

        assert result["preview"] is True
        assert result["action"] == "delete_machine_workload"
        assert result["target"] == {"name": wl.name, "workload_id": wl.workload_id}
        execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_executes_and_returns_result(self, mock_apm):
        from unittest.mock import AsyncMock

        from synology_apm.mcp.tools._workload_logic import destructive_workload_mutation

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl
        execute = AsyncMock(return_value={"ok": True})

        raw = await destructive_workload_mutation(
            _make_category("machine"), mock_apm,
            action_verb="delete", warning="This is destructive.",
            workload_id=_MACHINE_WL_ID, namespace="default", confirm=True, execute_fn=execute,
        )
        result = json.loads(raw)

        assert result == {"ok": True}
        execute.assert_called_once_with(wl)


class TestRetireWorkloadHelper:
    """Direct unit test for the module-level retire_workload helper — otherwise
    only exercised indirectly through the retire_machine_workload/retire_m365_workload
    wrapper tools above."""

    @pytest.mark.asyncio
    async def test_resolves_plan_and_calls_retire(self, mock_apm):
        from synology_apm.mcp.tools._workload_logic import retire_workload

        wl = make_machine_workload()
        plan = make_retirement_plan(plan_id="ret-001")
        mock_apm.retirement_plans.get.return_value = plan
        mock_apm.machine.workloads.retire.return_value = None

        result = await retire_workload(mock_apm, wl, "ret-001", lambda apm: apm.machine.workloads)

        mock_apm.retirement_plans.get.assert_called_once_with("ret-001")
        mock_apm.machine.workloads.retire.assert_called_once_with(wl, plan)
        assert result == {"ok": True, "workload_id": wl.workload_id, "retirement_plan_id": "ret-001"}
