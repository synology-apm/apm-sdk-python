"""Machine workload tools: shared workload tools + file-server management."""
from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context

from synology_apm.mcp._enums import FileServerTypeLiteral
from synology_apm.mcp._errors import run_tool
from synology_apm.mcp._helpers import JSON_LIST_VALIDATOR, resolve_machine_version
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import run_audited_tool
from synology_apm.mcp.tools._workload import register_workload_tools
from synology_apm.sdk import (
    APMClient,
    FileServerAddRequest,
    FileServerPathSelector,
    FileServerType,
    FileServerUpdateRequest,
)

_SELECTOR_SHAPE = '`selectors` — a list of {"path": str, "excluded_paths": [str, ...]} entries'

_ADD_DESCRIPTION = (
    "Add a file server workload to APM. Requires namespace, host IP, server type "
    "(smb/nas/nutanix/netapp), protection plan, and credentials; enable_vss only applies to smb "
    "servers (ignored otherwise). Fails if a file server at this host_ip is already enrolled in "
    "the same plan on the same backup server. Backup scope: pass `path` for a single unrestricted "
    f"path (default \"\" = whole file server root), or {_SELECTOR_SHAPE} — for multiple paths "
    "and/or excluded sub-paths (excluded_paths is optional per entry, defaults to none excluded). "
    "Supply only one of path/selectors."
)
_UPDATE_DESCRIPTION = (
    "Update connection settings and/or backup scope for an existing file server (FS) workload by "
    "ID. Every field must be supplied explicitly — call get_machine_workload first and resupply "
    "its current values (including fs_config.selectors) for anything you don't intend to change; "
    "enable_vss only applies to smb servers (ignored otherwise). login_password=None keeps the "
    "currently stored password (APM does not expose it for re-reading); pass a new password to "
    "rotate it. Backup scope: pass exactly one of `path` (single unrestricted path) or "
    f"{_SELECTOR_SHAPE}, matching the shape get_machine_workload's fs_config.selectors returns — "
    "for multiple paths and/or excluded sub-paths. Omitting both raises an error rather than "
    "silently resetting the backup scope to a single unrestricted path. Fails if workload_id is "
    "not an FS workload, or if the updated host_ip conflicts with another file server under the "
    "same plan."
)


def _selectors_from_entries(entries: list[dict[str, Any]]) -> tuple[FileServerPathSelector, ...]:
    """Convert the structured `selectors` param into a FileServerPathSelector tuple.

    Each entry: {"path": str, "excluded_paths": list[str] (optional, default [])} — the same
    shape get_machine_workload returns for fs_config.selectors.
    """
    if not entries:
        raise ValueError("selectors must contain at least one entry.")
    result: list[FileServerPathSelector] = []
    for i, entry in enumerate(entries):
        entry_path = entry.get("path")
        if not isinstance(entry_path, str):
            raise ValueError(f"selectors[{i}] must have a string 'path' key.")
        excluded = entry.get("excluded_paths") or []
        if not isinstance(excluded, list) or not all(isinstance(p, str) for p in excluded):
            raise ValueError(f"selectors[{i}]['excluded_paths'] must be a list of strings.")
        result.append(FileServerPathSelector(path=entry_path, excluded_paths=tuple(excluded)))
    return tuple(result)


def _resolve_add_selectors(path: str, selectors: list[dict[str, Any]] | None) -> tuple[FileServerPathSelector, ...]:
    if selectors is not None and path:
        raise ValueError("Pass either `path` or `selectors`, not both.")
    if selectors is not None:
        return _selectors_from_entries(selectors)
    return (FileServerPathSelector(path=path),)


def _resolve_update_selectors(
    path: str | None, selectors: list[dict[str, Any]] | None
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


def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register all machine workload tools onto server."""

    register_workload_tools(
        registrar,
        name_prefix="machine",
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
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _get() -> dict[str, Any]:
            workload, version = await resolve_machine_version(
                apm, workload_id=workload_id, namespace=namespace, version_id=version_id,
            )
            url = await apm.machine.workloads.get_verification_video_url(workload, version)
            return {"url": url, "workload_id": workload.workload_id, "version_id": version.version_id}

        return await run_tool(_get())

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
        selectors: Annotated[list[dict[str, Any]], JSON_LIST_VALIDATOR] | None = None,
        host_port: int = 445,
        enable_vss: bool = False,
        connection_timeout_seconds: int = 180,
        trigger_backup: bool = False,
    ) -> str:
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
        selectors: Annotated[list[dict[str, Any]], JSON_LIST_VALIDATOR] | None = None,
    ) -> str:
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
