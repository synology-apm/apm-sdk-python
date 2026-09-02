"""Shared fixtures for the gws CLI command tests.

Imported explicitly (like tests.unit.cli.conftest) by the gws command test
files; file-specific fixtures (shared drive workloads, versions) stay in
their own test files.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from synology_apm.sdk.enums import GWSWorkloadType, RetentionType, WorkloadCategory, WorkloadStatus
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.saas import GWSDomainInfo
from synology_apm.sdk.models.workload import GWSUserInfo, GWSWorkload

DOMAIN_ID = "gwsdemo.example.com"

WORKLOAD_ID = "gws-wl-id-001"

WORKLOAD_UID = "gws-wl-uid-001"

NAMESPACE = "ns-gws-001"

SAMPLE_TENANT = GWSDomainInfo(
    domain=DOMAIN_ID,
    name=DOMAIN_ID,
    domain_admin="evelyn.test@gwsdemo.example.com",
    category=WorkloadCategory.GWS,
    protected_data_bytes=0,
)

SAMPLE_WL = GWSWorkload(
    workload_id=WORKLOAD_ID,
    name="alice@gwsdemo.example.com",
    category=WorkloadCategory.GWS,
    namespace=NAMESPACE,
    last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    is_retired=False,
    protected_data_bytes=1024 * 1024 * 100,
    status=WorkloadStatus.SUCCESS,
    plan=ProtectionPlan(plan_id="plan-gws-001", name="GWS Daily", category=WorkloadCategory.GWS),
    workload_type=GWSWorkloadType.MAIL,
    domain=DOMAIN_ID,
    info=GWSUserInfo(email="alice@gwsdemo.example.com"),
    backup_server=LocationInfo(
        is_remote_storage=False,
        identifier="ns-server-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    ),
)

SAMPLE_PLAN = ProtectionPlan(
    plan_id="gws-plan-001",
    name="GWS Daily",
    category=WorkloadCategory.GWS,
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


def make_mock_apm(workloads: list[GWSWorkload] | None = None, tenant: GWSDomainInfo | None = None) -> AsyncMock:
    """Build a mock APMClient with pre-configured return values."""
    mock_apm = AsyncMock()
    mock_apm.saas.list.return_value = ([SAMPLE_TENANT], 5)
    mock_apm.saas.get_gws_domain.return_value = tenant or SAMPLE_TENANT
    mock_apm.gws.workloads.list.return_value = (workloads if workloads is not None else [SAMPLE_WL], 5)
    mock_apm.gws.workloads.get.return_value = SAMPLE_WL
    mock_apm.gws.workloads.get_by_name.return_value = SAMPLE_WL
    mock_apm.gws.workloads.lock_version.return_value = None
    mock_apm.gws.workloads.unlock_version.return_value = None
    mock_apm.gws.workloads.change_plan.return_value = None
    mock_apm.retirement_plans.get.return_value = SAMPLE_RETIREMENT_PLAN
    mock_apm.retirement_plans.get_by_name.return_value = SAMPLE_RETIREMENT_PLAN
    mock_apm.plans.get.return_value = SAMPLE_PLAN
    mock_apm.plans.get_by_name.return_value = SAMPLE_PLAN
    return mock_apm
