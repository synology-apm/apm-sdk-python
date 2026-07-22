"""Unit tests for synology_apm.sdk._http.WebAPISession: error handling.

Covers HTTP status code → exception mapping, JSON body error code mapping, and
SSL/connection-level error handling. Uses aiointercept to mock all HTTP requests;
no real APM connection required. The SSL/connection section reproduces real
network failure conditions (a genuinely untrusted TLS cert, a real closed port,
a real slow response past a tiny timeout) rather than injecting fake exception
instances, since aiointercept has no equivalent to aioresponses' exception=<instance>.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiointercept import CallbackResult, aiointercept

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.exceptions import (
    APIError,
    AuthenticationError,
    BackupServerDisconnectedError,
    ConnectionTimeoutError,
    NotSupportedError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from tests.unit.sdk.conftest import (
    BASE_URL,
    HOST,
    LOGIN_OK,
    TESTUSER_LOGIN_URL,
    closed_port,
    tls_test_server,
)
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


# ── HTTP status code → exception mapping ──────────────────────────────────


async def test_403_raises_permission_denied_error() -> None:
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan", status=403)
        with pytest.raises(PermissionDeniedError):
            await session.get("/api/v1/plan/backup_plan")
        await session.disconnect()


async def test_404_raises_resource_not_found_error() -> None:
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload/bad-id", status=404)
        with pytest.raises(ResourceNotFoundError):
            await session.get("/api/v1/workload/device_workload/bad-id")
        await session.disconnect()


async def test_501_raises_not_supported_error() -> None:
    """HTTP 501 should raise NotSupportedError (as of APM 1.2, returns 501 for M365 workloads)."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/m365_workload", status=501)
        with pytest.raises(NotSupportedError):
            await session.get("/api/v1/workload/m365_workload")
        await session.disconnect()


async def test_500_raises_api_error() -> None:
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=500)
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await session.disconnect()

    assert exc_info.value.error_code == 500


async def test_error_detail_code_2003_raises_backup_server_disconnected() -> None:
    """error.details[0].errorCode=2003 (backup server disconnected) should raise BackupServerDisconnectedError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            status=503,
            payload={
                "error": {
                    "code": 503,
                    "status": "Service Unavailable",
                    "message": "server tunnel invoke failed",
                    "details": [
                        {
                            "@type": "type.googleapis.com/api.ErrorDetail",
                            "errorCode": 2003,
                            "message": "connect node failed",
                        }
                    ],
                }
            },
        )
        with pytest.raises(BackupServerDisconnectedError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await session.disconnect()

    assert exc_info.value.error_code == 2003


async def test_error_detail_code_1402_raises_resource_not_found() -> None:
    """error.details[0].errorCode=1402 (backup server ID not found) should raise ResourceNotFoundError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            status=500,
            payload={
                "error": {
                    "code": 500,
                    "status": "Internal Server Error",
                    "message": "backup server not found",
                    "details": [
                        {
                            "@type": "type.googleapis.com/api.ErrorDetail",
                            "errorCode": 1402,
                            "message": "backup server not found",
                        }
                    ],
                }
            },
        )
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await session.disconnect()

    assert exc_info.value.error_code == 1402


# ── JSON body error code mapping ───────────────────────────────────────────


async def test_error_code_in_body_format1_raises_api_error() -> None:
    """{"error": {"code": N, "message": "..."}} format should raise the corresponding exception."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/plan/backup_plan",
            payload={"error": {"code": 7003, "message": "workload can not change to backup plan"}},
        )
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/plan/backup_plan")
        await session.disconnect()

    assert exc_info.value.error_code == 7003


async def test_error_code_in_body_format2_raises_api_error() -> None:
    """{"errorCode": N, "message": "..."} format should raise the corresponding exception."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.put(
            f"{BASE_URL}/api/v1/workload/device_workloads/plan",
            payload={"errorCode": 7003, "message": "workload can not change to backup plan"},
        )
        with pytest.raises(APIError) as exc_info:
            await session.put("/api/v1/workload/device_workloads/plan", json={})
        await session.disconnect()

    assert exc_info.value.error_code == 7003


async def test_error_code_7000_in_body_raises_resource_not_found() -> None:
    """errorCode=7000 (resource not found) should raise ResourceNotFoundError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload/missing-id",
            payload={"message": "resource not found", "errorCode": 7000},
        )
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await session.get("/api/v1/workload/device_workload/missing-id")
        await session.disconnect()

    assert exc_info.value.error_code == 7000


async def test_error_code_105_in_body_raises_permission_denied() -> None:
    """error.code=105 (permission denied) should raise PermissionDeniedError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/plan/backup_plan",
            payload={"error": {"code": 105, "message": "permission denied"}},
        )
        with pytest.raises(PermissionDeniedError) as exc_info:
            await session.get("/api/v1/plan/backup_plan")
        await session.disconnect()

    assert exc_info.value.error_code == 105


async def test_error_code_119_in_body_raises_authentication_error() -> None:
    """errorCode=119 (session expired) should raise AuthenticationError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            payload={"errorCode": 119, "message": "session expired"},
        )
        with pytest.raises(AuthenticationError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await session.disconnect()

    assert exc_info.value.error_code == 119


async def test_zero_error_code_in_body_is_not_an_error() -> None:
    """errorCode=0 is treated as success and should not raise an exception."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            payload={"workloads": [], "total": 0, "errorCode": 0},
        )
        result = await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert result["total"] == 0


async def test_success_response_without_error_fields_is_not_an_error() -> None:
    """Normal response (no error / errorCode fields) should not raise an exception."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/infra/backup_server",
            payload={"backupServers": [{"id": "srv-1"}], "total": 1},
        )
        result = await session.get("/api/v1/infra/backup_server")
        await disconnect_session(m, session)

    assert len(result["backupServers"]) == 1


# ── SSL / connection error handling ────────────────────────────────────────
#
# aiointercept intercepts at the DNS/connector layer and never terminates real
# TLS, and its exception= only supports a generic connection-close (no typed
# aiohttp/ssl exception injection) — so these reproduce real network failure
# conditions instead of injecting fake exception instances: a genuinely
# untrusted TLS certificate (tls_test_server), a real closed port
# (closed_port), and a real slow response past a deliberately tiny timeout.


async def test_connect_ssl_cert_error_raises_api_error_with_hint() -> None:
    """SSL certificate verification failure should raise APIError with an actionable hint, not the raw aiohttp exception."""
    async with tls_test_server() as host_port:
        session = WebAPISession(host_port, "testuser", "testpass", verify_ssl=True)
        with pytest.raises(APIError) as exc_info:
            await session.connect()

    assert "ssl certificate verification failed" in exc_info.value.message.lower()
    assert "verify_ssl=false" in exc_info.value.message.lower()
    assert session._connected is False


async def test_connect_connection_refused_raises_api_error() -> None:
    """When the host is unreachable, should raise APIError rather than the raw aiohttp exception."""
    session = WebAPISession(f"127.0.0.1:{closed_port()}", "testuser", "testpass", verify_ssl=False)
    with pytest.raises(APIError) as exc_info:
        await session.connect()

    assert "cannot connect" in exc_info.value.message.lower()
    assert session._connected is False


async def test_request_connection_error_during_api_call_raises_api_error() -> None:
    """Connection error during an API call (after connect) should be wrapped as APIError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", exception=True)
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "cannot connect" in exc_info.value.message.lower()


async def test_connect_server_timeout_raises_connection_timeout_error() -> None:
    """A login response slower than the session timeout should raise ConnectionTimeoutError, not APIError."""
    session = WebAPISession(HOST, "testuser", "testpass", verify_ssl=False, timeout=0.05)

    async def slow_login(url: Any, **kwargs: Any) -> CallbackResult:
        await asyncio.sleep(1)
        return CallbackResult(payload=LOGIN_OK)

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, callback=slow_login)
        with pytest.raises(ConnectionTimeoutError) as exc_info:
            await session.connect()

    assert "timed out" in exc_info.value.message.lower()
    assert session._connected is False


async def test_request_server_timeout_raises_connection_timeout_error() -> None:
    """A response slower than the session timeout should raise ConnectionTimeoutError.

    timeout=0.5 (not the tighter 0.05 used elsewhere) because this session's
    timeout also governs the plain, non-slow login performed by
    connect_session() during setup — a generous budget avoids that real
    (if fast) loopback round-trip spuriously exceeding the timeout under load,
    while staying comfortably under slow_response's deliberate 1s delay.
    """
    session = make_session(timeout=0.5)

    async def slow_response(url: Any, **kwargs: Any) -> CallbackResult:
        await asyncio.sleep(1)
        return CallbackResult(payload={})

    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", callback=slow_response)
        with pytest.raises(ConnectionTimeoutError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "timed out" in exc_info.value.message.lower()


async def test_request_server_disconnected_raises_api_error_cannot_connect() -> None:
    """A dropped connection mid-request should raise APIError with 'Cannot connect'.

    exception=True force-closes the connection from the server side, which is a
    real aiohttp.ServerDisconnectedError on the client — not a fabricated one.
    """
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", exception=True)
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "cannot connect" in exc_info.value.message.lower()
    assert not isinstance(exc_info.value, ConnectionTimeoutError)


# ── _request() error handling ──────────────────────────────────────────────────


async def test_request_4xx_range_raises_api_error() -> None:
    """_request() raises APIError for 4xx responses not covered by specific handlers (e.g. 408)."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=408, payload={"message": "Request Timeout"})
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert exc_info.value.error_code == 408
    assert "Request Timeout" in exc_info.value.message


async def test_request_400_with_nested_error_message_surfaces_message() -> None:
    """HTTP 400 with {"error": {"message": "..."}} structure should use the nested message, not 'HTTP error 400'."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.post(
            f"{BASE_URL}/api/v1/application/m365/tenant/auto_backup_rule",
            status=400,
            payload={
                "error": {
                    "code": 400,
                    "status": "Bad Request",
                    "message": "should not have multiple 'M365AutoBackupRule' with same plan x backup server x tenant",
                    "details": [],
                }
            },
        )
        with pytest.raises(APIError) as exc_info:
            await session.post("/api/v1/application/m365/tenant/auto_backup_rule", json={})
        await disconnect_session(m, session)

    assert exc_info.value.error_code == 400
    assert "should not have multiple" in exc_info.value.message


async def test_request_4xx_nested_error_message_takes_priority_over_top_level() -> None:
    """When both body["message"] and body["error"]["message"] are present, error.message wins."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            status=400,
            payload={"message": "generic top-level", "error": {"message": "specific nested"}},
        )
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "specific nested" in exc_info.value.message
    assert "generic top-level" not in exc_info.value.message


async def test_request_4xx_non_dict_error_field_falls_back_to_top_level_message() -> None:
    """When body["error"] is not a dict (e.g. a string), fall back to body["message"]."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            status=400,
            payload={"error": "plain error string", "message": "top-level message"},
        )
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "top-level message" in exc_info.value.message


async def test_request_ssl_cert_error_raises_api_error() -> None:
    """_request() wraps ClientConnectorCertificateError in APIError with SSL hint.

    A WebAPISession is bound to one host and one verify_ssl setting for its
    whole lifetime, so there is no way for a later request on an
    already-connected session to newly fail cert validation that an earlier
    connect() on the same host already passed — connecting with
    verify_ssl=True against a genuinely untrusted cert fails at connect()
    itself (see test_connect_ssl_cert_error_raises_api_error_with_hint).
    Reproduce the "_request() has its own cert-error handling" scenario
    instead by connecting with verify_ssl=False (succeeds despite the bad
    cert) and then flipping to True for the one subsequent request that
    should fail — session._verify_ssl is used here only to set up the
    scenario, never asserted on.
    """
    async with tls_test_server() as host_port:
        session = WebAPISession(host_port, "testuser", "testpass", verify_ssl=False)
        await session.connect()

        session._verify_ssl = True
        with pytest.raises(APIError, match="SSL certificate"):
            await session.get("/api/v1/workload/device_workload")
        await session.disconnect()


# ── _get_detail_error_code ────────────────────────────────────────────────────


async def test_get_detail_error_code_error_is_not_dict() -> None:
    """A 500 with {"error": "string"} (not a dict) falls through to generic APIError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": "plain string error"})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_get_detail_error_code_missing_details_key() -> None:
    """A 500 with {"error": {}} (no "details" key) falls through to generic APIError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": {"code": 9999}})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_get_detail_error_code_empty_details_list() -> None:
    """A 500 with {"error": {"details": []}} (empty list) falls through to generic APIError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": {"details": []}})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_get_detail_error_code_first_detail_not_dict() -> None:
    """A 500 with {"error": {"details": ["not-a-dict"]}} falls through to generic APIError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": {"details": ["not-a-dict"]}})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_request_4xx_non_dict_body_falls_back_to_default_message() -> None:
    """A 4xx response whose JSON body is not a dict (e.g. a list) should fall back to the default message."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=400, payload=["unexpected", "list", "body"])
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "HTTP error 400" in exc_info.value.message


async def test_success_response_invalid_utf8_body_raises_api_error() -> None:
    """A 2xx response whose body cannot be decoded as UTF-8 should raise APIError, not propagate UnicodeDecodeError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            status=200,
            body=b"\xff\xfe\xfd",
            content_type="application/json",
        )
        with pytest.raises(APIError, match="non-JSON response"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_success_response_non_dict_json_body_is_returned_as_is() -> None:
    """A 2xx response whose JSON body is a list (not a dict) should be returned unchanged, skipping error-code checks."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=200, payload=["a", "b"])
        result = await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert result == ["a", "b"]


async def test_error_code_in_body_format3_nested_raises_api_error() -> None:
    """{"error": {"errorCode": N, "message": "..."}} nested format should raise the corresponding exception."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.post(
            f"{BASE_URL}/api/v1/workload/m365_workload/batch",
            payload={"success": False, "error": {"errorCode": 7003, "message": "workload can not change to backup plan"}},
        )
        with pytest.raises(APIError) as exc_info:
            await session.post("/api/v1/workload/m365_workload/batch", json={})
        await session.disconnect()

    assert exc_info.value.error_code == 7003
    assert "workload can not change" in exc_info.value.message
