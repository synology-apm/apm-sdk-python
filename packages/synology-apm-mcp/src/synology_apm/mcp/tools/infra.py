"""Infrastructure tools: site info, backup servers, remote storage, hypervisors."""
from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastmcp import Context

from synology_apm.mcp._enums import BackupServerTypeLiteral, ServerStatusLiteral
from synology_apm.mcp._helpers import (
    JSON_LIST_VALIDATOR,
    LIST_RESULT_SUFFIX,
    clamp_limit,
    get_tool,
    list_tool,
    to_enum_list,
)
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import DESTRUCTIVE_PREVIEW_SUFFIX, destructive_tool, run_audited_tool
from synology_apm.sdk import (
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APMClient,
    APVStorageAddRequest,
    BackupServerType,
    C2ObjectStorageAddRequest,
    GenericS3StorageAddRequest,
    RemoteStorageUpdateRequest,
    RetirementPlan,
    ServerStatus,
    TieringPlan,
    WasabiCloudStorageAddRequest,
)

# ── Business logic helpers ────────────────────────────────────────────────────

async def _get_site_info(apm: APMClient) -> str:
    return await get_tool(apm.get_site_info(), lambda x: x.to_dict())


async def _list_backup_servers(
    apm: APMClient,
    name_contains: str | None,
    status_filter: list[ServerStatus] | None,
    type_filter: list[BackupServerType] | None,
    limit: int,
    offset: int,
) -> str:
    return await list_tool(
        apm.backup_servers.list(
            name_contains=name_contains,
            status_filter=status_filter,
            type_filter=type_filter,
            limit=clamp_limit(limit),
            offset=offset,
        ),
        lambda x: x.to_dict(),
        offset=offset,
    )


async def _get_backup_server(apm: APMClient, server_id: str) -> str:
    return await get_tool(apm.backup_servers.get(server_id), lambda x: x.to_dict())


async def _list_remote_storages(apm: APMClient) -> str:
    return await list_tool(apm.remote_storages.list(), lambda x: x.to_dict())


async def _get_remote_storage(apm: APMClient, storage_id: str) -> str:
    return await get_tool(apm.remote_storages.get(storage_id), lambda x: x.to_dict())


async def _list_hypervisors(apm: APMClient) -> str:
    return await list_tool(apm.hypervisors.list(), lambda x: x.to_dict())


async def _get_hypervisor(apm: APMClient, hypervisor_id: str) -> str:
    return await get_tool(apm.hypervisors.get(hypervisor_id), lambda x: x.to_dict())


async def _change_backup_server_tiering_plan(
    apm: APMClient,
    server_id: str,
    tiering_plan_id: str | None,
) -> dict[str, Any]:
    plan: TieringPlan | None
    if tiering_plan_id:
        server, plan = await asyncio.gather(
            apm.backup_servers.get(server_id),
            apm.tiering_plans.get(tiering_plan_id),
        )
    else:
        server = await apm.backup_servers.get(server_id)
        plan = None
    await apm.backup_servers.change_tiering_plan(server, plan)
    return {"ok": True, "backup_server_id": server.backup_server_id, "tiering_plan_id": plan.plan_id if plan else None}


def _build_storage_request(
    storage_type: str,
    access_key: str,
    secret_key: str,
    vault_name: str,
    endpoint: str,
    encryption_enabled: bool,
    relink_encryption_key: str,
    trust_self_signed: bool,
    unmanaged_retirement_plan: RetirementPlan | None = None,
) -> Any:
    common: dict[str, Any] = dict(
        access_key=access_key,
        secret_key=secret_key,
        encryption_enabled=encryption_enabled,
        relink_encryption_key=relink_encryption_key,
        unmanaged_retirement_plan=unmanaged_retirement_plan,
    )
    if storage_type == "s3_compatible":
        return GenericS3StorageAddRequest(vault_name=vault_name, endpoint=endpoint, trust_self_signed=trust_self_signed, **common)
    if storage_type == "active_protect_vault":
        return APVStorageAddRequest(endpoint=endpoint, trust_self_signed=trust_self_signed, **common)
    if storage_type == "amazon_s3":
        return AmazonS3StorageAddRequest(vault_name=vault_name, **common)
    if storage_type == "amazon_s3_china":
        return AmazonS3ChinaStorageAddRequest(vault_name=vault_name, **common)
    if storage_type == "c2_object_storage":
        return C2ObjectStorageAddRequest(vault_name=vault_name, **common)
    if storage_type == "wasabi":
        return WasabiCloudStorageAddRequest(vault_name=vault_name, **common)
    raise ValueError(
        f"Unsupported storage_type: {storage_type!r}. "
        "Choose: s3_compatible, active_protect_vault, amazon_s3, amazon_s3_china, c2_object_storage, wasabi"
    )


async def _add_remote_storage(
    apm: APMClient,
    storage_type: str,
    access_key: str,
    secret_key: str,
    vault_name: str,
    endpoint: str,
    encryption_enabled: bool,
    relink_encryption_key: str,
    trust_self_signed: bool,
    retirement_plan_id: str | None = None,
) -> dict[str, Any]:
    unmanaged_retirement_plan: RetirementPlan | None = (
        await apm.retirement_plans.get(retirement_plan_id) if retirement_plan_id else None
    )
    request = _build_storage_request(
        storage_type, access_key, secret_key, vault_name,
        endpoint, encryption_enabled, relink_encryption_key, trust_self_signed,
        unmanaged_retirement_plan,
    )
    result = await apm.remote_storages.add(request)
    return result.to_dict()


async def _update_remote_storage(
    apm: APMClient,
    storage_id: str,
    access_key: str,
    secret_key: str,
    endpoint: str,
    trust_self_signed: bool,
) -> dict[str, Any]:
    storage = await apm.remote_storages.get(storage_id)
    updated = await apm.remote_storages.update(
        storage,
        RemoteStorageUpdateRequest(
            access_key=access_key,
            secret_key=secret_key,
            endpoint=endpoint,
            trust_self_signed=trust_self_signed,
        ),
    )
    return updated.to_dict()


# ── Tool registration ─────────────────────────────────────────────────────────

def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register all infra tools onto server, gated by mode."""

    @registrar.tool(description="Get APM site overview: site UUID, external address, management servers, storage usage, and workload counts by type.")
    async def get_site_info(ctx: Context) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _get_site_info(apm)

    @registrar.tool(description=(
        f"List backup servers. {LIST_RESULT_SUFFIX} Filter by name, status "
        "(healthy/warning/critical/disconnected/syncing), or type (dp/nas)."
    ))
    async def list_backup_servers(
        ctx: Context,
        name_contains: str | None = None,
        status: Annotated[list[ServerStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
        server_type: Annotated[list[BackupServerTypeLiteral], JSON_LIST_VALIDATOR] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        status_filter = to_enum_list(ServerStatus, status)
        type_filter = to_enum_list(BackupServerType, server_type)
        return await _list_backup_servers(apm, name_contains, status_filter, type_filter, limit, offset)

    @registrar.tool(description="Get a single backup server by ID. Use list_backup_servers (optionally with name_contains) to find the ID.")
    async def get_backup_server(
        ctx: Context,
        server_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _get_backup_server(apm, server_id)

    @registrar.tool(description=f"List all remote storage destinations. {LIST_RESULT_SUFFIX}")
    async def list_remote_storages(ctx: Context) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _list_remote_storages(apm)

    @registrar.tool(description="Get a single remote storage destination by ID. Use list_remote_storages to find the ID.")
    async def get_remote_storage(
        ctx: Context,
        storage_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _get_remote_storage(apm, storage_id)

    @registrar.tool(description=f"List all registered hypervisors (vSphere, Hyper-V). {LIST_RESULT_SUFFIX}")
    async def list_hypervisors(ctx: Context) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _list_hypervisors(apm)

    @registrar.tool(description="Get a single hypervisor by ID. Use list_hypervisors to find the ID.")
    async def get_hypervisor(
        ctx: Context,
        hypervisor_id: str,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await _get_hypervisor(apm, hypervisor_id)

    @registrar.tool("admin", description="Assign or remove a tiering plan for a backup server. Fails if the backup server is not DP-type. Omit tiering_plan_id to remove the current tiering plan.")
    async def change_backup_server_tiering_plan(
        ctx: Context,
        server_id: str,
        tiering_plan_id: str | None = None,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_audited_tool(
            _change_backup_server_tiering_plan(apm, server_id, tiering_plan_id),
            action="change_backup_server_tiering_plan",
            params={"server_id": server_id, "tiering_plan_id": tiering_plan_id},
        )

    @registrar.tool("admin", description=(
        "Add a remote storage destination. storage_type: s3_compatible, active_protect_vault, amazon_s3, "
        "amazon_s3_china, c2_object_storage, wasabi. endpoint and trust_self_signed apply only to "
        "s3_compatible and active_protect_vault; vault_name is ignored for active_protect_vault. To re-add "
        "a previously encrypted vault, pass its saved key as relink_encryption_key; leave it empty for a "
        "new vault. If the target already has pre-existing backup catalogs not managed by this APM, pass "
        "retirement_plan_id to assign them to a retirement plan; otherwise adding storage with such "
        "catalogs fails. Returns the created storage and encryption key if encryption was enabled."
    ))
    async def add_remote_storage(
        ctx: Context,
        storage_type: Literal["s3_compatible", "active_protect_vault", "amazon_s3", "amazon_s3_china", "c2_object_storage", "wasabi"],
        access_key: str,
        secret_key: str,
        vault_name: str = "",
        endpoint: str = "",
        encryption_enabled: bool = False,
        relink_encryption_key: str = "",
        trust_self_signed: bool = False,
        retirement_plan_id: str | None = None,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]

        async def _do() -> dict[str, Any]:
            return await _add_remote_storage(
                apm, storage_type, access_key, secret_key, vault_name, endpoint,
                encryption_enabled, relink_encryption_key, trust_self_signed,
                retirement_plan_id,
            )

        return await run_audited_tool(
            _do(),
            action="add_remote_storage",
            params={"storage_type": storage_type},
        )

    @registrar.tool("admin", description="Update the credentials and endpoint of an existing remote storage destination by ID. Every field must be supplied explicitly on every call — the API cannot return existing credentials to resupply automatically. endpoint/trust_self_signed only take effect for s3_compatible/active_protect_vault storage; ignored for other storage types.")
    async def update_remote_storage(
        ctx: Context,
        storage_id: str,
        access_key: str,
        secret_key: str,
        endpoint: str,
        trust_self_signed: bool,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_audited_tool(
            _update_remote_storage(apm, storage_id, access_key, secret_key, endpoint, trust_self_signed),
            action="update_remote_storage",
            params={"storage_id": storage_id},
        )

    @registrar.tool("admin", description=(
        "Remove a remote storage destination and all associated data by ID. Fails if the storage is "
        f"referenced by active plans. {DESTRUCTIVE_PREVIEW_SUFFIX}"
    ))
    async def delete_remote_storage(
        ctx: Context,
        storage_id: str,
        confirm: bool = False,
    ) -> str:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await destructive_tool(
            confirm=confirm,
            action="delete_remote_storage",
            warning="This permanently removes the remote storage destination and all associated backup data. Pass confirm=true to proceed.",
            resolve_coro=apm.remote_storages.get(storage_id),
            preview_target_fn=lambda s: {"name": s.name, "storage_id": s.storage_id, "storage_type": s.storage_type.value},
            execute_fn=lambda s: apm.remote_storages.delete(s),
            params={"storage_id": storage_id, "confirm": confirm},
        )
