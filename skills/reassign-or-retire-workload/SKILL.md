---
name: reassign-or-retire-workload
description: "Move a workload to a different protection or retirement plan, or take it out of protection entirely (retire to a retirement plan, or permanently delete). Use when the user asks to reassign a workload's plan, retire a workload, or delete a workload and its backup data."
---

# Reassign or Retire a Workload

When the user asks to move a workload to a different plan, retire it, or permanently delete it
and its backup data:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on — especially its **Destructive action preview pattern** section, which the retire and
delete steps below depend on.

1. Find the workload's id with `list_machine_workloads`/`list_m365_workloads`
   (`name_contains`/`keyword`), then confirm you have the right one with
   `get_machine_workload`/`get_m365_workload` before doing anything destructive.

## Reassigning to a different plan

2. Resolve the target plan's id with `list_protection_plans` or `list_retirement_plans`
   (`name_contains`). Confirm the target plan's name with the user, then call
   `change_machine_workload_plan`/`change_m365_workload_plan` with the workload id and the
   resolved `plan_id`. Requires `admin` mode.

## Retiring a workload

3. `retire_machine_workload`/`retire_m365_workload` move the workload to a retirement plan and
   stop new backups, but keep existing backup data under that plan's retention policy. Resolve
   the target retirement plan's id first (`list_retirement_plans`), then follow the
   [preview/confirm pattern](../apm-mcp-conventions/SKILL.md#destructive-action-preview-pattern).
   Requires `admin` mode.

## Deleting a workload

4. `delete_machine_workload`/`delete_m365_workload` permanently remove the workload **and its
   backup data** — this is irreversible, unlike retiring. Follow the same
   [preview/confirm pattern](../apm-mcp-conventions/SKILL.md#destructive-action-preview-pattern)
   as retiring, and make sure the preview/warning is read back to the user verbatim before the
   `confirm=true` call, given the data loss involved. Requires `admin` mode.
   `delete_m365_workload` succeeds silently if the workload is already gone — don't treat that
   as an error.

5. Report the outcome. If any call fails with a permission error, tell the user `admin` mode is
   required rather than retrying.

## See also

- [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) — list vs. get field completeness, permission modes, and the destructive action preview pattern
- [manage-apm-resource](../manage-apm-resource/SKILL.md) — the resource-level analog of this workload-level skill: updating or removing a plan/storage/file-server/auto-backup-rule rather than a workload
- [manage-running-jobs-and-version-locks](../manage-running-jobs-and-version-locks/SKILL.md) — for cancelling a running job or locking/unlocking a version, rather than reassigning or decommissioning the workload itself
