"""synology-apm-cli saas — SaaS application (Cloud Application) management commands."""
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
    SEARCH_OPTION,
)
from synology_apm.cli._validate import saas_application_id
from synology_apm.cli.output import ListOutputFormat, cell, console, dispatch_paginated_list, new_table
from synology_apm.sdk import GWSDomainInfo, SaasApplication

app = typer.Typer(
    help="List connected SaaS applications (M365 / GWS).",
    no_args_is_help=True,
)


# ── synology-apm-cli saas list ─────────────────────────────────────────────────────────

@app.command("list")
@run_async
async def saas_list(
    ctx: typer.Context,
    search: str | None = SEARCH_OPTION,
    limit: int = LIMIT_OPTION,
    offset: int = OFFSET_OPTION,
    page_all: bool = PAGE_ALL_OPTION,
    output: ListOutputFormat = LIST_OUTPUT_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose mode"),
) -> None:
    """List all connected SaaS applications (M365 + GWS)."""
    async with apm_session(ctx) as apm:
        result = await dispatch_paginated_list(
            lambda off, lim: apm.saas.list(keyword=search, limit=lim, offset=off),
            limit=limit, offset=offset, page_all=page_all, output=output,
            to_dict=SaasApplication.to_dict,
        )

    if result is None:
        return

    applications, total = result
    t = new_table()
    t.add_column("Category", width=10)
    t.add_column("Name", min_width=20)
    t.add_column("Tenant / Domain", min_width=26)
    t.add_column("Protected Size", width=14)
    t.add_column("ID", min_width=36)
    if verbose:
        t.add_column("Domain Admin", min_width=26)

    for application in applications:
        row = [
            cell(application.category.value.upper()),
            cell(application.name),
            cell(application.domain),
            cell(fmt_bytes(application.protected_data_bytes)),
            cell(saas_application_id(application)),
        ]
        if verbose:
            row.append(cell(application.domain_admin if isinstance(application, GWSDomainInfo) else ""))
        t.add_row(*row)

    console.print(t)
    print_list_footer(console, len(applications), total, offset)
