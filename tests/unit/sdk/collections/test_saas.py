"""Unit tests for SaasCollection."""
from __future__ import annotations

import pytest

from synology_apm.sdk.collections.saas import SaasCollection
from synology_apm.sdk.enums import WorkloadCategory
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.saas import SaasTenant
from tests.unit.sdk.conftest import BASE_URL, connected_session

CLOUDAPP_URL = f"{BASE_URL}/api/v1/application/cloudapp"

SAMPLE_M365_ENTRY = {
    "tenant": {
        "tenantId": "m365-tenant-uuid-001",
        "tenantName": "Contoso",
        "tenantMail": "admin@contoso.com",
        "dataUsageInfo": {"dataUsage": "1073741824"},
    },
}

SAMPLE_GWS_ENTRY = {
    "tenant": {
        "domainId": "gw-domain-001",
        "domainName": "Corp GWS",
        "domain": "corp.example.com",
        "dataUsageInfo": {"dataUsage": "536870912"},
    },
}


# ── list() ────────────────────────────────────────────────────────────────────


async def test_list_parses_m365_tenant_fields() -> None:
    """list() should correctly map M365 entry to SaasTenant."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [SAMPLE_M365_ENTRY], "gw": []})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    tenant = result[0]
    assert isinstance(tenant, SaasTenant)
    assert tenant.tenant_id == "m365-tenant-uuid-001"
    assert tenant.tenant_name == "Contoso"
    assert tenant.tenant_email == "admin@contoso.com"
    assert tenant.category == WorkloadCategory.M365
    assert tenant.protected_data_bytes == 1073741824


async def test_list_parses_gws_tenant_fields() -> None:
    """list() should correctly map GWS entry to SaasTenant."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [], "gw": [SAMPLE_GWS_ENTRY]})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    tenant = result[0]
    assert tenant.tenant_id == "gw-domain-001"
    assert tenant.tenant_name == "Corp GWS"
    assert tenant.tenant_email == "corp.example.com"
    assert tenant.category == WorkloadCategory.GWS
    assert tenant.protected_data_bytes == 536870912


async def test_list_returns_empty_when_no_tenants() -> None:
    """list() should return [] when both m365 and gw are empty."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [], "gw": []})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert result == []


async def test_list_m365_first_then_gws() -> None:
    """list() should return M365 tenants before GWS tenants."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [SAMPLE_M365_ENTRY], "gw": [SAMPLE_GWS_ENTRY]})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert result[0].category == WorkloadCategory.M365
    assert result[1].category == WorkloadCategory.GWS


async def test_list_returns_total_as_int_when_api_returns_string() -> None:
    """total should be int even when API returns it as a string."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [SAMPLE_M365_ENTRY], "gw": [], "total": "42"})
        col = SaasCollection(session)
        _, total = await col.list()
        await session.disconnect()

    assert total == 42
    assert isinstance(total, int)


# ── get_m365_tenant() ─────────────────────────────────────────────────────────


async def test_get_m365_tenant_returns_saas_tenant() -> None:
    """get_m365_tenant() should return SaasTenant with tenant details."""
    tenant_id = "m365-tenant-uuid-001"
    detail_url = f"{BASE_URL}/api/v1/application/m365/tenant/{tenant_id}"
    response = {
        "isFound": True,
        "data": {
            "tenant": {
                "tenantId": tenant_id,
                "tenantName": "Contoso",
                "tenantMail": "admin@contoso.com",
            }
        },
    }
    async with connected_session() as (session, m):

        m.get(detail_url, payload=response)
        col = SaasCollection(session)
        tenant = await col.get_m365_tenant(tenant_id)
        await session.disconnect()

    assert isinstance(tenant, SaasTenant)
    assert tenant.tenant_id == tenant_id
    assert tenant.tenant_name == "Contoso"
    assert tenant.tenant_email == "admin@contoso.com"
    assert tenant.category == WorkloadCategory.M365
    assert tenant.protected_data_bytes == 0


async def test_get_m365_tenant_raises_not_found_when_not_found() -> None:
    """get_m365_tenant() should raise ResourceNotFoundError when isFound=False."""
    tenant_id = "non-existent-tenant"
    detail_url = f"{BASE_URL}/api/v1/application/m365/tenant/{tenant_id}"

    async with connected_session() as (session, m):

        m.get(detail_url, payload={"isFound": False, "data": {}})
        col = SaasCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get_m365_tenant(tenant_id)
        await session.disconnect()

    assert exc_info.value.resource_type == "SaasTenant"
    assert exc_info.value.resource_id == tenant_id
