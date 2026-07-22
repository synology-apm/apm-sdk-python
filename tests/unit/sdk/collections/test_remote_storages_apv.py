"""Unit tests for RemoteStorageCollection.add(): APVStorageAddRequest (ActiveProtect Vault)."""
from __future__ import annotations

from typing import Any

import pytest

from synology_apm.sdk.collections.remote_storages import RemoteStorageCollection
from synology_apm.sdk.exceptions import APIError, RemoteStorageUnmanagedCatalogError
from synology_apm.sdk.models.remote_storage import APVStorageAddRequest
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

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="plan-uuid",
    name="Daily Backup",
    description="",
    retention=None,
    workload_count=0,
    run_schedule_by_controller_time=False,
)


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
