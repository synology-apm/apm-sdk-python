"""Tests for _config.py credential loading."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from synology_apm.sdk import AuthenticationError, KeyringUnavailableError, ResolvedConnection


def _load():
    from synology_apm.mcp._config import load_credentials
    return load_credentials()


def _resolve_mode():
    from synology_apm.mcp._config import resolve_mode
    return resolve_mode()


@pytest.fixture(autouse=True)
def _clear_apm_env(monkeypatch):
    for var in ("APM_HOST", "APM_USERNAME", "APM_PASSWORD", "APM_PROFILE", "APM_MCP_MODE", "APM_NO_VERIFY_SSL"):
        monkeypatch.delenv(var, raising=False)


class TestModeResolution:
    def test_defaults_to_operator(self, monkeypatch):
        assert _resolve_mode() == "operator"

    def test_all_valid_modes(self, monkeypatch):
        for mode_val in ("readonly", "operator", "manager", "admin"):
            monkeypatch.setenv("APM_MCP_MODE", mode_val)
            assert _resolve_mode() == mode_val

    def test_invalid_mode_exits(self, monkeypatch):
        monkeypatch.setenv("APM_MCP_MODE", "superuser")
        with pytest.raises(SystemExit) as exc_info:
            _resolve_mode()
        assert exc_info.value.code == 1


class TestConnectionResolution:
    def test_delegates_to_resolve_connection_with_env_values(self, monkeypatch):
        monkeypatch.setenv("APM_HOST", "apm.corp.com")
        monkeypatch.setenv("APM_USERNAME", "admin")
        monkeypatch.setenv("APM_PASSWORD", "secret")
        monkeypatch.setenv("APM_PROFILE", "lab")

        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", False),
        ) as mock_resolve:
            host, username, password, verify_ssl = _load()

        mock_resolve.assert_called_once_with(
            host="apm.corp.com",
            username="admin",
            password="secret",
            profile="lab",
        )
        assert host == "apm.corp.com"
        assert username == "admin"
        assert password == "secret"
        assert verify_ssl is True

    def test_no_verify_ssl_not_passed_to_resolve_connection(self, monkeypatch):
        """MCP no longer pre-parses APM_NO_VERIFY_SSL itself -- it defers entirely to
        resolve_connection()'s own env-var handling by omitting the kwarg."""
        monkeypatch.setenv("APM_NO_VERIFY_SSL", "true")
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", True),
        ) as mock_resolve:
            _, _, _, verify_ssl = _load()
        assert "no_verify_ssl" not in mock_resolve.call_args.kwargs
        assert verify_ssl is False

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
            _, _, _, verify_ssl = _load()
        assert verify_ssl is False

    def test_missing_host_raises_authentication_error(self, monkeypatch):
        """Missing credentials must not crash the process -- the caller (__main__.py)
        catches this and starts the server in degraded mode (see _server.py's
        build_lifespan())."""
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("", "admin", "secret", False),
        ):
            with pytest.raises(AuthenticationError):
                _load()

    def test_missing_username_raises_authentication_error(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "", "secret", False),
        ):
            with pytest.raises(AuthenticationError):
                _load()

    def test_missing_password_raises_authentication_error(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "", False),
        ):
            with pytest.raises(AuthenticationError):
                _load()

    def test_keyring_unavailable_raises_authentication_error(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            side_effect=KeyringUnavailableError("no keyring backend"),
        ):
            with pytest.raises(AuthenticationError):
                _load()

    def test_successful_resolution_returns_four_tuple(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", False),
        ):
            result = _load()
        assert len(result) == 4


class TestErrorMessagesPointToCli:
    """Error messages (both the raised exception and the stderr line) must name the
    concrete next step (synology-apm-cli config set), not just describe what's missing --
    see README's Configure APM Credentials / Troubleshooting sections, which this message
    text is meant to match."""

    def test_missing_host_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("", "admin", "secret", False),
        ):
            with pytest.raises(AuthenticationError) as exc_info:
                _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err

    def test_missing_username_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "", "secret", False),
        ):
            with pytest.raises(AuthenticationError) as exc_info:
                _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err

    def test_missing_password_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "", False),
        ):
            with pytest.raises(AuthenticationError) as exc_info:
                _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err

    def test_keyring_unavailable_message_points_to_cli(self, monkeypatch, capsys):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            side_effect=KeyringUnavailableError("no keyring backend"),
        ):
            with pytest.raises(AuthenticationError) as exc_info:
                _load()
        assert "synology-apm-cli config set" in str(exc_info.value)
        assert "synology-apm-cli config set" in capsys.readouterr().err
