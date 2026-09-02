"""SaasApplication — SaaS backup connection (Cloud Application) data models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..enums import WorkloadCategory
from ._shared import auto_to_dict


@dataclass(frozen=True)
class SaasApplication:
    """Base class for a SaaS backup connection (Cloud Application) — an M365 tenant or a
    GWS domain.

    Attributes:
        name: Display name.
        category: Business domain (M365 / GWS).
        protected_data_bytes: Protected data size in bytes.
    """
    name: str
    category: WorkloadCategory
    protected_data_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class M365TenantInfo(SaasApplication):
    """A connected Microsoft 365 tenant (category=M365).

    Attributes:
        tenant_id: Azure AD tenant UUID (unique identifier).
        domain: The tenant's domain; not used as the identifier (see tenant_id).
    """
    tenant_id: str
    domain: str


@dataclass(frozen=True)
class GWSDomainInfo(SaasApplication):
    """A connected Google Workspace domain (category=GWS).

    Attributes:
        domain: The connected Google Workspace domain; also serves as its unique identifier.
        domain_admin: Email address of the domain's administrator.
    """
    domain: str
    domain_admin: str
