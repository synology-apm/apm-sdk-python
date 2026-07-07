"""Subprocess wrapper that drives the real ``synology-apm`` CLI binary against a live APM.

Credentials are read from ``.env`` (same convention as ``tests/integration/conftest.py``)
and passed to the subprocess via environment variables, never via ``--password`` on argv.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ._debug_trace import parse_debug_trace

_DEFAULT_TIMEOUT = 120.0


@dataclass
class CliResult:
    """Result of one ``synology-apm`` invocation."""

    args: list[str]
    exit_code: int
    stdout: str
    stderr: str
    api_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CliEnv:
    """Connection details for the live APM under test, loaded from ``.env``."""

    cred_env: dict[str, str]
    host: str
    username: str


def load_cli_env() -> CliEnv:
    """Load APM connection details from ``.env`` for use as subprocess environment variables."""
    load_dotenv()

    host = os.environ.get("APM_HOST", "").strip()
    username = os.environ.get("APM_USERNAME", "").strip()
    password = os.environ.get("APM_PASSWORD", "").strip()
    no_verify_ssl = os.environ.get("APM_NO_VERIFY_SSL", "").strip()

    if "://" in host:
        host = host.split("://", 1)[1]

    if not host or not username or not password:
        raise RuntimeError("APM_HOST / APM_USERNAME / APM_PASSWORD must be set in .env")

    cred_env = {"APM_HOST": host, "APM_USERNAME": username, "APM_PASSWORD": password}
    if no_verify_ssl:
        cred_env["APM_NO_VERIFY_SSL"] = no_verify_ssl

    return CliEnv(cred_env=cred_env, host=host, username=username)


def _default_repo_root() -> Path:
    # _cli_runner.py -> cli -> smoke -> tests -> repo root
    return Path(__file__).resolve().parents[3]


class CliRunner:
    """Runs ``synology-apm`` as a subprocess and captures its output + ``--debug`` trace."""

    def __init__(self, cli_env: CliEnv, *, repo_root: Path | None = None) -> None:
        self._cli_env = cli_env
        self._repo_root = repo_root or _default_repo_root()

    def run(
        self,
        args: Sequence[str],
        *,
        output_format: str | None = None,
        env_overrides: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> CliResult:
        """Run ``synology-apm --no-input --debug <args> [-o <output_format>]``.

        Credentials are injected via environment variables; ``env_overrides`` is applied
        on top (e.g. to sandbox ``HOME`` for ``config`` commands).
        """
        full_args = ["uv", "run", "synology-apm", "--no-input", "--debug", *args]
        if output_format is not None:
            full_args += ["-o", output_format]

        env = dict(os.environ)
        env.update(self._cli_env.cred_env)
        if env_overrides:
            env.update(env_overrides)

        try:
            proc = subprocess.run(
                full_args,
                cwd=self._repo_root,
                env=env,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return CliResult(
                args=full_args,
                exit_code=-1,
                stdout=stdout,
                stderr=stderr + "\n[smoke.cli] TIMED OUT",
                api_calls=parse_debug_trace(stderr),
            )

        return CliResult(
            args=full_args,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            api_calls=parse_debug_trace(proc.stderr),
        )

    def run_python(
        self,
        script_args: Sequence[str],
        *,
        env_overrides: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> CliResult:
        """Run ``uv run python <script_args>`` with APM credentials in the environment."""
        full_args = ["uv", "run", "python", *script_args]
        env = dict(os.environ)
        env.update(self._cli_env.cred_env)
        if env_overrides:
            env.update(env_overrides)
        try:
            proc = subprocess.run(
                full_args,
                cwd=self._repo_root,
                env=env,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return CliResult(
                args=full_args,
                exit_code=-1,
                stdout=stdout,
                stderr=stderr + "\n[smoke.cli] TIMED OUT",
                api_calls=[],
            )
        return CliResult(
            args=full_args,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            api_calls=[],
        )
