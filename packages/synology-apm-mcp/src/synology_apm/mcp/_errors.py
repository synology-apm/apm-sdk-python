"""Error handling utilities for MCP tool and resource handlers."""
from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, TypeVar

from synology_apm.sdk import APMError, classify_error

_T = TypeVar("_T")


def startup_error(msg: str) -> NoReturn:
    """Print an error message to stderr and exit with status 1."""
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def sdk_error_to_dict(exc: Exception) -> dict[str, Any]:
    """Convert an SDK exception to a standardized error dict."""
    if isinstance(exc, APMError):
        code = classify_error(exc)
        return {"error": code or "apm_error", **exc.to_dict()}
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
