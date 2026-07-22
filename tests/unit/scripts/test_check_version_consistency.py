"""Tests for scripts/check_version_consistency.py."""
from __future__ import annotations

from pathlib import Path

import check_version_consistency
import pytest

_SDK = """
[project]
name = "synology-apm-sdk"
version = "1.2.3"
dependencies = []
"""


def _cli_toml(version: str, pin: str) -> str:
    return f"""
[project]
name = "synology-apm-cli"
version = "{version}"
dependencies = ["synology-apm-sdk=={pin}", "typer>=0.12,<0.26"]
"""


def _mcp_toml(version: str, pin: str) -> str:
    return f"""
[project]
name = "synology-apm-mcp"
version = "{version}"
dependencies = ["synology-apm-sdk=={pin}", "fastmcp>=2.0"]
"""


def _write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, sdk: str, cli: str, mcp: str) -> None:
    paths = {}
    for name, text in (("synology-apm-sdk", sdk), ("synology-apm-cli", cli), ("synology-apm-mcp", mcp)):
        path = tmp_path / name / "pyproject.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        paths[name] = path
    monkeypatch.setattr(check_version_consistency, "PYPROJECT_PATHS", paths)


class TestSdkPin:
    def test_extracts_pinned_version(self) -> None:
        assert check_version_consistency._sdk_pin(["synology-apm-sdk==1.2.3", "typer>=0.12"]) == "1.2.3"

    def test_returns_none_when_absent(self) -> None:
        assert check_version_consistency._sdk_pin(["typer>=0.12"]) is None


class TestMain:
    def test_matching_versions_and_pins_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
        _write(
            tmp_path, monkeypatch,
            sdk=_SDK,
            cli=_cli_toml("1.2.3", "1.2.3"),
            mcp=_mcp_toml("1.2.3", "1.2.3"),
        )

        assert check_version_consistency.main() == 0
        assert "OK: all packages at version '1.2.3'" in capsys.readouterr().out

    def test_mismatched_own_version_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
        _write(
            tmp_path, monkeypatch,
            sdk=_SDK,
            cli=_cli_toml("1.2.2", "1.2.3"),
            mcp=_mcp_toml("1.2.3", "1.2.3"),
        )

        assert check_version_consistency.main() == 1
        err = capsys.readouterr().err
        assert "synology-apm-cli/pyproject.toml version='1.2.2' does not match synology-apm-sdk version='1.2.3'" in err

    def test_stale_dependency_pin_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
        _write(
            tmp_path, monkeypatch,
            sdk=_SDK,
            cli=_cli_toml("1.2.3", "1.2.3"),
            mcp=_mcp_toml("1.2.3", "1.2.2"),
        )

        assert check_version_consistency.main() == 1
        err = capsys.readouterr().err
        assert "synology-apm-mcp/pyproject.toml pins synology-apm-sdk==1.2.2, but synology-apm-sdk version='1.2.3'" in err

    def test_missing_dependency_pin_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
        mcp = """
[project]
name = "synology-apm-mcp"
version = "1.2.3"
dependencies = ["fastmcp>=2.0"]
"""
        _write(tmp_path, monkeypatch, sdk=_SDK, cli=_cli_toml("1.2.3", "1.2.3"), mcp=mcp)

        assert check_version_consistency.main() == 1
        err = capsys.readouterr().err
        assert "synology-apm-mcp/pyproject.toml is missing a synology-apm-sdk== dependency pin" in err

    def test_real_repo_pyproject_files_pass_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Regression guard: the actual repo's three pyproject.toml files must stay
        version-consistent (same contract as `make check-version-consistency`, run
        here so a pytest run also catches drift)."""
        assert check_version_consistency.main() == 0
        assert capsys.readouterr().out.startswith("OK:")
