"""Starting-point template for a new APM SDK example script.

This is not a runnable example — it is scaffolding to copy when adding a new script under
``examples/``. Copy this file, rename it, fill in ``run()``'s body, and read
``examples/CLAUDE.md``'s "Script Skeleton" section for the non-obvious rules this file's
shape alone doesn't communicate (connection-settings resolution priority, the ``uv run``
invocation, and why ``add_profile_arg()`` belongs in every new script).

The leading underscore marks this file as internal, not a public example: it is excluded
from ``examples/README.md``'s listing and from any doc-example generation, the same way
``examples/_common.py`` is.
"""
from __future__ import annotations

import argparse
import sys

from _common import add_output_arg, add_profile_arg, make_client, run_main


async def run(output_format: str, profile: str | None = None) -> int | None:
    print("Collecting data...", file=sys.stderr)
    async with make_client(profile=profile) as apm:
        servers, total = await apm.backup_servers.list()
    ...
    return 0  # None also means success; run_main() maps APMError to exit code 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_output_arg(parser)
    add_profile_arg(parser)
    args = parser.parse_args()
    run_main(run(args.output, profile=args.profile))


if __name__ == "__main__":
    main()
