"""Error handling utilities for MCP tool and resource handlers."""
from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, TypeAlias, TypeVar

from fastmcp.exceptions import ToolError

from synology_apm.sdk import APMError, classify_error

_T = TypeVar("_T")

ToolResult: TypeAlias = dict[str, Any]
"""The structured result every MCP tool returns; FastMCP derives each tool's outputSchema
from this annotation and populates structuredContent from the returned dict directly."""

RECONFIGURE_HINT = (
    "Run `uvx synology-apm-cli config set` to update the connection settings, or if "
    "configured directly via environment variables, fix APM_HOST/APM_USERNAME/"
    "APM_PASSWORD/APM_NO_VERIFY_SSL (or select a different configured profile via "
    "APM_PROFILE), then restart the MCP server."
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


def raise_tool_error(exc: Exception) -> NoReturn:
    """Raise a ToolError carrying the JSON-encoded standardized error dict as its message.

    Surfaces as isError=true to the MCP client (FastMCP passes an explicitly raised
    ToolError through untouched), while keeping the message text machine-parseable JSON
    for any client that only reads the message.
    """
    raise ToolError(json.dumps(sdk_error_to_dict(exc), ensure_ascii=False)) from exc


async def run_resource(coro: Awaitable[_T], serializer: Callable[[_T], ToolResult]) -> ToolResult:
    """Await a coroutine and apply serializer, or raise a ToolError on exception."""
    try:
        return serializer(await coro)
    except Exception as exc:
        raise_tool_error(exc)


async def run_tool(coro: Awaitable[Any]) -> ToolResult:
    """Await a coroutine and return its result, or raise a ToolError on exception."""
    return await run_resource(coro, lambda x: x)
