"""MCP coverage check: SDK methods <-> mcp_coverage.toml <-> registered tools.

Pass 1 — manifest internal consistency: every sdk_path listed in the manifest
         (across [[mapping]] and [[not_exposed]]) must appear only once.

Pass 2 — manifest -> SDK: every sdk_path in the manifest must resolve to a real
         public async method on APMClient (catches typos and renamed/removed
         SDK methods).

Pass 3 — SDK -> manifest: every public async method reachable from APMClient
         (walking collection properties) must appear in at least one manifest
         entry (catches new SDK methods added without a coverage decision).

Pass 4 — manifest -> registered tools: every [[mapping]] mcp_tool must appear
         in the tool names registered by create_server(mode='admin').

Pass 5 — registered tools -> manifest: every registered tool must have at
         least one [[mapping]] entry.

Pass 6 — manifest mode -> actual registration mode: every [[mapping]] `mode`
         must match the least-permissive mode at which create_server() actually
         registers that tool (catches a tool's inline mode_allows() gate drifting
         out of sync with the manifest's declared mode).

Exit code 0 = clean; non-zero = errors printed to stderr.
"""
from __future__ import annotations

import inspect
import sys
import tomllib
from pathlib import Path
from typing import Any

MANIFEST = Path(__file__).parent / "mcp_coverage.toml"

# Session lifecycle methods, not domain operations — excluded from SDK surface walk.
_TOP_LEVEL_EXCLUDE = {"connect", "disconnect"}


def _load_manifest() -> dict[str, Any]:
    with open(MANIFEST, "rb") as f:
        return tomllib.load(f)


# Ascending permissiveness, matching synology_apm.mcp._security._LEVELS.
_MODES = ("readonly", "operator", "manager", "admin")


def _registered_tool_names_by_mode() -> dict[str, set[str]]:
    """Create a server at each mode and return {mode: registered tool names}.

    Modes are cumulative (each includes every less-permissive mode's tools), so the
    first mode (in ascending order) at which a tool appears is that tool's actual
    minimum required mode.
    """
    import asyncio

    from synology_apm.mcp._server import create_server

    async def _collect(mode: str) -> set[str]:
        server = create_server(mode=mode)
        return {t.name for t in await server.list_tools()}

    return {mode: asyncio.run(_collect(mode)) for mode in _MODES}


def _minimum_registered_mode(tool: str, by_mode: dict[str, set[str]]) -> str | None:
    """Return the least-permissive mode at which `tool` is registered, or None."""
    for mode in _MODES:
        if tool in by_mode[mode]:
            return mode
    return None


def _resolve_sdk_path(client: Any, path: str) -> bool:
    """Return True if the dotted path resolves to a real async method on client."""
    obj: Any = client
    for segment in path.split("."):
        try:
            obj = getattr(obj, segment)
        except Exception:
            return False
    return inspect.iscoroutinefunction(obj)


def _walk_sdk_surface(obj: Any, prefix: str = "") -> set[str]:
    """Return the dotted paths of every public async method reachable from obj.

    Recurses into properties/attributes that are themselves SDK collection
    instances (identified by their module living under synology_apm.sdk.collections);
    a try/except around each getattr safely skips properties that raise before
    connect() (e.g. APMClient.my_server) without needing to special-case them.
    """
    paths: set[str] = set()
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            member = getattr(obj, name)
        except Exception:
            continue
        path = f"{prefix}.{name}" if prefix else name
        if inspect.iscoroutinefunction(member):
            paths.add(path)
        elif type(member).__module__.startswith("synology_apm.sdk.collections"):
            paths |= _walk_sdk_surface(member, path)
    return paths


def main() -> int:
    data = _load_manifest()
    mappings = data.get("mapping", [])
    not_exposed = data.get("not_exposed", [])

    errors: list[str] = []

    # ── Pass 1: manifest internal consistency ─────────────────────────────────
    mapped_tools = {m["mcp_tool"] for m in mappings}
    all_sdk_paths = [m["sdk_path"] for m in mappings] + [n["sdk_path"] for n in not_exposed]
    seen: set[str] = set()
    for path in all_sdk_paths:
        if path in seen:
            errors.append(f"duplicate sdk_path in manifest: {path!r}")
        seen.add(path)

    # ── Pass 2: manifest → SDK ─────────────────────────────────────────────────
    from synology_apm.sdk import APMClient

    client = APMClient("check-mcp-coverage", "x", "x")

    unresolved = [path for path in all_sdk_paths if not _resolve_sdk_path(client, path)]
    if unresolved:
        errors.append("manifest sdk_path entries that do not resolve to a real SDK method:")
        errors.extend(f"  {path}" for path in unresolved)

    # ── Pass 3: SDK → manifest ─────────────────────────────────────────────────
    sdk_surface = _walk_sdk_surface(client) - _TOP_LEVEL_EXCLUDE
    unmapped_sdk = sorted(sdk_surface - set(all_sdk_paths))
    if unmapped_sdk:
        errors.append("SDK methods with no [[mapping]] or [[not_exposed]] manifest entry:")
        errors.extend(f"  {path}" for path in unmapped_sdk)

    # ── Pass 4/5/6: manifest ↔ registered tools ─────────────────────────────────
    try:
        by_mode = _registered_tool_names_by_mode()
    except Exception as exc:
        errors.append(f"failed to instantiate MCP server: {exc}")
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    registered = by_mode["admin"]

    # Every [[mapping]] mcp_tool must be registered
    stale: list[str] = []
    for m in mappings:
        if m["mcp_tool"] not in registered:
            stale.append(f"  manifest claims tool {m['mcp_tool']!r} (sdk: {m['sdk_path']!r}) but it is not registered")
    if stale:
        errors.append("stale [[mapping]] entries — tool not registered in admin mode:")
        errors.extend(stale)

    # Every registered tool must appear in at least one [[mapping]] entry
    unmapped = sorted(registered - mapped_tools)
    if unmapped:
        errors.append("registered tools missing from mcp_coverage.toml [[mapping]]:")
        for t in unmapped:
            errors.append(f"  {t}")

    # Every [[mapping]] `mode` must match the tool's actual minimum registration mode
    mode_mismatches: list[str] = []
    for m in mappings:
        tool, declared_mode = m["mcp_tool"], m["mode"]
        actual_mode = _minimum_registered_mode(tool, by_mode)
        if actual_mode is not None and actual_mode != declared_mode:
            mode_mismatches.append(
                f"  {tool}: manifest declares mode={declared_mode!r} but is actually "
                f"gated at mode={actual_mode!r}"
            )
    if mode_mismatches:
        errors.append("manifest mode does not match actual tool registration mode:")
        errors.extend(mode_mismatches)

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(mappings)} mapped, {len(not_exposed)} excluded, "
        f"{len(registered)} registered tools all accounted for."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
