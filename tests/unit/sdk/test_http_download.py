"""Unit tests for synology_apm.sdk._http.WebAPISession: downloads and debug logging.

Covers download_file(), debug-mode request/response logging, response-body
preservation on errors, and non-JSON body handling. Uses aioresponses to mock
all HTTP requests; no real APM connection required.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.exceptions import (
    APIError,
    AuthenticationError,
    ConnectionTimeoutError,
    PermissionDeniedError,
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


# ── response_body preservation and __str__ output ─────────────────────────


async def test_403_response_body_preserved_in_exception() -> None:
    """The response_body on a 403 exception should preserve the full JSON body returned by the API."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/plan/backup_plan",
            status=403,
            payload={"message": "forbidden", "detail": "insufficient rights"},
        )
        with pytest.raises(PermissionDeniedError) as exc_info:
            await session.get("/api/v1/plan/backup_plan")
        await session.disconnect()

    body = exc_info.value.response_body
    assert body == {"message": "forbidden", "detail": "insufficient rights"}


async def test_api_error_in_body_response_body_preserved() -> None:
    """API error from JSON body should store the full body in response_body (including extra fields)."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(
            f"{BASE_URL}/api/v1/plan/backup_plan",
            payload={"errorCode": 7003, "message": "cannot change plan", "extra": {"id": "x"}},
        )
        with pytest.raises(APIError) as exc_info:
            await session.get("/api/v1/plan/backup_plan")
        await session.disconnect()

    body = exc_info.value.response_body
    assert body["errorCode"] == 7003
    assert body["extra"] == {"id": "x"}


def test_apm_error_str_includes_response_body() -> None:
    """`__str__` should append formatted JSON when response_body is present, to aid debugging."""
    from synology_apm.sdk.exceptions import APIError

    err = APIError("Something went wrong", error_code=7003, response_body={"errorCode": 7003, "detail": "bad"})
    s = str(err)
    assert "Something went wrong" in s
    assert "7003" in s
    assert "detail" in s


def test_apm_error_str_without_response_body_returns_message_only() -> None:
    """`__str__` should return only the message when response_body is absent."""
    from synology_apm.sdk.exceptions import APIError

    err = APIError("Simple error")
    assert str(err) == "Simple error"


# ── download_file ─────────────────────────────────────────────────────────────


async def test_download_file_writes_content_and_calls_progress(tmp_path: Path) -> None:
    """download_file() writes binary content and calls on_progress after each chunk."""
    session = make_session()
    dest = tmp_path / "out.pst"
    calls: list[tuple[int, int | None]] = []

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token", body=b"Hello PST", headers={"Content-Length": "9"})
        await session.download_file(
            f"{BASE_URL}/portal/download/token",
            str(dest),
            on_progress=lambda downloaded, total: calls.append((downloaded, total)),
        )
        await session.disconnect()

    assert dest.read_bytes() == b"Hello PST"
    assert len(calls) >= 1
    assert calls[-1][0] == 9   # final downloaded count
    assert calls[-1][1] == 9   # total from Content-Length


async def test_download_file_total_is_none_without_content_length(tmp_path: Path) -> None:
    """download_file() passes total=None to on_progress when server omits Content-Length."""
    session = make_session()
    dest = tmp_path / "out.pst"
    totals: list[int | None] = []

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token2", body=b"data")
        await session.download_file(
            f"{BASE_URL}/portal/download/token2",
            str(dest),
            on_progress=lambda downloaded, total: totals.append(total),
        )
        await session.disconnect()

    assert all(t is None for t in totals)


async def test_download_file_removes_partial_file_on_error(tmp_path: Path) -> None:
    """download_file() deletes the temporary .part file if an exception occurs during writing."""
    from unittest.mock import patch

    session = make_session()
    dest = tmp_path / "out.pst"

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token3", body=b"partial data")

        with patch("synology_apm.sdk._http.os.unlink") as mock_unlink:
            # Simulate an error during iter_chunked by making write() raise
            with patch("builtins.open", side_effect=OSError("disk full")):
                with pytest.raises(OSError, match="disk full"):
                    await session.download_file(f"{BASE_URL}/portal/download/token3", str(dest))

        mock_unlink.assert_called_once_with(str(dest) + ".part")
        await session.disconnect()


async def test_download_file_failure_preserves_existing_dest_file(tmp_path: Path) -> None:
    """A failed download must leave a pre-existing file at dest_path untouched."""
    session = make_session()
    dest = tmp_path / "out.pst"
    dest.write_bytes(b"previous export")

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/empty2", body=b"")
        with pytest.raises(APIError, match="empty"):
            await session.download_file(f"{BASE_URL}/portal/download/empty2", str(dest))
        await session.disconnect()

    assert dest.read_bytes() == b"previous export"
    assert not (tmp_path / "out.pst.part").exists()


async def test_download_file_success_replaces_dest_and_leaves_no_part_file(tmp_path: Path) -> None:
    """A successful download replaces an existing dest file and leaves no .part file behind."""
    session = make_session()
    dest = tmp_path / "out.pst"
    dest.write_bytes(b"previous export")

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token7", body=b"new export")
        await session.download_file(f"{BASE_URL}/portal/download/token7", str(dest))
        await session.disconnect()

    assert dest.read_bytes() == b"new export"
    assert not (tmp_path / "out.pst.part").exists()


async def test_download_file_raises_api_error_on_4xx(tmp_path: Path) -> None:
    """download_file() raises APIError when the server returns a 4xx status."""
    session = make_session()
    dest = tmp_path / "out.pst"

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/expired", status=410)
        with pytest.raises(APIError, match="410"):
            await session.download_file(f"{BASE_URL}/portal/download/expired", str(dest))
        await session.disconnect()


async def test_download_file_not_connected_raises_authentication_error(tmp_path: Path) -> None:
    """download_file() raises AuthenticationError when called without connecting first."""
    session = make_session()
    dest = tmp_path / "out.pst"
    with pytest.raises(AuthenticationError, match="not connected"):
        await session.download_file(f"{BASE_URL}/portal/download/token", str(dest))


async def test_download_file_empty_response_raises_api_error(tmp_path: Path) -> None:
    """download_file() raises APIError when the server returns a 200 but sends zero bytes."""
    session = make_session()
    dest = tmp_path / "out.pst"

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/empty", body=b"")
        with pytest.raises(APIError, match="empty"):
            await session.download_file(f"{BASE_URL}/portal/download/empty", str(dest))
        await session.disconnect()


async def test_download_file_unlink_failure_during_write_error_is_swallowed(tmp_path: Path) -> None:
    """download_file() swallows OSError from unlink when cleaning up a partial file."""
    from unittest.mock import patch

    session = make_session()
    dest = tmp_path / "out.pst"

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token4", body=b"partial")

        with patch("synology_apm.sdk._http.os.unlink", side_effect=OSError("unlink failed")):
            with patch("builtins.open", side_effect=OSError("disk full")):
                with pytest.raises(OSError, match="disk full"):
                    await session.download_file(f"{BASE_URL}/portal/download/token4", str(dest))
        await session.disconnect()


async def test_download_file_connection_error_raises_api_error(tmp_path: Path) -> None:
    """download_file() raises APIError when the server connection is dropped."""
    session = make_session()
    dest = tmp_path / "out.pst"

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token5", exception=aiohttp.ServerDisconnectedError())
        with pytest.raises(APIError, match="Cannot connect"):
            await session.download_file(f"{BASE_URL}/portal/download/token5", str(dest))
        await session.disconnect()


async def test_download_file_timeout_raises_connection_timeout_error(tmp_path: Path) -> None:
    """download_file() raises ConnectionTimeoutError when the download times out."""
    session = make_session()
    dest = tmp_path / "out.pst"

    with aioresponses() as m:
        m.get("https://fake-apm.test/webapi/entry.cgi?account=testuser&api=SYNO.API.Auth&client=browser&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6", payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token6", exception=aiohttp.ServerTimeoutError())
        with pytest.raises(ConnectionTimeoutError, match="timed out"):
            await session.download_file(f"{BASE_URL}/portal/download/token6", str(dest))
        await session.disconnect()


# ── non-JSON body handling ────────────────────────────────────────────────────


async def test_success_status_with_non_json_body_raises_api_error() -> None:
    """A success status whose body is not valid JSON (e.g. HTML) raises APIError instead of returning {}."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              body=b"<html>Service Unavailable</html>", status=200)
        with pytest.raises(APIError, match="non-JSON"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_success_status_with_empty_body_returns_empty_dict() -> None:
    """A success status with an empty body still resolves to {} (some write endpoints return no body)."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", body=b"", status=200)
        result = await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert result == {}


async def test_error_status_with_non_json_body_still_maps_status_exception() -> None:
    """A 4xx/5xx with a non-JSON body raises the status-mapped exception, not the non-JSON error."""
    session = make_session()
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              body=b"<html>Forbidden</html>", status=403)
        with pytest.raises(PermissionDeniedError):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


# ── debug mode ────────────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DEBUG_REQ_RE = re.compile(r"^→ \[#(\d+)\] (GET|POST|PUT|DELETE|PATCH) (\S+)$", re.MULTILINE)
_DEBUG_RESP_RE = re.compile(
    r"^  ← \[#(\d+)\] (\d+) (GET|POST|PUT|DELETE|PATCH) (\S+) \(\d+\.\d\ds\)$", re.MULTILINE
)


async def test_debug_mode_logs_login_request_and_response(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, _do_login() calls _debug_print_request and _debug_print_response."""
    session = make_session(debug=True)
    with aioresponses() as m:
        await connect_session(m, session)
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "SYNO.API.Auth" in captured.err


async def test_debug_mode_logs_api_request_and_response(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, _request() calls _debug_print_request and _debug_print_response."""
    session = make_session(debug=True)
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", payload={"workloads": [], "total": 0})
        await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "/api/v1/workload/device_workload" in captured.err


async def test_download_file_debug_mode_logs_request_and_response(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, download_file() logs the URL and response status to stderr."""
    session = make_session(debug=True)
    dest = tmp_path / "out.pst"
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/portal/download/dbg", body=b"data")
        await session.download_file(f"{BASE_URL}/portal/download/dbg", str(dest))
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "/portal/download/dbg" in captured.err


async def test_debug_mode_post_request_logs_body(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, _request() logs the POST body to stderr."""
    session = make_session(debug=True)
    with aioresponses() as m:
        await connect_session(m, session)
        m.post(f"{BASE_URL}/api/v1/workload/device_workload/backup", payload={})
        await session.post(
            "/api/v1/workload/device_workload/backup",
            json={"workloadRefs": [{"uid": "abc"}]},
        )
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "workloadRefs" in captured.err


async def test_debug_mode_post_request_with_headers_logs_headers(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, _request() with custom headers logs them to stderr."""
    session = make_session(debug=True)
    with aioresponses() as m:
        await connect_session(m, session)
        m.post(f"{BASE_URL}/api/v1/log/aem-log", payload={})
        await session.post("/api/v1/log/aem-log", headers={"x-syno-tunnel-route": "node1"})
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "x-syno-tunnel-route" in captured.err


async def test_debug_request_and_response_lines_carry_matching_id(capsys: pytest.CaptureFixture[str]) -> None:
    """Debug request and response lines share a [#N] id, and the response repeats method/URL/duration."""
    session = make_session(debug=True)
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", payload={"workloads": [], "total": 0})
        await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)
    err = _ANSI_RE.sub("", capsys.readouterr().err)

    url = f"{BASE_URL}/api/v1/workload/device_workload"
    req = next(m_ for m_ in _DEBUG_REQ_RE.finditer(err) if m_.group(3) == url)
    resp = next(m_ for m_ in _DEBUG_RESP_RE.finditer(err) if m_.group(4) == url)
    assert resp.group(1) == req.group(1)
    assert resp.group(2) == "200"
    assert resp.group(3) == "GET"


async def test_debug_concurrent_requests_pair_by_id(capsys: pytest.CaptureFixture[str]) -> None:
    """Concurrent requests get distinct ids and each response line pairs with its own request."""
    session = make_session(debug=True)

    async def delay_response(url: Any, **kwargs: Any) -> None:
        for _ in range(5):
            await asyncio.sleep(0)

    slow_url = f"{BASE_URL}/api/v1/slow"
    fast_url = f"{BASE_URL}/api/v1/fast"
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(slow_url, payload={"which": "slow"}, callback=delay_response)
        m.get(fast_url, payload={"which": "fast"})
        await asyncio.gather(session.get("/api/v1/slow"), session.get("/api/v1/fast"))
        await disconnect_session(m, session)
    err = _ANSI_RE.sub("", capsys.readouterr().err)

    req_ids = {m_.group(3): m_.group(1) for m_ in _DEBUG_REQ_RE.finditer(err)}
    resp_ids = {m_.group(4): m_.group(1) for m_ in _DEBUG_RESP_RE.finditer(err)}
    assert req_ids[slow_url] != req_ids[fast_url]
    assert resp_ids[slow_url] == req_ids[slow_url]
    assert resp_ids[fast_url] == req_ids[fast_url]
    # The slow endpoint was requested first but answered last — the output really interleaved.
    assert err.index(f"← [#{req_ids[fast_url]}]") < err.index(f"← [#{req_ids[slow_url]}]")


async def test_debug_mode_large_response_body_is_truncated(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, _request() truncates response bodies exceeding _DEBUG_MAX_BODY."""
    session = make_session(debug=True)
    large_payload = {"data": "x" * 5000}
    with aioresponses() as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", payload=large_payload)
        await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "truncated" in captured.err
