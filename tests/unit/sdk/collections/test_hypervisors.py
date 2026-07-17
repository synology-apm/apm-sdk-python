"""Unit tests for HypervisorCollection."""
from __future__ import annotations

from typing import Any

import pytest

from synology_apm.sdk.collections.hypervisors import HypervisorCollection
from synology_apm.sdk.enums import HypervisorType
from synology_apm.sdk.exceptions import ResourceNotFoundError
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
)

LIST_URL = f"{BASE_URL}/api/v1/inventory"
HV_ID = "978eabd4-e332-459f-a8e0-35a0aa312118"
GET_URL = f"{BASE_URL}/api/v1/inventory/{HV_ID}"

SAMPLE_HYPERVISOR_RAW: dict[str, Any] = {
    "id": HV_ID,
    "spec": {
        "hostType": "ESXi",
        "hostName": "esxi1.example.com",
        "hostAddr": "192.0.2.40",
        "portWebapi": 443,
        "authUser": "root",
        "authPassword": "",
        "protocol": "HTTP",
        "description": "",
        "version": "6.5",

    },
    "status": {},
}


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_returns_hypervisors() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"inventories": [SAMPLE_HYPERVISOR_RAW]})
        collection = HypervisorCollection(session)
        hypervisors, total = await collection.list()
        await session.disconnect()

    assert total == 1
    assert len(hypervisors) == 1
    h = hypervisors[0]
    assert h.hypervisor_id == HV_ID
    assert h.hostname == "esxi1.example.com"
    assert h.address == "192.0.2.40"
    assert h.host_type == HypervisorType.VSPHERE_ESXI
    assert h.account == "root"
    assert h.description == ""
    assert h.port == 443
    assert h.version == "6.5"


async def test_list_empty() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"inventories": []})
        collection = HypervisorCollection(session)
        hypervisors, total = await collection.list()
        await session.disconnect()

    assert hypervisors == []
    assert total == 0


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_returns_hypervisor() -> None:
    async with connected_session() as (session, m):
        m.get(GET_URL, payload=SAMPLE_HYPERVISOR_RAW)
        collection = HypervisorCollection(session)
        h = await collection.get(HV_ID)
        await session.disconnect()

    assert h.hypervisor_id == HV_ID
    assert h.host_type == HypervisorType.VSPHERE_ESXI
    assert h.hostname == "esxi1.example.com"
    assert h.address == "192.0.2.40"
    assert h.port == 443
    assert h.account == "root"
    assert h.description == ""
    assert h.version == "6.5"


async def test_get_not_found_raises() -> None:
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/inventory/no-such-id", payload={})
        collection = HypervisorCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("no-such-id")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Hypervisor", resource_id="no-such-id")


async def test_get_not_found_http_404_raises_with_resource_fields() -> None:
    body = {
        "error": {
            "code": 404,
            "status": "Not Found",
            "message": "get inventory failed.",
            "details": [{"errorCode": 3007, "message": "resource not found"}],
        }
    }
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/inventory/no-such-id", status=404, payload=body)
        collection = HypervisorCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("no-such-id")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Hypervisor", resource_id="no-such-id")
    assert exc_info.value.error_code == 404
    assert exc_info.value.response_body == body


# ── get_by_name() ──────────────────────────────────────────────────────────


async def test_get_by_name_hostname() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"inventories": [SAMPLE_HYPERVISOR_RAW]})
        collection = HypervisorCollection(session)
        h = await collection.get_by_name("esxi1.example.com")
        await session.disconnect()

    assert h.hypervisor_id == HV_ID


async def test_get_by_name_case_insensitive() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"inventories": [SAMPLE_HYPERVISOR_RAW]})
        collection = HypervisorCollection(session)
        h = await collection.get_by_name("ESXI1.EXAMPLE.COM")
        await session.disconnect()

    assert h.hypervisor_id == HV_ID


async def test_get_by_name_address() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"inventories": [SAMPLE_HYPERVISOR_RAW]})
        collection = HypervisorCollection(session)
        h = await collection.get_by_name("192.0.2.40")
        await session.disconnect()

    assert h.hypervisor_id == HV_ID


async def test_get_by_name_not_found_raises() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"inventories": [SAMPLE_HYPERVISOR_RAW]})
        collection = HypervisorCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("no-such-hypervisor")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Hypervisor", resource_id="no-such-hypervisor")


async def test_get_by_name_does_not_match_hypervisor_id() -> None:
    """get_by_name() should not match on hypervisor_id; ID lookup goes through get()."""
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"inventories": [SAMPLE_HYPERVISOR_RAW]})
        collection = HypervisorCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name(HV_ID)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Hypervisor", resource_id=HV_ID)


# ── host_type mapping ──────────────────────────────────────────────────────


@pytest.mark.parametrize("api_val,expected", [
    ("ESXi",            HypervisorType.VSPHERE_ESXI),
    ("vCenter",         HypervisorType.VSPHERE_VCENTER),
    ("HyperV",          HypervisorType.HYPERV_STANDALONE),
    ("SCVMM",           HypervisorType.HYPERV_SCVMM),
    ("FailoverCluster", HypervisorType.HYPERV_FAILOVER_CLUSTER),
    ("SomeNewValue",    HypervisorType.UNKNOWN),  # fallback
])
async def test_host_type_mapping(api_val: str, expected: HypervisorType) -> None:
    async with connected_session() as (session, m):
        raw = {**SAMPLE_HYPERVISOR_RAW, "spec": {**SAMPLE_HYPERVISOR_RAW["spec"], "hostType": api_val}}
        m.get(LIST_URL, payload={"inventories": [raw]})
        collection = HypervisorCollection(session)
        hypervisors, _ = await collection.list()
        await session.disconnect()

    assert hypervisors[0].host_type == expected
