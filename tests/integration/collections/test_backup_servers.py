"""Integration tests: BackupServerCollection"""
from __future__ import annotations

import pytest

from synology_apm.sdk import APMClient, BackupServer, BackupServerType
from synology_apm.sdk.exceptions import APIError, ResourceNotFoundError
from synology_apm.sdk.models.workload import Workload

pytestmark = pytest.mark.integration


# ── list() ─────────────────────────────────────────────────────────────────


async def test_list_returns_list(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    assert isinstance(servers, list)


async def test_list_items_are_backup_server_instances(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    for server in servers:
        assert isinstance(server, BackupServer)


async def test_list_backup_server_ids_are_nonempty(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    for server in servers:
        assert server.backup_server_id, f"backup_server_id empty for server {server.name!r}"


async def test_list_backup_server_names_are_nonempty(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    for server in servers:
        assert server.name, f"name is empty for backup_server_id={server.backup_server_id}"


async def test_list_storage_fields_are_non_negative(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    for server in servers:
        assert server.storage_total_bytes is None or server.storage_total_bytes >= 0
        assert server.storage_used_bytes is None or server.storage_used_bytes >= 0


# ── get() ──────────────────────────────────────────────────────────────────


async def test_get_returns_backup_server_by_id(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    backup_server_id = servers[0].backup_server_id
    fetched = await apm.backup_servers.get(backup_server_id)
    assert fetched.backup_server_id == backup_server_id


async def test_get_raises_error_for_bad_id(apm: APMClient) -> None:
    # APM returns 500 (not 404) for unknown backup server IDs — accept either error
    with pytest.raises((ResourceNotFoundError, APIError)):
        await apm.backup_servers.get("00000000-0000-0000-0000-000000000000")


# ── namespace-filtered workloads ────────────────────────────────────────────


async def test_machine_workloads_namespace_filter_returns_matching_server(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    server = servers[0]
    workloads, _ = await apm.machine.workloads.list(namespace=server.namespace)
    assert isinstance(workloads, list)
    for wl in workloads:
        assert isinstance(wl, Workload)
        assert wl.namespace == server.namespace


# ── namespace-filtered activities ────────────────────────────────────────────


async def test_backup_activities_namespace_filter_returns_list(apm: APMClient) -> None:
    servers, _ = await apm.backup_servers.list()
    if not servers:
        pytest.skip("No backup servers available")
    server = servers[0]
    acts, _ = await apm.activities.backup.list(namespace=[server.namespace])
    assert isinstance(acts, list)


# ── change_tiering_plan() ──────────────────────────────────────────────────


async def test_change_tiering_plan_remove_then_restore(apm: APMClient) -> None:
    """Scenario A: server with an existing tiering plan — remove it then restore."""
    servers, _ = await apm.backup_servers.list()
    dp_with_plan = next(
        (s for s in servers if s.server_type == BackupServerType.DP and s.tiering_plan_name),
        None,
    )
    if dp_with_plan is None:
        pytest.skip("No DP server with a tiering plan assigned")
    original_plan_name = dp_with_plan.tiering_plan_name
    assert original_plan_name is not None

    await apm.backup_servers.change_tiering_plan(dp_with_plan, None)
    original_plan = await apm.tiering_plans.get_by_name(original_plan_name)
    await apm.backup_servers.change_tiering_plan(dp_with_plan, original_plan)


async def test_change_tiering_plan_apply_then_remove(apm: APMClient) -> None:
    """Scenario B: server with no tiering plan — apply one then remove to restore."""
    servers, _ = await apm.backup_servers.list()
    dp_without_plan = next(
        (s for s in servers if s.server_type == BackupServerType.DP and not s.tiering_plan_name),
        None,
    )
    if dp_without_plan is None:
        pytest.skip("No DP server without a tiering plan")
    tiering_plans, _ = await apm.tiering_plans.list()
    if not tiering_plans:
        pytest.skip("No tiering plans available to apply")

    await apm.backup_servers.change_tiering_plan(dp_without_plan, tiering_plans[0])
    await apm.backup_servers.change_tiering_plan(dp_without_plan, None)
