"""Direct unit tests for synology_apm.cli._serializers public functions.

Each test asserts the dict structure (required keys present, correct types, enum
values serialized to strings, etc.) without going through the CLI invocation pipeline.

Fields with zero CLI-specific transformation (no local-time conversion, renaming, or
computed additions) are not re-asserted here — they are already covered by the SDK-level
to_dict() tests in tests/unit/sdk/models/test_models.py.
"""
from __future__ import annotations

from datetime import UTC, datetime, time

from synology_apm.cli._serializers import (
    activity_log_to_csv_row,
    activity_log_to_dict,
    activity_to_dict,
    backup_activity_to_csv_row,
    connection_log_to_csv_row,
    connection_log_to_dict,
    drive_log_to_csv_row,
    drive_log_to_dict,
    hypervisor_to_csv_row,
    m365_export_activity_to_csv_row,
    m365_export_activity_to_dict,
    m365_workload_to_csv_row,
    m365_workload_to_dict,
    protection_plan_to_csv_row,
    protection_plan_to_dict,
    restore_activity_to_csv_row,
    retirement_plan_to_csv_row,
    retirement_plan_to_dict,
    server_to_csv_row,
    system_log_to_csv_row,
    system_log_to_dict,
    tiering_plan_to_csv_row,
    version_to_csv_row,
    version_to_dict,
    workload_to_csv_row,
    workload_to_dict,
)
from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    APMActivityLogType,
    BackupActivityStatus,
    BackupServerType,
    HypervisorType,
    LogLevel,
    M365ExportStatus,
    M365WorkloadType,
    MachineWorkloadType,
    RestoreActivityStatus,
    RetentionType,
    ScheduleFrequency,
    ServerStatus,
    VersionCopyStatus,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.models.activity import BackupActivity, M365ExportActivity, RestoreActivity
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.hypervisor import Hypervisor
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.log import APMActivityLog, ConnectionLog, DriveLog, SystemLog
from synology_apm.sdk.models.protection_plan import (
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.tiering_plan import TieringPlan, TieringStatus
from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion
from synology_apm.sdk.models.workload import M365UserInfo, M365Workload, MachineWorkload

# ── Shared fixtures ───────────────────────────────────────────────────────────

_PLAN = ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE)

SAMPLE_WL = MachineWorkload(
    workload_id="wl-id-001",
    name="vm-web-01",
    category=WorkloadCategory.MACHINE,
    namespace="ns-001",
    last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    is_retired=False,
    protected_data_bytes=500 * 1024 * 1024,
    status=WorkloadStatus.SUCCESS,
    plan=_PLAN,
    workload_type=MachineWorkloadType.PC,
    agent_version="1.2.0",
    backup_server=LocationInfo(
        is_remote_storage=False,
        identifier="ns-srv-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    ),
)

SAMPLE_SERVER = BackupServer(
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

SAMPLE_PROTECTION_PLAN = ProtectionPlan(
    plan_id="plan-001",
    name="Daily Backup",
    category=WorkloadCategory.MACHINE,
    policy=ProtectionPlanPolicy(
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_VERSIONS, versions=10),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(2, 0)),
    ),
    workload_count=3,
    successful_workload_count=2,
    unsuccessful_workload_count=1,
    is_immutable=False,
)

SAMPLE_RETIREMENT_PLAN = RetirementPlan(
    plan_id="retire-001",
    name="Compliance Retention",
    description="30-day compliance archive",
    retention=RetirementRetentionPolicy(days=30, keep_latest_version=True),
    workload_count=2,
)

SAMPLE_TIERING_PLAN = TieringPlan(
    plan_id="tiering-001",
    name="30-Day Tiering",
    description="Move old versions to S3",
    tiering_after_days=30,
    daily_check_time=time(2, 0),
    destination=LocationInfo(
        is_remote_storage=True,
        identifier="dest-ns-001",
        name="DSM-Storage",
        endpoint="192.0.2.20:8444",
        vault="MyVault",
    ),
    server_count=1,
    tiering_status=TieringStatus(
        status=VersionCopyStatus.COMPLETED,
        reason=None,
        pending_version_count=0,
    ),
)

_STARTED = datetime(2026, 4, 21, 8, 0, tzinfo=UTC)

SAMPLE_BACKUP_ACT = BackupActivity(
    activity_id="act-001",
    execution_id="exec-001",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_VM,
    workload_id="wl-id-001",
    workload_namespace="ns-001",
    workload_name="vm-web-01",
    plan_name="Daily Backup",
    started_at=_STARTED,
    finished_at=None,
    duration_seconds=None,
    data_transferred_bytes=None,
    progress=0,
    status=BackupActivityStatus.SUCCESS,
    data_change_bytes=1024,
    data_deduped_bytes=512,
)

SAMPLE_RESTORE_ACT = RestoreActivity(
    activity_id="act-002",
    execution_id="exec-002",
    namespace="ns-001",
    category=WorkloadCategory.MACHINE,
    workload_type=ActivityWorkloadType.MACHINE_VM,
    workload_id="wl-id-001",
    workload_namespace="ns-001",
    workload_name="vm-web-01",
    plan_name="Daily Backup",
    started_at=_STARTED,
    finished_at=None,
    duration_seconds=None,
    data_transferred_bytes=None,
    progress=0,
    status=RestoreActivityStatus.SUCCESS,
)

SAMPLE_VERSION = WorkloadVersion(
    version_id="ver-001",
    workload_id="wl-id-001",
    namespace="ns-001",
    created_at=_STARTED,
    status=VersionStatus.SUCCESS,
    execution_id="exec-001",
    locked=False,
    changed_size_bytes=1024,
    locations=[
        VersionLocation(
            namespace="ns-001",
            location_id="loc-001",
            location_info=LocationInfo(
                is_remote_storage=False,
                identifier="ns-srv-001",
                name="apm-server-01",
                endpoint="192.0.2.1",
                vault=None,
            ),
        ),
    ],
)


# ── workload_to_dict ──────────────────────────────────────────────────────────

def test_workload_to_dict_required_fields() -> None:
    d = workload_to_dict(SAMPLE_WL)
    required = {"workload_id", "name", "namespace", "category", "is_retired",
                "plan_id", "plan_name", "last_backup_at", "status", "workload_type",
                "backup_progress", "items_backed_up", "fs_config"}
    assert required <= set(d.keys())
    assert d["is_retired"] is False
    assert d["status"] == "success"
    assert d["category"] == "machine"
    assert d["workload_id"] == "wl-id-001"
    assert d["backup_progress"] is None
    assert d["items_backed_up"] is None
    assert d["fs_config"] is None


def test_workload_to_dict_backup_server_nested() -> None:
    d = workload_to_dict(SAMPLE_WL)
    assert d["backup_server"] is not None
    assert d["backup_server"]["name"] == "apm-server-01"
    assert d["backup_copy_destination"] is None


def test_workload_to_csv_row_required_fields() -> None:
    row = workload_to_csv_row(SAMPLE_WL)
    required = {"workload_id", "name", "namespace", "status", "workload_type",
                "plan_id", "plan_name", "last_backup_at"}
    assert required <= set(row.keys())
    assert isinstance(row["status"], str)
    assert row["workload_id"] == "wl-id-001"


# ── server_to_csv_row ─────────────────────────────────────────────────────────
# server_to_dict was removed: BackupServer.to_dict() has zero CLI-specific transform
# and is passed directly to dispatch_output/dispatch_paginated_list (see
# tests/unit/sdk/models/test_models.py for field coverage, and
# tests/unit/cli/commands/test_infra_backup_server.py for the JSON output wiring test).

def test_server_to_csv_row_required_fields() -> None:
    row = server_to_csv_row(SAMPLE_SERVER)
    required = {"backup_server_id", "namespace", "name", "hostname", "model",
                "system_version", "status", "serial"}
    assert required <= set(row.keys())
    assert isinstance(row["status"], str)
    assert row["status"] == "healthy"


# ── protection_plan_to_dict ───────────────────────────────────────────────────

def test_protection_plan_to_dict_required_fields() -> None:
    d = protection_plan_to_dict(SAMPLE_PROTECTION_PLAN)
    required = {"plan_id", "name", "category", "is_immutable", "policy",
                "workload_count", "successful_workload_count",
                "run_schedule_by_controller_time", "vm_config", "pc_config", "ps_config", "db_config"}
    assert required <= set(d.keys())
    assert isinstance(d["category"], str)
    assert d["category"] == "machine"
    assert d["is_immutable"] is False
    assert d["plan_id"] == "plan-001"
    assert d["run_schedule_by_controller_time"] is False
    assert d["vm_config"] is None
    assert d["pc_config"] is None
    assert d["ps_config"] is None
    assert d["db_config"] is None


def test_protection_plan_to_dict_policy_nested() -> None:
    d = protection_plan_to_dict(SAMPLE_PROTECTION_PLAN)
    policy = d["policy"]
    assert isinstance(policy, dict)
    assert "retention" in policy
    assert policy["retention"]["type"] == "keep_versions"
    assert policy["schedule_label"] == "Daily Backup"


def test_protection_plan_to_csv_row_required_fields() -> None:
    row = protection_plan_to_csv_row(SAMPLE_PROTECTION_PLAN)
    required = {"plan_id", "name", "category", "is_immutable",
                "retention_type", "workload_count"}
    assert required <= set(row.keys())
    assert isinstance(row["retention_type"], str)
    assert row["plan_id"] == "plan-001"


# ── retirement_plan_to_dict ───────────────────────────────────────────────────

def test_retirement_plan_to_dict_required_fields() -> None:
    d = retirement_plan_to_dict(SAMPLE_RETIREMENT_PLAN)
    required = {"plan_id", "name", "retention", "workload_count", "run_schedule_by_controller_time"}
    assert required <= set(d.keys())
    assert d["plan_id"] == "retire-001"
    assert d["retention"]["days"] == 30
    assert d["retention"]["keep_latest_version"] is True
    assert d["run_schedule_by_controller_time"] is False


def test_retirement_plan_to_csv_row_required_fields() -> None:
    row = retirement_plan_to_csv_row(SAMPLE_RETIREMENT_PLAN)
    required = {"plan_id", "name", "workload_count"}
    assert required <= set(row.keys())
    assert row["plan_id"] == "retire-001"
    assert row["retention_keep_latest"] is True


# ── tiering_plan_to_csv_row ───────────────────────────────────────────────────
# tiering_plan_to_dict was removed: TieringPlan.to_dict() has zero CLI-specific transform
# and is passed directly to dispatch_output/dispatch_paginated_list (see
# tests/unit/sdk/models/test_models.py for field coverage, and
# tests/unit/cli/commands/test_plan_tiering.py for the JSON output wiring test).

def test_tiering_plan_to_csv_row_required_fields() -> None:
    row = tiering_plan_to_csv_row(SAMPLE_TIERING_PLAN)
    required = {"plan_id", "name", "tiering_after_days", "server_count"}
    assert required <= set(row.keys())
    assert row["plan_id"] == "tiering-001"
    assert row["tiering_status"] == "completed"


# ── version_to_dict ───────────────────────────────────────────────────────────

def test_version_to_dict_enum_values_are_strings() -> None:
    d = version_to_dict(SAMPLE_VERSION)
    assert isinstance(d["status"], str)
    assert d["status"] == "success"
    assert isinstance(d["locked"], bool)
    assert isinstance(d["locations"], list)
    assert len(d["locations"]) == 1


def test_version_to_dict_excludes_execution_id_and_includes_ids() -> None:
    d = version_to_dict(SAMPLE_VERSION)
    assert "execution_id" not in d
    assert d["workload_id"] == "wl-id-001"
    assert d["namespace"] == "ns-001"
    assert d["portal_version_id"] == ""
    assert d["snapshot_id"] == ""


def test_version_to_dict_location_includes_namespace_and_connection_id() -> None:
    d = version_to_dict(SAMPLE_VERSION)
    loc = d["locations"][0]
    assert loc["location_id"] == "loc-001"
    assert loc["namespace"] == "ns-001"
    assert loc["connection_id"] is None
    assert loc["name"] == "apm-server-01"


def test_version_to_csv_row_required_fields() -> None:
    row = version_to_csv_row(SAMPLE_VERSION)
    assert isinstance(row["status"], str)
    assert row["version_id"] == "ver-001"
    assert row["location_count"] == 1


# ── activity_to_dict ──────────────────────────────────────────────────────────

def test_activity_to_dict_backup_fields() -> None:
    d = activity_to_dict(SAMPLE_BACKUP_ACT)
    assert d["activity_type"] == "backup"
    assert isinstance(d["status"], str)
    assert d["status"] == "success"
    assert "data_change_bytes" in d
    assert "data_deduped_bytes" in d
    assert d["workload_id"] == "wl-id-001"
    assert "execution_id" not in d


def test_activity_to_dict_restore_fields() -> None:
    d = activity_to_dict(SAMPLE_RESTORE_ACT)
    assert d["activity_type"] == "restore"
    assert isinstance(d["status"], str)
    assert d["status"] == "success"
    assert "execution_id" not in d
    assert "restore_type" not in d  # unset on SAMPLE_RESTORE_ACT, omitted rather than shown as null


def test_backup_activity_to_csv_row_required_fields() -> None:
    row = backup_activity_to_csv_row(SAMPLE_BACKUP_ACT)
    assert isinstance(row["status"], str)
    assert row["activity_id"] == "act-001"
    assert row["workload_name"] == "vm-web-01"


def test_restore_activity_to_csv_row_required_fields() -> None:
    row = restore_activity_to_csv_row(SAMPLE_RESTORE_ACT)
    assert isinstance(row["status"], str)
    assert row["activity_id"] == "act-002"


# ── Serializers centralized from command modules ─────────────────────────────

SAMPLE_M365_WL = M365Workload(
    workload_id="m365-wl-001",
    name="alice@contoso.com",
    category=WorkloadCategory.M365,
    namespace="ns-m365-001",
    last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    is_retired=False,
    protected_data_bytes=1024,
    backup_copy_data_bytes=512,
    status=WorkloadStatus.SUCCESS,
    plan=ProtectionPlan(plan_id="m365-plan-001", name="M365 Daily", category=WorkloadCategory.M365),
    workload_type=M365WorkloadType.EXCHANGE,
    tenant_id="tenant-001",
    info=M365UserInfo(user_principal_name="alice@contoso.com"),
    backup_server=LocationInfo(
        is_remote_storage=False,
        identifier="ns-server-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    ),
)


def test_m365_workload_to_dict_required_fields() -> None:
    d = m365_workload_to_dict(SAMPLE_M365_WL)
    required = {"workload_id", "name", "category", "workload_type", "namespace", "tenant_id",
                "is_retired", "status", "plan_name", "plan_id", "last_backup_at",
                "protected_data_bytes", "backup_copy_data_bytes", "info_label", "info",
                "backup_server", "backup_copy_destination"}
    assert required <= set(d.keys())
    assert d["workload_id"] == "m365-wl-001"
    assert d["workload_type"] == "exchange"
    assert d["status"] == "success"
    assert d["info_label"] == "alice@contoso.com"
    assert d["info"] == {"kind": "user", "user_principal_name": "alice@contoso.com"}
    assert d["backup_server"]["name"] == "apm-server-01"
    assert d["backup_copy_destination"] is None


def test_m365_workload_to_csv_row_required_fields() -> None:
    row = m365_workload_to_csv_row(SAMPLE_M365_WL)
    required = {"name", "info_label", "status", "last_backup_at", "protected_data_bytes",
                "backup_copy_data_bytes", "plan_name", "plan_id", "backup_server_name",
                "copy_destination_name", "copy_destination_vault", "workload_id", "namespace"}
    assert required <= set(row.keys())
    assert row["backup_server_name"] == "apm-server-01"
    assert row["copy_destination_name"] == ""
    assert row["workload_id"] == "m365-wl-001"


SAMPLE_HYPERVISOR = Hypervisor(
    hypervisor_id="978eabd4-e332-459f-a8e0-35a0aa312118",
    hostname="esxi1.example.com",
    address="192.0.2.40",
    host_type=HypervisorType.VSPHERE_ESXI,
    account="root",
    description="",
    port=443,
    version="6.5",
)


def test_hypervisor_to_csv_row_fields() -> None:
    row = hypervisor_to_csv_row(SAMPLE_HYPERVISOR)
    required = {"hostname", "address", "host_type", "account", "description", "hypervisor_id"}
    assert set(row.keys()) == required
    assert row["hostname"] == "esxi1.example.com"
    assert row["hypervisor_id"] == "978eabd4-e332-459f-a8e0-35a0aa312118"


SAMPLE_EXPORT_ACTIVITY = M365ExportActivity(
    activity_id="act-uuid-001",
    execution_id="188",
    namespace="ns-m365-001",
    workload_id="m365-wl-001",
    workload_namespace="ns-m365-001",
    source_name="Entire mailbox",
    is_archive_mail=False,
    status=M365ExportStatus.READY_TO_DOWNLOAD,
    started_at=datetime(2026, 5, 10, 9, 0, tzinfo=UTC),
    finished_at=None,
    version_timestamp=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
)


def test_m365_export_activity_to_dict_fields() -> None:
    d = m365_export_activity_to_dict(SAMPLE_EXPORT_ACTIVITY)
    required = {"activity_id", "namespace", "workload_id", "workload_namespace", "item",
                "is_archive_mail", "version_timestamp", "status", "started_at", "finished_at"}
    assert set(d.keys()) == required
    assert d["activity_id"] == "act-uuid-001"
    assert d["item"] == "Entire mailbox"
    assert d["status"] == "ready_to_download"
    assert d["finished_at"] is None
    assert d["started_at"] is not None
    assert d["is_archive_mail"] is False


def test_m365_export_activity_to_csv_row_fields() -> None:
    row = m365_export_activity_to_csv_row(SAMPLE_EXPORT_ACTIVITY)
    required = {"item", "version_timestamp", "status", "started_at", "finished_at", "activity_id"}
    assert set(row.keys()) == required
    assert row["finished_at"] == ""
    assert row["status"] == "ready_to_download"


def test_log_entry_serializers_fields() -> None:
    ts = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    activity = APMActivityLog(
        level=LogLevel.WARNING, log_type=APMActivityLogType.PROTECTION,
        timestamp=ts, username="admin", description="Backup task started.",
    )
    d = activity_log_to_dict(activity)
    assert d["level"] == "warning"
    assert d["type"] == "protection"
    assert d["username"] == "admin"
    assert d["timestamp"] is not None

    no_type = APMActivityLog(
        level=LogLevel.INFO, log_type=None, timestamp=ts, username="admin", description="x",
    )
    assert activity_log_to_dict(no_type)["type"] is None

    drive = DriveLog(
        level=LogLevel.ERROR, timestamp=ts, description="Drive failure detected.",
        server_name="apm-server-01", model="ST8000", location="Slot 1", serial="SN001",
    )
    dd = drive_log_to_dict(drive)
    assert set(dd.keys()) == {"level", "timestamp", "description", "server_name", "model", "location", "serial"}
    assert dd["serial"] == "SN001"

    conn = ConnectionLog(level=LogLevel.INFO, timestamp=ts, username="admin", description="Signed in.")
    dc = connection_log_to_dict(conn)
    assert set(dc.keys()) == {"level", "timestamp", "username", "description"}

    system = SystemLog(level=LogLevel.INFO, timestamp=ts, username="SYSTEM", description="Update installed.")
    ds = system_log_to_dict(system)
    assert set(ds.keys()) == {"level", "timestamp", "username", "description"}
    assert ds["username"] == "SYSTEM"


def test_log_entry_csv_rows_align_with_table_columns() -> None:
    """The log *_to_csv_row field sets follow each command's table column order, with '' for missing values."""
    ts = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    activity = APMActivityLog(
        level=LogLevel.WARNING, log_type=None,
        timestamp=ts, username="admin", description="Backup task started.",
    )
    ra = activity_log_to_csv_row(activity)
    assert list(ra.keys()) == ["level", "type", "timestamp", "username", "description"]
    assert ra["level"] == "warning"
    assert ra["type"] == ""

    drive = DriveLog(
        level=LogLevel.ERROR, timestamp=ts, description="Drive failure detected.",
        server_name="apm-server-01", model="ST8000", location="Slot 1", serial="SN001",
    )
    rd = drive_log_to_csv_row(drive)
    assert list(rd.keys()) == ["level", "timestamp", "model", "serial", "server_name", "location", "description"]
    assert rd["serial"] == "SN001"

    conn = ConnectionLog(level=LogLevel.INFO, timestamp=ts, username="admin", description="Signed in.")
    rc = connection_log_to_csv_row(conn)
    assert list(rc.keys()) == ["level", "timestamp", "username", "description"]
    assert rc["description"] == "Signed in."

    system = SystemLog(level=LogLevel.INFO, timestamp=ts, username="SYSTEM", description="Update installed.")
    rs = system_log_to_csv_row(system)
    assert list(rs.keys()) == ["level", "timestamp", "username", "description"]
    assert rs["username"] == "SYSTEM"


def test_protection_plan_to_dict_serializes_backup_copy_policy() -> None:
    """protection_plan_to_dict includes the backup copy policy section when configured."""
    from synology_apm.sdk.enums import RetentionType, ScheduleFrequency, WorkloadCategory
    from synology_apm.sdk.models.location import LocationInfo
    from synology_apm.sdk.models.protection_plan import (
        BackupCopyPolicy,
        ProtectionPlan,
        ProtectionPlanPolicy,
        ProtectionRetentionPolicy,
        ProtectionSchedule,
    )

    plan = ProtectionPlan(
        plan_id="copy-plan-001",
        name="Machine Copy Plan",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=1,
        backup_copy_policy=BackupCopyPolicy(
            destination=LocationInfo(
                is_remote_storage=False, identifier="ns-copy-001",
                name="apm-server-02", endpoint="192.0.2.2", vault=None,
            ),
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        ),
    )
    d = protection_plan_to_dict(plan)
    bcp = d["backup_copy_policy"]
    assert bcp is not None
    assert bcp["destination"]["name"] == "apm-server-02"
    assert bcp["retention"]["days"] == 7
    assert bcp["schedule_label"] == "After Backup"


def test_protection_plan_to_dict_task_schedule_none_when_empty() -> None:
    """A task schedule with neither time schedule nor event trigger serializes as None."""
    from synology_apm.sdk.enums import MachineOsType, MachineWorkloadType, RetentionType, WorkloadCategory
    from synology_apm.sdk.models.protection_plan import (
        MachineTaskConfig,
        MachineTaskSchedule,
        ProtectionPlan,
        ProtectionPlanPolicy,
        ProtectionRetentionPolicy,
    )

    plan = ProtectionPlan(
        plan_id="plan-empty-sched",
        name="Daily Backup",
        category=WorkloadCategory.MACHINE,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=None,
        ),
        workload_count=0,
        tasks=(
            MachineTaskConfig(
                MachineWorkloadType.PC, MachineOsType.WINDOWS,
                use_main_schedule=False,
                schedule=MachineTaskSchedule(time_schedule=None, event_trigger=None),
            ),
        ),
    )
    d = protection_plan_to_dict(plan)
    assert d["tasks"] is not None
    assert d["tasks"][0]["schedule"] is None
