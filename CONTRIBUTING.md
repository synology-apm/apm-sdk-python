# Contributing

See [`CLAUDE.md`](CLAUDE.md) for the development guide and the "Install From Source" section of
[`README.md`](README.md) for environment setup.

This file documents the canonical placeholder values used across the codebase.

## Example Data Conventions

The same artifacts covered by `CLAUDE.md`'s "Language Policy" — plus README examples and test
fixtures — must never contain real data captured from a live APM/test environment: real
hostnames, IPs, account names, tenant names, serial numbers, or version strings. This applies
even when the value was obtained by running the CLI/SDK against a real test server to verify
behavior — substitute it with a placeholder from the table below before committing.

Reuse these values consistently so examples form a coherent, recognizable "sample environment."

| Category | Canonical value(s) | Notes |
|----------|--------------------|-------|
| CLI connection target / `APM_HOST` / `SiteInfo.external_address` | `apm.corp.com` (primary), `apm2.corp.com` (secondary/DR site) | `.env`, `config set --host`, `APMClient(...)`, error messages |
| Primary backup server (`BackupServer.name` / `.hostname`) | `apm-server-01` / `192.0.2.1` | |
| Secondary backup server (HA) | `apm-server-02` / `192.0.2.2` | |
| Additional backup servers | `apm-server-03`, ... / `192.0.2.3`, ... | |
| Backup server in a non-default state | `apm-server-<state>` / `192.0.2.N` | e.g. `apm-server-dr`, `apm-server-updating`, `apm-server-tiering` |
| NAS-type backup server | `nas-server-01` / `10.0.0.10` | |
| ESXi / hypervisor | `esxi1.example.com` / `192.0.2.40` | |
| Nutanix hypervisor | `nutanix1.example.com` / `192.0.2.44` | covers Prism Element/Central |
| Proxmox hypervisor | `proxmox1.example.com` / `192.0.2.45` | covers Node/Cluster |
| AWS hypervisor (cloud inventory) | `aws-account-01` | cloud inventories are identified by an account/tenant alias, not a hostname/IP — no address value |
| Azure hypervisor (cloud inventory) | `azure-tenant-01` | same as above |
| Hypervisor account | `root` (local) / `administrator@vsphere.local` (vCenter) | |
| VM workload (primary) | `vm-web-01` (restore dest: `vm-web-01-restored`) | |
| Additional VM workloads | `vm-app-01`, `vm-db-01` (same `-restored` pattern) | |
| PC / device workload | `CORP-PC-001` | |
| Other device/workload examples | `old-laptop`, `prod-server-01`, `MyPC` | varied names are fine, no single canonical value required |
| File server / share workload | `Corp Share` | |
| M365 user mailbox | `alice@contoso.com` (secondary: `bob@contoso.com`) | |
| M365 group / shared mailbox | `marketing@contoso.com` | |
| M365 tenant / SaaS application | `Contoso` (name) / `contoso.onmicrosoft.com` (domain) | SDK: `M365TenantInfo` |
| SharePoint site | `Marketing` (`https://contoso.sharepoint.com/sites/Marketing`) | |
| M365 Team | `Engineering` | |
| GWS domain | `gwsdemo.example.com` | SDK: `GWSDomainInfo`; the domain string also serves as its own identifier |
| GWS domain admin | `evelyn.test@gwsdemo.example.com` | |
| GWS user (Mail/Drive/Contact/Calendar) | `alice@gwsdemo.example.com` | |
| GWS Shared Drive | `Marketing Drive` | |
| Primary remote storage (DSM-based) | `DSM-Storage` / `192.0.2.20:8444`, vault `MyVault` | |
| Tiering destination (S3-compatible) | `tiering-remote` / `https://s3.example.com:443` | |
| APV-based external vault | `APV Vault` / `apv.example.com`, vault `my-bucket` | distinct storage type from `DSM-Storage` |
| Azure Blob Storage remote storage | account `azurestorage01`, container `my-container`, secret `azure-secret-01` | tenant ID / application (client) ID reuse the Resource UUID pattern below (two distinct values) |
| Appliance model | `DP320` | |
| Reference NAS model | `DS720+` | |
| Serial number | `SN001` (pattern: `SN` + digits) | |
| APM software version | `APM 1.2-71845` | build number must stay fictional — never copy a version string from a live system |
| Protection / retirement plan name | `Daily Backup` / `Compliance Retention` | |
| Admin username | `admin` | |
| Two-factor authentication (TOTP) code | `123456` | |
| Resource UUIDs (workload/plan/namespace/tenant/version IDs) | `123e4567-e89b-12d3-a456-4266141740NN` (increment `NN` per distinct resource in an example) | based on the RFC 4122 example UUID; truncated form `123e4567-...` is fine |
| IP addresses (not covered above) | RFC 5737: `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`; or `10.0.0.0/24` | |
| Hostnames (not covered above) | RFC 2606: `example.com` / `example.org` | |

## Commit Convention

```
feat:  new feature        feat: implement WorkloadCollection.list()
fix:   bug fix            fix: handle 401 re-auth in _http.py
docs:  documentation      docs: map backup_now to REST API
test:  tests              test: add unit tests for MachineWorkload
chore: configuration      chore: add pytest-recording to dev deps
```

A commit message describes only what the diff actually contains — its end state and
motivation — not the development history behind it. If a bug was introduced and fixed
entirely within the same uncommitted working tree before this commit, don't narrate that
discovery/fix in the message: there is no prior commit showing the buggy state, so the
"fix" has no corresponding change visible in the diff. Just describe the resulting
behavior/feature.

## Version Bump

All three packages (`synology-apm-sdk`, `synology-apm-cli`, `synology-apm-mcp`) share a single
lockstep version number, bumped together and never independently. `make check-version-consistency`
(part of `make test`) statically compares the `version` field of all three
`packages/*/pyproject.toml` files and the `synology-apm-sdk==X.Y.Z` dependency pin in
`synology-apm-cli`'s and `synology-apm-mcp`'s `dependencies`.

External contributors do not need to manage version numbers — include your changes in a PR and
the maintainers will handle versioning and publishing.
