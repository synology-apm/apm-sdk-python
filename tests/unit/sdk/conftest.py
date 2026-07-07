"""Shared constants and helpers for SDK unit tests.

Import these in collection test files to avoid repeating boilerplate:

    from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session

Most tests only need connected_session() — an async context manager yielding a
logged-in WebAPISession plus its aioresponses mock, ready for endpoint mocks.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from aioresponses import aioresponses

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


@asynccontextmanager
async def connected_session(**kwargs: Any) -> AsyncIterator[tuple[WebAPISession, aioresponses]]:
    """Yield a connected WebAPISession and its aioresponses mock.

    Wraps the prologue every collection test repeats: create the session, mock
    the login endpoint, connect, then hand control to the test to register its
    own endpoint mocks. kwargs are forwarded to make_session().
    """
    session = make_session(**kwargs)
    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        yield session, m


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
