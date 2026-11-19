"""Custom cassette system for the integration tests.

Records and replays the SDK's API calls by patching WebAPISession._do_login and
WebAPISession._request at the instance level. pytest-recording supplies only the
--record-mode option.

Cassette file format  tests/cassettes/<module>__<func>.json:
  {
    "interactions": [
      {"kind": "login"},
      {"kind": "api", "method": "GET", "path": "/api/v1/...",
       "params": {...}, "body": null, "headers": {...} | null,
       "result": {"type": "data", "data": {...}}},
      {"kind": "api", ...,
       "result": {"type": "error", "error_class": "ResourceNotFoundError",
                  "message": "...", "error_code": 404,
                  "resource_type": "unknown", "resource_id": ""}}
    ]
  }

An error result also carries response_body when the error has one, and the
PlanInUseError flags for that class (see exc_to_record()).
"""
from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import Any

from synology_apm.sdk._http import WebAPISession, _redact
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

# Time-dependent query param keys, dropped from both sides before replay compares params.
VOLATILE_PARAMS: frozenset[str] = frozenset()


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
        headers: Any = None,
    ) -> None:
        entry: dict[str, Any] = {
            "kind": "api",
            "method": method,
            "path": path,
            "params": params,
            "body": body,
            "headers": headers,
        }
        entry["result"] = exc_to_record(exc) if exc is not None else {"type": "data", "data": data}
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

    def consume_login(self) -> None:
        """Consume the recorded login, which must be the next remaining entry."""
        if not self.interactions:
            raise AssertionError("Cassette exhausted: expected login")
        kind = self.interactions[0]["kind"]
        if kind != "login":
            raise AssertionError(f"Cassette: expected 'login', got {kind!r}")
        self.interactions.pop(0)

    def consume_api(
        self, method: str, path: str, params: Any = None, body: Any = None, headers: Any = None,
    ) -> Any:
        """Consume and replay the recorded entry for one API request.

        The request takes the first remaining entry with the same method and path whose
        params, body and headers also match, so concurrent requests may replay in a different
        order than recorded while identical requests replay in recorded order. Params are
        compared without the VOLATILE_PARAMS keys, and an entry recorded without a headers key
        matches any headers. A request that matches no entry raises AssertionError, naming the
        parts in which the nearest entry with the same method and path differs. Once the
        cassette is exhausted, a logout replays as an empty response.
        """
        if not self.interactions:
            if path == _LOGOUT_PATH:
                return {}
            raise AssertionError(f"Cassette exhausted: expected api({method} {path})")

        candidates = [
            i for i, entry in enumerate(self.interactions)
            if entry["kind"] == "api" and entry["method"] == method and entry["path"] == path
        ]
        if not candidates:
            next_entry = self.interactions[0]
            raise AssertionError(
                f"Cassette: no recorded entry for api({method} {path}); "
                f"next is {next_entry['kind']} {next_entry.get('method')} {next_entry.get('path')!r}"
            )
        request = {"params": params, "body": body, "headers": headers}
        mismatches = [(i, _mismatches(self.interactions[i], request)) for i in candidates]
        chosen = next((i for i, diff in mismatches if not diff), None)
        if chosen is None:
            _, nearest = min(mismatches, key=lambda item: len(item[1]))
            details = "; ".join(
                f"{part}: expected {_redacted(expected)!r}, got {_redacted(actual)!r}"
                for part, expected, actual in nearest
            )
            raise AssertionError(
                f"Cassette: api({method} {path}) matches no recorded entry; "
                f"nearest entry differs in {details}"
            )
        result = self.interactions.pop(chosen)["result"]
        if result["type"] == "error":
            raise _dict_to_exc(result)
        return result["data"]


def _normalize(value: Any) -> Any:
    """Return value as it reads back from a cassette file (tuples become lists, etc.)."""
    return json.loads(json.dumps(value, default=str))


def _stable_params(params: Any) -> Any:
    """Return normalized params without the VOLATILE_PARAMS keys."""
    params = _normalize(params)
    if isinstance(params, dict):
        return {k: v for k, v in params.items() if k not in VOLATILE_PARAMS}
    if isinstance(params, list):
        return [pair for pair in params if not (isinstance(pair, list) and pair and pair[0] in VOLATILE_PARAMS)]
    return params


def _mismatches(entry: dict[str, Any], request: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Return (part, expected, actual) for each request part that differs from entry."""
    diffs: list[tuple[str, Any, Any]] = []
    if _stable_params(entry.get("params")) != _stable_params(request["params"]):
        diffs.append(("params", entry.get("params"), request["params"]))
    if entry.get("body") != _normalize(request["body"]):
        diffs.append(("body", entry.get("body"), request["body"]))
    if "headers" in entry and entry["headers"] != _normalize(request["headers"]):
        diffs.append(("headers", entry["headers"], request["headers"]))
    return diffs


def _redacted(value: Any) -> Any:
    """Mask secrets in a request part; list-of-[key, value] params are masked like param tuples."""
    value = _normalize(value)
    if isinstance(value, list) and value and all(
        isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) for pair in value
    ):
        value = [tuple(pair) for pair in value]
    return _redact(value)


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
        headers = kwargs.get("headers")
        try:
            data = await original_request(method, path, _reauth=_reauth, **kwargs)
        except APMError as exc:
            cassette.push_api(method, path, params, body, None, exc, headers)
            raise
        cassette.push_api(method, path, params, body, data, None, headers)
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
        return cassette.consume_api(
            method, path, kwargs.get("params"), kwargs.get("json"), kwargs.get("headers"),
        )

    session._do_login = _do_login_replay  # type: ignore[method-assign]
    session._request = _request_replay  # type: ignore[method-assign]
