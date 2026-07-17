"""Unit tests for synology_apm.sdk.config — config file read/write and resolve_connection priority order."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import keyring.errors
import pytest

from synology_apm.sdk import (
    AppConfig,
    KeyringUnavailableError,
    PasswordStorage,
    ProfileConfig,
    delete_keyring_password,
    load_config,
    resolve_connection,
    save_config,
    set_keyring_password,
)

# ── CONFIG_DIR / XDG_CONFIG_HOME resolution ─────────────────────────────────
# CONFIG_DIR is resolved once at module import time, so these exercise it via
# a subprocess rather than patching — the only way to observe the actual
# public behavior (not the private _config_base_dir() helper) for different
# XDG_CONFIG_HOME values.


def _config_dir_in_subprocess(env: dict[str, str]) -> str:
    result = subprocess.run(
        [sys.executable, "-c", "from synology_apm.sdk.config import CONFIG_DIR; print(CONFIG_DIR)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_config_dir_uses_xdg_config_home_when_set(tmp_path: Path) -> None:
    """CONFIG_DIR should resolve under $XDG_CONFIG_HOME when set to a non-empty absolute path."""
    xdg_dir = tmp_path / "xdg"
    env = {**os.environ, "XDG_CONFIG_HOME": str(xdg_dir)}
    assert _config_dir_in_subprocess(env) == str(xdg_dir / "synology-apm")


def test_config_dir_falls_back_to_home_config_when_xdg_unset() -> None:
    """CONFIG_DIR should fall back to ~/.config/synology-apm when XDG_CONFIG_HOME is unset."""
    env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
    assert _config_dir_in_subprocess(env) == str(Path.home() / ".config" / "synology-apm")


def test_config_dir_falls_back_to_home_config_when_xdg_empty() -> None:
    """CONFIG_DIR should fall back to ~/.config/synology-apm when XDG_CONFIG_HOME is empty."""
    env = {**os.environ, "XDG_CONFIG_HOME": ""}
    assert _config_dir_in_subprocess(env) == str(Path.home() / ".config" / "synology-apm")


def test_config_dir_ignores_relative_xdg_config_home(tmp_path: Path) -> None:
    """A relative XDG_CONFIG_HOME should be treated as unset, per the XDG Base Directory Specification."""
    env = {**os.environ, "XDG_CONFIG_HOME": "relative/path"}
    assert _config_dir_in_subprocess(env) == str(Path.home() / ".config" / "synology-apm")


# ── load_config / save_config ──────────────────────────────────────────────


def test_load_config_missing_file(tmp_path: Path) -> None:
    """Should return an empty AppConfig when the config file does not exist."""
    with patch("synology_apm.sdk.config.CONFIG_FILE", tmp_path / "config.toml"):
        cfg = load_config()
    assert cfg.profiles == {}


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Loaded config after save should equal the saved config."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://10.0.0.1", username="admin"))
    cfg.set_profile("lab", ProfileConfig(host="https://10.0.0.2", username="admin", no_verify_ssl=True))

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        loaded = load_config()

    assert loaded.get_profile("default").host == "https://10.0.0.1"
    assert loaded.get_profile("default").username == "admin"
    assert loaded.get_profile("default").no_verify_ssl is False
    assert loaded.get_profile("lab").host == "https://10.0.0.2"
    assert loaded.get_profile("lab").no_verify_ssl is True


def test_save_and_load_password_roundtrip(tmp_path: Path) -> None:
    """The password field should round-trip correctly through save and load."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://10.0.0.1", username="admin", password="s3cr3t"))

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        loaded = load_config()

    assert loaded.get_profile("default").password == "s3cr3t"


def test_save_omits_empty_password(tmp_path: Path) -> None:
    """An empty password should not be written to config.toml."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://10.0.0.1", username="admin"))

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        content = cfg_file.read_text()

    assert "password" not in content


def test_save_creates_directory(tmp_path: Path) -> None:
    """save_config should automatically create a missing config directory."""
    cfg_dir = tmp_path / "nested" / "apm"
    cfg_file = cfg_dir / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://h", username="u"))

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", cfg_dir),
    ):
        save_config(cfg)

    assert cfg_file.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_save_sets_owner_only_permissions(tmp_path: Path) -> None:
    """save_config should restrict the config directory to 0700 and the file to 0600."""
    cfg_dir = tmp_path / "apm"
    cfg_file = cfg_dir / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://h", username="u", password="s3cr3t"))

    old_umask = os.umask(0o000)  # permissive umask must not widen the result
    try:
        with (
            patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
            patch("synology_apm.sdk.config.CONFIG_DIR", cfg_dir),
        ):
            save_config(cfg)
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_save_tightens_existing_loose_permissions(tmp_path: Path) -> None:
    """save_config over a pre-existing world-readable config should end up owner-only."""
    cfg_dir = tmp_path / "apm"
    cfg_dir.mkdir(mode=0o755)
    cfg_file = cfg_dir / "config.toml"
    cfg_file.write_text("[default]\nhost = 'h'\n")
    cfg_file.chmod(0o644)
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://h", username="u"))

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", cfg_dir),
    ):
        save_config(cfg)

    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600


def test_save_failure_preserves_existing_file(tmp_path: Path) -> None:
    """A failed write must leave the previous config.toml intact and no temp file behind."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[default]\nhost = 'old'\n")
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://new", username="u"))

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
        patch("synology_apm.sdk.config.tomli_w.dump", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        save_config(cfg)

    assert cfg_file.read_text() == "[default]\nhost = 'old'\n"
    assert list(tmp_path.iterdir()) == [cfg_file]


def test_save_leaves_no_temp_file_on_success(tmp_path: Path) -> None:
    """After a successful save only config.toml remains in the config directory."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://h", username="u"))

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)

    assert list(tmp_path.iterdir()) == [cfg_file]


# ── PasswordStorage / keyring persistence ──────────────────────────────────


def test_load_config_old_format_password_only_infers_plaintext(tmp_path: Path) -> None:
    """A pre-existing config file with only a `password` key (no `password_storage`) should infer PLAINTEXT."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\nhost = "h"\nusername = "u"\npassword = "secret"\n')

    with patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file):
        loaded = load_config()

    profile = loaded.get_profile("default")
    assert profile.password == "secret"
    assert profile.password_storage == PasswordStorage.PLAINTEXT


def test_load_config_no_password_no_storage_key_infers_none(tmp_path: Path) -> None:
    """A profile with neither `password` nor `password_storage` should infer NONE."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\nhost = "h"\nusername = "u"\n')

    with patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file):
        loaded = load_config()

    assert loaded.get_profile("default").password_storage == PasswordStorage.NONE


def test_save_keyring_storage_omits_plaintext_password(tmp_path: Path) -> None:
    """A KEYRING-storage profile should never write a plaintext password to the config file."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="h", username="u", password="leaked", password_storage=PasswordStorage.KEYRING),
    )

    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        content = cfg_file.read_text()
        loaded = load_config()

    assert "leaked" not in content
    assert 'password_storage = "keyring"' in content
    assert loaded.get_profile("default").password_storage == PasswordStorage.KEYRING
    assert loaded.get_profile("default").password == ""


# ── AppConfig helpers ──────────────────────────────────────────────────────


def test_get_profile_missing_returns_empty() -> None:
    cfg = AppConfig()
    p = cfg.get_profile("nonexistent")
    assert p.host == ""
    assert p.username == ""
    assert p.no_verify_ssl is False


def test_remove_profile_existing() -> None:
    cfg = AppConfig()
    cfg.set_profile("lab", ProfileConfig(host="https://h", username="u"))
    removed = cfg.remove_profile("lab")
    assert removed is True
    assert "lab" not in cfg.profiles


def test_remove_profile_missing() -> None:
    cfg = AppConfig()
    removed = cfg.remove_profile("ghost")
    assert removed is False


def test_profile_config_is_complete() -> None:
    assert ProfileConfig(host="https://h", username="u").is_complete() is True
    assert ProfileConfig(host="https://h", username="").is_complete() is False
    assert ProfileConfig(host="", username="u").is_complete() is False
    assert ProfileConfig().is_complete() is False


# ── resolve_connection priority order ──────────────────────────────────────


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("APM_HOST", "APM_USERNAME", "APM_PASSWORD", "APM_PROFILE", "APM_NO_VERIFY_SSL"):
        monkeypatch.delenv(key, raising=False)


def test_resolve_uses_cli_args_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Caller-supplied arguments should take priority over environment variables and config file."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("APM_HOST", "https://env-host")
    monkeypatch.setenv("APM_USERNAME", "env-user")

    cfg_file = tmp_path / "config.toml"
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        resolved = resolve_connection(
            host="https://cli-host",
            username="cli-user",
            password="cli-pass",
        )

    assert resolved.host == "https://cli-host"
    assert resolved.username == "cli-user"
    assert resolved.password == "cli-pass"


def test_resolve_uses_env_over_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Environment variables should take priority over the config file."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("APM_HOST", "https://env-host")
    monkeypatch.setenv("APM_USERNAME", "env-user")
    monkeypatch.setenv("APM_PASSWORD", "env-pass")

    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://file-host", username="file-user"))
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        resolved = resolve_connection()

    assert resolved.host == "https://env-host"
    assert resolved.username == "env-user"
    assert resolved.password == "env-pass"


def test_resolve_falls_back_to_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Should fall back to the config file (including password) when no CLI args or env vars are present."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="https://file-host", username="file-user", password="file-pass"),
    )
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        resolved = resolve_connection()

    assert resolved.host == "https://file-host"
    assert resolved.username == "file-user"
    assert resolved.password == "file-pass"


def test_resolve_password_env_over_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """APM_PASSWORD environment variable should take priority over the password in the config file."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("APM_PASSWORD", "env-pass")

    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="https://file-host", username="file-user", password="file-pass"),
    )
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        resolved = resolve_connection()

    assert resolved.password == "env-pass"


def test_resolve_password_cli_over_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Caller-supplied password should take priority over environment variables and config file."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("APM_PASSWORD", "env-pass")

    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="https://file-host", username="file-user", password="file-pass"),
    )
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        resolved = resolve_connection(password="cli-pass")

    assert resolved.password == "cli-pass"


def test_resolve_profile_selection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When a profile is specified, its config should be used."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="https://default", username="default-user"))
    cfg.set_profile("lab", ProfileConfig(host="https://lab-host", username="lab-user"))
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        resolved = resolve_connection(profile="lab")

    assert resolved.host == "https://lab-host"
    assert resolved.username == "lab-user"


def test_resolve_profile_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """APM_PROFILE environment variable should select the profile."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("APM_PROFILE", "lab")

    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile("lab", ProfileConfig(host="https://lab-host", username="lab-user"))
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        resolved = resolve_connection()

    assert resolved.host == "https://lab-host"
    assert resolved.username == "lab-user"


def test_resolve_no_verify_ssl_cli_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Caller-supplied no_verify_ssl=True should take priority over all other sources."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        resolved = resolve_connection(no_verify_ssl=True)
    assert resolved.no_verify_ssl is True


def test_resolve_no_verify_ssl_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """APM_NO_VERIFY_SSL=true should set no_verify_ssl."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("APM_NO_VERIFY_SSL", "true")
    cfg_file = tmp_path / "config.toml"
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        resolved = resolve_connection()
    assert resolved.no_verify_ssl is True


def test_resolve_no_verify_ssl_from_env_with_whitespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A trailing space (common from copy-pasted .env values) must not silently
    disable the flag and fall back to verifying SSL. no_verify_ssl=False is passed
    explicitly (not omitted) to match the CLI's real call site (cli/_helpers.py),
    which always passes a concrete bool, never None, when --no-verify-ssl isn't given."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("APM_NO_VERIFY_SSL", "true ")
    cfg_file = tmp_path / "config.toml"
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        resolved = resolve_connection(no_verify_ssl=False)
    assert resolved.no_verify_ssl is True


def test_resolve_no_verify_ssl_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """no_verify_ssl=true in the config file should take effect."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="https://h", username="u", no_verify_ssl=True),
    )
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        save_config(cfg)
        resolved = resolve_connection()
    assert resolved.no_verify_ssl is True


def test_resolve_no_verify_ssl_default_false(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """no_verify_ssl should default to False when not set from any source."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
    ):
        resolved = resolve_connection()
    assert resolved.no_verify_ssl is False


def test_resolve_connection_keyring_lookup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A profile with KEYRING storage should resolve its password via the OS keyring."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="https://h", username="u", password_storage=PasswordStorage.KEYRING),
    )
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
        patch("synology_apm.sdk.config.keyring.get_password", return_value="keyring-pass") as mock_get,
    ):
        save_config(cfg)
        resolved = resolve_connection()

    assert resolved.password == "keyring-pass"
    mock_get.assert_called_once_with("synology-apm-cli:default", "u")


def test_resolve_connection_keyring_skipped_when_cli_password_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The keyring should not be queried when a higher-priority password is already given."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="https://h", username="u", password_storage=PasswordStorage.KEYRING),
    )
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
        patch("synology_apm.sdk.config.keyring.get_password") as mock_get,
    ):
        save_config(cfg)
        resolved = resolve_connection(password="cli-pass")

    assert resolved.password == "cli-pass"
    mock_get.assert_not_called()


def test_set_keyring_password_calls_keyring_set() -> None:
    """set_keyring_password should store the password under the profile's stable service name."""
    with patch("synology_apm.sdk.config.keyring.set_password") as mock_set:
        set_keyring_password("default", "u", "s3cr3t")

    mock_set.assert_called_once_with("synology-apm-cli:default", "u", "s3cr3t")


def test_set_keyring_password_wraps_keyring_error() -> None:
    """A KeyringError from the backend should surface as KeyringUnavailableError."""
    with (
        patch("synology_apm.sdk.config.keyring.set_password", side_effect=keyring.errors.KeyringLocked()),
        pytest.raises(KeyringUnavailableError),
    ):
        set_keyring_password("default", "u", "s3cr3t")


def test_delete_keyring_password_success_returns_true() -> None:
    """A clean delete should return True."""
    with patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete:
        result = delete_keyring_password("default", "u")

    mock_delete.assert_called_once_with("synology-apm-cli:default", "u")
    assert result is True


def test_delete_keyring_password_already_absent_returns_true() -> None:
    """PasswordDeleteError (no entry to delete) should still be treated as success."""
    with patch(
        "synology_apm.sdk.config.keyring.delete_password",
        side_effect=keyring.errors.PasswordDeleteError(),
    ):
        result = delete_keyring_password("default", "u")

    assert result is True


def test_delete_keyring_password_backend_failure_returns_false() -> None:
    """A backend-level KeyringError (not PasswordDeleteError) should return False."""
    with patch(
        "synology_apm.sdk.config.keyring.delete_password",
        side_effect=keyring.errors.KeyringLocked(),
    ):
        result = delete_keyring_password("default", "u")

    assert result is False


def test_resolve_connection_keyring_error_propagates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A keyring backend error should propagate as KeyringUnavailableError."""
    _clean_env(monkeypatch)
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="https://h", username="u", password_storage=PasswordStorage.KEYRING),
    )
    with (
        patch("synology_apm.sdk.config.CONFIG_FILE", cfg_file),
        patch("synology_apm.sdk.config.CONFIG_DIR", tmp_path),
        patch("synology_apm.sdk.config.keyring.get_password", side_effect=keyring.errors.KeyringLocked()),
    ):
        save_config(cfg)
        with pytest.raises(KeyringUnavailableError):
            resolve_connection()
