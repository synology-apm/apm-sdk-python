---
name: generate-billing-report
description: "Compute backup charges per plan, server, workload type, or tenant from protected data volume. Use when the user asks for a billing or chargeback report."
---

# Generate Billing Report

When the user asks to compute backup charges per workload group, server, or protection plan:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Call `list_machine_workloads` (limit=500), with `is_retired=true` in a second pass to also
   capture retired (e.g. Retirement-Plan-billed) workloads. Call `list_saas_tenants` first to
   enumerate tenants, then call `list_m365_workloads` once per (tenant, `workload_type`) pair —
   it takes a single `workload_type` and has no "all types" option, so cover all six types
   (exchange, onedrive, chat, sharepoint, teams, group) per tenant, each with both
   `is_retired=false` and `is_retired=true`. Every one of these calls returns `{items, total}` —
   if `total` exceeds the returned item count, keep paging with `offset` until you've collected
   everything (see `apm-mcp-conventions`); a charge computed from a partial page silently
   undercounts.

2. Group workloads by the dimension the user requests:
   - **By plan**: group on `plan.name`
   - **By server**: group on `backup_server.name`
   - **By type**: group on `workload_type` (vm, pc, exchange, ...)
   - **By tenant**: group on `tenant_id` (M365 only)

3. For each group, sum `protected_data_bytes` (total protected data under management) — this is
   the only field the charge in step 4 is based on. `backup_copy_data_bytes` (data in a
   configured Backup Copy destination) is informational only; report it separately if the user
   wants visibility into secondary-copy storage, but don't fold it into the charge.

4. If the user provides a per-GB rate (e.g., "$0.05/GB/month"), compute the cost:
   - `charge = protected_data_bytes / 1024**3 * rate` (binary GB/GiB, matching the reference
     `examples/billing_report.py` tool's convention)

5. Present:
   - Summary table: group name | workload count | protected data (GB) | estimated charge
   - Sort by protected data descending
   - Include a totals row

6. Note: this skill computes estimates — actual billing depends on your organization's
   pricing model.
