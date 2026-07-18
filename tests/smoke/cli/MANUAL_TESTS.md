# Manual Tests — Irreversible Commands

> **Warning:** The commands below permanently retire a workload from APM protection. They are
> excluded from the CLI smoke-test automation because the action cannot be undone. Run them
> by hand, against a **disposable** workload, when you need to verify retirement behavior.

## Prerequisites

- A disposable workload you are willing to permanently retire, for each scope you want to
  test (a Machine Workload, and/or one workload per M365 scope).
- A valid Retirement Plan name or ID: `synology-apm-cli plan retirement list --verbose` (the
  `name` or `plan_id` column).
- For M365 scopes, optionally a tenant ID: `synology-apm-cli saas list --verbose` (the
  `tenant_id` column). `--tenant-id` may be omitted if there is only one M365 tenant.

## What to verify for every invocation

1. Even with `--yes`, the command prints the retirement plan's retention summary and an
   irreversibility warning before proceeding.
2. After retirement, the workload disappears from the default (protected-only) `list` and
   appears in `list --retired` with `"is_retired": true` (JSON output).
3. A subsequent `backup`/`cancel` on the retired workload fails with a clear error (the
   workload is no longer under active protection).

## 1. Machine Workload

Search mode (name lookup):

```bash
synology-apm-cli machine retire "vm-web-01" --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode (ID + namespace, from `synology-apm-cli machine list --verbose`):

```bash
synology-apm-cli machine retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli machine list --retired -o json` includes the workload with
`"is_retired": true`.

## 2. M365 Exchange mailbox

Search mode (UPN):

```bash
synology-apm-cli m365 exchange retire "alice@contoso.com" --tenant-id <TENANT_ID> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode:

```bash
synology-apm-cli m365 exchange retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli m365 exchange list --retired -o json` includes the workload with
`"is_retired": true`.

## 3. M365 OneDrive

Search mode (UPN):

```bash
synology-apm-cli m365 onedrive retire "alice@contoso.com" --tenant-id <TENANT_ID> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode:

```bash
synology-apm-cli m365 onedrive retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli m365 onedrive list --retired -o json` includes the workload with
`"is_retired": true`.

## 4. M365 Teams chat (user)

Search mode (UPN):

```bash
synology-apm-cli m365 chat retire "alice@contoso.com" --tenant-id <TENANT_ID> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode:

```bash
synology-apm-cli m365 chat retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli m365 chat list --retired -o json` includes the workload with
`"is_retired": true`.

## 5. M365 group / shared mailbox

Search mode (group email):

```bash
synology-apm-cli m365 group retire "marketing@contoso.com" --tenant-id <TENANT_ID> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode:

```bash
synology-apm-cli m365 group retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli m365 group list --retired -o json` includes the workload with
`"is_retired": true`.

## 6. M365 SharePoint site

Search mode (site name):

```bash
synology-apm-cli m365 sharepoint retire "Marketing" --tenant-id <TENANT_ID> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode:

```bash
synology-apm-cli m365 sharepoint retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli m365 sharepoint list --retired -o json` includes the workload with
`"is_retired": true`.

## 7. M365 Teams team

Search mode (team name):

```bash
synology-apm-cli m365 teams retire "Engineering" --tenant-id <TENANT_ID> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode:

```bash
synology-apm-cli m365 teams retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli m365 teams list --retired -o json` includes the workload with
`"is_retired": true`.
