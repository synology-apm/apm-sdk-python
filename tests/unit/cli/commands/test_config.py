"""Unit tests for synology_apm.cli.commands.config — the `config` Typer command group."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import keyring.errors
import pytest

from synology_apm.cli.commands.config import (
    PasswordDecision,
    _resolve_password_decision,
    _verify_connection_and_register_device,
)
from synology_apm.cli.main import app
from synology_apm.sdk import (
    DEFAULT_PROFILE,
    AppConfig,
    KeyringUnavailableError,
    PasswordStorage,
    ProfileConfig,
)
from synology_apm.sdk.exceptions import AuthenticationError, OTPIncorrectError, OTPRequiredError
from tests.unit.cli.conftest import runner


def _fake_apm_client(device_id: str | None = None, *, raises: BaseException | None = None) -> AsyncMock:
    """Build a fake `async with APMClient(...) as apm:` result — either yielding a stub
    `apm` whose `.device_id` is `device_id`, or raising `raises` from `__aenter__`."""
    ctx_mgr = AsyncMock()
    if raises is not None:
        ctx_mgr.__aenter__ = AsyncMock(side_effect=raises)
    else:
        mock_apm = MagicMock()
        mock_apm.device_id = device_id
        ctx_mgr.__aenter__ = AsyncMock(return_value=mock_apm)
    ctx_mgr.__aexit__ = AsyncMock(return_value=None)
    return ctx_mgr

# ── _resolve_password_decision (pure password/storage transition logic) ────


def test_resolve_password_decision_explicit_flag_forces_storage_and_value() -> None:
    """--save-password <mode> always uses the typed value, regardless of prior storage."""
    existing = ProfileConfig(username="u", password_storage=PasswordStorage.KEYRING)
    decision = _resolve_password_decision(existing, "u", PasswordStorage.PLAINTEXT, "typed")
    assert decision == PasswordDecision(PasswordStorage.PLAINTEXT, "typed", changed=True, rename_blocked=False)


def test_resolve_password_decision_typed_password_keeps_existing_storage() -> None:
    """Typing a new password without --save-password keeps the profile's current storage mode."""
    existing = ProfileConfig(username="u", password_storage=PasswordStorage.KEYRING)
    decision = _resolve_password_decision(existing, "u", None, "typed")
    assert decision == PasswordDecision(PasswordStorage.KEYRING, "typed", changed=True, rename_blocked=False)


def test_resolve_password_decision_typed_password_defaults_none_to_plaintext() -> None:
    """Typing a password for a profile with no prior storage defaults to plaintext."""
    existing = ProfileConfig(username="u", password_storage=PasswordStorage.NONE)
    decision = _resolve_password_decision(existing, "u", None, "typed")
    assert decision == PasswordDecision(PasswordStorage.PLAINTEXT, "typed", changed=True, rename_blocked=False)


def test_resolve_password_decision_blank_keeps_keyring_storage_without_rewriting() -> None:
    """Leaving the prompt blank on a keyring profile keeps KEYRING storage and signals no rewrite."""
    existing = ProfileConfig(username="u", password_storage=PasswordStorage.KEYRING)
    decision = _resolve_password_decision(existing, "u", None, "")
    assert decision == PasswordDecision(PasswordStorage.KEYRING, "", changed=False, rename_blocked=False)


def test_resolve_password_decision_blank_keeps_plaintext_password() -> None:
    """Leaving the prompt blank on a plaintext profile carries the existing password forward."""
    existing = ProfileConfig(username="u", password="old", password_storage=PasswordStorage.PLAINTEXT)
    decision = _resolve_password_decision(existing, "u", None, "")
    assert decision == PasswordDecision(PasswordStorage.PLAINTEXT, "old", changed=False, rename_blocked=False)


def test_resolve_password_decision_blank_rename_keyring_profile_is_blocked() -> None:
    """Renaming the account on a keyring profile without re-entering the password is blocked."""
    existing = ProfileConfig(username="old-user", password_storage=PasswordStorage.KEYRING)
    decision = _resolve_password_decision(existing, "new-user", None, "")
    assert decision.rename_blocked is True


def test_resolve_password_decision_blank_rename_plaintext_profile_is_allowed() -> None:
    """Renaming the account on a plaintext (or unsaved) profile is unaffected by the rename guard."""
    existing = ProfileConfig(username="old-user", password="old", password_storage=PasswordStorage.PLAINTEXT)
    decision = _resolve_password_decision(existing, "new-user", None, "")
    assert decision.rename_blocked is False


# ── CLI commands: config show / config set / config clear ─────────────────


def test_config_show_displays_profile() -> None:
    """config show displays host, username, password status, and SSL setting."""
    cfg = AppConfig(profiles={DEFAULT_PROFILE: ProfileConfig(host="apm.corp.com", username="admin")})
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg):
        result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "apm.corp.com" in result.output
    assert "admin" in result.output


def test_config_show_lists_all_profiles() -> None:
    """config show without --profile lists all profile names when more than one exists."""
    cfg = AppConfig(profiles={
        DEFAULT_PROFILE: ProfileConfig(host="apm1.corp.com", username="admin"),
        "lab": ProfileConfig(host="apm2.corp.com", username="test"),
    })
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg):
        result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "lab" in result.output


def test_config_clear_with_yes_flag_removes_profile() -> None:
    """config clear --yes removes the default profile without prompting."""
    cfg = AppConfig(profiles={DEFAULT_PROFILE: ProfileConfig(host="apm.corp.com", username="admin")})
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear", "--yes"])
    assert result.exit_code == 0
    mock_save.assert_called_once()
    saved: AppConfig = mock_save.call_args[0][0]
    assert DEFAULT_PROFILE not in saved.profiles


def test_config_clear_with_quiet_flag_produces_no_output() -> None:
    """config clear --yes --quiet removes the profile without printing the success line."""
    cfg = AppConfig(profiles={DEFAULT_PROFILE: ProfileConfig(host="apm.corp.com", username="admin")})
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear", "--yes", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""
    mock_save.assert_called_once()


def test_config_clear_all_with_quiet_flag_produces_no_output() -> None:
    """config clear --all --yes --quiet clears all profiles without printing the success line."""
    cfg = AppConfig(profiles={DEFAULT_PROFILE: ProfileConfig(host="apm.corp.com", username="admin")})
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear", "--all", "--yes", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""
    mock_save.assert_called_once()


def test_config_clear_nonexistent_profile_with_quiet_still_shows_warning() -> None:
    """config clear --quiet only suppresses success messages; the not-found warning still prints."""
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config"):
        result = runner.invoke(app, ["config", "clear", "--profile", "ghost", "--yes", "--quiet"])
    assert result.exit_code == 0
    assert "Profile 'ghost' does not exist" in result.output


def test_config_clear_requires_confirmation_and_aborts_on_no() -> None:
    """config clear without --yes prompts for confirmation; answering n cancels with exit 4."""
    cfg = AppConfig(profiles={DEFAULT_PROFILE: ProfileConfig(host="apm.corp.com", username="admin")})
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear"], input="n\n")
    assert result.exit_code == 4
    assert mock_save.call_count == 0


def test_config_set_saves_host_and_username() -> None:
    """config set --host --username saves those values to the default profile."""
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(
            app,
            ["config", "set", "--host", "apm.corp.com", "--username", "admin"],
            input="\nn\n",  # blank password, then "n" = do not skip SSL
        )
    assert result.exit_code == 0
    assert "Settings saved" in result.output
    saved: AppConfig = mock_save.call_args[0][0]
    profile = saved.get_profile(DEFAULT_PROFILE)
    assert profile.host == "apm.corp.com"
    assert profile.username == "admin"
    assert profile.no_verify_ssl is False


def test_config_set_no_profile_flag_defaults_to_default_profile() -> None:
    """config set with no --profile at all should still write to DEFAULT_PROFILE.

    Regression test for the --profile default changing from a concrete DEFAULT_PROFILE
    value to None (matching every other profile-flavored option's None-default convention).
    """
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(
            app,
            ["config", "set", "--host", "apm.corp.com", "--username", "admin"],
            input="\nn\n",
        )
    assert result.exit_code == 0
    assert f"profile: {DEFAULT_PROFILE}" in result.output
    saved: AppConfig = mock_save.call_args[0][0]
    assert saved.get_profile(DEFAULT_PROFILE).host == "apm.corp.com"


@pytest.mark.parametrize("provided_args,stdin_input,expected_prompt", [
    (["--username", "admin"], "apm.corp.com\n\nn\n", "APM host"),
    (["--host", "apm.corp.com"], "admin\n\nn\n", "Username"),
], ids=["host", "username"])
def test_config_set_prompts_for_missing_field(
    provided_args: list[str], stdin_input: str, expected_prompt: str
) -> None:
    """config set should prompt the user for whichever of --host/--username is not provided."""
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config"):
        result = runner.invoke(
            app,
            ["config", "set", *provided_args],
            input=stdin_input,
        )
    assert result.exit_code == 0
    assert expected_prompt in result.output


def test_config_set_shows_password_plaintext_warning() -> None:
    """config set with a saved password should warn about plaintext storage."""
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config"), \
         patch("synology_apm.cli.commands.config._verify_connection_and_register_device", return_value=("", "")):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "h", "--username", "u", "--save-password", "plaintext"],
            input="secret\nsecret\nn\n",  # password×2 confirmation, no SSL
        )
    assert result.exit_code == 0
    assert "plaintext" in result.output.lower()


def test_config_show_specific_profile() -> None:
    """config show --profile <name> should display that profile's settings."""
    cfg = AppConfig()
    cfg.set_profile("lab", ProfileConfig(host="lab.corp.com", username="labuser"))
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg):
        result = runner.invoke(app, ["config", "show", "--profile", "lab"])
    assert result.exit_code == 0
    assert "lab.corp.com" in result.output
    assert "labuser" in result.output


def test_config_clear_all_with_yes() -> None:
    """config clear --all --yes should remove all profiles without prompting."""
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="a.com", username="u"))
    cfg.set_profile("lab", ProfileConfig(host="b.com", username="v"))
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear", "--all", "--yes"])
    assert result.exit_code == 0
    assert "cleared" in result.output.lower()
    saved: AppConfig = mock_save.call_args[0][0]
    assert saved.profiles == {}


def test_config_clear_nonexistent_profile_shows_warning() -> None:
    """config clear on a profile that doesn't exist should print a warning."""
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config"):
        result = runner.invoke(app, ["config", "clear", "--profile", "ghost", "--yes"])
    assert result.exit_code == 0
    assert "does not exist" in result.output


# ── CLI commands: keyring storage ──────────────────────────────────────────


def test_config_set_save_password_keyring() -> None:
    """config set --save-password keyring stores the password via the OS keyring, not in the TOML file."""
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch("synology_apm.sdk.config.keyring.set_password") as mock_set, \
         patch("synology_apm.cli.commands.config._verify_connection_and_register_device", return_value=("", "")):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "h", "--username", "u", "--save-password", "keyring"],
            input="secret\nsecret\nn\n",
        )
    assert result.exit_code == 0
    assert "OS keyring" in result.output
    mock_set.assert_called_once_with("synology-apm-cli:default", "u", "secret")
    saved: AppConfig = mock_save.call_args[0][0]
    profile = saved.get_profile(DEFAULT_PROFILE)
    assert profile.password_storage == PasswordStorage.KEYRING
    assert profile.password == ""


def test_config_set_migrates_keyring_to_plaintext() -> None:
    """Switching a profile from keyring to plaintext storage deletes the old keyring entry."""
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="h", username="u", password_storage=PasswordStorage.KEYRING),
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete, \
         patch("synology_apm.cli.commands.config._verify_connection_and_register_device", return_value=("", "")):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "h", "--username", "u", "--save-password", "plaintext"],
            input="newsecret\nnewsecret\nn\n",
        )
    assert result.exit_code == 0
    mock_delete.assert_called_once_with("synology-apm-cli:default", "u")
    saved: AppConfig = mock_save.call_args[0][0]
    profile = saved.get_profile(DEFAULT_PROFILE)
    assert profile.password_storage == PasswordStorage.PLAINTEXT
    assert profile.password == "newsecret"


def test_config_set_migrates_plaintext_to_keyring() -> None:
    """Switching a profile from plaintext to keyring storage drops the plaintext password from the file."""
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="h", username="u", password="old", password_storage=PasswordStorage.PLAINTEXT),
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch("synology_apm.sdk.config.keyring.set_password") as mock_set, \
         patch("synology_apm.cli.commands.config._verify_connection_and_register_device", return_value=("", "")):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "h", "--username", "u", "--save-password", "keyring"],
            input="newsecret\nnewsecret\nn\n",
        )
    assert result.exit_code == 0
    mock_set.assert_called_once_with("synology-apm-cli:default", "u", "newsecret")
    saved: AppConfig = mock_save.call_args[0][0]
    profile = saved.get_profile(DEFAULT_PROFILE)
    assert profile.password_storage == PasswordStorage.KEYRING
    assert profile.password == ""


def test_config_set_blank_password_keeps_existing_keyring_entry() -> None:
    """Leaving the password prompt blank on a keyring-stored profile must not touch the keyring."""
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="h", username="u", password_storage=PasswordStorage.KEYRING),
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch("synology_apm.sdk.config.keyring.set_password") as mock_set, \
         patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete, \
         patch("synology_apm.cli.commands.config.get_keyring_password", return_value="s3cr3t") as mock_get, \
         patch("synology_apm.cli.commands.config._verify_connection_and_register_device", return_value=("", "")):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "newhost", "--profile", "default"],
            input="\nn\n",
        )
    assert result.exit_code == 0
    mock_set.assert_not_called()
    mock_delete.assert_not_called()
    mock_get.assert_called_once_with("default", "u")
    saved: AppConfig = mock_save.call_args[0][0]
    profile = saved.get_profile("default")
    assert profile.password_storage == PasswordStorage.KEYRING
    assert profile.host == "newhost"


def test_config_set_rename_keyring_profile_without_password_is_rejected() -> None:
    """Renaming the account on a keyring-stored profile without re-entering the password is rejected."""
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="h", username="old-user", password_storage=PasswordStorage.KEYRING),
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch("synology_apm.sdk.config.keyring.set_password") as mock_set, \
         patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete:
        result = runner.invoke(
            app,
            ["config", "set", "--username", "new-user", "--profile", "default"],
            input="\n",
        )
    assert result.exit_code != 0
    mock_set.assert_not_called()
    mock_delete.assert_not_called()
    mock_save.assert_not_called()


def test_config_set_keyring_backend_unavailable_shows_error() -> None:
    """config set --save-password keyring surfaces a clear error (not a traceback) when the backend fails."""
    cfg = AppConfig()
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch(
             "synology_apm.sdk.config.keyring.set_password",
             side_effect=keyring.errors.NoKeyringError("no backend"),
         ):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "h", "--username", "u", "--save-password", "keyring"],
            input="secret\nsecret\nn\n",
        )
    assert result.exit_code != 0
    assert "keyring" in result.output.lower()
    mock_save.assert_not_called()


def test_config_show_keyring_storage_no_keyring_call() -> None:
    """config show must never query the keyring backend, even for a KEYRING-storage profile."""
    cfg = AppConfig(
        profiles={DEFAULT_PROFILE: ProfileConfig(host="h", username="u", password_storage=PasswordStorage.KEYRING)}
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.sdk.config.keyring.get_password") as mock_get:
        result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "OS keyring" in result.output
    mock_get.assert_not_called()


def test_config_clear_deletes_keyring_entry() -> None:
    """config clear for a KEYRING-storage profile deletes the corresponding keyring entry."""
    cfg = AppConfig(
        profiles={DEFAULT_PROFILE: ProfileConfig(host="h", username="u", password_storage=PasswordStorage.KEYRING)}
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config"), \
         patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete:
        result = runner.invoke(app, ["config", "clear", "--yes"])
    assert result.exit_code == 0
    mock_delete.assert_called_once_with("synology-apm-cli:default", "u")


def test_config_clear_all_deletes_all_keyring_entries() -> None:
    """config clear --all deletes keyring entries only for profiles that use keyring storage."""
    cfg = AppConfig()
    cfg.set_profile("default", ProfileConfig(host="a", username="u", password_storage=PasswordStorage.KEYRING))
    cfg.set_profile("lab", ProfileConfig(host="b", username="v", password="x", password_storage=PasswordStorage.PLAINTEXT))
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config"), \
         patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete:
        result = runner.invoke(app, ["config", "clear", "--all", "--yes"])
    assert result.exit_code == 0
    mock_delete.assert_called_once_with("synology-apm-cli:default", "u")


def test_config_clear_swallows_keyring_delete_error() -> None:
    """config clear should succeed silently when the keyring entry is already gone."""
    cfg = AppConfig(
        profiles={DEFAULT_PROFILE: ProfileConfig(host="h", username="u", password_storage=PasswordStorage.KEYRING)}
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch(
             "synology_apm.sdk.config.keyring.delete_password",
             side_effect=keyring.errors.PasswordDeleteError("not found"),
         ):
        result = runner.invoke(app, ["config", "clear", "--yes"])
    assert result.exit_code == 0
    mock_save.assert_called_once()
    assert "may remain" not in result.output


def test_config_clear_warns_when_keyring_backend_fails() -> None:
    """config clear still succeeds when the keyring backend fails, but warns that the credential may remain."""
    cfg = AppConfig(
        profiles={DEFAULT_PROFILE: ProfileConfig(host="h", username="u", password_storage=PasswordStorage.KEYRING)}
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch(
             "synology_apm.sdk.config.keyring.delete_password",
             side_effect=keyring.errors.KeyringError("backend unavailable"),
         ):
        result = runner.invoke(app, ["config", "clear", "--yes"])
    assert result.exit_code == 0
    mock_save.assert_called_once()
    assert "may remain" in result.output


# ── config set --no-input ──────────────────────────────────────────────────


@pytest.mark.parametrize("provided_args,expected_error", [
    (["--username", "admin"], "--host is required"),
    (["--host", "apm.corp.com"], "--username is required"),
], ids=["host", "username"])
def test_config_set_no_input_missing_field_errors(
    provided_args: list[str], expected_error: str
) -> None:
    """config set with --no-input should error when --host or --username is missing."""
    with patch("synology_apm.cli.commands.config.load_config", return_value=AppConfig()), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["--no-input", "config", "set", *provided_args])
    assert result.exit_code != 0
    assert expected_error in result.output
    mock_save.assert_not_called()


def test_config_set_no_input_rejects_save_password() -> None:
    """--save-password needs interactive prompts and is rejected under --no-input."""
    with patch("synology_apm.cli.commands.config.load_config", return_value=AppConfig()), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(
            app,
            ["--no-input", "config", "set", "--host", "apm.corp.com", "--username", "admin",
             "--save-password", "plaintext"],
        )
    assert result.exit_code != 0
    assert "--save-password requires interactive input" in result.output
    mock_save.assert_not_called()


def test_config_set_no_input_saves_without_password() -> None:
    """Fully non-interactive set saves the profile with no password and keeps the SSL setting."""
    cfg = AppConfig()
    cfg.set_profile(DEFAULT_PROFILE, ProfileConfig(host="old-host", username="old", no_verify_ssl=True))
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(
            app, ["--no-input", "config", "set", "--host", "apm.corp.com", "--username", "admin"],
        )
    assert result.exit_code == 0, result.output
    assert "Settings saved" in result.output
    saved: AppConfig = mock_save.call_args[0][0]
    profile = saved.get_profile(DEFAULT_PROFILE)
    assert profile.host == "apm.corp.com"
    assert profile.username == "admin"
    assert profile.password == ""
    assert profile.password_storage == PasswordStorage.NONE
    assert profile.no_verify_ssl is True  # preserved from the existing profile


# ── config set prompt hint for saved plaintext password ────────────────────


def test_config_set_keyring_password_fetch_failure_skips_validation() -> None:
    """If the keyring can't be read back to test the connection with, config set still saves
    the profile instead of crashing — the connection-validation step is best-effort."""
    cfg = AppConfig()
    cfg.set_profile(
        "default",
        ProfileConfig(host="h", username="u", password_storage=PasswordStorage.KEYRING),
    )
    with (
        patch("synology_apm.cli.commands.config.load_config", return_value=cfg),
        patch("synology_apm.cli.commands.config.save_config") as mock_save,
        patch(
            "synology_apm.cli.commands.config.get_keyring_password",
            side_effect=KeyringUnavailableError("keyring locked"),
        ),
        patch("synology_apm.cli.commands.config.APMClient") as mock_client_cls,
    ):
        result = runner.invoke(app, ["config", "set", "--host", "h", "--username", "u"], input="\nn\n")
    assert result.exit_code == 0, result.output
    assert "Settings saved" in result.output
    mock_client_cls.assert_not_called()
    mock_save.assert_called_once()


def test_config_set_wires_into_verify_connection_with_correct_arguments() -> None:
    """config set calls _verify_connection_and_register_device with exactly the values it
    resolved (host/username/password/SSL setting/no_input) and the profile's existing
    ProfileConfig — this is the "public caller" wiring test tests/CLAUDE.md requires
    alongside directly testing that private helper's own branch logic."""
    existing = ProfileConfig(host="old-h", username="old-u", device_id="did-existing")
    cfg = AppConfig(profiles={DEFAULT_PROFILE: existing})
    with (
        patch("synology_apm.cli.commands.config.load_config", return_value=cfg),
        patch("synology_apm.cli.commands.config.save_config"),
        patch(
            "synology_apm.cli.commands.config._verify_connection_and_register_device",
            return_value=("", ""),
        ) as mock_verify,
    ):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "h", "--username", "u"],
            input="secret\ny\n",  # password, skip SSL verification = yes
        )
    assert result.exit_code == 0, result.output
    mock_verify.assert_called_once_with(
        host="h", username="u", password="secret", verify_ssl=False, existing=existing, no_input=False,
    )


def test_config_set_prints_verify_status_when_present() -> None:
    """config set prints _verify_connection_and_register_device's status line when non-empty."""
    cfg = AppConfig()
    with (
        patch("synology_apm.cli.commands.config.load_config", return_value=cfg),
        patch("synology_apm.cli.commands.config.save_config"),
        patch(
            "synology_apm.cli.commands.config._verify_connection_and_register_device",
            return_value=("did-abc", "✓ Connection verified."),
        ),
    ):
        result = runner.invoke(
            app,
            ["config", "set", "--host", "h", "--username", "u"],
            input="secret\nn\n",
        )
    assert result.exit_code == 0, result.output
    assert "✓ Connection verified." in result.output


def test_config_set_prompt_hint_for_saved_plaintext_password() -> None:
    """The password prompt offers to keep the previously saved plaintext password."""
    cfg = AppConfig()
    cfg.set_profile(
        DEFAULT_PROFILE,
        ProfileConfig(
            host="apm.corp.com", username="admin",
            password="s3cr3t", password_storage=PasswordStorage.PLAINTEXT,
        ),
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save, \
         patch("synology_apm.cli.commands.config._verify_connection_and_register_device", return_value=("", "")):
        result = runner.invoke(app, ["config", "set"], input="\nn\n")  # keep password, keep SSL
    assert result.exit_code == 0, result.output
    assert "leave blank to keep the saved password" in result.output
    saved: AppConfig = mock_save.call_args[0][0]
    assert saved.get_profile(DEFAULT_PROFILE).password == "s3cr3t"


# ── config clear --all confirmation prompt ─────────────────────────────────


def test_config_clear_all_confirmation_declined_aborts() -> None:
    """clear --all without --yes prompts; answering n cancels with exit 4 without saving."""
    cfg = AppConfig()
    cfg.set_profile(DEFAULT_PROFILE, ProfileConfig(host="apm.corp.com", username="admin"))
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear", "--all"], input="n\n")
    assert result.exit_code == 4
    mock_save.assert_not_called()


def test_config_clear_all_confirmation_accepted_clears() -> None:
    """clear --all without --yes prompts; answering y clears all profiles."""
    cfg = AppConfig()
    cfg.set_profile(DEFAULT_PROFILE, ProfileConfig(host="apm.corp.com", username="admin"))
    cfg.set_profile("lab", ProfileConfig(host="apm2.corp.com", username="admin"))
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear", "--all"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "All settings cleared" in result.output
    saved: AppConfig = mock_save.call_args[0][0]
    assert saved.profiles == {}


# ── config show password status line ───────────────────────────────────────


def test_config_show_plaintext_saved_password_status() -> None:
    """config show marks a saved plaintext password as such."""
    cfg = AppConfig()
    cfg.set_profile(
        DEFAULT_PROFILE,
        ProfileConfig(
            host="apm.corp.com", username="admin",
            password="s3cr3t", password_storage=PasswordStorage.PLAINTEXT,
        ),
    )
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg):
        result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "saved, plaintext" in result.output


# ── config show — 2FA trusted-device status ────────────────────────────────


def test_config_show_registered_device() -> None:
    """config show reports a registered trusted device."""
    cfg = AppConfig(profiles={
        DEFAULT_PROFILE: ProfileConfig(host="h", username="u", device_id="did-abc"),
    })
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg):
        result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "2FA device" in result.output
    assert "registered" in result.output


def test_config_show_no_registered_device() -> None:
    """config show reports no registered trusted device when none is on file."""
    cfg = AppConfig(profiles={DEFAULT_PROFILE: ProfileConfig(host="h", username="u")})
    with patch("synology_apm.cli.commands.config.load_config", return_value=cfg):
        result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "not registered" in result.output


# ── config clear --forget-device ────────────────────────────────────────────


def _cfg_with_default_profile() -> AppConfig:
    return AppConfig(profiles={DEFAULT_PROFILE: ProfileConfig(host="h", username="u", device_id="did-abc")})


def test_config_clear_forget_device_clears_only_device_fields() -> None:
    """config clear --forget-device clears device_id, leaving host/username/password intact."""
    with patch("synology_apm.cli.commands.config.load_config", return_value=_cfg_with_default_profile()), \
         patch("synology_apm.cli.commands.config.save_profile_device_token") as mock_save_token:
        result = runner.invoke(app, ["config", "clear", "--forget-device"])
    assert result.exit_code == 0, result.output
    assert "Trusted-device registration cleared" in result.output
    mock_save_token.assert_called_once_with(DEFAULT_PROFILE, "")


def test_config_clear_forget_device_no_confirmation_needed() -> None:
    """--forget-device skips the usual clear confirmation prompt (low-stakes, reversible)."""
    with patch("synology_apm.cli.commands.config.load_config", return_value=_cfg_with_default_profile()), \
         patch("synology_apm.cli.commands.config.save_profile_device_token") as mock_save_token:
        result = runner.invoke(app, ["config", "clear", "--forget-device"])  # no input supplied
    assert result.exit_code == 0, result.output
    mock_save_token.assert_called_once()


def test_config_clear_forget_device_quiet_produces_no_output() -> None:
    """--forget-device --quiet suppresses the success message, like the other clear variants."""
    with patch("synology_apm.cli.commands.config.load_config", return_value=_cfg_with_default_profile()), \
         patch("synology_apm.cli.commands.config.save_profile_device_token") as mock_save_token:
        result = runner.invoke(app, ["config", "clear", "--forget-device", "--quiet"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""
    mock_save_token.assert_called_once()


def test_config_clear_forget_device_nonexistent_profile_shows_warning() -> None:
    """--forget-device for a profile that was never configured warns and never fabricates
    a phantom profile entry (must not call save_profile_device_token at all)."""
    with patch("synology_apm.cli.commands.config.load_config", return_value=AppConfig()), \
         patch("synology_apm.cli.commands.config.save_profile_device_token") as mock_save_token:
        result = runner.invoke(app, ["config", "clear", "--forget-device", "--profile", "ghost"])
    assert result.exit_code == 0, result.output
    assert "Profile 'ghost' does not exist" in result.output
    mock_save_token.assert_not_called()


def test_config_clear_forget_device_rejects_all_flag() -> None:
    """--forget-device combined with --all is rejected rather than silently ignored."""
    with patch("synology_apm.cli.commands.config.load_config", return_value=AppConfig()), \
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
        result = runner.invoke(app, ["config", "clear", "--forget-device", "--all"])
    assert result.exit_code != 0
    mock_save.assert_not_called()


# ── _verify_connection_and_register_device ─────────────────────────────────
# Tested directly (a private helper, but complex enough to warrant it — mirroring
# this file's existing precedent for _resolve_password_decision): it is the one
# place synology-apm-cli ever handles a two-factor code.


async def test_verify_connection_skips_entirely_under_no_input() -> None:
    """--no-input never attempts a connection, and reuses the existing device token
    when the host/username are unchanged."""
    existing = ProfileConfig(host="h", username="u", device_id="did-abc")
    with patch("synology_apm.cli.commands.config.APMClient") as mock_cls:
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="p", verify_ssl=True, existing=existing, no_input=True,
        )
    assert (device_id, status) == ("did-abc", "")
    mock_cls.assert_not_called()


async def test_verify_connection_skips_entirely_with_no_password() -> None:
    """No password available to test with also skips the connection attempt entirely."""
    existing = ProfileConfig(host="h", username="u")
    with patch("synology_apm.cli.commands.config.APMClient") as mock_cls:
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="", verify_ssl=True, existing=existing, no_input=False,
        )
    assert (device_id, status) == ("", "")
    mock_cls.assert_not_called()


async def test_verify_connection_skips_entirely_with_no_password_and_changed_identity() -> None:
    """When skipped (no password) *and* host/username differ from `existing`, the old device
    token is never carried forward — unlike the same-identity case above."""
    existing = ProfileConfig(host="old-host", username="u", device_id="did-abc")
    with patch("synology_apm.cli.commands.config.APMClient") as mock_cls:
        device_id, status = await _verify_connection_and_register_device(
            host="new-host", username="u", password="", verify_ssl=True, existing=existing, no_input=False,
        )
    assert (device_id, status) == ("", "")
    mock_cls.assert_not_called()


async def test_verify_connection_trial_otp_incorrect_falls_through_to_prompt() -> None:
    """A stored device_id that gets rejected as OTPIncorrectError (not just OTPRequiredError)
    on the trial connect also falls through to the interactive OTP-registration path."""
    existing = ProfileConfig(host="h", username="u", device_id="did-stale")
    with (
        patch(
            "synology_apm.cli.commands.config.APMClient",
            side_effect=[
                _fake_apm_client(raises=OTPIncorrectError("stale device rejected")),
                _fake_apm_client("did-new"),
            ],
        ),
        patch("synology_apm.cli.commands.config.typer.prompt", return_value="123456") as mock_prompt,
    ):
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="p", verify_ssl=True, existing=existing, no_input=False,
        )
    assert device_id == "did-new"
    assert "Registered a trusted device" in status
    mock_prompt.assert_called_once()


async def test_verify_connection_success_no_otp_needed() -> None:
    """A successful connection with no two-factor challenge reports "Connection verified"."""
    existing = ProfileConfig(host="h", username="u", device_id="did-abc")
    with patch(
        "synology_apm.cli.commands.config.APMClient", return_value=_fake_apm_client("did-abc"),
    ) as mock_cls:
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="p", verify_ssl=True, existing=existing, no_input=False,
        )
    assert device_id == "did-abc"
    assert "Connection verified" in status
    mock_cls.assert_called_once_with(
        "h", "u", "p", otp_code=None, device_id="did-abc", verify_ssl=True,
    )


async def test_verify_connection_does_not_reuse_device_id_when_host_changed() -> None:
    """A stored device_id is never sent when the host differs from the existing profile's."""
    existing = ProfileConfig(host="old-host", username="u", device_id="did-abc")
    with patch(
        "synology_apm.cli.commands.config.APMClient", return_value=_fake_apm_client("did-new"),
    ) as mock_cls:
        await _verify_connection_and_register_device(
            host="new-host", username="u", password="p", verify_ssl=True, existing=existing, no_input=False,
        )
    mock_cls.assert_called_once_with(
        "new-host", "u", "p", otp_code=None, device_id=None, verify_ssl=True,
    )


async def test_verify_connection_otp_required_prompts_and_registers_device() -> None:
    """OTPRequiredError on the trial connection prompts once, then registers a new device."""
    existing = ProfileConfig(host="h", username="u")
    with (
        patch(
            "synology_apm.cli.commands.config.APMClient",
            side_effect=[
                _fake_apm_client(raises=OTPRequiredError("two-factor code required")),
                _fake_apm_client("did-new"),
            ],
        ),
        patch("synology_apm.cli.commands.config.typer.prompt", return_value="123456") as mock_prompt,
    ):
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="p", verify_ssl=True, existing=existing, no_input=False,
        )
    assert device_id == "did-new"
    assert "Registered a trusted device" in status
    mock_prompt.assert_called_once_with("Two-factor authentication code")


async def test_verify_connection_otp_incorrect_retries_then_succeeds() -> None:
    """A wrong code re-prompts; a subsequent correct code registers the device."""
    existing = ProfileConfig(host="h", username="u")
    with (
        patch(
            "synology_apm.cli.commands.config.APMClient",
            side_effect=[
                _fake_apm_client(raises=OTPRequiredError("two-factor code required")),
                _fake_apm_client(raises=OTPIncorrectError("wrong code")),
                _fake_apm_client("did-new"),
            ],
        ),
        patch("synology_apm.cli.commands.config.typer.prompt", side_effect=["000000", "123456"]) as mock_prompt,
    ):
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="p", verify_ssl=True, existing=existing, no_input=False,
        )
    assert device_id == "did-new"
    assert "Registered a trusted device" in status
    assert mock_prompt.call_count == 2


async def test_verify_connection_otp_incorrect_exhausts_attempts() -> None:
    """Repeated OTPIncorrectError gives up after the configured attempt limit, without raising."""
    existing = ProfileConfig(host="h", username="u")
    with (
        patch(
            "synology_apm.cli.commands.config.APMClient",
            side_effect=[
                _fake_apm_client(raises=OTPRequiredError("two-factor code required")),
                _fake_apm_client(raises=OTPIncorrectError("wrong code")),
                _fake_apm_client(raises=OTPIncorrectError("wrong code")),
                _fake_apm_client(raises=OTPIncorrectError("wrong code")),
            ],
        ),
        patch("synology_apm.cli.commands.config.typer.prompt", return_value="000000"),
    ):
        result = await _verify_connection_and_register_device(
            host="h", username="u", password="p", verify_ssl=True, existing=existing, no_input=False,
        )
    assert result == ("", "")


async def test_verify_connection_unrelated_error_same_identity_preserves_existing_token() -> None:
    """An unrelated connection failure (e.g. bad password) for the same host/username preserves
    the previously-registered device token rather than discarding it."""
    existing = ProfileConfig(host="h", username="u", device_id="did-abc")
    with patch(
        "synology_apm.cli.commands.config.APMClient",
        return_value=_fake_apm_client(raises=AuthenticationError("bad password")),
    ):
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="wrong", verify_ssl=True, existing=existing, no_input=False,
        )
    assert device_id == "did-abc"
    assert status == ""


async def test_verify_connection_otp_retry_unrelated_error_stops_immediately() -> None:
    """An unrelated APMError raised *during* an OTP-code retry (not the initial trial connect)
    stops the retry loop immediately rather than being mistaken for another incorrect code."""
    existing = ProfileConfig(host="h", username="u")
    with (
        patch(
            "synology_apm.cli.commands.config.APMClient",
            side_effect=[
                _fake_apm_client(raises=OTPRequiredError("two-factor code required")),
                _fake_apm_client(raises=AuthenticationError("account disabled")),
            ],
        ),
        patch("synology_apm.cli.commands.config.typer.prompt", return_value="123456") as mock_prompt,
    ):
        device_id, status = await _verify_connection_and_register_device(
            host="h", username="u", password="p", verify_ssl=True, existing=existing, no_input=False,
        )
    assert device_id == ""
    assert status != ""
    mock_prompt.assert_called_once()


async def test_verify_connection_unrelated_error_different_identity_clears_token() -> None:
    """An unrelated connection failure for a changed host/username never carries the old
    (inapplicable) device token forward."""
    existing = ProfileConfig(host="old-host", username="u", device_id="did-abc")
    with patch(
        "synology_apm.cli.commands.config.APMClient",
        return_value=_fake_apm_client(raises=AuthenticationError("bad password")),
    ):
        device_id, status = await _verify_connection_and_register_device(
            host="new-host", username="u", password="wrong", verify_ssl=True, existing=existing, no_input=False,
        )
    assert device_id == ""
    assert status != ""
