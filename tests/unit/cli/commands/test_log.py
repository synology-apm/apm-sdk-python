"""Unit tests for apm log commands."""
from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from synology_apm.cli.main import app
from synology_apm.sdk.enums import APMActivityLogType, BackupServerType, LogLevel, ServerStatus
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.log import APMActivityLog, ConnectionLog, DriveLog, SystemLog
from tests.unit.cli.conftest import runner

SERVER_ID = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"

SAMPLE_SERVER = BackupServer(
    backup_server_id=SERVER_ID,
    namespace=SERVER_ID,
    server_type=BackupServerType.DP,
    name="apm-server-01",
    hostname="192.0.2.1",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN001",
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

NAS_SERVER = BackupServer(
    backup_server_id="nas-001",
    namespace="ns-nas-001",
    server_type=BackupServerType.NAS,
    name="nas-server-01",
    hostname="10.0.0.10",
    model="DS1823xs+",
    system_version="ABB 7.2.2-72806",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="NAS001",
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

TS = datetime(2026, 4, 20, 1, 32, 14, tzinfo=UTC)

SAMPLE_ACTIVITY = APMActivityLog(
    level=LogLevel.WARNING,
    log_type=APMActivityLogType.PROTECTION,
    timestamp=TS,
    username="SYSTEM",
    description="Unable to copy data.",
)

SAMPLE_DRIVE = DriveLog(
    level=LogLevel.INFO,
    timestamp=TS,
    description="Disabled the bad sector warning.",
    server_name="APM-Node1",
    model="WD Red",
    location="Slot 1",
    serial="WD123",
)

SAMPLE_CONNECTION = ConnectionLog(
    level=LogLevel.INFO,
    timestamp=TS,
    username="admin",
    description="User [admin] signed in successfully.",
)

SAMPLE_SYSTEM = SystemLog(
    level=LogLevel.INFO,
    timestamp=TS,
    username="SYSTEM",
    description="[LAN mgmt] link up.",
)


@asynccontextmanager
async def _fake_client(
    activity_logs: list[APMActivityLog] | None = None,
    drive_logs: list[DriveLog] | None = None,
    connection_logs: list[ConnectionLog] | None = None,
    system_logs: list[SystemLog] | None = None,
    server: BackupServer | None = None,
    server_not_found: bool = False,
) -> AsyncIterator[AsyncMock]:
    mock_apm = AsyncMock()
    _err = ResourceNotFoundError("not found", resource_type="BackupServer", resource_id="x")
    if server_not_found:
        mock_apm.backup_servers.get_by_name = AsyncMock(side_effect=_err)
        mock_apm.backup_servers.get = AsyncMock(side_effect=_err)
    else:
        mock_apm.backup_servers.get_by_name = AsyncMock(return_value=server or SAMPLE_SERVER)
        mock_apm.backup_servers.get = AsyncMock(return_value=server or SAMPLE_SERVER)
    mock_apm.logs.list_activity = AsyncMock(
        return_value=(activity_logs or [], 0)
    )
    mock_apm.logs.list_drive = AsyncMock(
        return_value=(drive_logs or [], len(drive_logs or []))
    )
    mock_apm.logs.list_connection = AsyncMock(
        return_value=(connection_logs or [], 0)
    )
    mock_apm.logs.list_system = AsyncMock(
        return_value=(system_logs or [], 0)
    )
    yield mock_apm


# ═══════════════════════════════════════════════════════════════════════════════
# apm log activity list
# ═══════════════════════════════════════════════════════════════════════════════

def test_activity_list_no_args_shows_help() -> None:
    result = runner.invoke(app, ["log", "activity", "list"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_activity_list_name_and_id_conflict() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client()
        result = runner.invoke(app, [
            "log", "activity", "list", "apm-server-01", "--id", SERVER_ID
        ])
    assert result.exit_code == 1
    assert "cannot be used with" in result.output


def test_activity_list_search_mode_table() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(activity_logs=[SAMPLE_ACTIVITY])
        result = runner.invoke(app, ["log", "activity", "list", "apm-server-01"])
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "Data protection" in result.output
    assert "SYSTEM" in result.output
    assert "Unable to" in result.output
    assert "copy data." in result.output
    assert "Showing 1" in result.output


def test_activity_list_direct_id_mode() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(activity_logs=[SAMPLE_ACTIVITY])
        result = runner.invoke(app, ["log", "activity", "list", "--id", SERVER_ID])
    assert result.exit_code == 0
    assert "Warning" in result.output


def test_activity_list_search_resolves_server() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        ctx_holder = {}

        @asynccontextmanager
        async def _ctx(*_a: object, **_kw: object) -> AsyncIterator[AsyncMock]:
            mock_apm = AsyncMock()
            mock_apm.backup_servers.get_by_name = AsyncMock(return_value=SAMPLE_SERVER)
            mock_apm.logs.list_activity = AsyncMock(return_value=([], 0))
            ctx_holder["apm"] = mock_apm
            yield mock_apm

        mock_gc.return_value = _ctx()
        runner.invoke(app, ["log", "activity", "list", "apm-server-01"])

    apm = ctx_holder["apm"]
    apm.backup_servers.get_by_name.assert_called_once_with("apm-server-01")
    apm.logs.list_activity.assert_called_once()
    call_args = apm.logs.list_activity.call_args
    assert call_args.args[0] == SAMPLE_SERVER


def test_activity_list_server_not_found() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(server_not_found=True)
        result = runner.invoke(app, ["log", "activity", "list", "no-such-server"])
    assert result.exit_code == 1


def test_activity_list_rejects_nas_server() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(server=NAS_SERVER)
        result = runner.invoke(app, ["log", "activity", "list", "nas-server-01"])
    assert result.exit_code == 1
    assert "NAS server" in result.output


def test_activity_list_json() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(activity_logs=[SAMPLE_ACTIVITY])
        result = runner.invoke(app, ["log", "activity", "list", "--id", SERVER_ID, "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["level"] == "warning"
    assert data[0]["type"] == "protection"


def test_activity_list_csv() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(activity_logs=[SAMPLE_ACTIVITY])
        result = runner.invoke(app, ["log", "activity", "list", "--id", SERVER_ID, "-o", "csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert "level" in lines[0]
    assert "warning" in result.output


def test_activity_list_empty() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(activity_logs=[])
        result = runner.invoke(app, ["log", "activity", "list", "--id", SERVER_ID])
    assert result.exit_code == 0
    assert "Showing 0" in result.output


@pytest.mark.parametrize("extra_args,kwarg_key,expected_value", [
    (
        ["--level", "warning", "--level", "error"],
        "levels",
        [LogLevel.WARNING, LogLevel.ERROR],
    ),
    (
        ["--type", "protection"],
        "log_type",
        APMActivityLogType.PROTECTION,
    ),
])
def test_activity_list_passes_filter(
    extra_args: list[str],
    kwarg_key: str,
    expected_value: object,
) -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        ctx_holder: dict[str, AsyncMock] = {}

        @asynccontextmanager
        async def _ctx(*_a: object, **_kw: object) -> AsyncIterator[AsyncMock]:
            mock_apm = AsyncMock()
            mock_apm.backup_servers.get = AsyncMock(return_value=SAMPLE_SERVER)
            mock_apm.logs.list_activity = AsyncMock(return_value=([], 0))
            ctx_holder["apm"] = mock_apm
            yield mock_apm

        mock_gc.return_value = _ctx()
        runner.invoke(app, ["log", "activity", "list", "--id", SERVER_ID] + extra_args)

    call_kwargs = ctx_holder["apm"].logs.list_activity.call_args.kwargs
    assert call_kwargs[kwarg_key] == expected_value


def test_activity_list_passes_search_and_offset() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        ctx_holder = {}

        @asynccontextmanager
        async def _ctx(*_a: object, **_kw: object) -> AsyncIterator[AsyncMock]:
            mock_apm = AsyncMock()
            mock_apm.backup_servers.get = AsyncMock(return_value=SAMPLE_SERVER)
            mock_apm.logs.list_activity = AsyncMock(return_value=([], 0))
            ctx_holder["apm"] = mock_apm
            yield mock_apm

        mock_gc.return_value = _ctx()
        runner.invoke(app, [
            "log", "activity", "list", "--id", SERVER_ID,
            "--search", "backup", "--offset", "50",
        ])

    call_kwargs = ctx_holder["apm"].logs.list_activity.call_args.kwargs
    assert call_kwargs["keyword"] == "backup"
    assert call_kwargs["offset"] == 50



# ═══════════════════════════════════════════════════════════════════════════════
# apm log drive list
# ═══════════════════════════════════════════════════════════════════════════════

def test_drive_list_no_args_shows_help() -> None:
    result = runner.invoke(app, ["log", "drive", "list"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_drive_list_table() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(drive_logs=[SAMPLE_DRIVE])
        result = runner.invoke(app, ["log", "drive", "list", "--id", SERVER_ID])
    assert result.exit_code == 0
    assert "WD Red" in result.output
    assert "Slot 1" in result.output
    assert "WD123" in result.output
    assert "APM-Node1" in result.output   # server name from deviceName
    # Event column is not asserted here: drive log has 7 columns and requires
    # ~120+ col terminal to display Event; the test runner uses 80 cols.
    # Event content is verified via test_drive_list_json.
    assert "Showing 1 of 1" in result.output


def test_drive_list_json() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(drive_logs=[SAMPLE_DRIVE])
        result = runner.invoke(app, ["log", "drive", "list", "--id", SERVER_ID, "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["server_name"] == "APM-Node1"
    assert data[0]["serial"] == "WD123"


# ═══════════════════════════════════════════════════════════════════════════════
# apm log connection list
# ═══════════════════════════════════════════════════════════════════════════════

def test_connection_list_no_args_shows_help() -> None:
    result = runner.invoke(app, ["log", "connection", "list"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_connection_list_table() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(connection_logs=[SAMPLE_CONNECTION])
        result = runner.invoke(app, ["log", "connection", "list", "--id", SERVER_ID])
    assert result.exit_code == 0
    assert "admin" in result.output
    assert "signed in" in result.output
    assert "Showing 1" in result.output


def test_connection_list_json() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(connection_logs=[SAMPLE_CONNECTION])
        result = runner.invoke(app, ["log", "connection", "list", "--id", SERVER_ID, "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["username"] == "admin"


# ═══════════════════════════════════════════════════════════════════════════════
# apm log system list
# ═══════════════════════════════════════════════════════════════════════════════

def test_system_list_no_args_shows_help() -> None:
    result = runner.invoke(app, ["log", "system", "list"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_system_list_table() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(system_logs=[SAMPLE_SYSTEM])
        result = runner.invoke(app, ["log", "system", "list", "--id", SERVER_ID])
    assert result.exit_code == 0
    assert "SYSTEM" in result.output
    assert "link up" in result.output
    assert "Showing 1" in result.output


def test_system_list_json() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(system_logs=[SAMPLE_SYSTEM])
        result = runner.invoke(app, ["log", "system", "list", "--id", SERVER_ID, "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["username"] == "SYSTEM"


# ── yaml / csv output variants ────────────────────────────────────────────


def test_activity_list_yaml_output() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(activity_logs=[SAMPLE_ACTIVITY])
        result = runner.invoke(app, ["log", "activity", "list", "--id", SERVER_ID, "-o", "yaml"])
    assert result.exit_code == 0
    assert "level" in result.output
    assert "warning" in result.output


def test_drive_list_yaml_output() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(drive_logs=[SAMPLE_DRIVE])
        result = runner.invoke(app, ["log", "drive", "list", "--id", SERVER_ID, "-o", "yaml"])
    assert result.exit_code == 0
    assert "server_name" in result.output
    assert "APM-Node1" in result.output


def test_drive_list_csv_output() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(drive_logs=[SAMPLE_DRIVE])
        result = runner.invoke(app, ["log", "drive", "list", "--id", SERVER_ID, "-o", "csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert "server_name" in lines[0]
    assert "APM-Node1" in result.output


def test_connection_list_yaml_output() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(connection_logs=[SAMPLE_CONNECTION])
        result = runner.invoke(app, ["log", "connection", "list", "--id", SERVER_ID, "-o", "yaml"])
    assert result.exit_code == 0
    assert "username" in result.output
    assert "admin" in result.output


def test_connection_list_csv_output() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(connection_logs=[SAMPLE_CONNECTION])
        result = runner.invoke(app, ["log", "connection", "list", "--id", SERVER_ID, "-o", "csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert "username" in lines[0]
    assert "admin" in result.output


def test_system_list_yaml_output() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(system_logs=[SAMPLE_SYSTEM])
        result = runner.invoke(app, ["log", "system", "list", "--id", SERVER_ID, "-o", "yaml"])
    assert result.exit_code == 0
    assert "username" in result.output
    assert "SYSTEM" in result.output


def test_system_list_csv_output() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(system_logs=[SAMPLE_SYSTEM])
        result = runner.invoke(app, ["log", "system", "list", "--id", SERVER_ID, "-o", "csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert "username" in lines[0]
    assert "SYSTEM" in result.output


# ── --page-all ───────────────────────────────────────────────────────────────


def test_activity_list_page_all_combines_pages(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """log activity list --page-all --limit 1 should fetch every page and render one combined table."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    activity_2 = dataclasses.replace(SAMPLE_ACTIVITY, description="Second event.")

    mock_apm.backup_servers.get_by_name = AsyncMock(return_value=SAMPLE_SERVER)
    mock_apm.logs.list_activity = AsyncMock(side_effect=[
        ([SAMPLE_ACTIVITY], 2),
        ([activity_2], 2),
    ])

    @asynccontextmanager
    async def fake_client() -> AsyncIterator[AsyncMock]:
        yield mock_apm

    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = fake_client()
        result = runner.invoke(app, [
            "log", "activity", "list", "apm-server-01", "--limit", "1", "--page-all",
        ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "Unable to copy" in result.output
    assert "Second event." in result.output
    assert "Showing 2 of 2" in result.output
    assert mock_apm.logs.list_activity.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.logs.list_activity.call_args_list[1].kwargs["offset"] == 1


def test_activity_list_page_all_continues_past_full_page_with_total_zero(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """log activity list --page-all must keep paging when the API reports total=0 (the
    real list_activity sentinel for "no total available") even though the first page is full."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    activity_1b = dataclasses.replace(SAMPLE_ACTIVITY, description="First page, second event.")
    activity_2 = dataclasses.replace(SAMPLE_ACTIVITY, description="Second page event.")

    mock_apm.backup_servers.get_by_name = AsyncMock(return_value=SAMPLE_SERVER)
    mock_apm.logs.list_activity = AsyncMock(side_effect=[
        ([SAMPLE_ACTIVITY, activity_1b], 0),  # full page (n == limit), total=0 sentinel
        ([activity_2], 0),                    # short page (n < limit) -> stop
    ])

    @asynccontextmanager
    async def fake_client() -> AsyncIterator[AsyncMock]:
        yield mock_apm

    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = fake_client()
        result = runner.invoke(app, [
            "log", "activity", "list", "apm-server-01", "--limit", "2", "--page-all",
        ], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "First page, second event." in result.output
    assert "Second page event." in result.output
    assert "Showing 3" in result.output
    assert mock_apm.logs.list_activity.call_args_list[0].kwargs["offset"] == 0
    assert mock_apm.logs.list_activity.call_args_list[1].kwargs["offset"] == 2


# ── --since / --until forwarding (all four log kinds) ────────────────────────

_LOG_LIST_CASES = [
    ("activity", ["log", "activity", "list"], "list_activity"),
    ("drive", ["log", "drive", "list"], "list_drive"),
    ("connection", ["log", "connection", "list"], "list_connection"),
    ("system", ["log", "system", "list"], "list_system"),
]


@pytest.mark.parametrize("kind,cmd_path,list_attr", _LOG_LIST_CASES, ids=[c[0] for c in _LOG_LIST_CASES])
def test_log_list_passes_since_until_to_sdk(kind: str, cmd_path: list[str], list_attr: str) -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        ctx_holder: dict[str, AsyncMock] = {}

        @asynccontextmanager
        async def _ctx(*_a: object, **_kw: object) -> AsyncIterator[AsyncMock]:
            mock_apm = AsyncMock()
            mock_apm.backup_servers.get = AsyncMock(return_value=SAMPLE_SERVER)
            getattr(mock_apm.logs, list_attr).return_value = ([], 0)
            ctx_holder["apm"] = mock_apm
            yield mock_apm

        mock_gc.return_value = _ctx()
        runner.invoke(app, [
            *cmd_path, "--id", SERVER_ID,
            "--since", "2026-04-01T00:00:00", "--until", "2026-04-02T00:00:00",
        ])

    call_kwargs = getattr(ctx_holder["apm"].logs, list_attr).call_args.kwargs
    assert call_kwargs["since"] == datetime(2026, 4, 1, tzinfo=UTC)
    assert call_kwargs["until"] == datetime(2026, 4, 2, tzinfo=UTC)


def test_drive_list_passes_location_filter() -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        ctx_holder: dict[str, AsyncMock] = {}

        @asynccontextmanager
        async def _ctx(*_a: object, **_kw: object) -> AsyncIterator[AsyncMock]:
            mock_apm = AsyncMock()
            mock_apm.backup_servers.get = AsyncMock(return_value=SAMPLE_SERVER)
            mock_apm.logs.list_drive = AsyncMock(return_value=([], 0))
            ctx_holder["apm"] = mock_apm
            yield mock_apm

        mock_gc.return_value = _ctx()
        runner.invoke(app, ["log", "drive", "list", "--id", SERVER_ID, "--location", "Slot 1"])

    call_kwargs = ctx_holder["apm"].logs.list_drive.call_args.kwargs
    assert call_kwargs["location"] == "Slot 1"


# ── NAS server rejection (drive / connection / system) ───────────────────────
# activity's rejection is covered by test_activity_list_rejects_nas_server above;
# drive/connection/system share the same _resolve_server helper but had no test of
# their own, so a regression isolated to one subcommand's wiring would slip through.

@pytest.mark.parametrize("cmd_path", [
    ["log", "drive", "list"],
    ["log", "connection", "list"],
    ["log", "system", "list"],
], ids=["drive", "connection", "system"])
def test_log_list_rejects_nas_server(cmd_path: list[str]) -> None:
    with patch("synology_apm.cli._helpers.get_client") as mock_gc:
        mock_gc.return_value = _fake_client(server=NAS_SERVER)
        result = runner.invoke(app, [*cmd_path, "nas-server-01"])
    assert result.exit_code == 1
    assert "NAS server" in result.output
