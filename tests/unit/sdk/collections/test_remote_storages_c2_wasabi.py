"""Unit tests for RemoteStorageCollection.add(): C2ObjectStorageAddRequest and WasabiCloudStorageAddRequest."""
from __future__ import annotations

from typing import Any

import pytest

from synology_apm.sdk.collections.remote_storages import RemoteStorageCollection
from synology_apm.sdk.exceptions import RemoteStorageUnmanagedCatalogError
from synology_apm.sdk.models.remote_storage import C2ObjectStorageAddRequest, WasabiCloudStorageAddRequest
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from tests.unit.sdk.conftest import make_session, patched_session

STORAGE_ID = "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"

SAMPLE_STORAGE_RAW: dict[str, Any] = {
    "id": STORAGE_ID,
    "displayName": "DSM-Storage",
    "storageType": "AEV",
    "modelName": "DSM",
    "endpoint": "192.0.2.20:8444",
    "connectionStatus": "Connection",
    "usedSpace": "453378",
    "remainingSpace": "366960877568",
}

SAMPLE_CONNECTION: dict[str, Any] = {
    "id": "conn-001",
    "backupServerNamespace": "ns-abc",
    "status": "Unmanaged",
}
SAMPLE_CONNECTIONS_RAW: dict[str, Any] = {"connections": [SAMPLE_CONNECTION]}
EMPTY_CONNECTIONS_RAW: dict[str, Any] = {"connections": []}

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="plan-uuid",
    name="Daily Backup",
    description="",
    retention=None,
    workload_count=0,
    run_schedule_by_controller_time=False,
)


# ── add(): C2ObjectStorageAddRequest ───────────────────────────────────────


async def test_add_c2_posts_correct_body() -> None:
    session = make_session()
    region_cert_called = False
    vhost_called = False
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal region_cert_called, vhost_called
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            region_cert_called = True
            return {"region": "tw-001", "cert": ""}
        if "support_virtual_host" in path:
            vhost_called = True
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = C2ObjectStorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="apm-test-new-1",
        )
        result = await collection.add(req)

    assert not region_cert_called
    assert not vhost_called
    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "C2_S3"
    assert add_body["endpoint"] == ""
    assert add_body["displayName"] == "apm-test-new-1"
    assert result.storage.storage_id == STORAGE_ID


async def test_add_c2_catalog_check_uses_endpoint_empty() -> None:
    session = make_session()
    catalog_body_captured: dict[str, Any] | None = None

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal catalog_body_captured
        if "storage_connection/remote" in path:
            catalog_body_captured = json
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = C2ObjectStorageAddRequest(access_key="ak", secret_key="sk", vault_name="apm-test-new-1")
        await collection.add(req)

    assert catalog_body_captured is not None
    assert catalog_body_captured["endpoint"] == ""
    assert catalog_body_captured["certificate"] == ""
    assert "supportVirtualHost" not in catalog_body_captured


async def test_add_c2_unmanaged_catalogs_no_plan_raises() -> None:
    session = make_session()
    add_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal add_called
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            add_called = True
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    with patched_session(session, post=fake_post):
        collection = RemoteStorageCollection(session)
        req = C2ObjectStorageAddRequest(access_key="ak", secret_key="sk", vault_name="apm-test-new-1")
        with pytest.raises(RemoteStorageUnmanagedCatalogError):
            await collection.add(req)

    assert not add_called


async def test_add_c2_unmanaged_catalogs_with_plan_relinks() -> None:
    session = make_session()
    batch_relink_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal batch_relink_called
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        if "batch_relink" in path:
            batch_relink_called = True
            return {}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = C2ObjectStorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="apm-test-new-1",
            unmanaged_retirement_plan=SAMPLE_RETIREMENT_PLAN,
        )
        await collection.add(req)

    assert batch_relink_called


# ── add(): WasabiCloudStorageAddRequest ────────────────────────────────────


async def test_add_wasabi_posts_correct_body() -> None:
    session = make_session()
    region_cert_called = False
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal region_cert_called
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            region_cert_called = True
            return {}
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = WasabiCloudStorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="apm-test-1",
        )
        result = await collection.add(req)

    assert not region_cert_called
    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "WASABI_S3"
    assert add_body["endpoint"] == ""
    assert add_body["displayName"] == "apm-test-1"
    assert add_body["vaultName"] == "apm-test-1"
    assert result.storage.storage_id == STORAGE_ID


async def test_add_wasabi_catalog_check_uses_endpoint_empty() -> None:
    session = make_session()
    catalog_body_captured: dict[str, Any] | None = None

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal catalog_body_captured
        if "storage_connection/remote" in path:
            catalog_body_captured = json
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = WasabiCloudStorageAddRequest(access_key="ak", secret_key="sk", vault_name="apm-test-1")
        await collection.add(req)

    assert catalog_body_captured is not None
    assert catalog_body_captured["endpoint"] == ""
    assert catalog_body_captured["certificate"] == ""
    assert "supportVirtualHost" not in catalog_body_captured


async def test_add_wasabi_unmanaged_catalogs_no_plan_raises() -> None:
    session = make_session()
    add_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal add_called
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            add_called = True
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    with patched_session(session, post=fake_post):
        collection = RemoteStorageCollection(session)
        req = WasabiCloudStorageAddRequest(access_key="ak", secret_key="sk", vault_name="apm-test-1")
        with pytest.raises(RemoteStorageUnmanagedCatalogError):
            await collection.add(req)

    assert not add_called


async def test_add_wasabi_unmanaged_catalogs_with_plan_relinks() -> None:
    session = make_session()
    batch_relink_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal batch_relink_called
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        if "batch_relink" in path:
            batch_relink_called = True
            return {}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = WasabiCloudStorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="apm-test-1",
            unmanaged_retirement_plan=SAMPLE_RETIREMENT_PLAN,
        )
        await collection.add(req)

    assert batch_relink_called
