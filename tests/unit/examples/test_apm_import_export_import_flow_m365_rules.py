"""Unit tests for the import pipeline in examples/apm_import_export.py.

Covers the M365 auto-backup-rule import subtopic: YAML entry parsing (user rules and
collab-service settings), per-tenant execution against the SDK, and dry-run action
computation.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import apm_import_export as ie
import pytest

from synology_apm.sdk import APMError, M365AutoBackupRule, M365AutoBackupRuleListResult, M365CollabServiceSetting
from tests.unit.examples._fixtures import make_backup_server, make_fake_apm

_M365_PLAN_UUID = "123e4567-e89b-12d3-a456-426614174002"
_TENANT_UUID = "123e4567-e89b-12d3-a456-426614174060"
_GROUP_UUID = "123e4567-e89b-12d3-a456-426614174012"

_M365_PLANS_BY_NAME = {"M365 Daily Backup": _M365_PLAN_UUID}
_PLAN_NAME_BY_REF = {"plan-2": "M365 Daily Backup"}


# ── _parse_m365_rule_entries error paths ──────────────────────────────────────


def _m365_rules_data(**tenant_overrides: Any) -> dict[str, Any]:
    tenant_block: dict[str, Any] = {
        "tenant_ref": "tenant-1",
        "user_rules": [
            {
                "backup_server_ref": "server-1",
                "plan_ref": "plan-2",
                "exchange_groups": [_GROUP_UUID],
            }
        ],
    }
    tenant_block.update(tenant_overrides)
    return {"m365_auto_backup_rules": [tenant_block]}


def test_parse_m365_rule_entries_unknown_tenant_ref_skips_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")

    rule_entries, collab_entries = ie._parse_m365_rule_entries(
        _m365_rules_data(tenant_ref="tenant-99"),
        {"server-1": bs}, _M365_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"tenant-1": _TENANT_UUID},
    )

    assert rule_entries == []
    assert collab_entries == []
    assert "tenant_ref 'tenant-99' not found in saas_tenants section" in capsys.readouterr().err


def test_parse_m365_rule_entries_tenant_id_fallback_without_ref() -> None:
    """When tenant_ref is absent, tenant_id is used directly (backward compatibility)."""
    bs = make_backup_server(namespace="ns-apm-server-01")
    data = _m365_rules_data(tenant_ref="", tenant_id=_TENANT_UUID)

    rule_entries, _ = ie._parse_m365_rule_entries(
        data, {"server-1": bs}, _M365_PLANS_BY_NAME, _PLAN_NAME_BY_REF, {}
    )

    assert len(rule_entries) == 1
    assert rule_entries[0].tenant_id == _TENANT_UUID
    assert rule_entries[0].parse_error is None


@pytest.mark.parametrize(
    ("rule_overrides", "expected_error"),
    [
        ({"backup_server_ref": ""}, "backup_server_ref is required"),
        ({"plan_ref": ""}, "plan_ref is required"),
        (
            {"plan_ref": "plan-99"},
            "plan_ref 'plan-99' not found in protection_plans section",
        ),
        (
            {"backup_server_ref": "server-99"},
            f"backup_server_ref 'server-99' not found (tenant '{_TENANT_UUID}')",
        ),
    ],
    ids=["no-bs-ref", "no-plan-ref", "unknown-plan-ref", "unknown-bs-ref"],
)
def test_parse_m365_rule_entries_user_rule_errors(
    rule_overrides: dict[str, Any], expected_error: str
) -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")
    rule = {
        "backup_server_ref": "server-1",
        "plan_ref": "plan-2",
        **rule_overrides,
    }
    data = {"m365_auto_backup_rules": [{"tenant_ref": "tenant-1", "user_rules": [rule]}]}

    rule_entries, _ = ie._parse_m365_rule_entries(
        data, {"server-1": bs}, _M365_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"tenant-1": _TENANT_UUID},
    )

    assert rule_entries[0].parse_error == expected_error


def test_parse_m365_rule_entries_user_rule_plan_not_on_server() -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")

    rule_entries, _ = ie._parse_m365_rule_entries(
        _m365_rules_data(), {"server-1": bs}, {}, _PLAN_NAME_BY_REF,
        {"tenant-1": _TENANT_UUID},
    )

    assert rule_entries[0].parse_error == (
        f"plan 'M365 Daily Backup' (tenant '{_TENANT_UUID}') not found on this server"
    )


def test_parse_m365_rule_entries_collab_error_notes_succeeded_services() -> None:
    """Collab parse errors are aggregated and note the services that resolved fine."""
    bs = make_backup_server(namespace="ns-apm-server-01")
    data = {
        "m365_auto_backup_rules": [
            {
                "tenant_ref": "tenant-1",
                "user_rules": [],
                "collab_services": {
                    "sharepoint": {"backup_server_ref": "server-1", "plan_ref": "plan-2"},
                    "teams": {"backup_server_ref": "server-99", "plan_ref": "plan-2"},
                },
            }
        ]
    }

    _, collab_entries = ie._parse_m365_rule_entries(
        data, {"server-1": bs}, _M365_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"tenant-1": _TENANT_UUID},
    )

    assert len(collab_entries) == 1
    ce = collab_entries[0]
    assert ce.sharepoint == M365CollabServiceSetting(
        plan_id=_M365_PLAN_UUID, namespace="ns-apm-server-01"
    )
    assert ce.teams is None
    assert ce.parse_error == (
        f"backup_server_ref 'server-99' not found (tenant '{_TENANT_UUID}' teams) "
        "(succeeded but not applied: sharepoint)"
    )


# ── _execute_m365_rules ───────────────────────────────────────────────────────


def _empty_rules_result(
    rules: tuple[M365AutoBackupRule, ...] = (),
    **collab: M365CollabServiceSetting,
) -> M365AutoBackupRuleListResult:
    disabled = M365CollabServiceSetting(plan_id="", namespace="")
    return M365AutoBackupRuleListResult(
        rules=rules,
        group_exchange=collab.get("group_exchange", disabled),
        mysite=collab.get("mysite", disabled),
        sharepoint=collab.get("sharepoint", disabled),
        teams=collab.get("teams", disabled),
    )


def _make_rule_entry(
    *,
    parse_error: str | None = None,
    plan_id: str = _M365_PLAN_UUID,
) -> ie._M365RuleEntry:
    return ie._M365RuleEntry(
        tenant_id=_TENANT_UUID,
        kind="m365_user_rule",
        backup_server_ref="server-1",
        resolved_namespace="ns-apm-server-01",
        plan_ref="plan-2",
        resolved_plan_id=plan_id,
        exchange_groups=[_GROUP_UUID],
        onedrive_groups=[],
        chat_groups=[],
        raw={},
        parse_error=parse_error,
    )


def _make_collab_entry(
    *,
    parse_error: str | None = None,
) -> ie._M365CollabEntry:
    return ie._M365CollabEntry(
        tenant_id=_TENANT_UUID,
        group_exchange=None,
        mysite=None,
        sharepoint=M365CollabServiceSetting(
            plan_id=_M365_PLAN_UUID, namespace="ns-apm-server-01"
        ),
        teams=None,
        parse_error=parse_error,
    )


async def test_execute_m365_rules_fetch_failure_fails_all_entries() -> None:
    apm = make_fake_apm()
    apm.m365.auto_backup_rules.list = AsyncMock(side_effect=APMError("tenant offline"))

    results = await ie._execute_m365_rules(
        apm, _TENANT_UUID, [_make_rule_entry()], [_make_collab_entry()],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.result) for r in results] == [
        ("m365_user_rule", "failed"),
        ("m365_collab_services", "failed"),
    ]
    assert all(r.error_msg == "failed to fetch current rules: tenant offline" for r in results)


async def test_execute_m365_rules_creates_new_rule_and_applies_collab() -> None:
    apm = make_fake_apm()
    apm.m365.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.m365.auto_backup_rules.create = AsyncMock()
    apm.m365.auto_backup_rules.update_collab_settings = AsyncMock()
    collab = _make_collab_entry()

    results = await ie._execute_m365_rules(
        apm, _TENANT_UUID, [_make_rule_entry()], [collab],
        "skip", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("m365_user_rule", "create", "ok"),
        # No existing collab config — applied even under on_conflict=skip.
        ("m365_collab_services", "overwrite", "ok"),
    ]
    apm.m365.auto_backup_rules.create.assert_awaited_once_with(
        tenant_id=_TENANT_UUID,
        namespace="ns-apm-server-01",
        plan_id=_M365_PLAN_UUID,
        exchange_group_ids=[_GROUP_UUID],
        onedrive_group_ids=[],
        chat_group_ids=[],
    )
    apm.m365.auto_backup_rules.update_collab_settings.assert_awaited_once_with(
        tenant_id=_TENANT_UUID,
        group_exchange=None,
        mysite=None,
        sharepoint=collab.sharepoint,
        teams=None,
    )


async def test_execute_m365_rules_overwrites_existing_rule() -> None:
    existing_rule = M365AutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        tenant_id=_TENANT_UUID,
        plan_id=_M365_PLAN_UUID,
        exchange_group_ids=(),
        onedrive_group_ids=(),
        chat_group_ids=(),
    )
    apm = make_fake_apm()
    apm.m365.auto_backup_rules.list = AsyncMock(
        return_value=_empty_rules_result(rules=(existing_rule,))
    )
    apm.m365.auto_backup_rules.update = AsyncMock()

    results = await ie._execute_m365_rules(
        apm, _TENANT_UUID, [_make_rule_entry()], [],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.action, r.result) for r in results] == [("overwrite", "ok")]
    apm.m365.auto_backup_rules.update.assert_awaited_once_with(
        existing_rule,
        plan_id=_M365_PLAN_UUID,
        exchange_group_ids=[_GROUP_UUID],
        onedrive_group_ids=[],
        chat_group_ids=[],
    )


async def test_execute_m365_rules_skips_existing_rule_and_active_collab_on_skip() -> None:
    existing_rule = M365AutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        tenant_id=_TENANT_UUID,
        plan_id=_M365_PLAN_UUID,
        exchange_group_ids=(),
        onedrive_group_ids=(),
        chat_group_ids=(),
    )
    active_collab = M365CollabServiceSetting(
        plan_id=_M365_PLAN_UUID, namespace="ns-apm-server-01"
    )
    apm = make_fake_apm()
    apm.m365.auto_backup_rules.list = AsyncMock(
        return_value=_empty_rules_result(rules=(existing_rule,), sharepoint=active_collab)
    )
    apm.m365.auto_backup_rules.update = AsyncMock()
    apm.m365.auto_backup_rules.update_collab_settings = AsyncMock()

    results = await ie._execute_m365_rules(
        apm, _TENANT_UUID, [_make_rule_entry()], [_make_collab_entry()],
        "skip", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("m365_user_rule", "skip", "skipped"),
        ("m365_collab_services", "skip", "skipped"),
    ]
    apm.m365.auto_backup_rules.update.assert_not_awaited()
    apm.m365.auto_backup_rules.update_collab_settings.assert_not_awaited()


async def test_execute_m365_rules_parse_errors_fail_entries() -> None:
    apm = make_fake_apm()
    apm.m365.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())

    results = await ie._execute_m365_rules(
        apm, _TENANT_UUID,
        [_make_rule_entry(parse_error="plan_ref is required")],
        [_make_collab_entry(parse_error="backup_server_ref is required")],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.action, r.result, r.error_msg) for r in results] == [
        ("error", "failed", "plan_ref is required"),
        ("error", "failed", "backup_server_ref is required"),
    ]


async def test_execute_m365_rules_interrupted_skips_remaining_work() -> None:
    apm = make_fake_apm()
    apm.m365.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.m365.auto_backup_rules.create = AsyncMock()
    interrupted = asyncio.Event()
    interrupted.set()

    results = await ie._execute_m365_rules(
        apm, _TENANT_UUID, [_make_rule_entry()], [_make_collab_entry()],
        "overwrite", asyncio.Semaphore(5), interrupted,
    )

    assert [(r.action, r.result) for r in results] == [
        ("skip", "skipped"),
        ("skip", "skipped"),
    ]
    apm.m365.auto_backup_rules.create.assert_not_awaited()


async def test_execute_m365_rules_create_failure_is_recorded_per_rule() -> None:
    apm = make_fake_apm()
    apm.m365.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.m365.auto_backup_rules.create = AsyncMock(side_effect=APMError("quota exceeded"))

    results = await ie._execute_m365_rules(
        apm, _TENANT_UUID, [_make_rule_entry()], [],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.action, r.result, r.error_msg) for r in results] == [
        ("create", "failed", "quota exceeded"),
    ]


# ── _compute_m365_dry_actions ─────────────────────────────────────────────────


def test_compute_m365_dry_actions_rule_states() -> None:
    rule_err = _make_rule_entry(parse_error="plan_ref is required")
    rule_unknown = _make_rule_entry()
    existing_rule = M365AutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        tenant_id=_TENANT_UUID,
        plan_id=_M365_PLAN_UUID,
        exchange_group_ids=(),
        onedrive_group_ids=(),
        chat_group_ids=(),
    )

    label = f"{_TENANT_UUID}:server-1"

    # parse error → error; tenant missing from prefetch → unknown
    assert ie._compute_m365_dry_actions([rule_err], [], {}, "skip") == [
        (label, "m365_user_rule", "error")
    ]
    assert ie._compute_m365_dry_actions([rule_unknown], [], {}, "skip") == [
        (label, "m365_user_rule", "unknown")
    ]
    # rule exists → overwrite/skip by on_conflict; absent → create
    existing = {_TENANT_UUID: _empty_rules_result(rules=(existing_rule,))}
    assert ie._compute_m365_dry_actions([_make_rule_entry()], [], existing, "overwrite") == [
        (label, "m365_user_rule", "overwrite")
    ]
    assert ie._compute_m365_dry_actions([_make_rule_entry()], [], existing, "skip") == [
        (label, "m365_user_rule", "skip")
    ]
    no_rules = {_TENANT_UUID: _empty_rules_result()}
    assert ie._compute_m365_dry_actions([_make_rule_entry()], [], no_rules, "skip") == [
        (label, "m365_user_rule", "create")
    ]


def test_compute_m365_dry_actions_collab_states() -> None:
    active = M365CollabServiceSetting(plan_id=_M365_PLAN_UUID, namespace="ns-apm-server-01")
    with_active = {_TENANT_UUID: _empty_rules_result(sharepoint=active)}
    without_active = {_TENANT_UUID: _empty_rules_result()}

    assert ie._compute_m365_dry_actions([], [_make_collab_entry()], with_active, "skip") == [
        (_TENANT_UUID, "m365_collab_services", "skip")
    ]
    assert ie._compute_m365_dry_actions([], [_make_collab_entry()], with_active, "overwrite") == [
        (_TENANT_UUID, "m365_collab_services", "overwrite")
    ]
    # No existing collab config — applied even under on_conflict=skip.
    assert ie._compute_m365_dry_actions([], [_make_collab_entry()], without_active, "skip") == [
        (_TENANT_UUID, "m365_collab_services", "overwrite")
    ]
    assert ie._compute_m365_dry_actions(
        [], [_make_collab_entry(parse_error="plan_ref is required")], without_active, "skip"
    ) == [(_TENANT_UUID, "m365_collab_services", "error")]
