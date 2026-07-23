"""Unit tests for pure parsing functions in examples/apm_import_export.py."""
from __future__ import annotations

import re
from datetime import time, timedelta
from typing import Any

import apm_import_export as ie
import pytest

from synology_apm.sdk import (
    BackupCopyConfig,
    BackupServer,
    GFSRetention,
    M365PlanCreateRequest,
    MachinePlanCreateRequest,
    ProtectionRetentionPolicy,
    RemoteStorage,
    RetentionType,
    ScheduleFrequency,
    WeekDay,
)
from tests.unit.examples._fixtures import make_backup_server, make_remote_storage

# ── _is_uuid ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123e4567-e89b-12d3-a456-426614174000", True),
        ("ABCDEF00-0000-0000-0000-000000000000", True),
        ("123E4567-e89b-12D3-A456-426614174000", True),
        ("not-a-uuid", False),
        ("", False),
        ("123e4567-e89b-12d3-a456", False),
    ],
    ids=["lowercase", "uppercase", "mixed-case", "not-a-uuid", "empty", "partial"],
)
def test_is_uuid(value: str, expected: bool) -> None:
    assert ie._is_uuid(value) is expected


# ── _dedupe_by_key ────────────────────────────────────────────────────────────


def test_dedupe_by_key_keeps_first_occurrence_drops_duplicate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = ie._dedupe_by_key(["a", "a", "b"], key_of=str, warn_of=lambda s: f"dup {s}")

    assert result == ["a", "b"]
    assert "dup a" in capsys.readouterr().err


def test_dedupe_by_key_no_duplicates_returns_all(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = ie._dedupe_by_key(["x", "y", "z"], key_of=str, warn_of=lambda s: f"dup {s}")

    assert result == ["x", "y", "z"]
    assert capsys.readouterr().err == ""


def test_dedupe_by_key_multiple_duplicates_one_warning_each(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = ie._dedupe_by_key(
        ["a", "b", "a", "b", "c"], key_of=str, warn_of=lambda s: f"warn:{s}"
    )

    assert result == ["a", "b", "c"]
    err = capsys.readouterr().err
    assert err.count("warn:a") == 1
    assert err.count("warn:b") == 1


# ── _filter_to_keys ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("items", "allowed_keys", "expected"),
    [
        (["a", "b", "c"], ["c", "a"], ["a", "c"]),
        (["a", "a", "b"], ["a", "b"], ["a", "b"]),
    ],
    ids=["retains-original-order", "consumes-each-key-once"],
)
def test_filter_to_keys_order_and_dedup(
    items: list[str], allowed_keys: list[str], expected: list[str]
) -> None:
    """Retained items keep their original order, and a duplicated item is kept only
    once per matching allowed key."""
    result = ie._filter_to_keys(items=items, key_of=str, allowed_keys=allowed_keys)
    assert result == expected


def test_filter_to_keys_excludes_items_not_in_allowed_keys() -> None:
    result = ie._filter_to_keys(
        items=["a", "b", "c"],
        key_of=str,
        allowed_keys=["b"],
    )
    assert result == ["b"]


def test_filter_to_keys_empty_allowed_keys_returns_empty() -> None:
    result = ie._filter_to_keys(items=["a", "b"], key_of=str, allowed_keys=[])
    assert result == []


def test_filter_to_keys_empty_items_returns_empty() -> None:
    result: list[str] = ie._filter_to_keys(items=[], key_of=str, allowed_keys=["a"])
    assert result == []


# ── _parse_retention ──────────────────────────────────────────────────────────


def test_parse_retention_keep_days_default() -> None:
    policy = ie._parse_retention({"type": "keep_days"})
    assert policy == ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=30)


def test_parse_retention_keep_days_explicit() -> None:
    policy = ie._parse_retention({"type": "keep_days", "days": 7})
    assert policy == ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=7)


def test_parse_retention_keep_versions_default() -> None:
    policy = ie._parse_retention({"type": "keep_versions"})
    assert policy == ProtectionRetentionPolicy(RetentionType.KEEP_VERSIONS, versions=5)


def test_parse_retention_keep_versions_explicit() -> None:
    policy = ie._parse_retention({"type": "keep_versions", "versions": 10})
    assert policy == ProtectionRetentionPolicy(RetentionType.KEEP_VERSIONS, versions=10)


def test_parse_retention_keep_advanced_gfs_defaults() -> None:
    policy = ie._parse_retention({"type": "keep_advanced"})
    expected_gfs = GFSRetention(
        daily_versions=7,
        weekly_versions=4,
        monthly_versions=12,
        yearly_versions=3,
    )
    assert policy.retention_type == RetentionType.KEEP_ADVANCED
    assert policy.gfs == expected_gfs
    assert policy.days is None
    assert policy.versions is None


def test_parse_retention_keep_advanced_with_days() -> None:
    policy = ie._parse_retention({"type": "keep_advanced", "days": 14})
    assert policy.retention_type == RetentionType.KEEP_ADVANCED
    assert policy.days == 14
    assert policy.versions is None
    expected_gfs = GFSRetention(
        daily_versions=7,
        weekly_versions=4,
        monthly_versions=12,
        yearly_versions=3,
    )
    assert policy.gfs == expected_gfs


def test_parse_retention_keep_advanced_custom_gfs() -> None:
    policy = ie._parse_retention({
        "type": "keep_advanced",
        "gfs": {
            "daily_versions": 14,
            "weekly_versions": 8,
            "monthly_versions": 6,
            "yearly_versions": 1,
        },
    })
    assert policy.retention_type == RetentionType.KEEP_ADVANCED
    assert policy.gfs is not None
    assert policy.gfs.daily_versions == 14
    assert policy.gfs.weekly_versions == 8
    assert policy.gfs.monthly_versions == 6
    assert policy.gfs.yearly_versions == 1


def test_parse_retention_keep_all() -> None:
    policy = ie._parse_retention({"type": "keep_all"})
    assert policy == ProtectionRetentionPolicy(RetentionType.KEEP_ALL)


# ── _parse_schedule ───────────────────────────────────────────────────────────


def test_parse_schedule_daily_with_start_time() -> None:
    schedule = ie._parse_schedule({
        "frequency": "daily",
        "start_time": "03:00",
        "weekdays": [],
    })
    assert schedule.frequency == ScheduleFrequency.DAILY
    assert schedule.start_time == time(3, 0)
    assert schedule.weekdays == ()


def test_parse_schedule_weekly_with_weekdays() -> None:
    schedule = ie._parse_schedule({
        "frequency": "weekly",
        "start_time": "22:30",
        "weekdays": ["monday", "friday"],
    })
    assert schedule.frequency == ScheduleFrequency.WEEKLY
    assert schedule.start_time == time(22, 30)
    assert WeekDay.MONDAY in schedule.weekdays
    assert WeekDay.FRIDAY in schedule.weekdays
    assert len(schedule.weekdays) == 2


def test_parse_schedule_manual_no_start_time() -> None:
    schedule = ie._parse_schedule({"frequency": "manual", "weekdays": []})
    assert schedule.frequency == ScheduleFrequency.MANUAL
    assert schedule.start_time is None
    assert schedule.weekdays == ()


def test_parse_schedule_after_backup() -> None:
    schedule = ie._parse_schedule({"frequency": "after_backup", "weekdays": []})
    assert schedule.frequency == ScheduleFrequency.AFTER_BACKUP


# ── _parse_task_schedule_dict ─────────────────────────────────────────────────


def test_parse_task_schedule_dict_empty_dict_returns_none_fields() -> None:
    ts = ie._parse_task_schedule_dict({})
    assert ts.time_schedule is None
    assert ts.event_trigger is None


def test_parse_task_schedule_dict_with_time_schedule() -> None:
    ts = ie._parse_task_schedule_dict({
        "time_schedule": {"frequency": "daily", "start_time": "02:00", "weekdays": []},
    })
    assert ts.time_schedule is not None
    assert ts.time_schedule.frequency == ScheduleFrequency.DAILY
    assert ts.time_schedule.start_time == time(2, 0)
    assert ts.event_trigger is None


def test_parse_task_schedule_dict_with_event_trigger() -> None:
    ts = ie._parse_task_schedule_dict({
        "event_trigger": {
            "on_sign_out": True,
            "on_lock": False,
            "on_startup": False,
            "min_interval": "2h",
        },
    })
    assert ts.event_trigger is not None
    assert ts.event_trigger.on_sign_out is True
    assert ts.event_trigger.on_lock is False
    assert ts.event_trigger.on_startup is False
    assert ts.event_trigger.min_interval == timedelta(hours=2)
    assert ts.time_schedule is None


# ── _build_ref_map ────────────────────────────────────────────────────────────


def _make_bs_list() -> list[BackupServer]:
    return [
        make_backup_server(
            name="apm-server-01",
            backup_server_id="123e4567-e89b-12d3-a456-426614174020",
        ),
        make_backup_server(
            name="apm-server-02",
            backup_server_id="123e4567-e89b-12d3-a456-426614174021",
        ),
    ]


def _id_of(b: BackupServer) -> str:
    return b.backup_server_id


def _name_of(b: BackupServer) -> str:
    return b.name


@pytest.mark.parametrize(
    "name_or_id",
    [
        "123e4567-e89b-12d3-a456-426614174020",
        "APM-SERVER-01",
    ],
    ids=["by-uuid", "by-name-case-insensitive"],
)
def test_build_ref_map_happy_path_resolves_entry(name_or_id: str) -> None:
    """An entry resolves to the matching resource whether name_or_id is its UUID or its
    name (matched case-insensitively)."""
    entries: list[dict[str, Any]] = [
        {"ref_key": "bs-ref", "name_or_id": name_or_id},
    ]
    ref_map, errors = ie._build_ref_map(
        "backup_servers", entries, _make_bs_list(), _id_of, _name_of
    )
    assert errors == []
    assert "bs-ref" in ref_map
    assert ref_map["bs-ref"].name == "apm-server-01"


def test_build_ref_map_multiple_entries() -> None:
    entries: list[dict[str, Any]] = [
        {"ref_key": "ref-a", "name_or_id": "apm-server-01"},
        {"ref_key": "ref-b", "name_or_id": "apm-server-02"},
    ]
    ref_map, errors = ie._build_ref_map(
        "backup_servers", entries, _make_bs_list(), _id_of, _name_of
    )
    assert errors == []
    assert ref_map["ref-a"].name == "apm-server-01"
    assert ref_map["ref-b"].name == "apm-server-02"


@pytest.mark.parametrize(
    ("name_or_id", "expected_substring"),
    [
        ("00000000-0000-0000-0000-000000000099", "UUID"),
        ("no-such-server", "not found"),
    ],
    ids=["uuid-not-found", "name-not-found"],
)
def test_build_ref_map_not_found_returns_error(
    name_or_id: str, expected_substring: str
) -> None:
    """A name_or_id that matches no resource, whether it looks like a UUID or a name,
    returns an empty ref_map and a single descriptive error."""
    entries: list[dict[str, Any]] = [
        {"ref_key": "bs-ref", "name_or_id": name_or_id},
    ]
    ref_map, errors = ie._build_ref_map(
        "backup_servers", entries, _make_bs_list(), _id_of, _name_of
    )
    assert ref_map == {}
    assert len(errors) == 1
    assert expected_substring in errors[0]


def test_build_ref_map_missing_ref_key_returns_error() -> None:
    entries: list[dict[str, Any]] = [
        {"name_or_id": "apm-server-01"},
    ]
    _, errors = ie._build_ref_map(
        "backup_servers", entries, _make_bs_list(), _id_of, _name_of
    )
    assert len(errors) == 1
    assert "missing 'ref_key'" in errors[0]


def test_build_ref_map_duplicate_ref_key_returns_error() -> None:
    entries: list[dict[str, Any]] = [
        {"ref_key": "bs-ref", "name_or_id": "apm-server-01"},
        {"ref_key": "bs-ref", "name_or_id": "apm-server-02"},
    ]
    _, errors = ie._build_ref_map(
        "backup_servers", entries, _make_bs_list(), _id_of, _name_of
    )
    assert len(errors) == 1
    assert "duplicate" in errors[0]


def test_build_ref_map_non_mapping_entry_returns_error() -> None:
    entries: list[Any] = ["not-a-dict"]
    _, errors = ie._build_ref_map(
        "backup_servers", entries, _make_bs_list(), _id_of, _name_of
    )
    assert len(errors) == 1
    assert "not a mapping" in errors[0]


def test_build_ref_map_mixed_valid_and_invalid_entries() -> None:
    entries: list[Any] = [
        {"ref_key": "ok-ref", "name_or_id": "apm-server-01"},
        {"name_or_id": "apm-server-02"},  # missing ref_key
    ]
    ref_map, errors = ie._build_ref_map(
        "backup_servers", entries, _make_bs_list(), _id_of, _name_of
    )
    assert "ok-ref" in ref_map
    assert len(errors) == 1


# ── _build_saas_tenant_ref_map ────────────────────────────────────────────────


def test_build_saas_tenant_ref_map_happy_path() -> None:
    entries: list[dict[str, Any]] = [
        {"ref_key": "tenant-ref", "tenant_id": "tenant-uuid-001"},
    ]
    ref_map, errors = ie._build_saas_tenant_ref_map(entries)
    assert errors == []
    assert ref_map == {"tenant-ref": "tenant-uuid-001"}


def test_build_saas_tenant_ref_map_multiple_entries() -> None:
    entries: list[dict[str, Any]] = [
        {"ref_key": "tenant-a", "tenant_id": "uuid-001"},
        {"ref_key": "tenant-b", "tenant_id": "uuid-002"},
    ]
    ref_map, errors = ie._build_saas_tenant_ref_map(entries)
    assert errors == []
    assert ref_map["tenant-a"] == "uuid-001"
    assert ref_map["tenant-b"] == "uuid-002"


@pytest.mark.parametrize(
    ("entries", "expected_errors"),
    [
        (
            [{"tenant_id": "tenant-uuid-001"}],
            ["saas_tenants entry missing 'ref_key'"],
        ),
        (
            [{"ref_key": "tenant-ref"}],
            ["saas_tenants ref_key='tenant-ref' missing 'tenant_id'"],
        ),
    ],
    ids=["missing-ref-key", "missing-tenant-id"],
)
def test_build_saas_tenant_ref_map_missing_field_returns_error(
    entries: list[dict[str, Any]], expected_errors: list[str]
) -> None:
    """An entry missing ref_key, or missing tenant_id, returns a single descriptive error."""
    _, errors = ie._build_saas_tenant_ref_map(entries)
    assert errors == expected_errors


def test_build_saas_tenant_ref_map_duplicate_ref_key_returns_error() -> None:
    entries: list[dict[str, Any]] = [
        {"ref_key": "tenant-ref", "tenant_id": "uuid-001"},
        {"ref_key": "tenant-ref", "tenant_id": "uuid-002"},
    ]
    _, errors = ie._build_saas_tenant_ref_map(entries)
    assert len(errors) == 1
    assert "duplicate" in errors[0]


def test_build_saas_tenant_ref_map_empty_list() -> None:
    ref_map, errors = ie._build_saas_tenant_ref_map([])
    assert ref_map == {}
    assert errors == []


# ── _resolve_backup_copy ──────────────────────────────────────────────────────


def _make_bc_dict(
    destination_type: str = "appliance",
    destination_ref: str = "bs-ref",
) -> dict[str, Any]:
    return {
        "destination_type": destination_type,
        "destination_ref": destination_ref,
        "retention": {"type": "keep_all"},
        "schedule": {"frequency": "daily", "start_time": "03:00", "weekdays": []},
    }


def test_resolve_backup_copy_appliance_destination() -> None:
    bs = make_backup_server(name="apm-server-01")
    servers_by_ref: dict[str, BackupServer] = {"bs-ref": bs}
    storages_by_ref: dict[str, RemoteStorage] = {}

    result = ie._resolve_backup_copy(
        _make_bc_dict("appliance", "bs-ref"),
        servers_by_ref,
        storages_by_ref,
        "Test Plan",
    )

    assert isinstance(result, BackupCopyConfig)
    assert result.destination is bs
    assert result.retention == ProtectionRetentionPolicy(RetentionType.KEEP_ALL)
    assert result.schedule.frequency == ScheduleFrequency.DAILY


def test_resolve_backup_copy_remote_storage_destination() -> None:
    rs = make_remote_storage(name="DSM-Storage")
    servers_by_ref: dict[str, BackupServer] = {}
    storages_by_ref: dict[str, RemoteStorage] = {"rs-ref": rs}

    result = ie._resolve_backup_copy(
        _make_bc_dict("remote_storage", "rs-ref"),
        servers_by_ref,
        storages_by_ref,
        "Test Plan",
    )

    assert isinstance(result, BackupCopyConfig)
    assert result.destination is rs
    assert result.retention == ProtectionRetentionPolicy(RetentionType.KEEP_ALL)


@pytest.mark.parametrize(
    "destination_type",
    ["appliance", "remote_storage"],
    ids=["appliance-ref-not-found", "remote-storage-ref-not-found"],
)
def test_resolve_backup_copy_ref_not_found_raises_value_error(
    destination_type: str,
) -> None:
    """A backup_copy destination_ref that resolves to neither an appliance nor a
    remote storage entry raises ValueError, for either destination type."""
    servers_by_ref: dict[str, BackupServer] = {}
    storages_by_ref: dict[str, RemoteStorage] = {}

    with pytest.raises(
        ValueError,
        match=re.escape("backup_copy destination not found for plan 'Test Plan'"),
    ):
        ie._resolve_backup_copy(
            _make_bc_dict(destination_type, "missing-ref"),
            servers_by_ref,
            storages_by_ref,
            "Test Plan",
        )


def test_resolve_backup_copy_unknown_destination_type_raises_value_error() -> None:
    with pytest.raises(
        ValueError,
        match="unknown backup_copy destination_type",
    ):
        ie._resolve_backup_copy(
            _make_bc_dict("unsupported_type", "some-ref"),
            {},
            {},
            "Test Plan",
        )


# ── _parse_fs_selectors ───────────────────────────────────────────────────────


def test_parse_fs_selectors_empty_list_returns_root_selector() -> None:
    result = ie._parse_fs_selectors([])
    assert len(result) == 1
    assert result[0].path == ""


def test_parse_fs_selectors_single_entry() -> None:
    result = ie._parse_fs_selectors([{"path": "/data", "excluded_paths": []}])
    assert len(result) == 1
    assert result[0].path == "/data"


def test_parse_fs_selectors_with_excluded_paths() -> None:
    result = ie._parse_fs_selectors([
        {"path": "/home", "excluded_paths": ["/home/tmp", "/home/.cache"]},
    ])
    assert len(result) == 1
    assert result[0].path == "/home"
    assert "/home/tmp" in result[0].excluded_paths
    assert "/home/.cache" in result[0].excluded_paths


def test_parse_fs_selectors_multiple_entries() -> None:
    result = ie._parse_fs_selectors([
        {"path": "/data"},
        {"path": "/config"},
    ])
    assert len(result) == 2
    assert result[0].path == "/data"
    assert result[1].path == "/config"


# ── _parse_protection_request ─────────────────────────────────────────────────


def test_parse_protection_request_unknown_type_raises_value_error() -> None:
    d: dict[str, Any] = {
        "name_or_id": "Test Plan",
        "type": "UNKNOWN",
        "retention": {"type": "keep_all"},
        "schedule": {"frequency": "daily", "weekdays": []},
    }
    with pytest.raises(ValueError, match="Unknown protection plan type"):
        ie._parse_protection_request(d, {}, {})


def test_parse_protection_request_machine_plan_minimal() -> None:
    d: dict[str, Any] = {
        "name_or_id": "Daily Backup",
        "type": "machine",
        "retention": {"type": "keep_days", "days": 14},
        "schedule": {"frequency": "daily", "start_time": "02:00", "weekdays": []},
    }
    result = ie._parse_protection_request(d, {}, {})
    assert isinstance(result, MachinePlanCreateRequest)
    assert result.name == "Daily Backup"
    assert result.retention.retention_type == RetentionType.KEEP_DAYS
    assert result.retention.days == 14
    assert result.schedule.frequency == ScheduleFrequency.DAILY


def test_parse_protection_request_m365_plan() -> None:
    d: dict[str, Any] = {
        "name_or_id": "M365 Backup",
        "type": "m365",
        "retention": {"type": "keep_versions", "versions": 10},
        "schedule": {"frequency": "weekly", "start_time": "03:00", "weekdays": ["sunday"]},
    }
    result = ie._parse_protection_request(d, {}, {})
    assert isinstance(result, M365PlanCreateRequest)
    assert result.name == "M365 Backup"
    assert result.retention.retention_type == RetentionType.KEEP_VERSIONS
    assert result.retention.versions == 10


def test_parse_protection_request_machine_type_case_insensitive() -> None:
    d: dict[str, Any] = {
        "name_or_id": "Daily Backup",
        "type": "Machine",
        "retention": {"type": "keep_all"},
        "schedule": {"frequency": "manual", "weekdays": []},
    }
    result = ie._parse_protection_request(d, {}, {})
    assert isinstance(result, MachinePlanCreateRequest)


def test_parse_protection_request_with_backup_copy() -> None:
    bs = make_backup_server(name="apm-server-01")
    servers_by_ref: dict[str, BackupServer] = {"bs-ref": bs}

    d: dict[str, Any] = {
        "name_or_id": "Daily Backup",
        "type": "machine",
        "retention": {"type": "keep_all"},
        "schedule": {"frequency": "daily", "start_time": "01:00", "weekdays": []},
        "backup_copy": {
            "destination_type": "appliance",
            "destination_ref": "bs-ref",
            "retention": {"type": "keep_days", "days": 7},
            "schedule": {"frequency": "after_backup", "weekdays": []},
        },
    }
    result = ie._parse_protection_request(d, servers_by_ref, {})
    assert isinstance(result, MachinePlanCreateRequest)
    assert result.backup_copy is not None
    assert result.backup_copy.destination is bs


# ── Deferred-RS mechanism ─────────────────────────────────────────────────────


def _make_plan_with_rs_backup_copy(dest_ref: str) -> dict[str, Any]:
    return {
        "name_or_id": "Daily Backup",
        "type": "machine",
        "retention": {"type": "keep_days", "days": 30},
        "schedule": {"frequency": "daily", "start_time": "02:00", "weekdays": []},
        "backup_copy": {
            "destination_type": "remote_storage",
            "destination_ref": dest_ref,
            "retention": {"type": "keep_days", "days": 7},
            "schedule": {"frequency": "after_backup", "start_time": None, "weekdays": []},
        },
    }


def test_parse_all_entries_pending_rs_ref_defers_without_error() -> None:
    """A plan whose RS backup-copy ref is in rs_pending_refs gets request=None, parse_error=None."""
    data: dict[str, Any] = {
        "protection_plans": [_make_plan_with_rs_backup_copy("pending-rs")]
    }

    entries = ie._parse_all_entries(data, {}, {}, rs_pending_refs={"pending-rs"})

    assert len(entries) == 1
    entry = entries[0]
    assert entry.request is None
    assert entry.parse_error is None
    assert entry.kind == "protection-plan"


def test_parse_all_entries_non_pending_rs_ref_records_parse_error() -> None:
    """A plan whose RS ref is NOT pending (just missing) gets a parse_error, not a deferral."""
    data: dict[str, Any] = {
        "protection_plans": [_make_plan_with_rs_backup_copy("missing-rs")]
    }

    entries = ie._parse_all_entries(data, {}, {}, rs_pending_refs=set())

    assert len(entries) == 1
    assert entries[0].request is None
    assert entries[0].parse_error is not None
