"""Unit tests for serialization helper functions in examples/apm_import_export.py."""
from __future__ import annotations

import csv
import io
from datetime import time
from pathlib import Path
from typing import Any

import apm_import_export as ie
import pytest
import yaml

from synology_apm.sdk import (
    BackupCopyPolicy,
    DbActionOnError,
    GFSRetention,
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
    MachineBackupWindow,
    MachineDbConfig,
    MachineOsType,
    MachineTaskConfig,
    MachineTaskScope,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorageType,
    RetentionType,
    ScheduleFrequency,
    WeekDay,
    WorkloadCategory,
)
from tests.unit.examples._fixtures import (
    make_backup_server,
    make_file_server_config,
    make_location_info,
    make_machine_workload,
    make_protection_plan,
    make_remote_storage,
    make_saas_tenant,
)

# ── _yaml_scalar ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "s",
    [
        "simple",
        "key: value",
        "yes",
        "true",
        "1.5",
        "contains: colon and spaces",
        "",
    ],
    ids=["simple", "colon", "yes", "true", "float", "colon-spaces", "empty"],
)
def test_yaml_scalar_round_trips(s: str) -> None:
    """yaml.safe_load(_yaml_scalar(s)) == s for strings that would be mis-parsed as bare scalars."""
    assert yaml.safe_load(ie._yaml_scalar(s)) == s


# ── _ser_time ─────────────────────────────────────────────────────────────────


def test_ser_time_formats_as_hhmm() -> None:
    """_ser_time formats a time object as HH:MM."""
    assert ie._ser_time(time(14, 30)) == "14:30"


def test_ser_time_none_returns_none() -> None:
    """_ser_time returns None when given None."""
    assert ie._ser_time(None) is None


# ── _ser_backup_server ────────────────────────────────────────────────────────


def test_ser_backup_server_with_description() -> None:
    """The entry carries ref_key + display name; the comment lists id, name, hostname, type, description."""
    bs = make_backup_server(description="Primary appliance")

    result = ie._ser_backup_server(bs, "server-1")

    assert result == {
        "ref_key": "server-1",
        "name_or_id": "apm-server-01",
        "_comment": (
            "name_or_id: 123e4567-e89b-12d3-a456-426614174020 | "
            "name: apm-server-01 | hostname: 192.0.2.1 | type: dp | "
            "description: Primary appliance"
        ),
    }


def test_ser_backup_server_without_description_omits_description_part() -> None:
    """An empty description does not add a 'description:' segment to the comment."""
    bs = make_backup_server(description="")

    result = ie._ser_backup_server(bs, "server-2")

    assert result["_comment"] == (
        "name_or_id: 123e4567-e89b-12d3-a456-426614174020 | "
        "name: apm-server-01 | hostname: 192.0.2.1 | type: dp"
    )


# ── _ser_saas_tenant ──────────────────────────────────────────────────────────


def test_ser_saas_tenant_entry_and_comment() -> None:
    """The entry carries ref_key + tenant_id; the comment lists name and email."""
    tenant = make_saas_tenant()

    result = ie._ser_saas_tenant(tenant, "tenant-1")

    assert result == {
        "ref_key": "tenant-1",
        "tenant_id": "123e4567-e89b-12d3-a456-426614174060",
        "_comment": "name: Contoso | email: admin@contoso.com",
    }


# ── _ser_remote_storage — three branches ──────────────────────────────────────


def test_ser_remote_storage_not_importable_type_full_entry() -> None:
    """Non-importable type (AZURE_BLOB): full entry with endpoint, trust_self_signed=False,
    and an import-not-supported comment."""
    rs = make_remote_storage(storage_type=RemoteStorageType.AZURE_BLOB)

    result = ie._ser_remote_storage(rs, "ref-1")

    assert result == {
        "ref_key": "ref-1",
        "name_or_id": "tiering-remote",
        "endpoint": "https://s3.example.com:443",
        "storage_type": "azure_blob",
        "encryption_enabled": False,
        "vault_name": "my-bucket",
        "trust_self_signed": False,
        "_comment": (
            "name_or_id: 123e4567-e89b-12d3-a456-426614174030 | "
            "name: tiering-remote | endpoint: https://s3.example.com:443 | "
            "type: azure_blob | import: not supported for this type"
        ),
    }


def test_ser_remote_storage_importable_endpoint_free_full_entry() -> None:
    """Importable endpoint-free type (AMAZON_S3): no endpoint or trust_self_signed field;
    the comment notes the native endpoint-free import path."""
    rs = make_remote_storage(storage_type=RemoteStorageType.AMAZON_S3, encryption_enabled=True)

    result = ie._ser_remote_storage(rs, "ref-2")

    assert result == {
        "ref_key": "ref-2",
        "name_or_id": "tiering-remote",
        "storage_type": "amazon_s3",
        "encryption_enabled": True,
        "vault_name": "my-bucket",
        "_comment": (
            "name_or_id: 123e4567-e89b-12d3-a456-426614174030 | "
            "name: tiering-remote | endpoint: https://s3.example.com:443 | "
            "type: amazon_s3 | import: native endpoint-free path "
            "(no endpoint or trust_self_signed needed)"
        ),
    }


def test_ser_remote_storage_importable_endpoint_required_full_entry() -> None:
    """Importable endpoint-required type (S3_COMPATIBLE): endpoint present,
    trust_self_signed=True, plain informational comment."""
    rs = make_remote_storage(storage_type=RemoteStorageType.S3_COMPATIBLE)

    result = ie._ser_remote_storage(rs, "ref-3")

    assert result == {
        "ref_key": "ref-3",
        "name_or_id": "tiering-remote",
        "endpoint": "https://s3.example.com:443",
        "storage_type": "s3_compatible",
        "encryption_enabled": False,
        "vault_name": "my-bucket",
        "trust_self_signed": True,
        "_comment": (
            "name_or_id: 123e4567-e89b-12d3-a456-426614174030 | "
            "name: tiering-remote | endpoint: https://s3.example.com:443 | "
            "type: s3_compatible"
        ),
    }


# ── _ser_retention ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("retention", "expected"),
    [
        (
            ProtectionRetentionPolicy(RetentionType.KEEP_ALL),
            {"type": "keep_all"},
        ),
        (
            ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=7),
            {"type": "keep_days", "days": 7},
        ),
        (
            ProtectionRetentionPolicy(RetentionType.KEEP_VERSIONS, versions=10),
            {"type": "keep_versions", "versions": 10},
        ),
        (
            ProtectionRetentionPolicy(
                RetentionType.KEEP_ADVANCED,
                days=30,
                versions=None,
                gfs=GFSRetention(
                    daily_versions=7,
                    weekly_versions=4,
                    monthly_versions=12,
                    yearly_versions=3,
                ),
            ),
            {
                "type": "keep_advanced",
                "days": 30,
                "versions": None,
                "gfs": {
                    "daily_versions": 7,
                    "weekly_versions": 4,
                    "monthly_versions": 12,
                    "yearly_versions": 3,
                },
            },
        ),
    ],
    ids=["keep_all", "keep_days", "keep_versions", "keep_advanced"],
)
def test_ser_retention(
    retention: ProtectionRetentionPolicy, expected: dict[str, Any]
) -> None:
    """Each retention type serializes to exactly its documented key set and values."""
    assert ie._ser_retention(retention) == expected


# ── _write_commented_section ──────────────────────────────────────────────────


def test_write_commented_section_yaml_round_trip() -> None:
    """Entry data round-trips through YAML; _comment is a raw comment line, not a parsed field."""
    fh = io.StringIO()
    entries: list[dict[str, Any]] = [{"name": "test", "value": 42, "_comment": "a comment"}]

    ie._write_commented_section(fh, "test_section", entries)
    raw = fh.getvalue()

    parsed: dict[str, Any] = yaml.safe_load(raw)
    assert "test_section" in parsed
    entry = parsed["test_section"][0]
    assert entry["name"] == "test"
    assert entry["value"] == 42
    assert "_comment" not in entry
    assert "  # a comment" in raw.splitlines()


def test_write_commented_section_empty_list_writes_empty_section() -> None:
    """An empty entries list writes the section as an inline empty-list scalar."""
    fh = io.StringIO()

    ie._write_commented_section(fh, "my_section", [])

    assert fh.getvalue() == "my_section: []\n"


# ── _write_ref_section ────────────────────────────────────────────────────────


def test_write_ref_section_includes_ref_key_name_and_comment_line() -> None:
    """ref_key and name_or_id appear in the output; _comment is written as a '  # ...' comment line."""
    fh = io.StringIO()
    entries: list[dict[str, Any]] = [
        {"ref_key": "server-1", "name_or_id": "apm-server-01", "_comment": "some info"}
    ]

    ie._write_ref_section(fh, "backup_servers", entries)
    raw = fh.getvalue()

    assert "server-1" in raw
    assert "apm-server-01" in raw
    assert "  # some info" in raw


# ── _write_saas_tenants_section ───────────────────────────────────────────────


def test_write_saas_tenants_section_empty_list_writes_empty_section() -> None:
    """An empty entries list writes the section as an inline empty-list scalar."""
    fh = io.StringIO()

    ie._write_saas_tenants_section(fh, [])

    assert fh.getvalue() == "saas_tenants: []\n"


def test_write_saas_tenants_section_entries_round_trip_with_comment() -> None:
    """Entries parse back with ref_key + tenant_id; _comment becomes a raw comment line."""
    fh = io.StringIO()
    entries: list[dict[str, Any]] = [
        {
            "ref_key": "tenant-1",
            "tenant_id": "123e4567-e89b-12d3-a456-426614174060",
            "_comment": "name: Contoso | email: admin@contoso.com",
        }
    ]

    ie._write_saas_tenants_section(fh, entries)
    raw = fh.getvalue()

    assert yaml.safe_load(raw) == {
        "saas_tenants": [
            {"ref_key": "tenant-1", "tenant_id": "123e4567-e89b-12d3-a456-426614174060"}
        ]
    }
    assert "  # name: Contoso | email: admin@contoso.com" in raw.splitlines()


# ── _ser_protection_plan fallbacks ────────────────────────────────────────────


def test_ser_protection_plan_policy_none_uses_keep_all_and_manual_schedule() -> None:
    """When policy=None, serialized retention is keep_all and schedule is manual with no weekdays."""
    plan = ProtectionPlan(
        plan_id="123e4567-e89b-12d3-a456-426614174060",
        name="No Policy Plan",
        category=WorkloadCategory.MACHINE,
        policy=None,
    )

    result = ie._ser_protection_plan(plan, {}, {}, "ref-np")

    assert result["retention"] == {"type": "keep_all"}
    assert result["schedule"]["frequency"] == "manual"
    assert result["schedule"]["start_time"] is None
    assert result["schedule"]["weekdays"] == []


def test_ser_protection_plan_unknown_rs_ref_falls_back_to_raw_name() -> None:
    """When backup-copy RS destination is absent from rs_ref_keys, raw location name is used."""
    loc = make_location_info(
        is_remote_storage=True,
        identifier="rs-id-001",
        name="DSM-Storage",
        endpoint="192.0.2.20:8444",
    )
    bcp = BackupCopyPolicy(
        destination=loc,
        retention=ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=7),
        schedule=ProtectionSchedule(ScheduleFrequency.AFTER_BACKUP, start_time=None),
    )
    plan = ProtectionPlan(
        plan_id="123e4567-e89b-12d3-a456-426614174061",
        name="Plan With Unknown RS",
        category=WorkloadCategory.MACHINE,
        backup_copy_policy=bcp,
    )

    # Pass an empty rs_ref_keys so "DSM-Storage" is not in the mapping
    result = ie._ser_protection_plan(plan, {}, {}, "ref-plan")

    assert result["backup_copy"] is not None
    assert result["backup_copy"]["destination_ref"] == "DSM-Storage"


def test_ser_protection_plan_unknown_bs_ref_falls_back_to_raw_name() -> None:
    """When backup-copy appliance destination is absent from bs_ref_keys, raw name is used."""
    loc = make_location_info()  # apm-server-01 appliance location
    bcp = BackupCopyPolicy(
        destination=loc,
        retention=ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=14),
        schedule=ProtectionSchedule(ScheduleFrequency.AFTER_BACKUP, start_time=None),
    )
    plan = ProtectionPlan(
        plan_id="123e4567-e89b-12d3-a456-426614174062",
        name="Plan With Unknown BS",
        category=WorkloadCategory.MACHINE,
        backup_copy_policy=bcp,
    )

    # Empty bs_ref_keys → falls back to raw location name
    result = ie._ser_protection_plan(plan, {}, {}, "ref-plan")

    assert result["backup_copy"] is not None
    assert result["backup_copy"]["destination_ref"] == "apm-server-01"


# ── _ser_protection_plan machine-only blocks ─────────────────────────────────


def test_ser_protection_plan_db_config_block() -> None:
    """db_config serializes each enum field to its semantic string value."""
    plan = make_protection_plan(
        db_config=MachineDbConfig(
            action_on_error=DbActionOnError.STOP,
            mssql_log_setting=MssqlLogSetting.TRUNCATE,
            oracle_log_setting=OracleLogSetting.DELETE,
        ),
    )

    result = ie._ser_protection_plan(plan, {}, {}, "plan-1")

    assert result["db_config"] == {
        "action_on_error": "stop",
        "mssql_log_setting": "truncate",
        "oracle_log_setting": "delete",
    }


def test_ser_protection_plan_machine_none_configs_serialize_as_none() -> None:
    """A machine plan with no per-type configs writes explicit nulls for each block."""
    plan = make_protection_plan()

    result = ie._ser_protection_plan(plan, {}, {}, "plan-1")

    assert result["vm_config"] is None
    assert result["pc_config"] is None
    assert result["ps_config"] is None
    assert result["db_config"] is None
    assert result["backup_window"] is None
    assert result["tasks"] is None


def test_ser_protection_plan_backup_window_allowed_hours_mapping() -> None:
    """allowed_hours maps WeekDay enums to weekday names and frozensets to sorted hour lists."""
    plan = make_protection_plan(
        backup_window=MachineBackupWindow(
            enabled=True,
            allowed_hours={
                WeekDay.MONDAY: frozenset({23, 0, 22}),
                WeekDay.SATURDAY: frozenset({8}),
            },
        ),
    )

    result = ie._ser_protection_plan(plan, {}, {}, "plan-1")

    assert result["backup_window"] == {
        "enabled": True,
        "allowed_hours": {"monday": [0, 22, 23], "saturday": [8]},
    }


def test_ser_protection_plan_tasks_list() -> None:
    """Each task serializes workload/os/scope enum values, volume list, and flags."""
    task = MachineTaskConfig(
        workload_type=MachineWorkloadType.PC,
        os_type=MachineOsType.WINDOWS,
        scope=MachineTaskScope.CUSTOM_VOLUME,
        custom_volumes=("C:", "D:"),
        include_external_drives=True,
        include_boot_partition=False,
        use_main_schedule=True,
        schedule=None,
    )
    plan = make_protection_plan(tasks=(task,))

    result = ie._ser_protection_plan(plan, {}, {}, "plan-1")

    assert result["tasks"] == [
        {
            "workload_type": "pc",
            "os_type": "windows",
            "scope": "custom_volume",
            "custom_volumes": ["C:", "D:"],
            "include_external_drives": True,
            "include_boot_partition": False,
            "use_main_schedule": True,
            "schedule": None,
        }
    ]


# ── _ser_file_server empty-dict cases ─────────────────────────────────────────


def test_ser_file_server_retired_workload_returns_empty_dict() -> None:
    """A retired FS workload serializes to {} (excluded from export)."""
    wl = make_machine_workload(
        workload_type=MachineWorkloadType.FS,
        is_retired=True,
        fs_config=make_file_server_config(),
    )

    result = ie._ser_file_server(wl, {}, {})

    assert result == {}


def test_ser_file_server_no_fs_config_returns_empty_dict() -> None:
    """An FS workload with fs_config=None (not yet configured) serializes to {}."""
    wl = make_machine_workload(
        workload_type=MachineWorkloadType.FS,
        is_retired=False,
        fs_config=None,
    )

    result = ie._ser_file_server(wl, {}, {})

    assert result == {}


# ── _ser_m365_auto_backup_rules_block ─────────────────────────────────────────


def test_ser_m365_auto_backup_rules_block_all_disabled_returns_none() -> None:
    """Returns None when there are no user rules and all collab services are disabled."""
    disabled = M365CollabServiceSetting(plan_id="", namespace="")
    result_obj = M365AutoBackupRuleListResult(
        rules=(),
        group_exchange=disabled,
        mysite=disabled,
        sharepoint=disabled,
        teams=disabled,
    )
    tenant = make_saas_tenant()

    result = ie._ser_m365_auto_backup_rules_block(tenant, result_obj, {}, {}, "ref-tenant")

    assert result is None


def test_ser_m365_auto_backup_rules_block_maps_refs_and_omits_disabled_services() -> None:
    """The enabled service maps plan_id/namespace to their ref keys; disabled services are
    absent; user_rules is empty when there are no user rules; tenant_ref is carried over."""
    disabled = M365CollabServiceSetting(plan_id="", namespace="")
    enabled = M365CollabServiceSetting(
        plan_id="123e4567-e89b-12d3-a456-426614174001",
        namespace="ns-apm-server-01",
    )
    result_obj = M365AutoBackupRuleListResult(
        rules=(),
        group_exchange=enabled,
        mysite=disabled,
        sharepoint=disabled,
        teams=disabled,
    )
    tenant = make_saas_tenant()
    plan_id_to_ref = {"123e4567-e89b-12d3-a456-426614174001": "plan-1"}
    bs_ns_to_ref = {"ns-apm-server-01": "server-1"}

    result = ie._ser_m365_auto_backup_rules_block(
        tenant, result_obj, plan_id_to_ref, bs_ns_to_ref, "tenant-1"
    )

    assert result == {
        "tenant_ref": "tenant-1",
        "user_rules": [],
        "collab_services": {
            "group_exchange": {"backup_server_ref": "server-1", "plan_ref": "plan-1"},
        },
    }


def test_ser_m365_auto_backup_rules_block_serializes_user_rules() -> None:
    """User rules map namespace/plan_id to ref keys and carry all three group ID lists."""
    disabled = M365CollabServiceSetting(plan_id="", namespace="")
    rule = M365AutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        tenant_id="123e4567-e89b-12d3-a456-426614174060",
        plan_id="123e4567-e89b-12d3-a456-426614174001",
        exchange_group_ids=("123e4567-e89b-12d3-a456-426614174012",),
        onedrive_group_ids=(),
        chat_group_ids=("123e4567-e89b-12d3-a456-426614174013",),
    )
    result_obj = M365AutoBackupRuleListResult(
        rules=(rule,),
        group_exchange=disabled,
        mysite=disabled,
        sharepoint=disabled,
        teams=disabled,
    )
    tenant = make_saas_tenant()
    plan_id_to_ref = {"123e4567-e89b-12d3-a456-426614174001": "plan-1"}
    bs_ns_to_ref = {"ns-apm-server-01": "server-1"}

    result = ie._ser_m365_auto_backup_rules_block(
        tenant, result_obj, plan_id_to_ref, bs_ns_to_ref, "tenant-1"
    )

    assert result == {
        "tenant_ref": "tenant-1",
        "user_rules": [
            {
                "backup_server_ref": "server-1",
                "plan_ref": "plan-1",
                "exchange_groups": ["123e4567-e89b-12d3-a456-426614174012"],
                "onedrive_groups": [],
                "chat_groups": ["123e4567-e89b-12d3-a456-426614174013"],
            }
        ],
        "collab_services": {},
    }


# ── _write_export_yaml ───────────────────────────────────────────────────────


def test_write_export_yaml_writes_each_section_under_correct_key(tmp_path: Path) -> None:
    """Each data list lands under its own top-level YAML key, not a neighboring one."""
    output = str(tmp_path / "export.yaml")

    ie._write_export_yaml(
        output,
        bs_data=[{"ref_key": "server-1", "name_or_id": "apm-server-01"}],
        rs_data=[{"ref_key": "remote-1", "name_or_id": "tiering-remote"}],
        protection_data=[{"ref_key": "plan-1", "name_or_id": "Daily Backup"}],
        retirement_data=[{"ref_key": "plan-2", "name_or_id": "Compliance Retention"}],
        tiering_data=[{"ref_key": "plan-3", "name_or_id": "Tiering Plan"}],
        fs_data=[{"backup_server_ref": "server-1", "name": "Corp Share"}],
        saas_data=[{"ref_key": "tenant-1", "tenant_id": "123e4567-e89b-12d3-a456-426614174060"}],
        m365_auto_bkp_data=[{"tenant_ref": "tenant-1", "user_rules": [], "collab_services": {}}],
    )

    parsed: dict[str, Any] = yaml.safe_load(Path(output).read_text(encoding="utf-8"))
    assert parsed["backup_servers"][0]["name_or_id"] == "apm-server-01"
    assert parsed["remote_storages"][0]["name_or_id"] == "tiering-remote"
    assert parsed["protection_plans"][0]["name_or_id"] == "Daily Backup"
    assert parsed["retirement_plans"][0]["name_or_id"] == "Compliance Retention"
    assert parsed["tiering_plans"][0]["name_or_id"] == "Tiering Plan"
    assert parsed["file_servers"][0]["name"] == "Corp Share"
    assert parsed["saas_tenants"][0]["tenant_id"] == "123e4567-e89b-12d3-a456-426614174060"
    assert parsed["m365_auto_backup_rules"][0]["tenant_ref"] == "tenant-1"


# ── _write_fs_credentials_csv / _write_storage_credentials_csv ──────────────


def test_write_fs_credentials_csv_writes_expected_rows(tmp_path: Path) -> None:
    path = str(tmp_path / "fs-credentials.csv")

    ie._write_fs_credentials_csv(path, [("192.0.2.1", "admin"), ("192.0.2.2", "root")])

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [
        {"endpoint": "192.0.2.1", "login_user": "admin", "password": ""},
        {"endpoint": "192.0.2.2", "login_user": "root", "password": ""},
    ]


def test_write_storage_credentials_csv_writes_expected_rows(tmp_path: Path) -> None:
    path = str(tmp_path / "storage-credentials.csv")

    ie._write_storage_credentials_csv(
        path, [("s3_compatible", "https://s3.example.com:443", "my-bucket")]
    )

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [
        {
            "storage_type": "s3_compatible",
            "endpoint": "https://s3.example.com:443",
            "vault_name": "my-bucket",
            "access_key": "",
            "secret_key": "",
            "relink_encryption_key": "",
        }
    ]
