"""Shared context building blocks for the CLI and SDK smoke-test tools.

Both tools keep their own SmokeContext (one drives the CLI binary via subprocess,
the other awaits SDK coroutines in-process) but share the per-domain bookkeeping
types and the step-anchor slug rule defined here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DomainStats:
    """Per-domain counters; the CLI tool leaves na/checks_* at 0."""

    ran: int = 0
    skipped: int = 0
    na: int = 0
    unexpected: int = 0
    checks_passed: int = 0
    checks_failed: int = 0


@dataclass
class StepResult:
    step: str
    ok: bool
    skipped: bool
    label: str
    has_detail: bool
    note: str = field(default="")


def step_slug(step: str) -> str:
    """Convert a step name to a URL-safe HTML anchor ID."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", step.lower())).strip("-")
