"""Shared CLI helpers — client initialization, session wrapper, and spinner."""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import typer

from synology_apm.cli.errors import (
    EXIT_ERROR,
    _dynamic_console,
    apm_error_handler,
    err_console,
    handle_keyring_error,
    missing_config_hint,
)
from synology_apm.cli.errors import (
    abortable as _abortable,
)
from synology_apm.sdk import APMClient, KeyringUnavailableError, resolve_connection

_spinner_console = _dynamic_console(stderr=True)

_debug_mode: bool = False


def enable_debug() -> None:
    """Enable debug mode (called by the --debug flag)."""
    global _debug_mode
    _debug_mode = True


def is_debug() -> bool:
    """Return whether debug mode is currently enabled."""
    return _debug_mode


@contextlib.asynccontextmanager
async def get_client(ctx: typer.Context) -> AsyncIterator[APMClient]:
    """Resolve connection settings by priority and create an APMClient."""
    obj: dict[str, Any] = ctx.obj or {}
    if obj.get("debug"):
        enable_debug()
    try:
        resolved = resolve_connection(
            host=obj.get("host"),
            username=obj.get("username"),
            password=obj.get("password"),
            profile=obj.get("profile"),
            no_verify_ssl=obj.get("no_verify_ssl"),
        )
        eff_host, eff_username, eff_password = resolved.host, resolved.username, resolved.password
    except KeyringUnavailableError as exc:
        handle_keyring_error(
            exc, "Set the APM_PASSWORD environment variable instead, or use a plaintext-stored profile."
        )
    if not resolved.is_complete():
        missing_config_hint(resolved.profile)  # NoReturn
    if not eff_password:
        if obj.get("no_input"):
            err_console.print("[red]✗[/red] Password is required. Set APM_PASSWORD or use a config profile.")
            raise typer.Exit(code=EXIT_ERROR)
        eff_password = typer.prompt("Password", hide_input=True)
    async with APMClient(
        eff_host, eff_username, eff_password,
        verify_ssl=resolved.verify_ssl,
        debug=_debug_mode,
    ) as apm:
        server = apm.my_server
        version_str = f" ({server.system_version})" if server.system_version else ""
        err_console.print(f"[dim]Connected to {server.name}{version_str}[/dim]")
        yield apm


@contextlib.asynccontextmanager
async def apm_session(
    ctx: typer.Context,
    *,
    spinner: str | None = None,
    abortable: bool = False,
) -> AsyncIterator[APMClient]:
    """Open an APMClient session wrapped in the standard CLI context stack.

    Combines apm_error_handler → api_spinner (when ``spinner`` is given) →
    get_client into one context manager; ``abortable=True`` adds the outermost
    abortable() layer so a declined confirmation exits cleanly with EXIT_CANCEL.
    """
    with contextlib.ExitStack() as stack:
        if abortable:
            stack.enter_context(_abortable())
        stack.enter_context(apm_error_handler())
        if spinner is not None:
            stack.enter_context(api_spinner(spinner))
        async with get_client(ctx) as apm:
            yield apm


@contextlib.contextmanager
def api_spinner(message: str = "Loading...") -> Iterator[None]:
    """Show a transient spinner on stderr while waiting for API responses.

    Automatically suppressed when stderr is not a TTY (CI, piped output).
    """
    with _spinner_console.status(f"[dim]{message}[/dim]"):
        yield
