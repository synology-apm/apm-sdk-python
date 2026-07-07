"""Unit tests for synology_apm.cli._helpers, _display, _validate, and m365_export pure functions."""
from __future__ import annotations

import datetime as _dt
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
import typer

from synology_apm.cli._display import (
    fmt_backup_activity_status,
    fmt_backup_copy,
    fmt_bytes,
    fmt_datetime,
    fmt_duration,
    fmt_location_name,
    fmt_restore_activity_status,
    fmt_retention,
    fmt_verify_status,
    fmt_version_status,
    print_list_footer,
)
from synology_apm.cli._helpers import enable_debug, is_debug
from synology_apm.cli._validate import WorkloadRef, parse_time_filter
from synology_apm.cli.errors import EXIT_ERROR
from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    M365WorkloadType,
    RestoreActivityStatus,
    RetentionType,
    VerifyStatus,
    VersionStatus,
    WorkloadCategory,
)
from synology_apm.sdk.models.activity import BackupActivity, RestoreActivity
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import GFSRetention, ProtectionRetentionPolicy
from synology_apm.sdk.models.saas import SaasTenant
from synology_apm.sdk.models.version import VersionLocation

# ── fmt_bytes ─────────────────────────────────────────────────────────────────

def test_fmt_bytes_zero() -> None:
    assert fmt_bytes(0) == "0 B"

def test_fmt_bytes_bytes() -> None:
    assert fmt_bytes(512) == "512.0 B"

def test_fmt_bytes_kilobytes() -> None:
    assert fmt_bytes(2048) == "2.0 KB"

def test_fmt_bytes_megabytes() -> None:
    assert fmt_bytes(5 * 1024 ** 2) == "5.0 MB"

def test_fmt_bytes_gigabytes() -> None:
    assert fmt_bytes(3 * 1024 ** 3) == "3.0 GB"

def test_fmt_bytes_terabytes() -> None:
    assert fmt_bytes(10 * 1024 ** 4) == "10.0 TB"

def test_fmt_bytes_none() -> None:
    assert fmt_bytes(None) == "-"


# ── fmt_datetime ──────────────────────────────────────────────────────────────

def test_fmt_datetime_none() -> None:
    assert fmt_datetime(None) == "-"

def test_fmt_datetime_with_value() -> None:
    dt = datetime(2026, 4, 21, 9, 30, tzinfo=UTC)
    result = fmt_datetime(dt)
    assert "2026" in result
    assert ":" in result


# ── fmt_duration ──────────────────────────────────────────────────────────────

def test_fmt_duration_none() -> None:
    assert fmt_duration(None) == "-"

def test_fmt_duration_negative() -> None:
    assert fmt_duration(-1) == "-"

def test_fmt_duration_under_one_minute() -> None:
    assert fmt_duration(45) == "0:45"

def test_fmt_duration_one_minute() -> None:
    assert fmt_duration(60) == "1:00"

def test_fmt_duration_minutes_seconds() -> None:
    assert fmt_duration(125) == "2:05"

def test_fmt_duration_with_hours() -> None:
    assert fmt_duration(3661) == "1:01:01"

def test_fmt_duration_exactly_one_hour() -> None:
    assert fmt_duration(3600) == "1:00:00"


# ── fmt_retention ─────────────────────────────────────────────────────────────

def test_fmt_retention_keep_all() -> None:
    r = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ALL)
    assert fmt_retention(r) == "Keep all"

def test_fmt_retention_keep_days_singular() -> None:
    r = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=1)
    assert fmt_retention(r) == "1 day"

def test_fmt_retention_keep_days_plural() -> None:
    r = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30)
    assert fmt_retention(r) == "30 days"

def test_fmt_retention_keep_versions() -> None:
    r = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=10)
    assert fmt_retention(r) == "10 versions"

def test_fmt_retention_keep_advanced() -> None:
    r = ProtectionRetentionPolicy(
        retention_type=RetentionType.KEEP_ADVANCED,
        gfs=GFSRetention(daily_versions=7, weekly_versions=4, monthly_versions=12, yearly_versions=1),
    )
    assert fmt_retention(r) == "Advanced rules"

def test_fmt_retention_fallback_returns_dash() -> None:
    r = ProtectionRetentionPolicy(retention_type=RetentionType.NONE)
    assert fmt_retention(r) == "-"


# ── fmt_version_status ────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected_fragment", [
    (VersionStatus.SUCCESS,     "Success"),
    (VersionStatus.FAILED,        "Failed"),
    (VersionStatus.PARTIAL,       "Partial"),
    (VersionStatus.BACKING_UP,    "Backing up"),
    (VersionStatus.CANCELED,      "⊘ Canceled"),
    (VersionStatus.PAUSED,        "Paused"),
    (VersionStatus.DELETING,      "Deleting"),
    (VersionStatus.DELETE_FAILED, "Delete Failed"),
    (VersionStatus.NO_BACKUPS,    "-"),
])
def test_fmt_version_status(status: VersionStatus, expected_fragment: str) -> None:
    result = fmt_version_status(status)
    assert expected_fragment in result


# ── fmt_verify_status ─────────────────────────────────────────────────────────

def test_fmt_verify_status_none_returns_dash() -> None:
    assert fmt_verify_status(None) == "-"

@pytest.mark.parametrize("status,expected_fragment", [
    (VerifyStatus.SUCCESS,       "Success"),
    (VerifyStatus.FAILED,        "Failed"),
    (VerifyStatus.PARTIAL,       "Partial"),
    (VerifyStatus.CANCELED,      "⊘ Canceled"),
    (VerifyStatus.VERIFYING,     "Verifying"),
    (VerifyStatus.WAITING,       "Waiting"),
    (VerifyStatus.NOT_SUPPORTED, "Unable to perform"),
    (VerifyStatus.NOT_ENABLED,   "Not enabled"),
])
def test_fmt_verify_status(status: VerifyStatus, expected_fragment: str) -> None:
    result = fmt_verify_status(status)
    assert expected_fragment in result


# ── fmt_backup_activity_status / fmt_restore_activity_status ─────────────────

def _make_backup_activity(
    status: BackupActivityStatus,
    progress: int = 0,
    processed_success_count: int | None = None,
) -> BackupActivity:
    return BackupActivity(
        activity_id="act-001",
        execution_id="exec-001",
        namespace="ns-001",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_VM,
        workload_id="wl-001",
        workload_namespace="ns-001",
        workload_name="vm-web-01",
        plan_name="Daily Backup",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=None,
        duration_seconds=None,
        data_transferred_bytes=None,
        progress=progress,
        status=status,
        processed_success_count=processed_success_count,
    )


def _make_restore_activity(
    status: RestoreActivityStatus,
    progress: int = 0,
    processed_success_count: int | None = None,
) -> RestoreActivity:
    return RestoreActivity(
        activity_id="act-002",
        execution_id="exec-002",
        namespace="ns-001",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_VM,
        workload_id="wl-001",
        workload_namespace="ns-001",
        workload_name="vm-web-01",
        plan_name="Daily Backup",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=None,
        duration_seconds=None,
        data_transferred_bytes=None,
        progress=progress,
        status=status,
        processed_success_count=processed_success_count,
    )


@pytest.mark.parametrize("status,progress,success_count,expected_fragment", [
    (BackupActivityStatus.QUEUING,    0,  None, "Waiting"),
    (BackupActivityStatus.BACKING_UP, 0,  None, "Backing up"),
    (BackupActivityStatus.BACKING_UP, 42, None, "42%"),
    (BackupActivityStatus.BACKING_UP, 0,  30,   "30 items"),
    (BackupActivityStatus.SUCCESS,    0,  None, "Success"),
    (BackupActivityStatus.FAILED,     0,  None, "Failed"),
    (BackupActivityStatus.PARTIAL,    0,  None, "Partial"),
    (BackupActivityStatus.CANCELED,   0,  None, "⊘ Canceled"),
])
def test_fmt_backup_activity_status(
    status: BackupActivityStatus,
    progress: int,
    success_count: int | None,
    expected_fragment: str,
) -> None:
    act = _make_backup_activity(status, progress=progress, processed_success_count=success_count)
    assert expected_fragment in fmt_backup_activity_status(act)


@pytest.mark.parametrize("status,progress,success_count,expected_fragment", [
    (RestoreActivityStatus.PREPARING, 0, None, "Preparing"),
    (RestoreActivityStatus.RESTORING, 0, None, "Restoring"),
    (RestoreActivityStatus.RESTORING, 0, 5,    "5 items"),
])
def test_fmt_restore_activity_status(
    status: RestoreActivityStatus,
    progress: int,
    success_count: int | None,
    expected_fragment: str,
) -> None:
    act = _make_restore_activity(status, progress=progress, processed_success_count=success_count)
    assert expected_fragment in fmt_restore_activity_status(act)


# ── parse_time_filter ───────────────────────────────────────────────────────────────

def test_parse_time_filter_hours() -> None:
    result = parse_time_filter("2h")
    diff = datetime.now(tz=UTC) - result
    assert 1.9 * 3600 < diff.total_seconds() < 2.1 * 3600

def test_parse_time_filter_days() -> None:
    result = parse_time_filter("7d")
    diff = datetime.now(tz=UTC) - result
    assert 6.9 * 86400 < diff.total_seconds() < 7.1 * 86400

def test_parse_time_filter_minutes() -> None:
    result = parse_time_filter("30m")
    diff = datetime.now(tz=UTC) - result
    assert 29 * 60 < diff.total_seconds() < 31 * 60

def test_parse_time_filter_iso8601() -> None:
    result = parse_time_filter("2026-04-21T00:00:00+00:00")
    assert result == datetime(2026, 4, 21, 0, 0, tzinfo=UTC)

def test_parse_time_filter_iso8601_naive_assumes_utc() -> None:
    result = parse_time_filter("2026-04-21")
    assert result == datetime(2026, 4, 21, 0, 0, tzinfo=UTC)

def test_parse_time_filter_invalid_raises_bad_parameter() -> None:
    with pytest.raises(typer.BadParameter):
        parse_time_filter("not-a-date")


# ── WorkloadRef.resolve_machine / resolve_m365 ───────────────────────────────

async def test_resolve_machine_direct_mode_calls_get() -> None:
    """A WorkloadRef with namespace set (direct mode) should dispatch to get()."""
    sentinel: Any = "machine-wl"
    apm = AsyncMock()
    apm.machine.workloads.get.return_value = sentinel
    ref = WorkloadRef("wl-id-001", "ns-001", True)

    result = await ref.resolve_machine(apm)

    assert result == sentinel
    apm.machine.workloads.get.assert_called_once_with("wl-id-001", namespace="ns-001")
    apm.machine.workloads.get_by_name.assert_not_called()


async def test_resolve_machine_search_mode_calls_get_by_name() -> None:
    """A WorkloadRef without namespace (search mode) should dispatch to get_by_name()."""
    sentinel: Any = "machine-wl"
    apm = AsyncMock()
    apm.machine.workloads.get_by_name.return_value = sentinel
    ref = WorkloadRef("CORP-PC-001", None, False)

    result = await ref.resolve_machine(apm, is_retired=True)

    assert result == sentinel
    apm.machine.workloads.get_by_name.assert_called_once_with("CORP-PC-001", is_retired=True)
    apm.machine.workloads.get.assert_not_called()


async def test_resolve_m365_direct_mode_calls_get() -> None:
    """A WorkloadRef with namespace set (direct mode) should dispatch to get()."""
    sentinel: Any = "m365-wl"
    apm = AsyncMock()
    apm.m365.workloads.get.return_value = sentinel
    ref = WorkloadRef("wl-uid", "ns-1", True)

    result = await ref.resolve_m365(apm, "tid-1", M365WorkloadType.EXCHANGE)

    assert result == sentinel
    apm.m365.workloads.get.assert_called_once_with(
        "wl-uid", "ns-1", tenant_id="tid-1", workload_type=M365WorkloadType.EXCHANGE
    )
    apm.m365.workloads.get_by_name.assert_not_called()


async def test_resolve_m365_search_mode_calls_get_by_name() -> None:
    """A WorkloadRef without namespace (search mode) should dispatch to get_by_name()."""
    sentinel: Any = "m365-wl"
    apm = AsyncMock()
    apm.m365.workloads.get_by_name.return_value = sentinel
    ref = WorkloadRef("alice@contoso.com", None, False)

    result = await ref.resolve_m365(apm, "tid-1", M365WorkloadType.EXCHANGE, is_retired=True)

    assert result == sentinel
    apm.m365.workloads.get_by_name.assert_called_once_with(
        "alice@contoso.com", "tid-1", workload_type=M365WorkloadType.EXCHANGE, is_retired=True
    )
    apm.m365.workloads.get.assert_not_called()


async def test_resolve_m365_auto_resolves_tenant_when_none() -> None:
    """resolve_m365() with tenant_id=None falls back to the first M365 tenant from saas.list()."""
    sentinel: Any = "m365-wl"
    apm = AsyncMock()
    apm.saas.list.return_value = (
        [
            SaasTenant(
                tenant_id="auto-tid",
                tenant_name="Contoso",
                tenant_email="admin@contoso.com",
                category=WorkloadCategory.M365,
                protected_data_bytes=0,
            )
        ],
        1,
    )
    apm.m365.workloads.get_by_name.return_value = sentinel
    ref = WorkloadRef("alice@contoso.com", None, False)

    result = await ref.resolve_m365(apm, None, M365WorkloadType.EXCHANGE)

    assert result == sentinel
    apm.m365.workloads.get_by_name.assert_called_once_with(
        "alice@contoso.com", "auto-tid", workload_type=M365WorkloadType.EXCHANGE, is_retired=False
    )


# ── print_list_footer ──────────────────────────────────────────────────────────


class _FakeConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: str) -> None:
        self.lines.append(text)


def test_print_list_footer_shows_n_of_total() -> None:
    c = _FakeConsole()
    print_list_footer(c, 25, 153)  # type: ignore[arg-type]
    assert c.lines == ["[dim]Showing 25 of 153[/dim]"]


def test_print_list_footer_with_offset() -> None:
    c = _FakeConsole()
    print_list_footer(c, 25, 100, offset=25)  # type: ignore[arg-type]
    assert c.lines == ["[dim]Showing 26–50 of 100[/dim]"]


def test_print_list_footer_shows_when_n_equals_total() -> None:
    """Should still display when n == total so the user can confirm the total count."""
    c = _FakeConsole()
    print_list_footer(c, 3, 3)  # type: ignore[arg-type]
    assert c.lines == ["[dim]Showing 3 of 3[/dim]"]


def test_print_list_footer_unknown_total_shows_count_only() -> None:
    """When total is unknown (None or negative), show just the shown count, no 'of N'."""
    c = _FakeConsole()
    print_list_footer(c, 25, -1)  # type: ignore[arg-type]
    assert c.lines == ["[dim]Showing 25[/dim]"]
    c = _FakeConsole()
    print_list_footer(c, 25, None)  # type: ignore[arg-type]
    assert c.lines == ["[dim]Showing 25[/dim]"]


# ── _auto_download_filename / _auto_download_filename_by_id ──────────────

_TODAY = _dt.date.today().strftime("%Y%m%d")


def test_auto_download_filename_primary_mailbox() -> None:
    from synology_apm.cli.commands.m365_export import _auto_download_filename
    assert _auto_download_filename("jon snow", archive_mailbox=False) == f"jon_snow_{_TODAY}_mailbox.pst"


def test_auto_download_filename_archive_mailbox() -> None:
    from synology_apm.cli.commands.m365_export import _auto_download_filename
    assert _auto_download_filename("alice@contoso.com", archive_mailbox=True) == (
        f"alice_contoso.com_{_TODAY}_archive_mailbox.pst"
    )


def test_auto_download_filename_special_chars_replaced() -> None:
    from synology_apm.cli.commands.m365_export import _auto_download_filename
    assert _auto_download_filename("Alice (Admin)", archive_mailbox=False) == (
        f"Alice__Admin_{_TODAY}_mailbox.pst"
    )


def test_auto_download_filename_empty_name_falls_back() -> None:
    from synology_apm.cli.commands.m365_export import _auto_download_filename
    assert _auto_download_filename("", archive_mailbox=False) == f"export_{_TODAY}_mailbox.pst"


def test_auto_download_filename_by_id_uses_execution_id() -> None:
    from synology_apm.cli.commands.m365_export import _auto_download_filename_by_id
    assert _auto_download_filename_by_id("jon snow", "192") == "jon_snow_192.pst"


def test_auto_download_filename_by_id_empty_name_falls_back() -> None:
    from synology_apm.cli.commands.m365_export import _auto_download_filename_by_id
    assert _auto_download_filename_by_id("", "99") == "export_99.pst"


# ── fmt_bytes — PB range ──────────────────────────────────────────────────────

def test_fmt_bytes_petabytes() -> None:
    assert fmt_bytes(10 * 1024 ** 5) == "10.0 PB"


# ── enable_debug / is_debug ───────────────────────────────────────────────────

def test_enable_debug_sets_flag_and_is_debug_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import synology_apm.cli._helpers as _h
    monkeypatch.setattr(_h, "_debug_mode", False)
    assert is_debug() is False
    enable_debug()
    assert is_debug() is True


# ── get_client() ─────────────────────────────────────────────────────────────


async def test_get_client_no_input_missing_password_exits_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client() with no_input=True and no password should exit with EXIT_ERROR code."""
    import synology_apm.cli._helpers as _h
    monkeypatch.setattr(_h, "_debug_mode", False)

    mock_ctx = MagicMock()
    mock_ctx.obj = {
        "host": "apm.test", "username": "admin",
        "password": None, "profile": None,
        "no_input": True, "no_verify_ssl": False,
    }

    with patch("synology_apm.cli._helpers.resolve_connection", return_value=("apm.test", "admin", "", False)):
        with pytest.raises(click.exceptions.Exit) as exc_info:
            async with _h.get_client(mock_ctx):
                pass  # pragma: no cover

    assert exc_info.value.exit_code == EXIT_ERROR


async def test_get_client_missing_host_calls_missing_config_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client() with empty host calls missing_config_hint (NoReturn)."""
    import synology_apm.cli._helpers as _h
    monkeypatch.setattr(_h, "_debug_mode", False)

    mock_ctx = MagicMock()
    mock_ctx.obj = {}

    mock_hint = MagicMock(side_effect=SystemExit(2))

    with patch("synology_apm.cli._helpers.resolve_connection", return_value=("", "", "", False)):
        with patch("synology_apm.cli._helpers.missing_config_hint", mock_hint):
            with pytest.raises(SystemExit):
                async with _h.get_client(mock_ctx):
                    pass  # pragma: no cover

    mock_hint.assert_called_once()


async def test_get_client_debug_flag_enables_debug_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client() with debug=True in ctx.obj calls enable_debug()."""
    import synology_apm.cli._helpers as _h
    monkeypatch.setattr(_h, "_debug_mode", False)

    mock_ctx = MagicMock()
    mock_ctx.obj = {"debug": True, "host": "h", "username": "u", "password": "p",
                    "profile": None, "no_input": False, "no_verify_ssl": False}

    mock_apm = AsyncMock()
    mock_apm.my_server.name = "APM-Server"
    mock_apm.my_server.system_version = "1.2"

    fake_ctx_mgr = AsyncMock()
    fake_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_apm)
    fake_ctx_mgr.__aexit__ = AsyncMock(return_value=None)

    with patch("synology_apm.cli._helpers.resolve_connection", return_value=("h", "u", "p", False)):
        with patch("synology_apm.cli._helpers.APMClient", return_value=fake_ctx_mgr):
            with patch("synology_apm.cli._helpers.enable_debug") as mock_enable:
                async with _h.get_client(mock_ctx):
                    pass

    mock_enable.assert_called_once()


async def test_get_client_yields_apm_client_with_connection_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client() yields the APMClient and prints the connected server name."""
    import synology_apm.cli._helpers as _h
    monkeypatch.setattr(_h, "_debug_mode", False)

    mock_ctx = MagicMock()
    mock_ctx.obj = {"host": "h", "username": "u", "password": "p",
                    "profile": None, "no_input": False, "no_verify_ssl": False}

    mock_apm = AsyncMock()
    mock_apm.my_server.name = "apm-server-01"
    mock_apm.my_server.system_version = ""

    fake_ctx_mgr = AsyncMock()
    fake_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_apm)
    fake_ctx_mgr.__aexit__ = AsyncMock(return_value=None)

    with patch("synology_apm.cli._helpers.resolve_connection", return_value=("h", "u", "p", False)):
        with patch("synology_apm.cli._helpers.APMClient", return_value=fake_ctx_mgr):
            async with _h.get_client(mock_ctx) as apm:
                assert apm is mock_apm


async def test_get_client_prompts_for_password_when_not_no_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client() calls typer.prompt for password when no_input is False and password is empty."""
    import synology_apm.cli._helpers as _h
    monkeypatch.setattr(_h, "_debug_mode", False)

    mock_ctx = MagicMock()
    mock_ctx.obj = {"host": "apm.test", "username": "admin", "password": None,
                    "profile": None, "no_input": False, "no_verify_ssl": False}

    mock_apm = AsyncMock()
    mock_apm.my_server.name = "APM"
    mock_apm.my_server.system_version = ""
    fake_ctx_mgr = AsyncMock()
    fake_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_apm)
    fake_ctx_mgr.__aexit__ = AsyncMock(return_value=None)

    with patch("synology_apm.cli._helpers.resolve_connection", return_value=("apm.test", "admin", "", False)):
        with patch("synology_apm.cli._helpers.typer") as mock_typer:
            mock_typer.prompt.return_value = "secret"
            with patch("synology_apm.cli._helpers.APMClient", return_value=fake_ctx_mgr):
                async with _h.get_client(mock_ctx):
                    pass
    mock_typer.prompt.assert_called_once_with("Password", hide_input=True)


# ── fmt_location_name ─────────────────────────────────────────────────────────

def _make_location(name: str, vault: str | None) -> VersionLocation:
    info = LocationInfo(is_remote_storage=False, identifier="ns", name=name, endpoint="", vault=vault)
    return VersionLocation(namespace="ns", location_info=info, location_id="v1")


def test_fmt_location_name_without_vault() -> None:
    loc = _make_location("apm-server-01", vault=None)
    assert fmt_location_name(loc) == "apm-server-01"


def test_fmt_location_name_with_vault() -> None:
    loc = _make_location("apm-server-01", vault="MyVault")
    assert fmt_location_name(loc) == "apm-server-01 (MyVault)"


# ── fmt_backup_copy ───────────────────────────────────────────────────────────

class _FakeWorkloadWithCopy:
    def __init__(self, loc: LocationInfo | None) -> None:
        self.backup_copy_destination = loc


def test_fmt_backup_copy_with_vault() -> None:
    loc = LocationInfo(is_remote_storage=True, identifier="ns", name="S3-Vault", endpoint="", vault="CorpVault")
    wl = _FakeWorkloadWithCopy(loc)
    assert fmt_backup_copy(wl) == "S3-Vault (CorpVault)"  # type: ignore[arg-type]


def test_fmt_backup_copy_without_vault() -> None:
    loc = LocationInfo(is_remote_storage=False, identifier="ns", name="APM-Server-02", endpoint="", vault=None)
    wl = _FakeWorkloadWithCopy(loc)
    assert fmt_backup_copy(wl) == "APM-Server-02"  # type: ignore[arg-type]


def test_fmt_backup_copy_none_returns_dash() -> None:
    assert fmt_backup_copy(_FakeWorkloadWithCopy(None)) == "-"  # type: ignore[arg-type]


# ── fmt_copy_status ───────────────────────────────────────────────────────────


def test_fmt_copy_status_completed_no_versions_to_copy_override() -> None:
    """fmt_copy_status returns 'No versions to copy' when status=COMPLETED and reason=NO_VERSIONS_TO_COPY."""
    from synology_apm.cli._display import fmt_copy_status
    from synology_apm.sdk.enums import CopyReason, VersionCopyStatus
    from synology_apm.sdk.models.protection_plan import PlanBackupCopyStatus

    info = PlanBackupCopyStatus(
        status=VersionCopyStatus.COMPLETED,
        reason=CopyReason.NO_VERSIONS_TO_COPY,
    )
    assert fmt_copy_status(info) == "No versions to copy"


def test_fmt_copy_status_completed_without_no_versions_reason_returns_completed_string() -> None:
    """fmt_copy_status returns the COMPLETED display string when reason is not NO_VERSIONS_TO_COPY."""
    from synology_apm.cli._display import fmt_copy_status
    from synology_apm.sdk.enums import VersionCopyStatus
    from synology_apm.sdk.models.protection_plan import PlanBackupCopyStatus

    info = PlanBackupCopyStatus(
        status=VersionCopyStatus.COMPLETED,
        reason=None,
    )
    result = fmt_copy_status(info)
    assert "Completed" in result


def test_fmt_copy_status_none_returns_dash() -> None:
    """fmt_copy_status returns '-' when info is None."""
    from synology_apm.cli._display import fmt_copy_status

    assert fmt_copy_status(None) == "-"


# ── fmt_category ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cat,expected", [
    (WorkloadCategory.MACHINE, "Machine"),
    (WorkloadCategory.M365,    "M365"),
    (WorkloadCategory.GWS,     "GWS"),
])
def test_fmt_category(cat: WorkloadCategory, expected: str) -> None:
    from synology_apm.cli._display import fmt_category
    assert fmt_category(cat) == expected


# ── fmt_workload_status ───────────────────────────────────────────────────────

from synology_apm.sdk.enums import WorkloadStatus  # noqa: E402


class _FakeWorkload:
    """Minimal duck-type for fmt_workload_status / fmt_backup_server / fmt_backup_copy."""
    def __init__(
        self,
        status: WorkloadStatus,
        items_backed_up: int | None = None,
        backup_progress: int | None = None,
    ) -> None:
        self.status = status
        self.items_backed_up = items_backed_up
        self.backup_progress = backup_progress


def test_fmt_workload_status_items_backed_up_branch() -> None:
    """BACKING_UP with items_backed_up set → 'N items' label."""
    from synology_apm.cli._display import fmt_workload_status
    wl = _FakeWorkload(WorkloadStatus.BACKING_UP, items_backed_up=42)
    assert "42 items" in fmt_workload_status(wl)  # type: ignore[arg-type]


def test_fmt_workload_status_progress_branch() -> None:
    """BACKING_UP with backup_progress set (and items_backed_up=None) → 'N%' label."""
    from synology_apm.cli._display import fmt_workload_status
    wl = _FakeWorkload(WorkloadStatus.BACKING_UP, items_backed_up=None, backup_progress=75)
    assert "75%" in fmt_workload_status(wl)  # type: ignore[arg-type]


def test_fmt_workload_status_non_backing_up_uses_display_map() -> None:
    """Non-BACKING_UP statuses are resolved from the display map."""
    from synology_apm.cli._display import fmt_workload_status
    expected = {
        WorkloadStatus.SUCCESS:    "[green]✓ Success[/green]",
        WorkloadStatus.FAILED:     "[red]✗ Failed[/red]",
        WorkloadStatus.PARTIAL:    "[yellow]⚠ Partial[/yellow]",
        WorkloadStatus.CANCELED:   "[bright_black]⊘ Canceled[/bright_black]",
        WorkloadStatus.NO_BACKUPS: "[bright_black]— No Backups[/bright_black]",
    }
    for status, display in expected.items():
        wl = _FakeWorkload(status)
        result = fmt_workload_status(wl)  # type: ignore[arg-type]
        assert result == display, f"unexpected display for {status}"


async def test_get_client_keyring_unavailable_exits_with_error() -> None:
    """get_client() converts KeyringUnavailableError into a CLI error exit."""
    from unittest.mock import MagicMock, patch

    from synology_apm.cli._helpers import get_client
    from synology_apm.cli.config import KeyringUnavailableError

    ctx = MagicMock()
    ctx.obj = {}
    with patch(
        "synology_apm.cli._helpers.resolve_connection",
        side_effect=KeyringUnavailableError("keyring locked"),
    ):
        with pytest.raises(typer.Exit) as exc_info:
            async with get_client(ctx):
                pass  # pragma: no cover - never entered

    assert exc_info.value.exit_code == 1


def test_main_entry_point_invokes_app() -> None:
    """The console-script main() runs the Typer app."""
    from unittest.mock import patch

    from synology_apm.cli.main import main

    with patch("sys.argv", ["synology-apm", "--help"]):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
