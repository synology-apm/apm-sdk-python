---
name: manage-running-jobs-and-version-locks
description: "Cancel an in-progress backup, restore, or M365 export job, or lock/unlock a specific backup version to control automatic retention cleanup. Use when the user asks to stop, pause, or cancel a running job, or to protect/release a specific backup version from deletion."
---

# Manage Running Jobs and Version Locks

When the user asks to stop or cancel a job that's currently running, or to protect (or release)
a specific backup version from automatic retention cleanup:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

## Cancelling a running job

1. If the user names a workload rather than a specific job: find its id with
   `list_machine_workloads`/`list_m365_workloads` (`name_contains`/`keyword`), then cancel by
   workload with `cancel_machine_backup`/`cancel_m365_backup`. If the user already knows the
   activity (e.g. from a report produced by `daily-backup-report` or
   `analyze-restore-activities`), cancel it directly by id with `cancel_backup_activity` or
   `cancel_restore_activity` instead — no workload lookup needed.
2. All four cancel tools require `operator` mode or higher (see
   [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md#permission-modes) for what to do on a
   permission error).
3. Report the outcome; a job that already finished before the cancel call reaches the server is
   not an error — just report its final status.

## Locking or unlocking a version

4. List the workload's versions with `list_machine_versions`/`list_m365_versions` (newest
   first) to find the target `version_id` — or use `get_machine_version`/`get_m365_version`
   with no `version_id` if the user means the latest version.
5. Call `lock_machine_version`/`lock_m365_version` to prevent a version from being
   auto-deleted by its plan's retention policy, or `unlock_machine_version`/`unlock_m365_version`
   to release that hold and let normal retention apply again.
6. Both require `admin` mode.
7. Report the outcome, including the version's timestamp so the user can confirm it's the one
   they meant.

## See also

- [daily-backup-report](../daily-backup-report/SKILL.md), [catch-up-overdue-backups](../catch-up-overdue-backups/SKILL.md), [analyze-restore-activities](../analyze-restore-activities/SKILL.md) — likely places a stuck or unwanted running job first surfaces
- [reassign-or-retire-workload](../reassign-or-retire-workload/SKILL.md) — for moving a workload to a different plan or taking it out of protection entirely, rather than controlling one job or version
