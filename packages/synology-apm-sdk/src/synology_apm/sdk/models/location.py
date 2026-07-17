"""Storage location display model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._shared import auto_to_dict


@dataclass(frozen=True)
class LocationInfo:
    """Display information for a storage location.

    Used for both Workload.backup_server / backup_copy_destination and
    VersionLocation.location_info (the entries of WorkloadVersion.locations).

    Attributes:
        is_remote_storage: True for remote storage destinations (e.g. S3, APV); False for on-appliance backup servers.
        identifier:        Namespace of the backup server (appliance) or storage UID (remote storage).
        name:              Display name of the server or storage endpoint.
        endpoint:          Connection address of the server or storage endpoint.
        vault:             Vault name; None when no vault is configured.
    """
    is_remote_storage: bool
    identifier: str
    name: str
    endpoint: str
    vault: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)
