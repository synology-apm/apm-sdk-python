"""Credential loading for the MCP server.

Connection settings are resolved via the SDK's shared `resolve_connection()`
(env vars → ~/.config/synology-apm/config.toml, the same profile store and
keyring entries synology-apm-cli uses). APM_MCP_MODE controls the operation
mode (default: operator) and is MCP-specific, resolved here.
"""
from __future__ import annotations

import os

from synology_apm.mcp._errors import startup_error
from synology_apm.sdk import KeyringUnavailableError, resolve_connection

_VALID_MODES = {"readonly", "operator", "manager", "admin"}


def _resolve_mode() -> str:
    """Return APM_MCP_MODE (default "operator"), or exit if it's not a recognized mode."""
    mode = os.environ.get("APM_MCP_MODE", "operator").strip()
    if mode not in _VALID_MODES:
        startup_error(
            f"APM_MCP_MODE={mode!r} is not valid. "
            f"Choose one of: {', '.join(sorted(_VALID_MODES))}"
        )
    return mode


def load_credentials() -> tuple[str, str, str, bool, str]:
    """Return (host, username, password, verify_ssl, mode).

    Calls startup_error() (sys.exit(1)) if required credentials are missing,
    the profile's keyring-stored password cannot be read, or APM_MCP_MODE is
    unrecognized.
    """
    mode = _resolve_mode()

    try:
        resolved = resolve_connection(
            host=os.environ.get("APM_HOST") or None,
            username=os.environ.get("APM_USERNAME") or None,
            password=os.environ.get("APM_PASSWORD") or None,
            profile=os.environ.get("APM_PROFILE") or None,
        )
    except KeyringUnavailableError as exc:
        startup_error(f"{exc} Set APM_PASSWORD instead, or use a plaintext-stored profile.")

    if not resolved.host or not resolved.username:
        startup_error(
            "Missing credentials. Set APM_HOST, APM_USERNAME, APM_PASSWORD env vars, "
            "or create ~/.config/synology-apm/config.toml."
        )
    if not resolved.password:
        startup_error(
            "No password found. Set APM_PASSWORD or configure password_storage in the config file."
        )

    return resolved.host, resolved.username, resolved.password, not resolved.no_verify_ssl, mode
