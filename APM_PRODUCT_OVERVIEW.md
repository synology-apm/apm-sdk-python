# Synology ActiveProtect Manager — Product Overview

> Purpose: APM product domain knowledge — the backup/recovery model, workload categories, and
> key concepts that the SDK's design builds on.
>
> This document does not describe the SDK itself. For the SDK's design rationale, public
> interface, type definitions, enum/API string mappings, and collection behavior rules, see
> `packages/synology-apm-sdk/src/synology_apm/sdk/README.md` (design contract) and the Sphinx API docs. For SDK usage examples, see
> `packages/synology-apm-sdk/README.md`.

---

## APM Product Feature Overview

### Backup & Recovery

APM refers to all protected objects collectively as **Workload**, divided by business domain into `WorkloadCategory`: **MACHINE** (device backup) and **M365** (cloud SaaS backup); `GWS` (Google Workspace SaaS) is reserved as of APM 1.2 and not yet supported.

#### MACHINE (Device Backup)

Further divided into four subtypes by `MachineWorkloadType`:

| `MachineWorkloadType` | Backup Level | Backup Technology | Restore Modes |
|---|---|---|---|
| **PC** | Block (disk image) | Windows: VSS + CBT incremental; Mac: agent-based backup | Bare metal restore / Volume / File download |
| **PS** (Physical Server) | Block (disk image) | Windows: VSS + CBT; Linux: agent-based backup | Bare metal restore / P2V / Instant Restore / Volume restore |
| **VM** (Virtual Machine) | Block (disk image) | RCT / CBT agentless (VMware / Hyper-V, etc.) | Instant Restore / V2V / Volume / File |
| **FS** (File Server) | File (file-level) | Backed up via network share protocols such as SMB (not block-level) | Folder / Single-file restore |

- PC / PS / VM: A Version represents a full disk image at a point in time; the smallest restore granularity can go down to a single file (granular restore). Application-aware backups such as MSSQL / Oracle fall under PS/VM, not separate subtypes
- FS: A Version represents a file tree snapshot at a point in time; the smallest restore granularity is folder / single file. Currently backed up via SMB, divided by source device type into SMB (Windows File Server, etc.), Synology NAS, Nutanix Files, NetApp ONTAP; NFS is not yet supported

#### M365 (Cloud SaaS Backup)

Backs up cloud data via the Microsoft 365 API. **One Workload = one account (or site/team) for one service**, divided by `M365WorkloadType` into six subtypes:

| `M365WorkloadType` | Workload Subject | Info Type | Identification |
|---|---|---|---|
| **EXCHANGE** | A single user's Exchange mailbox | `M365UserInfo` | `user_principal_name` |
| **ONEDRIVE** | A single user's OneDrive | `M365UserInfo` | `user_principal_name` |
| **CHAT** | A single user's Teams 1:1 chat | `M365UserInfo` | `user_principal_name` |
| **SHAREPOINT** | A single SharePoint site | `M365SiteInfo` | `site_url`, `site_name` |
| **TEAMS** | A single Team (covering all its channels) | `M365TeamInfo` | `team_id`, `team_name`, `web_url` |
| **GROUP** | M365 Group (shared mailbox) | `M365GroupInfo` | `group_id`, `display_name`, `mail` |

> For the same user, Exchange, OneDrive, and Chat are three independent workloads, each with its own backup schedule and version history.
> The backup unit for `TEAMS` is the entire Team (covering all channels), not a single channel.

- A Version represents the state of cloud items at a point in time
- Smallest restore granularity: mailbox / single email / file / chat history

**Key differences between MACHINE and M365:**

| | MACHINE (PC/PS/VM) | MACHINE (FS) | M365 |
|---|---|---|---|
| Backup level | Block (disk image) | File (file-level) | Item (cloud object) |
| Requires Agent | Yes (some) | No (SMB) | No (API) |
| Version semantics | Disk snapshot | File tree snapshot | Cloud items snapshot |
| Instant Restore | Supported | Not applicable | Not applicable |

**Key Concepts:**

- **Protection Plan**: The core unit of a backup task, including schedule, version retention rules (by days / version count / GFS), backup sources, and application-aware settings
- **Immutable Backup**: Locks versions via WORM mechanism; the retention period is determined at creation time and cannot be modified afterward
- **CBT / RCT**: Changed Block Tracking / Resilient Change Tracking, significantly reducing the transfer volume of incremental backups
- **Version retention execution**: Runs automatically every day at 01:00 AM
- **Concurrent backup limits**: Machine workloads support up to 40 concurrent backups (of which scheduled backups are capped at 20); M365 supports up to 60 concurrent backups

### Backup Copy

Copies backup versions to off-site storage, implementing a 3-2-1 backup strategy. Backup Copy settings are part of a Protection Plan, not a standalone resource, and the current APM API supports only one Backup Copy destination per Plan. (For the SDK-level representation — `ProtectionPlan.copy_policy` / `copy_destination`, `WorkloadVersion.locations` — see `packages/synology-apm-sdk/src/synology_apm/sdk/README.md`.)

**Other Technical Details:**
- Supported destination types: C2 Object Storage, AWS S3, AWS S3 China, ActiveProtect Vault, Wasabi, Azure Blob, Azure Blob China, S3-compatible storage
- Supports source-side global deduplication
- Provides immutable protection via the S3 Object Lock API and Synology NAS WORM API

### Scheduling

A sub-setting of the Protection Plan, not a standalone module:

- Schedule granularity: hourly / daily / weekly
- Supports timezone configuration
- Supports a backup window — restricts which time periods backups may run
- Supports event triggers (Windows PC): screen lock, user logoff, system startup
- Supports pre/post-backup actions (auto shutdown, prevent sleep, Wake-on-LAN)

### Centralized Management

Enterprise-grade multi-site management capability:

- Manages up to **2,500 sites** and **150,000 workloads**
- Supports Synology NAS as an ActiveProtect site (via Active Backup for Business 3.0)
- Centralized management of Protection Plans, tasks, and status across all backup servers

### Account & Privileges

- Local user and group management
- Domain / LDAP integration (Active Directory, OpenLDAP)
- Role-based access control (RBAC): Permission Management

### Security / Ransomware Defense

Differentiating capabilities emphasized in APM 1.1+:

- **WORM / Immutable Backup**: Prevents backups from being deleted or tampered with
- **Air-gap Backup**: Three tiers of air-gap options
- **Allow Access Mode**: Allowlist of IPs to restrict inbound traffic
- **Self-healing Integrity Check**: Self-healing integrity verification
- **Sandbox Recovery Test**: Validates restores in an isolated environment using a built-in hypervisor

### Monitoring & Notification

- Dashboard: Overview of overall protection status
- Activities: Backup / restore activity logs
- Resource Monitor: CPU / memory / disk usage
- Notification: Email / webhook alerts
- Log: System event logs and operation audit records

### Storage / Tiering

- Storage pool management, disk health monitoring
- Tiering: Automatically moves older versions to lower-cost storage tiers

### Affiliated Utilities

| Tool | Description |
|---|---|
| **ActiveProtect Agent** | Installed on protected endpoints, supports batch deployment (MSI parameters) |
| **ActiveProtect Recovery Media Creator** | Creates bootable ISO / USB recovery media |
| **ActiveProtect Recovery Tool** | Front-end tool for performing bare metal restores |
| **ActiveProtect Vault** | DSM package, serves as a Backup Copy destination |
