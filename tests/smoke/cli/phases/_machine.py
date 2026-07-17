"""machine phase — ``machine list``/``get``/``version list``/``version get``.

Verifies that the CLI binary can connect, list and get Machine Workloads and
their version history without crashing or producing malformed output.
State-mutating roundtrips (lock/unlock, backup/cancel, change-plan) are
covered by the SDK smoke test and are intentionally excluded here.
"""
from __future__ import annotations

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
        ["machine", "version", "list", "--id", workload_id, "--namespace", namespace],
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
