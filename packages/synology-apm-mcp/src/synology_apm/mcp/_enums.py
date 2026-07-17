"""Literal type aliases mirroring SDK enums, shared across MCP tool modules.

Single source of truth so a given SDK enum's accepted string values are declared once,
not re-typed (and potentially drift) in every tool file that filters or accepts it.
"""
from __future__ import annotations

from typing import Literal

MachineWorkloadTypeLiteral = Literal["pc", "ps", "vm", "fs"]
M365WorkloadTypeLiteral = Literal["exchange", "onedrive", "chat", "sharepoint", "teams", "group"]
BackupActivityStatusLiteral = Literal[
    "queuing", "backing_up", "canceling", "success", "failed", "partial", "canceled"
]
RestoreActivityStatusLiteral = Literal[
    "preparing", "restoring", "canceling", "ready_for_migrate", "migrate_vm_manually",
    "migrating", "success", "failed", "partial", "canceled",
]
WorkloadStatusLiteral = Literal[
    "queuing", "backing_up", "success", "failed", "partial", "canceled", "no_backups", "deleting"
]
VerifyStatusLiteral = Literal[
    "verifying", "success", "failed", "canceled", "not_supported", "not_enabled", "partial", "waiting"
]
ServerStatusLiteral = Literal["healthy", "warning", "critical", "disconnected", "syncing"]
BackupServerTypeLiteral = Literal["dp", "nas"]
LogLevelLiteral = Literal["info", "warning", "error"]
FileServerTypeLiteral = Literal["smb", "nas", "nutanix", "netapp"]
WeekDayLiteral = Literal["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
