"""Sandbox predicates shared by the sandbox integration test and the SDK/CLI smoke phases."""
from __future__ import annotations

from typing import Any

from synology_apm.sdk import SandboxResources, SandboxSettings, VersionStatus, WorkloadVersion

RESTORABLE_STATUSES = (VersionStatus.SUCCESS, VersionStatus.PARTIAL)


def is_restorable_version(version: WorkloadVersion) -> bool:
    """Whether a sandbox can start from this version: SUCCESS/PARTIAL with a backup-server copy."""
    return version.status in RESTORABLE_STATUSES and any(
        not loc.location_info.is_remote_storage for loc in version.locations
    )


def has_backup_server_copy(version: dict[str, Any]) -> bool:
    """Whether a CLI ``machine version list -o json`` entry has a copy on a backup server."""
    return any(not loc.get("is_remote_storage") for loc in version.get("locations", []))


def assert_defaults_fit(resources: SandboxResources, settings: SandboxSettings) -> None:
    """Assert that default settings fit the default (first) location's limits and networks."""
    assert resources.locations, "no sandbox location"
    default = resources.locations[0]
    assert settings.location_id == default.location_id, (settings.location_id, default.location_id)
    assert default.min_memory_mb <= settings.memory_mb <= default.max_memory_mb, (
        settings.memory_mb, default.min_memory_mb, default.max_memory_mb,
    )
    assert 1 <= settings.cpu_count <= default.max_cpu_count, (settings.cpu_count, default.max_cpu_count)
    adapter_networks = {a.network_id for a in settings.network_adapters}
    assert adapter_networks <= {n.network_id for n in default.networks}, adapter_networks
    assert settings.disks, "no disk"
