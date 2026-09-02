"""Unit tests for GWSAutoBackupRuleCollection: list/create/update/delete/update_collab_settings/
update_protected_account_types."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from synology_apm.sdk.collections._shared import _is_terminating
from synology_apm.sdk.collections.gws_auto_backup_rule import (
    GWSAutoBackupRuleCollection,
    _parse_rule,
    _parse_shared_drive_setting,
)
from synology_apm.sdk.exceptions import ResourceNotFoundError
from synology_apm.sdk.models.gws_auto_backup_rule import (
    GWSAutoBackupRule,
    GWSAutoBackupRuleListResult,
    GWSSharedDriveSetting,
)
from tests.unit.sdk.conftest import BASE_URL, assert_resource_error, make_session, null_out

DOMAIN_ID = "gwsdemo.example.com"
NAMESPACE = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"
PLAN_ID = "4e7d87ed-fadc-433a-95cf-1cdaca3574b3"
RULE_UID = "1ca9860a-ab7b-4a3e-bcca-e45315d31907"
GROUP_ID_A = "29117b62-828a-4774-8b18-41f7c2c5b34e"
GROUP_ID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

LIST_URL = f"{BASE_URL}/api/v1/application/gw/domain/auto_backup_rule/{DOMAIN_ID}"
CREATE_URL = f"{BASE_URL}/api/v1/application/gw/domain/auto_backup_rule"
UPDATE_URL = f"{BASE_URL}/api/v1/application/gw/domain/auto_backup_rule/{RULE_UID}"
COLLAB_URL = f"{BASE_URL}/api/v1/application/gw/domain/auto_backup_rule/collab_service"
DOMAIN_URL = f"{BASE_URL}/api/v1/application/gw/domain/{DOMAIN_ID}"

EMPTY_SHARED_DRIVE_SETTING = {"planId": "", "namespace": "", "backupUserId": ""}
ACTIVE_SHARED_DRIVE_SETTING = {"planId": PLAN_ID, "namespace": NAMESPACE, "backupUserId": "user-id-001"}

SAMPLE_RULE_RAW = {
    "uid": RULE_UID,
    "namespace": NAMESPACE,
    "autoBackupRule": {
        "uid": RULE_UID,
        "namespace": NAMESPACE,
        "metadata": {"creationVersion": "1", "resourceVersion": "1"},
        "spec": {"domain": DOMAIN_ID, "backupPlanId": PLAN_ID},
        "status": {},
    },
    "mailGroupIds": [GROUP_ID_A],
    "calendarGroupIds": [],
    "contactGroupIds": [],
    "driveGroupIds": [],
}

TERMINATING_RULE_RAW = {
    "uid": RULE_UID,
    "namespace": NAMESPACE,
    "autoBackupRule": {
        "uid": RULE_UID,
        "namespace": NAMESPACE,
        "metadata": {
            "creationVersion": "1",
            "resourceVersion": "1",
            "deletionTimestamp": "1782797122",
        },
        "spec": {"domain": DOMAIN_ID, "backupPlanId": PLAN_ID},
        "status": {},
    },
    "mailGroupIds": [GROUP_ID_A],
    "calendarGroupIds": [],
    "contactGroupIds": [],
    "driveGroupIds": [],
}

SAMPLE_LIST_RESPONSE = {
    "rulesWithMetas": [SAMPLE_RULE_RAW],
    "teamDriveSetting": ACTIVE_SHARED_DRIVE_SETTING,
}

EMPTY_LIST_RESPONSE: dict[str, Any] = {
    "rulesWithMetas": [],
}

SAMPLE_DOMAIN_GET_RESPONSE = {
    "isFound": True,
    "data": {
        "gwDomain": {
            "domain": DOMAIN_ID,
            "domainName": "GWS Demo",
            "globalConfig": {
                "selfServiceMyDrive": True,
                "autoAddUnlicensed": True,
                "autoAddArchived": False,
            },
        },
    },
}


@pytest.mark.asyncio
async def test_list_parses_rules_and_shared_drive_setting() -> None:
    """list() returns GWSAutoBackupRuleListResult with parsed rules, shared drive setting,
    and the domain's protected-account-type setting (fetched via a second GET)."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [SAMPLE_LIST_RESPONSE, SAMPLE_DOMAIN_GET_RESPONSE]
        result = await col.list(DOMAIN_ID)

    assert mock_get.call_args_list[0].args == (
        f"/api/v1/application/gw/domain/auto_backup_rule/{DOMAIN_ID}",
    )
    assert isinstance(result, GWSAutoBackupRuleListResult)
    assert len(result.rules) == 1

    rule = result.rules[0]
    assert rule.uid == RULE_UID
    assert rule.namespace == NAMESPACE
    assert rule.domain == DOMAIN_ID
    assert rule.plan_id == PLAN_ID
    assert rule.mail_group_ids == (GROUP_ID_A,)
    assert rule.calendar_group_ids == ()
    assert rule.contact_group_ids == ()
    assert rule.drive_group_ids == ()

    assert result.shared_drive_setting is not None
    assert result.shared_drive_setting.plan_id == PLAN_ID
    assert result.shared_drive_setting.namespace == NAMESPACE
    assert result.shared_drive_setting.backup_user_id == "user-id-001"
    assert result.shared_drive_setting.enabled is True
    assert result.include_unlicensed_accounts is True
    assert result.include_archived_accounts is False


@pytest.mark.asyncio
async def test_list_empty_response() -> None:
    """list() handles an empty rules list, no shared drive setting, and disabled account types."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)
    domain_response_no_config = {"isFound": True, "data": {"gwDomain": {"domain": DOMAIN_ID}}}

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [EMPTY_LIST_RESPONSE, domain_response_no_config]
        result = await col.list(DOMAIN_ID)

    assert result.rules == ()
    assert result.shared_drive_setting is None
    assert result.include_unlicensed_accounts is False
    assert result.include_archived_accounts is False


@pytest.mark.asyncio
async def test_list_excludes_terminating_rules() -> None:
    """list() excludes rules whose deletionTimestamp is non-zero (soft-delete pending)."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [
            {**SAMPLE_LIST_RESPONSE, "rulesWithMetas": [TERMINATING_RULE_RAW]},
            SAMPLE_DOMAIN_GET_RESPONSE,
        ]
        result = await col.list(DOMAIN_ID)
    assert result.rules == ()


@pytest.mark.asyncio
async def test_list_raises_not_found_when_domain_not_found() -> None:
    """list() raises ResourceNotFoundError when the domain's globalConfig lookup reports
    isFound=False, instead of silently treating both account-type settings as disabled."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [SAMPLE_LIST_RESPONSE, {"isFound": False, "data": {}}]
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.list(DOMAIN_ID)

    assert_resource_error(exc_info, resource_type="GWSDomainInfo", resource_id=DOMAIN_ID)


# ── Null vs. absent JSON field handling ───────────────────────────────────


@pytest.mark.parametrize("null_paths", [
    ("autoBackupRule",),
    ("autoBackupRule.metadata", "autoBackupRule.spec"),
], ids=["null_auto_backup_rule", "null_metadata_and_spec"])
def test_parse_rule_survives_null_fields(null_paths: tuple[str, ...]) -> None:
    raw = null_out(SAMPLE_RULE_RAW, *null_paths)

    rule = _parse_rule(raw)

    assert rule.uid == RULE_UID
    assert rule.namespace == NAMESPACE
    assert rule.domain == ""
    assert rule.plan_id == ""
    assert _is_terminating(raw) is False


@pytest.mark.parametrize("null_paths", [
    ("autoBackupRule",),
    ("autoBackupRule.metadata",),
    ("autoBackupRule.metadata.deletionTimestamp",),
], ids=["null_auto_backup_rule", "null_metadata", "null_deletion_timestamp"])
def test_is_terminating_treats_null_paths_as_not_terminating(null_paths: tuple[str, ...]) -> None:
    raw = null_out(TERMINATING_RULE_RAW, *null_paths)

    assert _is_terminating(raw) is False


def test_parse_shared_drive_setting_survives_null_fields() -> None:
    raw = null_out(ACTIVE_SHARED_DRIVE_SETTING, "planId", "namespace", "backupUserId")

    setting = _parse_shared_drive_setting(raw)

    assert setting.plan_id == ""
    assert setting.namespace == ""
    assert setting.backup_user_id == ""
    assert setting.enabled is False


# ── create() / update() / delete() ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_sends_correct_post_body() -> None:
    """create() POSTs with a nested ruleSpec and per-subtype group ID arrays."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await col.create(
            domain=DOMAIN_ID,
            namespace=NAMESPACE,
            plan_id=PLAN_ID,
            mail_group_ids=[GROUP_ID_A],
            calendar_group_ids=[],
            contact_group_ids=[],
            drive_group_ids=[],
        )

    mock_post.assert_called_once_with(
        "/api/v1/application/gw/domain/auto_backup_rule",
        json={
            "namespace": NAMESPACE,
            "ruleSpec": {"domain": DOMAIN_ID, "backupPlanId": PLAN_ID},
            "mailGroupIds": [GROUP_ID_A],
            "calendarGroupIds": [],
            "contactGroupIds": [],
            "driveGroupIds": [],
        },
    )


@pytest.mark.asyncio
async def test_create_defaults_empty_group_lists() -> None:
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await col.create(domain=DOMAIN_ID, namespace=NAMESPACE, plan_id=PLAN_ID)

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["mailGroupIds"] == []
    assert body["calendarGroupIds"] == []
    assert body["contactGroupIds"] == []
    assert body["driveGroupIds"] == []


@pytest.mark.asyncio
async def test_update_sends_correct_put_body() -> None:
    """update() PUTs to the rule's UID with backupPlanId at top level (no ruleSpec wrapper)."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)
    rule = GWSAutoBackupRule(
        uid=RULE_UID,
        namespace=NAMESPACE,
        domain=DOMAIN_ID,
        plan_id=PLAN_ID,
        mail_group_ids=(GROUP_ID_A,),
        calendar_group_ids=(),
        contact_group_ids=(),
        drive_group_ids=(),
    )

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update(rule, mail_group_ids=[GROUP_ID_A, GROUP_ID_B])

    mock_put.assert_called_once_with(
        f"/api/v1/application/gw/domain/auto_backup_rule/{RULE_UID}",
        json={
            "namespace": NAMESPACE,
            "backupPlanId": PLAN_ID,
            "mailGroupIds": [GROUP_ID_A, GROUP_ID_B],
            "calendarGroupIds": [],
            "contactGroupIds": [],
            "driveGroupIds": [],
        },
    )


@pytest.mark.asyncio
async def test_update_keeps_unchanged_fields() -> None:
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)
    rule = GWSAutoBackupRule(
        uid=RULE_UID,
        namespace=NAMESPACE,
        domain=DOMAIN_ID,
        plan_id=PLAN_ID,
        mail_group_ids=(GROUP_ID_A,),
        calendar_group_ids=(GROUP_ID_B,),
        contact_group_ids=(),
        drive_group_ids=(),
    )

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update(rule)

    _, kwargs = mock_put.call_args
    body = kwargs["json"]
    assert body["backupPlanId"] == PLAN_ID
    assert body["mailGroupIds"] == [GROUP_ID_A]
    assert body["calendarGroupIds"] == [GROUP_ID_B]
    assert body["contactGroupIds"] == []
    assert body["driveGroupIds"] == []


@pytest.mark.asyncio
async def test_delete_sends_delete_with_namespace_param() -> None:
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)
    rule = GWSAutoBackupRule(
        uid=RULE_UID,
        namespace=NAMESPACE,
        domain=DOMAIN_ID,
        plan_id=PLAN_ID,
        mail_group_ids=(GROUP_ID_A,),
        calendar_group_ids=(),
        contact_group_ids=(),
        drive_group_ids=(),
    )

    with patch.object(session, "delete", new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = {}
        await col.delete(rule)

    mock_delete.assert_called_once_with(
        f"/api/v1/application/gw/domain/auto_backup_rule/{RULE_UID}",
        params={"namespace": NAMESPACE},
    )


# ── update_collab_settings() ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_collab_settings_sends_shared_drive_setting() -> None:
    """update_collab_settings() PUTs the single Shared Drive setting (not M365's 4-way)."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    shared_drive = GWSSharedDriveSetting(plan_id=PLAN_ID, namespace=NAMESPACE, backup_user_id="user-id-001")

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update_collab_settings(domain=DOMAIN_ID, shared_drive=shared_drive)

    mock_put.assert_called_once_with(
        "/api/v1/application/gw/domain/auto_backup_rule/collab_service",
        json={
            "domain": DOMAIN_ID,
            "teamDriveSetting": {"planId": PLAN_ID, "namespace": NAMESPACE, "backupUserId": "user-id-001"},
        },
    )


@pytest.mark.asyncio
async def test_update_collab_settings_none_sends_empty_setting() -> None:
    """update_collab_settings(shared_drive=None) disables the Shared Drive setting."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update_collab_settings(domain=DOMAIN_ID)

    _, kwargs = mock_put.call_args
    body = kwargs["json"]
    assert body["teamDriveSetting"] == {"planId": "", "namespace": "", "backupUserId": ""}


def test_shared_drive_setting_enabled_property() -> None:
    assert GWSSharedDriveSetting(plan_id=PLAN_ID, namespace=NAMESPACE, backup_user_id="").enabled is True
    assert GWSSharedDriveSetting(plan_id="", namespace="", backup_user_id="").enabled is False


# ── update_protected_account_types() ───────────────────────────────────────


@pytest.mark.asyncio
async def test_update_protected_account_types_reads_then_merges_into_full_config() -> None:
    """update_protected_account_types() reads the domain's current globalConfig first and only
    overrides the two account-type fields, to avoid resetting unrelated domain settings."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_get.return_value = SAMPLE_DOMAIN_GET_RESPONSE
        mock_put.return_value = {}
        await col.update_protected_account_types(
            DOMAIN_ID, include_unlicensed_accounts=False, include_archived_accounts=True,
        )

    mock_get.assert_called_once_with(f"/api/v1/application/gw/domain/{DOMAIN_ID}")
    mock_put.assert_called_once_with(
        f"/api/v1/application/gw/domain/{DOMAIN_ID}",
        json={
            "domainName": "GWS Demo",
            "globalConfig": {
                "selfServiceMyDrive": True,
                "autoAddUnlicensed": False,
                "autoAddArchived": True,
            },
        },
    )


@pytest.mark.asyncio
async def test_update_protected_account_types_preserves_unrelated_config_fields() -> None:
    """Unrelated globalConfig fields (e.g. selfServiceMyDrive) must survive unchanged."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_get.return_value = SAMPLE_DOMAIN_GET_RESPONSE
        mock_put.return_value = {}
        await col.update_protected_account_types(
            DOMAIN_ID, include_unlicensed_accounts=True, include_archived_accounts=True,
        )

    _, kwargs = mock_put.call_args
    assert kwargs["json"]["globalConfig"]["selfServiceMyDrive"] is True


@pytest.mark.asyncio
async def test_update_protected_account_types_raises_not_found_when_domain_not_found() -> None:
    """update_protected_account_types() raises ResourceNotFoundError when the domain lookup
    reports isFound=False, instead of silently PUTting to a nonexistent domain."""
    session = make_session()
    col = GWSAutoBackupRuleCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get, \
         patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_get.return_value = {"isFound": False, "data": {}}
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await col.update_protected_account_types(
                DOMAIN_ID, include_unlicensed_accounts=True, include_archived_accounts=False,
            )

    assert_resource_error(exc_info, resource_type="GWSDomainInfo", resource_id=DOMAIN_ID)
    mock_put.assert_not_called()
