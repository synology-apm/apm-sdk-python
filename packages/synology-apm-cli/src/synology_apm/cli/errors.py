"""SDK Exception → CLI error message and exit code mapping."""
from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import NoReturn, cast

import typer
from rich.console import Console

from synology_apm.sdk import APMError, KeyringUnavailableError, ResourceNotFoundError, classify_error

err_console = Console(stderr=True)

# Exit code table (see src/synology_apm/cli/README.md — Status and Color Conventions)
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2
EXIT_CONNECT = 3
EXIT_CANCEL = 4
EXIT_NOT_SUPPORTED = 5

# Exit code for each ERROR_CODES classification that isn't the EXIT_ERROR default.
_EXIT_CODE_BY_CODE: dict[str, int] = {
    "authentication_error": EXIT_AUTH,
    "not_management_server": EXIT_CONNECT,
    "backup_server_disconnected": EXIT_CONNECT,
    "connection_timeout": EXIT_CONNECT,
    "not_supported": EXIT_NOT_SUPPORTED,
}


def _exit(code: int, message: str, detail: str = "") -> NoReturn:
    err_console.print(f"[red]✗[/red] {message}")
    if detail:
        err_console.print(f"  {detail}")
    raise typer.Exit(code=code)


def _not_found_message(exc: APMError) -> tuple[str, str]:
    # classify_error() only maps ResourceNotFoundError to "not_found" at runtime (see
    # ERROR_CODES's docstring) — cast() documents that as a type-only narrowing to
    # access resource_type/resource_id, not a runtime branch.
    not_found = cast(ResourceNotFoundError, exc)
    if not_found.resource_type and not_found.resource_type != "unknown":
        return f"{not_found.resource_type} not found: {not_found.resource_id}", ""
    return exc.message, ""


# Only codes needing something other than a plain exc.message passthrough are listed
# here; every other code defaults to (exc.message, "") in _message_for.
_MESSAGE_BUILDERS: dict[str, Callable[[APMError], tuple[str, str]]] = {
    "authentication_error": lambda exc: (f"Authentication failed: {exc.message}", ""),
    "not_found": _not_found_message,
    "permission_denied": lambda exc: (f"Permission denied: {exc.message}", ""),
    "not_supported": lambda exc: (f"Not supported: {exc.message}", ""),
    "backup_server_disconnected": lambda exc: (
        "Unable to perform this operation because the designated backup server is disconnected",
        "",
    ),
    "connection_timeout": lambda exc: ("Connection timed out", exc.message),
}


def _message_for(code: str, exc: APMError) -> tuple[str, str]:
    """Return (message, detail) for an sdk.ERROR_CODES-classified exception."""
    builder = _MESSAGE_BUILDERS.get(code)
    return builder(exc) if builder is not None else (exc.message, "")


def handle_apm_error(exc: APMError) -> NoReturn:
    """Convert an SDK exception to a CLI error message and exit the process."""
    code = classify_error(exc)
    if code is not None:
        message, detail = _message_for(code, exc)
        _exit(_EXIT_CODE_BY_CODE.get(code, EXIT_ERROR), message, detail)

    # APIError and other unclassified APMErrors (backup rejected, connection issues, etc.)
    msg = exc.message
    if "ssl certificate verification failed" in msg.lower():
        _exit(
            EXIT_CONNECT,
            "SSL certificate verification failed",
            "For self-signed certificates, add the --no-verify-ssl flag "
            "or choose to skip SSL verification when running config set.",
        )
    if "connect" in msg.lower():
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
    err_console.print("    synology-apm-cli config set --host <APM_HOST> --username <USER>")
    err_console.print()
    err_console.print("  Or set environment variables:")
    err_console.print("    export APM_HOST=apm.corp.com")
    err_console.print("    export APM_USERNAME=admin")
    err_console.print("    export APM_PASSWORD=...")
    raise typer.Exit(code=EXIT_ERROR)
