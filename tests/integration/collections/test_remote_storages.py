"""Integration tests: RemoteStorageCollection"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import RemoteStorageStatus, RemoteStorageType
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.remote_storage import RemoteStorage
from tests.unit.sdk.conftest import assert_resource_error

pytestmark = pytest.mark.integration


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_returns_list(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    assert isinstance(storages, list)


async def test_list_total_matches_length(apm: APMClient) -> None:
    storages, total = await apm.remote_storages.list()
    assert total == len(storages)


async def test_list_items_are_remote_storage_instances(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    for s in storages:
        assert isinstance(s, RemoteStorage)


async def test_list_ids_are_nonempty(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    for s in storages:
        assert s.storage_id, f"storage_id empty for {s.name!r}"


async def test_list_type_is_valid_enum(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    valid = set(RemoteStorageType)
    for s in storages:
        assert s.storage_type in valid, f"unexpected storage_type {s.storage_type!r}"


async def test_list_status_is_valid_enum(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    valid = set(RemoteStorageStatus)
    for s in storages:
        assert s.status in valid, f"unexpected status {s.status!r}"


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_returns_remote_storage_by_id(apm: APMClient) -> None:
    storages, _ = await apm.remote_storages.list()
    if not storages:
        pytest.skip("No remote storages configured on this APM instance")
    storage_id = storages[0].storage_id
    fetched = await apm.remote_storages.get(storage_id)
    assert fetched.storage_id == storage_id


async def test_get_raises_not_found_for_bad_id(apm: APMClient) -> None:
    with pytest.raises(ResourceNotFoundError) as exc_info:
        await apm.remote_storages.get("00000000-0000-0000-0000-000000000000")
    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id="00000000-0000-0000-0000-000000000000")
