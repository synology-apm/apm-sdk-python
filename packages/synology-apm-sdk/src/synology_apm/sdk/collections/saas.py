"""SaasCollection — collection interface for SaaS applications (Cloud Applications)."""
from __future__ import annotations

from typing import Any

from .._http import WebAPISession
from ..enums import WorkloadCategory
from ..exceptions import ResourceNotFoundError
from ..models.saas import GWSDomainInfo, M365TenantInfo
from ._shared import ListResult


class SaasCollection:
    """Lists all SaaS applications connected to APM (M365 + GWS).

    Accessed via APMClient.saas; should not be instantiated directly.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def get_m365_tenant(self, tenant_id: str) -> M365TenantInfo:
        """Fetch details for a specific M365 tenant.

        Args:
            tenant_id: Azure AD tenant UUID.

        Returns:
            M365TenantInfo with protected_data_bytes set to 0 (usage data is not available
            for this lookup).

        Raises:
            ResourceNotFoundError: The specified tenant was not found.
        """
        raw = await self._session.get(f"/api/v1/application/m365/tenant/{tenant_id}")
        if not raw.get("isFound"):
            raise ResourceNotFoundError(
                f"M365 tenant '{tenant_id}' not found.",
                resource_type="M365TenantInfo",
                resource_id=tenant_id,
            )
        tenant = (raw.get("data") or {}).get("tenant") or {}
        return M365TenantInfo(
            tenant_id=tenant.get("tenantId") or tenant_id,
            name=tenant.get("tenantName") or "",
            domain=tenant.get("tenantMail") or "",
            category=WorkloadCategory.M365,
            protected_data_bytes=0,
        )

    async def get_gws_domain(self, domain: str) -> GWSDomainInfo:
        """Fetch details for a specific GWS domain.

        Args:
            domain: Google Workspace domain.

        Returns:
            GWSDomainInfo for the domain.

        Raises:
            ResourceNotFoundError: The specified domain was not found.
        """
        raw = await self._session.get(f"/api/v1/application/gw/domain/{domain}")
        if not raw.get("isFound"):
            raise ResourceNotFoundError(
                f"GWS domain '{domain}' not found.",
                resource_type="GWSDomainInfo",
                resource_id=domain,
            )
        gw_domain = (raw.get("data") or {}).get("gwDomain") or {}
        usage_info = gw_domain.get("dataUsageInfo") or {}
        domain_str = gw_domain.get("domain") or domain
        return GWSDomainInfo(
            domain=domain_str,
            name=gw_domain.get("domainName") or domain_str,
            domain_admin=gw_domain.get("adminEmail") or "",
            category=WorkloadCategory.GWS,
            protected_data_bytes=int(usage_info.get("dataUsage") or 0),
        )

    async def list(
        self, keyword: str | None = None, limit: int = 500, offset: int = 0
    ) -> ListResult[M365TenantInfo | GWSDomainInfo]:
        """List all connected SaaS applications (M365 + GWS).

        Args:
            keyword: Name keyword (partial match).
            limit:   Maximum records to return (default 500).
            offset:  Pagination start offset (default 0).

        Returns:
            (list of M365TenantInfo / GWSDomainInfo (M365 first, GWS after), total count)
        """
        body: dict[str, Any] = {
            "offset": offset,
            "limit": limit,
            "m365First": True,
            "sortBy": "NAME_ASC",
        }
        if keyword:
            body["filter"] = {"cloudappKeyword": keyword}
        raw = await self._session.post("/api/v1/application/cloudapp", json=body)
        apps: list[M365TenantInfo | GWSDomainInfo] = [_parse_m365_tenant(entry) for entry in raw.get("m365") or []]
        apps.extend(_parse_gws_domain(entry) for entry in raw.get("gw") or [])

        # cloudapp endpoint returns total as string — server-side bug
        raw_total = raw.get("total")
        return ListResult(apps, int(raw_total) if raw_total is not None else None)


def _parse_m365_tenant(entry: dict[str, Any]) -> M365TenantInfo:
    tenant = entry.get("tenant") or {}
    usage_info = tenant.get("dataUsageInfo") or {}
    return M365TenantInfo(
        tenant_id=tenant.get("tenantId") or "",
        name=tenant.get("tenantName") or "",
        domain=tenant.get("tenantMail") or "",
        category=WorkloadCategory.M365,
        protected_data_bytes=int(usage_info.get("dataUsage") or 0),
    )


def _parse_gws_domain(entry: dict[str, Any]) -> GWSDomainInfo:
    gw_domain = entry.get("gwDomain") or {}
    usage_info = gw_domain.get("dataUsageInfo") or {}
    domain_str = gw_domain.get("domain") or ""
    return GWSDomainInfo(
        domain=domain_str,
        name=gw_domain.get("domainName") or domain_str,
        domain_admin=gw_domain.get("adminEmail") or "",
        category=WorkloadCategory.GWS,
        protected_data_bytes=int(usage_info.get("dataUsage") or 0),
    )
