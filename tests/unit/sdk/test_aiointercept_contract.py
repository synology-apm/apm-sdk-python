"""Contract tests for the aiointercept library itself (not the SDK).

Pins the specific aiointercept==0.1.9 behaviors this test suite's migration
away from aioresponses depends on, so a future version bump that silently
changes any of them fails loudly and locally here, rather than as scattered,
hard-to-diagnose failures across the ~25 SDK test files that use aiointercept.
Deliberately does not touch WebAPISession/APMClient — that's the SDK's own
request/response contract, already covered elsewhere.
"""
from __future__ import annotations

import aiohttp
import pytest
from aiointercept import CallbackResult, aiointercept
from yarl import URL


async def test_requests_dict_is_keyed_by_method_and_url_with_kwargs() -> None:
    """.requests must stay a {(METHOD, URL): [request, ...]} mapping with a .kwargs["json"] accessor.

    This is the exact shape aioresponses' m.requests has, and the whole SDK
    test suite's request-body assertions (m.requests[key][0].kwargs["json"])
    depend on it staying this way.
    """
    async with aiointercept(mock_external_urls=True) as m:
        m.post("https://contract-probe.test/x", payload={"ok": True})
        async with (
            aiohttp.ClientSession() as session,
            session.post("https://contract-probe.test/x", json={"a": 1}, ssl=False) as resp,
        ):
            assert await resp.json() == {"ok": True}

        key = ("POST", URL("https://contract-probe.test/x"))
        assert key in m.requests
        assert m.requests[key][0].kwargs["json"] == {"a": 1}


async def test_status_and_repeat_kwargs_behave_as_expected() -> None:
    """status= sets the response code; repeat=True serves every subsequent call, not just the first."""
    async with aiointercept(mock_external_urls=True) as m:
        m.get("https://contract-probe.test/y", status=404, repeat=True)
        async with aiohttp.ClientSession() as session:
            async with session.get("https://contract-probe.test/y", ssl=False) as resp:
                assert resp.status == 404
            async with session.get("https://contract-probe.test/y", ssl=False) as resp:
                assert resp.status == 404


async def test_exception_true_raises_a_real_server_disconnected_error() -> None:
    """exception=True must keep raising a genuine aiohttp.ServerDisconnectedError client-side.

    The SDK's connection-error tests rely on this being a real exception the
    SDK's _CONNECTION_ERRORS tuple actually catches, not a fabricated one.
    """
    async with aiointercept(mock_external_urls=True) as m:
        m.get("https://contract-probe.test/z", exception=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.ServerDisconnectedError):
                async with session.get("https://contract-probe.test/z", ssl=False):
                    pass


async def test_callback_is_the_full_response_handler() -> None:
    """A registered callback must build and return its own CallbackResult (no separate payload=)."""
    async def handler(url: URL, **kwargs: object) -> CallbackResult:
        return CallbackResult(payload={"from": "callback"})

    async with aiointercept(mock_external_urls=True) as m:
        m.get("https://contract-probe.test/w", callback=handler)
        async with (
            aiohttp.ClientSession() as session,
            session.get("https://contract-probe.test/w", ssl=False) as resp,
        ):
            assert await resp.json() == {"from": "callback"}


async def test_mock_external_urls_intercepts_a_bare_https_host_with_no_injection_point() -> None:
    """mock_external_urls=True must transparently intercept a plain https://<host> request.

    WebAPISession builds its own aiohttp.ClientSession internally with no way
    to point it at aiointercept's own server_url, so this DNS/connector-level
    interception mode is the only one compatible with the SDK.
    """
    async with aiointercept(mock_external_urls=True) as m:
        m.get("https://contract-probe.test/v", payload={"intercepted": True})
        async with (
            aiohttp.ClientSession() as session,
            session.get("https://contract-probe.test/v", ssl=False) as resp,
        ):
            assert await resp.json() == {"intercepted": True}
