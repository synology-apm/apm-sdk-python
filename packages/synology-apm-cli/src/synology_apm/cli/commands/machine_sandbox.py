"""synology-apm-cli machine sandbox resources / start / screenshot / connect / vnc.

resources and start take the workload as <name> (search mode) or --workload-id + --namespace
(direct mode). The console commands (screenshot, connect, vnc) take the sandbox's restore
activity as <name> (the workload's one ongoing sandbox) or --id.
"""
from __future__ import annotations

import asyncio
import contextlib
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli._display import (
    fmt_datetime,
    fmt_machine_workload_type,
    fmt_memory_mb,
    fmt_on_off,
    fmt_sandbox_adapter_model,
    fmt_sandbox_disk_controller,
    fmt_sandbox_machine_type,
    fmt_sandbox_video_card,
    fmt_sriov_support,
)
from synology_apm.cli._helpers import api_spinner, apm_session, confirm_overwrite, wait_for_interrupt
from synology_apm.cli._options import OUTPUT_OPTION
from synology_apm.cli._validate import (
    SANDBOX_KEY_ARGS,
    SANDBOX_MACHINE_TYPE_ARGS,
    SANDBOX_VIDEO_CARD_ARGS,
    WorkloadRef,
    parse_enum_list,
    parse_enum_scalar,
    print_resolved_version,
    require_or_help,
    resolve_by_name_or_id,
    validate_name_or_id_args,
    validate_version_workload_args,
)
from synology_apm.cli.errors import EXIT_ERROR, err_console, fail
from synology_apm.cli.output import (
    OutputFormat,
    cell,
    console,
    dispatch_output,
    new_table,
)
from synology_apm.sdk import (
    APMClient,
    MachineWorkload,
    RestoreActivity,
    RestoreType,
    SandboxAdapterModel,
    SandboxDisk,
    SandboxDiskController,
    SandboxNetworkAdapter,
    SandboxResources,
    SandboxSettings,
    SandboxSettingsError,
    WorkloadVersion,
)

sandbox_app = typer.Typer(
    help="Run sandbox recovery drills: boot a PS/VM backup version on the built-in hypervisor.",
    no_args_is_help=True,
)

_PAGE_SIZE = 100
_ON_OFF = {"on": True, "off": False}
_NIC_FORMAT = (
    f"NETWORK_ID:MODEL:on|off (MODEL: {', '.join(m.value for m in SandboxAdapterModel)}; on|off: SR-IOV)"
)
_DISK_FORMAT = (
    f"CONTROLLER:on|off (CONTROLLER: {', '.join(c.value for c in SandboxDiskController)}; "
    "on|off: space reclamation)"
)
_LABEL_WIDTH = 24

_PRESS_HELP = "Key to press before the capture; repeat for several keys, pressed in order: " + ", ".join(
    SANDBOX_KEY_ARGS
)
_VIDEO_CARD_HELP = "Video card: " + ", ".join(SANDBOX_VIDEO_CARD_ARGS)
_MACHINE_TYPE_HELP = "Machine type: " + ", ".join(SANDBOX_MACHINE_TYPE_ARGS)

_NAME_ARG = typer.Argument(None, help="Workload name (search mode)")
_WORKLOAD_ID_OPTION = typer.Option(None, "--workload-id", help="Workload ID (direct mode)")
_NAMESPACE_OPTION = typer.Option(None, "--namespace", "-n", help="Backup server namespace (direct mode)")
_VERSION_ID_OPTION = typer.Option(
    None, "--version-id", help="Version ID (from version list; omit for the latest successful or partial version)",
)
_ACTIVITY_NAME_ARG = typer.Argument(None, help="Workload name (search mode; its one ongoing sandbox)")
_ACTIVITY_ID_OPTION = typer.Option(None, "--id", help="Restore activity ID of the sandbox (direct mode)")


# ── Flag parsing ──────────────────────────────────────────────────────────────────────

def _parse_on_off(raw: str, flag: str, fmt: str, value: str) -> bool:
    if raw not in _ON_OFF:
        fail(cell(f"{flag} '{value}': expected {fmt}"))
    return _ON_OFF[raw]


def _parse_nic(value: str) -> SandboxNetworkAdapter:
    parts = value.rsplit(":", 2)
    if len(parts) != 3 or not parts[0]:
        fail(cell(f"--nic '{value}': expected {_NIC_FORMAT}"))
    network_id, model, sriov = parts
    try:
        model_enum = SandboxAdapterModel(model)
    except ValueError:
        fail(cell(f"--nic '{value}': expected {_NIC_FORMAT}"))
    return SandboxNetworkAdapter(
        network_id=network_id, model=model_enum, sriov=_parse_on_off(sriov, "--nic", _NIC_FORMAT, value),
    )


def _parse_disk(value: str) -> SandboxDisk:
    parts = value.split(":")
    if len(parts) != 2:
        fail(cell(f"--disk '{value}': expected {_DISK_FORMAT}"))
    try:
        controller = SandboxDiskController(parts[0])
    except ValueError:
        fail(cell(f"--disk '{value}': expected {_DISK_FORMAT}"))
    return SandboxDisk(controller=controller, space_reclamation=_parse_on_off(parts[1], "--disk", _DISK_FORMAT, value))


def _parse_changes(
    *,
    location_id: str | None,
    vm_name: str | None,
    cpu: int | None,
    memory: int | None,
    cpu_compatibility_mode: bool | None,
    hyperv: bool | None,
    video_card: str | None,
    machine_type: str | None,
    nics: list[str] | None,
    no_network: bool,
    disks: list[str] | None,
) -> dict[str, Any]:
    """The settings given on the command line, keyed like SandboxSettings.to_dict().

    Exits with an error for a malformed flag value.
    """
    if nics and no_network:
        fail("--nic and --no-network are mutually exclusive")
    changes: dict[str, Any] = {
        "location_id": location_id,
        "name": vm_name,
        "cpu_count": cpu,
        "memory_mb": memory,
        "cpu_compatibility_mode": cpu_compatibility_mode,
        "hyperv_enlightenments": hyperv,
        "video_card": parse_enum_scalar(video_card, SANDBOX_VIDEO_CARD_ARGS, "video-card"),
        "machine_type": parse_enum_scalar(machine_type, SANDBOX_MACHINE_TYPE_ARGS, "machine-type"),
        "network_adapters": [] if no_network else [_parse_nic(n) for n in nics] if nics else None,
        "disks": [_parse_disk(d) for d in disks] if disks else None,
    }
    return {k: v for k, v in changes.items() if v is not None}


# ── Shared helpers ────────────────────────────────────────────────────────────────────

async def _resolve_version(
    apm: APMClient, ref: WorkloadRef, version_id: str | None, spinner: str,
) -> tuple[MachineWorkload, WorkloadVersion]:
    """The workload and the version (the latest one a sandbox can use when version_id is None).

    Reports the version when it was picked automatically.
    """
    with api_spinner(spinner):
        wl = await ref.resolve_machine(apm)
        if version_id is not None:
            version = await apm.machine.workloads.get_version(wl, version_id)
        else:
            version = await apm.machine.sandboxes.get_latest_version(wl)
    print_resolved_version(version_id, version)
    return wl, version


async def _ongoing_sandbox(apm: APMClient, name: str) -> RestoreActivity:
    """The one ongoing sandbox of the workload named ``name``; exits when there is none or several."""
    found: list[RestoreActivity] = []
    while True:
        page, total = await apm.activities.restore.list(
            keyword=name, restore_types=[RestoreType.INSTANT_SANDBOX], limit=_PAGE_SIZE, offset=len(found),
        )
        found.extend(page)
        if len(page) < _PAGE_SIZE or (total is not None and len(found) >= total):
            break
    sandboxes = [a for a in found if a.workload_name == name]
    if len(sandboxes) == 1:
        return sandboxes[0]
    if not sandboxes:
        fail(f"{cell(name)} has no ongoing sandbox.")
    err_console.print(f"[red]✗[/red] {cell(name)} has {len(sandboxes)} ongoing sandboxes; choose one with --id:")
    for a in sandboxes:
        err_console.print(f"  {cell(a.activity_id)}  {cell(a.restore_destination or '')}", soft_wrap=True)
    raise typer.Exit(code=EXIT_ERROR)


async def _sandbox_activity(apm: APMClient, name: str | None, activity_id: str | None) -> RestoreActivity:
    """The sandbox's restore activity, by ID or as the workload's one ongoing sandbox.

    Readiness is checked by the console call that uses it.
    """
    return await resolve_by_name_or_id(
        name, activity_id,
        lambda i: apm.activities.restore.get(i, include_logs=False),
        lambda n: _ongoing_sandbox(apm, n),
    )


def _wl_args(wl: MachineWorkload) -> str:
    return f"--workload-id {shlex.quote(wl.workload_id)} --namespace {shlex.quote(wl.namespace)}"


def _row(label: str, value: str, *, changed: bool = False) -> None:
    note = " [bright_black](set)[/bright_black]" if changed else ""
    err_console.print(f"  {label + ':':<{_LABEL_WIDTH}}{cell(value)}{note}", soft_wrap=True)


# Single-row settings: (label, SandboxSettings field, formatter); network adapters and disks
# follow.
_SETTING_ROWS: tuple[tuple[str, str, Callable[[Any], str]], ...] = (
    ("Location ID", "location_id", str),
    ("Name", "name", str),
    ("CPU", "cpu_count", str),
    ("Memory", "memory_mb", fmt_memory_mb),
    ("CPU compatibility mode", "cpu_compatibility_mode", fmt_on_off),
    ("Hyper-V enlightenments", "hyperv_enlightenments", fmt_on_off),
    ("Video card", "video_card", fmt_sandbox_video_card),
    ("Machine type", "machine_type", fmt_sandbox_machine_type),
)


def _print_settings(wl: MachineWorkload, version: WorkloadVersion, s: SandboxSettings, changed: set[str]) -> None:
    """The final settings (to stderr); those given on the command line are marked."""
    _row("Workload", f"{wl.name} ({fmt_machine_workload_type(wl)}, ID: {wl.workload_id})")
    _row("Version", f"{version.version_id} ({fmt_datetime(version.created_at)})")
    for label, field, fmt in _SETTING_ROWS:
        _row(label, fmt(getattr(s, field)), changed=field in changed)
    nic_changed = "network_adapters" in changed
    if not s.network_adapters:
        _row("Network", "No connection", changed=nic_changed)
    for i, a in enumerate(s.network_adapters, 1):
        _row(
            f"Network adapter {i}",
            f"{a.network_id} · {fmt_sandbox_adapter_model(a.model)} · SR-IOV {fmt_on_off(a.sriov)}",
            changed=nic_changed,
        )
    for i, d in enumerate(s.disks, 1):
        _row(
            f"Virtual disk {i}",
            f"{fmt_sandbox_disk_controller(d.controller)} · space reclamation {fmt_on_off(d.space_reclamation)}",
            changed="disks" in changed,
        )


# SandboxSettings field → the flag that sets it, for error messages.
_FIELD_FLAGS = {
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
# Settings whose allowed values `machine sandbox resources` lists.
_RESOURCE_FIELDS = frozenset({"location_id", "cpu_count", "memory_mb", "network_adapters"})


def _settings_error(
    exc: SandboxSettingsError, given: set[str], wl: MachineWorkload, version: WorkloadVersion,
) -> typer.Exit:
    """Report a settings error under the flags that set it; point at the allowed values if listed."""
    fields = [exc.field] if exc.other_field is None else [f for f in (exc.field, exc.other_field) if f in given]
    flags = " and ".join(_FIELD_FLAGS.get(f, f) for f in fields or [exc.field])
    err_console.print(f"[red]✗[/red] {cell(flags)}: {cell(exc.message)}", soft_wrap=True)
    if exc.field in _RESOURCE_FIELDS:
        err_console.print(
            f"  Allowed values: synology-apm-cli machine sandbox resources {_wl_args(wl)} "
            f"--version-id {shlex.quote(version.version_id)}",
            markup=False, highlight=False, soft_wrap=True,
        )
    return typer.Exit(code=EXIT_ERROR)


# ── synology-apm-cli machine sandbox resources ─────────────────────────────────────────

@sandbox_app.command("resources")
@run_async
async def sandbox_resources(
    ctx: typer.Context,
    name: str | None = _NAME_ARG,
    version_id: str | None = _VERSION_ID_OPTION,
    workload_id: str | None = _WORKLOAD_ID_OPTION,
    namespace: str | None = _NAMESPACE_OPTION,
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Show allowed sandbox settings per location: CPU and memory limits, networks.

    \b
    Examples:
      synology-apm-cli machine sandbox resources "prod-server-01"
      synology-apm-cli machine sandbox resources --workload-id <wl-id> --namespace <ns> --version-id <ver-id>
    """
    ref = validate_version_workload_args(ctx, name, workload_id, namespace)
    async with apm_session(ctx) as apm:
        wl, version = await _resolve_version(apm, ref, version_id, "Loading sandbox resources...")
        with api_spinner("Loading sandbox resources..."):
            resources = await apm.machine.sandboxes.get_resources(wl, version)

    if dispatch_output(resources, output, SandboxResources.to_dict):
        return
    console.print(f"[bold]Sandbox Resources — {cell(wl.name)}[/bold]")
    for i, loc in enumerate(resources.locations):
        suffix = " (default)" if i == 0 else ""
        console.print()
        console.print(f"[bold]── Location: {cell(loc.name)}{suffix}[/bold]")
        console.print(f"{'Location ID:':<{_LABEL_WIDTH}}{cell(loc.location_id)}")
        console.print(f"{'CPU:':<{_LABEL_WIDTH}}1 - {loc.max_cpu_count}")
        console.print(f"{'Memory:':<{_LABEL_WIDTH}}{loc.min_memory_mb} - {fmt_memory_mb(loc.max_memory_mb)}")
        if loc.networks:
            t = new_table()
            t.add_column("Network ID")
            t.add_column("Network")
            t.add_column("SR-IOV")
            for n in loc.networks:
                t.add_row(cell(n.network_id), cell(n.name), fmt_sriov_support(n.supports_sriov))
            console.print(t)
        else:
            console.print(f"{'Networks:':<{_LABEL_WIDTH}}none")


# ── synology-apm-cli machine sandbox start ─────────────────────────────────────────────

@sandbox_app.command("start")
@run_async
async def sandbox_start(
    ctx: typer.Context,
    name: str | None = _NAME_ARG,
    version_id: str | None = _VERSION_ID_OPTION,
    workload_id: str | None = _WORKLOAD_ID_OPTION,
    namespace: str | None = _NAMESPACE_OPTION,
    location_id: str | None = typer.Option(
        None, "--location-id",
        help="Location ID from `machine sandbox resources` (default: the first one listed)",
    ),
    vm_name: str | None = typer.Option(
        None, "--vm-name", help="Sandbox VM name (default: <workload name>_<version date>_<time>)",
    ),
    cpu: int | None = typer.Option(None, "--cpu", help="Number of virtual CPUs"),
    memory: int | None = typer.Option(None, "--memory", help="Memory in MB"),
    cpu_compatibility_mode: bool | None = typer.Option(
        None, "--cpu-compatibility-mode/--no-cpu-compatibility-mode",
        help="CPU compatibility mode (default: off; requires --no-hyperv)",
    ),
    hyperv: bool | None = typer.Option(
        None, "--hyperv/--no-hyperv",
        help="Hyper-V enlightenments (default: on; must be off with CPU compatibility mode)",
    ),
    video_card: str | None = typer.Option(None, "--video-card", help=_VIDEO_CARD_HELP),
    machine_type: str | None = typer.Option(
        None, "--machine-type", help=_MACHINE_TYPE_HELP,
    ),
    nics: list[str] | None = typer.Option(
        None, "--nic", help=f"Network adapter, repeatable; replaces the default adapters: {_NIC_FORMAT}",
    ),
    no_network: bool = typer.Option(False, "--no-network", help="No network adapter"),
    disks: list[str] | None = typer.Option(
        None, "--disk", help=f"Virtual disk, once per backed-up disk in order; replaces the defaults: {_DISK_FORMAT}",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Check the settings and print them; start nothing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress the result message (the settings are still printed to stderr)",
    ),
    output: OutputFormat = OUTPUT_OPTION,
) -> None:
    """Start a sandbox from a backup version.

    Settings not given keep their defaults; `machine sandbox resources` lists the allowed
    values. The final settings are printed before the confirmation; -o json/yaml prints them
    instead of the result message. The sandbox is a restore activity: follow it with
    `activity restore list`/`get`, and stop it (which also deletes it) with
    `activity restore cancel`.

    \b
    Examples:
      synology-apm-cli machine sandbox start "prod-server-01" --dry-run
      synology-apm-cli machine sandbox start "prod-server-01" --memory 2048 --no-network --vm-name drill-01
      synology-apm-cli machine sandbox start --workload-id <wl-id> --namespace <ns> --version-id <ver-id> -y
    """
    ref = validate_version_workload_args(ctx, name, workload_id, namespace)
    changes = _parse_changes(
        location_id=location_id, vm_name=vm_name, cpu=cpu, memory=memory,
        cpu_compatibility_mode=cpu_compatibility_mode, hyperv=hyperv, video_card=video_card,
        machine_type=machine_type, nics=nics, no_network=no_network, disks=disks,
    )

    async with apm_session(ctx, abortable=True) as apm:
        wl, version = await _resolve_version(apm, ref, version_id, "Loading sandbox settings...")
        try:
            with api_spinner("Loading sandbox settings..."):
                defaults = await apm.machine.sandboxes.get_default_settings(
                    wl, version, location_id=changes.get("location_id"),
                )
            settings = defaults.with_changes(changes)
            err_console.print("Sandbox settings:")
            _print_settings(wl, version, settings, set(changes))
            if dry_run:
                with api_spinner("Checking the settings..."):
                    await apm.machine.sandboxes.validate_settings(wl, version, settings)
            else:
                if not yes:
                    typer.confirm("\n  Start this sandbox?", abort=True)
                with api_spinner("Starting sandbox..."):
                    await apm.machine.sandboxes.start(wl, version, settings)
        except SandboxSettingsError as exc:
            raise _settings_error(exc, set(changes), wl, version) from exc

    _print_start_result(wl, settings, dry_run=dry_run, output=output, quiet=quiet)


def _print_start_result(
    wl: MachineWorkload, settings: SandboxSettings, *, dry_run: bool, output: OutputFormat, quiet: bool,
) -> None:
    if dispatch_output(settings, output, SandboxSettings.to_dict) or quiet:
        return
    if dry_run:
        console.print("[green]✓[/green] Settings are valid; nothing was started.")
        return
    console.print("[green]✓[/green] Sandbox started.")
    console.print(f"  Workload: {cell(wl.name)}")
    console.print(f"  Sandbox:  {cell(settings.name)}")
    console.print(
        "\n  Use `synology-apm-cli activity restore list` to check progress.", markup=False, highlight=False,
    )


# ── synology-apm-cli machine sandbox screenshot ────────────────────────────────────────

@sandbox_app.command("screenshot")
@run_async
async def sandbox_screenshot(
    ctx: typer.Context,
    name: str | None = _ACTIVITY_NAME_ARG,
    activity_id: str | None = _ACTIVITY_ID_OPTION,
    filename: Path | None = typer.Option(None, "--filename", "-f", dir_okay=False, help="PNG file to write"),
    press: list[str] | None = typer.Option(
        None, "--press",
        help=_PRESS_HELP,
    ),
    delay: float | None = typer.Option(
        None, "--delay", help="Seconds between the keys and the capture, 0-60 (default with --press: 2)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Overwrite an existing --filename without asking"),
) -> None:
    """Save a ready sandbox's current screen as a PNG image, optionally after keys.

    Use it to check that the restored OS booted: a login screen or desktop
    means it did. The OS can still be booting after the sandbox is ready.
    The sandbox is powered on first if needed. The image can show sensitive
    content.

    --press shift wakes a blanked screen, --press enter starts the default
    boot entry, and --press ctrl_alt_delete opens a Windows sign-in screen
    (it can also restart a sandbox showing a text console). Without --press,
    no input is sent to the sandbox.

    \b
    Examples:
      synology-apm-cli machine sandbox screenshot --id <activity-id> -f sandbox.png
      synology-apm-cli machine sandbox screenshot "prod-server-01" -f sandbox.png --press shift
    """
    validate_name_or_id_args(ctx, name, activity_id)
    out = require_or_help(ctx, filename)
    keys = parse_enum_list(press, SANDBOX_KEY_ARGS, "press") or []
    if delay is not None and not keys:
        fail("--delay applies only with --press")
    confirm_overwrite(out, yes)
    async with apm_session(ctx, spinner="Capturing the screen...") as apm:
        act = await _sandbox_activity(apm, name, activity_id)
        png = await apm.machine.sandboxes.get_screenshot(act, keys=keys, delay_seconds=delay)
    try:
        await asyncio.to_thread(out.write_bytes, png)
    except OSError as exc:
        fail(f"Could not write {cell(str(out))}: {cell(exc.strerror or str(exc))}")
    console.print(f"[green]✓[/green] Screenshot saved: {cell(str(out))}", soft_wrap=True)


# ── synology-apm-cli machine sandbox connect ───────────────────────────────────────────

@sandbox_app.command("connect")
@run_async
async def sandbox_connect(
    ctx: typer.Context,
    name: str | None = _ACTIVITY_NAME_ARG,
    activity_id: str | None = _ACTIVITY_ID_OPTION,
) -> None:
    """Print a single-use web console URL for a ready sandbox.

    The sandbox is powered on first if needed. The URL needs no sign-in:
    open it right away and do not share it.

    \b
    Examples:
      synology-apm-cli machine sandbox connect --id <activity-id>
      synology-apm-cli machine sandbox connect "prod-server-01"
    """
    validate_name_or_id_args(ctx, name, activity_id)
    async with apm_session(ctx, spinner="Opening console...") as apm:
        act = await _sandbox_activity(apm, name, activity_id)
        url = await apm.machine.sandboxes.get_console_link(act)
    typer.echo(url)
    err_console.print(
        "[yellow]⚠[/yellow] Single-use link with no sign-in: open it now and don't share it. "
        "The console can take a moment to appear.",
        soft_wrap=True,
    )


# ── synology-apm-cli machine sandbox vnc ───────────────────────────────────────────────

@sandbox_app.command("vnc")
@run_async
async def sandbox_vnc(
    ctx: typer.Context,
    name: str | None = _ACTIVITY_NAME_ARG,
    activity_id: str | None = _ACTIVITY_ID_OPTION,
    port: int = typer.Option(0, "--port", help="Local port to listen on, 0-65535 (default 0: any free port)"),
) -> None:
    """Let VNC clients on this computer view and use a ready sandbox until Ctrl+C.

    Prints a vnc://127.0.0.1:PORT address for any VNC client. The address needs no
    password and accepts every connection from this computer while the command runs.
    The sandbox is powered on first if needed.

    \b
    Examples:
      synology-apm-cli machine sandbox vnc --id <activity-id>
      synology-apm-cli machine sandbox vnc "prod-server-01" --port 5900
    """
    validate_name_or_id_args(ctx, name, activity_id)
    if not 0 <= port <= 65535:
        fail(f"--port {port} is not between 0 and 65535")
    async with apm_session(ctx) as apm, contextlib.AsyncExitStack() as stack:
        # Opening powers the sandbox on first if needed.
        try:
            with api_spinner("Opening the VNC relay..."):
                act = await _sandbox_activity(apm, name, activity_id)
                endpoint = await stack.enter_async_context(
                    apm.machine.sandboxes.open_vnc_bridge(act, port=port)
                )
        except OSError as exc:  # only opening is reported this way, not later I/O errors
            fail(f"Could not open the VNC relay: {cell(exc.strerror or str(exc))}")
        typer.echo(endpoint.url)
        err_console.print(
            "[yellow]⚠[/yellow] No password: anyone on this computer can connect. Press Ctrl+C to close."
        )
        await wait_for_interrupt()
    err_console.print("VNC relay closed.")
