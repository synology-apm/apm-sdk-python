"""Unit tests for apm infra storage commands."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import RemoteStorageStatus, RemoteStorageType
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.remote_storage import RemoteStorage
from tests.unit.cli.conftest import invoke_cli

SAMPLE_REMOTE_STORAGE = RemoteStorage(
    storage_id="f0d5d047-7dda-59fe-8d1b-47441c80bd1e",
    name="DSM-Storage",
    storage_type=RemoteStorageType.ACTIVE_PROTECT_VAULT,
    device_model="DSM",
    endpoint="192.0.2.20:8444",
    status=RemoteStorageStatus.CONNECTED,
    used_bytes=453378,
    remaining_bytes=366960877568,
)

AUTH_FAIL_REMOTE_STORAGE = RemoteStorage(
    storage_id="auth-fail-id",
    name="DSM-Storage-auth-fail",
    storage_type=RemoteStorageType.ACTIVE_PROTECT_VAULT,
    device_model="",
    endpoint="192.0.2.21:8444",
    status=RemoteStorageStatus.AUTH_FAILED,
    used_bytes=None,
    remaining_bytes=None,
)

NO_REMAINING_REMOTE_STORAGE = RemoteStorage(
    storage_id="no-remaining-id",
    name="DSM-Storage-no-remaining",
    storage_type=RemoteStorageType.ACTIVE_PROTECT_VAULT,
    device_model="DSM",
    endpoint="192.0.2.22:8444",
    status=RemoteStorageStatus.CONNECTED,
    used_bytes=453378,
    remaining_bytes=None,
)


# ── storage list ───────────────────────────────────────────────────────────


def test_storage_list_table(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.list.return_value = ([SAMPLE_REMOTE_STORAGE], 1)
    result = invoke_cli(mock_apm, ["infra", "storage", "list"])
    assert result.exit_code == 0
    assert "DSM-Storage" in result.output
    assert "ActiveProtect" in result.output
    assert "Connected" in result.output
    assert "Client-Side Encryption" in result.output
    assert "Disabled" in result.output


def test_storage_list_encryption_enabled(mock_apm: AsyncMock) -> None:
    """storage list should show 'Enabled' in the Encryption column when encryption_enabled=True."""
    encrypted_storage = RemoteStorage(
        storage_id="enc-id",
        name="Encrypted-Vault",
        storage_type=RemoteStorageType.ACTIVE_PROTECT_VAULT,
        device_model="DSM",
        endpoint="192.0.2.23:8444",
        status=RemoteStorageStatus.CONNECTED,
        used_bytes=1024,
        remaining_bytes=1073741824,
        encryption_enabled=True,
    )
    mock_apm.remote_storages.list.return_value = ([encrypted_storage], 1)
    result = invoke_cli(mock_apm, ["infra", "storage", "list"], env={"COLUMNS": "300"})
    assert result.exit_code == 0
    assert "Enabled" in result.output


def test_storage_list_verbose_shows_id(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.list.return_value = ([SAMPLE_REMOTE_STORAGE], 1)
    result = invoke_cli(mock_apm, ["infra", "storage", "list", "--verbose"])
    assert result.exit_code == 0
    assert "f0d5d047-7dda-59fe-8d1b-47441c80bd1e" in result.output


def test_storage_list_json(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.list.return_value = ([SAMPLE_REMOTE_STORAGE], 1)
    result = invoke_cli(mock_apm, ["infra", "storage", "list", "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["name"] == "DSM-Storage"
    assert data[0]["status"] == "connected"


def test_storage_list_csv(mock_apm: AsyncMock) -> None:
    """infra storage list --output csv should output CSV with a storage_id field."""
    mock_apm.remote_storages.list.return_value = ([SAMPLE_REMOTE_STORAGE], 1)
    result = invoke_cli(mock_apm, ["infra", "storage", "list", "-o", "csv"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert "storage_id" in lines[0]
    assert "DSM-Storage" in result.output


def test_storage_list_empty(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.list.return_value = ([], 0)
    result = invoke_cli(mock_apm, ["infra", "storage", "list"])
    assert result.exit_code == 0
    assert "Showing 0 of 0" in result.output


def test_storage_list_usage_no_data(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.list.return_value = ([AUTH_FAIL_REMOTE_STORAGE], 1)
    result = invoke_cli(mock_apm, ["infra", "storage", "list"])
    assert result.exit_code == 0
    assert "Authentication Failed" in result.output


# ── storage get ────────────────────────────────────────────────────────────


def test_storage_get_by_name(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.get_by_name.return_value = SAMPLE_REMOTE_STORAGE
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "DSM-Storage"])
    assert result.exit_code == 0
    assert "DSM-Storage" in result.output
    assert "ActiveProtect Vault (DSM)" in result.output
    assert "192.0.2.20:8444" in result.output


def test_storage_get_by_id(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.get.return_value = SAMPLE_REMOTE_STORAGE
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "--id", "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"])
    assert result.exit_code == 0
    assert "DSM-Storage" in result.output
    assert "f0d5d047-7dda-59fe-8d1b-47441c80bd1e" in result.output


def test_storage_get_no_args_shows_help() -> None:
    result = invoke_cli(AsyncMock(), ["infra", "storage", "get"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_storage_get_name_and_id_conflict(mock_apm: AsyncMock) -> None:
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "somename", "--id", "some-id"])
    assert result.exit_code == 1
    assert "cannot be used with" in result.output


def test_storage_get_json(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.get_by_name.return_value = SAMPLE_REMOTE_STORAGE
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "DSM-Storage", "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["name"] == "DSM-Storage"


def test_storage_get_shows_used_and_remaining(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.get_by_name.return_value = SAMPLE_REMOTE_STORAGE
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "DSM-Storage"])
    assert result.exit_code == 0
    assert "Used:" in result.output
    assert "Remaining:" in result.output


def test_storage_get_shows_encryption_field(mock_apm: AsyncMock) -> None:
    """storage get should display the Client-Side Encryption field."""
    mock_apm.remote_storages.get_by_name.return_value = SAMPLE_REMOTE_STORAGE
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "DSM-Storage"])
    assert result.exit_code == 0
    assert "Client-Side Encryption" in result.output
    assert "Disabled" in result.output


def test_storage_get_json_includes_encryption_enabled(mock_apm: AsyncMock) -> None:
    """storage get --output json should include the encryption_enabled field."""
    mock_apm.remote_storages.get_by_name.return_value = SAMPLE_REMOTE_STORAGE
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "DSM-Storage", "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["encryption_enabled"] is False


def test_storage_get_none_values_show_dash(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.get_by_name.return_value = AUTH_FAIL_REMOTE_STORAGE
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "DSM-Storage-auth-fail"])
    assert result.exit_code == 0
    assert "Used:      -" in result.output
    assert "Remaining: -" in result.output


def test_storage_get_not_found(mock_apm: AsyncMock) -> None:
    mock_apm.remote_storages.get_by_name.side_effect = ResourceNotFoundError(
        "not found", resource_type="RemoteStorage", resource_id="x"
    )
    result = invoke_cli(mock_apm, ["infra", "storage", "get", "no-such-storage"])
    assert result.exit_code == 1
