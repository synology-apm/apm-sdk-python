"""All Enum definitions for the APM SDK."""
from enum import Enum


class WorkloadCategory(Enum):
    """Business domain of a Workload.

    MACHINE: Device backup (PC / PS / VM / FS), accessed via APMClient.machine.
    M365: Microsoft 365 SaaS backup, accessed via APMClient.m365.
    GWS: Google Workspace SaaS backup (reserved; not supported as of APM 1.2).
    """
    MACHINE = "machine"
    M365    = "m365"
    GWS     = "gws"


class MachineWorkloadType(Enum):
    """Sub-type of a Machine category Workload (PC / PS / VM / FS)."""
    PC = "pc"
    PS = "ps"
    VM = "vm"
    FS = "fs"


class M365WorkloadType(Enum):
    """Service sub-type of an M365 Workload."""
    EXCHANGE   = "exchange"
    ONEDRIVE   = "onedrive"
    CHAT       = "chat"
    SHAREPOINT = "sharepoint"
    TEAMS      = "teams"
    GROUP      = "group"


class ActivityWorkloadType(Enum):
    """Workload sub-type identifier as recorded in backup and restore activity records.

    Finer-grained than WorkloadCategory: MACHINE maps to multiple subtypes (PC, PS, VM, FS, etc.).
    UNKNOWN is used when the activity records an unrecognised workload sub-type.
    """
    MACHINE_PC      = "machine_pc"
    MACHINE_PS      = "machine_ps"
    MACHINE_VM      = "machine_vm"
    MACHINE_FS      = "machine_fs"
    MACHINE_CLOUDVM = "machine_cloudvm"
    M365            = "m365"
    GWS             = "gws"
    ORACLE          = "oracle"
    MSSQL           = "mssql"
    UNKNOWN         = "unknown"


class WorkloadStatType(Enum):
    """Workload type used in site-wide dashboard statistics."""
    MACHINE_PC  = "machine_pc"
    MACHINE_PS  = "machine_ps"
    MACHINE_VM  = "machine_vm"
    MACHINE_FS  = "machine_fs"
    M365        = "m365"
    GWS         = "gws"


class BackupServerType(Enum):
    """Hardware type of a backup server.

    DP:  ActiveProtect Appliance (purpose-built backup device).
    NAS: Synology NAS running Active Backup for Business.
    """
    DP  = "dp"
    NAS = "nas"


class BackupServerRole(Enum):
    """Role of a backup server in the APM cluster.

    PRIMARY:   Primary Management Server.
    SECONDARY: Secondary Management Server / Standby.
    """
    PRIMARY   = "primary"
    SECONDARY = "secondary"


class ServerStatus(Enum):
    """Health status of a backup server."""
    HEALTHY      = "healthy"
    WARNING      = "warning"
    CRITICAL     = "critical"
    DISCONNECTED = "disconnected"
    SYNCING      = "syncing"


class RemoteStorageStatus(Enum):
    """Connection status of a remote storage."""
    CONNECTED         = "connected"
    AUTH_FAILED       = "auth_failed"
    DISCONNECTED      = "disconnected"
    UNKNOWN           = "unknown"
    VAULT_NOT_MOUNTED = "vault_not_mounted"
    DATA_CORRUPTED    = "data_corrupted"
    UNMANAGED_CATALOG = "unmanaged_catalog"


class RemoteStorageType(Enum):
    """Storage type of a remote storage."""
    ACTIVE_PROTECT_VAULT = "active_protect_vault"
    C2_OBJECT_STORAGE    = "c2_object_storage"
    AMAZON_S3            = "amazon_s3"
    AMAZON_S3_CHINA      = "amazon_s3_china"
    WASABI               = "wasabi"
    AZURE_BLOB           = "azure_blob"
    AZURE_BLOB_CHINA     = "azure_blob_china"
    S3_COMPATIBLE        = "s3_compatible"
    UNKNOWN              = "unknown"


class BackupActivityStatus(Enum):
    """Status of a backup activity."""
    QUEUING    = "queuing"
    BACKING_UP = "backing_up"
    CANCELING  = "canceling"
    SUCCESS    = "success"
    FAILED     = "failed"
    PARTIAL    = "partial"
    CANCELED   = "canceled"


class RestoreActivityStatus(Enum):
    """Status of a restore activity."""
    PREPARING            = "preparing"
    RESTORING            = "restoring"
    CANCELING            = "canceling"
    READY_FOR_MIGRATE    = "ready_for_migrate"
    MIGRATE_VM_MANUALLY  = "migrate_vm_manually"
    MIGRATING            = "migrating"
    SUCCESS              = "success"
    FAILED               = "failed"
    PARTIAL              = "partial"
    CANCELED             = "canceled"


class VersionStatus(Enum):
    """Backup status of a WorkloadVersion."""
    NO_BACKUPS   = "no_backups"
    BACKING_UP   = "backing_up"
    SUCCESS      = "success"
    FAILED       = "failed"
    PARTIAL      = "partial"
    CANCELED     = "canceled"
    PAUSED       = "paused"
    DELETING     = "deleting"
    DELETE_FAILED = "delete_failed"


class WeekDay(Enum):
    """Weekday for a schedule cycle (0=Sunday, 1=Monday, ..., 6=Saturday)."""
    SUNDAY    = 0
    MONDAY    = 1
    TUESDAY   = 2
    WEDNESDAY = 3
    THURSDAY  = 4
    FRIDAY    = 5
    SATURDAY  = 6


class ScheduleFrequency(Enum):
    """Backup schedule frequency. MANUAL means no schedule (on-demand only);
    AFTER_BACKUP triggers automatically after each backup (Backup Copy only)."""
    MANUAL       = "manual"
    HOURLY       = "hourly"
    DAILY        = "daily"
    WEEKLY       = "weekly"
    AFTER_BACKUP = "after_backup"


class RetentionType(Enum):
    """Version retention policy type.

    KEEP_ALL:      Keep all backup versions indefinitely.
    KEEP_DAYS:     Keep versions created within the last N days.
    KEEP_VERSIONS: Keep only the latest N versions.
    KEEP_ADVANCED: Advanced GFS (Grandfather-Father-Son) retention rules.
    NONE:          Do not retain any versions.
    """
    KEEP_ALL      = "keep_all"
    KEEP_DAYS     = "keep_days"
    KEEP_VERSIONS = "keep_versions"
    KEEP_ADVANCED = "keep_advanced"
    NONE          = "none"


class WorkloadStatus(Enum):
    """Current backup status of a Workload.

    QUEUING: A job is waiting in the queue.
    BACKING_UP: Backup in progress (backup_progress holds the current percentage).
    SUCCESS: Most recent backup succeeded.
    FAILED: Most recent backup failed.
    PARTIAL: Most recent backup partially succeeded.
    CANCELED: Most recent backup was canceled.
    NO_BACKUPS: Never backed up, or status unavailable.
    DELETING: Workload deletion is in progress (transient).
    RETIRED: Workload is under a Retirement Plan; new backups will no longer be created.
    """
    QUEUING    = "queuing"
    BACKING_UP = "backing_up"
    SUCCESS    = "success"
    FAILED     = "failed"
    PARTIAL    = "partial"
    CANCELED   = "canceled"
    NO_BACKUPS = "no_backups"
    DELETING   = "deleting"
    RETIRED    = "retired"


class BackupScope(Enum):
    """Data scope of a Machine Workload backup."""
    ENTIRE_DEVICE_WITH_EXT_DRIVES = "entire_device_with_ext_drives"
    ENTIRE_DEVICE                 = "entire_device"
    VOLUME                        = "volume"
    FILE                          = "file"


class M365ExportStatus(Enum):
    """Status of an M365 mailbox PST export task."""
    READY_TO_DOWNLOAD = "ready_to_download"
    DOWNLOADED        = "downloaded"
    CANCELED          = "canceled"
    PREPARING         = "preparing"
    EXPIRED           = "expired"
    FAILED            = "failed"
    WARNING           = "warning"
    UNKNOWN           = "unknown"


class FileServerType(Enum):
    """File Server protocol / OS type.

    UNKNOWN is used when APM reports a server type not yet recognised by this SDK version.
    """
    SMB           = "smb"
    SYNOLOGY_NAS  = "nas"
    NUTANIX_FILES = "nutanix"
    NETAPP_ONTAP  = "netapp"
    UNKNOWN       = "unknown"


class VerifyStatus(Enum):
    """Backup verification status of a Workload or backup version (PS/VM only).

    VERIFY_NONE and missing verification results are both represented as None in the SDK;
    this enum only contains states that are meaningful to display.
    """
    VERIFYING     = "verifying"
    SUCCESS       = "success"
    FAILED        = "failed"
    CANCELED      = "canceled"
    NOT_SUPPORTED = "not_supported"
    NOT_ENABLED   = "not_enabled"
    PARTIAL       = "partial"
    WAITING       = "waiting"


# API verifyStatus raw string → VerifyStatus mapping.
# Shared by MachineWorkloadCollection and BackupActivityCollection parsers.
_VERIFY_STATUS_MAP: dict[str, "VerifyStatus"] = {
    "VERIFY_VERIFYING":     VerifyStatus.VERIFYING,
    "VERIFY_COMPLETED":     VerifyStatus.SUCCESS,
    "VERIFY_FAILED":        VerifyStatus.FAILED,
    "VERIFY_CANCEL":        VerifyStatus.CANCELED,
    "VERIFY_NOT_SUPPORTED": VerifyStatus.NOT_SUPPORTED,
    "VERIFY_NOT_ENABLED":   VerifyStatus.NOT_ENABLED,
    "VERIFY_PARTIAL":       VerifyStatus.PARTIAL,
    "VERIFY_WAITING":       VerifyStatus.WAITING,
}


class VersionCopyStatus(Enum):
    """Transfer status for a backup version copy, plan backup copy, or tiering operation.

    COMPLETED:   Transfer has finished successfully.
    NOT_ENABLED: Backup Copy or Tiering is not configured.
    IN_PROGRESS: Transfer is actively running.
    WAITING:     Transfer is queued and waiting to start.
    SCHEDULED:   Transfer is scheduled but not yet queued.
    SKIPPED:     Transfer was skipped; see copy_reason for details.
    FAILED:      Transfer failed; see copy_reason for details.
    RETRY:       Transfer is waiting to retry after a recoverable error; see copy_reason for details.
    """
    COMPLETED   = "completed"
    NOT_ENABLED = "not_enabled"
    IN_PROGRESS = "in_progress"
    WAITING     = "waiting"
    SCHEDULED   = "scheduled"
    SKIPPED     = "skipped"
    FAILED      = "failed"
    RETRY       = "retry"


class CopyReason(Enum):
    """Detail reason for a backup copy or tiering operation in a non-nominal state.

    Used in WorkloadVersion.copy_reason and PlanBackupCopyStatus.reason to describe
    why a copy is SKIPPED, RETRY, or FAILED.
    """
    DESTINATION_DISCONNECTED = "destination_disconnected"
    VERSION_INCOMPATIBLE     = "version_incompatible"
    DESTINATION_UPDATING     = "destination_updating"
    AUTH_FAILED              = "auth_failed"
    STORAGE_FULL             = "storage_full"
    QUOTA_EXCEEDED           = "quota_exceeded"
    INFRASTRUCTURE_ERROR     = "infrastructure_error"
    VAULT_SETUP_ISSUE        = "vault_setup_issue"
    DATA_CORRUPTED           = "data_corrupted"
    DESTINATION_MISSING      = "destination_missing"
    VOLUME_READONLY          = "volume_readonly"
    CERT_AUTH_FAILED         = "cert_auth_failed"
    NO_DESTINATION           = "no_destination"
    NO_VERSIONS_TO_COPY      = "no_versions_to_copy"
    SKIPPED_DB_OUTDATED      = "skipped_db_outdated"
    SKIPPED_NAS_ENCRYPTED    = "skipped_nas_encrypted"
    SKIPPED_NAS_TO_EXTERNAL  = "skipped_nas_to_external"
    SKIPPED_SOURCE_EQ_DEST   = "skipped_source_eq_dest"


_VERSION_COPY_STATUS_MAP: dict[str, "VersionCopyStatus"] = {
    "COPY_STATUS_NONE":        VersionCopyStatus.COMPLETED,
    "COPY_STATUS_NOT_ENABLED": VersionCopyStatus.NOT_ENABLED,
    "COPY_STATUS_IN_PROGRESS": VersionCopyStatus.IN_PROGRESS,
    "COPY_STATUS_WAITING":     VersionCopyStatus.WAITING,
    "COPY_STATUS_SCHEDULED":   VersionCopyStatus.SCHEDULED,
    "COPY_STATUS_SKIPPED":     VersionCopyStatus.SKIPPED,
    "COPY_STATUS_FAILED":      VersionCopyStatus.FAILED,
    "COPY_STATUS_RETRY":       VersionCopyStatus.RETRY,
}


class DbActionOnError(Enum):
    """Action when a database processing error occurs during backup.

    CONTINUE: Continue the backup even if a database error occurs.
    STOP:     Abort the backup on the first database error.
    """
    CONTINUE = "continue"  # API: IGNORE_FAILURES
    STOP     = "stop"      # API: REQUIRE_SUCCESS


class MssqlLogSetting(Enum):
    """Transaction log handling for Microsoft SQL Server databases during backup.

    DO_NOT_TRUNCATE: Leave transaction logs intact after backup.
    TRUNCATE:        Truncate transaction logs after backup.
    """
    DO_NOT_TRUNCATE = "do_not_truncate"  # API: DELETE_LOGS_BY_DB_RULE
    TRUNCATE        = "truncate"          # API: TRUNCATE_LOGS


class OracleLogSetting(Enum):
    """Archived log handling for Oracle databases during backup.

    DO_NOT_DELETE: Leave archived logs intact after backup.
    DELETE:        Delete archived logs after backup.
    """
    DO_NOT_DELETE = "do_not_delete"  # API: NOT_DELETE_LOGS
    DELETE        = "delete"          # API: DELETE_LOGS


class MachineOsType(Enum):
    """Operating system type within a configDevice task entry.

    WINDOWS: Windows PC or physical server.
    MAC:     macOS PC.
    LINUX:   Linux physical server.
    NONE:    Not applicable (VM and FS tasks always use NONE).
    """
    WINDOWS = "windows"  # API: "WINDOWS"
    MAC     = "mac"      # API: "MAC"
    LINUX   = "linux"    # API: "LINUX"
    NONE    = "none"     # API: "NONE"


class MachineTaskScope(Enum):
    """Backup scope for a PC or physical server task entry.

    ENTIRE_MACHINE: All volumes; optionally includes external drives.
    SYSTEM_VOLUME:  Operating system volume only.
    CUSTOM_VOLUME:  User-specified volumes or mount points.
    """
    ENTIRE_MACHINE = "entire_machine"  # API: BACKUP_SOURCE_BAREMETAL
    SYSTEM_VOLUME  = "system_volume"   # API: BACKUP_SOURCE_SYSVOL
    CUSTOM_VOLUME  = "custom_volume"   # API: BACKUP_SOURCE_CUSVOL


class HypervisorType(Enum):
    """Hypervisor inventory type.

    VSPHERE_ESXI:            VMware vSphere (ESXi standalone host)
    VSPHERE_VCENTER:         VMware vSphere (vCenter Server)
    HYPERV_STANDALONE:       Microsoft Hyper-V (Standalone)
    HYPERV_SCVMM:            Microsoft Hyper-V (SCVMM)
    HYPERV_FAILOVER_CLUSTER: Microsoft Hyper-V (Failover Cluster)
    UNKNOWN:                 Unrecognized host type.
    """
    VSPHERE_ESXI             = "vsphere_esxi"
    VSPHERE_VCENTER          = "vsphere_vcenter"
    HYPERV_STANDALONE        = "hyperv_standalone"
    HYPERV_SCVMM             = "hyperv_scvmm"
    HYPERV_FAILOVER_CLUSTER  = "hyperv_failover_cluster"
    UNKNOWN                  = "unknown"


class LogLevel(Enum):
    """Severity level of a server log entry."""
    INFO = "info"
    WARNING     = "warning"
    ERROR       = "error"


class APMActivityLogType(Enum):
    """Category of an APM activity log entry."""
    PROTECTION  = "protection"
    SYSTEM      = "system"
    DATA_ACCESS = "data_access"


class RestoreType(Enum):
    """Restore mode of a restore activity."""
    FILE_LEVEL        = "file_level"
    FULL              = "full"
    SYSTEM_VOLUME     = "system_volume"
    CUSTOMIZED_VOLUME = "customized_volume"
    VM_FULL           = "vm_full"
    INSTANT_AEM       = "instant_aem"
    INSTANT_VMWARE    = "instant_vmware"
    INSTANT_HYPERV    = "instant_hyperv"
    ORACLE_DATABASE   = "oracle_database"
    MSSQL_DATABASE    = "mssql_database"
    INSTANT_NUTANIX   = "instant_nutanix"
    INSTANT_PROXMOX   = "instant_proxmox"
    UNKNOWN           = "unknown"
