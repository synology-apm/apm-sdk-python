"""Models for M365 auto-backup rules (User Services and Collaboration Services)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class M365AutoBackupRule:
    """A User Services auto-backup rule for an M365 tenant.

    User Services rules automatically add Exchange, OneDrive, and/or Chat members
    of specified Azure AD groups to backup protection under a given plan and server.

    Attributes:
        uid:               Rule identifier.
        namespace:         Backup server namespace this rule targets.
        tenant_id:         Azure AD tenant ID this rule belongs to.
        plan_id:           Protection plan ID applied to matched workloads.
        exchange_group_ids: Azure AD group IDs whose Exchange members are auto-protected.
        onedrive_group_ids: Azure AD group IDs whose OneDrive members are auto-protected.
        chat_group_ids:    Azure AD group IDs whose Chat members are auto-protected.
    """

    uid: str
    namespace: str
    tenant_id: str
    plan_id: str
    exchange_group_ids: tuple[str, ...]
    onedrive_group_ids: tuple[str, ...]
    chat_group_ids: tuple[str, ...]


@dataclass(frozen=True)
class M365CollabServiceSetting:
    """Auto-backup setting for one Collaboration Service type (enabled when plan_id is non-empty).

    Collaboration Services (Microsoft 365 Groups, SharePoint Sites, SharePoint Personal Sites,
    Teams) are tenant-wide — all items of the selected type are automatically included.

    Attributes:
        plan_id:   Protection plan ID applied to matched workloads; empty string = disabled.
        namespace: Backup server namespace; empty string when disabled.
    """

    plan_id: str
    namespace: str

    @property
    def enabled(self) -> bool:
        """True when this collaboration service type has an assigned backup plan."""
        return bool(self.plan_id)


@dataclass(frozen=True)
class M365AutoBackupRuleListResult:
    """Full auto-backup configuration for one M365 tenant.

    Attributes:
        rules:           User Services rules (one per plan/server combination).
        group_exchange:  Auto-backup setting for Microsoft 365 Groups.
        mysite:          Auto-backup setting for SharePoint Personal Sites (MySite).
        sharepoint:      Auto-backup setting for SharePoint Sites.
        teams:           Auto-backup setting for Teams.
    """

    rules: tuple[M365AutoBackupRule, ...]
    group_exchange: M365CollabServiceSetting
    mysite: M365CollabServiceSetting
    sharepoint: M365CollabServiceSetting
    teams: M365CollabServiceSetting
