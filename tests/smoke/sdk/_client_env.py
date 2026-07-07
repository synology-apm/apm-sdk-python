"""Load APM connection details from .env for direct SDK use (mirrors _cli_runner.load_cli_env)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class SdkEnv:
    host: str
    username: str
    password: str
    verify_ssl: bool


def load_sdk_env() -> SdkEnv:
    """Load APM_HOST / APM_USERNAME / APM_PASSWORD / APM_NO_VERIFY_SSL from .env."""
    load_dotenv()

    host = os.environ.get("APM_HOST", "").strip()
    username = os.environ.get("APM_USERNAME", "").strip()
    password = os.environ.get("APM_PASSWORD", "").strip()
    no_verify_ssl = os.environ.get("APM_NO_VERIFY_SSL", "").strip()

    if "://" in host:
        host = host.split("://", 1)[1]

    if not host or not username or not password:
        raise RuntimeError("APM_HOST / APM_USERNAME / APM_PASSWORD must be set in .env")

    return SdkEnv(host=host, username=username, password=password, verify_ssl=not no_verify_ssl)
