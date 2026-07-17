"""Configuration file read/write — $XDG_CONFIG_HOME/synology-apm/config.toml.

Shared by synology-apm-cli and synology-apm-mcp so both consumers resolve
connection settings from the same profile store and keyring entries.

Priority (high → low):
  1. Caller-supplied values (CLI options / explicit arguments)
  2. Environment variables (APM_HOST, APM_USERNAME, APM_PASSWORD, APM_PROFILE, APM_NO_VERIFY_SSL)
  3. Config file ($XDG_CONFIG_HOME/synology-apm/config.toml, default ~/.config/synology-apm/config.toml)
"""
from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import keyring
import keyring.errors
import tomli_w

from .exceptions import KeyringUnavailableError


def _config_base_dir() -> Path:
    """Return the XDG config base directory.

    Per the XDG Base Directory Specification: `$XDG_CONFIG_HOME` is used when set to a
    non-empty absolute path; an unset, empty, or relative value falls back to `~/.config`.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg and Path(xdg).is_absolute():
        return Path(xdg)
    return Path.home() / ".config"


CONFIG_DIR = _config_base_dir() / "synology-apm"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_PROFILE = "default"

# Stable, documented keyring "service" naming convention (see the CLI README's
# Authentication Configuration section) — kept as "synology-apm-cli" even though
# this module is now shared, so existing pre-seeded credentials keep working.
KEYRING_SERVICE_PREFIX = "synology-apm-cli"


class PasswordStorage(Enum):
    """Where a profile's password is persisted, if at all."""

    NONE = "none"
    PLAINTEXT = "plaintext"
    KEYRING = "keyring"


def _keyring_service(profile: str) -> str:
    """Return the keyring 'service' identifier for a profile's stored password.

    Format: ``synology-apm-cli:<profile>``. This is a stable, documented convention
    (see the CLI README's Authentication Configuration section) that can be used to
    pre-seed a credential with the ``keyring`` CLI tool directly, e.g.
    ``keyring set synology-apm-cli:<profile> <username>``.
    """
    return f"{KEYRING_SERVICE_PREFIX}:{profile}"


def set_keyring_password(profile: str, username: str, password: str) -> None:
    """Store a profile's password in the OS keyring.

    Raises:
        KeyringUnavailableError: When the OS keyring backend is unavailable or the write fails.
    """
    try:
        keyring.set_password(_keyring_service(profile), username, password)
    except keyring.errors.KeyringError as exc:
        raise KeyringUnavailableError(f"Could not save the password to the OS keyring: {exc}") from exc


def _get_keyring_password(profile: str, username: str) -> str | None:
    try:
        return keyring.get_password(_keyring_service(profile), username)
    except keyring.errors.KeyringError as exc:
        raise KeyringUnavailableError(f"Could not read the password from the OS keyring: {exc}") from exc


def delete_keyring_password(profile: str, username: str) -> bool:
    """Delete the profile's OS keyring entry.

    Returns True when the entry is gone afterwards (deleted, or it did not
    exist); False when the keyring backend failed and a stored credential may
    remain.
    """
    try:
        keyring.delete_password(_keyring_service(profile), username)
    except keyring.errors.PasswordDeleteError:
        return True
    except keyring.errors.KeyringError:
        return False
    return True


@dataclass
class ProfileConfig:
    """Settings for a single profile."""

    host: str = ""
    username: str = ""
    password: str = ""
    no_verify_ssl: bool = False
    password_storage: PasswordStorage = PasswordStorage.NONE

    def is_complete(self) -> bool:
        """Returns True when both host and username are set."""
        return bool(self.host and self.username)


@dataclass
class AppConfig:
    """Container for all profile settings."""

    profiles: dict[str, ProfileConfig] = field(default_factory=dict)

    def get_profile(self, name: str) -> ProfileConfig:
        """Return the specified profile; returns an empty ProfileConfig if it does not exist."""
        return self.profiles.get(name, ProfileConfig())

    def set_profile(self, name: str, profile: ProfileConfig) -> None:
        """Store a profile (in-memory only; call save_config to persist)."""
        self.profiles[name] = profile

    def remove_profile(self, name: str) -> bool:
        """Remove a profile; returns True if a profile was actually removed."""
        if name in self.profiles:
            del self.profiles[name]
            return True
        return False


def load_config() -> AppConfig:
    """Load all profile settings from config.toml."""
    if not CONFIG_FILE.exists():
        return AppConfig()

    with CONFIG_FILE.open("rb") as f:
        raw = tomllib.load(f)

    profiles: dict[str, ProfileConfig] = {}
    for section, values in raw.items():
        if isinstance(values, dict):
            stored_password = values.get("password", "")
            storage_raw = values.get("password_storage")
            if storage_raw is not None:
                storage = PasswordStorage(storage_raw)
            elif stored_password:
                # Pre-existing config file written before password_storage existed.
                storage = PasswordStorage.PLAINTEXT
            else:
                storage = PasswordStorage.NONE
            profiles[section] = ProfileConfig(
                host=values.get("host", ""),
                username=values.get("username", ""),
                password=stored_password,
                no_verify_ssl=bool(values.get("no_verify_ssl", False)),
                password_storage=storage,
            )
    return AppConfig(profiles=profiles)


def save_config(config: AppConfig) -> None:
    """Write all profile settings to config.toml.

    The config file may hold a plaintext password, so it is written with
    owner-only permissions (directory 0700, file 0600) and replaced atomically:
    a failed write never leaves a truncated config.toml behind.
    """
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)

    raw: dict[str, dict[str, object]] = {}
    for name, profile in config.profiles.items():
        section: dict[str, object] = {}
        if profile.host:
            section["host"] = profile.host
        if profile.username:
            section["username"] = profile.username
        if profile.password_storage != PasswordStorage.KEYRING and profile.password:
            section["password"] = profile.password
        if profile.password_storage != PasswordStorage.NONE:
            section["password_storage"] = profile.password_storage.value
        if profile.no_verify_ssl:
            section["no_verify_ssl"] = True
        raw[name] = section

    # mkstemp creates the file with 0600 regardless of umask; os.replace then
    # carries those permissions onto config.toml.
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(raw, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class ResolvedConnection:
    """Connection settings resolved by :func:`resolve_connection`.

    Attributes:
        host: Resolved host (hostname or ``host:port``, no scheme); empty string if unresolved.
        username: Resolved username; empty string if unresolved.
        password: Resolved password; may be an empty string (caller must handle this separately).
        no_verify_ssl: Whether to skip TLS certificate verification.
    """

    host: str
    username: str
    password: str
    no_verify_ssl: bool


def resolve_connection(
    *,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    profile: str | None = None,
    no_verify_ssl: bool | None = None,
) -> ResolvedConnection:
    """Resolve connection settings by priority.

    Priority: caller-supplied values > environment variables > config file (where a
    config-file profile's password may itself be stored in plaintext or looked up
    from the OS keyring).

    Raises:
        KeyringUnavailableError: When the profile's password is stored in the OS keyring and the
            backend is unavailable or the lookup fails; the caller handles the error message.

    Returns:
        A :class:`ResolvedConnection`. Its ``password`` may be an empty string
        (caller must handle this separately).
    """
    # 1. Determine which profile to use
    effective_profile = (
        profile
        or os.environ.get("APM_PROFILE", "")
        or DEFAULT_PROFILE
    )

    # 2. Load the profile base from the config file
    cfg = load_config()
    file_profile = cfg.get_profile(effective_profile)

    # 3. Merge settings from all layers (higher priority overrides lower)
    effective_host = (
        host
        or os.environ.get("APM_HOST", "")
        or file_profile.host
    )
    effective_username = (
        username
        or os.environ.get("APM_USERNAME", "")
        or file_profile.username
    )
    effective_password = (
        password
        or os.environ.get("APM_PASSWORD", "")
        or file_profile.password
    )
    if not effective_password and file_profile.password_storage == PasswordStorage.KEYRING:
        effective_password = _get_keyring_password(effective_profile, file_profile.username) or ""

    # no_verify_ssl: caller-supplied True > environment variable > config file
    if no_verify_ssl:
        effective_no_verify = True
    elif os.environ.get("APM_NO_VERIFY_SSL", "").strip().lower() in ("1", "true", "yes"):
        effective_no_verify = True
    else:
        effective_no_verify = file_profile.no_verify_ssl

    return ResolvedConnection(effective_host, effective_username, effective_password, effective_no_verify)
