"""asyncio.run() wrapper so Typer commands can call the async SDK.

Typer does not natively support async commands; this wrapper bridges the gap.
"""
from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


def run_async(fn: F) -> Callable[..., Any]:
    """Wrap an async function as a synchronous function (for use as a Typer command)."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))
    return wrapper
