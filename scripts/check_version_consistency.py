"""Version consistency check: synology-apm-sdk / -cli / -mcp share one lockstep version.

Pass 1 — every package's `project.version` in its own pyproject.toml must equal
         synology-apm-sdk's version (the three packages are released together; see
         CLAUDE.md "Release / Version Bump").

Pass 2 — the `synology-apm-sdk==X.Y.Z` pin in synology-apm-cli's and
         synology-apm-mcp's `project.dependencies` must equal synology-apm-sdk's
         version (catches a version bump that updated a package's own `version`
         field but left its dependency pin on the SDK stale).

Exit code 0 = clean; non-zero = errors printed to stderr.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
PACKAGES = ("synology-apm-sdk", "synology-apm-cli", "synology-apm-mcp")
PYPROJECT_PATHS = {name: ROOT / "packages" / name / "pyproject.toml" for name in PACKAGES}


def _load(name: str) -> dict[str, Any]:
    with open(PYPROJECT_PATHS[name], "rb") as f:
        return tomllib.load(f)


def _sdk_pin(dependencies: list[str]) -> str | None:
    for dep in dependencies:
        if dep.startswith("synology-apm-sdk=="):
            return dep.split("==", 1)[1]
    return None


def main() -> int:
    data = {name: _load(name) for name in PACKAGES}
    versions = {name: data[name]["project"]["version"] for name in PACKAGES}
    sdk_version = versions["synology-apm-sdk"]

    errors: list[str] = []
    for name in ("synology-apm-cli", "synology-apm-mcp"):
        if versions[name] != sdk_version:
            errors.append(
                f"{name}/pyproject.toml version={versions[name]!r} does not match "
                f"synology-apm-sdk version={sdk_version!r}"
            )

        pin = _sdk_pin(data[name]["project"]["dependencies"])
        if pin is None:
            errors.append(f"{name}/pyproject.toml is missing a synology-apm-sdk== dependency pin")
        elif pin != sdk_version:
            errors.append(
                f"{name}/pyproject.toml pins synology-apm-sdk=={pin}, but "
                f"synology-apm-sdk version={sdk_version!r}"
            )

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"OK: all packages at version {sdk_version!r}; dependency pins consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
