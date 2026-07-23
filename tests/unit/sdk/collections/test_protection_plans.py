"""Unit tests for ProtectionPlanCollection read/parse paths (list / get / field parsing)."""
from __future__ import annotations

from datetime import time
from typing import Any

import pytest
from aiointercept import aiointercept
from yarl import URL

from synology_apm.sdk.collections.protection_plans import MachinePlanCollection
from synology_apm.sdk.enums import (
    CopyReason,
    RetentionType,
    ScheduleFrequency,
    VersionCopyStatus,
    WeekDay,
    WorkloadCategory,
)
from synology_apm.sdk.exceptions import PlanNameConflictError, ResourceNotFoundError
from tests.unit.sdk.collections._plan_fixtures import (
    PLAN_ID,
    SAMPLE_PLAN_WITH_SCHEDULE,
    _make_machine_request,
    make_collections,
)
from tests.unit.sdk.conftest import (
    BASE_URL,
    LOGIN_OK,
    LOGIN_URL,
    assert_resource_error,
    connected_session,
    make_session,
)

PLANS_URL = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=DEVICE"

SAMPLE_PLAN_RAW: dict[str, Any] = {
    "id": PLAN_ID,
    "spec": {
        "name": "Daily Backup",
        "retention": {"keepDays": 30},
        "backupCopy": {"enabled": False, "destination": ""},
    },
    "protectedWorkloadCount": 2,
    "unprotectedWorkloadCount": 1,
}

COPY_DEST_ID = "0903a27c-35e3-483e-bda4-8c8c77475fb9"
REMOTE_DEST_ID = "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"

SAMPLE_PLAN_WITH_COPY_APPLIANCE: dict[str, Any] = {
    "id": "copy-plan-001",
    "spec": {
        "name": "Machine Copy Plan",
        "retention": {"keepDays": 30},
        "backupCopy": {
            "enabled": True,
            "destinationType": "APPLIANCE",
            "destination": COPY_DEST_ID,
            "schedule": {
                "scheduleType": "EVENT",
                "repeatType": "ONCE",
                "runWeekday": [],
                "repeatHour": 0,
                "runHour": 20,
                "runMin": 0,
            },
            "retention": {"keepDays": 1},
        },
    },
    "protectedWorkloadCount": 1,
    "unprotectedWorkloadCount": 0,
}

SAMPLE_PLAN_WITH_COPY_REMOTE: dict[str, Any] = {
    "id": "copy-plan-002",
    "spec": {
        "name": "Remote Copy Plan",
        "retention": {"keepDays": 30},
        "backupCopy": {
            "enabled": True,
            "destinationType": "ACTIVE_BACKUP_ENTERPRISE_VAULT",
            "destination": REMOTE_DEST_ID,
            "schedule": {
                "scheduleType": "EVENT",
                "repeatType": "ONCE",
                "runWeekday": [],
                "repeatHour": 0,
                "runHour": 20,
                "runMin": 0,
            },
            "retention": {"keepDays": 7},
        },
    },
    "protectedWorkloadCount": 0,
    "unprotectedWorkloadCount": 1,
}

BACKUP_SERVER_RAW: dict[str, Any] = {
    "id": "bs-server-001",
    "namespace": COPY_DEST_ID,  # plan's destination field contains the server's namespace
    "spec": {"addr": "192.168.1.10"},
    "status": {"hostName": "My NAS"},
}

REMOTE_STORAGE_RAW: dict[str, Any] = {
    "id": REMOTE_DEST_ID,
    "displayName": "APV Vault",
    "endpoint": "apv.example.com",
    "vaultName": "my-bucket",
}

# A plan whose backupCopy is JSON null (key present, value null) — distinct from an
# absent key or {"enabled": false}. Observed on real servers for locked FS plans.
SAMPLE_PLAN_NULL_COPY: dict[str, Any] = {
    "id": "null-copy-plan-001",
    "spec": {
        "name": "Null Copy Plan",
        "retention": {"keepDays": 30},
        "backupCopy": None,
    },
    "protectedWorkloadCount": 1,
    "unprotectedWorkloadCount": 0,
}




# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_parses_plan_fields() -> None:
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        plans = make_collections(session)
        result, total = await plans.list()
        await session.disconnect()

    assert total == 1
    plan = result[0]
    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup"
    assert plan.category == WorkloadCategory.MACHINE
    assert plan.workload_count == 3  # 2 protected + 1 unprotected
    assert plan.policy is not None
    assert plan.policy.retention.retention_type == RetentionType.KEEP_DAYS
    assert plan.policy.retention.days == 30
    assert plan.policy.schedule is None  # list() does not include schedule info
    assert plan.backup_copy_policy is None


async def test_list_returns_empty_for_no_plans() -> None:
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [], "total": 0})
        plans = make_collections(session)
        result, total = await plans.list()
        await session.disconnect()

    assert result == []


# ── get() by id ────────────────────────────────────────────────────────────


async def test_get_calls_direct_endpoint() -> None:
    """get() should call GET /api/v1/plan/backup_plan/{id} directly."""
    async with connected_session() as (session, m):

        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}",
              payload=SAMPLE_PLAN_WITH_SCHEDULE)
        plans = make_collections(session)
        plan = await plans.get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup"


async def test_get_parses_schedule() -> None:
    """get() should correctly parse schedule info into policy.schedule."""
    async with connected_session() as (session, m):

        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}",
              payload=SAMPLE_PLAN_WITH_SCHEDULE)
        plans = make_collections(session)
        plan = await plans.get(PLAN_ID)
        await session.disconnect()

    assert plan.policy is not None
    assert plan.policy.schedule is not None
    assert plan.policy.schedule.frequency == ScheduleFrequency.DAILY
    assert plan.policy.schedule.start_time is not None
    assert plan.policy.schedule.start_time.hour == 2
    assert plan.policy.schedule.start_time.minute == 30


# ── get_by_name() ──────────────────────────────────────────────────────────


async def test_get_by_name_resolves_via_list() -> None:
    """get_by_name(name) performs server-side list(name_contains=name) search and returns directly without calling get()."""
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=Daily+Backup&limit=100&offset=0&serviceType=DEVICE"
        m.get(keyword_url, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        plans = make_collections(session)
        plan = await plans.get_by_name("Daily Backup")
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup"


async def test_get_by_name_raises_not_found_when_missing() -> None:
    async with connected_session() as (session, m):

        keyword_url = f"{BASE_URL}/api/v1/plan/backup_plan?keyword=Non-existent+Plan&limit=100&offset=0&serviceType=DEVICE"
        m.get(keyword_url, payload={"plans": [], "total": 0})
        plans = make_collections(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await plans.get_by_name("Non-existent Plan")
        await session.disconnect()

    assert exc_info.value.resource_type == "ProtectionPlan"
    assert exc_info.value.resource_id == "Non-existent Plan"


# ── retention parsing ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "retention_raw, expected_type, checks",
    [
        (
            {"keepVersions": 7},
            RetentionType.KEEP_VERSIONS,
            {"versions": 7},
        ),
        (
            {"keepAll": True},
            RetentionType.KEEP_ALL,
            {},
        ),
        (
            {"keepDays": 30, "keepVersions": 5},
            RetentionType.KEEP_ADVANCED,
            {"days": 30, "versions": 5, "gfs_not_none": True},
        ),
        (
            {"gfsDays": 7, "gfsWeeks": 4, "gfsMonths": 12, "gfsYears": 1},
            RetentionType.KEEP_ADVANCED,
            {
                "days_none": True,
                "versions_none": True,
                "gfs": {"daily_versions": 7, "weekly_versions": 4, "monthly_versions": 12, "yearly_versions": 1},
            },
        ),
    ],
    ids=["keep_versions", "keep_all", "keep_advanced_days_and_versions", "keep_advanced_gfs"],
)
async def test_get_parses_retention_type(
    retention_raw: dict[str, Any],
    expected_type: RetentionType,
    checks: dict[str, Any],
) -> None:
    """Retention fields are parsed to the correct RetentionType and associated field values."""
    plan_raw = {
        "id": PLAN_ID,
        "spec": {
            "name": "Plan",
            "retention": retention_raw,
            "backupCopy": {"enabled": False, "destination": ""},
        },
        "protectedWorkloadCount": 0,
        "unprotectedWorkloadCount": 0,
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan_raw], "total": 1})
        plans = make_collections(session)
        result, total = await plans.list()
        await session.disconnect()

    policy = result[0].policy
    assert policy is not None
    r = policy.retention
    assert r.retention_type == expected_type
    if "days" in checks:
        assert r.days == checks["days"]
    if "days_none" in checks:
        assert r.days is None
    if "versions" in checks:
        assert r.versions == checks["versions"]
    if "versions_none" in checks:
        assert r.versions is None
    if "gfs_not_none" in checks:
        assert r.gfs is not None
    if "gfs" in checks:
        assert r.gfs is not None
        for attr, val in checks["gfs"].items():
            assert getattr(r.gfs, attr) == val


# ── backup copy parsing ─────────────────────────────────────────────────────


async def test_list_copy_none_when_disabled() -> None:
    """When backupCopy.enabled=false, backup_copy_policy should be None."""
    async with connected_session() as (session, m):

        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    assert result[0].backup_copy_policy is None


async def test_list_parses_backup_copy_policy_with_appliance_destination() -> None:
    """When backupCopy.enabled=true with APPLIANCE type, backup_copy_policy should be populated
    with destination resolved via backup server list (looked up by namespace)."""
    session = make_session()
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    list_url = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=DEVICE"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

        m.get(list_url, payload={"plans": [SAMPLE_PLAN_WITH_COPY_APPLIANCE], "total": 1})
        m.get(servers_url, payload={"backupServers": [BACKUP_SERVER_RAW], "total": 1})

        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    plan = result[0]
    assert plan.backup_copy_policy is not None
    assert plan.backup_copy_policy.retention.retention_type == RetentionType.KEEP_DAYS
    assert plan.backup_copy_policy.retention.days == 1
    assert plan.backup_copy_policy.schedule.frequency == ScheduleFrequency.AFTER_BACKUP
    assert plan.backup_copy_policy.destination.is_remote_storage is False
    assert plan.backup_copy_policy.destination.name == "My NAS"
    assert plan.backup_copy_policy.destination.identifier == COPY_DEST_ID
    assert plan.backup_copy_policy.destination.endpoint == "192.168.1.10"


async def test_list_parses_backup_copy_policy_with_remote_storage() -> None:
    """When backupCopy.enabled=true with non-APPLIANCE type, the external_storage API should be queried."""
    session = make_session()
    storage_url = f"{BASE_URL}/api/v1/external_storage/{REMOTE_DEST_ID}"
    list_url = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=DEVICE"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

        m.get(list_url, payload={"plans": [SAMPLE_PLAN_WITH_COPY_REMOTE], "total": 1})
        m.get(storage_url, payload=REMOTE_STORAGE_RAW)

        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    plan = result[0]
    assert plan.backup_copy_policy is not None
    assert plan.backup_copy_policy.destination.is_remote_storage is True
    assert plan.backup_copy_policy.destination.name == "APV Vault"
    assert plan.backup_copy_policy.destination.identifier == REMOTE_DEST_ID
    assert plan.backup_copy_policy.destination.endpoint == "apv.example.com"
    assert plan.backup_copy_policy.destination.vault == "my-bucket"


async def test_list_deduplicates_destination_lookups() -> None:
    """Multiple plans sharing the same APPLIANCE destination trigger only one backup server list call."""
    plan_a = {**SAMPLE_PLAN_WITH_COPY_APPLIANCE, "id": "plan-a"}
    plan_b = {**SAMPLE_PLAN_WITH_COPY_APPLIANCE, "id": "plan-b"}
    session = make_session()
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    list_url = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=DEVICE"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

        m.get(list_url, payload={"plans": [plan_a, plan_b], "total": 2})
        m.get(servers_url, payload={"backupServers": [BACKUP_SERVER_RAW], "total": 1})

        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    assert len(result) == 2
    assert result[0].backup_copy_policy is not None
    assert result[1].backup_copy_policy is not None
    assert result[0].backup_copy_policy.destination.name == result[1].backup_copy_policy.destination.name

    servers_key = ("GET", URL(servers_url))
    assert len(m.requests[servers_key]) == 1


async def test_list_survives_null_backup_copy() -> None:
    """A plan whose backupCopy is JSON null must not crash list(); it yields backup_copy_policy=None
    while a sibling plan with a valid APPLIANCE copy destination is still resolved."""
    session = make_session()
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    list_url = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=DEVICE"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

        m.get(list_url, payload={
            "plans": [SAMPLE_PLAN_NULL_COPY, SAMPLE_PLAN_WITH_COPY_APPLIANCE], "total": 2,
        })
        m.get(servers_url, payload={"backupServers": [BACKUP_SERVER_RAW], "total": 1})

        plans = make_collections(session)
        result, total = await plans.list()
        await session.disconnect()

    assert total == 2
    null_plan = next(p for p in result if p.plan_id == "null-copy-plan-001")
    copy_plan = next(p for p in result if p.plan_id == "copy-plan-001")
    assert null_plan.backup_copy_policy is None
    assert copy_plan.backup_copy_policy is not None
    assert copy_plan.backup_copy_policy.destination.name == "My NAS"


async def test_get_survives_null_backup_copy() -> None:
    """get() of a plan whose backupCopy is JSON null must not crash; backup_copy_policy is None."""
    async with connected_session() as (session, m):

        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_NULL_COPY)
        plans = make_collections(session)
        plan = await plans.get(PLAN_ID)
        await session.disconnect()

    assert plan.name == "Null Copy Plan"
    assert plan.backup_copy_policy is None


async def test_list_backup_copy_policy_none_when_lookup_fails() -> None:
    """When the backup server list returns no matching namespace, backup_copy_policy is None
    and the plan still returns normally."""
    session = make_session()
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    list_url = f"{BASE_URL}/api/v1/plan/backup_plan?offset=0&limit=500&serviceType=DEVICE"

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

        m.get(list_url, payload={"plans": [SAMPLE_PLAN_WITH_COPY_APPLIANCE], "total": 1})
        m.get(servers_url, payload={"backupServers": [], "total": 0})  # no matching namespace

        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    plan = result[0]
    assert plan.backup_copy_policy is None   # destination lookup failed → whole backup_copy_policy is None


@pytest.mark.parametrize(
    "raw_schedule, expected_frequency, expected_start_time, expected_weekdays",
    [
        (
            {"scheduleType": "NONE", "repeatType": "DAILY", "runWeekday": [], "repeatHour": 0, "runHour": 0, "runMin": 0},
            ScheduleFrequency.MANUAL,
            None,
            None,
        ),
        (
            {"scheduleType": "SCHEDULE", "repeatType": "WEEKLY", "runWeekday": [1, 3, 5], "repeatHour": 0, "runHour": 10, "runMin": 0},
            ScheduleFrequency.WEEKLY,
            time(10, 0),
            [WeekDay.MONDAY, WeekDay.WEDNESDAY, WeekDay.FRIDAY],
        ),
        (
            {"scheduleType": "SCHEDULE", "repeatType": "DAILY", "runWeekday": [], "repeatHour": 1, "runHour": 0, "runMin": 30},
            ScheduleFrequency.HOURLY,
            time(0, 30),
            None,
        ),
    ],
    ids=["manual", "weekly", "hourly"],
)
async def test_get_parses_schedule_frequency(
    raw_schedule: dict[str, Any],
    expected_frequency: ScheduleFrequency,
    expected_start_time: time | None,
    expected_weekdays: list[WeekDay] | None,
) -> None:
    """Schedule frequency and start time are parsed correctly from the raw schedule dict."""
    plan_raw = {
        **SAMPLE_PLAN_WITH_SCHEDULE,
        "spec": {
            **SAMPLE_PLAN_WITH_SCHEDULE["spec"],
            "configDevice": {"mainSchedule": raw_schedule},
        },
    }
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=plan_raw)
        plans = make_collections(session)
        plan = await plans.get(PLAN_ID)
        await session.disconnect()

    assert plan.policy is not None
    sched = plan.policy.schedule
    assert sched is not None
    assert sched.frequency == expected_frequency
    if expected_start_time is None:
        assert sched.start_time is None
    else:
        assert sched.start_time == expected_start_time
    if expected_weekdays is not None:
        for day in expected_weekdays:
            assert day in sched.weekdays


_PLAN_WITH_SAE_TASK: dict[str, Any] = {
    "id": PLAN_ID,
    "spec": {
        "name": "Daily Backup",
        "retention": {"keepDays": 30},
        "backupCopy": {"enabled": False, "destination": ""},
        "configDevice": {
            "mainSchedule": {"scheduleType": "SCHEDULE", "repeatType": "DAILY",
                             "repeatHour": 0, "runHour": 9, "runMin": 0, "runWeekday": []},
            "task": [
                {
                    "workloadType": "PC", "osType": "WINDOWS", "useMainSchedule": False,
                    "schedule": {
                        "scheduleType": "SCHEDULE_AND_EVENT", "repeatType": "DAILY",
                        "repeatHour": 0, "runWeekday": [5], "runHour": 9, "runMin": 0,
                        "logOff": True, "screenLock": True, "startup": True,
                        "periodBase": "HOUR", "periodLength": 1,
                    },
                },
            ],
        },
    },
    "protectedWorkloadCount": 0,
    "unprotectedWorkloadCount": 0,
}

_PLAN_WITH_SAE_WEEKLY_TASK: dict[str, Any] = {
    **_PLAN_WITH_SAE_TASK,
    "spec": {
        **_PLAN_WITH_SAE_TASK["spec"],
        "configDevice": {
            **_PLAN_WITH_SAE_TASK["spec"]["configDevice"],
            "task": [
                {
                    "workloadType": "PC", "osType": "WINDOWS", "useMainSchedule": False,
                    "schedule": {
                        "scheduleType": "SCHEDULE_AND_EVENT", "repeatType": "WEEKLY",
                        "repeatHour": 0, "runWeekday": [1, 3, 5], "runHour": 9, "runMin": 30,
                        "logOff": True, "screenLock": False, "startup": True,
                        "periodBase": "HOUR", "periodLength": 2,
                    },
                },
            ],
        },
    },
}

_PLAN_WITH_EVENT_ONLY_TASK: dict[str, Any] = {
    **_PLAN_WITH_SAE_TASK,
    "spec": {
        **_PLAN_WITH_SAE_TASK["spec"],
        "configDevice": {
            **_PLAN_WITH_SAE_TASK["spec"]["configDevice"],
            "task": [
                {
                    "workloadType": "PC", "osType": "WINDOWS", "useMainSchedule": False,
                    "schedule": {
                        "scheduleType": "EVENT",
                        "logOff": True, "screenLock": False, "startup": True,
                        "periodBase": "MIN", "periodLength": 30,
                    },
                },
            ],
        },
    },
}


async def test_task_schedule_parses_schedule_and_event_daily() -> None:
    """scheduleType=SCHEDULE_AND_EVENT with repeatType=DAILY parses to DAILY time_schedule + event_trigger."""
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=_PLAN_WITH_SAE_TASK)
        col = MachinePlanCollection(session)
        plan = await col.get(PLAN_ID)
        await session.disconnect()

    assert plan.tasks is not None
    pc_task = plan.tasks[0]
    assert pc_task.schedule is not None
    ts = pc_task.schedule.time_schedule
    assert ts is not None
    assert ts.frequency == ScheduleFrequency.DAILY
    assert ts.start_time is not None
    assert ts.start_time.hour == 9
    assert ts.start_time.minute == 0
    et = pc_task.schedule.event_trigger
    assert et is not None
    assert et.on_sign_out is True
    assert et.on_lock is True
    assert et.on_startup is True


async def test_task_schedule_parses_schedule_and_event_weekly() -> None:
    """scheduleType=SCHEDULE_AND_EVENT with repeatType=WEEKLY parses to WEEKLY time_schedule + event_trigger."""
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=_PLAN_WITH_SAE_WEEKLY_TASK)
        col = MachinePlanCollection(session)
        plan = await col.get(PLAN_ID)
        await session.disconnect()

    assert plan.tasks is not None
    pc_task = plan.tasks[0]
    assert pc_task.schedule is not None
    ts = pc_task.schedule.time_schedule
    assert ts is not None
    assert ts.frequency == ScheduleFrequency.WEEKLY
    assert WeekDay.MONDAY in ts.weekdays
    assert WeekDay.WEDNESDAY in ts.weekdays
    assert WeekDay.FRIDAY in ts.weekdays
    assert ts.start_time is not None
    assert ts.start_time.hour == 9
    assert ts.start_time.minute == 30
    et = pc_task.schedule.event_trigger
    assert et is not None
    assert et.on_sign_out is True
    assert et.on_startup is True


async def test_retention_none_when_no_keep_values() -> None:
    """Retention with no keep fields set should map to RetentionType.NONE."""
    plan_no_retention = {
        **SAMPLE_PLAN_RAW,
        "spec": {**SAMPLE_PLAN_RAW["spec"], "retention": {}},
    }
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=plan_no_retention)
        plans = make_collections(session)
        plan = await plans.get(PLAN_ID)
        await session.disconnect()

    assert plan.policy is not None
    assert plan.policy.retention.retention_type == RetentionType.NONE
    assert plan.policy.retention.days is None


async def test_task_schedule_parses_event_only() -> None:
    """scheduleType=EVENT parses to time_schedule=None + event_trigger set."""
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=_PLAN_WITH_EVENT_ONLY_TASK)
        col = MachinePlanCollection(session)
        plan = await col.get(PLAN_ID)
        await session.disconnect()

    assert plan.tasks is not None
    pc_task = plan.tasks[0]
    assert pc_task.schedule is not None
    assert pc_task.schedule.time_schedule is None
    et = pc_task.schedule.event_trigger
    assert et is not None
    assert et.on_sign_out is True
    assert et.on_lock is False
    assert et.on_startup is True
    assert et.min_interval.total_seconds() == 30 * 60


async def test_task_schedule_no_events_parses_event_trigger_as_none() -> None:
    """scheduleType=SCHEDULE with no event flags parses to event_trigger=None."""
    plan_raw: dict[str, Any] = {
        **_PLAN_WITH_SAE_TASK,
        "spec": {
            **_PLAN_WITH_SAE_TASK["spec"],
            "configDevice": {
                **_PLAN_WITH_SAE_TASK["spec"]["configDevice"],
                "task": [
                    {
                        "workloadType": "PC", "osType": "WINDOWS", "useMainSchedule": False,
                        "schedule": {
                            "scheduleType": "SCHEDULE", "repeatType": "DAILY",
                            "repeatHour": 0, "runWeekday": [5], "runHour": 9, "runMin": 0,
                            "logOff": False, "screenLock": False, "startup": False,
                            "periodBase": "HOUR", "periodLength": 1,
                        },
                    },
                ],
            },
        },
    }
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=plan_raw)
        col = MachinePlanCollection(session)
        plan = await col.get(PLAN_ID)
        await session.disconnect()

    assert plan.tasks is not None
    pc_task = plan.tasks[0]
    assert pc_task.schedule is not None
    ts = pc_task.schedule.time_schedule
    assert ts is not None
    assert ts.frequency == ScheduleFrequency.DAILY
    assert pc_task.schedule.event_trigger is None


# ── backup copy status parsing ──────────────────────────────────────────────


_COPY_SKIP: Any = object()  # sentinel: skip this field assertion in copy-status parametrize


@pytest.mark.parametrize(
    "copy_status_dict, expected_status, expected_reason, expected_pending, expected_remaining",
    [
        # backupCopyStatus field absent → backup_copy_status is None
        (None, None, _COPY_SKIP, _COPY_SKIP, _COPY_SKIP),
        # NOT_ENABLED
        ({"copyStatus": "NOT_ENABLED"}, VersionCopyStatus.NOT_ENABLED, None, _COPY_SKIP, _COPY_SKIP),
        # COMPLETED, no pending versions
        ({"copyStatus": "COMPLETED", "pendingVersionCount": "0"}, VersionCopyStatus.COMPLETED, None, 0, _COPY_SKIP),
        # COMPLETED with pending versions → WAITING
        (
            {"copyStatus": "COMPLETED", "pendingVersionCount": "3", "remainingBytes": "1048576"},
            VersionCopyStatus.WAITING, _COPY_SKIP, 3, 1048576,
        ),
        # NO_VERSIONS_TO_COPY → COMPLETED with detail reason
        ({"copyStatus": "NO_VERSIONS_TO_COPY"}, VersionCopyStatus.COMPLETED, CopyReason.NO_VERSIONS_TO_COPY, _COPY_SKIP, _COPY_SKIP),
        # DESTINATION_DISCONNECTED → RETRY
        (
            {"copyStatus": "DESTINATION_DISCONNECTED", "pendingVersionCount": "4", "remainingBytes": "2097152"},
            VersionCopyStatus.RETRY, CopyReason.DESTINATION_DISCONNECTED, 4, 2097152,
        ),
        # INFRASTRUCTURE_ERROR → FAILED; remainingBytes "0" → None
        (
            {"copyStatus": "INFRASTRUCTURE_ERROR", "pendingVersionCount": "2", "remainingBytes": "0"},
            VersionCopyStatus.FAILED, CopyReason.INFRASTRUCTURE_ERROR, 2, None,
        ),
        # DOING → IN_PROGRESS
        (
            {"copyStatus": "DOING", "pendingVersionCount": "3", "remainingBytes": "1048576"},
            VersionCopyStatus.IN_PROGRESS, None, 3, 1048576,
        ),
        # remainingBytes "0" maps to None, not 0
        (
            {"copyStatus": "COMPLETED", "pendingVersionCount": "2", "remainingBytes": "0"},
            VersionCopyStatus.WAITING, _COPY_SKIP, _COPY_SKIP, None,
        ),
    ],
    ids=[
        "no_field",
        "not_enabled",
        "completed_no_pending",
        "completed_with_pending",
        "no_versions_to_copy",
        "retry",
        "failed",
        "in_progress",
        "remaining_zero_is_none",
    ],
)
async def test_parse_plan_copy_status(
    copy_status_dict: dict[str, Any] | None,
    expected_status: VersionCopyStatus | None,
    expected_reason: CopyReason | None | object,
    expected_pending: int | object,
    expected_remaining: int | None | object,
) -> None:
    """backupCopyStatus is parsed to the correct status, reason, and progress fields."""
    plan_raw: dict[str, Any] = {**SAMPLE_PLAN_RAW}
    if copy_status_dict is not None:
        plan_raw = {**plan_raw, "backupCopyStatus": copy_status_dict}
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan_raw], "total": 1})
        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    bcs = result[0].backup_copy_status
    if copy_status_dict is None:
        assert bcs is None
        return
    assert bcs is not None
    assert bcs.status == expected_status
    if expected_reason is not _COPY_SKIP:
        assert bcs.reason == expected_reason
    if expected_pending is not _COPY_SKIP:
        assert bcs.pending_version_count == expected_pending
    if expected_remaining is not _COPY_SKIP:
        assert bcs.remaining_bytes == expected_remaining


# ── run_schedule_by_controller_time parsing ────────────────────────────────────


async def test_run_schedule_by_controller_time_absent_means_false() -> None:
    """run_schedule_by_controller_time is False when the controller-time flag is absent."""
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [SAMPLE_PLAN_RAW], "total": 1})
        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    assert result[0].run_schedule_by_controller_time is False


async def test_run_schedule_by_controller_time_present_means_true() -> None:
    """run_schedule_by_controller_time is True when the controller-time flag is present."""
    plan_raw = {
        **SAMPLE_PLAN_RAW,
        "spec": {**SAMPLE_PLAN_RAW["spec"], "controllerUtcOffset": 0},
    }
    async with connected_session() as (session, m):
        m.get(PLANS_URL, payload={"plans": [plan_raw], "total": 1})
        plans = make_collections(session)
        result, _ = await plans.list()
        await session.disconnect()

    assert result[0].run_schedule_by_controller_time is True


# ── get() ResourceNotFoundError on detail code 4001 ─────────────────────────


async def test_get_raises_resource_not_found_on_api_error_4001() -> None:
    """get() maps an APIError with detail code 4001 to ResourceNotFoundError."""
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4001}]}
    }
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", status=500, payload=error_body)
        col = MachinePlanCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.get(PLAN_ID)
        await session.disconnect()

    assert exc_info.value.resource_type == "ProtectionPlan"
    assert exc_info.value.resource_id == PLAN_ID


# ── update() PlanNameConflictError on detail code 4013 ──────────────────────


async def test_machine_update_raises_plan_name_conflict_on_4013() -> None:
    """update() raises PlanNameConflictError when the server returns detail code 4013."""
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4013}]}
    }
    update_url = f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.put(update_url, status=500, payload=error_body)
        col = MachinePlanCollection(session)
        with pytest.raises(PlanNameConflictError) as exc_info:
            await col.update(PLAN_ID, _make_machine_request())
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="Daily Backup")


# ── get() parsing — vm_config / pc_config / ps_config ───────────────────────


async def test_get_parses_vm_pc_ps_config() -> None:
    """get() populates vm_config, pc_config, and ps_config when configDevice includes those sections."""
    plan_raw: dict[str, Any] = {
        "id": PLAN_ID,
        "spec": {
            "name": "Daily Backup",
            "retention": {"keepDays": 30},
            "backupCopy": {"enabled": False, "destination": ""},
            "configDevice": {
                "mainSchedule": {
                    "scheduleType": "SCHEDULE",
                    "repeatType": "DAILY",
                    "repeatHour": 0,
                    "runHour": 2,
                    "runMin": 0,
                    "runWeekday": [],
                },
                "configVm": {
                    "enableAppAwareBkp": True,
                    "enableVerification": True,
                    "verificationPolicy": 60,
                    "enableDatastoreAware": True,
                    "datastoreReservedPercentage": 15,
                },
                "configPc": {
                    "shutdownAfterComplete": True,
                    "wakeUp": True,
                    "windowsWorkingState": False,
                },
                "configPs": {
                    "enableAppAwareBkp": False,
                    "enableVerification": False,
                    "verificationPolicy": 120,
                    "shutdownAfterComplete": False,
                    "wakeUp": False,
                    "windowsWorkingState": True,
                },
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    async with connected_session() as (session, m):
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=plan_raw)
        col = MachinePlanCollection(session)
        plan = await col.get(PLAN_ID)
        await session.disconnect()

    assert plan.vm_config is not None
    assert plan.vm_config.enable_app_aware_bkp is True
    assert plan.vm_config.enable_verification is True
    assert plan.vm_config.verification_video_duration_seconds == 60
    assert plan.vm_config.enable_datastore_usage_detection is True
    assert plan.vm_config.datastore_min_free_space_percent == 15

    assert plan.pc_config is not None
    assert plan.pc_config.shutdown_after_backup is True
    assert plan.pc_config.wake_for_backup is True
    assert plan.pc_config.prevent_sleep_during_backup is False

    assert plan.ps_config is not None
    assert plan.ps_config.enable_app_aware_bkp is False
    assert plan.ps_config.enable_verification is False
    assert plan.ps_config.shutdown_after_backup is False
    assert plan.ps_config.prevent_sleep_during_backup is True
