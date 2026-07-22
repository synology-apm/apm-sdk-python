"""Tests for _config.py credential loading."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from synology_apm.sdk import AuthenticationError, KeyringUnavailableError, ResolvedConnection


def _load(profile=None):
    from synology_apm.mcp._config import load_credentials
    return load_credentials(profile=profile)


def _resolve_mode():
    from synology_apm.mcp._config import resolve_mode
    return resolve_mode()


def _resolve_startup_state(profile=None):
    from synology_apm.mcp._config import resolve_startup_state
    return resolve_startup_state(profile)


@pytest.fixture(autouse=True)
def _clear_apm_env(monkeypatch):
    for var in ("APM_HOST", "APM_USERNAME", "APM_PASSWORD", "APM_PROFILE", "APM_MCP_MODE", "APM_NO_VERIFY_SSL"):
        monkeypatch.delenv(var, raising=False)


class TestModeResolution:
    def test_defaults_to_operator(self, monkeypatch):
        assert _resolve_mode() == "operator"

    def test_all_valid_modes(self, monkeypatch):
        for mode_val in ("readonly", "operator", "admin"):
            monkeypatch.setenv("APM_MCP_MODE", mode_val)
            assert _resolve_mode() == mode_val

    def test_invalid_mode_exits(self, monkeypatch):
        monkeypatch.setenv("APM_MCP_MODE", "superuser")
        with pytest.raises(SystemExit) as exc_info:
            _resolve_mode()
        assert exc_info.value.code == 1


class TestConnectionResolution:
    def test_delegates_to_resolve_connection_with_profile_only(self, monkeypatch):
        """load_credentials() no longer pre-reads APM_HOST/APM_USERNAME/APM_PASSWORD
        itself -- it defers entirely to resolve_connection()'s own env-var handling,
        forwarding only the profile parameter it received."""
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", True),
        ) as mock_resolve:
            resolved = _load(profile="lab")

        mock_resolve.assert_called_once_with(profile="lab")
        assert resolved.host == "apm.corp.com"
        assert resolved.username == "admin"
        assert resolved.password == "secret"
        assert resolved.verify_ssl is True

    def test_delegates_to_resolve_connection_with_no_profile(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", True),
        ) as mock_resolve:
            _load()
        mock_resolve.assert_called_once_with(profile=None)

    def test_no_verify_ssl_env_flag_with_trailing_whitespace(self, monkeypatch, tmp_path):
        """A trailing space (common from copy-pasted .env values) must not silently
        disable the flag and fall back to verifying SSL. Exercises the real (unmocked)
        resolve_connection() end-to-end, since MCP no longer pre-parses this env var
        itself -- the whitespace-stripping now lives entirely in the SDK."""
        monkeypatch.setenv("APM_HOST", "apm.corp.com")
        monkeypatch.setenv("APM_USERNAME", "admin")
        monkeypatch.setenv("APM_PASSWORD", "secret")
        monkeypatch.setenv("APM_NO_VERIFY_SSL", "true ")
        cfg_file = tmp_path / "config.toml"
        with (
            patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
            patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
        ):
            resolved = _load()
        assert resolved.verify_ssl is False

    def test_missing_host_raises_authentication_error(self, monkeypatch):
        """Missing credentials must not crash the process -- the caller (__main__.py)
        catches this and starts the server in degraded mode (see _server.py's
        build_lifespan())."""
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("", "admin", "secret", True),
        ), pytest.raises(AuthenticationError):
            _load()

    def test_missing_username_raises_authentication_error(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "", "secret", True),
        ), pytest.raises(AuthenticationError):
            _load()

    def test_missing_password_raises_authentication_error(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "", True),
        ), pytest.raises(AuthenticationError):
            _load()

    def test_keyring_unavailable_raises_authentication_error(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            side_effect=KeyringUnavailableError("no keyring backend"),
        ), pytest.raises(AuthenticationError):
            _load()

    def test_successful_resolution_returns_resolved_connection(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", True),
        ):
            result = _load()
        assert result.host == "apm.corp.com"
        assert result.username == "admin"
        assert result.password == "secret"
        assert result.verify_ssl is True


class TestErrorMessagesPointToCli:
    """Error messages (both the raised exception and the stderr line) must name the
    concrete next step (synology-apm-cli config set), not just describe what's missing --
    see README's Configure APM Credentials / Troubleshooting sections, which this message
    text is meant to match."""

    def test_missing_host_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("", "admin", "secret", True),
        ), pytest.raises(AuthenticationError) as exc_info:
            _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err

    def test_missing_username_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "", "secret", True),
        ), pytest.raises(AuthenticationError) as exc_info:
            _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err

    def test_missing_password_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "", True),
        ), pytest.raises(AuthenticationError) as exc_info:
            _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err

    def test_keyring_unavailable_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            side_effect=KeyringUnavailableError("no keyring backend"),
        ), pytest.raises(AuthenticationError) as exc_info:
            _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err


class TestResolveStartupState:
    """resolve_startup_state() composes resolve_mode() + load_credentials(), including
    the degraded-mode fallback -- pulled out of __main__.main() so this logic is
    unit-testable without invoking FastMCP's stdio transport loop."""

    def test_success_returns_resolved_mode_and_no_error(self, monkeypatch):
        monkeypatch.setenv("APM_MCP_MODE", "admin")
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", True),
        ):
            resolved, mode, config_error = _resolve_startup_state(profile="lab")
        assert resolved.host == "apm.corp.com"
        assert resolved.username == "admin"
        assert resolved.password == "secret"
        assert resolved.verify_ssl is True
        assert mode == "admin"
        assert config_error is None

    def test_forwards_profile_to_load_credentials(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", True),
        ) as mock_resolve:
            _resolve_startup_state(profile="lab")
        mock_resolve.assert_called_once_with(profile="lab")

    def test_authentication_error_returns_placeholder_and_prints_degraded_notice(
        self, monkeypatch, capsys
    ):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("", "", "", True),
        ):
            resolved, mode, config_error = _resolve_startup_state()
        assert resolved.host == ""
        assert resolved.username == ""
        assert resolved.password == ""
        assert resolved.verify_ssl is True
        assert mode == "operator"
        assert isinstance(config_error, AuthenticationError)
        assert "Continuing in degraded mode" in capsys.readouterr().err
