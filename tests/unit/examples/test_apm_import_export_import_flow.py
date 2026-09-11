"""Unit tests for the import pipeline in examples/apm_import_export.py.

Covers per-item execution dispatch, credential auto-detection, the resource index fetch,
dry-run plan rendering, the run_import orchestrator, and main() argument wiring. The
file-server, remote-storage, and M365 auto-backup-rule subtopics have their own sibling
test modules (see test_apm_import_export_import_flow_fs.py,
test_apm_import_export_import_flow_rs.py, test_apm_import_export_import_flow_m365_rules.py);
conflict checking and deferred-RS plan-request building live in
test_apm_import_export_import_flow_conflicts.py.
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
    APMError,
    GWSAutoBackupRuleListResult,
    GWSPlanCreateRequest,
    M365AutoBackupRule,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
    M365PlanCreateRequest,
    MachinePlanCreateRequest,
    MachineWorkloadType,
    PlanNameConflictError,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RemoteStorageAddResult,
    RemoteStorageConflictError,
    ResourceNotFoundError,
    RetentionType,
    RetirementPlanCreateRequest,
    ScheduleFrequency,
    TieringPlanCreateRequest,
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
    apm.gws.plans.create = AsyncMock()
    apm.retirement_plans.create = AsyncMock()
    apm.tiering_plans.create = AsyncMock()

    machine_req = _machine_req()
    m365_req = M365PlanCreateRequest(
        name="M365 Daily Backup", retention=_RETENTION, schedule=_SCHEDULE
    )
    gws_req = GWSPlanCreateRequest(
        name="GWS Daily Backup", retention=_RETENTION, schedule=_SCHEDULE
    )
    retirement_req = RetirementPlanCreateRequest(name="Compliance Retention", retention_days=365)
    tiering_req = TieringPlanCreateRequest(
        name="Tier Old Versions",
        tiering_after_days=30,
        destination=make_remote_storage(),
        daily_check_time=time(20, 0),
    )

    for req in (machine_req, m365_req, gws_req, retirement_req, tiering_req):
        entry = _make_import_entry(request=req)
        result = await ie._execute_one(apm, entry, "create", None)
        assert (result.result, result.error_msg) == ("ok", "")

    apm.machine.plans.create.assert_awaited_once_with(machine_req)
    apm.m365.plans.create.assert_awaited_once_with(m365_req)
    apm.gws.plans.create.assert_awaited_once_with(gws_req)
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


async def test_execute_one_overwrite_dispatches_by_request_type() -> None:
    """Overwrite (update) dispatch for m365/gws/retirement/tiering -- machine's own update
    dispatch is covered by test_execute_one_overwrite_updates_with_existing_id above."""
    apm = make_fake_apm()
    apm.m365.plans.update = AsyncMock()
    apm.gws.plans.update = AsyncMock()
    apm.retirement_plans.update = AsyncMock()
    apm.tiering_plans.update = AsyncMock()

    m365_req = M365PlanCreateRequest(
        name="M365 Daily Backup", retention=_RETENTION, schedule=_SCHEDULE
    )
    gws_req = GWSPlanCreateRequest(
        name="GWS Daily Backup", retention=_RETENTION, schedule=_SCHEDULE
    )
    retirement_req = RetirementPlanCreateRequest(name="Compliance Retention", retention_days=365)
    tiering_req = TieringPlanCreateRequest(
        name="Tier Old Versions",
        tiering_after_days=30,
        destination=make_remote_storage(),
        daily_check_time=time(20, 0),
    )

    for req in (m365_req, gws_req, retirement_req, tiering_req):
        entry = _make_import_entry(request=req)
        result = await ie._execute_one(apm, entry, "overwrite", _MACHINE_PLAN_UUID)
        assert (result.result, result.error_msg) == ("ok", "")

    apm.m365.plans.update.assert_awaited_once_with(_MACHINE_PLAN_UUID, m365_req)
    apm.gws.plans.update.assert_awaited_once_with(_MACHINE_PLAN_UUID, gws_req)
    apm.retirement_plans.update.assert_awaited_once_with(_MACHINE_PLAN_UUID, retirement_req)
    apm.tiering_plans.update.assert_awaited_once_with(_MACHINE_PLAN_UUID, tiering_req)


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


async def test_execute_one_value_error_maps_to_failed() -> None:
    """A bare ValueError from the SDK call (not APMError/PlanNameConflictError) is still
    reported as a failure rather than propagating."""
    apm = make_fake_apm()
    apm.machine.plans.create = AsyncMock(side_effect=ValueError("invalid field combination"))
    entry = _make_import_entry(request=_machine_req())

    result = await ie._execute_one(apm, entry, "create", None)

    assert (result.result, result.error_msg) == ("failed", "invalid field combination")


# ── _status_line / _result_cell ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "error_msg", "ok_warning", "expected"),
    [
        ("ok", "", False, "ok"),
        ("ok", "relink required", True, "ok (warning: relink required)"),
        ("ok", "relink required", False, "ok"),
        ("failed", "backend busy", False, "failed: backend busy"),
        ("skipped", "", False, "skipped"),
    ],
    ids=["ok", "ok-with-warning", "ok-warning-not-requested", "failed", "skipped-fallback"],
)
def test_status_line_formats_by_result(
    result: str, error_msg: str, ok_warning: bool, expected: str
) -> None:
    assert ie._status_line(result, error_msg, ok_warning=ok_warning) == expected


@pytest.mark.parametrize(
    ("result", "error_msg", "ok_warning", "expected"),
    [
        ("ok", "", False, "ok"),
        ("ok", "relink required", True, "ok (warning: relink required)"),
        ("ok", "relink required", False, "ok"),
        ("failed", "backend busy", False, "failed: backend busy"),
        ("skipped", "", False, "skipped"),
    ],
    ids=["ok", "ok-with-warning", "ok-warning-not-requested", "failed", "skipped-fallback"],
)
def test_result_cell_formats_by_result(
    result: str, error_msg: str, ok_warning: bool, expected: str
) -> None:
    assert ie._result_cell(result, error_msg, ok_warning=ok_warning) == expected


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
            "tenant_id": "", "client_id": "", "secret": "",
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


async def test_fetch_import_index_returns_all_six_lists() -> None:
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

    bs_list, rs_list, machine_stubs, m365_stubs, gws_stubs, fs_wls = await ie._fetch_import_index(apm)

    assert bs_list == [bs]
    assert rs_list == [rs]
    assert machine_stubs == [machine_stub]
    assert m365_stubs == [m365_stub]
    assert gws_stubs == []
    assert fs_wls == [fs_wl]
    assert apm.machine.workloads.list.await_args.kwargs["workload_types"] == [
        MachineWorkloadType.FS
    ]


# ── _build_plan_name_by_ref ─────────────────────────────────────────────────────


def test_build_plan_name_by_ref_skips_entry_missing_ref_key_or_name_or_id() -> None:
    data: dict[str, Any] = {
        "protection_plans": [
            {"ref_key": "", "name_or_id": "Daily Backup"},
            {"ref_key": "plan-1", "name_or_id": ""},
        ]
    }

    assert ie._build_plan_name_by_ref(data, []) == {}


def test_build_plan_name_by_ref_resolves_uuid_to_stub_name() -> None:
    stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    data: dict[str, Any] = {
        "protection_plans": [{"ref_key": "plan-1", "name_or_id": _MACHINE_PLAN_UUID}]
    }

    assert ie._build_plan_name_by_ref(data, [stub]) == {"plan-1": "Daily Backup"}


def test_build_plan_name_by_ref_unmatched_uuid_is_omitted() -> None:
    data: dict[str, Any] = {
        "protection_plans": [{"ref_key": "plan-1", "name_or_id": _MACHINE_PLAN_UUID}]
    }

    assert ie._build_plan_name_by_ref(data, []) == {}


def test_build_plan_name_by_ref_uses_name_as_is() -> None:
    data: dict[str, Any] = {
        "protection_plans": [{"ref_key": "plan-1", "name_or_id": "Daily Backup"}]
    }

    assert ie._build_plan_name_by_ref(data, []) == {"plan-1": "Daily Backup"}


# ── _report_parse_errors ─────────────────────────────────────────────────────────


def test_report_parse_errors_prints_every_section_with_blank_line_separators(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every category's parse errors are printed, and a blank line separates each populated
    section from the previous one -- this function is never exercised end-to-end by
    run_import (every scenario there either has no parse errors or aborts earlier)."""
    plan_entry = _make_import_entry(name="Daily Backup", parse_error="bad retention")
    fs_entry = ie._FsEntry(
        host_ip="10.0.0.10", backup_server_ref="server-1", resolved_namespace="ns-1",
        plan_name="Daily Backup", raw={}, parse_error="host_ip is required",
    )
    rs_entry = ie._RsEntry(
        name_or_id="tiering-remote", ref_key="storage-1", endpoint="", vault_name="my-bucket",
        storage_type_str="s3_compatible", raw={}, parse_error="endpoint is required",
    )
    m365_rule = ie._M365RuleEntry(
        tenant_id=_TENANT_UUID, kind="m365_user_rule", backup_server_ref="server-1",
        resolved_namespace="", plan_ref="plan-2", resolved_plan_id="",
        exchange_groups=[], onedrive_groups=[], chat_groups=[], raw={},
        parse_error="plan_ref is required",
    )
    m365_collab = ie._M365CollabEntry(
        tenant_id=_TENANT_UUID, group_exchange=None, mysite=None, sharepoint=None, teams=None,
        parse_error="backup_server_ref is required",
    )
    gws_rule = ie._GWSRuleEntry(
        domain="gwsdemo.example.com", kind="gws_user_rule", backup_server_ref="server-1",
        resolved_namespace="", plan_ref="plan-2", resolved_plan_id="",
        mail_groups=[], calendar_groups=[], contact_groups=[], drive_groups=[], raw={},
        parse_error="plan_ref is required",
    )
    gws_collab = ie._GWSCollabEntry(
        domain="gwsdemo.example.com", shared_drive_specified=True, shared_drive=None,
        shared_drive_parse_error="backup_server_ref is required",
        include_unlicensed_accounts=None, include_archived_accounts=None,
    )

    ie._report_parse_errors(
        [plan_entry], [fs_entry], [rs_entry], [m365_rule], [m365_collab], [gws_rule], [gws_collab],
    )

    err = capsys.readouterr().err
    assert "1 plan parse error:" in err
    assert "bad retention" in err
    assert "1 file server parse error:" in err
    assert "host_ip is required" in err
    assert "1 remote storage parse error:" in err
    assert "endpoint is required" in err
    assert "[m365_user_rule]" in err and "plan_ref is required" in err
    assert "[m365_collab]" in err and "backup_server_ref is required" in err
    assert "[gws_user_rule]" in err
    assert "[gws_collab]" in err
    # Every section after the first (plan/fs/rs/m365/gws) is preceded by exactly one blank
    # separator line -- verifies _report_parse_errors' printed-flag bookkeeping directly.
    lines = err.splitlines()
    section_starts = [
        next(i for i, ln in enumerate(lines) if marker in ln)
        for marker in (
            "plan parse error:", "file server parse error:", "remote storage parse error:",
            "[m365_user_rule]", "[gws_user_rule]",
        )
    ]
    assert section_starts == sorted(section_starts)  # sections appear in the expected order
    assert all(lines[i - 1] == "" for i in section_starts[1:])


# ── _print_dry_run_plan ───────────────────────────────────────────────────────


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
        [entry], [fse], [rse], plan_actions, fs_actions, rs_actions, m365_dry, []
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


def _fs_raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "host_ip": "10.0.0.10",
        "backup_server_ref": "server-1",
        "plan_ref": "plan-1",
        "login_user": "admin",
    }
    raw.update(overrides)
    return raw


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
    apm.m365.plans.list = AsyncMock(return_value=([], 0))
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
    # Phase 7 resolves the M365 rule's plan_id from this create() return value directly
    # (no re-fetch of the plan list), so it must return the real created plan.
    apm.m365.plans.create = AsyncMock(return_value=m365_stub)
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


async def test_run_import_credential_load_error_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed explicit --fs-credentials file aborts the import before any APM
    connection is made (_autodetect_and_load_credentials returning None)."""
    input_path = _write_import_yaml(tmp_path, {})
    bad_creds = tmp_path / "bad.csv"
    bad_creds.write_text("wrong,header\n", encoding="utf-8")

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=True,
        fs_credentials_path=str(bad_creds),
    )

    assert exit_code == 1
    assert "Error loading fs-credentials file" in capsys.readouterr().err


async def test_run_import_saas_and_gws_domain_ref_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Malformed saas_tenants/gws_domains entries print their own ref-map-build warnings."""
    input_path = _write_import_yaml(tmp_path, {
        "saas_tenants": [{"ref_key": "tenant-1"}],  # missing tenant_id
        "gws_domains": [{"ref_key": "domain-1"}],   # missing domain
    })
    apm = make_fake_apm()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(input_path, on_conflict="skip", dry_run=False, yes=True)

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "Warning: saas_tenants: saas_tenants ref_key='tenant-1' missing 'tenant_id'" in err
    assert "Warning: gws_domains: gws_domains ref_key='domain-1' missing 'domain'" in err


async def test_run_import_interrupt_mid_flight_skips_remaining_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupt fired right as Phase 4 begins (before any RS/plan/FS SDK call) causes
    all three to skip rather than proceed -- covers the interrupted.is_set() guard in each
    of _run_rs/_run_plan/_run_fs, which the standalone execute-function tests never exercise
    at the run_import level."""
    input_path = _write_import_yaml(tmp_path, {
        "remote_storages": [_rs_yaml_entry()],
        "protection_plans": [_machine_plan_yaml()],
        "file_servers": [_fs_raw()],
    })
    _write_fs_creds(tmp_path / "config.fs-credentials.csv")
    _write_rs_creds(tmp_path / "config.storage-credentials.csv")

    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([], 0))
    apm.remote_storages.list = AsyncMock(return_value=([], 0))
    apm.machine.plans.list = AsyncMock(return_value=([], 0))
    apm.m365.plans.list = AsyncMock(return_value=([], 0))
    apm.machine.workloads.list = AsyncMock(return_value=([], 0))
    apm.machine.plans.create = AsyncMock()
    apm.remote_storages.add = AsyncMock()
    apm.machine.workloads.add_file_server = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    def _fake_register_interrupt(loop: Any, event: asyncio.Event) -> None:
        event.set()

    monkeypatch.setattr(ie, "register_interrupt", _fake_register_interrupt)
    monkeypatch.setattr(ie, "unregister_interrupt", lambda loop: None)

    exit_code = await ie.run_import(
        input_path, on_conflict="skip", dry_run=False, yes=True, concurrency=2
    )

    assert exit_code == 0
    apm.remote_storages.add.assert_not_awaited()
    apm.machine.plans.create.assert_not_awaited()
    apm.machine.workloads.add_file_server.assert_not_awaited()


async def test_run_import_m365_prefetch_failure_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A failure fetching existing M365 rules during the dry-run prefetch (phase 3c) warns
    per-tenant rather than aborting -- distinct from the export-side and execution-phase
    (_execute_m365_rules) fetch-failure cases, which are tested separately."""
    input_path = _write_import_yaml(tmp_path, {
        "saas_tenants": [{"ref_key": "tenant-1", "tenant_id": _TENANT_UUID}],
        "backup_servers": [{"ref_key": "server-1", "name_or_id": "apm-server-01"}],
        "protection_plans": [{
            "ref_key": "plan-2", "name_or_id": "M365 Daily Backup", "type": "m365",
            "retention": {"type": "keep_versions", "versions": 10},
            "schedule": {"frequency": "daily", "start_time": "03:00", "weekdays": []},
        }],
        "m365_auto_backup_rules": [{
            "tenant_ref": "tenant-1",
            "user_rules": [{"backup_server_ref": "server-1", "plan_ref": "plan-2"}],
        }],
    })
    bs = make_backup_server(name="apm-server-01", namespace="ns-apm-server-01")
    m365_stub = make_protection_plan(
        plan_id=_M365_PLAN_UUID, name="M365 Daily Backup", category=WorkloadCategory.M365
    )
    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([bs], 1))
    apm.remote_storages.list = AsyncMock(return_value=([], 0))
    apm.machine.plans.list = AsyncMock(return_value=([], 0))
    apm.m365.plans.list = AsyncMock(return_value=([m365_stub], 1))
    apm.machine.workloads.list = AsyncMock(return_value=([], 0))
    apm.m365.auto_backup_rules.list = AsyncMock(side_effect=APMError("tenant offline"))
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="overwrite", dry_run=True, yes=True, concurrency=2
    )

    assert exit_code == 0
    err = capsys.readouterr().err
    assert f"could not fetch existing rules for tenant '{_TENANT_UUID}'" in err


def _rs_backed_machine_plan_yaml() -> dict[str, Any]:
    return _machine_plan_yaml(backup_copy={
        "destination_type": "remote_storage",
        "destination_ref": "storage-1",
        "retention": {"type": "keep_days", "days": 7},
        "schedule": {"frequency": "after_backup", "start_time": None, "weekdays": []},
    })


async def test_run_import_rs_creation_fallback_locates_storage_after_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_interrupt: None,
) -> None:
    """When add() fails with a conflict (another client registered the same storage
    concurrently), the fallback re-fetch still locates it by name, so a dependent plan's
    deferred backup_copy destination resolves correctly instead of being left unresolved."""
    input_path = _write_import_yaml(tmp_path, {
        "remote_storages": [_rs_yaml_entry()],
        "protection_plans": [_rs_backed_machine_plan_yaml()],
    })
    _write_rs_creds(tmp_path / "config.storage-credentials.csv")
    machine_stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    found_rs = make_remote_storage(name="tiering-remote")

    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([], 0))
    apm.remote_storages.list = AsyncMock(side_effect=[([], 0), ([found_rs], 1)])
    apm.machine.plans.list = AsyncMock(return_value=([machine_stub], 1))
    apm.m365.plans.list = AsyncMock(return_value=([], 0))
    apm.machine.workloads.list = AsyncMock(return_value=([], 0))
    apm.remote_storages.add = AsyncMock(side_effect=RemoteStorageConflictError(
        "already registered", resource_type="RemoteStorage", resource_id="tiering-remote"
    ))
    apm.machine.plans.update = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="overwrite", dry_run=False, yes=True, concurrency=2
    )

    assert exit_code == 1  # the RS entry itself still failed, even though the plan resolved
    update_args = apm.machine.plans.update.await_args.args
    assert update_args[1].backup_copy is not None
    assert update_args[1].backup_copy.destination is found_rs


async def test_run_import_plan_action_flips_to_error_when_rs_never_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_interrupt: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When RS creation fails and the fallback re-fetch still can't find a match, a dependent
    plan's deferred backup_copy resolution fails too -- its action flips to "error" (via the
    post-RS-creation re-parse) rather than the plan silently going through without its
    backup_copy."""
    input_path = _write_import_yaml(tmp_path, {
        "remote_storages": [_rs_yaml_entry()],
        "protection_plans": [_rs_backed_machine_plan_yaml()],
    })
    _write_rs_creds(tmp_path / "config.storage-credentials.csv")
    machine_stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")

    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([], 0))
    apm.remote_storages.list = AsyncMock(side_effect=[([], 0), ([], 0)])
    apm.machine.plans.list = AsyncMock(return_value=([machine_stub], 1))
    apm.m365.plans.list = AsyncMock(return_value=([], 0))
    apm.machine.workloads.list = AsyncMock(return_value=([], 0))
    apm.remote_storages.add = AsyncMock(side_effect=RemoteStorageConflictError(
        "already registered", resource_type="RemoteStorage", resource_id="tiering-remote"
    ))
    apm.machine.plans.update = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="overwrite", dry_run=False, yes=True, concurrency=2
    )

    assert exit_code == 1
    apm.machine.plans.update.assert_not_awaited()
    err = capsys.readouterr().err
    assert "[error] 'Daily Backup'" in err


async def test_run_import_fs_and_rs_overwrite_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_interrupt: None,
) -> None:
    """When the target server already has a matching FS workload and RS (matched by UUID),
    both are updated rather than created -- no run_import test previously populated
    existing_fs/existing_rs_by_name from a non-empty live list, so this path was only ever
    unit-tested at the helper-function level (_build_fs_requests/_execute_one_fs, RS
    action-selection)."""
    rs_uuid = "123e4567-e89b-12d3-a456-426614174099"
    input_path = _write_import_yaml(tmp_path, {
        "backup_servers": [{"ref_key": "server-1", "name_or_id": "apm-server-01"}],
        "protection_plans": [_machine_plan_yaml()],
        "file_servers": [_fs_raw()],
        "remote_storages": [_rs_yaml_entry(name_or_id=rs_uuid)],
    })
    _write_fs_creds(tmp_path / "config.fs-credentials.csv")
    _write_rs_creds(tmp_path / "config.storage-credentials.csv")

    bs = make_backup_server(name="apm-server-01", namespace="ns-apm-server-01")
    machine_stub = make_protection_plan(plan_id=_MACHINE_PLAN_UUID, name="Daily Backup")
    existing_fs_wl = make_machine_workload(
        workload_type=MachineWorkloadType.FS,
        namespace="ns-apm-server-01",
        fs_config=make_file_server_config(host_ip="10.0.0.10"),
        plan=machine_stub,
    )
    existing_rs = make_remote_storage(storage_id=rs_uuid, name="tiering-remote")

    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([bs], 1))
    apm.remote_storages.list = AsyncMock(return_value=([existing_rs], 1))
    apm.machine.plans.list = AsyncMock(return_value=([machine_stub], 1))
    apm.m365.plans.list = AsyncMock(return_value=([], 0))
    apm.machine.workloads.list = AsyncMock(return_value=([existing_fs_wl], 1))
    apm.machine.plans.update = AsyncMock()
    apm.machine.workloads.update_file_server = AsyncMock()
    apm.remote_storages.update = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="overwrite", dry_run=False, yes=True, concurrency=2
    )

    assert exit_code == 0
    apm.machine.workloads.update_file_server.assert_awaited_once()
    assert apm.machine.workloads.update_file_server.await_args.args[0] is existing_fs_wl
    apm.remote_storages.update.assert_awaited_once()
    assert apm.remote_storages.update.await_args.args[0] is existing_rs


async def test_run_import_gws_full_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_interrupt: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GWS-flavored variant of test_run_import_full_pipeline_happy_path (gws_domains +
    gws_auto_backup_rules instead of saas_tenants + m365_auto_backup_rules) -- no other
    run_import test supplies these sections at all, so the GWS existing-rules prefetch
    (phase 3c) and GWS rule execution (phase 8) were previously unreachable."""
    gws_plan_uuid = "123e4567-e89b-12d3-a456-426614174003"
    input_path = _write_import_yaml(tmp_path, {
        "backup_servers": [{"ref_key": "server-1", "name_or_id": "apm-server-01"}],
        "protection_plans": [{
            "ref_key": "plan-2",
            "name_or_id": "GWS Daily Backup",
            "type": "gws",
            "retention": {"type": "keep_versions", "versions": 10},
            "schedule": {"frequency": "daily", "start_time": "03:00", "weekdays": []},
        }],
        "gws_domains": [{"ref_key": "domain-1", "domain": "gwsdemo.example.com"}],
        "gws_auto_backup_rules": [{
            "domain_ref": "domain-1",
            "user_rules": [{
                "backup_server_ref": "server-1",
                "plan_ref": "plan-2",
                "mail_groups": [_GROUP_UUID],
            }],
            "collab_services": {
                "shared_drive": {"backup_server_ref": "server-1", "plan_ref": "plan-2"},
            },
        }],
    })

    bs = make_backup_server(name="apm-server-01", namespace="ns-apm-server-01")
    gws_stub = make_protection_plan(
        plan_id=gws_plan_uuid, name="GWS Daily Backup", category=WorkloadCategory.GWS
    )

    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([bs], 1))
    apm.remote_storages.list = AsyncMock(return_value=([], 0))
    apm.machine.plans.list = AsyncMock(return_value=([], 0))
    apm.m365.plans.list = AsyncMock(return_value=([], 0))
    apm.gws.plans.list = AsyncMock(return_value=([], 0))
    apm.machine.workloads.list = AsyncMock(return_value=([], 0))
    # Phase 8 resolves the GWS rule's plan_id from this create() return value directly
    # (no re-fetch of the plan list), mirroring the M365 happy-path test.
    apm.gws.plans.create = AsyncMock(return_value=gws_stub)
    apm.gws.auto_backup_rules.list = AsyncMock(return_value=GWSAutoBackupRuleListResult(
        rules=(), shared_drive_setting=None,
        include_unlicensed_accounts=False, include_archived_accounts=False,
    ))
    apm.gws.auto_backup_rules.create = AsyncMock()
    apm.gws.auto_backup_rules.update_collab_settings = AsyncMock()
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="overwrite", dry_run=False, yes=True, concurrency=2
    )

    assert exit_code == 0
    assert apm.gws.plans.create.await_args.args[0].name == "GWS Daily Backup"
    apm.gws.auto_backup_rules.create.assert_awaited_once_with(
        domain="gwsdemo.example.com",
        namespace="ns-apm-server-01",
        plan_id=gws_plan_uuid,
        mail_group_ids=[_GROUP_UUID],
        calendar_group_ids=[],
        contact_group_ids=[],
        drive_group_ids=[],
    )
    collab_kwargs = apm.gws.auto_backup_rules.update_collab_settings.await_args.kwargs
    assert collab_kwargs["shared_drive"].plan_id == gws_plan_uuid
    err = capsys.readouterr().err
    assert "Checking existing GWS auto-backup rules..." in err
    assert "GWS gws_user_rule" in err
    assert "GWS gws_shared_drive" in err


async def test_run_import_gws_prefetch_failure_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """GWS-flavored variant of test_run_import_m365_prefetch_failure_warns: a failure fetching
    existing GWS rules during the dry-run prefetch (phase 3c) warns per-domain rather than
    aborting."""
    input_path = _write_import_yaml(tmp_path, {
        "backup_servers": [{"ref_key": "server-1", "name_or_id": "apm-server-01"}],
        "protection_plans": [{
            "ref_key": "plan-2", "name_or_id": "GWS Daily Backup", "type": "gws",
            "retention": {"type": "keep_versions", "versions": 10},
            "schedule": {"frequency": "daily", "start_time": "03:00", "weekdays": []},
        }],
        "gws_domains": [{"ref_key": "domain-1", "domain": "gwsdemo.example.com"}],
        "gws_auto_backup_rules": [{
            "domain_ref": "domain-1",
            "user_rules": [{"backup_server_ref": "server-1", "plan_ref": "plan-2"}],
        }],
    })
    bs = make_backup_server(name="apm-server-01", namespace="ns-apm-server-01")
    gws_stub = make_protection_plan(
        plan_id="123e4567-e89b-12d3-a456-426614174003", name="GWS Daily Backup",
        category=WorkloadCategory.GWS,
    )
    apm = make_fake_apm()
    apm.backup_servers.list = AsyncMock(return_value=([bs], 1))
    apm.remote_storages.list = AsyncMock(return_value=([], 0))
    apm.machine.plans.list = AsyncMock(return_value=([], 0))
    apm.m365.plans.list = AsyncMock(return_value=([], 0))
    apm.gws.plans.list = AsyncMock(return_value=([gws_stub], 1))
    apm.machine.workloads.list = AsyncMock(return_value=([], 0))
    apm.gws.auto_backup_rules.list = AsyncMock(side_effect=APMError("domain offline"))
    patch_make_client(monkeypatch, ie, apm)

    exit_code = await ie.run_import(
        input_path, on_conflict="overwrite", dry_run=True, yes=True, concurrency=2
    )

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "could not fetch existing rules for domain 'gwsdemo.example.com'" in err


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
    "tiering-plan", "file-server", "m365-auto-backup-rule", "gws-auto-backup-rule",
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

    assert run_export.call_args == call("out.yaml", 5, True, False, profile=None)


def test_main_export_flags(
    monkeypatch: pytest.MonkeyPatch, wired_main: tuple[MagicMock, MagicMock]
) -> None:
    run_export, _ = wired_main
    monkeypatch.setattr(sys, "argv", [
        "apm_import_export.py", "export", "out.yaml",
        "--concurrency", "3", "--no-credentials-template", "--yes", "--profile", "lab",
    ])

    ie.main()

    assert run_export.call_args == call("out.yaml", 3, False, True, profile="lab")


def test_main_import_defaults_expand_type_all(
    monkeypatch: pytest.MonkeyPatch, wired_main: tuple[MagicMock, MagicMock]
) -> None:
    _, run_import = wired_main
    monkeypatch.setattr(sys, "argv", ["apm_import_export.py", "import", "config.yaml"])

    ie.main()

    assert run_import.call_args == call(
        "config.yaml", "skip", False, False, 5, None, None, _ALL_IMPORT_TYPES, profile=None
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
        "--profile", "lab",
    ])

    ie.main()

    assert run_import.call_args == call(
        "config.yaml", "overwrite", True, True, 2, "fs.csv", "rs.csv", {"file-server"},
        profile="lab",
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
