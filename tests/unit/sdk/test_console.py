"""Unit tests for synology_apm.sdk._console: how SandboxConsole reaches a VM's console.

The console link GETs go through aiointercept; the console WebSocket is a FakeConsoleOpener
(tests/unit/sdk/rfb_fakes.py) at the WebAPISession.ws_connect boundary.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from aiointercept import aiointercept
from yarl import URL

from synology_apm.sdk._console import SandboxConsole, SandboxConsoles
from synology_apm.sdk.enums import SandboxKey
from synology_apm.sdk.exceptions import APIError, ConnectionTimeoutError, PermissionDeniedError
from tests.unit.sdk.conftest import make_session
from tests.unit.sdk.rfb_fakes import (
    FakeConsole,
    FakeConsoleOpener,
    decode_png,
    frame,
    greeting,
    rejected,
)

ORIGIN = "https://apm2.corp.com"
VM_ID = "vm/1"
CONSOLE_PATH = "/synovirtualization/ws/vm%2F1"
COOKIE = {"Set-Cookie": "sharing_sid=s1; path=/"}
# Tests that must not time out use TIMEOUT_S; the timeout tests use SHORT_TIMEOUT_S.
TIMEOUT_S = 5.0
SHORT_TIMEOUT_S = 0.2


def _link(n: int) -> str:
    return f"{ORIGIN}/sharing/link-{n}"


class _Minter:
    """Counts mints; mint n returns _link(n). fail fails the next mint; delay slows each."""

    def __init__(self, *, fail: bool = False, delay: float = 0) -> None:
        self.count = 0
        self.fail = fail
        self.delay = delay

    async def __call__(self) -> str:
        self.count += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            self.fail = False
            raise APIError("mint failed")
        return _link(self.count)


@dataclass
class Env:
    console: SandboxConsole
    mint: _Minter
    opener: FakeConsoleOpener
    m: Any

    def link_gets(self) -> int:
        return sum(len(self.m.requests.get(("GET", URL(_link(n))), [])) for n in range(1, 6))

    def access_ids(self) -> list[str]:
        return [sharing_id for _, _, sharing_id in self.opener.opened]


@asynccontextmanager
async def _env(
    *outcomes: FakeConsole | Exception,
    stall: bool = False,
    mint: _Minter | None = None,
    link_status: int = 200,
    debug: bool = False,
    timeout_s: float = TIMEOUT_S,
) -> AsyncIterator[Env]:
    minter = mint or _Minter()
    opener = FakeConsoleOpener(*outcomes, stall=stall)
    session = make_session(debug=debug)
    console = SandboxConsole(session, VM_ID, minter, timeout_s=timeout_s)
    async with aiointercept(mock_external_urls=True) as m:
        for n in range(1, 6):
            m.get(_link(n), status=link_status, headers=COOKIE if link_status == 200 else None, repeat=True)
        with opener.install():
            try:
                yield Env(console, minter, opener, m)
            finally:
                await session.disconnect()


async def _screenshot(console: SandboxConsole) -> bytes:
    png = await console.screenshot((), 0)
    return png


# ── Access ───────────────────────────────────────────────────────────────────


async def test_first_connection_opens_the_link_then_the_console_on_its_host() -> None:
    async with _env(frame()) as env:
        png = await _screenshot(env.console)

        assert (env.mint.count, env.link_gets()) == (1, 1)
        assert env.opener.opened == [(ORIGIN, CONSOLE_PATH, "link-1")]
        assert env.opener.cookies == [{"sharing_sid": "s1"}]  # the link's cookie
        assert len(env.opener.closed) == 1
    assert decode_png(png)[2] == bytes([1, 2, 3])


async def test_connections_reuse_the_access() -> None:
    async with _env(frame(), frame()) as env:
        await _screenshot(env.console)
        async with env.console.connect():
            pass

        assert (env.mint.count, env.link_gets()) == (1, 1)
        assert env.access_ids() == ["link-1", "link-1"]
        assert len(env.opener.closed) == 2


@pytest.mark.parametrize("link", [
    f"{ORIGIN}/other/link-1", f"{ORIGIN}/sharing/", f"{ORIGIN}/sharing/a/b", "/sharing/link-1",
], ids=["other-path", "no-id", "nested", "relative"])
async def test_malformed_link_is_not_opened(link: str) -> None:
    async def mint() -> str:
        return link

    session = make_session()
    console = SandboxConsole(session, VM_ID, mint, timeout_s=TIMEOUT_S)
    async with aiointercept(mock_external_urls=True) as m:
        with pytest.raises(APIError, match="link could not be opened"):
            async with console.connect():
                pass
        assert not m.requests
    await session.disconnect()


@pytest.mark.parametrize("status", [200, 404], ids=["no-cookie", "not-found"])
async def test_unusable_link_raises(status: int) -> None:
    session = make_session()
    console = SandboxConsole(session, VM_ID, _Minter(), timeout_s=TIMEOUT_S)
    async with aiointercept(mock_external_urls=True) as m:
        m.get(_link(1), status=status)
        with pytest.raises(APIError, match="link could not be opened") as exc_info:
            async with console.connect():
                pass
        await session.disconnect()
    assert exc_info.value.error_code == status


async def test_rejected_access_is_replaced_once() -> None:
    async with _env(rejected(400), frame()) as env:
        await _screenshot(env.console)

        assert (env.mint.count, env.link_gets()) == (2, 2)
        assert env.access_ids() == ["link-1", "link-2"]


async def test_rejected_twice_raises() -> None:
    async with _env(rejected(401), rejected(400)) as env:
        with pytest.raises(APIError, match="refused the connection") as exc_info:
            async with env.console.connect():
                pass

        assert type(exc_info.value) is APIError and exc_info.value.error_code == 400
        assert env.mint.count == 2


async def test_permission_denied_is_not_retried() -> None:
    async with _env(PermissionDeniedError("Permission denied", error_code=403)) as env:
        with pytest.raises(PermissionDeniedError):
            async with env.console.connect():
                pass

        assert (env.mint.count, len(env.opener.opened)) == (1, 1)


async def test_replacing_the_access_leaves_open_connections_alone() -> None:
    held = FakeConsole(live=True)
    async with _env(held, rejected(), frame()) as env, env.console.connect():
        await _screenshot(env.console)  # rejected on link-1, succeeds on link-2

        assert env.access_ids() == ["link-1", "link-1", "link-2"]
        assert not held.is_closed


async def test_concurrent_rejections_replace_the_access_once() -> None:
    async with _env(rejected(), rejected(), frame(), frame()) as env:
        await asyncio.gather(_screenshot(env.console), _screenshot(env.console))

        assert env.mint.count == 2
        assert sorted(env.access_ids()) == ["link-1", "link-1", "link-2", "link-2"]


async def test_failed_mint_leaves_no_access() -> None:
    async with _env(frame(), mint=_Minter(fail=True)) as env:
        with pytest.raises(APIError, match="mint failed"):
            await _screenshot(env.console)
        await _screenshot(env.console)

        assert env.mint.count == 2
        assert env.access_ids() == ["link-2"]


# ── Reconnecting and timeouts ────────────────────────────────────────────────


async def test_console_closed_while_starting_reconnects_once_on_the_same_access() -> None:
    async with _env(FakeConsole(b""), frame()) as env:
        await _screenshot(env.console)

        assert env.mint.count == 1
        assert env.access_ids() == ["link-1", "link-1"]
        assert len(env.opener.closed) == 2


async def test_console_closed_twice_while_starting_raises() -> None:
    async with _env(FakeConsole(b""), FakeConsole(b"")) as env:
        with pytest.raises(APIError, match="closed unexpectedly") as exc_info:
            await _screenshot(env.console)

        assert type(exc_info.value) is APIError
        assert len(env.opener.opened) == 2


async def test_console_closed_after_starting_is_not_retried() -> None:
    async with _env(FakeConsole(greeting(1, 1)), frame()) as env:
        with pytest.raises(APIError, match="closed unexpectedly") as exc_info:
            await _screenshot(env.console)

        assert type(exc_info.value) is APIError
        assert len(env.opener.opened) == 1
        assert len(env.opener.closed) == 1


async def test_retries_are_bounded() -> None:
    # Each of the two start attempts is rejected, gets a new access, then closes early.
    async with _env(rejected(), FakeConsole(b""), rejected(), FakeConsole(b"")) as env:
        with pytest.raises(APIError, match="closed unexpectedly"):
            await _screenshot(env.console)

        assert (env.mint.count, len(env.opener.opened)) == (3, 4)


@pytest.mark.parametrize("bridge", [False, True], ids=["rfb", "raw"])
async def test_stalled_handshake_times_out(bridge: bool) -> None:
    async with _env(stall=True, timeout_s=SHORT_TIMEOUT_S) as env:
        with pytest.raises(ConnectionTimeoutError, match="did not answer in time"):
            if bridge:
                async with env.console.connect():
                    pass
            else:
                await _screenshot(env.console)


async def test_silent_screen_times_out_after_the_keys_wait() -> None:
    async with _env(FakeConsole(greeting(1, 1), live=True), timeout_s=SHORT_TIMEOUT_S) as env:
        start = asyncio.get_running_loop().time()
        with pytest.raises(ConnectionTimeoutError, match="did not send its screen in time"):
            await env.console.screenshot([SandboxKey.SHIFT], SHORT_TIMEOUT_S)
        # Bounded by the wait plus the timeout, not just the timeout.
        assert asyncio.get_running_loop().time() - start >= 2 * SHORT_TIMEOUT_S
        assert len(env.opener.closed) == 1


async def test_stalled_rfb_start_times_out() -> None:
    async with _env(FakeConsole(live=True), timeout_s=SHORT_TIMEOUT_S) as env:
        with pytest.raises(ConnectionTimeoutError, match="did not answer in time"):
            await _screenshot(env.console)
        assert len(env.opener.closed) == 1


async def test_minting_is_not_timed() -> None:
    async with _env(frame(), mint=_Minter(delay=SHORT_TIMEOUT_S * 2), timeout_s=SHORT_TIMEOUT_S) as env:
        await _screenshot(env.console)


async def test_debug_output_hides_the_link(capsys: pytest.CaptureFixture[str]) -> None:
    async with _env(rejected(), frame(), debug=True) as env:
        await _screenshot(env.console)

    err = capsys.readouterr().err
    assert err.count(f"GET {ORIGIN}/sharing/***") == 4  # two links, a request and a response line each
    assert "link-1" not in err and "link-2" not in err


# ── serve_vnc() ──────────────────────────────────────────────────────────────


async def test_serve_vnc_probes_the_console_then_relays_each_client() -> None:
    probe, client = FakeConsole(), FakeConsole(live=True)
    async with _env(probe, client) as env:
        async with env.console.serve_vnc("127.0.0.1", 0) as port:
            assert probe.is_closed  # opened and closed before listening
            reader, _ = await asyncio.open_connection("127.0.0.1", port)
            client.feed(b"RFB 003.008\n")
            assert await asyncio.wait_for(reader.readexactly(12), 1) == b"RFB 003.008\n"
            assert env.access_ids() == ["link-1", "link-1"]
        assert client.is_closed


async def test_serve_vnc_fails_before_listening_when_the_console_is_unreachable() -> None:
    error = APIError("The connection was refused (HTTP 500).", error_code=500)
    async with _env(error) as env:
        with pytest.raises(APIError) as exc_info:
            async with env.console.serve_vnc("127.0.0.1", 0):
                pytest.fail("relay opened")
    assert exc_info.value is error


# ── SandboxConsoles ──────────────────────────────────────────────────────────


async def test_consoles_keep_the_most_recently_used() -> None:
    session = make_session()
    consoles = SandboxConsoles(session, max_consoles=2, timeout_s=TIMEOUT_S)
    mint = _Minter()
    a, b = consoles.get("ns-1", "vm-a", mint), consoles.get("ns-1", "vm-b", mint)
    assert consoles.get("ns-1", "vm-a", mint) is a  # vm-a is now the most recent
    consoles.get("ns-1", "vm-c", mint)  # drops vm-b, the least recently used

    assert consoles.get("ns-1", "vm-a", mint) is a
    assert consoles.get("ns-1", "vm-b", mint) is not b


async def test_consoles_are_kept_per_server_and_vm() -> None:
    consoles = SandboxConsoles(make_session(), timeout_s=TIMEOUT_S)
    mint = _Minter()
    on_first = consoles.get("ns-1", "vm-a", mint)

    assert consoles.get("ns-2", "vm-a", mint) is not on_first
    assert consoles.get("ns-1", "vm-a", mint) is on_first


async def test_consoles_do_not_drop_a_console_while_it_mints() -> None:
    gate = asyncio.Event()

    async def slow_mint() -> str:
        await gate.wait()
        return _link(1)

    session = make_session()
    consoles = SandboxConsoles(session, max_consoles=1, timeout_s=TIMEOUT_S)
    minting = consoles.get("ns-1", "vm-a", slow_mint)
    async with _env(frame()) as env:
        task = asyncio.create_task(_screenshot(minting))
        await asyncio.sleep(0)  # the screenshot now holds vm-a's mint
        other = consoles.get("ns-1", "vm-b", _Minter())

        assert consoles.get("ns-1", "vm-a", _Minter()) is minting  # kept although over the limit
        gate.set()
        await asyncio.wait_for(task, 5)
        consoles.get("ns-1", "vm-c", _Minter())  # both are idle now: both are dropped
        assert consoles.get("ns-1", "vm-b", _Minter()) is not other
        assert env.access_ids() == ["link-1"]
    await session.disconnect()


async def test_consoles_use_the_mint_given_when_created() -> None:
    first, second = _Minter(), _Minter()
    consoles = SandboxConsoles(make_session(), timeout_s=TIMEOUT_S)
    console = consoles.get("ns-1", "vm-a", first)
    assert consoles.get("ns-1", "vm-a", second) is console
    async with _env(frame()) as env:
        await _screenshot(console)
        assert (first.count, second.count) == (1, 0)
        assert env.access_ids() == ["link-1"]
