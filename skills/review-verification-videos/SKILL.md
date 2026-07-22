---
name: review-verification-videos
description: "Review backup-verification videos for VM/PS workloads and flag workloads whose backups failed verification. Use when the user asks to check backup verification status or review verification videos."
---

# Review Backup Verification Videos

When the user asks to review, check, or find backup-verification videos for VM/PS workloads
(verification confirms a backup is bootable/recoverable):

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Call `list_machine_workloads` with `workload_types="ps,vm"` and `is_retired=false`
   (paginate with `limit`/`offset` if `total` exceeds the page size). If the user only wants
   workloads that failed verification (not the full success/failed/in-progress breakdown),
   narrow this call with `verify_status=["failed","partial","canceled"]` instead of listing
   everything and grouping in step 3.

2. For each workload, call `get_machine_version` for that workload without a `version_id` — it
   resolves to the latest backup version in a single call.

3. Group by the latest version's `verify_status`:
   - `success` — a verification video is available; proceed to step 4 if the user wants it
   - `failed` / `partial` / `canceled` — flag as a workload needing attention (no usable video)
   - `verifying` / `waiting` — verification in progress or queued but not yet started
   - `not_supported` / `not_enabled` / missing — verification isn't configured for this
     workload; do not report it as a failure

4. For workloads with `verify_status == success`, call `get_machine_verification_video_url`
   with the workload and the version's `version_id` to get a time-limited download URL.

5. Present:
   - Summary: count by verification outcome (success / failed / in-progress / not configured)
   - For each `success` workload: name, version date, video URL (note that it expires — tell
     the user to download or open it promptly)
   - Workloads with `failed`/`partial`/`canceled` verification: name, last version date —
     recommend investigating with the `investigate-backup-failure` skill or re-running backup.

6. `get_machine_verification_video_url` is readonly and does not require elevated mode. There
   is no bulk "download all" tool — hand back the list of URLs for the user (or their own
   tooling) to fetch individually.
