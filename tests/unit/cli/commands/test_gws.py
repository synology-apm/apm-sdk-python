"""Unit tests for apm gws error paths, YAML output, and cross-cutting validation."""
from __future__ import annotations

from unittest.mock import AsyncMock

from synology_apm.sdk.exceptions import ResourceNotFoundError
from tests.unit.cli.commands._gws_fixtures import (
    DOMAIN_ID,
    NAMESPACE,
    SAMPLE_TENANT,
    WORKLOAD_UID,
    make_mock_apm,
)
from tests.unit.cli.conftest import invoke_cli


def _gws_error() -> ResourceNotFoundError:
    return ResourceNotFoundError("not found", resource_type="GWSWorkload", resource_id="x")


def test_gws_mail_list_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.saas.list.return_value = ([SAMPLE_TENANT], 5)
    mock_apm.saas.get_gws_domain.return_value = SAMPLE_TENANT
    mock_apm.gws.workloads.list.side_effect = _gws_error()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID])

    assert result.exit_code == 1

def test_gws_mail_get_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.gws.workloads.get.side_effect = _gws_error()

    result = invoke_cli(mock_apm, ["gws", "mail", "get", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 1

def test_gws_mail_backup_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.gws.workloads.get.side_effect = _gws_error()

    result = invoke_cli(mock_apm, ["gws", "mail", "backup", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 1

def test_gws_mail_retire_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.gws.workloads.get.side_effect = _gws_error()

    result = invoke_cli(mock_apm, ["gws", "mail", "retire", "--id", WORKLOAD_UID, "--namespace", NAMESPACE, "--plan", "p-001", "--yes"])

    assert result.exit_code == 1

def test_gws_mail_list_yaml_output() -> None:
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "workload_id" in result.output

def test_gws_mail_get_namespace_without_id_exits_1() -> None:
    """gws mail get --namespace without --id should print error and exit 1."""
    result = invoke_cli(AsyncMock(), ["gws", "mail", "get", "--namespace", NAMESPACE])

    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output

def test_gws_mail_list_no_gws_domain_exits_1() -> None:
    """_resolve_saas_tenant_id should exit 1 when no GWS domain is present."""
    mock_apm = make_mock_apm()
    mock_apm.saas.list.return_value = ([], 0)

    result = invoke_cli(mock_apm, ["gws", "mail", "list"])

    assert result.exit_code == 1
    assert "No GWS domain" in result.output

def test_gws_no_export_subcommand() -> None:
    """gws mail has no export sub-app (GWS has no PST-export equivalent)."""
    result = invoke_cli(AsyncMock(), ["gws", "mail", "export", "list"])

    assert result.exit_code != 0
