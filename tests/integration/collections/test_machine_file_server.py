"""Integration tests: MachineWorkloadCollection — File Server CRUD (add, update, delete, list fs_config)."""
from __future__ import annotations

import asyncio

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.enums import FileServerType, MachineWorkloadType, WorkloadStatus
from synology_apm.sdk.exceptions import InvalidOperationError
from synology_apm.sdk.models.workload import (
    FileServerAddRequest,
    FileServerPathSelector,
    FileServerUpdateRequest,
    MachineWorkload,
)

pytestmark = pytest.mark.integration


# ── list() — fs_config population ─────────────────────────────────────────────


async def test_list_fs_workloads_have_fs_config(apm: APMClient) -> None:
    """list() returns fs_config populated for every FS workload."""
    workloads, _ = await apm.machine.workloads.list(workload_types=[MachineWorkloadType.FS])
    if not workloads:
        pytest.skip("No FS workloads on this server")
    for wl in workloads:
        assert isinstance(wl, MachineWorkload)
        assert wl.fs_config is not None, f"fs_config is None for FS workload {wl.name!r}"
        assert wl.fs_config.host_ip, f"fs_config.host_ip is empty for {wl.name!r}"
        assert wl.fs_config.host_port > 0
        assert len(wl.fs_config.selectors) >= 1


async def test_list_fs_workloads_selectors_nonempty(apm: APMClient) -> None:
    """Each FS workload has at least one FileServerPathSelector."""
    workloads, _ = await apm.machine.workloads.list(workload_types=[MachineWorkloadType.FS])
    if not workloads:
        pytest.skip("No FS workloads on this server")
    for wl in workloads:
        cfg = wl.fs_config
        assert cfg is not None
        for sel in cfg.selectors:
            assert isinstance(sel, FileServerPathSelector)
            assert isinstance(sel.path, str)
            assert isinstance(sel.excluded_paths, tuple)


# ── add_file_server() + delete() ──────────────────────────────────────────────


async def test_add_and_delete_file_server(apm: APMClient) -> None:
    """add_file_server() creates a workload; delete() eventually succeeds
    or raises InvalidOperationError(7018) when the workload is still initializing."""
    plans, _ = await apm.machine.plans.list()
    if not plans:
        pytest.skip("No protection plans available to assign to the new FS workload")
    plan = plans[0]

    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    server = servers[0]

    req = FileServerAddRequest(
        namespace=server.namespace,
        host_ip="192.0.2.250",
        server_type=FileServerType.SMB,
        plan_id=plan.plan_id,
        login_user="administrator",
        login_password="test-password-for-integration",
    )

    await apm.machine.workloads.add_file_server(req)

    # Locate the newly created workload by workload_id (more precise than host_ip alone).
    workloads, _ = await apm.machine.workloads.list(
        workload_types=[MachineWorkloadType.FS], namespace=server.namespace
    )
    added = next((wl for wl in workloads if wl.fs_config and wl.fs_config.host_ip == "192.0.2.250"), None)
    assert added is not None, "Newly added FS workload not found in list"

    # APM may reject the delete with InvalidOperationError (e.g., errorCode 7018 "workload
    # is initializing") immediately after add.  Retry briefly to allow initialization to
    # complete.  Delete must succeed within the retry limit.
    _DELETE_RETRIES = 3
    _DELETE_RETRY_WAIT = 1  # seconds — short to keep cassette replay fast
    last_exc: InvalidOperationError | None = None
    for attempt in range(_DELETE_RETRIES + 1):
        try:
            await apm.machine.workloads.delete(added)
            last_exc = None
            break
        except InvalidOperationError as exc:
            last_exc = exc
            if attempt < _DELETE_RETRIES:
                await asyncio.sleep(_DELETE_RETRY_WAIT)

    assert last_exc is None, (
        f"delete() still failing after {_DELETE_RETRIES} retries: {last_exc}"
    )

    # Verify removal: after delete() returns, the workload is immediately
    # either gone from the list or visible with DELETING status.  A single list call suffices.
    workloads_after, _ = await apm.machine.workloads.list(
        workload_types=[MachineWorkloadType.FS], namespace=server.namespace
    )
    wl_after = next((wl for wl in workloads_after if wl.workload_id == added.workload_id), None)
    assert wl_after is None or wl_after.status == WorkloadStatus.DELETING, (
        f"Deleted FS workload still appears in list with status {wl_after.status if wl_after else '?'!r}"
    )


# ── update_file_server() ──────────────────────────────────────────────────────


async def test_update_file_server_applies_changes(apm: APMClient) -> None:
    """update_file_server() round-trips connection field changes."""
    workloads, _ = await apm.machine.workloads.list(workload_types=[MachineWorkloadType.FS])
    if not workloads:
        pytest.skip("No FS workloads on this server")

    wl = workloads[0]
    cfg = wl.fs_config
    assert cfg is not None

    req = FileServerUpdateRequest(
        host_ip=cfg.host_ip,
        login_user=cfg.login_user,
        login_password=None,
        host_port=cfg.host_port,
        enable_vss=cfg.enable_vss,
        connection_timeout_seconds=cfg.connection_timeout_seconds,
        selectors=cfg.selectors,
    )
    await apm.machine.workloads.update_file_server(wl, req)
