"""Workload data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..enums import (
    FileServerType,
    M365WorkloadType,
    MachineWorkloadType,
    VerifyStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from .location import LocationInfo
from .protection_plan import ProtectionPlan
from .retirement_plan import RetirementPlan


@dataclass(frozen=True)
class Workload:
    """Base class for all Workloads.

    Attributes:
        workload_id:             Unique workload identifier within APM.
        name:                    Display name.
        category:                Business domain (MACHINE / M365 / GWS).
        namespace:               Namespace of the owning backup server.
        last_backup_at:          Time of the last successful backup; None if never backed up.
        is_retired:              Whether the workload is retired.
        protected_data_bytes:     Protected data size in bytes.
        status:                   Current backup status. RETIRED for workloads under a Retirement Plan.
        plan:                     The plan currently applied to this workload (protection or
                                  retirement). Only plan_id, name, and category are guaranteed;
                                  other fields are None unless the full plan was separately
                                  fetched via the plans / retirement_plans collection.
        backup_progress:          Backup progress percentage (0–100); set for PC/PS/VM when BACKING_UP, else None.
        items_backed_up:          Number of items backed up; set for FS/M365 when BACKING_UP, None for PC/PS/VM.
        backup_server:            Backup server location info; None if unknown.
        backup_copy_destination:  Backup Copy destination location info; None if not configured.
        backup_copy_data_bytes:   Backup Copy storage space in bytes; 0 if no Backup Copy is configured.
    """
    workload_id: str
    name: str
    category: WorkloadCategory
    namespace: str
    last_backup_at: datetime | None
    is_retired: bool
    protected_data_bytes: int
    status: WorkloadStatus = field(kw_only=True)
    plan: ProtectionPlan | RetirementPlan = field(kw_only=True)
    backup_progress: int | None = field(default=None, kw_only=True)
    items_backed_up: int | None = field(default=None, kw_only=True)
    backup_server: LocationInfo | None = field(default=None, kw_only=True)
    backup_copy_destination: LocationInfo | None = field(default=None, kw_only=True)
    backup_copy_data_bytes: int = field(default=0, kw_only=True)

    @property
    def is_backing_up(self) -> bool:
        """Whether a backup job is currently running."""
        return self.backup_progress is not None or self.items_backed_up is not None


@dataclass(frozen=True)
class MachineWorkload(Workload):
    """Device backup Workload (category=MACHINE).

    Covers PCs, physical servers, VMs, and file servers (PC / PS / VM / FS sub-types).
    The category field is always WorkloadCategory.MACHINE.

    Attributes:
        workload_type:   Device sub-type (PC / PS / VM / FS).
        agent_version:   Installed agent version (PC/PS); None for VM and FS.
        verify_status:   Most recent backup verification result (PS/VM only); None for PC/FS.
        device_uuid:     Device UUID (PC/PS/VM); None for FS.
        ip_address:      IP address reported by the agent (PC/PS); None for VM and FS.
        inventory_name:  Hypervisor inventory name where the VM resides (VM only); None otherwise.
        inventory_type:  Hypervisor type, e.g. ESXi / HyperV (VM only); None otherwise.
        fs_config:       File server connection and scope config (FS only); None for PC/PS/VM.
    """
    workload_type: MachineWorkloadType
    agent_version: str | None
    verify_status: VerifyStatus | None = field(default=None, kw_only=True)
    device_uuid: str | None = field(default=None, kw_only=True)
    ip_address: str | None = field(default=None, kw_only=True)
    inventory_name: str | None = field(default=None, kw_only=True)
    inventory_type: str | None = field(default=None, kw_only=True)
    fs_config: FileServerConfig | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class M365UserInfo:
    """User information for a Mailbox / OneDrive / TeamsChat Workload."""
    user_principal_name: str

    @property
    def label(self) -> str:
        """Standard identifier for the M365 user (User Principal Name)."""
        return self.user_principal_name


@dataclass(frozen=True)
class M365SiteInfo:
    """Site information for a SharePoint Site Workload."""
    site_url: str
    site_name: str

    @property
    def label(self) -> str:
        """Standard identifier for the SharePoint site (Site URL)."""
        return self.site_url


@dataclass(frozen=True)
class M365TeamInfo:
    """Team information for a Teams Workload."""
    team_id: str
    team_name: str
    web_url: str

    @property
    def label(self) -> str:
        """Standard identifier for the Teams workload (Team Web URL)."""
        return self.web_url


@dataclass(frozen=True)
class M365GroupInfo:
    """Group information for a Group Exchange (M365 Group / shared mailbox) Workload."""
    group_id: str
    display_name: str
    mail: str

    @property
    def label(self) -> str:
        """Standard identifier for the M365 Group (group mail address)."""
        return self.mail


M365Info = M365UserInfo | M365SiteInfo | M365TeamInfo | M365GroupInfo


@dataclass(frozen=True)
class M365Workload(Workload):
    """Microsoft 365 SaaS backup Workload (category=M365).

    Each M365Workload represents one service for one account.
    The category field is always WorkloadCategory.M365.

    Attributes:
        workload_type: M365 service sub-type (EXCHANGE / ONEDRIVE, etc.).
        tenant_id:     Azure AD tenant ID.
        info:          Resource info; type varies by workload_type.
    """
    workload_type: M365WorkloadType
    tenant_id: str
    info: M365Info


@dataclass(frozen=True)
class FileServerPathSelector:
    """One path scope entry for a File Server backup.

    Attributes:
        path:           Top-level path to back up; empty string means the whole file server root.
        excluded_paths: Sub-paths to exclude from backup within this path.
    """
    path: str
    excluded_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileServerConfig:
    """File server connection settings and backup scope.

    Passwords are not included in this model.

    Attributes:
        host_ip:                    File server IP address.
        host_port:                  SMB/NAS connection port.
        server_type:                File server protocol / OS type.
        login_user:                 Login account (password not included).
        enable_vss:                 Whether Windows VSS is enabled (SMB only).
        connection_timeout_seconds: Connection timeout in seconds.
        selectors:                  Backup scope selectors; always has at least one entry.
    """
    host_ip: str
    host_port: int
    server_type: FileServerType
    login_user: str
    enable_vss: bool
    connection_timeout_seconds: int
    selectors: tuple[FileServerPathSelector, ...]


@dataclass(frozen=True)
class FileServerAddRequest:
    """Spec for registering one File Server workload in APM.

    Attributes:
        namespace:                  Backup server namespace of the backup server to register this file server with.
        host_ip:                    File server IP address.
        server_type:                File server protocol / OS type (FileServerType enum).
        plan_id:                    UUID of the Protection Plan to apply to this file server.
        login_user:                 Login account for the file server.
        login_password:             Login password for the file server (must be non-empty).
        host_port:                  Port for the SMB/NAS connection (default 445).
        enable_vss:                 Enable Windows VSS snapshot (SMB only; ignored for other types).
        connection_timeout_seconds: Connection timeout in seconds (default 180).
        trigger_backup:             Trigger an immediate backup after successful registration.
        selectors:                  Backup scope selectors; defaults to whole file server root.

    Raises:
        ValueError: login_password must be non-empty.
    """
    namespace: str
    host_ip: str
    server_type: FileServerType
    plan_id: str
    login_user: str
    login_password: str
    host_port: int = 445
    enable_vss: bool = False
    connection_timeout_seconds: int = 180
    trigger_backup: bool = False
    selectors: tuple[FileServerPathSelector, ...] = (FileServerPathSelector(path=""),)

    def __post_init__(self) -> None:
        if not self.login_password or not self.login_password.strip():
            raise ValueError("login_password must not be empty")


@dataclass(frozen=True)
class FileServerUpdateRequest:
    """Spec for updating an existing File Server workload in APM.

    Server type cannot be changed after creation and is not included here.

    Attributes:
        host_ip:                    Updated file server IP address.
        login_user:                 Updated login account.
        login_password:             Updated password; ``None`` keeps the existing stored password.
                                    Empty or whitespace-only string is rejected.
        host_port:                  Updated connection port (default 445).
        enable_vss:                 Updated VSS setting (SMB only).
        connection_timeout_seconds: Updated connection timeout in seconds (default 180).
        selectors:                  Updated backup scope selectors.

    Raises:
        ValueError: login_password is an empty or whitespace-only string (pass None to keep the existing password).
    """
    host_ip: str
    login_user: str
    login_password: str | None
    host_port: int = 445
    enable_vss: bool = False
    connection_timeout_seconds: int = 180
    selectors: tuple[FileServerPathSelector, ...] = (FileServerPathSelector(path=""),)

    def __post_init__(self) -> None:
        if self.login_password is not None and not self.login_password.strip():
            raise ValueError("login_password must not be empty (pass None to keep the existing password)")
