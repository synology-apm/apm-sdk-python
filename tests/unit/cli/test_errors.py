"""Unit tests for synology_apm.cli.errors: handle_apm_error exit code mapping."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import typer

from synology_apm.cli.errors import (
    EXIT_AUTH,
    EXIT_CONNECT,
    EXIT_ERROR,
    EXIT_NOT_SUPPORTED,
    handle_apm_error,
)
from synology_apm.sdk.exceptions import (
    APMError,
    AuthenticationError,
    BackupServerDisconnectedError,
    ConnectionTimeoutError,
    DuplicateWorkloadError,
    InvalidOperationError,
    NotManagementServerError,
    NotSupportedError,
    PermissionDeniedError,
    PlanInUseError,
    PlanNameConflictError,
    ResourceNotFoundError,
    ResourceNotReadyError,
)
from tests.unit.cli.conftest import invoke_cli

# ── handle_apm_error exit code mapping ───────────────────────────────────────

@pytest.mark.parametrize("exc,expected_exit", [
    (AuthenticationError("bad password"),                               EXIT_AUTH),
    (NotManagementServerError("not primary node"),                      EXIT_CONNECT),
    (BackupServerDisconnectedError("server offline"),                   EXIT_CONNECT),
    (ConnectionTimeoutError("timed out"),                               EXIT_CONNECT),
    (APMError("SSL certificate verification failed"),                   EXIT_CONNECT),
    (APMError("cannot connect to host"),                                EXIT_CONNECT),
    (InvalidOperationError("not allowed", resource_type="Workload", resource_id="wl-001"), EXIT_ERROR),
    (ResourceNotReadyError("not ready"),                                EXIT_ERROR),
    (ResourceNotFoundError("not found", resource_type="Workload", resource_id="wl-001"), EXIT_ERROR),
    (PlanNameConflictError("dup name", resource_type="Plan", resource_id="plan-001"), EXIT_ERROR),
    (PlanInUseError("in use", resource_type="Plan", resource_id="plan-001"),          EXIT_ERROR),
    (DuplicateWorkloadError("dup", resource_type="Workload", resource_id="wl-001"),   EXIT_ERROR),
    (PermissionDeniedError("access denied"),                            EXIT_ERROR),
    (NotSupportedError("feature not available"),                        EXIT_NOT_SUPPORTED),
    (APMError("some unexpected error"),                                 EXIT_ERROR),
])
def test_handle_apm_error_exit_code(exc: APMError, expected_exit: int) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        handle_apm_error(exc)
    assert exc_info.value.exit_code == expected_exit


# ── Command-level error path tests ────────────────────────────────────────────
# These verify that apm_error_handler() correctly funnels SDK exceptions
# through handle_apm_error() to the right exit code.  One or two
# representative errors per command are sufficient — the exhaustive
# subclass→exit-code mapping is already covered above.


def _make_machine_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.machine.workloads.list.return_value = ([], 0)
    mock.machine.workloads.get_by_name.return_value = None
    return mock


def test_machine_list_auth_error_exits_2() -> None:
    mock_apm = _make_machine_mock()
    mock_apm.machine.workloads.list.side_effect = AuthenticationError("bad password")
    result = invoke_cli(mock_apm, ["machine", "list"])
    assert result.exit_code == EXIT_AUTH


def test_machine_get_not_found_exits_1() -> None:
    mock_apm = _make_machine_mock()
    mock_apm.machine.workloads.get_by_name.side_effect = ResourceNotFoundError(
        "not found", resource_type="Workload", resource_id="vm-web-01"
    )
    result = invoke_cli(mock_apm, ["machine", "get", "vm-web-01"])
    assert result.exit_code == EXIT_ERROR


def test_infra_server_list_connection_timeout_exits_3(mock_apm: AsyncMock) -> None:
    mock_apm.backup_servers.list.side_effect = ConnectionTimeoutError("connection timed out")
    result = invoke_cli(mock_apm, ["infra", "server", "list"])
    assert result.exit_code == EXIT_CONNECT


def test_plan_protection_list_not_supported_exits_5(mock_apm: AsyncMock) -> None:
    mock_apm.plans.list.side_effect = NotSupportedError("feature not available")
    result = invoke_cli(mock_apm, ["plan", "protection", "list"])
    assert result.exit_code == EXIT_NOT_SUPPORTED
