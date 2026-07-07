"""Integration tests: get_site_info()"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient, BackupServer, SiteInfo, SiteStorageStats
from synology_apm.sdk.models.system import WorkloadUsageSummary

pytestmark = pytest.mark.integration


# ── get_site_info() ────────────────────────────────────────────────────────


async def test_get_site_info_returns_site_info(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert isinstance(site, SiteInfo)


async def test_site_info_has_nonempty_uuid(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.site_uuid, "site_uuid should not be empty"


async def test_site_info_primary_management_server_is_backup_server(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert isinstance(site.primary_management_server, BackupServer)


async def test_site_info_primary_management_server_has_nonempty_name(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.primary_management_server is not None
    assert site.primary_management_server.name, "management server name should not be empty"


async def test_site_info_primary_management_server_has_system_version(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.primary_management_server is not None
    assert site.primary_management_server.system_version, "system_version should not be empty"
    assert "APM" in site.primary_management_server.system_version, (
        f"unexpected system version string: {site.primary_management_server.system_version}"
    )


async def test_site_info_primary_management_server_has_model(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.primary_management_server is not None
    assert site.primary_management_server.model, "model should not be empty"


async def test_site_info_primary_management_server_has_serial(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.primary_management_server is not None
    assert site.primary_management_server.serial, "serial should not be empty"


async def test_site_info_site_storage_is_correct_type(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert isinstance(site.site_storage, SiteStorageStats)


async def test_site_storage_transfer_bytes_non_negative(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.site_storage.logical_backup_data_bytes >= 0


async def test_site_storage_used_bytes_non_negative(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.site_storage.physical_backup_data_bytes >= 0


async def test_site_storage_space_saved_non_negative(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.site_storage.backup_data_reduction_bytes >= 0


async def test_site_storage_saving_ratio_in_range(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert 0.0 <= site.site_storage.backup_data_reduction_ratio <= 100.0


async def test_site_info_workload_usage_is_correct_type(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert isinstance(site.workload_usage, WorkloadUsageSummary)


async def test_site_info_workload_usage_total_count_non_negative(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.workload_usage.total_count >= 0


async def test_site_info_workload_usage_total_protected_data_non_negative(apm: APMClient) -> None:
    site = await apm.get_site_info()
    assert site.workload_usage.total_protected_data_bytes >= 0
