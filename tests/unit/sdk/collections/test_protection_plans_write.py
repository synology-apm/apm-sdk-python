"""Unit tests for protection plan create/update/delete flows and request-body building."""
from __future__ import annotations

from datetime import time
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.collections.protection_plans import M365PlanCollection, MachinePlanCollection
from synology_apm.sdk.enums import (
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    RetentionType,
    ScheduleFrequency,
)
from synology_apm.sdk.exceptions import APIError, PlanInUseError, PlanNameConflictError
from synology_apm.sdk.models.protection_plan import (
    EventTriggerConfig,
    GFSRetention,
    MachinePlanCreateRequest,
    MachineTaskConfig,
    MachineTaskSchedule,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from tests.unit.sdk.collections._plan_fixtures import (
    PLAN_CREATE_URL,
    PLAN_ID,
    SAMPLE_PLAN_WITH_SCHEDULE,
    _assert_sample_m365_plan,
    _assert_sample_machine_plan,
    _make_m365_request,
    _make_machine_request,
)
from tests.unit.sdk.conftest import (
    BASE_URL,
    assert_resource_error,
    connected_session,
    request_json,
)

# ── MachinePlanCollection.create() / update() / delete() ─────────────────────

async def test_machine_create_posts_body_and_returns_plan() -> None:
    """MachinePlanCollection.create() should POST and return the created plan via get()."""
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        plan = await col.create(_make_machine_request())
        await session.disconnect()

    _assert_sample_machine_plan(plan)

    post_key = ("POST", URL(PLAN_CREATE_URL))
    body = request_json(m, post_key)
    assert body["plan"]["name"] == "Daily Backup"
    assert body["plan"]["serviceType"] == "DEVICE"
    assert body["plan"]["retention"]["keepDays"] == 30
    assert len(body["plan"]["configDevice"]["task"]) == 6


async def test_machine_create_duplicate_name_raises() -> None:
    """create() should raise PlanNameConflictError on errorCode 4013."""
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [{"errorCode": 4013}]}
    }
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, status=500, payload=error_body)
        col = MachinePlanCollection(session)
        with pytest.raises(PlanNameConflictError) as exc_info:
            await col.create(_make_machine_request())
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="Daily Backup")


async def test_machine_create_missing_id_in_response_raises_api_error() -> None:
    """create() raises APIError when the POST response contains no 'id' field."""
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={})
        col = MachinePlanCollection(session)
        with pytest.raises(APIError, match="no plan ID"):
            await col.create(_make_machine_request())
        await session.disconnect()


def test_machine_create_immutable_non_keep_days_raises_value_error() -> None:
    """MachinePlanCreateRequest with is_immutable=True and non-KEEP_DAYS retention raises ValueError."""
    with pytest.raises(ValueError, match="(?i)immutable"):
        MachinePlanCreateRequest(
            name="Immutable Plan",
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ALL),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
            is_immutable=True,
        )


async def test_machine_create_tasks_differing_only_in_include_external_drives_not_duplicate() -> None:
    """Two tasks with the same (workload_type, os_type) but different include_external_drives
    are distinct and must not raise a duplicate ValueError."""
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS, include_external_drives=False),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS, include_external_drives=True),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    request = MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        plan = await col.create(request)
        await session.disconnect()
    assert plan.plan_id == PLAN_ID


def test_machine_create_identical_tasks_raise_value_error() -> None:
    """Two completely identical MachineTaskConfig entries raise ValueError at construction."""
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS),  # identical
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    with pytest.raises(ValueError, match="Duplicate task"):
        MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)


async def test_machine_create_event_flags_emitted_for_pc_tasks() -> None:
    """PC tasks with event_trigger emit logOff/screenLock/startup; non-PC tasks do not accept event_trigger."""
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    pc_sched = MachineTaskSchedule(
        time_schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0)),
        event_trigger=EventTriggerConfig(on_sign_out=True, on_lock=True, on_startup=True),
    )
    non_pc_sched = MachineTaskSchedule(
        time_schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0)),
    )
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS, use_main_schedule=False, schedule=pc_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC,     use_main_schedule=False, schedule=non_pc_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS, use_main_schedule=False, schedule=non_pc_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX,   use_main_schedule=False, schedule=non_pc_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE,    use_main_schedule=False, schedule=non_pc_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE,    use_main_schedule=False, schedule=non_pc_sched),
    )
    request = MachinePlanCreateRequest(
        name="Event Test Plan",
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
        schedule=_DAILY,
        tasks=tasks,
    )
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        await col.create(request)
        await session.disconnect()

    post_body = request_json(m, ("POST", URL(PLAN_CREATE_URL)))
    task_list = post_body["plan"]["configDevice"]["task"]
    pc_win = next(t for t in task_list if t["workloadType"] == "PC" and t["osType"] == "WINDOWS")
    assert pc_win["schedule"]["logOff"] is True
    assert pc_win["schedule"]["screenLock"] is True
    assert pc_win["schedule"]["startup"] is True
    assert pc_win["schedule"]["scheduleType"] == "SCHEDULE_AND_EVENT"
    for t in task_list:
        if t["workloadType"] != "PC" or t["osType"] != "WINDOWS":
            assert t["schedule"]["logOff"] is False
            assert t["schedule"]["screenLock"] is False
            assert t["schedule"]["startup"] is False


async def test_machine_create_schedule_and_event_emits_correct_schedule_type() -> None:
    """PC task with DAILY time_schedule + event_trigger emits scheduleType=SCHEDULE_AND_EVENT."""
    from datetime import timedelta
    sae_sched = MachineTaskSchedule(
        time_schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
        event_trigger=EventTriggerConfig(on_sign_out=True, on_lock=True, on_startup=True, min_interval=timedelta(hours=1)),
    )
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS, use_main_schedule=False, schedule=sae_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    request = MachinePlanCreateRequest(name="SAE Test Plan", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        await col.create(request)
        await session.disconnect()

    post_body = request_json(m, ("POST", URL(PLAN_CREATE_URL)))
    task_list = post_body["plan"]["configDevice"]["task"]
    pc_win_task = next(t for t in task_list if t["workloadType"] == "PC" and t["osType"] == "WINDOWS")
    sched = pc_win_task["schedule"]
    assert sched["scheduleType"] == "SCHEDULE_AND_EVENT"
    assert sched["repeatType"] == "DAILY"
    assert sched["logOff"] is True
    assert sched["screenLock"] is True
    assert sched["startup"] is True


async def test_machine_update_schedule_and_event_emits_correct_schedule_type() -> None:
    """PUT with a PC task using time_schedule + event_trigger emits scheduleType=SCHEDULE_AND_EVENT."""
    from datetime import timedelta
    sae_sched = MachineTaskSchedule(
        time_schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
        event_trigger=EventTriggerConfig(on_sign_out=True, on_lock=False, on_startup=True, min_interval=timedelta(hours=1)),
    )
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS, use_main_schedule=False, schedule=sae_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    request = MachinePlanCreateRequest(name="Daily Backup", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)
    update_url = f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.put(update_url, payload={})
        m.get(update_url, payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        await col.update(PLAN_ID, request)
        await session.disconnect()

    put_body = request_json(m, ("PUT", URL(update_url)))
    task_list = put_body["plan"]["configDevice"]["task"]
    pc_win_task = next(t for t in task_list if t["workloadType"] == "PC" and t["osType"] == "WINDOWS")
    sched = pc_win_task["schedule"]
    assert sched["scheduleType"] == "SCHEDULE_AND_EVENT"
    assert sched["logOff"] is True
    assert sched["screenLock"] is False
    assert sched["startup"] is True


async def test_machine_update_puts_body_and_returns_plan() -> None:
    """MachinePlanCollection.update() should PUT and return the updated plan."""
    update_url = f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.put(update_url, payload={})
        m.get(update_url, payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        plan = await col.update(PLAN_ID, _make_machine_request())
        await session.disconnect()

    _assert_sample_machine_plan(plan)
    put_key = ("PUT", URL(update_url))
    body = request_json(m, put_key)
    assert body["plan"]["name"] == "Daily Backup"
    assert body["plan"]["serviceType"] == "DEVICE"
    assert body["plan"]["retention"]["keepDays"] == 30


async def test_machine_delete_sends_delete_request() -> None:
    """MachinePlanCollection.delete() should send DELETE to the correct URL."""
    delete_url = f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.delete(delete_url, payload={})
        col = MachinePlanCollection(session)
        await col.delete(PLAN_ID)
        await session.disconnect()

    assert ("DELETE", URL(delete_url)) in m.requests


async def test_machine_delete_plan_in_use_both_codes_raises() -> None:
    """delete() with codes 4017 and 4019 should raise PlanInUseError with both flags set."""
    delete_url = f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}"
    error_body: dict[str, Any] = {
        "error": {"code": 500, "details": [
            {"errorCode": 4019},
            {"errorCode": 4017},
        ]}
    }
    async with connected_session() as (session, m):
        m.delete(delete_url, status=500, payload=error_body)
        col = MachinePlanCollection(session)
        with pytest.raises(PlanInUseError) as exc_info:
            await col.delete(PLAN_ID)
        await session.disconnect()

    err = exc_info.value
    assert err.has_workloads is True
    assert err.has_server_template is True
    assert err.has_backup_servers is False


# ── M365PlanCollection.create() / update() / delete() ────────────────────────

SAMPLE_M365_PLAN_RAW: dict[str, Any] = {
    "id": PLAN_ID,
    "spec": {
        "name": "M365 Daily",
        "serviceType": "M365",
        "retention": {"keepDays": 30},
        "backupCopy": {"enabled": False, "destination": ""},
    },
    "protectedWorkloadCount": 2,
    "unprotectedWorkloadCount": 0,
}


def _make_m365_collections(session: WebAPISession) -> M365PlanCollection:
    return M365PlanCollection(session)


async def test_m365_create_posts_body_and_returns_plan() -> None:
    """M365PlanCollection.create() should POST with serviceType=M365 and return the created plan."""
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_M365_PLAN_RAW)
        col = _make_m365_collections(session)
        plan = await col.create(_make_m365_request())
        await session.disconnect()

    post_key = ("POST", URL(PLAN_CREATE_URL))
    body = request_json(m, post_key)
    assert body["plan"]["serviceType"] == "M365"
    assert body["plan"]["name"] == "M365 Daily"
    assert body["plan"]["configM365"]["schedule"]["runHour"] == 9
    assert body["plan"]["retention"]["keepDays"] == 30
    _assert_sample_m365_plan(plan)


async def test_m365_create_duplicate_name_raises() -> None:
    """M365PlanCollection.create() should raise PlanNameConflictError on errorCode 4013."""
    error_body: dict[str, Any] = {"error": {"code": 500, "details": [{"errorCode": 4013}]}}
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, status=500, payload=error_body)
        col = _make_m365_collections(session)
        with pytest.raises(PlanNameConflictError) as exc_info:
            await col.create(_make_m365_request())
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="ProtectionPlan", resource_id="M365 Daily")


async def test_m365_update_puts_body_and_returns_plan() -> None:
    """M365PlanCollection.update() should PUT and return the updated plan."""
    update_url = f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.put(update_url, payload={})
        m.get(update_url, payload=SAMPLE_M365_PLAN_RAW)
        col = _make_m365_collections(session)
        plan = await col.update(PLAN_ID, _make_m365_request())
        await session.disconnect()

    put_key = ("PUT", URL(update_url))
    body = request_json(m, put_key)
    assert body["plan"]["name"] == "M365 Daily"
    assert body["plan"]["serviceType"] == "M365"
    assert body["plan"]["retention"]["keepDays"] == 30
    assert body["plan"]["configM365"]["schedule"]["runHour"] == 9
    _assert_sample_m365_plan(plan)


async def test_m365_delete_sends_delete_request() -> None:
    """M365PlanCollection.delete() should send DELETE to the correct URL."""
    delete_url = f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}"
    async with connected_session() as (session, m):
        m.delete(delete_url, payload={})
        col = _make_m365_collections(session)
        await col.delete(PLAN_ID)
        await session.disconnect()

    assert ("DELETE", URL(delete_url)) in m.requests


# ── EventTriggerConfig and three-mode PC task schedule ───────────────────────


def test_event_trigger_config_requires_at_least_one_flag() -> None:
    """EventTriggerConfig with all flags False raises ValueError."""
    with pytest.raises(ValueError, match="At least one event trigger"):
        EventTriggerConfig(on_sign_out=False, on_lock=False, on_startup=False)


def test_event_trigger_config_requires_positive_min_interval() -> None:
    """EventTriggerConfig with zero min_interval raises ValueError."""
    from datetime import timedelta
    with pytest.raises(ValueError, match="positive duration"):
        EventTriggerConfig(on_sign_out=True, min_interval=timedelta(0))



async def test_machine_create_event_only_emits_event_scheduletype() -> None:
    """PC task with time_schedule=None + event_trigger emits scheduleType=EVENT."""
    from datetime import timedelta
    event_sched = MachineTaskSchedule(
        time_schedule=None,
        event_trigger=EventTriggerConfig(on_sign_out=True, on_startup=True, min_interval=timedelta(minutes=30)),
    )
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS, use_main_schedule=False, schedule=event_sched),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    request = MachinePlanCreateRequest(name="Event Plan", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        await col.create(request)
        await session.disconnect()

    post_body = request_json(m, ("POST", URL(PLAN_CREATE_URL)))
    task_list = post_body["plan"]["configDevice"]["task"]
    pc_win_task = next(t for t in task_list if t["workloadType"] == "PC" and t["osType"] == "WINDOWS")
    sched = pc_win_task["schedule"]
    assert sched["scheduleType"] == "EVENT"
    assert sched["logOff"] is True
    assert sched["screenLock"] is False
    assert sched["startup"] is True
    assert sched["periodBase"] == "MIN"
    assert sched["periodLength"] == 30


def test_validate_event_trigger_rejected_for_non_pc() -> None:
    """event_trigger on a non-PC task raises ValueError at MachinePlanCreateRequest construction."""
    from datetime import timedelta
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    ps_with_event = MachineTaskSchedule(
        time_schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0)),
        event_trigger=EventTriggerConfig(on_sign_out=True, min_interval=timedelta(hours=1)),
    )
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS,
                          use_main_schedule=False, schedule=ps_with_event),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    with pytest.raises(ValueError, match="event_trigger is only valid for PC tasks"):
        MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)


def test_event_trigger_on_use_main_schedule_task_allowed() -> None:
    """event_trigger in a task schedule is accepted when use_main_schedule=True (schedule is ignored)."""
    from datetime import timedelta
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    ps_with_event = MachineTaskSchedule(
        time_schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(3, 0)),
        event_trigger=EventTriggerConfig(on_sign_out=True, min_interval=timedelta(hours=1)),
    )
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        # use_main_schedule=True (default) with a schedule containing event_trigger — must NOT raise
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS,
                          use_main_schedule=True, schedule=ps_with_event),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    req = MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)
    assert req.tasks is not None
    assert len(req.tasks) == 6



# ── Direct construction tests: __post_init__ validation ──────────────────


def test_machine_plan_weekly_main_schedule_no_weekdays_raises() -> None:
    """MachinePlanCreateRequest with WEEKLY main schedule and no weekdays raises ValueError."""
    with pytest.raises(ValueError, match="weekday"):
        MachinePlanCreateRequest(
            name="Daily Backup",
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.WEEKLY, start_time=time(9, 0)),
        )


def test_machine_plan_task_after_backup_schedule_raises() -> None:
    """MachinePlanCreateRequest with AFTER_BACKUP task schedule raises ValueError."""
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS,
                          use_main_schedule=False,
                          schedule=MachineTaskSchedule(time_schedule=ProtectionSchedule(
                              frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None))),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    with pytest.raises(ValueError, match="AFTER_BACKUP"):
        MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)


def test_machine_plan_task_invalid_os_type_raises() -> None:
    """MachinePlanCreateRequest with mismatched task os_type raises ValueError."""
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.LINUX),  # invalid
    )
    with pytest.raises(ValueError, match="os_type"):
        MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)


def test_machine_plan_task_vm_scope_not_none_raises() -> None:
    """MachinePlanCreateRequest with VM task scope != None raises ValueError."""
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.MAC),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE,
                          scope=MachineTaskScope.ENTIRE_MACHINE),  # invalid
    )
    with pytest.raises(ValueError, match="scope=None"):
        MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)


def test_machine_plan_create_request_after_backup_raises() -> None:
    """MachinePlanCreateRequest with AFTER_BACKUP main schedule raises ValueError at construction."""
    with pytest.raises(ValueError, match="AFTER_BACKUP"):
        MachinePlanCreateRequest(
            name="Daily Backup",
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        )


def test_machine_plan_create_request_missing_mandatory_pair_raises() -> None:
    """MachinePlanCreateRequest with tasks missing a mandatory pair raises ValueError at construction."""
    _DAILY = ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0))
    _KEEP_DAYS = ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)
    tasks = (
        MachineTaskConfig(workload_type=MachineWorkloadType.PC,  os_type=MachineOsType.WINDOWS),
        # (PC, MAC) intentionally missing
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.WINDOWS),
        MachineTaskConfig(workload_type=MachineWorkloadType.PS,  os_type=MachineOsType.LINUX),
        MachineTaskConfig(workload_type=MachineWorkloadType.FS,  os_type=MachineOsType.NONE),
        MachineTaskConfig(workload_type=MachineWorkloadType.VM,  os_type=MachineOsType.NONE),
    )
    with pytest.raises(ValueError, match="Task list must include"):
        MachinePlanCreateRequest(name="Test", retention=_KEEP_DAYS, schedule=_DAILY, tasks=tasks)


# ── retention body builders ───────────────────────────────────────────────────


async def test_machine_create_builds_keep_days_retention_body() -> None:
    """create() with KEEP_DAYS retention emits keepAll=False and keepDays in the POST body."""
    request = MachinePlanCreateRequest(
        name="Daily Backup",
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=14),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
    )
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        await col.create(request)
        await session.disconnect()

    post_body = request_json(m, ("POST", URL(PLAN_CREATE_URL)))
    retention = post_body["plan"]["retention"]
    assert retention["keepAll"] is False
    assert retention["keepDays"] == 14
    assert "keepVersions" not in retention
    assert "gfsDays" not in retention


async def test_machine_create_builds_keep_advanced_retention_body() -> None:
    """create() with KEEP_ADVANCED retention includes keepDays, keepVersions, and all GFS fields."""
    request = MachinePlanCreateRequest(
        name="Daily Backup",
        retention=ProtectionRetentionPolicy(
            retention_type=RetentionType.KEEP_ADVANCED,
            days=30,
            versions=5,
            gfs=GFSRetention(daily_versions=7, weekly_versions=4, monthly_versions=12, yearly_versions=1),
        ),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
    )
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        await col.create(request)
        await session.disconnect()

    post_body = request_json(m, ("POST", URL(PLAN_CREATE_URL)))
    retention = post_body["plan"]["retention"]
    assert retention["keepAll"] is False
    assert retention["keepDays"] == 30
    assert retention["keepVersions"] == 5
    assert retention["gfsDays"] == 7
    assert retention["gfsWeeks"] == 4
    assert retention["gfsMonths"] == 12
    assert retention["gfsYears"] == 1


# ── schedule body builder — MANUAL frequency ─────────────────────────────────


async def test_machine_create_builds_manual_schedule_body() -> None:
    """create() with MANUAL frequency emits scheduleType=NONE in the mainSchedule POST body."""
    request = MachinePlanCreateRequest(
        name="Daily Backup",
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.MANUAL, start_time=None),
    )
    async with connected_session() as (session, m):
        m.post(PLAN_CREATE_URL, payload={"id": PLAN_ID})
        m.get(f"{BASE_URL}/api/v1/plan/backup_plan/{PLAN_ID}", payload=SAMPLE_PLAN_WITH_SCHEDULE)
        col = MachinePlanCollection(session)
        await col.create(request)
        await session.disconnect()

    post_body = request_json(m, ("POST", URL(PLAN_CREATE_URL)))
    main_sched = post_body["plan"]["configDevice"]["mainSchedule"]
    assert main_sched["scheduleType"] == "NONE"
    assert main_sched["repeatType"] == "DAILY"


