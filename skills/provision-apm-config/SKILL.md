---
name: provision-apm-config
description: "Bulk-create or update APM infrastructure and plans from a config description: remote storages, protection/retirement/tiering plans, file servers, and M365 auto-backup rules. Use when the user asks to bulk-provision, import, or set up APM configuration from a document or list of resources."
---

# Provision APM Configuration (Bulk Import)

When the user asks to bulk-create, import, or provision APM infrastructure/plans from a
config description (remote storage, protection/retirement/tiering plans, file servers, M365
auto-backup rules):

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

**This workflow creates real, billable infrastructure. Every create/update tool below commits
immediately on the first call** — unlike the delete tools, there is no preview/confirm step
built into the tool itself. This skill supplies its own single confirmation step (step 4) before
any tool below is called; never skip it.

**Credential handling**: never accept passwords, access keys, or secret keys embedded in a
document the user pastes into chat or a shared file. Ask for each credential directly at the
point you're about to use it, and never echo a credential back after using it.

## Resolve everything by name — never by a copied id

Every `*_id` (`plan_id`, `storage_id`, workload ids, auto-backup-rule `uid`) is generated fresh,
locally, by whichever APM server creates that resource. It is never portable to a different
server, and it isn't stable across a re-import either. What identifies a resource in the config
description — and in every cross-reference between its entries — is its real **name** (what
matching against the target server, and create-vs-reuse, is based on), never an id copied from
wherever the description originated (an export, a previous run, a different environment).

Cross-references between entries in the same description (e.g. "this tiering plan's destination
is remote storage X"; "this file server uses plan Y") don't need to repeat the target's full
name inline every time — it's fine to use a short **intra-document anchor** instead (e.g.
`ref: plan-1` on the plan entry, then `destination_ref: plan-1` on the entry that points to it).
The anchor is purely a document-authoring convenience: it is never sent to the API and never
matched against anything on the server. It only resolves back to whichever entry in *this*
description defined it — and from there to that entry's real name, which is what actually gets
looked up or created. Don't confuse an anchor with a real id: an anchor like `plan-1` has no
meaning outside this one description, while a leftover `plan_id` from an export would look like
real data but silently fail to resolve on a different server.

At execution time, two kinds of referenced (real-name) resources need different handling. Every
MCP tool — including `get_*` — identifies its target by id only (see
[apm-mcp-conventions](../apm-mcp-conventions/SKILL.md)'s Resource identification section); "by
name" below always means "call the matching `list_*` tool with a name filter, then use the id
from the matching result," never a name parameter on `get_*` itself.

- **Resources this skill cannot create** — backup servers, M365/SaaS tenants. Resolve these by
  name against the *target* server (`list_backup_servers` with `name_contains`, then
  `get_backup_server` with the matched `server_id`; `list_saas_tenants` matched by `tenant_name`
  — there is no `get_*` tool that accepts a tenant name). Not found → this is a hard error; ask
  the user to register it first, or confirm the name is right for this target (this is also how
  you'd retarget the same config description to a different server: change just this one name —
  every anchor/cross-reference elsewhere in the description stays the same).
- **Resources this skill can create** — remote storages, protection/retirement/tiering plans.
  Resolve these by name against the target server first (the conflict check in step 3). If a
  match exists, every later stage that references it uses *that resource's existing id*. If no
  match exists, create it — every later stage that references it uses *the id this run's own
  create call just returned*. Never reuse an id that came from the source description itself.

This is exactly what makes the same config description replayable against a different APM
server: same names (and the anchors that link entries to each other) in, correctly-resolved
(existing or freshly created) ids out, every time.

**The general rule this collapses to**: an `update_*` call needs an id as *input* — and the only
legitimate source for that id is a `list_*` lookup by name against the target server (followed
by `get_*` with that id for the full current state), never the source description. A
`create_*`/`add_*` call needs no id for the resource itself at all — names and config values
only go in; the id it returns is the *output*, and that output is the only thing later stages
are allowed to use when they need to reference this newly created resource.

1. Collect the desired config from the user: every resource named, and every cross-reference
   between resources either spelled out by name directly ("this tiering plan's destination is
   the remote storage named X") or via a short intra-document anchor you assign while collecting
   the description (e.g. give the remote storage entry `ref: storage-1`, then have the tiering
   plan point at `destination_ref: storage-1` instead of repeating the full name) — either form
   is fine, as long as every anchor ultimately resolves back to a named entry in this same
   description.

2. Resolve the reference-only entities first, since later stages need their real values:
   - Each named backup server → `list_backup_servers` with `name_contains` → matched
     `server_id` → `get_backup_server` with that id → its `namespace` (needed by file servers
     and M365 auto-backup rules).
   - Each named M365/SaaS tenant → `list_saas_tenants`, matched by `tenant_name` → its
     `tenant_id` (needed by M365 auto-backup rules).
   - Any of these not found is a hard error for the affected entries — report it and ask the
     user to fix the name or register the resource first, rather than guessing.

3. Check for conflicts on the creatable entities — list what already exists on the target:
   - `list_remote_storages`, `list_protection_plans`, `list_retirement_plans`,
     `list_tiering_plans`, `list_machine_workloads` with `workload_types="fs"`,
     `list_m365_auto_backup_rules` (per resolved tenant).
   - Match desired names against existing ones (case-insensitive). Ask the user once, up front:
     for anything that already exists, should this skip it or update it? (a single global choice
     is enough unless the user wants per-item control.)

4. **Present the full plan** — what will be created, what will be updated, what will be
   skipped, in the execution order below — and get explicit confirmation before calling any
   create/update tool.

5. Execute in this order; each stage resolves its own references by name first (per the section
   above) before deciding whether to create or reuse:

   a. **Remote storages** (`add_remote_storage` / `update_remote_storage`) — if step 3 found an
      existing match, use its `storage_id` downstream; otherwise create and use
      `storage.storage_id` from the response. Field requirements vary by `storage_type`: only
      `s3_compatible`/`active_protect_vault` take `endpoint`/`trust_self_signed`; every type
      except `active_protect_vault` needs `vault_name`. Leave `relink_encryption_key` blank for
      a brand-new vault. If `encryption_enabled=true`, the create result includes
      `encryption_key` — show it to the user and tell them to store it securely; it cannot be
      retrieved again. If overwriting an existing match, see
      [manage-apm-resource](../manage-apm-resource/SKILL.md) — `update_remote_storage` requires
      working credentials on every call, with no "leave unchanged" option.

   b. **Protection plans** (`create_machine_protection_plan` / `create_m365_protection_plan`,
      or `update_machine_protection_plan` / `update_m365_protection_plan` when overwriting) —
      existing match → use its `plan_id`; otherwise create and use
      the `plan_id` from the response. See
      [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md#complex-parameter-formats-protection-plans)
      for the full retention/schedule/backup-copy field formats and cross-field requirements
      (`schedule_frequency=weekly` needs a weekday; `is_immutable=true` needs
      `retention_type=keep_days`; `retention_type=keep_advanced` needs the four `gfs_*` params),
      plus the machine-only `tasks_json`/`backup_window_allowed_hours`/`vm_*`/`pc_*`/`ps_*`/`db_*`
      advanced fields. If overwriting an existing match, see
      [manage-apm-resource](../manage-apm-resource/SKILL.md) first — these update tools replace
      every setting, not just the ones you pass.

   c. **Retirement plans** (`create_retirement_plan` / `update_retirement_plan`) — same
      existing-vs-create resolution as (b). Independent of every other stage. If overwriting,
      see [manage-apm-resource](../manage-apm-resource/SKILL.md) — this update tool also
      replaces every setting.

   d. **Tiering plans** (`create_tiering_plan` / `update_tiering_plan`) — needs the resolved
      `destination_storage_id` from stage (a) (the existing or freshly-created one — never a
      foreign id) when creating. If overwriting, see
      [manage-apm-resource](../manage-apm-resource/SKILL.md) — every field, including the
      destination, must be supplied explicitly; fetch the plan's current state with
      `get_tiering_plan` first and resupply anything you're not changing.

   e. **File servers** (`add_machine_file_server` / `update_machine_file_server`) — needs the
      `namespace` resolved in step 2 and the `plan_id` from stage (b). Backup scope is either
      `path` or `selectors` (pass exactly one) — see
      [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md#file-servers-backup-scope-path-vs-selectors)
      for the `selectors` entry shape. **Returns no workload identifier.** If you need to
      reference this file server again later in the session, call `list_machine_workloads` with
      `workload_types="fs"` and match by `host_ip`. If overwriting, see
      [manage-apm-resource](../manage-apm-resource/SKILL.md) — every field must be supplied
      explicitly except `login_password`, which accepts `None` to keep the current password;
      `update_machine_file_server` also requires `path` or `selectors` on every call — there is
      no default, so fetch the workload's current `fs_config.selectors` via
      `get_machine_workload` and resupply it verbatim (as `selectors`) unless you're
      deliberately changing the backup scope.

   f. **M365 auto-backup rules** (`create_m365_auto_backup_rule` /
      `update_m365_auto_backup_rule`) — needs the `tenant_id`/`namespace` resolved in step 2 and
      the `plan_id` from stage (b); the plan must be an M365-category plan. **Returns no rule
      id.** If you need to update or delete this rule later in the session, call
      `list_m365_auto_backup_rules` and match by namespace + plan_id to find its `uid`. The three
      group-id list fields (`exchange_group_ids`/`onedrive_group_ids`/`chat_group_ids`) on the
      update call use tri-state semantics — omit a field to leave that list unchanged, pass `[]`
      to clear it — see
      [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md#update-semantics-every-field-every-time)
      (this is the opposite of every other field on this call, e.g. `plan_id`, which must always
      be supplied explicitly to keep its current value).

   g. **M365 collab settings** (`update_m365_collab_settings`) — **replaces all four service
      types (group_exchange/mysite/sharepoint/teams) in one call**; any type you don't pass is
      reset to disabled. Always call `list_m365_auto_backup_rules` first to read the tenant's
      current `collab_settings`, and re-supply the plan_id/namespace for every type that's
      already enabled and that you don't intend to change — see
      [manage-apm-resource](../manage-apm-resource/SKILL.md) for the same rule stated generally.

6. Report the outcome of every item (created / updated / skipped / failed with reason) — one
   item failing must not stop the rest of the batch.

7. All tools in this workflow require `admin` mode. If a call fails with a permission error,
   tell the user admin mode is required rather than retrying.

## See also

- [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) — list vs. get field completeness, pagination convention, permission modes, and the update-semantics table
- [export-apm-config](../export-apm-config/SKILL.md) — the read-side counterpart; a good source for producing the config description this skill consumes
- [manage-apm-resource](../manage-apm-resource/SKILL.md) — updating or removing one already-existing resource outside of a bulk import
