# Test Data Prerequisites

The underlying APM resources required are the same as `tests/smoke/cli/TEST_DATA.md` — if
you've already set up a test APM for the CLI smoke test, no additional setup is needed. This
document restates the checklist in terms of SDK collections/methods.

> **Note:** Every item below is a soft prerequisite. A fresh/empty APM still produces a valid
> run — steps whose prerequisite data is missing are recorded as `skipped` in `index.md`, not
> as failures (and never as `checks_failed`). This checklist exists so a reviewer can tell
> "skipped because the data doesn't apply to this APM" apart from "skipped because the test
> environment has no data for it".

---

## Infra

- At least one backup server (`apm.backup_servers.list()`).
- At least one **DP-type** appliance (`server.server_type == BackupServerType.DP`) —
  **required**: without it, the entire `log` domain is skipped.
- Optional: a remote storage device (`apm.remote_storages.list()`), to exercise
  `infra.remote_storages.get[direct]` and `infra.remote_storages.check[usage_parsing]`.
- Optional: a hypervisor, ESXi or vCenter (`apm.hypervisors.list()`), to exercise
  `infra.hypervisors.get[direct]`.

### Remote Storage CRUD roundtrip (add / update / delete)

The infra phase can also run a create–update–delete roundtrip against real storage backends.
Because the APM server validates connectivity before accepting a registration, this requires
real credentials — they are read from a separate TOML file, not from `.env`.

**Setup:**

```bash
cp tests/smoke/smoke_creds.toml.example tests/smoke/smoke_creds.toml
# Edit tests/smoke/smoke_creds.toml — replace placeholder values with real credentials.
```

`tests/smoke/smoke_creds.toml` is gitignored.

**Supported `type` values for `[[remote_storage]]` blocks:**

| `type` | `endpoint` | `vault` | `trust_self_signed` |
|---|---|---|---|
| `s3_compatible` | required (full URL, e.g. `https://s3.example.com:443`) | required | optional |
| `apv` | required (`host:port`, no `https://`) | ignored | optional |
| `amazon_s3` | ignored | required | ignored |
| `wasabi` | ignored | required | ignored |
| `c2` | ignored | required | ignored |
| `amazon_s3_china` | ignored | required | ignored |

Each `[[remote_storage]]` entry runs a full add→update→delete roundtrip, including the
unmanaged-catalog and (when `relink_encryption_key` is set) encryption-relink checks — see
`README.md`'s "Round-trip operations" section for the exact step sequence. To exercise the
unmanaged-catalog raise path, include at least one entry whose vault already holds catalogs
(e.g. one previously used by another APM instance) — deleting the storage at the end of the
roundtrip leaves adopted catalogs unmanaged again, so such a vault stays reusable across runs.

## Machine

The machine phase runs one block per workload type (PC, PS, VM, FS). Each block picks the
first matching active workload from `apm.machine.workloads.list(limit=500)`; if none is found
for a type, that block is skipped.

- Optional: at least one active **PC** workload — exercises the PC type block.
- Optional: at least one active **PS** workload — exercises the PS type block.
- Optional: at least one active **VM** workload — exercises the VM type block.
- Optional: at least one active **FS** workload — exercises the FS type block (checks
  `fs_config` population and runs a no-op `update_file_server()` round-trip).
- Optional: any of the above workloads with at least one backup version
  (`apm.machine.workloads.list_versions(workload, limit=20)`) — needed for
  `machine.{type}.versions.get_latest`, the lock/unlock round trip, and
  `activity.backup.get_by_version`.
- Optional: a version with non-empty `locations` — needed for the lock/unlock round trip
  to toggle state; if empty, the round trip instead exercises the `APIError` path.
- Optional: a PS or VM workload with a version whose `verify_status ==
  VerifyStatus.SUCCESS` — needed to exercise `machine.{type}.video_url.get`
  (`get_verification_video_url()`).

The `change_plan` round-trip in each type block creates a disposable protection plan inline —
no pre-existing plans are required.

## M365 / SaaS

- At least one tenant with `category == WorkloadCategory.M365` (`apm.saas.list(limit=500)`) —
  **required**: without it, the entire `m365` domain is skipped.
- For each of the six `M365WorkloadType` scopes — `exchange`, `onedrive`, `chat`, `sharepoint`,
  `teams`, `group` — at least one workload (`apm.m365.workloads.list(tenant_id, workload_type,
  limit=500)`) with at least one backup version
  (`apm.m365.workloads.list_versions(workload, limit=20)`).

> **Note:** A fresh tenant with data in only one or two scopes is fine — the remaining scopes
> are skipped gracefully but go unexercised. To cover all six, configure one protected
> workload per scope.

For the `exchange` and `group` scopes, the workload's latest version must have a non-empty
`portal_version_id` — needed for `m365.<scope>.export.start` and the rest of the export round
trip (`export.list`, `export.download_url.get`, `export.cancel`). Without it, all four export
steps (and, for `group`, `m365.group.check[export_no_archive_param]`) are skipped.

- Optional: at least one **retired** workload in any scope
  (`apm.m365.workloads.list(tenant_id, workload_type, is_retired=True, limit=500)`) — used by
  `m365.change_plan[retired_noop]` (see "Plan" below). If absent, that round trip is skipped.

## Activity

- Backup activity history (`apm.activities.backup.list(history=True, limit=100)`) — populated
  naturally once the Machine/M365 workloads above have been backed up at least once. Needed for
  `activity.backup.get[direct]`.
- Optional: at least one activity with `status == BackupActivityStatus.FAILED`, to give
  `activity.backup.check[status_failed_mapping]` a non-empty (but still trivially passing)
  result set.
- Optional: restore activity history (`apm.activities.restore.list(history=True, limit=100)`),
  to exercise `activity.restore.get[direct]`.
- Optional: an in-progress restore — start one (e.g. from the APM web UI) shortly before the
  activity phase runs, to exercise `activity.restore.cancel[ongoing]` and its settle check.
  There is no SDK call that triggers a restore, so the tool cannot produce one itself; without
  one, the cancel steps are recorded as conditional skips.

> **Warning:** The activity phase cancels the **first** in-progress restore it finds — do not
> run it while a restore you care about is in flight.

## Plan

- At least one Protection Plan (`apm.plans.list(limit=500)`) and one Retirement Plan
  (`apm.retirement_plans.list(limit=500)`).
- Optional: at least one Tiering Plan (`apm.tiering_plans.list(limit=500)`). If any tiering
  plan's `destination` is set, `plan.tiering.check[destination_resolution]` cross-checks
  `destination.identifier` against the `storage_id`s from `apm.remote_storages.list()` (Infra
  section above) — make sure the tiering destination is one of the remote storages already
  configured.
- Optional: at least **two distinct**, non-`is_immutable` M365-category Protection Plans, plus
  a workload in any scope whose `plan.name` matches one of them — needed for
  `m365.change_plan[switch]`/`[restore]` to find a workload and an alternative plan to switch
  it to and back. Both are auto-selected; skipped if absent.
- Optional: at least one retired workload in any scope whose `plan.name` matches the `name` of
  an existing Retirement Plan — needed for `m365.change_plan[retired_noop]`. Auto-selected;
  skipped if absent.

## Log

- No extra setup — reuses the DP-type appliance from "Infra" above.

---

## Sample environment

See `tests/smoke/cli/TEST_DATA.md`'s "Sample environment" section — the same placeholder
naming scheme applies here.
