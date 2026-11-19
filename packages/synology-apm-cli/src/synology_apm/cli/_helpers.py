"""Shared CLI helpers — client, session wrapper, spinner, polling, Ctrl+C waiting, overwrite confirmation."""
from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Generic, Literal, TypeVar

import typer

from synology_apm.cli.errors import (
    EXIT_AUTH,
    EXIT_CANCEL,
    EXIT_ERROR,
    _dynamic_console,
    apm_error_handler,
    config_set_hint,
    err_console,
    handle_keyring_error,
    missing_config_hint,
)
from synology_apm.cli.errors import (
    abortable as _abortable,
)
from synology_apm.cli.output import cell
from synology_apm.sdk import (
    APMClient,
    KeyringUnavailableError,
    OTPIncorrectError,
    OTPRequiredError,
    resolve_connection,
)

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
    try:
        async with APMClient(
            eff_host, eff_username, eff_password,
            device_id=resolved.device_id or None,
            verify_ssl=resolved.verify_ssl,
            debug=_debug_mode,
        ) as apm:
            server = apm.my_server
            version_str = f" ({server.system_version})" if server.system_version else ""
            err_console.print(f"[dim]Connected to {server.name}{version_str}[/dim]")
            yield apm
    except (OTPRequiredError, OTPIncorrectError):
        # Ordinary commands never prompt for a two-factor code — only `config set`
        # does (see commands/config.py). A missing/stale/rejected trusted-device
        # token here always means "go complete that flow again". Only OTPRequiredError
        # is expected on this otp_code-less path; OTPIncorrectError is caught too, as in
        # _verify_connection_and_register_device, so either one gets the same message.
        err_console.print(
            "[red]✗[/red] Two-factor authentication is required and this profile has no "
            "valid trusted-device registration."
        )
        err_console.print(f"  Run: {config_set_hint(resolved.profile)}")
        raise typer.Exit(code=EXIT_AUTH) from None


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


_T = TypeVar("_T")


@dataclass(frozen=True)
class PollResult(Generic[_T]):
    """Outcome of poll_until().

    Attributes:
        outcome: ``"done"`` when is_done() held for a fetched value, ``"interrupted"`` when
            Ctrl+C stopped the polling first.
        last:    The last non-None value fetch() returned (None if there was none).
    """

    outcome: Literal["done", "interrupted"]
    last: _T | None


@contextlib.contextmanager
def _sigint_event() -> Iterator[asyncio.Event]:
    """Capture Ctrl+C as an event for the duration of the block.

    While inside, Ctrl+C sets the yielded event instead of raising KeyboardInterrupt; the
    default handler is restored on exit.
    """
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, event.set)
    except (NotImplementedError, AttributeError):  # pragma: no cover
        def _win_handler(sig: int, frame: FrameType | None) -> None:  # pragma: no cover
            loop.call_soon_threadsafe(event.set)
        signal.signal(signal.SIGINT, _win_handler)  # pragma: no cover
    try:
        yield event
    finally:
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, AttributeError):  # pragma: no cover
            signal.signal(signal.SIGINT, signal.default_int_handler)  # pragma: no cover


async def wait_for_interrupt() -> None:
    """Wait until Ctrl+C; it is captured and does not raise KeyboardInterrupt."""
    with _sigint_event() as interrupt:
        await interrupt.wait()


async def poll_until(
    fetch: Callable[[], Awaitable[_T | None]],
    is_done: Callable[[_T], bool],
    *,
    interval: float,
) -> PollResult[_T]:
    """Call fetch() every ``interval`` seconds until is_done() holds for its value, or Ctrl+C.

    A None from fetch() means "nothing yet": is_done() is not called for it and it does not
    replace the last value. Ctrl+C is captured while polling (it does not raise
    KeyboardInterrupt); the caller decides what to do through ``PollResult.outcome``.
    """
    last: _T | None = None
    with _sigint_event() as interrupt:
        while not interrupt.is_set():
            value = await fetch()
            if value is not None:
                last = value
                if is_done(value):
                    return PollResult("done", last)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(interrupt.wait(), timeout=interval)
    return PollResult("interrupted", last)


def confirm_overwrite(path: str | Path, yes: bool) -> None:
    """Ask before overwriting an existing file unless ``yes``; Exit(EXIT_CANCEL) when declined or on Ctrl+C."""
    if yes or not Path(path).exists():
        return
    err_console.print(f"[yellow]![/yellow] File already exists: {cell(str(path))}", soft_wrap=True)
    try:
        overwrite = typer.confirm("Overwrite?", default=False)
    except typer.Abort:
        overwrite = False
    if not overwrite:
        err_console.print("\n[bright_black]Cancelled.[/bright_black]")
        raise typer.Exit(EXIT_CANCEL)
