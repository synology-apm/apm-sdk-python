---
name: analyze-restore-activities
description: "Summarize restore activity trends and investigate restore failures over a time window. Use when the user asks about restore history, trends, or a specific restore failure."
---

# Analyze Restore Activities

When the user asks to summarize recent restore activity, understand restore trends, or
investigate a specific restore:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Determine the time window. Default: last 7 days. Compute `since`/`until` ISO 8601 strings.

2. Call `list_restore_activities` twice with the computed window and `limit=500`: once with
   `history=true` for completed restores, and once with the default `history=false` for
   ongoing ones.

3. Set aside activities whose `restore_type` is `instant_sandbox`: they are recovery drills
   (see [sandbox-recovery-drill](../sandbox-recovery-drill/SKILL.md)), not restores. Report
   them separately; a drill normally ends `canceled` when its sandbox is cleaned up, so that is
   not a failure.

4. Group the remaining results by `status` into three buckets (covering all 10 statuses a
   restore other than a drill can have, so none is silently dropped):
   - **success**: `success`
   - **failed**: `failed`, `partial`, `canceled`
   - **in-progress**: `preparing`, `restoring`, `canceling`, `ready_for_migrate`,
     `migrate_vm_manually`, `migrating`

5. For each failed restore, call `get_restore_activity` and read the `message` of its
   `log_entries`.

6. Identify patterns:
   - **Frequent restores of the same workload**: may indicate data quality issues
   - **Long restore durations**: `finished_at - started_at` > expected
   - **Failed restores**: collect error messages and look for common causes

7. Present:
   - Summary: total restores, success rate, average duration
   - Failed restores: workload name, error message, timestamp
   - Most-restored workloads in the period (sorted by restore count)
   - Recovery drills (if any): count and outcome
   - Any anomalies or recommendations

8. If the user asks about a specific restore or workload, call `get_restore_activity` by
   `activity_id` for full detail.
