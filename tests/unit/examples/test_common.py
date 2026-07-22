"""Unit tests for examples/_common.py shared helpers."""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import signal
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _common import (
    M365_TYPE_LABELS,
    WORKLOAD_TYPE_ORDER,
    Progress,
    _remove_quietly,
    add_category_args,
    add_output_arg,
    add_profile_arg,
    category_label,
    collect_backup_servers,
    collect_m365_workloads,
    collect_workloads,
    fmt_bytes,
    fmt_compact_duration,
    fmt_dt,
    fmt_duration,
    fmt_speed,
    interruptible_sleep,
    list_m365_tenants,
    make_client,
    paginate,
    parse_compact_duration,
    prompt_yes_no,
    register_interrupt,
    resolve_m365_services,
    run_main,
    safe_path,
    unregister_interrupt,
    workload_type_label,
)

from synology_apm.sdk import (
    APMError,
    KeyringUnavailableError,
    M365WorkloadType,
    MachineWorkloadType,
    ResolvedConnection,
    WorkloadCategory,
)
from tests.unit.examples._fixtures import (
    make_backup_server,
    make_fake_apm,
    make_m365_workload,
    make_machine_workload,
    make_saas_tenant,
)

# ── make_client ────────────────────────────────────────────────────────────────


def _mock_resolved(
    monkeypatch: pytest.MonkeyPatch,
    *,
    host: str = "apm.corp.com",
    username: str = "admin",
    password: str = "password",
    verify_ssl: bool = True,
) -> MagicMock:
    """Point _common.resolve_connection at a fake ResolvedConnection, sidestepping
    any real config.toml on the test machine."""
    mock_resolve = MagicMock(
        return_value=ResolvedConnection(host, username, password, verify_ssl)
    )
    monkeypatch.setattr("_common.resolve_connection", mock_resolve)
    return mock_resolve


def test_make_client_builds_client_from_resolved_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_resolved(monkeypatch)
    fake_cls = MagicMock(name="APMClient")
    monkeypatch.setattr("_common.APMClient", fake_cls)
    client = make_client()
    fake_cls.assert_called_once_with("apm.corp.com", "admin", "password", verify_ssl=True)
    assert client is fake_cls.return_value


@pytest.mark.parametrize("verify_ssl", [True, False])
def test_make_client_passes_through_verify_ssl(monkeypatch: pytest.MonkeyPatch, verify_ssl: bool) -> None:
    _mock_resolved(monkeypatch, verify_ssl=verify_ssl)
    fake_cls = MagicMock(name="APMClient")
    monkeypatch.setattr("_common.APMClient", fake_cls)
    make_client()
    assert fake_cls.call_args.kwargs["verify_ssl"] is verify_ssl


def test_make_client_forwards_explicit_arguments_to_resolve_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_resolve = _mock_resolved(monkeypatch)
    monkeypatch.setattr("_common.APMClient", MagicMock(name="APMClient"))
    make_client(
        host="apm2.corp.com", username="alice", password="secret",
        profile="lab", no_verify_ssl=True,
    )
    mock_resolve.assert_called_once_with(
        host="apm2.corp.com", username="alice", password="secret",
        profile="lab", no_verify_ssl=True,
    )


def test_make_client_missing_host_raises_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_resolved(monkeypatch, host="")
    with pytest.raises(KeyError) as exc_info:
        make_client()
    assert exc_info.value.args[0] == "APM_HOST"


def test_make_client_missing_username_raises_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_resolved(monkeypatch, username="")
    with pytest.raises(KeyError) as exc_info:
        make_client()
    assert exc_info.value.args[0] == "APM_USERNAME"


def test_make_client_missing_password_raises_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_resolved(monkeypatch, password="")
    with pytest.raises(KeyError) as exc_info:
        make_client()
    assert exc_info.value.args[0] == "APM_PASSWORD"


def test_make_client_propagates_keyring_unavailable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**kwargs: object) -> None:
        raise KeyringUnavailableError("keyring locked")

    monkeypatch.setattr("_common.resolve_connection", _raise)
    with pytest.raises(KeyringUnavailableError):
        make_client()


# ── fmt_bytes ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "n,expected",
    [
        (None, "—"),
        (0, "0.0 B"),
        (1023, "1023.0 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**4, "1.0 TB"),
        # 1024**5 exceeds TB threshold; loop falls through to the PB return
        (1024**5, "1.0 PB"),
    ],
)
def test_fmt_bytes(n: int | None, expected: str) -> None:
    assert fmt_bytes(n) == expected


# ── fmt_duration ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (None, "—"),
        (0, "00:00:00"),
        (3661, "01:01:01"),
        (90000, "25:00:00"),  # >24 h stays in hours, not rolling over to days
    ],
)
def test_fmt_duration(seconds: float | None, expected: str) -> None:
    assert fmt_duration(seconds) == expected


# ── fmt_speed ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "size_bytes,duration_secs,expected",
    [
        (0, 1.0, "0.0 B/s"),
        (1536, 1.0, "1.5 KB/s"),
        # 1024**4 B/s exceeds the GB/s threshold; loop falls through to the TB/s return
        (1024**4, 1.0, "1.0 TB/s"),
        (3 * 1024**4, 2.0, "1.5 TB/s"),
        (0, 0.0, "—"),    # zero duration → non-positive guard
        (1024, -1.0, "—"),  # negative duration → non-positive guard
    ],
)
def test_fmt_speed(size_bytes: int, duration_secs: float, expected: str) -> None:
    assert fmt_speed(size_bytes, duration_secs) == expected


# ── fmt_dt ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def tz_utc_plus_2() -> Iterator[None]:
    """Pin the process's local timezone to UTC+2 so fmt_dt output is deterministic."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Etc/GMT-2"  # POSIX sign convention: Etc/GMT-2 is UTC+2
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_fmt_dt_none_returns_empty_default() -> None:
    assert fmt_dt(None) == ""


def test_fmt_dt_none_returns_custom_default() -> None:
    assert fmt_dt(None, default="N/A") == "N/A"


def test_fmt_dt_converts_utc_to_local_time(tz_utc_plus_2: None) -> None:
    dt = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    assert fmt_dt(dt) == "2026-06-15 14:00:00"


def test_fmt_dt_converts_fixed_offset_to_local_time(tz_utc_plus_2: None) -> None:
    # 07:00 at UTC-3 is 10:00 UTC, i.e. 12:00 in the pinned UTC+2 local zone.
    dt = datetime(2026, 6, 15, 7, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    assert fmt_dt(dt) == "2026-06-15 12:00:00"


def test_fmt_dt_custom_fmt(tz_utc_plus_2: None) -> None:
    # 23:00 UTC crosses midnight into the next day in the UTC+2 local zone.
    dt = datetime(2026, 6, 15, 23, 0, 0, tzinfo=UTC)
    assert fmt_dt(dt, fmt="%Y-%m-%d") == "2026-06-16"


# ── safe_path ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "s,expected",
    [
        ("a/b:c*d", "a_b_c_d"),
        ("", "unknown"),
        ("   ", "unknown"),            # whitespace-only → stripped to "" → "unknown"
        ("normal-name.txt", "normal-name.txt"),
        ('a"b<c>d|e', "a_b_c_d_e"),
    ],
)
def test_safe_path(s: str, expected: str) -> None:
    assert safe_path(s) == expected


# ── compact duration ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "td",
    [
        timedelta(days=2),
        timedelta(hours=6),
        timedelta(minutes=30),
    ],
)
def test_compact_duration_roundtrip(td: timedelta) -> None:
    assert parse_compact_duration(fmt_compact_duration(td)) == td


def test_fmt_compact_duration_non_whole_hour_stays_as_minutes() -> None:
    # 90 minutes is not a whole-hour multiple → "90m", not "1h30m"
    assert fmt_compact_duration(timedelta(minutes=90)) == "90m"


def test_fmt_compact_duration_sub_minute_floors_to_1m() -> None:
    # 30 s → secs//60 = 0 → max(1, 0) = 1 → "1m"
    assert fmt_compact_duration(timedelta(seconds=30)) == "1m"


def test_fmt_compact_duration_48h_expressed_as_days() -> None:
    assert fmt_compact_duration(timedelta(hours=48)) == "2d"


@pytest.mark.parametrize("s", ["30x", "h", "", "1.5h"])
def test_parse_compact_duration_invalid_raises_value_error(s: str) -> None:
    with pytest.raises(ValueError):
        parse_compact_duration(s)


def test_parse_compact_duration_strips_surrounding_spaces() -> None:
    assert parse_compact_duration(" 30m ") == timedelta(minutes=30)


# ── workload type labels ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "workload_type,expected",
    [
        (M365WorkloadType.EXCHANGE, "Exchange"),
        (M365WorkloadType.GROUP, "Group"),
    ],
)
def test_workload_type_label_m365(workload_type: M365WorkloadType, expected: str) -> None:
    wl = make_m365_workload(workload_type=workload_type)
    assert workload_type_label(wl) == expected


@pytest.mark.parametrize(
    "workload_type,expected",
    [
        (MachineWorkloadType.VM, "VM"),
        (MachineWorkloadType.PC, "PC"),
        (MachineWorkloadType.FS, "FS"),
    ],
)
def test_workload_type_label_machine(workload_type: MachineWorkloadType, expected: str) -> None:
    wl = make_machine_workload(workload_type=workload_type)
    assert workload_type_label(wl) == expected


def test_category_label_m365_workload() -> None:
    assert category_label(make_m365_workload()) == "M365"


def test_category_label_machine_workload() -> None:
    assert category_label(make_machine_workload()) == "Machine"


# ── enum exhaustiveness ────────────────────────────────────────────────────────


def test_m365_type_labels_covers_all_m365_workload_types() -> None:
    assert set(M365_TYPE_LABELS.keys()) == set(M365WorkloadType)


def test_workload_type_order_covers_all_machine_and_m365_types() -> None:
    assert set(WORKLOAD_TYPE_ORDER) == set(MachineWorkloadType) | set(M365WorkloadType)


# ── paginate ───────────────────────────────────────────────────────────────────


async def test_paginate_drains_multiple_pages() -> None:
    async def fake_list(limit: int, offset: int) -> tuple[list[int], int]:
        if offset < 4:
            return [offset, offset + 1], 4
        return [], 4

    items, total = await paginate(fake_list)
    assert items == [0, 1, 2, 3]
    assert total == 4


async def test_paginate_empty_first_chunk_returns_empty() -> None:
    async def empty_list(limit: int, offset: int) -> tuple[list[int], int]:
        return [], 0

    items, total = await paginate(empty_list)
    assert items == []
    assert total == 0


async def test_paginate_stops_on_empty_chunk_even_when_total_not_reached() -> None:
    calls: list[int] = []

    async def underfull(limit: int, offset: int) -> tuple[list[int], int]:
        calls.append(offset)
        if offset == 0:
            return [0, 1], 4  # server says total=4 but subsequent page is empty
        return [], 4

    items, total = await paginate(underfull, page=2)
    assert items == [0, 1]
    assert total == 4
    assert len(calls) == 2  # second call discovered the empty chunk and stopped


# ── collect_workloads / collect_m365_workloads ────────────────────────────────


async def test_collect_workloads_machine_category_returns_machine_items_only() -> None:
    apm = make_fake_apm()
    wl = make_machine_workload()
    apm.machine.workloads.list.return_value = ([wl], 1)
    workloads, total = await collect_workloads(apm, "machine", None, is_retired=False)
    assert workloads == [wl]
    assert total == 1
    apm.m365.workloads.list.assert_not_called()
    apm.saas.list.assert_not_called()


async def test_collect_workloads_forwards_is_retired_to_machine_list() -> None:
    apm = make_fake_apm()
    await collect_workloads(apm, "machine", None, is_retired=True)
    assert apm.machine.workloads.list.call_args.kwargs["is_retired"] is True


async def test_collect_workloads_m365_category_returns_m365_items_and_forwards_filters() -> None:
    apm = make_fake_apm()
    tenant = make_saas_tenant()
    wl = make_m365_workload(tenant_id=tenant.tenant_id)
    apm.saas.list.return_value = ([tenant], 1)
    apm.m365.workloads.list.return_value = ([wl], 1)
    workloads, total = await collect_workloads(
        apm, "m365", [M365WorkloadType.EXCHANGE], is_retired=False
    )
    assert workloads == [wl]
    assert total == 1
    apm.machine.workloads.list.assert_not_called()
    call_kwargs = apm.m365.workloads.list.call_args.kwargs
    assert call_kwargs["tenant_id"] == tenant.tenant_id
    assert call_kwargs["workload_type"] is M365WorkloadType.EXCHANGE
    assert call_kwargs["is_retired"] is False


async def test_collect_workloads_all_category_merges_results_and_sums_totals() -> None:
    apm = make_fake_apm()
    tenant = make_saas_tenant()
    machine_wl = make_machine_workload()
    m365_wl = make_m365_workload(tenant_id=tenant.tenant_id)
    apm.machine.workloads.list.return_value = ([machine_wl], 1)
    apm.saas.list.return_value = ([tenant], 1)
    apm.m365.workloads.list.return_value = ([m365_wl], 1)
    workloads, total = await collect_workloads(
        apm, "all", [M365WorkloadType.EXCHANGE], is_retired=False
    )
    assert workloads == [machine_wl, m365_wl]
    assert total == 2


async def test_collect_workloads_m365_none_services_queries_every_type() -> None:
    apm = make_fake_apm()
    apm.saas.list.return_value = ([make_saas_tenant()], 1)
    await collect_workloads(apm, "m365", None, is_retired=False)
    queried_types = {c.kwargs["workload_type"] for c in apm.m365.workloads.list.call_args_list}
    assert queried_types == set(M365WorkloadType)


async def test_collect_m365_workloads_provided_tenants_skips_saas_list() -> None:
    apm = make_fake_apm()
    tenant = make_saas_tenant()
    wl = make_m365_workload(tenant_id=tenant.tenant_id)
    apm.m365.workloads.list.return_value = ([wl], 1)
    workloads, total = await collect_m365_workloads(
        apm,
        [M365WorkloadType.EXCHANGE],
        is_retired=False,
        tenants=[tenant],
    )
    assert workloads == [wl]
    assert total == 1
    apm.saas.list.assert_not_called()


# ── list_m365_tenants / collect_backup_servers ─────────────────────────────────


async def test_list_m365_tenants_filters_to_m365_category() -> None:
    apm = make_fake_apm()
    m365_tenant = make_saas_tenant()
    gws_tenant = make_saas_tenant(
        tenant_id="123e4567-e89b-12d3-a456-426614174061",
        category=WorkloadCategory.GWS,
    )
    apm.saas.list.return_value = ([m365_tenant, gws_tenant], 2)
    tenants = await list_m365_tenants(apm)
    assert tenants == [m365_tenant]


async def test_collect_backup_servers_returns_all_servers() -> None:
    apm = make_fake_apm()
    server = make_backup_server()
    apm.backup_servers.list.return_value = ([server], 1)
    servers = await collect_backup_servers(apm)
    assert servers == [server]
    assert apm.backup_servers.list.call_args.kwargs == {"limit": 500, "offset": 0}


# ── argparse helpers ───────────────────────────────────────────────────────────


def test_add_category_args_is_required_when_no_default() -> None:
    parser = argparse.ArgumentParser()
    add_category_args(parser, verb="export")
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_add_category_args_is_optional_when_default_given() -> None:
    parser = argparse.ArgumentParser()
    add_category_args(parser, verb="export", default="all")
    args = parser.parse_args([])
    assert args.category == "all"


def test_add_output_arg_default_is_table() -> None:
    parser = argparse.ArgumentParser()
    add_output_arg(parser)
    args = parser.parse_args([])
    assert args.output == "table"


def test_add_output_arg_accepts_csv_and_json() -> None:
    parser = argparse.ArgumentParser()
    add_output_arg(parser)
    assert parser.parse_args(["-o", "csv"]).output == "csv"
    assert parser.parse_args(["--output", "json"]).output == "json"


def test_add_profile_arg_defaults_to_none() -> None:
    parser = argparse.ArgumentParser()
    add_profile_arg(parser)
    assert parser.parse_args([]).profile is None


def test_add_profile_arg_accepts_value() -> None:
    parser = argparse.ArgumentParser()
    add_profile_arg(parser)
    assert parser.parse_args(["--profile", "lab"]).profile == "lab"


def test_resolve_m365_services_m365_without_service_prints_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = argparse.ArgumentParser()
    add_category_args(parser, verb="export")
    args = argparse.Namespace(category="m365", m365_service=None)
    with pytest.raises(SystemExit):
        resolve_m365_services(parser, args)
    assert "--m365-service is required" in capsys.readouterr().err


def test_resolve_m365_services_machine_with_service_prints_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = argparse.ArgumentParser()
    add_category_args(parser, verb="export")
    args = argparse.Namespace(category="machine", m365_service=["exchange"])
    with pytest.raises(SystemExit):
        resolve_m365_services(parser, args)
    assert "not valid" in capsys.readouterr().err


def test_resolve_m365_services_returns_typed_list() -> None:
    parser = argparse.ArgumentParser()
    add_category_args(parser, verb="export")
    args = argparse.Namespace(category="m365", m365_service=["exchange", "onedrive"])
    result = resolve_m365_services(parser, args)
    assert result == [M365WorkloadType.EXCHANGE, M365WorkloadType.ONEDRIVE]


def test_resolve_m365_services_all_without_service_returns_none() -> None:
    parser = argparse.ArgumentParser()
    add_category_args(parser, verb="export")
    args = argparse.Namespace(category="all", m365_service=None)
    assert resolve_m365_services(parser, args) is None


# ── run_main exit-code contract ────────────────────────────────────────────────


def test_run_main_none_return_exits_0() -> None:
    async def _coro() -> int | None:
        return None

    with pytest.raises(SystemExit) as exc_info:
        run_main(_coro())
    assert exc_info.value.code == 0


def test_run_main_nonzero_return_exits_with_that_code() -> None:
    async def _coro() -> int | None:
        return 3

    with pytest.raises(SystemExit) as exc_info:
        run_main(_coro())
    assert exc_info.value.code == 3


def test_run_main_apm_error_exits_1_with_message(capsys: pytest.CaptureFixture[str]) -> None:
    async def _coro() -> int | None:
        raise APMError("boom")

    with pytest.raises(SystemExit) as exc_info:
        run_main(_coro())
    assert exc_info.value.code == 1
    assert "APM error: boom" in capsys.readouterr().err


def test_run_main_key_error_exits_1_with_hint(capsys: pytest.CaptureFixture[str]) -> None:
    async def _coro() -> int | None:
        raise KeyError("APM_HOST")

    with pytest.raises(SystemExit) as exc_info:
        run_main(_coro())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Missing APM_HOST." in err
    assert "uv run --env-file .env" in err
    assert "export APM_HOST" in err
    assert "synology-apm-cli config set" in err


def test_run_main_keyring_unavailable_exits_1_with_hint(capsys: pytest.CaptureFixture[str]) -> None:
    async def _coro() -> int | None:
        raise KeyringUnavailableError("keyring locked")

    with pytest.raises(SystemExit) as exc_info:
        run_main(_coro())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Keyring error: keyring locked" in err
    assert "synology-apm-cli config set --save-password plaintext" in err


def test_run_main_keyboard_interrupt_exits_130(capsys: pytest.CaptureFixture[str]) -> None:
    async def _coro() -> int | None:
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exc_info:
        run_main(_coro())
    assert exc_info.value.code == 130
    assert "Aborted." in capsys.readouterr().err


# ── _remove_quietly ────────────────────────────────────────────────────────────


def test_remove_quietly_removes_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "test_file.txt"
    path.write_text("data")
    _remove_quietly(str(path))
    assert not path.exists()


def test_remove_quietly_does_not_raise_for_missing_file() -> None:
    _remove_quietly("/nonexistent/path/that/does/not/exist.tmp")


# ── Progress ───────────────────────────────────────────────────────────────────


def test_progress_line_reports_counts_with_labels() -> None:
    progress = Progress(total=5, noun="video")
    progress.exporting = 2
    progress.downloading = 1
    progress.done = 2
    line = progress.line()
    assert "2 exporting tasks, 1 downloading, 3 videos remaining" in line
    assert line.endswith("elapsed")


def test_progress_line_show_exporting_false_omits_exporting_part() -> None:
    progress = Progress(total=4, noun="user", show_exporting=False)
    progress.downloading = 3
    progress.done = 1
    line = progress.line()
    assert "exporting" not in line
    assert "3 downloading, 3 users remaining" in line


def test_print_progress_rewrites_line_in_place(capsys: pytest.CaptureFixture[str]) -> None:
    progress = Progress(total=2, noun="item", show_exporting=False)
    progress.print_progress()
    out = capsys.readouterr().out
    assert out.startswith("\r\x1b[K")
    assert "2 items remaining" in out


def test_clear_progress_after_print_erases_line(capsys: pytest.CaptureFixture[str]) -> None:
    progress = Progress(total=2, noun="item")
    progress.print_progress()
    capsys.readouterr()  # discard the progress line itself
    progress.clear_progress()
    assert capsys.readouterr().out == "\r\x1b[K"


def test_clear_progress_without_prior_print_emits_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    progress = Progress(total=2, noun="item")
    progress.clear_progress()
    assert capsys.readouterr().out == ""


# ── interruptible_sleep ────────────────────────────────────────────────────────


async def test_interruptible_sleep_returns_true_when_event_already_set() -> None:
    event = asyncio.Event()
    event.set()
    # A pre-set event returns immediately, well before the 60 s timeout.
    assert await interruptible_sleep(60.0, event) is True


async def test_interruptible_sleep_returns_false_on_timeout() -> None:
    event = asyncio.Event()
    assert await interruptible_sleep(0.01, event) is False


# ── prompt_yes_no ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("y\n", True),
        ("yes\n", True),
        ("Y\n", True),
        ("n\n", False),
        ("anything else\n", False),
        ("", False),  # EOF (Ctrl+D) declines
    ],
)
async def test_prompt_yes_no_answers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
    expected: bool,
) -> None:
    # StringIO has no fileno(), exercising the executor fallback read path.
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    assert await prompt_yes_no("Continue? [y/N] ") is expected
    assert "Continue? [y/N] " in capsys.readouterr().err


async def test_prompt_yes_no_reads_from_fd_backed_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real pipe fd exercises the loop.add_reader path: the answer is already
    # buffered in the kernel, so the readable callback fires immediately.
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"yes\n")
    os.close(write_fd)
    reader = os.fdopen(read_fd)
    monkeypatch.setattr("sys.stdin", reader)
    try:
        assert await prompt_yes_no("Continue? [y/N] ") is True
    finally:
        reader.close()


# ── register_interrupt / unregister_interrupt ──────────────────────────────────


async def test_register_interrupt_sigint_sets_event_instead_of_raising() -> None:
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    register_interrupt(loop, event)
    try:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.wait_for(event.wait(), timeout=2.0)
    finally:
        unregister_interrupt(loop)
    assert event.is_set()


async def test_unregister_interrupt_removes_sigint_handler() -> None:
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    register_interrupt(loop, event)
    unregister_interrupt(loop)
    # The loop-level handler is gone: removing again reports nothing to remove.
    assert loop.remove_signal_handler(signal.SIGINT) is False
