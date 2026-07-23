"""Unit tests for apm m365 exchange/onedrive commands: list/get/backup/cancel/retire/change-plan."""
from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import M365WorkloadType, WorkloadStatus
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from synology_apm.sdk.models.workload import M365Workload
from tests.unit.cli.commands._m365_fixtures import (
    NAMESPACE,
    SAMPLE_PLAN,
    SAMPLE_RETIREMENT_PLAN,
    SAMPLE_TENANT,
    SAMPLE_WL,
    TENANT_ID,
    WORKLOAD_ID,
    WORKLOAD_UID,
    make_mock_apm,
)
from tests.unit.cli.conftest import invoke_cli

SAMPLE_WL_RETIRED = dataclasses.replace(
    SAMPLE_WL, is_retired=True, status=WorkloadStatus.RETIRED,
    plan=RetirementPlan(plan_id="retire-plan-001", name="30-Day Archive"),
)






def test_m365_exchange_list_table_shows_workloads() -> None:
    """m365 exchange list should show workload names in table."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    assert "alice@contoso.com" in result.output

def test_m365_exchange_list_json_output() -> None:
    """m365 exchange list --output json should produce JSON array."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)

def test_m365_exchange_list_table_shows_tenant_header() -> None:
    """m365 exchange list (table) should fetch and display the tenant header."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    assert "Contoso" in result.output
    assert "admin@contoso.com" in result.output
    mock_apm.saas.get_m365_tenant.assert_awaited_once_with(TENANT_ID)


def test_m365_exchange_list_table_shows_dash_for_empty_tenant_email() -> None:
    """Tenant header shows '-' for the domain when tenant_email is empty."""
    partial_tenant = dataclasses.replace(SAMPLE_TENANT, tenant_email="")
    mock_apm = make_mock_apm(tenant=partial_tenant)

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    assert "Contoso (-)" in result.output


def test_m365_exchange_list_json_output_skips_tenant_fetch() -> None:
    """m365 exchange list --output json should not fetch tenant info (unused for non-table output)."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--output", "json"])

    assert result.exit_code == 0, result.output
    mock_apm.saas.get_m365_tenant.assert_not_awaited()

def test_m365_exchange_list_csv_output() -> None:
    """m365 exchange list --output csv should output flat CSV with backup_server_name / info_label, not nested dicts."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--output", "csv"])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    headers = lines[0].split(",")
    assert "workload_id" in headers
    assert "backup_server_name" in headers      # flattened field
    assert "info_label" in headers              # M365-specific flattened field
    assert "backup_server" not in headers       # nested field name must not appear
    assert "apm-server-01" in result.output     # value of backup_server.name

@pytest.mark.parametrize("subcommand,expected_type", [
    ("exchange", M365WorkloadType.EXCHANGE),
    ("onedrive", M365WorkloadType.ONEDRIVE),
])
def test_m365_list_passes_workload_type_to_sdk(
    subcommand: str, expected_type: M365WorkloadType
) -> None:
    """m365 <subcommand> list should call workloads.list() with the matching M365WorkloadType."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["m365", subcommand, "list", "-t", TENANT_ID])

    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["workload_type"] == expected_type

def test_m365_exchange_list_passes_limit_to_sdk() -> None:
    """m365 exchange list --limit 50 should call workloads.list(limit=50)."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--limit", "50"])

    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["limit"] == 50

def test_m365_exchange_list_verbose_shows_ids() -> None:
    """m365 exchange list --verbose should show workload ID and namespace."""
    mock_apm = make_mock_apm()

    result = invoke_cli(
        mock_apm,
        ["m365", "exchange", "list", "-t", TENANT_ID, "--verbose"],
        env={"COLUMNS": "300"},
    )

    assert result.exit_code == 0, result.output
    assert WORKLOAD_ID in result.output
    assert NAMESPACE in result.output

def test_m365_exchange_list_auto_resolves_first_tenant() -> None:
    """m365 exchange list without -t should use first M365 tenant from saas.list()."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list"])

    assert result.exit_code == 0, result.output
    mock_apm.saas.list.assert_called_once()

def test_m365_exchange_list_retired_flag() -> None:
    """m365 exchange list --retired should call workloads.list(is_retired=True)."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--retired"])

    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["is_retired"] is True

@pytest.mark.parametrize("subcommand", ["exchange", "onedrive"])
def test_m365_list_namespace_filter(subcommand: str) -> None:
    """m365 <subcommand> list --namespace <ns> should pass namespace to the SDK."""
    mock_apm = make_mock_apm()

    invoke_cli(mock_apm, ["m365", subcommand, "list", "-t", TENANT_ID, "--namespace", NAMESPACE])

    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["namespace"] == NAMESPACE

def test_m365_exchange_list_plan_filter_resolves_by_name() -> None:
    """m365 exchange list --plan <name> resolves against Protection Plans and passes plan= to the SDK."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--plan", "M365 Daily"])

    assert result.exit_code == 0, result.output
    mock_apm.plans.get_by_name.assert_awaited_once_with("M365 Daily")
    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["plan"] == [SAMPLE_PLAN]

def test_m365_exchange_list_plan_filter_resolves_against_retirement_plans_when_retired() -> None:
    """m365 exchange list --retired --plan <name> resolves against Retirement Plans."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "list", "-t", TENANT_ID,
        "--retired", "--plan", "30-Day Archive",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_awaited_once_with("30-Day Archive")
    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["plan"] == [SAMPLE_RETIREMENT_PLAN]

@pytest.mark.parametrize("kwarg_name", ["plan", "status"])
def test_m365_exchange_list_no_filter_passes_none(kwarg_name: str) -> None:
    """m365 exchange list without --plan/--status passes plan=None/status=None to the SDK."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs[kwarg_name] is None

@pytest.mark.parametrize("status_flags,expected_status", [
    (["--status", "failed"], [WorkloadStatus.FAILED]),
    (
        ["--status", "failed", "--status", "partial"],
        [WorkloadStatus.FAILED, WorkloadStatus.PARTIAL],
    ),
])
def test_m365_exchange_list_status_filter(
    status_flags: list[str], expected_status: list[WorkloadStatus]
) -> None:
    """m365 exchange list --status <value> (repeatable) should pass status= to the SDK."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, *status_flags])

    assert result.exit_code == 0, result.output
    call_kwargs = mock_apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["status"] == expected_status

def test_m365_exchange_list_invalid_status_exits_1() -> None:
    """m365 exchange list --status <invalid> should exit with code 1."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID, "--status", "nope"])

    assert result.exit_code == 1

def test_m365_exchange_list_shows_backup_server_name() -> None:
    """m365 exchange list table should display the backup server hostname."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    assert "apm-server" in result.output

def test_m365_exchange_get_direct_mode() -> None:
    """m365 exchange get --id <uid> --namespace <ns> should call workloads.get directly."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "get", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get.assert_called_once_with(
        WORKLOAD_UID, NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE
    )

def test_m365_exchange_get_search_mode() -> None:
    """m365 exchange get <identity> should call workloads.get with keyword."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "get", "alice@contoso.com", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get_by_name.assert_called_once_with(
        "alice@contoso.com", TENANT_ID,
        workload_type=M365WorkloadType.EXCHANGE, is_retired=False,
    )

def test_m365_exchange_get_shows_backup_server() -> None:
    """m365 exchange get detail should display the Backup Server row."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, ["m365", "exchange", "get", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0, result.output
    assert "Backup Server" in result.output
    assert "apm-server-01" in result.output

def test_m365_exchange_get_json_output() -> None:
    """m365 exchange get --output json should output JSON object."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "get", "--id", WORKLOAD_UID,
        "--namespace", NAMESPACE, "--output", "json",
    ])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["workload_id"] == WORKLOAD_ID

def test_m365_exchange_get_no_args_shows_help() -> None:
    """m365 exchange get without arguments should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "get"])

    assert result.exit_code == 0
    assert "Usage" in result.output

def test_m365_exchange_get_id_without_namespace_exits_1() -> None:
    """m365 exchange get --id without --namespace should exit with code 1."""
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "get", "--id", WORKLOAD_UID])

    assert result.exit_code == 1

def test_m365_exchange_get_name_and_id_conflict_exits_1() -> None:
    """m365 exchange get <name> --id <uid> should exit with code 1."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "get", "alice@contoso.com",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 1

def test_m365_exchange_backup_direct_mode_triggers_backup() -> None:
    """m365 exchange backup --id --namespace should call backup_now."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "backup",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.backup_now.assert_called_once()

def test_m365_exchange_backup_quiet_suppresses_output() -> None:
    """m365 exchange backup --quiet should produce minimal output."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "backup",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE, "--quiet",
    ])

    assert result.exit_code == 0, result.output
    assert "Backup triggered" not in result.output

def test_m365_exchange_cancel_direct_mode_with_yes() -> None:
    """m365 exchange cancel --id --namespace --yes should call cancel_backup."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "cancel",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE, "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.cancel_backup.assert_called_once()

def test_m365_exchange_retire_direct_mode_with_yes() -> None:
    """m365 exchange retire --id --namespace --plan --yes should resolve --plan by name and call retire."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "plan-archive-001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_called_once_with("plan-archive-001")
    mock_apm.retirement_plans.get.assert_not_called()
    mock_apm.m365.workloads.retire.assert_called_once()

def test_m365_exchange_retire_resolves_plan_by_uuid() -> None:
    """--plan with a UUID resolves via retirement_plans.get(), never calls retirement_plans.get_by_name()."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "0c8f033b-1111-1111-1111-000000000001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get.assert_called_once_with("0c8f033b-1111-1111-1111-000000000001")
    mock_apm.retirement_plans.get_by_name.assert_not_called()

def test_m365_exchange_retire_shows_plan_info() -> None:
    """m365 exchange retire should display plan name and retention before prompting."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "plan-archive-001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    assert "30-Day Archive" in result.output        # plan name
    assert "30 days" in result.output               # retention from SAMPLE_RETIREMENT_PLAN
    assert "alice@contoso.com" in result.output     # workload name from SAMPLE_WL

def test_m365_exchange_retire_abort_exits_4() -> None:
    """m365 exchange retire (without --yes) → user declines → exit 4."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "retire",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "plan-archive-001",
    ], input="n\n")

    assert result.exit_code == 4

def test_m365_exchange_retire_no_plan_shows_help() -> None:
    """m365 exchange retire without --plan should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "retire", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0
    assert "Usage" in result.output

def test_m365_exchange_change_plan_search_mode_active_workload() -> None:
    """change-plan on an active workload resolves --plan against Protection Plans by name."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "change-plan", "alice@contoso.com", "-t", TENANT_ID,
        "--plan", "M365 Daily", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.plans.get_by_name.assert_called_once_with("M365 Daily")
    mock_apm.plans.get.assert_not_called()
    mock_apm.m365.workloads.change_plan.assert_called_once_with(SAMPLE_WL, SAMPLE_PLAN)
    assert "Plan changed" in result.output


def test_m365_exchange_change_plan_search_mode_retired_workload() -> None:
    """change-plan --retired resolves --plan against Retirement Plans for an already-retired workload."""
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_by_name.return_value = SAMPLE_WL_RETIRED

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "change-plan", "alice@contoso.com", "-t", TENANT_ID,
        "--retired", "--plan", "30-Day Archive", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.retirement_plans.get_by_name.assert_called_once_with("30-Day Archive")
    mock_apm.retirement_plans.get.assert_not_called()
    mock_apm.m365.workloads.change_plan.assert_called_once_with(SAMPLE_WL_RETIRED, SAMPLE_RETIREMENT_PLAN)


def test_m365_exchange_change_plan_direct_mode() -> None:
    """change-plan --id/--namespace resolves the workload via get() (direct mode)."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "change-plan",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
        "--plan", "m365-plan-001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get.assert_called_once_with(
        WORKLOAD_UID, NAMESPACE, tenant_id=TENANT_ID, workload_type=M365WorkloadType.EXCHANGE
    )
    mock_apm.m365.workloads.change_plan.assert_called_once_with(SAMPLE_WL, SAMPLE_PLAN)


def test_m365_exchange_change_plan_resolves_plan_by_uuid() -> None:
    """--plan with a UUID resolves via plans.get() (direct mode), never calls plans.get_by_name()."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "change-plan", "alice@contoso.com", "-t", TENANT_ID,
        "--plan", "0c8f033b-1111-1111-1111-000000000001", "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.plans.get.assert_called_once_with("0c8f033b-1111-1111-1111-000000000001")
    mock_apm.plans.get_by_name.assert_not_called()


def test_m365_exchange_change_plan_abort_exits_4() -> None:
    """change-plan (without --yes) → user declines → exit 4."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "change-plan", "alice@contoso.com", "-t", TENANT_ID,
        "--plan", "M365 Daily",
    ], input="n\n")

    assert result.exit_code == 4
    mock_apm.m365.workloads.change_plan.assert_not_called()


def test_m365_exchange_change_plan_no_plan_shows_help() -> None:
    """m365 exchange change-plan without --plan should show help and exit 0."""
    result = invoke_cli(AsyncMock(), ["m365", "exchange", "change-plan", "--id", WORKLOAD_UID, "--namespace", NAMESPACE])

    assert result.exit_code == 0
    assert "Usage" in result.output


def test_m365_exchange_cancel_abort_exits_4() -> None:
    """m365 exchange cancel (without --yes) → user declines → exit 4."""
    mock_apm = make_mock_apm()

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "cancel",
        "--id", WORKLOAD_UID, "--namespace", NAMESPACE,
    ], input="n\n")

    assert result.exit_code == 4


SAMPLE_WL_2 = dataclasses.replace(SAMPLE_WL, workload_id="m365-wl-id-002", name="bob@contoso.com")


def test_m365_exchange_list_page_all_table_combines_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """m365 exchange list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.list.side_effect = [
        ([SAMPLE_WL], 2),
        ([SAMPLE_WL_2], 2),
    ]

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "list", "-t", TENANT_ID,
        "--limit", "1", "--page-all",
    ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "alice@contoso.com" in result.output
    assert "bob@contoso.com" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.m365.workloads.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.m365.workloads.list.call_args_list[1].kwargs["offset"] == 1


def test_m365_exchange_retire_search_mode_already_retired_errors() -> None:
    """retire by name errors out when the workload is only found among retired workloads."""
    from synology_apm.sdk.exceptions import ResourceNotFoundError as _NotFound

    mock_apm = make_mock_apm()

    async def _get_by_name(name: str, tenant_id: str, workload_type: M365WorkloadType, is_retired: bool = False) -> M365Workload:
        if is_retired:
            return SAMPLE_WL_RETIRED
        raise _NotFound("not found", resource_type="M365Workload", resource_id=name)

    mock_apm.m365.workloads.get_by_name = AsyncMock(side_effect=_get_by_name)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "retire", "alice@contoso.com",
        "-t", TENANT_ID, "--plan", "30-Day Archive", "--yes",
    ])

    assert result.exit_code == 1
    assert "already retired" in result.output
    mock_apm.m365.workloads.retire.assert_not_called()


def test_m365_exchange_list_table_without_tenant_header_when_tenant_missing() -> None:
    """list omits the tenant header when tenant details cannot be resolved."""
    mock_apm = make_mock_apm()
    mock_apm.saas.get_m365_tenant.return_value = None

    result = invoke_cli(mock_apm, ["m365", "exchange", "list", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    assert "Tenant:" not in result.output
    assert "alice@contoso.com" in result.output


def test_m365_exchange_get_shows_copy_size_when_present() -> None:
    """m365 get renders the Copy Size line when backup copy data exists."""
    wl = dataclasses.replace(SAMPLE_WL, backup_copy_data_bytes=1024**3)
    mock_apm = make_mock_apm()
    mock_apm.m365.workloads.get_by_name.return_value = wl

    result = invoke_cli(mock_apm, ["m365", "exchange", "get", "alice@contoso.com", "-t", TENANT_ID])

    assert result.exit_code == 0, result.output
    copy_line = next(line for line in result.output.splitlines() if "Copy Size:" in line)
    assert "1.0 GB" in copy_line
