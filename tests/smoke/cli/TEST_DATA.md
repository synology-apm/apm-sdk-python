# Test Data Prerequisites

This checklist lists the resources that should exist on your `.env`-configured APM test
server *before* running the smoke test, so that as much of the CLI as possible is actually
exercised rather than gracefully skipped.

> **Note:** Every item below is a soft prerequisite. A fresh/empty APM still produces a valid
> run — steps whose prerequisite data is missing are recorded as `skipped` in `index.md`, not
> as failures. This checklist exists so a reviewer can tell "skipped because the feature
> isn't covered yet" apart from "skipped because the test environment has no data for it".

---

## Infra

- At least one backup server (`synology-apm-cli infra server list`).
- At least one **DP-type** appliance (`synology-apm-cli infra server list --type dp`) —
  **required**: without it, the entire `log` domain is skipped.
- Optional: a remote storage device, to exercise `infra storage get`.
- Optional: a hypervisor (ESXi or vCenter), to exercise `infra hypervisor get`.

## Machine

- At least one active Machine Workload (`synology-apm-cli machine list`).
- At least one of them has a backup version
  (`synology-apm-cli machine version list <name>`) — needed to exercise `version get`. The
  phase prefers a workload that has been backed up when picking its get/version target.
- Optional: at least one **retired** Machine Workload
  (`synology-apm-cli machine list --retired`) — exercises a non-empty `list --retired`.

## M365 / SaaS

- At least one M365-category tenant (`synology-apm-cli saas list`) — **required**: without it,
  the entire `m365` domain is skipped.
- For each of the six scopes — `exchange`, `onedrive`, `chat`, `group`, `sharepoint`,
  `teams` — at least one workload (`synology-apm-cli m365 <scope> list`) with at least one backup
  version (`synology-apm-cli m365 <scope> version list <name>`). The phase prefers a workload
  that has been backed up when picking each scope's get/version target.
- Optional: an existing export task for the `exchange`/`group` scope's selected workload —
  makes `m365 <scope> export list` return a non-empty listing (the command runs either way).

> **Note:** A fresh tenant with data in only one or two scopes is fine — the remaining scopes
> are skipped gracefully but go unexercised. To cover all six, configure one protected
> workload per scope.

- Optional: at least one **retired** workload in any scope (`synology-apm-cli m365 <scope> list
  --retired`) — exercises a non-empty `list --retired` for that scope.

## Activity

- Backup activity history (`synology-apm-cli activity backup list --history`) — populated
  naturally once the Machine/M365 workloads above have been backed up at least once; the
  search-mode `list`/`get` steps take their workload name from this history.
- Optional: restore activity history (`synology-apm-cli activity restore list --history`), to
  exercise the `activity restore list --search` / `get` steps.

## Plan

- At least one Protection Plan and one Retirement Plan
  (`synology-apm-cli plan protection list` / `synology-apm-cli plan retirement list`).
- Optional: at least one Tiering Plan (`synology-apm-cli plan tiering list`).

## Log

- No extra setup — reuses the DP-type appliance from "Infra" above.

---

## Sample environment

If you are setting up a test APM from scratch, the placeholder names in root `CLAUDE.md`'s
"Example Data Conventions" table double as a ready-made naming scheme — e.g. a `vm-web-01`
Machine Workload under a `Daily Backup` Protection Plan, an `alice@contoso.com` mailbox and
OneDrive, a `Marketing` SharePoint site, and so on. Naming real test resources after these
placeholders also makes it easy to write example commands/docs later without having to
substitute names.
