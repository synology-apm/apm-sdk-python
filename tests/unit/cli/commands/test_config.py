"""Unit tests for synology_apm.cli.commands.config — the `config` Typer command group."""
from __future__ import annotations

from unittest.mock import patch

import keyring.errors
import pytest

from synology_apm.cli.commands.config import PasswordDecision, _resolve_password_decision
from synology_apm.cli.main import app
from synology_apm.sdk import DEFAULT_PROFILE, AppConfig, PasswordStorage, ProfileConfig
from tests.unit.cli.conftest import runner

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
         patch("synology_apm.cli.commands.config.save_config"):
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
         patch("synology_apm.sdk.config.keyring.set_password") as mock_set:
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
         patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete:
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
         patch("synology_apm.sdk.config.keyring.set_password") as mock_set:
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
         patch("synology_apm.sdk.config.keyring.delete_password") as mock_delete:
        result = runner.invoke(
            app,
            ["config", "set", "--host", "newhost", "--profile", "default"],
            input="\nn\n",
        )
    assert result.exit_code == 0
    mock_set.assert_not_called()
    mock_delete.assert_not_called()
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
         patch("synology_apm.cli.commands.config.save_config") as mock_save:
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
