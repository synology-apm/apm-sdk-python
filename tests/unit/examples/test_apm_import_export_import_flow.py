"""Unit tests for the import pipeline in examples/apm_import_export.py.

Covers conflict checking, action selection, request building, per-item execution,
credential auto-detection, the run_import orchestrator, and main() argument wiring.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import apm_import_export as ie
import pytest
import yaml

from synology_apm.sdk import (
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APMError,
    APVStorageAddRequest,
    C2ObjectStorageAddRequest,
    DuplicateWorkloadError,
    FileServerAddRequest,
    FileServerType,
    FileServerUpdateRequest,
    GenericS3StorageAddRequest,
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
    M365PlanCreateRequest,
    MachinePlanCreateRequest,
    MachineWorkloadType,
    PlanNameConflictError,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorage,
    RemoteStorageAddResult,
    RemoteStorageConflictError,
    RemoteStorageInUseError,
    RemoteStorageType,
    RemoteStorageUnmanagedCatalogError,
    RemoteStorageUpdateRequest,
    ResourceNotFoundError,
    RetentionType,
    RetirementPlan,
    RetirementPlanCreateRequest,
    ScheduleFrequency,
    TieringPlan,
    TieringPlanCreateRequest,
    WasabiCloudStorageAddRequest,
    WorkloadCategory,
)
from tests.unit.examples._fixtures import (
    make_backup_server,
    make_fake_apm,
    make_file_server_config,
    make_machine_workload,
    make_protection_plan,
    make_remote_storage,
    patch_make_client,
)

_MACHINE_PLAN_UUID = "123e4567-e89b-12d3-a456-426614174001"
_M365_PLAN_UUID = "123e4567-e89b-12d3-a456-426614174002"
_TENANT_UUID = "123e4567-e89b-12d3-a456-426614174060"
_GROUP_UUID = "123e4567-e89b-12d3-a456-426614174012"

_RETENTION = ProtectionRetentionPolicy(RetentionType.KEEP_DAYS, days=14)
_SCHEDULE = ProtectionSchedule(ScheduleFrequency.DAILY, start_time=time(2, 0))


@pytest.fixture
def no_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep run_import from touching real SIGINT handlers."""
    monkeypatch.setattr(ie, "register_interrupt", lambda loop, event: None)
    monkeypatch.setattr(ie, "unregister_interrupt", lambda loop: None)


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


# ── _select_rs_actions ────────────────────────────────────────────────────────


def _make_rs_entry(
    *,
    name_or_id: str = "tiering-remote",
    ref_key: str = "storage-1",
    endpoint: str = "https://s3.example.com:443",
    vault_name: str = "my-bucket",
    storage_type_str: str = "s3_compatible",
    raw: dict[str, Any] | None = None,
    parse_error: str | None = None,
) -> ie._RsEntry:
    return ie._RsEntry(
        name_or_id=name_or_id,
        ref_key=ref_key,
        endpoint=endpoint,
        vault_name=vault_name,
        storage_type_str=storage_type_str,
        raw=raw if raw is not None else {"trust_self_signed": True},
        parse_error=parse_error,
    )


_RS_CREDS: dict[tuple[str, str, str], dict[str, str]] = {
    ("s3_compatible", "https://s3.example.com:443", "my-bucket"): {
        "access_key": "AK",
        "secret_key": "SK",
        "relink_encryption_key": "RK",
    },
}


def test_select_rs_actions_create_builds_add_request() -> None:
    rse = _make_rs_entry()

    actions = ie._select_rs_actions([rse], _RS_CREDS, "skip", {}, {})

    assert actions == {ie._rs_key(rse): "create"}
    assert rse.request == GenericS3StorageAddRequest(
        access_key="AK",
        secret_key="SK",
        vault_name="my-bucket",
        endpoint="https://s3.example.com:443",
        encryption_enabled=False,
        relink_encryption_key="RK",
        trust_self_signed=True,
    )


def test_select_rs_actions_existing_name_overwrite_builds_update_request() -> None:
    rse = _make_rs_entry()
    existing = make_remote_storage(name="tiering-remote")

    actions = ie._select_rs_actions(
        [rse], _RS_CREDS, "overwrite", {}, {"tiering-remote": existing}
    )

    assert actions == {ie._rs_key(rse): "overwrite"}
    assert rse.request == RemoteStorageUpdateRequest(
        access_key="AK",
        secret_key="SK",
        endpoint="https://s3.example.com:443",
        trust_self_signed=True,
    )


def test_select_rs_actions_existing_uuid_skip_leaves_request_unbuilt() -> None:
    storage_id = "123e4567-e89b-12d3-a456-426614174030"
    rse = _make_rs_entry(name_or_id=storage_id)
    existing = make_remote_storage(storage_id=storage_id)

    actions = ie._select_rs_actions([rse], _RS_CREDS, "skip", {storage_id: existing}, {})

    assert actions == {ie._rs_key(rse): "skip"}
    assert rse.request is None


def test_select_rs_actions_parse_error_maps_to_error() -> None:
    rse = _make_rs_entry(parse_error="unrecognized storage_type 'tape'")

    actions = ie._select_rs_actions([rse], _RS_CREDS, "skip", {}, {})

    assert actions == {ie._rs_key(rse): "error"}
    assert rse.request is None


# ── _build_fs_requests ────────────────────────────────────────────────────────


def _make_fs_entry(
    *,
    host_ip: str = "10.0.0.10",
    resolved_namespace: str = "ns-apm-server-01",
    plan_name: str = "Daily Backup",
    raw: dict[str, Any] | None = None,
    parse_error: str | None = None,
) -> ie._FsEntry:
    return ie._FsEntry(
        host_ip=host_ip,
        backup_server_ref="server-1",
        resolved_namespace=resolved_namespace,
        plan_name=plan_name,
        raw=raw if raw is not None else {"host_ip": host_ip, "login_user": "admin"},
        parse_error=parse_error,
    )


def _fs_actions_for(fse: ie._FsEntry, action: str) -> dict[str, str]:
    return {f"{fse.host_ip}:{fse.resolved_namespace}:{fse.plan_name}": action}


_PLANS_BY_NAME = {"Daily Backup": _MACHINE_PLAN_UUID}


def test_build_fs_requests_create_with_credential_builds_add_request() -> None:
    fse = _make_fs_entry()
    actions = _fs_actions_for(fse, "create")

    ie._build_fs_requests([fse], {("10.0.0.10", "admin"): "fs-pw"}, actions, _PLANS_BY_NAME)

    assert fse.parse_error is None
    assert isinstance(fse.request, FileServerAddRequest)
    assert fse.request.login_password == "fs-pw"
    assert fse.request.login_user == "admin"
    assert fse.request.plan_id == _MACHINE_PLAN_UUID
    assert fse.request.namespace == "ns-apm-server-01"
    assert actions == _fs_actions_for(fse, "create")


def test_build_fs_requests_create_without_credentials_file_is_error() -> None:
    fse = _make_fs_entry()
    actions = _fs_actions_for(fse, "create")

    ie._build_fs_requests([fse], None, actions, _PLANS_BY_NAME)

    assert fse.parse_error == (
        "no fs-credentials file provided — use --fs-credentials to supply a password"
    )
    assert actions == _fs_actions_for(fse, "error")


def test_build_fs_requests_create_with_missing_credential_row_is_error() -> None:
    fse = _make_fs_entry()
    actions = _fs_actions_for(fse, "create")

    ie._build_fs_requests(
        [fse], {("192.0.2.99", "admin"): "other-pw"}, actions, _PLANS_BY_NAME
    )

    assert fse.parse_error == (
        "credential not found for endpoint='10.0.0.10', login_user='admin' "
        "in fs-credentials file"
    )
    assert actions == _fs_actions_for(fse, "error")


def test_build_fs_requests_create_with_empty_password_is_error() -> None:
    fse = _make_fs_entry()
    actions = _fs_actions_for(fse, "create")

    ie._build_fs_requests([fse], {("10.0.0.10", "admin"): ""}, actions, _PLANS_BY_NAME)

    assert fse.parse_error == (
        "password is empty for endpoint='10.0.0.10', login_user='admin' "
        "in fs-credentials file"
    )
    assert actions == _fs_actions_for(fse, "error")


def test_build_fs_requests_overwrite_without_credential_keeps_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fse = _make_fs_entry()
    actions = _fs_actions_for(fse, "overwrite")

    ie._build_fs_requests(
        [fse], {("192.0.2.99", "admin"): "other-pw"}, actions, _PLANS_BY_NAME
    )

    assert isinstance(fse.request, FileServerUpdateRequest)
    assert fse.request.login_password is None
    assert actions == _fs_actions_for(fse, "overwrite")
    err = capsys.readouterr().err
    assert "keeping existing stored password" in err


def test_build_fs_requests_overwrite_with_credential_sets_password() -> None:
    fse = _make_fs_entry()
    actions = _fs_actions_for(fse, "overwrite")

    ie._build_fs_requests([fse], {("10.0.0.10", "admin"): "new-pw"}, actions, _PLANS_BY_NAME)

    assert isinstance(fse.request, FileServerUpdateRequest)
    assert fse.request.login_password == "new-pw"


def test_build_fs_requests_skip_and_error_entries_untouched() -> None:
    fse_skip = _make_fs_entry(host_ip="10.0.0.11")
    fse_error = _make_fs_entry(host_ip="10.0.0.12", parse_error="host_ip is required")
    actions = {**_fs_actions_for(fse_skip, "skip"), **_fs_actions_for(fse_error, "error")}

    ie._build_fs_requests(
        [fse_skip, fse_error], {("10.0.0.11", "admin"): "pw"}, actions, _PLANS_BY_NAME
    )

    assert fse_skip.request is None
    assert fse_error.request is None


# ── _execute_one ──────────────────────────────────────────────────────────────


def _machine_req(name: str = "Daily Backup") -> MachinePlanCreateRequest:
    return MachinePlanCreateRequest(name=name, retention=_RETENTION, schedule=_SCHEDULE)


async def test_execute_one_error_action_returns_failed_with_parse_error() -> None:
    entry = _make_import_entry(parse_error="bad retention")
    apm = make_fake_apm()

    result = await ie._execute_one(apm, entry, "error", None)

    assert (result.action, result.result, result.error_msg) == ("error", "failed", "bad retention")


async def test_execute_one_skip_action_returns_skipped() -> None:
    entry = _make_import_entry(request=_machine_req())
    apm = make_fake_apm()

    result = await ie._execute_one(apm, entry, "skip", _MACHINE_PLAN_UUID)

    assert (result.action, result.result, result.error_msg) == ("skip", "skipped", "")


async def test_execute_one_missing_request_returns_failed() -> None:
    entry = _make_import_entry(request=None)
    apm = make_fake_apm()

    result = await ie._execute_one(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("failed", "no request")


async def test_execute_one_create_dispatches_by_request_type() -> None:
    """Each request type creates through its own SDK collection."""
    apm = make_fake_apm()
    apm.machine.plans.create = AsyncMock()
    apm.m365.plans.create = AsyncMock()
    apm.retirement_plans.create = AsyncMock()
    apm.tiering_plans.create = AsyncMock()

    machine_req = _machine_req()
    m365_req = M365PlanCreateRequest(
        name="M365 Daily Backup", retention=_RETENTION, schedule=_SCHEDULE
    )
    retirement_req = RetirementPlanCreateRequest(name="Compliance Retention", retention_days=365)
    tiering_req = TieringPlanCreateRequest(
        name="Tier Old Versions",
        tiering_after_days=30,
        destination=make_remote_storage(),
        daily_check_time=time(20, 0),
    )

    for req in (machine_req, m365_req, retirement_req, tiering_req):
        entry = _make_import_entry(request=req)
        result = await ie._execute_one(apm, entry, "create", None)
        assert (result.result, result.error_msg) == ("ok", "")

    apm.machine.plans.create.assert_awaited_once_with(machine_req)
    apm.m365.plans.create.assert_awaited_once_with(m365_req)
    apm.retirement_plans.create.assert_awaited_once_with(retirement_req)
    apm.tiering_plans.create.assert_awaited_once_with(tiering_req)


async def test_execute_one_overwrite_updates_with_existing_id() -> None:
    apm = make_fake_apm()
    apm.machine.plans.update = AsyncMock()
    req = _machine_req()
    entry = _make_import_entry(request=req)

    result = await ie._execute_one(apm, entry, "overwrite", _MACHINE_PLAN_UUID)

    assert (result.result, result.error_msg) == ("ok", "")
    apm.machine.plans.update.assert_awaited_once_with(_MACHINE_PLAN_UUID, req)


async def test_execute_one_resolved_name_replaces_uuid_in_request_name() -> None:
    """When name_or_id was a UUID, the update request carries the real display name so the
    overwrite does not rename the plan."""
    apm = make_fake_apm()
    apm.machine.plans.update = AsyncMock()
    req = _machine_req(name=_MACHINE_PLAN_UUID)
    entry = _make_import_entry(
        name=_MACHINE_PLAN_UUID, request=req, resolved_name="Daily Backup"
    )

    result = await ie._execute_one(apm, entry, "overwrite", _MACHINE_PLAN_UUID)

    assert result.result == "ok"
    sent_req = apm.machine.plans.update.await_args.args[1]
    assert sent_req.name == "Daily Backup"
    assert req.name == _MACHINE_PLAN_UUID  # original request is not mutated


async def test_execute_one_plan_name_conflict_maps_to_failed() -> None:
    apm = make_fake_apm()
    apm.machine.plans.create = AsyncMock(
        side_effect=PlanNameConflictError(
            "name taken", resource_type="ProtectionPlan", resource_id="Daily Backup"
        )
    )
    entry = _make_import_entry(request=_machine_req())

    result = await ie._execute_one(apm, entry, "create", None)

    assert result.result == "failed"
    assert result.error_msg == "name conflict: name taken"


async def test_execute_one_apm_error_maps_to_failed() -> None:
    apm = make_fake_apm()
    apm.machine.plans.create = AsyncMock(side_effect=APMError("backend busy"))
    entry = _make_import_entry(request=_machine_req())

    result = await ie._execute_one(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("failed", "backend busy")


# ── _execute_one_fs ───────────────────────────────────────────────────────────


def _fs_add_request() -> FileServerAddRequest:
    return FileServerAddRequest(
        namespace="ns-apm-server-01",
        host_ip="10.0.0.10",
        server_type=FileServerType.SMB,
        plan_id=_MACHINE_PLAN_UUID,
        login_user="admin",
        login_password="fs-pw",
    )


async def test_execute_one_fs_create_calls_add_file_server() -> None:
    apm = make_fake_apm()
    apm.machine.workloads.add_file_server = AsyncMock()
    entry = _make_fs_entry()
    entry.request = _fs_add_request()

    result = await ie._execute_one_fs(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("ok", "")
    apm.machine.workloads.add_file_server.assert_awaited_once_with(entry.request)


async def test_execute_one_fs_overwrite_calls_update_file_server() -> None:
    apm = make_fake_apm()
    apm.machine.workloads.update_file_server = AsyncMock()
    existing_wl = make_machine_workload(
        workload_type=MachineWorkloadType.FS, fs_config=make_file_server_config()
    )
    entry = _make_fs_entry()
    entry.request = FileServerUpdateRequest(
        host_ip="10.0.0.10", login_user="admin", login_password=None
    )

    result = await ie._execute_one_fs(apm, entry, "overwrite", existing_wl)

    assert (result.result, result.error_msg) == ("ok", "")
    apm.machine.workloads.update_file_server.assert_awaited_once_with(existing_wl, entry.request)


async def test_execute_one_fs_overwrite_without_existing_workload_fails() -> None:
    apm = make_fake_apm()
    entry = _make_fs_entry()
    entry.request = FileServerUpdateRequest(
        host_ip="10.0.0.10", login_user="admin", login_password=None
    )

    result = await ie._execute_one_fs(apm, entry, "overwrite", None)

    assert (result.result, result.error_msg) == ("failed", "existing workload not found")


async def test_execute_one_fs_create_with_wrong_request_type_fails() -> None:
    apm = make_fake_apm()
    entry = _make_fs_entry()
    entry.request = FileServerUpdateRequest(
        host_ip="10.0.0.10", login_user="admin", login_password=None
    )

    result = await ie._execute_one_fs(apm, entry, "create", None)

    assert (result.result, result.error_msg) == (
        "failed", "internal error: add request not built"
    )


async def test_execute_one_fs_duplicate_workload_error() -> None:
    apm = make_fake_apm()
    apm.machine.workloads.add_file_server = AsyncMock(
        side_effect=DuplicateWorkloadError(
            "already registered", resource_type="file_server", resource_id="10.0.0.10"
        )
    )
    entry = _make_fs_entry()
    entry.request = _fs_add_request()

    result = await ie._execute_one_fs(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("failed", "duplicate: already registered")


async def test_execute_one_fs_error_and_skip_actions() -> None:
    apm = make_fake_apm()
    entry_err = _make_fs_entry(parse_error="host_ip is required")
    entry_skip = _make_fs_entry()

    res_err = await ie._execute_one_fs(apm, entry_err, "error", None)
    res_skip = await ie._execute_one_fs(apm, entry_skip, "skip", None)

    assert (res_err.result, res_err.error_msg) == ("failed", "host_ip is required")
    assert (res_skip.result, res_skip.error_msg) == ("skipped", "")


# ── _execute_one_rs ───────────────────────────────────────────────────────────


def _rs_add_req() -> GenericS3StorageAddRequest:
    return GenericS3StorageAddRequest(
        access_key="AK", secret_key="SK",
        vault_name="my-bucket", endpoint="https://s3.example.com:443",
    )


async def test_execute_one_rs_create_returns_key_and_storage() -> None:
    created = make_remote_storage()
    apm = make_fake_apm()
    apm.remote_storages.add = AsyncMock(
        return_value=RemoteStorageAddResult(
            storage=created, encryption_key="NEWKEY123", relink_warning="relink pending"
        )
    )
    entry = _make_rs_entry()
    entry.request = _rs_add_req()

    result = await ie._execute_one_rs(apm, entry, "create", None)

    assert (result.action, result.result) == ("create", "ok")
    assert result.error_msg == "relink pending"
    assert result.issued_encryption_key == "NEWKEY123"
    assert result.created_storage is created
    apm.remote_storages.add.assert_awaited_once_with(entry.request)


async def test_execute_one_rs_overwrite_calls_update() -> None:
    existing = make_remote_storage()
    apm = make_fake_apm()
    apm.remote_storages.update = AsyncMock()
    entry = _make_rs_entry()
    entry.request = RemoteStorageUpdateRequest(access_key="AK", secret_key="SK")

    result = await ie._execute_one_rs(apm, entry, "overwrite", existing)

    assert (result.result, result.error_msg) == ("ok", "")
    apm.remote_storages.update.assert_awaited_once_with(existing, entry.request)


async def test_execute_one_rs_wrong_request_types_fail() -> None:
    apm = make_fake_apm()
    entry_create = _make_rs_entry()
    entry_create.request = RemoteStorageUpdateRequest(access_key="AK", secret_key="SK")
    entry_update = _make_rs_entry()
    entry_update.request = _rs_add_req()

    res_create = await ie._execute_one_rs(apm, entry_create, "create", None)
    res_update = await ie._execute_one_rs(
        apm, entry_update, "overwrite", make_remote_storage()
    )

    assert res_create.error_msg == "internal error: wrong request type for create"
    assert res_update.error_msg == "internal error: wrong request type for update"


async def test_execute_one_rs_overwrite_without_existing_storage_fails() -> None:
    apm = make_fake_apm()
    entry = _make_rs_entry()
    entry.request = RemoteStorageUpdateRequest(access_key="AK", secret_key="SK")

    result = await ie._execute_one_rs(apm, entry, "overwrite", None)

    assert (result.result, result.error_msg) == ("failed", "existing storage not found")


@pytest.mark.parametrize(
    ("exc", "expected_msg"),
    [
        (
            RemoteStorageConflictError(
                "vault registered", resource_type="RemoteStorage", resource_id="my-bucket"
            ),
            "conflict: vault registered",
        ),
        (
            RemoteStorageInUseError(
                "assigned to plans", resource_type="RemoteStorage",
                resource_id="123e4567-e89b-12d3-a456-426614174030",
            ),
            "in use: assigned to plans",
        ),
        (
            RemoteStorageUnmanagedCatalogError(
                "unmanaged catalogs", vault_name="my-bucket", catalog_count=3
            ),
            "unmanaged catalogs (3) in vault 'my-bucket'; "
            "re-add manually via the SDK and pass unmanaged_retirement_plan",
        ),
        (APMError("backend busy"), "backend busy"),
    ],
    ids=["conflict", "in-use", "unmanaged-catalog", "apm-error"],
)
async def test_execute_one_rs_error_mapping(exc: APMError, expected_msg: str) -> None:
    apm = make_fake_apm()
    apm.remote_storages.add = AsyncMock(side_effect=exc)
    entry = _make_rs_entry()
    entry.request = _rs_add_req()

    result = await ie._execute_one_rs(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("failed", expected_msg)


async def test_execute_one_rs_error_and_skip_actions() -> None:
    apm = make_fake_apm()
    entry_err = _make_rs_entry(parse_error="unrecognized storage_type 'tape'")
    entry_skip = _make_rs_entry()

    res_err = await ie._execute_one_rs(apm, entry_err, "error", None)
    res_skip = await ie._execute_one_rs(apm, entry_skip, "skip", None)

    assert (res_err.result, res_err.error_msg) == ("failed", "unrecognized storage_type 'tape'")
    assert (res_skip.result, res_skip.error_msg) == ("skipped", "")


# ── _parse_fs_entries ─────────────────────────────────────────────────────────


def _fs_raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "host_ip": "10.0.0.10",
        "backup_server_ref": "server-1",
        "plan_ref": "plan-1",
        "login_user": "admin",
    }
    raw.update(overrides)
    return raw


def test_parse_fs_entries_happy_path_resolves_namespace_and_plan() -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")
    data = {"file_servers": [_fs_raw()]}

    entries = ie._parse_fs_entries(
        data, _PLANS_BY_NAME, {"plan-1": "Daily Backup"}, {"server-1": bs}
    )

    assert len(entries) == 1
    fse = entries[0]
    assert fse.parse_error is None
    assert fse.host_ip == "10.0.0.10"
    assert fse.resolved_namespace == "ns-apm-server-01"
    assert fse.plan_name == "Daily Backup"


@pytest.mark.parametrize(
    ("raw", "expected_error"),
    [
        (_fs_raw(host_ip=""), "host_ip is required"),
        (_fs_raw(backup_server_ref=""), "backup_server_ref is required"),
        (_fs_raw(plan_ref=""), "plan_ref is required"),
        (
            _fs_raw(plan_ref="plan-99"),
            "plan_ref 'plan-99' not found in protection_plans section",
        ),
    ],
    ids=["no-host-ip", "no-bs-ref", "no-plan-ref", "unknown-plan-ref"],
)
def test_parse_fs_entries_field_errors(raw: dict[str, Any], expected_error: str) -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")

    entries = ie._parse_fs_entries(
        {"file_servers": [raw]}, _PLANS_BY_NAME, {"plan-1": "Daily Backup"}, {"server-1": bs}
    )

    assert entries[0].parse_error == expected_error


def test_parse_fs_entries_plan_not_on_server() -> None:
    bs = make_backup_server(namespace="ns-apm-server-01")

    entries = ie._parse_fs_entries(
        {"file_servers": [_fs_raw()]}, {}, {"plan-1": "Daily Backup"}, {"server-1": bs}
    )

    assert entries[0].parse_error == (
        "plan 'Daily Backup' (plan_ref='plan-1') not found on this server"
    )


def test_parse_fs_entries_unresolved_backup_server_ref() -> None:
    entries = ie._parse_fs_entries(
        {"file_servers": [_fs_raw()]}, _PLANS_BY_NAME, {"plan-1": "Daily Backup"}, {}
    )

    assert entries[0].parse_error == (
        "backup_server_ref 'server-1' not found (check reference resolution errors above)"
    )
    assert entries[0].resolved_namespace == ""


# ── _parse_rs_entries ─────────────────────────────────────────────────────────


def _rs_yaml_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ref_key": "storage-1",
        "name_or_id": "tiering-remote",
        "endpoint": "https://s3.example.com:443",
        "storage_type": "s3_compatible",
        "encryption_enabled": False,
        "vault_name": "my-bucket",
        "trust_self_signed": True,
    }
    entry.update(overrides)
    return entry


def test_parse_rs_entries_happy_path() -> None:
    data = {"remote_storages": [_rs_yaml_entry()]}

    entries = ie._parse_rs_entries(data, _RS_CREDS)

    assert len(entries) == 1
    rse = entries[0]
    assert rse.parse_error is None
    assert rse.name_or_id == "tiering-remote"
    assert rse.ref_key == "storage-1"
    assert rse.endpoint == "https://s3.example.com:443"
    assert rse.vault_name == "my-bucket"
    assert rse.storage_type_str == "s3_compatible"


def test_parse_rs_entries_no_credential_row_skips_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = {"remote_storages": [_rs_yaml_entry(vault_name="other-bucket")]}

    entries = ie._parse_rs_entries(data, _RS_CREDS)

    assert entries == []
    err = capsys.readouterr().err
    assert "has no matching row in storage-credentials file — skipping" in err


def test_parse_rs_entries_unknown_storage_type_is_parse_error() -> None:
    creds = {("tape", "https://s3.example.com:443", "my-bucket"): {
        "access_key": "AK", "secret_key": "SK", "relink_encryption_key": "",
    }}
    data = {"remote_storages": [_rs_yaml_entry(storage_type="tape")]}

    entries = ie._parse_rs_entries(data, creds)

    assert len(entries) == 1
    assert entries[0].parse_error == "unrecognized storage_type 'tape'"


def test_parse_rs_entries_non_importable_type_skips_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    creds = {("azure_blob", "https://s3.example.com:443", "my-bucket"): {
        "access_key": "AK", "secret_key": "SK", "relink_encryption_key": "",
    }}
    data = {"remote_storages": [_rs_yaml_entry(storage_type="azure_blob")]}

    entries = ie._parse_rs_entries(data, creds)

    assert entries == []
    err = capsys.readouterr().err
    assert "not supported for import, skipping" in err


# ── _build_rs_add_request ─────────────────────────────────────────────────────


_RS_REQ_KWARGS: dict[str, Any] = {
    "vault_name": "my-bucket",
    "endpoint": "https://s3.example.com:443",
    "access_key": "AK",
    "secret_key": "SK",
    "relink_key": "RK",
    "encryption_enabled": True,
    "trust_self_signed": True,
}


@pytest.mark.parametrize(
    ("storage_type", "expected"),
    [
        (
            RemoteStorageType.ACTIVE_PROTECT_VAULT,
            APVStorageAddRequest(
                access_key="AK", secret_key="SK",
                endpoint="https://s3.example.com:443",
                encryption_enabled=True, relink_encryption_key="RK",
                trust_self_signed=True,
            ),
        ),
        (
            RemoteStorageType.S3_COMPATIBLE,
            GenericS3StorageAddRequest(
                access_key="AK", secret_key="SK",
                vault_name="my-bucket", endpoint="https://s3.example.com:443",
                encryption_enabled=True, relink_encryption_key="RK",
                trust_self_signed=True,
            ),
        ),
        (
            RemoteStorageType.AMAZON_S3,
            AmazonS3StorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
        (
            RemoteStorageType.AMAZON_S3_CHINA,
            AmazonS3ChinaStorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
        (
            RemoteStorageType.C2_OBJECT_STORAGE,
            C2ObjectStorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
        (
            RemoteStorageType.WASABI,
            WasabiCloudStorageAddRequest(
                access_key="AK", secret_key="SK", vault_name="my-bucket",
                encryption_enabled=True, relink_encryption_key="RK",
            ),
        ),
    ],
    ids=["apv", "s3-compatible", "amazon-s3", "amazon-s3-china", "c2", "wasabi"],
)
def test_build_rs_add_request_dispatch(
    storage_type: RemoteStorageType, expected: Any
) -> None:
    """Each importable storage type builds its own request type with the right fields."""
    result = ie._build_rs_add_request(storage_type, **_RS_REQ_KWARGS)

    assert type(result) is type(expected)
    assert result == expected


# ── _parse_m365_rule_entries error paths ──────────────────────────────────────


_M365_PLANS_BY_NAME = {"M365 Daily Backup": _M365_PLAN_UUID}
_PLAN_NAME_BY_REF = {"plan-2": "M365 Daily Backup"}


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


# ── _autodetect_and_load_credentials ──────────────────────────────────────────


def _write_fs_creds(path: Path) -> None:
    path.write_text("endpoint,login_user,password\n10.0.0.10,admin,fs-pw\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _write_rs_creds(path: Path) -> None:
    path.write_text(
        "storage_type,endpoint,vault_name,access_key,secret_key,relink_encryption_key\n"
        "s3_compatible,https://s3.example.com:443,my-bucket,AK,SK,\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def test_autodetect_credentials_nothing_found_returns_none_tuple(tmp_path: Path) -> None:
    input_path = str(tmp_path / "config.yaml")

    result = ie._autodetect_and_load_credentials(input_path, None, None)

    assert result == (None, None, None)


def test_autodetect_credentials_discovers_sibling_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path = tmp_path / "config.yaml"
    _write_fs_creds(tmp_path / "config.fs-credentials.csv")
    _write_rs_creds(tmp_path / "config.storage-credentials.csv")

    result = ie._autodetect_and_load_credentials(str(input_path), None, None)

    assert result is not None
    fs_creds, rs_creds, rs_path = result
    assert fs_creds == {("10.0.0.10", "admin"): "fs-pw"}
    assert rs_creds == {
        ("s3_compatible", "https://s3.example.com:443", "my-bucket"): {
            "access_key": "AK", "secret_key": "SK", "relink_encryption_key": "",
        }
    }
    assert rs_path == str(tmp_path / "config.storage-credentials.csv")
    err = capsys.readouterr().err
    assert "Using auto-detected FS credentials:" in err
    assert "Using auto-detected storage credentials:" in err


def test_autodetect_credentials_explicit_fs_load_error_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("wrong,header\n", encoding="utf-8")

    result = ie._autodetect_and_load_credentials(
        str(tmp_path / "config.yaml"), str(bad), None
    )

    assert result is None
    assert "Error loading fs-credentials file" in capsys.readouterr().err


def test_autodetect_credentials_explicit_storage_load_error_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = ie._autodetect_and_load_credentials(
        str(tmp_path / "config.yaml"), None, str(tmp_path / "missing.csv")
    )

    assert result is None
    assert "Error loading storage-credentials file" in capsys.readouterr().err


# ── _fetch_import_index ───────────────────────────────────────────────────────


async def test_fetch_import_index_returns_all_five_lists() -> None:
    bs = make_backup_server()
    rs = make_remote_storage()
    machine_stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID)
    m365_stub = make_protection_plan(
        plan_id=_M365_PLAN_UUID, name="M365 Daily Backup", category=WorkloadCategory.M365
    )
    fs_wl = make_machine_workload(
        workload_type=MachineWorkloadType.FS, fs_config=make_file_server_config()
    )
    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([bs], 1))
    apm.remote_storages.list = AsyncMock(return_value=([rs], 1))
    apm.machine.plans.list = AsyncMock(return_value=([machine_stub], 1))
    apm.m365.plans.list = AsyncMock(return_value=([m365_stub], 1))
    apm.machine.workloads.list = AsyncMock(return_value=([fs_wl], 1))

    bs_list, rs_list, machine_stubs, m365_stubs, fs_wls = await ie._fetch_import_index(apm)

    assert bs_list == [bs]
    assert rs_list == [rs]
    assert machine_stubs == [machine_stub]
    assert m365_stubs == [m365_stub]
    assert fs_wls == [fs_wl]
    assert apm.machine.workloads.list.await_args.kwargs["workload_types"] == [
        MachineWorkloadType.FS
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


# ── _print_dry_run_plan ───────────────────────────────────────────────────────


def test_print_dry_run_plan_table_and_counts(capsys: pytest.CaptureFixture[str]) -> None:
    entry = _make_import_entry(name="Daily Backup")
    fse = _make_fs_entry()
    rse = _make_rs_entry()
    plan_actions: dict[str, tuple[str, str | None]] = {
        "protection-plan:Daily Backup": ("create", None)
    }
    fs_actions = {"10.0.0.10:ns-apm-server-01:Daily Backup": "overwrite"}
    rs_actions = {ie._rs_key(rse): "skip"}
    m365_dry = [(f"{_TENANT_UUID}:server-1", "m365_user_rule", "error")]

    n_create, n_overwrite, n_error = ie._print_dry_run_plan(
        [entry], [fse], [rse], plan_actions, fs_actions, rs_actions, m365_dry
    )

    assert (n_create, n_overwrite, n_error) == (1, 1, 1)
    captured = capsys.readouterr()
    plan_line = next(ln for ln in captured.out.splitlines() if "Daily Backup" in ln and "machine" in ln)
    assert "create" in plan_line
    fs_line = next(ln for ln in captured.out.splitlines() if "10.0.0.10" in ln)
    assert "file_server" in fs_line
    assert "overwrite" in fs_line
    rs_line = next(ln for ln in captured.out.splitlines() if "tiering-remote" in ln)
    assert "remote_storage" in rs_line
    assert "skip" in rs_line
    assert "1 to create, 1 to overwrite, 1 to skip, 1 error." in captured.err


# ── run_import ────────────────────────────────────────────────────────────────


def _write_import_yaml(tmp_path: Path, data: dict[str, Any]) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"version": 1, **data}), encoding="utf-8")
    return str(p)


def _machine_plan_yaml(**overrides: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "ref_key": "plan-1",
        "name_or_id": "Daily Backup",
        "type": "machine",
        "retention": {"type": "keep_days", "days": 14},
        "schedule": {"frequency": "daily", "start_time": "02:00", "weekdays": []},
    }
    d.update(overrides)
    return d


async def test_run_import_full_pipeline_happy_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_interrupt: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A YAML covering every section runs the whole 7-phase pipeline: RS create with
    encryption-key write-back, deferred plan/tiering resolution against the newly created
    storage, plan overwrite/create, FS create, and M365 rule + collab application."""
    input_path = _write_import_yaml(tmp_path, {
        "backup_servers": [{"ref_key": "server-1", "name_or_id": "apm-server-01"}],
        "remote_storages": [_rs_yaml_entry()],
        "protection_plans": [
            _machine_plan_yaml(backup_copy={
                "destination_type": "remote_storage",
                "destination_ref": "storage-1",
                "retention": {"type": "keep_days", "days": 7},
                "schedule": {"frequency": "after_backup", "start_time": None, "weekdays": []},
            }),
            {
                "ref_key": "plan-2",
                "name_or_id": "M365 Daily Backup",
                "type": "m365",
                "retention": {"type": "keep_versions", "versions": 10},
                "schedule": {"frequency": "daily", "start_time": "03:00", "weekdays": []},
            },
        ],
        "retirement_plans": [{"name_or_id": "Compliance Retention", "retention_days": 365}],
        "tiering_plans": [{
            "name_or_id": "Tier Old Versions",
            "tiering_after_days": 30,
            "destination_ref": "storage-1",
            "daily_check_time": "20:00",
        }],
        "file_servers": [_fs_raw()],
        "saas_tenants": [{"ref_key": "tenant-1", "tenant_id": _TENANT_UUID}],
        "m365_auto_backup_rules": [{
            "tenant_ref": "tenant-1",
            "user_rules": [{
                "backup_server_ref": "server-1",
                "plan_ref": "plan-2",
                "exchange_groups": [_GROUP_UUID],
            }],
            "collab_services": {
                "sharepoint": {"backup_server_ref": "server-1", "plan_ref": "plan-2"},
            },
        }],
    })
    _write_fs_creds(tmp_path / "config.fs-credentials.csv")
    rs_creds_path = tmp_path / "config.storage-credentials.csv"
    _write_rs_creds(rs_creds_path)

    bs = make_backup_server(name="apm-server-01", namespace="ns-apm-server-01")
    machine_stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    m365_stub = make_protection_plan(
        plan_id=_M365_PLAN_UUID, name="M365 Daily Backup", category=WorkloadCategory.M365
    )
    created_rs = make_remote_storage(name="tiering-remote")

    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([bs], 1))
    apm.remote_storages.list = AsyncMock(return_value=([], 0))
    apm.machine.plans.list = AsyncMock(return_value=([machine_stub], 1))
    # Second call is the Phase 7 refresh that must see the plan created in Phase 6.
    apm.m365.plans.list = AsyncMock(side_effect=[([], 0), ([m365_stub], 1)])
    apm.machine.workloads.list = AsyncMock(return_value=([], 0))
    apm.retirement_plans.get_by_name = AsyncMock(side_effect=ResourceNotFoundError(
        "not found", resource_type="RetirementPlan", resource_id="Compliance Retention"
    ))
    apm.tiering_plans.get_by_name = AsyncMock(side_effect=ResourceNotFoundError(
        "not found", resource_type="TieringPlan", resource_id="Tier Old Versions"
    ))
    apm.remote_storages.add = AsyncMock(return_value=RemoteStorageAddResult(
        storage=created_rs, encryption_key="NEWKEY123", relink_warning=None
    ))
    apm.machine.plans.update = AsyncMock()
    apm.m365.plans.create = AsyncMock()
    apm.retirement_plans.create = AsyncMock()
    apm.tiering_plans.create = AsyncMock()
    apm.machine.workloads.add_file_server = AsyncMock()
    apm.m365.auto_backup_rules.list = AsyncMock(return_value=_empty_rules_result())
    apm.m365.auto_backup_rules.create = AsyncMock()
    apm.m365.auto_backup_rules.update_collab_settings = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="overwrite", dry_run=False, yes=True, concurrency=2
    )

    assert exit_code == 0
    # Remote storage created, issued key written back to the credentials CSV with a backup.
    apm.remote_storages.add.assert_awaited_once()
    assert "NEWKEY123" in rs_creds_path.read_text(encoding="utf-8")
    bak_files = list(tmp_path.glob("config.storage-credentials.csv.*.bak"))
    assert len(bak_files) == 1
    assert "NEWKEY123" not in bak_files[0].read_text(encoding="utf-8")
    # Existing machine plan overwritten with the backup copy resolved to the new storage.
    update_args = apm.machine.plans.update.await_args.args
    assert update_args[0] == _MACHINE_PLAN_UUID
    assert update_args[1].name == "Daily Backup"
    assert update_args[1].backup_copy is not None
    assert update_args[1].backup_copy.destination is created_rs
    # New M365 / retirement / tiering plans created.
    assert apm.m365.plans.create.await_args.args[0].name == "M365 Daily Backup"
    assert apm.retirement_plans.create.await_args.args[0].retention_days == 365
    tiering_req = apm.tiering_plans.create.await_args.args[0]
    assert tiering_req.tiering_after_days == 30
    assert tiering_req.destination is created_rs
    # File server created with the credential from the CSV.
    fs_req = apm.machine.workloads.add_file_server.await_args.args[0]
    assert fs_req.login_password == "fs-pw"
    assert fs_req.plan_id == _MACHINE_PLAN_UUID
    assert fs_req.namespace == "ns-apm-server-01"
    # M365 rule and collab settings applied against the plan created in Phase 6.
    apm.m365.auto_backup_rules.create.assert_awaited_once_with(
        tenant_id=_TENANT_UUID,
        namespace="ns-apm-server-01",
        plan_id=_M365_PLAN_UUID,
        exchange_group_ids=[_GROUP_UUID],
        onedrive_group_ids=[],
        chat_group_ids=[],
    )
    collab_kwargs = apm.m365.auto_backup_rules.update_collab_settings.await_args.kwargs
    assert collab_kwargs["sharepoint"] == M365CollabServiceSetting(
        plan_id=_M365_PLAN_UUID, namespace="ns-apm-server-01"
    )
    assert collab_kwargs["group_exchange"] is None
    captured = capsys.readouterr()
    assert "succeeded, 0 failed." in captured.err


async def test_run_import_dry_run_prints_plan_without_executing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = _write_import_yaml(
        tmp_path, {"protection_plans": [_machine_plan_yaml()]}
    )
    apm = make_fake_apm()
    apm.machine.plans.create = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=True, yes=True
    )

    assert exit_code == 0
    apm.machine.plans.create.assert_not_awaited()
    captured = capsys.readouterr()
    assert "1 to create, 0 to overwrite, 0 to skip, 0 errors." in captured.err


async def test_run_import_confirmation_declined_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = _write_import_yaml(
        tmp_path, {"protection_plans": [_machine_plan_yaml()]}
    )
    apm = make_fake_apm()
    apm.machine.plans.create = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    async def _decline(message: str) -> bool:
        return False

    monkeypatch.setattr(ie, "prompt_yes_no", _decline)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=False
    )

    assert exit_code == 0
    apm.machine.plans.create.assert_not_awaited()
    assert "Aborted." in capsys.readouterr().err


async def test_run_import_all_skips_reports_nothing_to_do(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = _write_import_yaml(
        tmp_path, {"protection_plans": [_machine_plan_yaml()]}
    )
    stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    apm = make_fake_apm()
    apm.machine.plans.list = AsyncMock(return_value=([stub], 1))
    apm.machine.plans.update = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=True
    )

    assert exit_code == 0
    apm.machine.plans.update.assert_not_awaited()
    assert "Nothing to do." in capsys.readouterr().err


async def test_run_import_failed_create_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_interrupt: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = _write_import_yaml(
        tmp_path, {"protection_plans": [_machine_plan_yaml()]}
    )
    apm = make_fake_apm()
    apm.machine.plans.create = AsyncMock(side_effect=APMError("backend busy"))
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=True
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "0 succeeded, 1 failed." in captured.err


async def test_run_import_unresolved_backup_server_ref_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = _write_import_yaml(tmp_path, {
        "backup_servers": [{"ref_key": "server-1", "name_or_id": "no-such-server"}],
        "file_servers": [_fs_raw()],
    })
    apm = make_fake_apm()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=True
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "reference resolution error" in err
    assert "Aborting import due to unresolved references." in err


async def test_run_import_unresolved_remote_storage_ref_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An RS ref entry with no credentials row and no live match aborts the import."""
    input_path = _write_import_yaml(tmp_path, {
        "remote_storages": [{"ref_key": "storage-1", "name_or_id": "no-such-storage"}],
    })
    apm = make_fake_apm()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=True
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "1 unresolved remote storage reference(s):" in err


async def test_run_import_invalid_yaml_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("version: 2\n", encoding="utf-8")

    exit_code = await ie.run_import(str(p), on_conflict="skip", dry_run=False, yes=True)

    assert exit_code == 1
    assert "Error loading" in capsys.readouterr().err


async def test_run_import_no_matching_entries_returns_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = _write_import_yaml(
        tmp_path, {"protection_plans": [_machine_plan_yaml()]}
    )
    apm = make_fake_apm()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=True,
        import_types={"retirement-plan"},
    )

    assert exit_code == 0
    assert "No matching entries found" in capsys.readouterr().err


# ── main() argument wiring ────────────────────────────────────────────────────


_ALL_IMPORT_TYPES = {
    "remote-storage", "protection-plan", "retirement-plan",
    "tiering-plan", "file-server", "m365-auto-backup-rule",
}


@pytest.fixture
def wired_main(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Patch run_export/run_import (and run_main) so main() only records the call args."""
    run_export = MagicMock(name="run_export")
    run_import = MagicMock(name="run_import")
    monkeypatch.setattr(ie, "run_export", run_export)
    monkeypatch.setattr(ie, "run_import", run_import)
    monkeypatch.setattr(ie, "run_main", MagicMock(name="run_main"))
    return run_export, run_import


def test_main_export_defaults(
    monkeypatch: pytest.MonkeyPatch, wired_main: tuple[MagicMock, MagicMock]
) -> None:
    run_export, _ = wired_main
    monkeypatch.setattr(sys, "argv", ["apm_import_export.py", "export", "out.yaml"])

    ie.main()

    assert run_export.call_args == call("out.yaml", 5, True, False)


def test_main_export_flags(
    monkeypatch: pytest.MonkeyPatch, wired_main: tuple[MagicMock, MagicMock]
) -> None:
    run_export, _ = wired_main
    monkeypatch.setattr(sys, "argv", [
        "apm_import_export.py", "export", "out.yaml",
        "--concurrency", "3", "--no-credentials-template", "--yes",
    ])

    ie.main()

    assert run_export.call_args == call("out.yaml", 3, False, True)


def test_main_import_defaults_expand_type_all(
    monkeypatch: pytest.MonkeyPatch, wired_main: tuple[MagicMock, MagicMock]
) -> None:
    _, run_import = wired_main
    monkeypatch.setattr(sys, "argv", ["apm_import_export.py", "import", "config.yaml"])

    ie.main()

    assert run_import.call_args == call(
        "config.yaml", "skip", False, False, 5, None, None, _ALL_IMPORT_TYPES
    )


def test_main_import_single_type_and_flags(
    monkeypatch: pytest.MonkeyPatch, wired_main: tuple[MagicMock, MagicMock]
) -> None:
    _, run_import = wired_main
    monkeypatch.setattr(sys, "argv", [
        "apm_import_export.py", "import", "config.yaml",
        "--type", "file-server", "--on-conflict", "overwrite",
        "--dry-run", "--yes", "--concurrency", "2",
        "--fs-credentials", "fs.csv", "--storage-credentials", "rs.csv",
    ])

    ie.main()

    assert run_import.call_args == call(
        "config.yaml", "overwrite", True, True, 2, "fs.csv", "rs.csv", {"file-server"}
    )


def test_main_requires_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    wired_main: tuple[MagicMock, MagicMock],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["apm_import_export.py"])

    with pytest.raises(SystemExit) as exc_info:
        ie.main()

    assert exc_info.value.code == 2
    assert "command" in capsys.readouterr().err
