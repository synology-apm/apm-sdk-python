---
name: daily-backup-report
description: "Generate a backup status report for a time window (today, yesterday, or a date range), grouped by success/failed/in-progress. Use when the user asks for a backup status report or daily summary."
---

# Daily Backup Report

When the user asks for a backup status report for today, yesterday, or a specific date:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Determine the time window. Default: the last 24 hours. If the user says "yesterday," use
   that calendar day in UTC. Compute ISO 8601 `since`/`until` strings.

2. Call `list_backup_activities` with `history=true` and the computed `since`/`until`.
   Use `limit=500` to capture all activities in the window.

3. Group results by `status`:
   - **Success** (`success`)
   - **Failed** (`failed`, `partial`)
   - **In progress** (`queuing`, `backing_up`, `canceling`)
   - **Canceled** (`canceled`)

4. For each failed/partial workload, call `get_backup_activity` and read its `log_entries`
   array (see [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md#list-vs-get-field-completeness)
   for the `message`-field detail).

5. Present the report in this order:
   - Summary table: status counts by workload type (VM, PC, Exchange, OneDrive, ...)
   - Failed/partial workloads: name, last error, last attempt time
   - In-progress workloads (if any): name, started_at
   - Overall health assessment: "All backups succeeded", "N failures need attention", etc.

6. If the user asks to trigger catch-up backups for failed workloads, use the
   `catch-up-overdue-backups` skill.
