"""Regression guard for the `_seg()` path-segment-escaping convention.

See the SDK README's "Adding a New SDK Method or Field" recipe: any `{variable}`
interpolated into a request path must be wrapped in `_seg()` (collections/_shared.py) so
the value cannot inject an extra path segment into the request URL. Source-scanning is the
only way to catch a future collection method that reintroduces a bare f"...{some_id}"
interpolation, since nothing else in the request/response contract tests would fail.
"""
from __future__ import annotations

import re
from pathlib import Path

import synology_apm.sdk.collections as _collections_pkg

_COLLECTIONS_DIR = Path(_collections_pkg.__file__).parent

# An f-string literal building a request path ("/api/...") that interpolates a bare
# variable/attribute-access expression not wrapped in _seg(...).
_UNESCAPED_INTERPOLATION = re.compile(
    r"""f['"][^'"]*/api[^'"]*\{[a-zA-Z_][a-zA-Z0-9_.]*\}"""
)


def _find_violations() -> list[str]:
    violations = []
    for path in sorted(_COLLECTIONS_DIR.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if "_seg(" in line:
                continue
            if _UNESCAPED_INTERPOLATION.search(line):
                violations.append(f"{path.relative_to(_COLLECTIONS_DIR)}:{lineno}: {line.strip()}")
    return violations


def test_no_unescaped_id_interpolation_in_api_paths() -> None:
    """Every f-string that builds a request path and interpolates a variable must route it
    through _seg(); a bare f"...{some_id}" silently reintroduces path-segment injection."""
    violations = _find_violations()
    assert violations == [], "Path segment(s) not wrapped in _seg():\n" + "\n".join(violations)


def test_convention_check_actually_detects_a_violation(tmp_path: Path) -> None:
    """Guard the guard: confirm the regex/scan can actually fail, not just pass vacuously."""
    bad_file = tmp_path / "_fake_collection.py"
    bad_file.write_text('raw = await session.get(f"/api/v1/thing/{thing_id}")\n')

    violations = [
        f"{path.name}:{lineno}: {line.strip()}"
        for path in [bad_file]
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if "_seg(" not in line and _UNESCAPED_INTERPOLATION.search(line)
    ]
    assert violations != []
