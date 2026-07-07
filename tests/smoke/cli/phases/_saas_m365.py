"""saas / m365 phase — ``saas list`` plus per-scope ``m365 <scope> ...`` commands.

For each of the 6 M365 workload scopes (exchange/onedrive/chat/group/sharepoint/teams),
repeats the same list/get/version pattern as the machine phase, plus the read-only
``export list`` for the exchange/group scopes. A scope is conditionally skipped (not
failed) if its ``list`` returns no workloads. The whole phase is skipped if ``saas list``
shows no M365-category tenant.

State-mutating roundtrips (lock/unlock, backup/cancel, change-plan) are covered by the
SDK smoke test and are intentionally excluded here, mirroring the machine phase.
"""
from __future__ import annotations

from .._context import SmokeContext, parse_json, pick_backed_up_workload

DOMAIN = "m365"


def run(ctx: SmokeContext) -> None:
    _, tenant_json = ctx.run_both(DOMAIN, "saas.list", ["saas", "list"])
    tenants = parse_json(tenant_json) or []
    ctx.data["saas_tenants"] = tenants

    ctx.run(
        DOMAIN, "saas.list[page-all]", ["saas", "list", "--page-all"],
        output_format="json", note="Exercises NDJSON streaming for --page-all.",
    )
    ctx.run(DOMAIN, "saas.list[csv]", ["saas", "list"], output_format="csv")
    ctx.run(DOMAIN, "saas.list[yaml]", ["saas", "list"], output_format="yaml")

    if not any(t.get("category") == "m365" for t in tenants):
        ctx.skip(DOMAIN, "m365.*", "No M365-category tenant found via `saas list` — all m365 scope tests skipped.")
        return

    for scope in ctx.m365_scopes:
        _run_scope(ctx, scope)


def _run_scope(ctx: SmokeContext, scope: str) -> None:
    _, all_json = ctx.run_both(DOMAIN, f"m365.{scope}.list", ["m365", scope, "list"])

    _, retired_json = ctx.run_both(DOMAIN, f"m365.{scope}.list[retired]", ["m365", scope, "list", "--retired"])
    retired_workloads = parse_json(retired_json) or []
    ctx.data.setdefault("retired_m365_workloads", {})[scope] = retired_workloads
    if not retired_workloads:
        ctx.skip(DOMAIN, f"m365.{scope}.list[retired][data]", f"No retired {scope} workloads found.")

    workloads = parse_json(all_json) or []
    ctx.data.setdefault("m365_workloads", {})[scope] = workloads
    if not workloads:
        ctx.skip(
            DOMAIN, f"m365.{scope}.get / version",
            f"No {scope} workloads found — skipping get/version steps for this scope.",
        )
        return

    workload = pick_backed_up_workload(workloads)
    name = workload["name"]
    workload_id = workload["workload_id"]
    namespace = workload["namespace"]
    tenant_id = workload["tenant_id"]

    ctx.run_both(DOMAIN, f"m365.{scope}.get[search]", ["m365", scope, "get", name, "--tenant-id", tenant_id])
    ctx.run_both(
        DOMAIN, f"m365.{scope}.get[direct]",
        ["m365", scope, "get", "--id", workload_id, "--namespace", namespace],
    )

    if scope in ("exchange", "group"):
        ctx.run_both(
            DOMAIN, f"m365.{scope}.export.list",
            ["m365", scope, "export", "list", name, "--tenant-id", tenant_id],
        )

    _, search_ver_json = ctx.run_both(
        DOMAIN, f"m365.{scope}.version.list[search]",
        ["m365", scope, "version", "list", name, "--tenant-id", tenant_id],
    )
    _, direct_ver_json = ctx.run_both(
        DOMAIN, f"m365.{scope}.version.list[direct]",
        ["m365", scope, "version", "list", "--id", workload_id, "--namespace", namespace],
    )

    search_versions = parse_json(search_ver_json) or []
    direct_versions = parse_json(direct_ver_json) or []
    ctx.data.setdefault("m365_versions", {})[scope] = direct_versions

    if search_versions:
        ctx.run_both(
            DOMAIN, f"m365.{scope}.version.get[latest]",
            ["m365", scope, "version", "get", name, "--tenant-id", tenant_id],
        )
    else:
        ctx.skip(
            DOMAIN, f"m365.{scope}.version.get[latest]",
            f"Search-mode version list returned no versions for {name!r} ({scope})"
            " (workload not backed up yet, or the name matches a different workload).",
        )

    if direct_versions:
        version_id = direct_versions[0]["version_id"]
        ctx.run_both(
            DOMAIN, f"m365.{scope}.version.get[direct]",
            ["m365", scope, "version", "get", "--workload-id", workload_id, "--namespace", namespace, "--id", version_id],
        )
    else:
        ctx.skip(
            DOMAIN, f"m365.{scope}.version.get[direct]",
            f"Workload {name!r} ({scope}) has no backup versions yet.",
        )
