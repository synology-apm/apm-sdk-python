"""Security utilities: operation mode, audit log, and destructive-tool confirmation."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from synology_apm.mcp._errors import sdk_error_to_dict

_LEVELS: dict[str, int] = {"readonly": 0, "operator": 1, "manager": 2, "admin": 3}

DESTRUCTIVE_PREVIEW_SUFFIX = "When confirm=false (default), returns a preview. Pass confirm=true to execute."
"""Append to a destructive tool's description= so every confirm/preview tool
documents the same confirm=false/true contract in the same words, instead of
each call site retyping (and risking drift from) this sentence."""


def mode_allows(required: str, current: str) -> bool:
    """Return True when current mode is at least as permissive as required."""
    return _LEVELS.get(current, 0) >= _LEVELS.get(required, 0)


def audit_log(action: str, params: dict[str, Any], outcome: str) -> None:
    """Append one JSON line to the audit log file, if APM_MCP_AUDIT_LOG is set."""
    log_path = os.environ.get("APM_MCP_AUDIT_LOG", "")
    if not log_path:
        return
    entry = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": action,
        "params": params,
        "outcome": outcome,
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        print(f"Warning: audit log write failed: {exc}", file=sys.stderr)


def confirm_or_preview(action: str, target: dict[str, Any], warning: str) -> dict[str, Any]:
    """Return a dry-run preview dict (no execution)."""
    return {
        "preview": True,
        "action": action,
        "target": target,
        "warning": warning,
    }


async def run_audited_tool(coro: Awaitable[Any], action: str, params: dict[str, Any]) -> str:
    """Await a mutation coroutine, record the outcome in the audit log, return JSON."""
    try:
        result = await coro
        await asyncio.to_thread(audit_log, action, params, "ok")
        return json.dumps(result if result is not None else {}, ensure_ascii=False)
    except Exception as exc:
        await asyncio.to_thread(audit_log, action, params, f"error: {type(exc).__name__}")
        return json.dumps(sdk_error_to_dict(exc), ensure_ascii=False)


async def destructive_tool(
    confirm: bool,
    action: str,
    warning: str,
    resolve_coro: Awaitable[Any],
    preview_target_fn: Callable[[Any], dict[str, Any]],
    execute_fn: Callable[[Any], Awaitable[Any]],
    params: dict[str, Any],
) -> str:
    """Generic handler for admin+confirm tools: resolve → preview-or-execute.

    The resolve step and preview-building step are both wrapped in try/except
    so a ResourceNotFoundError during lookup, or any failure while building the
    preview, returns our standardized error JSON rather than propagating
    unhandled. When confirm=False, returns a dry-run preview. When confirm=True,
    calls execute_fn(target) via run_audited_tool().

    A resolve/preview failure is audit-logged only when confirm=True: that is
    an attempted action that failed, matching the outcome recorded for an
    execute-time failure. A confirm=False failure never attempted anything
    (only a preview was requested), so it is not logged.
    """
    try:
        target = await resolve_coro
        if not confirm:
            preview = confirm_or_preview(action, preview_target_fn(target), warning)
            return json.dumps(preview, ensure_ascii=False)
    except Exception as exc:
        if confirm:
            await asyncio.to_thread(audit_log, action, params, f"error: {type(exc).__name__}")
        return json.dumps(sdk_error_to_dict(exc), ensure_ascii=False)
    return await run_audited_tool(execute_fn(target), action, params)
