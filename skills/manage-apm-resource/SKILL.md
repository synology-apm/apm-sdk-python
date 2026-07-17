---
name: manage-apm-resource
description: "View, update, or remove a single existing APM resource — a remote storage, protection/retirement/tiering plan, file server, backup server's tiering-plan assignment, or M365 auto-backup rule/collab settings. Use when the user asks to change, update, rotate, remove, or reassign a single already-existing resource (not a bulk import)."
---

# Manage an APM Resource

When the user asks to change, update, rotate, remove, or reassign settings on a specific
existing remote storage, protection/retirement/tiering plan, file server, backup server, or
M365 auto-backup rule:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on — especially its **Update semantics** and **Destructive action preview pattern**
sections, which this skill's entire design depends on.

**Every tool — read or mutate — identifies its target by id only.** There is no name-based
lookup anywhere except the business `name` field you're setting on a plan (and `vault_name`,
`export_name`, which are unrelated business fields, not lookup keys).

## Resolving the target resource

1. Call the matching `list_*` tool with a keyword/name filter (`name_contains` for plans, backup
   servers, and workloads; `keyword` for M365 workloads; `list_remote_storages`/`list_hypervisors`/
   `list_saas_tenants` return everything unfiltered — scan the result yourself). If more than one result matches,
   pick the right one from the returned fields (or ask the user to disambiguate) — the tool will
   never guess for you.
2. Call the corresponding `get_*` tool with that id to fetch the resource's full current state:
   `get_remote_storage`, `get_protection_plan`, `get_retirement_plan`, `get_tiering_plan`,
   `get_backup_server`, or `get_machine_workload` (for a file server). M365 auto-backup rules
   have no `get_*`/name field — match by `namespace` + `plan_id` from
   `list_m365_auto_backup_rules` to find the `uid`.

## Updating a resource

**Every `update_*` tool commits immediately on the first call** — there is no preview/confirm
step built in (unlike the delete tools below). Confirm the exact change with the user before
calling it.

3. **Every `update_*` tool requires the complete desired field set on every call — there is no
   partial-patch or keep-current-if-omitted behavior**, with two exceptions (see
   [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md#update-semantics-every-field-every-time)):
   `update_machine_file_server`'s `login_password` accepts `None` to explicitly keep the current
   password (APM does not expose it for re-reading, so there is no way to resupply it); and
   `update_m365_auto_backup_rule`'s `exchange_group_ids`/`onedrive_group_ids`/`chat_group_ids`
   use the opposite convention — omit a field to keep it unchanged, pass `[]` to clear it.
   `update_machine_file_server`'s backup scope (`path` or `selectors`) has no such exception — it
   must be resupplied on every call, and passing neither raises an error rather than silently
   resetting the file server to a single unrestricted path; if the workload has more than one
   selector or any `excluded_paths`, fetch `fs_config.selectors` via `get_machine_workload` and
   pass it back unchanged as `selectors` unless you're deliberately changing scope. For every
   other field on every `update_*` tool, take the current value from the `get_*` result in step 2
   and resupply it unchanged for anything the user isn't asking to change — the tool will not fill
   in anything on your behalf, and there is no hidden default to fall back on.

4. Present the exact before/after change to the user and confirm before calling the `update_*`
   tool — this workflow's only safety gate, since the tool itself won't ask.

5. Call the `update_*` tool and report the result. If it fails with a permission error, tell the
   user `admin` mode is required rather than retrying.

## Assigning a tiering plan to a backup server

`change_backup_server_tiering_plan` assigns (or removes, with `tiering_plan_id=None`) the
tiering plan on a DP-type backup server — it's a single-field settings change on the server
resource, not a plan-level operation. Resolve the server's id with `list_backup_servers`
(step 1 above) and the tiering plan's id with `list_tiering_plans`, confirm the change with the
user, then call it. Requires `admin` mode.

## Removing a resource

`delete_remote_storage`, `delete_protection_plan`, `delete_retirement_plan`,
`delete_tiering_plan`, and `delete_m365_auto_backup_rule` all use the destructive-action
preview/confirm pattern described in
[apm-mcp-conventions](../apm-mcp-conventions/SKILL.md#destructive-action-preview-pattern): call
first with `confirm=false` to get a preview, show it to the user, then call again with
`confirm=true` only after explicit approval — never skip straight to `confirm=true`. Resolve the
target's id first (per "Resolving the target resource" above; `delete_m365_auto_backup_rule`
takes the `uid` found via `list_m365_auto_backup_rules`).

`delete_remote_storage` fails if the storage is still referenced by an active tiering plan or
retirement plan — resolve or reassign those references first (see
[provision-apm-config](../provision-apm-config/SKILL.md) for bulk reassignment, or the update
steps above for a single plan). All of these require `admin` mode.

## See also

- [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) — list vs. get field completeness, pagination convention, permission modes, the update-semantics table, and the destructive-action preview pattern
- [provision-apm-config](../provision-apm-config/SKILL.md) — for bulk-creating or updating many resources at once from a config description, rather than one specific resource
- [reassign-or-retire-workload](../reassign-or-retire-workload/SKILL.md) — the workload-level analog of this resource-level skill: moving a workload to a different plan, or retiring/deleting the workload itself
