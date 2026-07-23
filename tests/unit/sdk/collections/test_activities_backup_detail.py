"""Unit tests for BackupActivityCollection.get(), get_by_version(), and cancel()."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
from aiointercept import aiointercept

from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.collections.activities import BackupActivityCollection
from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    BackupScope,
    LogLevel,
    VersionStatus,
    WorkloadCategory,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.activity import BackupActivity
from synology_apm.sdk.models.version import WorkloadVersion
from tests.unit.sdk.conftest import (
    BASE_URL,
    LOGIN_OK,
    LOGIN_URL,
    connected_session,
    make_session,
)

RECENT_URL = (
    f"{BASE_URL}/api/v2/activity/backup/activities"
    "?listMethod=RECENT&offset=0&limit=100"
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


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_raises_resource_not_found() -> None:
    """get() should raise ResourceNotFoundError when the activity is not found."""
    session = make_session()
    empty_list: dict[str, Any] = {"activities": []}
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        # get() calls list() twice (RECENT + HISTORY) internally
        m.get(re.compile(r".*/api/v2/activity/backup/activities.*listMethod=RECENT.*"), payload=empty_list)
        m.get(re.compile(r".*/api/v2/activity/backup/activities.*listMethod=HISTORY.*"), payload=empty_list)

        collection = BackupActivityCollection(session)
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await collection.get("no-such-uid")
        await session.disconnect()

    assert exc_info.value.resource_type == "Activity"
    assert exc_info.value.resource_id == "no-such-uid"


async def test_get_returns_enriched_activity_with_detail_and_logs() -> None:
    """get() on success should call the detail API and log API, returning an Activity with enriched fields."""
    session = make_session()

    detail_url = (
        f"{BASE_URL}/api/v1/activity/backup/activity"
        "?executionId=ABE_1"
        "&workloadUid=fbf93425-d9e7-1c70-f4b2-231d7fc7b116"
        "&namespace=9053e422-4154-4abc-b03a-6e3d8e17b2d5"
    )
    log_url = (
        f"{BASE_URL}/api/v1/log/detail-log"
        "?limit=1001&offset=0&backupActivityUid=act-uid-001"
    )
    detail_payload = {
        "activity": {
            "status": {"changeDataSize": "512000", "dedupedDataSize": "256000"},
            "spec": {"machineInfo": {"backupScope": "ENTIRE_DEVICE"}},
        }
    }
    log_payload = {
        "detailLogs": [
            {"timestamp": "1776734685", "level": "LEVEL_INFORMATION", "description": "Backup started"},
            {"timestamp": "1776735285", "level": "LEVEL_WARNING", "description": "Low disk space"},
        ]
    }

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        m.get(detail_url, payload=detail_payload)
        m.get(log_url, payload=log_payload)

        collection = BackupActivityCollection(session)
        act = await collection.get("act-uid-001")
        await session.disconnect()

    assert act.activity_id == "act-uid-001"
    assert act.data_change_bytes == 512000
    assert act.data_deduped_bytes == 256000
    assert act.backup_scope == BackupScope.ENTIRE_DEVICE
    assert act.log_entries is not None
    assert len(act.log_entries) == 2
    assert act.log_entries[0].level == LogLevel.INFO
    assert act.log_entries[0].message == "Backup started"
    assert act.log_entries[1].level == LogLevel.WARNING


async def test_get_returns_none_optional_fields_when_detail_is_empty() -> None:
    """When the detail API returns an empty body, optional fields should be None."""
    session = make_session()

    detail_url = re.compile(r".*/api/v1/activity/backup/activity.*")
    log_url = re.compile(r".*/api/v1/log/detail-log.*")

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        m.get(detail_url, payload={"activity": {}})
        m.get(log_url, payload={"detailLogs": []})

        collection = BackupActivityCollection(session)
        act = await collection.get("act-uid-001")
        await session.disconnect()

    assert act.data_change_bytes is None
    assert act.data_deduped_bytes is None
    assert act.backup_scope is None
    assert act.log_entries == ()


async def test_get_survives_null_activity_and_detail_logs() -> None:
    """The detail API returning {"activity": null} and the log API returning
    {"detailLogs": null} (both JSON null, key present -- distinct from an absent key or an
    empty dict/list) must not crash get(); all derived fields fall back to their safe defaults."""
    session = make_session()

    detail_url = re.compile(r".*/api/v1/activity/backup/activity.*")
    log_url = re.compile(r".*/api/v1/log/detail-log.*")

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        m.get(detail_url, payload={"activity": None})
        m.get(log_url, payload={"detailLogs": None})

        collection = BackupActivityCollection(session)
        act = await collection.get("act-uid-001")
        await session.disconnect()

    assert act.data_change_bytes is None
    assert act.data_deduped_bytes is None
    assert act.backup_scope is None
    assert act.log_entries == ()


# ── cancel() ──────────────────────────────────────────────────────────────


def _make_activity(activity_id: str, namespace: str, category: WorkloadCategory) -> BackupActivity:
    wt = ActivityWorkloadType.M365 if category == WorkloadCategory.M365 else ActivityWorkloadType.MACHINE_PC
    return BackupActivity(
        activity_id=activity_id,
        execution_id="EX_1",
        namespace=namespace,
        category=category,
        workload_type=wt,
        workload_id="wl-uid",
        workload_namespace="",
        workload_name="Test Workload",
        plan_name="Plan",
        status=BackupActivityStatus.BACKING_UP,
        started_at=datetime(2026, 4, 27, 10, 0, tzinfo=UTC),
        finished_at=None,
        duration_seconds=None,
        data_transferred_bytes=None,
        progress=50,
    )


async def test_cancel_m365_uses_m365_pairs() -> None:
    """M365 activity cancel() should put the pair in m365NsUidPairs; deviceNsUidPairs should be empty."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    act = _make_activity("f919fdce-0cbc-4489-ba3e-cf9716b94379", "9053e422-4154-4abc-b03a-6e3d8e17b2d5", WorkloadCategory.M365)

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        collection = BackupActivityCollection(session)
        await collection.cancel(act)
        body = mock_post.call_args[1]["json"]

    await session.disconnect()

    assert body["m365NsUidPairs"] == [{"namespace": "9053e422-4154-4abc-b03a-6e3d8e17b2d5", "uid": "f919fdce-0cbc-4489-ba3e-cf9716b94379"}]
    assert body["deviceNsUidPairs"] == []
    assert body["gwNsUidPairs"] == []


async def test_cancel_machine_body_format() -> None:
    """Machine activity cancel() body should use deviceNsUidPairs (verified via direct mock)."""
    from unittest.mock import AsyncMock, patch
    session = make_session()
    act = _make_activity("act-uid-002", "ns-abc", WorkloadCategory.MACHINE)

    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL, payload=LOGIN_OK)
        await session.connect()

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        collection = BackupActivityCollection(session)
        await collection.cancel(act)
        body = mock_post.call_args[1]["json"]

    await session.disconnect()

    assert body["deviceNsUidPairs"] == [{"namespace": "ns-abc", "uid": "act-uid-002"}]
    assert body["m365NsUidPairs"] == []
    assert body["gwNsUidPairs"] == []


# ── get() log-entry parsing ──────────────────────────────────────────────


async def _get_activity_with_logs(detail_logs: list[dict[str, Any]]) -> BackupActivity:
    """Run get() with the standard sample activity and the given detail-log payload."""
    async with connected_session() as (session, m):
        m.get(RECENT_URL, payload={"activities": [SAMPLE_ACTIVITY_RAW], "total": 1})
        m.get(re.compile(r".*/api/v1/activity/backup/activity.*"), payload={"activity": {}})
        m.get(re.compile(r".*/api/v1/log/detail-log.*"), payload={"detailLogs": detail_logs})
        act = await BackupActivityCollection(session).get("act-uid-001")
        await session.disconnect()
    return act


async def test_get_log_entries_parses_levels_and_messages() -> None:
    act = await _get_activity_with_logs([
        {"timestamp": "1700000000", "level": "LEVEL_INFORMATION", "description": "info"},
        {"timestamp": "1700001000", "level": "LEVEL_WARNING", "description": "warn"},
        {"timestamp": "1700002000", "level": "LEVEL_ERROR", "description": "err"},
    ])
    assert act.log_entries is not None
    assert [e.level for e in act.log_entries] == [LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR]
    assert [e.message for e in act.log_entries] == ["info", "warn", "err"]
    assert act.log_entries[0].timestamp == datetime.fromtimestamp(1700000000, tz=UTC)


async def test_get_log_entries_unknown_level_defaults_to_info() -> None:
    act = await _get_activity_with_logs(
        [{"timestamp": "1700000000", "level": "UNKNOWN_LEVEL", "description": "msg"}]
    )
    assert act.log_entries is not None
    assert act.log_entries[0].level == LogLevel.INFO
    assert act.log_entries[0].message == "msg"


async def test_get_log_entries_zero_timestamp_uses_now() -> None:
    act = await _get_activity_with_logs(
        [{"timestamp": "0", "level": "LEVEL_INFORMATION", "description": "msg"}]
    )
    assert act.log_entries is not None
    assert abs((act.log_entries[0].timestamp - datetime.now(UTC)).total_seconds()) < 5


# ── x-syno-tunnel-route header ────────────────────────────────────────────


async def test_backup_get_sends_tunnel_route_header() -> None:
    """BackupActivityCollection.get() should set x-syno-tunnel-route header to activity.namespace when calling the log API."""
    from unittest.mock import AsyncMock

    session = AsyncMock(spec=WebAPISession)
    session.get.side_effect = [
        {"activities": [{"activity": {
            "uid": "act-uid-001",
            "namespace": "ns-backup-server-001",
            "spec": {"workload": {"uid": "wl-uid"}, "workloadType": "PC"},
            "status": {"executionId": "ABE_1", "backupStatus": "SUCCESS",
                       "startTime": "1776734685", "endTime": "0",
                       "durationTime": "0", "transferredDataSize": "0", "progress": 100},
        }}], "total": 1},
        {"activity": {}},
        {"detailLogs": []},
    ]

    collection = BackupActivityCollection(session)
    await collection.get("act-uid-001")

    log_call = next(c for c in session.get.call_args_list if "detail-log" in c.args[0])
    assert log_call.kwargs.get("headers") == {"x-syno-tunnel-route": "ns-backup-server-001"}


async def test_backup_get_by_version_sends_tunnel_route_header() -> None:
    """get_by_version() should set x-syno-tunnel-route header to version.namespace when calling the log API."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    session = AsyncMock(spec=WebAPISession)
    session.get.side_effect = [
        {"activity": {"uid": "act-uid-002", "spec": {}, "status": {}}},
        {"detailLogs": []},
    ]
    version = WorkloadVersion(
        version_id="ver-001",
        workload_id="wl-uid",
        namespace="ns-backup-server-002",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="EX_1",
        locked=False,
        changed_size_bytes=0,
    )

    collection = BackupActivityCollection(session)
    await collection.get_by_version(version)

    log_call = next(c for c in session.get.call_args_list if "detail-log" in c.args[0])
    assert log_call.kwargs.get("headers") == {"x-syno-tunnel-route": "ns-backup-server-002"}


async def test_backup_get_by_version_survives_null_detail_uid() -> None:
    """detail.uid present as JSON null must fall back to "" for the log-fetch's
    backupActivityUid param, not crash by passing None to the log endpoint."""
    from unittest.mock import AsyncMock

    session = AsyncMock(spec=WebAPISession)
    session.get.side_effect = [
        {"activity": {"uid": None, "spec": {}, "status": {}}},
        {"detailLogs": []},
    ]
    version = WorkloadVersion(
        version_id="ver-001",
        workload_id="wl-uid",
        namespace="ns-001",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="EX_1",
        locked=False,
        changed_size_bytes=0,
    )

    collection = BackupActivityCollection(session)
    act = await collection.get_by_version(version)

    log_call = next(c for c in session.get.call_args_list if "detail-log" in c.args[0])
    assert log_call.kwargs["params"]["backupActivityUid"] == ""
    assert act.log_entries == ()
