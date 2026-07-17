---
name: investigate-backup-failure
description: "Root-cause a single backup failure or a broader failure trend across workloads and backup servers. Use when the user asks why a backup failed, why backups are failing, or wants to review backup-server logs for errors."
---

# Investigate Backup Failure

When the user asks why a backup failed, or wants to understand recent backup failures for
a specific workload or across the environment:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. **Identify the scope**:
   - If the user names a specific workload: find its id with `list_machine_workloads` (or
     `list_m365_workloads`) using `name_contains`/`keyword`, then call `get_machine_workload` (or
     `get_m365_workload`) with that id to get its current `status` and `last_backup_at`.
   - If the scope is broad ("why are backups failing?"): proceed to step 2 without
     filtering to a single workload.

2. Call `list_backup_activities` with `status=failed,partial` and the target time window
   (default: last 24 hours). Use `history=true` and `limit=500`.
   - If investigating a specific workload, add `keyword=<workload_name>`.

3. For each failed activity (up to 10 most recent), call `get_backup_activity` and read its
   `log_entries` array — each entry's `message` field holds the detail text (there is no
   separate `error_message` field).

4. Identify patterns:
   - **Same error across many workloads on one server**: server-level issue. An activity only
     exposes its `namespace`, not a server id — call `list_backup_servers` and match on
     `namespace` to find the server, then `get_backup_server` to check its `status`.
   - **Single workload, recurring failures**: workload-level issue (agent, network, data).
   - **Single workload, first occurrence**: may be transient; check if a retry succeeded.
   - **Large number of failures starting at the same time**: check `list_system_logs` and
     `list_activity_logs` on the relevant backup server (DP type only) for that window. If the
     pattern looks connectivity-related (timeouts, auth errors), also check
     `list_connection_logs`; if it looks storage-related (I/O errors, disk-full), also check
     `list_drive_logs`.

5. Present findings:
   - Failure count and affected workloads
   - Most common error messages (group identical messages)
   - Pattern assessment: server-wide, workload-specific, or transient
   - Recommended action:
     - Server issue → check server health, network, disk
     - Workload issue → re-run backup with `backup_machine_workload` or review workload config
     - Transient → monitor next scheduled backup

6. If the user asks to re-trigger a failed backup, use `backup_machine_workload` or
   `backup_m365_workload`. These require `operator` mode or higher.

## See also

- [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) — list vs. get field completeness, pagination convention, permission modes
