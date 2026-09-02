"""Unit tests for apm gws mail/shared-drive commands: list/get/backup/cancel/retire/change-plan."""
from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import GWSWorkloadType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from synology_apm.sdk.models.workload import GWSSharedDriveInfo, GWSWorkload
from tests.unit.cli.commands._gws_fixtures import (
    DOMAIN_ID,
    NAMESPACE,
    SAMPLE_PLAN,
    SAMPLE_RETIREMENT_PLAN,
    SAMPLE_TENANT,
    SAMPLE_WL,
    WORKLOAD_ID,
    WORKLOAD_UID,
    make_mock_apm,
)
from tests.unit.cli.conftest import invoke_cli

SAMPLE_WL_RETIRED = dataclasses.replace(
    SAMPLE_WL, is_retired=True, status=WorkloadStatus.RETIRED,
    plan=RetirementPlan(plan_id="retire-plan-001", name="30-Day Archive"),
)

SAMPLE_SHARED_DRIVE_WL = GWSWorkload(
    workload_id="gws-wl-id-002",
    name="Marketing Drive",
    category=WorkloadCategory.GWS,
    namespace=NAMESPACE,
    last_backup_at=SAMPLE_WL.last_backup_at,
    is_retired=False,
    protected_data_bytes=1024 * 1024 * 200,
    status=WorkloadStatus.SUCCESS,
    plan=SAMPLE_WL.plan,
    workload_type=GWSWorkloadType.SHARED_DRIVE,
    domain=DOMAIN_ID,
    info=GWSSharedDriveInfo(drive_id="drive-001", drive_name="Marketing Drive"),
    backup_user="alice@gwsdemo.example.com",
    backup_server=SAMPLE_WL.backup_server,
)


def test_gws_mail_list_table_shows_workloads() -> None:
    """gws mail list should show workload names in table."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "alice@gwsdemo.example.com" in result.output

def test_gws_mail_list_json_output() -> None:
    """gws mail list --output json should produce JSON array."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["workload_id"] == WORKLOAD_ID

def test_gws_mail_list_table_shows_domain_header() -> None:
    """gws mail list (table) should fetch and display the domain header."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID])

    assert result.exit_code == 0, result.output
    assert "gwsdemo.example.com" in result.output
    mock_apm.saas.get_gws_domain.assert_awaited_once_with(DOMAIN_ID)


def test_gws_mail_list_table_shows_dash_for_empty_domain() -> None:
    """Domain header shows '-' for the domain when domain is empty."""
    partial_tenant = dataclasses.replace(SAMPLE_TENANT, domain="")
    mock_apm = make_mock_apm(tenant=partial_tenant)

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID])

    assert result.exit_code == 0, result.output
    assert f"{DOMAIN_ID} (-)" in result.output


def test_gws_mail_list_json_output_skips_tenant_fetch() -> None:
    """gws mail list --output json should not fetch domain info (unused for non-table output)."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--output", "json"])

    assert result.exit_code == 0, result.output
    mock_apm.saas.get_gws_domain.assert_not_awaited()

def test_gws_mail_list_csv_output() -> None:
    """gws mail list --output csv should output flat CSV with backup_server_name / info_label, not nested dicts."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "workload_id" in headers
    assert "backup_server_name" in headers      # flattened field
    assert "info_label" in headers              # GWS-specific flattened field
    assert "backup_server" not in headers       # nested field name must not appear
    assert "apm-server-01" in result.output     # value of backup_server.name

@pytest.mark.parametrize("subcommand,expected_type", [
    ("mail", GWSWorkloadType.MAIL),
    ("calendar", GWSWorkloadType.CALENDAR),
    ("contact", GWSWorkloadType.CONTACT),
    ("drive", GWSWorkloadType.DRIVE),
    ("shared-drive", GWSWorkloadType.SHARED_DRIVE),
])
def test_gws_list_passes_workload_type_to_sdk(
    subcommand: str, expected_type: GWSWorkloadType
) -> None:
    """gws <subcommand> list should call workloads.list() with the matching GWSWorkloadType."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["gws", subcommand, "list", "-d", DOMAIN_ID])

    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["workload_type"] == expected_type

def test_gws_mail_list_passes_limit_to_sdk() -> None:
    """gws mail list --limit 50 should call workloads.list(limit=50)."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--limit", "50"])

    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["limit"] == 50

def test_gws_mail_list_verbose_shows_ids() -> None:
    """gws mail list --verbose should show workload ID and namespace."""
    mock_apm = make_mock_apm()

    result = invoke_cli(
        mock_apm,
        ["gws", "mail", "list", "-d", DOMAIN_ID, "--verbose"],
        env={"COLUMNS": "300"},
    )

    assert result.exit_code == 0, result.output
    assert WORKLOAD_ID in result.output
    assert NAMESPACE in result.output

def test_gws_mail_list_auto_resolves_first_domain() -> None:
    """gws mail list without -t should use first GWS domain from saas.list()."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list"])

    assert result.exit_code == 0, result.output
    mock_apm.saas.list.assert_called_once()

def test_gws_mail_list_retired_flag() -> None:
    """gws mail list --retired should call workloads.list(is_retired=True)."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--retired"])

    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["is_retired"] is True

@pytest.mark.parametrize("subcommand", ["mail", "drive"])
def test_gws_list_namespace_filter(subcommand: str) -> None:
    """gws <subcommand> list --namespace <ns> should pass namespace to the SDK as a list."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["gws", subcommand, "list", "-d", DOMAIN_ID, "--namespace", NAMESPACE])

    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["namespace"] == [NAMESPACE]

def test_gws_list_namespace_filter_is_repeatable() -> None:
    """gws mail list --namespace is repeatable (OR-filter), matching the SDK's
    namespace: str -> list[str] promotion."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, [
        "gws", "mail", "list", "-d", DOMAIN_ID,
        "--namespace", "ns-001", "--namespace", "ns-002",
    ])

    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["namespace"] == ["ns-001", "ns-002"]

def test_gws_mail_list_plan_filter_resolves_by_name() -> None:
    """gws mail list --plan <name> resolves against Protection Plans and passes plan= to the SDK."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--plan", "GWS Daily"])

    assert result.exit_code == 0, result.output
    mock_apm.plans.get_by_name.assert_awaited_once_with("GWS Daily")
    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["plan"] == [SAMPLE_PLAN]

def test_gws_mail_list_plan_filter_resolves_against_retirement_plans_when_retired() -> None:
    """gws mail list --retired --plan <name> resolves against Retirement Plans."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "list", "-d", DOMAIN_ID,
        "--retired", "--plan", "30-Day Archive",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_awaited_once_with("30-Day Archive")
    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["plan"] == [SAMPLE_RETIREMENT_PLAN]

@pytest.mark.parametrize("kwarg_name", ["plan", "status"])
def test_gws_mail_list_no_filter_passes_none(kwarg_name: str) -> None:
    """gws mail list without --plan/--status passes plan=None/status=None to the SDK."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID])

    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs[kwarg_name] is None

@pytest.mark.parametrize("status_flags,expected_status", [
    (["--status", "failed"], [WorkloadStatus.FAILED]),
    (
        ["--status", "failed", "--status", "partial"],
        [WorkloadStatus.FAILED, WorkloadStatus.PARTIAL],
    ),
])
def test_gws_mail_list_status_filter(
    status_flags: list[str], expected_status: list[WorkloadStatus]
) -> None:
    """gws mail list --status <value> (repeatable) should pass status= to the SDK."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, *status_flags])

    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.gws.workloads.list.call_args.kwargs
    assert call_kwargs["status"] == expected_status

def test_gws_mail_list_invalid_status_exits_1() -> None:
    """gws mail list --status <invalid> should exit with code 1."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID, "--status", "nope"])

    assert result.exit_code == 1

def test_gws_mail_list_shows_backup_server_name() -> None:
    """gws mail list table should display the backup server hostname."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID])

    assert result.exit_code == 0, result.output
    assert "apm-server" in result.output

def test_gws_shared_drive_list_shows_backup_user_column() -> None:
    """gws shared-drive list table shows the Backup User column, not Email."""
    mock_apm = make_mock_apm(workloads=[SAMPLE_SHARED_DRIVE_WL])

    result = invoke_cli(
        mock_apm, ["gws", "shared-drive", "list", "-d", DOMAIN_ID], env={"COLUMNS": "300"}
    )

    assert result.exit_code == 0, result.output
    assert "Backup User" in result.output
    assert "Email" not in result.output
    assert "alice@gwsdemo.example.com" in result.output
    assert "Marketing Drive" in result.output

def test_gws_mail_get_direct_mode() -> None:
    """gws mail get --id <uid> --namespace <ns> should call workloads.get directly."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "get", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0, result.output
    mock_apm.gws.workloads.get.assert_called_once_with(
        WORKLOAD_UID, NAMESPACE, domain=DOMAIN_ID, workload_type=GWSWorkloadType.MAIL
    )

def test_gws_mail_get_search_mode() -> None:
    """gws mail get <email> should call workloads.get_by_name with keyword."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "get", "alice@gwsdemo.example.com", "-d", DOMAIN_ID])

    assert result.exit_code == 0, result.output
    mock_apm.gws.workloads.get_by_name.assert_called_once_with(
        "alice@gwsdemo.example.com", DOMAIN_ID,
        workload_type=GWSWorkloadType.MAIL, is_retired=False,
    )

def test_gws_shared_drive_get_search_mode_by_name() -> None:
    """gws shared-drive get <drive-name> should call workloads.get_by_name with the drive name."""
    mock_apm = make_mock_apm()
    mock_apm.gws.workloads.get_by_name.return_value = SAMPLE_SHARED_DRIVE_WL

    result = invoke_cli(mock_apm, ["gws", "shared-drive", "get", "Marketing Drive", "-d", DOMAIN_ID])

    assert result.exit_code == 0, result.output
    mock_apm.gws.workloads.get_by_name.assert_called_once_with(
        "Marketing Drive", DOMAIN_ID,
        workload_type=GWSWorkloadType.SHARED_DRIVE, is_retired=False,
    )
    assert "Backup User" in result.output

def test_gws_mail_get_shows_backup_server() -> None:
    """gws mail get detail should display the Backup Server row."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["gws", "mail", "get", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0, result.output
    assert "Backup Server" in result.output
    assert "apm-server-01" in result.output

def test_gws_mail_get_json_output() -> None:
    """gws mail get --output json should output JSON object."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "get", "--id", WORKLOAD_UID,
        "--namespace", NAMESPACE, "--output", "json",
    ])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["workload_id"] == WORKLOAD_ID

def test_gws_mail_get_no_args_shows_help() -> None:
    """gws mail get without arguments should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["gws", "mail", "get"])

    assert result.exit_code == 0
    assert "Usage" in result.output

def test_gws_mail_get_id_without_namespace_exits_1() -> None:
    """gws mail get --id without --namespace should exit with code 1."""
    result = invoke_cli(AsyncMock(), ["gws", "mail", "get", "--id", WORKLOAD_UID])

    assert result.exit_code == 1

def test_gws_mail_get_name_and_id_conflict_exits_1() -> None:
    """gws mail get <name> --id <uid> should exit with code 1."""
    result = invoke_cli(AsyncMock(), [
        "gws", "mail", "get", "alice@gwsdemo.example.com",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 1

def test_gws_mail_backup_direct_mode_triggers_backup() -> None:
    """gws mail backup --id --namespace should call backup_now."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "backup",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    mock_apm.gws.workloads.backup_now.assert_called_once()

def test_gws_mail_backup_quiet_suppresses_output() -> None:
    """gws mail backup --quiet should produce minimal output."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "backup",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE, "--quiet",
    ])

    assert result.exit_code == 0, result.output
    assert "Backup triggered" not in result.output

def test_gws_mail_cancel_direct_mode_with_yes() -> None:
    """gws mail cancel --id --namespace --yes should call cancel_backup."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "cancel",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE, "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.gws.workloads.cancel_backup.assert_called_once()

def test_gws_mail_retire_direct_mode_with_yes() -> None:
    """gws mail retire --id --namespace --plan --yes should resolve --plan by name and call retire."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "plan-archive-001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_called_once_with("plan-archive-001")
    mock_apm.retirement_plans.get.assert_not_called()
    mock_apm.gws.workloads.retire.assert_called_once()

def test_gws_mail_retire_resolves_plan_by_uuid() -> None:
    """--plan with a UUID resolves via retirement_plans.get(), never calls retirement_plans.get_by_name()."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "0c8f033b-1111-1111-1111-000000000001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get.assert_called_once_with("0c8f033b-1111-1111-1111-000000000001")
    mock_apm.retirement_plans.get_by_name.assert_not_called()

def test_gws_mail_retire_shows_plan_info() -> None:
    """gws mail retire should display plan name and retention before prompting."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "plan-archive-001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    assert "30-Day Archive" in result.output        # plan name
    assert "30 days" in result.output               # retention from SAMPLE_RETIREMENT_PLAN
    assert "alice@gwsdemo.example.com" in result.output  # workload name from SAMPLE_WL

def test_gws_mail_retire_abort_exits_4() -> None:
    """gws mail retire (without --yes) → user declines → exit 4."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "plan-archive-001",
    ], input="n\n")

    assert result.exit_code == 4

def test_gws_mail_retire_no_plan_shows_help() -> None:
    """gws mail retire without --plan should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["gws", "mail", "retire", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0
    assert "Usage" in result.output

def test_gws_mail_change_plan_search_mode_active_workload() -> None:
    """change-plan on an active workload resolves --plan against Protection Plans by name."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "change-plan", "alice@gwsdemo.example.com", "-d", DOMAIN_ID,
        "--plan", "GWS Daily", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.plans.get_by_name.assert_called_once_with("GWS Daily")
    mock_apm.plans.get.assert_not_called()
    mock_apm.gws.workloads.change_plan.assert_called_once_with(SAMPLE_WL, SAMPLE_PLAN)
    assert "Plan changed" in result.output


def test_gws_mail_change_plan_search_mode_retired_workload() -> None:
    """change-plan --retired resolves --plan against Retirement Plans for an already-retired workload."""
    mock_apm = make_mock_apm()
    mock_apm.gws.workloads.get_by_name.return_value = SAMPLE_WL_RETIRED

    result = invoke_cli(mock_apm, [
        "gws", "mail", "change-plan", "alice@gwsdemo.example.com", "-d", DOMAIN_ID,
        "--retired", "--plan", "30-Day Archive", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_called_once_with("30-Day Archive")
    mock_apm.retirement_plans.get.assert_not_called()
    mock_apm.gws.workloads.change_plan.assert_called_once_with(SAMPLE_WL_RETIRED, SAMPLE_RETIREMENT_PLAN)


def test_gws_mail_change_plan_direct_mode() -> None:
    """change-plan --id/--namespace resolves the workload via get() (direct mode)."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "change-plan",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "gws-plan-001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.gws.workloads.get.assert_called_once_with(
        WORKLOAD_UID, NAMESPACE, domain=DOMAIN_ID, workload_type=GWSWorkloadType.MAIL
    )
    mock_apm.gws.workloads.change_plan.assert_called_once_with(SAMPLE_WL, SAMPLE_PLAN)


def test_gws_mail_change_plan_resolves_plan_by_uuid() -> None:
    """--plan with a UUID resolves via plans.get() (direct mode), never calls plans.get_by_name()."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "change-plan", "alice@gwsdemo.example.com", "-d", DOMAIN_ID,
        "--plan", "0c8f033b-1111-1111-1111-000000000001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.plans.get.assert_called_once_with("0c8f033b-1111-1111-1111-000000000001")
    mock_apm.plans.get_by_name.assert_not_called()


def test_gws_mail_change_plan_abort_exits_4() -> None:
    """change-plan (without --yes) → user declines → exit 4."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "change-plan", "alice@gwsdemo.example.com", "-d", DOMAIN_ID,
        "--plan", "GWS Daily",
    ], input="n\n")

    assert result.exit_code == 4
    mock_apm.gws.workloads.change_plan.assert_not_called()


def test_gws_mail_change_plan_no_plan_shows_help() -> None:
    """gws mail change-plan without --plan should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["gws", "mail", "change-plan", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0
    assert "Usage" in result.output


def test_gws_mail_cancel_abort_exits_4() -> None:
    """gws mail cancel (without --yes) → user declines → exit 4."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "gws", "mail", "cancel",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
    ], input="n\n")

    assert result.exit_code == 4


SAMPLE_WL_2 = dataclasses.replace(SAMPLE_WL, workload_id="gws-wl-id-003", name="bob@gwsdemo.example.com")


def test_gws_mail_list_page_all_table_combines_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """gws mail list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_apm()
    mock_apm.gws.workloads.list.side_effect = [
        ([SAMPLE_WL], 2),
        ([SAMPLE_WL_2], 2),
    ]

    result = invoke_cli(mock_apm, [
        "gws", "mail", "list", "-d", DOMAIN_ID,
        "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "alice@gwsdemo.example.com" in result.output
    assert "bob@gwsdemo.example.com" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.gws.workloads.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.gws.workloads.list.call_args_list[1].kwargs["offset"] == 1


def test_gws_mail_retire_search_mode_already_retired_errors() -> None:
    """retire by name errors out when the workload is only found among retired workloads."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError as _NotFound

    mock_apm = make_mock_apm()

    async def _get_by_name(name: str, domain: str, workload_type: GWSWorkloadType, is_retired: bool = False) -> GWSWorkload:
        if is_retired:
            return SAMPLE_WL_RETIRED
        raise _NotFound("not found", resource_type="GWSWorkload", resource_id=name)

    mock_apm.gws.workloads.get_by_name = AsyncMock(side_effect=_get_by_name)

    result = invoke_cli(mock_apm, [
        "gws", "mail", "retire", "alice@gwsdemo.example.com",
        "-d", DOMAIN_ID, "--plan", "30-Day Archive", "--yes",
    ])

    assert result.exit_code == 1
    assert "already retired" in result.output
    mock_apm.gws.workloads.retire.assert_not_called()


def test_gws_mail_list_table_without_domain_header_when_tenant_missing() -> None:
    """list omits the domain header when domain details cannot be resolved."""
    mock_apm = make_mock_apm()
    mock_apm.saas.get_gws_domain.return_value = None

    result = invoke_cli(mock_apm, ["gws", "mail", "list", "-d", DOMAIN_ID], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Domain:" not in result.output
    assert "alice@gwsdemo.example.com" in result.output


def test_gws_mail_get_shows_copy_size_when_present() -> None:
    """gws get renders the Copy Size line when backup copy data exists."""
    wl = dataclasses.replace(SAMPLE_WL, backup_copy_data_bytes=1024**3)
    mock_apm = make_mock_apm()
    mock_apm.gws.workloads.get_by_name.return_value = wl

    result = invoke_cli(mock_apm, ["gws", "mail", "get", "alice@gwsdemo.example.com", "-d", DOMAIN_ID])

    assert result.exit_code == 0, result.output
    copy_line = next(line for line in result.output.splitlines() if "Copy Size:" in line)
    assert "1.0 GB" in copy_line
