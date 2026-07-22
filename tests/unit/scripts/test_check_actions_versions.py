"""Tests for scripts/check_actions_versions.py.

No test here hits the network: `check_actions_versions._run_git_ls_remote` (the single
seam both tag-resolution and tag-listing funnel through) is monkeypatched with a fake that
answers from an in-memory fixture instead. Unlike `test_check_version_consistency.py`, there
is no "regression guard against the real repo files" test — that pattern only works offline,
and this checker's real-repo pass needs live network access to github.com.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import check_actions_versions
import pytest

_SHA_A = "a" * 40
_SHA_B = "b" * 40
_SHA_C = "c" * 40


def _write_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str) -> Path:
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    path = workflows_dir / name
    path.write_text(text)
    monkeypatch.setattr(check_actions_versions, "WORKFLOWS_DIR", workflows_dir)
    return path


def _owner_repo_from_url(url: str) -> str:
    return url.removeprefix("https://github.com/").removesuffix(".git")


def _fake_ls_remote(
    tag_shas: dict[tuple[str, str], str], repo_tags: dict[str, list[str]]
) -> Callable[..., str]:
    def fake(*args: str) -> str:
        if args[0] == "--tags":
            owner_repo = _owner_repo_from_url(args[1])
            tags = repo_tags.get(owner_repo, [])
            return "".join(f"{_SHA_A}\trefs/tags/{tag}\n" for tag in tags)

        owner_repo = _owner_repo_from_url(args[0])
        tag = args[1].removeprefix("refs/tags/")
        sha = tag_shas.get((owner_repo, tag))
        if sha is None:
            return ""
        return f"{sha}\trefs/tags/{tag}\n"

    return fake


def _patch_ls_remote(monkeypatch: pytest.MonkeyPatch, tag_shas: dict[tuple[str, str], str], repo_tags: dict[str, list[str]]) -> None:
    monkeypatch.setattr(check_actions_versions, "_run_git_ls_remote", _fake_ls_remote(tag_shas, repo_tags))


_CLEAN_WORKFLOW = f"""\
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{_SHA_A} # v7
"""


class TestDiscoverPins:
    def test_valid_pin_parsed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_workflow(tmp_path, monkeypatch, "ci.yml", _CLEAN_WORKFLOW)

        pins, errors = check_actions_versions._discover_pins()

        assert errors == []
        assert len(pins) == 1
        pin = pins[0]
        assert pin.owner_repo == "actions/checkout"
        assert pin.sha == _SHA_A
        assert pin.tag == "v7"
        assert pin.workflow_file == "ci.yml"

    def test_local_reusable_workflow_ref_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        text = """\
name: Release
on: push
jobs:
  test:
    uses: ./.github/workflows/ci.yml
"""
        _write_workflow(tmp_path, monkeypatch, "release.yml", text)

        pins, errors = check_actions_versions._discover_pins()

        assert pins == []
        assert errors == []

    def test_missing_tag_comment_flagged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        text = f"""\
name: CI
on: push
jobs:
  build:
    steps:
      - uses: actions/checkout@{_SHA_A}
"""
        _write_workflow(tmp_path, monkeypatch, "ci.yml", text)

        pins, errors = check_actions_versions._discover_pins()

        assert pins == []
        assert len(errors) == 1
        assert "missing a trailing '# vX.Y.Z' tag comment" in errors[0]

    def test_non_sha_pin_flagged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    steps:
      - uses: actions/checkout@v7 # v7
"""
        _write_workflow(tmp_path, monkeypatch, "ci.yml", text)

        pins, errors = check_actions_versions._discover_pins()

        assert pins == []
        assert len(errors) == 1
        assert "not pinned to a full commit SHA" in errors[0]

    def test_uses_inside_run_block_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        text = f"""\
name: CI
on: push
jobs:
  build:
    steps:
      - uses: actions/checkout@{_SHA_A} # v7
      - run: |
          echo "uses: not-a-real/action@{_SHA_B} # v1"
"""
        _write_workflow(tmp_path, monkeypatch, "ci.yml", text)

        pins, errors = check_actions_versions._discover_pins()

        assert errors == []
        assert len(pins) == 1
        assert pins[0].owner_repo == "actions/checkout"


class TestParseVersion:
    def test_major_only_tag(self) -> None:
        assert check_actions_versions._parse_version("v7") == (7, 0, 0)

    def test_full_semver_tag(self) -> None:
        assert check_actions_versions._parse_version("v8.3.2") == (8, 3, 2)

    def test_non_semver_tag_returns_none(self) -> None:
        assert check_actions_versions._parse_version("latest") is None


class TestBestTag:
    def test_picks_higher_version_regardless_of_component_count(self) -> None:
        best = check_actions_versions._best_tag("v7", ["v5", "v6", "v7", "v8.3.2"])
        assert best == "v8.3.2"  # not restricted to tags with the same number of components

    def test_no_change_when_already_latest(self) -> None:
        best = check_actions_versions._best_tag("v8.3.2", ["v7", "v8", "v8.3.2"])
        assert best == "v8.3.2"

    def test_unparseable_tag_left_unchanged(self) -> None:
        assert check_actions_versions._best_tag("latest", ["v1", "v2"]) == "latest"


class TestResolveTagSha:
    def test_prefers_dereferenced_annotated_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(*args: str) -> str:
            return f"{_SHA_A}\trefs/tags/v1\n{_SHA_B}\trefs/tags/v1^{{}}\n"

        monkeypatch.setattr(check_actions_versions, "_run_git_ls_remote", fake)
        assert check_actions_versions._resolve_tag_sha("owner/repo", "v1") == _SHA_B

    def test_falls_back_to_lightweight_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(*args: str) -> str:
            return f"{_SHA_A}\trefs/tags/v1\n"

        monkeypatch.setattr(check_actions_versions, "_run_git_ls_remote", fake)
        assert check_actions_versions._resolve_tag_sha("owner/repo", "v1") == _SHA_A

    def test_returns_none_on_git_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(*args: str) -> str:
            raise RuntimeError("network unreachable")

        monkeypatch.setattr(check_actions_versions, "_run_git_ls_remote", fake)
        assert check_actions_versions._resolve_tag_sha("owner/repo", "v1") is None


class TestMain:
    def test_clean_state_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _write_workflow(tmp_path, monkeypatch, "ci.yml", _CLEAN_WORKFLOW)
        _patch_ls_remote(
            monkeypatch,
            tag_shas={("actions/checkout", "v7"): _SHA_A},
            repo_tags={"actions/checkout": ["v6", "v7"]},
        )

        assert check_actions_versions.main([]) == 0
        assert "OK: 1 action pin(s)" in capsys.readouterr().out

    def test_sha_mismatch_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _write_workflow(tmp_path, monkeypatch, "ci.yml", _CLEAN_WORKFLOW)
        _patch_ls_remote(
            monkeypatch,
            tag_shas={("actions/checkout", "v7"): _SHA_B},  # tag now points elsewhere
            repo_tags={"actions/checkout": ["v7"]},
        )

        assert check_actions_versions.main([]) == 1
        err = capsys.readouterr().err
        assert f"now resolves to {_SHA_B}, but pinned SHA is {_SHA_A}" in err

    def test_newer_tag_available_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _write_workflow(tmp_path, monkeypatch, "ci.yml", _CLEAN_WORKFLOW)
        _patch_ls_remote(
            monkeypatch,
            # v8.3.2 has more components than the pinned v7 — the comparison is not
            # restricted to same-shaped tags, and this also resolves to a genuinely
            # different commit, so it must still be reported.
            tag_shas={("actions/checkout", "v7"): _SHA_A, ("actions/checkout", "v8.3.2"): _SHA_C},
            repo_tags={"actions/checkout": ["v6", "v7", "v8.3.2"]},
        )

        assert check_actions_versions.main([]) == 1
        err = capsys.readouterr().err
        assert "newer tag 'v8.3.2' available (currently pinned at 'v7')" in err

    def test_same_commit_under_a_different_tag_label_is_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_workflow(tmp_path, monkeypatch, "ci.yml", _CLEAN_WORKFLOW)
        _patch_ls_remote(
            monkeypatch,
            # v7.0.1 outranks the pinned v7 by parsed version, but resolves to the
            # exact same commit already pinned — not real drift, just a more
            # specific alias for the same release, so this must not be flagged.
            tag_shas={("actions/checkout", "v7"): _SHA_A, ("actions/checkout", "v7.0.1"): _SHA_A},
            repo_tags={"actions/checkout": ["v6", "v7", "v7.0.1"]},
        )

        assert check_actions_versions.main([]) == 0
        assert "OK: 1 action pin(s)" in capsys.readouterr().out

    def test_write_rewrites_outdated_pin_in_place(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = f"""\
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      # a comment that must survive untouched
      - uses: actions/checkout@{_SHA_A} # v7
      - run: echo hello
"""
        path = _write_workflow(tmp_path, monkeypatch, "ci.yml", text)
        _patch_ls_remote(
            monkeypatch,
            tag_shas={("actions/checkout", "v8"): _SHA_C},
            repo_tags={"actions/checkout": ["v7", "v8"]},
        )

        assert check_actions_versions.main(["--write"]) == 0
        out = capsys.readouterr().out
        assert "OK: rewrote 1 pin(s)" in out

        new_text = path.read_text()
        assert f"uses: actions/checkout@{_SHA_C} # v8" in new_text
        assert "# a comment that must survive untouched" in new_text
        assert "- run: echo hello" in new_text

    def test_write_on_clean_state_is_a_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _write_workflow(tmp_path, monkeypatch, "ci.yml", _CLEAN_WORKFLOW)
        original = path.read_text()
        _patch_ls_remote(
            monkeypatch,
            tag_shas={("actions/checkout", "v7"): _SHA_A},
            repo_tags={"actions/checkout": ["v6", "v7"]},
        )

        assert check_actions_versions.main(["--write"]) == 0
        assert "OK: rewrote 0 pin(s)" in capsys.readouterr().out
        assert path.read_text() == original
