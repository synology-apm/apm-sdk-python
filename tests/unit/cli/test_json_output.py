"""--page-all NDJSON streaming tests for key list commands.

The JSON wiring tests (--output json parseable, correct ID field) now live
in the per-command test files alongside the other command tests.  This file
retains only the Phase 6b --page-all NDJSON streaming tests that require
multi-page side_effect mocking.

Machine list --page-all NDJSON is covered in
tests/unit/cli/commands/test_machine_workloads.py.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    BackupActivityStatus,
    BackupServerType,
    ServerStatus,
    WorkloadCategory,
)
from synology_apm.sdk.models.activity import BackupActivity
from synology_apm.sdk.models.backup_server import BackupServer
from tests.unit.cli.conftest import invoke_cli

_SERVER = BackupServer(
    backup_server_id="bs-001",
    namespace="ns-001",
    server_type=BackupServerType.DP,
    name="apm-server-01",
    hostname="192.0.2.1",
    model="DP320",
    system_version="APM 1.2-71845",
    is_updating=False,
    status=ServerStatus.HEALTHY,
    serial="SN001",
    storage_total_bytes=10 * 1024 ** 4,
    storage_used_bytes=3 * 1024 ** 4,
    logical_backup_data_bytes=10 * 1024 ** 3,
    physical_backup_data_bytes=4 * 1024 ** 3,
)

_ACT = BackupActivity(
    activity_id="act-001",
    execution_id="exec-001",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_VM,
    workload_id="wl-001",
    workload_namespace="ns-001",
    workload_name="vm-web-01",
    plan_name="Daily Backup",
    started_at=datetime(2026, 4, 21, 8, 0, tzinfo=UTC),
    finished_at=None,
    duration_seconds=None,
    data_transferred_bytes=None,
    progress=0,
    status=BackupActivityStatus.SUCCESS,
)


# ── --page-all NDJSON streaming ───────────────────────────────────────────────
# machine list --page-all NDJSON is covered in test_machine_workloads.py.
# These tests cover activity backup list and infra server list.


def test_activity_backup_list_page_all_streams_ndjson(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """activity backup list --page-all --output json should stream one JSON object per activity."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    act2 = dataclasses.replace(_ACT, activity_id="act-002")
    mock_apm.activities.backup.list.side_effect = [
        ([_ACT], 2),
        ([act2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "activity", "backup", "list", "--limit", "1", "--page-all", "--output", "json",
    ])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.strip().splitlines() if ln]
    assert len(lines) == 2
    records = [json.loads(ln) for ln in lines]
    assert records[0]["activity_id"] == "act-001"
    assert records[1]["activity_id"] == "act-002"


def test_infra_server_list_page_all_streams_ndjson(mock_apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """infra server list --page-all --output json should stream one JSON object per server."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    server2 = dataclasses.replace(_SERVER, backup_server_id="bs-002", name="apm-server-02")
    mock_apm.backup_servers.list.side_effect = [
        ([_SERVER], 2),
        ([server2], 2),
    ]
    result = invoke_cli(mock_apm, [
        "infra", "server", "list", "--limit", "1", "--page-all", "--output", "json",
    ])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.strip().splitlines() if ln]
    assert len(lines) == 2
    records = [json.loads(ln) for ln in lines]
    assert records[0]["backup_server_id"] == "bs-001"
    assert records[1]["backup_server_id"] == "bs-002"
