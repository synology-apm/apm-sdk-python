---
name: catch-up-overdue-backups
description: "Find workloads with overdue or failed backups and trigger catch-up backups after user confirmation. Use when the user asks to catch up overdue backups or fix workloads that haven't backed up recently."
---

# Catch Up Overdue Backups

When the user asks to find workloads with overdue or failed backups and trigger catch-up:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Call `list_backup_activities` with `status=failed,partial` and `since` set to 48 hours
   ago (or a window the user specifies). Use `history=true` and `limit=500`.

2. Extract the distinct workload names/IDs from the failed activities.

3. Also call `list_machine_workloads` with no filter to find workloads whose
   `last_backup_at` is older than expected (e.g., more than 25 hours ago for a daily plan).
   Treat this static threshold as the overdue signal rather than reading a per-plan schedule
   (see `apm-mcp-conventions` for why `list_machine_workloads` can't give you that directly).

4. Deduplicate: build a combined list of workloads needing catch-up.

5. **Before triggering anything**, present the list to the user:
   - Workload name, type, last backup time, failure reason (if available)
   - Ask: "Trigger immediate backup for all N workloads? (yes/no, or specify a subset)"

6. For each workload the user approves, call `backup_machine_workload` or
   `backup_m365_workload` as appropriate.

7. After triggering, call `list_backup_activities` (without `history=true`) to show the
   queued/running activities and confirm the backups started.

8. Note: `backup_machine_workload` and `backup_m365_workload` require `operator` mode or
   higher. If the server responds with a permission error, inform the user.

## See also

- [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) — list vs. get field completeness, pagination convention, permission modes
