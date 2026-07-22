"""Entry point for the synology-apm-mcp MCP server."""
from __future__ import annotations

import argparse
import os


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="MCP server for Synology ActiveProtect Manager. Reads credentials from env vars or ~/.config/synology-apm/config.toml."
    )
    parser.add_argument("--profile", metavar="NAME", default=None, help="Config profile to use (overrides APM_PROFILE env var)")
    parser.add_argument("--debug", action="store_true", help="Enable SDK debug logging")
    args = parser.parse_args()

    from synology_apm.mcp._config import resolve_startup_state
    from synology_apm.mcp._server import run

    resolved, mode, config_error = resolve_startup_state(args.profile)

    try:
        run(resolved, debug=args.debug, mode=mode, config_error=config_error)
    except KeyboardInterrupt:
        # os._exit(), not sys.exit(): FastMCP's stdio transport keeps a background
        # thread blocked reading stdin (there's no client to send EOF), so normal
        # interpreter shutdown would hang joining it in threading._shutdown() -- and
        # a second Ctrl+C landing during that hang corrupts the shutdown sequence
        # (see the "Fatal Python error: _enter_buffered_busy" crash this replaces).
        # os._exit() terminates the process immediately, skipping that entirely.
        os._exit(130)


if __name__ == "__main__":
    main()
