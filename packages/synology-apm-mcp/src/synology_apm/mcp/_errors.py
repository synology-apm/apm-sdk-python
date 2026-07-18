"""Error handling utilities for MCP tool and resource handlers."""
from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, TypeVar

from synology_apm.sdk import APMError, classify_error

_T = TypeVar("_T")

RECONFIGURE_HINT = (
    "Run `synology-apm-cli config set` to update the connection settings (or the "
    "APM_HOST/APM_USERNAME/APM_PASSWORD/APM_PROFILE environment variables, if the server "
    "was configured directly), then restart the MCP server."
)
"""Hint appended to error dicts whose failure mode is "the currently configured
credentials/connection settings don't work" -- lets the calling agent relay concrete
next steps to the user instead of just the raw error message."""

_RECONFIGURE_CODES = {
    "authentication_error",
    "not_management_server",
    "connection_timeout",
    "ssl_error",
    "connection_error",
}


def log_error(msg: str) -> None:
    """Print an error message to stderr without exiting."""
    print(f"Error: {msg}", file=sys.stderr)


def startup_error(msg: str) -> NoReturn:
    """Print an error message to stderr and exit with status 1."""
    log_error(msg)
    sys.exit(1)


def _classify_unclassified(exc: APMError) -> str:
    """Give an unclassified APIError (connection/SSL failures) a specific code.

    Mirrors the CLI's own message-substring handling in cli/errors.py::handle_apm_error --
    classify_error() deliberately leaves bare APIError unclassified since each consumer of
    the SDK has its own fallback for it (see ERROR_CODES's docstring).
    """
    msg = exc.message.lower()
    if "ssl certificate verification failed" in msg:
        return "ssl_error"
    if "connect" in msg:
        return "connection_error"
    return "apm_error"


def sdk_error_to_dict(exc: Exception) -> dict[str, Any]:
    """Convert an SDK exception to a standardized error dict."""
    if isinstance(exc, APMError):
        code = classify_error(exc) or _classify_unclassified(exc)
        result = {"error": code, **exc.to_dict()}
        if code in _RECONFIGURE_CODES:
            result["hint"] = RECONFIGURE_HINT
        return result
    if isinstance(exc, ValueError):
        return {"error": "invalid_argument", "message": str(exc)}
    return {"error": "unexpected_error", "message": str(exc)}


async def run_resource(coro: Awaitable[_T], serializer: Callable[[_T], Any]) -> str:
    """Await a coroutine, apply serializer, JSON-serialize, or return an error JSON."""
    try:
        return json.dumps(serializer(await coro), ensure_ascii=False)
    except Exception as exc:
        return json.dumps(sdk_error_to_dict(exc), ensure_ascii=False)


async def run_tool(coro: Awaitable[Any]) -> str:
    """Await a coroutine, JSON-serialize the result, or return an error JSON on exception."""
    return await run_resource(coro, lambda x: x)
