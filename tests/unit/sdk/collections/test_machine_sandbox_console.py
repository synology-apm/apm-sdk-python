"""Unit tests for MachineSandboxCollection's console: get_console_link(), get_screenshot(), open_vnc_bridge()."""
from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import AsyncIterator
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from aiointercept import aiointercept
from yarl import URL

from synology_apm.sdk.collections.machine_sandbox import MachineSandboxCollection
from synology_apm.sdk.enums import (
    RestoreActivityStatus,
    RestoreType,
    SandboxKey,
)
from synology_apm.sdk.exceptions import (
    APIError,
    InvalidOperationError,
    ResourceNotFoundError,
    ResourceNotReadyError,
)
from tests.unit.sandbox_builders import ACT_NS, VM_GUEST, WL_ID, WL_NS, make_sandbox_activity
from tests.unit.sdk.collections._sandbox_fixtures import (
    ERR_POWER_ON,
    ERR_SYSTEM,
    NS_A,
    NS_B,
    SERVERS,
    Env,
    FakeVmm,
    make_sandboxes,
    server_raw,
)
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, make_session, patched_session
from tests.unit.sdk.rfb_fakes import (
    FakeConsole,
    FakeConsoleOpener,
    decode_png,
    frame,
    greeting,
    raw_rect,
    rejected,
    solid,
    update,
)

# ── get_console_link() ───────────────────────────────────────────────────────


async def test_console_link_on_primary_uses_the_session_host(env: Env) -> None:
    url = await env.sandboxes.get_console_link(make_sandbox_activity())

    assert url == f"{BASE_URL}/sharing/AbCdEf123"
    assert env.vmm.ops() == ["backup_server", "connect_to_vm"]  # lookup first: a failure mints nothing
    call = env.vmm.one("connect_to_vm")
    assert call.method == "POST"
    assert call.body == {"guestId": VM_GUEST, "workload": {"uid": WL_ID, "namespace": WL_NS}}
    assert call.headers == {"x-syno-tunnel-route": ACT_NS}


async def test_console_link_on_another_server_opens_on_that_server(env: Env) -> None:
    url = await env.sandboxes.get_console_link(make_sandbox_activity(namespace=NS_B))

    assert url == "https://192.0.2.2/sharing/AbCdEf123"
    assert env.vmm.one("connect_to_vm").headers == {"x-syno-tunnel-route": NS_B}


@pytest.mark.parametrize(("addr", "origin"), [
    ("2001:db8::2", "https://[2001:db8::2]"),
    ("apm-server-02.example.com", "https://apm-server-02.example.com"),
], ids=["ipv6", "hostname"])
async def test_console_link_on_another_server_uses_its_address(env: Env, addr: str, origin: str) -> None:
    servers = [SERVERS[0], server_raw(NS_B, "apm-server-02", addr, "REPLICA")]
    env.vmm.responses["backup_server"] = {"backupServers": servers, "total": len(servers)}

    url = await env.sandboxes.get_console_link(make_sandbox_activity(namespace=NS_B))

    assert url == f"{origin}/sharing/AbCdEf123"
    assert env.vmm.ops() == ["backup_server", "connect_to_vm"]  # no tiering-plan lookups


async def test_console_link_keeps_an_absolute_link(env: Env) -> None:
    env.vmm.responses["connect_to_vm"] = {"url": "https://192.0.2.9/sharing/AbCdEf123"}

    url = await env.sandboxes.get_console_link(make_sandbox_activity(namespace=NS_B))

    assert url == "https://192.0.2.9/sharing/AbCdEf123"


async def test_console_link_unknown_server_mints_nothing(env: Env) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await env.sandboxes.get_console_link(make_sandbox_activity(namespace=NS_A))

    assert_resource_error(exc_info, resource_type="BackupServer", resource_id=NS_A)
    assert "connect_to_vm" not in env.vmm.ops()


_STARTING = {RestoreActivityStatus.PREPARING, RestoreActivityStatus.RESTORING}


@pytest.mark.parametrize("overrides", [
    {"restore_type": RestoreType.INSTANT_VMWARE},
    {"sandbox_vm_id": None},
    *({"status": s} for s in RestoreActivityStatus if s != RestoreActivityStatus.SANDBOX_READY and s not in _STARTING),
], ids=lambda o: "-".join(f"{k}={getattr(v, 'value', v)}" for k, v in o.items()))
async def test_console_link_requires_running_sandbox(env: Env, overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.get_console_link(make_sandbox_activity(**overrides))
    assert_resource_error(exc_info, resource_type="RestoreActivity", resource_id="rst-uid-001")
    assert env.vmm.calls == []


@pytest.mark.parametrize("status", [RestoreActivityStatus.PREPARING, RestoreActivityStatus.RESTORING],
                         ids=["preparing", "restoring"])
async def test_console_link_of_starting_sandbox_is_not_ready(env: Env, status: RestoreActivityStatus) -> None:
    with pytest.raises(ResourceNotReadyError, match="still starting"):
        await env.sandboxes.get_console_link(make_sandbox_activity(status=status))
    with pytest.raises(ResourceNotReadyError, match="still starting"):
        await env.sandboxes.get_console_link(make_sandbox_activity(status=status, sandbox_vm_id=None))
    assert env.vmm.calls == []


async def test_console_link_power_on_failure_maps_to_invalid_operation(env: Env) -> None:
    env.vmm.errors["connect_to_vm"] = ERR_POWER_ON
    with pytest.raises(InvalidOperationError, match="could not be powered on") as exc_info:
        await env.sandboxes.get_console_link(make_sandbox_activity())
    assert_resource_error(exc_info, resource_type="RestoreActivity", resource_id="rst-uid-001")
    assert exc_info.value.error_code == 932


async def test_console_link_missing_vm_raises_not_found(env: Env) -> None:
    # An unknown guest answers HTTP 404 with empty details.
    env.vmm.errors["connect_to_vm"] = ResourceNotFoundError(
        "Resource not found", resource_type="unknown", resource_id="", error_code=404,
        response_body={"error": {"code": 404, "message": "Not Found", "details": []}},
    )
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await env.sandboxes.get_console_link(make_sandbox_activity())
    assert_resource_error(exc_info, resource_type="RestoreActivity", resource_id="rst-uid-001")


async def test_console_link_other_api_error_propagates(env: Env) -> None:
    env.vmm.errors["connect_to_vm"] = ERR_SYSTEM
    with pytest.raises(APIError) as exc_info:
        await env.sandboxes.get_console_link(make_sandbox_activity())
    assert exc_info.value is ERR_SYSTEM


# ── get_screenshot() / open_vnc_bridge() ─────────────────────────────────────

CONSOLE_PATH = f"/synovirtualization/ws/{VM_GUEST}"
LINK_ID = "AbCdEf123"
LINK_PATH = f"/sharing/{LINK_ID}"
# aiointercept serves host names only, so the secondary here has a name, not an IP.
SECONDARY_ORIGIN = "https://apm-server-02"
CONSOLE_SERVERS = [
    server_raw(ACT_NS, "apm-server-01", "192.0.2.1", "LEADER"),
    server_raw(NS_B, "apm-server-02", "apm-server-02", "REPLICA"),
]
_NOT_RUNNING = [
    {"restore_type": RestoreType.INSTANT_VMWARE},
    {"sandbox_vm_id": None},
    {"status": RestoreActivityStatus.CANCELED},
]


class Console(NamedTuple):
    sandboxes: MachineSandboxCollection
    opener: FakeConsoleOpener
    vmm: FakeVmm
    m: aiointercept

    def mints(self) -> int:
        return self.vmm.ops().count("connect_to_vm")

    def link_gets(self, origin: str = BASE_URL) -> int:
        return len(self.m.requests.get(("GET", URL(origin + LINK_PATH)), []))


@contextlib.asynccontextmanager
async def _console_env(
    *outcomes: FakeConsole | Exception, cookie: bool = True,
) -> AsyncIterator[Console]:
    """The collection on a fake APM: vmm and server-list calls, console links, and consoles."""
    session = make_session()
    vmm = FakeVmm()
    vmm.responses["backup_server"] = {"backupServers": CONSOLE_SERVERS, "total": len(CONSOLE_SERVERS)}
    opener = FakeConsoleOpener(*outcomes)

    async with aiointercept(mock_external_urls=True) as m:
        for origin in (BASE_URL, SECONDARY_ORIGIN):
            m.get(origin + LINK_PATH, headers={"Set-Cookie": "sharing_sid=s1; path=/"} if cookie else None, repeat=True)
        with patched_session(session, get=vmm.get, post=vmm.post), opener.install():
            try:
                yield Console(make_sandboxes(session), opener, vmm, m)
            finally:
                await session.disconnect()


async def test_screenshot_opens_console_access_then_the_console() -> None:
    frame = greeting(2, 1) + update(raw_rect(0, 0, 2, 1, solid(2, 1, (1, 2, 3))))
    async with _console_env(FakeConsole(frame)) as con:
        shot = await con.sandboxes.get_screenshot(make_sandbox_activity())

        assert con.vmm.ops() == ["backup_server", "connect_to_vm"]
        assert con.vmm.one("connect_to_vm").headers == {"x-syno-tunnel-route": ACT_NS}
        assert con.link_gets() == 1
        assert con.opener.opened == [(BASE_URL, CONSOLE_PATH, LINK_ID)]
        assert len(con.opener.closed) == 1
    assert decode_png(shot) == (2, 1, bytes([1, 2, 3, 1, 2, 3]))


async def test_screenshots_on_one_client_reuse_the_access() -> None:
    async with _console_env(frame(), frame()) as con:
        await con.sandboxes.get_screenshot(make_sandbox_activity())
        await con.sandboxes.get_screenshot(make_sandbox_activity())

        assert (con.mints(), con.link_gets()) == (1, 1)
        assert len(con.opener.opened) == 2


async def test_screenshot_on_another_server_opens_there() -> None:
    async with _console_env(frame()) as con:
        await con.sandboxes.get_screenshot(make_sandbox_activity(namespace=NS_B))

        assert con.link_gets(SECONDARY_ORIGIN) == 1
        assert con.opener.opened == [(SECONDARY_ORIGIN, CONSOLE_PATH, LINK_ID)]


async def test_rejected_access_is_replaced_through_a_new_link() -> None:
    # The retry policy itself is tested in test_console.py; this checks the new link's wiring.
    async with _console_env(rejected(), frame()) as con:
        await con.sandboxes.get_screenshot(make_sandbox_activity())

        assert con.vmm.ops() == ["backup_server", "connect_to_vm", "backup_server", "connect_to_vm"]
        assert (con.link_gets(), len(con.opener.opened)) == (2, 2)


async def test_screenshot_and_bridge_of_starting_sandbox_are_not_ready() -> None:
    starting = make_sandbox_activity(status=RestoreActivityStatus.RESTORING)
    async with _console_env() as con:
        with pytest.raises(ResourceNotReadyError):
            await con.sandboxes.get_screenshot(starting)
        with pytest.raises(ResourceNotReadyError):
            async with con.sandboxes.open_vnc_bridge(starting):
                pass
        assert (con.vmm.calls, con.opener.opened) == ([], [])


async def test_screenshot_with_keys_uses_one_console_and_default_delay() -> None:
    console = frame((9, 9, 9))
    sleep = AsyncMock()
    async with _console_env(console) as con:
        with patch("asyncio.sleep", sleep):
            shot = await con.sandboxes.get_screenshot(make_sandbox_activity(), keys=[SandboxKey.SHIFT])

        assert len(con.opener.opened) == 1
    # The fake APM also yields with sleep(0); the key delay is the only other sleep.
    assert [c.args for c in sleep.await_args_list if c.args != (0,)] == [(2.0,)]
    assert struct.pack(">BBxxI", 4, 1, 0xFFE1) in b"".join(console.sent)
    assert decode_png(shot)[2] == bytes([9, 9, 9])


@pytest.mark.parametrize(("keys", "delay"), [
    ([], 1.0),
    ([SandboxKey.ENTER], -1),
    ([SandboxKey.ENTER], 61),
], ids=["delay-without-keys", "negative-delay", "delay-over-60"])
async def test_screenshot_rejects_invalid_delay(keys: list[SandboxKey], delay: float) -> None:
    async with _console_env() as con:
        with pytest.raises(ValueError, match="delay_seconds"):
            await con.sandboxes.get_screenshot(make_sandbox_activity(), keys=keys, delay_seconds=delay)
        assert (con.vmm.calls, con.opener.opened) == ([], [])


async def test_screenshot_with_keys_waits_the_given_delay() -> None:
    sleep = AsyncMock()
    async with _console_env(frame()) as con:
        with patch("asyncio.sleep", sleep):
            await con.sandboxes.get_screenshot(make_sandbox_activity(), keys=[SandboxKey.ENTER], delay_seconds=0.5)
    assert [c.args for c in sleep.await_args_list if c.args != (0,)] == [(0.5,)]


async def test_failed_mint_leaves_no_access() -> None:
    async with _console_env(frame()) as con:
        con.vmm.errors["connect_to_vm"] = ERR_POWER_ON
        with pytest.raises(InvalidOperationError, match="could not be powered on"):
            await con.sandboxes.get_screenshot(make_sandbox_activity())
        del con.vmm.errors["connect_to_vm"]

        await con.sandboxes.get_screenshot(make_sandbox_activity())
        assert (con.mints(), con.link_gets()) == (2, 1)


async def test_used_up_link_raises() -> None:
    async with _console_env(cookie=False) as con:
        with pytest.raises(APIError, match="link could not be opened"):
            await con.sandboxes.get_screenshot(make_sandbox_activity())
        assert con.opener.opened == []


async def test_screenshot_console_error_propagates() -> None:
    error = APIError("The connection was refused (HTTP 500).", error_code=500)
    async with _console_env(error) as con:
        with pytest.raises(APIError) as exc_info:
            await con.sandboxes.get_screenshot(make_sandbox_activity())
    assert exc_info.value is error


async def test_screenshot_escapes_the_vm_id_in_the_console_path() -> None:
    async with _console_env(frame()) as con:
        await con.sandboxes.get_screenshot(make_sandbox_activity(sandbox_vm_id="vm/1?x"))

        [(_, path, _)] = con.opener.opened
    assert path == "/synovirtualization/ws/vm%2F1%3Fx"


@pytest.mark.parametrize("overrides", _NOT_RUNNING, ids=["not-sandbox", "no-vm", "ended"])
async def test_screenshot_and_bridge_require_running_sandbox(overrides: dict[str, Any]) -> None:
    async with _console_env() as con:
        with pytest.raises(InvalidOperationError) as exc_info:
            await con.sandboxes.get_screenshot(make_sandbox_activity(**overrides))
        assert_resource_error(exc_info, resource_type="RestoreActivity", resource_id="rst-uid-001")
        with pytest.raises(InvalidOperationError) as exc_info:
            async with con.sandboxes.open_vnc_bridge(make_sandbox_activity(**overrides)):
                pass
        assert_resource_error(exc_info, resource_type="RestoreActivity", resource_id="rst-uid-001")
        assert (con.vmm.calls, con.opener.opened) == ([], [])


async def test_bridge_relays_clients_through_the_console_access() -> None:
    probe, client_console = FakeConsole(), FakeConsole(live=True)
    async with _console_env(probe, client_console) as con:
        async with con.sandboxes.open_vnc_bridge(make_sandbox_activity()) as endpoint:
            assert endpoint.to_dict() == {"host": "127.0.0.1", "port": endpoint.port, "url": f"vnc://127.0.0.1:{endpoint.port}"}
            reader, _ = await asyncio.open_connection(endpoint.host, endpoint.port)
            client_console.feed(b"RFB 003.008\n")
            assert await asyncio.wait_for(reader.readexactly(12), 1) == b"RFB 003.008\n"

            # One access for the open check and the client.
            assert con.opener.opened == [(BASE_URL, CONSOLE_PATH, LINK_ID)] * 2
            assert (con.mints(), con.link_gets()) == (1, 1)
        assert probe.is_closed and client_console.is_closed


async def test_bridge_fails_on_open_when_console_unreachable() -> None:
    error = APIError("The connection was refused (HTTP 500).", error_code=500)
    async with _console_env(error) as con:
        with pytest.raises(APIError) as exc_info:
            async with con.sandboxes.open_vnc_bridge(make_sandbox_activity()):
                pytest.fail("bridge opened")
    assert exc_info.value is error
