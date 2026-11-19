"""Integration test fixtures.

--record-mode (from pytest-recording) picks where each test runs:
    none (default)  replay the test's cassette in tests/cassettes/; skip when it is missing
    new_episodes    record against the live APM when the cassette is missing, replay otherwise
    all             record every test against the live APM, overwriting its cassette
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from synology_apm.sdk import APMClient
from synology_apm.sdk.exceptions import APMError
from tests.cassette_lib import _Cassette, cassette_path, install_recording, install_replay

load_dotenv()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: marks tests as integration tests")


def _get_env(key: str) -> str | None:
    return os.environ.get(key, "").strip() or None


@pytest.fixture(scope="session")
def record_mode(request: pytest.FixtureRequest) -> str:
    mode = request.config.getoption("--record-mode")
    assert isinstance(mode, str)
    return mode


@pytest.fixture(scope="function")
def live(request: pytest.FixtureRequest, record_mode: str) -> bool:
    """Whether this test runs against the live APM (True) or replays its cassette (False).

    Live under --record-mode=all, and under --record-mode=new_episodes when the test has no
    cassette yet; replay otherwise.
    """
    return record_mode == "all" or (
        record_mode == "new_episodes" and not cassette_path(request.node.nodeid).exists()
    )


@pytest_asyncio.fixture(scope="function")
async def apm(request: pytest.FixtureRequest, live: bool) -> AsyncGenerator[APMClient, None]:
    """Function-scoped APMClient backed by a per-test cassette.

    Replay (``live`` is False): loads tests/cassettes/<module>__<test>.json and replays its
    interactions without touching the network.

    Live (``live`` is True): connects to the APM configured in .env, runs the test, then saves
    the cassette. The client uses its default request timeout unless the test carries
    @pytest.mark.apm_timeout(seconds).
    """
    path = cassette_path(request.node.nodeid)

    if not live:
        # ── Replay ───────────────────────────────────────────────────────────
        if not path.exists():
            pytest.skip("No cassette — run with --record-mode=new_episodes first")
        cassette = _Cassette.load(path)
        client = APMClient("localhost", "user", "pass", verify_ssl=False)
        install_replay(client._session, cassette)
        await client.connect()
        yield client
        await client.disconnect()

    else:
        # ── Record ───────────────────────────────────────────────────────────
        host = _get_env("APM_HOST")
        user = _get_env("APM_USERNAME")
        pwd = _get_env("APM_PASSWORD")
        if not host or not user or not pwd:
            pytest.skip("APM_HOST / APM_USERNAME / APM_PASSWORD not set in .env")

        # Accept a full URL in .env (e.g. "https://apm.corp.com").
        if host and "://" in host:
            host = host.split("://", 1)[1]

        timeout_marker = request.node.get_closest_marker("apm_timeout")
        if timeout_marker is None:
            client = APMClient(host, user, pwd, verify_ssl=False)
        else:
            client = APMClient(host, user, pwd, verify_ssl=False, timeout=float(timeout_marker.args[0]))
        cassette = _Cassette()
        install_recording(client._session, cassette)

        try:
            await client.connect()
        except APMError as exc:
            pytest.skip(f"Cannot connect to APM at {host}: {exc}")
        except Exception as exc:
            pytest.skip(f"Server unreachable ({type(exc).__name__}): {exc}")

        yield client
        await client.disconnect()
        cassette.save(path)
