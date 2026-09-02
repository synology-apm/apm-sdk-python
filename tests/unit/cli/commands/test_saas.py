"""Unit tests for apm saas commands."""
from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import WorkloadCategory
from synology_apm.sdk.models.saas import GWSDomainInfo, M365TenantInfo
from tests.unit.cli.conftest import invoke_cli

SAMPLE_M365_TENANT = M365TenantInfo(
    tenant_id="m365-tenant-uuid-001",
    name="Contoso",
    domain="contoso.onmicrosoft.com",
    category=WorkloadCategory.M365,
    protected_data_bytes=1073741824,
)

SAMPLE_GWS_DOMAIN = GWSDomainInfo(
    domain="gwsdemo.example.com",
    name="gwsdemo.example.com",
    domain_admin="evelyn.test@gwsdemo.example.com",
    category=WorkloadCategory.GWS,
    protected_data_bytes=536870912,
)


# ── saas list ─────────────────────────────────────────────────────────────────


def test_saas_list_table_shows_tenants(mock_apm: AsyncMock) -> None:
    """saas list (table) should show tenant name, provider and ID."""
    mock_apm.saas.list.return_value = ([SAMPLE_M365_TENANT, SAMPLE_GWS_DOMAIN], 5)

    result = invoke_cli(mock_apm, ["saas", "list"])

    assert result.exit_code == 0, result.output
    assert "Contoso" in result.output
    assert "gwsdemo.example.com" in result.output
    assert "M365" in result.output or "m365" in result.output


def test_saas_list_json_output(mock_apm: AsyncMock) -> None:
    """saas list --output json should output JSON array with serialized enum fields."""
    mock_apm.saas.list.return_value = ([SAMPLE_M365_TENANT], 5)

    result = invoke_cli(mock_apm, ["saas", "list", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["tenant_id"] == "m365-tenant-uuid-001"
    assert data[0]["name"] == "Contoso"
    assert data[0]["category"] == "m365"


def test_saas_list_yaml_output(mock_apm: AsyncMock) -> None:
    """saas list --output yaml should output YAML."""
    mock_apm.saas.list.return_value = ([SAMPLE_M365_TENANT], 5)

    result = invoke_cli(mock_apm, ["saas", "list", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "tenant_id: m365-tenant-uuid-001" in result.output


def test_saas_list_csv_output(mock_apm: AsyncMock) -> None:
    """saas list --output csv should output CSV with a tenant_id field."""
    mock_apm.saas.list.return_value = ([SAMPLE_M365_TENANT], 5)

    result = invoke_cli(mock_apm, ["saas", "list", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "tenant_id" in lines[0]
    assert "m365-tenant-uuid-001" in result.output


def test_saas_list_empty_result(mock_apm: AsyncMock) -> None:
    """saas list with no tenants should succeed with empty output."""
    mock_apm.saas.list.return_value = ([], 5)

    result = invoke_cli(mock_apm, ["saas", "list"])

    assert result.exit_code == 0
    assert "Showing 0 of 5" in result.output


def test_saas_list_passes_limit_to_sdk(mock_apm: AsyncMock) -> None:
    """saas list --limit 10 should call saas.list(limit=10)."""
    mock_apm.saas.list.return_value = ([], 5)

    invoke_cli(mock_apm, ["saas", "list", "--limit", "10"])

    mock_apm.saas.list.assert_called_once_with(keyword=None, limit=10, offset=0)


def test_saas_list_search_passes_keyword_to_sdk(mock_apm: AsyncMock) -> None:
    """saas list --search contoso should call saas.list(keyword="contoso")."""
    mock_apm.saas.list.return_value = ([], 5)

    invoke_cli(mock_apm, ["saas", "list", "--search", "contoso"])

    mock_apm.saas.list.assert_called_once_with(keyword="contoso", limit=25, offset=0)


def test_saas_list_search_short_flag(mock_apm: AsyncMock) -> None:
    """saas list -s contoso should behave the same as --search."""
    mock_apm.saas.list.return_value = ([], 5)

    invoke_cli(mock_apm, ["saas", "list", "-s", "contoso"])

    mock_apm.saas.list.assert_called_once_with(keyword="contoso", limit=25, offset=0)


def test_saas_list_verbose_shows_domain_admin(mock_apm: AsyncMock) -> None:
    """saas list -v should add a Domain Admin column, populated for GWS and blank for M365."""
    mock_apm.saas.list.return_value = ([SAMPLE_M365_TENANT, SAMPLE_GWS_DOMAIN], 2)

    result = invoke_cli(mock_apm, ["saas", "list", "-v"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Domain Admin" in result.output
    assert "evelyn.test@gwsdemo.example.com" in result.output


def test_saas_list_no_verbose_hides_domain_admin(mock_apm: AsyncMock) -> None:
    """saas list without -v should not show the Domain Admin column."""
    mock_apm.saas.list.return_value = ([SAMPLE_GWS_DOMAIN], 1)

    result = invoke_cli(mock_apm, ["saas", "list"])

    assert result.exit_code == 0, result.output
    assert "Domain Admin" not in result.output


def test_saas_list_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    """saas list should exit 1 when the SDK raises an APMError."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    mock_apm.saas.list.side_effect = ResourceNotFoundError(
        "not found", resource_type="M365TenantInfo", resource_id="x"
    )

    result = invoke_cli(mock_apm, ["saas", "list"])

    assert result.exit_code == 1


# ── --page-all ───────────────────────────────────────────────────────────────

def test_saas_list_page_all_combines_pages(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """saas list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    second_domain = dataclasses.replace(SAMPLE_GWS_DOMAIN, domain="gwsdemo2.example.com", name="gwsdemo2.example.com")
    mock_apm.saas.list.side_effect = [
        ([SAMPLE_M365_TENANT], 2),
        ([second_domain], 2),
    ]

    result = invoke_cli(mock_apm, [
        "saas", "list", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Contoso" in result.output
    assert "gwsdemo2.example.com" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.saas.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.saas.list.call_args_list[1].kwargs["offset"] == 1
