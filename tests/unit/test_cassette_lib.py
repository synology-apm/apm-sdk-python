"""Unit tests for the integration-test cassette recording and replay matching."""
from __future__ import annotations

import pathlib
from typing import Any

import pytest

import tests.cassette_lib as cassette_lib
from synology_apm.sdk.exceptions import ResourceNotFoundError
from tests.cassette_lib import _Cassette

_LOGOUT_PATH = "/api/v1/preference/logout"


def _entry(path: str, data: Any, *, params: Any = None, body: Any = None, headers: Any = None,
           without_headers: bool = False) -> dict[str, Any]:
    entry: dict[str, Any] = {"kind": "api", "method": "POST", "path": path, "params": params, "body": body}
    if not without_headers:
        entry["headers"] = headers
    entry["result"] = {"type": "data", "data": data}
    return entry


def _cassette(*entries: dict[str, Any]) -> _Cassette:
    c = _Cassette()
    c.interactions = list(entries)
    return c


def test_same_path_requests_replay_by_headers_regardless_of_order() -> None:
    c = _cassette(
        _entry("/x", "from-a", body={"isEdit": False}, headers={"route": "a"}),
        _entry("/x", "from-b", body={"isEdit": False}, headers={"route": "b"}),
    )
    assert c.consume_api("POST", "/x", None, {"isEdit": False}, {"route": "b"}) == "from-b"
    assert c.consume_api("POST", "/x", None, {"isEdit": False}, {"route": "a"}) == "from-a"


def test_params_compared_as_recorded_json() -> None:
    c = _cassette(
        _entry("/x", "first", params=[["id", "1"]]),
        _entry("/x", "second", params=[["id", "2"]]),
    )
    assert c.consume_api("POST", "/x", [("id", "2")]) == "second"


def test_identical_requests_replay_in_recorded_order() -> None:
    c = _cassette(_entry("/poll", "restoring"), _entry("/poll", "ready"))
    assert [c.consume_api("POST", "/poll"), c.consume_api("POST", "/poll")] == ["restoring", "ready"]


def test_entry_recorded_without_headers_matches_any_headers() -> None:
    c = _cassette(_entry("/x", "recorded", params={"id": "1"}, without_headers=True))
    assert c.consume_api("POST", "/x", {"id": "1"}, None, {"route": "a"}) == "recorded"


def test_params_mismatch_raises_instead_of_falling_back() -> None:
    c = _cassette(_entry("/x", "recorded", params={"id": "1"}, without_headers=True))
    with pytest.raises(AssertionError, match=r"params: expected \{'id': '1'\}, got \{'id': '2'\}"):
        c.consume_api("POST", "/x", {"id": "2"})


def test_body_mismatch_raises_naming_body() -> None:
    c = _cassette(_entry("/x", "recorded", body={"name": "plan-a"}))
    with pytest.raises(AssertionError) as exc_info:
        c.consume_api("POST", "/x", None, {"name": "plan-b"})
    message = str(exc_info.value)
    assert "body: expected {'name': 'plan-a'}, got {'name': 'plan-b'}" in message
    assert "params" not in message


def test_headers_mismatch_raises_naming_headers() -> None:
    c = _cassette(_entry("/x", "recorded", headers={"route": "a"}))
    with pytest.raises(AssertionError, match=r"headers: expected \{'route': 'a'\}, got \{'route': 'b'\}"):
        c.consume_api("POST", "/x", None, None, {"route": "b"})


def test_mismatch_message_redacts_secrets() -> None:
    c = _cassette(_entry("/x", "recorded", body={"user": "admin", "password": "recorded-secret"}))
    with pytest.raises(AssertionError) as exc_info:
        c.consume_api("POST", "/x", None, {"user": "operator", "password": "actual-secret"})
    message = str(exc_info.value)
    assert "recorded-secret" not in message
    assert "actual-secret" not in message
    assert "'password': '***'" in message


def test_volatile_params_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cassette_lib, "VOLATILE_PARAMS", frozenset({"since"}))
    c = _cassette(_entry("/x", "recorded", params=[["id", "1"], ["since", "t1"]]))
    assert c.consume_api("POST", "/x", [("id", "1"), ("since", "t2")]) == "recorded"


def test_tuple_params_survive_save_and_load(tmp_path: pathlib.Path) -> None:
    recorded = _Cassette()
    recorded.push_api("GET", "/x", [("id", "1"), ("status", "a")], None, {"ok": True}, None)
    path = tmp_path / "cassette.json"
    recorded.save(path)

    replayed = _Cassette.load(path)
    assert replayed.consume_api("GET", "/x", [("id", "1"), ("status", "a")]) == {"ok": True}


def test_error_record_round_trip_keeps_resource_fields(tmp_path: pathlib.Path) -> None:
    recorded = _Cassette()
    exc = ResourceNotFoundError("not found", resource_type="Workload", resource_id="WL_1", error_code=404)
    recorded.push_api("GET", "/x", None, None, None, exc)
    path = tmp_path / "cassette.json"
    recorded.save(path)

    replayed = _Cassette.load(path)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        replayed.consume_api("GET", "/x")
    assert exc_info.value.resource_type == "Workload"
    assert exc_info.value.resource_id == "WL_1"
    assert exc_info.value.error_code == 404


def test_exhausted_cassette_raises() -> None:
    c = _cassette(_entry("/x", "only"))
    c.consume_api("POST", "/x")
    with pytest.raises(AssertionError, match=r"Cassette exhausted: expected api\(POST /x\)"):
        c.consume_api("POST", "/x")


def test_logout_replays_empty_once_cassette_is_exhausted() -> None:
    c = _cassette()
    assert c.consume_api("GET", _LOGOUT_PATH) == {}


def test_login_must_be_next_entry() -> None:
    c = _cassette(_entry("/x", "first"), {"kind": "login"})
    with pytest.raises(AssertionError, match="expected 'login'"):
        c.consume_login()
    assert c.consume_api("POST", "/x") == "first"
    c.consume_login()
