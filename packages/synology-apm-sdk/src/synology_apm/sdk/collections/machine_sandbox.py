"""MachineSandboxCollection — instant restore of PS/VM backup versions to the built-in sandbox."""
from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict

from .._console import SandboxConsole, SandboxConsoles
from .._http import WebAPISession
from ..enums import (
    MachineWorkloadType,
    RestoreActivityStatus,
    RestoreType,
    SandboxAdapterModel,
    SandboxDiskController,
    SandboxKey,
    SandboxMachineType,
    SandboxVideoCard,
    VersionStatus,
)
from ..exceptions import (
    APIError,
    InvalidOperationError,
    ResourceNotFoundError,
    ResourceNotReadyError,
    SandboxSettingsError,
)
from ..models.activity import RestoreActivity
from ..models.sandbox import (
    SandboxDisk,
    SandboxLocation,
    SandboxNetwork,
    SandboxNetworkAdapter,
    SandboxResources,
    SandboxSettings,
    SandboxVncEndpoint,
    default_sandbox_name,
)
from ..models.version import VersionLocation, WorkloadVersion
from ..models.workload import MachineWorkload
from ._activity_parsers import _MACHINE_TYPE_TO_CATEGORY_SERVICE
from ._shared import _check_active_for_write, _not_found_as, _tunnel_headers, _wl_params, _wl_ref
from .backup_servers import BackupServerCollection

if TYPE_CHECKING:
    from .machine import MachineWorkloadCollection

# A sandbox is a restore activity (raw restoreType INSTANT_RESTORE_AEM). Settings and start
# calls go to the portal "vmm" endpoints routed with the chosen version location's namespace;
# connect_to_vm is routed with the activity's namespace. Request contract: BEHAVIOR_REFERENCE.md,
# MachineSandboxCollection.
_VMM = "/api/v1/portal/vmm/"
_BRIDGE_HOST = "127.0.0.1"
_KEY_DELAY_DEFAULT_S = 2.0
_KEY_DELAY_MAX_S = 60

_GIB = 1024 ** 3
_MIN_MEMORY_MB = 128
_SMALL_MEMORY_STEPS_MB = (128, 256, 512, 768)

# errorCode in error.details of a failed open_in_vm: not enough free memory on the host.
_NOT_ENOUGH_MEMORY_CODE = 7026
# errorCode in error.details of a failed connect_to_vm: the VM could not be powered on.
_POWER_ON_FAILED_CODE = 932

# The version statuses a sandbox can be restored from, in the order they are requested.
_RESTORABLE_STATUSES = (VersionStatus.SUCCESS, VersionStatus.PARTIAL)
_STARTING = frozenset({RestoreActivityStatus.PREPARING, RestoreActivityStatus.RESTORING})
_SANDBOX_WORKLOAD_TYPES: dict[MachineWorkloadType, str] = {
    t: _MACHINE_TYPE_TO_CATEGORY_SERVICE[t] for t in (MachineWorkloadType.PS, MachineWorkloadType.VM)
}

# Raw values of the sandbox settings. Every sandbox runs at average CPU priority (cpuWeight)
# with the matching disk I/O priority (devWeight).
_CPU_WEIGHT = "256"
_DEV_WEIGHT = "3"
_VDISK_MODE: dict[SandboxDiskController, int] = {
    SandboxDiskController.SCSI: 1,
    SandboxDiskController.IDE:  2,
    SandboxDiskController.SATA: 16,
}
_VDISK_MODE_TO_CONTROLLER: dict[int, SandboxDiskController] = {v: k for k, v in _VDISK_MODE.items()}
_VNIC_TYPE: dict[SandboxAdapterModel, int] = {
    SandboxAdapterModel.VIRTIO:  1,
    SandboxAdapterModel.E1000:   2,
    SandboxAdapterModel.RTL8139: 4,
}
_VIDEO_CARDS: dict[str, SandboxVideoCard] = {c.value: c for c in SandboxVideoCard}


# ── Parsed vmm responses ─────────────────────────────────────────────────────
# Raw keys appear only in the parsers below and in _open_in_vm_body().


@dataclass(frozen=True)
class _SpecDisk:
    """One backed-up disk of the machine spec."""

    capacity_bytes: int
    controller: SandboxDiskController | None  # None: a controller the SDK does not know
    volume_path: str


@dataclass(frozen=True)
class _VmSpec:
    """The backed-up machine's spec (list_spec)."""

    cpu_count: int
    memory_mb: int
    video_card: SandboxVideoCard | None  # None: a video card the SDK does not know
    machine_type: SandboxMachineType
    use_ovmf: bool
    disks: tuple[_SpecDisk, ...]
    raw: dict[str, Any]  # echoed in open_in_vm (vdiskStruct, birthInfo)


@dataclass(frozen=True)
class _Repo:
    """A storage repo a sandbox can be stored on (list_repo); values are echoed as received."""

    repo_id: str
    name: str
    host_id: str
    host_name: str
    allocated_size: str
    size: str
    volume_path: str


@dataclass(frozen=True)
class _Plan:
    """Everything start() needs to know about one version location."""

    version_location: VersionLocation
    spec: _VmSpec
    location: SandboxLocation
    repo: _Repo  # where the sandbox is stored
    existing_names: frozenset[str]


def _parse_spec(raw_spec: dict[str, Any]) -> _VmSpec:
    specs = raw_spec.get("specs") or [{}]
    raw: dict[str, Any] = specs[0] or {}
    return _VmSpec(
        cpu_count=int(raw.get("vcpuNum") or 1),
        memory_mb=int(raw.get("vramSize") or 0),
        video_card=_VIDEO_CARDS.get(str(raw.get("videoCard") or "")),
        machine_type=SandboxMachineType.PC if raw.get("machineType") == "pc" else SandboxMachineType.Q35,
        use_ovmf=bool(raw.get("useOvmf", False)),
        disks=tuple(
            _SpecDisk(
                capacity_bytes=int(d.get("capacity") or 0),
                controller=_VDISK_MODE_TO_CONTROLLER.get(int(d.get("vdiskMode") or 0)),
                volume_path=str(d.get("volumePath") or ""),
            )
            for d in raw.get("vdiskStruct") or []
        ),
        raw=raw,
    )


def _parse_repos(raw_repos: dict[str, Any]) -> list[_Repo]:
    """The repos, sorted by name."""
    repos = [
        _Repo(
            repo_id=str(r.get("repoId") or ""),
            name=str(r.get("name") or ""),
            host_id=str(r.get("hostId") or ""),
            host_name=str(r.get("hostName") or ""),
            allocated_size=str(r.get("allocatedSize") or "0"),
            size=str(r.get("size") or "0"),
            volume_path=str(r.get("volumePath") or ""),
        )
        for r in raw_repos.get("repos") or []
    ]
    return sorted(repos, key=lambda r: r.name)


def _parse_location(location: VersionLocation, raw: dict[str, Any]) -> SandboxLocation:
    """The limits and usable networks of one location, from its list_resource response."""
    return SandboxLocation(
        location_id=location.location_id,
        name=location.location_info.name,
        max_cpu_count=int(raw.get("maxCpuNum") or 0),
        min_memory_mb=_MIN_MEMORY_MB,
        max_memory_mb=int(raw.get("maxRamSize") or 0) // 1024,
        networks=tuple(
            SandboxNetwork(
                network_id=str(n.get("networkId") or ""),
                name=str(n.get("name") or ""),
                supports_sriov=bool(n.get("supportSriov")),
            )
            for n in raw.get("networks") or []
            if n.get("permitted")
        ),
    )


def _parse_guest_names(basic: dict[str, Any]) -> frozenset[str]:
    return frozenset(str(g.get("name")) for g in basic.get("guests") or [] if g.get("name"))


# ── open_in_vm body ──────────────────────────────────────────────────────────


class _OpenInVmVnic(TypedDict):
    preferSriov: bool
    vnicType: int
    type: str
    mac: str
    networkId: str


class _OpenInVmVdisk(TypedDict):
    type: str
    vdiskMode: int
    name: str
    unmap: bool
    iopsEnable: bool
    devLimit: str
    devReservation: str
    devWeight: str
    vdiskSize: str
    idx: str


class _OpenInVmBody(TypedDict):
    useOvmf: bool
    vnics: list[_OpenInVmVnic]
    increaseAllocatedSize: str
    autoSwitch: int
    vdisks: list[_OpenInVmVdisk]
    vdiskStruct: list[dict[str, Any]]
    name: str
    vcpuNum: str
    vramSize: str
    videoCard: str
    cpuWeight: str
    machineType: str
    cpuPassthru: bool
    hypervEnlighten: bool
    cpuPinNum: str
    birthInfo: Any
    repoId: str
    repoName: str
    hostId: str
    repoHostName: str
    allocatedSize: str
    size: str
    versionId: str
    versionTimestamp: str
    workloadType: str
    workload: dict[str, str]
    tunnelNamespace: str


def _open_in_vm_body(
    plan: _Plan,
    workload: MachineWorkload,
    version: WorkloadVersion,
    settings: SandboxSettings,
    macs: Sequence[str],
) -> _OpenInVmBody:
    """The open_in_vm body, including the fields that always take a fixed value."""
    spec, repo = plan.spec, plan.repo
    return {
        "useOvmf": spec.use_ovmf,
        "vnics": [
            {
                "preferSriov": adapter.sriov,
                "vnicType": _VNIC_TYPE[adapter.model],
                "type": "add",
                "mac": mac,
                "networkId": adapter.network_id,
            }
            for adapter, mac in zip(settings.network_adapters, macs, strict=True)
        ],
        "increaseAllocatedSize": str(sum(d.capacity_bytes for d in spec.disks)),
        "autoSwitch": 0,
        "vdisks": [
            {
                "type": "add",
                "vdiskMode": _VDISK_MODE[disk.controller],
                "name": f"Virtual disk {i + 1}",
                "unmap": disk.space_reclamation,
                "iopsEnable": False,
                "devLimit": "0",
                "devReservation": "0",
                "devWeight": _DEV_WEIGHT,
                "vdiskSize": str(math.ceil(spec_disk.capacity_bytes / _GIB)),
                "idx": str(i),
            }
            for i, (disk, spec_disk) in enumerate(zip(settings.disks, spec.disks, strict=True))
        ],
        "vdiskStruct": spec.raw.get("vdiskStruct") or [],
        "name": settings.name,
        "vcpuNum": str(settings.cpu_count),
        "vramSize": str(settings.memory_mb),
        "videoCard": settings.video_card.value,
        "cpuWeight": _CPU_WEIGHT,
        "machineType": settings.machine_type.value,
        "cpuPassthru": not settings.cpu_compatibility_mode,
        "hypervEnlighten": settings.hyperv_enlightenments,
        "cpuPinNum": "0",
        "birthInfo": spec.raw.get("birthInfo"),
        "repoId": repo.repo_id,
        "repoName": repo.name,
        "hostId": repo.host_id,
        "repoHostName": repo.host_name,
        "allocatedSize": repo.allocated_size,
        "size": repo.size,
        "versionId": plan.version_location.location_id,
        "versionTimestamp": str(int(version.created_at.timestamp())),
        "workloadType": _SANDBOX_WORKLOAD_TYPES[workload.workload_type],
        "workload": _wl_ref(workload),
        "tunnelNamespace": plan.version_location.namespace,
    }


# ── Rules ────────────────────────────────────────────────────────────────────


def _require_ps_vm(workload: MachineWorkload) -> None:
    """Raise unless the workload is a PS or VM workload."""
    if not isinstance(workload, MachineWorkload) or workload.workload_type not in _SANDBOX_WORKLOAD_TYPES:
        raise InvalidOperationError(
            f"Workload '{workload.name}' is not a physical server (PS) or VM workload; "
            "only those can be restored to a sandbox.",
            resource_type="Workload",
            resource_id=workload.workload_id,
        )


def _require_restorable(version: WorkloadVersion) -> None:
    if version.status not in _RESTORABLE_STATUSES:
        raise InvalidOperationError(
            f"Version '{version.version_id}' has status '{version.status.value}'; only "
            "successful or partially successful versions can be restored to a sandbox.",
            resource_type="WorkloadVersion",
            resource_id=version.version_id,
        )


def _eligible_locations(version: WorkloadVersion) -> list[VersionLocation]:
    """Non-remote locations, the backup itself first, then by server name."""
    locations = [loc for loc in version.locations if not loc.location_info.is_remote_storage]
    if not locations:
        raise InvalidOperationError(
            f"Version '{version.version_id}' has no copy on a backup server; a sandbox can only "
            "be restored from a backup server.",
            resource_type="WorkloadVersion",
            resource_id=version.version_id,
        )
    return sorted(locations, key=lambda loc: (loc.location_id != version.version_id, loc.location_info.name))


def _find_location(version: WorkloadVersion, location_id: str | None) -> VersionLocation:
    """The eligible location with this ID; the default (first) one when location_id is None."""
    locations = _eligible_locations(version)
    if location_id is None:
        return locations[0]
    for loc in locations:
        if loc.location_id == location_id:
            return loc
    raise SandboxSettingsError(
        f"location_id '{location_id}' is not a backup-server location of version "
        f"'{version.version_id}'; see get_resources().",
        field="location_id",
    )


def _round_default_memory(spec_mb: int, max_mb: int) -> int:
    """The default memory: the backed-up memory rounded up, then capped at max_mb."""
    if spec_mb <= _SMALL_MEMORY_STEPS_MB[-1]:
        mb = next(step for step in _SMALL_MEMORY_STEPS_MB if step >= spec_mb)
        return min(mb, max_mb)
    mb = math.ceil(spec_mb / 1024) * 1024
    if mb > max_mb:
        mb = max(max_mb // 1024 * 1024, _MIN_MEMORY_MB)
    return mb


def _default_settings(plan: _Plan, workload_name: str, created_at: datetime) -> SandboxSettings:
    """The default settings at the plan's location (see get_default_settings())."""
    spec, loc = plan.spec, plan.location
    machine_type = spec.machine_type
    disks = tuple(
        SandboxDisk(
            controller=(
                SandboxDiskController.SATA if machine_type == SandboxMachineType.Q35
                else d.controller or SandboxDiskController.SATA
            ),
            space_reclamation=False,
        )
        for d in spec.disks
    )
    adapters: tuple[SandboxNetworkAdapter, ...] = ()
    if loc.networks:
        adapters = (SandboxNetworkAdapter(
            network_id=loc.networks[0].network_id, model=SandboxAdapterModel.RTL8139, sriov=False,
        ),)
    return SandboxSettings(
        location_id=loc.location_id,
        name=default_sandbox_name(workload_name, created_at, plan.existing_names),
        cpu_count=max(min(spec.cpu_count, loc.max_cpu_count), 1),
        memory_mb=_round_default_memory(spec.memory_mb, loc.max_memory_mb),
        cpu_compatibility_mode=False,
        hyperv_enlightenments=True,
        video_card=spec.video_card or SandboxVideoCard.VMVGA,
        machine_type=machine_type,
        network_adapters=adapters,
        disks=disks,
    )


def _validate(settings: SandboxSettings, plan: _Plan) -> None:
    """Raise SandboxSettingsError naming the first setting the chosen location does not allow.

    The rules that need no location are checked when SandboxSettings is built.
    """
    loc = plan.location
    if settings.name in plan.existing_names:
        raise SandboxSettingsError(f"name '{settings.name}' is already used by another VM.", field="name")
    if not 1 <= settings.cpu_count <= loc.max_cpu_count:
        raise SandboxSettingsError(f"cpu_count must be between 1 and {loc.max_cpu_count}.", field="cpu_count")
    if not loc.min_memory_mb <= settings.memory_mb <= loc.max_memory_mb:
        raise SandboxSettingsError(
            f"memory_mb must be between {loc.min_memory_mb} and {loc.max_memory_mb}.", field="memory_mb",
        )
    networks = {n.network_id: n for n in loc.networks}
    for adapter in settings.network_adapters:
        network = networks.get(adapter.network_id)
        if network is None:
            raise SandboxSettingsError(
                f"network_adapters: network '{adapter.network_id}' is not available at this location.",
                field="network_adapters",
            )
        if adapter.sriov and not network.supports_sriov:
            raise SandboxSettingsError(
                f"network_adapters: network '{network.name}' does not support SR-IOV.", field="network_adapters",
            )
    disk_count = len(plan.spec.disks)
    if len(settings.disks) != disk_count:
        raise SandboxSettingsError(
            f"disks must have one entry per backed-up disk ({disk_count}).", field="disks",
        )


def _require_ready_sandbox(activity: RestoreActivity) -> str:
    """Return the sandbox VM's ID, or raise unless the activity is a ready sandbox."""
    if activity.restore_type != RestoreType.INSTANT_SANDBOX:
        raise InvalidOperationError(
            f"Restore activity '{activity.activity_id}' is not a sandbox.",
            resource_type="RestoreActivity",
            resource_id=activity.activity_id,
        )
    if activity.status in _STARTING:
        raise ResourceNotReadyError(
            f"Sandbox of restore activity '{activity.activity_id}' is still starting "
            f"(status '{activity.status.value}')."
        )
    if activity.status != RestoreActivityStatus.SANDBOX_READY or not activity.sandbox_vm_id:
        raise InvalidOperationError(
            f"Sandbox of restore activity '{activity.activity_id}' is no longer running "
            f"(status '{activity.status.value}').",
            resource_type="RestoreActivity",
            resource_id=activity.activity_id,
        )
    return activity.sandbox_vm_id


class MachineSandboxCollection:
    """Instant restore of PS/VM backup versions to the appliance's built-in sandbox.

    A started sandbox is a restore activity of the workload (``RestoreType.INSTANT_SANDBOX``,
    ``restore_destination`` = the sandbox's name): find it with
    ``apm.activities.restore.list(restore_types=[RestoreType.INSTANT_SANDBOX])``, follow it
    with ``apm.activities.restore.get()`` until its status is
    ``RestoreActivityStatus.SANDBOX_READY``, check its screen with ``get_screenshot()``, view it
    with ``get_console_link()`` (browser) or ``open_vnc_bridge()`` (any VNC client), and stop and
    delete it with ``apm.activities.restore.cancel()``.

    Accessed via APMClient.machine.sandboxes; should not be instantiated directly.
    """

    def __init__(
        self,
        session: WebAPISession,
        backup_servers: BackupServerCollection,
        workloads: MachineWorkloadCollection,
        *,
        consoles: SandboxConsoles | None = None,
    ) -> None:
        self._session = session
        self._backup_servers = backup_servers
        self._workloads = workloads
        self._consoles = consoles or SandboxConsoles(session)

    async def get_latest_version(self, workload: MachineWorkload) -> WorkloadVersion:
        """Return the workload's newest successful or partially successful version.

        These are the versions a sandbox can be restored from.

        Args:
            workload: A PS or VM MachineWorkload.

        Raises:
            InvalidOperationError: The workload is not PS/VM, or has no successful or
                partially successful version.
        """
        _require_ps_vm(workload)
        try:
            return await self._workloads.get_latest_version(workload, statuses=list(_RESTORABLE_STATUSES))
        except ResourceNotFoundError as exc:
            raise InvalidOperationError(
                f"Workload '{workload.name}' has no successful or partially successful version "
                "to restore to a sandbox.",
                resource_type="Workload",
                resource_id=workload.workload_id,
            ) from exc

    async def _list_resource(self, workload: MachineWorkload, location: VersionLocation) -> SandboxLocation:
        raw: dict[str, Any] = await self._session.get(
            _VMM + "list_resource", params=_wl_params(workload), headers=_tunnel_headers(location.namespace),
        )
        return _parse_location(location, raw)

    async def _load(self, workload: MachineWorkload, version: WorkloadVersion, location: VersionLocation) -> _Plan:
        """Load the location's spec, storage, limits and VM names.

        Raises:
            InvalidOperationError: No storage at the location can hold the sandbox.
        """
        headers = _tunnel_headers(location.namespace)
        raw_spec, raw_repos, sandbox_location, basic = await asyncio.gather(
            self._session.post(
                _VMM + "list_spec",
                json={"versionIds": [location.location_id], "workloads": [_wl_ref(workload)]},
                headers=headers,
            ),
            self._session.post(_VMM + "list_repo", json={"isEdit": False}, headers=headers),
            self._list_resource(workload, location),
            self._session.get(_VMM + "list_basic", params=_wl_params(workload), headers=headers),
        )
        spec = _parse_spec(raw_spec)
        repos = _parse_repos(raw_repos)
        volume = spec.disks[0].volume_path if spec.disks else ""
        if not volume or not any(r.volume_path == volume for r in repos):
            raise InvalidOperationError(
                f"No storage on the backup's volume at '{sandbox_location.name}' can hold a sandbox "
                f"of version '{version.version_id}'.",
                resource_type="WorkloadVersion",
                resource_id=version.version_id,
            )
        return _Plan(
            version_location=location,
            spec=spec,
            location=sandbox_location,
            repo=repos[0],
            existing_names=_parse_guest_names(basic),
        )

    async def get_resources(self, workload: MachineWorkload, version: WorkloadVersion) -> SandboxResources:
        """Return the CPU and memory limits and the networks a sandbox of this version can use.

        There is one entry per version location the sandbox can be restored from (backup
        server locations only); the first is the default location.

        Args:
            workload: A PS or VM MachineWorkload.
            version:  A successful or partially successful version of the workload.

        Raises:
            InvalidOperationError: The workload is not PS/VM, the version is not restorable,
                or it has no backup-server location.
        """
        _require_ps_vm(workload)
        _require_restorable(version)
        locations = await asyncio.gather(
            *(self._list_resource(workload, loc) for loc in _eligible_locations(version))
        )
        return SandboxResources(locations=tuple(locations))

    async def get_default_settings(
        self, workload: MachineWorkload, version: WorkloadVersion, *, location_id: str | None = None,
    ) -> SandboxSettings:
        """Return the default sandbox settings, ready to pass to start().

        The defaults are for ``location_id``, or the default location (the first of
        ``get_resources()``):

        - CPU count and memory of the backed-up machine, capped at the location's maximum
          (memory rounded up first);
        - its video card (VMware SVGA if unknown) and machine type (Q35 unless it was PC);
        - CPU compatibility mode off, Hyper-V enlightenments on;
        - one RTL8139 adapter without SR-IOV on the first network, or none without networks;
        - one disk entry per backed-up disk: SATA with Q35, otherwise the backed-up
          controller (SATA if unknown); space reclamation off;
        - the name from ``default_sandbox_name()``: ``<workload name>_<YYYY-MM-DD>_<HHMMSS>``
          of the version time in local time, made unique among the existing VMs.

        To change some settings, pick the location first and then apply the changes:
        ``(await get_default_settings(wl, v, location_id=changes.get("location_id")))
        .with_changes(changes)``.

        Args:
            workload:    A PS or VM MachineWorkload.
            version:     A successful or partially successful version of the workload.
            location_id: A location of ``get_resources()``; the default location if omitted.

        Raises:
            SandboxSettingsError: ``location_id`` is not a backup-server location of the
                version (``field`` is ``"location_id"``).
            InvalidOperationError: The workload is not PS/VM, the version is not restorable,
                it has no backup-server location, or no storage at the location can hold the
                sandbox.
        """
        _require_ps_vm(workload)
        _require_restorable(version)
        plan = await self._load(workload, version, _find_location(version, location_id))
        return _default_settings(plan, workload.name, version.created_at)

    async def validate_settings(
        self, workload: MachineWorkload, version: WorkloadVersion, settings: SandboxSettings,
    ) -> None:
        """Check, without starting anything, that start() would accept these settings.

        Args:
            workload: A PS or VM MachineWorkload that is not retired.
            version:  A successful or partially successful version of the workload.
            settings: The sandbox settings to check.

        Raises:
            SandboxSettingsError: A setting is not allowed at the chosen location, or
                ``settings.location_id`` is not a backup-server location of the version;
                ``field`` names the setting.
            InvalidOperationError: The workload is not PS/VM or is retired, the version is
                not restorable, it has no backup-server location, or no storage at the
                location can hold the sandbox.
        """
        await self._prepare(workload, version, settings)

    async def start(
        self, workload: MachineWorkload, version: WorkloadVersion, settings: SandboxSettings,
    ) -> None:
        """Start a sandbox: instantly restore the version to a new VM on the built-in hypervisor.

        The settings are checked as by ``validate_settings()`` before anything is created.
        Returns once the request is accepted. The sandbox then appears among the workload's
        restore activities, with ``restore_destination`` = ``settings.name``, and can be used
        once its status is ``RestoreActivityStatus.SANDBOX_READY``.

        Args:
            workload: A PS or VM MachineWorkload that is not retired.
            version:  A successful or partially successful version of the workload.
            settings: Every sandbox setting (see ``get_default_settings()``).

        Raises:
            SandboxSettingsError: A setting is not allowed at the chosen location, or
                ``settings.location_id`` is not a backup-server location of the version;
                ``field`` names the setting.
            InvalidOperationError: The workload is not PS/VM or is retired, the version is
                not restorable, it has no backup-server location, no storage at the location
                can hold the sandbox, or the appliance does not have enough free memory for
                ``settings.memory_mb``.
            APIError: The sandbox could not be prepared or started.
        """
        plan = await self._prepare(workload, version, settings)
        headers = _tunnel_headers(plan.version_location.namespace)
        raw_macs = await asyncio.gather(*(
            self._session.get(_VMM + "gen_mac", params=_wl_params(workload), headers=headers)
            for _ in settings.network_adapters
        ))
        macs = [str(m.get("mac") or "") for m in raw_macs]
        if not all(macs) or len({m.lower() for m in macs}) != len(macs):
            raise APIError(
                "The sandbox could not be prepared: the appliance returned no usable MAC address "
                "for every network adapter."
            )
        body = _open_in_vm_body(plan, workload, version, settings, macs)
        try:
            await self._session.post(_VMM + "open_in_vm", json=body, headers=headers)
        except APIError as exc:
            if exc.error_code == _NOT_ENOUGH_MEMORY_CODE:
                raise InvalidOperationError(
                    f"Not enough free memory on the appliance for a {settings.memory_mb} MB sandbox; "
                    "lower the memory or stop other sandboxes.",
                    resource_type="Workload",
                    resource_id=workload.workload_id,
                    error_code=exc.error_code,
                    response_body=exc.response_body,
                ) from exc
            raise

    async def _prepare(
        self, workload: MachineWorkload, version: WorkloadVersion, settings: SandboxSettings,
    ) -> _Plan:
        """Run the checks of validate_settings(); return the plan of the chosen location."""
        _require_ps_vm(workload)
        _check_active_for_write(workload, "cannot start a sandbox")
        _require_restorable(version)
        plan = await self._load(workload, version, _find_location(version, settings.location_id))
        _validate(settings, plan)
        return plan

    async def get_console_link(self, activity: RestoreActivity) -> str:
        """Return a link that opens the sandbox's console (web VNC) in a browser.

        The sandbox is powered on first if needed. The link opens on the backup server that
        runs the sandbox, which must be reachable from the browser. It works once and needs no
        sign-in: treat it as a credential and do not log or share it.

        Args:
            activity: The sandbox's restore activity, with status
                ``RestoreActivityStatus.SANDBOX_READY`` (see
                ``RestoreActivityCollection.get()``).

        Returns:
            The absolute link.

        Raises:
            ResourceNotReadyError: The sandbox is still starting.
            InvalidOperationError: The activity is not a running sandbox, or the sandbox could
                not be powered on.
            ResourceNotFoundError: The sandbox VM, or the backup server that runs it, no
                longer exists.
        """
        _require_ready_sandbox(activity)
        return await self._connect_to_vm(activity)

    async def _connect_to_vm(self, activity: RestoreActivity) -> str:
        # The server is looked up first: connect_to_vm powers the VM on, logs an activity entry
        # and mints a single-use link, none of which should happen if the lookup then fails.
        origin = await self._backup_servers._origin_for_namespace(activity.namespace)
        if origin is None:
            raise ResourceNotFoundError(
                f"The backup server that runs the sandbox ('{activity.namespace}') was not found.",
                resource_type="BackupServer", resource_id=activity.namespace,
            )
        try:
            with _not_found_as(
                "RestoreActivity", activity.activity_id,
                message=f"The sandbox VM of restore activity '{activity.activity_id}' no longer exists.",
            ):
                resp = await self._session.post(
                    _VMM + "connect_to_vm",
                    json={
                        "guestId": activity.sandbox_vm_id,
                        "workload": {"uid": activity.workload_id, "namespace": activity.workload_namespace},
                    },
                    headers=_tunnel_headers(activity.namespace),
                )
        except APIError as exc:
            if exc.error_code == _POWER_ON_FAILED_CODE:
                raise InvalidOperationError(
                    "The sandbox could not be powered on.",
                    resource_type="RestoreActivity",
                    resource_id=activity.activity_id,
                    error_code=exc.error_code,
                    response_body=exc.response_body,
                ) from exc
            raise
        # The server that runs the VM mints the link on its own host as a relative path.
        path = str(resp["url"])
        return path if "://" in path else origin + path

    async def get_screenshot(
        self,
        activity: RestoreActivity,
        *,
        keys: Sequence[SandboxKey] = (),
        delay_seconds: float | None = None,
    ) -> bytes:
        """Capture the sandbox's current screen, for example to check that its OS booted.

        Like ``get_console_link()``, the sandbox is powered on first if needed. The client
        keeps one console access per sandbox VM and reuses it across calls; each new access
        adds an entry to the activity's log. Without ``keys`` it sends no input to the guest.
        The image can show whatever the guest displays: handle it as sensitive.

        Keys are pressed in order, then the screen is captured ``delay_seconds`` later. Typical
        uses: ``SandboxKey.SHIFT`` wakes a blanked screen, ``SandboxKey.ENTER`` starts the
        default boot entry, and ``SandboxKey.CTRL_ALT_DELETE`` opens a Windows sign-in screen
        (it can also restart a guest that shows a text console).

        Args:
            activity: The sandbox's restore activity, with status
                ``RestoreActivityStatus.SANDBOX_READY``.
            keys: Keys to press before the capture.
            delay_seconds: Seconds between the keys and the capture, 0 to 60; defaults to 2
                when ``keys`` are given. Only allowed with ``keys``.

        Returns:
            The screen as PNG file content.

        Raises:
            ValueError: ``delay_seconds`` is given without ``keys``, or is out of range.
            ResourceNotReadyError: The sandbox is still starting.
            InvalidOperationError: The activity is not a running sandbox, or the sandbox could
                not be powered on.
            ResourceNotFoundError: The sandbox VM, or the backup server that runs it, no
                longer exists.
            PermissionDeniedError: The console refused this user.
            APIError: The console could not be reached or did not answer as expected.
            ConnectionTimeoutError: The console did not answer, or send its screen, in time.
        """
        if delay_seconds is not None and not keys:
            raise ValueError("delay_seconds applies only when keys are given.")
        if delay_seconds is None:
            delay_seconds = _KEY_DELAY_DEFAULT_S if keys else 0.0
        if not 0 <= delay_seconds <= _KEY_DELAY_MAX_S:
            raise ValueError(f"delay_seconds must be between 0 and {_KEY_DELAY_MAX_S}.")
        return await self._console(activity).screenshot(keys, delay_seconds)

    @contextlib.asynccontextmanager
    async def open_vnc_bridge(
        self, activity: RestoreActivity, *, port: int = 0,
    ) -> AsyncIterator[SandboxVncEndpoint]:
        """Relay VNC clients on this machine to the sandbox's screen while the context is open.

        Any VNC client can connect to the yielded address, without a password; the relay
        listens on the loopback interface only, but every user of this machine can reach it.
        Each VNC client gets its own connection to the sandbox; a VNC client whose connection
        to the sandbox fails is disconnected. Like ``get_screenshot()``, it powers the sandbox
        on if needed. ``APMClient.disconnect()`` also closes every sandbox connection of the bridge.

        Args:
            activity: The sandbox's restore activity, with status
                ``RestoreActivityStatus.SANDBOX_READY``.
            port: Local TCP port to listen on; 0 picks a free one.

        Yields:
            The local address VNC clients connect to.

        Raises:
            ResourceNotReadyError: The sandbox is still starting.
            InvalidOperationError: The activity is not a running sandbox, or the sandbox could
                not be powered on.
            ResourceNotFoundError: The sandbox VM, or the backup server that runs it, no
                longer exists.
            PermissionDeniedError: The console refused this user (checked once on open).
            APIError: The console could not be reached (checked once on open).
            ConnectionTimeoutError: The console did not answer in time (checked once on open).
            OSError: The port could not be bound.
        """
        async with self._console(activity).serve_vnc(_BRIDGE_HOST, port) as bound:
            yield SandboxVncEndpoint(host=_BRIDGE_HOST, port=bound)

    def _console(self, activity: RestoreActivity) -> SandboxConsole:
        """This client's console for the activity's sandbox VM (which must be ready)."""
        vm_id = _require_ready_sandbox(activity)

        async def mint() -> str:
            return await self._connect_to_vm(activity)

        return self._consoles.get(activity.namespace, vm_id, mint)
