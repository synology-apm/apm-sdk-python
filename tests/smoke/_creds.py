"""Shared credential loader for SDK and CLI smoke test CRUD roundtrips.

Drop a ``tests/smoke/smoke_creds.toml`` file (see ``--output-creds-template``) to enable
add/update/delete roundtrip testing.  Without that file (or with ``--creds <path>``
pointing to an absent path), all CRUD steps are automatically skipped.
"""
from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_CREDS_PATH = Path(__file__).parent / "smoke_creds.toml"


@dataclass
class RemoteStorageCred:
    """Credential entry for one ``[[remote_storage]]`` block in smoke_creds.toml."""

    type: str
    access_key: str
    secret_key: str
    name: str = ""
    endpoint: str = ""
    vault: str = ""
    trust_self_signed: bool = False
    relink_encryption_key: str = ""

    def display_name(self) -> str:
        return self.name or self.type


_REMOTE_STORAGE_CRED_FIELDS = {f.name for f in dataclasses.fields(RemoteStorageCred)}


@dataclass
class SmokeCreds:
    """Parsed contents of smoke_creds.toml."""

    remote_storage: list[RemoteStorageCred] = field(default_factory=list)


def load_smoke_creds(path: Path | None = None) -> SmokeCreds:
    """Load credentials from a TOML file.

    Returns an empty SmokeCreds if the file does not exist, or if path is None and
    the default path (tests/smoke/smoke_creds.toml) is absent.
    """
    resolved = path if path is not None else _DEFAULT_CREDS_PATH
    if not resolved.exists():
        return SmokeCreds()
    with open(resolved, "rb") as f:
        data = tomllib.load(f)
    entries = []
    for entry in data.get("remote_storage", []):
        unknown = entry.keys() - _REMOTE_STORAGE_CRED_FIELDS
        if unknown:
            print(f"[smoke_creds] warning: ignoring unknown key(s) in [[remote_storage]]: {', '.join(sorted(unknown))}")
        entries.append(RemoteStorageCred(**{k: v for k, v in entry.items() if k in _REMOTE_STORAGE_CRED_FIELDS}))
    return SmokeCreds(remote_storage=entries)


TEMPLATE = """\
# smoke_creds.toml — CRUD roundtrip credentials for the SDK/CLI smoke tests.
# Fill in real values, save as tests/smoke/smoke_creds.toml (or pass --creds <path>), then run:
#
#   uv run python -m tests.smoke.sdk [--creds tests/smoke/smoke_creds.toml]
#
# Each [[remote_storage]] block is tested independently (add → get → update → delete).
# Remove or comment out any block whose storage backend is unavailable in your lab.

# S3-compatible storage (e.g. MinIO, Ceph RGW, or any S3-compatible object store).
[[remote_storage]]
type = "s3_compatible"
name = "smoke-s3"                        # label shown in smoke test step names
endpoint = "https://s3.example.com:443"  # full URL including port
access_key = "AKIAIOSFODNN7EXAMPLE"
secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
vault = "my-test-bucket"                 # bucket name; also used as the APM display name
trust_self_signed = false                # set true for self-signed TLS certificates
# relink_encryption_key = "..."         # set to test encrypted-vault relinking; omit for a fresh (non-encrypted) vault

# ActiveProtect Vault (APV) storage.
# [[remote_storage]]
# type = "apv"
# name = "smoke-apv"
# endpoint = "apv.example.com:8444"     # host:port format — no https:// prefix
# access_key = "admin"
# secret_key = "password"
# trust_self_signed = true              # APV servers typically use self-signed certs

# Amazon S3 (APM derives the endpoint from the bucket region).
# [[remote_storage]]
# type = "amazon_s3"
# name = "smoke-aws"
# access_key = "AKIAIOSFODNN7EXAMPLE"
# secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
# vault = "my-test-bucket"

# Wasabi Cloud Storage (APM derives the endpoint from the bucket region).
# [[remote_storage]]
# type = "wasabi"
# name = "smoke-wasabi"
# access_key = "AKIAIOSFODNN7EXAMPLE"
# secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
# vault = "my-test-bucket"

# C2 Object Storage (APM derives the endpoint from the bucket region).
# [[remote_storage]]
# type = "c2"
# name = "smoke-c2"
# access_key = "AKIAIOSFODNN7EXAMPLE"
# secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
# vault = "my-test-bucket"

# Amazon S3 China region (APM derives the China region endpoint).
# [[remote_storage]]
# type = "amazon_s3_china"
# name = "smoke-aws-cn"
# access_key = "AKIAIOSFODNN7EXAMPLE"
# secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
# vault = "my-test-bucket"

# Future: hypervisor CRUD (placeholder — not yet implemented).
# [[hypervisor]]
# type = "esxi"                          # esxi | vcenter
# name = "smoke-esxi"
# hostname = "esxi1.example.com"
# port = 443
# username = "root"
# password = "vmware"
# trust_self_signed = true
"""
