"""Integration tests: LogCollection (list_activity / list_drive / list_connection / list_system)"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import LogLevel
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.log import APMActivityLog, ConnectionLog, DriveLog, SystemLog

pytestmark = pytest.mark.integration


async def _first_server(apm: APMClient) -> BackupServer:
    """Helper: return the first backup server or skip the test."""
    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    return servers[0]


# ── list_activity() ────────────────────────────────────────────────────────


async def test_list_activity_returns_list(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, _ = await apm.logs.list_activity(server)
    assert isinstance(logs, list)


async def test_list_activity_items_are_activity_log_instances(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, _ = await apm.logs.list_activity(server)
    for entry in logs:
        assert isinstance(entry, APMActivityLog)


async def test_list_activity_level_is_valid_enum(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, _ = await apm.logs.list_activity(server)
    valid = set(LogLevel)
    for entry in logs:
        assert entry.level in valid


async def test_list_activity_timestamp_is_timezone_aware(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, _ = await apm.logs.list_activity(server)
    for entry in logs:
        assert entry.timestamp.tzinfo is not None


# ── list_drive() ───────────────────────────────────────────────────────────


async def test_list_drive_returns_list(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, total = await apm.logs.list_drive(server)
    assert isinstance(logs, list)
    assert isinstance(total, int)


async def test_list_drive_items_are_drive_log_instances(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, _ = await apm.logs.list_drive(server)
    for entry in logs:
        assert isinstance(entry, DriveLog)


# ── list_connection() ──────────────────────────────────────────────────────


async def test_list_connection_returns_list(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, total = await apm.logs.list_connection(server)
    assert isinstance(logs, list)
    assert total == 0  # endpoint never reports a total


async def test_list_connection_items_are_connection_log_instances(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, _ = await apm.logs.list_connection(server)
    for entry in logs:
        assert isinstance(entry, ConnectionLog)


# ── list_system() ──────────────────────────────────────────────────────────


async def test_list_system_returns_list(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, total = await apm.logs.list_system(server)
    assert isinstance(logs, list)
    assert total == 0  # endpoint never reports a total


async def test_list_system_items_are_system_log_instances(apm: APMClient) -> None:
    server = await _first_server(apm)
    logs, _ = await apm.logs.list_system(server)
    for entry in logs:
        assert isinstance(entry, SystemLog)
