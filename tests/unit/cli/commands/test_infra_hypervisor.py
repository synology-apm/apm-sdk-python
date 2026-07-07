"""Unit tests for apm infra hypervisor commands."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import HypervisorType
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.hypervisor import Hypervisor
from tests.unit.cli.conftest import invoke_cli

SAMPLE_HYPERVISOR = Hypervisor(
    hypervisor_id="978eabd4-e332-459f-a8e0-35a0aa312118",
    hostname="esxi1.example.com",
    address="192.0.2.40",
    host_type=HypervisorType.VSPHERE_ESXI,
    account="root",
    description="",
    port=443,
    version="6.5",
)

VCENTER_HYPERVISOR = Hypervisor(
    hypervisor_id="vc-id-001",
    hostname="vc.lab.local",
    address="192.0.2.41",
    host_type=HypervisorType.VSPHERE_VCENTER,
    account="administrator@vsphere.local",
    description="Lab vCenter",
    port=443,
    version="7.0.3",
)


# ── hypervisor list ────────────────────────────────────────────────────────


def test_hypervisor_list_table(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.list.return_value = ([SAMPLE_HYPERVISOR], 1)
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "list"])
    assert result.exit_code == 0
    assert "esxi1" in result.output
    assert "192.0.2.40" in result.output
    assert "VMware vSphere (ESXi)" in result.output
    assert "root" in result.output


def test_hypervisor_list_verbose_shows_id(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.list.return_value = ([SAMPLE_HYPERVISOR], 1)
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "list", "--verbose"])
    assert result.exit_code == 0
    assert "978eabd4-e332-459f-a8e0-35a0aa312118" in result.output


def test_hypervisor_list_json(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.list.return_value = ([SAMPLE_HYPERVISOR], 1)
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "list", "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["hostname"] == "esxi1.example.com"
    assert data[0]["host_type"] == "vsphere_esxi"


def test_hypervisor_list_csv(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.list.return_value = ([SAMPLE_HYPERVISOR], 1)
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "list", "-o", "csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert "hypervisor_id" in lines[0]
    assert "esxi1.example.com" in result.output


def test_hypervisor_list_empty(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.list.return_value = ([], 0)
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "list"])
    assert result.exit_code == 0
    assert "Showing 0 of 0" in result.output


# ── hypervisor get ─────────────────────────────────────────────────────────


def test_hypervisor_get_by_name(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.get_by_name.return_value = SAMPLE_HYPERVISOR
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "get", "esxi1.example.com"])
    assert result.exit_code == 0
    assert "esxi1.example.com" in result.output
    assert "VMware vSphere (ESXi)" in result.output
    assert "192.0.2.40" in result.output
    assert "root" in result.output
    assert "6.5" in result.output


def test_hypervisor_get_by_id(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.get.return_value = SAMPLE_HYPERVISOR
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "get", "--id", "978eabd4-e332-459f-a8e0-35a0aa312118"])
    assert result.exit_code == 0
    assert "esxi1.example.com" in result.output
    assert "978eabd4-e332-459f-a8e0-35a0aa312118" in result.output


def test_hypervisor_get_no_args_shows_help() -> None:
    result = invoke_cli(AsyncMock(), ["infra", "hypervisor", "get"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_hypervisor_get_name_and_id_conflict(mock_apm: AsyncMock) -> None:
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "get", "somename", "--id", "some-id"])
    assert result.exit_code == 1
    assert "cannot be used with" in result.output


def test_hypervisor_get_json(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.get_by_name.return_value = SAMPLE_HYPERVISOR
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "get", "esxi1.example.com", "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["hypervisor_id"] == "978eabd4-e332-459f-a8e0-35a0aa312118"


def test_hypervisor_get_not_found(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.get_by_name.side_effect = ResourceNotFoundError(
        "not found", resource_type="Hypervisor", resource_id="x"
    )
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "get", "no-such-hypervisor"])
    assert result.exit_code == 1


def test_hypervisor_get_description_dash_when_empty(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.get_by_name.return_value = SAMPLE_HYPERVISOR
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "get", "esxi1.example.com"])
    assert result.exit_code == 0
    assert "Description: -" in result.output


def test_hypervisor_get_description_shown(mock_apm: AsyncMock) -> None:
    mock_apm.hypervisors.get_by_name.return_value = VCENTER_HYPERVISOR
    result = invoke_cli(mock_apm, ["infra", "hypervisor", "get", "vc.lab.local"])
    assert result.exit_code == 0
    assert "Lab vCenter" in result.output
