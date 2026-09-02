"""Shared helpers for the APM SDK example scripts.

This module is not part of the SDK; it only collects boilerplate that every
example would otherwise duplicate (credential loading, byte/duration formatting,
workload-type labels, pagination, and the interrupt/progress primitives used by
the concurrent download scripts). It imports only the public ``synology_apm.sdk`` API.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import sys
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import FrameType
from typing import Any, NoReturn, TypeVar

from synology_apm.sdk import (
    APMClient,
    APMError,
    BackupServer,
    GWSDomainInfo,
    GWSWorkload,
    GWSWorkloadType,
    KeyringUnavailableError,
    M365TenantInfo,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    MachineWorkloadType,
    resolve_connection,
)

T = TypeVar("T")

Category = str  # "machine" | "m365" | "gws" | "all"


# ── Credentials / entry point ───────────────────────────────────────────────

def make_client(
    *,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    profile: str | None = None,
    no_verify_ssl: bool | None = None,
) -> APMClient:
    """Build an APMClient; each argument overrides the matching value resolve_connection() finds.

    Raises KeyError if a required value is still missing, or KeyringUnavailableError if a
    profile's keyring-stored password can't be read; run_main() turns both into a friendly
    message.
    """
    resolved = resolve_connection(
        host=host, username=username, password=password,
        profile=profile, no_verify_ssl=no_verify_ssl,
    )
    for env_name, value in (
        ("APM_HOST", resolved.host),
        ("APM_USERNAME", resolved.username),
        ("APM_PASSWORD", resolved.password),
    ):
        if not value:
            raise KeyError(env_name)
    return APMClient(
        resolved.host, resolved.username, resolved.password, verify_ssl=resolved.verify_ssl
    )


def run_main(coro: Coroutine[Any, Any, int | None]) -> NoReturn:
    """Run *coro* under asyncio with the error handling every example shares.

    Exits with the int the coroutine returns (0 if it returns None), maps APMError
    to exit code 1, and prints a hint when a required value is unset or unreadable.
    """
    try:
        result = asyncio.run(coro)
        sys.exit(result or 0)
    except APMError as e:
        print(f"APM error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
    except KeyError as e:
        var = e.args[0]
        print(f"Missing {var}.", file=sys.stderr)
        print(
            f"  Set it via `uv run --env-file .env ...` / export {var}=<value>, or "
            "configure a profile: synology-apm-cli config set",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyringUnavailableError as e:
        print(f"Keyring error: {e}", file=sys.stderr)
        print(
            "  Set APM_PASSWORD directly, or run: "
            "synology-apm-cli config set --save-password plaintext",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Formatting ──────────────────────────────────────────────────────────────

def fmt_bytes(n: int | None) -> str:
    """Human-readable byte size; '—' for None."""
    if n is None:
        return "—"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(f) < 1024.0:
            return f"{f:.1f} {unit}"
        f /= 1024.0
    return f"{f:.1f} PB"


def fmt_duration(seconds: float | None) -> str:
    """HH:MM:SS from a second count; '—' for None."""
    if seconds is None:
        return "—"
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_speed(size_bytes: int, duration_secs: float) -> str:
    """Transfer rate; '—' when duration is non-positive."""
    if duration_secs <= 0:
        return "—"
    bps = size_bytes / duration_secs
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if abs(bps) < 1024.0:
            return f"{bps:.1f} {unit}"
        bps /= 1024.0
    return f"{bps:.1f} TB/s"


def fmt_compact_duration(d: timedelta) -> str:
    """Convert a timedelta to a compact duration string: e.g. '2d', '6h', '30m'."""
    secs = int(d.total_seconds())
    if secs % 86400 == 0:
        return f"{secs // 86400}d"
    if secs % 3600 == 0:
        return f"{secs // 3600}h"
    return f"{max(1, secs // 60)}m"


def parse_compact_duration(s: str) -> timedelta:
    """Parse a compact duration string ('30m', '6h', '2d') into a timedelta."""
    s = s.strip()
    if s.endswith("d") and s[:-1].isdigit():
        return timedelta(days=int(s[:-1]))
    if s.endswith("h") and s[:-1].isdigit():
        return timedelta(hours=int(s[:-1]))
    if s.endswith("m") and s[:-1].isdigit():
        return timedelta(minutes=int(s[:-1]))
    raise ValueError(f"Invalid duration string {s!r}. Expected format: '30m', '6h', or '2d'.")


def fmt_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S", default: str = "") -> str:
    """Format a datetime as a local time string with *fmt*, returning *default* for None."""
    return dt.astimezone().strftime(fmt) if dt else default


def safe_path(s: str) -> str:
    """Strip characters that are invalid in Windows/POSIX path components."""
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip() or "unknown"


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ── Workload-type labels ────────────────────────────────────────────────────

M365_TYPE_LABELS: dict[M365WorkloadType, str] = {
    M365WorkloadType.EXCHANGE:   "Exchange",
    M365WorkloadType.ONEDRIVE:   "OneDrive",
    M365WorkloadType.CHAT:       "Chat",
    M365WorkloadType.SHAREPOINT: "SharePoint",
    M365WorkloadType.TEAMS:      "Teams",
    M365WorkloadType.GROUP:      "Group",
}

GWS_TYPE_LABELS: dict[GWSWorkloadType, str] = {
    GWSWorkloadType.MAIL:         "Mail",
    GWSWorkloadType.CALENDAR:     "Calendar",
    GWSWorkloadType.CONTACT:      "Contact",
    GWSWorkloadType.DRIVE:        "Drive",
    GWSWorkloadType.SHARED_DRIVE: "Shared Drive",
}


def workload_type_label(wl: MachineWorkload | M365Workload | GWSWorkload) -> str:
    """Display label for a workload's type (e.g. 'VM', 'Exchange', 'Shared Drive')."""
    if isinstance(wl, M365Workload):
        return M365_TYPE_LABELS.get(wl.workload_type, wl.workload_type.value)
    if isinstance(wl, GWSWorkload):
        return GWS_TYPE_LABELS.get(wl.workload_type, wl.workload_type.value)
    return wl.workload_type.value.upper()


WORKLOAD_TYPE_ORDER: tuple[MachineWorkloadType | M365WorkloadType | GWSWorkloadType, ...] = (
    MachineWorkloadType.VM,
    MachineWorkloadType.PS,
    MachineWorkloadType.PC,
    MachineWorkloadType.FS,
    M365WorkloadType.EXCHANGE,
    M365WorkloadType.ONEDRIVE,
    M365WorkloadType.SHAREPOINT,
    M365WorkloadType.TEAMS,
    M365WorkloadType.CHAT,
    M365WorkloadType.GROUP,
    GWSWorkloadType.MAIL,
    GWSWorkloadType.CALENDAR,
    GWSWorkloadType.CONTACT,
    GWSWorkloadType.DRIVE,
    GWSWorkloadType.SHARED_DRIVE,
)


def category_label(wl: MachineWorkload | M365Workload | GWSWorkload) -> str:
    """'M365', 'GWS', or 'Machine' for the workload's domain."""
    if isinstance(wl, M365Workload):
        return "M365"
    if isinstance(wl, GWSWorkload):
        return "GWS"
    return "Machine"


# ── Pagination ──────────────────────────────────────────────────────────────

async def paginate(
    list_call: Callable[[int, int], Awaitable[tuple[list[T], int | None]]],
    *,
    page: int = 500,
) -> tuple[list[T], int | None]:
    """Drain a paginated ``list(limit, offset) -> (items, total)`` SDK call.

    Returns (all_items, server_total) where server_total is the total reported on
    the first page (None if the endpoint does not report a reliable count, in
    which case pagination stops once a page shorter than *page* is returned).
    """
    items: list[T] = []
    offset = 0
    total: int | None = 0
    while True:
        chunk, chunk_total = await list_call(page, offset)
        if offset == 0:
            total = chunk_total
        items.extend(chunk)
        offset += len(chunk)
        if not chunk or (chunk_total is not None and offset >= chunk_total):
            break
    return items, total


async def list_m365_tenants(apm: APMClient, *, page: int = 500) -> list[M365TenantInfo]:
    """All SaaS applications in the M365 category."""
    tenants, _ = await paginate(
        lambda limit, offset: apm.saas.list(limit=limit, offset=offset), page=page
    )
    return [t for t in tenants if isinstance(t, M365TenantInfo)]


async def list_gws_domains(apm: APMClient, *, page: int = 500) -> list[GWSDomainInfo]:
    """All SaaS applications in the GWS category."""
    domains, _ = await paginate(
        lambda limit, offset: apm.saas.list(limit=limit, offset=offset), page=page
    )
    return [d for d in domains if isinstance(d, GWSDomainInfo)]


async def collect_backup_servers(apm: APMClient, *, page: int = 500) -> list[BackupServer]:
    """All backup servers in the cluster."""
    servers, _ = await paginate(
        lambda limit, offset: apm.backup_servers.list(limit=limit, offset=offset), page=page
    )
    return servers


async def collect_machine_workloads(
    apm: APMClient, *, is_retired: bool, page: int = 500
) -> tuple[list[MachineWorkload], int]:
    """All machine workloads matching *is_retired* (True=retired only, False=protected only)."""
    items, total = await paginate(
        lambda limit, offset: apm.machine.workloads.list(
            is_retired=is_retired, limit=limit, offset=offset
        ),
        page=page,
    )
    assert total is not None  # MachineWorkloadCollection.list() always reports a real total
    return items, total


async def collect_m365_workloads(
    apm: APMClient,
    services: list[M365WorkloadType],
    *,
    is_retired: bool,
    page: int = 500,
    tenants: list[M365TenantInfo] | None = None,
) -> tuple[list[M365Workload], int]:
    """All M365 workloads of the given service types across every M365 tenant.

    Pass *tenants* (from list_m365_tenants) to reuse an already-fetched tenant
    list and avoid an extra lookup; otherwise the tenants are fetched here.
    """
    if tenants is None:
        tenants = await list_m365_tenants(apm, page=page)
    results: list[M365Workload] = []
    total = 0

    def _list_call(
        tenant: M365TenantInfo, service: M365WorkloadType
    ) -> Callable[[int, int], Awaitable[tuple[list[M365Workload], int | None]]]:
        async def _call(limit: int, offset: int) -> tuple[list[M365Workload], int | None]:
            return await apm.m365.workloads.list(
                tenant_id=tenant.tenant_id, workload_type=service,
                is_retired=is_retired, limit=limit, offset=offset,
            )
        return _call

    for tenant in tenants:
        for service in services:
            items, sub_total = await paginate(_list_call(tenant, service), page=page)
            assert sub_total is not None  # M365WorkloadCollection.list() always reports a real total
            results.extend(items)
            total += sub_total
    return results, total


async def collect_gws_workloads(
    apm: APMClient,
    services: list[GWSWorkloadType],
    *,
    is_retired: bool,
    page: int = 500,
    domains: list[GWSDomainInfo] | None = None,
) -> tuple[list[GWSWorkload], int]:
    """All GWS workloads of the given service types across every GWS domain.

    Pass *domains* (from list_gws_domains) to reuse an already-fetched domain
    list and avoid an extra lookup; otherwise the domains are fetched here.
    """
    if domains is None:
        domains = await list_gws_domains(apm, page=page)
    results: list[GWSWorkload] = []
    total = 0

    def _list_call(
        domain: GWSDomainInfo, service: GWSWorkloadType
    ) -> Callable[[int, int], Awaitable[tuple[list[GWSWorkload], int | None]]]:
        async def _call(limit: int, offset: int) -> tuple[list[GWSWorkload], int | None]:
            return await apm.gws.workloads.list(
                domain=domain.domain, workload_type=service,
                is_retired=is_retired, limit=limit, offset=offset,
            )
        return _call

    for domain in domains:
        for service in services:
            items, sub_total = await paginate(_list_call(domain, service), page=page)
            assert sub_total is not None  # GWSWorkloadCollection.list() always reports a real total
            results.extend(items)
            total += sub_total
    return results, total


async def collect_workloads(
    apm: APMClient,
    category: Category,
    m365_services: list[M365WorkloadType] | None,
    *,
    is_retired: bool,
    page: int = 500,
    gws_services: list[GWSWorkloadType] | None = None,
) -> tuple[list[MachineWorkload | M365Workload | GWSWorkload], int]:
    """Collect machine and/or M365 and/or GWS workloads for *category*.

    *m365_services*/*gws_services* of None means all service types for that category.
    Returns (workloads, total) where total is the sum of server-reported totals.
    """
    workloads: list[MachineWorkload | M365Workload | GWSWorkload] = []
    total = 0
    if category in ("machine", "all"):
        machine, machine_total = await collect_machine_workloads(
            apm, is_retired=is_retired, page=page
        )
        workloads.extend(machine)
        total += machine_total
    if category in ("m365", "all"):
        services = m365_services if m365_services is not None else list(M365WorkloadType)
        m365, m365_total = await collect_m365_workloads(
            apm, services, is_retired=is_retired, page=page
        )
        workloads.extend(m365)
        total += m365_total
    if category in ("gws", "all"):
        gws_types = gws_services if gws_services is not None else list(GWSWorkloadType)
        gws, gws_total = await collect_gws_workloads(
            apm, gws_types, is_retired=is_retired, page=page
        )
        workloads.extend(gws)
        total += gws_total
    return workloads, total


# ── Shared argparse options ─────────────────────────────────────────────────

_M365_SERVICE_CHOICES = ["exchange", "onedrive", "chat", "sharepoint", "teams", "group"]
_GWS_WORKLOAD_TYPE_CHOICES = ["mail", "calendar", "contact", "drive", "shared_drive"]


def add_category_args(
    parser: argparse.ArgumentParser, *, verb: str, default: str | None = None
) -> None:
    """Add --category, --m365-service, and --gws-workload-type. *verb* describes the
    action (e.g. 'export').

    Pass *default* to make --category optional with a fallback value (e.g. ``"all"``);
    omit it (or pass None) to keep --category required. "all" means "machine + m365 + gws".
    """
    parser.add_argument(
        "--category", choices=["machine", "m365", "gws", "all"],
        default=default, required=default is None,
        help=f"Workload category to {verb} (all = machine + m365 + gws)",
    )
    parser.add_argument(
        "--m365-service", dest="m365_service", action="append",
        choices=_M365_SERVICE_CHOICES, metavar="SERVICE",
        help=(
            "M365 service type: exchange, onedrive, chat, sharepoint, teams, group. "
            "Repeat to include multiple (e.g. --m365-service exchange --m365-service onedrive). "
            "Required when --category is m365; optional (defaults to all) when --category is all."
        ),
    )
    parser.add_argument(
        "--gws-workload-type", dest="gws_workload_type", action="append",
        choices=_GWS_WORKLOAD_TYPE_CHOICES, metavar="TYPE",
        help=(
            "GWS workload type: mail, calendar, contact, drive, shared_drive. "
            "Repeat to include multiple (e.g. --gws-workload-type mail --gws-workload-type drive). "
            "Required when --category is gws; optional (defaults to all) when --category is all."
        ),
    )


def add_output_arg(parser: argparse.ArgumentParser) -> None:
    """Add the standard -o/--output table|csv|json option."""
    parser.add_argument(
        "-o", "--output", dest="output", choices=["table", "csv", "json"], default="table",
        help="Output format: table, csv, or json (default: table)",
    )


def add_profile_arg(parser: argparse.ArgumentParser) -> None:
    """Add the standard --profile option (same flag/help text as synology-apm-cli and
    synology-apm-mcp). Pass ``args.profile`` to ``make_client(profile=...)``."""
    parser.add_argument(
        "--profile", metavar="NAME", default=None,
        help="Config profile to use (overrides APM_PROFILE env var)",
    )


def resolve_m365_services(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> list[M365WorkloadType] | None:
    """Validate --category/--m365-service and return the requested types (None = all)."""
    if args.category == "m365" and not args.m365_service:
        parser.error("--m365-service is required when --category is m365")
    if args.category in ("machine", "gws") and args.m365_service:
        parser.error(f"--m365-service is not valid with --category {args.category}")
    return [M365WorkloadType(s) for s in args.m365_service] if args.m365_service else None


def resolve_gws_workload_types(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> list[GWSWorkloadType] | None:
    """Validate --category/--gws-workload-type and return the requested types (None = all)."""
    if args.category == "gws" and not args.gws_workload_type:
        parser.error("--gws-workload-type is required when --category is gws")
    if args.category in ("machine", "m365") and args.gws_workload_type:
        parser.error(f"--gws-workload-type is not valid with --category {args.category}")
    return [GWSWorkloadType(s) for s in args.gws_workload_type] if args.gws_workload_type else None


# ── Concurrent-download primitives (progress + interrupt) ────────────────────

@dataclass
class Progress:
    """Shared real-time progress counters for the concurrent download scripts.

    *noun* names the unit being processed ("user", "group", "video"). Set
    *show_exporting* to False for pipelines without a separate export phase.
    """
    total: int
    noun: str = "item"
    show_exporting: bool = True
    exporting: int = 0
    downloading: int = 0
    done: int = 0
    on_line: bool = field(default=False, init=False, repr=False)
    start_time: datetime = field(default_factory=datetime.now, init=False, repr=False)

    def _elapsed(self) -> str:
        return fmt_duration((datetime.now() - self.start_time).total_seconds())

    def line(self) -> str:
        remaining = self.total - self.done
        parts: list[str] = []
        if self.show_exporting:
            parts.append(f"{self.exporting} exporting tasks")
        parts.append(f"{self.downloading} downloading")
        parts.append(f"{remaining} {self.noun}s remaining")
        parts.append(f"{self._elapsed()} elapsed")
        return "  " + ", ".join(parts)

    def print_progress(self) -> None:
        print(f"\r\033[K{self.line()}", end="", flush=True)
        self.on_line = True

    def clear_progress(self) -> None:
        if self.on_line:
            print("\r\033[K", end="", flush=True)
            self.on_line = False


def register_interrupt(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
    """Make Ctrl+C set *event* instead of raising KeyboardInterrupt."""
    try:
        loop.add_signal_handler(signal.SIGINT, event.set)
    except (NotImplementedError, AttributeError):
        # Windows: loop.add_signal_handler is unavailable; use signal.signal with
        # call_soon_threadsafe so the event is set from the event-loop thread.
        def _win_handler(sig: int, frame: FrameType | None) -> None:
            loop.call_soon_threadsafe(event.set)
        signal.signal(signal.SIGINT, _win_handler)


def unregister_interrupt(loop: asyncio.AbstractEventLoop) -> None:
    """Restore default SIGINT (KeyboardInterrupt) handling."""
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except (NotImplementedError, AttributeError):
        signal.signal(signal.SIGINT, signal.default_int_handler)


async def interruptible_sleep(secs: float, event: asyncio.Event) -> bool:
    """Sleep for *secs* or until *event* is set. Returns True if the event fired."""
    try:
        await asyncio.wait_for(event.wait(), timeout=secs)
        return True
    except TimeoutError:
        return False


async def prompt_yes_no(message: str) -> bool:
    """Read a y/N answer from stdin without blocking the event loop."""
    sys.stderr.write(message)
    sys.stderr.flush()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()

    def _on_readable() -> None:
        # Runs in the event-loop thread once stdin is readable (i.e. the user
        # pressed Enter or Ctrl+D); data is already in the kernel buffer so
        # readline() returns immediately without blocking.
        loop.remove_reader(sys.stdin.fileno())
        if fut.done():
            return
        try:
            fut.set_result(sys.stdin.readline())
        except Exception as exc:
            fut.set_exception(exc)

    try:
        loop.add_reader(sys.stdin.fileno(), _on_readable)
    except (NotImplementedError, OSError):
        # Windows (ProactorEventLoop) or non-fd stdin: fall back to executor.
        try:
            raw = await loop.run_in_executor(None, sys.stdin.readline)
        except (KeyboardInterrupt, EOFError):
            print(file=sys.stderr)
            return False
        return raw.strip().lower() in ("y", "yes")

    try:
        raw = await fut
    except (KeyboardInterrupt, EOFError):
        loop.remove_reader(sys.stdin.fileno())
        print(file=sys.stderr)
        return False
    return raw.strip().lower() in ("y", "yes")
