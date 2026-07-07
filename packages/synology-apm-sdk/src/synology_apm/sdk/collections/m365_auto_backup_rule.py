"""M365AutoBackupRuleCollection — manage M365 auto-backup rules."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .._http import WebAPISession
from ..models.m365_auto_backup_rule import (
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
)


def _parse_collab_setting(raw: dict[str, Any]) -> M365CollabServiceSetting:
    return M365CollabServiceSetting(
        plan_id=raw.get("planId", ""),
        namespace=raw.get("namespace", ""),
    )


def _is_terminating(raw: dict[str, Any]) -> bool:
    ts = raw.get("autoBackupRule", {}).get("metadata", {}).get("deletionTimestamp", "0")
    return ts not in ("", "0")


def _parse_rule(raw: dict[str, Any]) -> M365AutoBackupRule:
    rule_obj: dict[str, Any] = raw.get("autoBackupRule", {})
    spec: dict[str, Any] = rule_obj.get("spec", {})
    return M365AutoBackupRule(
        uid=raw.get("uid", ""),
        namespace=raw.get("namespace", ""),
        tenant_id=spec.get("tenantId", ""),
        plan_id=spec.get("backupPlanId", ""),
        exchange_group_ids=tuple(raw.get("exchangeGroupIds") or []),
        onedrive_group_ids=tuple(raw.get("onedriveGroupIds") or []),
        chat_group_ids=tuple(raw.get("chatGroupIds") or []),
    )


class M365AutoBackupRuleCollection:
    """Collection interface for managing M365 auto-backup rules.

    Accessed via APMClient.m365.auto_backup_rules; should not be instantiated directly.

    Auto-backup rules have two independently-managed sections:

    - **User Services** (CRUD on per-plan rules): Exchange / OneDrive / Chat workloads
      are added automatically when their Azure AD group membership changes.
    - **Collaboration Services** (single settings object per tenant): Microsoft 365 Groups,
      SharePoint Sites, SharePoint Personal Sites, and Teams — all items of the enabled
      types are included automatically.
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(self, tenant_id: str) -> M365AutoBackupRuleListResult:
        """Return all auto-backup rules and collaboration service settings for a tenant.

        Args:
            tenant_id: Azure AD tenant ID.

        Returns:
            M365AutoBackupRuleListResult containing user-service rules and per-type
            collaboration service settings. Rules pending deletion are excluded.
        """
        raw = await self._session.get(
            f"/api/v1/application/m365/tenant/auto_backup_rule/{tenant_id}",
        )
        rules = tuple(
            _parse_rule(r) for r in (raw.get("rulesWithMetas") or [])
            if not _is_terminating(r)
        )
        return M365AutoBackupRuleListResult(
            rules=rules,
            group_exchange=_parse_collab_setting(raw.get("groupExchangeSetting") or {}),
            mysite=_parse_collab_setting(raw.get("mySiteSetting") or {}),
            sharepoint=_parse_collab_setting(raw.get("generalSiteSetting") or {}),
            teams=_parse_collab_setting(raw.get("teamsSetting") or {}),
        )

    async def create(
        self,
        tenant_id: str,
        namespace: str,
        plan_id: str,
        exchange_group_ids: Sequence[str] | None = None,
        onedrive_group_ids: Sequence[str] | None = None,
        chat_group_ids: Sequence[str] | None = None,
    ) -> None:
        """Create a new User Services auto-backup rule for an M365 tenant.

        A single rule associates one protection plan and one backup server with a set of
        Azure AD groups for Exchange, OneDrive, and/or Chat service types.

        Args:
            tenant_id:          Azure AD tenant ID.
            namespace:          Backup server namespace (= BackupServer.namespace).
            plan_id:            Protection plan ID to apply (= ProtectionPlan.plan_id).
            exchange_group_ids: Azure AD group IDs whose Exchange members are auto-protected.
            onedrive_group_ids: Azure AD group IDs whose OneDrive members are auto-protected.
            chat_group_ids:     Azure AD group IDs whose Chat members are auto-protected.
        """
        await self._session.post(
            "/api/v1/application/m365/tenant/auto_backup_rule",
            json={
                "namespace": namespace,
                "ruleSpec": {"tenantId": tenant_id, "backupPlanId": plan_id},
                "exchangeGroupIds": exchange_group_ids or [],
                "onedriveGroupIds": onedrive_group_ids or [],
                "chatGroupIds": chat_group_ids or [],
            },
        )

    async def update(
        self,
        rule: M365AutoBackupRule,
        plan_id: str | None = None,
        exchange_group_ids: Sequence[str] | None = None,
        onedrive_group_ids: Sequence[str] | None = None,
        chat_group_ids: Sequence[str] | None = None,
    ) -> None:
        """Update an existing User Services auto-backup rule.

        Only the supplied fields are changed; omit a parameter to keep its current value.

        Args:
            rule:               Existing rule to update (obtained via list()).
            plan_id:            Replacement protection plan ID, or None to keep current.
            exchange_group_ids: Replacement Exchange group IDs, or None to keep current.
            onedrive_group_ids: Replacement OneDrive group IDs, or None to keep current.
            chat_group_ids:     Replacement Chat group IDs, or None to keep current.
        """
        await self._session.put(
            f"/api/v1/application/m365/tenant/auto_backup_rule/{rule.uid}",
            json={
                "namespace": rule.namespace,
                "backupPlanId": plan_id if plan_id is not None else rule.plan_id,
                "exchangeGroupIds": (
                    exchange_group_ids if exchange_group_ids is not None else list(rule.exchange_group_ids)
                ),
                "onedriveGroupIds": (
                    onedrive_group_ids if onedrive_group_ids is not None else list(rule.onedrive_group_ids)
                ),
                "chatGroupIds": chat_group_ids if chat_group_ids is not None else list(rule.chat_group_ids),
            },
        )

    async def delete(self, rule: M365AutoBackupRule) -> None:
        """Delete a User Services auto-backup rule.

        Args:
            rule: Rule to delete (obtained via list()).
        """
        await self._session.delete(
            f"/api/v1/application/m365/tenant/auto_backup_rule/{rule.uid}",
            params={"namespace": rule.namespace},
        )

    async def update_collab_settings(
        self,
        tenant_id: str,
        group_exchange: M365CollabServiceSetting | None = None,
        mysite: M365CollabServiceSetting | None = None,
        sharepoint: M365CollabServiceSetting | None = None,
        teams: M365CollabServiceSetting | None = None,
    ) -> None:
        """Replace the Collaboration Services auto-backup settings for an M365 tenant.

        Replaces all four service-type settings atomically; any type not provided (or
        None) is set to disabled (empty plan). Pass the current M365CollabServiceSetting
        values from list() to preserve unmodified types.

        Args:
            tenant_id:     Azure AD tenant ID.
            group_exchange: Setting for Microsoft 365 Groups; None = disabled.
            mysite:        Setting for SharePoint Personal Sites; None = disabled.
            sharepoint:    Setting for SharePoint Sites; None = disabled.
            teams:         Setting for Teams; None = disabled.
        """
        _empty: dict[str, str] = {"planId": "", "namespace": ""}

        def _ser(s: M365CollabServiceSetting | None) -> dict[str, str]:
            if s is None or not s.enabled:
                return _empty
            return {"planId": s.plan_id, "namespace": s.namespace}

        await self._session.put(
            "/api/v1/application/m365/tenant/auto_backup_rule/collab_service",
            json={
                "tenantId": tenant_id,
                "groupExchangeSetting": _ser(group_exchange),
                "mySiteSetting": _ser(mysite),
                "generalSiteSetting": _ser(sharepoint),
                "teamsSetting": _ser(teams),
            },
        )
