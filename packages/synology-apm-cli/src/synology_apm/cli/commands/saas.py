"""synology-apm saas — SaaS tenant (Cloud Application) management commands."""
from __future__ import annotations

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import fmt_bytes, print_list_footer
from synology_apm.cli._helpers import apm_session
from synology_apm.cli._options import (
    LIMIT_OPTION,
    LIST_OUTPUT_OPTION,
    OFFSET_OPTION,
    PAGE_ALL_OPTION,
)
from synology_apm.cli.output import ListOutputFormat, cell, console, dispatch_paginated_list, new_table
from synology_apm.sdk import SaasTenant

app = typer.Typer(
    help="List connected SaaS tenants (M365 / GWS).",
    no_args_is_help=True,
)


# ── synology-apm saas list ─────────────────────────────────────────────────────────

@app.command("list")
@run_async
async def saas_list(
    ctx: typer.Context,
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
) -> None:
    """List all connected SaaS tenants (M365 + GWS)."""
    async with apm_session(ctx) as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.saas.list(limit=lim, offset=off),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=SaasTenant.to_dict,
        )

    if result is None:
        return

    tenants, total = result
    t = new_table()
    t.add_column("Category", width=10)
    t.add_column("Name", min_width=20)
    t.add_column("Email / Domain", min_width=26)
    t.add_column("Protected Size", width=14)
    t.add_column("Tenant ID", min_width=36)

    for tenant in tenants:
        t.add_row(
            cell(tenant.category.value.upper()),
            cell(tenant.tenant_name),
            cell(tenant.tenant_email),
            cell(fmt_bytes(tenant.protected_data_bytes) if tenant.protected_data_bytes else None),
            cell(tenant.tenant_id),
        )

    console.print(t)
    print_list_footer(console, len(tenants), total, offset)
