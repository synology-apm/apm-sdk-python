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
    "NutanixPE":       HypervisorType.NUTANIX_PRISM_ELEMENT,
    "NutanixPC":       HypervisorType.NUTANIX_PRISM_CENTRAL,
    "ProxmoxNode":     HypervisorType.PROXMOX_NODE,
    "ProxmoxCluster":  HypervisorType.PROXMOX_CLUSTER,
    "AWS":             HypervisorType.AWS,
    "Azure":           HypervisorType.AZURE,
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
        items = [_parse_hypervisor(h) for h in raw.get("inventories") or []]
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
    spec = raw.get("spec") or {}
    return Hypervisor(
        hypervisor_id=raw.get("id") or "",
        hostname=spec.get("hostName") or "",
        address=spec.get("hostAddr") or "",
        host_type=_HOST_TYPE_MAP.get(spec.get("hostType") or "", HypervisorType.UNKNOWN),
        account=spec.get("authUser") or "",
        description=spec.get("description") or "",
        port=int(spec.get("portWebapi") or 0),
        version=spec.get("version") or "",
    )


def _parse_optional_host_type(raw: str | None) -> HypervisorType | None:
    """Convert a raw inventory host-type string to HypervisorType, for callers where the
    value is optional (e.g. a workload's hypervisor inventory link).

    "" and the API's "NONE" sentinel both mean "not linked to any hypervisor inventory" and
    parse as None; any other unrecognized string still parses as HypervisorType.UNKNOWN
    (a hypervisor type is present, just not one this SDK version recognizes).
    """
    if not raw or raw == "NONE":
        return None
    return _HOST_TYPE_MAP.get(raw, HypervisorType.UNKNOWN)
