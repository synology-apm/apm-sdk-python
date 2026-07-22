"""HypervisorCollection — collection interface for managing hypervisor inventory servers."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..enums import HypervisorType
from ..exceptions import ResourceNotFoundError
from ..models.hypervisor import Hypervisor
from ._shared import ListResult, _not_found_as

_HOST_TYPE_MAP: dict[str, HypervisorType] = {
    "ESXi":            HypervisorType.VSPHERE_ESXI,
    "vCenter":         HypervisorType.VSPHERE_VCENTER,
    "HyperV":          HypervisorType.HYPERV_STANDALONE,
    "SCVMM":           HypervisorType.HYPERV_SCVMM,
    "FailoverCluster": HypervisorType.HYPERV_FAILOVER_CLUSTER,
}


class HypervisorCollection:
    """Collection interface for managing hypervisor inventory servers in APM.

    Accessed via APMClient.hypervisors; should not be instantiated directly.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(self) -> ListResult[Hypervisor]:
        """List all registered hypervisor inventory servers.

        Returns:
            ListResult of (list of Hypervisor, total count)
        """
        raw = await self._session.get("/api/v1/inventory")
        items = [_parse_hypervisor(h) for h in raw.get("inventories", [])]
        return ListResult(items, len(items))

    async def get(self, hypervisor_id: str) -> Hypervisor:
        """Fetch a hypervisor inventory server by ID.

        Args:
            hypervisor_id: Hypervisor UUID.

        Raises:
            ResourceNotFoundError: The specified hypervisor does not exist.
        """
        with _not_found_as("Hypervisor", hypervisor_id):
            raw = await self._session.get(f"/api/v1/inventory/{hypervisor_id}")
            if not raw.get("id"):
                raise ResourceNotFoundError("empty response", resource_type="unknown", resource_id="")
        return _parse_hypervisor(raw)

    async def get_by_name(self, name: str) -> Hypervisor:
        """Fetch a hypervisor inventory server by hostname.

        Matches in order: case-insensitive hostname → case-insensitive address;
        returns the first match.

        Args:
            name: Hostname or address.

        Raises:
            ResourceNotFoundError: No hypervisor with an exact match was found.
        """
        items, _ = await self.list()
        q = name.lower()
        for h in items:
            if h.hostname.lower() == q or h.address.lower() == q:
                return h
        raise ResourceNotFoundError(
            f"Hypervisor '{name}' not found.",
            resource_type="Hypervisor",
            resource_id=name,
        )


def _parse_hypervisor(raw: dict[str, Any]) -> Hypervisor:
    """Convert an inventory object from an API response to the SDK Hypervisor model."""
    spec = raw.get("spec", {})
    return Hypervisor(
        hypervisor_id=raw.get("id", ""),
        hostname=spec.get("hostName", ""),
        address=spec.get("hostAddr", ""),
        host_type=_HOST_TYPE_MAP.get(spec.get("hostType", ""), HypervisorType.UNKNOWN),
        account=spec.get("authUser", ""),
        description=spec.get("description", ""),
        port=int(spec.get("portWebapi", 0)),
        version=spec.get("version", ""),
    )
