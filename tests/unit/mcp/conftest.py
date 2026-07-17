"""Shared fixtures for MCP unit tests."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from synology_apm.sdk import (
    ActivityWorkloadType,
    BackupActivity,
    BackupActivityStatus,
    BackupServer,
    BackupServerRole,
    BackupServerType,
    Hypervisor,
    HypervisorType,
    LocationInfo,
    M365ExportActivity,
    M365ExportStatus,
    M365UserInfo,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    MachineWorkloadType,
    ProtectionPlan,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorage,
    RemoteStorageStatus,
    RemoteStorageType,
    RestoreActivity,
    RestoreActivityStatus,
    RetentionType,
    RetirementPlan,
    SaasTenant,
    ScheduleFrequency,
    ServerStatus,
    TieringPlan,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
    WorkloadVersion,
)


@pytest.fixture
def mock_apm():
    """Return a deeply-mocked APMClient."""
    apm = MagicMock()

    # Top-level methods
    apm.get_site_info = AsyncMock()

    # Sub-collections
    apm.backup_servers.list = AsyncMock()
    apm.backup_servers.get = AsyncMock()
    apm.backup_servers.get_by_name = AsyncMock()
    apm.backup_servers.change_tiering_plan = AsyncMock()

    apm.remote_storages.list = AsyncMock()
    apm.remote_storages.get = AsyncMock()
    apm.remote_storages.get_by_name = AsyncMock()
    apm.remote_storages.add = AsyncMock()
    apm.remote_storages.update = AsyncMock()
    apm.remote_storages.delete = AsyncMock()

    apm.hypervisors.list = AsyncMock()
    apm.hypervisors.get = AsyncMock()
    apm.hypervisors.get_by_name = AsyncMock()

    apm.machine.workloads.list = AsyncMock()
    apm.machine.workloads.get = AsyncMock()
    apm.machine.workloads.get_by_name = AsyncMock()
    apm.machine.workloads.backup_now = AsyncMock()
    apm.machine.workloads.cancel_backup = AsyncMock()
    apm.machine.workloads.retire = AsyncMock()
    apm.machine.workloads.delete = AsyncMock()
    apm.machine.workloads.change_plan = AsyncMock()
    apm.machine.workloads.add_file_server = AsyncMock()
    apm.machine.workloads.update_file_server = AsyncMock()
    apm.machine.workloads.list_versions = AsyncMock()
    apm.machine.workloads.get_version = AsyncMock()
    apm.machine.workloads.get_latest_version = AsyncMock()
    apm.machine.workloads.lock_version = AsyncMock()
    apm.machine.workloads.unlock_version = AsyncMock()
    apm.machine.workloads.get_verification_video_url = AsyncMock()

    apm.machine.plans.list = AsyncMock()
    apm.machine.plans.get = AsyncMock()
    apm.machine.plans.get_by_name = AsyncMock()
    apm.machine.plans.create = AsyncMock()
    apm.machine.plans.update = AsyncMock()
    apm.machine.plans.delete = AsyncMock()

    apm.m365.workloads.list = AsyncMock()
    apm.m365.workloads.get = AsyncMock()
    apm.m365.workloads.get_by_name = AsyncMock()
    apm.m365.workloads.backup_now = AsyncMock()
    apm.m365.workloads.cancel_backup = AsyncMock()
    apm.m365.workloads.retire = AsyncMock()
    apm.m365.workloads.delete = AsyncMock()
    apm.m365.workloads.change_plan = AsyncMock()
    apm.m365.workloads.list_versions = AsyncMock()
    apm.m365.workloads.get_version = AsyncMock()
    apm.m365.workloads.get_latest_version = AsyncMock()
    apm.m365.workloads.lock_version = AsyncMock()
    apm.m365.workloads.unlock_version = AsyncMock()

    apm.m365.plans.list = AsyncMock()
    apm.m365.plans.get = AsyncMock()
    apm.m365.plans.get_by_name = AsyncMock()
    apm.m365.plans.create = AsyncMock()
    apm.m365.plans.update = AsyncMock()
    apm.m365.plans.delete = AsyncMock()

    apm.m365.auto_backup_rules.list = AsyncMock()
    apm.m365.auto_backup_rules.create = AsyncMock()
    apm.m365.auto_backup_rules.update = AsyncMock()
    apm.m365.auto_backup_rules.delete = AsyncMock()
    apm.m365.auto_backup_rules.update_collab_settings = AsyncMock()

    apm.m365.exchange_export.list = AsyncMock()
    apm.m365.exchange_export.start = AsyncMock()
    apm.m365.exchange_export.cancel = AsyncMock()
    apm.m365.exchange_export.get_download_url_by_activity = AsyncMock()

    apm.m365.group_export.list = AsyncMock()
    apm.m365.group_export.start = AsyncMock()
    apm.m365.group_export.cancel = AsyncMock()
    apm.m365.group_export.get_download_url_by_activity = AsyncMock()

    apm.plans.list = AsyncMock()
    apm.plans.get = AsyncMock()
    apm.plans.get_by_name = AsyncMock()
    apm.plans.create = AsyncMock()
    apm.plans.delete = AsyncMock()

    apm.retirement_plans.list = AsyncMock()
    apm.retirement_plans.get = AsyncMock()
    apm.retirement_plans.get_by_name = AsyncMock()
    apm.retirement_plans.create = AsyncMock()
    apm.retirement_plans.update = AsyncMock()
    apm.retirement_plans.delete = AsyncMock()

    apm.tiering_plans.list = AsyncMock()
    apm.tiering_plans.get = AsyncMock()
    apm.tiering_plans.get_by_name = AsyncMock()
    apm.tiering_plans.create = AsyncMock()
    apm.tiering_plans.update = AsyncMock()
    apm.tiering_plans.delete = AsyncMock()

    apm.activities.backup.list = AsyncMock()
    apm.activities.backup.get = AsyncMock()
    apm.activities.backup.cancel = AsyncMock()

    apm.activities.restore.list = AsyncMock()
    apm.activities.restore.get = AsyncMock()
    apm.activities.restore.cancel = AsyncMock()

    apm.saas.list = AsyncMock()
    apm.saas.get_m365_tenant = AsyncMock()

    apm.logs.list_activity = AsyncMock()
    apm.logs.list_drive = AsyncMock()
    apm.logs.list_connection = AsyncMock()
    apm.logs.list_system = AsyncMock()

    return apm


@pytest.fixture
def mock_ctx(mock_apm):
    """Return a mocked FastMCP Context with lifespan_context["apm"] set."""
    ctx = MagicMock()
    ctx.lifespan_context = {"apm": mock_apm}
    return ctx


@pytest.fixture(scope="session")
def admin_server():
    """Return a FastMCP server with every tool registered (mode="admin").

    Session-scoped: registration depends only on mode, not on any per-test mock
    (the APM client is looked up from ctx.lifespan_context at call time, not baked
    in at registration), so rebuilding it per test would just be wasted work across
    ~200+ tests.
    """
    from synology_apm.mcp._server import create_server

    return create_server(mode="admin")


@pytest.fixture
def resource_server(mock_apm):
    """Return a FastMCP server with only resources registered, plus a real
    lifespan yielding mock_apm — required because FastMCP resolves a resource's
    ctx via contextvar-based dependency injection rather than accepting it as an
    overridable argument (unlike tools), so exercising real resource wiring needs
    an in-memory fastmcp.Client session rather than a direct .fn() call.
    """
    from synology_apm.mcp import resources

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        yield {"apm": mock_apm}

    server = FastMCP("synology-apm", lifespan=lifespan)
    resources.register(server)
    return server


async def call_tool(server: FastMCP, tool_name: str, ctx: Any, **kwargs: Any) -> str:
    """Invoke a registered tool by name through its real FastMCP wiring."""
    tool = await server.get_tool(tool_name)
    assert tool is not None, f"tool {tool_name!r} is not registered"
    return await tool.fn(ctx=ctx, **kwargs)  # type: ignore[attr-defined]


async def assert_destructive_preview_then_execute(
    server: FastMCP,
    ctx: Any,
    tool_name: str,
    tool_kwargs: dict[str, Any],
    execute_mock: AsyncMock,
    expected_target: dict[str, Any] | None = None,
) -> None:
    """Call a destructive tool (confirm=False then confirm=True) through its real
    wiring, asserting the preview-then-execute contract shared by every
    delete/retire tool: no execution without confirmation, exactly one execution
    once confirmed."""
    preview_raw = await call_tool(server, tool_name, ctx, confirm=False, **tool_kwargs)
    preview = json.loads(preview_raw)
    assert preview["preview"] is True
    if expected_target is not None:
        assert preview["target"] == expected_target
    execute_mock.assert_not_called()

    await call_tool(server, tool_name, ctx, confirm=True, **tool_kwargs)
    execute_mock.assert_called_once()


# ── Model factory helpers ─────────────────────────────────────────────────────

def make_backup_server(**kwargs) -> BackupServer:
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
        description="",
        tiering_plan_name=None,
        tiering_plan_destination=None,
        tiering_status=None,
    )
    defaults.update(kwargs)
    return BackupServer(**defaults)


def make_hypervisor(**kwargs) -> Hypervisor:
    defaults: dict[str, Any] = dict(
        hypervisor_id="hyp-001",
        hostname="esxi1.example.com",
        address="192.0.2.40",
        host_type=HypervisorType.VSPHERE_ESXI,
        account="root",
        description="",
        port=443,
        version="7.0.0",
    )
    defaults.update(kwargs)
    return Hypervisor(**defaults)


def make_remote_storage(**kwargs) -> RemoteStorage:
    defaults: dict[str, Any] = dict(
        storage_id="stor-001",
        name="DSM-Storage",
        storage_type=RemoteStorageType.S3_COMPATIBLE,
        device_model="DS720+",
        endpoint="192.0.2.20:8444",
        status=RemoteStorageStatus.CONNECTED,
        used_bytes=1_000_000_000,
        remaining_bytes=9_000_000_000,
        encryption_enabled=False,
        vault_name="MyVault",
    )
    defaults.update(kwargs)
    return RemoteStorage(**defaults)


def make_protection_plan(**kwargs) -> ProtectionPlan:
    from synology_apm.sdk import ProtectionPlanPolicy

    defaults: dict[str, Any] = dict(
        plan_id="plan-001",
        name="Daily Backup",
        category=WorkloadCategory.MACHINE,
        description="",
        workload_count=5,
        successful_workload_count=4,
        unsuccessful_workload_count=1,
        is_immutable=False,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(
                retention_type=RetentionType.KEEP_DAYS,
                days=30,
            ),
            schedule=ProtectionSchedule(
                frequency=ScheduleFrequency.DAILY,
                start_time=None,
            ),
        ),
        backup_copy_policy=None,
        backup_copy_status=None,
        run_schedule_by_controller_time=False,
        vm_config=None,
        pc_config=None,
        ps_config=None,
        db_config=None,
        backup_window=None,
        tasks=None,
    )
    defaults.update(kwargs)
    return ProtectionPlan(**defaults)


def make_retirement_plan(**kwargs) -> RetirementPlan:
    from synology_apm.sdk import RetirementRetentionPolicy

    defaults: dict[str, Any] = dict(
        plan_id="ret-001",
        name="Compliance Retention",
        description="",
        retention=RetirementRetentionPolicy(days=365, keep_latest_version=True),
        workload_count=2,
        run_schedule_by_controller_time=False,
    )
    defaults.update(kwargs)
    return RetirementPlan(**defaults)


def make_saas_tenant(**kwargs) -> SaasTenant:
    defaults: dict[str, Any] = dict(
        tenant_id="tenant-001",
        tenant_name="Contoso",
        tenant_email="admin@contoso.com",
        category=WorkloadCategory.M365,
        protected_data_bytes=5_000_000_000,
    )
    defaults.update(kwargs)
    return SaasTenant(**defaults)


def make_machine_workload(**kwargs) -> MachineWorkload:
    from datetime import UTC, datetime

    defaults: dict[str, Any] = dict(
        workload_id="123e4567-e89b-12d3-a456-426614174001",
        name="vm-web-01",
        category=WorkloadCategory.MACHINE,
        workload_type=MachineWorkloadType.VM,
        namespace="default",
        last_backup_at=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        is_retired=False,
        protected_data_bytes=50_000_000_000,
        status=WorkloadStatus.SUCCESS,
        plan=make_protection_plan(),
        backup_progress=None,
        items_backed_up=None,
        backup_server=LocationInfo(is_remote_storage=False, identifier="srv-001", name="apm-server-01", endpoint="192.0.2.1", vault=None),
        backup_copy_destination=None,
        backup_copy_data_bytes=0,
        agent_version="1.2.0",
        verify_status=None,
        device_uuid=None,
        ip_address="192.0.2.100",
        inventory_name="vm-web-01",
        inventory_type="vm",
        fs_config=None,
    )
    defaults.update(kwargs)
    return MachineWorkload(**defaults)


def make_workload_version(**kwargs) -> WorkloadVersion:
    from datetime import UTC, datetime
    defaults: dict[str, Any] = dict(
        version_id="ver-001",
        workload_id="123e4567-e89b-12d3-a456-426614174001",
        namespace="default",
        created_at=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        status=VersionStatus.SUCCESS,
        execution_id="exec-001",
        locked=False,
        changed_size_bytes=1_000_000,
    )
    defaults.update(kwargs)
    return WorkloadVersion(**defaults)


def make_backup_activity(**kwargs) -> BackupActivity:
    from datetime import UTC, datetime
    defaults: dict[str, Any] = dict(
        activity_id="act-001",
        execution_id="exec-001",
        namespace="default",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_VM,
        workload_id="123e4567-e89b-12d3-a456-426614174001",
        workload_namespace="default",
        workload_name="vm-web-01",
        plan_name="Daily Backup",
        started_at=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 14, 2, 30, tzinfo=UTC),
        duration_seconds=1800,
        data_transferred_bytes=1_000_000,
        progress=100,
        status=BackupActivityStatus.SUCCESS,
    )
    defaults.update(kwargs)
    return BackupActivity(**defaults)


def make_restore_activity(**kwargs) -> RestoreActivity:
    from datetime import UTC, datetime
    defaults: dict[str, Any] = dict(
        activity_id="rst-001",
        execution_id="exec-rst-001",
        namespace="default",
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_VM,
        workload_id="123e4567-e89b-12d3-a456-426614174001",
        workload_namespace="default",
        workload_name="vm-web-01",
        plan_name="Daily Backup",
        started_at=datetime(2026, 7, 14, 3, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 14, 3, 15, tzinfo=UTC),
        duration_seconds=900,
        data_transferred_bytes=500_000,
        progress=100,
        status=RestoreActivityStatus.SUCCESS,
    )
    defaults.update(kwargs)
    return RestoreActivity(**defaults)


def make_tiering_plan(**kwargs) -> TieringPlan:
    from datetime import time
    defaults: dict[str, Any] = dict(
        plan_id="tier-001",
        name="30-Day Tiering",
        description="",
        tiering_after_days=30,
        daily_check_time=time(2, 0),
        destination=None,
        server_count=2,
        tiering_status=None,
        run_schedule_by_controller_time=False,
    )
    defaults.update(kwargs)
    return TieringPlan(**defaults)


def make_export_activity(**kwargs) -> M365ExportActivity:
    defaults: dict[str, Any] = dict(
        activity_id="exp-001",
        execution_id="exec-exp-001",
        namespace="default",
        workload_id="wl-001",
        workload_namespace="default",
        source_name="alice@contoso.com",
        is_archive_mail=False,
        status=M365ExportStatus.READY_TO_DOWNLOAD,
        started_at=None,
        finished_at=None,
        version_timestamp=None,
    )
    defaults.update(kwargs)
    return M365ExportActivity(**defaults)


def make_m365_workload(**kwargs) -> M365Workload:
    from datetime import UTC, datetime
    defaults: dict[str, Any] = dict(
        workload_id="123e4567-e89b-12d3-a456-426614174002",
        name="alice@contoso.com",
        category=WorkloadCategory.M365,
        workload_type=M365WorkloadType.EXCHANGE,
        namespace="default",
        tenant_id="tenant-001",
        last_backup_at=datetime(2026, 7, 14, 3, 0, tzinfo=UTC),
        is_retired=False,
        protected_data_bytes=500_000_000,
        status=WorkloadStatus.SUCCESS,
        plan=make_protection_plan(category=WorkloadCategory.M365),
        backup_progress=None,
        backup_server=LocationInfo(
            is_remote_storage=False, identifier="srv-001",
            name="apm-server-01", endpoint="192.0.2.1", vault=None,
        ),
        backup_copy_destination=None,
        backup_copy_data_bytes=0,
        info=M365UserInfo(user_principal_name="alice@contoso.com"),
    )
    defaults.update(kwargs)
    return M365Workload(**defaults)
