"""Custom cassette system for aiohttp-based integration tests.

pytest-recording/vcrpy does not support aiohttp's async transport, so this
module patches WebAPISession._do_login and WebAPISession._request at the
instance level to record and replay HTTP interactions.

Cassette file format  tests/cassettes/<module>__<func>.json:
  {
    "interactions": [
      {"kind": "login"},
      {"kind": "api", "method": "GET", "path": "/api/v1/...",
       "params": {...}, "body": null,
       "result": {"type": "data", "data": {...}}},
      {"kind": "api", ...,
       "result": {"type": "error", "error_class": "ResourceNotFoundError",
                  "message": "...", "error_code": 404,
                  "resource_type": "unknown", "resource_id": ""}}
    ]
  }
"""
from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import Any

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.exceptions import (
    APIError,
    APMError,
    AuthenticationError,
    DuplicateWorkloadError,
    InvalidOperationError,
    NotSupportedError,
    PermissionDeniedError,
    PlanInUseError,
    PlanNameConflictError,
    ResourceNotFoundError,
    _ResourceError,
)

CASSETTES_DIR = pathlib.Path(__file__).parent / "cassettes"

_EXCEPTION_CLASSES: dict[str, type[APMError]] = {
    "ResourceNotFoundError": ResourceNotFoundError,
    "NotSupportedError": NotSupportedError,
    "AuthenticationError": AuthenticationError,
    "PermissionDeniedError": PermissionDeniedError,
    "InvalidOperationError": InvalidOperationError,
    "PlanInUseError": PlanInUseError,
    "PlanNameConflictError": PlanNameConflictError,
    "DuplicateWorkloadError": DuplicateWorkloadError,
    "APIError": APIError,
}

_LOGOUT_PATH = "/api/v1/preference/logout"


def cassette_path(nodeid: str) -> pathlib.Path:
    """Map a pytest node ID to a cassette file path."""
    # "tests/integration/test_workloads.py::test_list_returns_list"
    # -> "tests/cassettes/test_workloads__test_list_returns_list.json"
    parts = nodeid.split("::")
    module_stem = pathlib.Path(parts[0]).stem
    func_name = parts[-1]
    return CASSETTES_DIR / f"{module_stem}__{func_name}.json"


def exc_to_record(exc: APMError, *, transform: Callable[[Any], Any] = lambda x: x) -> dict[str, Any]:
    """Serialize an APMError to a JSON-safe record (shared with the SDK smoke-test trace).

    transform is applied to response_body (e.g. the smoke tool passes its to_jsonable).
    Resource fields are included for any _ResourceError subclass, so new resource
    exception types are covered automatically.
    """
    d: dict[str, Any] = {
        "type": "error",
        "error_class": type(exc).__name__,
        "message": exc.message,
        "error_code": exc.error_code,
    }
    if exc.response_body is not None:
        d["response_body"] = transform(exc.response_body)
    if isinstance(exc, _ResourceError):
        d["resource_type"] = exc.resource_type
        d["resource_id"] = exc.resource_id
    if isinstance(exc, PlanInUseError):
        d["has_workloads"] = exc.has_workloads
        d["has_server_template"] = exc.has_server_template
        d["has_backup_servers"] = exc.has_backup_servers
    return d


_exc_to_dict = exc_to_record


def _dict_to_exc(d: dict[str, Any]) -> APMError:
    cls = _EXCEPTION_CLASSES.get(d["error_class"], APIError)
    response_body = d.get("response_body")
    if cls is PlanInUseError:
        return cls(
            d["message"],
            resource_type=d.get("resource_type", "unknown"),
            resource_id=d.get("resource_id", ""),
            has_workloads=d.get("has_workloads", False),
            has_server_template=d.get("has_server_template", False),
            has_backup_servers=d.get("has_backup_servers", False),
            error_code=d.get("error_code"),
            response_body=response_body,
        )
    if issubclass(cls, _ResourceError):
        return cls(
            d["message"],
            resource_type=d.get("resource_type", "unknown"),
            resource_id=d.get("resource_id", ""),
            error_code=d.get("error_code"),
            response_body=response_body,
        )
    return cls(d["message"], error_code=d.get("error_code"), response_body=response_body)


class _Cassette:
    def __init__(self) -> None:
        self.interactions: list[dict[str, Any]] = []
        self._pos: int = 0

    # ── Recording ────────────────────────────────────────────────────────────

    def push_login(self) -> None:
        self.interactions.append({"kind": "login"})

    def push_api(
        self,
        method: str,
        path: str,
        params: Any,
        body: Any,
        data: Any,
        exc: APMError | None,
    ) -> None:
        entry: dict[str, Any] = {
            "kind": "api",
            "method": method,
            "path": path,
            "params": params,
            "body": body,
        }
        entry["result"] = _exc_to_dict(exc) if exc is not None else {"type": "data", "data": data}
        self.interactions.append(entry)

    def save(self, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"interactions": self.interactions}, f, indent=2, default=str)

    # ── Replay ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: pathlib.Path) -> _Cassette:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        c = cls()
        c.interactions = data["interactions"]
        return c

    def _has_next(self) -> bool:
        return self._pos < len(self.interactions)

    def _peek_kind(self) -> str | None:
        if self._has_next():
            kind = self.interactions[self._pos]["kind"]
            assert kind is None or isinstance(kind, str)
            return kind
        return None

    def _consume(self) -> dict[str, Any]:
        entry = self.interactions[self._pos]
        self._pos += 1
        return entry

    def consume_login(self) -> None:
        entry = self._consume()
        if entry["kind"] != "login":
            raise AssertionError(f"Cassette: expected 'login', got {entry['kind']!r}")

    def consume_api(self, method: str, path: str) -> Any:
        if not self._has_next():
            # Cassette exhausted — only safe for best-effort calls like logout
            if path == _LOGOUT_PATH:
                return {}
            raise AssertionError(f"Cassette exhausted: expected api({method} {path})")

        # Fast path: next entry matches exactly (covers all sequential tests)
        next_entry = self.interactions[self._pos]
        if (
            next_entry["kind"] == "api"
            and next_entry["method"] == method
            and next_entry["path"] == path
        ):
            return self._finish_api(self._consume())

        # Slow path: scan forward for a matching entry.  This handles coroutines
        # that ran in parallel via asyncio.gather() and were recorded in
        # network-completion order but replayed in a different order.
        for i in range(self._pos, len(self.interactions)):
            entry = self.interactions[i]
            if entry["kind"] == "api" and entry["method"] == method and entry["path"] == path:
                # Remove the entry at position i and return its result
                self.interactions.pop(i)
                return self._finish_api(entry)

        raise AssertionError(
            f"Cassette: no matching entry for api({method} {path}); "
            f"next is {next_entry.get('method')} {next_entry.get('path')!r}"
        )

    def _finish_api(self, entry: dict[str, Any]) -> Any:
        result = entry["result"]
        if result["type"] == "error":
            raise _dict_to_exc(result)
        return result["data"]


def install_recording(session: WebAPISession, cassette: _Cassette) -> None:
    """Wrap session methods to record every interaction into cassette."""
    original_do_login = session._do_login
    original_request = session._request

    async def _do_login_record() -> None:
        await original_do_login()
        cassette.push_login()

    async def _request_record(
        method: str,
        path: str,
        *,
        _reauth: bool = True,
        **kwargs: Any,
    ) -> Any:
        params = kwargs.get("params")
        body = kwargs.get("json")
        try:
            data = await original_request(method, path, _reauth=_reauth, **kwargs)
        except APMError as exc:
            cassette.push_api(method, path, params, body, None, exc)
            raise
        cassette.push_api(method, path, params, body, data, None)
        return data

    session._do_login = _do_login_record  # type: ignore[method-assign]
    session._request = _request_record  # type: ignore[method-assign]


def install_replay(session: WebAPISession, cassette: _Cassette) -> None:
    """Replace session methods with cassette-backed replay."""

    async def _do_login_replay() -> None:
        cassette.consume_login()

    async def _request_replay(
        method: str,
        path: str,
        *,
        _reauth: bool = True,
        **kwargs: Any,
    ) -> Any:
        return cassette.consume_api(method, path)

    session._do_login = _do_login_replay  # type: ignore[method-assign]
    session._request = _request_replay  # type: ignore[method-assign]
