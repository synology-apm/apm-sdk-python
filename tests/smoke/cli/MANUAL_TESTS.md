# Manual Tests — Irreversible Commands

> **Warning:** The commands below permanently retire a workload from APM protection. They are
> excluded from the CLI smoke-test automation because the action cannot be undone. Run them
> by hand, against a **disposable** workload, when you need to verify retirement behavior.

## Prerequisites

- A disposable workload you are willing to permanently retire, for each scope you want to
  test (a Machine Workload, and/or one workload per M365 scope).
- A valid Retirement Plan name or ID: `synology-apm-cli plan retirement list --verbose` (the
  `name` or `plan_id` column).
- For M365/GWS scopes, optionally a tenant ID or domain: `synology-apm-cli saas list` (the `ID`
  column — the M365 tenant UUID or the GWS domain). `--tenant-id`/`--domain` may be omitted if
  there is only one tenant/domain of that category.

## What to verify for every invocation

1. Even with `--yes`, the command prints the retirement plan's retention summary and an
   irreversibility warning before proceeding.
2. After retirement, the workload disappears from the default (protected-only) `list` and
   appears in `list --retired` with `"is_retired": true` (JSON output).
3. A subsequent `backup`/`cancel` on the retired workload fails with a clear error (the
   workload is no longer under active protection).

## Template

Search mode (by name/identifier):

```bash
synology-apm-cli <SUBCOMMAND> retire "<IDENTIFIER>" [--tenant-id <TENANT_ID>] --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Direct mode (ID + namespace, from `synology-apm-cli <SUBCOMMAND> list --verbose`):

```bash
synology-apm-cli <SUBCOMMAND> retire --id <WORKLOAD_ID> --namespace <NAMESPACE> --plan <RETIREMENT_PLAN_NAME_OR_ID> --yes
```

Verify: `synology-apm-cli <SUBCOMMAND> list --retired -o json` includes the workload with
`"is_retired": true`.

Substitute `<SUBCOMMAND>` and `<IDENTIFIER>` per scope (`--tenant-id` applies to M365 scopes
only and `--domain` applies to GWS scopes only, each optional per the Prerequisites note above):

| Scope | `<SUBCOMMAND>` | Identifier type | Example `<IDENTIFIER>` |
|---|---|---|---|
| Machine Workload | `machine` | Workload name | `vm-web-01` |
| M365 Exchange mailbox | `m365 exchange` | UPN | `alice@contoso.com` |
| M365 OneDrive | `m365 onedrive` | UPN | `alice@contoso.com` |
| M365 Teams chat (user) | `m365 chat` | UPN | `alice@contoso.com` |
| M365 group / shared mailbox | `m365 group` | Group email | `marketing@contoso.com` |
| M365 SharePoint site | `m365 sharepoint` | Site name | `Marketing` |
| M365 Teams team | `m365 teams` | Team name | `Engineering` |
| GWS Mail | `gws mail` | Email | `alice@gwsdemo.example.com` |
| GWS Drive | `gws drive` | Email | `alice@gwsdemo.example.com` |
| GWS Contact | `gws contact` | Email | `alice@gwsdemo.example.com` |
| GWS Calendar | `gws calendar` | Email | `alice@gwsdemo.example.com` |
| GWS Shared Drive | `gws shared-drive` | Drive name | `Marketing Drive` |
