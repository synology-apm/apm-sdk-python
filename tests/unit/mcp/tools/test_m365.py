"""Tests for tools/m365.py: tenant, workload, export tools."""
from __future__ import annotations

import json

import pytest

from synology_apm.sdk import M365WorkloadType
from tests.unit.mcp.conftest import (
    assert_destructive_preview_then_execute,
    call_tool,
    make_export_activity,
    make_m365_workload,
    make_protection_plan,
    make_saas_tenant,
    make_workload_version,
)

_WL_ID = "123e4567-e89b-12d3-a456-426614174002"


class TestListSaasTenants:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm, mock_ctx, admin_server):
        tenant = make_saas_tenant()
        mock_apm.saas.list.return_value = ([tenant], 1)

        raw = await call_tool(admin_server, "list_saas_tenants", mock_ctx)
        result = json.loads(raw)

        assert result["total"] == 1
        assert result["items"][0]["tenant_name"] == "Contoso"
        _, kwargs = mock_apm.saas.list.call_args
        assert kwargs["limit"] == 100
        assert kwargs["offset"] == 0


class TestGetSaasTenant:
    @pytest.mark.asyncio
    async def test_returns_tenant_dict(self, mock_apm, mock_ctx, admin_server):
        tenant = make_saas_tenant()
        mock_apm.saas.get_m365_tenant.return_value = tenant

        raw = await call_tool(admin_server, "get_saas_tenant", mock_ctx, tenant_id="tenant-001")
        result = json.loads(raw)

        assert result["tenant_id"] == "tenant-001"
        assert result["tenant_name"] == "Contoso"
        mock_apm.saas.get_m365_tenant.assert_called_once_with("tenant-001")


class TestListM365Workloads:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        wl = make_m365_workload()
        mock_apm.m365.workloads.list.return_value = ([wl], 1)

        result = await list_result(
            mock_apm.m365.workloads.list(tenant_id="tenant-001", workload_type=M365WorkloadType.EXCHANGE, limit=100),
            lambda x: x.to_dict(),
        )
        assert result["total"] == 1
        assert result["items"][0]["name"] == "alice@contoso.com"
        assert result["items"][0]["workload_type"] == "exchange"

    @pytest.mark.asyncio
    async def test_plan_ids_resolves_and_forwards_plan_filter(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        plan = make_protection_plan(plan_id="plan-001")
        mock_apm.plans.get.return_value = plan
        mock_apm.m365.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_m365_workloads")
        await tool.fn(ctx=mock_ctx, tenant_id="tenant-001", plan_ids=["plan-001"])

        mock_apm.plans.get.assert_called_once_with("plan-001")
        _, kwargs = mock_apm.m365.workloads.list.call_args
        assert kwargs["plan"] == [plan]

    @pytest.mark.asyncio
    async def test_plan_ids_omitted_forwards_none(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.m365.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_m365_workloads")
        await tool.fn(ctx=mock_ctx, tenant_id="tenant-001")

        mock_apm.plans.get.assert_not_called()
        _, kwargs = mock_apm.m365.workloads.list.call_args
        assert kwargs["plan"] is None

    @pytest.mark.asyncio
    async def test_status_forwarded_to_sdk_as_enum_list(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import WorkloadStatus

        mock_apm.m365.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_m365_workloads")
        await tool.fn(ctx=mock_ctx, tenant_id="tenant-001", status=["failed", "partial"])

        _, kwargs = mock_apm.m365.workloads.list.call_args
        assert kwargs["status"] == [WorkloadStatus.FAILED, WorkloadStatus.PARTIAL]

    @pytest.mark.asyncio
    async def test_status_omitted_forwards_none(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        mock_apm.m365.workloads.list.return_value = ([], 0)

        server = create_server(mode="admin")
        tool = await server.get_tool("list_m365_workloads")
        await tool.fn(ctx=mock_ctx, tenant_id="tenant-001")

        _, kwargs = mock_apm.m365.workloads.list.call_args
        assert kwargs["status"] is None


class TestUpdateM365CollabSettings:
    @pytest.mark.asyncio
    async def test_half_given_pair_returns_invalid_argument_error(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("update_m365_collab_settings")
        raw = await tool.fn(
            ctx=mock_ctx,
            tenant_id="tenant-001",
            group_exchange_plan_id="plan-001",
            group_exchange_namespace=None,
        )
        result = json.loads(raw)

        assert result["error"] == "invalid_argument"
        assert "plan_id and namespace" in result["message"]
        mock_apm.m365.auto_backup_rules.update_collab_settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_pair_updates_that_type_only(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("update_m365_collab_settings")
        raw = await tool.fn(
            ctx=mock_ctx,
            tenant_id="tenant-001",
            teams_plan_id="plan-002",
            teams_namespace="default",
        )
        result = json.loads(raw)

        assert result["ok"] is True
        mock_apm.m365.auto_backup_rules.update_collab_settings.assert_called_once()
        _, kwargs = mock_apm.m365.auto_backup_rules.update_collab_settings.call_args
        assert kwargs["teams"].plan_id == "plan-002"
        assert kwargs["teams"].namespace == "default"
        assert kwargs["group_exchange"] is None
        assert kwargs["mysite"] is None
        assert kwargs["sharepoint"] is None


class TestDeleteM365Workload:
    @pytest.mark.asyncio
    async def test_preview_then_execute(self, mock_apm, mock_ctx, admin_server):
        wl = make_m365_workload()
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.workloads.delete.return_value = None

        await assert_destructive_preview_then_execute(
            admin_server,
            mock_ctx,
            "delete_m365_workload",
            {
                "workload_id": _WL_ID,
                "namespace": "default",
                "tenant_id": "tenant-001",
                "workload_type": "exchange",
            },
            mock_apm.m365.workloads.delete,
            expected_target={"name": wl.name, "workload_id": wl.workload_id},
        )

        mock_apm.m365.workloads.delete.assert_called_once_with(wl)


class TestBackupM365Workload:
    @pytest.mark.asyncio
    async def test_unknown_workload_returns_structured_error(self, mock_apm, mock_ctx, tmp_path):
        """Mirrors the machine-workload case: resolve failures must be caught inside the
        closure passed to run_audited_tool, not propagate unhandled out of the tool.
        """
        import os
        from unittest.mock import patch

        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import ResourceNotFoundError

        mock_apm.m365.workloads.get.side_effect = ResourceNotFoundError(
            "not found", "M365Workload", "does-not-exist"
        )

        server = create_server(mode="admin")
        tool = await server.get_tool("backup_m365_workload")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            result = await tool.fn(ctx=mock_ctx, workload_id="does-not-exist", namespace="default", tenant_id="tenant-001", workload_type="exchange")

        parsed = json.loads(result)
        assert parsed["error"] == "not_found"
        assert parsed["resource_id"] == "does-not-exist"

        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert "ResourceNotFoundError" in entries[0]["outcome"]
        mock_apm.m365.workloads.backup_now.assert_not_called()

    @pytest.mark.asyncio
    async def test_audit_log_includes_tenant_id_and_workload_type(self, mock_apm, mock_ctx, tmp_path):
        """The M365 audit trail must record which tenant/workload_type a mutation
        targeted, not just workload_name/workload_id (which are ambiguous across tenants)."""
        import os
        from unittest.mock import patch

        from synology_apm.mcp._server import create_server

        wl = make_m365_workload()
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.workloads.backup_now.return_value = None

        server = create_server(mode="admin")
        tool = await server.get_tool("backup_m365_workload")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await tool.fn(ctx=mock_ctx, workload_id=_WL_ID, namespace="default", tenant_id="tenant-001", workload_type="exchange")

        entry = json.loads(log_file.read_text().strip())
        assert entry["params"]["tenant_id"] == "tenant-001"
        assert entry["params"]["workload_type"] == "exchange"


class TestGetM365Workload:
    @pytest.mark.asyncio
    async def test_resolves_by_id_and_namespace(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server

        wl = make_m365_workload(is_retired=True)
        mock_apm.m365.workloads.get.return_value = wl

        server = create_server(mode="admin")
        tool = await server.get_tool("get_m365_workload")
        result = await tool.fn(ctx=mock_ctx, workload_id=_WL_ID, namespace="default", tenant_id="tenant-001", workload_type="exchange")

        mock_apm.m365.workloads.get.assert_called_once()
        assert json.loads(result)["is_retired"] is True


class TestM365WorkloadTypeResolution:
    @pytest.mark.asyncio
    async def test_get_m365_workload_requires_workload_type(self, mock_ctx):
        """workload_type has no sensible universal default for a get-by-id tool, so it
        must be a required parameter — omitting it is a schema error, not a runtime
        ValueError from deep inside a resolve helper."""
        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("get_m365_workload")
        with pytest.raises(TypeError):
            await tool.fn(ctx=mock_ctx, workload_id=_WL_ID, namespace="default", tenant_id="tenant-001")


class TestUpdateM365AutoBackupRule:
    @pytest.mark.asyncio
    async def test_forwards_explicitly_supplied_fields(self, mock_apm, mock_ctx):
        """Fields explicitly supplied by the caller are forwarded exactly as given —
        an empty group-id list clears that list."""
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import M365AutoBackupRule, M365AutoBackupRuleListResult, M365CollabServiceSetting

        rule = M365AutoBackupRule(
            uid="rule-001", namespace="default", tenant_id="tenant-001", plan_id="plan-001",
            exchange_group_ids=("group-a",), onedrive_group_ids=(), chat_group_ids=(),
        )
        _disabled = M365CollabServiceSetting(plan_id="", namespace="")
        mock_apm.m365.auto_backup_rules.list.return_value = M365AutoBackupRuleListResult(
            rules=(rule,), group_exchange=_disabled, mysite=_disabled, sharepoint=_disabled, teams=_disabled,
        )

        server = create_server(mode="admin")
        tool = await server.get_tool("update_m365_auto_backup_rule")
        await tool.fn(
            ctx=mock_ctx,
            rule_uid="rule-001",
            plan_id="plan-002",
            exchange_group_ids=["group-b", "group-c"],
            onedrive_group_ids=[],
            chat_group_ids=["group-d"],
            tenant_id="tenant-001",
        )

        (called_rule,), kwargs = mock_apm.m365.auto_backup_rules.update.call_args
        assert called_rule is rule
        assert kwargs["plan_id"] == "plan-002"
        assert kwargs["exchange_group_ids"] == ["group-b", "group-c"]
        assert kwargs["onedrive_group_ids"] == []
        assert kwargs["chat_group_ids"] == ["group-d"]

    @pytest.mark.asyncio
    async def test_omitted_fields_pass_none_for_keep_current(self, mock_apm, mock_ctx):
        """Every field is optional; omitting one passes None through to the SDK's
        update(), which keeps that field's current value unchanged."""
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import M365AutoBackupRule, M365AutoBackupRuleListResult, M365CollabServiceSetting

        rule = M365AutoBackupRule(
            uid="rule-001", namespace="default", tenant_id="tenant-001", plan_id="plan-001",
            exchange_group_ids=("group-a",), onedrive_group_ids=(), chat_group_ids=(),
        )
        _disabled = M365CollabServiceSetting(plan_id="", namespace="")
        mock_apm.m365.auto_backup_rules.list.return_value = M365AutoBackupRuleListResult(
            rules=(rule,), group_exchange=_disabled, mysite=_disabled, sharepoint=_disabled, teams=_disabled,
        )

        server = create_server(mode="admin")
        tool = await server.get_tool("update_m365_auto_backup_rule")
        await tool.fn(ctx=mock_ctx, rule_uid="rule-001", tenant_id="tenant-001")

        (called_rule,), kwargs = mock_apm.m365.auto_backup_rules.update.call_args
        assert called_rule is rule
        assert kwargs["plan_id"] is None
        assert kwargs["exchange_group_ids"] is None
        assert kwargs["onedrive_group_ids"] is None
        assert kwargs["chat_group_ids"] is None

    @pytest.mark.asyncio
    async def test_rule_not_found_raises_value_error(self, mock_apm, mock_ctx):
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import M365AutoBackupRuleListResult, M365CollabServiceSetting

        _disabled = M365CollabServiceSetting(plan_id="", namespace="")
        mock_apm.m365.auto_backup_rules.list.return_value = M365AutoBackupRuleListResult(
            rules=(), group_exchange=_disabled, mysite=_disabled, sharepoint=_disabled, teams=_disabled,
        )

        server = create_server(mode="admin")
        tool = await server.get_tool("update_m365_auto_backup_rule")
        result = await tool.fn(
            ctx=mock_ctx,
            rule_uid="missing",
            plan_id="plan-002",
            exchange_group_ids=[],
            onedrive_group_ids=[],
            chat_group_ids=[],
            tenant_id="tenant-001",
        )

        parsed = json.loads(result)
        assert parsed["error"] == "invalid_argument"
        mock_apm.m365.auto_backup_rules.update.assert_not_called()


class TestStartExchangeExport:
    @pytest.mark.asyncio
    async def test_location_id_forwarded_to_sdk(self, mock_apm, mock_ctx):
        from synology_apm.sdk import LocationInfo, M365ExportStartResult, VersionLocation

        wl = make_m365_workload()
        version = make_workload_version()
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.workloads.get_version.return_value = version
        mock_apm.m365.exchange_export.start.return_value = M365ExportStartResult(
            execution_id="exec-001",
            ready_to_download=True,
            export_name="alice.pst",
            location=VersionLocation(namespace="default", location_info=LocationInfo(is_remote_storage=False, identifier="srv-001", name="apm-server-01", endpoint="192.0.2.1", vault=None), location_id="loc-002", connection_id=None),
            workload=wl,
            version=version,
        )

        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("start_exchange_export")
        await tool.fn(
            ctx=mock_ctx,
            version_id="ver-001",
            workload_id=_WL_ID,
            namespace="default",
            tenant_id="tenant-001",
            location_id="loc-002",
        )

        mock_apm.m365.exchange_export.start.assert_called_once()
        _, kwargs = mock_apm.m365.exchange_export.start.call_args
        assert kwargs["location_id"] == "loc-002"

    @pytest.mark.asyncio
    async def test_location_id_defaults_to_none(self, mock_apm, mock_ctx):
        from synology_apm.sdk import LocationInfo, M365ExportStartResult, VersionLocation

        wl = make_m365_workload()
        version = make_workload_version()
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.workloads.get_version.return_value = version
        mock_apm.m365.exchange_export.start.return_value = M365ExportStartResult(
            execution_id="exec-001",
            ready_to_download=True,
            export_name="alice.pst",
            location=VersionLocation(namespace="default", location_info=LocationInfo(is_remote_storage=False, identifier="srv-001", name="apm-server-01", endpoint="192.0.2.1", vault=None), location_id="loc-001", connection_id=None),
            workload=wl,
            version=version,
        )

        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("start_exchange_export")
        await tool.fn(
            ctx=mock_ctx,
            version_id="ver-001",
            workload_id=_WL_ID,
            namespace="default",
            tenant_id="tenant-001",
        )

        _, kwargs = mock_apm.m365.exchange_export.start.call_args
        assert kwargs["location_id"] is None


class TestStartGroupExport:
    @pytest.mark.asyncio
    async def test_location_id_forwarded_to_sdk(self, mock_apm, mock_ctx):
        from synology_apm.sdk import LocationInfo, M365ExportStartResult, VersionLocation

        wl = make_m365_workload(workload_type=M365WorkloadType.GROUP)
        version = make_workload_version()
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.workloads.get_version.return_value = version
        mock_apm.m365.group_export.start.return_value = M365ExportStartResult(
            execution_id="exec-002",
            ready_to_download=True,
            export_name="marketing.pst",
            location=VersionLocation(namespace="default", location_info=LocationInfo(is_remote_storage=False, identifier="srv-001", name="apm-server-01", endpoint="192.0.2.1", vault=None), location_id="loc-002", connection_id=None),
            workload=wl,
            version=version,
        )

        from synology_apm.mcp._server import create_server

        server = create_server(mode="admin")
        tool = await server.get_tool("start_group_export")
        await tool.fn(
            ctx=mock_ctx,
            version_id="ver-001",
            workload_id=_WL_ID,
            namespace="default",
            tenant_id="tenant-001",
            workload_type="group",
            location_id="loc-002",
        )

        mock_apm.m365.group_export.start.assert_called_once()
        _, kwargs = mock_apm.m365.group_export.start.call_args
        assert kwargs["location_id"] == "loc-002"


class TestListM365AutoBackupRules:
    @pytest.mark.asyncio
    async def test_returns_rules_and_collab_settings(self, mock_apm, mock_ctx, admin_server):
        from synology_apm.sdk import M365AutoBackupRule, M365AutoBackupRuleListResult, M365CollabServiceSetting

        rule = M365AutoBackupRule(
            uid="rule-001", namespace="default", tenant_id="tenant-001", plan_id="plan-001",
            exchange_group_ids=("group-a",), onedrive_group_ids=(), chat_group_ids=(),
        )
        _disabled = M365CollabServiceSetting(plan_id="", namespace="")
        mock_apm.m365.auto_backup_rules.list.return_value = M365AutoBackupRuleListResult(
            rules=(rule,), group_exchange=_disabled, mysite=_disabled, sharepoint=_disabled, teams=_disabled,
        )

        raw = await call_tool(admin_server, "list_m365_auto_backup_rules", mock_ctx, tenant_id="tenant-001")
        result = json.loads(raw)

        assert result["rules"][0]["uid"] == "rule-001"
        mock_apm.m365.auto_backup_rules.list.assert_called_once_with("tenant-001")


class TestListExchangeExports:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm, mock_ctx, admin_server):
        wl = make_m365_workload()
        act = make_export_activity()
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.exchange_export.list.return_value = ([act], 1)

        raw = await call_tool(
            admin_server, "list_exchange_exports", mock_ctx,
            workload_id=_WL_ID, namespace="default", tenant_id="tenant-001",
        )
        result = json.loads(raw)

        assert result["total"] == 1
        assert result["items"][0]["activity_id"] == "exp-001"
        mock_apm.m365.exchange_export.list.assert_called_once()
        (called_wl,), kwargs = mock_apm.m365.exchange_export.list.call_args
        assert called_wl is wl
        assert kwargs["limit"] == 100


class TestCancelExchangeExport:
    @pytest.mark.asyncio
    async def test_resolves_activity_and_cancels(self, mock_apm, mock_ctx, admin_server):
        wl = make_m365_workload()
        act = make_export_activity(activity_id="exp-001")
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.exchange_export.list.return_value = ([act], 1)
        mock_apm.m365.exchange_export.cancel.return_value = None

        raw = await call_tool(
            admin_server, "cancel_exchange_export", mock_ctx,
            activity_id="exp-001", workload_id=_WL_ID, namespace="default", tenant_id="tenant-001",
        )
        result = json.loads(raw)

        assert result["ok"] is True
        mock_apm.m365.exchange_export.cancel.assert_called_once_with(act)


class TestGetExchangeExportDownloadUrl:
    @pytest.mark.asyncio
    async def test_returns_url(self, mock_apm, mock_ctx, admin_server):
        wl = make_m365_workload()
        act = make_export_activity(activity_id="exp-001")
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.exchange_export.list.return_value = ([act], 1)
        mock_apm.m365.exchange_export.get_download_url_by_activity.return_value = "https://example.com/alice.pst"

        raw = await call_tool(
            admin_server, "get_exchange_export_download_url", mock_ctx,
            activity_id="exp-001", workload_id=_WL_ID, namespace="default", tenant_id="tenant-001",
        )
        result = json.loads(raw)

        assert result["url"] == "https://example.com/alice.pst"
        mock_apm.m365.exchange_export.get_download_url_by_activity.assert_called_once_with(act)


class TestListGroupExports:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm, mock_ctx, admin_server):
        wl = make_m365_workload(workload_type=M365WorkloadType.GROUP)
        act = make_export_activity(activity_id="exp-group-001")
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.group_export.list.return_value = ([act], 1)

        raw = await call_tool(
            admin_server, "list_group_exports", mock_ctx,
            workload_id=_WL_ID, namespace="default", tenant_id="tenant-001", workload_type="group",
        )
        result = json.loads(raw)

        assert result["total"] == 1
        assert result["items"][0]["activity_id"] == "exp-group-001"


class TestCancelGroupExport:
    @pytest.mark.asyncio
    async def test_resolves_activity_and_cancels(self, mock_apm, mock_ctx, admin_server):
        wl = make_m365_workload(workload_type=M365WorkloadType.GROUP)
        act = make_export_activity(activity_id="exp-group-001")
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.group_export.list.return_value = ([act], 1)
        mock_apm.m365.group_export.cancel.return_value = None

        raw = await call_tool(
            admin_server, "cancel_group_export", mock_ctx,
            activity_id="exp-group-001", workload_id=_WL_ID, namespace="default",
            tenant_id="tenant-001", workload_type="group",
        )
        result = json.loads(raw)

        assert result["ok"] is True
        mock_apm.m365.group_export.cancel.assert_called_once_with(act)


class TestGetGroupExportDownloadUrl:
    @pytest.mark.asyncio
    async def test_returns_url(self, mock_apm, mock_ctx, admin_server):
        wl = make_m365_workload(workload_type=M365WorkloadType.GROUP)
        act = make_export_activity(activity_id="exp-group-001")
        mock_apm.m365.workloads.get.return_value = wl
        mock_apm.m365.group_export.list.return_value = ([act], 1)
        mock_apm.m365.group_export.get_download_url_by_activity.return_value = "https://example.com/marketing.pst"

        raw = await call_tool(
            admin_server, "get_group_export_download_url", mock_ctx,
            activity_id="exp-group-001", workload_id=_WL_ID, namespace="default",
            tenant_id="tenant-001", workload_type="group",
        )
        result = json.loads(raw)

        assert result["url"] == "https://example.com/marketing.pst"
        mock_apm.m365.group_export.get_download_url_by_activity.assert_called_once_with(act)


class TestCreateM365AutoBackupRule:
    @pytest.mark.asyncio
    async def test_creates_rule_with_group_ids(self, mock_apm, mock_ctx, admin_server):
        mock_apm.m365.auto_backup_rules.create.return_value = None

        raw = await call_tool(
            admin_server, "create_m365_auto_backup_rule", mock_ctx,
            namespace="default", plan_id="plan-001", tenant_id="tenant-001",
            exchange_group_ids=["group-a"], onedrive_group_ids=[], chat_group_ids=["group-b"],
        )
        result = json.loads(raw)

        assert result["ok"] is True
        assert result["plan_id"] == "plan-001"
        mock_apm.m365.auto_backup_rules.create.assert_called_once_with(
            tenant_id="tenant-001", namespace="default", plan_id="plan-001",
            exchange_group_ids=["group-a"], onedrive_group_ids=[], chat_group_ids=["group-b"],
        )


class TestDeleteM365AutoBackupRule:
    @pytest.mark.asyncio
    async def test_preview_then_execute(self, mock_apm, mock_ctx, admin_server):
        from synology_apm.sdk import M365AutoBackupRule, M365AutoBackupRuleListResult, M365CollabServiceSetting

        rule = M365AutoBackupRule(
            uid="rule-001", namespace="default", tenant_id="tenant-001", plan_id="plan-001",
            exchange_group_ids=("group-a",), onedrive_group_ids=(), chat_group_ids=(),
        )
        _disabled = M365CollabServiceSetting(plan_id="", namespace="")
        mock_apm.m365.auto_backup_rules.list.return_value = M365AutoBackupRuleListResult(
            rules=(rule,), group_exchange=_disabled, mysite=_disabled, sharepoint=_disabled, teams=_disabled,
        )
        mock_apm.m365.auto_backup_rules.delete.return_value = None

        await assert_destructive_preview_then_execute(
            admin_server,
            mock_ctx,
            "delete_m365_auto_backup_rule",
            {"rule_uid": "rule-001", "tenant_id": "tenant-001"},
            mock_apm.m365.auto_backup_rules.delete,
            expected_target={"uid": "rule-001", "tenant_id": "tenant-001", "plan_id": "plan-001"},
        )

        mock_apm.m365.auto_backup_rules.delete.assert_called_once_with(rule)
