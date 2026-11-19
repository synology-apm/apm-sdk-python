"""Access to a sandbox's console (private module).

An access is a sharing link minted by connect_to_vm and opened once for its cookie; the
protocol is in BEHAVIOR_REFERENCE.md (Sandbox console access).
An access keeps working, so one is kept per VM and replaced only when a handshake rejects it,
which is also how a powered-off guest gets powered on again.

An access is only values (host, link id, cookies), so replacing one never touches connections
still open on the old one, and a kept access holds no resources. Every link GET and WebSocket
goes through the WebAPISession's console client, which keeps no cookies and is closed (with
every console WebSocket) when the session disconnects.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar
from urllib.parse import quote, urlsplit

import aiohttp

from ._http import WebAPISession
from ._relay import serve_relay
from ._vnc import ConsoleClosedError, RfbClient
from .enums import SandboxKey
from .exceptions import APIError, ConnectionTimeoutError

# Console WebSocket path on the host that runs the VM.
_CONSOLE_WS = "/synovirtualization/ws/"
_LINK_PATH_PREFIX = "/sharing/"
# Handshake statuses for an access the console no longer accepts (expired, revoked, or the
# guest is powered off).
_REJECTED = frozenset({400, 401})
_UNUSABLE_LINK = "The sandbox console link could not be opened."
# Bounds each connecting step, and a capture after its keys' wait. Minting an access (which
# may power the VM on) is not bounded by it.
_DEFAULT_TIMEOUT_S = 30
# Consoles a client keeps (each one saves a mint, and an activity-log entry, on reuse).
_DEFAULT_MAX_CONSOLES = 16

_T = TypeVar("_T")


class BinarySocket(Protocol):
    """A binary message socket: the subset of aiohttp.ClientWebSocketResponse that the RFB
    client (_vnc.py) and the relay (_relay.py) use."""

    async def receive(self) -> aiohttp.WSMessage: ...

    async def send_bytes(self, data: bytes) -> None: ...


@dataclass(frozen=True, eq=False)
class _Access:
    """A console link opened once: its host, its id, and the cookies it set."""

    origin: str
    link_id: str
    cookies: Mapping[str, str]

    @classmethod
    async def open(cls, http: WebAPISession, url: str) -> _Access:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        link_id = parts.path.removeprefix(_LINK_PATH_PREFIX)
        if not parts.netloc or link_id in ("", parts.path) or "/" in link_id:
            raise APIError(_UNUSABLE_LINK)
        status, cookies = await http.fetch_cookies(url, shown=origin + _LINK_PATH_PREFIX + "***")
        if status != 200 or not cookies:
            raise APIError(_UNUSABLE_LINK, error_code=status)
        return cls(origin, link_id, cookies)


class _Rejected(Exception):
    """A handshake rejected the access (status 400/401); the access is replaced once."""

    def __init__(self, status: int | None) -> None:
        super().__init__(f"The console rejected the access (HTTP {status}).")
        self.status = status


async def _retry_once(
    attempt: Callable[[bool], Awaitable[_T]],
    retry_on: type[Exception],
    final_error: Callable[[Exception], Exception],
) -> _T:
    """Run ``attempt(False)``; on ``retry_on``, run ``attempt(True)`` once.

    A second ``retry_on`` raises ``final_error(exc)`` from it; any other error propagates.
    """
    try:
        return await attempt(False)
    except retry_on:
        pass
    try:
        return await attempt(True)
    except retry_on as exc:
        raise final_error(exc) from exc


@contextlib.contextmanager
def _timeout_as_error() -> Iterator[None]:
    try:
        yield
    except TimeoutError as exc:
        raise ConnectionTimeoutError("The sandbox console did not answer in time.") from exc


class SandboxConsole:
    """The console of one sandbox VM, through an access kept for this client.

    ``mint`` returns a new console link; it is called (untimed: it may power the VM on) for
    the first connection and whenever the console rejects the current access. A failed mint
    leaves no access, so the next connection mints again. ``timeout_s`` bounds each connecting
    step (WebSocket handshake, RFB start), and a capture after its keys' wait. Holds no
    resources between connections.

    Retries are bounded by structure: a rejected access is replaced once per connection, and
    a connection that closes while the RFB session starts is reopened once on the same access.
    Nothing is retried once an RFB session has started, since keys may have been pressed.
    """

    def __init__(
        self, http: WebAPISession, vm_id: str, mint: Callable[[], Awaitable[str]], *, timeout_s: float,
    ) -> None:
        self._http = http
        self._timeout_s = timeout_s
        self._path = _CONSOLE_WS + quote(vm_id, safe="")
        self._mint = mint
        self._lock = asyncio.Lock()
        self._access: _Access | None = None

    @property
    def busy(self) -> bool:
        """Whether an access is being minted right now."""
        return self._lock.locked()

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncIterator[BinarySocket]:
        """A raw console WebSocket (for relaying a VNC client), closed on exit.

        Raises:
            PermissionDeniedError: The console refused this user.
            APIError: The console refused the access even after it was replaced, or could not
                be reached.
            ConnectionTimeoutError: The handshake did not finish in time.
        """
        with _timeout_as_error():
            ws = await self._connect()
        try:
            yield ws
        finally:
            await ws.close()

    @contextlib.asynccontextmanager
    async def serve_vnc(self, host: str, port: int) -> AsyncIterator[int]:
        """Relay local VNC clients to this console while open; yield the bound port.

        One connection is opened and closed first, so an unreachable console fails here
        rather than on the first VNC client. Each VNC client then gets its own connection.

        Raises:
            PermissionDeniedError, APIError, ConnectionTimeoutError: As ``connect()``, for the
                first connection.
            OSError: The port could not be bound.
        """
        async with self.connect():
            pass
        async with serve_relay(self.connect, host, port, debug=self._http.debug) as bound:
            yield bound

    async def screenshot(self, keys: Sequence[SandboxKey], wait: float) -> bytes:
        """Press ``keys``, wait ``wait`` seconds, and capture the screen as PNG bytes.

        Raises:
            PermissionDeniedError: The console refused this user.
            APIError: The console could not be reached, refused the access, or closed the
                connection.
            ConnectionTimeoutError: The console did not answer, or send its screen, in time.
        """
        async with self._open_rfb() as rfb:
            try:
                async with asyncio.timeout(self._timeout_s + wait):
                    return await rfb.capture(keys, wait)
            except TimeoutError as exc:
                raise ConnectionTimeoutError("The sandbox console did not send its screen in time.") from exc

    # ── Connecting ─────────────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def _open_rfb(self) -> AsyncIterator[RfbClient]:
        """A started RFB session, closed on exit; a ConsoleClosedError inside becomes an APIError."""
        with _timeout_as_error():
            ws, rfb = await _retry_once(
                lambda _retry: self._try_rfb(),
                ConsoleClosedError,  # a console can drop a fresh connection
                lambda exc: APIError(str(exc)),
            )
        try:
            yield rfb
        except ConsoleClosedError as exc:
            raise APIError(str(exc)) from exc
        finally:
            await ws.close()

    async def _try_rfb(self) -> tuple[aiohttp.ClientWebSocketResponse, RfbClient]:
        ws = await self._connect()
        try:
            async with asyncio.timeout(self._timeout_s):
                return ws, await RfbClient.start(ws)
        except BaseException:
            await ws.close()
            raise

    async def _connect(self) -> aiohttp.ClientWebSocketResponse:
        access: _Access | None = None

        async def attempt(retry: bool) -> aiohttp.ClientWebSocketResponse:
            nonlocal access
            access = await self._current(stale=access if retry else None)
            return await self._handshake(access)

        def refused(exc: Exception) -> APIError:
            status = exc.status if isinstance(exc, _Rejected) else None
            return APIError("The sandbox console refused the connection.", error_code=status)

        return await _retry_once(attempt, _Rejected, refused)

    async def _handshake(self, access: _Access) -> aiohttp.ClientWebSocketResponse:
        params = {"app_id": str(uuid.uuid4()), "sharing_id": access.link_id}
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._http.ws_connect(access.origin + self._path, params=params, cookies=access.cookies)
        except APIError as exc:
            if type(exc) is APIError and exc.error_code in _REJECTED:
                raise _Rejected(exc.error_code) from exc
            raise

    async def _current(self, stale: _Access | None = None) -> _Access:
        """The access to use; a new one when there is none or it still is ``stale``.

        Compare-then-refresh (as WebAPISession._relogin()): callers rejected on the same
        access mint once, and a caller holding an already-replaced access gets the new one.
        """
        async with self._lock:
            if self._access is not None and self._access is not stale:
                return self._access
            self._access = None
            self._access = await _Access.open(self._http, await self._mint())
            return self._access


class SandboxConsoles:
    """The sandbox consoles a client keeps: the ``max_consoles`` most recently used.

    A console is keyed by the namespace of the backup server that runs the VM and the VM's
    ID. Dropping a console only forgets its access (the next use mints again); connections
    still open on it are not affected. A console that is minting an access is not dropped.
    """

    def __init__(
        self,
        http: WebAPISession,
        *,
        max_consoles: int = _DEFAULT_MAX_CONSOLES,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._max = max_consoles
        self._timeout_s = timeout_s
        self._consoles: dict[tuple[str, str], SandboxConsole] = {}  # least recently used first

    def get(self, namespace: str, vm_id: str, mint: Callable[[], Awaitable[str]]) -> SandboxConsole:
        """The VM's console; ``mint`` (see SandboxConsole) is used only when it is new."""
        key = (namespace, vm_id)
        console = self._consoles.pop(key, None)
        if console is None:
            console = SandboxConsole(self._http, vm_id, mint, timeout_s=self._timeout_s)
            idle = [k for k, c in self._consoles.items() if not c.busy]
            for k in idle[:max(len(self._consoles) + 1 - self._max, 0)]:
                del self._consoles[k]
        self._consoles[key] = console
        return console
