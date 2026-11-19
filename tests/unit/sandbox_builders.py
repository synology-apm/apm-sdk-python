"""Sandbox model builders and IDs shared by the SDK, CLI and MCP unit tests.

Each builder returns a complete, valid value, so a test states only what it cares about.
make_sandbox_resources() takes its locations; the other builders take keyword overrides,
applied with ``dataclasses.replace()``.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

from synology_apm.sdk.enums import (
    ActivityWorkloadType,
    RestoreActivityStatus,
    RestoreType,
    SandboxAdapterModel,
    SandboxDiskController,
    SandboxMachineType,
    SandboxVideoCard,
    WorkloadCategory,
)
from synology_apm.sdk.models.activity import RestoreActivity
from synology_apm.sdk.models.sandbox import (
    SandboxDisk,
    SandboxLocation,
    SandboxNetwork,
    SandboxNetworkAdapter,
    SandboxResources,
    SandboxSettings,
)

WL_ID = "123e4567-e89b-12d3-a456-426614174001"
WL_NS = "123e4567-e89b-12d3-a456-426614174002"
LOC_A = "123e4567-e89b-12d3-a456-426614174010"   # the backup itself (version_id), on apm-server-03
EXEC_ID = "123e4567-e89b-12d3-a456-426614174020"
ACT_NS = "123e4567-e89b-12d3-a456-426614174021"  # apm-server-01, the primary
VM_GUEST = "123e4567-e89b-12d3-a456-426614174030"
CREATED_AT = datetime(2026, 4, 25, 1, 0, 0, tzinfo=UTC)


def make_sandbox_settings(**overrides: Any) -> SandboxSettings:
    """Settings for a two-disk PS backup at LOC_A, one RTL8139 adapter on net-1."""
    base = SandboxSettings(
        location_id=LOC_A,
        name="drill-01",
        cpu_count=4,
        memory_mb=4096,
        cpu_compatibility_mode=False,
        hyperv_enlightenments=True,
        video_card=SandboxVideoCard.VGA,
        machine_type=SandboxMachineType.Q35,
        network_adapters=(SandboxNetworkAdapter("net-1", SandboxAdapterModel.RTL8139, False),),
        disks=(
            SandboxDisk(SandboxDiskController.SATA, False),
            SandboxDisk(SandboxDiskController.SATA, False),
        ),
    )
    return dataclasses.replace(base, **overrides)


def make_sandbox_location(**overrides: Any) -> SandboxLocation:
    """LOC_A on apm-server-03: 8 CPUs, 128-16384 MB, a plain and an SR-IOV network."""
    base = SandboxLocation(
        location_id=LOC_A,
        name="apm-server-03",
        max_cpu_count=8,
        min_memory_mb=128,
        max_memory_mb=16384,
        networks=(
            SandboxNetwork("net-1", "Default VM Network", False),
            SandboxNetwork("net-2", "SR-IOV Network", True),
        ),
    )
    return dataclasses.replace(base, **overrides)


def make_sandbox_resources(*locations: SandboxLocation) -> SandboxResources:
    """Resources with the given locations; ``make_sandbox_location()`` alone by default."""
    return SandboxResources(locations=locations or (make_sandbox_location(),))


def make_sandbox_activity(**overrides: Any) -> RestoreActivity:
    """A ready sandbox of prod-server-01, run by the primary (ACT_NS)."""
    base = RestoreActivity(
        activity_id="rst-uid-001",
        execution_id=EXEC_ID,
        namespace=ACT_NS,
        category=WorkloadCategory.MACHINE,
        workload_type=ActivityWorkloadType.MACHINE_PS,
        workload_id=WL_ID,
        workload_namespace=WL_NS,
        workload_name="prod-server-01",
        plan_name="",
        status=RestoreActivityStatus.SANDBOX_READY,
        started_at=CREATED_AT,
        finished_at=None,
        duration_seconds=None,
        data_transferred_bytes=None,
        progress=0,
        restore_type=RestoreType.INSTANT_SANDBOX,
        restore_destination="drill-01",
        sandbox_vm_id=VM_GUEST,
    )
    return dataclasses.replace(base, **overrides)
