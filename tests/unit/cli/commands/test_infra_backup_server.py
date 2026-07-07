"""Unit tests for apm infra server commands."""
from __future__ import annotations

import dataclasses
import json
from datetime import time
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import BackupServerRole, BackupServerType, CopyReason, ServerStatus, VersionCopyStatus
from synology_apm.sdk.exceptions import InvalidOperationError
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.tiering_plan import TieringPlan, TieringStatus
from tests.unit.cli.conftest import invoke_cli

SAMPLE_SERVER = BackupServer(
    backup_server_id="bs-001",
    namespace="ns-001",
    server_type=BackupServerType.DP,
    name="apm-server-01",
    hostname="192.0.2.1",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN1234567",
    role=BackupServerRole.PRIMARY,
    storage_total_bytes=10 * 1024 ** 4,
    storage_used_bytes=3 * 1024 ** 4,
    logical_backup_data_bytes=10 * 1024 ** 3,
    physical_backup_data_bytes=4 * 1024 ** 3,
)

OFFLINE_SERVER = BackupServer(
    backup_server_id="bs-002",
    namespace="ns-002",
    server_type=BackupServerType.DP,
    name="apm-server-dr",
    hostname="192.0.2.4",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.DISCONNECTED,
    serial="SN9876543",
    storage_total_bytes=5 * 1024 ** 4,
    storage_used_bytes=0,
    logical_backup_data_bytes=0,
    physical_backup_data_bytes=0,
)

SAMPLE_TIERING_PLAN = TieringPlan(
    plan_id="f56f8969-a831-47a6-9de0-279696dafea6",
    name="30-Day Tiering",
    description="",
    tiering_after_days=30,
    daily_check_time=time(2, 0),
    destination=None,
    server_count=1,
    tiering_status=None,
    run_schedule_by_controller_time=False,
)

NAS_SERVER = BackupServer(
    backup_server_id="bs-003",
    namespace="ns-003",
    server_type=BackupServerType.NAS,
    name="nas-server-01",
    hostname="10.0.0.10",
    model="DS1823xs+",
    system_version=None,
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="NAS001",
    storage_total_bytes=12 * 1024 ** 4,
    storage_used_bytes=4 * 1024 ** 4,
    logical_backup_data_bytes=5 * 1024 ** 3,
    physical_backup_data_bytes=2 * 1024 ** 3,
)

UPDATING_SERVER = BackupServer(
    backup_server_id="bs-upd",
    namespace="ns-upd",
    server_type=BackupServerType.DP,
    name="apm-server-updating",
    hostname="192.0.2.5",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=True,
    status=ServerStatus.HEALTHY,
    serial="SN999",
    storage_total_bytes=10 * 1024 ** 4,
    storage_used_bytes=3 * 1024 ** 4,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

NO_STORAGE_SERVER = BackupServer(
    backup_server_id="bs-004",
    namespace="ns-004",
    server_type=BackupServerType.NAS,
    name="nas-server-02",
    hostname="10.0.0.11",
    model="DS923+",
    system_version=None,
    is_updating=False,
    status=ServerStatus.WARNING,
    serial="NAS002",
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)


# ── infra server list ─────────────────────────────────────────────────────


def test_server_list_table_output(mock_apm: AsyncMock) -> None:
    """server list should display server names, status, and Storage Usage in combined format."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER, OFFLINE_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output
    assert "apm-server-dr" in result.output
    assert "Usage" in result.output
    assert "30%" in result.output             # SAMPLE_SERVER: 3 / 10 TiB = 30%


def test_server_list_json_output(mock_apm: AsyncMock) -> None:
    """server list --output json should output a JSON array."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["backup_server_id"] == "bs-001"


def test_server_list_csv_output(mock_apm: AsyncMock) -> None:
    """infra server list --output csv should output CSV with a backup_server_id field."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "backup_server_id" in lines[0]
    assert "bs-001" in result.output


def test_server_list_shows_ip_by_default(mock_apm: AsyncMock) -> None:
    """server list (without --verbose) should show the IP field by default."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "IP" in result.output
    assert "192.0.2.1" in result.output


def test_server_list_verbose_shows_description_server_id_and_namespace(mock_apm: AsyncMock) -> None:
    """server list --verbose should additionally show Description, Server ID, and Namespace columns."""
    described_server = BackupServer(
        backup_server_id="bs-001",
        namespace="ns-001",
        server_type=BackupServerType.DP,
        name="apm-server-01",
        hostname="192.0.2.1",
        model="DP320",
        system_version="APM 1.2-71845",
        is_updating=False,
        status=ServerStatus.HEALTHY,
        serial="SN1234567",
        role=BackupServerRole.PRIMARY,
        storage_total_bytes=10 * 1024 ** 4,
        storage_used_bytes=3 * 1024 ** 4,
        logical_backup_data_bytes=10 * 1024 ** 3,
        physical_backup_data_bytes=4 * 1024 ** 3,
        description="Primary lab server",
    )
    mock_apm.backup_servers.list.return_value = ([described_server], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list", "--verbose"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Description" in result.output
    assert "Primary lab server" in result.output
    assert "Server ID" in result.output
    assert "Namespace" in result.output
    assert "bs-001" in result.output
    assert "ns-001" in result.output
    assert "192.0.2.1" in result.output


def test_server_list_verbose_empty_description_shows_dash(mock_apm: AsyncMock) -> None:
    """server list --verbose should show '-' in Description column when description is empty."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list", "--verbose"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Description" in result.output


def test_server_get_shows_description(mock_apm: AsyncMock) -> None:
    """server get should display the Description field."""
    described_server = BackupServer(
        backup_server_id="bs-001",
        namespace="ns-001",
        server_type=BackupServerType.DP,
        name="apm-server-01",
        hostname="192.0.2.1",
        model="DP320",
        system_version="APM 1.2-71845",
        is_updating=False,
        status=ServerStatus.HEALTHY,
        serial="SN1234567",
        storage_total_bytes=10 * 1024 ** 4,
        storage_used_bytes=3 * 1024 ** 4,
        logical_backup_data_bytes=10 * 1024 ** 3,
        physical_backup_data_bytes=4 * 1024 ** 3,
        description="Primary lab server",
    )
    mock_apm.backup_servers.get.return_value = described_server

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-001"])

    assert result.exit_code == 0, result.output
    assert "Description:" in result.output
    assert "Primary lab server" in result.output


def test_server_get_empty_description_shows_dash(mock_apm: AsyncMock) -> None:
    """server get should display '-' when description is empty."""
    mock_apm.backup_servers.get.return_value = SAMPLE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-001"])

    assert result.exit_code == 0, result.output
    assert "Description:" in result.output


# ── infra server get ──────────────────────────────────────────────────────


def test_server_get_direct_mode_table_output(mock_apm: AsyncMock) -> None:
    """server get --id should call backup_servers.get() and display the details."""
    mock_apm.backup_servers.get.return_value = SAMPLE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-001"])

    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output
    assert "DP320" in result.output
    assert "ns-001" in result.output
    mock_apm.backup_servers.get.assert_called_once_with("bs-001")


def test_server_get_direct_mode_json_output(mock_apm: AsyncMock) -> None:
    """server get --id --output json should output a JSON object."""
    mock_apm.backup_servers.get.return_value = SAMPLE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-001", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["backup_server_id"] == "bs-001"
    assert data["serial"] == "SN1234567"
    assert data["logical_backup_data_bytes"] == 10 * 1024**3
    assert data["backup_data_reduction_ratio"] == 60.0


def test_server_get_search_mode_found(mock_apm: AsyncMock) -> None:
    """server get <name> should call get_by_name() and display the result."""
    mock_apm.backup_servers.get_by_name.return_value = SAMPLE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "apm-server-01"])

    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output
    mock_apm.backup_servers.get_by_name.assert_called_once_with("apm-server-01")


def test_server_get_search_mode_not_found(mock_apm: AsyncMock) -> None:
    """Should exit with code 1 when get_by_name() raises ResourceNotFoundError."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError

    mock_apm.backup_servers.get_by_name.side_effect = ResourceNotFoundError(
        "BackupServer 'no-such-server' not found.",
        resource_type="BackupServer",
        resource_id="no-such-server",
    )

    result = invoke_cli(mock_apm, ["infra", "server", "get", "no-such-server"])

    assert result.exit_code == 1


def test_server_get_no_args_shows_help() -> None:
    """server get (no arguments) should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["infra", "server", "get"])

    assert result.exit_code == 0
    assert "Usage" in result.output


def test_server_get_id_and_name_conflict() -> None:
    """Providing both <name> and --id should return exit 1."""
    result = invoke_cli(AsyncMock(), ["infra", "server", "get", "apm-server-01", "--id", "bs-001"])

    assert result.exit_code == 1


def test_server_get_shows_storage_usage_and_data_reduction(mock_apm: AsyncMock) -> None:
    """server get should display both Storage Usage and Data Reduction Summary sections."""
    mock_apm.backup_servers.get.return_value = SAMPLE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-001"])

    assert result.exit_code == 0, result.output
    assert "Storage Usage" in result.output
    assert "Used:" in result.output           # new label (was "Backup:")
    assert "(30%)" in result.output           # 3 / 10 TiB = 30%, shown inline after used bytes
    assert "Data Reduction Summary" in result.output
    assert "60.0%" in result.output           # 10 GiB transfer, 4 GiB used → 60% saved


def test_server_list_shows_healthy_status(mock_apm: AsyncMock) -> None:
    """server list should show 'Healthy' rather than 'Normal'."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Healthy" in result.output
    assert "Normal" not in result.output


def test_server_list_nas_shows_dash_for_system_version(mock_apm: AsyncMock) -> None:
    """NAS server list should display '-' for system_version (DP-only field)."""
    mock_apm.backup_servers.list.return_value = ([NAS_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "System Version" in result.output
    nas_line = next(line for line in result.output.splitlines() if "nas-server-01" in line)
    assert " - " in f" {nas_line.strip()} "  # System Version cell renders as '-'


def test_server_list_no_storage_shows_dash(mock_apm: AsyncMock) -> None:
    """When storage data is unavailable, server list Storage Usage column should show '-'."""
    mock_apm.backup_servers.list.return_value = ([NO_STORAGE_SERVER], 5)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Usage" in result.output
    assert "- / -" not in result.output


@pytest.mark.parametrize("status_flags,expected_filter", [
    (["--status", "healthy"], [ServerStatus.HEALTHY]),
    (["--status", "healthy", "--status", "disconnected"], [ServerStatus.HEALTHY, ServerStatus.DISCONNECTED]),
    ([], None),
])
def test_server_list_status_filter_passes_to_sdk(mock_apm: AsyncMock, status_flags: list[str], expected_filter: list[ServerStatus] | None) -> None:
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 5)
    result = invoke_cli(mock_apm, ["infra", "server", "list"] + status_flags)
    assert result.exit_code == 0, result.output
    mock_apm.backup_servers.list.assert_called_once_with(
        name_contains=None, status_filter=expected_filter, type_filter=None, limit=25, offset=0,
    )


@pytest.mark.parametrize("type_flags,expected_filter", [
    (["--type", "dp"], [BackupServerType.DP]),
    (["--type", "nas"], [BackupServerType.NAS]),
    (["--type", "dp", "--type", "nas"], [BackupServerType.DP, BackupServerType.NAS]),
])
def test_server_list_type_filter_passes_to_sdk(mock_apm: AsyncMock, type_flags: list[str], expected_filter: list[BackupServerType]) -> None:
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 5)
    result = invoke_cli(mock_apm, ["infra", "server", "list"] + type_flags)
    assert result.exit_code == 0, result.output
    mock_apm.backup_servers.list.assert_called_once_with(
        name_contains=None, status_filter=None, type_filter=expected_filter, limit=25, offset=0,
    )


def test_server_get_no_storage_shows_dash(mock_apm: AsyncMock) -> None:
    """When storage and data are unavailable, server get sections should show '-'."""
    mock_apm.backup_servers.get.return_value = NO_STORAGE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-004"])

    assert result.exit_code == 0, result.output
    assert "Storage Usage" in result.output
    assert "Data Reduction Summary" in result.output
    assert "Total:  -" in result.output
    assert "Used:   -" in result.output
    assert "Logical Backup Data:   -" in result.output
    assert "Physical Backup Data:  -" in result.output


def test_server_list_updating_shows_updating_label(mock_apm: AsyncMock) -> None:
    """When is_updating=True, the System Version column should show 'Updating...' instead of the version string."""
    mock_apm.backup_servers.list.return_value = ([UPDATING_SERVER], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Updating..." in result.output
    assert "APM 1.2-71845" not in result.output


def test_server_get_updating_shows_updating_label(mock_apm: AsyncMock) -> None:
    """When is_updating=True, server get System Version line should show 'Updating...'."""
    mock_apm.backup_servers.get.return_value = UPDATING_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-upd"])

    assert result.exit_code == 0, result.output
    assert "Updating..." in result.output
    assert "APM 1.2-71845" not in result.output


def test_server_list_not_updating_shows_version(mock_apm: AsyncMock) -> None:
    """When is_updating=False, the System Version column should show the actual version string."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "APM 1.2-71845" in result.output


# ── tiering plan display ──────────────────────────────────────────────────


_TIERING_DEST = LocationInfo(
    is_remote_storage=True,
    identifier="external-storage-uuid-001",
    name="tiering-remote",
    endpoint="https://s3.example.com:443",
    vault="tiering-remote",
)

TIERING_SERVER = BackupServer(
    backup_server_id="bs-tier",
    namespace="ns-tier",
    server_type=BackupServerType.DP,
    name="apm-server-tiering",
    hostname="192.0.2.6",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN-TIER",
    storage_total_bytes=10 * 1024 ** 4,
    storage_used_bytes=3 * 1024 ** 4,
    logical_backup_data_bytes=10 * 1024 ** 3,
    physical_backup_data_bytes=4 * 1024 ** 3,
    tiering_plan_name="tiering plan 1",
    tiering_plan_destination=_TIERING_DEST,
)


def test_server_list_shows_tiering_plan_name(mock_apm: AsyncMock) -> None:
    """server list should show the tiering plan name in the Tiering Plan column."""
    mock_apm.backup_servers.list.return_value = ([TIERING_SERVER], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Tiering Plan" in result.output
    assert "tiering plan 1" in result.output


def test_server_list_shows_dash_for_no_tiering_plan(mock_apm: AsyncMock) -> None:
    """server list should show '-' in the Tiering Plan column when no plan is assigned."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Tiering Plan" in result.output


def test_server_get_shows_tiering_plan_section(mock_apm: AsyncMock) -> None:
    """server get should display the Tiering Plan section with plan name, destination, endpoint, and vault."""
    mock_apm.backup_servers.get.return_value = TIERING_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-tier"])

    assert result.exit_code == 0, result.output
    assert "Tiering Plan" in result.output
    assert "tiering plan 1" in result.output
    assert "tiering-remote" in result.output
    assert "https://s3.example.com:443" in result.output


def test_server_get_shows_not_configured_when_no_tiering_plan(mock_apm: AsyncMock) -> None:
    """server get should display 'Not configured' when no tiering plan is assigned."""
    mock_apm.backup_servers.get.return_value = SAMPLE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-001"])

    assert result.exit_code == 0, result.output
    assert "Tiering Plan" in result.output
    assert "Not configured" in result.output


def test_server_get_json_includes_tiering_plan_fields(mock_apm: AsyncMock) -> None:
    """server get --output json should include tiering_plan_name, destination name, and namespace."""
    mock_apm.backup_servers.get.return_value = TIERING_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-tier", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["tiering_plan_name"] == "tiering plan 1"
    assert data["tiering_plan_destination"]["name"] == "tiering-remote"
    assert data["tiering_plan_destination"]["identifier"] == "external-storage-uuid-001"
    assert "Updating..." not in result.output


# ── Role column ───────────────────────────────────────────────────────────────

SECONDARY_SERVER = BackupServer(
    backup_server_id="bs-sec",
    namespace="ns-sec",
    server_type=BackupServerType.DP,
    name="apm-server-02",
    hostname="192.0.2.2",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN-SEC",
    role=BackupServerRole.SECONDARY,
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)


def test_server_list_shows_primary_badge_in_name_for_primary_server(mock_apm: AsyncMock) -> None:
    """Server with role=PRIMARY should show '(Primary)' badge next to the name."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output
    assert "(Primary)" in result.output
    assert "Role" not in result.output


def test_server_list_shows_secondary_badge_in_name_for_secondary_server(mock_apm: AsyncMock) -> None:
    """Server with role=SECONDARY should show '(Secondary)' badge next to the name."""
    mock_apm.backup_servers.list.return_value = ([SECONDARY_SERVER], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "apm-server-02" in result.output
    assert "(Secondary)" in result.output
    assert "Role" not in result.output


def test_server_list_no_badge_for_regular_server(mock_apm: AsyncMock) -> None:
    """Regular server (role=None) should show no badge and no Role column."""
    regular = BackupServer(
        backup_server_id="bs-reg",
        namespace="ns-reg",
        server_type=BackupServerType.NAS,
        name="nas-server-03",
        hostname="10.0.0.12",
        model="DS923+",
        system_version=None,
        is_updating=False,
        status=ServerStatus.HEALTHY,
        serial="NAS-REG",
        role=None,
        storage_total_bytes=None,
        storage_used_bytes=None,
        logical_backup_data_bytes=None,
        physical_backup_data_bytes=None,
    )
    mock_apm.backup_servers.list.return_value = ([regular], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "(Primary)" not in result.output
    assert "(Secondary)" not in result.output
    assert "Role" not in result.output


# ── --page-all ───────────────────────────────────────────────────────────────


def test_server_list_page_all_combines_pages(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """infra server list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    second_server = dataclasses.replace(OFFLINE_SERVER, backup_server_id="bs-003", name="apm-server-02")
    mock_apm.backup_servers.list.side_effect = [
        ([SAMPLE_SERVER], 2),
        ([second_server], 2),
    ]

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "list", "--limit", "1", "--page-all"],
        env={"COLUMNS": "300"},
    )

    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output
    assert "apm-server-02" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.backup_servers.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.backup_servers.list.call_args_list[1].kwargs["offset"] == 1


# ── tiering status display ────────────────────────────────────────────────


def test_server_list_shows_tiering_status_in_progress(mock_apm: AsyncMock) -> None:
    """server list should show Tiering Status column with IN_PROGRESS for a server with active tiering."""
    server = dataclasses.replace(
        TIERING_SERVER,
        tiering_status=TieringStatus(
            status=VersionCopyStatus.IN_PROGRESS, reason=None,
            pending_version_count=3, remaining_bytes=1048576,
        ),
    )
    mock_apm.backup_servers.list.return_value = ([server], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Tiering Status" in result.output
    assert "Copying" in result.output


def test_server_list_shows_dash_when_no_tiering_status(mock_apm: AsyncMock) -> None:
    """server list should show '-' in Tiering Status column when tiering_status is None."""
    mock_apm.backup_servers.list.return_value = ([SAMPLE_SERVER], 1)

    result = invoke_cli(mock_apm, ["infra", "server", "list"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Tiering Status" in result.output


def test_server_get_shows_tiering_status_with_pending_and_reason(mock_apm: AsyncMock) -> None:
    """server get should show tiering status, pending count, and reason as a standalone section after Tiering Plan."""
    server = dataclasses.replace(
        TIERING_SERVER,
        tiering_status=TieringStatus(
            status=VersionCopyStatus.RETRY,
            reason=CopyReason.DESTINATION_DISCONNECTED,
            pending_version_count=5,
        ),
    )
    mock_apm.backup_servers.get.return_value = server

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-tier"])

    assert result.exit_code == 0, result.output
    assert "Tiering Status" in result.output
    assert "5 version(s) pending" in result.output
    assert "disconnected" in result.output.lower()


def test_server_get_no_tiering_status_block_when_not_configured(mock_apm: AsyncMock) -> None:
    """server get should not show Tiering Status when no tiering plan is configured."""
    mock_apm.backup_servers.get.return_value = SAMPLE_SERVER

    result = invoke_cli(mock_apm, ["infra", "server", "get", "--id", "bs-001"])

    assert result.exit_code == 0, result.output
    assert "Tiering Status" not in result.output


# ── infra server change-plan ──────────────────────────────────────────────


def test_server_change_plan_search_mode_apply(mock_apm: AsyncMock) -> None:
    """change-plan resolves server by name and plan by name, then calls change_tiering_plan."""
    mock_apm.backup_servers.get_by_name.return_value = SAMPLE_SERVER
    mock_apm.tiering_plans.get_by_name.return_value = SAMPLE_TIERING_PLAN
    mock_apm.backup_servers.change_tiering_plan.return_value = None

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "change-plan", "apm-server-01", "--plan", "30-Day Tiering", "--yes"],
    )

    assert result.exit_code == 0, result.output
    mock_apm.backup_servers.get_by_name.assert_called_once_with("apm-server-01")
    mock_apm.tiering_plans.get_by_name.assert_called_once_with("30-Day Tiering")
    mock_apm.backup_servers.change_tiering_plan.assert_called_once_with(SAMPLE_SERVER, SAMPLE_TIERING_PLAN)
    assert "Tiering plan updated" in result.output


def test_server_change_plan_search_mode_remove(mock_apm: AsyncMock) -> None:
    """change-plan --remove calls change_tiering_plan with plan=None."""
    mock_apm.backup_servers.get_by_name.return_value = SAMPLE_SERVER
    mock_apm.backup_servers.change_tiering_plan.return_value = None

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "change-plan", "apm-server-01", "--remove", "--yes"],
    )

    assert result.exit_code == 0, result.output
    mock_apm.backup_servers.change_tiering_plan.assert_called_once_with(SAMPLE_SERVER, None)
    assert "Tiering plan updated" in result.output


def test_server_change_plan_direct_mode_apply(mock_apm: AsyncMock) -> None:
    """change-plan --id resolves server via get(), then applies the plan."""
    mock_apm.backup_servers.get.return_value = SAMPLE_SERVER
    mock_apm.tiering_plans.get_by_name.return_value = SAMPLE_TIERING_PLAN
    mock_apm.backup_servers.change_tiering_plan.return_value = None

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "change-plan", "--id", "bs-001", "--plan", "30-Day Tiering", "--yes"],
    )

    assert result.exit_code == 0, result.output
    mock_apm.backup_servers.get.assert_called_once_with("bs-001")
    mock_apm.backup_servers.change_tiering_plan.assert_called_once_with(SAMPLE_SERVER, SAMPLE_TIERING_PLAN)


def test_server_change_plan_resolves_plan_by_uuid(mock_apm: AsyncMock) -> None:
    """When --plan is UUID-shaped, uses tiering_plans.get() not get_by_name()."""
    mock_apm.backup_servers.get_by_name.return_value = SAMPLE_SERVER
    mock_apm.tiering_plans.get.return_value = SAMPLE_TIERING_PLAN
    mock_apm.backup_servers.change_tiering_plan.return_value = None

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "change-plan", "apm-server-01",
         "--plan", "f56f8969-a831-47a6-9de0-279696dafea6", "--yes"],
    )

    assert result.exit_code == 0, result.output
    mock_apm.tiering_plans.get.assert_called_once_with("f56f8969-a831-47a6-9de0-279696dafea6")
    mock_apm.tiering_plans.get_by_name.assert_not_called()


def test_server_change_plan_abort_on_no(mock_apm: AsyncMock) -> None:
    """change-plan exits with code 1 when user declines the confirmation prompt."""
    mock_apm.backup_servers.get_by_name.return_value = SAMPLE_SERVER
    mock_apm.tiering_plans.get_by_name.return_value = SAMPLE_TIERING_PLAN

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "change-plan", "apm-server-01", "--plan", "30-Day Tiering"],
        input="n\n",
    )

    assert result.exit_code != 0
    mock_apm.backup_servers.change_tiering_plan.assert_not_called()


def test_server_change_plan_remove_shows_warning(mock_apm: AsyncMock) -> None:
    """--remove prints the immutability/lock-duration warning before the confirmation prompt."""
    mock_apm.backup_servers.get_by_name.return_value = SAMPLE_SERVER
    mock_apm.backup_servers.change_tiering_plan.return_value = None

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "change-plan", "apm-server-01", "--remove", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "Removing the tiering plan" in result.output


def test_server_change_plan_error_both_plan_and_remove() -> None:
    """Providing both --plan and --remove exits with code 1."""
    result = invoke_cli(
        AsyncMock(),
        ["infra", "server", "change-plan", "apm-server-01", "--plan", "foo", "--remove"],
    )

    assert result.exit_code == 1
    assert "--plan and --remove are mutually exclusive" in result.output


def test_server_change_plan_error_neither_plan_nor_remove() -> None:
    """Providing neither --plan nor --remove shows help."""
    result = invoke_cli(AsyncMock(), ["infra", "server", "change-plan", "apm-server-01"])

    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_server_change_plan_nas_server_propagates_invalid_operation(mock_apm: AsyncMock) -> None:
    """InvalidOperationError from the SDK (NAS server) is handled by apm_error_handler."""
    mock_apm.backup_servers.get_by_name.return_value = NAS_SERVER
    mock_apm.tiering_plans.get_by_name.return_value = SAMPLE_TIERING_PLAN
    mock_apm.backup_servers.change_tiering_plan.side_effect = InvalidOperationError(
        "Tiering plans are only supported for DP-type backup servers ('nas-server-01' is not DP-type).",
        resource_type="BackupServer",
        resource_id="bs-003",
    )

    result = invoke_cli(
        mock_apm,
        ["infra", "server", "change-plan", "nas-server-01", "--plan", "30-Day Tiering", "--yes"],
    )

    assert result.exit_code == 1
