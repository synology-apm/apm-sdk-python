"""Tests for tools/gws.py: domain lookup + auto-backup rules + collaboration settings."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from synology_apm.sdk import GWSAutoBackupRule, GWSAutoBackupRuleListResult, GWSSharedDriveSetting
from tests.unit.mcp.conftest import (
    assert_destructive_preview_then_execute,
    call_tool,
    make_gws_domain_info,
    make_gws_workload,
)

_DOMAIN_ID = "gwsdemo.example.com"


def _make_rule(**kwargs: object) -> GWSAutoBackupRule:
    defaults: dict[str, object] = dict(
        uid="rule-001", namespace="default", domain=_DOMAIN_ID, plan_id="plan-001",
        mail_group_ids=("group-a",), calendar_group_ids=(), contact_group_ids=(), drive_group_ids=(),
    )
    defaults.update(kwargs)
    return GWSAutoBackupRule(**defaults)  # type: ignore[arg-type]


def _make_rule_list_result(**kwargs: object) -> GWSAutoBackupRuleListResult:
    defaults: dict[str, object] = dict(
        rules=(_make_rule(),),
        shared_drive_setting=None,
        include_unlicensed_accounts=False,
        include_archived_accounts=False,
    )
    defaults.update(kwargs)
    return GWSAutoBackupRuleListResult(**defaults)  # type: ignore[arg-type]


class TestGetGwsDomain:
    @pytest.mark.asyncio
    async def test_returns_tenant_dict(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        tenant = make_gws_domain_info(domain=_DOMAIN_ID, name=_DOMAIN_ID)
        mock_apm.saas.get_gws_domain.return_value = tenant

        result = await call_tool(admin_server, "get_gws_domain", mock_ctx, domain=_DOMAIN_ID)

        assert result["domain"] == _DOMAIN_ID
        assert result["category"] == "gws"
        mock_apm.saas.get_gws_domain.assert_called_once_with(_DOMAIN_ID)


class TestListGwsWorkloads:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        wl = make_gws_workload()
        mock_apm.gws.workloads.list.return_value = ([wl], 1)

        result = await call_tool(admin_server, "list_gws_workloads", mock_ctx, domain=_DOMAIN_ID)

        assert result["total"] == 1
        assert result["items"][0]["name"] == wl.name

    @pytest.mark.asyncio
    async def test_namespaces_forwarded_to_sdk_as_list(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        mock_apm.gws.workloads.list.return_value = ([], 0)

        await call_tool(admin_server, "list_gws_workloads", mock_ctx, domain=_DOMAIN_ID, namespaces=["ns-001", "ns-002"])

        _, kwargs = mock_apm.gws.workloads.list.call_args
        assert kwargs["namespace"] == ["ns-001", "ns-002"]

    @pytest.mark.asyncio
    async def test_namespaces_omitted_forwards_none(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        mock_apm.gws.workloads.list.return_value = ([], 0)

        await call_tool(admin_server, "list_gws_workloads", mock_ctx, domain=_DOMAIN_ID)

        _, kwargs = mock_apm.gws.workloads.list.call_args
        assert kwargs["namespace"] is None


class TestListGwsAutoBackupRules:
    @pytest.mark.asyncio
    async def test_returns_rules_and_shared_drive_setting(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        mock_apm.gws.auto_backup_rules.list.return_value = _make_rule_list_result(
            shared_drive_setting=GWSSharedDriveSetting(plan_id="plan-002", namespace="default", backup_user_id="backup-user-001"),
        )

        result = await call_tool(admin_server, "list_gws_auto_backup_rules", mock_ctx, domain=_DOMAIN_ID)

        assert result["rules"][0]["uid"] == "rule-001"
        assert result["shared_drive_setting"]["plan_id"] == "plan-002"
        mock_apm.gws.auto_backup_rules.list.assert_called_once_with(_DOMAIN_ID)


class TestCreateGwsAutoBackupRule:
    @pytest.mark.asyncio
    async def test_creates_rule_with_group_ids(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        mock_apm.gws.auto_backup_rules.create.return_value = None

        result = await call_tool(
            admin_server, "create_gws_auto_backup_rule", mock_ctx,
            namespace="default", plan_id="plan-001", domain=_DOMAIN_ID,
            mail_group_ids=["group-a"], calendar_group_ids=[], drive_group_ids=["group-b"],
        )

        assert result["ok"] is True
        assert result["plan_id"] == "plan-001"
        mock_apm.gws.auto_backup_rules.create.assert_called_once_with(
            domain=_DOMAIN_ID, namespace="default", plan_id="plan-001",
            mail_group_ids=["group-a"], calendar_group_ids=[], contact_group_ids=None, drive_group_ids=["group-b"],
        )


class TestUpdateGwsAutoBackupRule:
    @pytest.mark.asyncio
    async def test_forwards_explicitly_supplied_fields(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        rule = _make_rule()
        mock_apm.gws.auto_backup_rules.list.return_value = _make_rule_list_result(rules=(rule,))

        await call_tool(
            admin_server, "update_gws_auto_backup_rule", mock_ctx,
            rule_uid="rule-001", plan_id="plan-002",
            mail_group_ids=["group-b", "group-c"], drive_group_ids=[],
            domain=_DOMAIN_ID,
        )

        (called_rule,), kwargs = mock_apm.gws.auto_backup_rules.update.call_args
        assert called_rule is rule
        assert kwargs["plan_id"] == "plan-002"
        assert kwargs["mail_group_ids"] == ["group-b", "group-c"]
        assert kwargs["drive_group_ids"] == []

    @pytest.mark.asyncio
    async def test_omitted_fields_pass_none_for_keep_current(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        rule = _make_rule()
        mock_apm.gws.auto_backup_rules.list.return_value = _make_rule_list_result(rules=(rule,))

        await call_tool(admin_server, "update_gws_auto_backup_rule", mock_ctx, rule_uid="rule-001", domain=_DOMAIN_ID)

        (called_rule,), kwargs = mock_apm.gws.auto_backup_rules.update.call_args
        assert called_rule is rule
        assert kwargs["plan_id"] is None
        assert kwargs["mail_group_ids"] is None
        assert kwargs["calendar_group_ids"] is None
        assert kwargs["contact_group_ids"] is None
        assert kwargs["drive_group_ids"] is None

    @pytest.mark.asyncio
    async def test_rule_not_found_raises_invalid_argument_error(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        mock_apm.gws.auto_backup_rules.list.return_value = _make_rule_list_result(rules=())

        with pytest.raises(ToolError) as exc_info:
            await call_tool(admin_server, "update_gws_auto_backup_rule", mock_ctx, rule_uid="missing", domain=_DOMAIN_ID)

        parsed = json.loads(str(exc_info.value))
        assert parsed["error"] == "invalid_argument"
        mock_apm.gws.auto_backup_rules.update.assert_not_called()


class TestUpdateGwsCollabSettings:
    @pytest.mark.asyncio
    async def test_half_given_pair_returns_invalid_argument_error(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        with pytest.raises(ToolError) as exc_info:
            await call_tool(
                admin_server, "update_gws_collab_settings", mock_ctx,
                domain=_DOMAIN_ID, shared_drive_plan_id="plan-001", shared_drive_namespace=None,
            )
        result = json.loads(str(exc_info.value))

        assert result["error"] == "invalid_argument"
        assert "plan_id and namespace" in result["message"]
        mock_apm.gws.auto_backup_rules.update_collab_settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_pair_updates_shared_drive_setting(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        result = await call_tool(
            admin_server, "update_gws_collab_settings", mock_ctx,
            domain=_DOMAIN_ID, shared_drive_plan_id="plan-002", shared_drive_namespace="default",
            shared_drive_backup_user_id="backup-user-001",
        )

        assert result["ok"] is True
        mock_apm.gws.auto_backup_rules.update_collab_settings.assert_called_once()
        _, kwargs = mock_apm.gws.auto_backup_rules.update_collab_settings.call_args
        assert kwargs["shared_drive"].plan_id == "plan-002"
        assert kwargs["shared_drive"].namespace == "default"
        assert kwargs["shared_drive"].backup_user_id == "backup-user-001"

    @pytest.mark.asyncio
    async def test_omitted_disables_shared_drive(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        result = await call_tool(admin_server, "update_gws_collab_settings", mock_ctx, domain=_DOMAIN_ID)

        assert result["ok"] is True
        _, kwargs = mock_apm.gws.auto_backup_rules.update_collab_settings.call_args
        assert kwargs["shared_drive"] is None


class TestUpdateGwsProtectedAccountTypes:
    @pytest.mark.asyncio
    async def test_forwards_both_flags(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        result = await call_tool(
            admin_server, "update_gws_protected_account_types", mock_ctx,
            domain=_DOMAIN_ID, include_unlicensed_accounts=True, include_archived_accounts=False,
        )

        assert result["ok"] is True
        assert result["include_unlicensed_accounts"] is True
        assert result["include_archived_accounts"] is False
        mock_apm.gws.auto_backup_rules.update_protected_account_types.assert_called_once_with(
            _DOMAIN_ID, include_unlicensed_accounts=True, include_archived_accounts=False,
        )


class TestDeleteGwsAutoBackupRule:
    @pytest.mark.asyncio
    async def test_preview_then_execute(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        rule = _make_rule()
        mock_apm.gws.auto_backup_rules.list.return_value = _make_rule_list_result(rules=(rule,))
        mock_apm.gws.auto_backup_rules.delete.return_value = None

        await assert_destructive_preview_then_execute(
            admin_server,
            mock_ctx,
            "delete_gws_auto_backup_rule",
            {"rule_uid": "rule-001", "domain": _DOMAIN_ID},
            mock_apm.gws.auto_backup_rules.delete,
            expected_target={"uid": "rule-001", "domain": _DOMAIN_ID, "plan_id": "plan-001"},
        )

        mock_apm.gws.auto_backup_rules.delete.assert_called_once_with(rule)
