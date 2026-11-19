"""Minimal RFB (VNC) client over a console WebSocket, and PNG encoding (private module).

The sandbox console is QEMU's VNC server tunnelled through a binary WebSocket that a console
access authorizes (see _console.py); the VNC layer itself offers only security type None.
RfbClient implements just what a still screenshot needs (RFC 6143), in two phases: start()
connects and changes nothing in the guest; capture() may press keys. Protocol 3.8,
a 32-bit true-colour pixel format, the zlib (6) and Raw (0) encodings, the DesktopSize
pseudo-encoding (the guest can change resolution between the handshake and the capture,
e.g. a key that boots the OS), and key events for the named keys a screenshot can be
preceded by.
"""
from __future__ import annotations

import asyncio
import struct
import zlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

import aiohttp

from .enums import SandboxKey
from .exceptions import APIError

if TYPE_CHECKING:
    from ._console import BinarySocket

_VERSION = b"RFB 003.008\n"
_SECURITY_NONE = 1
_ENCODING_RAW = 0
_ENCODING_ZLIB = 6
_ENCODING_DESKTOP_SIZE = -223
# Upper bound on a screen side the client accepts; buffers are sized from server values.
_MAX_SIDE = 4096
_BYTES_PER_PIXEL = 4
# SetPixelFormat: 32 bpp, depth 24, little-endian, true colour, 8-bit channels with red in
# the lowest byte, so every pixel arrives as the bytes R, G, B, padding.
_PIXEL_FORMAT = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 0, 8, 16)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_CLOSED = "The sandbox console connection closed unexpectedly."

# X11 keysyms per key, pressed in order and released in reverse.
_KEYSYMS: dict[SandboxKey, tuple[int, ...]] = {
    SandboxKey.ENTER:           (0xFF0D,),
    SandboxKey.ESC:             (0xFF1B,),
    SandboxKey.SPACE:           (0x0020,),
    SandboxKey.TAB:             (0xFF09,),
    SandboxKey.BACKSPACE:       (0xFF08,),
    SandboxKey.LEFT:            (0xFF51,),
    SandboxKey.UP:              (0xFF52,),
    SandboxKey.RIGHT:           (0xFF53,),
    SandboxKey.DOWN:            (0xFF54,),
    **{SandboxKey(f"f{n}"): (0xFFBD + n,) for n in range(1, 13)},
    SandboxKey.SHIFT:           (0xFFE1,),
    SandboxKey.CTRL_ALT_DELETE: (0xFFE3, 0xFFE9, 0xFFFF),
}


class ConsoleClosedError(Exception):
    """The console connection closed before the expected data arrived.

    Internal: callers decide from the phase whether to reconnect, and raise an APIError
    when they give up.
    """


class _Reader:
    """Reads exact byte counts from a stream of binary WebSocket messages."""

    def __init__(self, ws: BinarySocket) -> None:
        self._ws = ws
        self._buf = bytearray()

    async def read(self, n: int) -> bytes:
        while len(self._buf) < n:
            msg = await self._ws.receive()
            if msg.type != aiohttp.WSMsgType.BINARY:
                raise ConsoleClosedError(_CLOSED)
            self._buf += msg.data
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    async def unpack(self, fmt: str) -> tuple[int, ...]:
        return struct.unpack(fmt, await self.read(struct.calcsize(fmt)))

    async def reason(self) -> str:
        (length,) = await self.unpack(">I")
        return (await self.read(length)).decode(errors="replace")


async def _handshake(ws: BinarySocket, reader: _Reader) -> tuple[int, int]:
    """Negotiate protocol 3.8 with security None; return the screen (width, height)."""
    if await reader.read(len(_VERSION)) != _VERSION:
        raise APIError("The sandbox console uses an unsupported protocol version.")
    await ws.send_bytes(_VERSION)
    (count,) = await reader.unpack(">B")
    if count == 0:
        raise APIError(f"The sandbox console refused the connection: {await reader.reason()}")
    if _SECURITY_NONE not in await reader.read(count):
        raise APIError("The sandbox console requires a sign-in method that is not supported.")
    await ws.send_bytes(bytes([_SECURITY_NONE]))
    (result,) = await reader.unpack(">I")
    if result != 0:
        raise APIError(f"The sandbox console refused the connection: {await reader.reason()}")
    await ws.send_bytes(b"\x01")  # ClientInit: share the desktop with other viewers
    width, height = await reader.unpack(">HH")
    await reader.read(16)  # server pixel format; replaced by _PIXEL_FORMAT below
    await reader.read((await reader.unpack(">I"))[0])  # desktop name
    return width, height


class RfbClient:
    """One RFB session on a fresh console connection."""

    def __init__(self, ws: BinarySocket, reader: _Reader, width: int, height: int) -> None:
        self._ws = ws
        self._reader = reader
        self._width = width
        self._height = height

    @classmethod
    async def start(cls, ws: BinarySocket) -> RfbClient:
        """Negotiate the session and its pixel format; sends nothing to the guest."""
        reader = _Reader(ws)
        width, height = _check_size(*await _handshake(ws, reader))
        await ws.send_bytes(b"\x00\x00\x00\x00" + _PIXEL_FORMAT)
        await ws.send_bytes(
            struct.pack(">BxHiii", 2, 3, _ENCODING_ZLIB, _ENCODING_RAW, _ENCODING_DESKTOP_SIZE)
        )
        return cls(ws, reader, width, height)

    async def capture(self, keys: Sequence[SandboxKey] = (), wait: float = 0.0) -> bytes:
        """Press keys, wait, then read one full frame; return it as PNG bytes.

        The server handles client messages in order, so the keys are delivered before the
        frame is requested.
        """
        if keys:
            await self._ws.send_bytes(b"".join(_key_events(key) for key in keys))
            await asyncio.sleep(wait)
        await _request_frame(self._ws, self._width, self._height)
        return await _read_frame(self._ws, self._reader, self._width, self._height)


async def _read_frame(ws: BinarySocket, reader: _Reader, width: int, height: int) -> bytes:
    """Read server messages until one full frame has arrived; follow resolution changes."""
    frame = bytearray(width * height * _BYTES_PER_PIXEL)
    inflater = zlib.decompressobj()  # one zlib stream per connection, across all rectangles
    while True:
        (msg_type,) = await reader.unpack(">B")
        if msg_type == 0:  # FramebufferUpdate
            resized = False
            (rects,) = await reader.unpack(">xH")
            for _ in range(rects):
                x, y, w, h, encoding = await reader.unpack(">HHHHi")
                if encoding == _ENCODING_DESKTOP_SIZE:  # no pixel data: the new screen size
                    width, height = _check_size(w, h)
                    frame = bytearray(width * height * _BYTES_PER_PIXEL)
                    resized = True
                    continue
                if x + w > width or y + h > height:
                    raise APIError("The sandbox console sent an inconsistent screen update.")
                size = w * h * _BYTES_PER_PIXEL
                if encoding == _ENCODING_RAW:
                    pixels = await reader.read(size)
                elif encoding == _ENCODING_ZLIB:
                    (length,) = await reader.unpack(">I")
                    # max_length is size + 1 so that the length check below catches a
                    # rectangle that inflates past its own size.
                    pixels = inflater.decompress(await reader.read(length), size + 1)
                else:
                    raise APIError(f"The sandbox console sent an unsupported encoding ({encoding}).")
                if len(pixels) != size:
                    raise APIError("The sandbox console sent an inconsistent screen update.")
                _blit(frame, width, pixels, x, y, w, h)
            if not resized:
                return encode_png(frame, width, height)
            # The request answered so far was for the old size: ask for the whole new screen.
            await _request_frame(ws, width, height)
        elif msg_type == 1:  # SetColourMapEntries (unused in true colour)
            _, count = await reader.unpack(">xHH")
            await reader.read(count * 6)
        elif msg_type == 3:  # ServerCutText
            await reader.read(3)
            await reader.reason()
        elif msg_type != 2:  # 2 = Bell
            raise APIError(f"The sandbox console sent an unexpected message ({msg_type}).")


def _check_size(width: int, height: int) -> tuple[int, int]:
    if not width or not height:
        raise APIError("The sandbox console has no display.")
    if width > _MAX_SIDE or height > _MAX_SIDE:
        raise APIError(f"The sandbox console reported an unsupported screen size ({width}x{height}).")
    return width, height


async def _request_frame(ws: BinarySocket, width: int, height: int) -> None:
    """FramebufferUpdateRequest, non-incremental, for the whole screen."""
    await ws.send_bytes(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))


def _key_events(key: SandboxKey) -> bytes:
    """KeyEvent messages that press a key's keysyms in order and release them in reverse."""
    syms = _KEYSYMS[key]
    return b"".join(struct.pack(">BBxxI", 4, 1, sym) for sym in syms) + b"".join(
        struct.pack(">BBxxI", 4, 0, sym) for sym in reversed(syms)
    )


def _blit(frame: bytearray, width: int, pixels: bytes, x: int, y: int, w: int, h: int) -> None:
    row = w * _BYTES_PER_PIXEL
    src = memoryview(pixels)
    for r in range(h):
        start = ((y + r) * width + x) * _BYTES_PER_PIXEL
        frame[start:start + row] = src[r * row:(r + 1) * row]


def encode_png(frame: bytes | bytearray, width: int, height: int) -> bytes:
    """Encode an R, G, B, padding pixel buffer as an 8-bit RGB PNG."""
    rgb = bytearray(width * height * 3)
    for channel in range(3):
        rgb[channel::3] = frame[channel::_BYTES_PER_PIXEL]
    stride = width * 3
    scanlines = b"".join(b"\x00" + rgb[i:i + stride] for i in range(0, len(rgb), stride))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (
        _PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )
