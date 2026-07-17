---
name: analyze-storage-capacity
description: "Analyze APM storage usage and capacity across backup servers and remote/tiered storage, and flag servers approaching capacity. Use when the user asks about storage usage, capacity planning, or which servers are full."
---

# Analyze Storage Capacity

When the user asks to analyze storage usage, plan capacity, or understand how much space is
being used:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Read the `apm://site` resource for site-level storage totals
   (`site_storage.logical_backup_data_bytes`, `site_storage.physical_backup_data_bytes`).

2. Call `list_backup_servers` to get per-server storage:
   - `storage_total_bytes`, `storage_used_bytes`, `logical_backup_data_bytes`, `physical_backup_data_bytes`
   - Note any servers with `tiering_plan_name` set (data is being offloaded).

3. Call `list_remote_storages` to get remote/tiered storage usage:
   - `used_bytes`, `remaining_bytes` per destination.

4. Call `list_tiering_plans` to understand what tiering policies are active:
   - `tiering_after_days`, `daily_check_time`, destination storage.

5. Compute:
   - Deduplication ratio: logical / physical (higher = better compression/dedup)
   - Usage percentage per server: used / total
   - Estimated days until full (if growth rate can be inferred from recent activity)

6. Present:
   - Per-server capacity bar (used / total, % full)
   - Tiering status: how much data has been offloaded to remote storage
   - Servers approaching capacity (>80% used): flag as needing attention
   - Recommendation: enable tiering, add storage, or retire old workloads

7. If the user asks about specific workload storage, call `list_machine_workloads` and sort by
   `protected_data_bytes` descending to identify the largest consumers. There is no server-side
   sort — page through with `offset` until `total` is fully collected (see
   `apm-mcp-conventions`) before sorting, otherwise the "largest" result is only the largest
   within whichever single page happened to be returned.

## See also

- [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) — list vs. get field completeness, pagination convention, permission modes
