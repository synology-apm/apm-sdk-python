"""Hypervisor data model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..enums import HypervisorType
from ._shared import auto_to_dict


@dataclass(frozen=True)
class Hypervisor:
    """A hypervisor inventory server registered in APM.

    Attributes:
        hypervisor_id: Unique hypervisor identifier.
        hostname:      Display hostname.
        address:       IP address or FQDN used for connection.
        host_type:     Hypervisor product type.
        account:       Authentication account.
        description:   User-provided description.
        port:          Web API port.
        version:       Hypervisor software version.
    """
    hypervisor_id: str
    hostname:      str
    address:       str
    host_type:     HypervisorType
    account:       str
    description:   str
    port:          int
    version:       str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)
