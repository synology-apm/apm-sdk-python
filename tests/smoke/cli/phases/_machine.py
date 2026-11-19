"""machine phase — ``machine`` list/get/version commands and read-only ``machine sandbox`` steps.

Lock/unlock, backup/cancel and change-plan are covered by the SDK smoke test. The sandbox
steps run ``resources`` and ``start --dry-run`` only.
"""
from __future__ import annotations

from typing import Any

from tests.sandbox_checks import has_backup_server_copy

from .._context import SmokeContext, parse_json, pick_backed_up_workload

DOMAIN = "machine"


def run(ctx: SmokeContext) -> None:
    _, all_json = ctx.run_both(DOMAIN, "machine.list[all]", ["machine", "list"])

    for wtype in ("pc", "ps", "vm", "fs"):
        ctx.run_both(DOMAIN, f"machine.list[{wtype}]", ["machine", "list", "--type", wtype])

    _, retired_json = ctx.run_both(DOMAIN, "machine.list[retired]", ["machine", "list", "--retired"])
    if not (parse_json(retired_json) or []):
        ctx.skip(DOMAIN, "machine.list[retired][data]", "No retired Machine Workloads found.")

    _, status_json = ctx.run_both(
        DOMAIN, "machine.list[status]", ["machine", "list", "--status", "success", "--status", "failed"],
    )
    if not (parse_json(status_json) or []):
        ctx.skip(DOMAIN, "machine.list[status][data]", "No Machine Workloads with status success/failed found.")

    _, verify_status_json = ctx.run_both(
        DOMAIN, "machine.list[verify-status]", ["machine", "list", "--verify-status", "not_enabled"],
    )
    if not (parse_json(verify_status_json) or []):
        ctx.skip(
            DOMAIN, "machine.list[verify-status][data]", "No Machine Workloads with verify-status not_enabled found.",
        )

    ctx.run(
        DOMAIN, "machine.list[page-all]", ["machine", "list", "--page-all"],
        output_format="json", note="Exercises NDJSON streaming for --page-all.",
    )
    ctx.run(DOMAIN, "machine.list[csv]", ["machine", "list"], output_format="csv")
    ctx.run(DOMAIN, "machine.list[yaml]", ["machine", "list"], output_format="yaml")

    workloads = parse_json(all_json) or []
    ctx.data["machine_workloads"] = workloads

    if not workloads:
        ctx.skip(
            DOMAIN, "machine.get / version",
            "No Machine Workloads found — skipping get/version steps.",
        )
        return

    workload = pick_backed_up_workload(workloads)
    name = workload["name"]
    workload_id = workload["workload_id"]
    namespace = workload["namespace"]

    ctx.run_both(DOMAIN, "machine.get[search]", ["machine", "get", name])
    ctx.run_both(
        DOMAIN, "machine.get[direct]",
        ["machine", "get", "--id", workload_id, "--namespace", namespace],
    )

    _, search_ver_json = ctx.run_both(DOMAIN, "machine.version.list[search]", ["machine", "version", "list", name])
    _, direct_ver_json = ctx.run_both(
        DOMAIN, "machine.version.list[direct]",
        ["machine", "version", "list", "--workload-id", workload_id, "--namespace", namespace],
    )

    search_versions = parse_json(search_ver_json) or []
    direct_versions = parse_json(direct_ver_json) or []

    if search_versions:
        ctx.run_both(DOMAIN, "machine.version.get[latest]", ["machine", "version", "get", name])
    else:
        ctx.skip(
            DOMAIN, "machine.version.get[latest]",
            f"Search-mode version list returned no versions for {name!r}"
            " (workload not backed up yet, or the name matches a different workload).",
        )

    if direct_versions:
        version_id = direct_versions[0]["version_id"]
        ctx.run_both(
            DOMAIN, "machine.version.get[direct]",
            ["machine", "version", "get", "--workload-id", workload_id, "--namespace", namespace, "--id", version_id],
        )
    else:
        ctx.skip(DOMAIN, "machine.version.get[direct]", f"Workload {name!r} has no backup versions yet.")

    _sandbox_steps(ctx, workloads)


def _sandbox_steps(ctx: SmokeContext, workloads: list[dict[str, Any]]) -> None:
    """Read-only ``machine sandbox resources`` / ``start --dry-run`` on the first restorable PS/VM version."""
    steps = ("machine.sandbox.resources", "machine.sandbox.start[dry-run]")
    for wl in workloads:
        if wl.get("workload_type") not in ("ps", "vm") or wl.get("is_retired"):
            continue
        wl_args = ["--workload-id", wl["workload_id"], "--namespace", wl["namespace"]]
        ver = ctx.run(
            DOMAIN, f"machine.sandbox.version.list[{wl['name']}]",
            ["machine", "version", "list", *wl_args, "--status", "success", "--status", "partial", "--limit", "10"],
            output_format="json",
        )
        version = next((v for v in parse_json(ver) or [] if has_backup_server_copy(v)), None)
        if version is None:
            continue
        args = [*wl_args, "--version-id", version["version_id"]]
        ctx.run_both(DOMAIN, steps[0], ["machine", "sandbox", "resources", *args])
        ctx.run_both(DOMAIN, steps[1], ["machine", "sandbox", "start", *args, "--dry-run"])
        return
    for step in steps:
        ctx.skip(DOMAIN, step, "No active PS/VM workload with a restorable backup-server version.")
