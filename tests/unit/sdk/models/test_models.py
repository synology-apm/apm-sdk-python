"""Unit tests for SDK model properties."""
from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

import pytest

from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    APMActivityLogType,
    BackupActivityStatus,
    BackupServerRole,
    BackupServerType,
    CopyReason,
    DbActionOnError,
    FileServerType,
    HypervisorType,
    LogLevel,
    M365ExportStatus,
    M365WorkloadType,
    MachineOsType,
    MachineTaskScope,
    MachineWorkloadType,
    MssqlLogSetting,
    OracleLogSetting,
    RemoteStorageStatus,
    RemoteStorageType,
    RestoreActivityStatus,
    RetentionType,
    ScheduleFrequency,
    ServerStatus,
    VersionCopyStatus,
    VersionStatus,
    WeekDay,
    WorkloadCategory,
    WorkloadStatType,
    WorkloadStatus,
)
from synology_apm.sdk.models.activity import (
    ActivityLogEntry,
    BackupActivity,
    M365ExportActivity,
    RestoreActivity,
)
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.hypervisor import Hypervisor
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.log import APMActivityLog, ConnectionLog, DriveLog, SystemLog
from synology_apm.sdk.models.m365_auto_backup_rule import (
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
)
from synology_apm.sdk.models.protection_plan import (
    BackupCopyConfig,
    BackupCopyPolicy,
    EventTriggerConfig,
    GFSRetention,
    M365PlanCreateRequest,
    MachineBackupWindow,
    MachineDbConfig,
    MachinePcConfig,
    MachinePsConfig,
    MachineTaskConfig,
    MachineTaskSchedule,
    MachineVmConfig,
    PlanBackupCopyStatus,
    ProtectionPlan,
    ProtectionPlanPolicy,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
)
from synology_apm.sdk.models.remote_storage import RemoteStorage, RemoteStorageAddResult
from synology_apm.sdk.models.retirement_plan import RetirementPlan, RetirementRetentionPolicy
from synology_apm.sdk.models.saas import SaasTenant
from synology_apm.sdk.models.system import SiteInfo, SiteStorageStats, WorkloadTypeStat, WorkloadUsageSummary
from synology_apm.sdk.models.tiering_plan import TieringPlan, TieringStatus
from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion
from synology_apm.sdk.models.workload import (
    FileServerConfig,
    FileServerPathSelector,
    M365GroupInfo,
    M365Info,
    M365SiteInfo,
    M365TeamInfo,
    M365UserInfo,
    M365Workload,
    MachineWorkload,
)


def make_machine_wl(**kwargs: Any) -> MachineWorkload:
    defaults: dict[str, Any] = dict(
        workload_id="wl-id-001",
        name="TestPC",
        category=WorkloadCategory.MACHINE,
        namespace="ns-001",
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.NO_BACKUPS,
        plan=ProtectionPlan(plan_id="plan-001", name="Test Plan", category=WorkloadCategory.MACHINE),
        workload_type=MachineWorkloadType.PC,
        agent_version="1.2.0",
    )
    defaults.update(kwargs)
    return MachineWorkload(**defaults)


def make_version(**kwargs: Any) -> WorkloadVersion:
    defaults: dict[str, Any] = dict(
        version_id="ver-001",
        workload_id="wl-id-001",
        namespace="ns-001",
        execution_id="exec-001",
        status=VersionStatus.SUCCESS,
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        changed_size_bytes=512,
        locked=False,
        locations=[],
    )
    defaults.update(kwargs)
    return WorkloadVersion(**defaults)


def make_location(*, is_remote: bool = False) -> VersionLocation:
    info = LocationInfo(
        is_remote_storage=is_remote,
        identifier="ns-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    )
    return VersionLocation(namespace="ns-001", location_info=info, location_id="ver-001")


# ── Workload.is_backing_up ─────────────────────────────────────────────────

@pytest.mark.parametrize("backup_progress,expected", [
    (None, False),
    (42, True),
    (0, True),
], ids=["no_progress", "progress_set", "progress_zero"])
def test_is_backing_up_reflects_backup_progress(backup_progress: int | None, expected: bool) -> None:
    """is_backing_up should be False only when backup_progress is None -- a progress of 0
    still counts as actively backing up."""
    wl = make_machine_wl(backup_progress=backup_progress)
    assert wl.is_backing_up is expected


def test_is_backing_up_true_when_items_backed_up_set() -> None:
    wl = make_machine_wl(items_backed_up=100)
    assert wl.is_backing_up is True


def test_is_backing_up_false_when_both_none() -> None:
    wl = make_machine_wl(backup_progress=None, items_backed_up=None)
    assert wl.is_backing_up is False


# ── M365 *Info.label ──────────────────────────────────────────────────────

def test_m365_user_info_label() -> None:
    info = M365UserInfo(user_principal_name="alice@contoso.com")
    assert info.label == "alice@contoso.com"


def test_m365_site_info_label() -> None:
    info = M365SiteInfo(
        site_url="https://contoso.sharepoint.com/sites/hr",
        site_name="HR Site",
    )
    assert info.label == "https://contoso.sharepoint.com/sites/hr"


def test_m365_team_info_label() -> None:
    info = M365TeamInfo(
        team_id="team-001",
        team_name="Engineering",
        web_url="https://teams.microsoft.com/l/team/123",
    )
    assert info.label == "https://teams.microsoft.com/l/team/123"


def test_m365_group_info_label() -> None:
    info = M365GroupInfo(
        group_id="grp-001",
        display_name="Marketing Group",
        mail="marketing@contoso.com",
    )
    assert info.label == "marketing@contoso.com"


# ── VersionLocation.location_info ─────────────────────────────────────────

def test_location_info_appliance() -> None:
    loc = make_location(is_remote=False)
    assert loc.location_info.is_remote_storage is False
    assert loc.location_info.vault is None


def test_location_info_remote_with_vault() -> None:
    info = LocationInfo(
        is_remote_storage=True,
        identifier="storage-uid-001",
        name="DSM-Storage",
        endpoint="192.0.2.20:8444",
        vault="MyVault",
    )
    loc = VersionLocation(namespace="ns-001", location_info=info, location_id="ver-002")
    assert loc.location_info.is_remote_storage is True
    assert loc.location_info.vault == "MyVault"


def test_version_locations_count() -> None:
    version = make_version(locations=[make_location(), make_location(is_remote=True)])
    assert len(version.locations) == 2
    assert version.locations[0].location_info.is_remote_storage is False
    assert version.locations[1].location_info.is_remote_storage is True


def test_version_empty_locations() -> None:
    version = make_version(locations=[])
    assert version.locations == []


# ── Lightweight ProtectionPlan/RetirementPlan (workload-embedded plan reference) ──

def test_protection_plan_lightweight_defaults() -> None:
    plan = ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE)
    assert plan.policy is None
    assert plan.workload_count is None


def test_retirement_plan_lightweight_defaults() -> None:
    plan = RetirementPlan(plan_id="plan-002", name="Compliance Retention")
    assert plan.description == ""
    assert plan.retention is None
    assert plan.workload_count is None


def test_workload_plan_field() -> None:
    wl = make_machine_wl(plan=ProtectionPlan(plan_id="plan-003", name="Daily Backup", category=WorkloadCategory.MACHINE))
    assert wl.plan.plan_id == "plan-003"
    assert wl.plan.name == "Daily Backup"


# ── to_dict() coverage ──────────────────────────────────────────────────────
#
# Every SDK response model dataclass exposes a to_dict() method (see the SDK
# README's Design Conventions). These tests build a representative instance
# per model and assert on its to_dict() output; the exhaustiveness test at the
# bottom of this file guards against a new model being added without one.


def make_backup_server(**kwargs: Any) -> BackupServer:
    defaults: dict[str, Any] = dict(
        backup_server_id="srv-001",
        namespace="default",
        server_type=BackupServerType.DP,
        name="apm-server-01",
        hostname="192.0.2.1",
        model="DP320",
        system_version="APM 1.2-71845",
        status=ServerStatus.HEALTHY,
        is_updating=False,
        serial="SN001",
        storage_total_bytes=10_000_000_000,
        storage_used_bytes=3_000_000_000,
        logical_backup_data_bytes=8_000_000_000,
        physical_backup_data_bytes=3_000_000_000,
        role=BackupServerRole.PRIMARY,
    )
    defaults.update(kwargs)
    return BackupServer(**defaults)


def make_hypervisor(**kwargs: Any) -> Hypervisor:
    defaults: dict[str, Any] = dict(
        hypervisor_id="hyp-001", hostname="esxi1.example.com", address="192.0.2.40",
        host_type=HypervisorType.VSPHERE_ESXI, account="root", description="", port=443, version="7.0.0",
    )
    defaults.update(kwargs)
    return Hypervisor(**defaults)


def make_remote_storage(**kwargs: Any) -> RemoteStorage:
    defaults: dict[str, Any] = dict(
        storage_id="stor-001", name="DSM-Storage", storage_type=RemoteStorageType.S3_COMPATIBLE,
        device_model="DS720+", endpoint="192.0.2.20:8444", status=RemoteStorageStatus.CONNECTED,
        used_bytes=1_000_000_000, remaining_bytes=9_000_000_000, vault_name="MyVault",
    )
    defaults.update(kwargs)
    return RemoteStorage(**defaults)


def make_saas_tenant(**kwargs: Any) -> SaasTenant:
    defaults: dict[str, Any] = dict(
        tenant_id="tenant-001", tenant_name="Contoso", tenant_email="admin@contoso.com",
        category=WorkloadCategory.M365, protected_data_bytes=5_000_000_000,
    )
    defaults.update(kwargs)
    return SaasTenant(**defaults)


class TestLocationInfoToDict:
    def test_fields(self) -> None:
        loc = LocationInfo(is_remote_storage=True, identifier="stor-001", name="DSM-Storage", endpoint="192.0.2.20:8444", vault="MyVault")
        assert loc.to_dict() == {
            "is_remote_storage": True, "identifier": "stor-001", "name": "DSM-Storage",
            "endpoint": "192.0.2.20:8444", "vault": "MyVault",
        }


class TestTieringStatusToDict:
    def test_fields(self) -> None:
        ts = TieringStatus(status=VersionCopyStatus.IN_PROGRESS, reason=None, pending_version_count=3, remaining_bytes=1000)
        d = ts.to_dict()
        assert d["status"] == "in_progress"
        assert d["pending_version_count"] == 3
        assert d["remaining_bytes"] == 1000


class TestHypervisorToDict:
    def test_fields(self) -> None:
        hyp = make_hypervisor()
        d = hyp.to_dict()
        assert d == {
            "hypervisor_id": "hyp-001",
            "hostname": "esxi1.example.com",
            "address": "192.0.2.40",
            "host_type": "vsphere_esxi",
            "account": "root",
            "description": "",
            "port": 443,
            "version": "7.0.0",
        }


class TestRemoteStorageToDict:
    def test_fields(self) -> None:
        storage = make_remote_storage()
        d = storage.to_dict()
        assert d == {
            "storage_id": "stor-001",
            "name": "DSM-Storage",
            "storage_type": "s3_compatible",
            "device_model": "DS720+",
            "endpoint": "192.0.2.20:8444",
            "status": "connected",
            "used_bytes": 1_000_000_000,
            "remaining_bytes": 9_000_000_000,
            "encryption_enabled": False,
            "vault_name": "MyVault",
        }

    def test_add_result_nests_storage(self) -> None:
        storage = make_remote_storage()
        result = RemoteStorageAddResult(storage=storage, encryption_key="key-abc")
        d = result.to_dict()
        assert d["storage"]["storage_id"] == "stor-001"
        assert d["encryption_key"] == "key-abc"


class TestBackupServerToDict:
    def test_fields_include_reduction_stats(self) -> None:
        server = make_backup_server()
        d = server.to_dict()
        assert d["backup_server_id"] == "srv-001"
        assert d["server_type"] == "dp"
        assert d["storage_usage_pct"] == 30.0
        assert d["backup_data_reduction_bytes"] == 5_000_000_000
        assert d["backup_data_reduction_ratio"] == 62.5

    def test_nested_tiering_destination(self) -> None:
        dest = LocationInfo(is_remote_storage=True, identifier="stor-001", name="DSM-Storage", endpoint="e", vault=None)
        server = make_backup_server(tiering_plan_name="30-Day", tiering_plan_destination=dest)
        d = server.to_dict()
        assert d["tiering_plan_destination"]["name"] == "DSM-Storage"

    def test_no_tiering_plan_fields_are_none(self) -> None:
        server = make_backup_server()
        d = server.to_dict()
        assert d["tiering_status"] is None
        assert d["tiering_plan_name"] is None
        assert d["tiering_plan_destination"] is None


class TestSiteInfoToDict:
    def test_nested_structure(self) -> None:
        server = make_backup_server()
        stats = SiteStorageStats(logical_backup_data_bytes=100, physical_backup_data_bytes=40)
        usage = WorkloadUsageSummary(by_type=(WorkloadTypeStat(workload_type=WorkloadStatType.MACHINE_PC, total_count=5, protected_data_bytes=1000),))
        site = SiteInfo(
            site_uuid="uuid-001", external_address="apm.corp.com", port="443",
            primary_management_server=server, secondary_management_server=None,
            site_storage=stats, workload_usage=usage,
        )
        d = site.to_dict()
        assert d["site_uuid"] == "uuid-001"
        assert d["primary_management_server"]["backup_server_id"] == "srv-001"
        assert d["secondary_management_server"] is None
        assert d["site_storage"]["backup_data_reduction_bytes"] == 60
        assert d["workload_usage"]["total_count"] == 5
        assert d["workload_usage"]["by_type"][0]["workload_type"] == "machine_pc"


def test_site_storage_stats_reduction_ratio_zero_when_logical_bytes_zero() -> None:
    """backup_data_reduction_ratio should be 0.0 (not a ZeroDivisionError) when logical_backup_data_bytes is 0."""
    stats = SiteStorageStats(logical_backup_data_bytes=0, physical_backup_data_bytes=0)
    assert stats.backup_data_reduction_ratio == 0.0


class TestSaasTenantToDict:
    def test_fields(self) -> None:
        tenant = make_saas_tenant()
        d = tenant.to_dict()
        assert d["tenant_id"] == "tenant-001"
        assert d["category"] == "m365"


class TestWorkloadVersionToDict:
    def test_fields_and_nested_locations(self) -> None:
        version = make_version(locations=[make_location(), make_location(is_remote=True)])
        d = version.to_dict()
        assert d["version_id"] == "ver-001"
        assert d["status"] == "success"
        assert len(d["locations"]) == 2
        assert d["locations"][0]["location_info"]["is_remote_storage"] is False
        assert d["locations"][1]["location_info"]["is_remote_storage"] is True


class TestMachineWorkloadToDict:
    def test_fields_and_super_composition(self) -> None:
        wl = make_machine_wl(ip_address="192.0.2.100")
        d = wl.to_dict()
        # base Workload fields
        assert d["workload_id"] == "wl-id-001"
        assert d["plan"] == {"plan_id": "plan-001", "name": "Test Plan", "kind": "protection"}
        # MachineWorkload-specific fields
        assert d["workload_type"] == "pc"
        assert d["agent_version"] == "1.2.0"
        assert d["ip_address"] == "192.0.2.100"

    def test_fs_config_nested(self) -> None:
        cfg = FileServerConfig(
            host_ip="192.0.2.50", host_port=445, server_type=FileServerType.SMB,
            login_user="admin", enable_vss=True, connection_timeout_seconds=180,
            selectors=(FileServerPathSelector(path="/data", excluded_paths=("/data/tmp",)),),
        )
        wl = make_machine_wl(workload_type=MachineWorkloadType.FS, agent_version=None, fs_config=cfg)
        d = wl.to_dict()
        assert d["fs_config"]["host_ip"] == "192.0.2.50"
        assert d["fs_config"]["selectors"] == [{"path": "/data", "excluded_paths": ["/data/tmp"]}]


def _make_m365_workload(info: M365Info, **kwargs: Any) -> M365Workload:
    defaults: dict[str, Any] = dict(
        workload_id="wl-002", name="alice@contoso.com", category=WorkloadCategory.M365,
        namespace="default", last_backup_at=None, is_retired=False, protected_data_bytes=1,
        status=WorkloadStatus.SUCCESS,
        plan=ProtectionPlan(plan_id="plan-001", name="Daily", category=WorkloadCategory.M365),
        workload_type=M365WorkloadType.EXCHANGE, tenant_id="tenant-001",
        info=info,
    )
    defaults.update(kwargs)
    return M365Workload(**defaults)


class TestM365WorkloadToDict:
    def test_fields_and_polymorphic_info(self) -> None:
        wl = _make_m365_workload(M365UserInfo(user_principal_name="alice@contoso.com"))
        d = wl.to_dict()
        assert d["tenant_id"] == "tenant-001"
        assert d["info"] == {"kind": "user", "user_principal_name": "alice@contoso.com"}

    def test_site_info_variant(self) -> None:
        wl = _make_m365_workload(
            M365SiteInfo(site_url="https://contoso.sharepoint.com/sites/Marketing", site_name="Marketing")
        )
        d = wl.to_dict()
        assert d["info"] == {
            "kind": "site",
            "site_url": "https://contoso.sharepoint.com/sites/Marketing",
            "site_name": "Marketing",
        }

    def test_team_info_variant(self) -> None:
        wl = _make_m365_workload(
            M365TeamInfo(team_id="team-001", team_name="Engineering", web_url="https://teams.microsoft.com/l/team/team-001")
        )
        d = wl.to_dict()
        assert d["info"] == {
            "kind": "team",
            "team_id": "team-001",
            "team_name": "Engineering",
            "web_url": "https://teams.microsoft.com/l/team/team-001",
        }

    def test_group_info_variant(self) -> None:
        wl = _make_m365_workload(
            M365GroupInfo(group_id="group-001", display_name="Marketing Group", mail="marketing@contoso.com")
        )
        d = wl.to_dict()
        assert d["info"] == {
            "kind": "group",
            "group_id": "group-001",
            "display_name": "Marketing Group",
            "mail": "marketing@contoso.com",
        }


class TestProtectionPlanToDict:
    def test_nested_policy_and_retention(self) -> None:
        policy = ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_ADVANCED, gfs=GFSRetention(1, 2, 3, 4)),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.WEEKLY, start_time=time(2, 0), weekdays=(WeekDay.MONDAY,)),
        )
        plan = ProtectionPlan(plan_id="p1", name="Daily", category=WorkloadCategory.MACHINE, policy=policy, workload_count=5)
        d = plan.to_dict()
        assert d["policy"]["retention"]["gfs"]["daily_versions"] == 1
        assert d["policy"]["schedule"]["weekdays"] == ["monday"]
        assert d["policy"]["schedule"]["start_time"] == "02:00"

    def test_nested_device_configs_and_tasks(self) -> None:
        task = MachineTaskConfig(
            workload_type=MachineWorkloadType.PC, os_type=MachineOsType.WINDOWS,
            scope=MachineTaskScope.ENTIRE_MACHINE, use_main_schedule=False,
            schedule=MachineTaskSchedule(event_trigger=EventTriggerConfig(on_lock=True)),
        )
        plan = ProtectionPlan(
            plan_id="p2", name="Daily", category=WorkloadCategory.MACHINE,
            vm_config=MachineVmConfig(), pc_config=MachinePcConfig(), ps_config=MachinePsConfig(),
            db_config=MachineDbConfig(action_on_error=DbActionOnError.STOP, mssql_log_setting=MssqlLogSetting.TRUNCATE, oracle_log_setting=OracleLogSetting.DELETE),
            backup_window=MachineBackupWindow(enabled=True, allowed_hours={WeekDay.MONDAY: frozenset({1, 2})}),
            tasks=(task,),
        )
        d = plan.to_dict()
        assert d["vm_config"]["enable_app_aware_bkp"] is True
        assert d["db_config"]["action_on_error"] == "stop"
        assert d["backup_window"]["allowed_hours"] == {"monday": [1, 2]}
        assert d["tasks"][0]["schedule"]["event_trigger"]["on_lock"] is True

    def test_backup_copy_policy_and_status(self) -> None:
        dest = LocationInfo(is_remote_storage=False, identifier="ns", name="apm-server-01", endpoint="e", vault=None)
        bcp = BackupCopyPolicy(
            destination=dest,
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        )
        bcs = PlanBackupCopyStatus(status=VersionCopyStatus.SKIPPED, reason=CopyReason.NO_VERSIONS_TO_COPY, skipped_workload_count=2)
        plan = ProtectionPlan(
            plan_id="p3", name="Daily", category=WorkloadCategory.MACHINE,
            backup_copy_policy=bcp, backup_copy_status=bcs,
        )
        d = plan.to_dict()
        assert d["backup_copy_policy"]["destination"]["name"] == "apm-server-01"
        assert d["backup_copy_status"]["skipped_workload_count"] == 2


class TestRetirementPlanToDict:
    def test_fields(self) -> None:
        plan = make_retirement_plan()
        d = plan.to_dict()
        assert d["plan_id"] == "ret-001"
        assert d["retention"] == {"days": 365, "keep_latest_version": True}


class TestTieringPlanToDict:
    def test_fields(self) -> None:
        dest = LocationInfo(is_remote_storage=True, identifier="stor-001", name="DSM-Storage", endpoint="e", vault=None)
        plan = TieringPlan(
            plan_id="tier-001", name="30-Day Tiering", description="", tiering_after_days=30,
            daily_check_time=time(20, 0), destination=dest, server_count=2,
            tiering_status=TieringStatus(status=VersionCopyStatus.IN_PROGRESS, reason=None),
        )
        d = plan.to_dict()
        assert d["tiering_after_days"] == 30
        assert d["daily_check_time"] == "20:00"
        assert d["destination"]["name"] == "DSM-Storage"
        assert d["tiering_status"]["status"] == "in_progress"


class TestActivityToDict:
    def test_backup_activity_super_composition(self) -> None:
        act = BackupActivity(
            activity_id="a1", execution_id="e1", namespace="ns", category=WorkloadCategory.MACHINE,
            workload_type=ActivityWorkloadType.MACHINE_VM, workload_id="w1", workload_namespace="ns",
            workload_name="vm-web-01", plan_name="Daily", started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=None, duration_seconds=None, data_transferred_bytes=None, progress=50,
            status=BackupActivityStatus.BACKING_UP,
            log_entries=(ActivityLogEntry(timestamp=datetime(2026, 1, 1, tzinfo=UTC), level=LogLevel.INFO, message="m"),),
        )
        d = act.to_dict()
        assert d["activity_id"] == "a1"
        assert d["status"] == "backing_up"
        assert d["log_entries"] == [{"timestamp": "2026-01-01T00:00:00+00:00", "level": "info", "message": "m"}]

    def test_restore_activity_fields(self) -> None:
        act = RestoreActivity(
            activity_id="r1", execution_id="e2", namespace="ns", category=WorkloadCategory.MACHINE,
            workload_type=ActivityWorkloadType.MACHINE_VM, workload_id="w1", workload_namespace="ns",
            workload_name="vm-web-01", plan_name="Daily", started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=None, duration_seconds=None, data_transferred_bytes=None, progress=10,
            status=RestoreActivityStatus.RESTORING, operator="admin",
        )
        d = act.to_dict()
        assert d["status"] == "restoring"
        assert d["operator"] == "admin"

    def test_m365_export_activity_fields(self) -> None:
        act = M365ExportActivity(
            activity_id="x1", execution_id="ex1", namespace="ns", workload_id="w1",
            workload_namespace="ns", source_name="alice@contoso.com", is_archive_mail=False,
            status=M365ExportStatus.READY_TO_DOWNLOAD, started_at=None, finished_at=None,
        )
        d = act.to_dict()
        assert d["source_name"] == "alice@contoso.com"
        assert d["status"] == "ready_to_download"


class TestLogModelsToDict:
    def test_apm_activity_log(self) -> None:
        log = APMActivityLog(level=LogLevel.WARNING, log_type=APMActivityLogType.SYSTEM, timestamp=datetime(2026, 1, 1, tzinfo=UTC), username="admin", description="d")
        d = log.to_dict()
        assert d["level"] == "warning"
        assert d["log_type"] == "system"

    def test_drive_log(self) -> None:
        log = DriveLog(level=LogLevel.INFO, timestamp=datetime(2026, 1, 1, tzinfo=UTC), description="d", server_name="apm-server-01", model="DP320", location="slot 1", serial="SN001")
        d = log.to_dict()
        assert d["serial"] == "SN001"
        assert d["server_name"] == "apm-server-01"

    def test_connection_log(self) -> None:
        log = ConnectionLog(level=LogLevel.INFO, timestamp=datetime(2026, 1, 1, tzinfo=UTC), username="admin", description="d")
        assert log.to_dict()["username"] == "admin"

    def test_system_log(self) -> None:
        log = SystemLog(level=LogLevel.ERROR, timestamp=datetime(2026, 1, 1, tzinfo=UTC), username="SYSTEM", description="d")
        assert log.to_dict()["level"] == "error"


class TestM365AutoBackupRuleToDict:
    def test_list_result_nested_structure(self) -> None:
        rule = M365AutoBackupRule(uid="u1", namespace="ns", tenant_id="t1", plan_id="p1", exchange_group_ids=("g1",), onedrive_group_ids=(), chat_group_ids=())
        enabled = M365CollabServiceSetting(plan_id="p2", namespace="ns")
        disabled = M365CollabServiceSetting(plan_id="", namespace="")
        result = M365AutoBackupRuleListResult(rules=(rule,), group_exchange=enabled, mysite=disabled, sharepoint=disabled, teams=disabled)
        d = result.to_dict()
        assert d["rules"][0]["exchange_group_ids"] == ["g1"]
        assert d["collab_settings"]["group_exchange"]["enabled"] is True
        assert d["collab_settings"]["mysite"]["enabled"] is False


def make_retirement_plan(**kwargs: Any) -> RetirementPlan:
    defaults: dict[str, Any] = dict(
        plan_id="ret-001", name="Compliance Retention",
        retention=RetirementRetentionPolicy(days=365, keep_latest_version=True), workload_count=2,
    )
    defaults.update(kwargs)
    return RetirementPlan(**defaults)


# ── Construction-time validation (raises before any API call) ──────────────

_COPY_DEST = BackupServer(
    backup_server_id="bs-dp-001",
    namespace="123e4567-e89b-12d3-a456-426614174000",
    server_type=BackupServerType.DP,
    name="apm-server-01",
    hostname="192.0.2.1",
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


def _m365_request(backup_copy: BackupCopyConfig | None) -> M365PlanCreateRequest:
    return M365PlanCreateRequest(
        name="M365 Daily",
        retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
        schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(9, 0)),
        backup_copy=backup_copy,
    )


def test_m365_plan_weekly_backup_copy_no_weekdays_raises() -> None:
    """M365PlanCreateRequest with a WEEKLY Backup Copy schedule and no weekdays raises at construction."""
    with pytest.raises(ValueError, match="WEEKLY Backup Copy schedule requires at least one weekday"):
        _m365_request(
            BackupCopyConfig(
                destination=_COPY_DEST,
                retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
                schedule=ProtectionSchedule(frequency=ScheduleFrequency.WEEKLY, start_time=time(2, 0)),
            )
        )


def test_m365_plan_after_backup_copy_is_accepted() -> None:
    """AFTER_BACKUP remains valid for an M365 Backup Copy schedule (non-regression)."""
    request = _m365_request(
        BackupCopyConfig(
            destination=_COPY_DEST,
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=7),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.AFTER_BACKUP, start_time=None),
        )
    )
    assert request.backup_copy is not None


def test_backup_window_out_of_range_hour_raises_when_enabled() -> None:
    """An allowed hour outside 0-23 raises at construction time when the window is enabled."""
    with pytest.raises(ValueError, match="out of range 0-23"):
        MachineBackupWindow(enabled=True, allowed_hours={WeekDay.MONDAY: frozenset({30})})


def test_backup_window_out_of_range_hour_ignored_when_disabled() -> None:
    """allowed_hours is ignored when the window is disabled, so out-of-range values are retained
    without raising (the "Ignored when enabled is False" contract)."""
    window = MachineBackupWindow(enabled=False, allowed_hours={WeekDay.MONDAY: frozenset({30})})
    assert window.enabled is False
    assert window.allowed_hours[WeekDay.MONDAY] == frozenset({30})


def test_backup_window_boundary_hours_accepted() -> None:
    """Hours 0 and 23 are valid boundaries and must not raise."""
    window = MachineBackupWindow(enabled=True, allowed_hours={WeekDay.MONDAY: frozenset({0, 23})})
    assert window.allowed_hours[WeekDay.MONDAY] == frozenset({0, 23})


# ── Exhaustiveness: every response model must expose to_dict() ─────────────

_EXEMPT_INPUT_ONLY_MODELS = frozenset({
    # Write-only helper passed into MachinePlanCreateRequest.backup_copy /
    # M365PlanCreateRequest.backup_copy; never returned by the API (ProtectionPlan
    # exposes the read-only BackupCopyPolicy instead). Doesn't match the *Request
    # naming convention, so it needs an explicit exemption here.
    "BackupCopyConfig",
    # Local config-file dataclasses (~/.config/synology-apm/config.toml), not API
    # response models — outside the scope of the to_dict() response-model contract.
    "AppConfig",
    "ProfileConfig",
    "ResolvedConnection",
})


def test_every_response_model_has_to_dict() -> None:
    """Guards against a newly-added model forgetting to_dict() (see SDK README's
    Design Conventions). Excludes *Request input types (never serialized) and the
    explicitly-exempted input-only helpers in _EXEMPT_INPUT_ONLY_MODELS.
    """
    import dataclasses

    import synology_apm.sdk as sdk

    missing = []
    for name in sdk.__all__:
        obj = getattr(sdk, name)
        if not dataclasses.is_dataclass(obj):
            continue
        if name.endswith("Request") or name in _EXEMPT_INPUT_ONLY_MODELS:
            continue
        if not callable(getattr(obj, "to_dict", None)):
            missing.append(name)
    assert missing == []
