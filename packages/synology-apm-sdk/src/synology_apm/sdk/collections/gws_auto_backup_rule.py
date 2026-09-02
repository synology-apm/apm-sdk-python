"""GWSAutoBackupRuleCollection — manage GWS auto-backup rules."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from .._http import WebAPISession
from ..exceptions import ResourceNotFoundError
from ..models.gws_auto_backup_rule import (
    GWSAutoBackupRule,
    GWSAutoBackupRuleListResult,
    GWSSharedDriveSetting,
)
from ._shared import _is_terminating


def _parse_shared_drive_setting(raw: dict[str, Any]) -> GWSSharedDriveSetting:
    return GWSSharedDriveSetting(
        plan_id=raw.get("planId") or "",
        namespace=raw.get("namespace") or "",
        backup_user_id=raw.get("backupUserId") or "",
    )


def _parse_rule(raw: dict[str, Any]) -> GWSAutoBackupRule:
    rule_obj: dict[str, Any] = raw.get("autoBackupRule") or {}
    spec: dict[str, Any] = rule_obj.get("spec") or {}
    return GWSAutoBackupRule(
        uid=raw.get("uid") or "",
        namespace=raw.get("namespace") or "",
        domain=spec.get("domain") or "",
        plan_id=spec.get("backupPlanId") or "",
        mail_group_ids=tuple(raw.get("mailGroupIds") or []),
        calendar_group_ids=tuple(raw.get("calendarGroupIds") or []),
        contact_group_ids=tuple(raw.get("contactGroupIds") or []),
        drive_group_ids=tuple(raw.get("driveGroupIds") or []),
    )


class GWSAutoBackupRuleCollection:
    """Collection interface for managing GWS auto-backup rules.

    Accessed via APMClient.gws.auto_backup_rules; should not be instantiated directly.

    Auto-backup rules have two independently-managed sections:

    - **User Services** (CRUD on per-plan rules): Mail / Calendar / Contact / Drive workloads
      are added automatically when their Google Group membership changes, bound per service type.
    - **Collaboration Services** (single settings object per domain): Shared Drives — all
      shared drives are included automatically when enabled.

    Plus a domain-wide setting controlling whether unlicensed/archived Google Workspace
    accounts are included in auto-backup (see update_protected_account_types()).
    """

    def __init__(self, session: WebAPISession) -> None:
        self._session = session

    async def list(self, domain: str) -> GWSAutoBackupRuleListResult:
        """Return all auto-backup rules and collaboration service settings for a domain.

        Args:
            domain: Google Workspace domain.

        Returns:
            GWSAutoBackupRuleListResult containing user-service rules, the Shared Drive
            collaboration service setting, and the domain's protected-account-type setting.
            Rules pending deletion are excluded.

        Raises:
            ResourceNotFoundError: The specified domain was not found.
        """
        raw, global_config = await asyncio.gather(
            self._session.get(f"/api/v1/application/gw/domain/auto_backup_rule/{domain}"),
            self._get_global_config(domain),
        )
        rules = tuple(
            _parse_rule(r) for r in (raw.get("rulesWithMetas") or [])
            if not _is_terminating(r)
        )
        team_drive_setting = raw.get("teamDriveSetting")
        shared_drive_setting = _parse_shared_drive_setting(team_drive_setting) if team_drive_setting else None

        return GWSAutoBackupRuleListResult(
            rules=rules,
            shared_drive_setting=shared_drive_setting,
            include_unlicensed_accounts=bool(global_config.get("autoAddUnlicensed") or False),
            include_archived_accounts=bool(global_config.get("autoAddArchived") or False),
        )

    async def create(
        self,
        domain: str,
        namespace: str,
        plan_id: str,
        mail_group_ids: Sequence[str] | None = None,
        calendar_group_ids: Sequence[str] | None = None,
        contact_group_ids: Sequence[str] | None = None,
        drive_group_ids: Sequence[str] | None = None,
    ) -> None:
        """Create a new User Services auto-backup rule for a GWS domain.

        A single rule associates one protection plan and one backup server with a set of
        Google Groups for Mail, Calendar, Contact, and/or Drive service types.

        Args:
            domain:             Google Workspace domain.
            namespace:          Backup server namespace (= BackupServer.namespace).
            plan_id:            Protection plan ID to apply (= ProtectionPlan.plan_id).
            mail_group_ids:     Google Group IDs whose Mail members are auto-protected.
            calendar_group_ids: Google Group IDs whose Calendar members are auto-protected.
            contact_group_ids:  Google Group IDs whose Contact members are auto-protected.
            drive_group_ids:    Google Group IDs whose Drive members are auto-protected.
        """
        await self._session.post(
            "/api/v1/application/gw/domain/auto_backup_rule",
            json={
                "namespace": namespace,
                "ruleSpec": {"domain": domain, "backupPlanId": plan_id},
                "mailGroupIds": mail_group_ids or [],
                "calendarGroupIds": calendar_group_ids or [],
                "contactGroupIds": contact_group_ids or [],
                "driveGroupIds": drive_group_ids or [],
            },
        )

    async def update(
        self,
        rule: GWSAutoBackupRule,
        plan_id: str | None = None,
        mail_group_ids: Sequence[str] | None = None,
        calendar_group_ids: Sequence[str] | None = None,
        contact_group_ids: Sequence[str] | None = None,
        drive_group_ids: Sequence[str] | None = None,
    ) -> None:
        """Update an existing User Services auto-backup rule.

        Only the supplied fields are changed; omit a parameter to keep its current value.

        Args:
            rule:               Existing rule to update (obtained via list()).
            plan_id:            Replacement protection plan ID, or None to keep current.
            mail_group_ids:     Replacement Mail group IDs, or None to keep current.
            calendar_group_ids: Replacement Calendar group IDs, or None to keep current.
            contact_group_ids:  Replacement Contact group IDs, or None to keep current.
            drive_group_ids:    Replacement Drive group IDs, or None to keep current.
        """
        await self._session.put(
            f"/api/v1/application/gw/domain/auto_backup_rule/{rule.uid}",
            json={
                "namespace": rule.namespace,
                "backupPlanId": plan_id if plan_id is not None else rule.plan_id,
                "mailGroupIds": (
                    mail_group_ids if mail_group_ids is not None else list(rule.mail_group_ids)
                ),
                "calendarGroupIds": (
                    calendar_group_ids if calendar_group_ids is not None else list(rule.calendar_group_ids)
                ),
                "contactGroupIds": (
                    contact_group_ids if contact_group_ids is not None else list(rule.contact_group_ids)
                ),
                "driveGroupIds": (
                    drive_group_ids if drive_group_ids is not None else list(rule.drive_group_ids)
                ),
            },
        )

    async def delete(self, rule: GWSAutoBackupRule) -> None:
        """Delete a User Services auto-backup rule.

        Args:
            rule: Rule to delete (obtained via list()).
        """
        await self._session.delete(
            f"/api/v1/application/gw/domain/auto_backup_rule/{rule.uid}",
            params={"namespace": rule.namespace},
        )

    async def update_collab_settings(
        self,
        domain: str,
        shared_drive: GWSSharedDriveSetting | None = None,
    ) -> None:
        """Replace the Collaboration Services auto-backup setting for a GWS domain.

        GWS has a single Collaboration Service: Shared Drives. Pass the current
        GWSSharedDriveSetting from list() to preserve it unmodified, or None to disable it.

        Args:
            domain:       Google Workspace domain.
            shared_drive: Setting for Shared Drives; None = disabled.
        """
        if shared_drive is None or not shared_drive.enabled:
            team_drive_setting: dict[str, str] = {"planId": "", "namespace": "", "backupUserId": ""}
        else:
            team_drive_setting = {
                "planId": shared_drive.plan_id,
                "namespace": shared_drive.namespace,
                "backupUserId": shared_drive.backup_user_id,
            }

        await self._session.put(
            "/api/v1/application/gw/domain/auto_backup_rule/collab_service",
            json={"domain": domain, "teamDriveSetting": team_drive_setting},
        )

    async def update_protected_account_types(
        self,
        domain: str,
        *,
        include_unlicensed_accounts: bool,
        include_archived_accounts: bool,
    ) -> None:
        """Update which additional Google Workspace account types are auto-protected for a domain.

        Google Workspace accounts with licenses are always auto-protected; this setting
        controls whether unlicensed and/or archived accounts are also included. This is a
        domain-wide setting (not per-rule) — only these two fields change; other domain
        configuration is preserved.

        Args:
            domain:                      Google Workspace domain.
            include_unlicensed_accounts: Whether unlicensed accounts are auto-protected.
            include_archived_accounts:   Whether archived accounts are auto-protected.

        Raises:
            ResourceNotFoundError: The specified domain was not found.
        """
        domain_spec = await self._get_domain_spec(domain)
        global_config: dict[str, Any] = dict(domain_spec.get("globalConfig") or {})
        global_config["autoAddUnlicensed"] = include_unlicensed_accounts
        global_config["autoAddArchived"] = include_archived_accounts

        await self._session.put(
            f"/api/v1/application/gw/domain/{domain}",
            json={
                "domainName": domain_spec.get("domainName") or domain,
                "globalConfig": global_config,
            },
        )

    async def _get_domain_spec(self, domain: str) -> dict[str, Any]:
        raw = await self._session.get(f"/api/v1/application/gw/domain/{domain}")
        if not raw.get("isFound"):
            raise ResourceNotFoundError(
                f"GWS domain '{domain}' not found.",
                resource_type="GWSDomainInfo",
                resource_id=domain,
            )
        return (raw.get("data") or {}).get("gwDomain") or {}

    async def _get_global_config(self, domain: str) -> dict[str, Any]:
        domain_spec = await self._get_domain_spec(domain)
        return domain_spec.get("globalConfig") or {}
