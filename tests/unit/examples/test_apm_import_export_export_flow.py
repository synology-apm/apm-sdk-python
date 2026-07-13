"""Unit tests for the export pipeline in examples/apm_import_export.py.

Covers _fetch_protection_details and the run_export orchestrator: overwrite
confirmation, ref-key assignment, YAML file assembly, and the credentials
template CSVs.
"""
from __future__ import annotations

import asyncio
import csv
import os
import stat
from datetime import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import apm_import_export as ie
import pytest
import yaml

from synology_apm.sdk import (
    APMError,
    M365AutoBackupRuleListResult,
    M365CollabServiceSetting,
    MachineWorkloadType,
    ProtectionRetentionPolicy,
    ProtectionSchedule,
    RetentionType,
    ScheduleFrequency,
)
from synology_apm.sdk.models.protection_plan import ProtectionPlanPolicy
from tests.unit.examples._fixtures import (
    make_backup_server,
    make_fake_apm,
    make_file_server_config,
    make_location_info,
    make_machine_workload,
    make_protection_plan,
    make_remote_storage,
    make_saas_tenant,
    patch_make_client,
)

_PLAN_A = "123e4567-e89b-12d3-a456-426614174001"
_PLAN_B = "123e4567-e89b-12d3-a456-426614174002"
_TENANT_A = "123e4567-e89b-12d3-a456-426614174060"


def _make_detailed_plan(plan_id: str = _PLAN_A, name: str = "Daily Backup") -> Any:
    return make_protection_plan(
        plan_id=plan_id,
        name=name,
        policy=ProtectionPlanPolicy(
            retention=ProtectionRetentionPolicy(retention_type=RetentionType.KEEP_DAYS, days=30),
            schedule=ProtectionSchedule(frequency=ScheduleFrequency.DAILY, start_time=time(2, 0)),
        ),
    )


def _empty_rules_result() -> M365AutoBackupRuleListResult:
    disabled = M365CollabServiceSetting(plan_id="", namespace="")
    return M365AutoBackupRuleListResult(
        rules=(),
        group_exchange=disabled,
        mysite=disabled,
        sharepoint=disabled,
        teams=disabled,
    )


# ── _fetch_protection_details ─────────────────────────────────────────────────


async def test_fetch_protection_details_machine_dispatch() -> None:
    """Machine stubs are resolved through the machine plan collection by plan_id."""
    apm = make_fake_apm()
    detailed = _make_detailed_plan()
    apm.machine.plans.get = AsyncMock(return_value=detailed)
    stub = make_protection_plan(plan_id=_PLAN_A, name="Daily Backup")

    result = await ie._fetch_protection_details(
        apm, [stub], asyncio.Semaphore(2), is_machine=True
    )

    assert result == [detailed]
    apm.machine.plans.get.assert_awaited_once_with(_PLAN_A)


async def test_fetch_protection_details_m365_dispatch() -> None:
    """M365 stubs are resolved through the M365 plan collection."""
    apm = make_fake_apm()
    detailed = _make_detailed_plan()
    apm.m365.plans.get = AsyncMock(return_value=detailed)
    stub = make_protection_plan(plan_id=_PLAN_A)

    result = await ie._fetch_protection_details(
        apm, [stub], asyncio.Semaphore(2), is_machine=False
    )

    assert result == [detailed]
    apm.m365.plans.get.assert_awaited_once_with(_PLAN_A)


async def test_fetch_protection_details_drops_failed_plan_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A plan whose detail fetch fails is dropped with a warning; others survive."""
    apm = make_fake_apm()
    ok_plan = _make_detailed_plan(plan_id=_PLAN_B, name="Weekly Backup")
    apm.machine.plans.get = AsyncMock(side_effect=[APMError("boom"), ok_plan])
    stubs = [
        make_protection_plan(plan_id=_PLAN_A, name="Daily Backup"),
        make_protection_plan(plan_id=_PLAN_B, name="Weekly Backup"),
    ]

    result = await ie._fetch_protection_details(
        apm, stubs, asyncio.Semaphore(2), is_machine=True
    )

    assert result == [ok_plan]
    err = capsys.readouterr().err
    warning_line = next(ln for ln in err.splitlines() if "failed to fetch details" in ln)
    assert "'Daily Backup'" in warning_line


# ── run_export wiring helper ──────────────────────────────────────────────────


def _wire_export_apm(
    *,
    machine_plans: list[Any] | None = None,
    fs_workloads: list[Any] | None = None,
    rules_result: M365AutoBackupRuleListResult | None = None,
    rules_error: APMError | None = None,
) -> MagicMock:
    """Fake APM with one backup server, one remote storage, and one SaaS tenant."""
    apm = make_fake_apm()
    bs = make_backup_server()  # name apm-server-01
    rs = make_remote_storage()  # tiering-remote, s3_compatible, vault my-bucket
    plans = machine_plans if machine_plans is not None else [_make_detailed_plan()]

    apm.backup_servers.list = AsyncMock(return_value=([bs], 1))
    apm.remote_storages.list = AsyncMock(return_value=([rs], 1))
    apm.machine.plans.list = AsyncMock(return_value=(plans, len(plans)))
    apm.machine.plans.get = AsyncMock(side_effect=lambda pid: next(
        p for p in plans if p.plan_id == pid
    ))
    apm.machine.workloads.list = AsyncMock(
        return_value=(fs_workloads or [], len(fs_workloads or []))
    )
    apm.saas.list = AsyncMock(return_value=([make_saas_tenant(tenant_id=_TENANT_A)], 1))
    if rules_error is not None:
        apm.m365.auto_backup_rules.list = AsyncMock(side_effect=rules_error)
    else:
        apm.m365.auto_backup_rules.list = AsyncMock(
            return_value=rules_result if rules_result is not None else _empty_rules_result()
        )
    return apm


def _make_fs_workload(*, is_retired: bool = False) -> Any:
    return make_machine_workload(
        name="Corp Share",
        workload_type=MachineWorkloadType.FS,
        is_retired=is_retired,
        fs_config=make_file_server_config(),
        backup_server=make_location_info(name="apm-server-01"),
        plan=_make_detailed_plan(),
    )


# ── run_export — YAML assembly and ref keys ───────────────────────────────────


async def test_run_export_writes_yaml_with_ref_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exported YAML carries server-N/storage-N/plan-N/tenant-N ref keys."""
    apm = _wire_export_apm(fs_workloads=[_make_fs_workload()])
    patch_make_client(monkeypatch, ie, apm)
    out = tmp_path / "export.yaml"

    ret = await ie.run_export(str(out), concurrency=2)

    assert ret == 0
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["backup_servers"][0]["ref_key"] == "server-1"
    assert data["backup_servers"][0]["name_or_id"] == "apm-server-01"
    assert data["remote_storages"][0]["ref_key"] == "storage-1"
    assert data["remote_storages"][0]["name_or_id"] == "tiering-remote"
    assert data["protection_plans"][0]["ref_key"] == "plan-1"
    assert data["protection_plans"][0]["name_or_id"] == "Daily Backup"
    assert data["retirement_plans"] == []
    assert data["tiering_plans"] == []
    assert data["file_servers"][0]["backup_server_ref"] == "server-1"
    assert data["file_servers"][0]["plan_ref"] == "plan-1"
    assert data["saas_tenants"][0]["ref_key"] == "tenant-1"
    assert data["saas_tenants"][0]["tenant_id"] == _TENANT_A


async def test_run_export_assigns_unique_plan_refs_for_duplicate_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two plans sharing a name still get distinct plan-N refs (keyed by plan_id)."""
    plans = [
        _make_detailed_plan(plan_id=_PLAN_A, name="Daily Backup"),
        _make_detailed_plan(plan_id=_PLAN_B, name="Daily Backup"),
    ]
    apm = _wire_export_apm(machine_plans=plans)
    patch_make_client(monkeypatch, ie, apm)
    out = tmp_path / "export.yaml"

    ret = await ie.run_export(str(out), concurrency=2)

    assert ret == 0
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert [p["ref_key"] for p in data["protection_plans"]] == ["plan-1", "plan-2"]


async def test_run_export_rules_fetch_error_warns_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An auto-backup-rules fetch failure is warned per tenant, not fatal."""
    apm = _wire_export_apm(rules_error=APMError("tenant offline"))
    patch_make_client(monkeypatch, ie, apm)
    out = tmp_path / "export.yaml"

    ret = await ie.run_export(str(out), concurrency=2)

    assert ret == 0
    err = capsys.readouterr().err
    warning_line = next(
        ln for ln in err.splitlines() if "failed to fetch auto-backup rules" in ln
    )
    assert "'Contoso'" in warning_line
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["m365_auto_backup_rules"] == []


async def test_run_export_serializes_enabled_collab_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tenant with an enabled collab service produces an m365_auto_backup_rules block."""
    rules = M365AutoBackupRuleListResult(
        rules=(),
        group_exchange=M365CollabServiceSetting(plan_id=_PLAN_A, namespace="ns-apm-server-01"),
        mysite=M365CollabServiceSetting(plan_id="", namespace=""),
        sharepoint=M365CollabServiceSetting(plan_id="", namespace=""),
        teams=M365CollabServiceSetting(plan_id="", namespace=""),
    )
    apm = _wire_export_apm(rules_result=rules)
    patch_make_client(monkeypatch, ie, apm)
    out = tmp_path / "export.yaml"

    ret = await ie.run_export(str(out), concurrency=2)

    assert ret == 0
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    block = data["m365_auto_backup_rules"][0]
    assert block["tenant_ref"] == "tenant-1"
    assert block["collab_services"]["group_exchange"]["plan_ref"] == "plan-1"
    assert block["collab_services"]["group_exchange"]["backup_server_ref"] == "server-1"


async def test_run_export_summary_line_names_output_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The final stderr summary reports the counts and the output path."""
    apm = _wire_export_apm(fs_workloads=[_make_fs_workload()])
    patch_make_client(monkeypatch, ie, apm)
    out = tmp_path / "export.yaml"

    await ie.run_export(str(out), concurrency=2)

    err = capsys.readouterr().err
    summary = next(ln for ln in err.splitlines() if ln.startswith("Exported "))
    assert "1 protection," in summary
    assert "1 File Server workload(s)" in summary
    assert str(out) in summary


# ── run_export — credentials templates ────────────────────────────────────────


async def test_run_export_writes_credential_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FS and storage credential templates are written owner-only with data rows."""
    apm = _wire_export_apm(
        fs_workloads=[_make_fs_workload(), _make_fs_workload(is_retired=True)]
    )
    patch_make_client(monkeypatch, ie, apm)
    out = tmp_path / "export.yaml"

    ret = await ie.run_export(str(out), concurrency=2)

    assert ret == 0
    fs_csv = tmp_path / "export.fs-credentials.csv"
    with open(fs_csv, newline="", encoding="utf-8") as fh:
        fs_rows = list(csv.DictReader(fh))
    # The retired FS workload shares the same endpoint/user, so exactly one row remains.
    assert fs_rows == [{"endpoint": "10.0.0.10", "login_user": "admin", "password": ""}]
    assert stat.S_IMODE(os.stat(fs_csv).st_mode) == 0o600

    rs_csv = tmp_path / "export.storage-credentials.csv"
    with open(rs_csv, newline="", encoding="utf-8") as fh:
        rs_rows = list(csv.DictReader(fh))
    assert rs_rows == [{
        "storage_type": "s3_compatible",
        "endpoint": "https://s3.example.com:443",
        "vault_name": "my-bucket",
        "access_key": "",
        "secret_key": "",
        "relink_encryption_key": "",
    }]
    assert stat.S_IMODE(os.stat(rs_csv).st_mode) == 0o600


async def test_run_export_skips_credential_templates_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write_credentials_template=False writes only the YAML."""
    apm = _wire_export_apm(fs_workloads=[_make_fs_workload()])
    patch_make_client(monkeypatch, ie, apm)
    out = tmp_path / "export.yaml"

    ret = await ie.run_export(str(out), concurrency=2, write_credentials_template=False)

    assert ret == 0
    assert out.exists()
    assert not (tmp_path / "export.fs-credentials.csv").exists()
    assert not (tmp_path / "export.storage-credentials.csv").exists()


# ── run_export — overwrite confirmation ───────────────────────────────────────


async def test_run_export_declined_overwrite_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Declining the overwrite prompt aborts with exit code 1 before any fetch."""
    apm = _wire_export_apm()
    patch_make_client(monkeypatch, ie, apm)
    monkeypatch.setattr(ie, "prompt_yes_no", AsyncMock(return_value=False))
    out = tmp_path / "export.yaml"
    out.write_text("old content\n", encoding="utf-8")

    ret = await ie.run_export(str(out), concurrency=2)

    assert ret == 1
    err = capsys.readouterr().err
    assert "Aborted." in err
    assert str(out) in err  # named among the files to be overwritten
    assert out.read_text(encoding="utf-8") == "old content\n"
    apm.backup_servers.list.assert_not_awaited()


async def test_run_export_yes_skips_overwrite_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yes=True overwrites an existing file without prompting."""
    apm = _wire_export_apm()
    patch_make_client(monkeypatch, ie, apm)
    monkeypatch.setattr(
        ie, "prompt_yes_no", AsyncMock(side_effect=AssertionError("must not prompt"))
    )
    out = tmp_path / "export.yaml"
    out.write_text("old content\n", encoding="utf-8")

    ret = await ie.run_export(str(out), concurrency=2, yes=True)

    assert ret == 0
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["version"] == 1
