"""Unit tests for apm plan protection list command."""
from __future__ import annotations

import dataclasses
import json
from datetime import time
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import (
    M365WorkloadType,
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    GFSRetention,
    PlanBackupCopyStatus,
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.tiering_plan import TieringPlan
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload, MachineWorkload
from tests.unit.cli.conftest import invoke_cli

# ── Fixtures ──────────────────────────────────────────────────────────────


MACHINE_PLAN = ProtectionPlan(
    plan_id="machine-plan-001",
    name="Daily Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=10),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(2, 0)),
    ),
    workload_count=3,
    description="Default Machine Plan",
    successful_workload_count=2,
    unsuccessful_workload_count=1,
    is_immutable=False,
)

M365_PLAN = ProtectionPlan(
    plan_id="m365-plan-001",
    name="Daily Backup (M365)",
    category=WorkloadCategory.M365,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=None,
    ),
    workload_count=5,
    description="",
    successful_workload_count=4,
    unsuccessful_workload_count=1,
    is_immutable=True,
)

MACHINE_PLAN_NO_SCHEDULE = ProtectionPlan(
    plan_id="machine-plan-002",
    name="Weekly Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=None,
    ),
    workload_count=0,
    description="",
    successful_workload_count=0,
    unsuccessful_workload_count=0,
    is_immutable=False,
)

RETIREMENT_PLAN = RetirementPlan(
    plan_id="retire-plan-001",
    name="30-Day Retention",
    description="Default Retirement Plan",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=False),
    workload_count=1,
)

TIERING_PLAN = TieringPlan(
    plan_id="tiering-plan-001",
    name="30-Day Tiering",
    description="Move old versions to S3",
    tiering_after_days=30,
    daily_check_time=time(1, 30),
    destination=LocationInfo(
        is_remote_storage=True,
        identifier="dest-ns-001",
        name="My S3 Storage",
        endpoint="s3.amazonaws.com",
        vault=None,
    ),
    server_count=2,
)

SAMPLE_WL = MachineWorkload(
    workload_id="wl-id-001",
    name="CORP-PC-001",
    category=WorkloadCategory.MACHINE,
    namespace="ns-001",
    last_backup_at=None,
    is_retired=False,
    protected_data_bytes=0,
    status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PC,
    agent_version="1.2.0",
)

SAMPLE_M365_WL = M365Workload(
    workload_id="wl-m365-001",
    name="Alice",
    category=WorkloadCategory.M365,
    namespace="ns-m365-001",
    last_backup_at=None,
    is_retired=False,
    protected_data_bytes=0,
    status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-m365-001", name="Daily Backup (M365)", category=WorkloadCategory.M365),
    tenant_id="tenant-001",
    workload_type=M365WorkloadType.EXCHANGE,
    info=M365UserInfo(user_principal_name="alice@contoso.com"),
)


def make_mock_client() -> AsyncMock:
    mock = AsyncMock()
    mock.plans.list.return_value = ([MACHINE_PLAN, M365_PLAN], 5)
    mock.plans.get.return_value = MACHINE_PLAN
    mock.plans.get_by_name.return_value = MACHINE_PLAN
    mock.machine.workloads.get.return_value = SAMPLE_WL
    mock.machine.workloads.get_by_name.return_value = SAMPLE_WL
    mock.machine.workloads.retire.return_value = None
    mock.m365.workloads.get.return_value = SAMPLE_M365_WL
    mock.m365.workloads.get_by_name.return_value = SAMPLE_M365_WL
    mock.m365.workloads.retire.return_value = None
    mock.retirement_plans.list.return_value = ([RETIREMENT_PLAN], 5)
    mock.retirement_plans.get.return_value = RETIREMENT_PLAN
    mock.retirement_plans.get_by_name.return_value = RETIREMENT_PLAN
    mock.tiering_plans.list.return_value = ([TIERING_PLAN], 1)
    mock.tiering_plans.get.return_value = TIERING_PLAN
    mock.tiering_plans.get_by_name.return_value = TIERING_PLAN
    return mock


def _plan_error() -> ResourceNotFoundError:
    return ResourceNotFoundError("not found", resource_type="Plan", resource_id="x")


# ═══════════════════════════════════════════════════════════════════════════
# apm plan protection list
# ═══════════════════════════════════════════════════════════════════════════


def test_protection_list_all_shows_both_categories() -> None:
    """list (no arguments) calls plans.list(category=None) and should show both plans."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list"])

    assert result.exit_code == 0, result.output
    assert "Daily Backup" in result.output
    assert "Daily Backup (M365)" in result.output
    mock_apm.plans.list.assert_called_once_with(category=None, name_contains=None, limit=25, offset=0)


@pytest.mark.parametrize("cli_category,sdk_category", [
    ("machine", WorkloadCategory.MACHINE),
    ("m365", WorkloadCategory.M365),
])
def test_protection_list_category_filter(
    cli_category: str, sdk_category: WorkloadCategory
) -> None:
    """list --category <category> calls plans.list(category=<sdk_category>)."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", cli_category])

    assert result.exit_code == 0, result.output
    mock_apm.plans.list.assert_called_once_with(
        category=sdk_category, name_contains=None, limit=25, offset=0
    )


def test_protection_list_invalid_category() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "unknown"])

    assert result.exit_code == 1


def test_protection_list_json() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN], 5)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert isinstance(data, list)
    assert data[0]["name"] == "Daily Backup"


def test_protection_list_csv() -> None:
    """plan protection list --output csv should output flat CSV with retention_type, not nested policy dict."""
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN], 5)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "plan_id" in headers
    assert "retention_type" in headers          # flattened field
    assert "retention_versions" in headers      # renamed from retention_count
    assert "policy" not in headers              # nested field name must not appear
    assert "machine-plan-001" in result.output


def test_protection_list_shows_plan_id_in_verbose() -> None:
    """Plan ID is shown only in verbose mode (-v)."""
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN], 5)

    default_result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])
    verbose_result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine", "-v"])

    assert default_result.exit_code == 0, default_result.output
    assert "machine-plan-001" not in default_result.output
    assert verbose_result.exit_code == 0, verbose_result.output
    assert "machine-plan-001" in verbose_result.output


def test_protection_list_retention_by_days() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([MACHINE_PLAN_NO_SCHEDULE], 5)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])

    assert result.exit_code == 0, result.output
    assert "30 days" in result.output


def test_protection_list_retention_keep_all() -> None:
    """KEEP_ALL retention should display 'Keep all' in the table."""
    plan = ProtectionPlan(
        plan_id="p-keep-all",
        name="Keep All Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ALL),
            schedule=None,
        ),
        workload_count=0,
    )
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([plan], 1)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])

    assert result.exit_code == 0, result.output
    assert "Keep all" in result.output


def test_protection_list_retention_keep_advanced() -> None:
    """KEEP_ADVANCED retention should display 'GFS' with slot summary in the table."""
    plan = ProtectionPlan(
        plan_id="p-gfs",
        name="GFS Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(
                retention_type=RetentionType.KEEP_ADVANCED,
                gfs=GFSRetention(
                    daily_versions=7,
                    weekly_versions=4,
                    monthly_versions=12,
                    yearly_versions=1,
                ),
            ),
            schedule=None,
        ),
        workload_count=0,
    )
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([plan], 1)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"])

    assert result.exit_code == 0, result.output
    assert "Advanced rules" in result.output


# ── YAML output ───────────────────────────────────────────────────────────


def test_protection_list_yaml_output() -> None:
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--output", "yaml"])

    assert result.exit_code == 0, result.output
    assert "plan_id" in result.output


# ── error paths: SDK raises APMError → exit 1 ────────────────────────────


def test_protection_list_sdk_error_exits_1() -> None:
    mock_apm = make_mock_client()
    mock_apm.plans.list.side_effect = _plan_error()

    result = invoke_cli(mock_apm, ["plan", "protection", "list"])

    assert result.exit_code == 1


# ── Additional output format / edge case tests ────────────────────────────


def test_protection_list_csv_output() -> None:
    """plan protection list --output csv should produce CSV with plan_id header."""
    mock_apm = make_mock_client()

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "plan_id" in lines[0]
    assert "machine-plan-001" in result.output


# ── --page-all ───────────────────────────────────────────────────────────────


def test_protection_list_page_all_combines_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan protection list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    second_plan = dataclasses.replace(M365_PLAN, plan_id="machine-plan-002", name="Weekly Backup")
    mock_apm = make_mock_client()
    mock_apm.plans.list.side_effect = [
        ([MACHINE_PLAN], 2),
        ([second_plan], 2),
    ]

    result = invoke_cli(mock_apm, [
        "plan", "protection", "list", "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Daily Backup" in result.output
    assert "Weekly Backup" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.plans.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.plans.list.call_args_list[1].kwargs["offset"] == 1


def test_protection_list_shows_copy_status_column() -> None:
    """plan protection list table should include 'Copy Status' column when plan has backup_copy_status."""
    from synology_apm.sdk.enums import VersionCopyStatus

    plan_with_bcs = dataclasses.replace(
        MACHINE_PLAN,
        backup_copy_status=PlanBackupCopyStatus(status=VersionCopyStatus.COMPLETED, reason=None),
    )
    mock_apm = make_mock_client()
    mock_apm.plans.list.return_value = ([plan_with_bcs], 1)

    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "machine"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Copy Status" in result.output
