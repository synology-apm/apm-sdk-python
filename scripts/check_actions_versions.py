"""Checks (and optionally rewrites) pinned GitHub Actions in .github/workflows/*.yml.

Pass 1 — discover every `uses:` step across the workflow files and parse its
         `owner/repo`, pinned commit SHA, and trailing `# vX.Y.Z` tag comment.
         A `./`-prefixed value (a local reusable workflow ref) is skipped; a
         pin that isn't shaped `owner/repo@<40-hex-sha>`, or is missing the
         tag comment, is itself a "GitHub Actions Security Conventions"
         violation and is reported without further checks.

Pass 2 — for each well-formed pin, find the highest version tag upstream
         (via `git ls-remote --tags`, comparing parsed (major, minor, patch)
         regardless of how many components a tag itself specifies — a pin on
         `v7` is compared against `v8.3.2` too) and resolve its commit SHA
         (preferring the dereferenced `<tag>^{}` line for an annotated tag).
         A discrepancy is reported only when that resolved SHA differs from
         the pinned SHA — a floating tag and an exact tag that currently
         point at the identical commit are not treated as outdated just
         because their labels differ.

Exit code 0 = clean; non-zero = errors printed to stderr.

`--write` / `--apply` (a deliberate deviation from this repo's other checker
scripts, which take no arguments): rewrite each outdated pin's `uses:` line in
place to the resolved SHA/tag, via a targeted string replacement of the exact
matched line — never a full YAML re-dump, which would silently drop comments
and reformat the rest of the file. There is no confirmation prompt: this only
ever touches local, git-tracked files, so reviewing `git diff` before
committing is the safety net (matching how `uv lock --upgrade` works).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"

_USES_LINE_RE = re.compile(r"^(?P<indent>\s*(?:-\s+)?)uses:\s*(?P<value>\S+)(?:\s+#\s*(?P<comment>\S+))?\s*$")
_PIN_RE = re.compile(r"^(?P<owner_repo>[^/]+/[^/@]+)@(?P<sha>[0-9a-f]{40})$")
_VERSION_RE = re.compile(r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?$")


@dataclass
class ActionPin:
    workflow_file: str
    line_no: int
    indent: str
    owner_repo: str
    sha: str
    tag: str


@dataclass
class Discrepancy:
    pin: ActionPin
    new_sha: str
    new_tag: str
    reason: str


def _yaml_uses_values(data: dict[str, Any]) -> set[str]:
    """Every genuine `uses:` value reachable via jobs/steps — used to filter out
    raw-text `uses:` matches that are just incidental substrings of a `run:` block."""
    values: set[str] = set()
    jobs = data.get("jobs") or {}
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        job_uses = job.get("uses")
        if isinstance(job_uses, str):
            values.add(job_uses)
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("uses"), str):
                values.add(step["uses"])
    return values


def _discover_pins() -> tuple[list[ActionPin], list[str]]:
    pins: list[ActionPin] = []
    errors: list[str] = []

    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        text = wf_path.read_text()
        data = yaml.safe_load(text) or {}
        valid_values = _yaml_uses_values(data)

        for line_no, line in enumerate(text.splitlines(), start=1):
            m = _USES_LINE_RE.match(line)
            if not m:
                continue
            value = m.group("value")
            if value not in valid_values:
                continue  # incidental "uses:" text inside a run: block, not a real step
            if value.startswith("./"):
                continue  # local reusable workflow ref — exempt

            comment = m.group("comment")
            pin_m = _PIN_RE.match(value)
            if pin_m is None:
                errors.append(
                    f"{wf_path.name}:{line_no}: {value!r} is not pinned to a full commit SHA "
                    f"(expected owner/repo@<40-hex-sha>)"
                )
                continue
            if not comment:
                errors.append(f"{wf_path.name}:{line_no}: {value!r} is missing a trailing '# vX.Y.Z' tag comment")
                continue

            pins.append(
                ActionPin(
                    workflow_file=wf_path.name,
                    line_no=line_no,
                    indent=m.group("indent"),
                    owner_repo=pin_m.group("owner_repo"),
                    sha=pin_m.group("sha"),
                    tag=comment,
                )
            )

    return pins, errors


def _run_git_ls_remote(*args: str) -> str:
    result = subprocess.run(["git", "ls-remote", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git ls-remote {' '.join(args)} failed")
    return result.stdout


def _resolve_tag_sha(owner_repo: str, tag: str) -> str | None:
    url = f"https://github.com/{owner_repo}.git"
    try:
        output = _run_git_ls_remote(url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    except RuntimeError:
        return None

    plain_sha: str | None = None
    for out_line in output.splitlines():
        if not out_line.strip():
            continue
        sha, ref = out_line.split("\t", 1)
        if ref == f"refs/tags/{tag}^{{}}":
            return sha  # annotated tag, dereferenced — prefer this
        if ref == f"refs/tags/{tag}":
            plain_sha = sha
    return plain_sha


def _list_tags(owner_repo: str) -> list[str]:
    url = f"https://github.com/{owner_repo}.git"
    try:
        output = _run_git_ls_remote("--tags", url)
    except RuntimeError:
        return []

    tags: set[str] = set()
    for out_line in output.splitlines():
        if not out_line.strip():
            continue
        _, ref = out_line.split("\t", 1)
        if not ref.startswith("refs/tags/"):
            continue
        tags.add(ref[len("refs/tags/") :].removesuffix("^{}"))
    return sorted(tags)


def _parse_version(tag: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(tag)
    if not m:
        return None
    return (int(m.group("major")), int(m.group("minor") or 0), int(m.group("patch") or 0))


def _best_tag(tag: str, all_tags: list[str]) -> str:
    """Highest version among `all_tags`, compared by parsed (major, minor, patch) —
    not restricted to tags with the same number of components as `tag` itself."""
    current = _parse_version(tag)
    if current is None:
        return tag  # pinned tag itself isn't a recognizable version — leave it alone

    best_tag, best_version = tag, current
    for candidate in all_tags:
        version = _parse_version(candidate)
        if version is not None and version > best_version:
            best_tag, best_version = candidate, version
    return best_tag


def _evaluate(
    pin: ActionPin,
    tags_cache: dict[str, list[str]],
    sha_cache: dict[tuple[str, str], str | None],
) -> Discrepancy | str | None:
    """Return a Discrepancy if `pin` is outdated, None if clean, or an error message
    if upstream couldn't be resolved at all."""
    if pin.owner_repo not in tags_cache:
        tags_cache[pin.owner_repo] = _list_tags(pin.owner_repo)
    best_tag = _best_tag(pin.tag, tags_cache[pin.owner_repo])

    key = (pin.owner_repo, best_tag)
    if key not in sha_cache:
        sha_cache[key] = _resolve_tag_sha(pin.owner_repo, best_tag)
    resolved_sha = sha_cache[key]

    if resolved_sha is None:
        return (
            f"{pin.workflow_file}:{pin.line_no}: could not resolve tag {best_tag!r} for "
            f"{pin.owner_repo} (network error or the tag no longer exists)"
        )

    if resolved_sha == pin.sha:
        return None  # same commit already pinned, regardless of which tag label resolves to it

    if best_tag != pin.tag:
        return Discrepancy(
            pin, resolved_sha, best_tag, f"newer tag {best_tag!r} available (currently pinned at {pin.tag!r})"
        )
    return Discrepancy(
        pin, resolved_sha, pin.tag, f"tag {pin.tag!r} now resolves to {resolved_sha}, but pinned SHA is {pin.sha}"
    )


def _rewrite_pin(discrepancy: Discrepancy) -> bool:
    path = WORKFLOWS_DIR / discrepancy.pin.workflow_file
    lines = path.read_text().splitlines(keepends=True)
    idx = discrepancy.pin.line_no - 1
    if idx >= len(lines) or not _USES_LINE_RE.match(lines[idx].rstrip("\n")):
        return False

    newline = "\n" if lines[idx].endswith("\n") else ""
    new_value = f"{discrepancy.pin.owner_repo}@{discrepancy.new_sha}"
    lines[idx] = f"{discrepancy.pin.indent}uses: {new_value} # {discrepancy.new_tag}{newline}"
    path.write_text("".join(lines))
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write",
        "--apply",
        dest="write",
        action="store_true",
        help="Rewrite outdated pins in place instead of only reporting them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pins, errors = _discover_pins()

    tags_cache: dict[str, list[str]] = {}
    sha_cache: dict[tuple[str, str], str | None] = {}
    discrepancies: list[Discrepancy] = []

    for pin in pins:
        result = _evaluate(pin, tags_cache, sha_cache)
        if isinstance(result, str):
            errors.append(result)
        elif isinstance(result, Discrepancy):
            discrepancies.append(result)

    if args.write:
        rewritten = 0
        for discrepancy in discrepancies:
            if _rewrite_pin(discrepancy):
                rewritten += 1
            else:
                errors.append(f"{discrepancy.pin.workflow_file}:{discrepancy.pin.line_no}: failed to rewrite pin")

        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1
        print(f"OK: rewrote {rewritten} pin(s); {len(pins) - len(discrepancies)} already current.")
        return 0

    for discrepancy in discrepancies:
        errors.append(f"{discrepancy.pin.workflow_file}:{discrepancy.pin.line_no}: {discrepancy.reason}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    workflow_count = len({pin.workflow_file for pin in pins})
    print(f"OK: {len(pins)} action pin(s) across {workflow_count} workflow file(s), all current with upstream tags.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
