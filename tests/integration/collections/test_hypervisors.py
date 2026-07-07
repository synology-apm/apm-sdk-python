"""Integration tests: HypervisorCollection"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import HypervisorType
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.hypervisor import Hypervisor
from tests.unit.sdk.conftest import assert_resource_error

pytestmark = pytest.mark.integration


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_returns_list(apm: APMClient) -> None:
    hypervisors, _ = await apm.hypervisors.list()
    assert isinstance(hypervisors, list)


async def test_list_total_matches_length(apm: APMClient) -> None:
    hypervisors, total = await apm.hypervisors.list()
    assert total == len(hypervisors)


async def test_list_items_are_hypervisor_instances(apm: APMClient) -> None:
    hypervisors, _ = await apm.hypervisors.list()
    for h in hypervisors:
        assert isinstance(h, Hypervisor)


async def test_list_ids_are_nonempty(apm: APMClient) -> None:
    hypervisors, _ = await apm.hypervisors.list()
    for h in hypervisors:
        assert h.hypervisor_id, f"hypervisor_id empty for {h.hostname!r}"


async def test_list_host_type_is_valid_enum(apm: APMClient) -> None:
    hypervisors, _ = await apm.hypervisors.list()
    valid = set(HypervisorType)
    for h in hypervisors:
        assert h.host_type in valid, f"unexpected host_type {h.host_type!r}"


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_returns_hypervisor_by_id(apm: APMClient) -> None:
    hypervisors, _ = await apm.hypervisors.list()
    if not hypervisors:
        pytest.skip("No hypervisors registered on this APM instance")
    hypervisor_id = hypervisors[0].hypervisor_id
    fetched = await apm.hypervisors.get(hypervisor_id)
    assert fetched.hypervisor_id == hypervisor_id


async def test_get_raises_not_found_for_bad_id(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.hypervisors.get("00000000-0000-0000-0000-000000000000")
    assert_resource_error(exc_info, resource_type="Hypervisor", resource_id="00000000-0000-0000-0000-000000000000")
