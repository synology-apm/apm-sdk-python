"""A local TCP relay to WebSockets (private module).

Each local client gets its own WebSocket and the two exchange bytes as they are, so the relay
knows nothing about the protocol inside (for a sandbox console, VNC). It serves
MachineSandboxCollection.open_vnc_bridge().
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

import aiohttp

from .exceptions import APMError

if TYPE_CHECKING:
    from ._console import BinarySocket

_READ_SIZE = 64 * 1024


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ws: BinarySocket) -> None:
    """Copy bytes both ways until either side closes."""

    async def upstream() -> None:
        while data := await reader.read(_READ_SIZE):
            await ws.send_bytes(data)

    async def downstream() -> None:
        while True:
            msg = await ws.receive()
            if msg.type == aiohttp.WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return

    tasks = {asyncio.create_task(upstream()), asyncio.create_task(downstream())}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@contextlib.asynccontextmanager
async def serve_relay(
    open_socket: Callable[[], AbstractAsyncContextManager[BinarySocket]],
    host: str,
    port: int,
    *,
    debug: bool = False,
) -> AsyncIterator[int]:
    """Accept local TCP clients, each relayed to its own WebSocket; yield the bound port.

    ``open_socket`` opens one WebSocket per client. On exit, stops listening and closes every
    open relay; a client accepted while the relay is closing is disconnected without opening
    a WebSocket. With ``debug``, a client whose WebSocket fails gets a line on stderr.
    """
    relays: set[asyncio.Task[Any]] = set()
    closing = False

    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        try:
            if closing:
                return
            if task:
                relays.add(task)
            # A failed WebSocket just closes the client's socket: there is no caller to
            # report it to, and the client sees the disconnect.
            try:
                async with open_socket() as ws:
                    await _pump(reader, writer, ws)
            except (APMError, OSError, aiohttp.ClientError) as exc:
                if debug:
                    # Only SDK messages are shown: other errors can carry the console URL.
                    detail = f": {exc.message}" if isinstance(exc, APMError) else ""
                    name = type(exc).__name__
                    sys.stderr.write(f"VNC relay: a client's console connection failed ({name}{detail})\n")
        finally:
            relays.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(relay, host, port)
    try:
        yield int(server.sockets[0].getsockname()[1])
    finally:
        closing = True
        server.close()
        for task in list(relays):
            task.cancel()
        await asyncio.gather(*relays, return_exceptions=True)
        # Python 3.12+ also waits here for every accepted client, including late ones, which
        # end at once because of ``closing``.
        await server.wait_closed()
