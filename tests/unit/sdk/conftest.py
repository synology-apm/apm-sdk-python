"""Shared constants and helpers for SDK unit tests.

Import these in collection test files to avoid repeating boilerplate:

    from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session

Most tests only need connected_session() — an async context manager yielding a
logged-in WebAPISession plus its aiointercept mock, ready for endpoint mocks.
"""
from __future__ import annotations

import ssl
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import patch

import pytest
import trustme
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port
from aiointercept import aiointercept
from yarl import URL

from synology_apm.sdk._http import WebAPISession

BASE_URL = "https://fake-apm.test"
HOST = "fake-apm.test"
WEBAPI_URL = f"{BASE_URL}/webapi/entry.cgi"
LOGIN_URL = (
    "https://fake-apm.test/webapi/entry.cgi"
    "?account=user&api=SYNO.API.Auth&client=browser"
    "&enable_syno_token=yes&method=login&passwd=pass&session=webui&version=6"
)
LOGIN_OK: dict[str, Any] = {"success": True, "data": {"sid": "abc", "synotoken": "tok"}}
LOGOUT_OK: dict[str, Any] = {}


def make_session(**kwargs: Any) -> WebAPISession:
    """Create a WebAPISession pointed at the fake APM host (no SSL verification)."""
    return WebAPISession(HOST, "user", "pass", verify_ssl=False, **kwargs)


def assert_resource_error(
    exc_info: pytest.ExceptionInfo[Any],
    *,
    resource_type: str,
    resource_id: str,
) -> None:
    assert exc_info.value.resource_type == resource_type
    assert exc_info.value.resource_id == resource_id


def request_json(m: aiointercept, key: tuple[str, URL], index: int = 0) -> dict[str, Any]:
    """Return the captured JSON body of a request recorded in m.requests.

    Equivalent to m.requests[key][index].kwargs["json"], type-narrowed from
    Any | None to dict[str, Any] — every call site here expects a JSON body
    to actually be present, so the None case (a request with no body) is
    asserted away rather than propagated.
    """
    body = m.requests[key][index].kwargs["json"]
    assert isinstance(body, dict)
    return body


@asynccontextmanager
async def connected_session(**kwargs: Any) -> AsyncIterator[tuple[WebAPISession, aiointercept]]:
    """Yield a connected WebAPISession and its aiointercept mock.

    Wraps the prologue every collection test repeats: create the session, mock
    the login endpoint, connect, then hand control to the test to register its
    own endpoint mocks. kwargs are forwarded to make_session().

    mock_external_urls=True is required: WebAPISession builds its own
    aiohttp.ClientSession internally with no injection point, so interception
    has to happen via DNS/connector patching rather than by pointing the
    client at the mock server's own URL.
    """
    session = make_session(**kwargs)
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        try:
            yield session, m
        finally:
            await session.disconnect()


TESTUSER_LOGIN_URL = (
    "https://fake-apm.test/webapi/entry.cgi"
    "?account=testuser&api=SYNO.API.Auth&client=browser"
    "&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6"
)


def make_testuser_session(**kwargs: Any) -> WebAPISession:
    """Create a WebAPISession using the "testuser"/"testpass" credential set.

    Shared by test_http.py, test_http_errors.py, and test_http_download.py,
    which each need finer control over the aiointercept context (e.g. mixing
    raw exception-injection mocks with the normal login/logout flow) than
    connected_session()'s all-in-one context manager provides — hence a
    distinct credential set from make_session()'s "user"/"pass", kept separate
    to avoid the two conventions colliding.
    """
    return WebAPISession(HOST, "testuser", "testpass", verify_ssl=False, **kwargs)


async def connect_testuser_session(m: aiointercept, session: WebAPISession) -> None:
    """Register a single GET mock inside an aiointercept context to satisfy connect()."""
    m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
    await session.connect()


async def disconnect_testuser_session(m: aiointercept, session: WebAPISession) -> None:
    """Register a logout GET mock inside an aiointercept context to satisfy disconnect()."""
    m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
    await session.disconnect()


def closed_port() -> int:
    """Return a local TCP port that is bound and then immediately released.

    Nothing else grabs it fast enough in practice, so connecting to it raises a
    genuine OS-level connection-refused error — used to reproduce real
    ClientConnectorError-shaped failures without any mocking library.
    """
    return unused_port()


@asynccontextmanager
async def tls_test_server() -> AsyncIterator[str]:
    """Start a real HTTPS server with an untrusted self-signed cert; yield its "host:port".

    Used to reproduce genuine SSL certificate verification failures
    (aiohttp.ClientConnectorCertificateError) — aiointercept intercepts at the
    DNS/connector layer and never terminates real TLS, so certificate errors
    need an actual TLS-terminating server instead.
    """
    ca = trustme.CA()
    cert = ca.issue_cert("localhost", "127.0.0.1")
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert.configure_cert(server_ctx)

    async def _handler(request: web.Request) -> web.Response:
        return web.json_response(LOGIN_OK)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _handler)
    server = TestServer(app)
    await server.start_server(ssl=server_ctx)
    try:
        yield f"{server.host}:{server.port}"
    finally:
        await server.close()


def make_backup_activity_raw(
    *,
    spec: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    **top: Any,
) -> dict[str, Any]:
    """Build a complete raw backup-activity entry for list()/get() payloads.

    Returns the ``{"activity": {...}}`` wrapper shape used by the backup activity
    list endpoint. ``spec``/``status`` entries merge shallowly over the base
    activity's spec/status; other keyword args override top-level activity fields
    (``uid``, ``namespace``).
    """
    activity: dict[str, Any] = {
        "uid": "act-uid-001",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "workloadType": "MACHINE_PC",
            "workloadName": "CORP-PC-001",
            "workload": {"uid": "fbf93425-d9e7-1c70-f4b2-231d7fc7b116"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_1",
            "backupStatus": "SUCCESS",
            "startTime": "1776734685",
            "endTime": "1776735285",
            "durationTime": "600",
            "transferredDataSize": "1073741824",
            "progress": 100,
        },
    }
    activity["spec"].update(spec or {})
    activity["status"].update(status or {})
    activity.update(top)
    return {"activity": activity}


def make_restore_activity_raw(
    *,
    spec: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    **top: Any,
) -> dict[str, Any]:
    """Build a complete raw restore-activity entry for list() payloads.

    The base spec carries only the required fields — optional spec fields
    (``versionTimestamp``, ``restoreFromInfo``, ``destinationPath``,
    ``machineInfo``) are absent unless supplied via ``spec``, so tests can
    exercise both the missing-field defaults and specific overrides.
    """
    activity: dict[str, Any] = {
        "uid": "rst-uid-001",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "executionId": "97",
            "workload": {"uid": "wl-uid-001", "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5"},
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-web-01",
            "restoreType": "FULL_RESTORE",
            "destination": "vm-web-01-restored",
            "operator": "admin",
        },
        "status": {
            "startTime": "1777274897",
            "endTime": "1777274903",
            "progress": 100,
            "restoreStatus": "SUCCESS",
            "transferredSize": "1601",
        },
    }
    activity["spec"].update(spec or {})
    activity["status"].update(status or {})
    activity.update(top)
    return {"activity": activity, "permission": {"canBackup": True, "canRestore": True, "canSelfService": False}}


@contextmanager
def patched_session(
    session: WebAPISession,
    *,
    get: Any = None,
    post: Any = None,
    put: Any = None,
    delete: Any = None,
) -> Iterator[None]:
    """Patch the session's HTTP verbs with side-effect callables (path-dispatch fakes).

    Replaces the repeated ``with patch.object(session, "post", ...), patch.object(
    session, "get", ...):`` stacks; pass only the verbs the test needs.
    """
    with ExitStack() as stack:
        for name, side_effect in (("get", get), ("post", post), ("put", put), ("delete", delete)):
            if side_effect is not None:
                stack.enter_context(patch.object(session, name, side_effect=side_effect))
        yield
