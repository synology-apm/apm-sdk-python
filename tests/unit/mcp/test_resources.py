"""Tests for resources.py: each resource returns expected top-level keys.

Exercised through an in-memory fastmcp.Client session against resource_server
(see conftest.py) rather than calling the private helpers directly — FastMCP
resolves a resource's ctx via contextvar-based dependency injection at read
time, so this is the only way to drive the real @server.resource wiring
(including URI-template extraction for apm://server/{server_id}).
"""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from synology_apm.sdk import SiteInfo, SiteStorageStats, WorkloadUsageSummary
from tests.unit.mcp.conftest import (
    make_backup_server,
    make_protection_plan,
    make_retirement_plan,
    make_saas_tenant,
    make_tiering_plan,
)


def _make_site_info():
    return SiteInfo(
        site_uuid="uuid-001",
        external_address="apm.corp.com",
        port="443",
        primary_management_server=None,
        secondary_management_server=None,
        site_storage=SiteStorageStats(logical_backup_data_bytes=0, physical_backup_data_bytes=0),
        workload_usage=WorkloadUsageSummary(by_type=()),
    )


async def _read(resource_server, uri: str) -> dict:
    async with Client(resource_server) as client:
        result = await client.read_resource(uri)
        return json.loads(result[0].text)


class TestSiteResource:
    @pytest.mark.asyncio
    async def test_returns_site_info_dict(self, mock_apm, resource_server):
        mock_apm.get_site_info.return_value = _make_site_info()

        parsed = await _read(resource_server, "apm://site")

        assert parsed["site_uuid"] == "uuid-001"
        assert parsed["external_address"] == "apm.corp.com"


class TestServersResource:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm, resource_server):
        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs], 1)

        parsed = await _read(resource_server, "apm://servers")

        assert parsed["total"] == 1
        assert parsed["items"][0]["name"] == "apm-server-01"


class TestProtectionPlansResource:
    @pytest.mark.asyncio
    async def test_returns_plans(self, mock_apm, resource_server):
        plan = make_protection_plan()
        mock_apm.plans.list.return_value = ([plan], 1)

        parsed = await _read(resource_server, "apm://plans/protection")

        assert parsed["items"][0]["name"] == "Daily Backup"


class TestRetirementPlansResource:
    @pytest.mark.asyncio
    async def test_returns_plans(self, mock_apm, resource_server):
        plan = make_retirement_plan()
        mock_apm.retirement_plans.list.return_value = ([plan], 1)

        parsed = await _read(resource_server, "apm://plans/retirement")

        assert parsed["total"] == 1
        assert parsed["items"][0]["name"] == "Compliance Retention"


class TestTieringPlansResource:
    @pytest.mark.asyncio
    async def test_returns_plans(self, mock_apm, resource_server):
        plan = make_tiering_plan()
        mock_apm.tiering_plans.list.return_value = ([plan], 1)

        parsed = await _read(resource_server, "apm://plans/tiering")

        assert parsed["total"] == 1
        assert parsed["items"][0]["name"] == "30-Day Tiering"


class TestTenantsResource:
    @pytest.mark.asyncio
    async def test_returns_tenants(self, mock_apm, resource_server):
        tenant = make_saas_tenant()
        mock_apm.saas.list.return_value = ([tenant], 1)

        parsed = await _read(resource_server, "apm://tenants")

        assert parsed["items"][0]["tenant_name"] == "Contoso"


class TestServerByIdResource:
    @pytest.mark.asyncio
    async def test_extracts_server_id_from_uri_template(self, mock_apm, resource_server):
        bs = make_backup_server(backup_server_id="srv-002", name="apm-server-02")
        mock_apm.backup_servers.get.return_value = bs

        parsed = await _read(resource_server, "apm://server/srv-002")

        assert parsed["backup_server_id"] == "srv-002"
        assert parsed["name"] == "apm-server-02"
        mock_apm.backup_servers.get.assert_called_once_with("srv-002")


class TestResourceError:
    @pytest.mark.asyncio
    async def test_returns_error_json_on_exception(self, mock_apm, resource_server):
        from synology_apm.sdk import ResourceNotFoundError

        mock_apm.backup_servers.get.side_effect = ResourceNotFoundError("not found", "server", "srv-001")

        parsed = await _read(resource_server, "apm://server/srv-001")

        assert parsed["error"] == "not_found"
        assert parsed["resource_id"] == "srv-001"
