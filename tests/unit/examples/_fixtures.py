"""Fake SDK model builders and fake-client helpers for example unit tests."""
from __future__ import annotations

from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from synology_apm.sdk import (
    ActivityWorkloadType,
    BackupActivityStatus,
    BackupScope,
    BackupServerRole,
    BackupServerType,
    CopyReason,
    FileServerType,
    Hypervisor,
    M365WorkloadType,
    MachineWorkloadType,
    RemoteStorageStatus,
    RemoteStorageType,
    RestoreActivityStatus,
    RestoreType,
    ServerStatus,
    TieringStatus,
    VerifyStatus,
    VersionCopyStatus,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.models.activity import ActivityLogEntry, BackupActivity, RestoreActivity
from synology_apm.sdk.models.backup_server import BackupServer
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import (
    BackupCopyPolicy,
    MachineBackupWindow,
    MachineDbConfig,
    MachinePcConfig,
    MachinePsConfig,
    MachineTaskConfig,
    MachineVmConfig,
    PlanBackupCopyStatus,
    ProtectionPlan,
    ProtectionPlanPolicy,
)
from synology_apm.sdk.models.remote_storage import RemoteStorage
from synology_apm.sdk.models.saas import SaasTenant
from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion
from synology_apm.sdk.models.workload import (
    FileServerConfig,
    FileServerPathSelector,
    M365GroupInfo,
    M365Info,
    M365UserInfo,
    M365Workload,
    MachineWorkload,
)

_DT = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)


def make_location_info(
    *,
    is_remote_storage: bool = False,
    identifier: str = "ns-apm-server-01",
    name: str = "apm-server-01",
    endpoint: str = "192.0.2.1",
    vault: str | None = None,
) -> LocationInfo:
    """Build a LocationInfo with sensible defaults."""
    return LocationInfo(
        is_remote_storage=is_remote_storage,
        identifier=identifier,
        name=name,
        endpoint=endpoint,
        vault=vault,
    )


def make_protection_plan(
    *,
    plan_id: str = "123e4567-e89b-12d3-a456-426614174001",
    name: str = "Daily Backup",
    category: WorkloadCategory = WorkloadCategory.MACHINE,
    policy: ProtectionPlanPolicy | None = None,
    workload_count: int | None = None,
    description: str = "",
    successful_workload_count: int = 0,
    unsuccessful_workload_count: int = 0,
    is_immutable: bool = False,
    backup_copy_policy: BackupCopyPolicy | None = None,
    backup_copy_status: PlanBackupCopyStatus | None = None,
    run_schedule_by_controller_time: bool = False,
    vm_config: MachineVmConfig | None = None,
    pc_config: MachinePcConfig | None = None,
    ps_config: MachinePsConfig | None = None,
    db_config: MachineDbConfig | None = None,
    backup_window: MachineBackupWindow | None = None,
    tasks: tuple[MachineTaskConfig, ...] | None = None,
) -> ProtectionPlan:
    """Build a ProtectionPlan with sensible defaults."""
    return ProtectionPlan(
        plan_id=plan_id,
        name=name,
        category=category,
        policy=policy,
        workload_count=workload_count,
        description=description,
        successful_workload_count=successful_workload_count,
        unsuccessful_workload_count=unsuccessful_workload_count,
        is_immutable=is_immutable,
        backup_copy_policy=backup_copy_policy,
        backup_copy_status=backup_copy_status,
        run_schedule_by_controller_time=run_schedule_by_controller_time,
        vm_config=vm_config,
        pc_config=pc_config,
        ps_config=ps_config,
        db_config=db_config,
        backup_window=backup_window,
        tasks=tasks,
    )


def make_machine_workload(
    *,
    workload_id: str = "123e4567-e89b-12d3-a456-426614174001",
    name: str = "CORP-PC-001",
    category: WorkloadCategory = WorkloadCategory.MACHINE,
    namespace: str = "ns-apm-server-01",
    last_backup_at: datetime | None = _DT,
    is_retired: bool = False,
    protected_data_bytes: int = 10_737_418_240,
    status: WorkloadStatus = WorkloadStatus.SUCCESS,
    plan: ProtectionPlan | None = None,
    backup_progress: int | None = None,
    items_backed_up: int | None = None,
    backup_server: LocationInfo | None = None,
    backup_copy_destination: LocationInfo | None = None,
    backup_copy_data_bytes: int = 0,
    workload_type: MachineWorkloadType = MachineWorkloadType.PC,
    agent_version: str | None = "7.0.0-1234",
    verify_status: VerifyStatus | None = None,
    device_uuid: str | None = "123e4567-e89b-12d3-a456-426614174099",
    ip_address: str | None = "192.0.2.50",
    inventory_name: str | None = None,
    inventory_type: str | None = None,
    fs_config: FileServerConfig | None = None,
) -> MachineWorkload:
    """Build a MachineWorkload with sensible defaults."""
    resolved_plan = plan if plan is not None else make_protection_plan()
    return MachineWorkload(
        workload_id=workload_id,
        name=name,
        category=category,
        namespace=namespace,
        last_backup_at=last_backup_at,
        is_retired=is_retired,
        protected_data_bytes=protected_data_bytes,
        status=status,
        plan=resolved_plan,
        backup_progress=backup_progress,
        items_backed_up=items_backed_up,
        backup_server=backup_server,
        backup_copy_destination=backup_copy_destination,
        backup_copy_data_bytes=backup_copy_data_bytes,
        workload_type=workload_type,
        agent_version=agent_version,
        verify_status=verify_status,
        device_uuid=device_uuid,
        ip_address=ip_address,
        inventory_name=inventory_name,
        inventory_type=inventory_type,
        fs_config=fs_config,
    )


def make_m365_user_info(
    *,
    user_principal_name: str = "alice@contoso.com",
) -> M365UserInfo:
    """Build an M365UserInfo with sensible defaults."""
    return M365UserInfo(user_principal_name=user_principal_name)


def make_m365_group_info(
    *,
    group_id: str = "123e4567-e89b-12d3-a456-426614174010",
    display_name: str = "Marketing",
    mail: str = "marketing@contoso.com",
) -> M365GroupInfo:
    """Build an M365GroupInfo with sensible defaults."""
    return M365GroupInfo(
        group_id=group_id,
        display_name=display_name,
        mail=mail,
    )


def make_m365_workload(
    *,
    workload_id: str = "123e4567-e89b-12d3-a456-426614174002",
    name: str = "alice@contoso.com",
    category: WorkloadCategory = WorkloadCategory.M365,
    namespace: str = "ns-apm-server-01",
    last_backup_at: datetime | None = _DT,
    is_retired: bool = False,
    protected_data_bytes: int = 2_147_483_648,
    status: WorkloadStatus = WorkloadStatus.SUCCESS,
    plan: ProtectionPlan | None = None,
    backup_progress: int | None = None,
    items_backed_up: int | None = None,
    backup_server: LocationInfo | None = None,
    backup_copy_destination: LocationInfo | None = None,
    backup_copy_data_bytes: int = 0,
    workload_type: M365WorkloadType = M365WorkloadType.EXCHANGE,
    tenant_id: str = "123e4567-e89b-12d3-a456-426614174000",
    info: M365Info | None = None,
) -> M365Workload:
    """Build an M365Workload with sensible defaults."""
    resolved_plan = plan if plan is not None else make_protection_plan(
        category=WorkloadCategory.M365,
    )
    resolved_info: M365Info = info if info is not None else make_m365_user_info()
    return M365Workload(
        workload_id=workload_id,
        name=name,
        category=category,
        namespace=namespace,
        last_backup_at=last_backup_at,
        is_retired=is_retired,
        protected_data_bytes=protected_data_bytes,
        status=status,
        plan=resolved_plan,
        backup_progress=backup_progress,
        items_backed_up=items_backed_up,
        backup_server=backup_server,
        backup_copy_destination=backup_copy_destination,
        backup_copy_data_bytes=backup_copy_data_bytes,
        workload_type=workload_type,
        tenant_id=tenant_id,
        info=resolved_info,
    )


def make_file_server_config(
    *,
    host_ip: str = "10.0.0.10",
    host_port: int = 445,
    server_type: FileServerType = FileServerType.SMB,
    login_user: str = "admin",
    enable_vss: bool = False,
    connection_timeout_seconds: int = 180,
    selectors: tuple[FileServerPathSelector, ...] | None = None,
) -> FileServerConfig:
    """Build a FileServerConfig with sensible defaults."""
    resolved_selectors = selectors if selectors is not None else (FileServerPathSelector(path=""),)
    return FileServerConfig(
        host_ip=host_ip,
        host_port=host_port,
        server_type=server_type,
        login_user=login_user,
        enable_vss=enable_vss,
        connection_timeout_seconds=connection_timeout_seconds,
        selectors=resolved_selectors,
    )


def make_backup_server(
    *,
    backup_server_id: str = "123e4567-e89b-12d3-a456-426614174020",
    namespace: str = "ns-apm-server-01",
    server_type: BackupServerType = BackupServerType.DP,
    name: str = "apm-server-01",
    hostname: str = "192.0.2.1",
    model: str = "DP320",
    system_version: str | None = "APM 1.2-71845",
    status: ServerStatus = ServerStatus.HEALTHY,
    is_updating: bool = False,
    serial: str = "SN001",
    storage_total_bytes: int | None = 10_995_116_277_760,
    storage_used_bytes: int | None = 3_298_534_883_328,
    logical_backup_data_bytes: int | None = 5_497_558_138_880,
    physical_backup_data_bytes: int | None = 3_298_534_883_328,
    role: BackupServerRole | None = None,
    description: str = "",
    tiering_plan_name: str | None = None,
    tiering_plan_destination: LocationInfo | None = None,
    tiering_status: TieringStatus | None = None,
) -> BackupServer:
    """Build a BackupServer with sensible defaults."""
    return BackupServer(
        backup_server_id=backup_server_id,
        namespace=namespace,
        server_type=server_type,
        name=name,
        hostname=hostname,
        model=model,
        system_version=system_version,
        status=status,
        is_updating=is_updating,
        serial=serial,
        storage_total_bytes=storage_total_bytes,
        storage_used_bytes=storage_used_bytes,
        logical_backup_data_bytes=logical_backup_data_bytes,
        physical_backup_data_bytes=physical_backup_data_bytes,
        role=role,
        description=description,
        tiering_plan_name=tiering_plan_name,
        tiering_plan_destination=tiering_plan_destination,
        tiering_status=tiering_status,
    )


def make_remote_storage(
    *,
    storage_id: str = "123e4567-e89b-12d3-a456-426614174030",
    name: str = "tiering-remote",
    storage_type: RemoteStorageType = RemoteStorageType.S3_COMPATIBLE,
    device_model: str = "",
    endpoint: str = "https://s3.example.com:443",
    status: RemoteStorageStatus = RemoteStorageStatus.CONNECTED,
    used_bytes: int | None = 1_073_741_824,
    remaining_bytes: int | None = 9_663_676_416,
    encryption_enabled: bool = False,
    vault_name: str = "my-bucket",
) -> RemoteStorage:
    """Build a RemoteStorage with sensible defaults."""
    return RemoteStorage(
        storage_id=storage_id,
        name=name,
        storage_type=storage_type,
        device_model=device_model,
        endpoint=endpoint,
        status=status,
        used_bytes=used_bytes,
        remaining_bytes=remaining_bytes,
        encryption_enabled=encryption_enabled,
        vault_name=vault_name,
    )


def make_backup_activity(
    *,
    activity_id: str = "123e4567-e89b-12d3-a456-426614174040",
    execution_id: str = "123e4567-e89b-12d3-a456-426614174041",
    namespace: str = "ns-apm-server-01",
    category: WorkloadCategory = WorkloadCategory.MACHINE,
    workload_type: ActivityWorkloadType = ActivityWorkloadType.MACHINE_PC,
    workload_id: str = "123e4567-e89b-12d3-a456-426614174001",
    workload_namespace: str = "ns-apm-server-01",
    workload_name: str = "CORP-PC-001",
    plan_name: str = "Daily Backup",
    started_at: datetime = _DT,
    finished_at: datetime | None = None,
    duration_seconds: int | None = None,
    data_transferred_bytes: int | None = None,
    progress: int = 100,
    log_entries: tuple[ActivityLogEntry, ...] | None = None,
    processed_success_count: int | None = None,
    processed_warning_count: int | None = None,
    processed_error_count: int | None = None,
    status: BackupActivityStatus = BackupActivityStatus.SUCCESS,
    verify_status: VerifyStatus | None = None,
    data_change_bytes: int | None = None,
    data_deduped_bytes: int | None = None,
    backup_scope: BackupScope | None = None,
) -> BackupActivity:
    """Build a BackupActivity with sensible defaults."""
    return BackupActivity(
        activity_id=activity_id,
        execution_id=execution_id,
        namespace=namespace,
        category=category,
        workload_type=workload_type,
        workload_id=workload_id,
        workload_namespace=workload_namespace,
        workload_name=workload_name,
        plan_name=plan_name,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        data_transferred_bytes=data_transferred_bytes,
        progress=progress,
        log_entries=log_entries,
        processed_success_count=processed_success_count,
        processed_warning_count=processed_warning_count,
        processed_error_count=processed_error_count,
        status=status,
        verify_status=verify_status,
        data_change_bytes=data_change_bytes,
        data_deduped_bytes=data_deduped_bytes,
        backup_scope=backup_scope,
    )


def make_restore_activity(
    *,
    activity_id: str = "123e4567-e89b-12d3-a456-426614174050",
    execution_id: str = "123e4567-e89b-12d3-a456-426614174051",
    namespace: str = "ns-apm-server-01",
    category: WorkloadCategory = WorkloadCategory.MACHINE,
    workload_type: ActivityWorkloadType = ActivityWorkloadType.MACHINE_PC,
    workload_id: str = "123e4567-e89b-12d3-a456-426614174001",
    workload_namespace: str = "ns-apm-server-01",
    workload_name: str = "CORP-PC-001",
    plan_name: str = "Daily Backup",
    started_at: datetime = _DT,
    finished_at: datetime | None = None,
    duration_seconds: int | None = None,
    data_transferred_bytes: int | None = None,
    progress: int = 100,
    log_entries: tuple[ActivityLogEntry, ...] | None = None,
    processed_success_count: int | None = None,
    processed_warning_count: int | None = None,
    processed_error_count: int | None = None,
    status: RestoreActivityStatus = RestoreActivityStatus.SUCCESS,
    restore_type: RestoreType | None = None,
    restore_destination: str | None = None,
    operator: str | None = None,
    version_timestamp: datetime | None = None,
    restore_from_info: LocationInfo | None = None,
    destination_path: str | None = None,
    destination_inventory: Hypervisor | None = None,
) -> RestoreActivity:
    """Build a RestoreActivity with sensible defaults."""
    return RestoreActivity(
        activity_id=activity_id,
        execution_id=execution_id,
        namespace=namespace,
        category=category,
        workload_type=workload_type,
        workload_id=workload_id,
        workload_namespace=workload_namespace,
        workload_name=workload_name,
        plan_name=plan_name,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        data_transferred_bytes=data_transferred_bytes,
        progress=progress,
        log_entries=log_entries,
        processed_success_count=processed_success_count,
        processed_warning_count=processed_warning_count,
        processed_error_count=processed_error_count,
        status=status,
        restore_type=restore_type,
        restore_destination=restore_destination,
        operator=operator,
        version_timestamp=version_timestamp,
        restore_from_info=restore_from_info,
        destination_path=destination_path,
        destination_inventory=destination_inventory,
    )


def make_saas_tenant(
    *,
    tenant_id: str = "123e4567-e89b-12d3-a456-426614174060",
    tenant_name: str = "Contoso",
    tenant_email: str = "admin@contoso.com",
    category: WorkloadCategory = WorkloadCategory.M365,
    protected_data_bytes: int = 107_374_182_400,
) -> SaasTenant:
    """Build a SaasTenant with sensible defaults."""
    return SaasTenant(
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        tenant_email=tenant_email,
        category=category,
        protected_data_bytes=protected_data_bytes,
    )


def make_version_location(
    *,
    namespace: str = "ns-apm-server-01",
    location_info: LocationInfo | None = None,
    location_id: str = "123e4567-e89b-12d3-a456-426614174070",
    connection_id: str | None = None,
) -> VersionLocation:
    """Build a VersionLocation with sensible defaults."""
    resolved_location_info = location_info if location_info is not None else make_location_info()
    return VersionLocation(
        namespace=namespace,
        location_info=resolved_location_info,
        location_id=location_id,
        connection_id=connection_id,
    )


def make_workload_version(
    *,
    version_id: str = "123e4567-e89b-12d3-a456-426614174080",
    workload_id: str = "123e4567-e89b-12d3-a456-426614174001",
    namespace: str = "ns-apm-server-01",
    created_at: datetime = _DT,
    status: VersionStatus = VersionStatus.SUCCESS,
    execution_id: str = "123e4567-e89b-12d3-a456-426614174081",
    locked: bool = False,
    changed_size_bytes: int = 1_073_741_824,
    portal_version_id: str = "",
    snapshot_id: str = "",
    verify_status: VerifyStatus | None = None,
    locations: list[VersionLocation] | None = None,
    copy_status: VersionCopyStatus | None = None,
    copy_reason: CopyReason | None = None,
) -> WorkloadVersion:
    """Build a WorkloadVersion with sensible defaults."""
    resolved_locations: list[VersionLocation] = locations if locations is not None else []
    return WorkloadVersion(
        version_id=version_id,
        workload_id=workload_id,
        namespace=namespace,
        created_at=created_at,
        status=status,
        execution_id=execution_id,
        locked=locked,
        changed_size_bytes=changed_size_bytes,
        portal_version_id=portal_version_id,
        snapshot_id=snapshot_id,
        verify_status=verify_status,
        locations=resolved_locations,
        copy_status=copy_status,
        copy_reason=copy_reason,
    )


def make_fake_apm() -> MagicMock:
    """Build a fake APMClient usable as ``async with make_client() as apm``.

    Every list-style collection method is pre-wired as an AsyncMock returning
    ``([], 0)``; tests override ``return_value``/``side_effect`` per case and
    attach further AsyncMocks for any other awaited method they exercise.
    """
    apm = MagicMock(name="fake_apm")
    apm.__aenter__.return_value = apm
    for collection in (
        apm.machine.workloads,
        apm.m365.workloads,
        apm.machine.plans,
        apm.m365.plans,
        apm.plans,
        apm.activities.backup,
        apm.activities.restore,
        apm.backup_servers,
        apm.hypervisors,
        apm.remote_storages,
        apm.retirement_plans,
        apm.tiering_plans,
        apm.saas,
    ):
        collection.list = AsyncMock(return_value=([], 0))
    return apm


def patch_make_client(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    apm: MagicMock,
) -> None:
    """Point a script module's ``make_client`` at a fake APM client."""
    monkeypatch.setattr(module, "make_client", lambda **kwargs: apm)
