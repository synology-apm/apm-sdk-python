"""Unit tests for synology_apm.sdk._http.WebAPISession: downloads and debug logging.

Covers download_file(), debug-mode request/response logging, response-body
preservation on errors, and non-JSON body handling. Uses aiointercept to mock
all HTTP requests; no real APM connection required.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aiointercept import aiointercept

from synology_apm.sdk.exceptions import (
    APIError,
    AuthenticationError,
    ConnectionTimeoutError,
    PermissionDeniedError,
)
from tests.unit.sdk.conftest import BASE_URL, LOGIN_OK, TESTUSER_LOGIN_URL
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


# ── response_body preservation and __str__ output ─────────────────────────


async def test_403_response_body_preserved_in_exception() -> None:
    """The response_body on a 403 exception should preserve the full JSON body returned by the API."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
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
    async with aiointercept(mock_external_urls=True) as m:
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

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
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


async def test_download_file_reassembles_content_across_buffer_flushes(tmp_path: Path) -> None:
    """Content written across multiple in-loop buffer flushes reassembles exactly, in order."""
    session = make_session()
    dest = tmp_path / "out.pst"
    # iter_chunked(65536) only splits a response into multiple chunks once the body
    # exceeds its 65536-byte read size (a body under that arrives as a single chunk,
    # which would only exercise one flush regardless of the buffer threshold below) --
    # three distinguishable segments comfortably over that size force multiple reads.
    content = (b"A" * 70_000) + (b"B" * 70_000) + (b"C" * 123)

    # A 1-byte threshold forces the >= _DOWNLOAD_WRITE_BUFFER_SIZE branch to flush-and-clear
    # on every network chunk received, instead of only the single trailing flush after the loop.
    with patch("synology_apm.sdk._http._DOWNLOAD_WRITE_BUFFER_SIZE", 1):
        async with aiointercept(mock_external_urls=True) as m:
            m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
            await session.connect()
            m.get(f"{BASE_URL}/portal/download/token_batched", body=content)
            await session.download_file(f"{BASE_URL}/portal/download/token_batched", str(dest))
            await session.disconnect()

    assert dest.read_bytes() == content


async def test_download_file_total_is_none_without_content_length(tmp_path: Path) -> None:
    """download_file() passes total=None to on_progress when server omits Content-Length."""
    session = make_session()
    dest = tmp_path / "out.pst"
    totals: list[int | None] = []

    async def no_content_length_body() -> AsyncIterator[bytes]:
        # An async-generator body has no known length, so aiohttp.web streams
        # it with chunked transfer-encoding and never sets Content-Length —
        # unlike a plain bytes body, which aiohttp.web always measures and
        # sets Content-Length for automatically.
        yield b"data"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token2", body=no_content_length_body())
        await session.download_file(
            f"{BASE_URL}/portal/download/token2",
            str(dest),
            on_progress=lambda downloaded, total: totals.append(total),
        )
        await session.disconnect()

    assert all(t is None for t in totals)


async def test_download_file_removes_partial_file_on_error(tmp_path: Path) -> None:
    """download_file() deletes the temporary .part file if an exception occurs during writing."""
    session = make_session()
    dest = tmp_path / "out.pst"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token3", body=b"partial data")

        # Simulate an error during iter_chunked by making write() raise
        with (
            patch("synology_apm.sdk._http.os.unlink") as mock_unlink,
            patch("builtins.open", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            await session.download_file(f"{BASE_URL}/portal/download/token3", str(dest))

        mock_unlink.assert_called_once_with(str(dest) + ".part")
        await session.disconnect()


async def test_download_file_failure_preserves_existing_dest_file(tmp_path: Path) -> None:
    """A failed download must leave a pre-existing file at dest_path untouched."""
    session = make_session()
    dest = tmp_path / "out.pst"
    dest.write_bytes(b"previous export")

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
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

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
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

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
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

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/empty", body=b"")
        with pytest.raises(APIError, match="empty"):
            await session.download_file(f"{BASE_URL}/portal/download/empty", str(dest))
        await session.disconnect()


async def test_download_file_unlink_failure_during_write_error_is_swallowed(tmp_path: Path) -> None:
    """download_file() swallows OSError from unlink when cleaning up a partial file."""
    session = make_session()
    dest = tmp_path / "out.pst"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token4", body=b"partial")

        with (
            patch("synology_apm.sdk._http.os.unlink", side_effect=OSError("unlink failed")),
            patch("builtins.open", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            await session.download_file(f"{BASE_URL}/portal/download/token4", str(dest))
        await session.disconnect()


async def test_download_file_connection_error_raises_api_error(tmp_path: Path) -> None:
    """download_file() raises APIError when the server connection is dropped.

    exception=True force-closes the connection from the server side, which is
    a real aiohttp.ServerDisconnectedError on the client — not a fabricated one.
    """
    session = make_session()
    dest = tmp_path / "out.pst"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(f"{BASE_URL}/portal/download/token5", exception=True)
        with pytest.raises(APIError, match="Cannot connect"):
            await session.download_file(f"{BASE_URL}/portal/download/token5", str(dest))
        await session.disconnect()


async def test_download_file_timeout_raises_connection_timeout_error(tmp_path: Path) -> None:
    """download_file() raises ConnectionTimeoutError when the download times out.

    download_file() passes its own hardcoded aiohttp.ClientTimeout(sock_read=300)
    to the request, independent of the session's own configurable timeout= — a
    real reproduction would mean waiting a genuine 300 seconds, so this patches
    the underlying aiohttp session's get() directly (bypassing aiointercept for
    this one case only) rather than trying to trigger a real timeout.
    """
    import aiohttp

    session = make_session()
    dest = tmp_path / "out.pst"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        assert session._session is not None
        with (
            patch.object(session._session, "get", side_effect=aiohttp.ServerTimeoutError()),
            pytest.raises(ConnectionTimeoutError, match="timed out"),
        ):
            await session.download_file(f"{BASE_URL}/portal/download/token6", str(dest))
        await session.disconnect()


# ── non-JSON body handling ────────────────────────────────────────────────────


async def test_success_status_with_non_json_body_raises_api_error() -> None:
    """A success status whose body is not valid JSON (e.g. HTML) raises APIError instead of returning {}."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload",
              body=b"<html>Service Unavailable</html>", status=200)
        with pytest.raises(APIError, match="non-JSON"):
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)


async def test_success_status_with_empty_body_returns_empty_dict() -> None:
    """A success status with an empty body still resolves to {} (some write endpoints return no body)."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", body=b"", status=200)
        result = await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)

    assert result == {}


async def test_error_status_with_non_json_body_still_maps_status_exception() -> None:
    """A 4xx/5xx with a non-JSON body raises the status-mapped exception, not the non-JSON error."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
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
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "SYNO.API.Auth" in captured.err


async def test_debug_mode_logs_api_request_and_response(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, _request() calls _debug_print_request and _debug_print_response."""
    session = make_session(debug=True)
    async with aiointercept(mock_external_urls=True) as m:
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
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/portal/download/dbg", body=b"data")
        await session.download_file(f"{BASE_URL}/portal/download/dbg", str(dest))
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "/portal/download/dbg" in captured.err


async def test_debug_mode_post_request_logs_body(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, _request() logs the POST body to stderr."""
    session = make_session(debug=True)
    async with aiointercept(mock_external_urls=True) as m:
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
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.post(f"{BASE_URL}/api/v1/log/aem-log", payload={})
        await session.post("/api/v1/log/aem-log", headers={"x-syno-tunnel-route": "node1"})
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "x-syno-tunnel-route" in captured.err


async def test_debug_request_and_response_lines_carry_matching_id(capsys: pytest.CaptureFixture[str]) -> None:
    """Debug request and response lines share a [#N] id, and the response repeats method/URL/duration."""
    session = make_session(debug=True)
    async with aiointercept(mock_external_urls=True) as m:
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
    """Concurrent requests get distinct ids and each response line pairs with its own request.

    Gates the slow response on the fast request's client-side completion, not just its
    server-side callback firing. aiointercept runs its mock server on a separate thread and
    dispatches callbacks back onto the caller's loop (so loop-bound primitives like this
    Event keep working), but the actual response bytes are still written back over a real
    socket from that other thread. A rendezvous set from inside fast_response only orders
    "the callback ran" before "slow_response resumes" — the fast response's own transmission
    and the client printing its debug line still race against slow_response returning, and
    under heavy scheduling contention (e.g. many parallel test workers) that race can flip.
    Anchoring the Event to session.get() itself returning removes that race: it can only
    fire after the fast response's debug line has already been printed.
    """
    from aiointercept import CallbackResult

    session = make_session(debug=True)
    fast_client_done = asyncio.Event()

    async def slow_response(url: Any, **kwargs: Any) -> CallbackResult:
        await fast_client_done.wait()
        return CallbackResult(payload={"which": "slow"})

    async def fast_response(url: Any, **kwargs: Any) -> CallbackResult:
        return CallbackResult(payload={"which": "fast"})

    async def fetch_fast() -> None:
        await session.get("/api/v1/fast")
        fast_client_done.set()

    slow_url = f"{BASE_URL}/api/v1/slow"
    fast_url = f"{BASE_URL}/api/v1/fast"
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(slow_url, callback=slow_response)
        m.get(fast_url, callback=fast_response)
        await asyncio.gather(session.get("/api/v1/slow"), fetch_fast())
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
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", payload=large_payload)
        await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)
    captured = capsys.readouterr()
    assert "truncated" in captured.err
