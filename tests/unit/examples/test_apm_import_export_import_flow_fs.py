"""Unit tests for the import pipeline in examples/apm_import_export.py.

Covers the file-server import subtopic: request building (create/overwrite), per-item
execution against the SDK, and YAML entry parsing/reference resolution.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import apm_import_export as ie
import pytest

from synology_apm.sdk import (
    DuplicateWorkloadError,
    FileServerAddRequest,
    FileServerType,
    FileServerUpdateRequest,
    MachineWorkloadType,
)
from tests.unit.examples._fixtures import (
    make_backup_server,
    make_fake_apm,
    make_file_server_config,
    make_machine_workload,
)

_MACHINE_PLAN_UUID = "123e4567-e89b-12d3-a456-426614174001"
_PLANS_BY_NAME = {"Daily Backup": _MACHINE_PLAN_UUID}


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


@pytest.mark.parametrize(
    ("credentials", "expected_error"),
    [
        (
            {("192.0.2.99", "admin"): "other-pw"},
            "credential not found for endpoint='10.0.0.10', login_user='admin' "
            "in fs-credentials file",
        ),
        (
            {("10.0.0.10", "admin"): ""},
            "password is empty for endpoint='10.0.0.10', login_user='admin' "
            "in fs-credentials file",
        ),
    ],
    ids=["missing-credential-row", "empty-password"],
)
def test_build_fs_requests_create_credential_lookup_errors(
    credentials: dict[tuple[str, str], str], expected_error: str
) -> None:
    """A create entry whose credential row is missing, or whose password is empty,
    records a parse_error and marks the action as "error"."""
    fse = _make_fs_entry()
    actions = _fs_actions_for(fse, "create")

    ie._build_fs_requests([fse], credentials, actions, _PLANS_BY_NAME)

    assert fse.parse_error == expected_error
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


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("overwrite", ("failed", "existing workload not found")),
        ("create", ("failed", "internal error: add request not built")),
    ],
    ids=["overwrite-without-existing-workload", "create-with-wrong-request-type"],
)
async def test_execute_one_fs_fails_fast_on_action_request_mismatch(
    action: str, expected: tuple[str, str]
) -> None:
    """An "overwrite" with no existing workload, or a "create" whose request was built
    as an update request, fails without calling the SDK."""
    apm = make_fake_apm()
    entry = _make_fs_entry()
    entry.request = FileServerUpdateRequest(
        host_ip="10.0.0.10", login_user="admin", login_password=None
    )

    result = await ie._execute_one_fs(apm, entry, action, None)

    assert (result.result, result.error_msg) == expected


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
