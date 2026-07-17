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
    ERROR_CODES,
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
    RemoteStorageConflictError,
    RemoteStorageEncryptionMismatchError,
    RemoteStorageInUseError,
    RemoteStorageUnmanagedCatalogError,
    ResourceNotFoundError,
    ResourceNotReadyError,
)
from tests.unit.cli.conftest import invoke_cli

# ── handle_apm_error exit code mapping ───────────────────────────────────────

# Every SDK exception type sdk.ERROR_CODES individually classifies, one instance each —
# reused below both for the exit-code table and the "never falls to the generic
# fallback" regression check, so the two stay in sync with each other by construction.
_CLASSIFIED_INSTANCES: list[APMError] = [
    AuthenticationError("bad password"),
    NotManagementServerError("not primary node"),
    BackupServerDisconnectedError("server offline"),
    ConnectionTimeoutError("timed out"),
    InvalidOperationError("not allowed", resource_type="Workload", resource_id="wl-001"),
    ResourceNotReadyError("not ready"),
    ResourceNotFoundError("not found", resource_type="Workload", resource_id="wl-001"),
    PlanNameConflictError("dup name", resource_type="Plan", resource_id="plan-001"),
    PlanInUseError("in use", resource_type="Plan", resource_id="plan-001"),
    DuplicateWorkloadError("dup", resource_type="Workload", resource_id="wl-001"),
    PermissionDeniedError("access denied"),
    NotSupportedError("feature not available"),
    RemoteStorageConflictError("vault already registered", resource_type="RemoteStorage", resource_id="MyVault"),
    RemoteStorageInUseError("storage still assigned to plans", resource_type="RemoteStorage", resource_id="storage-001"),
    RemoteStorageEncryptionMismatchError("relink key required", resource_type="RemoteStorage", resource_id="MyVault"),
    RemoteStorageUnmanagedCatalogError("unmanaged catalogs found", vault_name="MyVault", catalog_count=3),
]


def test_classified_instances_cover_every_error_code() -> None:
    """Guards the guard: a type added to sdk.ERROR_CODES without a corresponding
    instance here would silently skip both checks below."""
    assert {type(exc) for exc in _CLASSIFIED_INSTANCES} == set(ERROR_CODES)


_EXPECTED_EXIT_BY_TYPE: dict[type, int] = {
    AuthenticationError: EXIT_AUTH,
    NotManagementServerError: EXIT_CONNECT,
    BackupServerDisconnectedError: EXIT_CONNECT,
    ConnectionTimeoutError: EXIT_CONNECT,
    NotSupportedError: EXIT_NOT_SUPPORTED,
    # everything else classified defaults to EXIT_ERROR
}


@pytest.mark.parametrize("exc,expected_exit", [
    *[(exc, _EXPECTED_EXIT_BY_TYPE.get(type(exc), EXIT_ERROR)) for exc in _CLASSIFIED_INSTANCES],
    (APMError("SSL certificate verification failed"), EXIT_CONNECT),
    (APMError("cannot connect to host"),               EXIT_CONNECT),
    (APMError("some unexpected error"),                EXIT_ERROR),
])
def test_handle_apm_error_exit_code(exc: APMError, expected_exit: int) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        handle_apm_error(exc)
    assert exc_info.value.exit_code == expected_exit


@pytest.mark.parametrize("exc", _CLASSIFIED_INSTANCES)
def test_classified_exceptions_never_hit_generic_fallback(exc: APMError, capsys: pytest.CaptureFixture[str]) -> None:
    """Every type in sdk.ERROR_CODES must be handled by handle_apm_error()'s classify
    step, not silently fall through to the generic "API error: ..." substring-matching
    tail — this is the regression guard for a future SDK exception being added to
    ERROR_CODES without CLI's classify step actually reaching it."""
    with pytest.raises(typer.Exit):
        handle_apm_error(exc)
    captured = capsys.readouterr()
    assert "API error:" not in captured.err


_MESSAGE_TEXT_CASES: list[tuple[APMError, str, str]] = [
    (AuthenticationError("bad password"), "Authentication failed: bad password", ""),
    (
        ResourceNotFoundError("not found", resource_type="Workload", resource_id="wl-001"),
        "Workload not found: wl-001",
        "",
    ),
    (
        # resource_type="unknown" is the sentinel _http.py/collections use when no
        # specific resource type is known (e.g. an empty API response) — must fall
        # back to exc.message instead of printing "unknown not found: ".
        ResourceNotFoundError("empty response", resource_type="unknown", resource_id=""),
        "empty response",
        "",
    ),
    (PermissionDeniedError("access denied"), "Permission denied: access denied", ""),
    (NotSupportedError("feature not available"), "Not supported: feature not available", ""),
    (
        BackupServerDisconnectedError("server offline"),
        "Unable to perform this operation because the designated backup server is disconnected",
        "",
    ),
    (ConnectionTimeoutError("timed out"), "Connection timed out", "timed out"),
    # Plain passthrough (no _MESSAGE_BUILDERS entry) — confirms the default branch
    # is unaffected by _MESSAGE_BUILDERS changes.
    (PlanInUseError("plan in use", resource_type="Plan", resource_id="plan-001"), "plan in use", ""),
]


@pytest.mark.parametrize("exc,expected_message,expected_detail", _MESSAGE_TEXT_CASES)
def test_handle_apm_error_message_text(
    exc: APMError,
    expected_message: str,
    expected_detail: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression guard for _MESSAGE_BUILDERS: asserts the actual text printed to
    stderr for each classified type with a custom message builder (plus the
    not_found resource_type=="unknown" fallback and one plain-passthrough type), so a
    typo'd or stale _MESSAGE_BUILDERS key fails here via observable output instead of
    requiring an assertion on the private dict directly."""
    with pytest.raises(typer.Exit):
        handle_apm_error(exc)
    captured = capsys.readouterr()
    # rich wraps long lines to the terminal width, inserting its own newlines —
    # normalize before substring-matching so wrapping doesn't split the expected text.
    normalized_err = " ".join(captured.err.split())
    assert expected_message in normalized_err
    if expected_detail:
        assert expected_detail in normalized_err


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
