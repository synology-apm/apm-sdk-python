"""Unit tests for apm saas commands."""
from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import WorkloadCategory
from synology_apm.sdk.models.saas import SaasTenant
from tests.unit.cli.conftest import invoke_cli

SAMPLE_M365_TENANT = SaasTenant(
    tenant_id="m365-tenant-uuid-001",
    tenant_name="Contoso",
    tenant_email="admin@contoso.com",
    category=WorkloadCategory.M365,
    protected_data_bytes=1073741824,
)

SAMPLE_GWS_TENANT = SaasTenant(
    tenant_id="gw-domain-001",
    tenant_name="Corp GWS",
    tenant_email="corp.example.com",
    category=WorkloadCategory.GWS,
    protected_data_bytes=536870912,
)


# ── saas list ─────────────────────────────────────────────────────────────────


def test_saas_list_table_shows_tenants(mock_apm: AsyncMock) -> None:
    """saas list (table) should show tenant name, provider and ID."""
    mock_apm.saas.list.return_value = ([SAMPLE_M365_TENANT, SAMPLE_GWS_TENANT], 5)

    result = invoke_cli(mock_apm, ["saas", "list"])

    assert result.exit_code == 0, result.output
    assert "Contoso" in result.output
    assert "Corp GWS" in result.output
    assert "M365" in result.output or "m365" in result.output


def test_saas_list_json_output(mock_apm: AsyncMock) -> None:
    """saas list --output json should output JSON array with serialized enum fields."""
    mock_apm.saas.list.return_value = ([SAMPLE_M365_TENANT], 5)

    result = invoke_cli(mock_apm, ["saas", "list", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["tenant_id"] == "m365-tenant-uuid-001"
    assert data[0]["tenant_name"] == "Contoso"
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

    mock_apm.saas.list.assert_called_once_with(limit=10, offset=0)


def test_saas_list_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    """saas list should exit 1 when the SDK raises an APMError."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    mock_apm.saas.list.side_effect = ResourceNotFoundError(
        "not found", resource_type="SaasTenant", resource_id="x"
    )

    result = invoke_cli(mock_apm, ["saas", "list"])

    assert result.exit_code == 1


# ── --page-all ───────────────────────────────────────────────────────────────

def test_saas_list_page_all_combines_pages(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """saas list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    second_tenant = dataclasses.replace(SAMPLE_GWS_TENANT, tenant_id="gw-domain-002", tenant_name="Second GWS")
    mock_apm.saas.list.side_effect = [
        ([SAMPLE_M365_TENANT], 2),
        ([second_tenant], 2),
    ]

    result = invoke_cli(mock_apm, [
        "saas", "list", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Contoso" in result.output
    assert "Second GWS" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.saas.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.saas.list.call_args_list[1].kwargs["offset"] == 1
