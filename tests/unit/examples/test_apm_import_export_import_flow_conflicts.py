"""Unit tests for the import pipeline in examples/apm_import_export.py.

Covers action determination, deferred-RS plan-request building, and conflict checking
against existing server resources (protection plans, retirement plans, tiering plans).
"""
from __future__ import annotations

from datetime import time
from typing import Any
from unittest.mock import AsyncMock

import apm_import_export as ie
import pytest

from synology_apm.sdk import (
    APMError,
    MachinePlanCreateRequest,
    RemoteStorage,
    ResourceNotFoundError,
    RetirementPlan,
    TieringPlan,
)
from tests.unit.examples._fixtures import make_fake_apm, make_protection_plan, make_remote_storage

_MACHINE_PLAN_UUID = "123e4567-e89b-12d3-a456-426614174001"


# ── _determine_action ─────────────────────────────────────────────────────────


def _make_import_entry(
    *,
    name: str = "Daily Backup",
    kind: str = "protection-plan",
    parse_error: str | None = None,
    request: Any = None,
    resolved_name: str | None = None,
) -> ie._ImportEntry:
    return ie._ImportEntry(
        name=name,
        kind=kind,
        subtype="machine" if kind == "protection-plan" else "",
        raw={},
        request=request,
        parse_error=parse_error,
        resolved_name=resolved_name,
    )


@pytest.mark.parametrize(
    ("parse_error", "existing_id", "on_conflict", "expected"),
    [
        ("Something went wrong", None, "skip", "error"),
        ("Something went wrong", "123e4567-e89b-12d3-a456-426614174001", "overwrite", "error"),
        (None, "123e4567-e89b-12d3-a456-426614174001", "overwrite", "overwrite"),
        (None, "123e4567-e89b-12d3-a456-426614174001", "skip", "skip"),
        (None, None, "skip", "create"),
        (None, None, "overwrite", "create"),
    ],
    ids=[
        "parse-error", "parse-error-overrides-existing",
        "existing-overwrite", "existing-skip",
        "new-create", "new-create-overwrite-conflict",
    ],
)
def test_determine_action(
    parse_error: str | None, existing_id: str | None, on_conflict: str, expected: str
) -> None:
    entry = _make_import_entry(parse_error=parse_error)
    assert ie._determine_action(entry, existing_id, on_conflict) == expected


# ── _build_plan_requests (deferred-RS mechanism) ──────────────────────────────


def _make_plan_with_rs_backup_copy(dest_ref: str) -> dict[str, Any]:
    return {
        "name_or_id": "Daily Backup",
        "type": "machine",
        "retention": {"type": "keep_days", "days": 30},
        "schedule": {"frequency": "daily", "start_time": "02:00", "weekdays": []},
        "backup_copy": {
            "destination_type": "remote_storage",
            "destination_ref": dest_ref,
            "retention": {"type": "keep_days", "days": 7},
            "schedule": {"frequency": "after_backup", "start_time": None, "weekdays": []},
        },
    }


def test_build_plan_requests_resolves_deferred_entry() -> None:
    """After RS creation, _build_plan_requests fills the request for a deferred entry."""
    fake_rs = make_remote_storage(name="DSM-Storage", storage_id="123e4567-e89b-12d3-a456-426614174030")
    data: dict[str, Any] = {
        "protection_plans": [_make_plan_with_rs_backup_copy("pending-rs")]
    }

    entries = ie._parse_all_entries(data, {}, {}, rs_pending_refs={"pending-rs"})
    assert entries[0].request is None
    assert entries[0].parse_error is None

    remote_storages_by_ref: dict[str, RemoteStorage] = {"pending-rs": fake_rs}
    ie._build_plan_requests(entries, {}, remote_storages_by_ref)

    assert entries[0].parse_error is None
    assert isinstance(entries[0].request, MachinePlanCreateRequest)
    assert entries[0].request.backup_copy is not None
    assert entries[0].request.backup_copy.destination is fake_rs


def test_build_plan_requests_skips_entries_with_existing_request() -> None:
    """Entries with an already-built request are not re-processed."""
    fake_rs = make_remote_storage()
    data: dict[str, Any] = {
        "protection_plans": [_make_plan_with_rs_backup_copy("ref-rs")]
    }
    remote_storages_by_ref: dict[str, RemoteStorage] = {"ref-rs": fake_rs}
    entries = ie._parse_all_entries(data, {}, remote_storages_by_ref)
    # Entry should already have a request (RS was available during first pass)
    assert entries[0].request is not None

    original_request = entries[0].request
    ie._build_plan_requests(entries, {}, remote_storages_by_ref)
    # request should be unchanged
    assert entries[0].request is original_request


def test_build_plan_requests_skips_entries_with_parse_error() -> None:
    """Entries with a parse_error (non-pending failures) are not re-processed."""
    data: dict[str, Any] = {
        "protection_plans": [_make_plan_with_rs_backup_copy("missing-rs")]
    }
    entries = ie._parse_all_entries(data, {}, {}, rs_pending_refs=set())
    assert entries[0].parse_error is not None

    original_error = entries[0].parse_error
    # Even if we now add the RS, the entry has parse_error so it must not be retried
    fake_rs = make_remote_storage()
    ie._build_plan_requests(entries, {}, {"missing-rs": fake_rs})
    assert entries[0].parse_error == original_error


def test_build_plan_requests_records_error_for_still_missing_ref() -> None:
    """A deferred entry whose RS ref is still missing after RS creation gets a parse error."""
    data: dict[str, Any] = {
        "protection_plans": [_make_plan_with_rs_backup_copy("pending-rs")]
    }
    entries = ie._parse_all_entries(data, {}, {}, rs_pending_refs={"pending-rs"})

    ie._build_plan_requests(entries, {}, {})  # RS creation failed — ref map still empty

    assert entries[0].request is None
    assert entries[0].parse_error is not None
    assert "backup_copy destination not found" in entries[0].parse_error


# ── _check_conflicts ──────────────────────────────────────────────────────────


async def test_check_conflicts_protection_plan_matched_by_name() -> None:
    stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    entry = _make_import_entry(name="Daily Backup")
    apm = make_fake_apm()

    existing = await ie._check_conflicts(apm, [entry], [stub])

    assert existing == {"protection-plan:Daily Backup": _MACHINE_PLAN_UUID}
    assert entry.parse_error is None
    assert entry.resolved_name is None


async def test_check_conflicts_protection_plan_uuid_resolves_display_name() -> None:
    stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    entry = _make_import_entry(name=_MACHINE_PLAN_UUID)
    apm = make_fake_apm()

    existing = await ie._check_conflicts(apm, [entry], [stub])

    assert existing[f"protection-plan:{_MACHINE_PLAN_UUID}"] == _MACHINE_PLAN_UUID
    assert entry.resolved_name == "Daily Backup"


async def test_check_conflicts_protection_plan_type_conflict() -> None:
    stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    entry = ie._ImportEntry(
        name="Daily Backup", kind="protection-plan", subtype="m365",
        raw={}, request=None, parse_error=None,
    )
    apm = make_fake_apm()

    await ie._check_conflicts(apm, [entry], [stub])

    assert entry.parse_error == (
        "type conflict: YAML declares type='m365' but the existing plan is type='machine'"
    )


async def test_check_conflicts_protection_plan_immutability_conflict() -> None:
    stub = make_protection_plan(
        plan_id=_MACHINE_PLAN_UUID, name="Daily Backup", is_immutable=False
    )
    entry = ie._ImportEntry(
        name="Daily Backup", kind="protection-plan", subtype="machine",
        raw={"is_immutable": True}, request=None, parse_error=None,
    )
    apm = make_fake_apm()

    await ie._check_conflicts(apm, [entry], [stub])

    assert entry.parse_error == (
        "immutability conflict: YAML declares is_immutable=True "
        "but the existing plan has is_immutable=False"
    )


async def test_check_conflicts_protection_plan_uuid_not_found_is_parse_error() -> None:
    entry = _make_import_entry(name=_MACHINE_PLAN_UUID)
    apm = make_fake_apm()

    existing = await ie._check_conflicts(apm, [entry], [])

    assert existing[f"protection-plan:{_MACHINE_PLAN_UUID}"] is None
    assert entry.parse_error == (
        f"protection plan UUID '{_MACHINE_PLAN_UUID}' not found on this server"
    )


async def test_check_conflicts_retirement_plan_by_name() -> None:
    plan = RetirementPlan(
        plan_id="123e4567-e89b-12d3-a456-426614174007",
        name="Compliance Retention",
        retention=None,
    )
    entry = _make_import_entry(name="Compliance Retention", kind="retirement-plan")
    apm = make_fake_apm()
    apm.retirement_plans.get_by_name = AsyncMock(return_value=plan)

    existing = await ie._check_conflicts(apm, [entry], [])

    assert existing == {
        "retirement-plan:Compliance Retention": "123e4567-e89b-12d3-a456-426614174007"
    }
    apm.retirement_plans.get_by_name.assert_awaited_once_with("Compliance Retention")


async def test_check_conflicts_retirement_plan_by_uuid_resolves_name() -> None:
    plan_uuid = "123e4567-e89b-12d3-a456-426614174007"
    plan = RetirementPlan(plan_id=plan_uuid, name="Compliance Retention", retention=None)
    entry = _make_import_entry(name=plan_uuid, kind="retirement-plan")
    apm = make_fake_apm()
    apm.retirement_plans.get = AsyncMock(return_value=plan)

    existing = await ie._check_conflicts(apm, [entry], [])

    assert existing == {f"retirement-plan:{plan_uuid}": plan_uuid}
    assert entry.resolved_name == "Compliance Retention"
    apm.retirement_plans.get.assert_awaited_once_with(plan_uuid)


async def test_check_conflicts_retirement_uuid_not_found_is_parse_error() -> None:
    plan_uuid = "123e4567-e89b-12d3-a456-426614174007"
    entry = _make_import_entry(name=plan_uuid, kind="retirement-plan")
    apm = make_fake_apm()
    apm.retirement_plans.get = AsyncMock(
        side_effect=ResourceNotFoundError(
            "not found", resource_type="RetirementPlan", resource_id=plan_uuid
        )
    )

    existing = await ie._check_conflicts(apm, [entry], [])

    assert existing[f"retirement-plan:{plan_uuid}"] is None
    assert entry.parse_error == (
        f"retirement-plan UUID '{plan_uuid}' not found on this server"
    )


async def test_check_conflicts_name_not_found_creates_without_error() -> None:
    entry = _make_import_entry(name="Compliance Retention", kind="retirement-plan")
    apm = make_fake_apm()
    apm.retirement_plans.get_by_name = AsyncMock(
        side_effect=ResourceNotFoundError(
            "not found", resource_type="RetirementPlan", resource_id="Compliance Retention"
        )
    )

    existing = await ie._check_conflicts(apm, [entry], [])

    assert existing["retirement-plan:Compliance Retention"] is None
    assert entry.parse_error is None


async def test_check_conflicts_tiering_plan_by_name() -> None:
    plan_uuid = "123e4567-e89b-12d3-a456-426614174009"
    plan = TieringPlan(
        plan_id=plan_uuid,
        name="Tier Old Versions",
        description="",
        tiering_after_days=30,
        daily_check_time=time(20, 0),
        destination=None,
        server_count=0,
        run_schedule_by_controller_time=False,
    )
    entry = _make_import_entry(name="Tier Old Versions", kind="tiering-plan")
    apm = make_fake_apm()
    apm.tiering_plans.get_by_name = AsyncMock(return_value=plan)

    existing = await ie._check_conflicts(apm, [entry], [])

    assert existing == {"tiering-plan:Tier Old Versions": plan_uuid}


async def test_check_conflicts_apm_error_sets_parse_error_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entry = _make_import_entry(name="Compliance Retention", kind="retirement-plan")
    apm = make_fake_apm()
    apm.retirement_plans.get_by_name = AsyncMock(side_effect=APMError("server unavailable"))

    existing = await ie._check_conflicts(apm, [entry], [])

    assert "retirement-plan:Compliance Retention" not in existing
    assert entry.parse_error == "conflict check failed: server unavailable"
    err = capsys.readouterr().err
    assert "Warning: could not check 'Compliance Retention': server unavailable" in err
