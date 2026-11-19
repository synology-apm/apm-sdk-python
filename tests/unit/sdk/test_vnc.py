"""Unit tests for synology_apm.sdk._vnc: the screenshot client's protocol and decoding.

RfbClient is branch-heavy byte handling that SandboxConsole and get_screenshot() only wrap, so
its cases are tested here directly; test_console.py and test_machine_sandbox_console.py cover
the wiring.
"""
from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence
from unittest.mock import AsyncMock, patch

import pytest

from synology_apm.sdk._console import BinarySocket
from synology_apm.sdk._vnc import ConsoleClosedError, RfbClient
from synology_apm.sdk.enums import SandboxKey
from synology_apm.sdk.exceptions import APIError
from tests.unit.sdk.rfb_fakes import (
    ENCODINGS_MSG,
    PIXEL_FORMAT_MSG,
    VERSION,
    FakeConsole,
    decode_png,
    desktop_size,
    greeting,
    raw_rect,
    solid,
    update,
    update_request,
    zlib_rect,
)

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


def _rgb(w: int, h: int, colour: tuple[int, int, int]) -> bytes:
    return bytes(colour) * (w * h)


async def capture_png(
    ws: BinarySocket, keys: Sequence[SandboxKey] = (), wait: float = 0.0,
) -> bytes:
    """Both phases on one console, as get_screenshot() runs them."""
    return await (await RfbClient.start(ws)).capture(keys, wait)


async def test_start_negotiates_without_touching_the_guest() -> None:
    console = FakeConsole(greeting(4, 2), live=True)

    await RfbClient.start(console)

    assert console.sent == [VERSION, b"\x01", b"\x01", PIXEL_FORMAT_MSG, ENCODINGS_MSG]


async def test_capture_handshake_and_requests() -> None:
    console = FakeConsole(greeting(4, 2) + update(raw_rect(0, 0, 4, 2, solid(4, 2, RED))))

    await capture_png(console)

    assert console.sent == [VERSION, b"\x01", b"\x01", PIXEL_FORMAT_MSG, ENCODINGS_MSG, update_request(4, 2)]


async def test_capture_raw_full_screen() -> None:
    console = FakeConsole(greeting(4, 2) + update(raw_rect(0, 0, 4, 2, solid(4, 2, RED))))

    png = await capture_png(console)

    assert decode_png(png) == (4, 2, _rgb(4, 2, RED))


async def test_capture_zlib_rectangles_share_one_stream() -> None:
    # The second rectangle only inflates with the first one's dictionary still in place.
    stream = zlib.compressobj()
    console = FakeConsole(greeting(4, 2) + update(
        zlib_rect(0, 0, 4, 1, solid(4, 1, GREEN), stream),
        zlib_rect(0, 1, 4, 1, solid(4, 1, GREEN), stream),
    ))

    png = await capture_png(console)

    assert decode_png(png) == (4, 2, _rgb(4, 2, GREEN))


async def test_capture_mixed_rectangles_and_uncovered_area() -> None:
    # Left column raw red, top-right zlib blue; the bottom-right pixel is never sent: black.
    console = FakeConsole(greeting(2, 2) + update(
        raw_rect(0, 0, 1, 2, solid(1, 2, RED)),
        zlib_rect(1, 0, 1, 1, solid(1, 1, BLUE), zlib.compressobj()),
    ))

    png = await capture_png(console)

    assert decode_png(png)[2] == bytes([*RED, *BLUE, *RED, 0, 0, 0])


async def test_capture_reassembles_messages_split_across_frames() -> None:
    console = FakeConsole(greeting(3, 1) + update(raw_rect(0, 0, 3, 1, solid(3, 1, BLUE))), chunk=1)

    png = await capture_png(console)

    assert decode_png(png) == (3, 1, _rgb(3, 1, BLUE))


async def test_capture_skips_other_server_messages_before_the_update() -> None:
    bell = b"\x02"
    cut_text = b"\x03\x00\x00\x00" + struct.pack(">I", 5) + b"hello"
    colour_map = b"\x01\x00" + struct.pack(">HH", 0, 2) + bytes(12)
    console = FakeConsole(
        greeting(1, 1) + bell + cut_text + colour_map + update(raw_rect(0, 0, 1, 1, solid(1, 1, GREEN)))
    )

    png = await capture_png(console)

    assert decode_png(png)[2] == bytes(GREEN)


async def test_capture_follows_a_resolution_change_and_requests_the_new_screen() -> None:
    # The guest switches 4x2 -> 2x1 after the request: the client re-requests the new size.
    console = FakeConsole(
        greeting(4, 2)
        + update(desktop_size(2, 1))
        + update(raw_rect(0, 0, 2, 1, solid(2, 1, GREEN)))
    )

    png = await capture_png(console)

    assert decode_png(png) == (2, 1, _rgb(2, 1, GREEN))
    assert console.sent[-2:] == [update_request(4, 2), update_request(2, 1)]


async def test_capture_resize_rectangles_after_desktop_size_use_the_new_size() -> None:
    # Pixels in the same update as the resize still land in the new frame; then a full re-request.
    console = FakeConsole(
        greeting(1, 1)
        + update(desktop_size(3, 1), raw_rect(0, 0, 3, 1, solid(3, 1, RED)))
        + update(raw_rect(2, 0, 1, 1, solid(1, 1, BLUE)))
    )

    png = await capture_png(console)

    assert decode_png(png) == (3, 1, bytes([*RED, *RED, *BLUE]))


@pytest.mark.parametrize(("server", "message"), [
    (greeting(1, 1, version=b"RFB 003.003\n"), "unsupported protocol version"),
    (VERSION + b"\x00" + struct.pack(">I", 4) + b"busy", "refused the connection: busy"),
    (greeting(1, 1, security=b"\x01\x02"), "sign-in method that is not supported"),
    (VERSION + b"\x01\x01" + struct.pack(">I", 1) + struct.pack(">I", 6) + b"denied", "refused the connection: denied"),
    (greeting(0, 0), "has no display"),
    (greeting(4097, 1), r"unsupported screen size \(4097x1\)"),
    (greeting(1, 1) + update(desktop_size(1, 5000)), r"unsupported screen size \(1x5000\)"),
    (greeting(1, 1) + update(zlib_rect(0, 0, 1, 1, solid(2, 1, RED), zlib.compressobj())),
     "inconsistent screen update"),
    (greeting(1, 1) + update(struct.pack(">HHHHi", 0, 0, 1, 1, 16)), r"unsupported encoding \(16\)"),
    (greeting(1, 1) + update(raw_rect(0, 0, 2, 1, solid(2, 1, RED))), "inconsistent screen update"),
    (greeting(1, 1) + b"\x09", r"unexpected message \(9\)"),
], ids=[
    "old-version", "no-security-types", "no-none-security", "security-failed", "no-display",
    "screen-too-large", "resized-too-large", "zlib-inflates-past-rect",
    "unknown-encoding", "rect-out-of-bounds", "unknown-message",
])
async def test_capture_rejects_unusable_console(server: bytes, message: str) -> None:
    with pytest.raises(APIError, match=message):
        await capture_png(FakeConsole(server))


@pytest.mark.parametrize("server", [b"", VERSION, greeting(1, 1)[:-3]], ids=["at-once", "after-version", "mid-init"])
async def test_start_closed_console_raises(server: bytes) -> None:
    with pytest.raises(ConsoleClosedError, match="closed unexpectedly"):
        await RfbClient.start(FakeConsole(server))


@pytest.mark.parametrize(("server", "keys"), [
    (greeting(1, 1), []),  # closed while waiting for the frame
    (greeting(2, 2) + update(raw_rect(0, 0, 2, 2, solid(2, 1, RED))), []),  # mid-rectangle
    (greeting(1, 1), [SandboxKey.ENTER]),  # closed after the keys went out
], ids=["frame", "mid-rectangle", "after-keys"])
async def test_capture_closed_console_raises(server: bytes, keys: list[SandboxKey]) -> None:
    rfb = await RfbClient.start(FakeConsole(server))
    with pytest.raises(ConsoleClosedError, match="closed unexpectedly"):
        await rfb.capture(keys, 0)


def _key(sym: int, down: bool) -> bytes:
    return struct.pack(">BBxxI", 4, int(down), sym)


def _key_messages(sent: list[bytes]) -> bytes:
    """The key-event message the client sent (between SetEncodings and the update request)."""
    return sent[sent.index(ENCODINGS_MSG) + 1]


async def test_capture_presses_keys_then_waits_then_requests_frame() -> None:
    console = FakeConsole(greeting(1, 1) + update(raw_rect(0, 0, 1, 1, solid(1, 1, RED))))
    order: list[str] = []
    sleep = AsyncMock(side_effect=lambda s: order.append(f"sleep {s} after {len(console.sent)} messages"))

    with patch("asyncio.sleep", sleep):
        png = await capture_png(console, [SandboxKey.ENTER], 1.5)

    assert console.sent == [
        VERSION, b"\x01", b"\x01", PIXEL_FORMAT_MSG, ENCODINGS_MSG,
        _key(0xFF0D, True) + _key(0xFF0D, False),
        update_request(1, 1),
    ]
    assert order == ["sleep 1.5 after 6 messages"]  # after the keys, before the request
    assert decode_png(png)[2] == bytes(RED)


async def test_capture_combination_presses_in_order_and_releases_in_reverse() -> None:
    console = FakeConsole(greeting(1, 1) + update(raw_rect(0, 0, 1, 1, solid(1, 1, RED))))

    await capture_png(console, [SandboxKey.CTRL_ALT_DELETE, SandboxKey.TAB], 0)

    ctrl, alt, delete, tab = 0xFFE3, 0xFFE9, 0xFFFF, 0xFF09
    assert _key_messages(console.sent) == (
        _key(ctrl, True) + _key(alt, True) + _key(delete, True)
        + _key(delete, False) + _key(alt, False) + _key(ctrl, False)
        + _key(tab, True) + _key(tab, False)
    )


async def _keysyms(key: SandboxKey) -> tuple[int, ...]:
    console = FakeConsole(greeting(1, 1) + update(raw_rect(0, 0, 1, 1, solid(1, 1, RED))))
    await capture_png(console, [key], 0)
    events = _key_messages(console.sent)
    pressed = [struct.unpack(">BBxxI", events[i:i + 8]) for i in range(0, len(events), 8)]
    assert all(msg_type == 4 for msg_type, _, _ in pressed)
    return tuple(sym for _, down, sym in pressed if down)


async def test_every_key_sends_its_own_keysyms() -> None:
    sent = {key: await _keysyms(key) for key in SandboxKey}

    assert all(sent.values())
    assert len(set(sent.values())) == len(SandboxKey)
    assert sent[SandboxKey.F1] == (0xFFBE,) and sent[SandboxKey.F12] == (0xFFC9,)
