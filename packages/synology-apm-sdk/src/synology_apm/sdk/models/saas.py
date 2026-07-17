"""SaasTenant — SaaS tenant (Cloud Application) data model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..enums import WorkloadCategory
from ._shared import auto_to_dict


@dataclass(frozen=True)
class SaasTenant:
    """A SaaS tenant connected to APM (M365 or GWS).

    Attributes:
        tenant_id: Unique tenant identifier (M365: Azure AD tenant UUID; GWS: domain).
        tenant_name: Tenant display name (M365 organization name / GWS domain).
        tenant_email: Primary email or domain of the tenant.
        category: Business domain (M365 / GWS).
        protected_data_bytes: Protected data size for this tenant in bytes.
    """
    tenant_id: str
    tenant_name: str
    tenant_email: str
    category: WorkloadCategory
    protected_data_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)
