"""Unit tests for the sandbox models: SandboxSettings rules, with_changes(), to_dict() shapes."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from synology_apm.sdk import SandboxSettingsError
from synology_apm.sdk.enums import (
    SandboxAdapterModel,
    SandboxDiskController,
    SandboxMachineType,
    SandboxVideoCard,
)
from synology_apm.sdk.models.sandbox import (
    SandboxDisk,
    SandboxNetwork,
    SandboxNetworkAdapter,
    SandboxVncEndpoint,
    default_sandbox_name,
)
from tests.unit.sandbox_builders import (
    LOC_A,
    make_sandbox_location,
    make_sandbox_resources,
    make_sandbox_settings,
)

# ── Rules checked when the settings are built ────────────────────────────────


@pytest.mark.parametrize("overrides,field,other_field,match", [
    ({"name": "n" * 81}, "name", None, "at most 80"),
    ({"name": ""}, "name", None, "empty"),
    ({"name": " drill"}, "name", None, "space"),
    ({"name": "drill "}, "name", None, "space"),
    ({"cpu_compatibility_mode": True, "hyperv_enlightenments": True},
     "hyperv_enlightenments", "cpu_compatibility_mode", "hyperv_enlightenments must be False"),
    ({"disks": (SandboxDisk(SandboxDiskController.IDE, False), SandboxDisk(SandboxDiskController.SATA, False))},
     "disks", "machine_type", "IDE controller"),
], ids=[
    "name_81", "name_empty", "name_leading_space", "name_trailing_space", "hyperv_with_compat_mode", "ide_with_q35",
])
def test_settings_reject_invalid_values_when_built(
    overrides: dict[str, Any], field: str, other_field: str | None, match: str,
) -> None:
    with pytest.raises(SandboxSettingsError, match=match) as exc_info:
        make_sandbox_settings(**overrides)
    assert (exc_info.value.field, exc_info.value.other_field) == (field, other_field)
    assert isinstance(exc_info.value, ValueError)  # callers catching ValueError still see it


@pytest.mark.parametrize("char", list('{}|^[]?=:+/*()$!"#%&\',;<>@`~\\'))
def test_settings_reject_forbidden_name_character(char: str) -> None:
    with pytest.raises(SandboxSettingsError, match="name must not contain") as exc_info:
        make_sandbox_settings(name=f"drill{char}01")
    assert exc_info.value.field == "name"


def test_settings_accept_ide_with_machine_type_pc_and_an_80_character_name() -> None:
    settings = make_sandbox_settings(
        name="n" * 80, machine_type=SandboxMachineType.PC, disks=(SandboxDisk(SandboxDiskController.IDE, False),),
    )
    assert (len(settings.name), settings.disks[0].controller) == (80, SandboxDiskController.IDE)


def test_settings_error_to_dict() -> None:
    error = SandboxSettingsError("x must be y.", field="x", other_field="y")
    assert error.to_dict() == {"message": "x must be y.", "field": "x", "other_field": "y"}
    assert str(error) == "x must be y."


# ── to_dict() shapes ─────────────────────────────────────────────────────────


def test_settings_to_dict_shape() -> None:
    assert make_sandbox_settings().to_dict() == {
        "location_id": LOC_A,
        "name": "drill-01",
        "cpu_count": 4,
        "memory_mb": 4096,
        "cpu_compatibility_mode": False,
        "hyperv_enlightenments": True,
        "video_card": "vga",
        "machine_type": "q35",
        "network_adapters": [{"network_id": "net-1", "model": "rtl8139", "sriov": False}],
        "disks": [
            {"controller": "sata", "space_reclamation": False},
            {"controller": "sata", "space_reclamation": False},
        ],
    }


def test_resources_to_dict_shape() -> None:
    resources = make_sandbox_resources(make_sandbox_location(networks=(SandboxNetwork("net-1", "Default", True),)))
    assert resources.to_dict() == {"locations": [{
        "location_id": LOC_A,
        "name": "apm-server-03",
        "max_cpu_count": 8,
        "min_memory_mb": 128,
        "max_memory_mb": 16384,
        "networks": [{"network_id": "net-1", "name": "Default", "supports_sriov": True}],
    }]}


def test_vnc_endpoint_to_dict_shape() -> None:
    endpoint = SandboxVncEndpoint(host="127.0.0.1", port=5901)
    assert endpoint.to_dict() == {"host": "127.0.0.1", "port": 5901, "url": "vnc://127.0.0.1:5901"}


# ── with_changes() ───────────────────────────────────────────────────────────


def test_with_changes_takes_to_dict_values() -> None:
    target = make_sandbox_settings(
        location_id="loc-2",
        name="drill-02",
        cpu_count=2,
        memory_mb=2048,
        cpu_compatibility_mode=True,
        hyperv_enlightenments=False,
        video_card=SandboxVideoCard.CIRRUS,
        machine_type=SandboxMachineType.PC,
        network_adapters=(SandboxNetworkAdapter("net-2", SandboxAdapterModel.VIRTIO, True),),
        disks=(SandboxDisk(SandboxDiskController.IDE, True), SandboxDisk(SandboxDiskController.SCSI, False)),
    )
    assert make_sandbox_settings().with_changes(target.to_dict()) == target


def test_with_changes_keeps_settings_not_given() -> None:
    changed = make_sandbox_settings().with_changes({"memory_mb": 2048})
    assert changed == make_sandbox_settings(memory_mb=2048)


def test_with_changes_accepts_enum_members_and_models() -> None:
    adapter = SandboxNetworkAdapter("net-2", SandboxAdapterModel.E1000, False)
    changed = make_sandbox_settings().with_changes({
        "video_card": SandboxVideoCard.VMVGA, "network_adapters": [adapter], "disks": (),
    })
    assert (changed.video_card, changed.network_adapters, changed.disks) == (SandboxVideoCard.VMVGA, (adapter,), ())


def test_with_changes_coerces_strings_inside_adapters_and_disks() -> None:
    changed = make_sandbox_settings().with_changes({
        "machine_type": "pc",
        "network_adapters": [{"network_id": "net-1", "model": "e1000", "sriov": False}],
        "disks": [{"controller": "ide", "space_reclamation": True}],
    })
    assert changed.machine_type == SandboxMachineType.PC
    assert changed.network_adapters == (SandboxNetworkAdapter("net-1", SandboxAdapterModel.E1000, False),)
    assert changed.disks == (SandboxDisk(SandboxDiskController.IDE, True),)


@pytest.mark.parametrize("changes,field,match", [
    ({"cpu": 2}, "cpu", "'cpu' is not a sandbox setting"),
    ({"video_card": "matrox"}, "video_card", "video_card must be one of cirrus, vga, vmvga"),
    ({"cpu_count": "4"}, "cpu_count", "cpu_count must be an integer"),
    ({"cpu_count": True}, "cpu_count", "cpu_count must be an integer"),
    ({"hyperv_enlightenments": "yes"}, "hyperv_enlightenments", "must be true or false"),
    ({"name": 7}, "name", "name must be a string"),
    ({"disks": {"controller": "sata", "space_reclamation": False}}, "disks", "disks must be a list"),
    ({"disks": [{"controller": "sata"}]}, "disks", "exactly the keys controller, space_reclamation"),
    ({"network_adapters": [{"network_id": "net-1", "model": "ne2000", "sriov": False}]},
     "network_adapters", "network_adapters.model must be one of"),
], ids=[
    "unknown_key", "unknown_enum_value", "string_int", "bool_int", "string_bool", "int_string",
    "items_not_a_list", "item_missing_key", "unknown_nested_enum_value",
])
def test_with_changes_rejects(changes: dict[str, Any], field: str, match: str) -> None:
    with pytest.raises(SandboxSettingsError, match=match) as exc_info:
        make_sandbox_settings().with_changes(changes)
    assert (exc_info.value.field, exc_info.value.other_field) == (field, None)


def test_with_changes_checks_the_result_like_the_constructor() -> None:
    with pytest.raises(SandboxSettingsError) as exc_info:
        make_sandbox_settings().with_changes({"cpu_compatibility_mode": True})
    assert (exc_info.value.field, exc_info.value.other_field) == ("hyperv_enlightenments", "cpu_compatibility_mode")


# ── default_sandbox_name() ───────────────────────────────────────────────────

_AT = datetime(2026, 4, 25, 1, 0, 0, tzinfo=UTC)


def test_default_name_is_workload_name_and_local_version_time() -> None:
    assert default_sandbox_name("prod-server-01", _AT, ()) == f"prod-server-01_{_AT.astimezone():%Y-%m-%d_%H%M%S}"


def test_default_name_takes_the_first_free_suffix() -> None:
    base = default_sandbox_name("prod-server-01", _AT, ())
    assert default_sandbox_name("prod-server-01", _AT, {base, f"{base}_1"}) == f"{base}_2"
