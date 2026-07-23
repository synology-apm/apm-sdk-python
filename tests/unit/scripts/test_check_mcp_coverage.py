"""Tests for scripts/check_mcp_coverage.py's manifest-consistency checks."""
from __future__ import annotations

from pathlib import Path

import check_mcp_coverage
import pytest

import synology_apm.mcp._server as mcp_server


def _write_manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "mcp_coverage.toml"
    path.write_text(text)
    return path


def _required_modes_all(*tool_names: str) -> dict[str, str]:
    """A tool_required_modes() fake where every listed tool's required mode is
    "readonly", so Pass 6 only flags a mismatch for tests whose manifest declares
    a different mode for that tool."""
    return {name: "readonly" for name in tool_names}


class TestLoadManifest:
    def test_loads_mapping_and_not_exposed_sections(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            [[mapping]]
            sdk_path = "get_site_info"
            mcp_tool = "get_site_info"
            mode = "readonly"

            [[not_exposed]]
            sdk_path = "backup_servers.get_by_name"
            reason = "internal resolution alias"
            """,
        )
        monkeypatch.setattr(check_mcp_coverage, "MANIFEST", manifest)

        data = check_mcp_coverage._load_manifest()

        assert data["mapping"] == [{"sdk_path": "get_site_info", "mcp_tool": "get_site_info", "mode": "readonly"}]
        assert data["not_exposed"] == [
            {"sdk_path": "backup_servers.get_by_name", "reason": "internal resolution alias"}
        ]


class TestResolveSdkPath:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("get_site_info", True),
            ("get_site_info_typo", False),
        ],
        ids=["real_async_method", "nonexistent_path"],
    )
    def test_resolves_path_to_expected_result(self, path: str, expected: bool) -> None:
        from synology_apm.sdk import APMClient

        client = APMClient("host", "user", "pass")
        assert check_mcp_coverage._resolve_sdk_path(client, path) is expected

    def test_returns_false_for_non_async_attribute(self) -> None:
        from synology_apm.sdk import APMClient

        client = APMClient("host", "user", "pass")
        # "connect" exists but resolving through a non-callable/non-coroutine attribute
        # (a plain string) must not be mistaken for a valid SDK method.
        client.not_a_coroutine = "plain-value"  # type: ignore[attr-defined]
        assert check_mcp_coverage._resolve_sdk_path(client, "not_a_coroutine") is False


class TestWalkSdkSurface:
    def test_finds_top_level_async_method(self) -> None:
        class Stub:
            async def do_thing(self) -> None: ...

        paths = check_mcp_coverage._walk_sdk_surface(Stub())
        assert paths == {"do_thing"}

    def test_recurses_into_sdk_collection_typed_property(self) -> None:
        class FakeCollection:
            async def list(self) -> None: ...

        FakeCollection.__module__ = "synology_apm.sdk.collections.fake"

        class Stub:
            def __init__(self) -> None:
                self.widgets = FakeCollection()

            async def get_site_info(self) -> None: ...

        paths = check_mcp_coverage._walk_sdk_surface(Stub())
        assert paths == {"get_site_info", "widgets.list"}

    def test_does_not_recurse_into_non_collection_typed_property(self) -> None:
        class Plain:
            async def list(self) -> None: ...

        class Stub:
            def __init__(self) -> None:
                self.widgets = Plain()

        paths = check_mcp_coverage._walk_sdk_surface(Stub())
        assert paths == set()


class TestMain:
    def test_duplicate_sdk_path_fails_pass_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            [[mapping]]
            sdk_path = "get_site_info"
            mcp_tool = "get_site_info"
            mode = "readonly"

            [[mapping]]
            sdk_path = "get_site_info"
            mcp_tool = "get_site_info_again"
            mode = "readonly"
            """,
        )
        monkeypatch.setattr(check_mcp_coverage, "MANIFEST", manifest)
        monkeypatch.setattr(check_mcp_coverage, "_walk_sdk_surface", lambda client: set())
        monkeypatch.setattr(
            mcp_server,
            "tool_required_modes",
            lambda: _required_modes_all("get_site_info", "get_site_info_again"),
        )

        assert check_mcp_coverage.main() == 1
        err = capsys.readouterr().err
        assert "duplicate sdk_path in manifest: 'get_site_info'" in err

    def test_typo_sdk_path_fails_pass_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            [[mapping]]
            sdk_path = "get_site_info_typo"
            mcp_tool = "get_site_info"
            mode = "readonly"
            """,
        )
        monkeypatch.setattr(check_mcp_coverage, "MANIFEST", manifest)
        monkeypatch.setattr(check_mcp_coverage, "_walk_sdk_surface", lambda client: set())
        monkeypatch.setattr(
            mcp_server, "tool_required_modes", lambda: _required_modes_all("get_site_info")
        )

        assert check_mcp_coverage.main() == 1
        err = capsys.readouterr().err
        assert "manifest sdk_path entries that do not resolve to a real SDK method:" in err
        assert "get_site_info_typo" in err

    def test_unmapped_sdk_method_fails_pass_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            [[mapping]]
            sdk_path = "get_site_info"
            mcp_tool = "get_site_info"
            mode = "readonly"
            """,
        )
        monkeypatch.setattr(check_mcp_coverage, "MANIFEST", manifest)
        monkeypatch.setattr(
            check_mcp_coverage, "_walk_sdk_surface", lambda client: {"get_site_info", "some_new_method"}
        )
        monkeypatch.setattr(
            mcp_server, "tool_required_modes", lambda: _required_modes_all("get_site_info")
        )

        assert check_mcp_coverage.main() == 1
        err = capsys.readouterr().err
        assert "SDK methods with no [[mapping]] or [[not_exposed]] manifest entry:" in err
        assert "some_new_method" in err

    def test_stale_mapping_fails_pass_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            [[mapping]]
            sdk_path = "get_site_info"
            mcp_tool = "get_site_info"
            mode = "readonly"
            """,
        )
        monkeypatch.setattr(check_mcp_coverage, "MANIFEST", manifest)
        monkeypatch.setattr(check_mcp_coverage, "_walk_sdk_surface", lambda client: {"get_site_info"})
        monkeypatch.setattr(mcp_server, "tool_required_modes", lambda: _required_modes_all())

        assert check_mcp_coverage.main() == 1
        err = capsys.readouterr().err
        assert "stale [[mapping]] entries — tool not registered in admin mode:" in err
        assert "get_site_info" in err

    def test_unmapped_registered_tool_fails_pass_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            [[mapping]]
            sdk_path = "get_site_info"
            mcp_tool = "get_site_info"
            mode = "readonly"
            """,
        )
        monkeypatch.setattr(check_mcp_coverage, "MANIFEST", manifest)
        monkeypatch.setattr(check_mcp_coverage, "_walk_sdk_surface", lambda client: {"get_site_info"})
        monkeypatch.setattr(
            mcp_server,
            "tool_required_modes",
            lambda: _required_modes_all("get_site_info", "some_untracked_tool"),
        )

        assert check_mcp_coverage.main() == 1
        err = capsys.readouterr().err
        assert "registered tools missing from mcp_coverage.toml [[mapping]]:" in err
        assert "some_untracked_tool" in err

    def test_mode_mismatch_fails_pass_6(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A tool actually gated at "operator" (e.g. its inline mode_allows() call was
        edited) but still declared "readonly" in the manifest must be caught, not just
        the reverse — this is the drift case the tool-mode single-sourcing (test_server.py
        deriving _EXPECTED_TOOL_MODES from this same manifest) can't catch on its own,
        since that only checks the manifest agrees with itself."""
        manifest = _write_manifest(
            tmp_path,
            """
            [[mapping]]
            sdk_path = "get_site_info"
            mcp_tool = "get_site_info"
            mode = "readonly"
            """,
        )
        monkeypatch.setattr(check_mcp_coverage, "MANIFEST", manifest)
        monkeypatch.setattr(check_mcp_coverage, "_walk_sdk_surface", lambda client: {"get_site_info"})
        monkeypatch.setattr(mcp_server, "tool_required_modes", lambda: {"get_site_info": "operator"})

        assert check_mcp_coverage.main() == 1
        err = capsys.readouterr().err
        assert "manifest mode does not match actual tool registration mode:" in err
        assert "get_site_info: manifest declares mode='readonly' but is actually gated at mode='operator'" in err

    def test_real_manifest_and_tree_pass_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Regression guard: the actual repo manifest against the actual registered
        tool set and SDK surface must stay consistent (same contract as `make
        check-mcp-coverage`, run here so a pytest run also catches drift)."""
        assert check_mcp_coverage.main() == 0
        out = capsys.readouterr().out
        assert out.startswith("OK:")
