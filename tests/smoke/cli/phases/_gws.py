"""gws phase — per-scope ``gws <scope> ...`` commands.

For each of the 5 GWS workload scopes (mail/calendar/contact/drive/shared-drive), repeats the
same list/get/version pattern as the machine phase. GWS has no export feature, so unlike the
m365 phase there is no ``export list`` step. A scope is conditionally skipped (not failed) if
its ``list`` returns no workloads. The whole phase is skipped if ``saas list`` shows no
GWS-category domain.

Calls ``saas list`` independently (not reusing the m365 phase's data) so ``--group gws`` works
standalone, mirroring the SDK smoke test's ``_gws.py``/``_gws_auto_backup_rule.py`` pattern.

State-mutating roundtrips (lock/unlock, backup/cancel, change-plan) are covered by the
SDK smoke test and are intentionally excluded here, mirroring the machine phase.
"""
from __future__ import annotations

from .._context import SmokeContext, parse_json, pick_backed_up_workload

DOMAIN = "gws"

GWS_SCOPES = ("mail", "calendar", "contact", "drive", "shared-drive")


def run(ctx: SmokeContext) -> None:
    _, domain_json = ctx.run_both(DOMAIN, "saas.list", ["saas", "list"])
    domains = parse_json(domain_json) or []

    if not any(d.get("category") == "gws" for d in domains):
        ctx.skip(DOMAIN, "gws.*", "No GWS-category domain found via `saas list` — all gws scope tests skipped.")
        return

    for scope in GWS_SCOPES:
        _run_scope(ctx, scope)


def _run_scope(ctx: SmokeContext, scope: str) -> None:
    _, all_json = ctx.run_both(DOMAIN, f"gws.{scope}.list", ["gws", scope, "list"])

    _, retired_json = ctx.run_both(DOMAIN, f"gws.{scope}.list[retired]", ["gws", scope, "list", "--retired"])
    retired_workloads = parse_json(retired_json) or []
    ctx.data.setdefault("retired_gws_workloads", {})[scope] = retired_workloads
    if not retired_workloads:
        ctx.skip(DOMAIN, f"gws.{scope}.list[retired][data]", f"No retired {scope} workloads found.")

    _, status_json = ctx.run_both(
        DOMAIN, f"gws.{scope}.list[status]", ["gws", scope, "list", "--status", "success", "--status", "failed"],
    )
    if not (parse_json(status_json) or []):
        ctx.skip(DOMAIN, f"gws.{scope}.list[status][data]", f"No {scope} workloads with status success/failed found.")

    workloads = parse_json(all_json) or []
    ctx.data.setdefault("gws_workloads", {})[scope] = workloads
    if not workloads:
        ctx.skip(
            DOMAIN, f"gws.{scope}.get / version",
            f"No {scope} workloads found — skipping get/version steps for this scope.",
        )
        return

    workload = pick_backed_up_workload(workloads)
    name = workload["name"]
    workload_id = workload["workload_id"]
    namespace = workload["namespace"]
    domain = workload["domain"]

    ctx.run_both(DOMAIN, f"gws.{scope}.get[search]", ["gws", scope, "get", name, "--domain", domain])
    ctx.run_both(
        DOMAIN, f"gws.{scope}.get[direct]",
        ["gws", scope, "get", "--id", workload_id, "--namespace", namespace],
    )

    _, search_ver_json = ctx.run_both(
        DOMAIN, f"gws.{scope}.version.list[search]",
        ["gws", scope, "version", "list", name, "--domain", domain],
    )
    _, direct_ver_json = ctx.run_both(
        DOMAIN, f"gws.{scope}.version.list[direct]",
        ["gws", scope, "version", "list", "--workload-id", workload_id, "--namespace", namespace],
    )

    search_versions = parse_json(search_ver_json) or []
    direct_versions = parse_json(direct_ver_json) or []
    ctx.data.setdefault("gws_versions", {})[scope] = direct_versions

    if search_versions:
        ctx.run_both(
            DOMAIN, f"gws.{scope}.version.get[latest]",
            ["gws", scope, "version", "get", name, "--domain", domain],
        )
    else:
        ctx.skip(
            DOMAIN, f"gws.{scope}.version.get[latest]",
            f"Search-mode version list returned no versions for {name!r} ({scope})"
            " (workload not backed up yet, or the name matches a different workload).",
        )

    if direct_versions:
        version_id = direct_versions[0]["version_id"]
        ctx.run_both(
            DOMAIN, f"gws.{scope}.version.get[direct]",
            ["gws", scope, "version", "get", "--workload-id", workload_id, "--namespace", namespace, "--id", version_id],
        )
    else:
        ctx.skip(
            DOMAIN, f"gws.{scope}.version.get[direct]",
            f"Workload {name!r} ({scope}) has no backup versions yet.",
        )
