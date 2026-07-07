"""Unit tests for apm infra info command."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import BackupServerRole, BackupServerType, ServerStatus, WorkloadStatType
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.system import (
    SiteInfo,
    SiteStorageStats,
    WorkloadTypeStat,
    WorkloadUsageSummary,
)
from tests.unit.cli.conftest import invoke_cli

SAMPLE_MGMT = BackupServer(
    backup_server_id="bs-mgmt-001",
    namespace="ns-mgmt",
    server_type=BackupServerType.DP,
    name="apm-server-01",
    hostname="192.0.2.1",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN1234567",
    role=BackupServerRole.PRIMARY,
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

SAMPLE_SECONDARY = BackupServer(
    backup_server_id="bs-sec-001",
    namespace="ns-sec",
    server_type=BackupServerType.DP,
    name="apm-server-02",
    hostname="192.0.2.2",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN9999999",
    role=BackupServerRole.SECONDARY,
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

SAMPLE_STORAGE = SiteStorageStats(
    logical_backup_data_bytes=10 * 1024 ** 3,   # 10 GB original data
    physical_backup_data_bytes=4 * 1024 ** 3,   # 4 GB actual storage
)

SAMPLE_WORKLOAD_USAGE = WorkloadUsageSummary(by_type=(
    WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_PC, total_count=1,  protected_data_bytes=199_096_201_216),
    WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_PS, total_count=0,  protected_data_bytes=0),
    WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_VM, total_count=6,  protected_data_bytes=95_284_154_368),
    WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_FS, total_count=1,  protected_data_bytes=622_608_384),
    WorkloadTypeStat(workload_type=WorkloadStatType.M365,       total_count=37, protected_data_bytes=116_600_709_120),
    WorkloadTypeStat(workload_type=WorkloadStatType.GWS,        total_count=0,  protected_data_bytes=0),
))

SAMPLE_SITE = SiteInfo(
    site_uuid="550e8400-e29b-41d4-a716-446655440000",
    external_address="apm.corp.com",
    port="443",
    primary_management_server=SAMPLE_MGMT,
    secondary_management_server=None,
    site_storage=SAMPLE_STORAGE,
    workload_usage=SAMPLE_WORKLOAD_USAGE,
)


def _make_mock_apm(site: SiteInfo = SAMPLE_SITE) -> AsyncMock:
    mock_apm = AsyncMock()
    mock_apm.get_site_info.return_value = site
    return mock_apm


def test_system_info_table_output() -> None:
    """infra info should display name, model, and system version of the management server."""
    result = invoke_cli(_make_mock_apm(), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output
    assert "DP320" in result.output
    assert "APM 1.2-71845" in result.output
    assert "SN1234567" in result.output


def test_system_info_shows_sections_and_storage() -> None:
    """infra info should display Site Information, Primary Management Server, and Data Reduction Summary."""
    result = invoke_cli(_make_mock_apm(), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "Site Information" in result.output
    assert "550e8400-e29b-41d4-a716-446655440000" in result.output
    # port=443 is omitted → URL shown without port
    assert "Management Center" in result.output
    assert "https://apm.corp.com" in result.output
    assert "Recovery Portal" in result.output
    assert "https://apm.corp.com/portal" in result.output
    assert "Primary Management Server" in result.output
    assert "Secondary Management Server" in result.output
    assert "Status" in result.output
    assert "Healthy" in result.output
    assert "Data Reduction Summary" in result.output
    assert "Total Logical Backup Data" in result.output
    assert "Total Physical Backup Data" in result.output
    assert "Data Reduced" in result.output
    # 10 GB original, 4 GB used → 6 GB reduced = 60.0%
    assert "60.0%" in result.output


def test_system_info_shows_url_with_non_standard_port() -> None:
    """When port is non-443, the URL should include the port number."""
    site = SiteInfo(
        site_uuid="uuid-port",
        external_address="apm.corp.com",
        port="10443",
        primary_management_server=SAMPLE_MGMT,
        secondary_management_server=None,
        site_storage=SAMPLE_STORAGE,
        workload_usage=SAMPLE_WORKLOAD_USAGE,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "https://apm.corp.com:10443" in result.output
    assert "https://apm.corp.com:10443/portal" in result.output


def test_system_info_shows_secondary_management_server() -> None:
    """When secondary_management_server is set, its details should appear in the secondary section."""
    site = SiteInfo(
        site_uuid="uuid-sec",
        external_address="apm.corp.com",
        port="",
        primary_management_server=SAMPLE_MGMT,
        secondary_management_server=SAMPLE_SECONDARY,
        site_storage=SAMPLE_STORAGE,
        workload_usage=SAMPLE_WORKLOAD_USAGE,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "Secondary Management Server" in result.output
    assert "apm-server-02" in result.output
    assert "SN9999999" in result.output
    assert "Not configured" not in result.output


def test_system_info_secondary_section_always_shown_when_none() -> None:
    """Secondary Management Server section is always shown; shows 'Not configured' when not set."""
    result = invoke_cli(_make_mock_apm(), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "Secondary Management Server" in result.output
    assert "Not configured" in result.output


def test_system_info_management_server_none_shows_not_available() -> None:
    """When primary_management_server is None, Primary Management Server section should show 'Not available'."""
    site = SiteInfo(
        site_uuid="uuid-no-mgmt",
        external_address="",
        port="",
        primary_management_server=None,
        secondary_management_server=None,
        site_storage=SAMPLE_STORAGE,
        workload_usage=SAMPLE_WORKLOAD_USAGE,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "Not available" in result.output


def test_system_info_json_output() -> None:
    """infra info --output json should output a JSON object containing site_uuid, primary_management_server and site_storage."""
    result = invoke_cli(_make_mock_apm(), ["infra", "info", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["site_uuid"] == "550e8400-e29b-41d4-a716-446655440000"
    assert "primary_management_server" in data
    assert data["primary_management_server"]["name"] == "apm-server-01"
    assert "secondary_management_server" in data
    assert "site_storage" in data
    assert "logical_backup_data_bytes" in data["site_storage"]


def test_system_info_yaml_output() -> None:
    """infra info --output yaml should output YAML format."""
    result = invoke_cli(_make_mock_apm(), ["infra", "info", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "name: apm-server-01" in result.output
    assert "site_uuid" in result.output
    assert "site_storage" in result.output


def test_system_info_zero_transfer_no_div_zero() -> None:
    """When logical_backup_data_bytes=0, reduction ratio should show 0.0% without dividing by zero."""
    site = SiteInfo(
        site_uuid="uuid-1",
        external_address="apm.corp.com",
        port="443",
        primary_management_server=SAMPLE_MGMT,
        secondary_management_server=None,
        site_storage=SiteStorageStats(logical_backup_data_bytes=0, physical_backup_data_bytes=0),
        workload_usage=SAMPLE_WORKLOAD_USAGE,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "0.0%" in result.output


def test_system_info_external_address_no_port() -> None:
    """When external_address is set but port is empty, URL should have no port suffix."""
    site = SiteInfo(
        site_uuid="uuid-1",
        external_address="apm.corp.com",
        port="",
        primary_management_server=SAMPLE_MGMT,
        secondary_management_server=None,
        site_storage=SAMPLE_STORAGE,
        workload_usage=SAMPLE_WORKLOAD_USAGE,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "https://apm.corp.com" in result.output
    assert "https://apm.corp.com:" not in result.output


def test_system_info_empty_address_shows_dash() -> None:
    """When external_address is empty, Management Center and Recovery Portal should show '-'."""
    site = SiteInfo(
        site_uuid="uuid-no-addr",
        external_address="",
        port="",
        primary_management_server=SAMPLE_MGMT,
        secondary_management_server=None,
        site_storage=SAMPLE_STORAGE,
        workload_usage=SAMPLE_WORKLOAD_USAGE,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "Management Center:  -" in result.output
    assert "Recovery Portal:    -" in result.output


def test_system_info_shows_workload_usage_summary() -> None:
    """infra info should display Workload Usage Summary section with type labels, counts, and sizes."""
    result = invoke_cli(_make_mock_apm(), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "Workload Usage Summary" in result.output
    assert "PC" in result.output
    assert "M365" in result.output
    assert "Total" in result.output
    # M365: 37 workloads, 116_600_709_120 bytes ≈ 108.6 GB
    assert "37" in result.output


def test_system_info_workload_zero_data_shows_dash() -> None:
    """Data Size should show '-' when protected_data_bytes is 0."""
    usage = WorkloadUsageSummary(by_type=(
        WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_PC, total_count=0, protected_data_bytes=0),
        WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_PS, total_count=0, protected_data_bytes=0),
        WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_VM, total_count=0, protected_data_bytes=0),
        WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_FS, total_count=0, protected_data_bytes=0),
        WorkloadTypeStat(workload_type=WorkloadStatType.M365,       total_count=0, protected_data_bytes=0),
        WorkloadTypeStat(workload_type=WorkloadStatType.GWS,        total_count=0, protected_data_bytes=0),
    ))
    site = SiteInfo(
        site_uuid="uuid-1",
        external_address="",
        port="",
        primary_management_server=SAMPLE_MGMT,
        secondary_management_server=None,
        site_storage=SAMPLE_STORAGE,
        workload_usage=usage,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "Workload Usage Summary" in result.output
    pc_line = next(line for line in result.output.splitlines() if line.startswith("PC"))
    assert pc_line.rstrip().endswith("-")  # zero bytes renders as '-' in the Data Size column


def test_system_info_json_includes_workload_usage() -> None:
    """infra info --output json should include workload_usage with by_type list."""
    result = invoke_cli(_make_mock_apm(), ["infra", "info", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    usage = data["workload_usage"]
    by_type = usage["by_type"]
    assert len(by_type) == 6
    # Per-field serialization contracts live in test_serializers.py; spot-check dispatch only.
    assert by_type[4]["workload_type"] == "m365"
    assert by_type[4]["total_count"] == 37


def test_system_info_skips_absent_workload_types() -> None:
    """Workload types missing from the usage summary are omitted from the table."""
    usage = WorkloadUsageSummary(by_type=(
        WorkloadTypeStat(workload_type=WorkloadStatType.M365, total_count=4, protected_data_bytes=1024**3),
    ))
    site = SiteInfo(
        site_uuid="uuid-1",
        external_address="",
        port="",
        primary_management_server=SAMPLE_MGMT,
        secondary_management_server=None,
        site_storage=SAMPLE_STORAGE,
        workload_usage=usage,
    )
    result = invoke_cli(_make_mock_apm(site=site), ["infra", "info"])

    assert result.exit_code == 0, result.output
    assert "M365" in result.output
    assert "GWS" not in result.output
