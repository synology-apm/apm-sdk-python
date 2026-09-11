"""Unit tests for RemoteStorageCollection.add()/update(): Azure Blob Storage."""
from __future__ import annotations

from typing import Any

import pytest

from synology_apm.sdk.collections.remote_storages import RemoteStorageCollection
from synology_apm.sdk.enums import RemoteStorageStatus, RemoteStorageType
from synology_apm.sdk.exceptions import RemoteStorageUnmanagedCatalogError
from synology_apm.sdk.models.remote_storage import (
    AzureBlobChinaStorageAddRequest,
    AzureBlobStorageAddRequest,
    AzureBlobStorageUpdateRequest,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from tests.unit.sdk.conftest import make_session, patched_session

STORAGE_ID = "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"
TENANT_ID = "123e4567-e89b-12d3-a456-426614174020"
CLIENT_ID = "123e4567-e89b-12d3-a456-426614174021"

SAMPLE_STORAGE_RAW: dict[str, Any] = {
    "id": STORAGE_ID,
    "displayName": "my-container",
    "storageType": "AZURE_BLOB",
    "vaultName": "my-container",
    "endpoint": "https://azurestorage01.blob.core.windows.net",
    "connectionStatus": "Connection",
    "usedSpace": "",
    "remainingSpace": "",
    "azureEntraAppId": CLIENT_ID,
    "azureAccountName": "azurestorage01",
}

EMPTY_CONNECTIONS_RAW: dict[str, Any] = {"connections": []}
SAMPLE_CONNECTION: dict[str, Any] = {
    "id": "conn-001",
    "backupServerNamespace": "ns-abc",
    "status": "Unmanaged",
}
SAMPLE_CONNECTIONS_RAW: dict[str, Any] = {"connections": [SAMPLE_CONNECTION]}

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="plan-uuid",
    name="Daily Backup",
    description="",
    retention=None,
    workload_count=0,
    run_schedule_by_controller_time=False,
)


# ── add(): AzureBlobStorageAddRequest / AzureBlobChinaStorageAddRequest ─────


async def test_add_azure_blob_posts_correct_body() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = AzureBlobStorageAddRequest(
            tenant_id=TENANT_ID, client_id=CLIENT_ID, secret="azure-secret-01",
            account_name="azurestorage01", vault_name="my-container",
        )
        result = await collection.add(req)

    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "AZURE_BLOB"
    assert add_body["displayName"] == "my-container"
    assert add_body["vaultName"] == "my-container"
    assert add_body["accessKey"] == ""
    assert add_body["secretKey"] == ""
    assert "endpoint" not in add_body
    assert add_body["azureInfo"] == {
        "accountName": "azurestorage01",
        "containerName": "my-container",
        "tenantId": TENANT_ID,
        "clientId": CLIENT_ID,
        "secret": "azure-secret-01",
    }
    assert result.storage.storage_id == STORAGE_ID
    assert result.storage.account_name == "azurestorage01"
    assert result.storage.client_id == CLIENT_ID


async def test_add_azure_blob_china_posts_correct_body() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return {**SAMPLE_STORAGE_RAW, "storageType": "AZURE_BLOB_CHINA"}

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = AzureBlobChinaStorageAddRequest(
            tenant_id=TENANT_ID, client_id=CLIENT_ID, secret="azure-secret-01",
            account_name="azurestorage01", vault_name="my-container",
        )
        await collection.add(req)

    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "AZURE_BLOB_CHINA"


async def test_add_azure_blob_catalog_check_uses_azure_body_shape() -> None:
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
        req = AzureBlobStorageAddRequest(
            tenant_id=TENANT_ID, client_id=CLIENT_ID, secret="azure-secret-01",
            account_name="azurestorage01", vault_name="my-container",
        )
        await collection.add(req)

    assert catalog_body_captured is not None
    assert catalog_body_captured == {
        "storageType": "AZURE_BLOB",
        "vaultName": "my-container",
        "supportVirtualHost": True,
        "azureInfo": {
            "accountName": "azurestorage01",
            "containerName": "my-container",
            "tenantId": TENANT_ID,
            "clientId": CLIENT_ID,
            "secret": "azure-secret-01",
        },
    }


async def test_add_azure_blob_unmanaged_catalogs_no_plan_raises() -> None:
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
        req = AzureBlobStorageAddRequest(
            tenant_id=TENANT_ID, client_id=CLIENT_ID, secret="azure-secret-01",
            account_name="azurestorage01", vault_name="my-container",
        )
        with pytest.raises(RemoteStorageUnmanagedCatalogError):
            await collection.add(req)

    assert not add_called


async def test_add_azure_blob_unmanaged_catalogs_with_plan_relinks() -> None:
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
        req = AzureBlobStorageAddRequest(
            tenant_id=TENANT_ID, client_id=CLIENT_ID, secret="azure-secret-01",
            account_name="azurestorage01", vault_name="my-container",
            unmanaged_retirement_plan=SAMPLE_RETIREMENT_PLAN,
        )
        await collection.add(req)

    assert batch_relink_called


# ── update(): AzureBlobStorageUpdateRequest ─────────────────────────────────


async def test_update_azure_blob_posts_correct_body() -> None:
    session = make_session()
    captured_body: dict[str, Any] | None = None

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal captured_body
        if path == "/api/v1/external_storage/update":
            captured_body = json
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        storage = await collection.get(STORAGE_ID)
        req = AzureBlobStorageUpdateRequest(
            tenant_id=TENANT_ID, client_id=CLIENT_ID, secret="new-azure-secret",
        )
        updated = await collection.update(storage, req)

    assert captured_body == {
        "id": STORAGE_ID,
        "azureInfo": {
            "accountName": "azurestorage01",
            "containerName": "my-container",
            "tenantId": TENANT_ID,
            "clientId": CLIENT_ID,
            "secret": "new-azure-secret",
        },
    }
    assert updated.storage_id == STORAGE_ID


# ── parsing: azureAccountName / azureEntraAppId ─────────────────────────────


async def test_get_parses_azure_account_and_client_id() -> None:
    session = make_session()

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, get=fake_get):
        collection = RemoteStorageCollection(session)
        storage = await collection.get(STORAGE_ID)

    assert storage.storage_type == RemoteStorageType.AZURE_BLOB
    assert storage.status == RemoteStorageStatus.CONNECTED
    assert storage.account_name == "azurestorage01"
    assert storage.client_id == CLIENT_ID
    assert storage.vault_name == "my-container"


async def test_get_non_azure_storage_has_empty_account_and_client_id() -> None:
    session = make_session()
    raw = {
        "id": STORAGE_ID,
        "displayName": "DSM-Storage",
        "storageType": "AEV",
        "endpoint": "192.0.2.20:8444",
        "connectionStatus": "Connection",
    }

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return raw

    with patched_session(session, get=fake_get):
        collection = RemoteStorageCollection(session)
        storage = await collection.get(STORAGE_ID)

    assert storage.account_name == ""
    assert storage.client_id == ""
