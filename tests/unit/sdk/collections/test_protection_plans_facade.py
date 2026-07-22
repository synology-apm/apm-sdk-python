"""Unit tests for ProtectionPlanCollection (apm.plans.*)."""
from __future__ import annotations

from datetime import time
from typing import Any

import pytest
from aiointercept import aiointercept
from yarl import URL

from synology_apm.sdk.client import APMClient
from synology_apm.sdk.enums import RetentionType, ScheduleFrequency, WorkloadCategory
from synology_apm.sdk.exceptions import APIError, PlanInUseError, ResourceNotFoundError
from synology_apm.sdk.models.protection_plan import (
    M365PlanCreateRequest,
    MachinePlanCreateRequest,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from tests.unit.sdk.conftest import BASE_URL, HOST, LOGIN_OK, LOGIN_URL, LOGOUT_OK, assert_resource_error, request_json

ME_URL = f"{BASE_URL}/api/v1/infra/backup_server/me"
ME_OK = {
    "id": "bs-me",
    "namespace": "ns-me",
    "role": "LEADER",
    "spec": {"addr": "fake-apm.test", "type": "DP"},
    "status": {
        "hostName": "APM-Server",
        "model": "DP320",
        "firmwareVer": "APM 1.2-71845",
        "serial": "SN-ME",
        "status": "NORMAL",
    },
}

SAMPLE_MACHINE_PLAN = {
    "id": "plan-machine-001",
    "spec": {
        "name": "Daily Machine Backup",
        "serviceType": "DEVICE",
        "retention": {"keepDays": 30},
    },
    "protectedWorkloadCount": 2,
    "unprotectedWorkloadCount": 0,
}

SAMPLE_M365_PLAN = {
    "id": "plan-m365-001",
    "spec": {
        "name": "M365 Daily",
        "serviceType": "M365",
        "retention": {"keepVersions": 10},
    },
    "protectedWorkloadCount": 5,
    "unprotectedWorkloadCount": 1,
}


# ── plans.list() ───────────────────────────────────────────────────────────


async def test_plans_list_machine_category() -> None:
    """plans.list(category=MACHINE) passes serviceType=DEVICE."""
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [SAMPLE_MACHINE_PLAN], "total": 1})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, total = await apm.plans.list(category=WorkloadCategory.MACHINE)

    assert len(plans) == 1
    assert plans[0].plan_id == "plan-machine-001"
    assert plans[0].name == "Daily Machine Backup"
    assert plans[0].category == WorkloadCategory.MACHINE


async def test_plans_list_m365_category() -> None:
    """plans.list(category=M365) passes serviceType=M365."""
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=M365"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [SAMPLE_M365_PLAN], "total": 1})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, total = await apm.plans.list(category=WorkloadCategory.M365)

    assert len(plans) == 1
    assert plans[0].plan_id == "plan-m365-001"
    assert plans[0].name == "M365 Daily"
    assert plans[0].category == WorkloadCategory.M365


async def test_plans_list_no_category_passes_both_service_types() -> None:
    """plans.list() without category passes both serviceType values."""
    plans_url = (
        f"{BASE_URL}/api/v1/plan/backup_plan"
        "?limit=500&offset=0&serviceType=DEVICE&serviceType=M365"
    )
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [SAMPLE_MACHINE_PLAN, SAMPLE_M365_PLAN], "total": 2})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, total = await apm.plans.list()

    assert len(plans) == 2
    categories = {p.category for p in plans}
    assert WorkloadCategory.MACHINE in categories
    assert WorkloadCategory.M365 in categories


# ── plans.get_by_name() ────────────────────────────────────────────────────


async def test_plans_get_by_name_returns_m365_plan_via_cross_category_search() -> None:
    """plans.get_by_name(name) uses a single cross-category API call and returns exact match."""
    keyword_url = (
        f"{BASE_URL}/api/v1/plan/backup_plan"
        "?keyword=M365+Daily&limit=100&offset=0&serviceType=DEVICE&serviceType=M365"
    )
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(keyword_url, payload={"plans": [SAMPLE_M365_PLAN], "total": 1})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plan = await apm.plans.get_by_name("M365 Daily")

    assert plan.plan_id == "plan-m365-001"
    assert plan.category == WorkloadCategory.M365


async def test_plans_get_by_name_raises_not_found_when_no_match() -> None:
    """plans.get_by_name(name) raises ResourceNotFoundError when no matching plan exists."""
    keyword_url = (
        f"{BASE_URL}/api/v1/plan/backup_plan"
        "?keyword=Non-Existent&limit=100&offset=0&serviceType=DEVICE&serviceType=M365"
    )
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(keyword_url, payload={"plans": [], "total": 0})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            with pytest.raises(ResourceNotFoundError) as exc_info:
                await apm.plans.get_by_name("Non-Existent")

    assert exc_info.value.resource_type == "ProtectionPlan"
    assert exc_info.value.resource_id == "Non-Existent"


async def test_plans_get_by_name_match_is_case_insensitive() -> None:
    """plans.get_by_name(name) matches plan names case-insensitively."""
    keyword_url = (
        f"{BASE_URL}/api/v1/plan/backup_plan"
        "?keyword=daily+machine+backup&limit=100&offset=0&serviceType=DEVICE&serviceType=M365"
    )
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(keyword_url, payload={"plans": [SAMPLE_MACHINE_PLAN], "total": 1})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plan = await apm.plans.get_by_name("daily machine backup")

    assert plan.plan_id == "plan-machine-001"


# ── plans.get() ────────────────────────────────────────────────────────────


async def test_plans_get_calls_direct_endpoint() -> None:
    """plans.get(id) calls GET /api/v1/plan/backup_plan/{id} and returns the parsed plan."""
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/plan-machine-001", payload=SAMPLE_MACHINE_PLAN)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plan = await apm.plans.get("plan-machine-001")

    assert plan.plan_id == "plan-machine-001"
    assert plan.name == "Daily Machine Backup"
    assert plan.category == WorkloadCategory.MACHINE


# ── _build_location_cache — remote storage branch ─────────────────────────


async def test_plans_list_resolves_external_storage_copy_destination() -> None:
    """list() resolves backup copy destinations with non-APPLIANCE destinationType via /api/v1/external_storage/{id}."""
    plan_with_copy = {
        "id": "plan-copy-001",
        "spec": {
            "name": "Cloud Copy Plan",
            "serviceType": "DEVICE",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destination": "ext-001",
                "destinationType": "CLOUD",
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    ext_url = f"{BASE_URL}/api/v1/external_storage/ext-001"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [plan_with_copy], "total": 1})
        m.get(
            ext_url,
            payload={
                "displayName": "S3 Bucket",
                "id": "ext-001",
                "endpoint": "s3.amazonaws.com",
                "vaultName": "my-vault",
            },
        )
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, _ = await apm.machine.plans.list()

    assert len(plans) == 1
    assert plans[0].backup_copy_policy is not None
    dest = plans[0].backup_copy_policy.destination
    assert dest.is_remote_storage is True
    assert dest.name == "S3 Bucket"
    assert dest.identifier == "ext-001"
    assert dest.endpoint == "s3.amazonaws.com"
    assert dest.vault == "my-vault"


async def test_plans_list_external_storage_empty_name_yields_no_destination() -> None:
    """list() sets backup_copy_policy=None when the external storage response has no displayName."""
    plan_with_copy = {
        "id": "plan-copy-002",
        "spec": {
            "name": "Cloud Copy Plan 2",
            "serviceType": "DEVICE",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destination": "ext-002",
                "destinationType": "CLOUD",
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    ext_url = f"{BASE_URL}/api/v1/external_storage/ext-002"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [plan_with_copy], "total": 1})
        m.get(ext_url, payload={"displayName": "", "id": "ext-002"})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, _ = await apm.machine.plans.list()

    assert plans[0].backup_copy_policy is None


async def test_plans_list_external_storage_error_propagates() -> None:
    """list() propagates a server error from the external storage lookup instead of dropping the destination."""
    plan_with_copy = {
        "id": "plan-copy-003",
        "spec": {
            "name": "Cloud Copy Plan 3",
            "serviceType": "DEVICE",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destination": "ext-003",
                "destinationType": "CLOUD",
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    ext_url = f"{BASE_URL}/api/v1/external_storage/ext-003"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [plan_with_copy], "total": 1})
        m.get(ext_url, status=500, payload={"error": "server error"})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            with pytest.raises(APIError):
                await apm.machine.plans.list()


async def test_plans_list_deleted_external_storage_yields_no_destination() -> None:
    """list() sets backup_copy_policy=None when the copy destination no longer exists."""
    plan_with_copy = {
        "id": "plan-copy-004",
        "spec": {
            "name": "Cloud Copy Plan 4",
            "serviceType": "DEVICE",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destination": "ext-004",
                "destinationType": "CLOUD",
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    ext_url = f"{BASE_URL}/api/v1/external_storage/ext-004"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [plan_with_copy], "total": 1})
        m.get(ext_url, status=404)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, _ = await apm.machine.plans.list()

    assert plans[0].backup_copy_policy is None


# ── _build_location_cache — APPLIANCE branch ──────────────────────────────


async def test_plans_list_resolves_appliance_copy_destination() -> None:
    """list() resolves APPLIANCE backup copy destinations via backup server list, looked up by namespace."""
    plan_with_copy = {
        "id": "plan-copy-bs-001",
        "spec": {
            "name": "Appliance Copy Plan",
            "serviceType": "DEVICE",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destination": "ns-backup-01",
                "destinationType": "APPLIANCE",
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [plan_with_copy], "total": 1})
        m.get(
            servers_url,
            payload={
                "backupServers": [
                    {
                        "id": "bs-001",
                        "namespace": "ns-backup-01",
                        "spec": {"addr": "192.168.1.10", "type": "DP"},
                        "status": {"hostName": "BackupServer-01"},
                    }
                ],
                "total": 1,
            },
        )
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, _ = await apm.machine.plans.list()

    assert len(plans) == 1
    assert plans[0].backup_copy_policy is not None
    dest = plans[0].backup_copy_policy.destination
    assert dest.is_remote_storage is False
    assert dest.name == "BackupServer-01"
    assert dest.identifier == "ns-backup-01"
    assert dest.endpoint == "192.168.1.10"
    assert dest.vault is None


async def test_plans_list_appliance_namespace_not_in_server_list_yields_no_destination() -> None:
    """list() sets backup_copy_policy=None when the plan's destination namespace is not in the server list."""
    plan_with_copy = {
        "id": "plan-copy-bs-002",
        "spec": {
            "name": "Appliance Copy Plan 2",
            "serviceType": "DEVICE",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destination": "ns-unknown",
                "destinationType": "APPLIANCE",
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [plan_with_copy], "total": 1})
        m.get(servers_url, payload={"backupServers": [], "total": 0})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, _ = await apm.machine.plans.list()

    assert plans[0].backup_copy_policy is None


async def test_plans_list_appliance_server_list_error_propagates() -> None:
    """list() propagates a server error from the backup server lookup instead of dropping the destination."""
    plan_with_copy = {
        "id": "plan-copy-bs-003",
        "spec": {
            "name": "Appliance Copy Plan 3",
            "serviceType": "DEVICE",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destination": "ns-backup-03",
                "destinationType": "APPLIANCE",
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    plans_url = f"{BASE_URL}/api/v1/plan/backup_plan?limit=500&offset=0&serviceType=DEVICE"
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [plan_with_copy], "total": 1})
        m.get(servers_url, status=500, payload={"error": "server error"})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            with pytest.raises(APIError):
                await apm.machine.plans.list()


async def test_plans_list_name_contains_passes_keyword_param() -> None:
    """list(name_contains=...) passes the keyword parameter to the API."""
    plans_url = (
        f"{BASE_URL}/api/v1/plan/backup_plan"
        "?keyword=Daily&limit=500&offset=0&serviceType=DEVICE&serviceType=M365"
    )
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.get(plans_url, payload={"plans": [SAMPLE_MACHINE_PLAN], "total": 1})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plans, total = await apm.plans.list(name_contains="Daily")

    assert total == 1
    assert plans[0].name == "Daily Machine Backup"


# ── plans.create() ─────────────────────────────────────────────────────────


async def test_plans_create_with_machine_request_dispatches_to_device_body() -> None:
    """apm.plans.create(MachinePlanCreateRequest) POSTs with serviceType=DEVICE."""
    create_url = f"{BASE_URL}/api/v1/plan/backup_plan"
    get_url = f"{BASE_URL}/api/v1/plan/backup_plan/plan-machine-001"
    _retention = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30)
    _schedule = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.post(create_url, payload={"id": "plan-machine-001"})
        m.get(get_url, payload=SAMPLE_MACHINE_PLAN)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plan = await apm.plans.create(MachinePlanCreateRequest(
                name="Daily Machine Backup",
                retention=_retention,
                schedule=_schedule,
            ))

    post_key = ("POST", URL(create_url))
    body = request_json(m, post_key)
    assert body["plan"]["serviceType"] == "DEVICE"
    assert body["plan"]["retention"]["keepDays"] == 30
    assert body["plan"]["configDevice"]["mainSchedule"]["runHour"] == 9
    assert plan.plan_id == "plan-machine-001"
    assert plan.name == "Daily Machine Backup"


async def test_plans_create_with_m365_request_dispatches_to_m365_body() -> None:
    """apm.plans.create(M365PlanCreateRequest) POSTs with serviceType=M365."""
    create_url = f"{BASE_URL}/api/v1/plan/backup_plan"
    get_url = f"{BASE_URL}/api/v1/plan/backup_plan/plan-m365-001"
    _retention = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30)
    _schedule = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.post(create_url, payload={"id": "plan-m365-001"})
        m.get(get_url, payload=SAMPLE_M365_PLAN)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            plan = await apm.plans.create(M365PlanCreateRequest(
                name="M365 Daily",
                retention=_retention,
                schedule=_schedule,
            ))

    post_key = ("POST", URL(create_url))
    body = request_json(m, post_key)
    assert body["plan"]["serviceType"] == "M365"
    assert body["plan"]["retention"]["keepDays"] == 30
    assert body["plan"]["configM365"]["schedule"]["runHour"] == 9
    assert plan.plan_id == "plan-m365-001"
    assert plan.name == "M365 Daily"


# ── plans.delete() ─────────────────────────────────────────────────────────


async def test_plans_delete_sends_delete_request() -> None:
    """apm.plans.delete(plan_id) sends DELETE to /api/v1/plan/backup_plan/{plan_id}."""
    delete_url = f"{BASE_URL}/api/v1/plan/backup_plan/plan-machine-001"
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.delete(delete_url, payload={})
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            await apm.plans.delete("plan-machine-001")

    assert ("DELETE", URL(delete_url)) in m.requests


async def test_plans_delete_plan_in_use_raises_plan_in_use_error() -> None:
    """apm.plans.delete(plan_id) raises PlanInUseError when the plan is assigned to workloads."""
    delete_url = f"{BASE_URL}/api/v1/plan/backup_plan/plan-machine-001"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4019}]}
    }
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        m.get(ME_URL, payload=ME_OK)
        m.delete(delete_url, status=500, payload=error_body)
        m.get(f"{BASE_URL}/api/v1/preference/logout", payload=LOGOUT_OK)
        async with APMClient(HOST, "user", "pass", verify_ssl=False) as apm:
            with pytest.raises(PlanInUseError) as exc_info:
                await apm.plans.delete("plan-machine-001")

    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="plan-machine-001")
    err = exc_info.value
    assert err.has_workloads is True
    assert err.has_server_template is False
