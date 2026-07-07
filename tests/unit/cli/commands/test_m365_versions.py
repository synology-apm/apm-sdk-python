"""Unit tests for apm m365 exchange version commands: list/get/lock/unlock."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    LogLevel,
    M365WorkloadType,
    VersionStatus,
    WorkloadCategory,
)
from synology_apm.sdk.models.activity import BackupActivity
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.version import WorkloadVersion
from tests.unit.cli.commands._m365_fixtures import (
    NAMESPACE,
    SAMPLE_WL,
    TENANT_ID,
    WORKLOAD_ID,
    WORKLOAD_UID,
    make_mock_apm,
)
from tests.unit.cli.conftest import invoke_cli

SAMPLE_VERSION = WorkloadVersion(
    version_id="ver-m365-001",
    workload_id=WORKLOAD_ID,
    namespace=NAMESPACE,
    created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    status=VersionStatus.SUCCESS,
    execution_id="M365_EX_1",
    locked=False,
    changed_size_bytes=1024 * 1024 * 50,
)

SAMPLE_ACT = BackupActivity(
    activity_id="act-m365-001",
    execution_id="M365_EX_1",
    namespace=NAMESPACE,
    category=WorkloadCategory.M365,
    workload_type=ActivityWorkloadType.M365,
    workload_id=WORKLOAD_UID,
    workload_namespace="",
    workload_name="alice@contoso.com",
    plan_name="M365 Daily",
    status=BackupActivityStatus.SUCCESS,
    started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    finished_at=datetime(2026, 4, 21, 9, 10, tzinfo=UTC),
    duration_seconds=600,
    data_transferred_bytes=1024 * 1024 * 50,
    progress=100,
)


def test_m365_exchange_version_list_direct_mode_table() -> None:
    """m365 exchange version list --id --namespace should list versions (table)."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.list_versions.return_value = ([SAMPLE_VERSION], 1)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "list",
        "--id", WORKLOAD_ID, "--namespace", NAMESPACE,
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "ver-m365-001" in result.output
    assert "Showing 1 of 1" in result.output

def test_m365_exchange_version_list_passes_offset_to_sdk() -> None:
    """version list --offset N should pass offset=N to list_versions and show footer with correct range."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.list_versions.return_value = ([SAMPLE_VERSION], 26)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "list",
        "--id", WORKLOAD_ID, "--namespace", NAMESPACE, "--offset", "25",
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.m365.workloads.list_versions.call_args.kwargs
    assert call_kwargs["offset"] == 25
    assert "26" in result.output           # row number starts at offset+1
    assert "26–26 of 26" in result.output  # footer shows correct range

def test_m365_exchange_version_list_json_output() -> None:
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.list_versions.return_value = ([SAMPLE_VERSION], 1)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "list",
        "--id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--output", "json",
    ])

    assert result.exit_code == 0, result.output
    import json as _json
    data = _json.loads(result.stdout)
    assert isinstance(data, list)
    assert data[0]["version_id"] == "ver-m365-001"

def test_m365_exchange_version_list_csv_output() -> None:
    """m365 exchange version list --output csv should output flat CSV with location_count, not nested locations list."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.list_versions.return_value = ([SAMPLE_VERSION], 1)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "list",
        "--id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--output", "csv",
    ])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    headers = lines[0].split(",")
    assert "version_id" in headers
    assert "location_count" in headers          # flattened field
    assert "locations" not in headers           # nested field name must not appear

def test_m365_exchange_version_list_search_mode() -> None:
    """version list search mode resolves tenant then workload."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.list_versions.return_value = ([], 0)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "list",
        "alice@contoso.com", "-t", TENANT_ID,
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get_by_name.assert_called_once()
    mock_apm.m365.workloads.list_versions.assert_called_once()

def test_m365_exchange_version_list_no_args_shows_help() -> None:
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "version", "list"])

    assert result.exit_code == 0
    assert "Usage" in result.output

def test_m365_exchange_version_get_direct_mode() -> None:
    """version get --workload-id --namespace --id (direct mode)."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "alice@contoso.com" in result.output
    assert "(Using version:" not in result.output
    mock_apm.m365.workloads.get.assert_called_once_with(
        WORKLOAD_ID, NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE
    )
    mock_apm.m365.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-m365-001")

def test_m365_exchange_version_get_search_mode() -> None:
    """version get search mode resolves workload then finds version."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "alice@contoso.com", "-t", TENANT_ID, "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "(Using version:" not in result.output
    mock_apm.m365.workloads.get_by_name.assert_called_once()

def test_m365_exchange_version_get_json_output() -> None:
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "ver-m365-001", "--output", "json",
    ])

    assert result.exit_code == 0, result.output
    import json as _json
    data = _json.loads(result.stdout)
    assert data["version_id"] == "ver-m365-001"
    assert data["activity"]["activity_id"] == "act-m365-001"

def test_m365_exchange_version_get_not_found_exits_1() -> None:
    """version get with non-existent version_id should exit 1."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.side_effect = ResourceNotFoundError(
        "not found", resource_type="WorkloadVersion", resource_id="nonexistent-ver"
    )

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "nonexistent-ver",
    ])

    assert result.exit_code == 1

def test_m365_exchange_version_get_no_args_shows_help() -> None:
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "version", "get"])

    assert result.exit_code == 0
    assert "Usage" in result.output

def test_m365_exchange_version_get_latest_search_mode() -> None:
    """version get without --id (search mode) should call get_latest_version()."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_latest_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "alice@contoso.com", "-t", TENANT_ID,
    ])

    assert result.exit_code == 0, result.output
    assert "(Using version: ver-m365-001, created at" in result.output
    mock_apm.m365.workloads.get_latest_version.assert_called_once()
    mock_apm.m365.workloads.list_versions.assert_not_called()

def test_m365_exchange_version_get_latest_direct_mode() -> None:
    """version get without --id (direct mode) should call get_latest_version()."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_latest_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    assert "(Using version: ver-m365-001, created at" in result.output
    mock_apm.m365.workloads.get_latest_version.assert_called_once_with(SAMPLE_WL)
    mock_apm.m365.workloads.list_versions.assert_not_called()

def test_m365_version_get_shows_processed_items() -> None:
    """version get detail should display the Processed items row (M365 activity)."""
    act_with_counts = BackupActivity(
        activity_id="act-m365-001",
        execution_id="M365_EX_1",
        namespace=NAMESPACE,
        category=WorkloadCategory.M365,
        workload_type=ActivityWorkloadType.M365,
        workload_id=WORKLOAD_UID,
        workload_namespace="",
        workload_name="alice@contoso.com",
        plan_name="M365 Daily",
        status=BackupActivityStatus.SUCCESS,
        started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 21, 9, 10, tzinfo=UTC),
        duration_seconds=600,
        data_transferred_bytes=None,
        progress=0,
        processed_success_count=5,
        processed_warning_count=2,
        processed_error_count=0,
    )
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = act_with_counts

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "Processed items" in result.output
    assert "5 succeeded" in result.output
    assert "2 warning" in result.output
    assert "0 error" in result.output

def test_m365_exchange_version_lock_direct_mode() -> None:
    """version lock --workload-id --namespace --id should fetch workload, look up the version, then call lock_version."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "lock",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "locked" in result.output
    mock_apm.m365.workloads.get.assert_called_once_with(
        WORKLOAD_ID, NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE
    )
    mock_apm.m365.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-m365-001")
    mock_apm.m365.workloads.lock_version.assert_called_once_with(SAMPLE_VERSION)

def test_m365_exchange_version_lock_search_mode() -> None:
    """version lock in search mode should first find the workload, look up the version, then call lock_version."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "lock",
        "alice@contoso.com", "-t", TENANT_ID, "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get_by_name.assert_called_once()
    mock_apm.m365.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-m365-001")
    mock_apm.m365.workloads.lock_version.assert_called_once_with(SAMPLE_VERSION)

def test_m365_exchange_version_lock_no_id_shows_help() -> None:
    """version lock without --id should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "version", "lock", "alice@contoso.com", "-t", TENANT_ID])

    assert result.exit_code == 0
    assert "Usage" in result.output

def test_m365_exchange_version_unlock_direct_mode() -> None:
    """version unlock in direct mode should fetch workload, look up the version, then call unlock_version."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "unlock",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "unlocked" in result.output
    mock_apm.m365.workloads.get.assert_called_once_with(
        WORKLOAD_ID, NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE
    )
    mock_apm.m365.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-m365-001")
    mock_apm.m365.workloads.unlock_version.assert_called_once_with(SAMPLE_VERSION)

def test_m365_exchange_version_get_name_and_workload_id_conflict_exits_1() -> None:
    """version get NAME --workload-id X should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "version", "get",
        "alice@contoso.com", "--workload-id", WORKLOAD_ID,
    ])

    assert result.exit_code == 1
    assert "cannot be used" in result.output

def test_m365_exchange_version_get_workload_id_without_namespace_exits_1() -> None:
    """version get --workload-id X (no --namespace) should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID,
    ])

    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output

def test_m365_exchange_version_get_shows_log_entries() -> None:
    """version get renders a log entries table when the activity has log_entries."""
    from synology_apm.sdk.models.activity import ActivityLogEntry

    act_with_logs = BackupActivity(
        activity_id="act-m365-001",
        execution_id="M365_EX_1",
        namespace=NAMESPACE,
        category=WorkloadCategory.M365,
        workload_type=ActivityWorkloadType.M365,
        workload_id=WORKLOAD_UID,
        workload_namespace="",
        workload_name="alice@contoso.com",
        plan_name="M365 Daily",
        status=BackupActivityStatus.SUCCESS,
        started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 21, 9, 10, tzinfo=UTC),
        duration_seconds=600,
        data_transferred_bytes=None,
        progress=100,
        log_entries=(
            ActivityLogEntry(
                timestamp=datetime(2026, 4, 21, 9, 1, tzinfo=UTC),
                level=LogLevel.WARNING,
                message="Mailbox quota approaching limit",
            ),
        ),
    )
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = act_with_logs

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "Logs" in result.output
    assert "Mailbox quota approaching limit" in result.output

def test_m365_exchange_version_lock_name_and_workload_id_conflict_exits_1() -> None:
    """version lock NAME --workload-id X should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "version", "lock",
        "alice@contoso.com", "--workload-id", WORKLOAD_ID, "--id", "ver-m365-001",
    ])

    assert result.exit_code == 1
    assert "cannot be used" in result.output

def test_m365_exchange_version_lock_workload_id_without_namespace_exits_1() -> None:
    """version lock --workload-id X --id V (no --namespace) should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "version", "lock",
        "--workload-id", WORKLOAD_ID, "--id", "ver-m365-001",
    ])

    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output


def test_m365_exchange_version_get_lists_all_locations() -> None:
    """version get renders every location, one per line."""
    import dataclasses

    from synology_apm.sdk.models.version import VersionLocation

    two_location_version = dataclasses.replace(
        SAMPLE_VERSION,
        locations=[
            VersionLocation(
                namespace=NAMESPACE,
                location_info=LocationInfo(
                    is_remote_storage=False, identifier="ns-server-001",
                    name="apm-server-01", endpoint="192.0.2.1", vault=None,
                ),
                location_id="loc-1",
            ),
            VersionLocation(
                namespace="ns-remote-001",
                location_info=LocationInfo(
                    is_remote_storage=True, identifier="storage-001",
                    name="DSM-Storage", endpoint="192.0.2.20:8444", vault="MyVault",
                ),
                location_id="loc-2",
            ),
        ],
    )
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_version.return_value = two_location_version
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "version", "get",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output
    assert "DSM-Storage" in result.output
