"""Unit tests for BackupActivityCollection.list() and get_latest_by_workload_name()."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
from aiointercept import aiointercept

from synology_apm.sdk.collections.activities import BackupActivityCollection
from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    M365WorkloadType,
    MachineWorkloadType,
    VerifyStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.workload import Workload
from tests.unit.sdk.conftest import (
    BASE_URL,
    LOGIN_OK,
    LOGIN_URL,
    assert_resource_error,
    connected_session,
    make_backup_activity_raw,
    make_session,
)

RECENT_URL = (
    f"{BASE_URL}/api/v2/activity/backup/activities"
    "?listMethod=RECENT&offset=0&limit=100"
    "&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
)
HISTORY_URL = (
    f"{BASE_URL}/api/v2/activity/backup/activities"
    "?listMethod=HISTORY&offset=0&limit=100"
    "&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
)

SAMPLE_ACTIVITY_RAW: dict[str, Any] = {
    "activity": {
        "uid": "act-uid-001",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "workloadType": "MACHINE_PC",
            "workloadName": "CORP-PC-001",
            "workload": {"uid": "fbf93425-d9e7-1c70-f4b2-231d7fc7b116"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_1",
            "backupStatus": "SUCCESS",
            "startTime": "1776734685",
            "endTime": "1776735285",
            "durationTime": "600",
            "transferredDataSize": "1073741824",
            "progress": 100,
        },
    }
}

SAMPLE_ACTIVITY_RUNNING: dict[str, Any] = {
    "activity": {
        "uid": "act-uid-002",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-web-01",
            "workload": {"uid": "vm-uid-001"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_2",
            "backupStatus": "BACKUPING",
            "startTime": "1776734685",
            "endTime": "0",
            "durationTime": "0",
            "transferredDataSize": "0",
            "progress": 42,
        },
    }
}

SAMPLE_ACTIVITY_RUNNING_NEG_DURATION: dict[str, Any] = {
    "activity": {
        "uid": "act-uid-003",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-app-01",
            "workload": {"uid": "vm-uid-002"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_3",
            "backupStatus": "BACKUPING",
            "startTime": "1776734685",
            "endTime": "0",
            "durationTime": "-1",
            "transferredDataSize": "0",
            "progress": 10,
        },
    }
}

SAMPLE_M365_ACTIVITY_RUNNING: dict[str, Any] = {
    "activity": {
        "uid": "act-m365-001",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "workloadType": "APPLICATION_M365",
            "workloadName": "alice@contoso.com",
            "workload": {"uid": "m365-uid-001"},
            "planName": "M365 Daily",
        },
        "status": {
            "executionId": "M365_1",
            "backupStatus": "BACKUPING",
            "startTime": "1776734685",
            "endTime": "0",
            "durationTime": "-1",
            "transferredDataSize": "0",
            "progress": 0,
            "processedSuccessCount": 5,
            "processedWarningCount": 2,
            "processedErrorCount": 1,
        },
    }
}

SAMPLE_FS_ACTIVITY_RUNNING: dict[str, Any] = {
    "activity": {
        "uid": "act-fs-001",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "workloadType": "MACHINE_FS",
            "workloadName": "Corp Share",
            "workload": {"uid": "fs-uid-001"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_fs_1",
            "backupStatus": "BACKUPING",
            "startTime": "1776734685",
            "endTime": "0",
            "durationTime": "0",
            "transferredDataSize": "0",
            "progress": 0,
            "processedSuccessCount": 312,
            "processedWarningCount": 5,
            "processedErrorCount": 2,
        },
    }
}


EMPTY_RESPONSE: dict[str, Any] = {"activities": []}


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_parses_activity_fields() -> None:
    async with connected_session() as (session, m):

        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list()
        await session.disconnect()

    assert total == 1
    act = activities[0]
    assert act.activity_id == "act-uid-001"
    assert act.execution_id == "ABE_1"
    assert act.workload_id == "fbf93425-d9e7-1c70-f4b2-231d7fc7b116"
    assert act.workload_name == "CORP-PC-001"
    assert act.category == WorkloadCategory.MACHINE
    assert act.workload_type == ActivityWorkloadType.MACHINE_PC
    assert act.plan_name == "Daily Backup"
    assert act.status == BackupActivityStatus.SUCCESS
    assert act.progress == 100
    assert act.duration_seconds == 600
    assert act.data_transferred_bytes == 1073741824
    assert act.finished_at == datetime.fromtimestamp(1776735285, tz=UTC)
    assert act.started_at == datetime.fromtimestamp(1776734685, tz=UTC)


async def test_list_running_activity_has_no_finished_at() -> None:
    async with connected_session() as (session, m):

        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RUNNING]})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list()
        await session.disconnect()

    act = activities[0]
    assert act.status == BackupActivityStatus.BACKING_UP
    assert act.finished_at is None
    assert act.duration_seconds is None
    assert act.data_transferred_bytes == 0
    assert act.progress == 42


async def test_running_activity_minus_one_duration_is_none() -> None:
    """When API returns durationTime="-1" (backup in progress), duration_seconds should be None."""
    async with connected_session() as (session, m):

        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RUNNING_NEG_DURATION]})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list()
        await session.disconnect()

    act = activities[0]
    assert act.duration_seconds is None


async def test_list_parses_data_change_and_deduped_bytes() -> None:
    """list() should populate data_change_bytes and data_deduped_bytes from changeDataSize/dedupedDataSize."""
    raw = {"activity": {
        "uid": "act-x", "namespace": "ns",
        "spec": {"workloadType": "PC", "workloadName": "W", "workload": {"uid": "uid"}, "planName": "P"},
        "status": {
            "executionId": "E", "backupStatus": "SUCCESS", "startTime": "1000000", "endTime": "1000600",
            "durationTime": "600", "transferredDataSize": "1073741824", "progress": 100,
            "changeDataSize": "524288000", "dedupedDataSize": "262144000",
        },
    }}
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [raw]})
        acts, _ = await BackupActivityCollection(session).list()
        await session.disconnect()
    assert acts[0].data_change_bytes == 524288000
    assert acts[0].data_deduped_bytes == 262144000


async def test_list_data_change_bytes_is_none_when_minus_one() -> None:
    """list() should convert changeDataSize='-1' (not applicable) to None."""
    raw = {"activity": {
        "uid": "act-y", "namespace": "ns",
        "spec": {"workloadType": "FS", "workloadName": "FS-01", "workload": {"uid": "uid"}, "planName": "P"},
        "status": {
            "executionId": "E2", "backupStatus": "SUCCESS", "startTime": "1000000", "endTime": "1000600",
            "durationTime": "600", "transferredDataSize": "-1", "progress": 100,
            "changeDataSize": "-1", "dedupedDataSize": "-1",
        },
    }}
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [raw]})
        acts, _ = await BackupActivityCollection(session).list()
        await session.disconnect()
    assert acts[0].data_change_bytes is None
    assert acts[0].data_deduped_bytes is None
    assert acts[0].data_transferred_bytes is None


@pytest.mark.parametrize("api_status,expected", [
    ("ERROR",            BackupActivityStatus.FAILED),
    ("UNKNOWN",          BackupActivityStatus.FAILED),
    ("CANCELING",        BackupActivityStatus.CANCELING),
    ("NOT_BACKED_UP_YET", BackupActivityStatus.QUEUING),
])
async def test_backup_status_mapping(api_status: str, expected: BackupActivityStatus) -> None:
    """API backupStatus values (e.g. ERROR) should map correctly to BackupActivityStatus; must not fall back to RUNNING."""
    raw = {"activity": {
        "uid": "act-x", "namespace": "ns",
        "spec": {"workloadType": "PC", "workloadName": "W", "workload": {"uid": "uid"}, "planName": "P"},
        "status": {"executionId": "E", "backupStatus": api_status, "startTime": "1000000", "endTime": "0",
                   "durationTime": "0", "transferredDataSize": "0", "progress": 0},
    }}
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [raw]})
        acts, total = await BackupActivityCollection(session).list()
        await session.disconnect()
    assert acts[0].status == expected


async def test_list_status_sends_server_side_filter() -> None:
    """list(status=...) should pass backupStatus to the API for server-side filtering; no client-side filtering."""
    async with connected_session() as (session, m):

        pat = re.compile(r".*/api/v2/activity/backup/activities\?.*backupStatus=SUCCESS")
        m.get(pat, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        completed, _ = await BackupActivityCollection(session).list(status=[BackupActivityStatus.SUCCESS])
        await session.disconnect()

    assert len(completed) == 1
    assert completed[0].status == BackupActivityStatus.SUCCESS


async def test_list_failed_status_sends_multiple_api_statuses() -> None:
    """BackupActivityStatus.FAILED should expand to backupStatus=ERROR&backupStatus=UNKNOWN."""
    async with connected_session() as (session, m):

        # aiohttp sorts params alphabetically: ERROR < UNKNOWN
        pat = re.compile(r".*backupStatus=ERROR.*backupStatus=UNKNOWN")
        m.get(pat, payload=EMPTY_RESPONSE)
        result, total = await BackupActivityCollection(session).list(status=[BackupActivityStatus.FAILED])
        await session.disconnect()

    assert result == []


async def test_list_multiple_statuses_sends_combined_api_statuses() -> None:
    """list(status=[SUCCESS, FAILED]) should merge both backupStatus groups and pass them to the API."""
    async with connected_session() as (session, m):

        pat = re.compile(r".*backupStatus=.*backupStatus=")
        m.get(pat, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        result, total = await BackupActivityCollection(session).list(
            status=[BackupActivityStatus.SUCCESS, BackupActivityStatus.FAILED]
        )
        await session.disconnect()

    assert len(result) == 1


async def test_list_machine_types_sends_category_service_params() -> None:
    """machine_types=[PC, VM] should pass the corresponding categoryService params to the API (server-side filtering)."""
    async with connected_session() as (session, m):

        pat = re.compile(r".*/api/v2/activity/backup/activities\?.*categoryService=MACHINE_PC.*categoryService=MACHINE_VM.*")
        m.get(pat, payload={"activities": [SAMPLE_ACTIVITY_RAW, SAMPLE_ACTIVITY_RUNNING]})
        collection = BackupActivityCollection(session)
        machine_acts, total = await collection.list(
            machine_types=[MachineWorkloadType.PC, MachineWorkloadType.VM]
        )
        await session.disconnect()

    assert len(machine_acts) == 2


async def test_list_m365_types_sends_saas_service_type_params() -> None:
    """m365_types=[EXCHANGE, TEAMS] should pass saasServiceType to the API (not categoryService)."""
    from unittest.mock import AsyncMock, patch

    session = make_session()
    collection = BackupActivityCollection(session)
    with patch.object(
        session, "get", AsyncMock(return_value={"activities": [SAMPLE_M365_ACTIVITY_RUNNING]})
    ) as mock_get:
        acts, total = await collection.list(m365_types=[M365WorkloadType.EXCHANGE, M365WorkloadType.TEAMS])

    saas_values = {v for k, v in mock_get.call_args_list[0][1]["params"] if k == "saasServiceType"}
    assert saas_values == {"M365_USER_EXCHANGE", "M365_TEAMS"}
    assert len(acts) == 1
    assert acts[0].category == WorkloadCategory.M365


async def test_list_history_mode_queries_history_endpoint() -> None:
    """list(history=True) should query the HISTORY endpoint and return completed activities."""
    async with connected_session() as (session, m):

        m.get(HISTORY_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list(history=True)
        await session.disconnect()

    assert len(activities) == 1
    assert total == 1


async def test_list_passes_offset_to_api() -> None:
    """list(offset=50) should pass offset=50 to the API query params."""
    async with connected_session() as (session, m):

        base = f"{BASE_URL}/api/v2/activity/backup/activities"
        fixed = "&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
        m.get(f"{base}?listMethod=RECENT&offset=50&limit=25{fixed}",
              payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 75})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list(offset=50, limit=25)
        await session.disconnect()

    assert len(activities) == 1
    assert total == 75


async def test_list_history_mode_returns_history_total() -> None:
    """list(history=True) total reflects only the HISTORY endpoint total."""
    async with connected_session() as (session, m):

        m.get(HISTORY_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 150})
        activities, total = await BackupActivityCollection(session).list(history=True)
        await session.disconnect()

    assert len(activities) == 1
    assert total == 150


async def test_list_with_namespace_passes_namespace_param() -> None:
    """namespace should be passed to the API via the namespace param."""
    ns = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"
    async with connected_session() as (session, m):

        recent_with_ns = (
            f"{BASE_URL}/api/v2/activity/backup/activities"
            f"?listMethod=RECENT&offset=0&limit=100"
            f"&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
            f"&namespace={ns}"
        )
        m.get(recent_with_ns, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list(namespace=[ns])
        await session.disconnect()

    assert len(activities) == 1


async def test_list_with_workload_passes_workload_params() -> None:
    """workload should be passed to the API via the workload.uid/workload.namespace params."""
    wl = Workload(
        workload_id="fbf93425-d9e7-1c70-f4b2-231d7fc7b116",
        name="vm-web-01",
        category=WorkloadCategory.MACHINE,
        namespace="9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.NO_BACKUPS,
        plan=ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    )
    async with connected_session() as (session, m):

        recent_with_workload = (
            f"{BASE_URL}/api/v2/activity/backup/activities"
            "?listMethod=RECENT&offset=0&limit=100"
            "&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
            f"&workload.uid={wl.workload_id}&workload.namespace={wl.namespace}"
        )
        m.get(recent_with_workload, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list(workload=wl)
        await session.disconnect()

    assert len(activities) == 1


async def test_list_with_namespace_and_workload_sends_both() -> None:
    """namespace and workload filters compose -- both sets of params are sent (AND)."""
    ns = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"
    wl = Workload(
        workload_id="fbf93425-d9e7-1c70-f4b2-231d7fc7b116",
        name="vm-web-01",
        category=WorkloadCategory.MACHINE,
        namespace=ns,
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.NO_BACKUPS,
        plan=ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    )
    async with connected_session() as (session, m):

        recent_with_both = (
            f"{BASE_URL}/api/v2/activity/backup/activities"
            "?listMethod=RECENT&offset=0&limit=100"
            "&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
            f"&namespace={ns}"
            f"&workload.uid={wl.workload_id}&workload.namespace={wl.namespace}"
        )
        m.get(recent_with_both, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        collection = BackupActivityCollection(session)
        activities, total = await collection.list(namespace=[ns], workload=wl)
        await session.disconnect()

    assert len(activities) == 1


# ── list() filter conditions (fill gaps) ────────────────────────────────────


async def test_list_filter_by_until() -> None:
    """until filter: rangeEndTime is passed server-side to the API; mock returns empty result."""
    session = make_session()
    cutoff = datetime.fromtimestamp(1776734000, tz=UTC)

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

        pat = re.compile(r".*/api/v2/activity/backup/activities\?.*rangeEndTime=1776734000")
        m.get(pat, payload=EMPTY_RESPONSE)
        collection = BackupActivityCollection(session)
        acts, total = await collection.list(until=cutoff)
        await session.disconnect()

    assert len(acts) == 0


# ── M365 processed counts (P2) ────────────────────────────────────────────


async def test_m365_activity_has_processed_counts() -> None:
    """M365 activity should parse processedSuccessCount / WarningCount / ErrorCount."""
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [SAMPLE_M365_ACTIVITY_RUNNING]})
        acts, total = await BackupActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.processed_success_count == 5
    assert act.processed_warning_count == 2
    assert act.processed_error_count == 1
    assert act.items_processed == 8


async def test_machine_activity_has_no_processed_counts() -> None:
    """Machine activity processed_success_count and the other two count fields should be None."""
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RUNNING]})
        acts, total = await BackupActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.processed_success_count is None
    assert act.processed_warning_count is None
    assert act.processed_error_count is None
    assert act.items_processed is None


def test_items_processed_property_sums_all_counts() -> None:
    """items_processed property should return the sum of all three counts."""
    from datetime import datetime

    from synology_apm.sdk.enums import BackupActivityStatus, WorkloadCategory
    from synology_apm.sdk.models.activity import BackupActivity
    act = BackupActivity(
        activity_id="x", execution_id="E", namespace="ns",
        category=WorkloadCategory.M365, workload_type=ActivityWorkloadType.M365,
        workload_id="w", workload_namespace="", workload_name="W", plan_name="P",
        status=BackupActivityStatus.BACKING_UP,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=None, duration_seconds=None, data_transferred_bytes=None,
        progress=0,
        processed_success_count=10, processed_warning_count=3, processed_error_count=2,
    )
    assert act.items_processed == 15


def test_items_processed_returns_none_for_machine() -> None:
    """items_processed property should return None when all three count fields are None."""
    from datetime import datetime

    from synology_apm.sdk.enums import BackupActivityStatus, WorkloadCategory
    from synology_apm.sdk.models.activity import BackupActivity
    act = BackupActivity(
        activity_id="x", execution_id="E", namespace="ns",
        category=WorkloadCategory.MACHINE, workload_type=ActivityWorkloadType.MACHINE_PC,
        workload_id="w", workload_namespace="", workload_name="W", plan_name="P",
        status=BackupActivityStatus.SUCCESS,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=None, duration_seconds=None, data_transferred_bytes=None,
        progress=0,
    )
    assert act.items_processed is None


# ── _parse_data_sizes (dedup negative value fix) ──────────────────────────


@pytest.mark.parametrize("changed,deduped,exp_changed,exp_deduped", [
    ("1005588480", "449496803", 1005588480, 449496803),  # normal positive values
    ("-1",         "-1",        None,        None),       # not applicable → None
    ("0",          "0",         0,           0),          # zero is a valid value
    (None,         None,        None,        None),       # missing fields → None
    ("1024",       "0",         1024,        0),          # mixed
])
async def test_list_data_size_fields(changed: str | None, deduped: str | None, exp_changed: int | None, exp_deduped: int | None) -> None:
    """list() maps changeDataSize/dedupedDataSize to data_change_bytes/data_deduped_bytes."""
    status: dict[str, Any] = {}
    if changed is not None:
        status["changeDataSize"] = changed
    if deduped is not None:
        status["dedupedDataSize"] = deduped
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [make_backup_activity_raw(status=status)]})
        acts, _ = await BackupActivityCollection(session).list()
        await session.disconnect()
    assert acts[0].data_change_bytes == exp_changed
    assert acts[0].data_deduped_bytes == exp_deduped


async def test_m365_activity_uses_application_m365_workload_type() -> None:
    """workloadType='APPLICATION_M365' should be recognised as M365 category and parsed processed counts."""
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [SAMPLE_M365_ACTIVITY_RUNNING]})
        acts, total = await BackupActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.category == WorkloadCategory.M365
    assert act.workload_type == ActivityWorkloadType.M365
    assert act.processed_success_count == 5
    assert act.items_processed == 8


# ── FS activity processed counts ──────────────────────────────────────────


async def test_fs_backup_activity_has_processed_counts() -> None:
    """MACHINE_FS backup activity should parse processedSuccessCount / WarningCount / ErrorCount."""
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [SAMPLE_FS_ACTIVITY_RUNNING]})
        acts, total = await BackupActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.category == WorkloadCategory.MACHINE
    assert act.processed_success_count == 312
    assert act.processed_warning_count == 5
    assert act.processed_error_count == 2
    assert act.items_processed == 319


async def test_vm_backup_activity_has_no_processed_counts() -> None:
    """VM backup activity (non-FS) processed counts should be None even if the API returns values."""
    vm_with_counts = {
        "activity": {
            "uid": "act-vm-001",
            "namespace": "ns-001",
            "spec": {
                "workloadType": "MACHINE_VM",
                "workloadName": "vm-web-01",
                "workload": {"uid": "vm-uid-001"},
                "planName": "Daily",
            },
            "status": {
                "executionId": "E1",
                "backupStatus": "SUCCESS",
                "startTime": "1776734685",
                "endTime": "1776738285",
                "durationTime": "3600",
                "transferredDataSize": "0",
                "progress": 100,
                "processedSuccessCount": 99,
            },
        }
    }
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [vm_with_counts]})
        acts, total = await BackupActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.processed_success_count is None
    assert act.items_processed is None


# ── keyword search ───────────────────────────────────────────────────────────


async def test_backup_list_keyword_appends_keyword_param() -> None:
    """BackupActivityCollection.list(keyword=...) should add the keyword param to the API request."""
    async with connected_session() as (session, m):

        recent_with_kw = RECENT_URL + "&keyword=CORP-PC-001"
        m.get(recent_with_kw, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        acts, total = await BackupActivityCollection(session).list(keyword="CORP-PC-001")
        await session.disconnect()

    assert len(acts) == 1
    assert acts[0].workload_name == "CORP-PC-001"


# ── get_latest_by_workload_name ───────────────────────────────────────────────


_KEYWORD_RECENT_PAT = re.compile(r"(?=.*keyword=CORP-PC-001)(?=.*listMethod=RECENT).*")
_KEYWORD_HISTORY_PAT = re.compile(r"(?=.*keyword=CORP-PC-001)(?=.*listMethod=HISTORY).*")


async def test_backup_get_latest_by_workload_name_returns_matching_activity() -> None:
    """get_latest_by_workload_name() returns the matching activity when an exact name match is found."""
    async with connected_session() as (session, m):
        m.get(_KEYWORD_RECENT_PAT, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        # get() enrichment: uid lookup via keyword-less RECENT list, then detail + logs
        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        m.get(re.compile(r".*/api/v1/activity/backup/activity\?.*"), payload={"activity": {}})
        m.get(re.compile(r".*/api/v1/log/detail-log.*"), payload={"detailLogs": []})
        result = await BackupActivityCollection(session).get_latest_by_workload_name("CORP-PC-001")
        await session.disconnect()

    assert result.activity_id == "act-uid-001"
    assert result.workload_name == "CORP-PC-001"
    assert result.status == BackupActivityStatus.SUCCESS


async def test_backup_get_latest_by_workload_name_raises_when_no_exact_match() -> None:
    """Should raise ResourceNotFoundError when keyword results contain no exact name match."""
    other = make_backup_activity_raw(spec={"workloadName": "CORP-PC-002"})
    async with connected_session() as (session, m):
        m.get(_KEYWORD_RECENT_PAT, payload={"activities": [other]})
        m.get(_KEYWORD_HISTORY_PAT, payload={"activities": []})
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await BackupActivityCollection(session).get_latest_by_workload_name("CORP-PC-001")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Activity", resource_id="CORP-PC-001")


async def test_backup_get_latest_by_workload_name_falls_back_to_history() -> None:
    """get_latest_by_workload_name() should search HISTORY when RECENT returns no exact match."""
    async with connected_session() as (session, m):
        m.get(_KEYWORD_RECENT_PAT, payload={"activities": []})
        m.get(_KEYWORD_HISTORY_PAT, payload={"activities": [SAMPLE_ACTIVITY_RAW]})
        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        m.get(re.compile(r".*/api/v1/activity/backup/activity\?.*"), payload={"activity": {}})
        m.get(re.compile(r".*/api/v1/log/detail-log.*"), payload={"detailLogs": []})
        result = await BackupActivityCollection(session).get_latest_by_workload_name("CORP-PC-001")
        await session.disconnect()

    # The match only exists in the HISTORY response, so returning it proves the fallback ran.
    assert result.activity_id == "act-uid-001"
    assert result.workload_name == "CORP-PC-001"


# ════════════════════════════════════════════════════════════════════════════
# backup list — since / until filter params
# ════════════════════════════════════════════════════════════════════════════

async def test_backup_list_since_sends_range_start_time() -> None:
    """list(since=dt) should append rangeStartTime to the query params."""
    from unittest.mock import AsyncMock, patch
    since = datetime(2026, 5, 1, tzinfo=UTC)
    session = make_session()
    collection = BackupActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [], "total": 0})) as mock_get:
        await collection.list(since=since)
    params = dict(mock_get.call_args_list[0][1]["params"])
    assert params.get("rangeStartTime") == str(int(since.timestamp()))


async def test_backup_list_until_sends_range_end_time() -> None:
    """list(until=dt) should append rangeEndTime to the query params."""
    from unittest.mock import AsyncMock, patch
    until = datetime(2026, 5, 7, tzinfo=UTC)
    session = make_session()
    collection = BackupActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [], "total": 0})) as mock_get:
        await collection.list(until=until)
    params = dict(mock_get.call_args_list[0][1]["params"])
    assert params.get("rangeEndTime") == str(int(until.timestamp()))


# ════════════════════════════════════════════════════════════════════════════
# backup activity parser — machineStatusInfo / verify_status
# ════════════════════════════════════════════════════════════════════════════

_ACT_WITH_VERIFY_VM = {
    "activity": {
        "uid": "act-verify-vm",
        "namespace": "ns-001",
        "spec": {
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-web-01",
            "workload": {"uid": "vm-uid-001"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_V1",
            "backupStatus": "SUCCESS",
            "startTime": "1776734685",
            "endTime": "1776735285",
            "durationTime": "600",
            "transferredDataSize": "0",
            "progress": 100,
            "machineStatusInfo": {"verifyStatus": "VERIFY_COMPLETED"},
        },
    }
}

_ACT_WITH_VERIFY_NOT_ENABLED_PC = {
    "activity": {
        "uid": "act-verify-pc",
        "namespace": "ns-001",
        "spec": {
            "workloadType": "MACHINE_PC",
            "workloadName": "CORP-PC-001",
            "workload": {"uid": "pc-uid-001"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_PC1",
            "backupStatus": "SUCCESS",
            "startTime": "1776734685",
            "endTime": "1776735285",
            "durationTime": "600",
            "transferredDataSize": "0",
            "progress": 100,
            "machineStatusInfo": {"verifyStatus": "VERIFY_NOT_ENABLED"},
        },
    }
}

_ACT_WITH_UNKNOWN_VERIFY = {
    "activity": {
        "uid": "act-verify-unk",
        "namespace": "ns-001",
        "spec": {
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-app-01",
            "workload": {"uid": "vm-uid-002"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_V2",
            "backupStatus": "SUCCESS",
            "startTime": "1776734685",
            "endTime": "1776735285",
            "durationTime": "600",
            "transferredDataSize": "0",
            "progress": 100,
            "machineStatusInfo": {"verifyStatus": "VERIFY_UNKNOWN_FUTURE_VALUE"},
        },
    }
}

_ACT_WITH_VERIFY_NONE_STRING = {
    "activity": {
        "uid": "act-verify-none-str",
        "namespace": "ns-001",
        "spec": {
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-db-01",
            "workload": {"uid": "vm-uid-003"},
            "planName": "Daily Backup",
        },
        "status": {
            "executionId": "ABE_VN",
            "backupStatus": "SUCCESS",
            "startTime": "1776734685",
            "endTime": "1776735285",
            "durationTime": "600",
            "transferredDataSize": "0",
            "progress": 100,
            "machineStatusInfo": {"verifyStatus": "VERIFY_NONE"},
        },
    }
}


async def test_backup_parser_maps_verify_none_string_to_none() -> None:
    """Parser treats machineStatusInfo.verifyStatus=VERIFY_NONE the same as absent (returns None)."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    collection = BackupActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [_ACT_WITH_VERIFY_NONE_STRING], "total": 1})):
        acts, _ = await collection.list()
    assert acts[0].verify_status is None


async def test_backup_parser_maps_verify_completed_for_vm() -> None:
    """Parser maps machineStatusInfo.verifyStatus=VERIFY_COMPLETED to VerifyStatus.SUCCESS for VM."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    collection = BackupActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [_ACT_WITH_VERIFY_VM], "total": 1})):
        acts, _ = await collection.list()
    assert acts[0].verify_status == VerifyStatus.SUCCESS


async def test_backup_parser_maps_verify_not_enabled_for_pc_to_none() -> None:
    """Parser converts machineStatusInfo.verifyStatus=VERIFY_NOT_ENABLED to None for PC (unsupported type)."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    collection = BackupActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [_ACT_WITH_VERIFY_NOT_ENABLED_PC], "total": 1})):
        acts, _ = await collection.list()
    assert acts[0].verify_status is None


async def test_backup_parser_maps_unknown_verify_status_to_none() -> None:
    """Parser returns None for an unrecognised verifyStatus string."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    collection = BackupActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [_ACT_WITH_UNKNOWN_VERIFY], "total": 1})):
        acts, _ = await collection.list()
    assert acts[0].verify_status is None
