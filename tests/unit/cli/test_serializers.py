"""Direct unit tests for synology_apm.cli._serializers public functions.

Each test asserts the dict structure (required keys present, correct types, enum
values serialized to strings, etc.) without going through the CLI invocation pipeline.
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
    hypervisor_to_dict,
    location_info_to_dict,
    m365_export_activity_to_csv_row,
    m365_export_activity_to_dict,
    m365_workload_to_csv_row,
    m365_workload_to_dict,
    protection_plan_to_csv_row,
    protection_plan_to_dict,
    remote_storage_to_dict,
    restore_activity_to_csv_row,
    retirement_plan_to_csv_row,
    retirement_plan_to_dict,
    server_to_csv_row,
    server_to_dict,
    site_info_to_dict,
    system_log_to_csv_row,
    system_log_to_dict,
    tenant_to_dict,
    tiering_plan_to_csv_row,
    tiering_plan_to_dict,
    tiering_status_to_dict,
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
    RemoteStorageStatus,
    RemoteStorageType,
    RestoreActivityStatus,
    RetentionType,
    ScheduleFrequency,
    ServerStatus,
    VersionCopyStatus,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatType,
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
from synology_apm.sdk.models.remote_storage import RemoteStorage
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.saas import SaasTenant
from synology_apm.sdk.models.system import SiteInfo, SiteStorageStats, WorkloadTypeStat, WorkloadUsageSummary
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
                "plan_id", "plan_name", "last_backup_at", "status", "workload_type"}
    assert required <= set(d.keys())
    assert d["is_retired"] is False
    assert d["status"] == "success"
    assert d["category"] == "machine"
    assert d["workload_id"] == "wl-id-001"


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


# ── server_to_dict ────────────────────────────────────────────────────────────

def test_server_to_dict_required_fields() -> None:
    d = server_to_dict(SAMPLE_SERVER)
    required = {"backup_server_id", "namespace", "name", "hostname", "model",
                "system_version", "status", "serial"}
    assert required <= set(d.keys())
    assert isinstance(d["status"], str)
    assert d["status"] == "healthy"
    assert d["backup_server_id"] == "bs-001"


def test_server_to_dict_no_tiering() -> None:
    d = server_to_dict(SAMPLE_SERVER)
    assert d["tiering_status"] is None
    assert d["tiering_plan_name"] is None


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
                "workload_count", "successful_workload_count"}
    assert required <= set(d.keys())
    assert isinstance(d["category"], str)
    assert d["category"] == "machine"
    assert d["is_immutable"] is False
    assert d["plan_id"] == "plan-001"


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
    required = {"plan_id", "name", "retention", "workload_count"}
    assert required <= set(d.keys())
    assert d["plan_id"] == "retire-001"
    assert d["retention"]["days"] == 30
    assert d["retention"]["keep_latest_version"] is True


def test_retirement_plan_to_csv_row_required_fields() -> None:
    row = retirement_plan_to_csv_row(SAMPLE_RETIREMENT_PLAN)
    required = {"plan_id", "name", "workload_count"}
    assert required <= set(row.keys())
    assert row["plan_id"] == "retire-001"
    assert row["retention_keep_latest"] is True


# ── tiering_plan_to_dict ──────────────────────────────────────────────────────

def test_tiering_plan_to_dict_required_fields() -> None:
    d = tiering_plan_to_dict(SAMPLE_TIERING_PLAN)
    required = {"plan_id", "name", "tiering_after_days", "daily_check_time",
                "server_count", "destination", "tiering_status"}
    assert required <= set(d.keys())
    assert d["plan_id"] == "tiering-001"
    assert d["tiering_after_days"] == 30
    assert d["daily_check_time"] == "02:00"


def test_tiering_plan_to_dict_destination_nested() -> None:
    d = tiering_plan_to_dict(SAMPLE_TIERING_PLAN)
    dest = d["destination"]
    assert dest is not None
    assert dest["name"] == "DSM-Storage"
    assert dest["vault"] == "MyVault"


def test_tiering_plan_to_dict_tiering_status_serialized() -> None:
    d = tiering_plan_to_dict(SAMPLE_TIERING_PLAN)
    ts = d["tiering_status"]
    assert ts is not None
    assert isinstance(ts["status"], str)
    assert ts["status"] == "completed"


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


def test_activity_to_dict_restore_fields() -> None:
    d = activity_to_dict(SAMPLE_RESTORE_ACT)
    assert d["activity_type"] == "restore"
    assert isinstance(d["status"], str)
    assert d["status"] == "success"


def test_backup_activity_to_csv_row_required_fields() -> None:
    row = backup_activity_to_csv_row(SAMPLE_BACKUP_ACT)
    assert isinstance(row["status"], str)
    assert row["activity_id"] == "act-001"
    assert row["workload_name"] == "vm-web-01"


def test_restore_activity_to_csv_row_required_fields() -> None:
    row = restore_activity_to_csv_row(SAMPLE_RESTORE_ACT)
    assert isinstance(row["status"], str)
    assert row["activity_id"] == "act-002"


# ── location_info_to_dict / tiering_status_to_dict ───────────────────────────

def test_location_info_to_dict_all_fields() -> None:
    loc = LocationInfo(
        is_remote_storage=True,
        identifier="ns-001",
        name="DSM-Storage",
        endpoint="192.0.2.20:8444",
        vault="MyVault",
    )
    d = location_info_to_dict(loc)
    assert d == {
        "is_remote_storage": True,
        "identifier": "ns-001",
        "name": "DSM-Storage",
        "endpoint": "192.0.2.20:8444",
        "vault": "MyVault",
    }


def test_tiering_status_to_dict_none_returns_none() -> None:
    assert tiering_status_to_dict(None) is None


def test_tiering_status_to_dict_enum_serialized() -> None:
    ts = TieringStatus(
        status=VersionCopyStatus.IN_PROGRESS,
        reason=None,
        pending_version_count=5,
    )
    d = tiering_status_to_dict(ts)
    assert d is not None
    assert isinstance(d["status"], str)
    assert d["status"] == "in_progress"
    assert d["pending_version_count"] == 5


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
                "protected_data_bytes", "backup_copy_data_bytes", "info_label",
                "backup_server", "backup_copy_destination"}
    assert required <= set(d.keys())
    assert d["workload_id"] == "m365-wl-001"
    assert d["workload_type"] == "exchange"
    assert d["status"] == "success"
    assert d["info_label"] == "alice@contoso.com"
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


def test_tenant_to_dict_fields() -> None:
    tenant = SaasTenant(
        tenant_id="m365-tenant-uuid-001",
        tenant_name="Contoso",
        tenant_email="admin@contoso.com",
        category=WorkloadCategory.M365,
        protected_data_bytes=1073741824,
    )
    d = tenant_to_dict(tenant)
    assert d == {
        "tenant_id": "m365-tenant-uuid-001",
        "tenant_name": "Contoso",
        "tenant_email": "admin@contoso.com",
        "category": "m365",
        "protected_data_bytes": 1073741824,
    }


def test_site_info_to_dict_fields() -> None:
    site = SiteInfo(
        site_uuid="550e8400-e29b-41d4-a716-446655440000",
        external_address="apm.corp.com",
        port="443",
        primary_management_server=SAMPLE_SERVER,
        secondary_management_server=None,
        site_storage=SiteStorageStats(
            logical_backup_data_bytes=2000,
            physical_backup_data_bytes=1000,
        ),
        workload_usage=WorkloadUsageSummary(
            by_type=(
                WorkloadTypeStat(
                    workload_type=WorkloadStatType.MACHINE_PC,
                    total_count=3,
                    protected_data_bytes=300,
                ),
            ),
        ),
    )
    d = site_info_to_dict(site)
    assert d["site_uuid"] == "550e8400-e29b-41d4-a716-446655440000"
    assert d["external_address"] == "apm.corp.com"
    assert d["primary_management_server"]["name"] == SAMPLE_SERVER.name
    assert d["secondary_management_server"] is None
    assert d["site_storage"]["logical_backup_data_bytes"] == 2000
    assert d["site_storage"]["backup_data_reduction_bytes"] == 1000
    assert d["site_storage"]["backup_data_reduction_ratio"] == 50.0
    assert d["workload_usage"]["by_type"][0]["workload_type"] == "machine_pc"
    assert d["workload_usage"]["by_type"][0]["total_count"] == 3


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


def test_hypervisor_to_dict_fields() -> None:
    d = hypervisor_to_dict(SAMPLE_HYPERVISOR)
    assert d == {
        "hypervisor_id": "978eabd4-e332-459f-a8e0-35a0aa312118",
        "hostname":      "esxi1.example.com",
        "address":       "192.0.2.40",
        "host_type":     HypervisorType.VSPHERE_ESXI.value,
        "account":       "root",
        "description":   "",
        "port":          443,
        "version":       "6.5",
    }


def test_hypervisor_to_csv_row_fields() -> None:
    row = hypervisor_to_csv_row(SAMPLE_HYPERVISOR)
    required = {"hostname", "address", "host_type", "account", "description", "hypervisor_id"}
    assert set(row.keys()) == required
    assert row["hostname"] == "esxi1.example.com"
    assert row["hypervisor_id"] == "978eabd4-e332-459f-a8e0-35a0aa312118"


def test_remote_storage_to_dict_fields() -> None:
    storage = RemoteStorage(
        storage_id="f0d5d047-7dda-59fe-8d1b-47441c80bd1e",
        name="DSM-Storage",
        storage_type=RemoteStorageType.ACTIVE_PROTECT_VAULT,
        device_model="DSM",
        endpoint="192.0.2.20:8444",
        status=RemoteStorageStatus.CONNECTED,
        used_bytes=453378,
        remaining_bytes=366960877568,
    )
    d = remote_storage_to_dict(storage)
    assert d["storage_id"] == "f0d5d047-7dda-59fe-8d1b-47441c80bd1e"
    assert d["name"] == "DSM-Storage"
    assert d["storage_type"] == RemoteStorageType.ACTIVE_PROTECT_VAULT.value
    assert d["status"] == RemoteStorageStatus.CONNECTED.value
    assert d["used_bytes"] == 453378
    assert d["remaining_bytes"] == 366960877568
    assert d["encryption_enabled"] is False


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
                "version_timestamp", "status", "started_at", "finished_at"}
    assert set(d.keys()) == required
    assert d["activity_id"] == "act-uuid-001"
    assert d["item"] == "Entire mailbox"
    assert d["status"] == "ready_to_download"
    assert d["finished_at"] is None
    assert d["started_at"] is not None


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
