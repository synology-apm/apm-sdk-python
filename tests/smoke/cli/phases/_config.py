"""config phase — ``config set``/``show``/``clear``, run against a sandboxed ``HOME``.

The real ``~/.config/synology-apm/config.toml`` is never touched: every invocation
overrides ``HOME`` to a throwaway temporary directory.
"""
from __future__ import annotations

import tempfile

from .._context import SmokeContext

DOMAIN = "config"


def run(ctx: SmokeContext) -> None:
    with tempfile.TemporaryDirectory(prefix="synology-apm-cli-smoke-home-") as tmp_home:
        env = {"HOME": tmp_home}

        ctx.run(
            DOMAIN, "config.show[empty]", ["config", "show"],
            env_overrides=env,
            note="Sandboxed HOME directory with no config file yet; all fields should show (not set).",
        )

        ctx.run(
            DOMAIN, "config.set",
            ["config", "set", "--host", ctx.cli_env.host, "--username", ctx.cli_env.username],
            env_overrides=env,
            note="With --no-input: password left blank (not saved); SSL kept at default.",
        )

        ctx.run(
            DOMAIN, "config.show[after-set]", ["config", "show"],
            env_overrides=env,
            note="Should now show the configured host/username; Password: (not saved); SSL: verify.",
        )

        ctx.run(DOMAIN, "config.clear", ["config", "clear", "--yes"], env_overrides=env)

        ctx.run(
            DOMAIN, "config.show[after-clear]", ["config", "show"],
            env_overrides=env,
            note="Should be back to (not set) for every field after config clear.",
        )
