---
name: workload-inventory
description: "Inventory all protected workloads (Machine + M365) by type, status, and plan coverage. Use when the user asks for a workload count, full inventory, or summary of what's being backed up."
---

# Workload Inventory

When the user asks for an inventory of all protected workloads, a workload count by type,
or a summary of what is being backed up:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Call `list_machine_workloads` (limit=500) for a snapshot of all machine workloads. If
   `total` exceeds the returned item count, keep paging with `offset` until all workloads are
   collected. Repeat with `is_retired=true` to also collect retired machine workloads (needed
   for the retired-count summary in step 6) — `is_retired` has no "all" option, so this always
   takes two passes.

2. Read `apm://tenants` to get all M365 tenants, then call `list_m365_workloads` for each
   tenant × workload type combination to collect all M365 workloads. Repeat each call with
   `is_retired=true` to also collect retired M365 workloads.

3. Read `apm://plans/protection` to cross-reference workload counts per plan.

4. Group machine workloads by:
   - `workload_type`: vm, pc, ps (physical server), fs (file server)
   - `status`: success, failed, partial, queuing, backing_up, canceled, no_backups, deleting, retired
   - `is_retired`: active vs retired

5. Group M365 workloads by:
   - `workload_type`: exchange, onedrive, chat, sharepoint, teams, group
   - Tenant name
   - `status`

6. Present:
   - Machine workloads: count by type, count by status, % with successful last backup
   - M365 workloads: count by tenant, count by type
   - Plans: which plans cover the most workloads, any workloads without a plan
   - Retired workloads: count (exclude from main summary unless user asks)

7. If the user asks for a specific workload, use `list_machine_workloads`/`list_m365_workloads`
   with `name_contains`/`keyword` to find its id and namespace, then `get_machine_workload` /
   `get_m365_workload` (M365 also needs `workload_type` — required, there's no fallback if
   omitted) for full detail.

8. If the request is scoped to a specific status (e.g. "show all failed backups", "which
   workloads still need verification") rather than a full inventory, skip steps 1-2's full
   listing and instead call `list_machine_workloads(status=["failed"])` /
   `list_m365_workloads(status=["failed"])` directly (add `verify_status=[...]` for a Machine
   verification-status query) — the client-side grouping in steps 4-5 is only needed for the
   "everything, broken down by status" overview case.

## See also

- [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) — list vs. get field completeness, pagination convention, permission modes
