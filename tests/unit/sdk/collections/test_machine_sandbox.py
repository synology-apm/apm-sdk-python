"""Unit tests for MachineSandboxCollection (instant restore to the built-in sandbox)."""
from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterator
from typing import Any

import pytest

from synology_apm.sdk import APMClient
from synology_apm.sdk.collections.machine_sandbox import MachineSandboxCollection
from synology_apm.sdk.enums import (
    MachineWorkloadType,
    SandboxAdapterModel,
    SandboxDiskController,
    SandboxMachineType,
    SandboxVideoCard,
    VersionStatus,
)
from synology_apm.sdk.exceptions import APIError, InvalidOperationError, SandboxSettingsError
from synology_apm.sdk.models.sandbox import (
    SandboxDisk,
    SandboxLocation,
    SandboxNetwork,
    SandboxNetworkAdapter,
    SandboxSettings,
)
from synology_apm.sdk.models.version import WorkloadVersion
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.sandbox_builders import CREATED_AT, EXEC_ID, LOC_A, WL_ID, WL_NS, make_sandbox_settings
from tests.unit.sdk.collections._sandbox_fixtures import (
    ERR_NOT_ENOUGH_MEMORY,
    ERR_SYSTEM,
    GIB,
    LOC_B,
    LOC_R,
    LOCATION_B,
    LOCATION_R,
    MACS,
    NS_A,
    NS_B,
    PS_SPEC,
    PS_VDISKS,
    REPOS,
    RESOURCE,
    VM_SPEC,
    Env,
    FakeVmm,
    make_location,
    make_sandboxes,
    make_version,
    make_workload,
)
from tests.unit.sdk.conftest import assert_resource_error, make_session, patched_session


@pytest.fixture
def taipei_tz(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the local time zone (UTC+8, no DST) so the default name is deterministic."""
    tzset = getattr(time, "tzset", None)  # POSIX only
    if tzset is None:
        pytest.skip("time.tzset() is not available on this platform")
    monkeypatch.setenv("TZ", "Asia/Taipei")
    tzset()
    yield
    monkeypatch.undo()
    tzset()


def _open_in_vm_body(env: Env) -> dict[str, Any]:
    body: dict[str, Any] = env.vmm.one("open_in_vm").body
    return body


# ── Facade ──────────────────────────────────────────────────────────────────


def test_client_machine_sandboxes_is_sandbox_collection() -> None:
    client = APMClient("apm.corp.com", "admin", "secret")
    assert isinstance(client.machine.sandboxes, MachineSandboxCollection)


# ── start(): request contract ────────────────────────────────────────────────


async def test_start_ps_sends_full_open_in_vm_body(env: Env) -> None:
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings())

    assert _open_in_vm_body(env) == {
        "useOvmf": True,
        "vnics": [{
            "preferSriov": False, "vnicType": 4, "type": "add", "mac": MACS[0], "networkId": "net-1",
        }],
        "increaseAllocatedSize": str(64 * GIB + 10 * GIB + 1),
        "autoSwitch": 0,
        "vdisks": [
            {"type": "add", "vdiskMode": 16, "name": "Virtual disk 1", "unmap": False, "iopsEnable": False,
             "devLimit": "0", "devReservation": "0", "devWeight": "3", "vdiskSize": "64", "idx": "0"},
            {"type": "add", "vdiskMode": 16, "name": "Virtual disk 2", "unmap": False, "iopsEnable": False,
             "devLimit": "0", "devReservation": "0", "devWeight": "3", "vdiskSize": "11", "idx": "1"},
        ],
        "vdiskStruct": PS_VDISKS,
        "name": "drill-01",
        "vcpuNum": "4",
        "vramSize": "4096",
        "videoCard": "vga",
        "cpuWeight": "256",
        "machineType": "q35",
        "cpuPassthru": True,
        "hypervEnlighten": True,
        "cpuPinNum": "0",
        "birthInfo": {"desc": EXEC_ID, "source": "backup"},
        "repoId": "repo-a",
        "repoName": "Sandbox-A",
        "hostId": "host-a",
        "repoHostName": "apm-server-03",
        "allocatedSize": "10",
        "size": "2000",
        "versionId": LOC_A,
        "versionTimestamp": str(int(CREATED_AT.timestamp())),
        "workloadType": "MACHINE_PS",
        "workload": {"uid": WL_ID, "namespace": WL_NS},
        "tunnelNamespace": NS_A,
    }


async def test_start_vm_sends_vm_type_disk_and_spec_fields() -> None:
    session = make_session()
    vmm = FakeVmm(spec=VM_SPEC)
    settings = make_sandbox_settings(
        machine_type=SandboxMachineType.PC,
        video_card=SandboxVideoCard.CIRRUS,
        cpu_count=2,
        memory_mb=2048,
        disks=(SandboxDisk(SandboxDiskController.IDE, True),),
    )
    with patched_session(session, get=vmm.get, post=vmm.post):
        await make_sandboxes(session).start(make_workload(MachineWorkloadType.VM), make_version(), settings)

    body = vmm.one("open_in_vm").body
    assert body["workloadType"] == "MACHINE_VM"
    assert body["useOvmf"] is False
    assert body["machineType"] == "pc"
    assert body["videoCard"] == "cirrus"
    assert body["vdiskStruct"] == VM_SPEC["vdiskStruct"]
    assert body["increaseAllocatedSize"] == str(16 * GIB)
    assert body["vdisks"] == [
        {"type": "add", "vdiskMode": 2, "name": "Virtual disk 1", "unmap": True, "iopsEnable": False,
         "devLimit": "0", "devReservation": "0", "devWeight": "3", "vdiskSize": "16", "idx": "0"},
    ]


async def test_start_request_sequence_paths_headers_and_workload(env: Env) -> None:
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings())

    ops = env.vmm.ops()
    assert sorted(ops[:4]) == ["list_basic", "list_repo", "list_resource", "list_spec"]
    assert ops[4:] == ["gen_mac", "open_in_vm"]
    assert "check_availability" not in env.vmm.ops()
    for call in env.vmm.calls:
        assert call.headers == {"x-syno-tunnel-route": NS_A}, call.op
    assert env.vmm.one("list_spec").body == {
        "versionIds": [LOC_A], "workloads": [{"uid": WL_ID, "namespace": WL_NS}],
    }
    assert env.vmm.one("list_repo").body == {"isEdit": False}
    wl_params = [("workload.uid", WL_ID), ("workload.namespace", WL_NS)]
    for op in ("list_resource", "list_basic", "gen_mac"):
        assert env.vmm.one(op).body == wl_params, op


async def test_start_at_non_default_location_uses_its_namespace_and_id(env: Env) -> None:
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(location_id=LOC_B))

    for call in env.vmm.calls:
        assert call.headers == {"x-syno-tunnel-route": NS_B}, call.op
    assert env.vmm.one("list_spec").body["versionIds"] == [LOC_B]
    body = _open_in_vm_body(env)
    assert body["versionId"] == LOC_B
    assert body["tunnelNamespace"] == NS_B


async def test_start_validates_against_chosen_location_limits(env: Env) -> None:
    env.vmm.respond("list_resource", {**RESOURCE, "maxCpuNum": 2}, namespace=NS_B)
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(cpu_count=4))  # fine at LOC_A
    with pytest.raises(SandboxSettingsError, match="cpu_count"):
        await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(location_id=LOC_B, cpu_count=4))


async def test_start_two_adapters_generate_one_mac_each_in_order(env: Env) -> None:
    adapters = (
        SandboxNetworkAdapter("net-2", SandboxAdapterModel.VIRTIO, True),
        SandboxNetworkAdapter("net-1", SandboxAdapterModel.E1000, False),
    )
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(network_adapters=adapters))

    assert env.vmm.ops().count("gen_mac") == 2
    assert _open_in_vm_body(env)["vnics"] == [
        {"preferSriov": True, "vnicType": 1, "type": "add", "mac": MACS[0], "networkId": "net-2"},
        {"preferSriov": False, "vnicType": 2, "type": "add", "mac": MACS[1], "networkId": "net-1"},
    ]


async def test_start_issues_lookups_and_mac_requests_concurrently(env: Env) -> None:
    adapters = (
        SandboxNetworkAdapter("net-1", SandboxAdapterModel.E1000, False),
        SandboxNetworkAdapter("net-1", SandboxAdapterModel.VIRTIO, False),
    )
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(network_adapters=adapters))

    assert env.vmm.max_in_flight == {"load": 4, "gen_mac": 2}


@pytest.mark.parametrize("responses", [
    [{"mac": "02:11:32:aa:bb:01"}, {"mac": "02:11:32:aa:bb:01"}],
    [{"mac": "02:11:32:AA:BB:01"}, {"mac": "02:11:32:aa:bb:01"}],
    [{"mac": "02:11:32:aa:bb:01"}, {}],
    [{"mac": "02:11:32:aa:bb:01"}, {"mac": ""}],
], ids=["duplicate", "duplicate_ignoring_case", "missing", "empty"])
async def test_start_without_distinct_macs_creates_nothing(env: Env, responses: list[dict[str, Any]]) -> None:
    env.vmm.macs = iter(responses)
    adapters = (
        SandboxNetworkAdapter("net-1", SandboxAdapterModel.E1000, False),
        SandboxNetworkAdapter("net-2", SandboxAdapterModel.VIRTIO, False),
    )
    with pytest.raises(APIError, match="MAC address"):
        await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(network_adapters=adapters))

    assert "open_in_vm" not in env.vmm.ops()


async def test_start_without_adapters_sends_no_vnics_and_generates_no_mac(env: Env) -> None:
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(network_adapters=()))

    assert "gen_mac" not in env.vmm.ops()
    assert _open_in_vm_body(env)["vnics"] == []


@pytest.mark.parametrize("controller,vdisk_mode", [
    (SandboxDiskController.SCSI, 1),
    (SandboxDiskController.SATA, 16),
])
async def test_start_disk_controller_and_space_reclamation(
    env: Env, controller: SandboxDiskController, vdisk_mode: int,
) -> None:
    disks = (SandboxDisk(controller, True), SandboxDisk(SandboxDiskController.SATA, False))
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(disks=disks))
    vdisks = _open_in_vm_body(env)["vdisks"]
    assert [(d["vdiskMode"], d["unmap"]) for d in vdisks] == [(vdisk_mode, True), (16, False)]


async def test_start_ide_controller_sent_with_machine_type_pc(env: Env) -> None:
    disks = (SandboxDisk(SandboxDiskController.IDE, False),) * 2
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(machine_type=SandboxMachineType.PC, disks=disks))
    body = _open_in_vm_body(env)
    assert body["machineType"] == "pc"
    assert [d["vdiskMode"] for d in body["vdisks"]] == [2, 2]


@pytest.mark.parametrize("model,vnic_type", [
    (SandboxAdapterModel.VIRTIO, 1),
    (SandboxAdapterModel.E1000, 2),
    (SandboxAdapterModel.RTL8139, 4),
])
async def test_start_adapter_model(env: Env, model: SandboxAdapterModel, vnic_type: int) -> None:
    adapters = (SandboxNetworkAdapter("net-1", model, False),)
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(network_adapters=adapters))
    assert _open_in_vm_body(env)["vnics"][0]["vnicType"] == vnic_type


@pytest.mark.parametrize("video_card", list(SandboxVideoCard))
async def test_start_video_card(env: Env, video_card: SandboxVideoCard) -> None:
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(video_card=video_card))
    assert _open_in_vm_body(env)["videoCard"] == video_card.value


@pytest.mark.parametrize("compat,hyperv,cpu_passthru", [
    (False, True, True),
    (False, False, True),
    (True, False, False),
])
async def test_start_cpu_compatibility_mode_and_hyperv(env: Env, compat: bool, hyperv: bool, cpu_passthru: bool) -> None:
    await env.sandboxes.start(
        make_workload(), make_version(), make_sandbox_settings(cpu_compatibility_mode=compat, hyperv_enlightenments=hyperv),
    )
    body = _open_in_vm_body(env)
    assert body["cpuPassthru"] is cpu_passthru
    assert body["hypervEnlighten"] is hyperv


async def test_start_stores_sandbox_on_first_repo_by_name_on_any_volume(env: Env) -> None:
    env.vmm.responses["list_repo"] = {"repos": [REPOS[0], {**REPOS[2], "name": "Archive"}]}
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings())
    body = _open_in_vm_body(env)
    assert (body["repoId"], body["repoName"], body["hostId"], body["repoHostName"], body["allocatedSize"], body["size"]) == (
        "repo-x", "Archive", "host-x", "apm-server-03", "0", "9000",
    )


# ── start(): validation (SandboxSettingsError before anything is created) ────


def _assert_nothing_created(env: Env) -> None:
    assert "gen_mac" not in env.vmm.ops()
    assert "open_in_vm" not in env.vmm.ops()


@pytest.mark.parametrize("overrides", [
    {"cpu_count": 1}, {"cpu_count": 8}, {"memory_mb": 128}, {"memory_mb": 16384}, {"name": "n" * 80},
], ids=["cpu_min", "cpu_max", "memory_min", "memory_max", "name_80"])
async def test_start_accepts_boundary_values(env: Env, overrides: dict[str, Any]) -> None:
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(**overrides))
    assert "open_in_vm" in env.vmm.ops()


@pytest.mark.parametrize("overrides,field,match", [
    ({"cpu_count": 0}, "cpu_count", "between 1 and 8"),
    ({"cpu_count": 9}, "cpu_count", "between 1 and 8"),
    ({"memory_mb": 127}, "memory_mb", "between 128 and 16384"),
    ({"memory_mb": 16385}, "memory_mb", "between 128 and 16384"),
    ({"name": "existing-vm"}, "name", "already used"),
    ({"disks": (SandboxDisk(SandboxDiskController.SATA, False),)}, "disks", "one entry per backed-up disk"),
    ({"disks": (SandboxDisk(SandboxDiskController.SATA, False),) * 3}, "disks", "one entry per backed-up disk"),
    ({"network_adapters": (SandboxNetworkAdapter("net-9", SandboxAdapterModel.RTL8139, False),)},
     "network_adapters", "net-9"),
    ({"network_adapters": (SandboxNetworkAdapter("net-3", SandboxAdapterModel.RTL8139, False),)},
     "network_adapters", "net-3"),
    ({"network_adapters": (SandboxNetworkAdapter("net-1", SandboxAdapterModel.RTL8139, True),)},
     "network_adapters", "SR-IOV"),
], ids=[
    "cpu_0", "cpu_above_max", "memory_127", "memory_above_max", "name_taken", "too_few_disks", "too_many_disks", "unknown_network", "network_not_permitted",
    "sriov_unsupported",
])
async def test_start_rejects_setting_the_location_does_not_allow(
    env: Env, overrides: dict[str, Any], field: str, match: str,
) -> None:
    settings = make_sandbox_settings(**overrides)
    with pytest.raises(SandboxSettingsError, match=match) as exc_info:
        await env.sandboxes.start(make_workload(), make_version(), settings)
    assert (exc_info.value.field, exc_info.value.other_field) == (field, None)
    _assert_nothing_created(env)


async def test_validate_settings_accepts_without_creating_anything(env: Env) -> None:
    await env.sandboxes.validate_settings(make_workload(), make_version(), make_sandbox_settings())
    assert env.vmm.ops().count("list_spec") == 1
    _assert_nothing_created(env)


async def test_validate_settings_rejects_as_start_does(env: Env) -> None:
    with pytest.raises(SandboxSettingsError, match="cpu_count must be between 1 and 8"):
        await env.sandboxes.validate_settings(make_workload(), make_version(), make_sandbox_settings(cpu_count=9))
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.validate_settings(make_workload(is_retired=True), make_version(), make_sandbox_settings())
    assert_resource_error(exc_info, resource_type="Workload", resource_id=WL_ID)
    _assert_nothing_created(env)


async def test_start_accepts_sriov_on_supporting_network(env: Env) -> None:
    adapters = (SandboxNetworkAdapter("net-2", SandboxAdapterModel.RTL8139, True),)
    await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(network_adapters=adapters))
    assert _open_in_vm_body(env)["vnics"][0]["preferSriov"] is True


@pytest.mark.parametrize("location_id", [LOC_R, "no-such-location"], ids=["remote", "unknown"])
async def test_start_rejects_location_not_on_backup_server(env: Env, location_id: str) -> None:
    with pytest.raises(SandboxSettingsError, match=location_id) as exc_info:
        await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings(location_id=location_id))
    assert exc_info.value.field == "location_id"
    assert env.vmm.calls == []


# ── start(): errors ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("workload,version,resource_type,resource_id", [
    (make_workload(MachineWorkloadType.PC), make_version(), "Workload", WL_ID),
    (make_workload(is_retired=True), make_version(), "Workload", WL_ID),
    (make_workload(), make_version(status=VersionStatus.FAILED), "WorkloadVersion", LOC_A),
    (make_workload(), make_version(locations=[LOCATION_R]), "WorkloadVersion", LOC_A),
], ids=["not_ps_vm", "retired", "failed_version", "remote_only"])
async def test_start_rejects_workload_or_version_without_requests(
    env: Env, workload: MachineWorkload, version: WorkloadVersion, resource_type: str, resource_id: str,
) -> None:
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.start(workload, version, make_sandbox_settings())
    assert_resource_error(exc_info, resource_type=resource_type, resource_id=resource_id)
    assert env.vmm.calls == []


async def test_start_without_same_volume_storage_raises(env: Env) -> None:
    env.vmm.responses["list_repo"] = {"repos": [REPOS[2]]}
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings())
    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id=LOC_A)
    _assert_nothing_created(env)


async def test_start_not_enough_memory_maps_to_invalid_operation(env: Env) -> None:
    env.vmm.errors["open_in_vm"] = ERR_NOT_ENOUGH_MEMORY
    with pytest.raises(InvalidOperationError, match="4096 MB") as exc_info:
        await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings())
    assert_resource_error(exc_info, resource_type="Workload", resource_id=WL_ID)
    assert exc_info.value.error_code == 7026


async def test_start_other_api_error_propagates_unchanged(env: Env) -> None:
    env.vmm.errors["open_in_vm"] = ERR_SYSTEM
    with pytest.raises(APIError) as exc_info:
        await env.sandboxes.start(make_workload(), make_version(), make_sandbox_settings())
    assert exc_info.value is ERR_SYSTEM


# ── get_resources() ──────────────────────────────────────────────────────────


async def test_get_resources_lists_backup_server_locations_default_first(env: Env) -> None:
    env.vmm.respond("list_resource", {"maxCpuNum": 2, "maxRamSize": 4096 * 1024, "networks": []}, namespace=NS_B)
    resources = await env.sandboxes.get_resources(make_workload(), make_version())

    assert [loc.location_id for loc in resources.locations] == [LOC_A, LOC_B]
    assert resources.locations[0] == SandboxLocation(
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
    loc_b = resources.locations[1]
    assert (loc_b.name, loc_b.max_cpu_count, loc_b.max_memory_mb, loc_b.networks) == ("apm-server-02", 2, 4096, ())
    # One list_resource per location, each sent to its own backup server.
    assert sorted((c.op, c.headers["x-syno-tunnel-route"]) for c in env.vmm.calls) == sorted([
        ("list_resource", NS_A), ("list_resource", NS_B),
    ])
    assert env.vmm.max_in_flight == {"load": 2}  # both locations load concurrently


@pytest.mark.parametrize("workload,version,resource_type,resource_id", [
    (make_workload(MachineWorkloadType.PC), make_version(), "Workload", WL_ID),
    (make_workload(), make_version(status=VersionStatus.CANCELED), "WorkloadVersion", LOC_A),
    (make_workload(), make_version(locations=[LOCATION_R]), "WorkloadVersion", LOC_A),
], ids=["not_ps_vm", "not_restorable", "remote_only"])
async def test_get_resources_rejects_without_requests(
    env: Env, workload: MachineWorkload, version: WorkloadVersion, resource_type: str, resource_id: str,
) -> None:
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.get_resources(workload, version)
    assert_resource_error(exc_info, resource_type=resource_type, resource_id=resource_id)
    assert env.vmm.calls == []


# ── get_default_settings() ───────────────────────────────────────────────────


async def test_default_settings_for_ps(env: Env, taipei_tz: None) -> None:
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings == SandboxSettings(
        location_id=LOC_A,
        name="prod-server-01_2026-04-25_090000",
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
    assert sorted(env.vmm.ops()) == ["list_basic", "list_repo", "list_resource", "list_spec"]


async def test_default_settings_are_accepted_by_start(env: Env) -> None:
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    await env.sandboxes.start(make_workload(), make_version(), settings)
    body = _open_in_vm_body(env)
    assert body["name"] == settings.name
    assert body["repoId"] == "repo-a"
    assert body["versionId"] == LOC_A


async def test_default_settings_at_a_chosen_location_use_its_choices(env: Env) -> None:
    env.vmm.respond("list_resource", {"maxCpuNum": 2, "maxRamSize": 2048 * 1024, "networks": [
        {"networkId": "net-b", "name": "B Network", "permitted": True, "supportSriov": False},
    ]}, namespace=NS_B)
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version(), location_id=LOC_B)

    assert settings.location_id == LOC_B
    assert (settings.cpu_count, settings.memory_mb) == (2, 2048)
    assert settings.network_adapters == (SandboxNetworkAdapter("net-b", SandboxAdapterModel.RTL8139, False),)
    assert {c.headers["x-syno-tunnel-route"] for c in env.vmm.calls} == {NS_B}
    assert env.vmm.one("list_spec").body["versionIds"] == [LOC_B]


@pytest.mark.parametrize("location_id", [LOC_R, "no-such-location"], ids=["remote", "unknown"])
async def test_default_settings_reject_location_not_on_backup_server(env: Env, location_id: str) -> None:
    with pytest.raises(SandboxSettingsError, match=location_id) as exc_info:
        await env.sandboxes.get_default_settings(make_workload(), make_version(), location_id=location_id)
    assert exc_info.value.field == "location_id"
    assert env.vmm.calls == []


async def test_default_location_without_backup_itself_is_first_copy_by_name(env: Env) -> None:
    copy_c = make_location(
        "123e4567-e89b-12d3-a456-426614174015", "123e4567-e89b-12d3-a456-426614174016", "apm-server-04", "192.0.2.4",
    )
    settings = await env.sandboxes.get_default_settings(
        make_workload(), make_version(locations=[LOCATION_R, copy_c, LOCATION_B]),
    )
    assert settings.location_id == LOC_B
    assert env.vmm.calls[0].headers == {"x-syno-tunnel-route": NS_B}


async def test_default_name_gets_first_free_suffix(env: Env, taipei_tz: None) -> None:
    env.vmm.responses["list_basic"] = {"guests": [
        {"name": "prod-server-01_2026-04-25_090000"}, {"name": "prod-server-01_2026-04-25_090000_1"},
    ]}
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings.name == "prod-server-01_2026-04-25_090000_2"


async def test_default_name_replaces_characters_a_sandbox_name_cannot_hold(env: Env, taipei_tz: None) -> None:
    wl = dataclasses.replace(make_workload(), name=" Win2019 (db:primary) ")
    settings = await env.sandboxes.get_default_settings(wl, make_version())

    assert settings.name == "Win2019 _db_primary__2026-04-25_090000"
    await env.sandboxes.start(wl, make_version(), settings)  # accepted as is


async def test_default_name_from_long_workload_name_fits_with_suffix(env: Env, taipei_tz: None) -> None:
    wl = dataclasses.replace(make_workload(), name="x" * 100)
    first = "x" * 62 + "_2026-04-25_090000"
    env.vmm.responses["list_basic"] = {"guests": [{"name": first}]}
    settings = await env.sandboxes.get_default_settings(wl, make_version())

    assert settings.name == "x" * 60 + "_2026-04-25_090000_1"
    assert len(first) == len(settings.name) == 80


@pytest.mark.parametrize("spec_cpu,expected", [("2", 2), ("8", 8), ("12", 8)])
async def test_default_cpu_count_capped_at_max(env: Env, spec_cpu: str, expected: int) -> None:
    env.vmm.responses["list_spec"] = {"specs": [{**PS_SPEC, "vcpuNum": spec_cpu}]}
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings.cpu_count == expected


@pytest.mark.parametrize("spec_mb,max_kb,expected", [
    (100, 16384 * 1024, 128),
    (128, 16384 * 1024, 128),
    (129, 16384 * 1024, 256),
    (700, 16384 * 1024, 768),
    (769, 16384 * 1024, 1024),
    (2047, 16384 * 1024, 2048),
    (2048, 16384 * 1024, 2048),
    (32768, 16384 * 1024, 16384),
    (32768, 15000 * 1024, 14336),
    (700, 600 * 1024, 600),
], ids=[
    "100", "128", "129", "700", "769", "2047", "2048",
    "above_whole_gib_max", "above_partial_gib_max", "small_above_max",
])
async def test_default_memory_rounding(env: Env, spec_mb: int, max_kb: int, expected: int) -> None:
    env.vmm.responses["list_spec"] = {"specs": [{**PS_SPEC, "vramSize": str(spec_mb)}]}
    env.vmm.responses["list_resource"] = {**RESOURCE, "maxRamSize": max_kb}
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings.memory_mb == expected


@pytest.mark.parametrize("spec_card,expected", [
    ("cirrus", SandboxVideoCard.CIRRUS),
    ("vga", SandboxVideoCard.VGA),
    ("vmvga", SandboxVideoCard.VMVGA),
    ("qxl", SandboxVideoCard.VMVGA),
    ("", SandboxVideoCard.VMVGA),
])
async def test_default_video_card(env: Env, spec_card: str, expected: SandboxVideoCard) -> None:
    env.vmm.responses["list_spec"] = {"specs": [{**PS_SPEC, "videoCard": spec_card}]}
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings.video_card == expected


@pytest.mark.parametrize("spec_machine,spec_modes,expected_machine,expected_controllers", [
    ("q35", [2, 1], SandboxMachineType.Q35, [SandboxDiskController.SATA, SandboxDiskController.SATA]),
    ("", [2, 1], SandboxMachineType.Q35, [SandboxDiskController.SATA, SandboxDiskController.SATA]),
    ("pc", [2, 1], SandboxMachineType.PC, [SandboxDiskController.IDE, SandboxDiskController.SCSI]),
    ("pc", [16, 32], SandboxMachineType.PC, [SandboxDiskController.SATA, SandboxDiskController.SATA]),
], ids=["q35_forces_sata", "unknown_machine_is_q35", "pc_keeps_controllers", "pc_unknown_mode_is_sata"])
async def test_default_machine_type_and_disk_controllers(
    env: Env,
    spec_machine: str,
    spec_modes: list[int],
    expected_machine: SandboxMachineType,
    expected_controllers: list[SandboxDiskController],
) -> None:
    vdisks = [{**d, "vdiskMode": m} for d, m in zip(PS_VDISKS, spec_modes, strict=True)]
    env.vmm.responses["list_spec"] = {"specs": [{**PS_SPEC, "machineType": spec_machine, "vdiskStruct": vdisks}]}
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings.machine_type == expected_machine
    assert [d.controller for d in settings.disks] == expected_controllers
    assert all(d.space_reclamation is False for d in settings.disks)


async def test_default_hyperv_on_regardless_of_backup(env: Env) -> None:
    env.vmm.responses["list_spec"] = {"specs": [{**PS_SPEC, "hypervEnlighten": False}]}
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings.hyperv_enlightenments is True
    assert settings.cpu_compatibility_mode is False


async def test_default_no_adapter_without_permitted_network(env: Env) -> None:
    env.vmm.responses["list_resource"] = {**RESOURCE, "networks": [RESOURCE["networks"][2]]}
    settings = await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert settings.network_adapters == ()


async def test_default_settings_without_storage_raises(env: Env) -> None:
    env.vmm.responses["list_repo"] = {"repos": [REPOS[2]]}
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.get_default_settings(make_workload(), make_version())
    assert_resource_error(exc_info, resource_type="WorkloadVersion", resource_id=LOC_A)


async def test_default_settings_rejects_non_ps_vm_without_requests(env: Env) -> None:
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.get_default_settings(make_workload(MachineWorkloadType.PC), make_version())
    assert_resource_error(exc_info, resource_type="Workload", resource_id=WL_ID)
    assert env.vmm.calls == []


# ── get_latest_version() ─────────────────────────────────────────────────────


def _raw_version(version_id: str, status: str) -> dict[str, Any]:
    return {"id": version_id, "namespace": NS_A, "spec": {"executionId": "EX_1"},
            "status": {"status": status, "startTime": str(int(CREATED_AT.timestamp()))}}


async def test_latest_version_is_newest_successful_or_partial(env: Env) -> None:
    env.vmm.responses["version"] = {"versions": [_raw_version(LOC_A, "PARTIAL")], "total": 1}

    version = await env.sandboxes.get_latest_version(make_workload())

    assert (version.version_id, version.status) == (LOC_A, VersionStatus.PARTIAL)
    call = env.vmm.one("version")
    assert call.method == "GET"
    assert [v for k, v in call.body if k == "status"] == ["COMPLETED", "PARTIAL"]
    assert ("limit", 1) in call.body
    assert ("orderDirection", "ORDER_DIRECTION_DESC") in call.body


async def test_latest_version_without_restorable_version_is_invalid_operation(env: Env) -> None:
    with pytest.raises(InvalidOperationError, match="no successful or partially successful version") as exc_info:
        await env.sandboxes.get_latest_version(make_workload())
    assert_resource_error(exc_info, resource_type="Workload", resource_id=WL_ID)


async def test_latest_version_rejects_non_ps_vm_without_requests(env: Env) -> None:
    with pytest.raises(InvalidOperationError) as exc_info:
        await env.sandboxes.get_latest_version(make_workload(MachineWorkloadType.PC))
    assert_resource_error(exc_info, resource_type="Workload", resource_id=WL_ID)
    assert env.vmm.calls == []


# ── Settings from changes (the CLI/MCP flow) ─────────────────────────────────


async def test_default_settings_with_changes_at_another_location_are_accepted(env: Env) -> None:
    env.vmm.respond("list_resource", {**RESOURCE, "maxCpuNum": 2}, namespace=NS_B)
    changes: dict[str, Any] = {
        "location_id": LOC_B, "cpu_count": 2, "video_card": "cirrus",
        "network_adapters": [{"network_id": "net-2", "model": "virtio", "sriov": True}],
    }

    defaults = await env.sandboxes.get_default_settings(
        make_workload(), make_version(), location_id=changes["location_id"],
    )
    await env.sandboxes.start(make_workload(), make_version(), defaults.with_changes(changes))

    body = env.vmm.one("open_in_vm").body
    assert (body["versionId"], body["vcpuNum"], body["videoCard"]) == (LOC_B, "2", "cirrus")
    assert body["vnics"] == [
        {"preferSriov": True, "vnicType": 1, "type": "add", "mac": MACS[0], "networkId": "net-2"},
    ]
