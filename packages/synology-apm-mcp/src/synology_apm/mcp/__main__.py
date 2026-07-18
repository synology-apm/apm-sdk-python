"""Entry point for the synology-apm-mcp MCP server."""
from __future__ import annotations

import argparse
import os
import sys


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="MCP server for Synology ActiveProtect Manager. Reads credentials from env vars or ~/.config/synology-apm/config.toml."
    )
    parser.add_argument("--profile", metavar="NAME", default=None, help="Config profile to use (overrides APM_PROFILE env var)")
    parser.add_argument("--debug", action="store_true", help="Enable SDK debug logging")
    args = parser.parse_args()

    if args.profile:
        os.environ["APM_PROFILE"] = args.profile

    from synology_apm.mcp._config import load_credentials, resolve_mode
    from synology_apm.mcp._server import run
    from synology_apm.sdk import AuthenticationError

    mode = resolve_mode()
    try:
        host, username, password, verify_ssl = load_credentials()
        config_error = None
    except AuthenticationError as exc:
        host, username, password, verify_ssl = "", "", "", True
        config_error = exc
        print(
            "Continuing in degraded mode: the server will start and wait for an MCP "
            "client, but every tool call will return the error above until reconfigured.",
            file=sys.stderr,
        )

    try:
        run(
            host=host,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            debug=args.debug,
            mode=mode,
            config_error=config_error,
        )
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
