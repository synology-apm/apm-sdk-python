"""SaasCollection — collection interface for SaaS tenants (Cloud Applications)."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..enums import WorkloadCategory
from ..exceptions import ResourceNotFoundError
from ..models.saas import SaasTenant
from ._shared import ListResult


class SaasCollection:
    """Lists all SaaS tenants connected to APM (M365 + GWS).

    Accessed via APMClient.saas; should not be instantiated directly.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def get_m365_tenant(self, tenant_id: str) -> SaasTenant:
        """Fetch details for a specific M365 tenant.

        Args:
            tenant_id: Azure AD tenant UUID.

        Returns:
            SaasTenant with protected_data_bytes set to 0 (usage data is not available for this lookup).

        Raises:
            ResourceNotFoundError: The specified tenant was not found.
            APIError: Server returned an unexpected error.
        """
        raw = await self._session.get(f"/api/v1/application/m365/tenant/{tenant_id}")
        if not raw.get("isFound"):
            raise ResourceNotFoundError(
                f"M365 tenant '{tenant_id}' not found.",
                resource_type="SaasTenant",
                resource_id=tenant_id,
            )
        tenant = (raw.get("data") or {}).get("tenant") or {}
        return SaasTenant(
            tenant_id=tenant.get("tenantId") or tenant_id,
            tenant_name=tenant.get("tenantName") or "",
            tenant_email=tenant.get("tenantMail") or "",
            category=WorkloadCategory.M365,
            protected_data_bytes=0,
        )

    async def list(self, limit: int = 500, offset: int = 0) -> ListResult[SaasTenant]:
        """List all connected SaaS tenants (M365 + GWS).

        Args:
            limit:  Maximum records to return (default 500).
            offset: Pagination start offset (default 0).

        Returns:
            (list of SaasTenant (M365 first, GWS after), total count)

        Raises:
            AuthenticationError: Session has expired.
            APIError: Server returned an unexpected error.
        """
        body = {
            "offset": offset,
            "limit": limit,
            "m365First": True,
            "sortBy": "NAME_ASC",
        }
        raw = await self._session.post("/api/v1/application/cloudapp", json=body)
        tenants: list[SaasTenant] = [_parse_m365_tenant(entry) for entry in raw.get("m365") or []]
        tenants.extend(_parse_gws_tenant(entry) for entry in raw.get("gw") or [])

        # cloudapp endpoint returns total as string — server-side bug
        raw_total = raw.get("total")
        return ListResult(tenants, int(raw_total) if raw_total is not None else None)


def _parse_m365_tenant(entry: dict[str, Any]) -> SaasTenant:
    tenant = entry.get("tenant") or {}
    usage_info = tenant.get("dataUsageInfo") or {}
    return SaasTenant(
        tenant_id=tenant.get("tenantId") or "",
        tenant_name=tenant.get("tenantName") or "",
        tenant_email=tenant.get("tenantMail") or "",
        category=WorkloadCategory.M365,
        protected_data_bytes=int(usage_info.get("dataUsage") or 0),
    )


def _parse_gws_tenant(entry: dict[str, Any]) -> SaasTenant:
    tenant = entry.get("tenant") or {}
    usage_info = tenant.get("dataUsageInfo") or {}
    return SaasTenant(
        tenant_id=tenant.get("domainId") or tenant.get("tenantId") or "",
        tenant_name=tenant.get("tenantName") or tenant.get("domainName") or "",
        tenant_email=tenant.get("tenantMail") or tenant.get("domain") or "",
        category=WorkloadCategory.GWS,
        protected_data_bytes=int(usage_info.get("dataUsage") or 0),
    )
