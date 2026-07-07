"""Install full-fidelity API trace recording on a WebAPISession instance.

Mirrors tests/cassette_lib.py's install_recording() pattern (instance-level monkeypatching of
_do_login/_request), but writes incrementally to a JSONL file and tags each line with the
current ctx.call() step via a contextvar, so concurrent asyncio.gather() calls are correctly
attributed.
"""
from __future__ import annotations

import contextvars
import json
import pathlib
from datetime import UTC, datetime
from typing import IO, Any

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.exceptions import APMError
from tests.cassette_lib import exc_to_record

from ._serialize import to_jsonable

current_step: contextvars.ContextVar[str] = contextvars.ContextVar("current_step", default="<setup>")


def install_trace(session: WebAPISession, jsonl_path: pathlib.Path) -> IO[str]:
    """Wrap session._do_login and session._request to append one JSON line per call to jsonl_path.

    Returns the open file handle so the caller can close() it when done.
    """
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    f: IO[str] = jsonl_path.open("a", encoding="utf-8")
    seq_counter = [0]

    original_do_login = session._do_login
    original_request = session._request

    def _write(record: dict[str, Any]) -> None:
        seq_counter[0] += 1
        record["seq"] = seq_counter[0]
        record["step"] = current_step.get()
        record["timestamp"] = datetime.now(UTC).isoformat()
        f.write(json.dumps(record, default=str) + "\n")
        f.flush()

    async def _do_login_trace() -> None:
        try:
            await original_do_login()
        except APMError as exc:
            _write({"kind": "login", "result": _exc_record(exc)})
            raise
        _write({"kind": "login", "result": {"type": "data"}})

    async def _request_trace(method: str, path: str, *, _reauth: bool = True, **kwargs: Any) -> Any:
        params = kwargs.get("params")
        body = kwargs.get("json")
        try:
            data = await original_request(method, path, _reauth=_reauth, **kwargs)
        except APMError as exc:
            _write({
                "kind": "api", "method": method, "path": path,
                "params": to_jsonable(params), "body": to_jsonable(body),
                "result": _exc_record(exc),
            })
            raise
        _write({
            "kind": "api", "method": method, "path": path,
            "params": to_jsonable(params), "body": to_jsonable(body),
            "result": {"type": "data", "status": None, "data": to_jsonable(data)},
        })
        return data

    session._do_login = _do_login_trace  # type: ignore[method-assign]
    session._request = _request_trace  # type: ignore[method-assign]
    return f


def _exc_record(exc: APMError) -> dict[str, Any]:
    record = exc_to_record(exc, transform=to_jsonable)
    record["status"] = exc.error_code
    record.setdefault("response_body", None)
    return record
