---
name: export-apm-config
description: "Export a structured snapshot of APM configuration (servers, storage, plans, hypervisors, file servers, M365/GWS tenants/domains and auto-backup rules) for audit or migration documentation. Use when the user asks to export, document, or snapshot the current APM setup."
---

# Export APM Configuration

When the user asks to export APM configuration, generate a configuration snapshot, or
document the current APM setup for auditing or migration purposes:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

1. Collect all configuration objects in parallel:
   - `apm://site` — site info, external address (equivalent to calling `get_site_info` directly;
     prefer the resource when the client supports it)
   - `apm://servers` — all backup servers
   - `apm://plans/protection` — all protection plans (machine + M365 + GWS): name/category/retention
   - `apm://plans/retirement` — all retirement plans
   - `apm://plans/tiering` — all tiering plans
   - `list_remote_storages` — remote storage destinations
   - `list_hypervisors` — registered hypervisors
   - `apm://saas-applications` — SaaS applications (M365 tenants + GWS domains)
   - `list_machine_workloads` with `workload_types="fs"` — file-server workloads
   - `list_m365_auto_backup_rules` for each tenant — M365 auto-backup rules and collab settings
   - `list_gws_auto_backup_rules` for each domain — GWS auto-backup rules, collab settings, and
     protected-account-types setting

2. Organize the output as a configuration document with sections:
   ```
   # APM Configuration Export
   ## Site
   ## Backup Servers (N)
   ## Remote Storage (N)
   ## Hypervisors (N)
   ## Protection Plans (N)
   ## Retirement Plans (N)
   ## Tiering Plans (N)
   ## File Server Workloads (N)
   ## M365 Tenants (N)
   ## M365 Auto-Backup Rules (N)
   ## GWS Domains (N)
   ## GWS Auto-Backup Rules (N)
   ```

3. For each section, include the key fields:
   - Backup servers: name, model, role (primary/secondary), status, tiering plan
   - Plans: name, category, retention policy — all available from the `apm://plans/protection`
     list. Schedule frequency is **not** included there and isn't fetched by default (see
     `apm-mcp-conventions` for the list-vs-get caveat and when the extra per-plan calls are
     worth it).
   - Remote storage: name, type, endpoint, vault, encryption status
   - File server workloads: name, connection type, backup server, plan
   - M365 auto-backup rules: tenant, rule scope (Exchange/OneDrive/Teams Chat), included
     group IDs, collab settings
   - GWS auto-backup rules: domain, rule scope (Mail/Calendar/Contact/Drive), included group
     IDs, Shared Drive collab setting, protected-account-types setting

4. The export describes configuration, not backup data. Sensitive fields (passwords, tokens)
   are never included in API responses.

   If the user wants more detail on one specific tenant, domain, or hypervisor than the
   list-level fields above provide, `get_m365_tenant`/`get_gws_domain`/`get_hypervisor` fetch
   that single record's full state (see `apm-mcp-conventions` for the list-vs-get caveat) — not
   needed for a full-environment export, only when drilling into one item.

5. Present the export as a structured markdown document the user can save or share.
   Note the export timestamp so readers know when it was captured.

## See also

- [provision-apm-config](../provision-apm-config/SKILL.md) — the create/import counterpart; re-provision this same configuration on another APM server
