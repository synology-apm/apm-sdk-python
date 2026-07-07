"""Shared fixtures for the m365 CLI command tests.

Imported explicitly (like tests.unit.cli.conftest) by the m365 command test
files; file-specific fixtures (group workloads, versions, export activities)
stay in their own test files.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import M365WorkloadType, RetentionType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.saas import SaasTenant
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload

TENANT_ID = "m365-tenant-uuid-001"

WORKLOAD_ID = "m365-wl-id-001"

WORKLOAD_UID = "m365-wl-uid-001"

NAMESPACE = "ns-m365-001"

SAMPLE_TENANT = SaasTenant(
    tenant_id=TENANT_ID,
    tenant_name="Contoso",
    tenant_email="admin@contoso.com",
    category=WorkloadCategory.M365,
    protected_data_bytes=0,
)

SAMPLE_WL = M365Workload(
    workload_id=WORKLOAD_ID,
    name="alice@contoso.com",
    category=WorkloadCategory.M365,
    namespace=NAMESPACE,
    last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    is_retired=False,
    protected_data_bytes=1024 * 1024 * 100,
    status=WorkloadStatus.SUCCESS,
    plan=ProtectionPlan(plan_id="plan-m365-001", name="M365 Daily", category=WorkloadCategory.M365),
    workload_type=M365WorkloadType.EXCHANGE,
    tenant_id=TENANT_ID,
    info=M365UserInfo(user_principal_name="alice@contoso.com"),
    backup_server=LocationInfo(
        is_remote_storage=False,
        identifier="ns-server-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    ),
)

SAMPLE_PLAN = ProtectionPlan(
    plan_id="m365-plan-001",
    name="M365 Daily",
    category=WorkloadCategory.M365,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=None,
    ),
    workload_count=5,
)

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="retire-plan-001",
    name="30-Day Archive",
    description="",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=False),
    workload_count=1,
)


def make_mock_apm(workloads: list[M365Workload] | None = None, tenant: SaasTenant | None = None) -> AsyncMock:
    """Build a mock APMClient with pre-configured return values."""
    mock_apm = AsyncMock()
    mock_apm.saas.list.return_value = ([SAMPLE_TENANT], 5)
    mock_apm.saas.get_m365_tenant.return_value = tenant or SAMPLE_TENANT
    mock_apm.m365.workloads.list.return_value = (workloads if workloads is not None else [SAMPLE_WL], 5)
    mock_apm.m365.workloads.get.return_value = SAMPLE_WL
    mock_apm.m365.workloads.get_by_name.return_value = SAMPLE_WL
    mock_apm.m365.workloads.lock_version.return_value = None
    mock_apm.m365.workloads.unlock_version.return_value = None
    mock_apm.m365.workloads.change_plan.return_value = None
    mock_apm.retirement_plans.get.return_value = SAMPLE_RETIREMENT_PLAN
    mock_apm.retirement_plans.get_by_name.return_value = SAMPLE_RETIREMENT_PLAN
    mock_apm.plans.get.return_value = SAMPLE_PLAN
    mock_apm.plans.get_by_name.return_value = SAMPLE_PLAN
    return mock_apm
