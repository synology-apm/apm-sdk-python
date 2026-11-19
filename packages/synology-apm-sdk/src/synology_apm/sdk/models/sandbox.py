"""Sandbox models: settings, available resources, and the VNC relay address."""
from __future__ import annotations

import dataclasses
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from ..enums import (
    SandboxAdapterModel,
    SandboxDiskController,
    SandboxMachineType,
    SandboxVideoCard,
)
from ..exceptions import SandboxSettingsError
from ._shared import auto_to_dict

_NAME_MAX_LEN = 80
_NAME_FORBIDDEN = frozenset('{}|^[]?=:+/*()$!"#%&\',;<>@`~\\')


def default_sandbox_name(workload_name: str, created_at: datetime, taken: Collection[str]) -> str:
    """The default name of a sandbox of a version: ``<workload name>_<YYYY-MM-DD>_<HHMMSS>``.

    The timestamp is ``created_at`` in local time. ``_1``, ``_2``, ... is appended while the
    name is in ``taken``. Characters a sandbox name cannot hold become ``_``, and the workload
    name loses leading and trailing spaces and is shortened so that the whole name fits in 80
    characters.

    Args:
        workload_name: The workload's display name.
        created_at:    The version's creation time.
        taken:         Names already in use by other VMs.
    """
    prefix = "".join("_" if c in _NAME_FORBIDDEN else c for c in workload_name).strip()
    stamp = f"_{created_at.astimezone():%Y-%m-%d_%H%M%S}"
    suffix = 0
    while True:
        tail = stamp + (f"_{suffix}" if suffix else "")
        name = prefix[:_NAME_MAX_LEN - len(tail)].strip() + tail
        if name not in taken:
            return name
        suffix += 1


@dataclass(frozen=True)
class SandboxNetworkAdapter:
    """One network adapter of a sandbox VM.

    Attributes:
        network_id: Network to connect to; one of ``SandboxLocation.networks``.
        model:      Adapter model.
        sriov:      Use SR-IOV; allowed only when the network supports it.
    """
    network_id: str
    model: SandboxAdapterModel
    sriov: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class SandboxDisk:
    """One sandbox virtual disk, in the order of the backed-up disks.

    Attributes:
        controller:        Virtual disk controller.
        space_reclamation: Reclaim space freed inside the guest.
    """
    controller: SandboxDiskController
    space_reclamation: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class SandboxSettings:
    """Every setting of a sandbox.

    Get the default values from ``MachineSandboxCollection.get_default_settings()`` and change
    them with ``with_changes()`` (or ``dataclasses.replace()``); allowed values per location
    come from ``MachineSandboxCollection.get_resources()``, and
    ``MachineSandboxCollection.validate_settings()`` checks them against the location.

    Attributes:
        location_id:            Version location to restore from: the ``location_id`` of a
            non-remote entry of ``WorkloadVersion.locations``. Networks and the CPU and
            memory limits depend on this location.
        name:                   Sandbox VM name: 1-80 characters, no leading or trailing
            space, none of ``{}|^[]?=:+/*()$!"#%&',;<>@`~\\``, and not the name of an
            existing VM.
        cpu_count:              Number of virtual CPUs (1 to the location's max_cpu_count).
        memory_mb:              Memory in MB (the location's min_memory_mb to max_memory_mb).
        cpu_compatibility_mode: CPU compatibility mode.
        hyperv_enlightenments:  Hyper-V enlightenments; must be False when
            cpu_compatibility_mode is True.
        video_card:             Video card.
        machine_type:           Machine type.
        network_adapters:       Network adapters; may be empty (no network).
        disks:                  One entry per backed-up disk, in order (no IDE controller
            with machine type Q35).

    Raises:
        SandboxSettingsError: The name breaks the format rules above (whether another VM has
            it is checked by ``validate_settings()``), Hyper-V enlightenments are on with CPU
            compatibility mode, or a disk uses IDE with machine type Q35.
    """
    location_id: str
    name: str
    cpu_count: int
    memory_mb: int
    cpu_compatibility_mode: bool
    hyperv_enlightenments: bool
    video_card: SandboxVideoCard
    machine_type: SandboxMachineType
    network_adapters: tuple[SandboxNetworkAdapter, ...]
    disks: tuple[SandboxDisk, ...]

    def __post_init__(self) -> None:
        name = self.name
        if not name:
            raise SandboxSettingsError("name must not be empty.", field="name")
        if len(name) > _NAME_MAX_LEN:
            raise SandboxSettingsError(f"name must be at most {_NAME_MAX_LEN} characters.", field="name")
        if name != name.strip():
            raise SandboxSettingsError("name must not start or end with a space.", field="name")
        bad = sorted(set(name) & _NAME_FORBIDDEN)
        if bad:
            raise SandboxSettingsError(f"name must not contain {''.join(bad)!r}.", field="name")
        if self.cpu_compatibility_mode and self.hyperv_enlightenments:
            raise SandboxSettingsError(
                "hyperv_enlightenments must be False when cpu_compatibility_mode is True.",
                field="hyperv_enlightenments", other_field="cpu_compatibility_mode",
            )
        if self.machine_type == SandboxMachineType.Q35 and any(
            d.controller == SandboxDiskController.IDE for d in self.disks
        ):
            raise SandboxSettingsError(
                "disks: the IDE controller is not available with machine type q35.",
                field="disks", other_field="machine_type",
            )

    def with_changes(self, changes: Mapping[str, Any]) -> SandboxSettings:
        """Return a copy with some settings changed; the inverse of ``to_dict()`` per key.

        ``changes`` is keyed like ``to_dict()`` and takes its value forms: enum fields take an
        enum member or its string value, and ``network_adapters`` / ``disks`` take a list of
        dicts keyed like their ``to_dict()`` (or of model instances). Settings not in
        ``changes`` keep their current value; ``location_id`` is changed like any other key.

        Args:
            changes: The settings to change.

        Raises:
            SandboxSettingsError: A key is not a setting, a value has the wrong type or is
                not one of the enum's values, or the result breaks a rule of this class.
        """
        unknown = [key for key in changes if key not in _CONVERTERS]
        if unknown:
            raise SandboxSettingsError(
                f"{unknown[0]!r} is not a sandbox setting; use one of {', '.join(_CONVERTERS)}.",
                field=str(unknown[0]),
            )
        return dataclasses.replace(
            self, **{key: _CONVERTERS[key](value, key, key) for key, value in changes.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


# ── with_changes() value conversion ──────────────────────────────────────────
# A converter takes (value, field, label): ``field`` is the SandboxSettings field an error
# names, ``label`` the key shown in its message (``field.key`` inside an adapter or disk).

_Converter = Callable[[Any, str, str], Any]
_E = TypeVar("_E", bound=Enum)


def _of_type(kind: type, noun: str) -> _Converter:
    def convert(value: Any, field: str, label: str) -> Any:
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise SandboxSettingsError(f"{label} must be {noun}.", field=field)
        return value
    return convert


def _of_enum(enum_type: type[_E]) -> _Converter:
    def convert(value: Any, field: str, label: str) -> _E:
        if isinstance(value, enum_type):
            return value
        try:
            return enum_type(value)
        except ValueError:
            choices = ", ".join(str(m.value) for m in enum_type)
            raise SandboxSettingsError(f"{label} must be one of {choices}.", field=field) from None
    return convert


def _of_items(model: type[Any], converters: Mapping[str, _Converter]) -> _Converter:
    def item(value: Any, field: str) -> Any:
        if isinstance(value, model):
            return value
        keys = ", ".join(converters)
        if not isinstance(value, Mapping) or set(value) != set(converters):
            raise SandboxSettingsError(f"{field}: each entry must have exactly the keys {keys}.", field=field)
        return model(**{key: conv(value[key], field, f"{field}.{key}") for key, conv in converters.items()})

    def convert(value: Any, field: str, label: str) -> tuple[Any, ...]:
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
            raise SandboxSettingsError(f"{label} must be a list.", field=field)
        return tuple(item(v, field) for v in value)
    return convert


_STR = _of_type(str, "a string")
_INT = _of_type(int, "an integer")
_BOOL = _of_type(bool, "true or false")

_CONVERTERS: dict[str, _Converter] = {
    "location_id": _STR,
    "name": _STR,
    "cpu_count": _INT,
    "memory_mb": _INT,
    "cpu_compatibility_mode": _BOOL,
    "hyperv_enlightenments": _BOOL,
    "video_card": _of_enum(SandboxVideoCard),
    "machine_type": _of_enum(SandboxMachineType),
    "network_adapters": _of_items(SandboxNetworkAdapter, {
        "network_id": _STR, "model": _of_enum(SandboxAdapterModel), "sriov": _BOOL,
    }),
    "disks": _of_items(SandboxDisk, {
        "controller": _of_enum(SandboxDiskController), "space_reclamation": _BOOL,
    }),
}


@dataclass(frozen=True)
class SandboxNetwork:
    """A network a sandbox can connect to.

    Attributes:
        network_id:     Network identifier.
        name:           Network name.
        supports_sriov: Whether adapters on this network can use SR-IOV.
    """
    network_id: str
    name: str
    supports_sriov: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class SandboxLocation:
    """Limits and choices for a sandbox restored from one version location.

    Attributes:
        location_id:   The version location's ``location_id`` (``SandboxSettings.location_id``).
        name:          Name of the backup server holding this location.
        max_cpu_count: Maximum number of virtual CPUs.
        min_memory_mb: Minimum memory in MB.
        max_memory_mb: Maximum memory in MB.
        networks:      Networks the sandbox can connect to.
    """
    location_id: str
    name: str
    max_cpu_count: int
    min_memory_mb: int
    max_memory_mb: int
    networks: tuple[SandboxNetwork, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class SandboxResources:
    """What a sandbox of one version can use, per location it can be restored from.

    Attributes:
        locations: One entry per eligible version location; the first is the default.
    """
    locations: tuple[SandboxLocation, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class SandboxVncEndpoint:
    """A local address that relays VNC clients to a sandbox's screen.

    Attributes:
        host: Loopback address the relay listens on.
        port: TCP port the relay listens on.
    """
    host: str
    port: int

    @property
    def url(self) -> str:
        """The address as a ``vnc://host:port`` URL."""
        return f"vnc://{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)
