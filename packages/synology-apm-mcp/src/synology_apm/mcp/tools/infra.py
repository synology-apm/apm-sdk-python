"""Infrastructure tools: site info, backup servers, remote storage, hypervisors."""
from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastmcp import Context

from synology_apm.mcp._enums import BackupServerTypeLiteral, ServerStatusLiteral
from synology_apm.mcp._helpers import (
    JSON_LIST_VALIDATOR,
    LIST_RESULT_SUFFIX,
    ToolResult,
    get_tool,
    list_tool,
    to_enum_list,
)
from synology_apm.mcp._registrar import ToolRegistrar
from synology_apm.mcp._security import DESTRUCTIVE_PREVIEW_SUFFIX, destructive_tool, run_audited_tool
from synology_apm.sdk import (
    AccessKeyStorageUpdateRequest,
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APMClient,
    APVStorageAddRequest,
    AzureBlobChinaStorageAddRequest,
    AzureBlobStorageAddRequest,
    AzureBlobStorageUpdateRequest,
    BackupServerType,
    C2ObjectStorageAddRequest,
    GenericS3StorageAddRequest,
    RemoteStorageType,
    RetirementPlan,
    ServerStatus,
    TieringPlan,
    WasabiCloudStorageAddRequest,
)

# ── Business logic helpers ────────────────────────────────────────────────────
#
# Only multi-step logic and request builders live here as named functions — trivial
# single-call get/list wrappers are inlined directly in their tool body below,
# matching the convention in tools/activity.py and tools/log.py.


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


def _require_fields(prefix: str, **fields: str) -> None:
    """Raise ValueError naming every field in `fields` that is empty.

    Shared by the add/update paths' storage_type-conditional required-field checks (access-key
    vs. Azure credentials) so the "collect missing, join, raise" logic exists in one place.
    """
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise ValueError(f"{prefix} requires: {', '.join(missing)}.")


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
    tenant_id: str = "",
    client_id: str = "",
    azure_secret: str = "",
    account_name: str = "",
) -> (
    GenericS3StorageAddRequest
    | APVStorageAddRequest
    | AmazonS3StorageAddRequest
    | AmazonS3ChinaStorageAddRequest
    | C2ObjectStorageAddRequest
    | WasabiCloudStorageAddRequest
    | AzureBlobStorageAddRequest
    | AzureBlobChinaStorageAddRequest
):
    shared: dict[str, Any] = dict(
        encryption_enabled=encryption_enabled,
        relink_encryption_key=relink_encryption_key,
        unmanaged_retirement_plan=unmanaged_retirement_plan,
    )
    if storage_type in ("azure_blob", "azure_blob_china"):
        _require_fields(
            storage_type, tenant_id=tenant_id, client_id=client_id,
            azure_secret=azure_secret, account_name=account_name, vault_name=vault_name,
        )
        azure_common: dict[str, Any] = dict(
            tenant_id=tenant_id, client_id=client_id, secret=azure_secret,
            account_name=account_name, vault_name=vault_name, **shared,
        )
        if storage_type == "azure_blob":
            return AzureBlobStorageAddRequest(**azure_common)
        return AzureBlobChinaStorageAddRequest(**azure_common)

    if storage_type in (
        "s3_compatible", "active_protect_vault", "amazon_s3",
        "amazon_s3_china", "c2_object_storage", "wasabi",
    ):
        _require_fields(storage_type, access_key=access_key, secret_key=secret_key)
        common: dict[str, Any] = dict(access_key=access_key, secret_key=secret_key, **shared)
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
        return WasabiCloudStorageAddRequest(vault_name=vault_name, **common)

    raise ValueError(
        f"Unsupported storage_type: {storage_type!r}. "
        "Choose: s3_compatible, active_protect_vault, amazon_s3, amazon_s3_china, c2_object_storage, "
        "wasabi, azure_blob, azure_blob_china"
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
    tenant_id: str = "",
    client_id: str = "",
    azure_secret: str = "",
    account_name: str = "",
) -> dict[str, Any]:
    unmanaged_retirement_plan: RetirementPlan | None = (
        await apm.retirement_plans.get(retirement_plan_id) if retirement_plan_id else None
    )
    request = _build_storage_request(
        storage_type, access_key, secret_key, vault_name,
        endpoint, encryption_enabled, relink_encryption_key, trust_self_signed,
        unmanaged_retirement_plan,
        tenant_id=tenant_id, client_id=client_id, azure_secret=azure_secret, account_name=account_name,
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
    tenant_id: str = "",
    client_id: str = "",
    azure_secret: str = "",
) -> dict[str, Any]:
    storage = await apm.remote_storages.get(storage_id)
    update_prefix = f"Updating {storage.storage_type.value} storage"
    if storage.storage_type in (RemoteStorageType.AZURE_BLOB, RemoteStorageType.AZURE_BLOB_CHINA):
        _require_fields(update_prefix, tenant_id=tenant_id, client_id=client_id, azure_secret=azure_secret)
        request: AccessKeyStorageUpdateRequest | AzureBlobStorageUpdateRequest = AzureBlobStorageUpdateRequest(
            tenant_id=tenant_id, client_id=client_id, secret=azure_secret,
        )
    else:
        _require_fields(update_prefix, access_key=access_key, secret_key=secret_key)
        request = AccessKeyStorageUpdateRequest(
            access_key=access_key,
            secret_key=secret_key,
            endpoint=endpoint,
            trust_self_signed=trust_self_signed,
        )
    updated = await apm.remote_storages.update(storage, request)
    return updated.to_dict()


# ── Tool registration ─────────────────────────────────────────────────────────

def register(registrar: ToolRegistrar) -> None:  # pragma: no cover
    """Register all infra tools onto server, gated by mode."""

    @registrar.tool(description="Get APM site overview: site UUID, external address, management servers, storage usage, and workload counts by type.")
    async def get_site_info(ctx: Context) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.get_site_info(), lambda x: x.to_dict())

    @registrar.tool(description=(
        "List backup servers. Filter by name, status "
        f"(healthy/warning/critical/disconnected/syncing), or type (dp/nas). {LIST_RESULT_SUFFIX}"
    ))
    async def list_backup_servers(
        ctx: Context,
        keyword: str | None = None,
        status: Annotated[list[ServerStatusLiteral], JSON_LIST_VALIDATOR] | None = None,
        server_type: Annotated[list[BackupServerTypeLiteral], JSON_LIST_VALIDATOR] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await list_tool(
            apm.backup_servers.list(
                keyword=keyword,
                status_filter=to_enum_list(ServerStatus, status),
                type_filter=to_enum_list(BackupServerType, server_type),
                limit=limit,
                offset=offset,
            ),
            lambda x: x.to_dict(),
            offset=offset,
        )

    @registrar.tool(description="Get a single backup server by ID. Use list_backup_servers (optionally with keyword) to find the ID.")
    async def get_backup_server(
        ctx: Context,
        server_id: str,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.backup_servers.get(server_id), lambda x: x.to_dict())

    @registrar.tool(description=f"List all remote storage destinations. {LIST_RESULT_SUFFIX}")
    async def list_remote_storages(ctx: Context) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await list_tool(apm.remote_storages.list(), lambda x: x.to_dict())

    @registrar.tool(description="Get a single remote storage destination by ID. Use list_remote_storages to find the ID.")
    async def get_remote_storage(
        ctx: Context,
        storage_id: str,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.remote_storages.get(storage_id), lambda x: x.to_dict())

    @registrar.tool(description=f"List all registered hypervisors (vSphere, Hyper-V, Nutanix, Proxmox, AWS, Azure). {LIST_RESULT_SUFFIX}")
    async def list_hypervisors(ctx: Context) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await list_tool(apm.hypervisors.list(), lambda x: x.to_dict())

    @registrar.tool(description="Get a single hypervisor by ID. Use list_hypervisors to find the ID.")
    async def get_hypervisor(
        ctx: Context,
        hypervisor_id: str,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await get_tool(apm.hypervisors.get(hypervisor_id), lambda x: x.to_dict())

    @registrar.tool("admin", description="Assign or remove a tiering plan for a backup server. Fails if the backup server is not DP-type. Omit tiering_plan_id to remove the current tiering plan.")
    async def change_backup_server_tiering_plan(
        ctx: Context,
        server_id: str,
        tiering_plan_id: str | None = None,
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_audited_tool(
            _change_backup_server_tiering_plan(apm, server_id, tiering_plan_id),
            action="change_backup_server_tiering_plan",
            params={"server_id": server_id, "tiering_plan_id": tiering_plan_id},
        )

    @registrar.tool("admin", description=(
        "Add a remote storage destination. storage_type: s3_compatible, active_protect_vault, amazon_s3, "
        "amazon_s3_china, c2_object_storage, wasabi, azure_blob, azure_blob_china. endpoint and "
        "trust_self_signed apply only to s3_compatible and active_protect_vault; vault_name is ignored for "
        "active_protect_vault and, for azure_blob/azure_blob_china, holds the container name instead of a "
        "bucket name. azure_blob/azure_blob_china require tenant_id, client_id, azure_secret, and "
        "account_name — the target Microsoft Entra application's tenant ID, application (client) ID, "
        "and client secret, plus the storage account and container. "
        "To re-add a previously encrypted vault, pass its saved key as relink_encryption_key; leave it "
        "empty for a new vault. If the target already has pre-existing backup catalogs not managed by "
        "this APM, pass retirement_plan_id to assign them to a retirement plan; otherwise adding storage "
        "with such catalogs fails. Returns the created storage and encryption key if encryption was "
        "enabled. relink_warning in the result is non-None if catalog relinking failed — the storage is "
        "still registered but its catalogs remain unlinked."
    ))
    async def add_remote_storage(
        ctx: Context,
        storage_type: Literal[
            "s3_compatible", "active_protect_vault", "amazon_s3", "amazon_s3_china",
            "c2_object_storage", "wasabi", "azure_blob", "azure_blob_china",
        ],
        access_key: str = "",
        secret_key: str = "",
        vault_name: str = "",
        endpoint: str = "",
        encryption_enabled: bool = False,
        relink_encryption_key: str = "",
        trust_self_signed: bool = False,
        retirement_plan_id: str | None = None,
        tenant_id: str = "",
        client_id: str = "",
        azure_secret: str = "",
        account_name: str = "",
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_audited_tool(
            _add_remote_storage(
                apm, storage_type, access_key, secret_key, vault_name, endpoint,
                encryption_enabled, relink_encryption_key, trust_self_signed,
                retirement_plan_id,
                tenant_id=tenant_id, client_id=client_id, azure_secret=azure_secret, account_name=account_name,
            ),
            action="add_remote_storage",
            params={"storage_type": storage_type},
        )

    @registrar.tool("admin", description=(
        "Update the credentials and endpoint of an existing remote storage destination by ID. Every field "
        "must be supplied explicitly on every call — the API cannot return existing credentials to "
        "resupply automatically. endpoint/trust_self_signed only take effect for "
        "s3_compatible/active_protect_vault storage. For azure_blob/azure_blob_china storage, pass "
        "tenant_id, client_id, and azure_secret instead — the Microsoft Entra application's tenant ID, "
        "application (client) ID, and client secret; access_key/secret_key/endpoint/trust_self_signed "
        "are ignored for these storage types."
    ))
    async def update_remote_storage(
        ctx: Context,
        storage_id: str,
        access_key: str = "",
        secret_key: str = "",
        endpoint: str = "",
        trust_self_signed: bool = False,
        tenant_id: str = "",
        client_id: str = "",
        azure_secret: str = "",
    ) -> ToolResult:
        apm: APMClient = ctx.lifespan_context["apm"]
        return await run_audited_tool(
            _update_remote_storage(
                apm, storage_id, access_key, secret_key, endpoint, trust_self_signed,
                tenant_id=tenant_id, client_id=client_id, azure_secret=azure_secret,
            ),
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
    ) -> ToolResult:
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
