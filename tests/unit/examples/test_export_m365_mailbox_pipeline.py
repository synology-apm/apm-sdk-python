"""Unit tests for examples/export_m365_mailbox.py — workload listing, export start, and run()/main() orchestration."""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import export_m365_mailbox as em
import pytest
from _common import Progress

from synology_apm.sdk import (
    APMError,
    M365ExportStartResult,
    M365ExportStatus,
    M365Workload,
    M365WorkloadType,
    ResourceNotFoundError,
)
from tests.unit.examples._fixtures import (
    make_fake_apm,
    make_m365_group_info,
    make_m365_user_info,
    make_m365_workload,
    make_version_location,
    make_workload_version,
    patch_make_client,
)

_TENANT_ID = "123e4567-e89b-12d3-a456-426614174000"
_EXEC_ID = "123e4567-e89b-12d3-a456-426614174091"
_EXEC_ID_2 = "123e4567-e89b-12d3-a456-426614174092"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _user_workload(upn: str = "alice@contoso.com") -> M365Workload:
    return make_m365_workload(name=upn, info=make_m365_user_info(user_principal_name=upn))


def _make_start_result(
    workload: M365Workload,
    *,
    execution_id: str = _EXEC_ID,
    ready: bool = False,
    export_name: str = "alice@contoso.com_20260514_mailbox.pst",
) -> M365ExportStartResult:
    return M365ExportStartResult(
        execution_id=execution_id,
        ready_to_download=ready,
        export_name=export_name,
        location=make_version_location(),
        workload=workload,
        version=make_workload_version(),
    )


def _make_progress() -> Progress:
    return Progress(total=1, noun="user")


def _exchange_apm() -> MagicMock:
    """Fake APM client with the exchange-export collection methods wired."""
    apm = make_fake_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(return_value=make_workload_version())
    export = apm.m365.exchange_export
    export.start = AsyncMock()
    export.list = AsyncMock(return_value=([], 0))
    export.cancel = AsyncMock()
    export.get_download_url_by_ready_result = AsyncMock(
        return_value="https://apm.corp.com/download/export"
    )
    export.get_download_url_by_activity = AsyncMock(
        return_value="https://apm.corp.com/download/export"
    )
    return apm


# ── list_workloads ────────────────────────────────────────────────────────────


async def test_list_workloads_total_zero_returns_early() -> None:
    apm = make_fake_apm()
    domain = em._build_exchange_domain("both")

    items, total = await em.list_workloads(apm, domain, _TENANT_ID, None)

    assert items == []
    assert total == 0
    apm.m365.workloads.list.assert_awaited_once_with(
        tenant_id=_TENANT_ID,
        workload_type=M365WorkloadType.EXCHANGE,
        is_retired=False,
        keyword=None,
        limit=1,
        offset=0,
    )

async def test_list_workloads_quick_count_then_paginates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl1 = _user_workload("alice@contoso.com")
    wl2 = _user_workload("bob@contoso.com")
    wl3 = _user_workload("carol@contoso.com")
    apm = make_fake_apm()
    apm.m365.workloads.list.side_effect = [
        ([wl1], 3),        # quick count (limit=1)
        ([wl1, wl2], 3),   # page 1
        ([wl3], 3),        # page 2
    ]
    domain = em._build_exchange_domain("both")

    items, total = await em.list_workloads(apm, domain, _TENANT_ID, "contoso")

    assert [em._upn(w) for w in items] == [
        "alice@contoso.com", "bob@contoso.com", "carol@contoso.com",
    ]
    assert total == 3
    quick, page1, page2 = apm.m365.workloads.list.await_args_list
    assert quick.kwargs["limit"] == 1
    assert page1.kwargs == {
        "tenant_id": _TENANT_ID,
        "workload_type": M365WorkloadType.EXCHANGE,
        "is_retired": False,
        "keyword": "contoso",
        "limit": 500,
        "offset": 0,
    }
    assert page2.kwargs["offset"] == 2
    assert "Found 3 Exchange user(s)." in capsys.readouterr().out

async def test_list_workloads_reports_fetch_progress_beyond_one_page(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wl = _user_workload()
    apm = make_fake_apm()
    apm.m365.workloads.list.side_effect = [
        ([wl], 501),
        ([wl] * 500, 501),
        ([wl], 501),
    ]
    domain = em._build_exchange_domain("both")

    items, total = await em.list_workloads(apm, domain, _TENANT_ID, None)

    assert len(items) == 501
    assert total == 501
    assert "Fetching... 500/501" in capsys.readouterr().out


# ── _start_workload ───────────────────────────────────────────────────────────


async def test_start_workload_splits_immediate_and_pending(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    version = make_workload_version()
    apm.m365.workloads.get_latest_version = AsyncMock(return_value=version)
    ready_result = _make_start_result(wl, execution_id=_EXEC_ID, ready=True)
    pending_result = _make_start_result(wl, execution_id=_EXEC_ID_2, ready=False)
    apm.m365.exchange_export.start = AsyncMock(side_effect=[ready_result, pending_result])
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert failures == []
    assert [j.unit_label for j in immediate] == ["mailbox"]
    assert immediate[0].status == M365ExportStatus.READY_TO_DOWNLOAD
    assert immediate[0].identity == "alice@contoso.com"
    assert [j.unit_label for j in pending] == ["archive mailbox"]
    assert pending[0].status == M365ExportStatus.PREPARING
    assert pending[0].dest_path.endswith("_archive_mailbox.pst")
    first, second = apm.m365.exchange_export.start.await_args_list
    assert first.args == (wl, version)
    assert first.kwargs["archive_mailbox"] is False
    assert first.kwargs["export_name"].endswith("_mailbox.pst")
    assert second.kwargs["archive_mailbox"] is True
    assert second.kwargs["export_name"].endswith("_archive_mailbox.pst")

async def test_start_workload_resume_skips_downloaded_pairs(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(
        return_value=_make_start_result(wl, ready=False)
    )
    domain = em._build_exchange_domain("both")
    skip_pairs = {("alice@contoso.com", "mailbox")}

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), skip_pairs
    )

    assert failures == []
    assert immediate == []
    assert [j.unit_label for j in pending] == ["archive mailbox"]
    apm.m365.exchange_export.start.assert_awaited_once()
    assert apm.m365.exchange_export.start.await_args.kwargs["archive_mailbox"] is True

async def test_start_workload_version_not_found_fails_all_units(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(
        side_effect=ResourceNotFoundError("not found", "WorkloadVersion", "vid")
    )
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert pending == []
    assert immediate == []
    assert [(f.identity, f.unit_label, f.error) for f in failures] == [
        ("alice@contoso.com", "mailbox", "no backup version found"),
        ("alice@contoso.com", "archive mailbox", "no backup version found"),
    ]
    apm.m365.exchange_export.start.assert_not_awaited()

async def test_start_workload_version_lookup_error_fans_out_to_all_units(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(
        side_effect=APMError("temporarily unavailable")
    )
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert pending == []
    assert immediate == []
    assert [f.error for f in failures] == [
        "version lookup failed: temporarily unavailable",
        "version lookup failed: temporarily unavailable",
    ]
    out = capsys.readouterr().out
    assert (
        "  [!!] alice@contoso.com: failed to get latest version: temporarily unavailable" in out
    )

async def test_start_workload_unit_not_found_keeps_other_units(tmp_path: Path) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(
        side_effect=[
            ResourceNotFoundError("not found", "M365Workload", "wid"),
            _make_start_result(wl, execution_id=_EXEC_ID_2, ready=False),
        ]
    )
    domain = em._build_exchange_domain("both")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert [(f.unit_label, f.error) for f in failures] == [("mailbox", "resource not found")]
    assert [j.unit_label for j in pending] == ["archive mailbox"]
    assert immediate == []

async def test_start_workload_unit_apm_error_records_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wl = _user_workload()
    apm = _exchange_apm()
    apm.m365.exchange_export.start = AsyncMock(side_effect=APMError("permission denied"))
    domain = em._build_exchange_domain("primary")

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert pending == []
    assert immediate == []
    assert [(f.unit_label, f.error) for f in failures] == [
        ("mailbox", "start failed: permission denied"),
    ]
    out = capsys.readouterr().out
    assert "  [!!] alice@contoso.com (mailbox): failed to start export: permission denied" in out

async def test_start_workload_group_domain_starts_single_unit(tmp_path: Path) -> None:
    wl = make_m365_workload(
        name="Marketing",
        workload_type=M365WorkloadType.GROUP,
        info=make_m365_group_info(mail="marketing@contoso.com"),
    )
    version = make_workload_version()
    apm = make_fake_apm()
    apm.m365.workloads.get_latest_version = AsyncMock(return_value=version)
    apm.m365.group_export.start = AsyncMock(
        return_value=_make_start_result(wl, ready=False)
    )
    domain = em._build_group_domain()

    pending, immediate, failures = await em._start_workload(
        apm, domain, wl, str(tmp_path), _make_progress(), None
    )

    assert failures == []
    assert immediate == []
    assert [(j.identity, j.unit_label) for j in pending] == [("marketing@contoso.com", "")]
    apm.m365.group_export.start.assert_awaited_once()
    assert apm.m365.group_export.start.await_args is not None
    assert apm.m365.group_export.start.await_args.args == (wl, version)
    export_name = apm.m365.group_export.start.await_args.kwargs["export_name"]
    assert export_name.startswith("marketing@contoso.com_")
    assert export_name.endswith(".pst")


# ── run() ─────────────────────────────────────────────────────────────────────


def _run_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "tenant_id": _TENANT_ID,
        "output_dir": "./exports",
        "keyword": None,
        "cancel": False,
        "dry_run": False,
        "yes": True,
        "concurrency": 2,
        "download_concurrency": 2,
        "csv_path": None,
        "resume_csv": None,
    }
    kwargs.update(overrides)
    return kwargs


async def test_run_missing_resume_csv_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_make_client(monkeypatch, em, make_fake_apm())
    domain = em._build_exchange_domain("both")
    missing = str(tmp_path / "missing_report.csv")

    rc = await em.run(domain, **_run_kwargs(resume_csv=missing))  # type: ignore[arg-type]

    assert rc == 1
    assert f"Error: resume CSV not found: {missing}" in capsys.readouterr().err

async def test_run_no_workloads_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs())  # type: ignore[arg-type]

    assert rc == 0
    assert "No Exchange workloads found." in capsys.readouterr().out

async def test_run_dry_run_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    wl = _user_workload()
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([wl], 1)))
    run_export_mock = AsyncMock()
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs(dry_run=True))  # type: ignore[arg-type]

    assert rc == 0
    assert "[dry-run] No exports started." in capsys.readouterr().out
    run_export_mock.assert_not_awaited()

async def test_run_cancel_mode_dispatches_to_cancel_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    wl = _user_workload()
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([wl], 1)))
    cancel_all_mock = AsyncMock(return_value=5)
    monkeypatch.setattr(em, "cancel_all", cancel_all_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs(cancel=True, dry_run=True))  # type: ignore[arg-type]

    assert rc == 5
    cancel_all_mock.assert_awaited_once()
    assert cancel_all_mock.await_args is not None
    args = cancel_all_mock.await_args.args
    assert args[0] is apm
    assert args[2] == [wl]
    assert args[3] is True  # dry_run forwarded

async def test_run_auto_names_csv_and_forwards_to_run_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    wl = _user_workload()
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([wl], 1)))
    run_export_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")
    output_dir = str(tmp_path / "exports")

    rc = await em.run(domain, **_run_kwargs(output_dir=output_dir))  # type: ignore[arg-type]

    assert rc == 0
    assert run_export_mock.await_args is not None
    args = run_export_mock.await_args.args
    assert args[2] == [wl]           # items
    assert args[3] == output_dir
    csv_arg = args[7]
    assert re.fullmatch(
        re.escape(output_dir) + r"/export_report_\d{8}_\d{6}\.csv", csv_arg
    )
    assert args[8] is None           # skip_pairs
    assert args[9] == []             # carried_rows

async def test_run_resume_filters_items_and_ignores_keyword(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    resume_csv = tmp_path / "previous_report.csv"
    with open(resume_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=em._EXCHANGE_CSV_FIELDS)
        writer.writeheader()
        writer.writerow({
            "upn": "alice@contoso.com", "domain": "contoso.com",
            "mailbox_type": "mailbox", "execution_id": _EXEC_ID,
            "status": "downloaded", "size_bytes": "1024", "error": "",
            "dest_path": "/exports/contoso.com/alice@contoso.com/mailbox.pst",
        })
        writer.writerow({
            "upn": "bob@contoso.com", "domain": "contoso.com",
            "mailbox_type": "mailbox", "execution_id": _EXEC_ID_2,
            "status": "failed", "size_bytes": "", "error": "download error",
            "dest_path": "",
        })

    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    alice = _user_workload("alice@contoso.com")
    bob = _user_workload("bob@contoso.com")
    list_mock = AsyncMock(return_value=([alice, bob], 2))
    monkeypatch.setattr(em, "list_workloads", list_mock)
    run_export_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(
        domain,
        **_run_kwargs(keyword="contoso", resume_csv=str(resume_csv)),  # type: ignore[arg-type]
    )

    assert rc == 0
    # --keyword is ignored in resume mode
    assert list_mock.await_args is not None
    assert list_mock.await_args.args[3] is None
    assert "Note: --keyword is ignored in resume mode" in capsys.readouterr().err
    assert run_export_mock.await_args is not None
    args = run_export_mock.await_args.args
    assert args[2] == [bob]                                        # only pending identities
    assert args[8] == {("alice@contoso.com", "mailbox")}           # skip_pairs
    assert len(args[9]) == 1                                       # carried rows
    assert args[9][0]["upn"] == "alice@contoso.com"

async def test_run_resume_all_downloaded_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    resume_csv = tmp_path / "previous_report.csv"
    with open(resume_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=em._EXCHANGE_CSV_FIELDS)
        writer.writeheader()
        writer.writerow({
            "upn": "alice@contoso.com", "domain": "contoso.com",
            "mailbox_type": "mailbox", "execution_id": _EXEC_ID,
            "status": "downloaded", "size_bytes": "1024", "error": "",
            "dest_path": "/exports/contoso.com/alice@contoso.com/mailbox.pst",
        })

    apm = make_fake_apm()
    patch_make_client(monkeypatch, em, apm)
    alice = _user_workload("alice@contoso.com")
    monkeypatch.setattr(em, "list_workloads", AsyncMock(return_value=([alice], 1)))
    run_export_mock = AsyncMock()
    monkeypatch.setattr(em, "run_export", run_export_mock)
    domain = em._build_exchange_domain("both")

    rc = await em.run(domain, **_run_kwargs(resume_csv=str(resume_csv)))  # type: ignore[arg-type]

    assert rc == 0
    assert "All users already downloaded — nothing to do." in capsys.readouterr().out
    run_export_mock.assert_not_awaited()


# ── main() / _add_common_args ─────────────────────────────────────────────────


def _capture_main_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch run/run_main so main() only records what it would pass."""
    captured: dict[str, object] = {}

    def fake_run(domain: em.MailExportDomain, **kwargs: object) -> object:
        captured["domain"] = domain
        captured.update(kwargs)

        async def _noop() -> int:
            return 0

        return _noop()

    monkeypatch.setattr(em, "run", fake_run)
    monkeypatch.setattr(em, "run_main", lambda coro: coro.close())
    return captured


def test_main_requires_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(sys, "argv", ["export_m365_mailbox.py"]), pytest.raises(SystemExit) as exc:
        em.main()
    assert exc.value.code == 2
    assert "the following arguments are required: {exchange,group}" in capsys.readouterr().err

def test_main_requires_tenant_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture_main_run(monkeypatch)
    with patch.object(sys, "argv", ["export_m365_mailbox.py", "exchange"]), pytest.raises(SystemExit) as exc:
        em.main()
    assert exc.value.code == 1
    assert "Error: --tenant-id is required" in capsys.readouterr().err

def test_main_rejects_primary_only_with_archive_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [
        "export_m365_mailbox.py", "exchange", "--tenant-id", _TENANT_ID,
        "--primary-only", "--archive-only",
    ]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc:
        em.main()
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err

def test_main_exchange_passes_arguments_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_main_run(monkeypatch)
    argv = [
        "export_m365_mailbox.py", "exchange",
        "--tenant-id", _TENANT_ID,
        "--output-dir", "/exports",
        "--keyword", "alice",
        "--csv", "/exports/report.csv",
        "--resume", "/exports/previous_report.csv",
        "--cancel",
        "--dry-run",
        "--yes",
        "--concurrency", "4",
        "--download-concurrency", "8",
        "--profile", "lab",
    ]
    with patch.object(sys, "argv", argv):
        em.main()

    domain = captured.pop("domain")
    assert isinstance(domain, em.MailExportDomain)
    assert domain.type_label == "Exchange"
    assert domain.extra_note == " — primary + archive"
    assert captured == {
        "tenant_id": _TENANT_ID,
        "output_dir": "/exports",
        "keyword": "alice",
        "cancel": True,
        "dry_run": True,
        "yes": True,
        "concurrency": 4,
        "download_concurrency": 8,
        "csv_path": "/exports/report.csv",
        "resume_csv": "/exports/previous_report.csv",
        "profile": "lab",
    }

@pytest.mark.parametrize(
    "scope_flag,expected_note",
    [
        ("--primary-only", " — primary only"),
        ("--archive-only", " — archive only"),
    ],
)
def test_main_exchange_scope_flags_select_domain_scope(
    monkeypatch: pytest.MonkeyPatch, scope_flag: str, expected_note: str
) -> None:
    captured = _capture_main_run(monkeypatch)
    argv = ["export_m365_mailbox.py", "exchange", "--tenant-id", _TENANT_ID, scope_flag]
    with patch.object(sys, "argv", argv):
        em.main()

    domain = captured["domain"]
    assert isinstance(domain, em.MailExportDomain)
    assert domain.extra_note == expected_note

def test_main_group_builds_group_domain_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_main_run(monkeypatch)
    argv = ["export_m365_mailbox.py", "group", "--tenant-id", _TENANT_ID]
    with patch.object(sys, "argv", argv):
        em.main()

    domain = captured.pop("domain")
    assert isinstance(domain, em.MailExportDomain)
    assert domain.type_label == "Group"
    assert domain.id_field == "group_mail"
    assert captured == {
        "tenant_id": _TENANT_ID,
        "output_dir": "./exports",
        "keyword": None,
        "cancel": False,
        "dry_run": False,
        "yes": False,
        "concurrency": 3,
        "download_concurrency": 5,
        "csv_path": None,
        "resume_csv": None,
        "profile": None,
    }
