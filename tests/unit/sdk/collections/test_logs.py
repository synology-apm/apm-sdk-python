"""Unit tests for LogCollection."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from synology_apm.sdk.collections.logs import LogCollection
from synology_apm.sdk.enums import APMActivityLogType, BackupServerType, LogLevel, ServerStatus
from synology_apm.sdk.models.backup_server import BackupServer
from tests.unit.sdk.conftest import make_session

NAMESPACE = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"

SAMPLE_SERVER = BackupServer(
    backup_server_id=NAMESPACE,
    namespace=NAMESPACE,
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

SAMPLE_ACTIVITY_RAW = {
    "type": "PROTECTION",
    "timestamp": "1779264734",
    "level": "LEVEL_WARNING",
    "description": "Unable to copy data.",
    "username": "SYSTEM",
}

SAMPLE_DRIVE_RAW = {
    "timestamp": "1735288615",
    "level": "LEVEL_INFORMATION",
    "description": "Disabled the bad sector warning.",
    "deviceName": "APM-Node1",
    "model": "-",
    "location": "-",
    "serial": "-",
}

SAMPLE_CONNECTION_RAW = {
    "timestamp": "1779265485",
    "level": "LEVEL_INFORMATION",
    "description": "User [admin] from [192.0.2.30] signed in successfully.",
    "username": "admin",
}

SAMPLE_SYSTEM_RAW = {
    "timestamp": "1778921660",
    "level": "LEVEL_INFORMATION",
    "description": "[LAN mgmt] link up.",
    "username": "SYSTEM",
}


# ── list_activity() ────────────────────────────────────────────────────────────

async def test_list_activity_parses_fields() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": [SAMPLE_ACTIVITY_RAW]}
        collection = LogCollection(session)
        logs, total = await collection.list_activity(SAMPLE_SERVER)

    assert total == 0
    assert len(logs) == 1
    e = logs[0]
    assert e.level == LogLevel.WARNING
    assert e.log_type == APMActivityLogType.PROTECTION
    assert e.timestamp == datetime.fromtimestamp(1779264734, tz=UTC)
    assert e.username == "SYSTEM"
    assert e.description == "Unable to copy data."


async def test_list_activity_sends_tunnel_header() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": []}
        collection = LogCollection(session)
        await collection.list_activity(SAMPLE_SERVER)

    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"x-syno-tunnel-route": NAMESPACE}


async def test_list_activity_sends_level_params() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": []}
        collection = LogCollection(session)
        await collection.list_activity(
            SAMPLE_SERVER,
            levels=[LogLevel.ERROR, LogLevel.WARNING],
        )

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["levels"] == ["LEVEL_ERROR", "LEVEL_WARNING"]


async def test_list_activity_sends_type_param() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": []}
        collection = LogCollection(session)
        await collection.list_activity(SAMPLE_SERVER, log_type=APMActivityLogType.DATA_ACCESS)

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["type"] == "DATA_ACCESS"


async def test_list_activity_sends_time_params() -> None:
    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 1, 2, tzinfo=UTC)
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": []}
        collection = LogCollection(session)
        await collection.list_activity(SAMPLE_SERVER, since=since, until=until)

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["startTime"] == int(since.timestamp())
    assert kwargs["params"]["endTime"] == int(until.timestamp())


async def test_list_activity_sends_keyword() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": []}
        collection = LogCollection(session)
        await collection.list_activity(SAMPLE_SERVER, keyword="searchterm")

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["keyword"] == "searchterm"


async def test_list_activity_default_limit_offset() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": []}
        collection = LogCollection(session)
        await collection.list_activity(SAMPLE_SERVER)

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["limit"] == 25
    assert kwargs["params"]["offset"] == 0


async def test_list_activity_empty() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": []}
        collection = LogCollection(session)
        logs, total = await collection.list_activity(SAMPLE_SERVER)

    assert logs == []
    assert total == 0


# ── list_drive() ───────────────────────────────────────────────────────────────

async def test_list_drive_parses_fields() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"driveLogs": [SAMPLE_DRIVE_RAW], "total": 1}
        collection = LogCollection(session)
        logs, total = await collection.list_drive(SAMPLE_SERVER)

    assert total == 1
    assert len(logs) == 1
    e = logs[0]
    assert e.level == LogLevel.INFO
    assert e.description == "Disabled the bad sector warning."
    assert e.timestamp == datetime.fromtimestamp(1735288615, tz=UTC)
    assert e.server_name == "APM-Node1"
    assert e.model == "-"
    assert e.location == "-"
    assert e.serial == "-"


async def test_list_drive_uses_api_total() -> None:
    """Drive log uses the API-reported total (not len)."""
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"driveLogs": [SAMPLE_DRIVE_RAW], "total": 99}
        collection = LogCollection(session)
        _, total = await collection.list_drive(SAMPLE_SERVER)

    assert total == 99


async def test_list_drive_sends_location_param() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"driveLogs": [], "total": 0}
        collection = LogCollection(session)
        await collection.list_drive(SAMPLE_SERVER, location="Slot 1")

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["location"] == "Slot 1"


async def test_list_drive_sends_tunnel_header() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"driveLogs": [], "total": 0}
        collection = LogCollection(session)
        await collection.list_drive(SAMPLE_SERVER)

    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"x-syno-tunnel-route": NAMESPACE}


# ── list_connection() ─────────────────────────────────────────────────────────

async def test_list_connection_parses_fields() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"connectionLogs": [SAMPLE_CONNECTION_RAW]}
        collection = LogCollection(session)
        logs, total = await collection.list_connection(SAMPLE_SERVER)

    assert total == 0
    e = logs[0]
    assert e.level == LogLevel.INFO
    assert e.username == "admin"
    assert e.description == "User [admin] from [192.0.2.30] signed in successfully."
    assert e.timestamp == datetime.fromtimestamp(1779265485, tz=UTC)


async def test_list_connection_sends_tunnel_header() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"connectionLogs": []}
        collection = LogCollection(session)
        await collection.list_connection(SAMPLE_SERVER)

    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"x-syno-tunnel-route": NAMESPACE}


# ── list_system() ─────────────────────────────────────────────────────────────

async def test_list_system_parses_fields() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"generalLogs": [SAMPLE_SYSTEM_RAW]}
        collection = LogCollection(session)
        logs, total = await collection.list_system(SAMPLE_SERVER)

    assert total == 0
    e = logs[0]
    assert e.level == LogLevel.INFO
    assert e.username == "SYSTEM"
    assert e.description == "[LAN mgmt] link up."
    assert e.timestamp == datetime.fromtimestamp(1778921660, tz=UTC)


async def test_list_system_sends_tunnel_header() -> None:
    session = make_session()
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"generalLogs": []}
        collection = LogCollection(session)
        await collection.list_system(SAMPLE_SERVER)

    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"x-syno-tunnel-route": NAMESPACE}


# ── Level mapping ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("api_val,expected", [
    ("LEVEL_INFORMATION", LogLevel.INFO),
    ("LEVEL_WARNING",     LogLevel.WARNING),
    ("LEVEL_ERROR",       LogLevel.ERROR),
    ("UNKNOWN_FUTURE",    LogLevel.INFO),  # fallback
])
async def test_level_mapping(api_val: str, expected: LogLevel) -> None:
    session = make_session()
    raw = {**SAMPLE_CONNECTION_RAW, "level": api_val}
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"connectionLogs": [raw]}
        collection = LogCollection(session)
        logs, _ = await collection.list_connection(SAMPLE_SERVER)

    assert logs[0].level == expected


# ── APMActivityLogType mapping ────────────────────────────────────────────────────

@pytest.mark.parametrize("api_val,expected", [
    ("PROTECTION",  APMActivityLogType.PROTECTION),
    ("SYSTEM",      APMActivityLogType.SYSTEM),
    ("DATA_ACCESS", APMActivityLogType.DATA_ACCESS),
    ("",            None),  # missing type → None
])
async def test_activity_type_mapping(api_val: str, expected: APMActivityLogType | None) -> None:
    session = make_session()
    raw = {**SAMPLE_ACTIVITY_RAW, "type": api_val}
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"aemLogs": [raw]}
        collection = LogCollection(session)
        logs, _ = await collection.list_activity(SAMPLE_SERVER)

    assert logs[0].log_type == expected


# ── Optional filter params for list_drive / list_connection / list_system ──────

async def test_list_drive_sends_optional_filter_params() -> None:
    """list_drive sends levels, startTime, endTime, and keyword params when provided."""
    session = make_session()
    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 6, 1, tzinfo=UTC)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"driveLogs": [], "total": 0}
        collection = LogCollection(session)
        await collection.list_drive(
            SAMPLE_SERVER,
            levels=[LogLevel.WARNING],
            since=since,
            until=until,
            keyword="sector",
        )
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["levels"] == ["LEVEL_WARNING"]
    assert kwargs["params"]["startTime"] == int(since.timestamp())
    assert kwargs["params"]["endTime"] == int(until.timestamp())
    assert kwargs["params"]["keyword"] == "sector"


async def test_list_connection_sends_optional_filter_params() -> None:
    """list_connection sends levels, startTime, endTime, and keyword params when provided."""
    session = make_session()
    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 6, 1, tzinfo=UTC)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"connectionLogs": []}
        collection = LogCollection(session)
        await collection.list_connection(
            SAMPLE_SERVER,
            levels=[LogLevel.ERROR],
            since=since,
            until=until,
            keyword="login",
        )
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["levels"] == ["LEVEL_ERROR"]
    assert kwargs["params"]["startTime"] == int(since.timestamp())
    assert kwargs["params"]["endTime"] == int(until.timestamp())
    assert kwargs["params"]["keyword"] == "login"


async def test_list_system_sends_optional_filter_params() -> None:
    """list_system sends levels, startTime, endTime, and keyword params when provided."""
    session = make_session()
    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 6, 1, tzinfo=UTC)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"generalLogs": []}
        collection = LogCollection(session)
        await collection.list_system(
            SAMPLE_SERVER,
            levels=[LogLevel.INFO],
            since=since,
            until=until,
            keyword="link",
        )
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["levels"] == ["LEVEL_INFORMATION"]
    assert kwargs["params"]["startTime"] == int(since.timestamp())
    assert kwargs["params"]["endTime"] == int(until.timestamp())
    assert kwargs["params"]["keyword"] == "link"
