"""Configuration file read/write — ~/.config/synology-apm/config.toml.

Priority (high → low):
  1. CLI options (passed by the caller)
  2. Environment variables (APM_HOST, APM_USERNAME, APM_PASSWORD, APM_PROFILE, APM_NO_VERIFY_SSL)
  3. Config file (~/.config/synology-apm/config.toml)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef, import-not-found]

import keyring
import keyring.errors
import tomli_w

CONFIG_DIR = Path.home() / ".config" / "synology-apm"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_PROFILE = "default"

KEYRING_SERVICE_PREFIX = "synology-apm-cli"


class PasswordStorage(StrEnum):
    """Where a profile's password is persisted, if at all."""

    NONE = "none"
    PLAINTEXT = "plaintext"
    KEYRING = "keyring"


class KeyringUnavailableError(RuntimeError):
    """Raised when the OS keyring backend is unavailable or an operation fails."""


def _keyring_service(profile: str) -> str:
    """Return the keyring 'service' identifier for a profile's stored password.

    Format: ``synology-apm-cli:<profile>``. This is a stable, documented convention
    (see the CLI README's Authentication Configuration section) that can be used to
    pre-seed a credential with the ``keyring`` CLI tool directly, e.g.
    ``keyring set synology-apm-cli:<profile> <username>``.
    """
    return f"{KEYRING_SERVICE_PREFIX}:{profile}"


def _set_keyring_password(profile: str, username: str, password: str) -> None:
    try:
        keyring.set_password(_keyring_service(profile), username, password)
    except keyring.errors.KeyringError as exc:
        raise KeyringUnavailableError(f"Could not save the password to the OS keyring: {exc}") from exc


def _get_keyring_password(profile: str, username: str) -> str | None:
    try:
        return keyring.get_password(_keyring_service(profile), username)
    except keyring.errors.KeyringError as exc:
        raise KeyringUnavailableError(f"Could not read the password from the OS keyring: {exc}") from exc


def _delete_keyring_password(profile: str, username: str) -> None:
    """Best-effort delete; swallows "not found" and backend-unavailable errors."""
    try:
        keyring.delete_password(_keyring_service(profile), username)
    except keyring.errors.KeyringError:
        pass


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
    """Write all profile settings to config.toml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

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

    with CONFIG_FILE.open("wb") as f:
        tomli_w.dump(raw, f)


def resolve_connection(
    *,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    profile: str | None = None,
    no_verify_ssl: bool | None = None,
) -> tuple[str, str, str, bool]:
    """Resolve connection settings by priority and return (host, username, password, no_verify_ssl).

    Priority: CLI options > environment variables > config file (where a config-file
    profile's password may itself be stored in plaintext or looked up from the OS keyring).

    Raises:
        SystemExit: When required connection info cannot be obtained; the caller handles the error message.
        KeyringUnavailableError: When the profile's password is stored in the OS keyring and the
            backend is unavailable or the lookup fails; the caller handles the error message.

    Returns:
        (host, username, password, no_verify_ssl) tuple.
        password may be an empty string (caller must handle this separately).
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

    # no_verify_ssl: CLI True > environment variable > config file
    if no_verify_ssl:
        effective_no_verify = True
    elif os.environ.get("APM_NO_VERIFY_SSL", "").lower() in ("1", "true", "yes"):
        effective_no_verify = True
    else:
        effective_no_verify = file_profile.no_verify_ssl

    return effective_host, effective_username, effective_password, effective_no_verify
