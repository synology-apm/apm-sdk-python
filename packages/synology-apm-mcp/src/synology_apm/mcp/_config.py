"""Credential loading for the MCP server.

Connection settings are resolved via the SDK's shared `resolve_connection()`
(env vars → ~/.config/synology-apm/config.toml, the same profile store and
keyring entries synology-apm-cli uses). APM_MCP_MODE controls the operation
mode (default: operator) and is MCP-specific, resolved here.
"""
from __future__ import annotations

import os

from synology_apm.mcp._errors import log_error, startup_error
from synology_apm.sdk import AuthenticationError, KeyringUnavailableError, resolve_connection

_VALID_MODES = {"readonly", "operator", "manager", "admin"}


def resolve_mode() -> str:
    """Return APM_MCP_MODE (default "operator"), or exit if it's not a recognized mode.

    Resolved independently of load_credentials(): an unrecognized mode is a deployment
    misconfiguration, not a "no usable credentials" condition, and there is no sensible
    tool set to register without knowing which mode was intended, so it still exits
    (sys.exit(1)) rather than degrading gracefully.
    """
    mode = os.environ.get("APM_MCP_MODE", "operator").strip()
    if mode not in _VALID_MODES:
        startup_error(
            f"APM_MCP_MODE={mode!r} is not valid. "
            f"Choose one of: {', '.join(sorted(_VALID_MODES))}"
        )
    return mode


def load_credentials() -> tuple[str, str, str, bool]:
    """Return (host, username, password, verify_ssl).

    Raises:
        AuthenticationError: No usable connection settings could be resolved -- missing
            host/username, missing password, or the profile's keyring-stored password
            could not be read. The message is also printed to stderr here, so a manually-
            launched server still surfaces it immediately. The caller (see __main__.py)
            catches this and starts the server without attempting a real connection (see
            _server.py's build_lifespan()), so every tool call returns this same error as
            a structured JSON response instead of the process exiting before any MCP
            session exists.
    """
    try:
        resolved = resolve_connection(
            host=os.environ.get("APM_HOST") or None,
            username=os.environ.get("APM_USERNAME") or None,
            password=os.environ.get("APM_PASSWORD") or None,
            profile=os.environ.get("APM_PROFILE") or None,
        )
    except KeyringUnavailableError as exc:
        msg = (
            f"{exc} Set APM_PASSWORD instead, or run `uvx synology-apm-cli config set "
            "--save-password plaintext`."
        )
        log_error(msg)
        raise AuthenticationError(msg) from exc

    if not resolved.host or not resolved.username:
        msg = (
            "Missing credentials. Run `uvx synology-apm-cli config set` to configure a profile, "
            "set APM_HOST, APM_USERNAME, APM_PASSWORD, APM_NO_VERIFY_SSL env vars directly, "
            "or select a different configured profile via APM_PROFILE."
        )
        log_error(msg)
        raise AuthenticationError(msg)
    if not resolved.password:
        msg = (
            "No password found. Run `uvx synology-apm-cli config set` to store one, set "
            "APM_PASSWORD directly, or select a different configured profile via APM_PROFILE."
        )
        log_error(msg)
        raise AuthenticationError(msg)

    return resolved.host, resolved.username, resolved.password, not resolved.no_verify_ssl
