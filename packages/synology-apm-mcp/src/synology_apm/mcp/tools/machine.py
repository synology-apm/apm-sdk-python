"""Machine workload tools: shared workload tools, sandboxes, and file-server management."""
from __future__ import annotations

from typing import Annotated, Any, NotRequired

from fastmcp import Context
from fastmcp.utilities.types import Image
from pydantic import StrictBool, StrictInt
from typing_extensions import TypedDict  # pydantic needs it on Python < 3.12

from synology_apm.mcp._enums import (
    FileServerTypeLiteral,
    SandboxAdapterModelLiteral,
    SandboxDiskControllerLiteral,
    SandboxKeyLiteral,
    SandboxMachineTypeLiteral,
    SandboxVideoCardLiteral,
)
from synology_apm.mcp._errors import run_tool
from synology_apm.mcp._helpers import JSON_LIST_VALIDATOR, ToolResult, resolve_machine_version, to_enum_list
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import run_audited_tool
from synology_apm.mcp.tools._workload import register_workload_tools
from synology_apm.sdk import (
    APMClient,
    FileServerAddRequest,
    FileServerPathSelector,
    FileServerType,
    FileServerUpdateRequest,
    MachineWorkload,
    SandboxKey,
    WorkloadVersion,
)


class _FsSelectorParam(TypedDict):
    path: str
    excluded_paths: NotRequired[list[str]]


class _SandboxAdapterParam(TypedDict):
    network_id: str
    model: SandboxAdapterModelLiteral
    sriov: StrictBool


class _SandboxDiskParam(TypedDict):
    controller: SandboxDiskControllerLiteral
    space_reclamation: StrictBool


class _SandboxSettingsParam(TypedDict, total=False):
    """Sandbox settings to change; every key is optional and keeps its default when omitted."""
    location_id: str
    name: str
    cpu_count: StrictInt
    memory_mb: StrictInt
    cpu_compatibility_mode: StrictBool
    hyperv_enlightenments: StrictBool
    video_card: SandboxVideoCardLiteral
    machine_type: SandboxMachineTypeLiteral
    network_adapters: list[_SandboxAdapterParam]
    disks: list[_SandboxDiskParam]


_ADD_DESCRIPTION = (
    "Add a file server workload to APM. Requires namespace, host IP, server type "
    "(smb/nas/nutanix/netapp), protection plan, and credentials; enable_vss only applies to smb "
    "servers (ignored otherwise). Fails if a file server at this host_ip is already enrolled in "
    "the same plan on the same backup server. Backup scope: pass `path` for a single unrestricted "
    "path (default \"\" = whole file server root), or `selectors` for multiple paths and/or excluded "
    "sub-paths (excluded_paths is optional per entry, defaults to none excluded). Supply only one "
    "of path/selectors."
)
_UPDATE_DESCRIPTION = (
    "Update connection settings and/or backup scope for an existing file server (FS) workload by "
    "ID. Every field must be supplied explicitly — call get_machine_workload first and resupply "
    "its current values (including fs_config.selectors) for anything you don't intend to change; "
    "enable_vss only applies to smb servers (ignored otherwise). login_password=None keeps the "
    "currently stored password (APM does not expose it for re-reading); pass a new password to "
    "rotate it. Backup scope: pass exactly one of `path` (single unrestricted path) or "
    "`selectors`, in the shape get_machine_workload's fs_config.selectors returns — for multiple "
    "paths and/or excluded sub-paths. Omitting both raises an error rather than "
    "silently resetting the backup scope to a single unrestricted path. Fails if workload_id is "
    "not an FS workload, or if the updated host_ip conflicts with another file server under the "
    "same plan."
)


def _selectors_from_entries(entries: list[_FsSelectorParam]) -> tuple[FileServerPathSelector, ...]:
    if not entries:
        raise ValueError("selectors must contain at least one entry.")
    return tuple(
        FileServerPathSelector(path=e["path"], excluded_paths=tuple(e.get("excluded_paths") or ()))
        for e in entries
    )


def _resolve_add_selectors(path: str, selectors: list[_FsSelectorParam] | None) -> tuple[FileServerPathSelector, ...]:
    if selectors is not None and path:
        raise ValueError("Pass either `path` or `selectors`, not both.")
    if selectors is not None:
        return _selectors_from_entries(selectors)
    return (FileServerPathSelector(path=path),)


def _resolve_update_selectors(
    path: str | None, selectors: list[_FsSelectorParam] | None
) -> tuple[FileServerPathSelector, ...]:
    if path is not None and selectors is not None:
        raise ValueError("Pass either `path` or `selectors`, not both.")
    if selectors is not None:
        return _selectors_from_entries(selectors)
    if path is not None:
        return (FileServerPathSelector(path=path),)
    raise ValueError(
        "Pass `path` (single path) or `selectors` (list of path/excluded_paths entries) "
        "explicitly — call get_machine_workload first and resupply the workload's current "
        "fs_config.selectors if you don't intend to change them."
    )


_SANDBOX_VERSION_HINT = (
    "version_id defaults to the latest successful or partially successful version."
)


async def _sandbox_version(
    apm: APMClient, workload_id: str, namespace: str, version_id: str | None,
) -> tuple[MachineWorkload, WorkloadVersion]:
    """The workload and version_id, or its latest version a sandbox can use."""
    workload = await apm.machine.workloads.get(workload_id, namespace)
    if version_id:
        return workload, await apm.machine.workloads.get_version(workload, version_id)
    return workload, await apm.machine.sandboxes.get_latest_version(workload)


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    register_workload_tools(
        registrar,
        name_prefix="machine",
        variant="machine",
        collection_fn=lambda apm: apm.machine.workloads,
        serializer=lambda wl: wl.to_dict(),
    )

    # ── Machine-only readonly tools ───────────────────────────────────────────

    @registrar.tool(description="Get the URL of the verification video for a machine workload version (PS/VM only, and only for versions whose verify_status is success). Returns a time-limited download URL.")
    async def get_machine_verification_video_url(
        ctx: Context,
        version_id: str,
        workload_id: str,
        namespace: str,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _get() -> dict[str, Any]:
            workload, version = await resolve_machine_version(
                apm, workload_id=workload_id, namespace=namespace, version_id=version_id,
            )
            url = await apm.machine.workloads.get_verification_video_url(workload, version)
            return {"url": url, "workload_id": workload.workload_id, "version_id": version.version_id}

        return await run_tool(_get())

    # ── Sandbox (instant restore of a PS/VM version to the built-in sandbox) ──

    @registrar.tool(description=(
        "List the CPU and memory limits and the networks a sandbox of a PS/VM version can use, "
        f"per location it can be restored from (the first is the default). {_SANDBOX_VERSION_HINT}"
    ))
    async def get_machine_sandbox_resources(
        ctx: Context, workload_id: str, namespace: str, version_id: str | None = None,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _get() -> dict[str, Any]:
            workload, version = await _sandbox_version(apm, workload_id, namespace, version_id)
            resources = await apm.machine.sandboxes.get_resources(workload, version)
            return {"version_id": version.version_id, **resources.to_dict()}

        return await run_tool(_get())

    @registrar.tool("operator", description=(
        "Start a sandbox: instantly restore a PS/VM version to a new VM on the appliance's built-in "
        f"hypervisor, for a recovery drill. {_SANDBOX_VERSION_HINT} settings holds only the settings to "
        "change; the others keep the defaults of settings.location_id (default: the first location of "
        "get_machine_sandbox_resources, which lists the allowed values). network_adapters and disks "
        "replace the default lists: network_adapters=[] means no network; disks has one entry per "
        "backed-up disk, in order. An invalid setting fails with an error whose field names it (other_field "
        "names the second setting when two settings conflict). "
        "Returns the version used and the final settings; dry_run=true only checks them. The sandbox is "
        "the workload's restore activity with restore_type instant_sandbox and restore_destination = "
        "settings.name: find it with list_restore_activities (workload_id + workload_namespace), poll "
        "get_restore_activity (include_logs=false) until status is sandbox_ready, and stop and delete it "
        "with cancel_restore_activity."
    ))
    async def start_machine_sandbox(
        ctx: Context,
        workload_id: str,
        namespace: str,
        version_id: str | None = None,
        settings: _SandboxSettingsParam | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        changes: dict[str, Any] = dict(settings or {})

        async def _start() -> dict[str, Any]:
            workload, version = await _sandbox_version(apm, workload_id, namespace, version_id)
            defaults = await apm.machine.sandboxes.get_default_settings(
                workload, version, location_id=changes.get("location_id"),
            )
            final = defaults.with_changes(changes)
            if dry_run:
                await apm.machine.sandboxes.validate_settings(workload, version, final)
            else:
                await apm.machine.sandboxes.start(workload, version, final)
            return {"version_id": version.version_id, "settings": final.to_dict()}

        if dry_run:
            return await run_tool(_start())
        params = {"workload_id": workload_id, "namespace": namespace, "version_id": version_id}
        return await run_audited_tool(_start(), "start_machine_sandbox", params)

    @registrar.tool("operator", description=(
        "Get a link that opens a sandbox's console (web VNC) in a browser; the sandbox is powered on first "
        "if needed, and each call adds an entry to its activity log. activity_id "
        "is the sandbox's restore activity, whose status must be sandbox_ready. The link opens on the "
        "backup server that runs the sandbox, so the browser must reach the link's host. "
        "The link works ONCE and needs no sign-in: open it in a browser tool immediately, never show it in "
        "chat, logs, or files, and request a new one for every open. "
        "To only check the screen, use get_machine_sandbox_screenshot instead."
    ))
    async def get_machine_sandbox_console_link(ctx: Context, activity_id: str) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _get() -> dict[str, Any]:
            activity = await apm.activities.restore.get(activity_id, include_logs=False)
            return {"url": await apm.machine.sandboxes.get_console_link(activity)}

        return await run_audited_tool(_get(), "get_machine_sandbox_console_link", {"activity_id": activity_id})

    @registrar.tool("operator", description=(
        "Get an image of a sandbox's current screen, for example to check that the restored OS booted "
        "(a login screen or desktop means it did; the OS can still be booting after sandbox_ready). "
        "activity_id is the sandbox's restore activity, whose status must be sandbox_ready. Can be called "
        "repeatedly; the sandbox is powered on first if needed, and usually only the "
        "first call for a sandbox adds an entry to its activity log. The image can show anything on the "
        "guest's screen: treat it as sensitive. Optional keys are pressed first, in order, and the image is taken "
        "delay_seconds later (default 2, max 60; only with keys): 'shift' wakes a blanked screen, 'enter' "
        "starts the default boot entry, 'ctrl_alt_delete' opens a Windows sign-in screen (it can also "
        "restart a guest showing a text console). Without keys, no input is sent to the guest."
    ))
    async def get_machine_sandbox_screenshot(
        ctx: Context,
        activity_id: str,
        keys: Annotated[list[SandboxKeyLiteral], JSON_LIST_VALIDATOR] | None = None,
        delay_seconds: float | None = None,
    ) -> Image:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _get() -> Image:
            activity = await apm.activities.restore.get(activity_id, include_logs=False)
            png = await apm.machine.sandboxes.get_screenshot(
                activity, keys=to_enum_list(SandboxKey, keys) or [], delay_seconds=delay_seconds,
            )
            return Image(data=png, format="png")

        return await run_audited_tool(_get(), "get_machine_sandbox_screenshot", {"activity_id": activity_id})

    # ── Machine-only admin tools ──────────────────────────────────────────────

    @registrar.tool("admin", description=_ADD_DESCRIPTION)
    async def add_machine_file_server(
        ctx: Context,
        namespace: str,
        host_ip: str,
        server_type: FileServerTypeLiteral,
        plan_id: str,
        login_user: str,
        login_password: str,
        path: str = "",
        selectors: Annotated[list[_FsSelectorParam], JSON_LIST_VALIDATOR] | None = None,
        host_port: int = 445,
        enable_vss: bool = False,
        connection_timeout_seconds: int = 180,
        trigger_backup: bool = False,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _add() -> dict[str, Any]:
            request = FileServerAddRequest(
                namespace=namespace,
                host_ip=host_ip,
                server_type=FileServerType(server_type),
                plan_id=plan_id,
                login_user=login_user,
                login_password=login_password,
                host_port=host_port,
                enable_vss=enable_vss,
                connection_timeout_seconds=connection_timeout_seconds,
                trigger_backup=trigger_backup,
                selectors=_resolve_add_selectors(path, selectors),
            )
            await apm.machine.workloads.add_file_server(request)
            return {"ok": True}

        return await run_audited_tool(
            _add(),
            action="add_machine_file_server",
            params={"host_ip": host_ip, "namespace": namespace},
        )

    @registrar.tool("admin", description=_UPDATE_DESCRIPTION)
    async def update_machine_file_server(
        ctx: Context,
        workload_id: str,
        namespace: str,
        host_ip: str,
        login_user: str,
        login_password: str | None,
        host_port: int,
        enable_vss: bool,
        connection_timeout_seconds: int,
        path: str | None = None,
        selectors: Annotated[list[_FsSelectorParam], JSON_LIST_VALIDATOR] | None = None,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _update() -> dict[str, Any]:
            workload = await apm.machine.workloads.get(workload_id, namespace)
            request = FileServerUpdateRequest(
                host_ip=host_ip,
                login_user=login_user,
                login_password=login_password,
                host_port=host_port,
                enable_vss=enable_vss,
                connection_timeout_seconds=connection_timeout_seconds,
                selectors=_resolve_update_selectors(path, selectors),
            )
            await apm.machine.workloads.update_file_server(workload, request)
            return {"ok": True, "workload_id": workload.workload_id}

        return await run_audited_tool(
            _update(),
            action="update_machine_file_server",
            params={"workload_id": workload_id, "host_ip": host_ip},
        )
