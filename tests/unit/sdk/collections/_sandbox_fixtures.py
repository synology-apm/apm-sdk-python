"""Raw vmm payloads, workload and version models, and a fake session for the SDK sandbox
collection tests.

conftest.py builds the ``env`` fixture from FakeVmm. The sandbox model builders and the IDs
shared with the CLI and MCP tests live in tests/unit/sandbox_builders.py.
"""
from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator
from typing import Any, NamedTuple

from synology_apm.sdk._console import SandboxConsoles
from synology_apm.sdk._http import WebAPISession
from synology_apm.sdk.collections.backup_servers import BackupServerCollection
from synology_apm.sdk.collections.machine import MachineWorkloadCollection
from synology_apm.sdk.collections.machine_sandbox import MachineSandboxCollection
from synology_apm.sdk.enums import (
    MachineWorkloadType,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import APIError
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.sandbox_builders import ACT_NS, CREATED_AT, EXEC_ID, LOC_A, WL_ID, WL_NS

GIB = 1024 ** 3
VMM = "/api/v1/portal/vmm/"
SERVERS_PATH = "/api/v1/infra/backup_server"
VERSIONS_PATH = f"/api/v1/workload/{WL_NS}/{WL_ID}/version"

NS_A = "123e4567-e89b-12d3-a456-426614174011"
LOC_B = "123e4567-e89b-12d3-a456-426614174012"   # backup copy on apm-server-02
NS_B = "123e4567-e89b-12d3-a456-426614174013"
LOC_R = "123e4567-e89b-12d3-a456-426614174014"   # copy on remote storage DSM-Storage

_PLAN = ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE)


def make_workload(
    wl_type: MachineWorkloadType = MachineWorkloadType.PS, *, is_retired: bool = False,
) -> MachineWorkload:
    name = {MachineWorkloadType.VM: "vm-web-01", MachineWorkloadType.PC: "CORP-PC-001"}.get(wl_type, "prod-server-01")
    return MachineWorkload(
        workload_id=WL_ID, name=name, category=WorkloadCategory.MACHINE, namespace=WL_NS,
        last_backup_at=None, is_retired=is_retired, protected_data_bytes=0,
        status=WorkloadStatus.SUCCESS, plan=_PLAN, workload_type=wl_type, agent_version=None,
    )


def make_location(
    location_id: str, namespace: str, name: str, endpoint: str, *, remote: bool = False,
) -> VersionLocation:
    info = LocationInfo(is_remote_storage=remote, identifier=namespace, name=name, endpoint=endpoint, vault=None)
    return VersionLocation(namespace=namespace, location_info=info, location_id=location_id)


# The backup's own location sorts first although its server name sorts last.
LOCATION_A = make_location(LOC_A, NS_A, "apm-server-03", "192.0.2.3")
LOCATION_B = make_location(LOC_B, NS_B, "apm-server-02", "192.0.2.2")
LOCATION_R = make_location(LOC_R, "storage-uid-001", "DSM-Storage", "192.0.2.20:8444", remote=True)


def make_version(
    locations: list[VersionLocation] | None = None, status: VersionStatus = VersionStatus.SUCCESS,
) -> WorkloadVersion:
    return WorkloadVersion(
        version_id=LOC_A, workload_id=WL_ID, namespace=NS_A, created_at=CREATED_AT, status=status,
        execution_id="EX_1", locked=False, changed_size_bytes=0,
        locations=[LOCATION_R, LOCATION_B, LOCATION_A] if locations is None else locations,
    )


PS_VDISKS: list[dict[str, Any]] = [
    {"capacity": str(64 * GIB), "vdiskMode": 16, "volumePath": "/volume1", "name": "disk-0"},
    {"capacity": str(10 * GIB + 1), "vdiskMode": 16, "volumePath": "/volume1", "name": "disk-1"},
]
PS_SPEC: dict[str, Any] = {
    "vcpuNum": "4", "vramSize": "4096", "videoCard": "vga", "machineType": "q35", "useOvmf": True,
    "osId": "windows", "vdiskStruct": PS_VDISKS,
    "birthInfo": {"desc": EXEC_ID, "source": "backup"},
}
VM_SPEC: dict[str, Any] = {
    "vcpuNum": "2", "vramSize": "2048", "videoCard": "cirrus", "machineType": "pc", "useOvmf": False,
    "osId": "linux",
    "vdiskStruct": [{"capacity": str(16 * GIB), "vdiskMode": 2, "volumePath": "/volume1", "name": "disk-0"}],
    "birthInfo": {"desc": EXEC_ID, "source": "backup"},
}
REPOS: list[dict[str, Any]] = [
    {"repoId": "repo-b", "name": "Sandbox-B", "hostId": "host-b", "hostName": "apm-server-03",
     "size": "1000", "used": "300", "allocatedSize": "50", "volumePath": "/volume1"},
    {"repoId": "repo-a", "name": "Sandbox-A", "hostId": "host-a", "hostName": "apm-server-03",
     "size": "2000", "used": "2500", "allocatedSize": "10", "volumePath": "/volume1"},
    {"repoId": "repo-x", "name": "Volume2-Share", "hostId": "host-x", "hostName": "apm-server-03",
     "size": "9000", "used": "0", "allocatedSize": "0", "volumePath": "/volume2"},
]
RESOURCE: dict[str, Any] = {
    "maxCpuNum": 8,
    "maxRamSize": 16384 * 1024,  # KB
    "networks": [
        {"networkId": "net-1", "name": "Default VM Network", "permitted": True, "supportSriov": False},
        {"networkId": "net-2", "name": "SR-IOV Network", "permitted": True, "supportSriov": True},
        {"networkId": "net-3", "name": "Restricted", "permitted": False, "supportSriov": True},
    ],
}
GUESTS: dict[str, Any] = {"guests": [{"name": "existing-vm"}]}
MACS = ["02:11:32:00:00:01", "02:11:32:00:00:02", "02:11:32:00:00:03"]


def detail_error_body(code: int, category: str, section: str, key: str, message: str) -> dict[str, Any]:
    """The HTTP 500 body of a failed vmm call (tunnel error with one detail entry)."""
    return {"error": {
        "code": 500, "status": "Internal Server Error", "message": "server tunnel invoke failed",
        "details": [{
            "@type": "type.googleapis.com/api.ErrorDetail", "category": category, "errorCode": code,
            "message": message, "errorString": {"class": category, "section": section, "key": key},
            "server": {"type": "dp", "namespace": NS_A},
        }],
    }}


def api_error(code: int, category: str, section: str, key: str, message: str) -> APIError:
    """The APIError the session raises for that body (detail message and code)."""
    return APIError(message, error_code=code, response_body=detail_error_body(code, category, section, key, message))


ERR_NOT_ENOUGH_MEMORY = api_error(
    7026, "abc", "device_workload", "insufficient_mem_restore_to_vmm", "[OpenInVm] not enough ram",
)
ERR_POWER_ON = api_error(932, "dsm", "vmm", "poweron_guest_sel_host", "power on failed")
ERR_SYSTEM = api_error(4300, "dsm", "error", "error_system", "system error")


def server_raw(namespace: str, name: str, addr: str, role: str) -> dict[str, Any]:
    return {"id": f"id-{name}", "namespace": namespace, "role": role,
            "spec": {"addr": addr}, "status": {"hostName": name}}


# The activity's server (ACT_NS) is the primary unless a test swaps the list.
SERVERS = [
    server_raw(ACT_NS, "apm-server-01", "192.0.2.1", "LEADER"),
    server_raw(NS_B, "apm-server-02", "192.0.2.2", "REPLICA"),
]


class Call(NamedTuple):
    method: str
    op: str
    body: Any
    headers: dict[str, str]


class FakeVmm:
    """Stands in for the session's get/post on the vmm endpoints, the backup server list and
    the workload's version list, recording every call.

    ``responses`` holds the answer per op; ``respond(op, value, namespace=...)`` sets the
    answer for the calls routed to one backup server only.
    """

    def __init__(self, spec: dict[str, Any] | None = None) -> None:
        self.responses: dict[str, Any] = {
            "list_spec": {"specs": [spec or PS_SPEC]},
            "list_repo": {"repos": REPOS},
            "list_resource": RESOURCE,
            "list_basic": GUESTS,
            "open_in_vm": {"taskId": "task-001"},
            "connect_to_vm": {"url": "/sharing/AbCdEf123"},
            "backup_server": {"backupServers": SERVERS, "total": len(SERVERS)},
            "version": {"versions": [], "total": 0},
        }
        self._by_namespace: dict[tuple[str, str], Any] = {}
        self.errors: dict[str, Exception] = {}
        self.calls: list[Call] = []
        self.macs: Iterator[Any] = iter({"mac": m} for m in MACS)  # gen_mac responses, in order
        self._in_flight: dict[str, int] = {}
        self.max_in_flight: dict[str, int] = {}  # per op group: "load" or "gen_mac"

    def respond(self, op: str, response: Any, *, namespace: str) -> None:
        self._by_namespace[(op, namespace)] = response

    def _handle(self, method: str, path: str, body: Any, headers: dict[str, str] | None) -> Any:
        if path == SERVERS_PATH:
            op = "backup_server"
        elif path == VERSIONS_PATH:
            op = "version"
        else:
            assert path.startswith(VMM), path
            op = path[len(VMM):]
        headers = dict(headers or {})
        self.calls.append(Call(method, op, copy.deepcopy(body), headers))
        if op in self.errors:
            raise self.errors[op]
        if op == "gen_mac":
            return next(self.macs)
        key = (op, headers.get("x-syno-tunnel-route", ""))
        return copy.deepcopy(self._by_namespace.get(key, self.responses[op]))

    async def _call(self, method: str, path: str, body: Any, headers: dict[str, str] | None) -> Any:
        """Yield once per request so that concurrently issued requests overlap."""
        group = "gen_mac" if path.endswith("gen_mac") else "load"
        self._in_flight[group] = self._in_flight.get(group, 0) + 1
        self.max_in_flight[group] = max(self.max_in_flight.get(group, 0), self._in_flight[group])
        try:
            await asyncio.sleep(0)
            return self._handle(method, path, body, headers)
        finally:
            self._in_flight[group] -= 1

    async def get(self, path: str, params: Any = None, headers: dict[str, str] | None = None) -> Any:
        return await self._call("GET", path, params, headers)

    async def post(self, path: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        return await self._call("POST", path, json, headers)

    def ops(self) -> list[str]:
        return [c.op for c in self.calls]

    def one(self, op: str) -> Call:
        matches = [c for c in self.calls if c.op == op]
        assert len(matches) == 1, (op, self.ops())
        return matches[0]


def make_sandboxes(session: WebAPISession, *, consoles: SandboxConsoles | None = None) -> MachineSandboxCollection:
    return MachineSandboxCollection(
        session, BackupServerCollection(session), MachineWorkloadCollection(session), consoles=consoles,
    )


class Env(NamedTuple):
    sandboxes: MachineSandboxCollection
    vmm: FakeVmm
