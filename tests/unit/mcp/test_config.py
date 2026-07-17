"""Tests for _config.py credential loading."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from synology_apm.sdk import KeyringUnavailableError, ResolvedConnection


def _load():
    from synology_apm.mcp._config import load_credentials
    return load_credentials()


@pytest.fixture(autouse=True)
def _clear_apm_env(monkeypatch):
    for var in ("APM_HOST", "APM_USERNAME", "APM_PASSWORD", "APM_PROFILE", "APM_MCP_MODE", "APM_NO_VERIFY_SSL"):
        monkeypatch.delenv(var, raising=False)


class TestModeResolution:
    def test_defaults_to_operator(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "secret", False),
        ):
            _, _, _, _, mode = _load()
        assert mode == "operator"

    def test_all_valid_modes(self, monkeypatch):
        for mode_val in ("readonly", "operator", "manager", "admin"):
            monkeypatch.setenv("APM_MCP_MODE", mode_val)
            with patch(
                "synology_apm.mcp._config.resolve_connection",
                return_value=ResolvedConnection("apm.corp.com", "admin", "secret", False),
            ):
                _, _, _, _, mode = _load()
            assert mode == mode_val

    def test_invalid_mode_exits(self, monkeypatch):
        monkeypatch.setenv("APM_MCP_MODE", "superuser")
        with pytest.raises(SystemExit) as exc_info:
            _load()
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
            host, username, password, verify_ssl, _ = _load()

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
            _, _, _, verify_ssl, _ = _load()
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
            _, _, _, verify_ssl, _ = _load()
        assert verify_ssl is False

    def test_missing_host_exits(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("", "admin", "secret", False),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _load()
        assert exc_info.value.code == 1

    def test_missing_username_exits(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "", "secret", False),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _load()
        assert exc_info.value.code == 1

    def test_missing_password_exits(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            return_value=ResolvedConnection("apm.corp.com", "admin", "", False),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _load()
        assert exc_info.value.code == 1

    def test_keyring_unavailable_error_exits_cleanly(self, monkeypatch):
        with patch(
            "synology_apm.mcp._config.resolve_connection",
            side_effect=KeyringUnavailableError("no keyring backend"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _load()
        assert exc_info.value.code == 1
