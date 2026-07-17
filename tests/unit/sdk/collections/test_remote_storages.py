"""Unit tests for RemoteStorageCollection."""
from __future__ import annotations

from typing import Any

import pytest

from synology_apm.sdk.collections.remote_storages import RemoteStorageCollection
from synology_apm.sdk.enums import RemoteStorageStatus, RemoteStorageType
from synology_apm.sdk.exceptions import (
    APIError,
    RemoteStorageConflictError,
    RemoteStorageEncryptionMismatchError,
    RemoteStorageInUseError,
    RemoteStorageUnmanagedCatalogError,
    ResourceNotFoundError,
)
from synology_apm.sdk.models.remote_storage import (
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APVStorageAddRequest,
    C2ObjectStorageAddRequest,
    GenericS3StorageAddRequest,
    RemoteStorage,
    RemoteStorageUpdateRequest,
    WasabiCloudStorageAddRequest,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, connected_session, make_session, patched_session

LIST_URL = f"{BASE_URL}/api/v1/external_storage/detail"
STORAGE_ID = "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"
GET_URL = f"{BASE_URL}/api/v1/external_storage/{STORAGE_ID}"

# New URL constants for CRUD operations
ADD_URL = f"{BASE_URL}/api/v1/external_storage"
UPDATE_URL = f"{BASE_URL}/api/v1/external_storage/update"
DELETE_URL = f"{BASE_URL}/api/v1/external_storage/{STORAGE_ID}"
REGION_CERT_URL = f"{BASE_URL}/api/v1/external_storage/compatable_s3/region_cert"
APV_CERT_URL = f"{BASE_URL}/api/v1/external_storage/cert"
APV_INFO_URL = f"{BASE_URL}/api/v1/external_storage/aev/info"
VHOST_URL = f"{BASE_URL}/api/v1/external_storage/bucket/support_virtual_host"
CATALOG_CHECK_URL = f"{BASE_URL}/api/v1/storage_connection/remote"
BATCH_RELINK_URL = f"{BASE_URL}/api/v1/storage_connection/batch_relink"

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

SAMPLE_APV_INFO_RAW: dict[str, Any] = {
    "vaultName": "my-bucket",
    "serverName": "APV Vault",
    "modelName": "DP320",
    "serverVersion": "1.0.0-0001",
}

SAMPLE_CONNECTION: dict[str, Any] = {
    "id": "conn-001",
    "backupServerNamespace": "ns-abc",
    "status": "Unmanaged",
}
SAMPLE_CONNECTIONS_RAW: dict[str, Any] = {"connections": [SAMPLE_CONNECTION]}
EMPTY_CONNECTIONS_RAW: dict[str, Any] = {"connections": []}

SAMPLE_ADD_RESPONSE: dict[str, Any] = {"id": STORAGE_ID, "encryptionKey": ""}

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="plan-uuid",
    name="Daily Backup",
    description="",
    retention=None,
    workload_count=0,
    run_schedule_by_controller_time=False,
)

SAMPLE_REMOTE_STORAGE = RemoteStorage(
    storage_id=STORAGE_ID,
    name="DSM-Storage",
    storage_type=RemoteStorageType.ACTIVE_PROTECT_VAULT,
    device_model="DSM",
    endpoint="192.0.2.20:8444",
    status=RemoteStorageStatus.CONNECTED,
    used_bytes=453378,
    remaining_bytes=366960877568,
)


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_returns_remote_storages() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [SAMPLE_STORAGE_RAW]})
        collection = RemoteStorageCollection(session)
        remote_storages, total = await collection.list()
        await session.disconnect()

    assert total == 1
    assert len(remote_storages) == 1
    s = remote_storages[0]
    assert s.storage_id == STORAGE_ID
    assert s.name == "DSM-Storage"
    assert s.storage_type == RemoteStorageType.ACTIVE_PROTECT_VAULT
    assert s.device_model == "DSM"
    assert s.endpoint == "192.0.2.20:8444"
    assert s.status == RemoteStorageStatus.CONNECTED
    assert s.used_bytes == 453378
    assert s.remaining_bytes == 366960877568


async def test_list_empty() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": []})
        collection = RemoteStorageCollection(session)
        remote_storages, total = await collection.list()
        await session.disconnect()

    assert remote_storages == []
    assert total == 0


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_returns_storage() -> None:
    async with connected_session() as (session, m):
        m.get(GET_URL, payload=SAMPLE_STORAGE_RAW)
        collection = RemoteStorageCollection(session)
        s = await collection.get(STORAGE_ID)
        await session.disconnect()

    assert s.storage_id == STORAGE_ID
    assert s.status == RemoteStorageStatus.CONNECTED


async def test_get_not_found_empty_body_raises() -> None:
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/external_storage/no-such-id", payload={})
        collection = RemoteStorageCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("no-such-id")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id="no-such-id")


async def test_get_not_found_http_404_raises_with_resource_fields() -> None:
    body = {
        "error": {
            "code": 404,
            "status": "Not Found",
            "message": "get storage from db failed.",
            "details": [{"errorCode": 3007, "message": "resource not found"}],
        }
    }
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/external_storage/no-such-id", status=404, payload=body)
        collection = RemoteStorageCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("no-such-id")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id="no-such-id")
    assert exc_info.value.error_code == 404
    assert exc_info.value.response_body == body


# ── get_by_name() ──────────────────────────────────────────────────────────


async def test_get_by_name_display_name() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [SAMPLE_STORAGE_RAW]})
        collection = RemoteStorageCollection(session)
        s = await collection.get_by_name("DSM-Storage")
        await session.disconnect()

    assert s.storage_id == STORAGE_ID


async def test_get_by_name_case_insensitive() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [SAMPLE_STORAGE_RAW]})
        collection = RemoteStorageCollection(session)
        s = await collection.get_by_name("DSM-STORAGE")
        await session.disconnect()

    assert s.storage_id == STORAGE_ID


async def test_get_by_name_not_found_raises() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [SAMPLE_STORAGE_RAW]})
        collection = RemoteStorageCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name("no-such-storage")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id="no-such-storage")


async def test_get_by_name_does_not_match_storage_id() -> None:
    """get_by_name() should not match on storage_id; ID lookup goes through get()."""
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [SAMPLE_STORAGE_RAW]})
        collection = RemoteStorageCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get_by_name(STORAGE_ID)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id=STORAGE_ID)


# ── parser: space fields ───────────────────────────────────────────────────


async def test_parser_zero_used_is_zero_not_none() -> None:
    """usedSpace="0" means no space has been used yet; should remain 0 and not be treated as null."""
    async with connected_session() as (session, m):
        raw = {**SAMPLE_STORAGE_RAW, "usedSpace": "0", "remainingSpace": "1073741824"}
        m.get(LIST_URL, payload={"storages": [raw]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    s = remote_storages[0]
    assert s.used_bytes == 0
    assert s.remaining_bytes == 1073741824


async def test_parser_empty_string_becomes_none() -> None:
    """usedSpace="" or remainingSpace="" should be converted to None."""
    async with connected_session() as (session, m):
        raw = {**SAMPLE_STORAGE_RAW, "usedSpace": "", "remainingSpace": None}
        m.get(LIST_URL, payload={"storages": [raw]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    s = remote_storages[0]
    assert s.used_bytes is None
    assert s.remaining_bytes is None


# ── status mapping ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("api_val,expected", [
    ("Connection",      RemoteStorageStatus.CONNECTED),
    ("AuthFailed",      RemoteStorageStatus.AUTH_FAILED),
    ("Disconnect",      RemoteStorageStatus.DISCONNECTED),
    ("Unknown",         RemoteStorageStatus.UNKNOWN),
    ("VaultNotMounted", RemoteStorageStatus.VAULT_NOT_MOUNTED),
    ("DataCorrupted",   RemoteStorageStatus.DATA_CORRUPTED),
    ("SomeUnmanaged",   RemoteStorageStatus.UNMANAGED_CATALOG),
    ("SomeNewValue",    RemoteStorageStatus.UNKNOWN),  # fallback
])
async def test_status_mapping(api_val: str, expected: RemoteStorageStatus) -> None:
    async with connected_session() as (session, m):
        raw = {**SAMPLE_STORAGE_RAW, "connectionStatus": api_val}
        m.get(LIST_URL, payload={"storages": [raw]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    assert remote_storages[0].status == expected


# ── storage type mapping ────────────────────────────────────────────────────


@pytest.mark.parametrize("api_val,expected", [
    ("AEV",              RemoteStorageType.ACTIVE_PROTECT_VAULT),
    ("C2_S3",            RemoteStorageType.C2_OBJECT_STORAGE),
    ("AWS_S3",           RemoteStorageType.AMAZON_S3),
    ("AWS_S3_CHINA",     RemoteStorageType.AMAZON_S3_CHINA),
    ("WASABI_S3",        RemoteStorageType.WASABI),
    ("AZURE_BLOB",       RemoteStorageType.AZURE_BLOB),
    ("AZURE_BLOB_CHINA", RemoteStorageType.AZURE_BLOB_CHINA),
    ("COMPATIBLE_S3",    RemoteStorageType.S3_COMPATIBLE),
    ("SOME_NEW_TYPE",    RemoteStorageType.UNKNOWN),  # fallback
])
async def test_storage_type_mapping(api_val: str, expected: RemoteStorageType) -> None:
    async with connected_session() as (session, m):
        raw = {**SAMPLE_STORAGE_RAW, "storageType": api_val}
        m.get(LIST_URL, payload={"storages": [raw]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    assert remote_storages[0].storage_type == expected


async def test_parser_encryption_enabled_true() -> None:
    """encryption_enabled should be True when isEncryption=true in the API response."""
    async with connected_session() as (session, m):
        raw = {**SAMPLE_STORAGE_RAW, "isEncryption": True}
        m.get(LIST_URL, payload={"storages": [raw]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    assert remote_storages[0].encryption_enabled is True


async def test_parser_encryption_enabled_false_when_absent() -> None:
    """encryption_enabled should default to False when isEncryption is absent."""
    async with connected_session() as (session, m):
        # SAMPLE_STORAGE_RAW has no isEncryption key
        m.get(LIST_URL, payload={"storages": [SAMPLE_STORAGE_RAW]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    assert remote_storages[0].encryption_enabled is False


# ── parser: name + vault_name ──────────────────────────────────────────────


async def test_parser_name_field() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [{**SAMPLE_STORAGE_RAW, "displayName": "DSM-Storage"}]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    assert remote_storages[0].name == "DSM-Storage"


async def test_parser_vault_name() -> None:
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [{**SAMPLE_STORAGE_RAW, "vaultName": "my-vault"}]})
        collection = RemoteStorageCollection(session)
        remote_storages, _ = await collection.list()
        await session.disconnect()

    assert remote_storages[0].vault_name == "my-vault"


# ── add(): S3 Compatible ────────────────────────────────────────────────────


async def test_add_s3_posts_correct_body() -> None:
    async with connected_session() as (session, m):
        m.post(REGION_CERT_URL, payload={"region": "us-east-1", "cert": ""})
        m.post(VHOST_URL, payload={"supportVirtualHost": True})
        m.post(CATALOG_CHECK_URL, payload=EMPTY_CONNECTIONS_RAW)
        m.post(ADD_URL, payload={"id": STORAGE_ID, "encryptionKey": ""})
        m.get(GET_URL, payload=SAMPLE_STORAGE_RAW)
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
        )
        result = await collection.add(req)
        await session.disconnect()

    assert result.storage.storage_id == STORAGE_ID
    assert result.encryption_key is None


async def test_add_s3_posts_correct_body_fields() -> None:
    """Verify S3 add body fields via patching the session post directly."""
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": []}
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
        )
        result = await collection.add(req)

    assert result.storage.storage_id == STORAGE_ID
    vhost_body = next(b["body"] for b in captured_bodies if "support_virtual_host" in b["path"])
    assert vhost_body["customizedRegion"] == "us-east-1"
    catalog_body = next(b["body"] for b in captured_bodies if "storage_connection/remote" in b["path"])
    assert catalog_body["customizedRegion"] == "us-east-1"
    assert catalog_body["supportVirtualHost"] is True
    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "COMPATIBLE_S3"
    assert add_body["displayName"] == "tiering-remote"
    assert add_body["vaultName"] == "tiering-remote"
    assert add_body["customizedRegion"] == "us-east-1"
    assert add_body["supportVirtualHost"] is True
    assert "certificate" not in add_body


@pytest.mark.parametrize("trust_self_signed,cert_from_api,expected_certificate", [
    (True, "PEMDATA", "PEMDATA"),   # self-signed cert returned by API → included in add body
    (True, "", None),               # CA-signed endpoint (empty cert) → cert omitted
    (False, "PEMDATA", None),       # trust_self_signed=False → cert omitted even if API returns one
], ids=["self_signed_with_cert", "ca_endpoint_omits_cert", "no_trust_self_signed"])
async def test_add_s3_trust_self_signed(
    trust_self_signed: bool, cert_from_api: str, expected_certificate: str | None
) -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": cert_from_api}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": []}
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
            trust_self_signed=trust_self_signed,
        )
        await collection.add(req)

    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    if expected_certificate is not None:
        assert add_body["certificate"] == expected_certificate
    else:
        assert "certificate" not in add_body


async def test_add_s3_relink_key_sent_in_body() -> None:
    """relink_encryption_key is forwarded as storageEncryptionKey in the add body."""
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": []}
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": "new-key"}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
            relink_encryption_key="old-key",
        )
        await collection.add(req)

    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageEncryptionKey"] == "old-key"


@pytest.mark.parametrize("raw_key,expected_key", [
    ("abc", "abc"),   # non-empty encryptionKey in response → returned as-is
    ("", None),       # empty encryptionKey in response → mapped to None
], ids=["encryption_key_present", "encryption_key_empty_is_none"])
async def test_add_s3_encryption_key(raw_key: str, expected_key: str | None) -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": []}
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": raw_key}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
        )
        result = await collection.add(req)

    assert result.encryption_key == expected_key


async def test_add_s3_conflict_raises() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": []}
        if path == "/api/v1/external_storage":
            raise APIError("conflict", error_code=3004)
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
        )
        with pytest.raises(RemoteStorageConflictError) as exc_info:
            await collection.add(req)

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id="tiering-remote")


async def test_add_s3_encryption_mismatch_raises() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": []}
        if path == "/api/v1/external_storage":
            raise APIError("encryption mismatch", error_code=3006)
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
        )
        with pytest.raises(RemoteStorageEncryptionMismatchError) as exc_info:
            await collection.add(req)

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id="tiering-remote")


async def test_add_s3_other_error_reraises() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": []}
        if path == "/api/v1/external_storage":
            raise APIError("auth failed", error_code=3001)
        return {}

    with patched_session(session, post=fake_post):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
        )
        with pytest.raises(APIError) as exc_info:
            await collection.add(req)

    assert exc_info.value.error_code == 3001


async def test_add_s3_unmanaged_catalogs_no_plan_raises() -> None:
    session = make_session()
    add_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal add_called
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            add_called = True
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    with patched_session(session, post=fake_post):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
            unmanaged_retirement_plan=None,
        )
        with pytest.raises(RemoteStorageUnmanagedCatalogError) as exc_info:
            await collection.add(req)

    assert exc_info.value.catalog_count == 1
    assert not add_called


async def test_add_s3_unmanaged_catalogs_with_plan_calls_batch_relink() -> None:
    session = make_session()
    batch_relink_body: dict[str, Any] | None = None

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal batch_relink_body
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        if "batch_relink" in path:
            batch_relink_body = json
            return {}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
            unmanaged_retirement_plan=SAMPLE_RETIREMENT_PLAN,
        )
        await collection.add(req)

    assert batch_relink_body is not None
    assert batch_relink_body["storageUuid"] == STORAGE_ID
    assert batch_relink_body["items"][0]["archivePlanUuid"] == "plan-uuid"


async def test_add_s3_batch_relink_failure_sets_relink_warning() -> None:
    """When batch_relink raises, add() returns success with relink_warning set."""
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        if "batch_relink" in path:
            raise APIError("relink failed", error_code=500)
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
            unmanaged_retirement_plan=SAMPLE_RETIREMENT_PLAN,
        )
        result = await collection.add(req)

    assert result.storage.storage_id == STORAGE_ID
    assert result.relink_warning is not None
    assert "relink failed" in result.relink_warning


async def test_add_s3_no_catalogs_skips_batch_relink() -> None:
    session = make_session()
    batch_relink_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal batch_relink_called
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
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
        req = GenericS3StorageAddRequest(
            access_key="ak", secret_key="sk",
            vault_name="tiering-remote", endpoint="https://s3.example.com:443",
        )
        await collection.add(req)

    assert not batch_relink_called


# ── add(): APV ─────────────────────────────────────────────────────────────


async def test_add_apv_trust_self_signed_fetches_cert_and_info() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "/cert" in path:
            return {"certificate": {"cert": "PEM"}}
        if "aev/info" in path:
            return SAMPLE_APV_INFO_RAW
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = APVStorageAddRequest(
            access_key="ak", secret_key="sk",
            endpoint="apv.example.com:5888",
            trust_self_signed=True,
        )
        await collection.add(req)

    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "AEV"
    assert add_body["vaultName"] == "my-bucket"
    assert add_body["displayName"] == "APV Vault"
    assert add_body["certificate"] == "PEM"


async def test_add_apv_no_trust_self_signed_skips_cert_fetch() -> None:
    session = make_session()
    cert_calls: list[str] = []
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "/cert" in path:
            cert_calls.append(path)
            return {"certificate": {"cert": "PEM"}}
        if "aev/info" in path:
            return SAMPLE_APV_INFO_RAW
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = APVStorageAddRequest(
            access_key="ak", secret_key="sk",
            endpoint="apv.example.com:5888",
            trust_self_signed=False,
        )
        await collection.add(req)

    assert not cert_calls
    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert "certificate" not in add_body


async def test_add_apv_omits_region_and_virtual_host() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "aev/info" in path:
            return SAMPLE_APV_INFO_RAW
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = APVStorageAddRequest(
            access_key="ak", secret_key="sk",
            endpoint="apv.example.com:5888",
        )
        await collection.add(req)

    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert "customizedRegion" not in add_body
    assert "supportVirtualHost" not in add_body


async def test_add_apv_unmanaged_catalogs_no_plan_raises() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "/cert" in path:
            return {"certificate": {"cert": "PEM"}}
        if "aev/info" in path:
            return SAMPLE_APV_INFO_RAW
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        return {}

    with patched_session(session, post=fake_post):
        collection = RemoteStorageCollection(session)
        req = APVStorageAddRequest(
            access_key="ak", secret_key="sk",
            endpoint="apv.example.com:5888",
            trust_self_signed=True,
            unmanaged_retirement_plan=None,
        )
        with pytest.raises(RemoteStorageUnmanagedCatalogError):
            await collection.add(req)


async def test_add_apv_unmanaged_catalogs_with_plan_calls_batch_relink() -> None:
    session = make_session()
    batch_relink_body: dict[str, Any] | None = None

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal batch_relink_body
        if "/cert" in path:
            return {"certificate": {"cert": ""}}
        if "aev/info" in path:
            return SAMPLE_APV_INFO_RAW
        if "storage_connection/remote" in path:
            return SAMPLE_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        if "batch_relink" in path:
            batch_relink_body = json
            return {}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = APVStorageAddRequest(
            access_key="ak", secret_key="sk",
            endpoint="apv.example.com:5888",
            unmanaged_retirement_plan=SAMPLE_RETIREMENT_PLAN,
        )
        await collection.add(req)

    assert batch_relink_body is not None
    assert batch_relink_body["items"][0]["archivePlanUuid"] == "plan-uuid"


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


# ── add(): AmazonS3StorageAddRequest / AmazonS3ChinaStorageAddRequest ───────


async def test_add_amazon_s3_posts_correct_body() -> None:
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
            return {}
        if "support_virtual_host" in path:
            vhost_called = True
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
        req = AmazonS3StorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="my-bucket",
        )
        result = await collection.add(req)

    assert not region_cert_called
    assert not vhost_called
    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "AWS_S3"
    assert add_body["endpoint"] == ""
    assert add_body["displayName"] == "my-bucket"
    assert add_body["vaultName"] == "my-bucket"
    assert result.storage.storage_id == STORAGE_ID


async def test_add_amazon_s3_china_posts_correct_body() -> None:
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
        req = AmazonS3ChinaStorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="my-bucket",
        )
        await collection.add(req)

    add_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage")
    assert add_body["storageType"] == "AWS_S3_CHINA"
    assert add_body["endpoint"] == ""


async def test_add_amazon_s3_catalog_check_called() -> None:
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
        req = AmazonS3StorageAddRequest(access_key="ak", secret_key="sk", vault_name="my-bucket")
        await collection.add(req)

    assert catalog_body_captured is not None
    assert catalog_body_captured["storageType"] == "AWS_S3"
    assert catalog_body_captured["endpoint"] == ""


async def test_add_amazon_s3_encryption_returns_key() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": "abc"}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = AmazonS3StorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="my-bucket", encryption_enabled=True,
        )
        result = await collection.add(req)

    assert result.encryption_key == "abc"


async def test_add_amazon_s3_no_encryption_key_is_none() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            return {"id": STORAGE_ID, "encryptionKey": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = AmazonS3StorageAddRequest(access_key="ak", secret_key="sk", vault_name="my-bucket")
        result = await collection.add(req)

    assert result.encryption_key is None


async def test_add_amazon_s3_conflict_raises() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        if path == "/api/v1/external_storage":
            raise APIError("conflict", error_code=3004)
        return {}

    with patched_session(session, post=fake_post):
        collection = RemoteStorageCollection(session)
        req = AmazonS3StorageAddRequest(access_key="ak", secret_key="sk", vault_name="my-bucket")
        with pytest.raises(RemoteStorageConflictError) as exc_info:
            await collection.add(req)

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id="my-bucket")


async def test_add_amazon_s3_unmanaged_catalogs_no_plan_raises() -> None:
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
        req = AmazonS3StorageAddRequest(access_key="ak", secret_key="sk", vault_name="my-bucket")
        with pytest.raises(RemoteStorageUnmanagedCatalogError):
            await collection.add(req)

    assert not add_called


async def test_add_amazon_s3_unmanaged_catalogs_with_plan_relinks() -> None:
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
        req = AmazonS3StorageAddRequest(
            access_key="ak", secret_key="sk", vault_name="my-bucket",
            unmanaged_retirement_plan=SAMPLE_RETIREMENT_PLAN,
        )
        await collection.add(req)

    assert batch_relink_called


# ── update() ───────────────────────────────────────────────────────────────


def _make_storage(storage_type: RemoteStorageType) -> RemoteStorage:
    return RemoteStorage(
        storage_id=STORAGE_ID,
        name="DSM-Storage",
        storage_type=storage_type,
        device_model="",
        endpoint="192.0.2.20:8444",
        status=RemoteStorageStatus.CONNECTED,
        used_bytes=None,
        remaining_bytes=None,
    )


async def test_update_no_trust_self_signed_posts_minimal_body() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []
    region_cert_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal region_cert_called
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            region_cert_called = True
            return {}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    storage = _make_storage(RemoteStorageType.S3_COMPATIBLE)
    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = RemoteStorageUpdateRequest(
            access_key="new-ak", secret_key="new-sk",
            endpoint="https://s3.example.com:443",
            trust_self_signed=False,
        )
        result = await collection.update(storage, req)

    assert not region_cert_called
    update_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage/update")
    assert update_body == {
        "id": STORAGE_ID,
        "accessKey": "new-ak",
        "secretKey": "new-sk",
        "endpoint": "https://s3.example.com:443",
    }
    assert "certificate" not in update_body
    assert "displayName" not in update_body
    assert result.storage_id == STORAGE_ID


async def test_update_s3_trust_self_signed_includes_cert() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            return {"cert": "PEM", "region": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    storage = _make_storage(RemoteStorageType.S3_COMPATIBLE)
    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = RemoteStorageUpdateRequest(
            access_key="new-ak", secret_key="new-sk",
            endpoint="https://s3.example.com:443",
            trust_self_signed=True,
        )
        await collection.update(storage, req)

    update_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage/update")
    assert update_body["certificate"] == "PEM"


async def test_update_s3_trust_self_signed_ca_endpoint_omits_cert() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            return {"cert": "", "region": ""}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    storage = _make_storage(RemoteStorageType.S3_COMPATIBLE)
    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = RemoteStorageUpdateRequest(
            access_key="new-ak", secret_key="new-sk",
            endpoint="https://s3.example.com:443",
            trust_self_signed=True,
        )
        await collection.update(storage, req)

    update_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage/update")
    assert "certificate" not in update_body


async def test_update_apv_trust_self_signed_fetches_cert() -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "/cert" in path:
            return {"certificate": {"cert": "PEM"}}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    storage = _make_storage(RemoteStorageType.ACTIVE_PROTECT_VAULT)
    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = RemoteStorageUpdateRequest(
            access_key="new-ak", secret_key="new-sk",
            endpoint="apv.example.com:5888",
            trust_self_signed=True,
        )
        await collection.update(storage, req)

    update_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage/update")
    assert update_body["certificate"] == "PEM"
    assert "displayName" not in update_body
    assert "storageType" not in update_body
    assert "vaultName" not in update_body


async def test_update_apv_no_trust_self_signed_skips_cert_fetch() -> None:
    session = make_session()
    cert_calls: list[str] = []
    captured_bodies: list[dict[str, Any]] = []

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "/cert" in path:
            cert_calls.append(path)
            return {"certificate": {"cert": "PEM"}}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    storage = _make_storage(RemoteStorageType.ACTIVE_PROTECT_VAULT)
    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = RemoteStorageUpdateRequest(
            access_key="new-ak", secret_key="new-sk",
            endpoint="apv.example.com:5888",
            trust_self_signed=False,
        )
        await collection.update(storage, req)

    assert not cert_calls
    update_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage/update")
    assert "certificate" not in update_body


@pytest.mark.parametrize("storage_type,api_storage_type", [
    (RemoteStorageType.AMAZON_S3, "AMAZON_S3"),
    (RemoteStorageType.AMAZON_S3_CHINA, "AMAZON_S3_CHINA"),
    (RemoteStorageType.C2_OBJECT_STORAGE, "C2_OBJECT_STORAGE"),
    (RemoteStorageType.WASABI, "WASABI"),
])
async def test_update_endpoint_free_omits_endpoint(storage_type: RemoteStorageType, api_storage_type: str) -> None:
    session = make_session()
    captured_bodies: list[dict[str, Any]] = []
    region_cert_called = False
    cert_called = False

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal region_cert_called, cert_called
        if json is not None:
            captured_bodies.append({"path": path, "body": json})
        if "region_cert" in path:
            region_cert_called = True
            return {}
        if "/cert" in path:
            cert_called = True
            return {}
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    storage = _make_storage(storage_type)
    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = RemoteStorageUpdateRequest(access_key="new-ak", secret_key="new-sk")
        await collection.update(storage, req)

    assert not region_cert_called
    assert not cert_called
    update_body = next(b["body"] for b in captured_bodies if b["path"] == "/api/v1/external_storage/update")
    assert update_body == {"id": STORAGE_ID, "accessKey": "new-ak", "secretKey": "new-sk"}
    assert "endpoint" not in update_body
    assert "certificate" not in update_body


async def test_update_returns_refreshed_storage() -> None:
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        return {}

    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return SAMPLE_STORAGE_RAW

    storage = _make_storage(RemoteStorageType.S3_COMPATIBLE)
    with patched_session(session, post=fake_post, get=fake_get):
        collection = RemoteStorageCollection(session)
        req = RemoteStorageUpdateRequest(
            access_key="new-ak", secret_key="new-sk", endpoint="https://s3.example.com:443"
        )
        result = await collection.update(storage, req)

    assert result.storage_id == STORAGE_ID
    assert result.name == "DSM-Storage"


# ── delete() ───────────────────────────────────────────────────────────────


async def test_delete_calls_correct_endpoint() -> None:
    session = make_session()
    delete_calls: list[str] = []

    async def fake_delete(path: str, **kw: Any) -> dict[str, Any]:
        delete_calls.append(path)
        return {}

    storage = _make_storage(RemoteStorageType.ACTIVE_PROTECT_VAULT)
    with patched_session(session, delete=fake_delete):
        collection = RemoteStorageCollection(session)
        await collection.delete(storage)

    assert delete_calls == [f"/api/v1/external_storage/{STORAGE_ID}"]


async def test_delete_not_found_propagates() -> None:
    session = make_session()

    async def fake_delete(path: str, **kw: Any) -> dict[str, Any]:
        raise ResourceNotFoundError("not found", resource_type="RemoteStorage", resource_id=STORAGE_ID)

    storage = _make_storage(RemoteStorageType.ACTIVE_PROTECT_VAULT)
    with patched_session(session, delete=fake_delete):
        collection = RemoteStorageCollection(session)
        with pytest.raises(ResourceNotFoundError):
            await collection.delete(storage)


async def test_delete_in_use_raises() -> None:
    session = make_session()

    async def fake_delete(path: str, **kw: Any) -> dict[str, Any]:
        raise APIError("in use", error_code=3014)

    storage = _make_storage(RemoteStorageType.ACTIVE_PROTECT_VAULT)
    with patched_session(session, delete=fake_delete):
        collection = RemoteStorageCollection(session)
        with pytest.raises(RemoteStorageInUseError) as exc_info:
            await collection.delete(storage)

    assert_resource_error(exc_info, resource_type="RemoteStorage", resource_id=STORAGE_ID)


async def test_delete_other_error_reraises() -> None:
    session = make_session()

    async def fake_delete(path: str, **kw: Any) -> dict[str, Any]:
        raise APIError("unexpected", error_code=3013)

    storage = _make_storage(RemoteStorageType.ACTIVE_PROTECT_VAULT)
    with patched_session(session, delete=fake_delete):
        collection = RemoteStorageCollection(session)
        with pytest.raises(APIError) as exc_info:
            await collection.delete(storage)

    assert exc_info.value.error_code == 3013


async def test_add_apv_missing_server_or_vault_name_raises_api_error() -> None:
    """add() raises APIError when the APV info endpoint omits serverName/vaultName."""
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "aev/info" in path:
            return {"serverName": "", "vaultName": ""}
        if "storage_connection/remote" in path:
            return EMPTY_CONNECTIONS_RAW
        return {}

    with patched_session(session, post=fake_post):
        collection = RemoteStorageCollection(session)
        req = APVStorageAddRequest(
            access_key="ak", secret_key="sk",
            endpoint="apv.example.com:5888",
        )
        with pytest.raises(APIError, match="did not return the expected server name"):
            await collection.add(req)
