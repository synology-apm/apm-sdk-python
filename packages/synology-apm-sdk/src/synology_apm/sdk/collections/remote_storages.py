"""RemoteStorageCollection — collection interface for managing remote storage devices."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..enums import RemoteStorageStatus, RemoteStorageType
from ..exceptions import (
    APIError,
    RemoteStorageConflictError,
    RemoteStorageEncryptionMismatchError,
    RemoteStorageInUseError,
    RemoteStorageUnmanagedCatalogError,
    ResourceNotFoundError,
)
from ..models.remote_storage import (
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APVStorageAddRequest,
    C2ObjectStorageAddRequest,
    GenericS3StorageAddRequest,
    RemoteStorage,
    RemoteStorageAddResult,
    RemoteStorageUpdateRequest,
    WasabiCloudStorageAddRequest,
    _S3VendorStorageAddRequest,
)
from ._shared import ListResult, _not_found_as

_REMOTE_STORAGE_STATUS_MAP: dict[str, RemoteStorageStatus] = {
    "Connection":      RemoteStorageStatus.CONNECTED,
    "AuthFailed":      RemoteStorageStatus.AUTH_FAILED,
    "Disconnect":      RemoteStorageStatus.DISCONNECTED,
    "Unknown":         RemoteStorageStatus.UNKNOWN,
    "VaultNotMounted": RemoteStorageStatus.VAULT_NOT_MOUNTED,
    "DataCorrupted":   RemoteStorageStatus.DATA_CORRUPTED,
    "SomeUnmanaged":   RemoteStorageStatus.UNMANAGED_CATALOG,
}

_REMOTE_STORAGE_TYPE_MAP: dict[str, RemoteStorageType] = {
    "AEV":              RemoteStorageType.ACTIVE_PROTECT_VAULT,
    "C2_S3":            RemoteStorageType.C2_OBJECT_STORAGE,
    "AWS_S3":           RemoteStorageType.AMAZON_S3,
    "AWS_S3_CHINA":     RemoteStorageType.AMAZON_S3_CHINA,
    "WASABI_S3":        RemoteStorageType.WASABI,
    "AZURE_BLOB":       RemoteStorageType.AZURE_BLOB,
    "AZURE_BLOB_CHINA": RemoteStorageType.AZURE_BLOB_CHINA,
    "COMPATIBLE_S3":    RemoteStorageType.S3_COMPATIBLE,
}

_ENDPOINT_REQUIRED_TYPES = {
    RemoteStorageType.ACTIVE_PROTECT_VAULT,
    RemoteStorageType.S3_COMPATIBLE,
}  # Only these two types use a caller-supplied endpoint; all others connect to fixed service endpoints.


async def _fetch_apv_cert(
    session: WebAPISession, endpoint: str, access_key: str, secret_key: str
) -> str:
    """Retrieve the self-signed TLS certificate from an APV server."""
    raw = await session.post("/api/v1/external_storage/cert", json={
        "storageType": "AEV",
        "accessKey":   access_key,
        "secretKey":   secret_key,
        "endpoint":    endpoint,
    })
    return (raw.get("certificate") or {}).get("cert") or ""


async def _fetch_apv_info(
    session: WebAPISession, endpoint: str, access_key: str, secret_key: str, cert: str
) -> dict[str, str]:
    """Retrieve vault metadata from an APV server.

    Returns dict with keys: vaultName, serverName, modelName, serverVersion.
    """
    raw: dict[str, str] = await session.post("/api/v1/external_storage/aev/info", json={
        "accessKey":   access_key,
        "secretKey":   secret_key,
        "endpoint":    endpoint,
        "certificate": cert,
    })
    return raw


async def _fetch_s3_cert_and_region(
    session: WebAPISession, endpoint: str, access_key: str, secret_key: str
) -> tuple[str, str]:
    """Detect region and optional self-signed certificate from an S3-compatible endpoint.

    Returns (cert_pem, region). cert_pem is empty for CA-signed endpoints.
    """
    raw = await session.post("/api/v1/external_storage/compatable_s3/region_cert", json={
        "endpoint":  endpoint,
        "accessKey": access_key,
        "secretKey": secret_key,
    })
    cert = raw.get("cert") or ""
    region = raw.get("region") or ""
    return cert, region


async def _fetch_s3_support_virtual_host(
    session: WebAPISession, endpoint: str, access_key: str, secret_key: str, bucket_name: str,
    region: str, cert: str,
) -> bool:
    """Check whether the S3-compatible endpoint supports virtual-hosted-style bucket addressing."""
    raw = await session.post("/api/v1/external_storage/bucket/support_virtual_host", json={
        "endpoint":          endpoint,
        "accessKey":         access_key,
        "secretKey":         secret_key,
        "bucketName":        bucket_name,
        "customizedRegion":  region,
        "certificate":       cert,
    })
    return bool(raw.get("supportVirtualHost", True))


class RemoteStorageCollection:
    """Collection interface for managing remote storage devices in APM.

    Accessed via APMClient.remote_storages; should not be instantiated directly.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(self) -> ListResult[RemoteStorage]:
        """List all remote storage devices.

        Returns all results at once; pagination is not available for this resource.

        Returns:
            ListResult of (list of RemoteStorage, total count)
        """
        raw = await self._session.get("/api/v1/external_storage/detail")
        items = [_parse_remote_storage(s) for s in raw.get("storages") or []]
        return ListResult(items, len(items))

    async def get(self, storage_id: str) -> RemoteStorage:
        """Fetch a remote storage device by ID.

        Args:
            storage_id: Remote storage UUID.

        Raises:
            ResourceNotFoundError: The specified remote storage does not exist.
        """
        with _not_found_as("RemoteStorage", storage_id):
            raw = await self._session.get(f"/api/v1/external_storage/{storage_id}")
            if not raw.get("id"):
                raise ResourceNotFoundError("empty response", resource_type="unknown", resource_id="")
        return _parse_remote_storage(raw)

    async def get_by_name(self, name: str) -> RemoteStorage:
        """Fetch a remote storage device by display name or endpoint.

        Matches in order: case-insensitive name → case-insensitive endpoint;
        returns the first match.

        Args:
            name: Display name or endpoint.

        Raises:
            ResourceNotFoundError: No remote storage with an exact match was found.
        """
        items, _ = await self.list()
        q = name.lower()
        for s in items:
            if s.name.lower() == q or s.endpoint.lower() == q:
                return s
        raise ResourceNotFoundError(
            f"RemoteStorage '{name}' not found.",
            resource_type="RemoteStorage",
            resource_id=name,
        )

    async def add(
        self,
        request: (
            GenericS3StorageAddRequest
            | APVStorageAddRequest
            | AmazonS3StorageAddRequest
            | AmazonS3ChinaStorageAddRequest
            | C2ObjectStorageAddRequest
            | WasabiCloudStorageAddRequest
        ),
    ) -> RemoteStorageAddResult:
        """Register a new remote storage device.

        For S3 Compatible storages, no region or virtual-host configuration is required.
        For ActiveProtect Vault storages, only credentials and endpoint are required — no
        vault name or display name input is needed.
        For Amazon S3, Amazon S3 China, C2 Object Storage, and Wasabi storages, only
        credentials and bucket name are required — no endpoint input is needed.

        Set trust_self_signed=True on GenericS3StorageAddRequest or APVStorageAddRequest when the
        endpoint uses a self-signed certificate. Endpoint-free request types do not expose
        this field — their endpoints use CA-signed certificates.

        For re-adding a previously encrypted vault, pass the saved encryption key in
        relink_encryption_key; leave it "" for a fresh vault — the returned result will
        include the newly issued key.

        If the vault contains pre-existing backup catalogs from a previous APM setup, provide
        unmanaged_retirement_plan to relink them. If catalogs are detected and
        unmanaged_retirement_plan is not set, RemoteStorageUnmanagedCatalogError is raised
        before any storage is created.

        Returns:
            RemoteStorageAddResult with the registered storage, the encryption key (if
            encryption_enabled is True; store it securely — it cannot be retrieved later),
            and relink_warning (non-None when catalog relinking was attempted but failed;
            the storage is registered but the catalogs remain unlinked).

        Raises:
            RemoteStorageUnmanagedCatalogError: Pre-existing catalogs found; unmanaged_retirement_plan required.
            RemoteStorageConflictError: The vault is already registered.
            RemoteStorageEncryptionMismatchError: The vault was registered with encryption;
                relink_encryption_key required.
            APIError: Other errors (e.g. invalid credentials, certificate issue, key format error).
        """
        body = await self._build_add_body(request)

        # Check for pre-existing backup catalogs. Endpoint-free types (Amazon S3, Amazon S3 China,
        # C2, Wasabi) have body["endpoint"]="" so they naturally send endpoint:"" here — no
        # special-casing needed. S3 Compatible and APV use the full endpoint and also require
        # customizedRegion/supportVirtualHost when present (same as the final add body).
        catalog_body: dict[str, Any] = {
            "storageType": body["storageType"],
            "accessKey":   body["accessKey"],
            "secretKey":   body["secretKey"],
            "vaultName":   body["vaultName"],
            "endpoint":    body.get("endpoint") or "",
            "certificate": body.get("certificate") or "",
        }
        if "customizedRegion" in body:
            catalog_body["customizedRegion"] = body["customizedRegion"]
        if "supportVirtualHost" in body:
            catalog_body["supportVirtualHost"] = body["supportVirtualHost"]
        catalog_raw = await self._session.post("/api/v1/storage_connection/remote", json=catalog_body)
        connections = catalog_raw.get("connections") or []
        if connections and request.unmanaged_retirement_plan is None:
            raise RemoteStorageUnmanagedCatalogError(
                f"Found {len(connections)} unmanaged catalog(s) in vault '{body['vaultName']}'; "
                f"provide unmanaged_retirement_plan to relink them.",
                vault_name=body["vaultName"],
                catalog_count=len(connections),
            )

        try:
            raw = await self._session.post("/api/v1/external_storage", json=body)
        except APIError as exc:
            if exc.error_code == 3004:
                raise RemoteStorageConflictError(
                    f"RemoteStorage vault '{body['vaultName']}' is already registered.",
                    resource_type="RemoteStorage",
                    resource_id=body["vaultName"],
                    error_code=exc.error_code,
                    response_body=exc.response_body,
                ) from exc
            if exc.error_code == 3006:
                raise RemoteStorageEncryptionMismatchError(
                    f"Vault '{body['vaultName']}' was registered with encryption; "
                    "provide relink_encryption_key from the original registration.",
                    resource_type="RemoteStorage",
                    resource_id=body["vaultName"],
                    error_code=exc.error_code,
                    response_body=exc.response_body,
                ) from exc
            raise

        storage_id = raw.get("id") or ""
        raw_key = raw.get("encryptionKey") or ""
        encryption_key: str | None = raw_key if raw_key else None

        relink_warning: str | None = None
        if connections and request.unmanaged_retirement_plan is not None:
            relink_items = [
                {
                    "connectionId":    c["id"],
                    "namespace":       c["backupServerNamespace"],
                    "archivePlanUuid": request.unmanaged_retirement_plan.plan_id,
                }
                for c in connections
            ]
            try:
                await self._session.post("/api/v1/storage_connection/batch_relink", json={
                    "storageUuid": storage_id,
                    "items":       relink_items,
                })
            except Exception as exc:
                relink_warning = str(exc)

        storage = await self.get(storage_id)
        return RemoteStorageAddResult(storage=storage, encryption_key=encryption_key,
                                      relink_warning=relink_warning)

    async def update(
        self,
        storage: RemoteStorage,
        request: RemoteStorageUpdateRequest,
    ) -> RemoteStorage:
        """Update the access credentials for a remote storage device.

        Only credentials and endpoint can be changed. Display name, storage type,
        and encryption settings are immutable once set at registration.

        trust_self_signed applies to S3_COMPATIBLE and ACTIVE_PROTECT_VAULT storages — set True
        to auto-fetch and pin the endpoint's self-signed TLS certificate. Leave False for all other
        storage types; their endpoints are CA-signed.

        Returns:
            Updated RemoteStorage reflecting the current connection state.

        Raises:
            ResourceNotFoundError: The storage no longer exists.
            APIError: Credential or certificate validation failed.
        """
        body = await self._build_update_body(storage, request)
        await self._session.post("/api/v1/external_storage/update", json=body)
        return await self.get(storage.storage_id)

    async def delete(self, storage: RemoteStorage) -> None:
        """Delete a remote storage device.

        Raises:
            ResourceNotFoundError:   The storage does not exist.
            RemoteStorageInUseError: The storage is referenced by active plans.
            APIError: Other errors.
        """
        try:
            await self._session.delete(
                f"/api/v1/external_storage/{storage.storage_id}", json={}
            )
        except APIError as exc:
            if exc.error_code == 3014:
                raise RemoteStorageInUseError(
                    f"RemoteStorage '{storage.name}' is referenced by active plans.",
                    resource_type="RemoteStorage",
                    resource_id=storage.storage_id,
                    error_code=exc.error_code,
                    response_body=exc.response_body,
                ) from exc
            raise

    async def _build_add_body(
        self,
        request: GenericS3StorageAddRequest | APVStorageAddRequest | _S3VendorStorageAddRequest,
    ) -> dict[str, Any]:
        enc_type = "Encryption" if request.encryption_enabled else "NoEncryption"
        if isinstance(request, APVStorageAddRequest):
            cert = ""
            if request.trust_self_signed:
                cert = await _fetch_apv_cert(
                    self._session, request.endpoint, request.access_key, request.secret_key
                )
            info = await _fetch_apv_info(
                self._session, request.endpoint, request.access_key, request.secret_key, cert
            )
            server_name = info.get("serverName") or ""
            vault_name  = info.get("vaultName") or ""
            if not server_name or not vault_name:
                raise APIError(
                    f"APV server at {request.endpoint!r} did not return the expected server name and vault name",
                    error_code=0,
                    response_body=info,
                )
            body: dict[str, Any] = {
                "storageType":           "AEV",
                "displayName":           server_name,
                "storageEncryptionType": enc_type,
                "storageEncryptionKey":  request.relink_encryption_key,
                "accessKey":             request.access_key,
                "secretKey":             request.secret_key,
                "vaultName":             vault_name,
                "endpoint":              request.endpoint,
            }
            if cert:
                body["certificate"] = cert
            return body
        elif isinstance(request, _S3VendorStorageAddRequest):
            # All vendor-managed endpoint types: APM derives endpoint/region server-side; no pre-flights.
            # Body structure is identical for all four — only storageType differs.
            if isinstance(request, AmazonS3StorageAddRequest):
                api_type = "AWS_S3"
            elif isinstance(request, AmazonS3ChinaStorageAddRequest):
                api_type = "AWS_S3_CHINA"
            elif isinstance(request, C2ObjectStorageAddRequest):
                api_type = "C2_S3"
            else:
                api_type = "WASABI_S3"
            return {
                "storageType":           api_type,
                "displayName":           request.vault_name,
                "storageEncryptionType": enc_type,
                "storageEncryptionKey":  request.relink_encryption_key,
                "accessKey":             request.access_key,
                "secretKey":             request.secret_key,
                "vaultName":             request.vault_name,
                "endpoint":              "",
            }
        else:
            # GenericS3StorageAddRequest: fetch region/cert first, then pass region to the
            # virtual-host check — the two calls are NOT independent (support_virtual_host
            # requires the real region to authenticate correctly).
            fetched_cert, region = await _fetch_s3_cert_and_region(
                self._session, request.endpoint, request.access_key, request.secret_key
            )
            supports_vhost = await _fetch_s3_support_virtual_host(
                self._session, request.endpoint, request.access_key, request.secret_key,
                request.vault_name, region, fetched_cert,
            )
            body = {
                "storageType":           "COMPATIBLE_S3",
                "displayName":           request.vault_name,
                "storageEncryptionType": enc_type,
                "storageEncryptionKey":  request.relink_encryption_key,
                "accessKey":             request.access_key,
                "secretKey":             request.secret_key,
                "vaultName":             request.vault_name,
                "endpoint":             request.endpoint,
                "customizedRegion":      region,
                "supportVirtualHost":    supports_vhost,
            }
            if request.trust_self_signed and fetched_cert:
                body["certificate"] = fetched_cert
            return body

    async def _build_update_body(
        self, storage: RemoteStorage, request: RemoteStorageUpdateRequest
    ) -> dict[str, Any]:
        # UPDATE is a pure credential re-auth endpoint — minimal body.
        # displayName, storageType, vaultName, encryption fields are silently ignored.
        # Endpoint-free types (Amazon S3, Amazon S3 China, C2, Wasabi): endpoint NOT sent
        # (wizard only sends {id, accessKey, secretKey}).
        is_endpoint_free = storage.storage_type not in _ENDPOINT_REQUIRED_TYPES
        is_apv = storage.storage_type == RemoteStorageType.ACTIVE_PROTECT_VAULT
        body: dict[str, Any] = {
            "id":        storage.storage_id,
            "accessKey": request.access_key,
            "secretKey": request.secret_key,
        }
        if not is_endpoint_free:
            body["endpoint"] = request.endpoint
        if request.trust_self_signed and not is_endpoint_free:
            if is_apv:
                cert = await _fetch_apv_cert(
                    self._session, request.endpoint, request.access_key, request.secret_key
                )
            else:
                cert, _ = await _fetch_s3_cert_and_region(
                    self._session, request.endpoint, request.access_key, request.secret_key
                )
            if cert:
                body["certificate"] = cert
        return body


def _parse_remote_storage(raw: dict[str, Any]) -> RemoteStorage:
    """Convert a storage object from an API response to the SDK RemoteStorage model."""
    used_raw = raw.get("usedSpace")
    remaining_raw = raw.get("remainingSpace")
    used_bytes = int(used_raw) if used_raw is not None and used_raw != "" else None
    remaining_bytes = int(remaining_raw) if remaining_raw is not None and remaining_raw != "" else None
    return RemoteStorage(
        storage_id=raw.get("id") or "",
        name=raw.get("displayName") or "",
        storage_type=_REMOTE_STORAGE_TYPE_MAP.get(raw.get("storageType") or "", RemoteStorageType.UNKNOWN),
        device_model=raw.get("modelName") or "",
        endpoint=raw.get("endpoint") or "",
        status=_REMOTE_STORAGE_STATUS_MAP.get(raw.get("connectionStatus") or "", RemoteStorageStatus.UNKNOWN),
        used_bytes=used_bytes,
        remaining_bytes=remaining_bytes,
        encryption_enabled=bool(raw.get("isEncryption") or False),
        vault_name=raw.get("vaultName") or "",
    )
