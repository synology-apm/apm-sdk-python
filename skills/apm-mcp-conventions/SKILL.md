---
name: apm-mcp-conventions
description: "Shared conventions for the synology-apm-mcp tools: list vs. get field completeness, pagination, and permission modes. Not a task itself — load this when another apm-mcp skill points here, or when writing a new skill that touches plans, activities, or mode-gated tools."
---

# APM MCP Shared Conventions

Cross-cutting behavior for the synology-apm-mcp workflow skills:

- **workload-inventory** — Full inventory of protected workloads by type and status
- **daily-backup-report** — Backup status report for a time window
- **catch-up-overdue-backups** — Find and trigger catch-up for overdue/failed workloads
- **analyze-storage-capacity** — Capacity planning across servers and remote storage
- **generate-billing-report** — Compute backup charges per plan, server, workload type, or tenant
- **analyze-restore-activities** — Summarize and investigate restore activity trends
- **export-apm-config** — Snapshot of all APM configuration objects
- **investigate-backup-failure** — Root cause analysis for backup failures
- **review-verification-videos** — Check backup-verification status and video links
- **export-m365-mailboxes** — Bulk PST export of Exchange or Group mailboxes
- **provision-apm-config** — Bulk-create or update APM infrastructure/plans from a config description
- **manage-apm-resource** — View, update, or remove a single existing resource
- **manage-running-jobs-and-version-locks** — Cancel a running job, or lock/unlock a backup version
- **reassign-or-retire-workload** — Move a workload to a different plan, or retire/delete it

Read this once rather than re-deriving the same caveats in every skill.

## List vs. get field completeness

`list_*` tools return lightweight, embedded references to related objects — not their full
detail. For example, a workload from `list_machine_workloads`/`list_m365_workloads` only
guarantees its `plan` sub-object has `plan_id`/`name`/`kind` (`kind` is `"protection"` or
`"retirement"`); fields like `plan.policy.schedule` are populated **only** by the corresponding
`get_*` call (`get_protection_plan`, `get_backup_server`, etc.), not by any `list_*` call.

**Rule of thumb**: don't call the matching `get_*` tool for every item in a list by default —
that's an N+1 call pattern once the list has more than a handful of items. Only fetch the
fuller detail when:
- the user specifically asks for that deeper field (e.g. "what's the backup schedule for each
  plan?"), or
- the item count is small enough that the extra calls are clearly cheap.

A cheaper static heuristic (e.g. "last backup older than 25 hours" instead of reading a plan's
actual schedule) is often good enough and keeps the tool-call footprint minimal.

**Activity detail fields**: `get_backup_activity`'s (and `get_restore_activity`'s) `log_entries`
array holds each entry's detail text in its `message` field — there is no separate
`error_message` field.

## Pagination convention

- Every `list_*` tool accepts `limit`/`offset` and returns `{items, total}`. If `total` exceeds
  the number of items returned, keep paging with `offset` until you've collected everything.
  Exception: `list_activity_logs`/`list_connection_logs`/`list_system_logs` return
  `{items, total, truncated}` with `total` always `null` — use the `truncated` flag instead to
  know whether more results exist beyond the current page.
- For activity queries (`list_backup_activities`, `list_restore_activities`), pass
  `history=true` to include completed activities in addition to active/queued ones — omit it
  when you only want what's currently running or queued.
- `limit=500` is the usual choice for "capture everything in one page" queries; it's the max
  page size most `list_*` tools accept.

## Permission modes

`APM_MCP_MODE` gates which tools are registered, in this order (each tier adds to the one
before it):

| Mode | Adds |
|---|---|
| `readonly` | list/get queries only — no mutations |
| `operator` | trigger/cancel backups, cancel activities, start/cancel/list M365 exports |
| `admin` | lock/unlock backup versions, create/update/delete plans, workloads, remote storage, file servers, auto-backup rules |

If a tool call fails with a permission error, tell the user which mode is required rather than
retrying — there's no way to escalate mid-session.

## Resource identification: id only, everywhere

No tool — read or mutate — accepts a name as a lookup key. `plan_name`, `server_name`,
`storage_name`, `hypervisor_name`, and `workload_name` do not exist as parameters anywhere in
this MCP server. The only `name`-shaped parameters that do exist are business fields you're
writing (a plan's display `name`, `vault_name`, `export_name`) — never confuse these with a way
to identify an existing resource.

To find a resource's id, call the matching `list_*` tool: `name_contains` narrows plans, backup
servers, and machine workloads server-side; `keyword` narrows M365 workloads server-side;
`list_remote_storages`/`list_hypervisors`/`list_saas_tenants` always return everything
unfiltered (no keyword param exists because there's nothing to filter server-side) — scan the
result yourself (for tenants, match on the `tenant_name` field). If more than one item matches,
decide which one is correct from the returned fields, or ask the user — no tool will pick one
for you.

## Update semantics: every field, every time

**Every `update_*` tool requires the complete desired field set on every call — there is no
partial-patch or keep-current-if-omitted behavior**, with exactly two exceptions:

- `update_machine_file_server`'s `login_password` accepts `None` to explicitly keep the current
  password, because APM does not expose it for re-reading (there is no other way to "leave it
  unchanged").
- `update_m365_auto_backup_rule`'s three group-id list fields (`exchange_group_ids`,
  `onedrive_group_ids`, `chat_group_ids`) use tri-state semantics instead: omit the field (leave
  it `None`) to keep that list unchanged, or pass `[]` explicitly to clear it. This is the
  opposite convention from every other list/object field in this server — don't pass `[]` when
  you mean "no change," and don't assume omitting it clears the list.

Every other field on every `update_*` tool must be supplied explicitly on every call — omitting
a field is a schema error, not a way to leave it unchanged.

**General rule**: fetch the resource's current full state with the matching `get_*` call before
calling any `update_*` tool, and re-supply every field from that current state except the ones
the user actually wants changed.

## Destructive action preview pattern

Every `delete_*` tool, plus `retire_machine_workload`/`retire_m365_workload`, takes a
`confirm: bool = False` parameter instead of committing immediately like `update_*` tools do:

- Called with `confirm=false` (the default), the tool does not execute — it returns a JSON
  preview instead: `{"preview": true, "action", "target", "warning"}`.
- Called with `confirm=true`, the tool executes the action and, if `APM_MCP_AUDIT_LOG` is
  configured, writes an audit-log entry for it.

**Always call once with `confirm=false` first**, show the returned preview/warning to the user,
and only call again with `confirm=true` after they explicitly approve — never skip straight to
`confirm=true` on the first call, even if the user's request already sounds like approval (e.g.
"delete workload X"), since the preview is what lets them catch a wrong target before anything
irreversible happens.

## Complex parameter formats: protection plans

`create_machine_protection_plan` / `update_machine_protection_plan` and
`create_m365_protection_plan` / `update_m365_protection_plan` share an identical
retention/schedule/Backup Copy parameter shape (M365 plans just omit the machine-only sections
below). Get the exact spelling and cross-field requirements right on the first call — none of
these are validated until the request reaches the tool.

**Retention (`retention_type`)** — the tool description already lists the five values and the
`is_immutable`/`keep_days` coupling; the one thing it omits:
- `keep_advanced` → set **all four** of `gfs_daily_versions`, `gfs_weekly_versions`,
  `gfs_monthly_versions`, `gfs_yearly_versions` (ints) — omitting even one raises an error. You
  may combine this with `retention_days`/`retention_versions` too.

**Main schedule** — see the tool description for `schedule_frequency`/`schedule_time`/`weekdays`
format; nothing to add here beyond that.

**Backup Copy (`backup_copy_*`)** — a second, independent copy destination with its own
retention and schedule. Leave `backup_copy_destination_id` unset to disable it entirely. Once
you set it, `backup_copy_destination_type` (`backup_server` or `remote_storage`),
`backup_copy_retention_type`, and `backup_copy_schedule_frequency` all become required in the
same call:
- `backup_copy_destination_type` determines which collection `backup_copy_destination_id` must
  come from — a `backup_server_id` under `backup_server`, a `storage_id` under
  `remote_storage`. An id from the wrong collection fails the lookup.
- `backup_copy_retention_type` accepts the same five values as the main retention, including
  `keep_advanced` with its own `backup_copy_gfs_*` quartet.
- `backup_copy_schedule_frequency` accepts `after_backup` in addition to `daily`/`weekly` (it
  does **not** accept `manual` or `hourly`, unlike the main schedule) — `after_backup` runs the
  copy right after each backup completes, ignoring `backup_copy_schedule_time`.
- `backup_copy_schedule_time`/`backup_copy_weekdays` follow the same `"HH:MM"`/3-letter-token
  format as the main schedule.

### Machine plans only: per-workload-type task overrides (`tasks_json`)

`tasks_json` is a **JSON-encoded string** (not a native list — encode it with your JSON
serializer before passing it), overriding the default per-workload-type task set. Omit it
entirely to accept the default 6-entry set with no per-type customization.

If you pass it, it must be a JSON array containing **exactly one entry for each of these 6
`(workload_type, os_type)` pairs — no more, no fewer** (a partial list to tweak just one pair is
rejected, not merged with defaults): `(pc, windows)`, `(pc, mac)`, `(ps, windows)`,
`(ps, linux)`, `(fs, none)`, `(vm, none)`. Valid `workload_type` values: `pc`, `ps`, `vm`, `fs`.
Valid `os_type` values: `windows`, `mac`, `linux`, `none` (`none` is required for `fs`/`vm`,
which have no OS distinction). Each entry's fields, all optional with these defaults if omitted:
`scope` (one of `entire_machine`, `system_volume`, `custom_volume`; omit for no scope
restriction), `custom_volumes` (list of strings, default `[]`), `include_external_drives`
(bool, default `false`), `include_boot_partition` (bool, default `true`), `use_main_schedule`
(bool, default `true`), `schedule` (omit to use the default; only meaningful when
`use_main_schedule=false`).

`schedule`, when present, is `{"time_schedule": {...}, "event_trigger": {...}}` — either or both:
- `time_schedule`: `{"frequency": "daily"|"weekly"|..., "start_time": "HH:MM", "weekdays": [...]}`
  — same format as the main plan schedule above.
- `event_trigger`: `{"on_sign_out": bool, "on_lock": bool, "on_startup": bool,
  "min_interval_seconds": int}` — at least one of `on_sign_out`/`on_lock`/`on_startup` must be
  `true`, and `min_interval_seconds` must be positive. Only meaningful for `pc` tasks (ignored
  for other workload types unless `use_main_schedule=false` makes it apply).

A full worked example — daily-scheduled default for 5 mandatory pairs, and a customized PC/
Windows task with both a weekly time schedule and an event trigger:

```json
[
  {
    "workload_type": "pc", "os_type": "windows", "use_main_schedule": false,
    "schedule": {
      "time_schedule": {"frequency": "weekly", "start_time": "09:00", "weekdays": ["mon", "wed", "fri"]},
      "event_trigger": {"on_sign_out": true, "on_lock": true, "on_startup": false, "min_interval_seconds": 1800}
    }
  },
  {"workload_type": "pc", "os_type": "mac"},
  {"workload_type": "ps", "os_type": "windows"},
  {"workload_type": "ps", "os_type": "linux"},
  {"workload_type": "fs", "os_type": "none"},
  {"workload_type": "vm", "os_type": "none"}
]
```

The 5 entries with no `schedule` key simply fall back to `use_main_schedule=true` (the plan's
top-level schedule) with default scope/volume settings — you don't need to spell out every
field, only the ones you're deviating from.

### Machine plans only: backup window (`backup_window_allowed_hours`)

A string of semicolon-separated `day:hours` clauses: `"mon:0-8,13-18;tue:0-23"`. Each clause is a
3-letter lowercase weekday (`sun`…`sat`) followed by `:` and a comma-separated list of hour
ranges (`H-H`, inclusive) or single hours (`H`), 0-23. Only meaningful when
`backup_window_enabled=true` — that flag and this string are independent parameters, so setting
one without the other is accepted but has no effect (string without `enabled=true`: ignored;
`enabled=true` without a string: no hours are restricted). Days/hours you don't mention are not
allowed for backups.

### Machine plans only: device-type advanced settings (`vm_*` / `pc_*` / `ps_*` / `db_*`)

Four independent, all-optional parameter groups, each gated by workload type:

| Group | Params | Applies to |
|---|---|---|
| `vm_*` | `vm_enable_app_aware_bkp`, `vm_enable_verification`, `vm_verification_video_duration_seconds`, `vm_enable_datastore_usage_detection`, `vm_datastore_min_free_space_percent` | VM tasks |
| `pc_*` | `pc_shutdown_after_backup`, `pc_wake_for_backup`, `pc_prevent_sleep_during_backup` | PC tasks |
| `ps_*` | `ps_enable_app_aware_bkp`, `ps_enable_verification`, `ps_verification_video_duration_seconds`, `ps_shutdown_after_backup`, `ps_wake_for_backup`, `ps_prevent_sleep_during_backup` | PS tasks |
| `db_*` | `db_action_on_error` (`continue`\|`stop`), `db_mssql_log_setting` (`do_not_truncate`\|`truncate`), `db_oracle_log_setting` (`do_not_delete`\|`delete`) | DB-aware PS/VM tasks |

Nothing enforces that a `vm_*` param only takes effect when the plan actually has VM tasks — set
the group matching what the plan targets.

**Per-group all-or-nothing-per-field footgun**: leaving every field in a group as `None` disables
that group entirely (its config becomes absent, not "default settings"). But as soon as you set
*any one* field in a group, every other field in that same group you leave as `None` resets to
that field's own default — not to the plan's current value — because these tools have no way to
read the plan's existing per-field settings back. The defaults are: `vm_enable_app_aware_bkp`
**true**, `vm_enable_verification` **false**, `vm_verification_video_duration_seconds` **120**,
`vm_enable_datastore_usage_detection` **false**, `vm_datastore_min_free_space_percent` **10**;
`pc_shutdown_after_backup`/`pc_wake_for_backup`/`pc_prevent_sleep_during_backup` all **false**;
`ps_enable_app_aware_bkp` **true**, `ps_enable_verification` **false**,
`ps_verification_video_duration_seconds` **120**,
`ps_shutdown_after_backup`/`ps_wake_for_backup`/`ps_prevent_sleep_during_backup` all **false**;
`db_action_on_error` **continue**, `db_mssql_log_setting` **do_not_truncate**,
`db_oracle_log_setting` **do_not_delete**. On `update_*`, always call `get_protection_plan` first
and resupply every field in a group you're touching at all, even the ones you don't intend to
change — never assume an omitted field preserves the plan's current value.

## File servers: backup scope (`path` vs `selectors`)

`add_machine_file_server`/`update_machine_file_server` take **exactly one** of `path` or
`selectors` (passing both is an error; on update, passing neither is also an error — there is no
default). `path` is a single string, the one directory the backup covers (`""` means the whole
file server root). `selectors` is a list of objects, each shaped
`{"path": str, "excluded_paths": [str, ...]}` — `excluded_paths` is optional per entry and
defaults to `[]` (nothing excluded). Use `selectors` for multiple included paths and/or
per-path exclusions; use `path` only for the single-unrestricted-path case. On update, fetch the
workload's current scope via `get_machine_workload`'s `fs_config.selectors` and pass it back
verbatim (as `selectors`, even if it has one entry) unless you're deliberately changing scope —
resupplying it as `path` instead loses any exclusions or additional paths it had.
