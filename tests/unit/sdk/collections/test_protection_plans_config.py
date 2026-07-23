"""Unit tests for machine plan config sections: retention/schedule variants, db config,
backup window, backup copy, and error re-raise paths — all via the public plan API."""
from __future__ import annotations

from datetime import time, timedelta
from typing import Any

import pytest
from yarl import URL

from synology_apm.sdk.collections._protection_plan_parsers import (
    _parse_backup_window,
    _parse_db_config,
    _parse_pc_config,
    _parse_ps_config,
    _parse_task_config,
    _parse_task_schedule,
    _parse_vm_config,
)
from synology_apm.sdk.collections.protection_plans import MachinePlanCollection
from synology_apm.sdk.enums import (
    BackupServerType,
    DbActionOnError,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    RemoteStorageStatus,
    RemoteStorageType,
    RetentionType,
    ScheduleFrequency,
    ServerStatus,
    VersionCopyStatus,
    WeekDay,
)
from synology_apm.sdk.exceptions import APIError
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.protection_plan import (
    BackupCopyConfig,
    EventTriggerConfig,
    MachineBackupWindow,
    MachineDbConfig,
    MachinePcConfig,
    MachinePlanCreateRequest,
    MachinePsConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineVmConfig,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.remote_storage import RemoteStorage
from tests.unit.sdk.conftest import BASE_URL, connected_session, request_json

PLAN_ID = "0c8f033b-fb57-4f46-9a9d-85e9d21c08ab"
PLAN_URL = f"{BASE_URL}/api/v1/plan/backup_plan"
PLAN_DETAIL_URL = f"{PLAN_URL}/{PLAN_ID}"
PLANS_LIST_URL = f"{PLAN_URL}?offset=0&limit=500&serviceType=DEVICE"

SAMPLE_PLAN_RESPONSE: dict[str, Any] = {
    "id": PLAN_ID,
    "spec": {
        "name": "Daily Backup",
        "retention": {"keepDays": 30},
        "backupCopy": {"enabled": False, "destination": ""},
    },
    "protectedWorkloadCount": 2,
    "unprotectedWorkloadCount": 1,
}

COPY_DEST_NAMESPACE = "0903a27c-35e3-483e-bda4-8c8c77475fb9"

SAMPLE_COPY_DEST_SERVER = BackupServer(
    backup_server_id="bs-dp-002",
    namespace=COPY_DEST_NAMESPACE,
    server_type=BackupServerType.DP,
    name="apm-server-02",
    hostname="192.0.2.2",
    model="DP320",
    system_version="APM 1.2-71845",
    status=ServerStatus.HEALTHY,
    is_updating=False,
    serial="SN001",
    storage_total_bytes=None,
    storage_used_bytes=None,
    logical_backup_data_bytes=None,
    physical_backup_data_bytes=None,
)

REMOTE_STORAGE_ID = "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"


def _make_remote_storage(storage_type: RemoteStorageType) -> RemoteStorage:
    return RemoteStorage(
        storage_id=REMOTE_STORAGE_ID,
        name="APV Vault",
        storage_type=storage_type,
        device_model="",
        endpoint="apv.example.com",
        status=RemoteStorageStatus.CONNECTED,
        used_bytes=None,
        remaining_bytes=None,
        vault_name="my-bucket",
    )


def _make_request(**overrides: Any) -> MachinePlanCreateRequest:
    params: dict[str, Any] = dict(
        name="Daily Backup",
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
    )
    params.update(overrides)
    return MachinePlanCreateRequest(**params)


def _copy_retention() -> ProtectionRetentionPolicy:
    return ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7)


def _six_tasks(pc_windows: MachineTaskConfig) -> tuple[MachineTaskConfig, ...]:
    """A full mandatory-pair task tuple with the PC/WINDOWS entry customized."""
    return (
        pc_windows,
        MachineTaskConfig(MachineWorkloadType.PC, MachineOsType.MAC),
        MachineTaskConfig(MachineWorkloadType.PS, MachineOsType.WINDOWS),
        MachineTaskConfig(MachineWorkloadType.PS, MachineOsType.LINUX),
        MachineTaskConfig(MachineWorkloadType.FS, MachineOsType.NONE),
        MachineTaskConfig(MachineWorkloadType.VM, MachineOsType.NONE),
    )


async def _create_and_capture_body(request: MachinePlanCreateRequest) -> tuple[ProtectionPlan, dict[str, Any]]:
    """Run create() against mocked POST + GET and return (returned plan, captured POST body)."""
    async with connected_session() as (session, m):
        m.post(PLAN_URL, payload={"id": PLAN_ID})
        m.get(PLAN_DETAIL_URL, payload=SAMPLE_PLAN_RESPONSE)
        plan = await MachinePlanCollection(session).create(request)
        await session.disconnect()
    body = request_json(m, ("POST", URL(PLAN_URL)))
    return plan, body


def _assert_created_plan(plan: ProtectionPlan) -> None:
    assert plan.plan_id == PLAN_ID
    assert plan.name == "Daily Backup"


# ── retention variants ─────────────────────────────────────────────────────


async def test_create_retention_keep_all() -> None:
    """KEEP_ALL retention builds a keepAll=True retention body."""
    request = _make_request(retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ALL))
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["retention"] == {"keepAll": True}


async def test_update_retention_keep_versions() -> None:
    """KEEP_VERSIONS retention builds a keepVersions body (via update())."""
    request = _make_request(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=10)
    )
    async with connected_session() as (session, m):
        m.put(PLAN_DETAIL_URL, payload={})
        m.get(PLAN_DETAIL_URL, payload=SAMPLE_PLAN_RESPONSE)
        plan = await MachinePlanCollection(session).update(PLAN_ID, request)
        await session.disconnect()

    _assert_created_plan(plan)
    body = request_json(m, ("PUT", URL(PLAN_DETAIL_URL)))
    assert body["plan"]["retention"] == {"keepAll": False, "keepVersions": 10}


async def test_create_retention_none() -> None:
    """NONE retention builds the bare keepAll=False retention body."""
    request = _make_request(retention=ProtectionRetentionPolicy(retention_type=RetentionType.NONE))
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["retention"] == {"keepAll": False}


# ── main schedule variants ─────────────────────────────────────────────────


async def test_create_hourly_main_schedule() -> None:
    """HOURLY frequency builds a repeatHour=1 daily schedule keeping the start minute."""
    request = _make_request(
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.HOURLY, start_time=time(0, 30))
    )
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["configDevice"]["mainSchedule"] == {
        "lastRunHour": 0, "lastRunMin": 0,
        "scheduleType": "SCHEDULE", "repeatType": "DAILY",
        "repeatHour": 1, "runWeekday": [5], "runHour": 0, "runMin": 30,
    }


async def test_create_weekly_main_schedule() -> None:
    """WEEKLY frequency builds a WEEKLY repeatType with the selected weekday values."""
    request = _make_request(
        schedule=ProtectionSchedule(
            frequency=ScheduleFrequency.WEEKLY,
            start_time=time(2, 30),
            weekdays=(WeekDay.MONDAY, WeekDay.FRIDAY),
        )
    )
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["configDevice"]["mainSchedule"] == {
        "lastRunHour": 0, "lastRunMin": 0,
        "scheduleType": "SCHEDULE", "repeatType": "WEEKLY",
        "repeatHour": 0, "runWeekday": [1, 5], "runHour": 2, "runMin": 30,
    }


# ── per-task event trigger with day-based interval ─────────────────────────


async def test_create_event_trigger_day_interval() -> None:
    """An event-only PC task with a whole-day min_interval emits periodBase=DAY."""
    pc_task = MachineTaskConfig(
        MachineWorkloadType.PC, MachineOsType.WINDOWS,
        use_main_schedule=False,
        schedule=MachineTaskSchedule(
            time_schedule=None,
            event_trigger=EventTriggerConfig(on_sign_out=True, min_interval=timedelta(days=1)),
        ),
    )
    request = _make_request(tasks=_six_tasks(pc_task))
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    task_body = body["plan"]["configDevice"]["task"][0]
    assert task_body["workloadType"] == "PC"
    assert task_body["useMainSchedule"] is False
    assert task_body["schedule"] == {
        "scheduleType": "EVENT",
        "logOff": True, "screenLock": False, "startup": False,
        "periodBase": "DAY", "periodLength": 1,
    }


# ── db config ──────────────────────────────────────────────────────────────


async def test_create_db_config_enabled() -> None:
    """A MachineDbConfig request enables DB backup with the mapped log settings."""
    request = _make_request(
        db_config=MachineDbConfig(
            action_on_error=DbActionOnError.STOP,
            mssql_log_setting=MssqlLogSetting.TRUNCATE,
            oracle_log_setting=OracleLogSetting.DELETE,
        )
    )
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["configDevice"]["configSqlServer"] == {
        "disableDbBackup": False,
        "logsProcessing": "REQUIRE_SUCCESS",
        "mssqlServer": {"logSettings": "TRUNCATE_LOGS"},
        "oracleServer": {"logSettings": "DELETE_LOGS"},
        "enableDefaultCredential": False,
        "guestOsCredential": {"userName": "", "password": ""},
        "dbCredentialSql": {"userName": "", "password": ""},
        "dbCredentialOracle": {"userName": "", "password": ""},
    }


# ── backup window ──────────────────────────────────────────────────────────


async def test_create_backup_window_enabled() -> None:
    """An enabled backup window encodes allowed hours into the 168-char bitmap."""
    request = _make_request(
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={WeekDay.MONDAY: frozenset({9, 10, 11})},
        )
    )
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    window = body["plan"]["configDevice"]["backupWindow"]
    assert window["enabled"] is True
    data = window["data"]
    assert len(data) == 168
    # Monday (weekday value 1) occupies offsets 24-47; hours 9-11 are allowed.
    assert data[24 + 9: 24 + 12] == "111"
    assert data.count("1") == 3


# ── backup copy ────────────────────────────────────────────────────────────


async def test_create_backup_copy_after_backup_to_backup_server() -> None:
    """AFTER_BACKUP copy schedule to a BackupServer emits an APPLIANCE EVENT copy config."""
    request = _make_request(
        backup_copy=BackupCopyConfig(
            destination=SAMPLE_COPY_DEST_SERVER,
            retention=_copy_retention(),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        )
    )
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["backupCopy"] == {
        "enabled": True,
        "destinationType": "APPLIANCE",
        "destination": COPY_DEST_NAMESPACE,
        "schedule": {"scheduleType": "EVENT", "runHour": 20, "runMin": 0},
        "retention": {"keepAll": False, "keepDays": 7},
    }


async def test_create_backup_copy_weekly_to_remote_storage() -> None:
    """A WEEKLY copy schedule to a RemoteStorage emits the mapped storage type and weekday."""
    request = _make_request(
        backup_copy=BackupCopyConfig(
            destination=_make_remote_storage(RemoteStorageType.ACTIVE_PROTECT_VAULT),
            retention=_copy_retention(),
            schedule=ProtectionSchedule(
                frequency=ScheduleFrequency.WEEKLY, start_time=time(1, 0), weekdays=(WeekDay.SUNDAY,),
            ),
        )
    )
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["backupCopy"] == {
        "enabled": True,
        "destinationType": "ACTIVE_BACKUP_ENTERPRISE_VAULT",
        "destination": REMOTE_STORAGE_ID,
        "schedule": {
            "scheduleType": "SCHEDULE", "repeatType": "WEEKLY",
            "runWeekday": [0], "runHour": 1, "runMin": 0,
        },
        "retention": {"keepAll": False, "keepDays": 7},
    }


async def test_create_backup_copy_daily_schedule() -> None:
    """A DAILY copy schedule emits a plain SCHEDULE entry with the start time."""
    request = _make_request(
        backup_copy=BackupCopyConfig(
            destination=SAMPLE_COPY_DEST_SERVER,
            retention=_copy_retention(),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(1, 0)),
        )
    )
    plan, body = await _create_and_capture_body(request)

    _assert_created_plan(plan)
    assert body["plan"]["backupCopy"]["schedule"] == {
        "scheduleType": "SCHEDULE", "runHour": 1, "runMin": 0,
    }


async def test_create_backup_copy_unsupported_storage_type_raises() -> None:
    """A RemoteStorage destination with an unmapped storage type raises ValueError before any request."""
    request = _make_request(
        backup_copy=BackupCopyConfig(
            destination=_make_remote_storage(RemoteStorageType.UNKNOWN),
            retention=_copy_retention(),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        )
    )
    async with connected_session() as (session, m):
        with pytest.raises(ValueError, match="Unsupported RemoteStorage"):
            await MachinePlanCollection(session).create(request)
        await session.disconnect()

    assert ("POST", URL(PLAN_URL)) not in m.requests


def test_machine_plan_weekly_backup_copy_no_weekdays_raises() -> None:
    """A WEEKLY Backup Copy schedule with no weekdays raises ValueError at construction time."""
    with pytest.raises(ValueError, match="WEEKLY Backup Copy schedule requires at least one weekday"):
        _make_request(
            backup_copy=BackupCopyConfig(
                destination=SAMPLE_COPY_DEST_SERVER,
                retention=_copy_retention(),
                schedule=ProtectionSchedule(frequency=ScheduleFrequency.WEEKLY, start_time=time(2, 0)),
            )
        )


def test_machine_plan_after_backup_copy_is_accepted() -> None:
    """AFTER_BACKUP remains a valid Backup Copy schedule (non-regression for the WEEKLY check)."""
    request = _make_request(
        backup_copy=BackupCopyConfig(
            destination=SAMPLE_COPY_DEST_SERVER,
            retention=_copy_retention(),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        )
    )
    assert request.backup_copy is not None
    assert request.backup_copy.schedule.frequency == ScheduleFrequency.AFTER_BACKUP


# ── request model validation (construction-time ValueError) ────────────────


def test_request_rejects_duplicate_fixed_count_tasks() -> None:
    """Two VM task entries violate the exactly-1 rule for fixed-count pairs."""
    tasks = _six_tasks(MachineTaskConfig(MachineWorkloadType.PC, MachineOsType.WINDOWS)) + (
        MachineTaskConfig(MachineWorkloadType.VM, MachineOsType.NONE, include_external_drives=True),
    )
    with pytest.raises(ValueError, match="exactly 1 entry"):
        _make_request(tasks=tasks)


def test_request_rejects_custom_volumes_without_custom_scope() -> None:
    """custom_volumes requires scope=CUSTOM_VOLUME."""
    pc_task = MachineTaskConfig(
        MachineWorkloadType.PC, MachineOsType.WINDOWS,
        scope=MachineTaskScope.ENTIRE_MACHINE,
        custom_volumes=("C:",),
    )
    with pytest.raises(ValueError, match="custom_volumes must be empty"):
        _make_request(tasks=_six_tasks(pc_task))


def test_request_rejects_missing_schedule_when_not_using_main() -> None:
    """use_main_schedule=False without a schedule is rejected."""
    pc_task = MachineTaskConfig(
        MachineWorkloadType.PC, MachineOsType.WINDOWS, use_main_schedule=False, schedule=None,
    )
    with pytest.raises(ValueError, match="must provide a schedule"):
        _make_request(tasks=_six_tasks(pc_task))


def test_request_rejects_weekly_task_schedule_without_weekdays() -> None:
    """A WEEKLY per-task schedule requires at least one weekday."""
    pc_task = MachineTaskConfig(
        MachineWorkloadType.PC, MachineOsType.WINDOWS,
        use_main_schedule=False,
        schedule=MachineTaskSchedule(
            time_schedule=ProtectionSchedule(frequency=ScheduleFrequency.WEEKLY, start_time=time(3, 0)),
        ),
    )
    with pytest.raises(ValueError, match="WEEKLY task schedule requires at least one weekday"):
        _make_request(tasks=_six_tasks(pc_task))


# ── parser: copy status edge cases ─────────────────────────────────────────


async def test_get_unknown_copy_status_parses_as_none() -> None:
    """An unrecognized copyStatus string yields backup_copy_status=None."""
    payload = {**SAMPLE_PLAN_RESPONSE, "backupCopyStatus": {"copyStatus": "SOME_UNKNOWN_STATUS"}}
    async with connected_session() as (session, m):
        m.get(PLAN_DETAIL_URL, payload=payload)
        plan = await MachinePlanCollection(session).get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.backup_copy_status is None


async def test_get_skipped_copy_status_parses_skipped_count() -> None:
    """SKIPPED_WORKLOAD copyStatus carries the skipped workload count."""
    payload = {
        **SAMPLE_PLAN_RESPONSE,
        "backupCopyStatus": {"copyStatus": "SKIPPED_WORKLOAD", "skippedWorkloadCount": "5"},
    }
    async with connected_session() as (session, m):
        m.get(PLAN_DETAIL_URL, payload=payload)
        plan = await MachinePlanCollection(session).get(PLAN_ID)
        await session.disconnect()

    bcs = plan.backup_copy_status
    assert bcs is not None
    assert bcs.status == VersionCopyStatus.SKIPPED
    assert bcs.skipped_workload_count == 5
    assert bcs.pending_version_count == 0
    assert bcs.remaining_bytes is None


# ── parser: machine config sections ────────────────────────────────────────


async def test_get_parses_machine_config_sections() -> None:
    """get() parses configSqlServer, backupWindow, and agentScope task fields."""
    window_data = ["0"] * 168
    window_data[24 + 9: 24 + 12] = ["1", "1", "1"]  # Monday 09:00-12:00
    payload = {
        **SAMPLE_PLAN_RESPONSE,
        "spec": {
            **SAMPLE_PLAN_RESPONSE["spec"],
            "configDevice": {
                "configSqlServer": {
                    "disableDbBackup": False,
                    "logsProcessing": "REQUIRE_SUCCESS",
                    "mssqlServer": {"logSettings": "TRUNCATE_LOGS"},
                    "oracleServer": {"logSettings": "DELETE_LOGS"},
                },
                "backupWindow": {"enabled": True, "data": "".join(window_data)},
                "task": [
                    {
                        "workloadType": "PS",
                        "osType": "WINDOWS",
                        "useMainSchedule": True,
                        "agentScope": {
                            "sourceType": "BACKUP_SOURCE_CUSVOL",
                            "customVolume": ["C:"],
                            "enableBackupExternal": True,
                            "includeBootPartition": False,
                        },
                    },
                ],
            },
        },
    }
    async with connected_session() as (session, m):
        m.get(PLAN_DETAIL_URL, payload=payload)
        plan = await MachinePlanCollection(session).get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.db_config == MachineDbConfig(
        action_on_error=DbActionOnError.STOP,
        mssql_log_setting=MssqlLogSetting.TRUNCATE,
        oracle_log_setting=OracleLogSetting.DELETE,
    )
    assert plan.backup_window is not None
    assert plan.backup_window.enabled is True
    assert plan.backup_window.allowed_hours == {WeekDay.MONDAY: frozenset({9, 10, 11})}
    assert plan.tasks is not None
    task = plan.tasks[0]
    assert task.workload_type == MachineWorkloadType.PS
    assert task.scope == MachineTaskScope.CUSTOM_VOLUME
    assert task.custom_volumes == ("C:",)
    assert task.include_external_drives is True
    assert task.include_boot_partition is False


async def test_get_disabled_db_backup_parses_as_none() -> None:
    """A configSqlServer section with DB backup disabled yields db_config=None."""
    payload = {
        **SAMPLE_PLAN_RESPONSE,
        "spec": {
            **SAMPLE_PLAN_RESPONSE["spec"],
            "configDevice": {"configSqlServer": {"disableDbBackup": True}},
        },
    }
    async with connected_session() as (session, m):
        m.get(PLAN_DETAIL_URL, payload=payload)
        plan = await MachinePlanCollection(session).get(PLAN_ID)
        await session.disconnect()

    assert plan.plan_id == PLAN_ID
    assert plan.db_config is None


# ── copy-destination cache filters non-destination servers ─────────────────


async def test_list_ignores_backup_servers_that_are_not_copy_destinations() -> None:
    """Only servers whose namespace matches a plan's copy destination enter the lookup cache."""
    plan_raw = {
        "id": "copy-plan-001",
        "spec": {
            "name": "Machine Copy Plan",
            "retention": {"keepDays": 30},
            "backupCopy": {
                "enabled": True,
                "destinationType": "APPLIANCE",
                "destination": COPY_DEST_NAMESPACE,
                "schedule": {"scheduleType": "EVENT", "runHour": 20, "runMin": 0},
                "retention": {"keepDays": 1},
            },
        },
        "protectedWorkloadCount": 1,
        "unprotectedWorkloadCount": 0,
    }
    matching_server = {
        "id": "bs-server-002",
        "namespace": COPY_DEST_NAMESPACE,
        "spec": {"addr": "192.0.2.2"},
        "status": {"hostName": "apm-server-02"},
    }
    other_server = {
        "id": "bs-server-003",
        "namespace": "1e0e18a4-0000-4000-8000-000000000003",
        "spec": {"addr": "192.0.2.3"},
        "status": {"hostName": "apm-server-03"},
    }
    servers_url = f"{BASE_URL}/api/v1/infra/backup_server?limit=3000&offset=0"
    async with connected_session() as (session, m):
        m.get(PLANS_LIST_URL, payload={"plans": [plan_raw], "total": 1})
        m.get(servers_url, payload={"backupServers": [other_server, matching_server], "total": 2})
        result, _ = await MachinePlanCollection(session).list()
        await session.disconnect()

    plan = result[0]
    assert plan.backup_copy_policy is not None
    assert plan.backup_copy_policy.destination.name == "apm-server-02"
    assert plan.backup_copy_policy.destination.identifier == COPY_DEST_NAMESPACE


# ── update()/delete() error re-raise paths ─────────────────────────────────


async def test_update_reraises_non_conflict_api_error() -> None:
    """update() re-raises an APIError whose detail codes are not a name conflict."""
    error_body = {"error": {"code": 500, "details": [{"errorCode": 9999}]}}
    async with connected_session() as (session, m):
        m.put(PLAN_DETAIL_URL, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await MachinePlanCollection(session).update(PLAN_ID, _make_request())
        await session.disconnect()

    assert exc_info.type is APIError


async def test_delete_reraises_non_in_use_api_error() -> None:
    """delete() re-raises an APIError whose detail codes are not the in-use codes."""
    error_body = {"error": {"code": 500, "details": [{"errorCode": 5000}]}}
    async with connected_session() as (session, m):
        m.delete(PLAN_DETAIL_URL, status=500, payload=error_body)
        with pytest.raises(APIError) as exc_info:
            await MachinePlanCollection(session).delete(PLAN_ID)
        await session.disconnect()

    assert exc_info.type is APIError


def test_build_backup_window_rejects_out_of_range_hour() -> None:
    """The request builder is a defensive last gate: an out-of-range hour that bypassed
    construction-time validation (allowed_hours is a mutable dict on a frozen dataclass)
    still raises instead of being silently dropped."""
    from synology_apm.sdk.collections._protection_plan_builders import _build_backup_window_dict

    window = MachineBackupWindow(enabled=True, allowed_hours={WeekDay.MONDAY: frozenset({8})})
    window.allowed_hours[WeekDay.MONDAY] = frozenset({30})  # mutate past __post_init__

    with pytest.raises(ValueError, match="out of range 0-23"):
        _build_backup_window_dict(window)


# ── null vs. absent JSON field handling ─────────────────────────────────────
#
# One test per parser function, each covering every field that function's
# null-safety touches at once — see the SDK README's "Null vs. Absent JSON Field
# Handling". Parser functions are pure and called directly, no HTTP mocking needed.


def test_parse_vm_config_survives_null_fields() -> None:
    """_parse_vm_config() with enableVerification/verificationPolicy/enableDatastoreAware/
    datastoreReservedPercentage all JSON null (keys present, values null — distinct
    from absent keys) must not crash; falls back to the documented defaults."""
    raw = {
        "enableVerification": None, "verificationPolicy": None,
        "enableDatastoreAware": None, "datastoreReservedPercentage": None,
    }
    config = _parse_vm_config(raw)
    assert config == MachineVmConfig(
        enable_verification=False,
        verification_video_duration_seconds=120,
        enable_datastore_usage_detection=False,
        datastore_min_free_space_percent=10,
    )


def test_parse_pc_config_survives_null_fields() -> None:
    """_parse_pc_config() with shutdownAfterComplete/wakeUp/windowsWorkingState all
    JSON null must not crash; falls back to the documented defaults (all False)."""
    raw = {"shutdownAfterComplete": None, "wakeUp": None, "windowsWorkingState": None}
    config = _parse_pc_config(raw)
    assert config == MachinePcConfig(
        shutdown_after_backup=False, wake_for_backup=False, prevent_sleep_during_backup=False,
    )


def test_parse_ps_config_survives_null_fields() -> None:
    """_parse_ps_config() with enableVerification/verificationPolicy/shutdownAfterComplete/
    wakeUp/windowsWorkingState all JSON null must not crash; falls back to the
    documented defaults."""
    raw = {
        "enableVerification": None, "verificationPolicy": None,
        "shutdownAfterComplete": None, "wakeUp": None, "windowsWorkingState": None,
    }
    config = _parse_ps_config(raw)
    assert config == MachinePsConfig(
        enable_verification=False,
        verification_video_duration_seconds=120,
        shutdown_after_backup=False,
        wake_for_backup=False,
        prevent_sleep_during_backup=False,
    )


def test_parse_db_config_survives_null_fields() -> None:
    """_parse_db_config() with logsProcessing/mssqlServer/oracleServer all JSON null
    (keys present, values null — distinct from an absent key) must not crash;
    db_config falls back to the documented default log settings."""
    raw = {"disableDbBackup": False, "logsProcessing": None, "mssqlServer": None, "oracleServer": None}
    config = _parse_db_config(raw)
    assert config == MachineDbConfig(
        action_on_error=DbActionOnError.CONTINUE,
        mssql_log_setting=MssqlLogSetting.DO_NOT_TRUNCATE,
        oracle_log_setting=OracleLogSetting.DO_NOT_DELETE,
    )


def test_parse_backup_window_survives_null_fields() -> None:
    """_parse_backup_window() with enabled/data both JSON null must not crash (a null
    data string would otherwise blow up the len()/indexing loop); falls back to
    disabled with no allowed hours."""
    raw = {"enabled": None, "data": None}
    window = _parse_backup_window(raw)
    assert window == MachineBackupWindow(enabled=False, allowed_hours={})


def test_parse_task_schedule_survives_null_fields() -> None:
    """_parse_task_schedule() with scheduleType/logOff/screenLock/startup/periodBase/
    periodLength all JSON null must not crash. A null scheduleType still falls
    through to the time-based branch; when at least one event flag is true despite
    the others being null, min_interval falls back to the documented 1-hour default."""
    time_based_raw = {
        "scheduleType": None, "logOff": None, "screenLock": None, "startup": None,
        "periodBase": None, "periodLength": None,
        "repeatType": "DAILY", "repeatHour": 0, "runHour": 9, "runMin": 0,
    }
    time_based = _parse_task_schedule(time_based_raw)
    assert time_based.time_schedule is not None
    assert time_based.time_schedule.frequency == ScheduleFrequency.DAILY
    assert time_based.time_schedule.start_time == time(9, 0)
    assert time_based.event_trigger is None

    event_raw = {
        "scheduleType": "EVENT", "logOff": True, "screenLock": None, "startup": None,
        "periodBase": None, "periodLength": None,
    }
    event_based = _parse_task_schedule(event_raw)
    assert event_based.time_schedule is None
    assert event_based.event_trigger is not None
    assert event_based.event_trigger.on_sign_out is True
    assert event_based.event_trigger.on_lock is False
    assert event_based.event_trigger.min_interval == timedelta(hours=1)


def test_parse_task_config_survives_null_fields() -> None:
    """_parse_task_config() with workloadType/osType/agentScope's sourceType/
    customVolume/enableBackupExternal all JSON null, and (separately) a JSON null
    schedule while use_main_schedule=False, must not crash; falls back to the
    documented defaults."""
    raw = {
        "workloadType": None, "osType": None, "useMainSchedule": True,
        "agentScope": {"sourceType": None, "customVolume": None, "enableBackupExternal": None},
    }
    config = _parse_task_config(raw)
    assert config.workload_type == MachineWorkloadType.PC
    assert config.os_type == MachineOsType.NONE
    assert config.scope == MachineTaskScope.ENTIRE_MACHINE
    assert config.custom_volumes == ()
    assert config.include_external_drives is False
    assert config.schedule is None

    raw_null_schedule = {
        "workloadType": "PC", "osType": "WINDOWS", "useMainSchedule": False, "schedule": None,
    }
    config_null_schedule = _parse_task_config(raw_null_schedule)
    assert config_null_schedule.schedule is not None
    assert config_null_schedule.schedule.time_schedule is not None
    assert config_null_schedule.schedule.time_schedule.frequency == ScheduleFrequency.DAILY
    assert config_null_schedule.schedule.event_trigger is None
