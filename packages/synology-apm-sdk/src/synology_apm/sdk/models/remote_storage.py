"""RemoteStorage data model."""
from __future__ import annotations

from dataclasses import dataclass

from ..enums import RemoteStorageStatus, RemoteStorageType
from .retirement_plan import RetirementPlan


@dataclass(frozen=True)
class RemoteStorage:
    """A remote storage device configured in APM (External Vault).

    Attributes:
        storage_id:          Unique remote storage identifier.
        name:                Display name.
        storage_type:        Storage type (RemoteStorageType).
        device_model:        Device model. Only meaningful for ACTIVE_PROTECT_VAULT.
        endpoint:            Connection address (host:port).
        status:              Connection status (RemoteStorageStatus).
        used_bytes:          Used space in bytes. None when unavailable.
        remaining_bytes:     Remaining available space in bytes. None when unavailable.
        encryption_enabled:  True when client-side encryption is enabled for this storage.
        vault_name:          Bucket or vault name. Empty for storage types that do not report it.
    """
    storage_id:         str
    name:               str
    storage_type:       RemoteStorageType
    device_model:       str
    endpoint:           str
    status:             RemoteStorageStatus
    used_bytes:         int | None
    remaining_bytes:    int | None
    encryption_enabled: bool = False
    vault_name:         str = ""


@dataclass(frozen=True)
class GenericS3StorageAddRequest:
    """Parameters for registering a new S3 Compatible remote storage device.

    If pre-existing backup catalogs are found in the bucket, add() raises
    RemoteStorageUnmanagedCatalogError unless unmanaged_retirement_plan is set.

    Attributes:
        access_key:                Access key.
        secret_key:                Secret key.
        vault_name:                Bucket name. Also used as the display name in APM.
        endpoint:                  Service endpoint URL (e.g. "https://s3.example.com:443").
        encryption_enabled:        True to enable client-side encryption.
        relink_encryption_key:     Encryption key issued when this bucket was previously registered
                                   with APM. Leave "" for a new bucket — APM generates the key.
                                   When re-adding, the old key remains valid even after a new key
                                   is issued.
        trust_self_signed:         True to auto-fetch and trust the endpoint's self-signed
                                   certificate. Use for endpoints with self-signed certificates;
                                   leave False for CA-signed endpoints.
        unmanaged_retirement_plan: Retirement plan to assign pre-existing backup catalogs found
                                   in the bucket. Required when unmanaged catalogs exist.
    """
    access_key:                str
    secret_key:                str
    vault_name:                str
    endpoint:                  str
    encryption_enabled:          bool = False
    relink_encryption_key:       str = ""
    trust_self_signed:           bool = False
    unmanaged_retirement_plan:   RetirementPlan | None = None


@dataclass(frozen=True)
class APVStorageAddRequest:
    """Parameters for registering a new ActiveProtect Vault remote storage device.

    If pre-existing backup catalogs are found in the vault, add() raises
    RemoteStorageUnmanagedCatalogError unless unmanaged_retirement_plan is set.

    Attributes:
        access_key:                Access key.
        secret_key:                Secret key.
        endpoint:                  APV server address in "host:port" format (no https:// scheme).
        encryption_enabled:        True to enable client-side encryption.
        relink_encryption_key:     Encryption key issued when this vault was previously registered
                                   with APM. Leave "" for a new vault — APM generates the key.
                                   When re-adding, the old key remains valid even after a new key
                                   is issued.
        trust_self_signed:         True to auto-fetch and trust the APV server's self-signed
                                   certificate. APV servers typically use self-signed certificates,
                                   so this is usually required.
        unmanaged_retirement_plan: Retirement plan to assign pre-existing backup catalogs found
                                   in the vault. Required when unmanaged catalogs exist.
    """
    access_key:                str
    secret_key:                str
    endpoint:                  str
    encryption_enabled:          bool = False
    relink_encryption_key:       str = ""
    trust_self_signed:           bool = False
    unmanaged_retirement_plan:   RetirementPlan | None = None


@dataclass(frozen=True)
class _S3VendorStorageAddRequest:
    """Shared fields for vendor-managed endpoint storage types (Amazon S3, Amazon S3 China,
    C2 Object Storage, Wasabi). APM derives the endpoint and region server-side; callers
    supply only credentials and the bucket name.
    """
    access_key:                str
    secret_key:                str
    vault_name:                str
    encryption_enabled:          bool = False
    relink_encryption_key:       str = ""
    unmanaged_retirement_plan:   RetirementPlan | None = None


@dataclass(frozen=True)
class AmazonS3StorageAddRequest(_S3VendorStorageAddRequest):
    """Parameters for registering a new Amazon S3 remote storage device.

    APM derives the endpoint and region from the bucket name and credentials.
    access_key / secret_key are the AWS credentials; all fields follow the same semantics
    as GenericS3StorageAddRequest, including raising RemoteStorageUnmanagedCatalogError
    from add() when the bucket contains pre-existing backup catalogs and
    unmanaged_retirement_plan is unset.
    """


@dataclass(frozen=True)
class AmazonS3ChinaStorageAddRequest(_S3VendorStorageAddRequest):
    """Parameters for registering a new Amazon S3 China region remote storage device.

    APM derives the endpoint and region from the bucket name and credentials.
    access_key / secret_key are the AWS credentials; all fields follow the same semantics
    as GenericS3StorageAddRequest, including raising RemoteStorageUnmanagedCatalogError
    from add() when the bucket contains pre-existing backup catalogs and
    unmanaged_retirement_plan is unset.
    """


@dataclass(frozen=True)
class C2ObjectStorageAddRequest(_S3VendorStorageAddRequest):
    """Parameters for registering a new Synology C2 Object Storage remote storage device.

    APM derives the endpoint and region from the bucket name and credentials.
    access_key / secret_key are the C2 credentials; all fields follow the same semantics
    as GenericS3StorageAddRequest, including raising RemoteStorageUnmanagedCatalogError
    from add() when the bucket contains pre-existing backup catalogs and
    unmanaged_retirement_plan is unset.
    """


@dataclass(frozen=True)
class WasabiCloudStorageAddRequest(_S3VendorStorageAddRequest):
    """Parameters for registering a new Wasabi Cloud Storage remote storage device.

    APM derives the endpoint and region from the bucket name and credentials.
    access_key / secret_key are the Wasabi credentials; all fields follow the same semantics
    as GenericS3StorageAddRequest, including raising RemoteStorageUnmanagedCatalogError
    from add() when the bucket contains pre-existing backup catalogs and
    unmanaged_retirement_plan is unset.
    """


@dataclass(frozen=True)
class RemoteStorageUpdateRequest:
    """Updated credentials for a remote storage device (APV, S3 Compatible, Amazon S3, C2 Object Storage, or Wasabi).

    Display name, storage type, and encryption settings are immutable once set at registration.

    endpoint and trust_self_signed apply to S3_COMPATIBLE and ACTIVE_PROTECT_VAULT storages.
    For APV, endpoint uses "host:port" format (no https:// scheme). For S3 Compatible, endpoint
    uses a full URL (e.g. "https://s3.example.com:443"). For endpoint-free storage types
    (AMAZON_S3, AMAZON_S3_CHINA, C2_OBJECT_STORAGE, WASABI), leave endpoint="" — it is not used.

    Attributes:
        access_key:        New access key.
        secret_key:        New secret key.
        endpoint:          New endpoint address. Sent for S3_COMPATIBLE (full URL) and
                           ACTIVE_PROTECT_VAULT (host:port); leave "" for endpoint-free types.
        trust_self_signed: True to auto-fetch and trust the endpoint's self-signed certificate.
                           Applies to S3_COMPATIBLE and ACTIVE_PROTECT_VAULT storages.
                           Leave False for endpoint-free types — their endpoints are CA-signed.
    """
    access_key:        str
    secret_key:        str
    endpoint:          str = ""
    trust_self_signed: bool = False


@dataclass(frozen=True)
class RemoteStorageAddResult:
    """Result of a successful add() call.

    Attributes:
        storage:        The newly registered remote storage device.
        encryption_key: Client-side encryption key. Store this key securely — it cannot be
                        retrieved later. When re-adding a previously encrypted vault, the old
                        key remains valid but the new key should be saved for future use.
                        None when encryption is not enabled.
        relink_warning: Non-None when catalog relinking was attempted but failed. The storage
                        is registered; catalogs remain unlinked and must be relinked manually.
                        None when no relinking was needed or relinking succeeded.
    """
    storage:        RemoteStorage
    encryption_key: str | None
    relink_warning: str | None = None
