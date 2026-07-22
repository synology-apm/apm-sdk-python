"""Unit tests for synology_apm.sdk._http.WebAPISession.

Uses aiointercept to mock all HTTP requests; no real APM connection required.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from aiointercept import aiointercept
from yarl import URL

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.exceptions import AuthenticationError
from tests.unit.sdk.conftest import BASE_URL, LOGIN_OK, LOGOUT_OK, TESTUSER_LOGIN_URL
from tests.unit.sdk.conftest import (
    connect_testuser_session as connect_session,
)
from tests.unit.sdk.conftest import (
    disconnect_testuser_session as disconnect_session,
)
from tests.unit.sdk.conftest import (
    make_testuser_session as make_session,
)

# ── Test fixtures & helpers ────────────────────────────────────────────────

# Login failure responses
LOGIN_FAIL_BAD_PASS: dict[str, Any] = {"success": False, "error": {"code": 400}}
LOGIN_FAIL_LOCKED: dict[str, Any] = {"success": False, "error": {"code": 406}}
LOGIN_FAIL_DISABLED: dict[str, Any] = {"success": False, "error": {"code": 401}}


# ── connect() ─────────────────────────────────────────────────────────────


async def test_connect_success_sets_connected_flag() -> None:
    """After a successful login, _connected should be True."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        assert session._connected is True
        await disconnect_session(m, session)


async def test_connect_wrong_password_raises_authentication_error() -> None:
    """Wrong password (error_code=400) should raise AuthenticationError and preserve the error_code."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_FAIL_BAD_PASS)
        with pytest.raises(AuthenticationError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 400
    assert session._connected is False


async def test_connect_locked_account_raises_authentication_error() -> None:
    """Locked account (error_code=406) should raise AuthenticationError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_FAIL_LOCKED)
        with pytest.raises(AuthenticationError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 406


async def test_connect_disabled_account_raises_authentication_error() -> None:
    """Disabled account (error_code=401) should raise AuthenticationError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_FAIL_DISABLED)
        with pytest.raises(AuthenticationError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 401


async def test_connect_login_failure_raises_authentication_error() -> None:
    """Login failure (success=False) should raise AuthenticationError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(
            TESTUSER_LOGIN_URL,
            payload={"success": False, "error": {"code": 400, "message": "no such account"}},
        )
        with pytest.raises(AuthenticationError):
            await session.connect()
    assert session._session is None  # session should be cleaned up


async def test_connect_cleans_up_session_on_failure() -> None:
    """After login failure, _session should be cleaned up to avoid resource leaks."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_FAIL_BAD_PASS)
        with pytest.raises(AuthenticationError):
            await session.connect()
    assert session._session is None
    assert session._connected is False


# ── disconnect() ──────────────────────────────────────────────────────────


async def test_disconnect_sends_logout_request() -> None:
    """disconnect() should call GET /api/v1/preference/logout."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        await session.disconnect()

    assert session._connected is False
    assert session._session is None


async def test_disconnect_without_connect_is_safe() -> None:
    """Calling disconnect() without first calling connect() should not raise an exception."""
    session = make_session()
    await session.disconnect()  # should not raise


async def test_disconnect_twice_is_idempotent() -> None:
    """Calling disconnect() multiple times should be safe."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        await session.disconnect()
        await session.disconnect()  # second call should not raise


async def test_disconnect_ignores_logout_errors() -> None:
    """When the logout request fails, disconnect() should still complete cleanup normally."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/preference/logout", status=500)
        await session.disconnect()  # should not raise

    assert session._session is None
    assert session._connected is False


# ── get() / post() / put() / delete() ─────────────────────────────────────


async def test_get_returns_parsed_json() -> None:
    """get() should return the JSON-parsed dict."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            payload={"workloads": [], "total": 0},
        )
        result = await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert result == {"workloads": [], "total": 0}


async def test_get_with_params_passes_query_string() -> None:
    """get() params should be passed as query string."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        # aiointercept matches on the full URL including query string
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload?namespace=ns-abc",
            payload={"workloads": [], "total": 0},
        )
        result = await session.get(
            "/api/v1/workload/device_workload",
            params={"namespace": "ns-abc"},
        )
        await disconnect_session(m, session)

    assert result["total"] == 0


async def test_post_sends_json_body() -> None:
    """post() should send a JSON body and return the parsed dict."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.post(
            f"{BASE_URL}/api/v1/workload/device_workload/backup",
            payload={"succeeded": {"namespaceWorkloadListMap": {}}, "failed": {"entries": []}},
        )
        result = await session.post(
            "/api/v1/workload/device_workload/backup",
            json={"workloadRefs": [{"uid": "uid-abc", "namespace": "ns-abc"}]},
        )
        await disconnect_session(m, session)

    assert "succeeded" in result
    assert result["failed"]["entries"] == []


async def test_put_sends_json_body() -> None:
    """put() should send a JSON body."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.put(
            f"{BASE_URL}/api/v1/workload/device_workloads/plan",
            payload={},
        )
        result = await session.put(
            "/api/v1/workload/device_workloads/plan",
            json={"nsWorkloadMap": {}, "planId": "plan-abc"},
        )
        await disconnect_session(m, session)

    assert result == {}


async def test_delete_sends_delete_request() -> None:
    """delete() should send a DELETE request."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.delete(
            f"{BASE_URL}/api/v1/plan/backup_plan/plan-abc",
            payload={},
        )
        result = await session.delete("/api/v1/plan/backup_plan/plan-abc")
        await disconnect_session(m, session)

    assert result == {}


async def test_request_without_connect_raises_authentication_error() -> None:
    """Calling get() without connecting first should raise AuthenticationError."""
    session = make_session()
    with pytest.raises(AuthenticationError, match="not connected"):
        await session.get("/api/v1/workload/device_workload")


# ── 401 automatic re-auth ──────────────────────────────────────────────────


async def test_401_triggers_reauth_and_retries_original_request() -> None:
    """HTTP 401 should automatically re-authenticate and retry the original request."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)

        # First request → 401
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)
        # re-auth: replay _do_login (GET)
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        # retry → success
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            payload={"workloads": [], "total": 0},
        )
        result = await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert result["total"] == 0


async def test_401_reauth_fails_raises_authentication_error() -> None:
    """When re-authentication also fails (e.g. account locked), should raise AuthenticationError without retrying."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)

        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_FAIL_LOCKED)

        with pytest.raises(AuthenticationError) as exc_info:
            await session.get("/api/v1/workload/device_workload")

        await session.disconnect()

    assert exc_info.value.error_code == 406


async def test_401_after_reauth_raises_not_retried_again() -> None:
    """When re-auth succeeds but the retry still returns 401, should raise AuthenticationError immediately (no third retry)."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)

        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)  # re-auth succeeds
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)  # retry still 401

        with pytest.raises(AuthenticationError):
            await session.get("/api/v1/workload/device_workload")

        await session.disconnect()


async def test_concurrent_401s_relogin_only_once() -> None:
    """Concurrent requests that hit 401 on the same expired session share a single re-login.

    Uses the same deterministic asyncio.Event rendezvous as
    test_concurrent_401_second_waiter_skips_relogin_when_epoch_already_advanced
    to force the exact interleaving, rather than relying on event-loop
    scheduling incidentally landing in the right order — real network I/O
    (unlike an in-memory mock) makes that ordering nondeterministic. This test
    checks a different angle than its sibling: the total number of real login
    network calls (m.requests), not the internal _do_login() call count.
    """
    do_login_started = asyncio.Event()
    do_login_may_finish = asyncio.Event()

    login_url = TESTUSER_LOGIN_URL

    session = make_session()
    real_do_login = session._do_login

    async def guarded_do_login() -> None:
        do_login_started.set()
        await do_login_may_finish.wait()
        await real_do_login()

    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)

        m.get(f"{BASE_URL}/api/v1/slow", status=401)
        m.get(f"{BASE_URL}/api/v1/fast", status=401)
        m.get(login_url, payload=LOGIN_OK)
        m.get(f"{BASE_URL}/api/v1/slow", payload={"which": "slow"})
        m.get(f"{BASE_URL}/api/v1/fast", payload={"which": "fast"})

        with patch.object(session, "_do_login", side_effect=guarded_do_login):
            slow_task = asyncio.create_task(session.get("/api/v1/slow"))
            await do_login_started.wait()  # slow has taken the True branch and is stuck in _do_login

            fast_task = asyncio.create_task(session.get("/api/v1/fast"))
            await asyncio.sleep(0)  # let fast reach its own 401 and queue on the (held) re-auth lock

            do_login_may_finish.set()  # let slow finish: bumps the epoch, then releases the lock
            r_slow, r_fast = await asyncio.gather(slow_task, fast_task)

        await session.disconnect()

    assert r_slow == {"which": "slow"}
    assert r_fast == {"which": "fast"}
    login_calls = sum(len(v) for k, v in m.requests.items() if "SYNO.API.Auth" in str(k[1]))
    assert login_calls == 2  # one for connect(), one shared re-login — not one per request


async def test_concurrent_401_second_waiter_skips_relogin_when_epoch_already_advanced() -> None:
    """The second of two concurrent 401s must detect the epoch already advanced (while it
    was queued on the re-auth lock) and skip calling _do_login() a second time — deterministic
    variant of test_concurrent_401s_relogin_only_once, using explicit events instead of
    incidental event-loop scheduling to force the exact interleaving onto the skip branch."""
    do_login_started = asyncio.Event()
    do_login_may_finish = asyncio.Event()
    do_login_call_count = 0

    session = make_session()
    real_do_login = session._do_login

    async def guarded_do_login() -> None:
        nonlocal do_login_call_count
        do_login_call_count += 1
        do_login_started.set()
        await do_login_may_finish.wait()
        await real_do_login()

    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)

        m.get(f"{BASE_URL}/api/v1/slow", status=401)
        m.get(f"{BASE_URL}/api/v1/fast", status=401)
        m.get(
            TESTUSER_LOGIN_URL,
            payload=LOGIN_OK,
        )
        m.get(f"{BASE_URL}/api/v1/slow", payload={"which": "slow"})
        m.get(f"{BASE_URL}/api/v1/fast", payload={"which": "fast"})

        with patch.object(session, "_do_login", side_effect=guarded_do_login):
            slow_task = asyncio.create_task(session.get("/api/v1/slow"))
            await do_login_started.wait()  # slow has taken the True branch and is stuck in _do_login

            fast_task = asyncio.create_task(session.get("/api/v1/fast"))
            await asyncio.sleep(0)  # let fast reach its own 401 and queue on the (held) re-auth lock

            do_login_may_finish.set()  # let slow finish: bumps the epoch, then releases the lock
            r_slow, r_fast = await asyncio.gather(slow_task, fast_task)

        await session.disconnect()

    assert r_slow == {"which": "slow"}
    assert r_fast == {"which": "fast"}
    assert do_login_call_count == 1  # fast must have skipped its own _do_login call


# ── URL format ─────────────────────────────────────────────────────────────


async def test_host_trailing_slash_is_stripped() -> None:
    """Trailing slash in host should be stripped to avoid double-slash paths."""
    session = WebAPISession(
        "fake-apm.test/",
        "user",
        "pass",
        verify_ssl=False,
    )
    async with aiointercept(mock_external_urls=True) as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=user&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=pass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", payload={"total": 0})
        await session.get("/api/v1/workload/device_workload")
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        await session.disconnect()


# ── connect() edge cases ───────────────────────────────────────────────────────


async def test_connect_second_call_reconnects() -> None:
    """connect() called on an already-connected session performs a fresh login."""
    session = make_session()
    login_url = TESTUSER_LOGIN_URL
    async with aiointercept(mock_external_urls=True) as m:
        m.get(login_url, payload=LOGIN_OK)
        await session.connect()
        m.get(login_url, payload=LOGIN_OK)
        await session.connect()
        await disconnect_session(m, session)

    assert len(m.requests.get(("GET", URL(login_url)), [])) == 2
