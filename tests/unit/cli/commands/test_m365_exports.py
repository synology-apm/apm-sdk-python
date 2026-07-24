"""Unit tests for apm m365 exchange/group export commands: list/cancel/download."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synology_apm.sdk import M365ExportStartResult
from synology_apm.sdk.enums import M365WorkloadType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.workload import M365GroupInfo, M365Workload
from tests.unit.cli.commands._m365_fixtures import (
    NAMESPACE,
    SAMPLE_TENANT,
    SAMPLE_WL,
    TENANT_ID,
    WORKLOAD_ID,
    make_mock_apm,
)
from tests.unit.cli.conftest import invoke_cli

GROUP_WL_ID = "fd53ac91-392a-4abc-af42-1afc9df367a9"

GROUP_NAMESPACE = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"

SAMPLE_GROUP_WL = M365Workload(
    workload_id=GROUP_WL_ID,
    name="Marketing",
    category=WorkloadCategory.M365,
    namespace=GROUP_NAMESPACE,
    last_backup_at=None,
    is_retired=False,
    protected_data_bytes=0,
    status=WorkloadStatus.NO_BACKUPS,
    plan=ProtectionPlan(plan_id="plan-group-001", name="Test Plan", category=WorkloadCategory.M365),
    workload_type=M365WorkloadType.GROUP,
    tenant_id=TENANT_ID,
    info=M365GroupInfo(group_id="group-uuid-001", display_name="Marketing", mail="marketing@contoso.com"),
)

def make_mock_apm_with_export(group: bool = False) -> AsyncMock:
    """Build a mock APMClient with exchange_export or group_export pre-configured."""
    from synology_apm.sdk.enums import M365ExportStatus
    from synology_apm.sdk.models.activity import M365ExportActivity

    mock_apm = make_mock_apm()
    if group:
        mock_apm.m365.workloads.get.return_value = SAMPLE_GROUP_WL
    sample_activity = M365ExportActivity(
        activity_id="act-uuid-001",
        execution_id="188",
        namespace=GROUP_NAMESPACE if group else NAMESPACE,
        workload_id=GROUP_WL_ID if group else WORKLOAD_ID,
        workload_namespace=GROUP_NAMESPACE if group else NAMESPACE,
        source_name="Entire mailbox",
        is_archive_mail=False,
        status=M365ExportStatus.READY_TO_DOWNLOAD,
        started_at=None,
        finished_at=None,
        version_timestamp=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
    )
    mock_apm.m365.exchange_export.list.return_value = ([sample_activity], 1)
    mock_apm.m365.group_export.list.return_value = ([sample_activity], 1)
    return mock_apm


def test_m365_exchange_export_list_calls_exchange_export_collection() -> None:
    """apm m365 exchange export list should call exchange_export.list()."""
    mock_apm = make_mock_apm_with_export(group=False)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "list",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.exchange_export.list.assert_called_once()
    mock_apm.m365.group_export.list.assert_not_called()

def test_m365_exchange_export_cancel_calls_exchange_export_collection() -> None:
    """apm m365 exchange export cancel should look up activity and call exchange_export.cancel()."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.cancel.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "cancel",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "act-uuid-001",
    ])

    assert result.exit_code == 0, result.output
    # cancel() is called with the M365ExportActivity object (not raw strings)
    mock_apm.m365.exchange_export.cancel.assert_called_once()
    call_arg = mock_apm.m365.exchange_export.cancel.call_args[0][0]
    assert call_arg.activity_id == "act-uuid-001"
    mock_apm.m365.group_export.cancel.assert_not_called()

def test_m365_exchange_export_cancel_quiet_mode() -> None:
    """apm m365 exchange export cancel --quiet should cancel and produce no success output."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.cancel.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "cancel",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--tenant-id", TENANT_ID,
        "--id", "act-uuid-001", "--quiet",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.exchange_export.cancel.assert_called_once()
    assert result.output.strip() == ""

def test_m365_group_export_list_calls_group_export_collection() -> None:
    """apm m365 group export list should call group_export.list(), not exchange_export."""
    mock_apm = make_mock_apm_with_export(group=True)

    result = invoke_cli(mock_apm, [
        "m365", "group", "export", "list",
        "--workload-id", GROUP_WL_ID, "--namespace", GROUP_NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.group_export.list.assert_called_once()
    mock_apm.m365.exchange_export.list.assert_not_called()

def test_m365_group_export_cancel_calls_group_export_collection() -> None:
    """apm m365 group export cancel should look up activity and call group_export.cancel()."""
    mock_apm = make_mock_apm_with_export(group=True)
    mock_apm.m365.group_export.cancel.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "group", "export", "cancel",
        "--workload-id", GROUP_WL_ID, "--namespace", GROUP_NAMESPACE,
        "--id", "act-uuid-001",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.group_export.cancel.assert_called_once()
    call_arg = mock_apm.m365.group_export.cancel.call_args[0][0]
    assert call_arg.activity_id == "act-uuid-001"
    mock_apm.m365.exchange_export.cancel.assert_not_called()

def test_m365_group_export_download_direct_mode_calls_group_export(tmp_path: Path) -> None:
    """apm m365 group export download --id should look up activity and call get_download_url_by_activity()."""
    mock_apm = make_mock_apm_with_export(group=True)
    mock_apm.m365.group_export.get_download_url_by_activity.return_value = "https://apm.example/download/token"
    mock_apm.download_file.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "group", "export", "download",
        "--workload-id", GROUP_WL_ID, "--namespace", GROUP_NAMESPACE,
        "--id", "act-uuid-001", "--filename", str(tmp_path / "out.pst"),
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.group_export.get_download_url_by_activity.assert_called_once()
    call_arg = mock_apm.m365.group_export.get_download_url_by_activity.call_args[0][0]
    assert call_arg.activity_id == "act-uuid-001"
    mock_apm.m365.exchange_export.get_download_url_by_activity.assert_not_called()

def test_m365_group_export_download_no_archive_mailbox_option() -> None:
    """apm m365 group export download --help should not show --archive-mailbox."""
    result = invoke_cli(AsyncMock(), ["m365", "group", "export", "download", "--help"])
    assert "--archive-mailbox" not in result.output

def test_m365_group_export_list_table_has_item_version_columns() -> None:
    """apm m365 group export list (table output) must show Item and Version columns, not Archive."""
    mock_apm = make_mock_apm_with_export(group=True)

    result = invoke_cli(mock_apm, [
        "m365", "group", "export", "list",
        "--workload-id", GROUP_WL_ID, "--namespace", GROUP_NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    assert "Archive" not in result.output
    assert "Item" in result.output
    assert "Version" in result.output

def test_m365_group_export_list_json_has_version_timestamp_and_archive_field() -> None:
    """apm m365 group export list --output json must include version_timestamp and is_archive_mail."""
    mock_apm = make_mock_apm_with_export(group=True)

    result = invoke_cli(mock_apm, [
        "m365", "group", "export", "list",
        "--workload-id", GROUP_WL_ID, "--namespace", GROUP_NAMESPACE,
        "--output", "json",
    ])

    assert result.exit_code == 0, result.output
    records = json.loads(result.stdout)
    assert len(records) == 1
    assert "version_timestamp" in records[0]
    assert "is_archive_mail" in records[0]
    assert "item" in records[0]

def test_m365_exchange_export_list_table_has_item_version_columns() -> None:
    """apm m365 exchange export list (table output) must show Item and Version columns, not Archive."""
    mock_apm = make_mock_apm_with_export(group=False)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "list",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    assert "Archive" not in result.output
    assert "Item" in result.output
    assert "Version" in result.output

def test_m365_exchange_export_list_json_has_version_timestamp_and_archive_field() -> None:
    """apm m365 exchange export list --output json must include version_timestamp and is_archive_mail."""
    mock_apm = make_mock_apm_with_export(group=False)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "list",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--output", "json",
    ])

    assert result.exit_code == 0, result.output
    records = json.loads(result.stdout)
    assert len(records) == 1
    assert "version_timestamp" in records[0]
    assert "is_archive_mail" in records[0]
    assert "item" in records[0]

def test_m365_exchange_export_list_search_mode_resolves_by_name() -> None:
    """export list <name> should resolve the workload by name (search mode)."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.saas.list.return_value = ([SAMPLE_TENANT], 1)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "list",
        "alice@contoso.com", "-t", TENANT_ID,
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get_by_name.assert_called_once_with(
        "alice@contoso.com", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=False
    )

def test_m365_exchange_export_list_empty_shows_no_tasks_message() -> None:
    """export list with no results should print 'No export tasks found.'"""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.list.return_value = ([], 0)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "list",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
    ])

    assert result.exit_code == 0, result.output
    assert "No export tasks found" in result.output

def test_m365_exchange_export_cancel_without_id_shows_help() -> None:
    """export cancel without --id should show help and exit 0."""
    result = invoke_cli(AsyncMock(), [
        "m365", "exchange", "export", "cancel",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
    ])
    assert result.exit_code == 0
    assert "Usage" in result.output or "usage" in result.output.lower()

def test_m365_exchange_export_cancel_search_mode_resolves_by_name() -> None:
    """export cancel <name> --id should resolve workload by name (search mode)."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.cancel.return_value = None
    mock_apm.saas.list.return_value = ([SAMPLE_TENANT], 1)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "cancel",
        "alice@contoso.com", "-t", TENANT_ID, "--id", "act-uuid-001",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get_by_name.assert_called_once_with(
        "alice@contoso.com", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=False
    )
    mock_apm.m365.exchange_export.cancel.assert_called_once()
    assert mock_apm.m365.exchange_export.cancel.call_args[0][0].activity_id == "act-uuid-001"

def test_m365_exchange_export_download_search_mode_resolves_by_name(tmp_path: Path) -> None:
    """export download <name> --id should resolve workload by name and call get_download_url_by_activity()."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.get_download_url_by_activity.return_value = "https://apm.example/dl/token"
    mock_apm.download_file.return_value = None
    mock_apm.saas.list.return_value = ([SAMPLE_TENANT], 1)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "alice@contoso.com", "-t", TENANT_ID,
        "--id", "act-uuid-001", "--filename", str(tmp_path / "out.pst"),
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.workloads.get_by_name.assert_called_once_with(
        "alice@contoso.com", TENANT_ID, workload_type=M365WorkloadType.EXCHANGE, is_retired=False
    )

def test_m365_exchange_export_download_autostart_ready_to_download(tmp_path: Path) -> None:
    """export download without --id: when start returns ready_to_download=True, downloads immediately."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_version = MagicMock()
    mock_apm.m365.workloads.get_latest_version = AsyncMock(return_value=mock_version)
    start_result = M365ExportStartResult(
        execution_id="auto-exec-001",
        export_name="export.pst",
        ready_to_download=True,
        location=MagicMock(),
        workload=SAMPLE_WL,
        version=MagicMock(),
    )
    mock_apm.m365.exchange_export.start = AsyncMock(return_value=start_result)
    mock_apm.m365.exchange_export.get_download_url_by_ready_result = AsyncMock(
        return_value="https://apm.example/dl/token"
    )
    mock_apm.download_file.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--filename", str(tmp_path / "out.pst"), "--yes",
    ])

    assert result.exit_code == 0, result.output
    assert "(Using version:" in result.output
    mock_apm.m365.exchange_export.start.assert_called_once()
    mock_apm.download_file.assert_called_once()

def test_m365_exchange_export_download_autostart_with_version_id_skips_resolved_message(tmp_path: Path) -> None:
    """export download --version-id: calls get_version() and does not print '(Using version: ...)'."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_version = MagicMock()
    mock_apm.m365.workloads.get_version = AsyncMock(return_value=mock_version)
    start_result = M365ExportStartResult(
        execution_id="auto-exec-002",
        export_name="export.pst",
        ready_to_download=True,
        location=MagicMock(),
        workload=SAMPLE_WL,
        version=MagicMock(),
    )
    mock_apm.m365.exchange_export.start = AsyncMock(return_value=start_result)
    mock_apm.m365.exchange_export.get_download_url_by_ready_result = AsyncMock(
        return_value="https://apm.example/dl/token"
    )
    mock_apm.download_file.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--filename", str(tmp_path / "out.pst"), "--yes", "--version-id", "ver-m365-001",
    ])

    assert result.exit_code == 0, result.output
    assert "(Using version:" not in result.output
    mock_apm.m365.workloads.get_version.assert_called_once_with(SAMPLE_WL, "ver-m365-001")
    mock_apm.m365.workloads.get_latest_version.assert_not_called()

def test_m365_exchange_export_download_autostart_no_wait_exits_0() -> None:
    """export download --no-wait: when start returns ready_to_download=False, exits 0 with message."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_version = MagicMock()
    mock_apm.m365.workloads.get_latest_version = AsyncMock(return_value=mock_version)
    start_result = M365ExportStartResult(
        execution_id="async-exec-001",
        export_name="export.pst",
        ready_to_download=False,
        location=MagicMock(),
        workload=SAMPLE_WL,
        version=MagicMock(),
    )
    mock_apm.m365.exchange_export.start = AsyncMock(return_value=start_result)
    mock_apm.m365.exchange_export.get_activity_by_result = AsyncMock(return_value=None)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--filename", "out.pst", "--yes", "--no-wait",
    ])

    assert result.exit_code == 0, result.output
    assert "to get the Activity ID" in result.output  # fallback hint when no activity id is available

def test_m365_exchange_export_download_existing_file_overwrite_declined_exits_4(tmp_path: Path) -> None:
    """export download: when file exists and user declines overwrite, prints Cancelled. and exits 4."""
    mock_apm = make_mock_apm_with_export(group=False)
    dest = tmp_path / "out.pst"
    dest.write_bytes(b"existing content")

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "act-uuid-001", "--filename", str(dest),
    ], input="n\n")

    assert result.exit_code == 4
    assert "Cancelled." in result.output

def test_m365_exchange_export_download_oserror_exits_1(tmp_path: Path) -> None:
    """export download: when download_file raises OSError, exits 1 with error message."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.get_download_url_by_activity.return_value = "https://apm.example/dl/token"
    mock_apm.download_file.side_effect = OSError("disk full")

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "act-uuid-001", "--filename", str(tmp_path / "out.pst"),
    ])

    assert result.exit_code == 1
    assert "Download failed" in result.output or "disk full" in result.output

def test_m365_exchange_export_download_autostart_poll_ready_downloads(tmp_path: Path) -> None:
    """export download: when polling finds READY_TO_DOWNLOAD, downloads the file immediately."""
    from synology_apm.sdk.enums import M365ExportStatus
    from synology_apm.sdk.models.activity import M365ExportActivity

    mock_apm = make_mock_apm_with_export(group=False)
    mock_version = MagicMock()
    mock_apm.m365.workloads.get_latest_version = AsyncMock(return_value=mock_version)
    start_result = M365ExportStartResult(
        execution_id="poll-exec-001",
        export_name="export.pst",
        ready_to_download=False,
        location=MagicMock(),
        workload=SAMPLE_WL,
        version=MagicMock(),
    )
    mock_apm.m365.exchange_export.start = AsyncMock(return_value=start_result)
    poll_activity = M365ExportActivity(
        activity_id="act-poll-001",
        execution_id="poll-exec-001",
        namespace=NAMESPACE,
        workload_id=WORKLOAD_ID,
        workload_namespace=NAMESPACE,
        source_name="Entire mailbox",
        is_archive_mail=False,
        status=M365ExportStatus.READY_TO_DOWNLOAD,
        started_at=None,
        finished_at=None,
        version_timestamp=None,
    )
    mock_apm.m365.exchange_export.get_activity_by_result = AsyncMock(return_value=poll_activity)
    mock_apm.m365.exchange_export.get_download_url_by_ready_result = AsyncMock(
        return_value="https://apm.test/dl/token"
    )
    mock_apm.download_file.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--filename", str(tmp_path / "out.pst"), "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.exchange_export.start.assert_called_once()
    mock_apm.download_file.assert_called_once()

def test_m365_exchange_export_download_autostart_poll_expired_exits_1() -> None:
    """export download: when polling returns EXPIRED (non-downloadable) status, exits 1."""
    from synology_apm.sdk.enums import M365ExportStatus
    from synology_apm.sdk.models.activity import M365ExportActivity

    mock_apm = make_mock_apm_with_export(group=False)
    mock_version = MagicMock()
    mock_apm.m365.workloads.get_latest_version = AsyncMock(return_value=mock_version)
    start_result = M365ExportStartResult(
        execution_id="poll-exec-002",
        export_name="export.pst",
        ready_to_download=False,
        location=MagicMock(),
        workload=SAMPLE_WL,
        version=MagicMock(),
    )
    mock_apm.m365.exchange_export.start = AsyncMock(return_value=start_result)
    expired_activity = M365ExportActivity(
        activity_id="act-poll-002",
        execution_id="poll-exec-002",
        namespace=NAMESPACE,
        workload_id=WORKLOAD_ID,
        workload_namespace=NAMESPACE,
        source_name="Entire mailbox",
        is_archive_mail=False,
        status=M365ExportStatus.EXPIRED,
        started_at=None,
        finished_at=None,
        version_timestamp=None,
    )
    mock_apm.m365.exchange_export.get_activity_by_result = AsyncMock(return_value=expired_activity)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--filename", "out.pst", "--yes",
    ])

    assert result.exit_code == 1
    assert "Expired" in result.output or "expired" in result.output.lower()

def test_m365_exchange_export_list_page_all_combines_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """m365 exchange export list --page-all --limit 1 should fetch every page and render one combined table."""
    from synology_apm.sdk.enums import M365ExportStatus
    from synology_apm.sdk.models.activity import M365ExportActivity

    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    mock_apm = make_mock_apm_with_export(group=False)
    activity_1 = M365ExportActivity(
        activity_id="act-uuid-001",
        execution_id="188",
        namespace=NAMESPACE,
        workload_id=WORKLOAD_ID,
        workload_namespace=NAMESPACE,
        source_name="Entire mailbox",
        is_archive_mail=False,
        status=M365ExportStatus.READY_TO_DOWNLOAD,
        started_at=None,
        finished_at=None,
        version_timestamp=None,
    )
    activity_2 = dataclasses.replace(activity_1, activity_id="act-uuid-002", source_name="Archive mailbox")
    mock_apm.m365.exchange_export.list.side_effect = [
        ([activity_1], 2),
        ([activity_2], 2),
    ]

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "list",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--limit", "1", "--page-all",
    ])

    assert result.exit_code == 0, result.output
    assert "act-uuid-001" in result.output
    assert "act-uuid-002" in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.m365.exchange_export.list.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.m365.exchange_export.list.call_args_list[1].kwargs["offset"] == 1


def test_m365_exchange_export_cancel_unknown_id_exits_1() -> None:
    """export cancel --id with an unknown activity id exits 1 without calling cancel()."""
    mock_apm = make_mock_apm_with_export(group=False)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "cancel",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "no-such-id",
    ])

    assert result.exit_code == 1
    assert "not found" in result.output
    mock_apm.m365.exchange_export.cancel.assert_not_called()

def test_m365_group_export_download_autostart_uses_group_collection(tmp_path: Path) -> None:
    """group export download without --id starts the export via group_export, not exchange_export."""
    mock_apm = make_mock_apm_with_export(group=True)
    mock_version = MagicMock()
    mock_apm.m365.workloads.get_latest_version = AsyncMock(return_value=mock_version)
    start_result = M365ExportStartResult(
        execution_id="group-exec-001",
        export_name="export.pst",
        ready_to_download=True,
        location=MagicMock(),
        workload=SAMPLE_GROUP_WL,
        version=MagicMock(),
    )
    mock_apm.m365.group_export.start = AsyncMock(return_value=start_result)
    mock_apm.m365.group_export.get_download_url_by_ready_result = AsyncMock(
        return_value="https://apm.example/dl/token"
    )
    mock_apm.download_file.return_value = None

    result = invoke_cli(mock_apm, [
        "m365", "group", "export", "download",
        "--workload-id", GROUP_WL_ID, "--namespace", GROUP_NAMESPACE,
        "--filename", str(tmp_path / "out.pst"), "--yes",
    ])

    assert result.exit_code == 0, result.output
    mock_apm.m365.group_export.start.assert_called_once()
    mock_apm.m365.exchange_export.start.assert_not_called()
    mock_apm.download_file.assert_called_once()

def test_m365_exchange_export_download_no_wait_shows_activity_id_when_found() -> None:
    """export download --no-wait prints the activity id when the started export is resolvable."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_version = MagicMock()
    mock_apm.m365.workloads.get_latest_version = AsyncMock(return_value=mock_version)
    start_result = M365ExportStartResult(
        execution_id="async-exec-002",
        export_name="export.pst",
        ready_to_download=False,
        location=MagicMock(),
        workload=SAMPLE_WL,
        version=MagicMock(),
    )
    mock_apm.m365.exchange_export.start = AsyncMock(return_value=start_result)
    mock_apm.m365.exchange_export.get_activity_by_result = AsyncMock(
        return_value=MagicMock(activity_id="act-uuid-777")
    )

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--filename", "out.pst", "--yes", "--no-wait",
    ])

    assert result.exit_code == 0, result.output
    assert "Activity ID: act-uuid-777" in result.output

def test_m365_exchange_export_download_direct_mode_unknown_id_exits_1(tmp_path: Path) -> None:
    """export download --id with an unknown activity id exits 1 before requesting a URL."""
    mock_apm = make_mock_apm_with_export(group=False)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "no-such-id", "--filename", str(tmp_path / "out.pst"),
    ])

    assert result.exit_code == 1
    assert "not found" in result.output
    mock_apm.m365.exchange_export.get_download_url_by_activity.assert_not_called()

def test_m365_exchange_export_download_reports_progress(tmp_path: Path) -> None:
    """export download forwards download progress callbacks without error."""
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.get_download_url_by_activity.return_value = "https://apm.example/dl/token"

    async def _fake_download(url: str, dest_path: str, on_progress: Any = None) -> None:
        if on_progress is not None:
            on_progress(10, 100)
            on_progress(100, 100)

    mock_apm.download_file = AsyncMock(side_effect=_fake_download)

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "act-uuid-001", "--filename", str(tmp_path / "out.pst"),
    ])

    assert result.exit_code == 0, result.output
    assert "Saved to" in result.output

def test_m365_exchange_export_download_oserror_leaves_part_cleanup_to_sdk(tmp_path: Path) -> None:
    """On download failure the CLI must not touch the filesystem itself.

    The SDK stages into a .part file and cleans it up on failure (dest is never
    written), so the CLI's only job is to report the error and exit non-zero. It
    must not unlink dest_path — doing so would delete a pre-existing good file.
    """
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.get_download_url_by_activity.return_value = "https://apm.example/dl/token"

    async def _fail_download(url: str, dest_path: str, on_progress: Any = None) -> None:
        # Mirror the real SDK: stage into .part, remove it on failure, never touch dest.
        part = Path(dest_path + ".part")
        part.write_bytes(b"partial")
        part.unlink()
        raise OSError("disk full")

    mock_apm.download_file = AsyncMock(side_effect=_fail_download)
    dest = tmp_path / "out.pst"

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "act-uuid-001", "--filename", str(dest), "--yes",
    ])

    assert result.exit_code == 1
    assert "Download failed" in result.output
    assert not dest.exists()
    assert not Path(str(dest) + ".part").exists()


def test_m365_exchange_export_download_oserror_preserves_existing_file(tmp_path: Path) -> None:
    """A failed download must NOT delete a pre-existing file at the destination path."""
    dest = tmp_path / "out.pst"
    dest.write_bytes(b"previous good backup")
    mock_apm = make_mock_apm_with_export(group=False)
    mock_apm.m365.exchange_export.get_download_url_by_activity.return_value = "https://apm.example/dl/token"
    mock_apm.download_file.side_effect = OSError("disk full")

    result = invoke_cli(mock_apm, [
        "m365", "exchange", "export", "download",
        "--workload-id", WORKLOAD_ID, "--namespace", NAMESPACE,
        "--id", "act-uuid-001", "--filename", str(dest), "--yes",
    ])

    assert result.exit_code == 1
    # The CLI must not have unlinked the user's existing file on failure.
    assert dest.exists()
    assert dest.read_bytes() == b"previous good backup"
