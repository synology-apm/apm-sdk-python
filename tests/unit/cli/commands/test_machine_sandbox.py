"""Unit tests for synology-apm-cli machine sandbox: resources / start / screenshot / connect / vnc."""
from __future__ import annotations

import contextlib
import dataclasses
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import Result

from synology_apm.sdk import SandboxSettingsError
from synology_apm.sdk.enums import (
    MachineWorkloadType,
    RestoreType,
    SandboxAdapterModel,
    SandboxDiskController,
    SandboxKey,
    SandboxMachineType,
    SandboxVideoCard,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.exceptions import InvalidOperationError
from synology_apm.sdk.models.activity import RestoreActivity
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.sandbox import (
    SandboxDisk,
    SandboxNetworkAdapter,
    SandboxSettings,
    SandboxVncEndpoint,
)
from synology_apm.sdk.models.version import WorkloadVersion
from synology_apm.sdk.models.workload import MachineWorkload
from tests.unit.cli.conftest import invoke_cli
from tests.unit.sandbox_builders import (
    WL_ID,
    WL_NS,
    make_sandbox_activity,
    make_sandbox_location,
    make_sandbox_resources,
    make_sandbox_settings,
)

LOC_B = "123e4567-e89b-12d3-a456-426614174011"
VER_ID = "123e4567-e89b-12d3-a456-426614174012"

WL = MachineWorkload(
    workload_id=WL_ID,
    name="prod-server-01",
    category=WorkloadCategory.MACHINE,
    namespace=WL_NS,
    last_backup_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    is_retired=False,
    protected_data_bytes=1024,
    status=WorkloadStatus.SUCCESS,
    plan=ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE),
    workload_type=MachineWorkloadType.PS,
    agent_version="1.2.0",
    backup_server=LocationInfo(
        is_remote_storage=False, identifier=WL_NS, name="apm-server-01", endpoint="192.0.2.1", vault=None,
    ),
)

VERSION = WorkloadVersion(
    version_id=VER_ID,
    workload_id=WL_ID,
    namespace=WL_NS,
    created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
    status=VersionStatus.SUCCESS,
    execution_id="ABE_1",
    locked=False,
    changed_size_bytes=1024,
)

SETTINGS = make_sandbox_settings()

# Every setting differs from SETTINGS, so a flag wired to the wrong field is caught.
FULL_FLAGS = [
    "--location-id", LOC_B,
    "--vm-name", "drill vm",
    "--cpu", "2",
    "--memory", "2048",
    "--cpu-compatibility-mode",
    "--no-hyperv",
    "--video-card", "vmvga",
    "--machine-type", "pc",
    "--nic", "net-2:e1000:on",
    "--nic", "net-3:virtio:off",
    "--disk", "ide:on",
    "--disk", "scsi:off",
]
FULL_SETTINGS = SandboxSettings(
    location_id=LOC_B,
    name="drill vm",
    cpu_count=2,
    memory_mb=2048,
    cpu_compatibility_mode=True,
    hyperv_enlightenments=False,
    video_card=SandboxVideoCard.VMVGA,
    machine_type=SandboxMachineType.PC,
    network_adapters=(
        SandboxNetworkAdapter("net-2", SandboxAdapterModel.E1000, sriov=True),
        SandboxNetworkAdapter("net-3", SandboxAdapterModel.VIRTIO, sriov=False),
    ),
    disks=(
        SandboxDisk(SandboxDiskController.IDE, space_reclamation=True),
        SandboxDisk(SandboxDiskController.SCSI, space_reclamation=False),
    ),
)

RESOURCES = make_sandbox_resources(
    make_sandbox_location(),
    make_sandbox_location(location_id=LOC_B, name="apm-server-04", max_cpu_count=4, max_memory_mb=8192, networks=()),
)

READY = make_sandbox_activity()

CONSOLE_LINK = "https://192.0.2.2/sharing/abc"

START = ["machine", "sandbox", "start", "prod-server-01"]
SANDBOX_LIST = dict(keyword="prod-server-01", restore_types=[RestoreType.INSTANT_SANDBOX], limit=100, offset=0)


@pytest.fixture
def apm() -> AsyncMock:
    m = AsyncMock()
    m.machine.workloads.get_by_name.return_value = WL
    m.machine.workloads.get.return_value = WL
    m.machine.workloads.get_version.return_value = VERSION
    m.machine.sandboxes.get_latest_version.return_value = VERSION
    m.machine.sandboxes.get_default_settings.return_value = SETTINGS
    m.machine.sandboxes.get_resources.return_value = RESOURCES
    m.machine.sandboxes.start.return_value = None
    m.machine.sandboxes.get_console_link.return_value = CONSOLE_LINK
    m.activities.restore.get.return_value = READY
    m.activities.restore.list.return_value = ([READY], 1)
    return m


def _line(output: str, label: str) -> str:
    return next(line for line in output.splitlines() if line.strip().startswith(label))


# ── resources ────────────────────────────────────────────────────────────────────────

def test_resources_uses_the_latest_sandbox_version_and_reports_it(apm: AsyncMock) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "resources", "prod-server-01"])

    assert result.exit_code == 0, result.output
    apm.machine.workloads.get_by_name.assert_awaited_once_with("prod-server-01", is_retired=False)
    apm.machine.sandboxes.get_latest_version.assert_awaited_once_with(WL)
    apm.machine.sandboxes.get_resources.assert_awaited_once_with(WL, VERSION)
    assert VER_ID in _line(result.stderr, "(Using version:")


def test_resources_with_version_id_resolves_it(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [
        "machine", "sandbox", "resources", "--workload-id", WL_ID, "--namespace", WL_NS, "--version-id", VER_ID,
    ])

    assert result.exit_code == 0, result.output
    apm.machine.workloads.get.assert_awaited_once_with(WL_ID, namespace=WL_NS)
    apm.machine.workloads.get_version.assert_awaited_once_with(WL, VER_ID)
    apm.machine.sandboxes.get_latest_version.assert_not_awaited()
    assert "Using version" not in result.stderr


def test_resources_sections_per_location(apm: AsyncMock) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "resources", "prod-server-01"])

    assert result.exit_code == 0, result.output
    assert "(default)" in _line(result.stdout, "── Location: apm-server-03")
    assert "(default)" not in _line(result.stdout, "── Location: apm-server-04")
    assert "1 - 8" in _line(result.stdout, "CPU:")
    assert "128 - 16384 MB" in _line(result.stdout, "Memory:")
    assert "supported" in _line(result.stdout, "net-2 ")
    assert "none" in _line(result.stdout, "Networks:")


def test_resources_json(apm: AsyncMock) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "resources", "prod-server-01", "-o", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == RESOURCES.to_dict()


def test_start_without_a_sandbox_version_exits_1(apm: AsyncMock) -> None:
    apm.machine.sandboxes.get_latest_version.side_effect = InvalidOperationError(
        "Workload 'prod-server-01' has no successful or partially successful version to restore to a sandbox.",
        resource_type="Workload", resource_id=WL_ID,
    )

    result = invoke_cli(apm, [*START, "-y"])

    assert result.exit_code == 1
    assert "has no successful or partially successful version" in _line(result.stderr, "✗")
    apm.machine.sandboxes.get_default_settings.assert_not_awaited()


# ── start: settings ──────────────────────────────────────────────────────────────────

def test_start_without_flags_uses_the_default_settings(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "-y"])

    assert result.exit_code == 0, result.output
    apm.machine.sandboxes.get_default_settings.assert_awaited_once_with(WL, VERSION, location_id=None)
    apm.machine.sandboxes.start.assert_awaited_once_with(WL, VERSION, SETTINGS)


def test_start_flags_override_every_setting(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, *FULL_FLAGS, "-y"])

    assert result.exit_code == 0, result.output
    # The defaults come from the chosen location; every flag replaces its setting.
    apm.machine.sandboxes.get_default_settings.assert_awaited_once_with(WL, VERSION, location_id=LOC_B)
    apm.machine.sandboxes.start.assert_awaited_once_with(WL, VERSION, FULL_SETTINGS)


def test_start_marks_every_setting_given_on_the_command_line(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, *FULL_FLAGS, "-y"])

    assert result.exit_code == 0, result.output
    rows = result.stderr.split("Sandbox settings:", 1)[1].split("✓", 1)[0].splitlines()
    settings_rows = [r for r in rows if r.strip() and not r.strip().startswith(("Workload:", "Version:"))]
    # 8 single-row settings, 2 adapters, 2 disks.
    assert len(settings_rows) == 12
    assert all(r.rstrip().endswith("(set)") for r in settings_rows), settings_rows


def test_start_no_network_replaces_the_default_adapters(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--no-network", "-y"])

    assert result.exit_code == 0, result.output
    assert apm.machine.sandboxes.start.await_args.args[2].network_adapters == ()


def test_start_prints_the_final_settings_and_marks_changes(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--memory", "2048", "-y"])

    assert result.exit_code == 0, result.output
    assert "2048 MB (set)" in _line(result.stderr, "Memory:")
    assert "(set)" not in _line(result.stderr, "CPU:")
    assert f"prod-server-01 (Physical Server, ID: {WL_ID})" in _line(result.stderr, "Workload:")
    assert VER_ID in _line(result.stderr, "Version:")
    assert "net-1 · RTL8139 · SR-IOV off" in _line(result.stderr, "Network adapter 1:")
    assert "SATA controller · space reclamation off" in _line(result.stderr, "Virtual disk 2:")


def test_start_with_version_id_does_not_pick_the_latest(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--version-id", VER_ID, "-y"])

    assert result.exit_code == 0, result.output
    apm.machine.workloads.get_version.assert_awaited_once_with(WL, VER_ID)
    apm.machine.sandboxes.get_latest_version.assert_not_awaited()


@pytest.mark.parametrize(("args", "message"), [
    (["--nic", "net-1:e1000:on", "--no-network"], "--nic and --no-network are mutually exclusive"),
    (["--nic", "net-1:e1000"], "--nic 'net-1:e1000': expected"),
    (["--nic", "net-1:fast:on"], "--nic 'net-1:fast:on': expected"),
    (["--nic", "net-1:e1000:yes"], "--nic 'net-1:e1000:yes': expected"),
    (["--disk", "sata"], "--disk 'sata': expected"),
    (["--disk", "nvme:on"], "--disk 'nvme:on': expected"),
    (["--video-card", "svga"], "Unsupported --video-card value: svga"),
    (["--machine-type", "i440"], "Unsupported --machine-type value: i440"),
], ids=[
    "nic-and-no-network", "nic-parts", "nic-model", "nic-sriov", "disk-parts", "disk-controller",
    "video-card", "machine-type",
])
def test_start_malformed_flag_exits_1_before_any_call(apm: AsyncMock, args: list[str], message: str) -> None:
    result = invoke_cli(apm, [*START, *args, "-y"])

    assert result.exit_code == 1
    assert message in result.stderr
    assert apm.method_calls == []


def _assert_resources_hint(result: Result) -> None:
    hint = _line(result.stderr, "Allowed values:")
    assert (
        f"synology-apm-cli machine sandbox resources --workload-id {WL_ID} --namespace {WL_NS} --version-id {VER_ID}"
    ) in hint


# Every SandboxSettings field, and the flag an error about it names.
FIELD_FLAGS = {
    "location_id": "--location-id",
    "name": "--vm-name",
    "cpu_count": "--cpu",
    "memory_mb": "--memory",
    "cpu_compatibility_mode": "--cpu-compatibility-mode",
    "hyperv_enlightenments": "--hyperv",
    "video_card": "--video-card",
    "machine_type": "--machine-type",
    "network_adapters": "--nic",
    "disks": "--disk",
}


def test_field_flags_cover_every_setting() -> None:
    assert set(FIELD_FLAGS) == {f.name for f in dataclasses.fields(SandboxSettings)}


@pytest.mark.parametrize(("field", "flag"), FIELD_FLAGS.items(), ids=list(FIELD_FLAGS))
def test_start_settings_error_names_the_flag_of_its_field(apm: AsyncMock, field: str, flag: str) -> None:
    apm.machine.sandboxes.start.side_effect = SandboxSettingsError("Not allowed here.", field=field)

    result = invoke_cli(apm, [*START, "-y"])

    assert result.exit_code == 1
    assert _line(result.stderr, "✗") == f"✗ {flag}: Not allowed here."


def test_start_setting_breaking_its_own_rule_names_the_flag(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--vm-name", "drill/01", "-y"])

    assert result.exit_code == 1
    assert _line(result.stderr, "✗") == "✗ --vm-name: name must not contain '/'."
    assert "Allowed values" not in result.stderr
    apm.machine.sandboxes.start.assert_not_awaited()


def test_start_pair_error_names_the_one_flag_given(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--cpu-compatibility-mode", "-y"])

    assert result.exit_code == 1
    assert _line(result.stderr, "✗").startswith("✗ --cpu-compatibility-mode: ")
    apm.machine.sandboxes.start.assert_not_awaited()


def test_start_pair_error_names_both_flags_when_both_are_given(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--cpu-compatibility-mode", "--hyperv", "-y"])

    assert result.exit_code == 1
    assert _line(result.stderr, "✗").startswith("✗ --hyperv and --cpu-compatibility-mode: ")
    apm.machine.sandboxes.start.assert_not_awaited()


def test_start_setting_rejected_by_the_location_points_at_resources(apm: AsyncMock) -> None:
    apm.machine.sandboxes.start.side_effect = SandboxSettingsError(
        "memory_mb must be between 128 and 16384.", field="memory_mb",
    )

    result = invoke_cli(apm, [*START, "--memory", "99999", "-y"])

    assert result.exit_code == 1
    assert _line(result.stderr, "✗") == "✗ --memory: memory_mb must be between 128 and 16384."
    _assert_resources_hint(result)


def test_start_unknown_location_points_at_resources(apm: AsyncMock) -> None:
    apm.machine.sandboxes.get_default_settings.side_effect = SandboxSettingsError(
        "location_id 'loc-x' is not a backup-server location of the version.", field="location_id",
    )

    result = invoke_cli(apm, [*START, "--location-id", "loc-x", "-y"])

    assert result.exit_code == 1
    apm.machine.sandboxes.get_default_settings.assert_awaited_once_with(WL, VERSION, location_id="loc-x")
    assert _line(result.stderr, "✗").startswith("✗ --location-id: location_id 'loc-x' is not")
    _assert_resources_hint(result)


def test_start_other_sdk_error_exits_1_without_the_hint(apm: AsyncMock) -> None:
    apm.machine.sandboxes.start.side_effect = InvalidOperationError(
        "Workload 'prod-server-01' is retired and cannot start a sandbox.",
        resource_type="Workload", resource_id=WL_ID,
    )

    result = invoke_cli(apm, [*START, "-y"])

    assert result.exit_code == 1
    assert "is retired" in _line(result.stderr, "✗")
    assert "Allowed values" not in result.stderr


# ── start: dry run, confirmation ─────────────────────────────────────────────────────

def test_start_dry_run_checks_and_starts_nothing(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--cpu", "2", "--dry-run"])

    assert result.exit_code == 0, result.output
    apm.machine.sandboxes.validate_settings.assert_awaited_once_with(
        WL, VERSION, dataclasses.replace(SETTINGS, cpu_count=2),
    )
    apm.machine.sandboxes.start.assert_not_awaited()
    assert "2 (set)" in _line(result.stderr, "CPU:")
    assert result.stdout == "✓ Settings are valid; nothing was started.\n"


def test_start_dry_run_json_is_the_settings(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--dry-run", "-o", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == SETTINGS.to_dict()


def test_start_dry_run_rejection_points_at_resources(apm: AsyncMock) -> None:
    apm.machine.sandboxes.validate_settings.side_effect = SandboxSettingsError(
        "cpu_count must be between 1 and 8.", field="cpu_count",
    )

    result = invoke_cli(apm, [*START, "--cpu", "9", "--dry-run"])

    assert result.exit_code == 1
    _assert_resources_hint(result)
    assert result.stdout == ""


def test_start_declined_confirmation_does_not_start(apm: AsyncMock) -> None:
    result = invoke_cli(apm, START, input="n\n")

    assert result.exit_code == 4
    assert "Start this sandbox?" in result.output
    apm.machine.sandboxes.start.assert_not_awaited()


def test_start_confirmed_starts(apm: AsyncMock) -> None:
    result = invoke_cli(apm, START, input="y\n")

    assert result.exit_code == 0, result.output
    apm.machine.sandboxes.start.assert_awaited_once()


# ── start: output ───────────────────────────────────────────────────────────────────

def test_start_reports_the_started_sandbox_like_a_backup(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "-y"])

    assert result.exit_code == 0, result.output
    apm.activities.restore.list.assert_not_awaited()
    apm.activities.restore.get.assert_not_awaited()
    assert result.stdout.splitlines()[0] == "✓ Sandbox started."
    assert "prod-server-01" in _line(result.stdout, "Workload:")
    assert SETTINGS.name in _line(result.stdout, "Sandbox:")
    assert "`synology-apm-cli activity restore list` to check progress" in result.stdout


def test_start_json_is_the_settings_used(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "--memory", "2048", "-y", "-o", "json"])

    assert result.exit_code == 0, result.output
    apm.machine.sandboxes.start.assert_awaited_once()
    assert json.loads(result.stdout) == dataclasses.replace(SETTINGS, memory_mb=2048).to_dict()


def test_start_quiet_prints_nothing(apm: AsyncMock) -> None:
    result = invoke_cli(apm, [*START, "-y", "-q"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    apm.machine.sandboxes.start.assert_awaited_once()


# ── screenshot ───────────────────────────────────────────────────────────────────────

SHOT = b"\x89PNG-bytes"


def test_screenshot_by_id_writes_png_and_reports_path(apm: AsyncMock, tmp_path: Path) -> None:
    apm.machine.sandboxes.get_screenshot.return_value = SHOT
    out = tmp_path / "shot.png"

    result = invoke_cli(apm, ["machine", "sandbox", "screenshot", "--id", "rst-uid-001", "-f", str(out)])

    assert result.exit_code == 0, result.output
    apm.activities.restore.get.assert_awaited_once_with("rst-uid-001", include_logs=False)
    apm.machine.sandboxes.get_screenshot.assert_awaited_once_with(READY, keys=[], delay_seconds=None)
    assert out.read_bytes() == b"\x89PNG-bytes"
    assert str(out) in _line(result.stdout, "✓ Screenshot saved:")


def test_screenshot_by_name_uses_the_workloads_running_sandbox(apm: AsyncMock, tmp_path: Path) -> None:
    apm.machine.sandboxes.get_screenshot.return_value = SHOT

    result = invoke_cli(apm, [
        "machine", "sandbox", "screenshot", "prod-server-01", "-f", str(tmp_path / "s.png"),
        "--press", "shift", "--press", "ctrl_alt_delete", "--delay", "0",
    ])

    assert result.exit_code == 0, result.output
    apm.activities.restore.list.assert_awaited_once_with(**SANDBOX_LIST)
    apm.machine.sandboxes.get_screenshot.assert_awaited_once_with(
        READY, keys=[SandboxKey.SHIFT, SandboxKey.CTRL_ALT_DELETE], delay_seconds=0,
    )


@pytest.mark.parametrize(("args", "message"), [
    (["--press", "a"], "Unsupported --press value: a"),
    (["--delay", "3"], "--delay applies only with --press"),
], ids=["unknown-key", "delay-without-press"])
def test_screenshot_malformed_flag_exits_1(apm: AsyncMock, tmp_path: Path, args: list[str], message: str) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "screenshot", "--id", "rst-uid-001", "-f", str(tmp_path / "s.png"), *args])

    assert result.exit_code == 1
    assert message in result.stderr
    assert apm.method_calls == []


def test_screenshot_without_filename_shows_help(apm: AsyncMock) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "screenshot", "--id", "rst-uid-001"])

    assert result.exit_code == 0
    assert "--filename" in result.stdout
    assert apm.method_calls == []


def test_screenshot_asks_before_overwriting(apm: AsyncMock, tmp_path: Path) -> None:
    apm.machine.sandboxes.get_screenshot.return_value = SHOT
    out = tmp_path / "s.png"
    out.write_bytes(b"old")
    args = ["machine", "sandbox", "screenshot", "--id", "rst-uid-001", "-f", str(out)]

    declined = invoke_cli(apm, args, input="n\n")
    assert declined.exit_code == 4
    assert out.read_bytes() == b"old"
    apm.machine.sandboxes.get_screenshot.assert_not_awaited()

    forced = invoke_cli(apm, [*args, "-y"])
    assert forced.exit_code == 0, forced.output
    assert out.read_bytes() == b"\x89PNG-bytes"


def test_screenshot_unwritable_path_exits_1(apm: AsyncMock, tmp_path: Path) -> None:
    apm.machine.sandboxes.get_screenshot.return_value = SHOT
    out = tmp_path / "missing-dir" / "shot.png"

    result = invoke_cli(apm, ["machine", "sandbox", "screenshot", "--id", "rst-uid-001", "-f", str(out)])

    assert result.exit_code == 1
    assert "shot.png" in _line(result.stderr, "✗ Could not write")


# ── connect ──────────────────────────────────────────────────────────────────────────

def test_connect_prints_only_url_on_stdout_and_the_warning_on_stderr(apm: AsyncMock) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "connect", "--id", "rst-uid-001"])

    assert result.exit_code == 0, result.output
    apm.activities.restore.get.assert_awaited_once_with("rst-uid-001", include_logs=False)
    apm.machine.sandboxes.get_console_link.assert_awaited_once_with(READY)
    assert result.stdout == "https://192.0.2.2/sharing/abc\n"
    assert "Single-use link" in result.stderr


def test_connect_sdk_error_exits_1(apm: AsyncMock) -> None:
    apm.machine.sandboxes.get_console_link.side_effect = InvalidOperationError(
        "The sandbox is no longer running.", resource_type="RestoreActivity", resource_id="rst-uid-001",
    )

    result = invoke_cli(apm, ["machine", "sandbox", "connect", "prod-server-01"])

    assert result.exit_code == 1
    apm.activities.restore.list.assert_awaited_once_with(**SANDBOX_LIST)
    assert "The sandbox is no longer running." in _line(result.stderr, "✗")
    assert result.stdout == ""


def test_name_picks_the_sandbox_of_that_exact_workload(apm: AsyncMock) -> None:
    other_workload = dataclasses.replace(READY, activity_id="rst-uid-002", workload_name="prod-server-01-old")
    apm.activities.restore.list.return_value = ([other_workload, READY], 2)

    result = invoke_cli(apm, ["machine", "sandbox", "connect", "prod-server-01"])

    assert result.exit_code == 0, result.output
    apm.machine.sandboxes.get_console_link.assert_awaited_once_with(READY)


def test_name_looks_through_every_page_of_sandboxes(apm: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    others = [dataclasses.replace(READY, activity_id=f"rst-other-{i}", workload_name="prod-server-01b") for i in range(100)]
    apm.activities.restore.list.side_effect = [(others, 101), ([READY], 101)]

    result = invoke_cli(apm, ["machine", "sandbox", "connect", "prod-server-01"])

    assert result.exit_code == 0, result.output
    assert [c.kwargs["offset"] for c in apm.activities.restore.list.await_args_list] == [0, 100]
    apm.machine.sandboxes.get_console_link.assert_awaited_once_with(READY)


def test_name_and_id_together_exit_1(apm: AsyncMock) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "connect", "prod-server-01", "--id", "rst-uid-001"])

    assert result.exit_code == 1
    assert "NAME and --id are mutually exclusive" in result.stderr
    apm.machine.sandboxes.get_console_link.assert_not_awaited()


def test_name_without_an_ongoing_sandbox_exits_1(apm: AsyncMock) -> None:
    apm.activities.restore.list.return_value = ([], 0)

    result = invoke_cli(apm, ["machine", "sandbox", "connect", "prod-server-01"])

    assert result.exit_code == 1
    assert "prod-server-01 has no ongoing sandbox." in result.stderr
    apm.machine.sandboxes.get_console_link.assert_not_awaited()


def test_name_with_several_sandboxes_lists_them_and_exits_1(apm: AsyncMock) -> None:
    second = dataclasses.replace(READY, activity_id="rst-uid-002", restore_destination="drill vm 2")
    apm.activities.restore.list.return_value = ([READY, second], 2)

    result = invoke_cli(apm, ["machine", "sandbox", "connect", "prod-server-01"])

    assert result.exit_code == 1
    assert "has 2 ongoing sandboxes; choose one with --id" in result.stderr
    assert "drill vm 2" in _line(result.stderr, "rst-uid-002")
    assert "drill-01" in _line(result.stderr, "rst-uid-001")
    apm.machine.sandboxes.get_console_link.assert_not_awaited()


@pytest.mark.parametrize("command", ["screenshot", "connect", "vnc"])
def test_console_commands_without_name_or_id_show_help(apm: AsyncMock, command: str) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", command])

    assert result.exit_code == 0
    assert "--id" in result.stdout
    assert apm.method_calls == []


# ── vnc ──────────────────────────────────────────────────────────────────────────────

def _bridge(apm: AsyncMock, *, error: OSError | None = None) -> list[str]:
    """Make open_vnc_bridge() an async context manager; return its enter/exit trace."""
    trace: list[str] = []

    @contextlib.asynccontextmanager
    async def open_vnc_bridge(activity: RestoreActivity, *, port: int = 0) -> AsyncIterator[SandboxVncEndpoint]:
        trace.append(f"open {activity.activity_id} port={port}")
        if error:
            raise error
        try:
            yield SandboxVncEndpoint(host="127.0.0.1", port=port or 49152)
        finally:
            trace.append("closed")

    apm.machine.sandboxes.open_vnc_bridge = MagicMock(side_effect=open_vnc_bridge)
    return trace


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    wait = AsyncMock()
    monkeypatch.setattr("synology_apm.cli.commands.machine_sandbox.wait_for_interrupt", wait)
    return wait


def test_vnc_prints_address_waits_then_closes(apm: AsyncMock, no_wait: AsyncMock) -> None:
    trace = _bridge(apm)

    result = invoke_cli(apm, ["machine", "sandbox", "vnc", "--id", "rst-uid-001", "--port", "5901"])

    assert result.exit_code == 0, result.output
    apm.activities.restore.get.assert_awaited_once_with("rst-uid-001", include_logs=False)
    assert trace == [f"open {READY.activity_id} port=5901", "closed"]
    no_wait.assert_awaited_once()
    assert result.stdout == "vnc://127.0.0.1:5901\n"
    assert "Ctrl+C" in _line(result.stderr, "⚠ No password")
    assert "VNC relay closed." in result.stderr


def test_vnc_port_unavailable_exits_1(apm: AsyncMock, no_wait: AsyncMock) -> None:
    _bridge(apm, error=OSError(48, "Address already in use"))

    result = invoke_cli(apm, ["machine", "sandbox", "vnc", "--id", "rst-uid-001", "--port", "5901"])

    assert result.exit_code == 1
    assert "Address already in use" in _line(result.stderr, "✗ Could not open the VNC relay")
    assert result.stdout == ""
    no_wait.assert_not_awaited()


def test_vnc_error_after_opening_is_not_reported_as_an_open_failure(apm: AsyncMock, no_wait: AsyncMock) -> None:
    trace = _bridge(apm)
    no_wait.side_effect = BrokenPipeError(32, "Broken pipe")

    result = invoke_cli(apm, ["machine", "sandbox", "vnc", "--id", "rst-uid-001"])

    assert result.exit_code != 0
    assert "Could not open the VNC relay" not in result.stderr
    assert trace[-1] == "closed"


def test_vnc_port_out_of_range_exits_1(apm: AsyncMock, no_wait: AsyncMock) -> None:
    result = invoke_cli(apm, ["machine", "sandbox", "vnc", "--id", "rst-uid-001", "--port", "70000"])

    assert result.exit_code == 1
    assert _line(result.stderr, "✗") == "✗ --port 70000 is not between 0 and 65535"
    assert apm.method_calls == []
