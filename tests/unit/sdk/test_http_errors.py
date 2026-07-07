"""Unit tests for synology_apm.sdk._http.WebAPISession: error handling.

Covers HTTP status code → exception mapping, JSON body error code mapping, and
SSL/connection-level error handling. Uses aioresponses to mock all HTTP requests;
no real APM connection required.
"""
from __future__ import annotations

import ssl
from typing import Any
from unittest.mock import Mock

import aiohttp
import pytest
from aioresponses import aioresponses

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

# ── Test fixtures & helpers ────────────────────────────────────────────────

BASE_URL = "https://fake-apm.test"
HOST = "fake-apm.test"
WEBAPI_URL = f"{BASE_URL}/webapi/entry.cgi"

# Standard success responses
LOGIN_OK: dict[str, Any] = {"success": True, "data": {"sid": "test-sid-abc", "synotoken": "test-token-xyz"}}
LOGOUT_OK: dict[str, Any] = {}


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


# ── HTTP status code → exception mapping ──────────────────────────────────


async def test_403_raises_permission_denied_error() -> None:
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan", status=403)
        with pytest.raises(PermissionDeniedError):
            await session.get("/api/v1/plan/backup_plan")
        await session.disconnect()


async def test_404_raises_resource_not_found_error() -> None:
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload/bad-id", status=404)
        with pytest.raises(ResourceNotFoundError):
            await session.get("/api/v1/workload/device_workload/bad-id")
        await session.disconnect()


async def test_501_raises_not_supported_error() -> None:
    """HTTP 501 should raise NotSupportedError (as of APM 1.2, returns 501 for M365 workloads)."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/m365_workload", status=501)
        with pytest.raises(NotSupportedError):
            await session.get("/api/v1/workload/m365_workload")
        await session.disconnect()


async def test_500_raises_api_error() -> None:
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=500)
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await session.disconnect()

    assert exc_info.value.error_code == 500


async def test_error_detail_code_2003_raises_backup_server_disconnected() -> None:
    """error.details[0].errorCode=2003 (backup server disconnected) should raise BackupServerDisconnectedError."""
    session = make_session()
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/infra/backup_server",
            payload={"backupServers": [{"id": "srv-1"}], "total": 1},
        )
        result = await session.get("/api/v1/infra/backup_server")
        await disconnect_session(m, session)

    assert len(result["backupServers"]) == 1


# ── SSL / connection error handling ────────────────────────────────────────


async def test_connect_ssl_cert_error_raises_api_error_with_hint() -> None:
    """SSL certificate verification failure should raise APIError with an actionable hint, not the raw aiohttp exception."""
    session = WebAPISession(HOST, "testuser", "testpass", verify_ssl=True)
    conn_key = Mock()
    ssl_exc = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
    cert_err = aiohttp.ClientConnectorCertificateError(conn_key, ssl_exc)

    with aioresponses() as m:
        m.get(
            "https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth"
            "&client=browser&enable_syno_token=yes&method=login&passwd=testpass"
            "&session=webui&version=6",
            exception=cert_err,
        )
        with pytest.raises(APIError) as exc_info:
            await session.connect()

    assert "ssl certificate verification failed" in exc_info.value.message.lower()
    assert "verify_ssl=false" in exc_info.value.message.lower()
    assert session._connected is False


async def test_connect_connection_refused_raises_api_error() -> None:
    """When the host is unreachable, should raise APIError rather than the raw aiohttp exception."""
    session = WebAPISession(HOST, "testuser", "testpass", verify_ssl=False)
    conn_key = Mock()
    conn_err = aiohttp.ClientConnectorError(conn_key, OSError("Connection refused"))

    with aioresponses() as m:
        m.get(
            "https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth"
            "&client=browser&enable_syno_token=yes&method=login&passwd=testpass"
            "&session=webui&version=6",
            exception=conn_err,
        )
        with pytest.raises(APIError) as exc_info:
            await session.connect()

    assert "cannot connect" in exc_info.value.message.lower()
    assert session._connected is False


async def test_request_connection_error_during_api_call_raises_api_error() -> None:
    """Connection error during an API call (after connect) should be wrapped as APIError."""
    session = make_session()
    conn_key = Mock()
    conn_err = aiohttp.ClientConnectorError(conn_key, OSError("Connection reset"))

    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", exception=conn_err)
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "cannot connect" in exc_info.value.message.lower()


async def test_connect_server_timeout_raises_connection_timeout_error() -> None:
    """ServerTimeoutError during login should raise ConnectionTimeoutError, not APIError."""
    session = WebAPISession(HOST, "testuser", "testpass", verify_ssl=False)
    with aioresponses() as m:
        m.get(
            "https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth"
            "&client=browser&enable_syno_token=yes&method=login&passwd=testpass"
            "&session=webui&version=6",
            exception=aiohttp.ServerTimeoutError(),
        )
        with pytest.raises(ConnectionTimeoutError) as exc_info:
            await session.connect()

    assert "timed out" in exc_info.value.message.lower()
    assert session._connected is False


async def test_request_server_timeout_raises_connection_timeout_error() -> None:
    """ServerTimeoutError during an API call should raise ConnectionTimeoutError."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", exception=aiohttp.ServerTimeoutError())
        with pytest.raises(ConnectionTimeoutError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "timed out" in exc_info.value.message.lower()


async def test_request_server_disconnected_raises_api_error_cannot_connect() -> None:
    """ServerDisconnectedError (stale pooled connection) should raise APIError with 'Cannot connect'."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/workload/device_workload",
            exception=aiohttp.ServerDisconnectedError(),
        )
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert "cannot connect" in exc_info.value.message.lower()
    assert not isinstance(exc_info.value, ConnectionTimeoutError)


# ── _request() error handling ──────────────────────────────────────────────────


async def test_request_4xx_range_raises_api_error() -> None:
    """_request() raises APIError for 4xx responses not covered by specific handlers (e.g. 408)."""
    session = make_session()
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    with aioresponses() as m:
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
    """_request() wraps ClientConnectorCertificateError in APIError with SSL hint."""
    import ssl as _ssl

    session = make_session()
    cert_err = aiohttp.ClientConnectorCertificateError(
        Mock(), _ssl.SSLCertVerificationError()
    )
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", exception=cert_err)
        with pytest.raises(APIError, match="SSL certificate"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


# ── _get_detail_error_code ────────────────────────────────────────────────────


async def test_get_detail_error_code_error_is_not_dict() -> None:
    """A 500 with {"error": "string"} (not a dict) falls through to generic APIError."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": "plain string error"})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_get_detail_error_code_missing_details_key() -> None:
    """A 500 with {"error": {}} (no "details" key) falls through to generic APIError."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": {"code": 9999}})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_get_detail_error_code_empty_details_list() -> None:
    """A 500 with {"error": {"details": []}} (empty list) falls through to generic APIError."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": {"details": []}})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_get_detail_error_code_first_detail_not_dict() -> None:
    """A 500 with {"error": {"details": ["not-a-dict"]}} falls through to generic APIError."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              status=500, payload={"error": {"details": ["not-a-dict"]}})
        with pytest.raises(APIError, match="HTTP 500"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_error_code_in_body_format3_nested_raises_api_error() -> None:
    """{"error": {"errorCode": N, "message": "..."}} nested format should raise the corresponding exception."""
    session = make_session()
    with aioresponses() as m:
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
