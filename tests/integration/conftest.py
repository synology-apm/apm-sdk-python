"""Integration test fixtures.

Default mode (--record-mode=none) replays from cassettes in tests/cassettes/
and requires no live APM server.

To record cassettes against a real APM:
    pytest tests/integration/ --record-mode=new_episodes -v

To run offline against recorded cassettes:
    pytest tests/integration/ --record-mode=none -v   (default)
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


@pytest_asyncio.fixture(scope="function")
async def apm(
    request: pytest.FixtureRequest,
    record_mode: str,
) -> AsyncGenerator[APMClient, None]:
    """Function-scoped APMClient backed by a per-test cassette.

    Replay mode (default): loads tests/cassettes/<module>__<test>.json and
    replays all interactions without hitting the network.

    Record mode (--record-mode=new_episodes|all): connects to the live APM
    server, runs the test, then saves the cassette.
    """
    path = cassette_path(request.node.nodeid)

    use_live = record_mode == "all" or (
        record_mode == "new_episodes" and not path.exists()
    )

    if not use_live:
        # ── Replay ───────────────────────────────────────────────────────────
        if not path.exists():
            pytest.skip("No cassette — run with --record-mode=new_episodes first")
        cassette = _Cassette.load(path)
        # Dummy credentials — no real connection is made
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

        # Strip scheme prefix if .env uses full URL (e.g. "https://10.0.0.1")
        if host and "://" in host:
            host = host.split("://", 1)[1]

        client = APMClient(host, user, pwd, verify_ssl=False, timeout=15.0)
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
