"""Infra domain phase: apm.get_site_info(), apm.backup_servers, apm.remote_storages, apm.hypervisors.

Populates ctx.data["site_info"], ctx.data["servers"], ctx.data["dp_servers"],
ctx.data["remote_storages"], and ctx.data["hypervisors"] for use by later phases.

Reads ctx.data["smoke_creds"] (set by __main__.py from tests/smoke/smoke_creds.toml) to
run an add → get → update → delete roundtrip for each [[remote_storage]] entry.  When no
credential file is present, all CRUD steps are recorded as skipped.
"""
from __future__ import annotations

import dataclasses
import secrets
from collections import defaultdict

from synology_apm.sdk import (
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APMError,
    APVStorageAddRequest,
    BackupServer,
    BackupServerRole,
    BackupServerType,
    C2ObjectStorageAddRequest,
    GenericS3StorageAddRequest,
    InvalidOperationError,
    RemoteStorage,
    RemoteStorageAddResult,
    RemoteStorageConflictError,
    RemoteStorageEncryptionMismatchError,
    RemoteStorageInUseError,
    RemoteStorageType,
    RemoteStorageUnmanagedCatalogError,
    RemoteStorageUpdateRequest,
    ResourceNotFoundError,
    RetirementPlan,
    RetirementPlanCreateRequest,
    SiteInfo,
    TieringPlan,
    TieringPlanCreateRequest,
    WasabiCloudStorageAddRequest,
)

from ..._creds import RemoteStorageCred, SmokeCreds
from .._context import SmokeContext
from .._trace import current_step as _current_step
from ._shared import SENTINEL_NAME as _SENTINEL_NAME
from ._shared import ZERO_UUID as _ZERO_UUID

DOMAIN = "infra"


async def run(ctx: SmokeContext) -> None:
    apm = ctx.apm

    site_info = await ctx.call(DOMAIN, "infra.site_info.get", lambda: apm.get_site_info())
    if site_info is not None:
        ctx.data["site_info"] = site_info

    servers_result = await ctx.call(
        DOMAIN, "infra.servers.list[all]", lambda: apm.backup_servers.list(limit=500)
    )
    servers, _total = servers_result if servers_result is not None else ([], 0)
    ctx.data["servers"] = servers
    ctx.data["dp_servers"] = [s for s in servers if s.server_type == BackupServerType.DP]

    by_name: BackupServer | None = None
    if servers:
        s0 = servers[0]
        await ctx.call(DOMAIN, "infra.servers.get[direct]", lambda: apm.backup_servers.get(s0.backup_server_id))
        by_name = await ctx.call(
            DOMAIN, "infra.servers.get_by_name[search]", lambda: apm.backup_servers.get_by_name(s0.name)
        )
    else:
        ctx.skip(DOMAIN, "infra.servers.get[direct]", "No backup servers found")
        ctx.skip(DOMAIN, "infra.servers.get_by_name[search]", "No backup servers found")

    await ctx.call_expect_not_found(DOMAIN, "infra.servers", "get",
        lambda: apm.backup_servers.get(_ZERO_UUID), "BackupServer", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, "infra.servers", "get_by_name",
        lambda: apm.backup_servers.get_by_name(_SENTINEL_NAME), "BackupServer", _SENTINEL_NAME)

    remote_storages_result = await ctx.call(
        DOMAIN, "infra.remote_storages.list", lambda: apm.remote_storages.list()
    )
    remote_storages, _total = remote_storages_result if remote_storages_result is not None else ([], 0)
    ctx.data["remote_storages"] = remote_storages

    dp_servers: list[BackupServer] = ctx.data.get("dp_servers", [])
    nas_servers = [s for s in servers if s.server_type == BackupServerType.NAS]
    await _run_tiering_plan_roundtrip(ctx, dp_servers, remote_storages, nas_servers)

    if remote_storages:
        rs0 = remote_storages[0]
        await ctx.call(
            DOMAIN, "infra.remote_storages.get[direct]", lambda: apm.remote_storages.get(rs0.storage_id)
        )
        await ctx.call(
            DOMAIN, "infra.remote_storages.get_by_name[search]",
            lambda: apm.remote_storages.get_by_name(rs0.name),
        )
    else:
        ctx.skip(DOMAIN, "infra.remote_storages.get[direct]", "No remote storages configured")
        ctx.skip(DOMAIN, "infra.remote_storages.get_by_name[search]", "No remote storages configured")

    await ctx.call_expect_not_found(DOMAIN, "infra.remote_storages", "get",
        lambda: apm.remote_storages.get(_ZERO_UUID), "RemoteStorage", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, "infra.remote_storages", "get_by_name",
        lambda: apm.remote_storages.get_by_name(_SENTINEL_NAME), "RemoteStorage", _SENTINEL_NAME)

    creds: SmokeCreds = ctx.data.get("smoke_creds", SmokeCreds())
    await _run_storage_crud_roundtrip(ctx, creds)

    hypervisors_result = await ctx.call(
        DOMAIN, "infra.hypervisors.list", lambda: apm.hypervisors.list()
    )
    hypervisors, _total = hypervisors_result if hypervisors_result is not None else ([], 0)
    ctx.data["hypervisors"] = hypervisors

    if hypervisors:
        h0 = hypervisors[0]
        await ctx.call(DOMAIN, "infra.hypervisors.get[direct]", lambda: apm.hypervisors.get(h0.hypervisor_id))
        await ctx.call(
            DOMAIN, "infra.hypervisors.get_by_name[search]",
            lambda: apm.hypervisors.get_by_name(h0.hostname),
        )
    else:
        ctx.skip(DOMAIN, "infra.hypervisors.get[direct]", "No hypervisors registered")
        ctx.skip(DOMAIN, "infra.hypervisors.get_by_name[search]", "No hypervisors registered")

    await ctx.call_expect_not_found(DOMAIN, "infra.hypervisors", "get",
        lambda: apm.hypervisors.get(_ZERO_UUID), "Hypervisor", _ZERO_UUID)
    await ctx.call_expect_not_found(DOMAIN, "infra.hypervisors", "get_by_name",
        lambda: apm.hypervisors.get_by_name(_SENTINEL_NAME), "Hypervisor", _SENTINEL_NAME)

    _run_checks(ctx, site_info, servers, by_name, remote_storages)


async def _run_tiering_plan_roundtrip(
    ctx: SmokeContext,
    dp_servers: list[BackupServer],
    remote_storages: list[RemoteStorage],
    nas_servers: list[BackupServer],
) -> None:
    """Create a disposable tiering plan, apply / remove / restore on a DP server, then delete.

    The NAS guard test runs inside this function while the plan still exists server-side,
    before the finally block deletes it.
    """
    apm = ctx.apm
    _dp_steps = (
        "infra.servers.tiering_plan[create]",
        "infra.servers.change_tiering_plan[apply]",
        "infra.servers.change_tiering_plan[remove]",
        "infra.servers.change_tiering_plan[restore]",
        "infra.servers.tiering_plan[delete]",
    )
    _nas_steps = (
        "infra.servers.change_tiering_plan[nas_raises]",
        "infra.servers.check[nas_raises_resource_type]",
        "infra.servers.check[nas_raises_resource_id]",
    )
    if not dp_servers:
        for step in _dp_steps:
            ctx.skip(DOMAIN, step, "No DP backup servers available")
        for step in _nas_steps:
            ctx.skip(DOMAIN, step, "No DP backup servers available")
        return
    if not remote_storages:
        for step in _dp_steps:
            ctx.skip(DOMAIN, step, "No remote storages configured (required as tiering destination)")
        for step in _nas_steps:
            ctx.skip(DOMAIN, step, "No remote storages configured (required as tiering destination)")
        return

    uid = secrets.token_hex(4)
    target = next((s for s in dp_servers if not s.tiering_plan_name), None) or dp_servers[0]
    original_plan_name = target.tiering_plan_name

    disposable_plan: TieringPlan | None = None
    try:
        disposable_plan = await ctx.call(
            DOMAIN, "infra.servers.tiering_plan[create]",
            lambda: apm.tiering_plans.create(TieringPlanCreateRequest(
                name=f"smoke-tier-{uid}",
                tiering_after_days=9999,
                destination=remote_storages[0],
            )),
            note="Creates a disposable tiering plan (tiering_after_days=9999) so it never triggers real tiering.",
        )

        if disposable_plan is not None:
            disp = disposable_plan
            await ctx.call(
                DOMAIN, "infra.servers.change_tiering_plan[apply]",
                lambda: apm.backup_servers.change_tiering_plan(target, disp),
            )
            await ctx.call(
                DOMAIN, "infra.servers.change_tiering_plan[remove]",
                lambda: apm.backup_servers.change_tiering_plan(target, None),
            )
        else:
            ctx.skip(DOMAIN, "infra.servers.change_tiering_plan[apply]", "Tiering plan creation did not succeed")
            ctx.skip(DOMAIN, "infra.servers.change_tiering_plan[remove]", "Tiering plan creation did not succeed")

        if original_plan_name is not None:
            try:
                original_plan = await apm.tiering_plans.get_by_name(original_plan_name)
            except Exception:
                original_plan = None
            if original_plan is not None:
                orig = original_plan
                await ctx.call(
                    DOMAIN, "infra.servers.change_tiering_plan[restore]",
                    lambda: apm.backup_servers.change_tiering_plan(target, orig),
                )
            else:
                ctx.skip(
                    DOMAIN, "infra.servers.change_tiering_plan[restore]",
                    f"Could not re-fetch original tiering plan {original_plan_name!r}",
                )
        else:
            ctx.na(DOMAIN, "infra.servers.change_tiering_plan[restore]", "Target server had no tiering plan")

        if not nas_servers or disposable_plan is None:
            reason = "No NAS servers available" if not nas_servers else "Tiering plan creation did not succeed"
            for step in _nas_steps:
                ctx.skip(DOMAIN, step, reason)
        else:
            _nas0 = nas_servers[0]
            _tier = disposable_plan
            nas_exc = await ctx.call_expect_error(
                DOMAIN, "infra.servers.change_tiering_plan[nas_raises]",
                lambda: apm.backup_servers.change_tiering_plan(_nas0, _tier),
                InvalidOperationError,
            )
            ctx.check_exc_attr(DOMAIN, "infra.servers.check[nas_raises_resource_type]",
                nas_exc, "resource_type", "BackupServer")
            ctx.check_exc_attr(DOMAIN, "infra.servers.check[nas_raises_resource_id]",
                nas_exc, "resource_id", _nas0.backup_server_id)
    finally:
        if disposable_plan is not None:
            disp_del = disposable_plan
            await ctx.call(
                DOMAIN, "infra.servers.tiering_plan[delete]",
                lambda: apm.tiering_plans.delete(disp_del),
            )
        else:
            ctx.skip(DOMAIN, "infra.servers.tiering_plan[delete]", "Tiering plan was not created")


_STORAGE_TYPE_MAP: dict[str, RemoteStorageType] = {
    "s3_compatible":  RemoteStorageType.S3_COMPATIBLE,
    "apv":            RemoteStorageType.ACTIVE_PROTECT_VAULT,
    "amazon_s3":      RemoteStorageType.AMAZON_S3,
    "amazon_s3_china": RemoteStorageType.AMAZON_S3_CHINA,
    "c2":             RemoteStorageType.C2_OBJECT_STORAGE,
    "wasabi":         RemoteStorageType.WASABI,
}


def _build_add_request(
    cred: RemoteStorageCred,
    *,
    retirement_plan: RetirementPlan | None,
) -> (
    GenericS3StorageAddRequest
    | APVStorageAddRequest
    | AmazonS3StorageAddRequest
    | AmazonS3ChinaStorageAddRequest
    | C2ObjectStorageAddRequest
    | WasabiCloudStorageAddRequest
    | None
):
    if cred.type == "s3_compatible":
        return GenericS3StorageAddRequest(
            access_key=cred.access_key,
            secret_key=cred.secret_key,
            vault_name=cred.vault,
            endpoint=cred.endpoint,
            trust_self_signed=cred.trust_self_signed,
            encryption_enabled=bool(cred.relink_encryption_key),
            relink_encryption_key=cred.relink_encryption_key,
            unmanaged_retirement_plan=retirement_plan,
        )
    if cred.type == "apv":
        return APVStorageAddRequest(
            access_key=cred.access_key,
            secret_key=cred.secret_key,
            endpoint=cred.endpoint,
            trust_self_signed=cred.trust_self_signed,
            encryption_enabled=bool(cred.relink_encryption_key),
            relink_encryption_key=cred.relink_encryption_key,
            unmanaged_retirement_plan=retirement_plan,
        )
    if cred.type == "amazon_s3":
        return AmazonS3StorageAddRequest(
            access_key=cred.access_key,
            secret_key=cred.secret_key,
            vault_name=cred.vault,
            encryption_enabled=bool(cred.relink_encryption_key),
            relink_encryption_key=cred.relink_encryption_key,
            unmanaged_retirement_plan=retirement_plan,
        )
    if cred.type == "amazon_s3_china":
        return AmazonS3ChinaStorageAddRequest(
            access_key=cred.access_key,
            secret_key=cred.secret_key,
            vault_name=cred.vault,
            encryption_enabled=bool(cred.relink_encryption_key),
            relink_encryption_key=cred.relink_encryption_key,
            unmanaged_retirement_plan=retirement_plan,
        )
    if cred.type == "c2":
        return C2ObjectStorageAddRequest(
            access_key=cred.access_key,
            secret_key=cred.secret_key,
            vault_name=cred.vault,
            encryption_enabled=bool(cred.relink_encryption_key),
            relink_encryption_key=cred.relink_encryption_key,
            unmanaged_retirement_plan=retirement_plan,
        )
    if cred.type == "wasabi":
        return WasabiCloudStorageAddRequest(
            access_key=cred.access_key,
            secret_key=cred.secret_key,
            vault_name=cred.vault,
            encryption_enabled=bool(cred.relink_encryption_key),
            relink_encryption_key=cred.relink_encryption_key,
            unmanaged_retirement_plan=retirement_plan,
        )
    return None


async def _run_storage_crud_roundtrip(ctx: SmokeContext, creds: SmokeCreds) -> None:
    """Run add → get → update → delete for each [[remote_storage]] credential entry.

    Credentials are grouped by storage type.  Within each type, the encrypted and
    non-encrypted variants run as independent tracks so their step lists contain only
    applicable steps — no N/A entries.
    """
    if not creds.remote_storage:
        ctx.skip(
            DOMAIN, "infra.remote_storages.add[*]",
            "No [[remote_storage]] entries in smoke_creds.toml"
            " — copy smoke_creds.toml.example to get started.",
        )
        return

    type_groups: dict[str, list[RemoteStorageCred]] = defaultdict(list)
    for cred in creds.remote_storage:
        type_groups[cred.type].append(cred)

    for type_name, entries in type_groups.items():
        encrypted_cred = next((c for c in entries if c.relink_encryption_key), None)
        plain_cred     = next((c for c in entries if not c.relink_encryption_key), None)
        if encrypted_cred is not None:
            await _run_one_storage_crud(ctx, encrypted_cred)
        else:
            ctx.skip(DOMAIN, f"infra.remote_storages.add[{type_name}/encrypted]",
                     f"No encrypted [[remote_storage]] entry configured for type '{type_name}'")
        if plain_cred is not None:
            await _run_one_storage_crud(ctx, plain_cred)
        else:
            ctx.skip(DOMAIN, f"infra.remote_storages.add[{type_name}/non_encrypted]",
                     f"No non-encrypted [[remote_storage]] entry configured for type '{type_name}'")


async def _run_one_storage_crud(ctx: SmokeContext, cred: RemoteStorageCred) -> None:
    apm = ctx.apm
    label = cred.display_name()
    encrypted = bool(cred.relink_encryption_key)
    # retirement_plan[delete] is NOT in this tuple — it is always run via try/finally.
    _inuse_steps = (
        f"infra.remote_storages.tiering_plan[{label}/inuse_create]",
        f"infra.remote_storages.delete[{label}/inuse_raises]",
        f"infra.remote_storages.check[{label}/inuse_resource_type]",
        f"infra.remote_storages.check[{label}/inuse_resource_id]",
        f"infra.remote_storages.tiering_plan[{label}/inuse_delete]",
    )
    _enc_steps: tuple[str, ...] = ()
    if encrypted:
        _enc_steps = (
            f"infra.remote_storages.add[{label}/no_key]",
            f"infra.remote_storages.check[{label}/no_key_resource_type]",
            f"infra.remote_storages.check[{label}/no_key_resource_id]",
            f"infra.remote_storages.check[{label}/encryption_key]",
        )
    _crud_steps = (
        f"infra.remote_storages.retirement_plan[{label}/create]",
        *_enc_steps,
        f"infra.remote_storages.add[{label}/unmanaged_raises]",
        f"infra.remote_storages.check[{label}/unmanaged_vault_name]",
        f"infra.remote_storages.check[{label}/unmanaged_catalog_count]",
        f"infra.remote_storages.add[{label}]",
        f"infra.remote_storages.add[{label}/duplicate]",
        f"infra.remote_storages.check[{label}/duplicate_resource_type]",
        f"infra.remote_storages.check[{label}/duplicate_resource_id]",
        f"infra.remote_storages.get[{label}]",
        f"infra.remote_storages.check[{label}/fields]",
        f"infra.remote_storages.update[{label}]",
        f"infra.remote_storages.check[{label}/post_update]",
        *_inuse_steps,
        f"infra.remote_storages.delete[{label}]",
        f"infra.remote_storages.get[{label}/post_delete]",
        f"infra.remote_storages.check[{label}/post_delete_resource_type]",
        f"infra.remote_storages.check[{label}/post_delete_resource_id]",
    )

    if _build_add_request(cred, retirement_plan=None) is None:
        for step in _crud_steps:
            ctx.skip(DOMAIN, step, f"Unknown storage type {cred.type!r}")
        ctx.skip(DOMAIN, f"infra.remote_storages.retirement_plan[{label}/delete]",
                 f"Unknown storage type {cred.type!r}")
        return

    plan: RetirementPlan | None = None
    try:
        plan_result: RetirementPlan | None = await ctx.call(
            DOMAIN, f"infra.remote_storages.retirement_plan[{label}/create]",
            lambda: apm.retirement_plans.create(RetirementPlanCreateRequest(
                name=f"smoke-rp-{secrets.token_hex(6)}",
                retention_days=9999,
            )),
        )
        if plan_result is None:
            ctx.skip_remaining(DOMAIN, _crud_steps, reason=f"retirement_plan[{label}/create] did not succeed")
            return
        plan = plan_result

        if encrypted:
            no_key_req = _build_add_request(
                dataclasses.replace(cred, relink_encryption_key=""), retirement_plan=plan,
            )
            assert no_key_req is not None
            no_key_exc = await ctx.call_expect_error(
                DOMAIN, f"infra.remote_storages.add[{label}/no_key]",
                lambda: apm.remote_storages.add(no_key_req),
                RemoteStorageEncryptionMismatchError,
            )
            ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/no_key_resource_type]",
                no_key_exc, "resource_type", "RemoteStorage")
            if cred.vault:
                ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/no_key_resource_id]",
                    no_key_exc, "resource_id", cred.vault)
            else:
                ctx.check(DOMAIN, f"infra.remote_storages.check[{label}/no_key_resource_id]",
                    isinstance(no_key_exc, RemoteStorageEncryptionMismatchError) and no_key_exc.resource_id != "",
                    note="Vault name is resolved at runtime for this storage type; verify the SDK populated resource_id.")

        # Unmanaged-catalog check, run for every entry: adding without a retirement plan
        # must raise when the vault holds catalogs left by a previous registration. On a
        # vault without any, that probe add succeeds instead — it is rolled back so the
        # plan-bearing add below runs identically for every entry, and the three steps
        # are recorded as skipped.
        step_unmanaged = f"infra.remote_storages.add[{label}/unmanaged_raises]"
        check_unmanaged_vault = f"infra.remote_storages.check[{label}/unmanaged_vault_name]"
        check_unmanaged_count = f"infra.remote_storages.check[{label}/unmanaged_catalog_count]"
        no_plan_req = _build_add_request(cred, retirement_plan=None)
        assert no_plan_req is not None
        probe_exc: APMError | None = None
        token = _current_step.set(step_unmanaged)
        try:
            try:
                probe = await apm.remote_storages.add(no_plan_req)
            except APMError as exc:
                probe_exc = exc
            else:
                try:
                    await apm.remote_storages.delete(probe.storage)
                except APMError as exc:
                    probe_exc = exc
        finally:
            _current_step.reset(token)

        if probe_exc is None:
            probe_reason = (
                "Vault holds no pre-existing catalogs — the probe add without a retirement"
                " plan succeeded (registration rolled back)"
            )
            for step in (step_unmanaged, check_unmanaged_vault, check_unmanaged_count):
                ctx.skip(DOMAIN, step, probe_reason)
        else:
            _probe_exc = probe_exc

            async def _reraise_probe() -> None:
                raise _probe_exc

            unmanaged_exc = await ctx.call_expect_error(
                DOMAIN, step_unmanaged, _reraise_probe,
                RemoteStorageUnmanagedCatalogError,
                note="Adding a vault holding pre-existing catalogs without an unmanaged"
                " retirement plan must raise RemoteStorageUnmanagedCatalogError.",
            )
            if isinstance(unmanaged_exc, RemoteStorageUnmanagedCatalogError):
                if cred.vault:
                    ctx.check(
                        DOMAIN, check_unmanaged_vault,
                        unmanaged_exc.vault_name == cred.vault,
                    )
                else:
                    ctx.check(
                        DOMAIN, check_unmanaged_vault,
                        unmanaged_exc.vault_name != "",
                        note="Vault name is resolved at runtime for this storage type; verify the SDK populated vault_name.",
                    )
                ctx.check(
                    DOMAIN, check_unmanaged_count,
                    unmanaged_exc.catalog_count > 0,
                )
            else:
                probe_reason = "Probe add did not raise RemoteStorageUnmanagedCatalogError"
                ctx.skip(DOMAIN, check_unmanaged_vault, probe_reason)
                ctx.skip(DOMAIN, check_unmanaged_count, probe_reason)

        req = _build_add_request(cred, retirement_plan=plan)
        assert req is not None
        add_result: RemoteStorageAddResult | None = await ctx.call(
            DOMAIN, f"infra.remote_storages.add[{label}]",
            lambda: apm.remote_storages.add(req),
        )
        if add_result is None:
            ctx.skip_remaining(DOMAIN, _crud_steps, reason=f"add[{label}] did not succeed")
            return

        storage_id = add_result.storage.storage_id

        dup_exc = await ctx.call_expect_error(
            DOMAIN, f"infra.remote_storages.add[{label}/duplicate]",
            lambda: apm.remote_storages.add(req),
            RemoteStorageConflictError,
        )
        ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/duplicate_resource_type]",
            dup_exc, "resource_type", "RemoteStorage")
        if cred.vault:
            ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/duplicate_resource_id]",
                dup_exc, "resource_id", cred.vault)
        else:
            ctx.check(DOMAIN, f"infra.remote_storages.check[{label}/duplicate_resource_id]",
                isinstance(dup_exc, RemoteStorageConflictError) and dup_exc.resource_id != "",
                note="Vault name is resolved at runtime for this storage type; verify the SDK populated resource_id.")

        if encrypted:
            key_note = f"encryption key: {add_result.encryption_key}" if add_result.encryption_key else ""
            ctx.check(
                DOMAIN, f"infra.remote_storages.check[{label}/encryption_key]",
                add_result.encryption_key is not None and len(add_result.encryption_key) > 0,
                note=key_note,
            )

        fetched: RemoteStorage | None = await ctx.call(
            DOMAIN, f"infra.remote_storages.get[{label}]",
            lambda: apm.remote_storages.get(storage_id),
        )

        if fetched is not None:
            expected_type = _STORAGE_TYPE_MAP.get(cred.type)
            type_ok = expected_type is None or fetched.storage_type == expected_type
            vault_ok = cred.type == "apv" or not cred.vault or fetched.vault_name == cred.vault
            enc_ok = fetched.encryption_enabled == bool(cred.relink_encryption_key)
            ctx.check(
                DOMAIN, f"infra.remote_storages.check[{label}/fields]",
                type_ok and vault_ok and enc_ok,
                note="storage_type, vault_name, and encryption_enabled should match the registered values.",
            )
        else:
            ctx.skip(DOMAIN, f"infra.remote_storages.check[{label}/fields]", "get did not succeed")

        update_request = RemoteStorageUpdateRequest(
            access_key=cred.access_key,
            secret_key=cred.secret_key,
            endpoint=cred.endpoint,
            trust_self_signed=cred.trust_self_signed,
        )
        base = fetched if fetched is not None else add_result.storage
        updated: RemoteStorage | None = await ctx.call(
            DOMAIN, f"infra.remote_storages.update[{label}]",
            lambda: apm.remote_storages.update(base, update_request),
        )

        if updated is not None:
            ctx.check(
                DOMAIN, f"infra.remote_storages.check[{label}/post_update]",
                updated.storage_id == storage_id,
                note="update() should return the same storage with an unchanged storage_id.",
            )
        else:
            ctx.skip(DOMAIN, f"infra.remote_storages.check[{label}/post_update]", "update did not succeed")

        to_delete = updated if updated is not None else base

        dp_servers_inuse: list[BackupServer] = ctx.data.get("dp_servers", [])
        if dp_servers_inuse:
            uid_inuse = secrets.token_hex(4)
            inuse_tier_plan: TieringPlan | None = None
            _to_delete = to_delete
            try:
                inuse_tier_plan = await ctx.call(
                    DOMAIN, f"infra.remote_storages.tiering_plan[{label}/inuse_create]",
                    lambda: apm.tiering_plans.create(TieringPlanCreateRequest(
                        name=f"smoke-tier-inuse-{uid_inuse}",
                        tiering_after_days=9999,
                        destination=_to_delete,
                    )),
                )
                if inuse_tier_plan is None:
                    ctx.skip(DOMAIN, f"infra.remote_storages.delete[{label}/inuse_raises]",
                        "Tiering plan creation did not succeed")
                    ctx.skip(DOMAIN, f"infra.remote_storages.check[{label}/inuse_resource_type]",
                        "Tiering plan creation did not succeed")
                    ctx.skip(DOMAIN, f"infra.remote_storages.check[{label}/inuse_resource_id]",
                        "Tiering plan creation did not succeed")
                else:
                    inuse_exc = await ctx.call_expect_error(
                        DOMAIN, f"infra.remote_storages.delete[{label}/inuse_raises]",
                        lambda: apm.remote_storages.delete(_to_delete),
                        RemoteStorageInUseError,
                    )
                    ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/inuse_resource_type]",
                        inuse_exc, "resource_type", "RemoteStorage")
                    ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/inuse_resource_id]",
                        inuse_exc, "resource_id", storage_id)
            finally:
                if inuse_tier_plan is not None:
                    _itp = inuse_tier_plan
                    await ctx.call(
                        DOMAIN, f"infra.remote_storages.tiering_plan[{label}/inuse_delete]",
                        lambda: apm.tiering_plans.delete(_itp),
                    )
                else:
                    ctx.skip(DOMAIN, f"infra.remote_storages.tiering_plan[{label}/inuse_delete]",
                        "Tiering plan was not created")
        else:
            ctx.skip_remaining(DOMAIN, _inuse_steps, reason="No DP backup servers available")

        await ctx.call(
            DOMAIN, f"infra.remote_storages.delete[{label}]",
            lambda: apm.remote_storages.delete(to_delete),
        )

        post_del_exc = await ctx.call_expect_error(
            DOMAIN, f"infra.remote_storages.get[{label}/post_delete]",
            lambda: apm.remote_storages.get(storage_id),
            ResourceNotFoundError,
        )
        ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/post_delete_resource_type]",
            post_del_exc, "resource_type", "RemoteStorage")
        ctx.check_exc_attr(DOMAIN, f"infra.remote_storages.check[{label}/post_delete_resource_id]",
            post_del_exc, "resource_id", storage_id)

    finally:
        if plan is not None:
            await ctx.call(
                DOMAIN, f"infra.remote_storages.retirement_plan[{label}/delete]",
                lambda: apm.retirement_plans.delete(plan),
            )
        else:
            ctx.skip(DOMAIN, f"infra.remote_storages.retirement_plan[{label}/delete]",
                     "retirement plan was not created")


def _run_checks(
    ctx: SmokeContext,
    site_info: SiteInfo | None,
    servers: list[BackupServer],
    by_name: BackupServer | None,
    remote_storages: list[RemoteStorage],
) -> None:
    if site_info is not None:
        primary = site_info.primary_management_server
        ctx.check(
            DOMAIN, "infra.site_info.check[roles]", primary is not None and primary.backup_server_id != "",
        )
        if primary is not None:
            ctx.check(
                DOMAIN, "infra.servers.check[role_consistency]",
                any(
                    s.backup_server_id == primary.backup_server_id and s.role == BackupServerRole.PRIMARY
                    for s in servers
                ),
                note="The primary management server from get_site_info() should also appear in servers.list().",
            )
        else:
            ctx.skip(DOMAIN, "infra.servers.check[role_consistency]", "No primary management server in site_info")
    else:
        ctx.skip(DOMAIN, "infra.site_info.check[roles]", "infra.site_info.get did not return a result")
        ctx.skip(DOMAIN, "infra.servers.check[role_consistency]", "infra.site_info.get did not return a result")

    if servers and by_name is not None:
        s0 = servers[0]
        ctx.check(
            DOMAIN, "infra.servers.check[get_by_name_match]", by_name.backup_server_id == s0.backup_server_id,
            note="get_by_name(servers[0].name) should resolve back to the same backup server.",
        )
    else:
        ctx.skip(DOMAIN, "infra.servers.check[get_by_name_match]", "No backup servers found")

    if remote_storages:
        ctx.check(
            DOMAIN, "infra.remote_storages.check[usage_parsing]",
            all(
                (s.used_bytes is None or (isinstance(s.used_bytes, int) and s.used_bytes >= 0))
                and (s.remaining_bytes is None or (isinstance(s.remaining_bytes, int) and s.remaining_bytes >= 0))
                for s in remote_storages
            ),
            note="used_bytes and remaining_bytes must be int or None (never an empty string).",
        )
    else:
        ctx.skip(DOMAIN, "infra.remote_storages.check[usage_parsing]", "No remote storages configured")
