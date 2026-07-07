"""Unit tests for apm m365 error paths, YAML output, and cross-cutting validation."""
from __future__ import annotations

from unittest.mock import AsyncMock

from synology_apm.sdk.exceptions import ResourceNotFoundError
from tests.unit.cli.commands._m365_fixtures import (
    NAMESPACE,
    SAMPLE_TENANT,
    TENANT_ID,
    WORKLOAD_ID,
    WORKLOAD_UID,
    make_mock_apm,
)
from tests.unit.cli.conftest import invoke_cli


def _m365_error() -> ResourceNotFoundError:
    return ResourceNotFoundError("not found", resource_type="M365Workload", resource_id="x")


def test_m365_exchange_list_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.saas.list.return_value = ([SAMPLE_TENANT], 5)
    mock_apm.saas.get_m365_tenant.return_value = SAMPLE_TENANT
    mock_apm.m365.workloads.list.side_effect = _m365_error()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID])

    assert result.exit_code == 1

def test_m365_exchange_get_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.m365.workloads.get.side_effect = _m365_error()

    result = invoke_cli(mock_apm, ["m365", "exchange", "get", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 1

def test_m365_exchange_backup_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.m365.workloads.get.side_effect = _m365_error()

    result = invoke_cli(mock_apm, ["m365", "exchange", "backup", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 1

def test_m365_exchange_retire_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.m365.workloads.get.side_effect = _m365_error()

    result = invoke_cli(mock_apm, ["m365", "exchange", "retire", "--id", WORKLOAD_UID, "--namespace", NAMESPACE, "--plan", "p-001", "--yes"])

    assert result.exit_code == 1

def test_m365_exchange_list_yaml_output() -> None:
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "workload_id" in result.output

def test_m365_exchange_get_namespace_without_id_exits_1() -> None:
    """m365 exchange get --namespace without --id should print error and exit 1."""
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "get", "--namespace", NAMESPACE])

    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output

def test_m365_exchange_list_no_m365_tenant_exits_1() -> None:
    """_resolve_tenant should exit 1 when no M365 tenant is present."""
    mock_apm = make_mock_apm()
    mock_apm.saas.list.return_value = ([], 0)

    result = invoke_cli(mock_apm, ["m365", "exchange", "list"])

    assert result.exit_code == 1
    assert "No M365 tenant" in result.output

def test_m365_export_list_workload_id_without_namespace_exits_1() -> None:
    """export list --workload-id without --namespace should exit 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "export", "list",
        "--workload-id", WORKLOAD_ID,
    ])
    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output

def test_m365_export_list_name_and_workload_id_conflict_exits_1() -> None:
    """export list <name> --workload-id X should exit 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "export", "list",
        "alice@contoso.com", "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
    ])
    assert result.exit_code == 1
    assert "cannot be used" in result.output

def test_m365_export_list_namespace_without_workload_id_exits_1() -> None:
    """export list --namespace without --workload-id should exit 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "export", "list",
        "--namespace", NAMESPACE,
    ])
    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output

def test_m365_export_list_no_args_shows_help() -> None:
    """export list with no arguments should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "export", "list"])
    assert result.exit_code == 0
    assert "Usage" in result.output or "usage" in result.output.lower()
