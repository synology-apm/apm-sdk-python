"""Shared credential loader for SDK and CLI smoke test CRUD roundtrips.

Drop a ``tests/smoke/smoke_creds.toml`` file (copy from ``smoke_creds.toml.example``) to
enable add/update/delete roundtrip testing.  Without that file, all CRUD steps are
automatically skipped.
"""
from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_CREDS_PATH = Path(__file__).parent / "smoke_creds.toml"


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


def load_smoke_creds() -> SmokeCreds:
    """Load credentials from tests/smoke/smoke_creds.toml.

    Returns an empty SmokeCreds if the file does not exist.
    """
    if not _CREDS_PATH.exists():
        return SmokeCreds()
    with open(_CREDS_PATH, "rb") as f:
        data = tomllib.load(f)
    entries = []
    for entry in data.get("remote_storage", []):
        unknown = entry.keys() - _REMOTE_STORAGE_CRED_FIELDS
        if unknown:
            print(f"[smoke_creds] warning: ignoring unknown key(s) in [[remote_storage]]: {', '.join(sorted(unknown))}")
        entries.append(RemoteStorageCred(**{k: v for k, v in entry.items() if k in _REMOTE_STORAGE_CRED_FIELDS}))
    return SmokeCreds(remote_storage=entries)
