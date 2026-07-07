"""Shared fixtures and helpers for the protection-plan test files.

Imported explicitly (like tests.unit.sdk.conftest) by test_protection_plans.py
and test_protection_plans_write.py.
"""
from __future__ import annotations

from datetime import time
from typing import Any

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.collections.protection_plans import MachinePlanCollection
from synology_apm.sdk.enums import RetentionType, ScheduleFrequency, WorkloadCategory
from synology_apm.sdk.models.protection_plan import (
    M365PlanCreateRequest,
    MachinePlanCreateRequest,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from tests.unit.sdk.conftest import BASE_URL

PLAN_ID = "0c8f033b-fb57-4f46-9a9d-85e9d21c08ab"

# Machine and M365 plans share the same creation endpoint (serviceType is in the body).
PLAN_CREATE_URL = f"{BASE_URL}/api/v1/plan/backup_plan"

SAMPLE_PLAN_WITH_SCHEDULE: dict[str, Any] = {
    "id": PLAN_ID,
    "spec": {
        "name": "Daily Backup",
        "retention": {"keepDays": 30},
        "configDevice": {
            "mainSchedule": {
                "scheduleType": "SCHEDULE",
                "repeatType": "DAILY",
                "repeatHour": 0,
                "runHour": 2,
                "runMin": 30,
                "runWeekday": [],
            }
        },
        "backupCopy": {"enabled": False, "destination": ""},
    },
    "protectedWorkloadCount": 2,
    "unprotectedWorkloadCount": 1,
}


def make_collections(session: WebAPISession) -> MachinePlanCollection:
    return MachinePlanCollection(session)


def _assert_sample_machine_plan(plan: ProtectionPlan) -> None:
    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup"
    assert plan.category == WorkloadCategory.MACHINE
    assert plan.workload_count == 3
    assert plan.policy is not None
    assert plan.policy.schedule is not None
    assert plan.policy.retention.days == 30
    assert plan.policy.schedule.frequency == ScheduleFrequency.DAILY


def _assert_sample_m365_plan(plan: ProtectionPlan) -> None:
    assert plan.plan_id == PLAN_ID
    assert plan.name == "M365 Daily"
    assert plan.category == WorkloadCategory.M365
    assert plan.workload_count == 2
    assert plan.policy is not None
    assert plan.policy.retention.retention_type == RetentionType.KEEP_DAYS
    assert plan.policy.retention.days == 30


def _make_machine_request() -> MachinePlanCreateRequest:
    return MachinePlanCreateRequest(
        name="Daily Backup",
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
    )


def _make_m365_request() -> M365PlanCreateRequest:
    return M365PlanCreateRequest(
        name="M365 Daily",
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
    )
