"""Unit tests for SaasCollection."""
from __future__ import annotations

import pytest

from synology_apm.sdk.collections.saas import SaasCollection, _parse_gws_domain, _parse_m365_tenant
from synology_apm.sdk.enums import WorkloadCategory
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.saas import GWSDomainInfo, M365TenantInfo
from tests.unit.sdk.conftest import BASE_URL, connected_session, make_session, null_out

CLOUDAPP_URL = f"{BASE_URL}/api/v1/application/cloudapp"

SAMPLE_M365_ENTRY = {
    "tenant": {
        "tenantId": "m365-tenant-uuid-001",
        "tenantName": "Contoso",
        "tenantMail": "contoso.onmicrosoft.com",
        "dataUsageInfo": {"dataUsage": "1073741824"},
    },
}

SAMPLE_GWS_ENTRY = {
    "gwDomain": {
        "domain": "gwsdemo.example.com",
        "domainName": "Gwsdemo",
        "adminEmail": "evelyn.test@gwsdemo.example.com",
        "dataUsageInfo": {"dataUsage": "536870912"},
    },
}


# ── list() ────────────────────────────────────────────────────────────────────


async def test_list_parses_m365_tenant_fields() -> None:
    """list() should correctly map M365 entry to M365TenantInfo."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [SAMPLE_M365_ENTRY], "gw": []})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    tenant = result[0]
    assert isinstance(tenant, M365TenantInfo)
    assert tenant.tenant_id == "m365-tenant-uuid-001"
    assert tenant.name == "Contoso"
    assert tenant.domain == "contoso.onmicrosoft.com"
    assert tenant.category == WorkloadCategory.M365
    assert tenant.protected_data_bytes == 1073741824


async def test_list_parses_gws_domain_fields() -> None:
    """list() should correctly map GWS entry to GWSDomainInfo."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [], "gw": [SAMPLE_GWS_ENTRY]})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    domain_info = result[0]
    assert isinstance(domain_info, GWSDomainInfo)
    assert domain_info.domain == "gwsdemo.example.com"
    assert domain_info.name == "Gwsdemo"
    assert domain_info.domain_admin == "evelyn.test@gwsdemo.example.com"
    assert domain_info.category == WorkloadCategory.GWS
    assert domain_info.protected_data_bytes == 536870912


async def test_list_parses_gws_domain_fields_without_domain_name() -> None:
    """GWS entries with no domainName should fall back to the domain string for name."""
    entry = {
        "gwDomain": {
            "domain": "gwsdemo.example.com",
            "dataUsageInfo": {"dataUsage": "536870912"},
        },
    }
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [], "gw": [entry]})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    domain_info = result[0]
    assert isinstance(domain_info, GWSDomainInfo)
    assert domain_info.domain == "gwsdemo.example.com"
    assert domain_info.name == "gwsdemo.example.com"
    assert domain_info.domain_admin == ""
    assert domain_info.category == WorkloadCategory.GWS


async def test_list_returns_empty_when_no_tenants() -> None:
    """list() should return [] when both m365 and gw are empty."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": [], "gw": []})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert result == []


async def test_list_m365_first_then_gws() -> None:
    """list() should return M365 tenants before GWS domains."""
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


async def test_list_sends_keyword_filter() -> None:
    """list(keyword=...) should send filter.cloudappKeyword in the request body."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = SaasCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365": [], "gw": []}
        await col.list(keyword="contoso")

    assert mock_post.call_count == 1
    body = mock_post.call_args[1]["json"]
    assert body["filter"] == {"cloudappKeyword": "contoso"}


async def test_list_omits_filter_when_no_keyword() -> None:
    """list() with no keyword should not send a filter key at all."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    col = SaasCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"m365": [], "gw": []}
        await col.list()

    body = mock_post.call_args[1]["json"]
    assert "filter" not in body


async def test_list_survives_null_m365_and_gw_keys() -> None:
    """m365/gw JSON null (key present, value null — distinct from an absent key) must not
    crash list(); both are treated as empty pages instead of raising."""
    async with connected_session() as (session, m):

        m.post(CLOUDAPP_URL, payload={"m365": None, "gw": None})
        col = SaasCollection(session)
        result, total = await col.list()
        await session.disconnect()

    assert result == []


# ── _parse_m365_tenant() / _parse_gws_domain(): null vs. absent JSON field handling ──
#
# Called directly (not through list()) per the project's null-field testing convention —
# these are standalone parser functions, so there's no need to drive them through a mocked
# HTTP round-trip.


@pytest.mark.parametrize("null_paths", [
    ("tenant",),
    ("tenant.tenantId", "tenant.tenantName", "tenant.tenantMail", "tenant.dataUsageInfo"),
], ids=["null_tenant", "null_nested_fields"])
def test_parse_m365_tenant_survives_null_fields(null_paths: tuple[str, ...]) -> None:
    raw = null_out(SAMPLE_M365_ENTRY, *null_paths)

    tenant = _parse_m365_tenant(raw)

    assert tenant.tenant_id == ""
    assert tenant.name == ""
    assert tenant.domain == ""
    assert tenant.category == WorkloadCategory.M365
    assert tenant.protected_data_bytes == 0


@pytest.mark.parametrize("null_paths", [
    ("gwDomain",),
    ("gwDomain.domain", "gwDomain.domainName", "gwDomain.adminEmail", "gwDomain.dataUsageInfo"),
], ids=["null_tenant", "null_nested_fields"])
def test_parse_gws_domain_survives_null_fields(null_paths: tuple[str, ...]) -> None:
    raw = null_out(SAMPLE_GWS_ENTRY, *null_paths)

    domain_info = _parse_gws_domain(raw)

    assert domain_info.domain == ""
    assert domain_info.name == ""
    assert domain_info.domain_admin == ""
    assert domain_info.category == WorkloadCategory.GWS
    assert domain_info.protected_data_bytes == 0


# ── get_m365_tenant() ─────────────────────────────────────────────────────────


async def test_get_m365_tenant_returns_tenant_info() -> None:
    """get_m365_tenant() should return M365TenantInfo with tenant details."""
    tenant_id = "m365-tenant-uuid-001"
    detail_url = f"{BASE_URL}/api/v1/application/m365/tenant/{tenant_id}"
    response = {
        "isFound": True,
        "data": {
            "tenant": {
                "tenantId": tenant_id,
                "tenantName": "Contoso",
                "tenantMail": "contoso.onmicrosoft.com",
            }
        },
    }
    async with connected_session() as (session, m):

        m.get(detail_url, payload=response)
        col = SaasCollection(session)
        tenant = await col.get_m365_tenant(tenant_id)
        await session.disconnect()

    assert isinstance(tenant, M365TenantInfo)
    assert tenant.tenant_id == tenant_id
    assert tenant.name == "Contoso"
    assert tenant.domain == "contoso.onmicrosoft.com"
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

    assert exc_info.value.resource_type == "M365TenantInfo"
    assert exc_info.value.resource_id == tenant_id


@pytest.mark.parametrize("response", [
    {"isFound": True, "data": None},
    {"isFound": True, "data": {"tenant": None}},
], ids=["null_data", "null_tenant"])
async def test_get_m365_tenant_survives_null_data_and_tenant(response: dict[str, object]) -> None:
    """data/tenant JSON null (key present, value null — distinct from an absent key) must
    not crash get_m365_tenant(); tenant_id falls back to the requested tenant_id and the
    remaining fields fall back to empty strings."""
    tenant_id = "m365-tenant-uuid-001"
    detail_url = f"{BASE_URL}/api/v1/application/m365/tenant/{tenant_id}"

    async with connected_session() as (session, m):

        m.get(detail_url, payload=response)
        col = SaasCollection(session)
        tenant = await col.get_m365_tenant(tenant_id)
        await session.disconnect()

    assert tenant.tenant_id == tenant_id
    assert tenant.name == ""
    assert tenant.domain == ""


# ── get_gws_domain() ──────────────────────────────────────────────────────────


async def test_get_gws_domain_returns_domain_info() -> None:
    """get_gws_domain() should return GWSDomainInfo with domain details."""
    domain = "gwsdemo.example.com"
    detail_url = f"{BASE_URL}/api/v1/application/gw/domain/{domain}"
    response = {
        "isFound": True,
        "data": {
            "gwDomain": {
                "domain": domain,
                "domainName": "Gwsdemo",
                "adminEmail": "evelyn.test@gwsdemo.example.com",
                "dataUsageInfo": {"dataUsage": 1024},
            }
        },
    }
    async with connected_session() as (session, m):

        m.get(detail_url, payload=response)
        col = SaasCollection(session)
        domain_info = await col.get_gws_domain(domain)
        await session.disconnect()

    assert isinstance(domain_info, GWSDomainInfo)
    assert domain_info.domain == domain
    assert domain_info.name == "Gwsdemo"
    assert domain_info.domain_admin == "evelyn.test@gwsdemo.example.com"
    assert domain_info.category == WorkloadCategory.GWS
    assert domain_info.protected_data_bytes == 1024


async def test_get_gws_domain_raises_not_found_when_not_found() -> None:
    """get_gws_domain() should raise ResourceNotFoundError when isFound=False."""
    domain = "non-existent-domain.example.com"
    detail_url = f"{BASE_URL}/api/v1/application/gw/domain/{domain}"

    async with connected_session() as (session, m):

        m.get(detail_url, payload={"isFound": False, "data": {}})
        col = SaasCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get_gws_domain(domain)
        await session.disconnect()

    assert exc_info.value.resource_type == "GWSDomainInfo"
    assert exc_info.value.resource_id == domain


@pytest.mark.parametrize("response", [
    {"isFound": True, "data": None},
    {"isFound": True, "data": {"gwDomain": None}},
], ids=["null_data", "null_gw_domain"])
async def test_get_gws_domain_survives_null_data_and_domain(response: dict[str, object]) -> None:
    """data/gwDomain JSON null (key present, value null — distinct from an absent key) must
    not crash get_gws_domain(); domain falls back to the requested domain and the
    remaining fields fall back to empty strings / zero."""
    domain = "gwsdemo.example.com"
    detail_url = f"{BASE_URL}/api/v1/application/gw/domain/{domain}"

    async with connected_session() as (session, m):

        m.get(detail_url, payload=response)
        col = SaasCollection(session)
        domain_info = await col.get_gws_domain(domain)
        await session.disconnect()

    assert domain_info.domain == domain
    assert domain_info.name == domain
    assert domain_info.domain_admin == ""
    assert domain_info.protected_data_bytes == 0
