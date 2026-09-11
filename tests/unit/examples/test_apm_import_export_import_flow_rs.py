"""Unit tests for the import pipeline in examples/apm_import_export.py.

Covers the remote-storage import subtopic: create/overwrite action selection, per-item
execution against the SDK, YAML entry parsing, and per-storage-type add-request building.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import apm_import_export as ie
import pytest

from synology_apm.sdk import (
    AccessKeyStorageUpdateRequest,
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APMError,
    APVStorageAddRequest,
    AzureBlobChinaStorageAddRequest,
    AzureBlobStorageAddRequest,
    AzureBlobStorageUpdateRequest,
    C2ObjectStorageAddRequest,
    GenericS3StorageAddRequest,
    RemoteStorageAddResult,
    RemoteStorageConflictError,
    RemoteStorageInUseError,
    RemoteStorageType,
    RemoteStorageUnmanagedCatalogError,
    WasabiCloudStorageAddRequest,
)
from tests.unit.examples._fixtures import make_fake_apm, make_remote_storage

# ── _select_rs_actions ────────────────────────────────────────────────────────


def _make_rs_entry(
    *,
    name_or_id: str = "tiering-remote",
    ref_key: str = "storage-1",
    endpoint: str = "https://s3.example.com:443",
    vault_name: str = "my-bucket",
    storage_type_str: str = "s3_compatible",
    raw: dict[str, Any] | None = None,
    parse_error: str | None = None,
) -> ie._RsEntry:
    return ie._RsEntry(
        name_or_id=name_or_id,
        ref_key=ref_key,
        endpoint=endpoint,
        vault_name=vault_name,
        storage_type_str=storage_type_str,
        raw=raw if raw is not None else {"trust_self_signed": True},
        parse_error=parse_error,
    )


_RS_CREDS: dict[tuple[str, str, str], dict[str, str]] = {
    ("s3_compatible", "https://s3.example.com:443", "my-bucket"): {
        "access_key": "AK",
        "secret_key": "SK",
        "relink_encryption_key": "RK",
    },
}


def test_select_rs_actions_create_builds_add_request() -> None:
    rse = _make_rs_entry()

    actions = ie._select_rs_actions([rse], _RS_CREDS, "skip", {}, {})

    assert actions == {ie._rs_key(rse): "create"}
    assert rse.request == GenericS3StorageAddRequest(
        access_key="AK",
        secret_key="SK",
        vault_name="my-bucket",
        endpoint="https://s3.example.com:443",
        encryption_enabled=False,
        relink_encryption_key="RK",
        trust_self_signed=True,
    )


def test_select_rs_actions_existing_name_overwrite_builds_update_request() -> None:
    rse = _make_rs_entry()
    existing = make_remote_storage(name="tiering-remote")

    actions = ie._select_rs_actions(
        [rse], _RS_CREDS, "overwrite", {}, {"tiering-remote": existing}
    )

    assert actions == {ie._rs_key(rse): "overwrite"}
    assert rse.request == AccessKeyStorageUpdateRequest(
        access_key="AK",
        secret_key="SK",
        endpoint="https://s3.example.com:443",
        trust_self_signed=True,
    )


def test_select_rs_actions_azure_create_builds_azure_add_request() -> None:
    rse = _make_rs_entry(
        endpoint="", vault_name="my-container", storage_type_str="azure_blob",
        raw={"account_name": "acct-1"},
    )
    creds = {
        ("azure_blob", "", "my-container"): {
            "tenant_id": "tenant-1", "client_id": "client-1", "secret": "secret-1",
        },
    }

    actions = ie._select_rs_actions([rse], creds, "skip", {}, {})

    assert actions == {ie._rs_key(rse): "create"}
    assert rse.request == AzureBlobStorageAddRequest(
        tenant_id="tenant-1", client_id="client-1", secret="secret-1",
        account_name="acct-1", vault_name="my-container",
    )


def test_select_rs_actions_azure_overwrite_builds_azure_update_request() -> None:
    rse = _make_rs_entry(
        endpoint="", vault_name="my-container", storage_type_str="azure_blob",
        raw={"account_name": "acct-1"},
    )
    creds = {
        ("azure_blob", "", "my-container"): {
            "tenant_id": "tenant-1", "client_id": "client-1", "secret": "secret-1",
        },
    }
    existing = make_remote_storage(name="tiering-remote", storage_type=RemoteStorageType.AZURE_BLOB)

    actions = ie._select_rs_actions([rse], creds, "overwrite", {}, {"tiering-remote": existing})

    assert actions == {ie._rs_key(rse): "overwrite"}
    assert rse.request == AzureBlobStorageUpdateRequest(
        tenant_id="tenant-1", client_id="client-1", secret="secret-1",
    )


def test_select_rs_actions_existing_uuid_skip_leaves_request_unbuilt() -> None:
    storage_id = "123e4567-e89b-12d3-a456-426614174030"
    rse = _make_rs_entry(name_or_id=storage_id)
    existing = make_remote_storage(storage_id=storage_id)

    actions = ie._select_rs_actions([rse], _RS_CREDS, "skip", {storage_id: existing}, {})

    assert actions == {ie._rs_key(rse): "skip"}
    assert rse.request is None


def test_select_rs_actions_parse_error_maps_to_error() -> None:
    rse = _make_rs_entry(parse_error="unrecognized storage_type 'tape'")

    actions = ie._select_rs_actions([rse], _RS_CREDS, "skip", {}, {})

    assert actions == {ie._rs_key(rse): "error"}
    assert rse.request is None


def test_select_rs_actions_build_request_failure_maps_to_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ValueError/KeyError raised while building the add request is caught and recorded
    as a parse error rather than propagating — the create-path counterpart to the parse-time
    'unrecognized storage_type' error above."""
    rse = _make_rs_entry()

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("boom")

    monkeypatch.setattr(ie, "_build_rs_add_request", _boom)

    actions = ie._select_rs_actions([rse], _RS_CREDS, "skip", {}, {})

    assert actions == {ie._rs_key(rse): "error"}
    assert rse.parse_error == "boom"
    assert rse.request is None


# ── _execute_one_rs ───────────────────────────────────────────────────────────


def _rs_add_req() -> GenericS3StorageAddRequest:
    return GenericS3StorageAddRequest(
        access_key="AK", secret_key="SK",
        vault_name="my-bucket", endpoint="https://s3.example.com:443",
    )


async def test_execute_one_rs_create_returns_key_and_storage() -> None:
    created = make_remote_storage()
    apm = make_fake_apm()
    apm.remote_storages.add = AsyncMock(
        return_value=RemoteStorageAddResult(
            storage=created, encryption_key="NEWKEY123", relink_warning="relink pending"
        )
    )
    entry = _make_rs_entry()
    entry.request = _rs_add_req()

    result = await ie._execute_one_rs(apm, entry, "create", None)

    assert (result.action, result.result) == ("create", "ok")
    assert result.error_msg == "relink pending"
    assert result.issued_encryption_key == "NEWKEY123"
    assert result.created_storage is created
    apm.remote_storages.add.assert_awaited_once_with(entry.request)


async def test_execute_one_rs_overwrite_calls_update() -> None:
    existing = make_remote_storage()
    apm = make_fake_apm()
    apm.remote_storages.update = AsyncMock()
    entry = _make_rs_entry()
    entry.request = AccessKeyStorageUpdateRequest(access_key="AK", secret_key="SK")

    result = await ie._execute_one_rs(apm, entry, "overwrite", existing)

    assert (result.result, result.error_msg) == ("ok", "")
    apm.remote_storages.update.assert_awaited_once_with(existing, entry.request)


async def test_execute_one_rs_create_accepts_azure_request() -> None:
    created = make_remote_storage(storage_type=RemoteStorageType.AZURE_BLOB)
    apm = make_fake_apm()
    apm.remote_storages.add = AsyncMock(
        return_value=RemoteStorageAddResult(storage=created, encryption_key=None)
    )
    entry = _make_rs_entry()
    entry.request = AzureBlobStorageAddRequest(
        tenant_id="tenant-1", client_id="client-1", secret="secret-1",
        account_name="acct-1", vault_name="my-container",
    )

    result = await ie._execute_one_rs(apm, entry, "create", None)

    assert (result.action, result.result) == ("create", "ok")
    apm.remote_storages.add.assert_awaited_once_with(entry.request)


async def test_execute_one_rs_overwrite_accepts_azure_request() -> None:
    existing = make_remote_storage(storage_type=RemoteStorageType.AZURE_BLOB)
    apm = make_fake_apm()
    apm.remote_storages.update = AsyncMock()
    entry = _make_rs_entry()
    entry.request = AzureBlobStorageUpdateRequest(
        tenant_id="tenant-1", client_id="client-1", secret="secret-1",
    )

    result = await ie._execute_one_rs(apm, entry, "overwrite", existing)

    assert (result.result, result.error_msg) == ("ok", "")
    apm.remote_storages.update.assert_awaited_once_with(existing, entry.request)


async def test_execute_one_rs_overwrite_type_mismatch_fails_without_crashing() -> None:
    """update() raising ValueError (request type doesn't match the matched storage's real
    type — e.g. a stale YAML entry) must be recorded as a per-item failure, not propagate and
    abort the whole batch."""
    existing = make_remote_storage()
    apm = make_fake_apm()
    apm.remote_storages.update = AsyncMock(
        side_effect=ValueError("Cannot update s3_compatible storage 'DSM-Storage' with a "
                                "AzureBlobStorageUpdateRequest; use AccessKeyStorageUpdateRequest instead.")
    )
    entry = _make_rs_entry()
    entry.request = AzureBlobStorageUpdateRequest(tenant_id="t", client_id="c", secret="s")

    result = await ie._execute_one_rs(apm, entry, "overwrite", existing)

    assert result.result == "failed"
    assert "Cannot update s3_compatible storage" in result.error_msg


async def test_execute_one_rs_wrong_request_types_fail() -> None:
    apm = make_fake_apm()
    entry_create = _make_rs_entry()
    entry_create.request = AccessKeyStorageUpdateRequest(access_key="AK", secret_key="SK")
    entry_update = _make_rs_entry()
    entry_update.request = _rs_add_req()

    res_create = await ie._execute_one_rs(apm, entry_create, "create", None)
    res_update = await ie._execute_one_rs(
        apm, entry_update, "overwrite", make_remote_storage()
    )

    assert res_create.error_msg == "internal error: wrong request type for create"
    assert res_update.error_msg == "internal error: wrong request type for update"


async def test_execute_one_rs_no_request_built_fails() -> None:
    apm = make_fake_apm()
    entry = _make_rs_entry()
    assert entry.request is None

    result = await ie._execute_one_rs(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("failed", "no request built")


async def test_execute_one_rs_overwrite_without_existing_storage_fails() -> None:
    apm = make_fake_apm()
    entry = _make_rs_entry()
    entry.request = AccessKeyStorageUpdateRequest(access_key="AK", secret_key="SK")

    result = await ie._execute_one_rs(apm, entry, "overwrite", None)

    assert (result.result, result.error_msg) == ("failed", "existing storage not found")


@pytest.mark.parametrize(
    ("exc", "expected_msg"),
    [
        (
            RemoteStorageConflictError(
                "vault registered", resource_type="RemoteStorage", resource_id="my-bucket"
            ),
            "conflict: vault registered",
        ),
        (
            RemoteStorageInUseError(
                "assigned to plans", resource_type="RemoteStorage",
                resource_id="123e4567-e89b-12d3-a456-426614174030",
            ),
            "in use: assigned to plans",
        ),
        (
            RemoteStorageUnmanagedCatalogError(
                "unmanaged catalogs", vault_name="my-bucket", catalog_count=3
            ),
            "unmanaged catalogs (3) in vault 'my-bucket'; "
            "re-add manually via the SDK and pass unmanaged_retirement_plan",
        ),
        (APMError("backend busy"), "backend busy"),
    ],
    ids=["conflict", "in-use", "unmanaged-catalog", "apm-error"],
)
async def test_execute_one_rs_error_mapping(exc: APMError, expected_msg: str) -> None:
    apm = make_fake_apm()
    apm.remote_storages.add = AsyncMock(side_effect=exc)
    entry = _make_rs_entry()
    entry.request = _rs_add_req()

    result = await ie._execute_one_rs(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("failed", expected_msg)


async def test_execute_one_rs_error_and_skip_actions() -> None:
    apm = make_fake_apm()
    entry_err = _make_rs_entry(parse_error="unrecognized storage_type 'tape'")
    entry_skip = _make_rs_entry()

    res_err = await ie._execute_one_rs(apm, entry_err, "error", None)
    res_skip = await ie._execute_one_rs(apm, entry_skip, "skip", None)

    assert (res_err.result, res_err.error_msg) == ("failed", "unrecognized storage_type 'tape'")
    assert (res_skip.result, res_skip.error_msg) == ("skipped", "")


# ── _parse_rs_entries ─────────────────────────────────────────────────────────


def _rs_yaml_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ref_key": "storage-1",
        "name_or_id": "tiering-remote",
        "endpoint": "https://s3.example.com:443",
        "storage_type": "s3_compatible",
        "encryption_enabled": False,
        "vault_name": "my-bucket",
        "trust_self_signed": True,
    }
    entry.update(overrides)
    return entry


def test_parse_rs_entries_happy_path() -> None:
    data = {"remote_storages": [_rs_yaml_entry()]}

    entries = ie._parse_rs_entries(data, _RS_CREDS)

    assert len(entries) == 1
    rse = entries[0]
    assert rse.parse_error is None
    assert rse.name_or_id == "tiering-remote"
    assert rse.ref_key == "storage-1"
    assert rse.endpoint == "https://s3.example.com:443"
    assert rse.vault_name == "my-bucket"
    assert rse.storage_type_str == "s3_compatible"


def test_parse_rs_entries_no_credential_row_skips_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = {"remote_storages": [_rs_yaml_entry(vault_name="other-bucket")]}

    entries = ie._parse_rs_entries(data, _RS_CREDS)

    assert entries == []
    err = capsys.readouterr().err
    assert "has no matching row in storage-credentials file — skipping" in err


def test_parse_rs_entries_unknown_storage_type_is_parse_error() -> None:
    creds = {("tape", "https://s3.example.com:443", "my-bucket"): {
        "access_key": "AK", "secret_key": "SK", "relink_encryption_key": "",
    }}
    data = {"remote_storages": [_rs_yaml_entry(storage_type="tape")]}

    entries = ie._parse_rs_entries(data, creds)

    assert len(entries) == 1
    assert entries[0].parse_error == "unrecognized storage_type 'tape'"


def test_parse_rs_entries_non_importable_type_skips_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    creds = {("unknown", "https://s3.example.com:443", "my-bucket"): {
        "access_key": "AK", "secret_key": "SK", "relink_encryption_key": "",
    }}
    data = {"remote_storages": [_rs_yaml_entry(storage_type="unknown")]}

    entries = ie._parse_rs_entries(data, creds)

    assert entries == []
    err = capsys.readouterr().err
    assert "not supported for import, skipping" in err


# ── _build_rs_add_request ─────────────────────────────────────────────────────


_RS_REQ_KWARGS: dict[str, Any] = {
    "vault_name": "my-bucket",
    "endpoint": "https://s3.example.com:443",
    "access_key": "AK",
    "secret_key": "SK",
    "relink_key": "RK",
    "encryption_enabled": True,
    "trust_self_signed": True,
}


@pytest.mark.parametrize(
    ("storage_type", "expected"),
    [
        (
            RemoteStorageType.ACTIVE_PROTECT_VAULT,
            APVStorageAddRequest(
                access_key="AK", secret_key="SK",
                endpoint="https://s3.example.com:443",
                encryption_enabled=True, relink_encryption_key="RK",
                trust_self_signed=True,
            ),
        ),
        (
            RemoteStorageType.S3_COMPATIBLE,
            GenericS3StorageAddRequest(
                access_key="AK", secret_key="SK",
                vault_name="my-bucket", endpoint="https://s3.example.com:443",
                encryption_enabled=True, relink_encryption_key="RK",
                trust_self_signed=True,
            ),
        ),
        (
            RemoteStorageType.AMAZON_S3,
            AmazonS3StorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
        (
            RemoteStorageType.AMAZON_S3_CHINA,
            AmazonS3ChinaStorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
        (
            RemoteStorageType.C2_OBJECT_STORAGE,
            C2ObjectStorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
        (
            RemoteStorageType.WASABI,
            WasabiCloudStorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
    ],
    ids=["apv", "s3-compatible", "amazon-s3", "amazon-s3-china", "c2", "wasabi"],
)
def test_build_rs_add_request_dispatch(
    storage_type: RemoteStorageType, expected: Any
) -> None:
    """Each importable storage type builds its own request type with the right fields."""
    result = ie._build_rs_add_request(storage_type, **_RS_REQ_KWARGS)

    assert type(result) is type(expected)
    assert result == expected


@pytest.mark.parametrize(
    ("storage_type", "expected_cls"),
    [
        (RemoteStorageType.AZURE_BLOB, AzureBlobStorageAddRequest),
        (RemoteStorageType.AZURE_BLOB_CHINA, AzureBlobChinaStorageAddRequest),
    ],
    ids=["azure-blob", "azure-blob-china"],
)
def test_build_rs_add_request_dispatch_azure(
    storage_type: RemoteStorageType, expected_cls: type[Any]
) -> None:
    """Azure types build an Azure add request from tenant_id/client_id/azure_secret/account_name."""
    result = ie._build_rs_add_request(
        storage_type,
        vault_name="my-container", endpoint="", access_key="", secret_key="",
        relink_key="RK", encryption_enabled=True, trust_self_signed=False,
        tenant_id="tenant-1", client_id="client-1", azure_secret="secret-1", account_name="acct-1",
    )

    assert isinstance(result, expected_cls)
    assert result.tenant_id == "tenant-1"
    assert result.client_id == "client-1"
    assert result.secret == "secret-1"
    assert result.account_name == "acct-1"
    assert result.vault_name == "my-container"
    assert result.encryption_enabled is True
    assert result.relink_encryption_key == "RK"
