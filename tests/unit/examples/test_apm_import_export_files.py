"""Tests for file I/O helpers in examples/apm_import_export.py."""
from __future__ import annotations

import csv
import os
import stat
from pathlib import Path

import apm_import_export as ie
import pytest

# ── _load_yaml ────────────────────────────────────────────────────────────────


def test_load_yaml_valid(tmp_path: Path) -> None:
    """A valid YAML file with version 1 is returned as a dict."""
    p = tmp_path / "config.yaml"
    p.write_text("version: 1\nprotection_plans: []\n", encoding="utf-8")
    result = ie._load_yaml(str(p))
    assert result == {"version": 1, "protection_plans": []}


def test_load_yaml_not_a_mapping(tmp_path: Path) -> None:
    """A YAML file whose top-level value is a list raises ValueError."""
    p = tmp_path / "bad.yaml"
    p.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping at the top level"):
        ie._load_yaml(str(p))


def test_load_yaml_missing_version(tmp_path: Path) -> None:
    """A YAML mapping with no version key raises ValueError about unsupported schema version."""
    p = tmp_path / "no_version.yaml"
    p.write_text("protection_plans: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported YAML schema version"):
        ie._load_yaml(str(p))


def test_load_yaml_wrong_version(tmp_path: Path) -> None:
    """A YAML mapping with version != 1 raises ValueError about unsupported schema version."""
    p = tmp_path / "v2.yaml"
    p.write_text("version: 2\nprotection_plans: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported YAML schema version"):
        ie._load_yaml(str(p))


# ── _warn_if_world_readable ───────────────────────────────────────────────────


def test_warn_if_world_readable_group_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file with mode 0o644 prints a WARNING to stderr containing 'chmod 600'."""
    p = tmp_path / "creds.csv"
    p.write_text("dummy\n", encoding="utf-8")
    os.chmod(p, 0o644)
    ie._warn_if_world_readable(str(p), "test-creds")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "chmod 600" in err


def test_warn_if_world_readable_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file with mode 0o600 produces no stderr output."""
    p = tmp_path / "creds.csv"
    p.write_text("dummy\n", encoding="utf-8")
    os.chmod(p, 0o600)
    ie._warn_if_world_readable(str(p), "test-creds")
    err = capsys.readouterr().err
    assert err == ""


# ── _load_fs_credentials / _load_rs_credentials ──────────────────────────────


def _write_cred_csv(path: Path, content: str) -> None:
    """Write credential CSV content and set permissions to 0o600."""
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def test_load_fs_credentials_happy_path(tmp_path: Path) -> None:
    """A well-formed FS credentials CSV is parsed into the expected dict."""
    p = tmp_path / "fs.csv"
    _write_cred_csv(p, "endpoint,login_user,password\nhost,user,pass\n")
    result = ie._load_fs_credentials(str(p))
    assert result == {("host", "user"): "pass"}


def test_load_fs_credentials_skips_comments_and_blanks(tmp_path: Path) -> None:
    """Lines starting with '#' and blank lines are skipped."""
    p = tmp_path / "fs.csv"
    _write_cred_csv(
        p,
        "endpoint,login_user,password\n"
        "# this is a comment\n"
        "\n"
        "host,user,secret\n",
    )
    result = ie._load_fs_credentials(str(p))
    assert result == {("host", "user"): "secret"}


def test_load_fs_credentials_bom(tmp_path: Path) -> None:
    """A UTF-8 BOM at the start of the file is handled transparently."""
    p = tmp_path / "fs_bom.csv"
    p.write_text("endpoint,login_user,password\nhost,user,pass\n", encoding="utf-8-sig")
    os.chmod(p, 0o600)
    result = ie._load_fs_credentials(str(p))
    assert result == {("host", "user"): "pass"}


def test_load_fs_credentials_missing_required_column(tmp_path: Path) -> None:
    """A header missing a required column raises ValueError."""
    p = tmp_path / "bad_fs.csv"
    _write_cred_csv(p, "endpoint,login_user\nhost,user\n")
    with pytest.raises(ValueError, match="must have a header row"):
        ie._load_fs_credentials(str(p))


@pytest.mark.parametrize(
    ("row", "error_match"),
    [
        (",user,pass", "endpoint must not be empty"),
        ("host,,pass", "login_user must not be empty"),
    ],
    ids=["empty-endpoint", "empty-login-user"],
)
def test_load_fs_credentials_empty_required_field(
    tmp_path: Path, row: str, error_match: str
) -> None:
    """An empty required field on a data row raises ValueError naming the field."""
    p = tmp_path / "fs.csv"
    _write_cred_csv(p, f"endpoint,login_user,password\n{row}\n")
    with pytest.raises(ValueError, match=error_match):
        ie._load_fs_credentials(str(p))


def test_load_fs_credentials_extra_columns_ignored(tmp_path: Path) -> None:
    """Extra columns beyond the required three are ignored."""
    p = tmp_path / "fs.csv"
    _write_cred_csv(p, "endpoint,login_user,password,extra\nhost,user,pass,ignored\n")
    result = ie._load_fs_credentials(str(p))
    assert result == {("host", "user"): "pass"}


def test_load_rs_credentials_happy_path(tmp_path: Path) -> None:
    """A well-formed RS credentials CSV is parsed into the expected dict."""
    p = tmp_path / "rs.csv"
    _write_cred_csv(
        p,
        "storage_type,endpoint,vault_name,access_key,secret_key,relink_encryption_key\n"
        "s3,https://s3.example.com,MyBucket,AK,SK,EK\n",
    )
    result = ie._load_rs_credentials(str(p))
    assert result == {
        ("s3", "https://s3.example.com", "MyBucket"): {
            "access_key": "AK",
            "secret_key": "SK",
            "relink_encryption_key": "EK",
        }
    }


def test_load_rs_credentials_missing_required_column(tmp_path: Path) -> None:
    """A header missing a required column raises ValueError."""
    p = tmp_path / "bad_rs.csv"
    _write_cred_csv(
        p,
        "storage_type,endpoint,vault_name,access_key\n"
        "s3,https://s3.example.com,MyBucket,AK\n",
    )
    with pytest.raises(ValueError, match="must have a header row"):
        ie._load_rs_credentials(str(p))


def test_load_rs_credentials_optional_relink_key_missing(tmp_path: Path) -> None:
    """When relink_encryption_key column is absent, it defaults to '' in the result dict."""
    p = tmp_path / "rs_no_relink.csv"
    _write_cred_csv(
        p,
        "storage_type,endpoint,vault_name,access_key,secret_key\n"
        "s3,https://s3.example.com,MyBucket,AK,SK\n",
    )
    result = ie._load_rs_credentials(str(p))
    assert result == {
        ("s3", "https://s3.example.com", "MyBucket"): {
            "access_key": "AK",
            "secret_key": "SK",
            "relink_encryption_key": "",
        }
    }


# ── _write_rs_credentials ─────────────────────────────────────────────────────


def _make_initial_rs_csv(
    path: Path, creds: dict[tuple[str, str, str], dict[str, str]]
) -> None:
    """Write an RS credentials CSV that _write_rs_credentials can rename to .bak."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "storage_type",
                "endpoint",
                "vault_name",
                "access_key",
                "secret_key",
                "relink_encryption_key",
            ],
        )
        writer.writeheader()
        for (storage_type, endpoint, vault_name), vals in creds.items():
            writer.writerow(
                {
                    "storage_type": storage_type,
                    "endpoint": endpoint,
                    "vault_name": vault_name,
                    "access_key": vals.get("access_key", ""),
                    "secret_key": vals.get("secret_key", ""),
                    "relink_encryption_key": vals.get("relink_encryption_key", ""),
                }
            )
    os.chmod(path, 0o600)


def _sample_rs_creds() -> dict[tuple[str, str, str], dict[str, str]]:
    return {
        ("s3", "https://s3.example.com", "MyBucket"): {
            "access_key": "AK",
            "secret_key": "SK",
            "relink_encryption_key": "EK",
        }
    }


def test_write_rs_credentials_roundtrip(tmp_path: Path) -> None:
    """Written credentials round-trip correctly through _load_rs_credentials."""
    p = tmp_path / "rs.csv"
    creds = _sample_rs_creds()
    _make_initial_rs_csv(p, creds)
    ie._write_rs_credentials(str(p), creds, "20260101")
    result = ie._load_rs_credentials(str(p))
    assert result == creds


def test_write_rs_credentials_keeps_owner_only_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rewritten credential file stays owner-only, so reloading it does not warn."""
    p = tmp_path / "rs.csv"
    creds = _sample_rs_creds()
    _make_initial_rs_csv(p, creds)
    ie._write_rs_credentials(str(p), creds, "20260101")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    ie._load_rs_credentials(str(p))
    assert "readable by group or others" not in capsys.readouterr().err


def test_write_rs_credentials_creates_bak(tmp_path: Path) -> None:
    """The original file is renamed to .<suffix>.bak after writing."""
    p = tmp_path / "rs.csv"
    creds = _sample_rs_creds()
    _make_initial_rs_csv(p, creds)
    ie._write_rs_credentials(str(p), creds, "20260101")
    bak = tmp_path / "rs.csv.20260101.bak"
    assert bak.exists()


def test_write_rs_credentials_tmp_cleaned_up(tmp_path: Path) -> None:
    """The .tmp working file does not exist after a successful write."""
    p = tmp_path / "rs.csv"
    creds = _sample_rs_creds()
    _make_initial_rs_csv(p, creds)
    ie._write_rs_credentials(str(p), creds, "20260101")
    tmp = tmp_path / "rs.csv.tmp"
    assert not tmp.exists()
