"""Unit tests for synology_apm.sdk._http.WebAPISession.

Uses aioresponses to mock all HTTP requests; no real APM connection required.
"""
from __future__ import annotations

from typing import Any

import pytest
from aioresponses import aioresponses
from yarl import URL

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.exceptions import AuthenticationError

# ── Test fixtures & helpers ────────────────────────────────────────────────

BASE_URL = "https://fake-apm.test"
HOST = "fake-apm.test"
WEBAPI_URL = f"{BASE_URL}/webapi/entry.cgi"

# Standard success responses
LOGIN_OK: dict[str, Any] = {"success": True, "data": {"sid": "test-sid-abc", "synotoken": "test-token-xyz"}}
LOGOUT_OK: dict[str, Any] = {}

# Login failure responses
LOGIN_FAIL_BAD_PASS: dict[str, Any] = {"success": False, "error": {"code": 400}}
LOGIN_FAIL_LOCKED: dict[str, Any] = {"success": False, "error": {"code": 406}}
LOGIN_FAIL_DISABLED: dict[str, Any] = {"success": False, "error": {"code": 401}}


def make_session(**kwargs: Any) -> WebAPISession:
    """Create a test session (verify_ssl=False, base_url pointing to the fake host)."""
    return WebAPISession(HOST, "testuser", "testpass", verify_ssl=False, **kwargs)


async def connect_session(m: aioresponses, session: WebAPISession) -> None:
    """Register a single GET mock inside an aioresponses context to satisfy connect()."""
    m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
    await session.connect()


async def disconnect_session(m: aioresponses, session: WebAPISession) -> None:
    """Register a logout GET mock inside an aioresponses context to satisfy disconnect()."""
    m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
    await session.disconnect()


# ── connect() ─────────────────────────────────────────────────────────────


async def test_connect_success_sets_connected_flag() -> None:
    """After a successful login, _connected should be True."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        assert session._connected is True
        await disconnect_session(m, session)


async def test_connect_wrong_password_raises_authentication_error() -> None:
    """Wrong password (error_code=400) should raise AuthenticationError and preserve the error_code."""
    session = make_session()
    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_FAIL_BAD_PASS)
        with pytest.raises(AuthenticationError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 400
    assert session._connected is False


async def test_connect_locked_account_raises_authentication_error() -> None:
    """Locked account (error_code=406) should raise AuthenticationError."""
    session = make_session()
    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_FAIL_LOCKED)
        with pytest.raises(AuthenticationError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 406


async def test_connect_disabled_account_raises_authentication_error() -> None:
    """Disabled account (error_code=401) should raise AuthenticationError."""
    session = make_session()
    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_FAIL_DISABLED)
        with pytest.raises(AuthenticationError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 401


async def test_connect_login_failure_raises_authentication_error() -> None:
    """Login failure (success=False) should raise AuthenticationError."""
    session = make_session()
    with aioresponses() as m:
        m.get(
            "https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6",
            payload={"success": False, "error": {"code": 400, "message": "no such account"}},
        )
        with pytest.raises(AuthenticationError):
            await session.connect()
    assert session._session is None  # session should be cleaned up


async def test_connect_cleans_up_session_on_failure() -> None:
    """After login failure, _session should be cleaned up to avoid resource leaks."""
    session = make_session()
    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_FAIL_BAD_PASS)
        with pytest.raises(AuthenticationError):
            await session.connect()
    assert session._session is None
    assert session._connected is False


# ── disconnect() ──────────────────────────────────────────────────────────


async def test_disconnect_sends_logout_request() -> None:
    """disconnect() should call GET /api/v1/preference/logout."""
    session = make_session()
    with aioresponses() as m:
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
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        await session.disconnect()
        await session.disconnect()  # second call should not raise


async def test_disconnect_ignores_logout_errors() -> None:
    """When the logout request fails, disconnect() should still complete cleanup normally."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/preference/logout", status=500)
        await session.disconnect()  # should not raise

    assert session._session is None
    assert session._connected is False


# ── get() / post() / put() / delete() ─────────────────────────────────────


async def test_get_returns_parsed_json() -> None:
    """get() should return the JSON-parsed dict."""
    session = make_session()
    with aioresponses() as m:
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
    with aioresponses() as m:
        await connect_session(m, session)
        # aioresponses matches on the full URL including query string
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
        await connect_session(m, session)

        # First request → 401
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)
        # re-auth: replay _do_login (GET)
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
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
    with aioresponses() as m:
        await connect_session(m, session)

        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_FAIL_LOCKED)

        with pytest.raises(AuthenticationError) as exc_info:
            await session.get("/api/v1/workload/device_workload")

        await session.disconnect()

    assert exc_info.value.error_code == 406


async def test_401_after_reauth_raises_not_retried_again() -> None:
    """When re-auth succeeds but the retry still returns 401, should raise AuthenticationError immediately (no third retry)."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)

        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)  # re-auth succeeds
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)  # retry still 401

        with pytest.raises(AuthenticationError):
            await session.get("/api/v1/workload/device_workload")

        await session.disconnect()


# ── URL format ─────────────────────────────────────────────────────────────


async def test_host_trailing_slash_is_stripped() -> None:
    """Trailing slash in host should be stripped to avoid double-slash paths."""
    session = WebAPISession(
        "fake-apm.test/",
        "user",
        "pass",
        verify_ssl=False,
    )
    with aioresponses() as m:
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
    login_url = "https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6"
    with aioresponses() as m:
        m.get(login_url, payload=LOGIN_OK)
        await session.connect()
        m.get(login_url, payload=LOGIN_OK)
        await session.connect()

    assert len(m.requests.get(("GET", URL(login_url)), [])) == 2
