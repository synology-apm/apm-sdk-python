"""Integration tests: SaasCollection (saas.list / saas.get_m365_tenant)"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import WorkloadCategory
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.saas import SaasTenant
from tests.unit.sdk.conftest import assert_resource_error

pytestmark = pytest.mark.integration


# ── saas.list() ───────────────────────────────────────────────────────────────


async def test_saas_list_returns_list(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    assert isinstance(tenants, list)


async def test_saas_list_items_are_saas_tenants(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    for t in tenants:
        assert isinstance(t, SaasTenant)


async def test_saas_list_category_is_m365_or_gws(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    valid = {WorkloadCategory.M365, WorkloadCategory.GWS}
    for t in tenants:
        assert t.category in valid


async def test_saas_list_tenant_ids_are_nonempty(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    for t in tenants:
        assert t.tenant_id, f"tenant_id empty for tenant {t.tenant_name!r}"


async def test_saas_list_tenant_names_are_nonempty(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    for t in tenants:
        assert t.tenant_name, f"tenant_name empty for tenant_id={t.tenant_id}"


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


async def test_get_m365_tenant_returns_saas_tenant(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if t.category == WorkloadCategory.M365]
    if not m365:
        pytest.skip("No M365 tenants configured")
    fetched = await apm.saas.get_m365_tenant(m365[0].tenant_id)
    assert isinstance(fetched, SaasTenant)


async def test_get_m365_tenant_id_matches(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if t.category == WorkloadCategory.M365]
    if not m365:
        pytest.skip("No M365 tenants configured")
    tid = m365[0].tenant_id
    fetched = await apm.saas.get_m365_tenant(tid)
    assert fetched.tenant_id == tid


async def test_get_m365_tenant_provider_is_m365(apm: APMClient) -> None:
    tenants, _ = await apm.saas.list()
    m365 = [t for t in tenants if t.category == WorkloadCategory.M365]
    if not m365:
        pytest.skip("No M365 tenants configured")
    fetched = await apm.saas.get_m365_tenant(m365[0].tenant_id)
    assert fetched.category == WorkloadCategory.M365


async def test_get_m365_tenant_nonexistent_raises_not_found(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.saas.get_m365_tenant("00000000-0000-0000-0000-000000000000")
    assert_resource_error(exc_info, resource_type="SaasTenant", resource_id="00000000-0000-0000-0000-000000000000")
