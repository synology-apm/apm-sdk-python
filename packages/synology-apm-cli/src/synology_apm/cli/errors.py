"""SDK Exception → CLI error message and exit code mapping."""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import NoReturn

import typer
from rich.console import Console

from synology_apm.cli.config import KeyringUnavailableError
from synology_apm.sdk import (
    APMError,
    AuthenticationError,
    BackupServerDisconnectedError,
    ConnectionTimeoutError,
    DuplicateWorkloadError,
    InvalidOperationError,
    NotManagementServerError,
    NotSupportedError,
    PermissionDeniedError,
    PlanInUseError,
    PlanNameConflictError,
    ResourceNotFoundError,
    ResourceNotReadyError,
)

err_console = Console(stderr=True)

# Exit code table (see src/synology_apm/cli/README.md — Status and Color Conventions)
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2
EXIT_CONNECT = 3
EXIT_CANCEL = 4
EXIT_NOT_SUPPORTED = 5


def _exit(code: int, message: str, detail: str = "") -> NoReturn:
    err_console.print(f"[red]✗[/red] {message}")
    if detail:
        err_console.print(f"  {detail}")
    raise typer.Exit(code=code)


def handle_apm_error(exc: APMError) -> NoReturn:
    """Convert an SDK exception to a CLI error message and exit the process."""
    if isinstance(exc, AuthenticationError):
        _exit(EXIT_AUTH, f"Authentication failed: {exc.message}")

    if isinstance(exc, NotManagementServerError):
        _exit(EXIT_CONNECT, exc.message)

    if isinstance(exc, InvalidOperationError):
        _exit(EXIT_ERROR, exc.message)

    if isinstance(exc, ResourceNotReadyError):
        _exit(EXIT_ERROR, exc.message)

    if isinstance(exc, ResourceNotFoundError):
        if exc.resource_type and exc.resource_type != "unknown":
            _exit(EXIT_ERROR, f"{exc.resource_type} not found: {exc.resource_id}")
        else:
            _exit(EXIT_ERROR, exc.message)

    if isinstance(exc, PlanNameConflictError):
        _exit(EXIT_ERROR, exc.message)

    if isinstance(exc, PlanInUseError):
        _exit(EXIT_ERROR, exc.message)

    if isinstance(exc, DuplicateWorkloadError):
        _exit(EXIT_ERROR, exc.message)

    if isinstance(exc, PermissionDeniedError):
        _exit(EXIT_ERROR, f"Permission denied: {exc.message}")

    if isinstance(exc, NotSupportedError):
        _exit(EXIT_NOT_SUPPORTED, f"Not supported: {exc.message}")

    if isinstance(exc, BackupServerDisconnectedError):
        _exit(EXIT_CONNECT, "Unable to perform this operation because the designated backup server is disconnected")

    if isinstance(exc, ConnectionTimeoutError):
        _exit(EXIT_CONNECT, "Connection timed out", exc.message)

    # APIError and other APMErrors (backup rejected, connection issues, etc.)
    msg = exc.message
    if "ssl certificate verification failed" in msg.lower():
        _exit(
            EXIT_CONNECT,
            "SSL certificate verification failed",
            "For self-signed certificates, add the --no-verify-ssl flag "
            "or choose to skip SSL verification when running config set.",
        )
    if "cannot connect" in msg.lower() or "connect" in msg.lower() or "connection" in msg.lower():
        _exit(EXIT_CONNECT, msg)

    _exit(EXIT_ERROR, f"API error: {msg}")


def handle_keyring_error(exc: KeyringUnavailableError, hint: str = "") -> NoReturn:
    """Convert a KeyringUnavailableError to a CLI error message and exit the process."""
    _exit(EXIT_ERROR, str(exc), hint)


@contextlib.contextmanager
def apm_error_handler() -> Iterator[None]:
    """Context manager that converts APMError and ValueError exceptions to CLI error messages.

    Usage::

        with apm_error_handler():
            async with get_client(ctx) as apm:
                ...
    """
    try:
        yield
    except Exception as exc:
        if isinstance(exc, APMError):
            handle_apm_error(exc)
        if isinstance(exc, ValueError):
            _exit(EXIT_ERROR, str(exc))
        raise


@contextlib.contextmanager
def abortable() -> Iterator[None]:
    """Convert a user-cancelled confirmation (typer.Abort) into a clean message and Exit(4).

    Wrap this OUTSIDE apm_error_handler so a declined ``typer.confirm(..., abort=True)``
    prints "Cancelled." and exits with EXIT_CANCEL instead of a traceback::

        with abortable():
            with apm_error_handler():
                async with get_client(ctx) as apm:
                    ...
    """
    try:
        yield
    except typer.Abort:
        err_console.print("\n[bright_black]Cancelled.[/bright_black]")
        raise typer.Exit(code=EXIT_CANCEL)


def missing_config_hint() -> NoReturn:
    """Display a hint when connection settings are missing and exit."""
    err_console.print("[red]✗[/red] Connection settings not configured")
    err_console.print()
    err_console.print("  Run first:")
    err_console.print("    synology-apm config set --host <APM_HOST> --username <USER>")
    err_console.print()
    err_console.print("  Or set environment variables:")
    err_console.print("    export APM_HOST=apm.corp.com")
    err_console.print("    export APM_USERNAME=admin")
    err_console.print("    export APM_PASSWORD=...")
    raise typer.Exit(code=EXIT_ERROR)
