"""Fixtures shared by the SDK collection tests."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.sdk.collections._sandbox_fixtures import Env, FakeVmm, make_sandboxes
from tests.unit.sdk.conftest import make_session, patched_session


@pytest.fixture
def env() -> Iterator[Env]:
    """A sandbox collection whose session answers from a FakeVmm."""
    session = make_session()
    vmm = FakeVmm()
    with patched_session(session, get=vmm.get, post=vmm.post):
        yield Env(make_sandboxes(session), vmm)
