"""Integration tests: SaasCollection (saas.list / saas.get_m365_tenant / saas.get_gws_domain)"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import WorkloadCategory
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.saas import GWSDomainInfo, M365TenantInfo
from tests.unit.sdk.conftest import assert_resource_error

pytestmark = pytest.mark.integration


# ── saas.list() ───────────────────────────────────────────────────────────────


async def test_saas_list_returns_list(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    assert isinstance(tenants, list)


async def test_saas_list_items_are_saas_applications(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    for t in tenants:
        assert isinstance(t, (M365TenantInfo, GWSDomainInfo))


async def test_saas_list_category_is_m365_or_gws(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    valid = {WorkloadCategory.M365, WorkloadCategory.GWS}
    for t in tenants:
        assert t.category in valid


async def test_saas_list_names_are_nonempty(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    for t in tenants:
        assert t.name, f"name empty for a SaaS application ({t.category.value})"


async def test_saas_list_data_usage_non_negative(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    for t in tenants:
        assert t.protected_data_bytes >= 0


async def test_saas_list_total_is_int(apm: APMClient) -> None:
    _, total = await apm.saas.list()
    assert isinstance(total, int)


async def test_saas_list_m365_tenants_present(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if t.category == WorkloadCategory.M365]
    assert len(m365) > 0, "Expected at least one M365 tenant configured in APM"


# ── saas.get_m365_tenant() ────────────────────────────────────────────────────


async def test_get_m365_tenant_returns_tenant_info(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if isinstance(t, M365TenantInfo)]
    if not m365:
        pytest.skip("No M365 tenants configured")
    fetched = await apm.saas.get_m365_tenant(m365[0].tenant_id)
    assert isinstance(fetched, M365TenantInfo)


async def test_get_m365_tenant_id_matches(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if isinstance(t, M365TenantInfo)]
    if not m365:
        pytest.skip("No M365 tenants configured")
    tid = m365[0].tenant_id
    fetched = await apm.saas.get_m365_tenant(tid)
    assert fetched.tenant_id == tid


async def test_get_m365_tenant_provider_is_m365(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if isinstance(t, M365TenantInfo)]
    if not m365:
        pytest.skip("No M365 tenants configured")
    fetched = await apm.saas.get_m365_tenant(m365[0].tenant_id)
    assert fetched.category == WorkloadCategory.M365


async def test_get_m365_tenant_nonexistent_raises_not_found(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.saas.get_m365_tenant("00000000-0000-0000-0000-000000000000")
    assert_resource_error(exc_info, resource_type="M365TenantInfo", resource_id="00000000-0000-0000-0000-000000000000")


# ── saas.get_gws_domain() ─────────────────────────────────────────────────────


async def _first_gws_domain(apm: APMClient) -> str:
    tenants, _ = await apm.saas.list()
    gws = [t for t in tenants if isinstance(t, GWSDomainInfo)]
    if not gws:
        pytest.skip("No GWS domains configured on this APM instance")
    return gws[0].domain


async def test_get_gws_domain_matches(apm: APMClient) -> None:
    domain = await _first_gws_domain(apm)
    fetched = await apm.saas.get_gws_domain(domain)
    assert fetched.domain == domain


async def test_get_gws_domain_category_is_gws(apm: APMClient) -> None:
    domain = await _first_gws_domain(apm)
    fetched = await apm.saas.get_gws_domain(domain)
    assert fetched.category == WorkloadCategory.GWS


async def test_get_gws_domain_nonexistent_raises_not_found(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.saas.get_gws_domain("nonexistent-domain.example.com")
    assert_resource_error(exc_info, resource_type="GWSDomainInfo", resource_id="nonexistent-domain.example.com")
