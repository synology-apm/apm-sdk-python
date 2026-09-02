"""Models for GWS auto-backup rules (User Services and Collaboration Services)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._shared import auto_to_dict


@dataclass(frozen=True)
class GWSAutoBackupRule:
    """A User Services auto-backup rule for a GWS domain.

    User Services rules automatically add Mail, Calendar, Contact, and/or Drive members
    of specified Google Groups to backup protection under a given plan and server.
    Each service type is bound to its own group list.

    Attributes:
        uid:                Rule identifier.
        namespace:          Backup server namespace this rule targets.
        domain:             Google Workspace domain this rule belongs to.
        plan_id:            Protection plan ID applied to matched workloads.
        mail_group_ids:     Google Group IDs whose Mail members are auto-protected.
        calendar_group_ids: Google Group IDs whose Calendar members are auto-protected.
        contact_group_ids:  Google Group IDs whose Contact members are auto-protected.
        drive_group_ids:    Google Group IDs whose Drive members are auto-protected.
    """

    uid: str
    namespace: str
    domain: str
    plan_id: str
    mail_group_ids: tuple[str, ...]
    calendar_group_ids: tuple[str, ...]
    contact_group_ids: tuple[str, ...]
    drive_group_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class GWSSharedDriveSetting:
    """Auto-backup setting for the Shared Drive Collaboration Service (enabled when plan_id is non-empty).

    Shared Drives are domain-wide — all shared drives are automatically included when enabled.

    Attributes:
        plan_id:        Protection plan ID applied to matched workloads; empty string = disabled.
        namespace:      Backup server namespace; empty string when disabled.
        backup_user_id: Google user ID used to back up shared drives; empty string when auto-selected.
    """

    plan_id: str
    namespace: str
    backup_user_id: str

    @property
    def enabled(self) -> bool:
        """True when the Shared Drive collaboration service has an assigned backup plan."""
        return bool(self.plan_id)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class GWSAutoBackupRuleListResult:
    """Full auto-backup configuration for one GWS domain.

    Attributes:
        rules:                     User Services rules (one per plan/server combination).
        shared_drive_setting:      Auto-backup setting for Shared Drives; None if never configured.
        include_unlicensed_accounts: Whether unlicensed Google Workspace accounts are auto-protected.
        include_archived_accounts:  Whether archived Google Workspace accounts are auto-protected.
    """

    rules: tuple[GWSAutoBackupRule, ...]
    shared_drive_setting: GWSSharedDriveSetting | None
    include_unlicensed_accounts: bool
    include_archived_accounts: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(
            self,
            exclude=frozenset({"shared_drive_setting"}),
            extra={
                "shared_drive_setting": (
                    self.shared_drive_setting.to_dict() if self.shared_drive_setting is not None else None
                ),
            },
        )
