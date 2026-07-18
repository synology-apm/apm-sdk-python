"""Unit tests for synology_apm.cli/errors.py — APM exception → exit code mapping."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import typer

from synology_apm.cli.errors import (
    EXIT_AUTH,
    EXIT_CANCEL,
    EXIT_CONNECT,
    EXIT_ERROR,
    EXIT_NOT_SUPPORTED,
    handle_apm_error,
    missing_config_hint,
)
from synology_apm.sdk import AppConfig, ProfileConfig
from synology_apm.sdk.exceptions import (
    APIError,
    AuthenticationError,
    BackupServerDisconnectedError,
    ConnectionTimeoutError,
    InvalidOperationError,
    NotSupportedError,
    PermissionDeniedError,
    ResourceNotFoundError,
)


def _exit_code(exc: Exception) -> int:
    """Return the typer.Exit code raised by handle_apm_error."""
    with pytest.raises(typer.Exit) as exc_info:
        handle_apm_error(exc)  # type: ignore[arg-type]
    return exc_info.value.exit_code


# ── Exit code constants ────────────────────────────────────────────────────


def test_exit_constants_values() -> None:
    assert EXIT_AUTH == 2
    assert EXIT_CONNECT == 3
    assert EXIT_CANCEL == 4
    assert EXIT_NOT_SUPPORTED == 5
    assert EXIT_ERROR == 1


# ── handle_apm_error() exit codes ─────────────────────────────────────────


def test_authentication_error_exits_2() -> None:
    assert _exit_code(AuthenticationError("wrong credentials", error_code=400)) == EXIT_AUTH


def test_invalid_operation_exits_1() -> None:
    assert _exit_code(
        InvalidOperationError("Workload 'X' is already retired.", resource_type="Workload", resource_id="wl-123")
    ) == EXIT_ERROR


def test_invalid_operation_uses_exception_message(capsys: pytest.CaptureFixture[str]) -> None:
    """InvalidOperationError should display exc.message directly (not resource_type/id)."""
    exc = InvalidOperationError(
        "Cannot apply a protection plan to retired workload 'MyPC'.",
        resource_type="Workload",
        resource_id="wl-123",
    )
    with pytest.raises(typer.Exit):
        handle_apm_error(exc)
    captured = capsys.readouterr()
    assert "Cannot apply a protection plan to retired workload 'MyPC'." in captured.err


def test_resource_not_found_exits_1() -> None:
    assert _exit_code(
        ResourceNotFoundError("not found", resource_type="Workload", resource_id="wl-123")
    ) == EXIT_ERROR


def test_permission_denied_exits_1() -> None:
    assert _exit_code(PermissionDeniedError("forbidden", error_code=105)) == EXIT_ERROR


def test_not_supported_exits_5() -> None:
    assert _exit_code(NotSupportedError("501 not supported", error_code=501)) == EXIT_NOT_SUPPORTED


def test_generic_api_error_exits_1() -> None:
    assert _exit_code(APIError("unknown error", error_code=999)) == EXIT_ERROR


def test_connect_error_in_message_exits_3() -> None:
    """APIError whose message contains 'connect' should map to EXIT_CONNECT."""
    assert _exit_code(APIError("Connection refused")) == EXIT_CONNECT


def test_connection_error_in_message_exits_3() -> None:
    assert _exit_code(APIError("connection timeout")) == EXIT_CONNECT


def test_backup_server_disconnected_exits_3() -> None:
    assert _exit_code(BackupServerDisconnectedError("The designated backup server is disconnected.", error_code=2003)) == EXIT_CONNECT


def test_backup_server_disconnected_message(capsys: pytest.CaptureFixture[str]) -> None:
    """BackupServerDisconnectedError should display the user-facing disconnection message."""
    with pytest.raises(typer.Exit):
        handle_apm_error(BackupServerDisconnectedError("The designated backup server is disconnected.", error_code=2003))
    captured = capsys.readouterr()
    assert "designated backup server" in captured.err.lower()


def test_connection_timeout_error_exits_3() -> None:
    assert _exit_code(ConnectionTimeoutError("Request to https://apm.corp.com timed out")) == EXIT_CONNECT


def test_connection_timeout_error_message_says_timed_out(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        handle_apm_error(ConnectionTimeoutError("Request to https://apm.corp.com timed out"))
    captured = capsys.readouterr()
    assert "timed out" in captured.err.lower()
    assert "cannot connect" not in captured.err.lower()


# ── handle_apm_error() message content ────────────────────────────────────


def test_authentication_error_includes_message(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        handle_apm_error(AuthenticationError("account locked"))


def test_resource_not_found_includes_resource_type(capsys: pytest.CaptureFixture[str]) -> None:
    """ResourceNotFoundError with a known resource_type shows 'Type not found: id'."""
    exc = ResourceNotFoundError("not found", resource_type="ProtectionPlan", resource_id="plan-xyz")
    with pytest.raises(typer.Exit):
        handle_apm_error(exc)
    captured = capsys.readouterr()
    assert "ProtectionPlan not found: plan-xyz" in captured.err


def test_resource_not_found_unknown_type_uses_message(capsys: pytest.CaptureFixture[str]) -> None:
    """ResourceNotFoundError with resource_type='unknown' (HTTP 404) falls back to exc.message."""
    exc = ResourceNotFoundError("Resource not found", resource_type="unknown", resource_id="", error_code=404)
    with pytest.raises(typer.Exit):
        handle_apm_error(exc)
    captured = capsys.readouterr()
    assert "Resource not found" in captured.err
    assert "unknown" not in captured.err


def test_not_supported_includes_message(capsys: pytest.CaptureFixture[str]) -> None:
    exc = NotSupportedError("M365 API not available")
    with pytest.raises(typer.Exit):
        handle_apm_error(exc)


# ── missing_config_hint() ─────────────────────────────────────────────────


def test_missing_config_hint_default_profile_no_other_profiles(capsys: pytest.CaptureFixture[str]) -> None:
    """With only the default profile attempted and no other profiles configured, the hint
    names the default profile, offers a bare `config set`, and lists env vars including
    APM_NO_VERIFY_SSL — but never mentions APM_PROFILE, since selecting a profile that
    doesn't exist wouldn't fix anything."""
    with patch("synology_apm.cli.errors.load_config", return_value=AppConfig()):
        with pytest.raises(typer.Exit) as exc_info:
            missing_config_hint()
    assert exc_info.value.exit_code == EXIT_ERROR

    captured = capsys.readouterr().err
    assert "for profile 'default'" in captured
    assert "Configured profiles found" not in captured
    assert "synology-apm-cli config set\n" in captured
    assert "--profile" not in captured
    assert "APM_PROFILE" not in captured
    assert "export APM_NO_VERIFY_SSL=true" in captured


def test_missing_config_hint_lists_other_configured_profiles(capsys: pytest.CaptureFixture[str]) -> None:
    """When other profiles already exist in config.toml, the hint surfaces them as a
    real, working fix (--profile <name> / APM_PROFILE=<name>)."""
    cfg = AppConfig(profiles={"prod": ProfileConfig(host="apm.corp.com", username="admin")})
    with patch("synology_apm.cli.errors.load_config", return_value=cfg):
        with pytest.raises(typer.Exit):
            missing_config_hint()

    captured = capsys.readouterr().err
    assert "Configured profiles found: prod" in captured
    assert "--profile <name> or APM_PROFILE=<name>" in captured


def test_missing_config_hint_non_default_profile_requested(capsys: pytest.CaptureFixture[str]) -> None:
    """When a non-default profile was requested and isn't configured, the config set
    example includes --profile <name> so the user configures the right profile."""
    with patch("synology_apm.cli.errors.load_config", return_value=AppConfig()):
        with pytest.raises(typer.Exit) as exc_info:
            missing_config_hint("prod")
    assert exc_info.value.exit_code == EXIT_ERROR

    captured = capsys.readouterr().err
    assert "for profile 'prod'" in captured
    assert "synology-apm-cli config set --profile prod" in captured


# ── APMError.__repr__ ──────────────────────────────────────────────────────


def test_api_error_repr_without_response_body() -> None:
    exc = APIError("disk full", error_code=500)
    r = repr(exc)
    assert "APIError" in r
    assert "disk full" in r
    assert "500" in r
    assert "response_body" not in r


def test_api_error_repr_with_response_body() -> None:
    exc = APIError("bad request", error_code=400, response_body={"detail": "x"})
    r = repr(exc)
    assert "APIError" in r
    assert "bad request" in r
    assert "response_body" in r


# ── APMError.__str__ with non-serializable body ────────────────────────────


def test_api_error_str_with_non_serializable_body() -> None:
    exc = APIError("oops", error_code=500, response_body=b"\xff\xfe")
    s = str(exc)
    assert "oops" in s
    assert "Response body" in s


# ── handle_apm_error: SSL certificate path ────────────────────────────────


def test_ssl_certificate_error_exits_3() -> None:
    assert _exit_code(APIError("SSL certificate verification failed for https://x")) == EXIT_CONNECT
