"""Literal type aliases mirroring SDK enums, shared across MCP tool modules.

Single source of truth so a given SDK enum's accepted string values are declared once,
not re-typed (and potentially drift) in every tool file that filters or accepts it.
"""
from __future__ import annotations

from typing import Literal

MachineWorkloadTypeLiteral = Literal["pc", "ps", "vm", "fs"]
M365WorkloadTypeLiteral = Literal["exchange", "onedrive", "chat", "sharepoint", "teams", "group"]
GWSWorkloadTypeLiteral = Literal["drive", "mail", "contact", "calendar", "shared_drive"]
BackupActivityStatusLiteral = Literal[
    "queuing", "backing_up", "canceling", "success", "failed", "partial", "canceled"
]
RestoreActivityStatusLiteral = Literal[
    "preparing", "restoring", "canceling", "sandbox_ready", "ready_for_migrate", "migrate_vm_manually",
    "migrating", "success", "failed", "partial", "canceled",
]
# Deliberately excludes "retired" (WorkloadStatus.RETIRED): retirement is filtered via
# the separate is_retired: bool parameter instead (see MachineWorkloadCollection.list()'s
# docstring) -- not a gap, see test_enums.py's WorkloadStatus-excludes-retired case.
WorkloadStatusLiteral = Literal[
    "queuing", "backing_up", "success", "failed", "partial", "canceled", "no_backups", "deleting"
]
VerifyStatusLiteral = Literal[
    "verifying", "success", "failed", "canceled", "not_supported", "not_enabled", "partial", "waiting"
]
# Statuses a listed version can have (versions still backing up are never listed).
VersionListStatusLiteral = Literal["success", "partial", "failed", "canceled"]
ServerStatusLiteral = Literal["healthy", "warning", "critical", "disconnected", "syncing"]
BackupServerTypeLiteral = Literal["dp", "nas"]
LogLevelLiteral = Literal["info", "warning", "error"]
FileServerTypeLiteral = Literal["smb", "nas", "nutanix", "netapp"]
WeekDayLiteral = Literal["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
SandboxVideoCardLiteral = Literal["cirrus", "vga", "vmvga"]
SandboxMachineTypeLiteral = Literal["pc", "q35"]
SandboxAdapterModelLiteral = Literal["rtl8139", "e1000", "virtio"]
SandboxDiskControllerLiteral = Literal["ide", "sata", "scsi"]
SandboxKeyLiteral = Literal[
    "enter", "esc", "space", "tab", "backspace", "up", "down", "left", "right",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "shift", "ctrl_alt_delete",
]
