"""Integration tests: MachineSandboxCollection — defaults, resources, and a full sandbox lifecycle.

The lifecycle test starts a real sandbox VM on the recording APM and always cancels it (which
also deletes the VM) in a ``finally`` block. Its cassette holds a single-use console link; it is
harmless once the sandbox is deleted, and cassettes are local-only (never committed). The
screenshot step runs only against the live APM: cassettes hold HTTP exchanges, not the WebSocket
the screen travels over.
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable

import pytest

from synology_apm.sdk import (
    APMClient,
    InvalidOperationError,
    MachineWorkload,
    MachineWorkloadType,
    RestoreActivity,
    RestoreActivityStatus,
    RestoreType,
    SandboxKey,
    WorkloadVersion,
)
from tests.sandbox_checks import assert_defaults_fit, is_restorable_version
from tests.unit.sdk.conftest import assert_resource_error

pytestmark = pytest.mark.integration

_POLL_INTERVAL = 10   # seconds
_READY_TIMEOUT = 180  # seconds


async def _find_ps_vm_version(apm: APMClient) -> tuple[MachineWorkload, WorkloadVersion]:
    """The first active PS/VM workload whose latest sandbox version has a backup-server copy."""
    workloads, _ = await apm.machine.workloads.list(
        workload_types=[MachineWorkloadType.PS, MachineWorkloadType.VM], is_retired=False,
    )
    for wl in workloads:
        try:
            version = await apm.machine.sandboxes.get_latest_version(wl)
        except InvalidOperationError:
            continue
        if is_restorable_version(version):
            return wl, version
    pytest.skip("No active PS/VM workload with a restorable backup-server version")


async def _find_sandbox(apm: APMClient, wl: MachineWorkload, name: str) -> RestoreActivity:
    """The workload's sandbox restore activity whose destination is the sandbox's name."""
    sandboxes, _ = await apm.activities.restore.list(workload=wl, restore_types=[RestoreType.INSTANT_SANDBOX])
    found = next((a for a in sandboxes if a.restore_destination == name), None)
    assert found is not None, [(a.restore_destination, a.status) for a in sandboxes]
    return found


async def _poll(
    apm: APMClient, activity_id: str, done: Callable[[RestoreActivity], bool], live: bool,
) -> RestoreActivity:
    """Poll the restore activity until done() or _READY_TIMEOUT; sleeps only against the live APM."""
    waited = 0
    while True:
        act = await apm.activities.restore.get(activity_id)
        if done(act) or waited >= _READY_TIMEOUT:
            return act
        if live:
            await asyncio.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL


async def test_default_settings_fit_default_location_resources(apm: APMClient) -> None:
    wl, version = await _find_ps_vm_version(apm)

    resources = await apm.machine.sandboxes.get_resources(wl, version)
    settings = await apm.machine.sandboxes.get_default_settings(wl, version)

    assert_defaults_fit(resources, settings)


@pytest.mark.apm_timeout(90)
async def test_sandbox_lifecycle(apm: APMClient, live: bool) -> None:
    wl, version = await _find_ps_vm_version(apm)
    settings = await apm.machine.sandboxes.get_default_settings(wl, version)
    settings = dataclasses.replace(settings, memory_mb=min(2048, settings.memory_mb))
    await apm.machine.sandboxes.validate_settings(wl, version, settings)

    await apm.machine.sandboxes.start(wl, version, settings)
    try:
        started = await _find_sandbox(apm, wl, settings.name)
        assert (started.workload_id, started.workload_namespace) == (wl.workload_id, wl.namespace)

        act = await _poll(
            apm, started.activity_id,
            lambda a: a.status not in (RestoreActivityStatus.PREPARING, RestoreActivityStatus.RESTORING),
            live,
        )
        assert act.status == RestoreActivityStatus.SANDBOX_READY, act.log_entries
        assert act.sandbox_vm_id is not None

        url = await apm.machine.sandboxes.get_console_link(act)
        assert url.startswith("https://") and "/sharing/" in url

        if live:
            png = await apm.machine.sandboxes.get_screenshot(act, keys=(SandboxKey.SHIFT,), delay_seconds=1)
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        # Cancel whatever start() created, even when an earlier step failed.
        await apm.activities.restore.cancel(await _find_sandbox(apm, wl, settings.name))

    canceled = await _poll(apm, started.activity_id, lambda a: a.status == RestoreActivityStatus.CANCELED, live)
    assert canceled.status == RestoreActivityStatus.CANCELED

    with pytest.raises(InvalidOperationError) as exc_info:
        await apm.machine.sandboxes.get_console_link(canceled)
    assert_resource_error(exc_info, resource_type="RestoreActivity", resource_id=canceled.activity_id)
