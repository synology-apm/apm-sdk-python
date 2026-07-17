"""Tests for tools/machine.py."""
from __future__ import annotations

import json

import pytest

from tests.unit.mcp.conftest import (
    assert_destructive_preview_then_execute,
    call_tool,
    make_machine_workload,
    make_protection_plan,
    make_retirement_plan,
    make_workload_version,
)


class TestListMachineWorkloads:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        wl = make_machine_workload()
        mock_apm.machine.workloads.list.return_value = ([wl], 1)

        result = await list_result(
            mock_apm.machine.workloads.list(limit=100),
            lambda x: x.to_dict(),
        )
        assert result["total"] == 1
        assert result["items"][0]["name"] == "vm-web-01"

    @pytest.mark.asyncio
    async def test_plan_ids_resolves_protection_plan_and_forwards_filter(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        plan = make_protection_plan(plan_id="plan-001")
        mock_apm.plans.get.return_value = plan
        mock_apm.machine.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_machine_workloads")
        await tool.fn(ctx=mock_ctx, plan_ids=["plan-001"])

        mock_apm.plans.get.assert_called_once_with("plan-001")
        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["plan"] == [plan]

    @pytest.mark.asyncio
    async def test_plan_ids_falls_back_to_retirement_plan_when_not_a_protection_plan(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import ResourceNotFoundError

        ret_plan = make_retirement_plan(plan_id="ret-001")
        mock_apm.plans.get.side_effect = ResourceNotFoundError(
            "not found", resource_type="ProtectionPlan", resource_id="ret-001"
        )
        mock_apm.retirement_plans.get.return_value = ret_plan
        mock_apm.machine.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_machine_workloads")
        await tool.fn(ctx=mock_ctx, plan_ids=["ret-001"])

        mock_apm.retirement_plans.get.assert_called_once_with("ret-001")
        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["plan"] == [ret_plan]

    @pytest.mark.asyncio
    async def test_hypervisor_id_forwarded_to_sdk(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_machine_workloads")
        await tool.fn(ctx=mock_ctx, hypervisor_id="hyp-001")

        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["hypervisor_id"] == "hyp-001"

    @pytest.mark.asyncio
    async def test_plan_ids_and_hypervisor_id_default_to_none(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_machine_workloads")
        await tool.fn(ctx=mock_ctx)

        mock_apm.plans.get.assert_not_called()
        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["plan"] is None
        assert kwargs["hypervisor_id"] is None

    @pytest.mark.asyncio
    async def test_status_forwarded_to_sdk_as_enum_list(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import WorkloadStatus

        mock_apm.machine.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_machine_workloads")
        await tool.fn(ctx=mock_ctx, status=["failed", "partial"])

        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["status"] == [WorkloadStatus.FAILED, WorkloadStatus.PARTIAL]

    @pytest.mark.asyncio
    async def test_verify_status_forwarded_to_sdk_as_enum_list(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import VerifyStatus

        mock_apm.machine.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_machine_workloads")
        await tool.fn(ctx=mock_ctx, verify_status=["not_enabled"])

        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["verify_status"] == [VerifyStatus.NOT_ENABLED]

    @pytest.mark.asyncio
    async def test_status_and_verify_status_default_to_none(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.machine.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_machine_workloads")
        await tool.fn(ctx=mock_ctx)

        _, kwargs = mock_apm.machine.workloads.list.call_args
        assert kwargs["status"] is None
        assert kwargs["verify_status"] is None


_WL_ID = "123e4567-e89b-12d3-a456-426614174001"


class TestBackupMachineWorkload:
    @pytest.mark.asyncio
    async def test_calls_backup_now(self, mock_apm, mock_ctx, admin_server):
        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.machine.workloads.backup_now.return_value = None

        await call_tool(
            admin_server, "backup_machine_workload", mock_ctx,
            workload_id=_WL_ID, namespace="default",
        )

        mock_apm.machine.workloads.get.assert_called_once_with(_WL_ID, "default")
        mock_apm.machine.workloads.backup_now.assert_called_once_with(wl)

    @pytest.mark.asyncio
    async def test_unknown_workload_returns_structured_error(self, mock_apm, mock_ctx, tmp_path):
        """A ResourceNotFoundError raised while resolving the workload must be caught by
        run_audited_tool's try/except (the resolve call now happens inside the closure
        passed to it) rather than propagating unhandled out of the tool function.
        """
        import json
        import os
        from unittest.mock import patch

        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import ResourceNotFoundError

        mock_apm.machine.workloads.get.side_effect = ResourceNotFoundError(
            "not found", "MachineWorkload", "does-not-exist"
        )

        server = create_server(mode="admin")
        tool = await server.get_tool("backup_machine_workload")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            result = await tool.fn(ctx=mock_ctx, workload_id="does-not-exist", namespace="default")

        parsed = json.loads(result)
        assert parsed["error"] == "not_found"
        assert parsed["resource_id"] == "does-not-exist"

        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert "ResourceNotFoundError" in entries[0]["outcome"]
        mock_apm.machine.workloads.backup_now.assert_not_called()


class TestGetMachineWorkload:
    @pytest.mark.asyncio
    async def test_resolves_by_id_and_namespace(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        wl = make_machine_workload(is_retired=True)
        mock_apm.machine.workloads.get.return_value = wl

        server = create_server(mode="admin")
        tool = await server.get_tool("get_machine_workload")
        result = await tool.fn(ctx=mock_ctx, workload_id=_WL_ID, namespace="default")

        mock_apm.machine.workloads.get.assert_called_once_with(_WL_ID, "default")
        assert json.loads(result)["is_retired"] is True

    @pytest.mark.asyncio
    async def test_not_found_returns_structured_error(self, mock_apm, mock_ctx, admin_server):
        from synology_apm.sdk import ResourceNotFoundError

        mock_apm.machine.workloads.get.side_effect = ResourceNotFoundError(
            "not found", resource_type="MachineWorkload", resource_id=_WL_ID,
        )

        raw = await call_tool(admin_server, "get_machine_workload", mock_ctx, workload_id=_WL_ID, namespace="default")
        result = json.loads(raw)

        assert result["error"] == "not_found"


class TestDeleteMachineWorkload:
    @pytest.mark.asyncio
    async def test_preview_then_execute(self, mock_apm, mock_ctx, admin_server):
        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.machine.workloads.delete.return_value = None

        await assert_destructive_preview_then_execute(
            admin_server,
            mock_ctx,
            "delete_machine_workload",
            {"workload_id": _WL_ID, "namespace": "default"},
            mock_apm.machine.workloads.delete,
            expected_target={"name": wl.name, "workload_id": wl.workload_id},
        )

        mock_apm.machine.workloads.delete.assert_called_once_with(wl)


class TestGetMachineVerificationVideoUrl:
    @pytest.mark.asyncio
    async def test_returns_url_for_resolved_version(self, mock_apm, mock_ctx, admin_server):
        wl = make_machine_workload()
        version = make_workload_version()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.machine.workloads.get_version.return_value = version
        mock_apm.machine.workloads.get_verification_video_url.return_value = "https://example.com/video.mp4"

        raw = await call_tool(
            admin_server, "get_machine_verification_video_url", mock_ctx,
            version_id="ver-001", workload_id=_WL_ID, namespace="default",
        )
        result = json.loads(raw)

        assert result["url"] == "https://example.com/video.mp4"
        assert result["version_id"] == "ver-001"
        mock_apm.machine.workloads.get_verification_video_url.assert_called_once_with(wl, version)


class TestUpdateMachineFileServer:
    @pytest.mark.asyncio
    async def test_sends_only_explicitly_supplied_fields_no_merge(self, mock_apm, mock_ctx):
        """Every field must be sent exactly as supplied by the caller — no fallback to
        any value read off the resolved workload."""
        from synology_apm.mcp._server import create_server

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_file_server")
        await tool.fn(
            ctx=mock_ctx,
            workload_id=_WL_ID,
            namespace="default",
            host_ip="192.0.2.1",
            login_user="admin",
            login_password="new-password",
            path="/data",
            host_port=8445,
            enable_vss=True,
            connection_timeout_seconds=60,
        )

        (_, request), _ = mock_apm.machine.workloads.update_file_server.call_args
        assert request.host_ip == "192.0.2.1"
        assert request.login_password == "new-password"
        assert request.host_port == 8445
        assert request.enable_vss is True
        assert request.connection_timeout_seconds == 60

    @pytest.mark.asyncio
    async def test_audit_log_records_workload_id_and_host_ip(self, mock_apm, mock_ctx, tmp_path):
        import os
        from unittest.mock import patch

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl

        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_file_server")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await tool.fn(
                ctx=mock_ctx,
                workload_id=_WL_ID,
                namespace="default",
                host_ip="192.0.2.1",
                login_user="admin",
                login_password="new-password",
                path="/data",
                host_port=8445,
                enable_vss=True,
                connection_timeout_seconds=60,
            )

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "update_machine_file_server"
        assert entry["params"] == {"workload_id": _WL_ID, "host_ip": "192.0.2.1"}
        assert entry["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_login_password_none_is_a_valid_explicit_keep_current_value(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_file_server")
        await tool.fn(
            ctx=mock_ctx,
            workload_id=_WL_ID,
            namespace="default",
            host_ip="192.0.2.1",
            login_user="admin",
            login_password=None,
            path="",
            host_port=445,
            enable_vss=False,
            connection_timeout_seconds=180,
        )

        (_, request), _ = mock_apm.machine.workloads.update_file_server.call_args
        assert request.login_password is None

    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_file_server")
        with pytest.raises(TypeError):
            await tool.fn(ctx=mock_ctx, workload_id=_WL_ID, namespace="default", host_ip="192.0.2.1", login_user="admin")

    @pytest.mark.asyncio
    async def test_selectors_param_builds_multi_selector_request_with_excluded_paths(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import FileServerPathSelector

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_file_server")
        await tool.fn(
            ctx=mock_ctx,
            workload_id=_WL_ID,
            namespace="default",
            host_ip="192.0.2.1",
            login_user="admin",
            login_password=None,
            host_port=445,
            enable_vss=False,
            connection_timeout_seconds=180,
            selectors=[{"path": "share1"}, {"path": "share2", "excluded_paths": ["tmp"]}],
        )

        (_, request), _ = mock_apm.machine.workloads.update_file_server.call_args
        assert request.selectors == (
            FileServerPathSelector(path="share1"),
            FileServerPathSelector(path="share2", excluded_paths=("tmp",)),
        )

    @pytest.mark.asyncio
    async def test_raises_when_neither_path_nor_selectors_supplied(self, mock_apm, mock_ctx):
        """The core regression guard: omitting both must fail loudly rather than silently
        collapsing an existing multi-selector/excluded-path config to a single root path."""
        from synology_apm.mcp._server import create_server

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_file_server")
        result = await tool.fn(
            ctx=mock_ctx,
            workload_id=_WL_ID,
            namespace="default",
            host_ip="192.0.2.1",
            login_user="admin",
            login_password=None,
            host_port=445,
            enable_vss=False,
            connection_timeout_seconds=180,
        )

        parsed = json.loads(result)
        assert parsed["error"] == "invalid_argument"
        mock_apm.machine.workloads.update_file_server.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_both_path_and_selectors_supplied(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        wl = make_machine_workload()
        mock_apm.machine.workloads.get.return_value = wl

        server = create_server(mode="admin")
        tool = await server.get_tool("update_machine_file_server")
        result = await tool.fn(
            ctx=mock_ctx,
            workload_id=_WL_ID,
            namespace="default",
            host_ip="192.0.2.1",
            login_user="admin",
            login_password=None,
            host_port=445,
            enable_vss=False,
            connection_timeout_seconds=180,
            path="/data",
            selectors=[{"path": "share1"}],
        )

        parsed = json.loads(result)
        assert parsed["error"] == "invalid_argument"
        mock_apm.machine.workloads.update_file_server.assert_not_called()


class TestAddMachineFileServer:
    @pytest.mark.asyncio
    async def test_defaults_to_single_root_selector_when_path_and_selectors_omitted(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import FileServerPathSelector

        server = create_server(mode="admin")
        tool = await server.get_tool("add_machine_file_server")
        await tool.fn(
            ctx=mock_ctx,
            namespace="default",
            host_ip="192.0.2.1",
            server_type="smb",
            plan_id="plan-001",
            login_user="admin",
            login_password="secret",
        )

        (request,), _ = mock_apm.machine.workloads.add_file_server.call_args
        assert request.selectors == (FileServerPathSelector(path=""),)

    @pytest.mark.asyncio
    async def test_uses_selectors_param_for_multiple_paths_with_exclusions(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import FileServerPathSelector

        server = create_server(mode="admin")
        tool = await server.get_tool("add_machine_file_server")
        await tool.fn(
            ctx=mock_ctx,
            namespace="default",
            host_ip="192.0.2.1",
            server_type="smb",
            plan_id="plan-001",
            login_user="admin",
            login_password="secret",
            selectors=[{"path": "share1"}, {"path": "share2", "excluded_paths": ["tmp"]}],
        )

        (request,), _ = mock_apm.machine.workloads.add_file_server.call_args
        assert request.selectors == (
            FileServerPathSelector(path="share1"),
            FileServerPathSelector(path="share2", excluded_paths=("tmp",)),
        )

    @pytest.mark.asyncio
    async def test_raises_when_both_path_and_selectors_supplied(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("add_machine_file_server")
        result = await tool.fn(
            ctx=mock_ctx,
            namespace="default",
            host_ip="192.0.2.1",
            server_type="smb",
            plan_id="plan-001",
            login_user="admin",
            login_password="secret",
            path="/data",
            selectors=[{"path": "share1"}],
        )

        parsed = json.loads(result)
        assert parsed["error"] == "invalid_argument"
        mock_apm.machine.workloads.add_file_server.assert_not_called()
