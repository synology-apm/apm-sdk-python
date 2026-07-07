"""Unit tests for M365AutoBackupRuleCollection: list/create/update/delete/update_collab_settings."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from synology_apm.sdk.collections.m365_auto_backup_rule import M365AutoBackupRuleCollection
from synology_apm.sdk.models.m365_auto_backup_rule import (
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
)
from tests.unit.sdk.conftest import BASE_URL, make_session

TENANT_ID = "87c467dd-ac00-45d8-babb-e2b0787e2d13"
NAMESPACE = "9053e422-4154-4abc-b03a-6e3d8e17b2d5"
PLAN_ID = "4e7d87ed-fadc-433a-95cf-1cdaca3574b3"
RULE_UID = "1ca9860a-ab7b-4a3e-bcca-e45315d31907"
GROUP_ID_A = "29117b62-828a-4774-8b18-41f7c2c5b34e"
GROUP_ID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

LIST_URL = f"{BASE_URL}/api/v1/application/m365/tenant/auto_backup_rule/{TENANT_ID}"
CREATE_URL = f"{BASE_URL}/api/v1/application/m365/tenant/auto_backup_rule"
UPDATE_URL = f"{BASE_URL}/api/v1/application/m365/tenant/auto_backup_rule/{RULE_UID}"
COLLAB_URL = f"{BASE_URL}/api/v1/application/m365/tenant/auto_backup_rule/collab_service"

EMPTY_SETTING = {"planId": "", "namespace": ""}
ACTIVE_SETTING = {"planId": PLAN_ID, "namespace": NAMESPACE}

SAMPLE_RULE_RAW = {
    "uid": RULE_UID,
    "namespace": NAMESPACE,
    "autoBackupRule": {
        "uid": RULE_UID,
        "namespace": NAMESPACE,
        "metadata": {"creationVersion": "1", "resourceVersion": "1"},
        "spec": {"tenantId": TENANT_ID, "backupPlanId": PLAN_ID},
        "status": {},
    },
    "exchangeGroupIds": [GROUP_ID_A],
    "onedriveGroupIds": [],
    "chatGroupIds": [],
}

SAMPLE_LIST_RESPONSE = {
    "rulesWithMetas": [SAMPLE_RULE_RAW],
    "groupExchangeSetting": ACTIVE_SETTING,
    "mySiteSetting": EMPTY_SETTING,
    "generalSiteSetting": EMPTY_SETTING,
    "teamsSetting": EMPTY_SETTING,
}

EMPTY_LIST_RESPONSE = {
    "rulesWithMetas": [],
    "groupExchangeSetting": EMPTY_SETTING,
    "mySiteSetting": EMPTY_SETTING,
    "generalSiteSetting": EMPTY_SETTING,
    "teamsSetting": EMPTY_SETTING,
}


@pytest.mark.asyncio
async def test_list_parses_rules_and_collab_settings() -> None:
    """list() returns M365AutoBackupRuleListResult with parsed rules and collab settings."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = SAMPLE_LIST_RESPONSE
        result = await col.list(TENANT_ID)

    mock_get.assert_called_once_with(
        f"/api/v1/application/m365/tenant/auto_backup_rule/{TENANT_ID}",
    )
    assert isinstance(result, M365AutoBackupRuleListResult)
    assert len(result.rules) == 1

    rule = result.rules[0]
    assert rule.uid == RULE_UID
    assert rule.namespace == NAMESPACE
    assert rule.tenant_id == TENANT_ID
    assert rule.plan_id == PLAN_ID
    assert rule.exchange_group_ids == (GROUP_ID_A,)
    assert rule.onedrive_group_ids == ()
    assert rule.chat_group_ids == ()

    assert result.group_exchange.plan_id == PLAN_ID
    assert result.group_exchange.namespace == NAMESPACE
    assert result.group_exchange.enabled is True
    assert result.mysite.enabled is False
    assert result.sharepoint.enabled is False
    assert result.teams.enabled is False


@pytest.mark.asyncio
async def test_list_empty_response() -> None:
    """list() handles an empty rules list and disabled collab settings."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)

    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = EMPTY_LIST_RESPONSE
        result = await col.list(TENANT_ID)

    assert result.rules == ()
    assert result.group_exchange.enabled is False
    assert result.mysite.enabled is False
    assert result.sharepoint.enabled is False
    assert result.teams.enabled is False


@pytest.mark.asyncio
async def test_list_excludes_terminating_rules() -> None:
    """list() excludes rules whose deletionTimestamp is non-zero (soft-delete pending)."""
    terminating_raw = {
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
            "spec": {"tenantId": TENANT_ID, "backupPlanId": PLAN_ID},
            "status": {},
        },
        "exchangeGroupIds": [GROUP_ID_A],
        "onedriveGroupIds": [],
        "chatGroupIds": [],
    }
    session = make_session()
    col = M365AutoBackupRuleCollection(session)
    with patch.object(session, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {**SAMPLE_LIST_RESPONSE, "rulesWithMetas": [terminating_raw]}
        result = await col.list(TENANT_ID)
    assert result.rules == ()


@pytest.mark.asyncio
async def test_create_sends_correct_post_body() -> None:
    """create() POSTs the correct namespace, ruleSpec, and group ID arrays."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await col.create(
            tenant_id=TENANT_ID,
            namespace=NAMESPACE,
            plan_id=PLAN_ID,
            exchange_group_ids=[GROUP_ID_A],
            onedrive_group_ids=[],
            chat_group_ids=[],
        )

    mock_post.assert_called_once_with(
        "/api/v1/application/m365/tenant/auto_backup_rule",
        json={
            "namespace": NAMESPACE,
            "ruleSpec": {"tenantId": TENANT_ID, "backupPlanId": PLAN_ID},
            "exchangeGroupIds": [GROUP_ID_A],
            "onedriveGroupIds": [],
            "chatGroupIds": [],
        },
    )


@pytest.mark.asyncio
async def test_create_defaults_empty_group_lists() -> None:
    """create() defaults exchange/onedrive/chat group IDs to empty lists when not supplied."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)

    with patch.object(session, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await col.create(tenant_id=TENANT_ID, namespace=NAMESPACE, plan_id=PLAN_ID)

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["exchangeGroupIds"] == []
    assert body["onedriveGroupIds"] == []
    assert body["chatGroupIds"] == []


@pytest.mark.asyncio
async def test_update_sends_correct_put_body() -> None:
    """update() PUTs to the rule's UID with backupPlanId at top level (no ruleSpec wrapper)."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)
    rule = M365AutoBackupRule(
        uid=RULE_UID,
        namespace=NAMESPACE,
        tenant_id=TENANT_ID,
        plan_id=PLAN_ID,
        exchange_group_ids=(GROUP_ID_A,),
        onedrive_group_ids=(),
        chat_group_ids=(),
    )

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update(rule, exchange_group_ids=[GROUP_ID_A, GROUP_ID_B])

    mock_put.assert_called_once_with(
        f"/api/v1/application/m365/tenant/auto_backup_rule/{RULE_UID}",
        json={
            "namespace": NAMESPACE,
            "backupPlanId": PLAN_ID,
            "exchangeGroupIds": [GROUP_ID_A, GROUP_ID_B],
            "onedriveGroupIds": [],
            "chatGroupIds": [],
        },
    )


@pytest.mark.asyncio
async def test_update_keeps_unchanged_fields() -> None:
    """update() preserves current rule fields when optional params are omitted."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)
    rule = M365AutoBackupRule(
        uid=RULE_UID,
        namespace=NAMESPACE,
        tenant_id=TENANT_ID,
        plan_id=PLAN_ID,
        exchange_group_ids=(GROUP_ID_A,),
        onedrive_group_ids=(GROUP_ID_B,),
        chat_group_ids=(),
    )

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update(rule)

    _, kwargs = mock_put.call_args
    body = kwargs["json"]
    assert body["backupPlanId"] == PLAN_ID
    assert body["exchangeGroupIds"] == [GROUP_ID_A]
    assert body["onedriveGroupIds"] == [GROUP_ID_B]
    assert body["chatGroupIds"] == []


@pytest.mark.asyncio
async def test_delete_sends_delete_with_namespace_param() -> None:
    """delete() sends DELETE with namespace passed as a params= dict, not embedded in the URL."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)
    rule = M365AutoBackupRule(
        uid=RULE_UID,
        namespace=NAMESPACE,
        tenant_id=TENANT_ID,
        plan_id=PLAN_ID,
        exchange_group_ids=(GROUP_ID_A,),
        onedrive_group_ids=(),
        chat_group_ids=(),
    )

    with patch.object(session, "delete", new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = {}
        await col.delete(rule)

    mock_delete.assert_called_once_with(
        f"/api/v1/application/m365/tenant/auto_backup_rule/{RULE_UID}",
        params={"namespace": NAMESPACE},
    )


@pytest.mark.asyncio
async def test_update_collab_settings_sends_all_four_types() -> None:
    """update_collab_settings() PUTs all four service settings including disabled ones."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)

    group_exchange = M365CollabServiceSetting(plan_id=PLAN_ID, namespace=NAMESPACE)
    sharepoint = M365CollabServiceSetting(plan_id=PLAN_ID, namespace=NAMESPACE)

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update_collab_settings(
            tenant_id=TENANT_ID,
            group_exchange=group_exchange,
            mysite=None,
            sharepoint=sharepoint,
            teams=None,
        )

    mock_put.assert_called_once_with(
        "/api/v1/application/m365/tenant/auto_backup_rule/collab_service",
        json={
            "tenantId": TENANT_ID,
            "groupExchangeSetting": {"planId": PLAN_ID, "namespace": NAMESPACE},
            "mySiteSetting": {"planId": "", "namespace": ""},
            "generalSiteSetting": {"planId": PLAN_ID, "namespace": NAMESPACE},
            "teamsSetting": {"planId": "", "namespace": ""},
        },
    )


@pytest.mark.asyncio
async def test_update_collab_settings_disabled_setting_sends_empty_strings() -> None:
    """update_collab_settings() sends empty planId/namespace for disabled settings."""
    session = make_session()
    col = M365AutoBackupRuleCollection(session)

    disabled = M365CollabServiceSetting(plan_id="", namespace="")

    with patch.object(session, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {}
        await col.update_collab_settings(
            tenant_id=TENANT_ID,
            group_exchange=disabled,
        )

    _, kwargs = mock_put.call_args
    body = kwargs["json"]
    assert body["groupExchangeSetting"] == {"planId": "", "namespace": ""}
    assert body["mySiteSetting"] == {"planId": "", "namespace": ""}


def test_collab_service_setting_enabled_property() -> None:
    """M365CollabServiceSetting.enabled is True only when plan_id is non-empty."""
    assert M365CollabServiceSetting(plan_id=PLAN_ID, namespace=NAMESPACE).enabled is True
    assert M365CollabServiceSetting(plan_id="", namespace="").enabled is False
    assert M365CollabServiceSetting(plan_id="", namespace=NAMESPACE).enabled is False
