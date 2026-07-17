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

    if args.profile:
        os.environ["APM_PROFILE"] = args.profile

    from synology_apm.mcp._config import load_credentials
    from synology_apm.mcp._server import run

    host, username, password, verify_ssl, mode = load_credentials()
    run(host=host, username=username, password=password, verify_ssl=verify_ssl, debug=args.debug, mode=mode)


if __name__ == "__main__":
    main()
