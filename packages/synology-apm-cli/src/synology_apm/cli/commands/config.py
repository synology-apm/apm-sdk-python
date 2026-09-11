"""synology-apm-cli config — configuration management commands."""
from __future__ import annotations

from dataclasses import dataclass

import typer

from synology_apm.cli._async import run_async
from synology_apm.cli.errors import EXIT_ERROR, abortable, err_console, handle_keyring_error
from synology_apm.cli.output import console
from synology_apm.sdk import (
    CONFIG_FILE,
    DEFAULT_PROFILE,
    APMClient,
    APMError,
    AppConfig,
    KeyringUnavailableError,
    OTPIncorrectError,
    OTPRequiredError,
    PasswordStorage,
    ProfileConfig,
    delete_keyring_password,
    get_keyring_password,
    load_config,
    save_config,
    save_profile_device_token,
    set_keyring_password,
)

# Two-factor prompt attempts config_set will make before giving up and saving
# the profile without a trusted-device registration.
_MAX_OTP_ATTEMPTS = 3


def _delete_keyring_password_or_warn(profile_name: str, username: str) -> None:
    """Delete the profile's OS keyring entry; warn when a credential may remain."""
    if not delete_keyring_password(profile_name, username):
        err_console.print(
            f"[yellow]⚠[/yellow] Could not remove the password for profile '{profile_name}' "
            "from the OS keyring; the stored credential may remain."
        )


def _clear_keyring_password(name: str, profile: ProfileConfig) -> None:
    """Best-effort delete the profile's OS keyring entry, if it has one."""
    if profile.password_storage == PasswordStorage.KEYRING:
        _delete_keyring_password_or_warn(name, profile.username)


@dataclass(frozen=True)
class PasswordDecision:
    """Outcome of resolving a `config set` password prompt/flag against the existing profile."""

    storage: PasswordStorage
    password: str
    changed: bool
    rename_blocked: bool


def _password_prompt_hint(existing: ProfileConfig) -> str:
    if existing.password_storage == PasswordStorage.KEYRING:
        return "Password (leave blank to keep the password stored in the OS keyring)"
    if existing.password_storage == PasswordStorage.PLAINTEXT and existing.password:
        return "Password (leave blank to keep the saved password)"
    return "Password (leave blank to prompt each time, not saved)"


def _resolve_password_decision(
    existing: ProfileConfig,
    new_username: str,
    save_password: PasswordStorage | None,
    entered_pwd: str,
) -> PasswordDecision:
    """Decide the new password storage mode/value from prompt input and the existing profile.

    `entered_pwd` is the value typed at the password prompt; "" means the user left it blank.
    The forced-prompt path (``save_password is not None``) always supplies a non-empty value
    via the CLI's confirmation prompt.
    """
    if save_password is not None:
        return PasswordDecision(storage=save_password, password=entered_pwd, changed=True, rename_blocked=False)

    if entered_pwd:
        typed_storage: PasswordStorage = (
            existing.password_storage
            if existing.password_storage != PasswordStorage.NONE
            else PasswordStorage.PLAINTEXT
        )
        return PasswordDecision(storage=typed_storage, password=entered_pwd, changed=True, rename_blocked=False)

    # Blank prompt: keep whatever was already stored.
    storage = existing.password_storage
    password = existing.password if storage == PasswordStorage.PLAINTEXT else ""
    rename_blocked = storage == PasswordStorage.KEYRING and new_username != existing.username
    return PasswordDecision(storage=storage, password=password, changed=False, rename_blocked=rename_blocked)


async def _verify_connection_and_register_device(
    *,
    host: str,
    username: str,
    password: str,
    verify_ssl: bool,
    existing: ProfileConfig,
    no_input: bool,
) -> tuple[str, str]:
    """Attempt a real connection; register a new trusted device via an
    interactive two-factor prompt if the account requires one.

    This is the only place synology-apm-cli ever handles a two-factor code —
    every other command only ever consumes a stored trusted-device token (see
    cli/_helpers.py's get_client()) and fails with an actionable message if
    that's missing or no longer valid.

    Returns (device_id, status_message). device_id is "" when no trusted
    device is confirmed valid for the settings just entered. `existing`'s
    device_id is only reused — never blindly kept — when host/username are
    unchanged from it (a device token registered for a different host/account
    can never apply to this one).
    """
    same_identity = host == existing.host and username == existing.username

    if no_input or not password:
        # Nothing to validate against, or the user opted out of prompting.
        if same_identity:
            return existing.device_id, ""
        return "", ""

    trial_device_id = existing.device_id if same_identity else ""

    async def _try_connect(*, otp_code: str | None, device_id: str | None) -> str | None:
        async with APMClient(
            host, username, password,
            otp_code=otp_code, device_id=device_id,
            verify_ssl=verify_ssl,
        ) as apm:
            return apm.device_id

    try:
        new_device_id = await _try_connect(otp_code=None, device_id=trial_device_id or None)
        return new_device_id or "", "✓ Connection verified."
    except (OTPRequiredError, OTPIncorrectError):
        # A stored device_id (if any) didn't satisfy two-factor authentication —
        # fall through to a fresh, OTP-verified registration attempt below.
        pass
    except APMError as exc:
        err_console.print(f"[yellow]⚠[/yellow] Could not verify the connection: {exc.message}")
        if same_identity:
            # An unrelated/transient failure shouldn't discard a previously-valid
            # device registration.
            return existing.device_id, ""
        return "", "Could not verify the connection; two-factor status unknown."

    for attempt in range(1, _MAX_OTP_ATTEMPTS + 1):
        otp_code = typer.prompt("Two-factor authentication code")
        try:
            new_device_id = await _try_connect(otp_code=otp_code, device_id=None)
            return new_device_id or "", "✓ Registered a trusted device for two-factor authentication."
        except OTPIncorrectError:
            if attempt == _MAX_OTP_ATTEMPTS:
                break
            err_console.print("[yellow]⚠[/yellow] Incorrect code.")
        except APMError as exc:
            err_console.print(f"[yellow]⚠[/yellow] Could not verify the connection: {exc.message}")
            return "", "Could not verify the connection; two-factor status unknown."

    err_console.print(
        "[yellow]⚠[/yellow] Could not complete two-factor verification; "
        "run `config set` again once you have a valid code."
    )
    return "", ""


app = typer.Typer(
    help="Manage APM connection settings (~/.config/synology-apm/config.toml).",
    no_args_is_help=True,
)


@app.command("set")
@run_async
async def config_set(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host", help="APM hostname or IP, supports host:port"),
    username: str | None = typer.Option(None, "--username", "-u", help="APM login account"),
    save_password: PasswordStorage | None = typer.Option(
        None,
        "--save-password",
        help=(
            "Prompt for password and save it using the given storage method: 'plaintext' "
            "(config file, readable by anyone with file access) or 'keyring' (OS credential "
            "store — macOS Keychain / Windows Credential Manager / Linux Secret Service)."
        ),
    ),
    profile: str | None = typer.Option(None, "--profile", help="Profile name"),
) -> None:
    """Configure APM connection settings (interactive wizard).

    \b
    Prompts interactively for:
      - APM host and account (can be pre-filled with --host / --username)
      - Password (leave blank to prompt on each command, not saved; --save-password forces saving)
      - SSL certificate verification (choose skip for self-signed certificates)

    When a password is available to test with, this command also attempts a real connection
    to verify it and, if the account has two-factor authentication enabled, prompts once for
    a verification code and registers this device to skip that prompt on future connections
    (see `config show`'s "2FA device" line, and `config clear --forget-device`). This is the
    only synology-apm-cli command that ever prompts for a two-factor code; every other command
    only consumes an already-registered device and points back here if none is on file.

    With --no-input, --host and --username must already be resolvable (from the flags here,
    an existing profile, or environment variables) or the command errors instead of prompting;
    --save-password is rejected (it requires an interactive password prompt), and the password
    is left unsaved.

    \b
    Multi-profile examples:
      synology-apm-cli config set --profile lab
      synology-apm-cli config set --host apm.corp.com --username admin --profile prod
      synology-apm-cli config set --save-password keyring --profile prod
    """
    profile = profile or DEFAULT_PROFILE
    no_input: bool = (ctx.obj or {}).get("no_input", False)
    cfg = load_config()
    existing = cfg.get_profile(profile)

    # ── Host ──────────────────────────────────────────────────────────────
    new_host = host or existing.host
    if not new_host:
        if no_input:
            err_console.print("[red]✗[/red] --host is required in non-interactive mode.")
            raise typer.Exit(code=EXIT_ERROR)
        new_host = typer.prompt("APM host (e.g. apm.corp.com or apm.corp.com:10443)")

    # ── Username ──────────────────────────────────────────────────────────
    new_username = username or existing.username
    if not new_username:
        if no_input:
            err_console.print("[red]✗[/red] --username is required in non-interactive mode.")
            raise typer.Exit(code=EXIT_ERROR)
        new_username = typer.prompt("Username")

    # ── Password ──────────────────────────────────────────────────────────
    if save_password is not None:
        if no_input:
            err_console.print("[red]✗[/red] --save-password requires interactive input; omit --no-input.")
            raise typer.Exit(code=EXIT_ERROR)
        # Force prompt with confirmation to ensure a value is entered
        entered_pwd = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    elif no_input:
        entered_pwd = ""  # leave blank = not saved, will prompt on each API call
    else:
        entered_pwd = typer.prompt(
            _password_prompt_hint(existing), hide_input=True, default="", show_default=False
        )

    decision = _resolve_password_decision(existing, new_username, save_password, entered_pwd)
    if decision.rename_blocked:
        err_console.print(
            "[red]✗[/red] Cannot rename the account for a keyring-stored profile without "
            "re-entering the password. Re-run with --save-password keyring."
        )
        raise typer.Exit(code=EXIT_ERROR)
    new_storage, new_password, password_changed = decision.storage, decision.password, decision.changed

    # ── SSL verify ────────────────────────────────────────────────────────
    new_no_verify = (
        existing.no_verify_ssl if no_input else
        typer.confirm(
            "Skip SSL verification? (choose y for self-signed certificates)",
            default=existing.no_verify_ssl,
        )
    )

    # ── Keyring migration ────────────────────────────────────────────────
    if existing.password_storage == PasswordStorage.KEYRING and (
        new_storage != PasswordStorage.KEYRING or new_username != existing.username
    ):
        _delete_keyring_password_or_warn(profile, existing.username)

    # password_changed guarantees the existing keyring entry is left untouched when the
    # user left the prompt blank to keep the previously stored password (see prompt hint above).
    if new_storage == PasswordStorage.KEYRING and password_changed:
        try:
            set_keyring_password(profile, new_username, new_password)
        except KeyringUnavailableError as exc:
            handle_keyring_error(exc)

    persisted_password = new_password if new_storage == PasswordStorage.PLAINTEXT else ""

    # ── Connection validation / two-factor device registration ──────────────
    if new_storage == PasswordStorage.KEYRING and not password_changed:
        # The prompt was left blank to keep the existing keyring-stored password —
        # fetch it back so there's something to test the connection with.
        try:
            password_to_test = get_keyring_password(profile, new_username) or ""
        except KeyringUnavailableError:
            password_to_test = ""
    else:
        password_to_test = new_password

    device_id, verify_status = await _verify_connection_and_register_device(
        host=new_host, username=new_username, password=password_to_test,
        verify_ssl=not new_no_verify, existing=existing, no_input=no_input,
    )

    updated = ProfileConfig(
        host=new_host,
        username=new_username,
        password=persisted_password,
        no_verify_ssl=new_no_verify,
        password_storage=new_storage,
        device_id=device_id,
    )
    cfg.set_profile(profile, updated)
    save_config(cfg)

    console.print(f"\n[green]✓[/green] Settings saved to {CONFIG_FILE} (profile: {profile})")
    if new_storage == PasswordStorage.PLAINTEXT:
        console.print(
            "[yellow]⚠[/yellow] Password is stored in plaintext in the config file "
            "(file permissions are restricted to the current user)."
        )
    elif new_storage == PasswordStorage.KEYRING:
        console.print("[green]✓[/green] Password stored in the OS keyring.")
    if verify_status:
        console.print(verify_status)


@app.command("show")
def config_show(
    profile: str | None = typer.Option(None, "--profile", help="Profile to display"),
) -> None:
    """Show current connection settings. Lists all profiles when --profile is not specified.

    Reports only whether a profile is configured to use keyring storage — it never queries
    the OS keyring itself, so this read-only command can't trigger an unexpected unlock prompt.
    """
    cfg = load_config()

    if profile:
        _show_single_profile(cfg, profile)
    else:
        # Show the default profile and list all available profiles
        _show_single_profile(cfg, DEFAULT_PROFILE)
        all_profiles = list(cfg.profiles.keys())
        if len(all_profiles) > 1:
            console.print(f"\nAll profiles: {', '.join(all_profiles)}")


@app.command("clear")
def config_clear(
    profile: str | None = typer.Option(None, "--profile", help="Profile name to clear"),
    all_profiles: bool = typer.Option(False, "--all", help="Clear all profiles"),
    forget_device: bool = typer.Option(
        False, "--forget-device",
        help=(
            "Only clear this profile's registered two-factor trusted device "
            "(keep host/username/password); forces a fresh verification on the next `config set`."
        ),
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output; suitable for scripting"),
) -> None:
    """Clear APM connection settings.

    Also removes the profile's OS keyring entry, if it has one. Clearing a profile that
    doesn't exist prints a warning rather than failing.
    """
    if forget_device:
        if all_profiles:
            err_console.print("[red]✗[/red] --forget-device cannot be combined with --all.")
            raise typer.Exit(code=EXIT_ERROR)
        target = profile or DEFAULT_PROFILE
        if target not in load_config().profiles:
            # Mirrors the whole-profile clear path below: never silently fabricate a
            # profile entry for a name that was never configured.
            console.print(f"[yellow]⚠[/yellow] Profile '{target}' does not exist.")
            return
        # Low-stakes and easily reversible (the next `config set` just re-registers a
        # device), unlike clearing a whole profile — no confirmation prompt needed.
        save_profile_device_token(target, "")
        if not quiet:
            console.print(f"[green]✓[/green] Trusted-device registration cleared for profile '{target}'.")
        return

    cfg = load_config()

    if all_profiles:
        if not yes:
            with abortable():
                typer.confirm("Confirm clear all settings?", abort=True)
        for name, p in cfg.profiles.items():
            _clear_keyring_password(name, p)
        cfg.profiles.clear()
        save_config(cfg)
        if not quiet:
            console.print("[green]✓[/green] All settings cleared.")
        return

    target = profile or DEFAULT_PROFILE
    if not yes:
        with abortable():
            typer.confirm(f"Confirm clear profile '{target}'?", abort=True)

    existing = cfg.get_profile(target)
    if cfg.remove_profile(target):
        _clear_keyring_password(target, existing)
        save_config(cfg)
        if not quiet:
            console.print(f"[green]✓[/green] Profile cleared: {target}")
    else:
        console.print(f"[yellow]⚠[/yellow] Profile '{target}' does not exist.")


def _show_single_profile(cfg: AppConfig, name: str) -> None:
    p = cfg.get_profile(name)
    console.print(f"Profile:  {name}")
    console.print(f"Host:     {p.host or '[bright_black](not set)[/bright_black]'}")
    console.print(f"User:     {p.username or '[bright_black](not set)[/bright_black]'}")
    if p.password_storage == PasswordStorage.KEYRING:
        pw_status = "[green](stored in OS keyring)[/green]"
    elif p.password_storage == PasswordStorage.PLAINTEXT and p.password:
        pw_status = "[yellow](saved, plaintext)[/yellow]"
    else:
        pw_status = "[bright_black](not saved)[/bright_black]"
    console.print(f"Password: {pw_status}")
    console.print(f"SSL:      {'skip verify' if p.no_verify_ssl else 'verify'}")
    if p.device_id:
        console.print("2FA device: [green]registered[/green]")
    else:
        console.print("2FA device: [bright_black](not registered)[/bright_black]")
