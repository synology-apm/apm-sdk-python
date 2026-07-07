"""Unit tests for apm machine version commands: list/get/lock/unlock."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, time
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    LogLevel,
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
    VerifyStatus,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.activity import BackupActivity
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.version import WorkloadVersion
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.cli.conftest import invoke_cli

SAMPLE_WL = MachineWorkload(
    workload_id="wl-id-001",
    name="CORP-PC-001",
    category=WorkloadCategory.MACHINE,
    namespace="ns-001",
    last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    is_retired=False,
    protected_data_bytes=1024 * 1024 * 500,
    status=WorkloadStatus.SUCCESS,
    plan=ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC,
    agent_version="1.2.0",
    backup_server=LocationInfo(
        is_remote_storage=False,
        identifier="ns-server-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    ),
)


SAMPLE_PLAN = ProtectionPlan(
    plan_id="plan-id-001",
    name="Daily Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=10),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(2, 0)),
    ),
    workload_count=3,
)


SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="retire-plan-001",
    name="30-Day Archive",
    description="",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=False),
    workload_count=1,
)


def make_mock_client(
    workloads: list[MachineWorkload] | None = None, plans: list[ProtectionPlan] | None = None
) -> AsyncMock:
    mock = AsyncMock()
    mock.machine.workloads.list.return_value = (workloads or [SAMPLE_WL], 5)
    mock.machine.workloads.get.return_value = SAMPLE_WL
    mock.machine.workloads.get_by_name.return_value = SAMPLE_WL
    mock.machine.workloads.backup_now.return_value = None
    mock.machine.workloads.cancel_backup.return_value = None
    mock.machine.workloads.retire.return_value = None
    mock.retirement_plans.get.return_value = SAMPLE_RETIREMENT_PLAN
    mock.machine.workloads.list_versions.return_value = ([], 0)
    mock.machine.workloads.lock_version.return_value = None
    mock.machine.workloads.unlock_version.return_value = None
    mock.machine.plans.list.return_value = (plans or [SAMPLE_PLAN], 5)
    mock.machine.plans.get.return_value = SAMPLE_PLAN
    mock.activities.list.return_value = ([], 5)
    return mock


def _sdk_error() -> ResourceNotFoundError:
    return ResourceNotFoundError("not found", resource_type="Workload", resource_id="x")


SAMPLE_VERSION = WorkloadVersion(
    version_id="ver-001",
    workload_id="wl-uid-001",
    namespace="ns-001",
    created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    status=VersionStatus.SUCCESS,
    execution_id="ABE_1",
    locked=False,
    changed_size_bytes=1024 * 1024 * 100,
)


SAMPLE_ACT_FOR_VERSION = BackupActivity(
    activity_id="act-uid-001",
    execution_id="ABE_1",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_PC,
    workload_id="wl-uid-001",
    workload_namespace="",
    workload_name="CORP-PC-001",
    plan_name="Daily Backup",
    status=BackupActivityStatus.SUCCESS,
    started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    finished_at=datetime(2026, 4, 21, 9, 30, tzinfo=UTC),
    duration_seconds=1800,
    data_transferred_bytes=1024 * 1024 * 100,
    progress=100,
)


SAMPLE_VERSION_2 = dataclasses.replace(SAMPLE_VERSION, version_id="ver-002")


def test_machine_versions_empty() -> None:
    """machine version list should not crash when there are no versions."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "version", "list", "wl-id-001"])
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output
    mock_apm.machine.workloads.list_versions.assert_called_once()


def test_machine_versions_json_output() -> None:
    """machine version list --output json should output a JSON array."""
    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "version", "list", "wl-id-001", "--output", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == []  # mocked workload has no versions


def test_machine_versions_yaml_output() -> None:
    import yaml

    mock_apm = make_mock_client()
    result = invoke_cli(mock_apm, ["machine", "version", "list", "wl-id-001", "--output", "yaml"])
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.output) == []  # mocked workload has no versions


def test_machine_version_get_shows_activity_detail() -> None:
    """machine version get should display activity details for the version (search mode)."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    assert "CORP-PC-001" in result.output
    assert "(Using version:" not in result.output
    mock_apm.machine.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-001")
    mock_apm.activities.backup.get_by_version.assert_called_once_with(SAMPLE_VERSION)


def test_machine_version_get_direct_mode() -> None:
    """machine version get in direct mode (--workload-id + --namespace + --id) fetches workload then calls get_version."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get",
        "--workload-id", "wl-id-001", "--namespace", "ns-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    assert "(Using version:" not in result.output
    mock_apm.machine.workloads.get.assert_called_once_with("wl-id-001", namespace="ns-001")
    mock_apm.machine.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-001")
    mock_apm.activities.backup.get_by_version.assert_called_once_with(SAMPLE_VERSION)


def test_machine_version_get_version_not_found_exits_error() -> None:
    """machine version get should exit with code 1 when version_id is not found."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.side_effect = ResourceNotFoundError(
        "not found", resource_type="WorkloadVersion", resource_id="nonexistent-ver"
    )
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "nonexistent-ver",
    ])
    assert result.exit_code == 1


def test_machine_version_get_latest_search_mode() -> None:
    """machine version get without --id should call get_latest_version() to retrieve the latest version."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_latest_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001",
    ])
    assert result.exit_code == 0, result.output
    assert "(Using version: ver-001, created at" in result.output
    mock_apm.machine.workloads.get_latest_version.assert_called_once()
    mock_apm.machine.workloads.list_versions.assert_not_called()


def test_machine_version_get_latest_direct_mode() -> None:
    """machine version get in direct mode without --id should call get_latest_version()."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_latest_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get",
        "--workload-id", "wl-id-001", "--namespace", "ns-001",
    ])
    assert result.exit_code == 0, result.output
    assert "(Using version: ver-001, created at" in result.output
    mock_apm.machine.workloads.get_latest_version.assert_called_once_with(SAMPLE_WL)
    mock_apm.machine.workloads.list_versions.assert_not_called()


def test_machine_version_lock_search_mode() -> None:
    """version lock <NAME> --id <ver-id> should look up the version then call lock_version (search mode)."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "lock", "CORP-PC-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    assert "locked" in result.output
    mock_apm.machine.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-001")
    mock_apm.machine.workloads.lock_version.assert_called_once_with(SAMPLE_VERSION)


def test_machine_version_lock_direct_mode() -> None:
    """version lock --workload-id --namespace --id should fetch workload, look up the version, then call lock_version."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "lock",
        "--workload-id", "wl-id-001", "--namespace", "ns-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.machine.workloads.get.assert_called_once_with("wl-id-001", namespace="ns-001")
    mock_apm.machine.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-001")
    mock_apm.machine.workloads.lock_version.assert_called_once_with(SAMPLE_VERSION)


def test_machine_version_lock_no_id_shows_help() -> None:
    """version lock <NAME> without --id should show help and exit 0."""
    result = invoke_cli(AsyncMock(), [
        "machine", "version", "lock", "CORP-PC-001",
    ])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_machine_version_lock_no_args_shows_help() -> None:
    """version lock without any arguments should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["machine", "version", "lock"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_machine_version_unlock_search_mode() -> None:
    """version unlock <NAME> --id <ver-id> should look up the version then call unlock_version (search mode)."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "unlock", "CORP-PC-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    assert "unlocked" in result.output
    mock_apm.machine.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-001")
    mock_apm.machine.workloads.unlock_version.assert_called_once_with(SAMPLE_VERSION)


def test_machine_version_unlock_direct_mode() -> None:
    """version unlock in direct mode should fetch workload, look up the version, then call unlock_version."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "unlock",
        "--workload-id", "wl-id-001", "--namespace", "ns-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    mock_apm.machine.workloads.get.assert_called_once_with("wl-id-001", namespace="ns-001")
    mock_apm.machine.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-001")
    mock_apm.machine.workloads.unlock_version.assert_called_once_with(SAMPLE_VERSION)


def test_machine_version_list_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.get_by_name.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, ["machine", "version", "list", "no-such-machine"])
    assert result.exit_code == 1


def test_machine_version_get_sdk_error_exits_1(mock_apm: AsyncMock) -> None:
    mock_apm.machine.workloads.get_by_name.side_effect = _sdk_error()
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "no-such-machine", "--id", "ver-001",
    ])
    assert result.exit_code == 1


def test_machine_versions_table_shows_version_rows() -> None:
    """version list table should show version ID and status when versions exist."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list_versions.return_value = ([SAMPLE_VERSION], 1)
    result = invoke_cli(mock_apm, [
        "machine", "version", "list", "CORP-PC-001",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "ver-001" in result.output
    assert "Showing 1 of 1" in result.output


def test_machine_version_list_passes_offset_to_sdk() -> None:
    """version list --offset N should pass offset=N to list_versions and show footer with correct range."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list_versions.return_value = ([SAMPLE_VERSION], 26)
    result = invoke_cli(mock_apm, [
        "machine", "version", "list", "CORP-PC-001", "--offset", "25",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.machine.workloads.list_versions.call_args.kwargs
    assert call_kwargs["offset"] == 25
    assert "26" in result.output          # row number starts at offset+1
    assert "26–26 of 26" in result.output  # footer shows correct range


def test_machine_version_get_json_output() -> None:
    """machine version get --output json should output a JSON object containing version and activity."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "ver-001", "--output", "json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["version_id"] == "ver-001"
    assert data["activity"]["activity_id"] == "act-uid-001"


def test_machine_version_list_csv_output() -> None:
    """machine version list --output csv should output flat CSV with location_count field, not nested locations list."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list_versions.return_value = ([SAMPLE_VERSION], 1)
    result = invoke_cli(mock_apm, [
        "machine", "version", "list", "wl-id-001", "--output", "csv",
    ])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "version_id" in headers
    assert "location_count" in headers          # flattened field
    assert "locations" not in headers           # nested field name must not appear
    assert "ver-001" in result.output


def test_machine_version_list_verbose_shows_workload_ids() -> None:
    """machine version list --verbose should include workload_id and namespace in output."""
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list_versions.return_value = ([], 0)
    result = invoke_cli(mock_apm, [
        "machine", "version", "list", "CORP-PC-001", "--verbose",
    ])
    assert result.exit_code == 0, result.output
    assert "wl-id-001" in result.output
    assert "ns-001" in result.output


def test_machine_version_list_vm_shows_verify_column() -> None:
    """machine version list for a VM workload should include a Backup Verification column."""
    vm_wl = MachineWorkload(
        workload_id="vm-id-001",
        name="VM-PROD",
        category=WorkloadCategory.MACHINE,
        namespace="ns-001",
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.SUCCESS,
        plan=ProtectionPlan(plan_id="plan-vm-001", name="Test Plan", category=WorkloadCategory.MACHINE),
        workload_type=MachineWorkloadType.VM,
        agent_version=None,
    )
    vm_version = WorkloadVersion(
        version_id="vm-ver-001",
        workload_id="vm-id-001",
        namespace="ns-001",
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="ABE_2",
        locked=False,
        changed_size_bytes=0,
        verify_status=VerifyStatus.SUCCESS,
    )
    mock_apm = make_mock_client(workloads=[vm_wl])
    mock_apm.machine.workloads.get_by_name.return_value = vm_wl
    mock_apm.machine.workloads.list_versions.return_value = ([vm_version], 1)
    result = invoke_cli(mock_apm, [
        "machine", "version", "list", "VM-PROD",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "Verification" in result.output


def test_machine_version_get_name_and_workload_id_conflict_exits_1() -> None:
    """machine version get NAME --workload-id X should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "machine", "version", "get", "CORP-PC-001", "--workload-id", "wl-001",
    ])
    assert result.exit_code == 1
    assert "cannot be used" in result.output


def test_machine_version_get_workload_id_without_namespace_exits_1() -> None:
    """machine version get --workload-id X (no --namespace) should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "machine", "version", "get", "--workload-id", "wl-001",
    ])
    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output


def test_machine_version_get_no_args_shows_help() -> None:
    """machine version get (no args) should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["machine", "version", "get"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_machine_version_get_shows_log_entries() -> None:
    """machine version get renders a log entries table when the activity has log_entries."""
    from synology_apm.sdk.models.activity import ActivityLogEntry

    act_with_logs = BackupActivity(
        activity_id="act-002",
        execution_id="ABE_1",
        namespace="ns-001",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_PC,
        workload_id="wl-uid-001",
        workload_namespace="",
        workload_name="CORP-PC-001",
        plan_name="Daily Backup",
        status=BackupActivityStatus.SUCCESS,
        started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 21, 9, 30, tzinfo=UTC),
        duration_seconds=1800,
        data_transferred_bytes=None,
        progress=100,
        log_entries=(
            ActivityLogEntry(
                timestamp=datetime(2026, 4, 21, 9, 5, tzinfo=UTC),
                level=LogLevel.INFO,
                message="Backup started successfully",
            ),
        ),
    )
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = act_with_logs
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    assert "Logs" in result.output
    assert "Backup started successfully" in result.output


def test_machine_version_lock_name_and_workload_id_conflict_exits_1() -> None:
    """version lock NAME --workload-id X should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "machine", "version", "lock", "CORP-PC-001",
        "--workload-id", "wl-001", "--id", "ver-001",
    ])
    assert result.exit_code == 1
    assert "cannot be used" in result.output


def test_machine_version_lock_workload_id_without_namespace_exits_1() -> None:
    """version lock --workload-id X --id V (no --namespace) should print error and exit 1."""
    result = invoke_cli(AsyncMock(), [
        "machine", "version", "lock",
        "--workload-id", "wl-001", "--id", "ver-001",
    ])
    assert result.exit_code == 1
    assert "--namespace" in result.output or "requires" in result.output


def test_machine_version_get_shows_locked_icon() -> None:
    """version get with locked=True should display the lock icon in output."""
    from synology_apm.sdk.models.version import WorkloadVersion

    locked_version = WorkloadVersion(
        version_id="ver-locked-001",
        workload_id="wl-uid-001",
        namespace="ns-001",
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="ABE_1",
        locked=True,
        changed_size_bytes=0,
    )
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = locked_version
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "ver-locked-001",
    ])
    assert result.exit_code == 0, result.output
    assert "🔒" in result.output


def test_machine_version_get_shows_locations() -> None:
    """version get with locations set should display location names in output."""
    from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion

    version_with_locs = WorkloadVersion(
        version_id="ver-locs-001",
        workload_id="wl-uid-001",
        namespace="ns-001",
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="ABE_1",
        locked=False,
        changed_size_bytes=0,
        locations=[
            VersionLocation(
                namespace="ns-001",
                location_info=LocationInfo(
                    is_remote_storage=False, identifier="ns-001",
                    name="apm-server-01", endpoint="192.0.2.1", vault=None,
                ),
                location_id="ver-locs-001",
            ),
        ],
    )
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = version_with_locs
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "ver-locs-001",
    ])
    assert result.exit_code == 0, result.output
    assert "apm-server-01" in result.output


def test_machine_version_get_shows_backup_scope() -> None:
    """version get with backup_scope set should display the scope label in output."""
    from synology_apm.sdk.enums import BackupScope

    act_with_scope = BackupActivity(
        activity_id="act-scope-001",
        execution_id="ABE_1",
        namespace="ns-001",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_PC,
        workload_id="wl-uid-001",
        workload_namespace="",
        workload_name="CORP-PC-001",
        plan_name="Daily Backup",
        status=BackupActivityStatus.SUCCESS,
        started_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 21, 9, 30, tzinfo=UTC),
        duration_seconds=1800,
        data_transferred_bytes=None,
        progress=100,
        backup_scope=BackupScope.ENTIRE_DEVICE,
    )
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = SAMPLE_VERSION
    mock_apm.activities.backup.get_by_version.return_value = act_with_scope
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    assert "Backup Scope" in result.output


def test_machine_version_list_page_all_combines_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """machine version list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list_versions.side_effect = [
        ([SAMPLE_VERSION], 2),
        ([SAMPLE_VERSION_2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "machine", "version", "list", "CORP-PC-001", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "ver-001" in result.output
    assert "ver-002" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.machine.workloads.list_versions.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.machine.workloads.list_versions.call_args_list[1].kwargs["offset"] == 1


def test_machine_version_list_shows_copy_status_column() -> None:
    """version list table should include 'Copy Status' and 'Locations' columns (renamed from 'Locs')."""
    from synology_apm.sdk.enums import VersionCopyStatus
    from synology_apm.sdk.models.version import VersionLocation

    version_with_copy = dataclasses.replace(
        SAMPLE_VERSION,
        copy_status=VersionCopyStatus.COMPLETED,
        locations=[
            VersionLocation(
                namespace="ns-001",
                location_info=LocationInfo(
                    is_remote_storage=False, identifier="ns-001",
                    name="apm-server-01", endpoint="192.0.2.1", vault=None,
                ),
                location_id="ver-001",
            ),
        ],
    )
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.list_versions.return_value = ([version_with_copy], 1)
    result = invoke_cli(mock_apm, [
        "machine", "version", "list", "CORP-PC-001",
    ], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "Copy Status" in result.output
    assert "Locations" in result.output
    assert "apm-server-01" in result.output  # location name shown in Locations column


def test_machine_version_get_shows_copy_status() -> None:
    """version get should display Copy Status when copy_status is set on the version."""
    from synology_apm.sdk.enums import CopyReason, VersionCopyStatus

    version_with_fail = dataclasses.replace(
        SAMPLE_VERSION,
        copy_status=VersionCopyStatus.FAILED,
        copy_reason=CopyReason.DESTINATION_DISCONNECTED,
    )
    mock_apm = make_mock_client()
    mock_apm.machine.workloads.get_version.return_value = version_with_fail
    mock_apm.activities.backup.get_by_version.return_value = SAMPLE_ACT_FOR_VERSION
    result = invoke_cli(mock_apm, [
        "machine", "version", "get", "CORP-PC-001", "--id", "ver-001",
    ])
    assert result.exit_code == 0, result.output
    assert "Copy Status" in result.output
    assert "Unable to perform" in result.output  # FAILED display string
