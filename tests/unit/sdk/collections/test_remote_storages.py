"""Unit tests for RemoteStorageCollection: list()/get()/get_by_name()/update()/delete(), and add() for S3-Compatible storage."""
from __future__ import annotations

from typing import Any

import pytest

from synology_apm.sdk.collections.remote_storages import (
    RemoteStorageCollection,
    _fetch_s3_cert_and_region,
    _parse_remote_storage,
)
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
    GenericS3StorageAddRequest,
    RemoteStorage,
    RemoteStorageUpdateRequest,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
    make_session,
    null_out,
    patched_session,
)

LIST_URL = f"{BASE_URL}/api/v1/external_storage/detail"
STORAGE_ID = "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"
GET_URL = f"{BASE_URL}/api/v1/external_storage/{STORAGE_ID}"

ADD_URL = f"{BASE_URL}/api/v1/external_storage"
REGION_CERT_URL = f"{BASE_URL}/api/v1/external_storage/compatable_s3/region_cert"
VHOST_URL = f"{BASE_URL}/api/v1/external_storage/bucket/support_virtual_host"
CATALOG_CHECK_URL = f"{BASE_URL}/api/v1/storage_connection/remote"

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


async def test_list_survives_null_storages_key() -> None:
    """storages JSON null (key present, value null — distinct from an absent key) must
    not crash list(); it is treated as an empty page instead of raising."""
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": None})
        collection = RemoteStorageCollection(session)
        remote_storages, total = await collection.list()
        await session.disconnect()

    assert remote_storages == []
    assert total == 0


def test_parse_remote_storage_survives_null_fields() -> None:
    """Every field _parse_remote_storage() touches with `or`/`.get(..., default)` defaults,
    as JSON null, must not crash it and must fall back to its documented safe default:
    string fields to "", enum fields to UNKNOWN, and encryption_enabled to False. Called
    directly (not through list()/get()) since it's a standalone parser function."""
    raw = null_out(
        SAMPLE_STORAGE_RAW,
        "id", "displayName", "storageType", "modelName", "endpoint",
        "connectionStatus", "isEncryption", "vaultName",
    )

    storage = _parse_remote_storage(raw)

    assert storage.storage_id == ""
    assert storage.name == ""
    assert storage.storage_type == RemoteStorageType.UNKNOWN
    assert storage.device_model == ""
    assert storage.endpoint == ""
    assert storage.status == RemoteStorageStatus.UNKNOWN
    assert storage.encryption_enabled is False
    assert storage.vault_name == ""


async def test_fetch_s3_cert_and_region_survives_null_fields() -> None:
    """cert/region JSON null in the region_cert response must not crash
    _fetch_s3_cert_and_region(); both fall back to empty strings. Called directly since
    it's a standalone helper, not a RemoteStorageCollection method."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"cert": None, "region": None}
        cert, region = await _fetch_s3_cert_and_region(
            session, "https://s3.example.com:443", "ak", "sk"
        )

    assert cert == ""
    assert region == ""


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


@pytest.mark.parametrize("name", ["DSM-Storage", "DSM-STORAGE"], ids=["display_name", "case_insensitive"])
async def test_get_by_name_matches_display_name(name: str) -> None:
    """get_by_name() should match by display name, case-insensitively."""
    async with connected_session() as (session, m):
        m.get(LIST_URL, payload={"storages": [SAMPLE_STORAGE_RAW]})
        collection = RemoteStorageCollection(session)
        s = await collection.get_by_name(name)
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
    (None, None),     # encryptionKey JSON null (key present, value null) → mapped to None
], ids=["encryption_key_present", "encryption_key_empty_is_none", "encryption_key_null_is_none"])
async def test_add_s3_encryption_key(raw_key: str | None, expected_key: str | None) -> None:
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


async def test_add_s3_survives_null_id_in_batch_relink_storage_uuid() -> None:
    """id JSON null (key present, value null — distinct from an absent key) in the
    /api/v1/external_storage response must not crash add(); the batch_relink call's
    storageUuid falls back to an empty string instead of raising."""
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
            return {"id": None, "encryptionKey": ""}
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
    assert batch_relink_body["storageUuid"] == ""


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


async def test_add_s3_survives_null_connections_key() -> None:
    """connections JSON null (key present, value null — distinct from an absent key) in
    the catalog-check response must not crash add(); it is treated as no unmanaged
    catalogs (same as an empty list), so add() succeeds without raising."""
    session = make_session()

    async def fake_post(path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        if "region_cert" in path:
            return {"region": "us-east-1", "cert": ""}
        if "support_virtual_host" in path:
            return {"supportVirtualHost": True}
        if "storage_connection/remote" in path:
            return {"connections": None}
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
