"""Unit tests for synology_apm.sdk._relay: the local TCP relay to WebSockets.

serve_relay() is exercised directly with FakeConsole sockets (tests/unit/sdk/rfb_fakes.py);
test_machine_sandbox_console.py covers how open_vnc_bridge() wires it to a sandbox console.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest

from synology_apm.sdk._relay import serve_relay
from synology_apm.sdk.exceptions import APIError
from tests.unit.sdk.rfb_fakes import FakeConsole

HOST = "127.0.0.1"


class _Sockets:
    """open_socket for serve_relay(): hands out the given sockets (or raises the given errors)."""

    def __init__(self, *outcomes: FakeConsole | Exception) -> None:
        self._outcomes = list(outcomes)
        self.handed: list[FakeConsole] = []

    def __call__(self) -> AbstractAsyncContextManager[FakeConsole]:
        @asynccontextmanager
        async def open_socket() -> AsyncIterator[FakeConsole]:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            self.handed.append(outcome)
            try:
                yield outcome
            finally:
                await outcome.close()

        return open_socket()


async def _until(condition: Callable[[], object]) -> None:
    """Let the relay tasks run until condition() holds (at most about 2 s)."""
    for _ in range(2000):
        if condition():
            return
        await asyncio.sleep(0.001)
    pytest.fail("condition not reached")


async def test_each_client_gets_its_own_socket() -> None:
    first, second = FakeConsole(live=True), FakeConsole(live=True)
    sockets = _Sockets(first, second)
    async with serve_relay(sockets, HOST, 0) as port:
        clients = [await asyncio.open_connection(HOST, port) for _ in range(2)]
        await _until(lambda: len(sockets.handed) == 2)
        for (_, writer), tag in zip(clients, (b"one", b"two"), strict=True):
            writer.write(tag)
            await writer.drain()
        first.feed(b"screen-1")
        second.feed(b"screen-2")
        replies = [await asyncio.wait_for(reader.readexactly(8), 1) for reader, _ in clients]
        await _until(lambda: first.sent and second.sent)

        assert sorted(replies) == [b"screen-1", b"screen-2"]
        assert sorted([*first.sent, *second.sent]) == [b"one", b"two"]

    # Exit closed every socket, disconnected every client, and closed the listening port.
    assert first.is_closed and second.is_closed
    assert [await asyncio.wait_for(reader.read(), 1) for reader, _ in clients] == [b"", b""]
    with pytest.raises(OSError):
        await asyncio.open_connection(HOST, port)


async def test_client_disconnect_closes_its_socket() -> None:
    socket = FakeConsole(live=True)
    async with serve_relay(_Sockets(socket), HOST, 0) as port:
        _, writer = await asyncio.open_connection(HOST, port)
        writer.close()
        await _until(lambda: socket.is_closed)


async def test_socket_close_disconnects_the_client() -> None:
    socket = FakeConsole(live=True)
    async with serve_relay(_Sockets(socket), HOST, 0) as port:
        reader, _ = await asyncio.open_connection(HOST, port)
        socket.feed(b"bye")
        socket.close_from_server()
        assert await asyncio.wait_for(reader.read(), 1) == b"bye"  # then EOF


async def test_failed_socket_disconnects_the_client(capsys: pytest.CaptureFixture[str]) -> None:
    async with serve_relay(_Sockets(APIError("unreachable")), HOST, 0) as port:
        reader, _ = await asyncio.open_connection(HOST, port)
        assert await asyncio.wait_for(reader.read(), 1) == b""
    assert capsys.readouterr().err == ""  # silent without debug


async def test_failed_socket_prints_a_debug_line(capsys: pytest.CaptureFixture[str]) -> None:
    async with serve_relay(_Sockets(APIError("unreachable")), HOST, 0, debug=True) as port:
        reader, _ = await asyncio.open_connection(HOST, port)
        assert await asyncio.wait_for(reader.read(), 1) == b""
    assert "console connection failed (APIError: unreachable)" in capsys.readouterr().err
