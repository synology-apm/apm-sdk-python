"""Tests for argument validation edge cases not covered by existing command test files."""
from __future__ import annotations

from unittest.mock import AsyncMock

from tests.unit.cli.conftest import invoke_cli

# ── activity backup list ──────────────────────────────────────────────────────

def test_activity_backup_list_invalid_since_exits_nonzero(mock_apm: AsyncMock) -> None:
    """--since with a value that cannot be parsed exits non-zero."""
    result = invoke_cli(mock_apm, ["activity", "backup", "list", "--since", "notaduration"])
    assert result.exit_code != 0


def test_activity_restore_list_invalid_since_exits_nonzero(mock_apm: AsyncMock) -> None:
    """--since with a value that cannot be parsed exits non-zero for restore list."""
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--since", "bad-value"])
    assert result.exit_code != 0


def test_activity_restore_list_invalid_status_exits_1(mock_apm: AsyncMock) -> None:
    """activity restore list --status <invalid> should exit with code 1."""
    result = invoke_cli(mock_apm, ["activity", "restore", "list", "--status", "nope"])
    assert result.exit_code == 1


# ── plan protection list ──────────────────────────────────────────────────────

def test_plan_protection_list_invalid_category_exits_1(mock_apm: AsyncMock) -> None:
    """plan protection list --category <invalid> should exit with code 1."""
    result = invoke_cli(mock_apm, ["plan", "protection", "list", "--category", "bad_category"])
    assert result.exit_code == 1
    assert "Invalid category" in result.output


# ── machine version ───────────────────────────────────────────────────────────

def test_machine_version_list_id_without_namespace_exits_1() -> None:
    """machine version list --id X (no --namespace) should exit 1."""
    result = invoke_cli(AsyncMock(), [
        "machine", "version", "list", "--id", "wl-001",
    ])
    assert result.exit_code == 1
    assert "--namespace" in result.output


def test_machine_version_list_name_and_id_conflict_exits_1() -> None:
    """machine version list NAME --id X --namespace Y should exit 1."""
    result = invoke_cli(AsyncMock(), [
        "machine", "version", "list", "vm-web-01",
        "--id", "wl-001", "--namespace", "ns-001",
    ])
    assert result.exit_code == 1
    assert "--id" in result.output or "--namespace" in result.output
