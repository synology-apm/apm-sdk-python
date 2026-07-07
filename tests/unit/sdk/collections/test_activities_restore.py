"""Unit tests for RestoreActivityCollection."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
from aioresponses import aioresponses

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.collections.activities import RestoreActivityCollection
from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    HypervisorType,
    RestoreActivityStatus,
    RestoreType,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.activity import RestoreActivity
from synology_apm.sdk.models.hypervisor import Hypervisor
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.workload import Workload
from tests.unit.sdk.conftest import (
    BASE_URL,
    LOGIN_OK,
    LOGIN_URL,
    assert_resource_error,
    connected_session,
    make_restore_activity_raw,
    make_session,
)

RESTORE_BASE_URL = f"{BASE_URL}/api/v2/activity/restore/activities"

RESTORE_RECENT_URL = (
    f"{RESTORE_BASE_URL}"
    "?listMethod=RECENT&offset=0&limit=100"
    "&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
)
RESTORE_HISTORY_URL = (
    f"{RESTORE_BASE_URL}"
    "?listMethod=HISTORY&offset=0&limit=100"
    "&orderBy=ORDER_BY_START_TIME&orderDirection=ORDER_DIRECTION_DESC"
)

SAMPLE_RESTORE_ACTIVITY_RAW: dict[str, Any] = {
    "activity": {
        "uid": "rst-uid-001",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "executionId": "97",
            "workload": {"uid": "wl-uid-001", "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5"},
            "workloadType": "APPLICATION_M365",
            "workloadName": "alice@contoso.com",
            "restoreType": "FILE_LEVEL_RESTORE",
            "destination": "alice@contoso.com",
            "operator": "admin",
            "destinationPath": "/some/path",
            "versionTimestamp": "1777270000",
            "restoreFromInfo": {
                "destinationType": "APPLIANCE", "storageType": "NONE",
                "hostname": "apm-server-01", "address": "192.0.2.1",
                "description": "", "containerName": "",
            },
            "machineInfo": {
                "additionalInfo": (
                    '{"hypervisor":{"host_id":"ha-host","name":"esxi1.example.com"},'
                    '"inventory_addr":"192.0.2.40","inventory_id":"inv-001",'
                    '"inventory_name":"esxi1.example.com","inventory_type":"ESXi"}'
                ),
            },
        },
        "status": {
            "startTime": "1777274897",
            "endTime": "1777274903",
            "progress": 100,
            "restoreStatus": "SUCCESS",
            "transferredSize": "1601",
            "processedSuccessCount": 2,
            "processedWarningCount": 0,
            "processedErrorCount": 0,
        },
    },
    "permission": {"canBackup": True, "canRestore": True, "canSelfService": False},
}

SAMPLE_RESTORE_RUNNING_RAW: dict[str, Any] = {
    "activity": {
        "uid": "rst-uid-002",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "executionId": "98",
            "workload": {"uid": "wl-uid-002", "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5"},
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-web-01",
            "restoreType": "FULL_RESTORE",
            "destination": "vm-web-01-restored",
            "operator": "admin",
        },
        "status": {
            "startTime": "1777280000",
            "endTime": "0",
            "progress": 55,
            "restoreStatus": "RESTORING",
            "transferredSize": "0",
        },
    },
    "permission": {"canBackup": False, "canRestore": True, "canSelfService": False},
}

EMPTY_RESTORE_RESPONSE: dict[str, Any] = {"activities": []}


# ── list() ─────────────────────────────────────────────────────────────────


async def test_restore_list_parses_fields() -> None:
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [SAMPLE_RESTORE_ACTIVITY_RAW], "total": 1})
        acts, total = await RestoreActivityCollection(session).list()
        await session.disconnect()

    assert total == 1
    act = acts[0]
    assert act.activity_id == "rst-uid-001"
    assert act.execution_id == "97"
    assert act.workload_id == "wl-uid-001"
    assert act.workload_name == "alice@contoso.com"
    assert act.category == WorkloadCategory.M365
    assert act.workload_type == ActivityWorkloadType.M365
    assert act.namespace == "9053e422-4154-4abc-b03a-6e3d8e17b2d5"
    assert isinstance(act, RestoreActivity)
    assert act.status == RestoreActivityStatus.SUCCESS
    assert act.progress == 100
    assert act.data_transferred_bytes == 1601
    assert act.restore_type == RestoreType.FILE_LEVEL
    assert act.restore_destination == "alice@contoso.com"
    assert act.operator == "admin"
    assert act.finished_at is not None
    assert act.duration_seconds == 6  # 1777274903 - 1777274897
    assert act.version_timestamp is not None
    assert act.destination_path == "/some/path"
    assert act.restore_from_info == LocationInfo(
        is_remote_storage=False, identifier="", name="apm-server-01", endpoint="192.0.2.1", vault=None,
    )
    assert act.destination_inventory == Hypervisor(
        hypervisor_id="", hostname="esxi1.example.com", address="192.0.2.40",
        host_type=HypervisorType.VSPHERE_ESXI, account="", description="", port=0, version="",
    )


async def test_restore_list_running_activity_has_restoring_status() -> None:
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [SAMPLE_RESTORE_RUNNING_RAW]})
        acts, total = await RestoreActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.status == RestoreActivityStatus.RESTORING
    assert act.finished_at is None
    assert act.duration_seconds is None
    assert act.data_transferred_bytes == 0
    assert act.progress == 55
    assert act.category == WorkloadCategory.MACHINE


async def _list_single_restore_activity(spec: dict[str, Any] | None = None) -> RestoreActivity:
    """Run list() against a single factory-built activity with the given spec overrides."""
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [make_restore_activity_raw(spec=spec)]})
        acts, _ = await RestoreActivityCollection(session).list()
        await session.disconnect()
    return acts[0]


async def test_restore_activity_without_optional_spec_fields_has_none_defaults() -> None:
    """An activity with no versionTimestamp/restoreFromInfo/destinationPath/machineInfo parses all four new fields as None."""
    act = await _list_single_restore_activity()
    assert act.version_timestamp is None
    assert act.restore_from_info is None
    assert act.destination_path is None
    assert act.destination_inventory is None


async def test_restore_from_info_compatible_s3_is_remote_storage() -> None:
    act = await _list_single_restore_activity(spec={
        "restoreFromInfo": {
            "destinationType": "COMPATIBLE_S3", "storageType": "S3",
            "hostname": "DSM-Storage", "address": "192.0.2.20:8444",
            "description": "", "containerName": "MyVault",
        },
    })
    assert act.restore_from_info == LocationInfo(
        is_remote_storage=True, identifier="", name="DSM-Storage",
        endpoint="192.0.2.20:8444", vault="MyVault",
    )


async def test_destination_path_empty_string_is_none() -> None:
    act = await _list_single_restore_activity(spec={"destinationPath": ""})
    assert act.destination_path is None


async def test_destination_inventory_none_when_machine_info_absent() -> None:
    act = await _list_single_restore_activity()
    assert act.destination_inventory is None


async def test_destination_inventory_none_on_invalid_json() -> None:
    act = await _list_single_restore_activity(spec={"machineInfo": {"additionalInfo": "{not valid json"}})
    assert act.destination_inventory is None


async def test_destination_inventory_none_when_additional_info_empty() -> None:
    act = await _list_single_restore_activity(spec={"machineInfo": {"additionalInfo": ""}})
    assert act.destination_inventory is None


async def test_destination_inventory_none_when_json_not_object() -> None:
    act = await _list_single_restore_activity(spec={"machineInfo": {"additionalInfo": "[]"}})
    assert act.destination_inventory is None


async def test_destination_inventory_none_when_inventory_name_missing() -> None:
    act = await _list_single_restore_activity(
        spec={"machineInfo": {"additionalInfo": '{"inventory_addr": "192.0.2.40"}'}}
    )
    assert act.destination_inventory is None


@pytest.mark.parametrize("api_status,expected", [
    ("PREPARING",           RestoreActivityStatus.PREPARING),
    ("CANCELING",           RestoreActivityStatus.CANCELING),
    ("READY_FOR_MIGRATE",   RestoreActivityStatus.READY_FOR_MIGRATE),
    ("MIGRATE_VM_MANUALLY", RestoreActivityStatus.MIGRATE_VM_MANUALLY),
    ("MIGRATING",           RestoreActivityStatus.MIGRATING),
    ("SUCCESS",             RestoreActivityStatus.SUCCESS),
    ("FAILED",              RestoreActivityStatus.FAILED),
    ("ERROR",               RestoreActivityStatus.FAILED),
    ("DEVICE_MISSING",      RestoreActivityStatus.FAILED),
    ("MIGRATE_FAILED",      RestoreActivityStatus.FAILED),
    ("WARNING",             RestoreActivityStatus.PARTIAL),
    ("PARTIAL_SUCCESS",     RestoreActivityStatus.PARTIAL),
    ("CANCELED",            RestoreActivityStatus.CANCELED),
])
async def test_restore_status_mapping(api_status: str, expected: RestoreActivityStatus) -> None:
    raw = {"activity": {
        "uid": "rst-x", "namespace": "ns",
        "spec": {"workloadType": "MACHINE_VM", "workloadName": "W", "workload": {"uid": "uid"}, "executionId": "E"},
        "status": {"startTime": "1000000", "endTime": "0", "progress": 0, "restoreStatus": api_status,
                   "transferredSize": "0"},
    }, "permission": {}}
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [raw]})
        acts, total = await RestoreActivityCollection(session).list()
        await session.disconnect()
    assert acts[0].status == expected


async def test_restore_list_status_filter_sends_restore_status_param() -> None:
    """list(status=RestoreActivityStatus.SUCCESS) should pass restoreStatus=SUCCESS to the API."""
    async with connected_session() as (session, m):
        pat = re.compile(r".*/api/v2/activity/restore/activities\?.*restoreStatus=SUCCESS")
        m.get(pat, payload={"activities": [SAMPLE_RESTORE_ACTIVITY_RAW]})
        acts, total = await RestoreActivityCollection(session).list(status=[RestoreActivityStatus.SUCCESS])
        await session.disconnect()

    assert len(acts) == 1


async def test_restore_list_with_workload_passes_workload_params() -> None:
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
        pat = re.compile(
            r".*/api/v2/activity/restore/activities\?.*"
            r"workload\.namespace=9053e422-4154-4abc-b03a-6e3d8e17b2d5"
            r".*workload\.uid=fbf93425-d9e7-1c70-f4b2-231d7fc7b116"
        )
        m.get(pat, payload={"activities": [SAMPLE_RESTORE_ACTIVITY_RAW]})
        acts, total = await RestoreActivityCollection(session).list(workload=wl)
        await session.disconnect()

    assert len(acts) == 1


async def test_restore_list_restoring_status_sends_only_restoring() -> None:
    """RESTORING filter should send exactly restoreStatus=RESTORING and not include PREPARING."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    collection = RestoreActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [], "total": 0})) as mock_get:
        await collection.list(status=[RestoreActivityStatus.RESTORING])
    status_values = [v for k, v in mock_get.call_args_list[0][1]["params"] if k == "restoreStatus"]
    assert status_values == ["RESTORING"]


async def test_restore_list_preparing_status_sends_only_preparing() -> None:
    """PREPARING filter should send exactly restoreStatus=PREPARING and not include RESTORING."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    collection = RestoreActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [], "total": 0})) as mock_get:
        await collection.list(status=[RestoreActivityStatus.PREPARING])
    status_values = [v for k, v in mock_get.call_args_list[0][1]["params"] if k == "restoreStatus"]
    assert status_values == ["PREPARING"]


async def test_restore_list_multiple_statuses() -> None:
    """list(status=[SUCCESS, FAILED]) should merge both restoreStatus groups and pass them to the API."""
    async with connected_session() as (session, m):
        pat = re.compile(r".*restoreStatus=.*restoreStatus=")
        m.get(pat, payload={"activities": [SAMPLE_RESTORE_ACTIVITY_RAW]})
        result, total = await RestoreActivityCollection(session).list(
            status=[RestoreActivityStatus.SUCCESS, RestoreActivityStatus.FAILED]
        )
        await session.disconnect()

    assert len(result) == 1


async def test_restore_list_m365_has_processed_counts() -> None:
    """M365 restore activity should parse processedSuccessCount / WarningCount / ErrorCount."""
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [SAMPLE_RESTORE_ACTIVITY_RAW]})
        acts, total = await RestoreActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.processed_success_count == 2
    assert act.processed_warning_count == 0
    assert act.processed_error_count == 0
    assert act.items_processed == 2


async def test_restore_list_machine_has_no_processed_counts() -> None:
    """Machine restore activity processed counts should be None."""
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [SAMPLE_RESTORE_RUNNING_RAW]})
        acts, total = await RestoreActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.processed_success_count is None
    assert act.items_processed is None


async def test_restore_list_transferred_size_field_name() -> None:
    """restore list uses transferredSize (not transferredDataSize)."""
    raw = {"activity": {
        "uid": "rst-x", "namespace": "ns",
        "spec": {"workloadType": "MACHINE_VM", "workloadName": "W", "workload": {"uid": "uid"}, "executionId": "E"},
        "status": {"startTime": "1000000", "endTime": "1000100", "progress": 100,
                   "restoreStatus": "SUCCESS", "transferredSize": "2048"},
    }, "permission": {}}
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [raw]})
        acts, total = await RestoreActivityCollection(session).list()
        await session.disconnect()
    assert acts[0].data_transferred_bytes == 2048


# ── get() ──────────────────────────────────────────────────────────────────


async def test_restore_list_workload_filter_http_404_raises_with_resource_fields() -> None:
    """list(workload=...) maps the API's HTTP 404 for an unknown workload to a fully-populated error."""
    wl = Workload(
        workload_id="00000000-0000-0000-0000-000000000000",
        name="nonexistent-workload",
        category=WorkloadCategory.MACHINE,
        namespace="00000000-0000-0000-0000-000000000000",
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.NO_BACKUPS,
        plan=ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    )
    async with connected_session() as (session, m):
        m.get(re.compile(r".*/api/v2/activity/restore/activities.*"), status=404)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await RestoreActivityCollection(session).list(workload=wl)
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Workload", resource_id=wl.workload_id)
    assert exc_info.value.error_code == 404


async def test_restore_get_raises_resource_not_found() -> None:
    """restore get() should raise ResourceNotFoundError when the activity is not found."""
    async with connected_session() as (session, m):
        m.get(re.compile(r".*/api/v2/activity/restore/activities.*listMethod=RECENT.*"), payload=EMPTY_RESTORE_RESPONSE)
        m.get(re.compile(r".*/api/v2/activity/restore/activities.*listMethod=HISTORY.*"), payload=EMPTY_RESTORE_RESPONSE)

        with pytest.raises(ResourceNotFoundError) as exc_info:
            await RestoreActivityCollection(session).get("no-such-uid")
        await session.disconnect()

    assert exc_info.value.resource_type == "Activity"
    assert exc_info.value.resource_id == "no-such-uid"


async def test_restore_get_uses_restore_activity_uid_param() -> None:
    """restore get() should use restoreActivityUid (not backupActivityUid) when calling the log endpoint."""
    session = make_session()
    log_url = (
        f"{BASE_URL}/api/v1/log/detail-log"
        "?limit=1001&offset=0&restoreActivityUid=rst-uid-001"
    )
    log_payload = {
        "detailLogs": [
            {"timestamp": "1777274903", "level": "LEVEL_INFORMATION", "description": "Restore complete"},
        ]
    }

    with aioresponses() as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(RESTORE_RECENT_URL, payload={"activities": [SAMPLE_RESTORE_ACTIVITY_RAW], "total": 1})
        m.get(log_url, payload=log_payload)

        act = await RestoreActivityCollection(session).get("rst-uid-001")
        await session.disconnect()

    assert act.activity_id == "rst-uid-001"
    assert act.log_entries is not None
    assert len(act.log_entries) == 1
    assert act.log_entries[0].message == "Restore complete"


# ── cancel() ──────────────────────────────────────────────────────────────


async def test_restore_cancel_posts_activities_body() -> None:
    """restore cancel() should POST the exact body fields required by the cancel-restore API."""
    from datetime import datetime
    from unittest.mock import AsyncMock, MagicMock

    from synology_apm.sdk.enums import ActivityWorkloadType, RestoreActivityStatus, WorkloadCategory
    from synology_apm.sdk.models.activity import RestoreActivity

    act = RestoreActivity(
        activity_id="rst-uid-002",
        execution_id="13",
        workload_id="50c90a1d-0f25-44ac-b22c-44acac6bc1e8",
        workload_name="vm-web-01",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_VM,
        namespace="9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        plan_name="",
        status=RestoreActivityStatus.RESTORING,
        started_at=datetime(2026, 4, 27, tzinfo=UTC),
        finished_at=None,
        duration_seconds=None,
        data_transferred_bytes=None,
        progress=30,
        workload_namespace="9053e422-4154-4abc-b03a-6e3d8e17b2d5",
    )

    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value={})
    await RestoreActivityCollection(mock_session).cancel(act)

    _, kwargs = mock_session.post.call_args
    entry = kwargs["json"]["activities"][0]
    assert entry["workloadType"]          == "MACHINE_VM"
    assert entry["executionId"]           == "13"
    assert entry["namespace"]             == "9053e422-4154-4abc-b03a-6e3d8e17b2d5"
    assert entry["workload"]["uid"]       == "50c90a1d-0f25-44ac-b22c-44acac6bc1e8"
    assert entry["workload"]["namespace"] == "9053e422-4154-4abc-b03a-6e3d8e17b2d5"


async def test_restore_cancel_uses_workload_namespace_not_activity_namespace() -> None:
    """cancel() workload.namespace should come from spec.workload.namespace, not activity.namespace."""
    from datetime import datetime
    from unittest.mock import AsyncMock, MagicMock

    from synology_apm.sdk.enums import ActivityWorkloadType, RestoreActivityStatus, WorkloadCategory
    from synology_apm.sdk.models.activity import RestoreActivity

    # workload_namespace deliberately differs from activity.namespace
    act = RestoreActivity(
        activity_id="rst-uid-003",
        execution_id="99",
        workload_id="wl-uid-abc",
        workload_name="FileServer",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_FS,
        namespace="activity-ns-aaaa",
        plan_name="",
        status=RestoreActivityStatus.RESTORING,
        started_at=datetime(2026, 4, 27, tzinfo=UTC),
        finished_at=None,
        duration_seconds=None,
        data_transferred_bytes=None,
        progress=10,
        workload_namespace="workload-ns-bbbb",
    )

    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value={})
    collection = RestoreActivityCollection(mock_session)
    await collection.cancel(act)

    _, kwargs = mock_session.post.call_args
    body = kwargs["json"]
    entry = body["activities"][0]
    # workload.namespace must be workload_namespace, not activity.namespace
    assert entry["workload"]["namespace"] == "workload-ns-bbbb"
    # top-level namespace remains the activity namespace
    assert entry["namespace"] == "activity-ns-aaaa"
    assert entry["workloadType"] == "MACHINE_FS"


async def test_restore_cancel_does_not_require_namespace() -> None:
    """restore cancel() unlike backup cancel() does not require a namespace parameter; it accepts an Activity object."""
    import inspect
    sig = inspect.signature(RestoreActivityCollection.cancel)
    params = list(sig.parameters.keys())
    assert "namespace" not in params
    assert "activity" in params


async def test_restore_cancel_unknown_workload_type_raises() -> None:
    """cancel() should raise InvalidOperationError when workload_type is UNKNOWN."""
    from unittest.mock import AsyncMock, MagicMock

    import pytest

    from synology_apm.sdk.enums import ActivityWorkloadType, RestoreActivityStatus, WorkloadCategory
    from synology_apm.sdk.exceptions import InvalidOperationError
    from synology_apm.sdk.models.activity import RestoreActivity

    act = RestoreActivity(
        activity_id="rst-uid-unknown",
        execution_id="X1",
        workload_id="wl-uid-x",
        workload_name="Unknown",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.UNKNOWN,
        namespace="ns-x",
        plan_name="",
        status=RestoreActivityStatus.RESTORING,
        started_at=datetime(2026, 4, 27, tzinfo=UTC),
        finished_at=None,
        duration_seconds=None,
        data_transferred_bytes=None,
        progress=0,
        workload_namespace="ns-x",
    )
    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value={})
    with pytest.raises(InvalidOperationError) as exc_info:
        await RestoreActivityCollection(mock_session).cancel(act)

    assert_resource_error(exc_info, resource_type="RestoreActivity", resource_id="rst-uid-unknown")
    mock_session.post.assert_not_called()


# ── x-syno-tunnel-route header ────────────────────────────────────────────


async def test_restore_get_sends_tunnel_route_header() -> None:
    """RestoreActivityCollection.get() should set x-syno-tunnel-route header to activity.namespace when calling the log API."""
    from unittest.mock import AsyncMock

    session = AsyncMock(spec=WebAPISession)
    session.get.side_effect = [
        {"activities": [{"activity": {
            "uid": "rst-uid-001",
            "namespace": "ns-restore-server-003",
            "spec": {"workload": {"uid": "wl-uid", "namespace": ""}, "workloadType": "VM",
                     "executionId": "EX_RST_1"},
            "status": {"restoreStatus": "SUCCESS", "startTime": "1776734685", "endTime": "0",
                       "progress": 100, "transferredSize": "0"},
        }}], "total": 1},
        {"detailLogs": []},
    ]

    collection = RestoreActivityCollection(session)
    await collection.get("rst-uid-001")

    log_call = next(c for c in session.get.call_args_list if "detail-log" in c.args[0])
    assert log_call.kwargs.get("headers") == {"x-syno-tunnel-route": "ns-restore-server-003"}


# ── FS activity processed counts ──────────────────────────────────────────


async def test_fs_restore_activity_has_processed_counts() -> None:
    """MACHINE_FS restore activity should also parse processed counts."""
    fs_restore = {
        "activity": {
            "uid": "rst-fs-001",
            "namespace": "ns-001",
            "spec": {
                "executionId": "R1",
                "workload": {"uid": "fs-uid-001", "namespace": "ns-001"},
                "workloadType": "MACHINE_FS",
                "workloadName": "Corp Share",
                "restoreType": "FILE_LEVEL_RESTORE",
                "destination": "/restored",
                "operator": "admin",
            },
            "status": {
                "startTime": "1776734685",
                "endTime": "1776735285",
                "progress": 100,
                "restoreStatus": "SUCCESS",
                "transferredSize": "204800",
                "processedSuccessCount": 150,
                "processedWarningCount": 1,
                "processedErrorCount": 0,
            },
        },
        "permission": {"canBackup": False, "canRestore": True, "canSelfService": False},
    }
    async with connected_session() as (session, m):
        m.get(RESTORE_RECENT_URL, payload={"activities": [fs_restore]})
        acts, total = await RestoreActivityCollection(session).list()
        await session.disconnect()

    act = acts[0]
    assert act.category == WorkloadCategory.MACHINE
    assert act.processed_success_count == 150
    assert act.processed_warning_count == 1
    assert act.processed_error_count == 0
    assert act.items_processed == 151


# ── keyword search ───────────────────────────────────────────────────────────


async def test_restore_list_keyword_appends_keyword_param() -> None:
    """RestoreActivityCollection.list(keyword=...) should add the keyword param to the API request."""
    async with connected_session() as (session, m):

        recent_with_kw = RESTORE_RECENT_URL + "&keyword=Corp+Share"
        m.get(recent_with_kw, payload={"activities": [SAMPLE_RESTORE_ACTIVITY_RAW]})
        acts, total = await RestoreActivityCollection(session).list(keyword="Corp Share")
        await session.disconnect()

    assert len(acts) == 1


# ── get_latest_by_workload_name ───────────────────────────────────────────────


_RESTORE_KEYWORD_RECENT_PAT = re.compile(r"(?=.*keyword=vm-web-01)(?=.*listMethod=RECENT).*")
_RESTORE_KEYWORD_HISTORY_PAT = re.compile(r"(?=.*keyword=vm-web-01)(?=.*listMethod=HISTORY).*")
_RESTORE_LOG_PAT = re.compile(r".*/api/v1/log/detail-log.*")


async def test_restore_get_latest_by_workload_name_returns_matching_activity() -> None:
    """RestoreActivityCollection.get_latest_by_workload_name() returns the matching activity for an exact name match."""
    async with connected_session() as (session, m):
        m.get(_RESTORE_KEYWORD_RECENT_PAT, payload={"activities": [make_restore_activity_raw()]})
        # get() enrichment: uid lookup via keyword-less RECENT list, then logs
        m.get(RESTORE_RECENT_URL, payload={"activities": [make_restore_activity_raw()], "total": 1})
        m.get(_RESTORE_LOG_PAT, payload={"detailLogs": []})
        result = await RestoreActivityCollection(session).get_latest_by_workload_name("vm-web-01")
        await session.disconnect()

    assert result.activity_id == "rst-uid-001"
    assert result.workload_name == "vm-web-01"
    assert result.status == RestoreActivityStatus.SUCCESS


async def test_restore_get_latest_by_workload_name_raises_when_no_exact_match() -> None:
    """restore get_latest_by_workload_name() should raise ResourceNotFoundError when there is no exact match."""
    other = make_restore_activity_raw(spec={"workloadName": "vm-app-01"})
    async with connected_session() as (session, m):
        m.get(_RESTORE_KEYWORD_RECENT_PAT, payload={"activities": [other]})
        m.get(_RESTORE_KEYWORD_HISTORY_PAT, payload={"activities": []})
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await RestoreActivityCollection(session).get_latest_by_workload_name("vm-web-01")
        await session.disconnect()

    assert_resource_error(exc_info, resource_type="Activity", resource_id="vm-web-01")


async def test_restore_get_latest_by_workload_name_falls_back_to_history() -> None:
    """get_latest_by_workload_name() should search HISTORY when RECENT returns no exact match."""
    async with connected_session() as (session, m):
        m.get(_RESTORE_KEYWORD_RECENT_PAT, payload={"activities": []})
        m.get(_RESTORE_KEYWORD_HISTORY_PAT, payload={"activities": [make_restore_activity_raw()]})
        m.get(RESTORE_RECENT_URL, payload={"activities": [make_restore_activity_raw()], "total": 1})
        m.get(_RESTORE_LOG_PAT, payload={"detailLogs": []})
        result = await RestoreActivityCollection(session).get_latest_by_workload_name("vm-web-01")
        await session.disconnect()

    # The match only exists in the HISTORY response, so returning it proves the fallback ran.
    assert result.activity_id == "rst-uid-001"
    assert result.workload_name == "vm-web-01"


# ════════════════════════════════════════════════════════════════════════════
# restore list — additional filter params (status, since/until)
# ════════════════════════════════════════════════════════════════════════════

async def test_restore_list_failed_status_sends_multiple_api_statuses() -> None:
    """RestoreActivityStatus.FAILED should expand to restoreStatus=DEVICE_MISSING&restoreStatus=FAILED&restoreStatus=MIGRATE_FAILED."""
    async with connected_session() as (session, m):

        # aiohttp sorts params alphabetically: DEVICE_MISSING < FAILED < MIGRATE_FAILED
        pat = re.compile(r".*restoreStatus=DEVICE_MISSING.*restoreStatus=FAILED.*restoreStatus=MIGRATE_FAILED")
        m.get(pat, payload=EMPTY_RESTORE_RESPONSE)
        result, total = await RestoreActivityCollection(session).list(status=[RestoreActivityStatus.FAILED])
        await session.disconnect()

    assert result == []


async def test_restore_list_since_sends_range_start_time() -> None:
    """list(since=dt) should append rangeStartTime to the restore query params."""
    from unittest.mock import AsyncMock, patch
    since = datetime(2026, 5, 1, tzinfo=UTC)
    session = make_session()
    collection = RestoreActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [], "total": 0})) as mock_get:
        await collection.list(since=since)
    params = dict(mock_get.call_args_list[0][1]["params"])
    assert params.get("rangeStartTime") == str(int(since.timestamp()))


async def test_restore_list_until_sends_range_end_time() -> None:
    """list(until=dt) should append rangeEndTime to the restore query params."""
    from unittest.mock import AsyncMock, patch
    until = datetime(2026, 5, 7, tzinfo=UTC)
    session = make_session()
    collection = RestoreActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [], "total": 0})) as mock_get:
        await collection.list(until=until)
    params = dict(mock_get.call_args_list[0][1]["params"])
    assert params.get("rangeEndTime") == str(int(until.timestamp()))


# ════════════════════════════════════════════════════════════════════════════
# restore activity parser — durationTime field
# ════════════════════════════════════════════════════════════════════════════

_RESTORE_WITH_DURATION_TIME = {
    "activity": {
        "uid": "rst-dur-001",
        "namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5",
        "spec": {
            "executionId": "99",
            "workload": {"uid": "wl-uid-001", "namespace": "ns-001"},
            "workloadType": "MACHINE_VM",
            "workloadName": "vm-web-01",
            "restoreType": "FULL_RESTORE",
            "destination": "vm-web-01-restored",
            "operator": "admin",
        },
        "status": {
            "startTime": "1777280000",
            "endTime": "0",
            "progress": 50,
            "restoreStatus": "RESTORING",
            "transferredSize": "0",
            "durationTime": "300",
        },
    },
    "permission": {"canBackup": False, "canRestore": True, "canSelfService": False},
}


async def test_restore_parser_uses_duration_time_when_present() -> None:
    """Restore parser should use durationTime directly when the field is present."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    collection = RestoreActivityCollection(session)
    with patch.object(session, "get", AsyncMock(return_value={"activities": [_RESTORE_WITH_DURATION_TIME], "total": 1})):
        acts, _ = await collection.list()
    assert acts[0].duration_seconds == 300
