"""synology-apm config — configuration management commands."""
from __future__ import annotations

from dataclasses import dataclass

import typer

from synology_apm.cli.config import (
    CONFIG_FILE,
    DEFAULT_PROFILE,
    AppConfig,
    KeyringUnavailableError,
    PasswordStorage,
    ProfileConfig,
    _delete_keyring_password,
    _set_keyring_password,
    load_config,
    save_config,
)
from synology_apm.cli.errors import EXIT_ERROR, err_console, handle_keyring_error
from synology_apm.cli.output import console


def _clear_keyring_password(name: str, profile: ProfileConfig) -> None:
    """Best-effort delete the profile's OS keyring entry, if it has one."""
    if profile.password_storage == PasswordStorage.KEYRING:
        _delete_keyring_password(name, profile.username)


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


app = typer.Typer(
    help="Manage APM connection settings (~/.config/synology-apm/config.toml).",
    no_args_is_help=True,
)


@app.command("set")
def config_set(
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
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", help="Profile name"),
) -> None:
    """Configure APM connection settings (interactive wizard).

    \b
    Prompts interactively for:
      - APM host and account (can be pre-filled with --host / --username)
      - Password (leave blank to prompt on each command, not saved; --save-password forces saving)
      - SSL certificate verification (choose skip for self-signed certificates)

    \b
    Multi-profile examples:
      synology-apm config set --profile lab
      synology-apm config set --host apm.corp.com --username admin --profile prod
      synology-apm config set --save-password keyring --profile prod
    """
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
        _delete_keyring_password(profile, existing.username)

    # password_changed guarantees the existing keyring entry is left untouched when the
    # user left the prompt blank to keep the previously stored password (see prompt hint above).
    if new_storage == PasswordStorage.KEYRING and password_changed:
        try:
            _set_keyring_password(profile, new_username, new_password)
        except KeyringUnavailableError as exc:
            handle_keyring_error(exc)

    persisted_password = new_password if new_storage == PasswordStorage.PLAINTEXT else ""

    updated = ProfileConfig(
        host=new_host,
        username=new_username,
        password=persisted_password,
        no_verify_ssl=new_no_verify,
        password_storage=new_storage,
    )
    cfg.set_profile(profile, updated)
    save_config(cfg)

    console.print(f"\n[green]✓[/green] Settings saved to {CONFIG_FILE} (profile: {profile})")
    if new_storage == PasswordStorage.PLAINTEXT:
        console.print(
            "[yellow]⚠[/yellow] Password is stored in plaintext. Secure the file permissions (chmod 600)."
        )
    elif new_storage == PasswordStorage.KEYRING:
        console.print("[green]✓[/green] Password stored in the OS keyring.")


@app.command("show")
def config_show(
    profile: str | None = typer.Option(None, "--profile", help="Profile to display"),
) -> None:
    """Show current connection settings. Lists all profiles when --profile is not specified."""
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
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Clear APM connection settings."""
    cfg = load_config()

    if all_profiles:
        if not yes:
            typer.confirm("Confirm clear all settings?", abort=True)
        for name, p in cfg.profiles.items():
            _clear_keyring_password(name, p)
        cfg.profiles.clear()
        save_config(cfg)
        console.print("[green]✓[/green] All settings cleared.")
        return

    target = profile or DEFAULT_PROFILE
    if not yes:
        typer.confirm(f"Confirm clear profile '{target}'?", abort=True)

    existing = cfg.get_profile(target)
    if cfg.remove_profile(target):
        _clear_keyring_password(target, existing)
        save_config(cfg)
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
