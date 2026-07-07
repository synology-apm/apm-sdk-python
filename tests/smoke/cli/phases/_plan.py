"""plan phase — ``plan protection``/``plan retirement``/``plan tiering`` commands."""
from __future__ import annotations

from .._context import SmokeContext, parse_json

DOMAIN = "plan"


def run(ctx: SmokeContext) -> None:
    _run_protection(ctx)
    _run_retirement(ctx)
    _run_tiering(ctx)


def _run_protection(ctx: SmokeContext) -> None:
    _, all_json = ctx.run_both(DOMAIN, "plan.protection.list[all]", ["plan", "protection", "list"])
    ctx.run_both(DOMAIN, "plan.protection.list[machine]", ["plan", "protection", "list", "--category", "machine"])
    ctx.run_both(DOMAIN, "plan.protection.list[m365]", ["plan", "protection", "list", "--category", "m365"])
    ctx.run(
        DOMAIN, "plan.protection.list[page-all]", ["plan", "protection", "list", "--page-all"],
        output_format="json", note="Exercises NDJSON streaming for --page-all.",
    )
    ctx.run(DOMAIN, "plan.protection.list[csv]", ["plan", "protection", "list"], output_format="csv")
    ctx.run(DOMAIN, "plan.protection.list[yaml]", ["plan", "protection", "list"], output_format="yaml")

    plans = parse_json(all_json) or []
    ctx.data["protection_plans"] = plans
    if plans:
        first = plans[0]
        ctx.run_both(DOMAIN, "plan.protection.get[search]", ["plan", "protection", "get", first["name"]])
        ctx.run_both(DOMAIN, "plan.protection.get[direct]", ["plan", "protection", "get", "--id", first["plan_id"]])
        ctx.run_both(
            DOMAIN, "plan.protection.list[search]",
            ["plan", "protection", "list", "--search", first["name"]],
        )
    else:
        ctx.skip(DOMAIN, "plan.protection.get", "No Protection Plans found.")


def _run_retirement(ctx: SmokeContext) -> None:
    _, all_json = ctx.run_both(DOMAIN, "plan.retirement.list", ["plan", "retirement", "list"])

    plans = parse_json(all_json) or []
    ctx.data["retirement_plans"] = plans
    if plans:
        first = plans[0]
        ctx.run_both(DOMAIN, "plan.retirement.get[search]", ["plan", "retirement", "get", first["name"]])
        ctx.run_both(DOMAIN, "plan.retirement.get[direct]", ["plan", "retirement", "get", "--id", first["plan_id"]])
    else:
        ctx.skip(DOMAIN, "plan.retirement.get", "No Retirement Plans found.")


def _run_tiering(ctx: SmokeContext) -> None:
    _, all_json = ctx.run_both(DOMAIN, "plan.tiering.list", ["plan", "tiering", "list"])

    plans = parse_json(all_json) or []
    ctx.data["tiering_plans"] = plans
    if plans:
        first = plans[0]
        ctx.run_both(DOMAIN, "plan.tiering.get[search]", ["plan", "tiering", "get", first["name"]])
        ctx.run_both(DOMAIN, "plan.tiering.get[direct]", ["plan", "tiering", "get", "--id", first["plan_id"]])
    else:
        ctx.skip(DOMAIN, "plan.tiering.get", "No Tiering Plans found.")
