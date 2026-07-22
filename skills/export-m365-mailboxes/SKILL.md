---
name: export-m365-mailboxes
description: "Bulk-export M365 Exchange or Group mailboxes to PST via APM's export pipeline, with polling and download links. Use when the user asks to export, download, or back up M365 mailboxes as PST files."
---

# Export M365 Mailboxes to PST

When the user asks to export, back up, or download M365 mailboxes as PST files — either
Exchange user mailboxes or M365 Group mailboxes:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Resolve the tenant: if the user doesn't give a `tenant_id`, call `list_saas_tenants` and
   ask which tenant (or use the only one if there's exactly one).

2. List the target mailboxes:
   - Exchange user mailboxes: `list_m365_workloads` with `workload_type="exchange"` and the
     resolved `tenant_id`
   - Group mailboxes: `list_m365_workloads` with `workload_type="group"`

3. For each mailbox workload, resolve the version to export: call `get_m365_version` for that
   workload without a `version_id` — it resolves to the latest backup version in a single call.

4. **Before starting anything**, present the list to the user (mailbox name, latest version
   date) and confirm scope — this is a bulk operation with real processing cost on the APM
   server.

5. Start the export for each approved mailbox:
   - Exchange: `start_exchange_export` with the workload identifier, the resolved
     `version_id`, and `archive_mailbox=false` for the primary mailbox (repeat with
     `archive_mailbox=true` only if the user also wants the archive mailbox). No tool or
     workload field reports whether a mailbox has an archive mailbox — there's no way to
     check in advance; treat a failure from the `archive_mailbox=true` call as "no archive
     mailbox for this user" rather than retrying.
   - Group: `start_group_export` with the workload identifier and `version_id` (no archive
     variant for groups).
   - Both require `operator` mode or higher.
   - The result includes `ready_to_download` — if `true`, skip polling and go straight to
     step 7 for that mailbox.

6. For exports not immediately ready, poll `list_exchange_exports` / `list_group_exports` for
   that workload and find the matching activity (most recently started, `status=preparing`)
   until its `status` becomes `ready_to_download` (or `failed`/`canceled`, which should be
   reported, not retried silently).

7. For each `ready_to_download` activity, call `get_exchange_export_download_url` /
   `get_group_export_download_url` with its `activity_id` to get the PST download link.

8. If the user wants to cancel in-progress exports instead, call `list_exchange_exports` /
   `list_group_exports`, filter to `status=preparing`, confirm with the user, then call
   `cancel_exchange_export` / `cancel_group_export` per `activity_id`.

9. Present a final report: mailbox name, export status, download URL (if ready) or failure
   reason (if failed) — note that each URL is time-limited, so tell the user to download
   promptly rather than treating the report as a permanent link list.
