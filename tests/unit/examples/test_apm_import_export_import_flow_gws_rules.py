"""Unit tests for the import pipeline in examples/apm_import_export.py.

Covers the GWS auto-backup-rule import subtopic: YAML entry parsing (user rules and
the shared_drive collab-service setting), per-domain execution against the SDK, and
dry-run action computation. Mirrors test_apm_import_export_import_flow_m365_rules.py.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import apm_import_export as ie
import pytest

from synology_apm.sdk import APMError, GWSAutoBackupRule, GWSAutoBackupRuleListResult, GWSSharedDriveSetting
from tests.unit.examples._fixtures import make_backup_server, make_fake_apm

_GWS_PLAN_UUID = "123e4567-e89b-12d3-a456-426614174002"
_DOMAIN = "gwsdemo.example.com"
_GROUP_UUID = "123e4567-e89b-12d3-a456-426614174012"

_GWS_PLANS_BY_NAME = {"GWS Daily Backup": _GWS_PLAN_UUID}
_PLAN_NAME_BY_REF = {"plan-2": "GWS Daily Backup"}


# ── _parse_gws_rule_entries error paths ────────────────────────────────────────


def _gws_rules_data(**domain_overrides: Any) -> dict[str, Any]:
    domain_block: dict[str, Any] = {
        "domain_ref": "domain-1",
        "user_rules": [
            {
                "backup_server_ref": "server-1",
                "plan_ref": "plan-2",
                "mail_groups": [_GROUP_UUID],
            }
        ],
    }
    domain_block.update(domain_overrides)
    return {"gws_auto_backup_rules": [domain_block]}


def test_parse_gws_rule_entries_unknown_domain_ref_skips_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")

    rule_entries, collab_entries = ie._parse_gws_rule_entries(
        _gws_rules_data(domain_ref="domain-99"),
        {"server-1": bs}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"domain-1": _DOMAIN},
    )

    assert rule_entries == []
    assert collab_entries == []
    assert "domain_ref 'domain-99' not found in gws_domains section" in capsys.readouterr().err


def test_parse_gws_rule_entries_domain_fallback_without_ref() -> None:
    """When domain_ref is absent, domain is used directly (backward compatibility)."""
    bs = make_backup_server(namespace="ns-apm-server-01")
    data = _gws_rules_data(domain_ref="", domain=_DOMAIN)

    rule_entries, _ = ie._parse_gws_rule_entries(
        data, {"server-1": bs}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF, {}
    )

    assert len(rule_entries) == 1
    assert rule_entries[0].domain == _DOMAIN
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
            f"backup_server_ref 'server-99' not found (domain '{_DOMAIN}')",
        ),
    ],
    ids=["no-bs-ref", "no-plan-ref", "unknown-plan-ref", "unknown-bs-ref"],
)
def test_parse_gws_rule_entries_user_rule_errors(
    rule_overrides: dict[str, Any], expected_error: str
) -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")
    rule = {
        "backup_server_ref": "server-1",
        "plan_ref": "plan-2",
        **rule_overrides,
    }
    data = {"gws_auto_backup_rules": [{"domain_ref": "domain-1", "user_rules": [rule]}]}

    rule_entries, _ = ie._parse_gws_rule_entries(
        data, {"server-1": bs}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"domain-1": _DOMAIN},
    )

    assert rule_entries[0].parse_error == expected_error


def test_parse_gws_rule_entries_user_rule_plan_not_on_server() -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")

    rule_entries, _ = ie._parse_gws_rule_entries(
        _gws_rules_data(), {"server-1": bs}, {}, _PLAN_NAME_BY_REF,
        {"domain-1": _DOMAIN},
    )

    assert rule_entries[0].parse_error == (
        f"plan 'GWS Daily Backup' (domain '{_DOMAIN}') not found on this server"
    )


def test_parse_gws_rule_entries_shared_drive_resolved() -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")
    data = {
        "gws_auto_backup_rules": [
            {
                "domain_ref": "domain-1",
                "user_rules": [],
                "collab_services": {
                    "shared_drive": {"backup_server_ref": "server-1", "plan_ref": "plan-2"},
                },
            }
        ]
    }

    _, collab_entries = ie._parse_gws_rule_entries(
        data, {"server-1": bs}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"domain-1": _DOMAIN},
    )

    assert len(collab_entries) == 1
    ce = collab_entries[0]
    assert ce.shared_drive_specified is True
    assert ce.shared_drive == GWSSharedDriveSetting(
        plan_id=_GWS_PLAN_UUID, namespace="ns-apm-server-01", backup_user_id="",
    )
    assert ce.shared_drive_parse_error is None


def test_parse_gws_rule_entries_shared_drive_error() -> None:
    data = {
        "gws_auto_backup_rules": [
            {
                "domain_ref": "domain-1",
                "user_rules": [],
                "collab_services": {
                    "shared_drive": {"backup_server_ref": "server-99", "plan_ref": "plan-2"},
                },
            }
        ]
    }

    _, collab_entries = ie._parse_gws_rule_entries(
        data, {}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"domain-1": _DOMAIN},
    )

    assert len(collab_entries) == 1
    assert collab_entries[0].shared_drive_specified is True
    assert collab_entries[0].shared_drive is None
    assert collab_entries[0].shared_drive_parse_error == (
        f"backup_server_ref 'server-99' not found (domain '{_DOMAIN}' shared_drive)"
    )


def test_parse_gws_rule_entries_skips_non_dict_domain_entry() -> None:
    data: dict[str, Any] = {"gws_auto_backup_rules": ["not-a-dict"]}

    rule_entries, collab_entries = ie._parse_gws_rule_entries(
        data, {}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF, {}
    )

    assert rule_entries == []
    assert collab_entries == []


def test_parse_gws_rule_entries_skips_entry_without_domain_ref_or_domain() -> None:
    """A domain block with neither domain_ref nor domain is skipped entirely."""
    data = _gws_rules_data(domain_ref="", domain="")

    rule_entries, collab_entries = ie._parse_gws_rule_entries(
        data, {}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF, {}
    )

    assert rule_entries == []
    assert collab_entries == []


def test_parse_gws_rule_entries_skips_non_dict_user_rule() -> None:
    data = _gws_rules_data(user_rules=["not-a-dict"])

    rule_entries, _ = ie._parse_gws_rule_entries(
        data, {}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF, {"domain-1": _DOMAIN}
    )

    assert rule_entries == []


def test_parse_gws_rule_entries_protected_account_types() -> None:
    """protected_account_types alone (no collab_services key) must not touch shared_drive."""
    data = {
        "gws_auto_backup_rules": [
            {
                "domain_ref": "domain-1",
                "user_rules": [],
                "protected_account_types": {
                    "include_unlicensed_accounts": True,
                    "include_archived_accounts": False,
                },
            }
        ]
    }

    _, collab_entries = ie._parse_gws_rule_entries(
        data, {}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF, {"domain-1": _DOMAIN},
    )

    assert len(collab_entries) == 1
    assert collab_entries[0].include_unlicensed_accounts is True
    assert collab_entries[0].include_archived_accounts is False
    assert collab_entries[0].shared_drive_specified is False
    assert collab_entries[0].shared_drive is None
    assert collab_entries[0].shared_drive_parse_error is None


def test_parse_gws_rule_entries_protected_account_types_omitted() -> None:
    """Omitting the whole block must map to None (leave unchanged), not False (disable)."""
    bs = make_backup_server(namespace="ns-apm-server-01")
    data = {
        "gws_auto_backup_rules": [
            {
                "domain_ref": "domain-1",
                "user_rules": [],
                "collab_services": {
                    "shared_drive": {"backup_server_ref": "server-1", "plan_ref": "plan-2"},
                },
            }
        ]
    }

    _, collab_entries = ie._parse_gws_rule_entries(
        data, {"server-1": bs}, _GWS_PLANS_BY_NAME, _PLAN_NAME_BY_REF,
        {"domain-1": _DOMAIN},
    )

    assert len(collab_entries) == 1
    assert collab_entries[0].include_unlicensed_accounts is None
    assert collab_entries[0].include_archived_accounts is None
    assert collab_entries[0].shared_drive_specified is True
    assert collab_entries[0].shared_drive is not None


# ── _execute_gws_rules ──────────────────────────────────────────────────────────


def _empty_rules_result(
    rules: tuple[GWSAutoBackupRule, ...] = (),
    shared_drive: GWSSharedDriveSetting | None = None,
    include_unlicensed_accounts: bool = False,
    include_archived_accounts: bool = False,
) -> GWSAutoBackupRuleListResult:
    return GWSAutoBackupRuleListResult(
        rules=rules,
        shared_drive_setting=shared_drive,
        include_unlicensed_accounts=include_unlicensed_accounts,
        include_archived_accounts=include_archived_accounts,
    )


def _make_rule_entry(
    *,
    parse_error: str | None = None,
    plan_id: str = _GWS_PLAN_UUID,
) -> ie._GWSRuleEntry:
    return ie._GWSRuleEntry(
        domain=_DOMAIN,
        kind="gws_user_rule",
        backup_server_ref="server-1",
        resolved_namespace="ns-apm-server-01",
        plan_ref="plan-2",
        resolved_plan_id=plan_id,
        mail_groups=[_GROUP_UUID],
        calendar_groups=[],
        contact_groups=[],
        drive_groups=[],
        raw={},
        parse_error=parse_error,
    )


def _make_collab_entry(
    *,
    shared_drive_specified: bool = True,
    shared_drive_parse_error: str | None = None,
    include_unlicensed_accounts: bool | None = False,
    include_archived_accounts: bool | None = False,
) -> ie._GWSCollabEntry:
    return ie._GWSCollabEntry(
        domain=_DOMAIN,
        shared_drive_specified=shared_drive_specified,
        shared_drive=(
            GWSSharedDriveSetting(
                plan_id=_GWS_PLAN_UUID, namespace="ns-apm-server-01", backup_user_id="",
            )
            if shared_drive_specified else None
        ),
        shared_drive_parse_error=shared_drive_parse_error,
        include_unlicensed_accounts=include_unlicensed_accounts,
        include_archived_accounts=include_archived_accounts,
    )


async def test_execute_gws_rules_fetch_failure_fails_all_entries() -> None:
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(side_effect=APMError("domain offline"))

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [_make_rule_entry()], [_make_collab_entry()],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.result) for r in results] == [
        ("gws_user_rule", "failed"),
        ("gws_shared_drive", "failed"),
        ("gws_protected_account_types", "failed"),
    ]
    assert all(r.error_msg == "failed to fetch current rules: domain offline" for r in results)


async def test_execute_gws_rules_creates_new_rule_and_applies_collab() -> None:
    apm = make_fake_apm()
    # include_unlicensed_accounts diverges from the current (default False) value, so
    # protected_account_types is applied even under on_conflict=skip.
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.create = AsyncMock()
    apm.gws.auto_backup_rules.update_collab_settings = AsyncMock()
    apm.gws.auto_backup_rules.update_protected_account_types = AsyncMock()
    collab = _make_collab_entry(include_unlicensed_accounts=True)

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [_make_rule_entry()], [collab],
        "skip", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("gws_user_rule", "create", "ok"),
        # No existing shared_drive config — applied even under on_conflict=skip.
        ("gws_shared_drive", "overwrite", "ok"),
        # Target diverges from current (True != False) — applied even under on_conflict=skip.
        ("gws_protected_account_types", "overwrite", "ok"),
    ]
    apm.gws.auto_backup_rules.create.assert_awaited_once_with(
        domain=_DOMAIN,
        namespace="ns-apm-server-01",
        plan_id=_GWS_PLAN_UUID,
        mail_group_ids=[_GROUP_UUID],
        calendar_group_ids=[],
        contact_group_ids=[],
        drive_group_ids=[],
    )
    apm.gws.auto_backup_rules.update_collab_settings.assert_awaited_once_with(
        _DOMAIN, shared_drive=collab.shared_drive,
    )
    apm.gws.auto_backup_rules.update_protected_account_types.assert_awaited_once_with(
        _DOMAIN, include_unlicensed_accounts=True, include_archived_accounts=False,
    )


async def test_execute_gws_rules_overwrites_existing_rule() -> None:
    existing_rule = GWSAutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        domain=_DOMAIN,
        plan_id=_GWS_PLAN_UUID,
        mail_group_ids=(),
        calendar_group_ids=(),
        contact_group_ids=(),
        drive_group_ids=(),
    )
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(
        return_value=_empty_rules_result(rules=(existing_rule,))
    )
    apm.gws.auto_backup_rules.update = AsyncMock()

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [_make_rule_entry()], [],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.action, r.result) for r in results] == [("overwrite", "ok")]
    apm.gws.auto_backup_rules.update.assert_awaited_once_with(
        existing_rule,
        plan_id=_GWS_PLAN_UUID,
        mail_group_ids=[_GROUP_UUID],
        calendar_group_ids=[],
        contact_group_ids=[],
        drive_group_ids=[],
    )


async def test_execute_gws_rules_skips_existing_rule_and_active_collab_on_skip() -> None:
    existing_rule = GWSAutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        domain=_DOMAIN,
        plan_id=_GWS_PLAN_UUID,
        mail_group_ids=(),
        calendar_group_ids=(),
        contact_group_ids=(),
        drive_group_ids=(),
    )
    active_collab = GWSSharedDriveSetting(
        plan_id=_GWS_PLAN_UUID, namespace="ns-apm-server-01", backup_user_id="",
    )
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(
        return_value=_empty_rules_result(rules=(existing_rule,), shared_drive=active_collab)
    )
    apm.gws.auto_backup_rules.update = AsyncMock()
    apm.gws.auto_backup_rules.update_collab_settings = AsyncMock()
    apm.gws.auto_backup_rules.update_protected_account_types = AsyncMock()

    # protected_account_types target (False/False) matches current (default False/False),
    # so it is independently skipped too.
    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [_make_rule_entry()], [_make_collab_entry()],
        "skip", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("gws_user_rule", "skip", "skipped"),
        ("gws_shared_drive", "skip", "skipped"),
        ("gws_protected_account_types", "skip", "skipped"),
    ]
    apm.gws.auto_backup_rules.update.assert_not_awaited()
    apm.gws.auto_backup_rules.update_collab_settings.assert_not_awaited()
    apm.gws.auto_backup_rules.update_protected_account_types.assert_not_awaited()


async def test_execute_gws_rules_skip_retries_divergent_protected_account_types() -> None:
    """Shared Drive already applied (simulating a prior partial success) must not mask a
    still-divergent protected_account_types from being retried under on_conflict=skip."""
    active_collab = GWSSharedDriveSetting(
        plan_id=_GWS_PLAN_UUID, namespace="ns-apm-server-01", backup_user_id="",
    )
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(
        return_value=_empty_rules_result(
            shared_drive=active_collab,
            include_unlicensed_accounts=False,
            include_archived_accounts=False,
        )
    )
    apm.gws.auto_backup_rules.update_collab_settings = AsyncMock()
    apm.gws.auto_backup_rules.update_protected_account_types = AsyncMock()
    collab = _make_collab_entry(include_unlicensed_accounts=True, include_archived_accounts=False)

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [], [collab], "skip", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("gws_shared_drive", "skip", "skipped"),
        ("gws_protected_account_types", "overwrite", "ok"),
    ]
    apm.gws.auto_backup_rules.update_collab_settings.assert_not_awaited()
    apm.gws.auto_backup_rules.update_protected_account_types.assert_awaited_once_with(
        _DOMAIN, include_unlicensed_accounts=True, include_archived_accounts=False,
    )


async def test_execute_gws_rules_parse_errors_fail_entries() -> None:
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())

    results = await ie._execute_gws_rules(
        apm, _DOMAIN,
        [_make_rule_entry(parse_error="plan_ref is required")],
        [_make_collab_entry(
            shared_drive_parse_error="backup_server_ref is required",
            include_unlicensed_accounts=None, include_archived_accounts=None,
        )],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result, r.error_msg) for r in results] == [
        ("gws_user_rule", "error", "failed", "plan_ref is required"),
        ("gws_shared_drive", "error", "failed", "backup_server_ref is required"),
    ]


async def test_execute_gws_rules_shared_drive_error_does_not_block_protected_account_types() -> None:
    """A shared_drive-only parse error must not fail an otherwise-valid protected_account_types
    update — the two settings are independent."""
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.update_protected_account_types = AsyncMock()
    collab = _make_collab_entry(
        shared_drive_parse_error="backup_server_ref is required",
        include_unlicensed_accounts=True, include_archived_accounts=False,
    )

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [], [collab], "skip", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result, r.error_msg) for r in results] == [
        ("gws_shared_drive", "error", "failed", "backup_server_ref is required"),
        ("gws_protected_account_types", "overwrite", "ok", ""),
    ]
    apm.gws.auto_backup_rules.update_protected_account_types.assert_awaited_once_with(
        _DOMAIN, include_unlicensed_accounts=True, include_archived_accounts=False,
    )


async def test_execute_gws_rules_protected_account_types_only_does_not_touch_shared_drive() -> None:
    """A collab entry with only protected_account_types specified must not call
    update_collab_settings at all (collab_services was absent from this import)."""
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.update_collab_settings = AsyncMock()
    apm.gws.auto_backup_rules.update_protected_account_types = AsyncMock()
    collab = _make_collab_entry(
        shared_drive_specified=False,
        include_unlicensed_accounts=True, include_archived_accounts=False,
    )

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [], [collab], "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("gws_protected_account_types", "overwrite", "ok"),
    ]
    apm.gws.auto_backup_rules.update_collab_settings.assert_not_awaited()


async def test_execute_gws_rules_interrupted_skips_remaining_work() -> None:
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.create = AsyncMock()
    interrupted = asyncio.Event()
    interrupted.set()

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [_make_rule_entry()], [_make_collab_entry()],
        "overwrite", asyncio.Semaphore(5), interrupted,
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("gws_user_rule", "skip", "skipped"),
        ("gws_shared_drive", "skip", "skipped"),
        ("gws_protected_account_types", "skip", "skipped"),
    ]
    apm.gws.auto_backup_rules.create.assert_not_awaited()


async def test_execute_gws_rules_create_failure_is_recorded_per_rule() -> None:
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.create = AsyncMock(side_effect=APMError("quota exceeded"))

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [_make_rule_entry()], [],
        "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.action, r.result, r.error_msg) for r in results] == [
        ("create", "failed", "quota exceeded"),
    ]


async def test_execute_gws_rules_omitted_protected_account_types_not_applied() -> None:
    """A collab entry with only shared_drive specified must not touch protected_account_types."""
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.update_collab_settings = AsyncMock()
    apm.gws.auto_backup_rules.update_protected_account_types = AsyncMock()
    collab = _make_collab_entry(include_unlicensed_accounts=None, include_archived_accounts=None)

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [], [collab], "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result) for r in results] == [
        ("gws_shared_drive", "overwrite", "ok"),
    ]
    apm.gws.auto_backup_rules.update_protected_account_types.assert_not_awaited()


async def test_execute_gws_rules_shared_drive_update_failure_is_recorded() -> None:
    """update_collab_settings() raising APMError for the Shared Drive setting is recorded as
    a failed overwrite, not raised (the protected-account-types equivalent below is a
    separate, independent call per the module docstring)."""
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.update_collab_settings = AsyncMock(
        side_effect=APMError("shared drive unavailable")
    )
    collab = _make_collab_entry(include_unlicensed_accounts=None, include_archived_accounts=None)

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [], [collab], "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result, r.error_msg) for r in results] == [
        ("gws_shared_drive", "overwrite", "failed", "shared drive unavailable"),
    ]


async def test_execute_gws_rules_protected_account_types_update_failure_is_recorded() -> None:
    """update_protected_account_types() raising APMError is recorded as a failed overwrite,
    not raised."""
    apm = make_fake_apm()
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.gws.auto_backup_rules.update_protected_account_types = AsyncMock(
        side_effect=APMError("account types service unavailable")
    )
    collab = _make_collab_entry(shared_drive_specified=False)

    results = await ie._execute_gws_rules(
        apm, _DOMAIN, [], [collab], "overwrite", asyncio.Semaphore(5), asyncio.Event(),
    )

    assert [(r.kind, r.action, r.result, r.error_msg) for r in results] == [
        ("gws_protected_account_types", "overwrite", "failed", "account types service unavailable"),
    ]


# ── _compute_gws_dry_actions ────────────────────────────────────────────────────


def test_compute_gws_dry_actions_rule_states() -> None:
    rule_err = _make_rule_entry(parse_error="plan_ref is required")
    rule_unknown = _make_rule_entry()
    existing_rule = GWSAutoBackupRule(
        uid="123e4567-e89b-12d3-a456-426614174011",
        namespace="ns-apm-server-01",
        domain=_DOMAIN,
        plan_id=_GWS_PLAN_UUID,
        mail_group_ids=(),
        calendar_group_ids=(),
        contact_group_ids=(),
        drive_group_ids=(),
    )

    label = f"{_DOMAIN}:server-1"

    # parse error → error; domain missing from prefetch → unknown
    assert ie._compute_gws_dry_actions([rule_err], [], {}, "skip") == [
        (label, "gws_user_rule", "error")
    ]
    assert ie._compute_gws_dry_actions([rule_unknown], [], {}, "skip") == [
        (label, "gws_user_rule", "unknown")
    ]
    # rule exists → overwrite/skip by on_conflict; absent → create
    existing = {_DOMAIN: _empty_rules_result(rules=(existing_rule,))}
    assert ie._compute_gws_dry_actions([_make_rule_entry()], [], existing, "overwrite") == [
        (label, "gws_user_rule", "overwrite")
    ]
    assert ie._compute_gws_dry_actions([_make_rule_entry()], [], existing, "skip") == [
        (label, "gws_user_rule", "skip")
    ]
    no_rules = {_DOMAIN: _empty_rules_result()}
    assert ie._compute_gws_dry_actions([_make_rule_entry()], [], no_rules, "skip") == [
        (label, "gws_user_rule", "create")
    ]


def test_compute_gws_dry_actions_shared_drive_states() -> None:
    active = GWSSharedDriveSetting(plan_id=_GWS_PLAN_UUID, namespace="ns-apm-server-01", backup_user_id="")
    with_active = {_DOMAIN: _empty_rules_result(shared_drive=active)}
    without_active = {_DOMAIN: _empty_rules_result()}
    # Omit protected_account_types entirely so only the gws_shared_drive row is produced.
    collab = _make_collab_entry(include_unlicensed_accounts=None, include_archived_accounts=None)

    assert ie._compute_gws_dry_actions([], [collab], with_active, "skip") == [
        (_DOMAIN, "gws_shared_drive", "skip")
    ]
    assert ie._compute_gws_dry_actions([], [collab], with_active, "overwrite") == [
        (_DOMAIN, "gws_shared_drive", "overwrite")
    ]
    # No existing shared_drive config — applied even under on_conflict=skip.
    assert ie._compute_gws_dry_actions([], [collab], without_active, "skip") == [
        (_DOMAIN, "gws_shared_drive", "overwrite")
    ]


def test_compute_gws_dry_actions_protected_account_types_states() -> None:
    existing = {_DOMAIN: _empty_rules_result(include_unlicensed_accounts=False, include_archived_accounts=False)}
    collab_matching = _make_collab_entry(include_unlicensed_accounts=False, include_archived_accounts=False)
    collab_diverging = _make_collab_entry(include_unlicensed_accounts=True, include_archived_accounts=False)

    # Target matches current → skip under on_conflict=skip, overwrite under on_conflict=overwrite.
    assert ie._compute_gws_dry_actions([], [collab_matching], existing, "skip") == [
        (_DOMAIN, "gws_shared_drive", "overwrite"),
        (_DOMAIN, "gws_protected_account_types", "skip"),
    ]
    assert ie._compute_gws_dry_actions([], [collab_matching], existing, "overwrite") == [
        (_DOMAIN, "gws_shared_drive", "overwrite"),
        (_DOMAIN, "gws_protected_account_types", "overwrite"),
    ]
    # Target diverges from current — applied even under on_conflict=skip.
    assert ie._compute_gws_dry_actions([], [collab_diverging], existing, "skip") == [
        (_DOMAIN, "gws_shared_drive", "overwrite"),
        (_DOMAIN, "gws_protected_account_types", "overwrite"),
    ]


def test_compute_gws_dry_actions_collab_error_and_unknown() -> None:
    without_active = {_DOMAIN: _empty_rules_result()}

    # A shared_drive-only parse error must not mask an independently-computable
    # protected_account_types action (current matches the default target → skip).
    assert ie._compute_gws_dry_actions(
        [], [_make_collab_entry(shared_drive_parse_error="plan_ref is required")], without_active, "skip"
    ) == [
        (_DOMAIN, "gws_shared_drive", "error"),
        (_DOMAIN, "gws_protected_account_types", "skip"),
    ]
    assert ie._compute_gws_dry_actions([], [_make_collab_entry()], {}, "skip") == [
        (_DOMAIN, "gws_shared_drive", "unknown"),
        (_DOMAIN, "gws_protected_account_types", "unknown"),
    ]


def test_compute_gws_dry_actions_shared_drive_parse_error_outranks_unknown_domain() -> None:
    """A deterministic shared_drive parse error must be reported as error even when the
    domain's current state also failed to prefetch (domain absent from the existing map) —
    error is more specific/actionable than unknown and must not be masked by it."""
    collab = _make_collab_entry(
        shared_drive_parse_error="backup_server_ref is required",
        include_unlicensed_accounts=None, include_archived_accounts=None,
    )

    assert ie._compute_gws_dry_actions([], [collab], {}, "skip") == [
        (_DOMAIN, "gws_shared_drive", "error"),
    ]


def test_compute_gws_dry_actions_shared_drive_not_specified_omits_row() -> None:
    """collab_services absent from the import must not produce a gws_shared_drive row."""
    existing = {_DOMAIN: _empty_rules_result()}
    collab = _make_collab_entry(shared_drive_specified=False, include_unlicensed_accounts=True)

    assert ie._compute_gws_dry_actions([], [collab], existing, "overwrite") == [
        (_DOMAIN, "gws_protected_account_types", "overwrite"),
    ]
