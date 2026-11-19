"""A scripted sandbox console (RFB server over a WebSocket) for SDK unit tests."""
from __future__ import annotations

import asyncio
import struct
import zlib
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import aiohttp

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.exceptions import APIError

VERSION = b"RFB 003.008\n"
# What the client must send after the server's greeting, in order: version, security None,
# shared ClientInit, SetPixelFormat (32 bpp, depth 24, little-endian, true colour, R/G/B
# shifts 0/8/16), SetEncodings [zlib, raw, DesktopSize], and a full-screen non-incremental
# update request.
PIXEL_FORMAT_MSG = bytes([0, 0, 0, 0, 32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 0, 8, 16, 0, 0, 0])
ENCODINGS_MSG = bytes([2, 0, 0, 3, 0, 0, 0, 6, 0, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0x21])


def update_request(width: int, height: int) -> bytes:
    return struct.pack(">BBHHHH", 3, 0, 0, 0, width, height)


def greeting(width: int, height: int, *, security: bytes = b"\x01\x01", version: bytes = VERSION) -> bytes:
    """Server bytes from the version through ServerInit (security result OK)."""
    name = b"QEMU (guest)"
    return (
        version + security + struct.pack(">I", 0)
        + struct.pack(">HH", width, height) + bytes(16) + struct.pack(">I", len(name)) + name
    )


def update(*rects: bytes) -> bytes:
    return struct.pack(">BxH", 0, len(rects)) + b"".join(rects)


def raw_rect(x: int, y: int, w: int, h: int, pixels: bytes) -> bytes:
    return struct.pack(">HHHHi", x, y, w, h, 0) + pixels


def desktop_size(width: int, height: int) -> bytes:
    """A DesktopSize pseudo-rectangle: the screen's new size, no pixel data."""
    return struct.pack(">HHHHi", 0, 0, width, height, -223)


def zlib_rect(x: int, y: int, w: int, h: int, pixels: bytes, stream: Any) -> bytes:
    """A zlib rectangle compressed with stream (a zlib.compressobj shared across rectangles)."""
    data: bytes = stream.compress(pixels) + stream.flush(zlib.Z_SYNC_FLUSH)
    return struct.pack(">HHHHi", x, y, w, h, 6) + struct.pack(">I", len(data)) + data


def solid(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    """w x h pixels in the negotiated R, G, B, padding byte order."""
    return bytes([*rgb, 0xAA]) * (w * h)


def decode_png(png: bytes) -> tuple[int, int, bytes]:
    """Decode an 8-bit RGB, unfiltered PNG; return (width, height, RGB bytes). Checks CRCs."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, dict[bytes, bytes]()
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
        assert struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])[0] == zlib.crc32(tag + data)
        chunks[tag] = chunks.get(tag, b"") + data
        pos += 12 + length
    width, height, depth, colour, _, _, _ = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    assert (depth, colour) == (8, 2)
    raw = zlib.decompress(chunks[b"IDAT"])
    stride = width * 3 + 1
    rows = [raw[i:i + stride] for i in range(0, len(raw), stride)]
    assert all(row[0] == 0 for row in rows) and len(rows) == height
    return width, height, b"".join(row[1:] for row in rows)


def frame(rgb: tuple[int, int, int] = (1, 2, 3)) -> FakeConsole:
    """A console that sends a 1x1 screen of ``rgb``, then closes."""
    return FakeConsole(greeting(1, 1) + update(raw_rect(0, 0, 1, 1, solid(1, 1, rgb))))


class FakeConsole:
    """A console WebSocket: receive() replays queued server bytes; send_bytes() records.

    Once drained it reports the connection closed, unless live=True: then it waits for
    feed() or close_from_server(), like a console with an idle screen.
    """

    def __init__(self, server: bytes = b"", *, chunk: int | None = None, live: bool = False) -> None:
        self.sent: list[bytes] = []
        self.is_closed = False
        self._live = live
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.feed(server, chunk=chunk)

    def feed(self, data: bytes, *, chunk: int | None = None) -> None:
        step = chunk or max(len(data), 1)
        for i in range(0, len(data), step):
            self._queue.put_nowait(data[i:i + step])

    def close_from_server(self) -> None:
        self._queue.put_nowait(None)

    async def receive(self) -> aiohttp.WSMessage:
        if self._queue.empty() and not self._live:
            return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)
        data = await self._queue.get()
        if data is None:
            return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, 1000, None)
        return aiohttp.WSMessage(aiohttp.WSMsgType.BINARY, data, None)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.is_closed = True


class FakeConsoleOpener:
    """Stands in for WebAPISession.ws_connect (see install()); records each open.

    Each open takes the next outcome: a FakeConsole to return, or an exception to raise.
    Once they run out, an idle live console. stall=True never finishes a handshake.
    """

    def __init__(self, *outcomes: FakeConsole | Exception, stall: bool = False) -> None:
        self._outcomes = list(outcomes)
        self._stall = stall
        self.opened: list[tuple[str, str, str]] = []  # (origin, path, sharing_id)
        self.cookies: list[dict[str, str]] = []  # the cookies sent with each open, in order
        self.handed: list[FakeConsole] = []

    @property
    def closed(self) -> list[FakeConsole]:
        return [c for c in self.handed if c.is_closed]

    def install(self) -> Any:
        """Patch WebAPISession.ws_connect for the duration of a with block."""
        opener = self

        async def ws_connect(
            session: WebAPISession, url: str, *, params: Mapping[str, str], cookies: Mapping[str, str],
        ) -> FakeConsole:
            parts = urlsplit(url)
            opener.opened.append((f"{parts.scheme}://{parts.netloc}", parts.path, params["sharing_id"]))
            opener.cookies.append(dict(cookies))
            if opener._stall:
                await asyncio.Event().wait()
            outcome = opener._outcomes.pop(0) if opener._outcomes else FakeConsole(live=True)
            if isinstance(outcome, Exception):
                raise outcome
            opener.handed.append(outcome)
            return outcome

        return patch.object(WebAPISession, "ws_connect", ws_connect)


def rejected(status: int = 400) -> APIError:
    """What WebAPISession.ws_connect raises when the console rejects the access."""
    return APIError(f"The connection was refused (HTTP {status}).", error_code=status)
