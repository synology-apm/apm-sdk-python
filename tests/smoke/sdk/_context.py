"""SmokeContext: SDK-direct live smoke test context. Mirrors tests/smoke/cli/_context.py's
DomainStats/DOMAINS/ctx.data/ctx.skip/report-file shape, but ctx.call wraps SDK coroutines
(tagging _trace.current_step) and ctx.check makes in-process assertions on returned models.
"""
from __future__ import annotations

import io
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, TypeVar

from synology_apm.sdk import APMClient
from synology_apm.sdk.exceptions import APMError, ResourceNotFoundError

from .._context import DomainStats, StepResult, step_slug
from . import _trace
from ._serialize import to_jsonable

T = TypeVar("T")

DOMAINS = ("infra", "machine", "m365", "m365_rule", "activity", "plan", "log")
M365_SCOPES = ("exchange", "onedrive", "chat", "sharepoint", "teams", "group")

_MAX_LIST_ITEMS = 5


def _truncate_result(result: Any) -> tuple[Any, str | None]:
    """Return (display_result, truncation_note) — truncates long list results."""
    if isinstance(result, tuple) and len(result) == 2:
        items_raw, total = result
        if isinstance(items_raw, (list, tuple)):
            items = list(items_raw)
            if len(items) > _MAX_LIST_ITEMS:
                note = f"…({len(items) - _MAX_LIST_ITEMS} more items, {total} total)"
                return (items[:_MAX_LIST_ITEMS], total), note
    elif isinstance(result, list) and len(result) > _MAX_LIST_ITEMS:
        note = f"…({len(result) - _MAX_LIST_ITEMS} more items)"
        return result[:_MAX_LIST_ITEMS], note
    return result, None


@dataclass
class SmokeContext:
    """Shared run state for a synology_apm.sdk live smoke test run.

    ``data`` is a free-form registry that phases use to pass discovered objects (workloads,
    versions, plans, servers, ...) forward to later phases.
    """

    apm: APMClient
    report_dir: Path
    m365_scopes: tuple[str, ...] = M365_SCOPES

    data: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, DomainStats] = field(default_factory=lambda: {d: DomainStats() for d in DOMAINS})
    step_results: dict[str, list[StepResult]] = field(
        default_factory=lambda: {d: [] for d in DOMAINS}
    )

    _files: dict[str, io.TextIOWrapper] = field(default_factory=dict, repr=False)
    _trace_file: IO[str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        for domain in DOMAINS:
            f = (self.report_dir / f"{domain}.md").open("w", encoding="utf-8")
            f.write(f"# {domain} — SDK smoke test report\n\n")
            self._files[domain] = f
        self._trace_file = _trace.install_trace(self.apm._session, self.report_dir / "api_trace.jsonl")
        self._emitted: set[tuple[str, str]] = set()

    async def call(
        self,
        domain: str,
        step: str,
        coro: Callable[[], Awaitable[T]],
        *,
        expect_error: type[APMError] | None = None,
        note: str = "",
    ) -> T | None:
        """Run one SDK coroutine, tagged as `step` in api_trace.jsonl, recorded into <domain>.md.

        Returns the awaited result, or None if it raised expect_error (recorded as expected)
        or any other APMError (recorded as `unexpected`, logged, not re-raised, so the phase
        can continue).
        """
        stats = self.stats[domain]
        self._mark_emitted(domain, step)
        stats.ran += 1
        token = _trace.current_step.set(step)
        try:
            result: T | None
            error_text: str | None = None
            error_body: Any = None
            try:
                result = await coro()
            except APMError as exc:
                result = None
                error_body = exc.response_body
                if expect_error is not None and isinstance(exc, expect_error):
                    error_text = f"{type(exc).__name__}: {exc.message} (expected)"
                else:
                    stats.unexpected += 1
                    error_text = f"{type(exc).__name__}: {exc.message} (FAILED)"
        finally:
            _trace.current_step.reset(token)

        self._write_call(domain, step, result, error_text, note=note, error_body=error_body)
        return result

    async def call_expect_value_error(
        self,
        domain: str,
        step: str,
        coro: Callable[[], Awaitable[Any]],
        *,
        note: str = "",
    ) -> ValueError | None:
        """Like call_expect_error(), but for ValueError raised by SDK input validation."""
        stats = self.stats[domain]
        self._mark_emitted(domain, step)
        stats.ran += 1
        token = _trace.current_step.set(step)
        try:
            result_exc: ValueError | None
            error_text: str
            try:
                await coro()
                result_exc = None
                error_text = "(no ValueError raised)"
                stats.checks_failed += 1
            except ValueError as exc:
                result_exc = exc
                error_text = f"ValueError: {exc} (expected)"
                stats.checks_passed += 1
        finally:
            _trace.current_step.reset(token)
        self._write_call(domain, step, None, error_text, note=note)
        return result_exc

    async def call_expect_error(
        self,
        domain: str,
        step: str,
        coro: Callable[[], Awaitable[T]],
        expect_error: type[APMError],
        *,
        note: str = "",
    ) -> APMError | None:
        """Like call(), but for steps that are EXPECTED to raise — returns the caught
        exception (so the phase can ctx.check its .resource_type/.error_code/etc.), or None
        if no exception was raised (itself a fact the phase should ctx.check).
        """
        stats = self.stats[domain]
        self._mark_emitted(domain, step)
        stats.ran += 1
        token = _trace.current_step.set(step)
        try:
            error_body: Any = None
            try:
                await coro()
                result_exc: APMError | None = None
                error_text = "(no exception raised)"
            except expect_error as exc:
                result_exc = exc
                error_body = exc.response_body
                error_text = f"{type(exc).__name__}: {exc.message} (expected)"
            except APMError as exc:
                stats.unexpected += 1
                result_exc = exc
                error_body = exc.response_body
                error_text = f"{type(exc).__name__}: {exc.message} (FAILED)"
        finally:
            _trace.current_step.reset(token)

        self._write_call(domain, step, None, error_text, note=note, error_body=error_body)
        return result_exc

    def check(self, domain: str, step: str, condition: bool, *, note: str = "") -> bool:
        """Record a PASSED/FAILED assertion (no API call) into ctx.step_results and ctx.stats."""
        stats = self.stats[domain]
        self._mark_emitted(domain, step)
        if condition:
            stats.checks_passed += 1
            label = "PASSED"
        else:
            stats.checks_failed += 1
            label = "FAILED"
        self.step_results[domain].append(
            StepResult(step, ok=condition, skipped=False, label=label, has_detail=False, note=note)
        )
        return condition

    def check_exc_attr(
        self,
        domain: str,
        step: str,
        exc: APMError | None,
        attr: str,
        expected: object,
        *,
        note: str = "",
    ) -> bool:
        """Assert that exc is not None and exc.<attr> == expected. Records a named check step."""
        return self.check(
            domain, step,
            exc is not None and getattr(exc, attr, None) == expected,
            note=note,
        )

    async def guard_error(
        self,
        domain: str,
        call_step: str,
        check_base: str,
        condition: bool,
        coro: Callable[[], Awaitable[Any]],
        error_type: type[APMError],
        resource_type: str,
        resource_id: object,
        *,
        skip_reason: str,
    ) -> APMError | None:
        """Run an expected error call + 2 attribute checks, or skip all three steps.

        check_base must end before the closing bracket, e.g.
        ``"machine.workloads.check[change_plan_active_ret"`` produces check steps
        ``"..._resource_type]"`` and ``"..._resource_id]"``.
        """
        check_type = f"{check_base}_resource_type]"
        check_id = f"{check_base}_resource_id]"
        if condition:
            exc = await self.call_expect_error(domain, call_step, coro, error_type)
            self.check_exc_attr(domain, check_type, exc, "resource_type", resource_type)
            self.check_exc_attr(domain, check_id, exc, "resource_id", resource_id)
            return exc
        self.skip_remaining(domain, [call_step, check_type, check_id], reason=skip_reason)
        return None

    async def call_expect_not_found(
        self,
        domain: str,
        step_base: str,
        method: str,
        coro: Callable[[], Awaitable[Any]],
        resource_type: str,
        resource_id: object,
        *,
        note: str = "",
    ) -> APMError | None:
        """call_expect_error(ResourceNotFoundError) + 2 check_exc_attr with auto-derived step names."""
        method_short = method.rsplit(".", 1)[-1]
        exc = await self.call_expect_error(
            domain, f"{step_base}.{method}[not_found]", coro, ResourceNotFoundError, note=note)
        self.check_exc_attr(domain, f"{step_base}.check[{method_short}_nf_resource_type]",
            exc, "resource_type", resource_type)
        self.check_exc_attr(domain, f"{step_base}.check[{method_short}_nf_resource_id]",
            exc, "resource_id", resource_id)
        return exc

    def _mark_emitted(self, domain: str, step: str) -> None:
        self._emitted.add((domain, step))

    def skip_remaining(self, domain: str, steps: Iterable[str], *, reason: str) -> None:
        """Skip any step in steps that has not been emitted yet in this domain."""
        for step in steps:
            if (domain, step) not in self._emitted:
                self.skip(domain, step, reason)

    def skip(self, domain: str, step: str, reason: str) -> None:
        """Record a conditional skip (e.g. no data of the required kind exists)."""
        self._mark_emitted(domain, step)
        self.stats[domain].skipped += 1
        self.step_results[domain].append(
            StepResult(step, ok=True, skipped=True, label=f"SKIPPED: {reason}", has_detail=False)
        )

    def na(self, domain: str, step: str, reason: str) -> None:
        """Record a step as not applicable for this configuration (not a data gap)."""
        self._mark_emitted(domain, step)
        self.stats[domain].na += 1
        self.step_results[domain].append(
            StepResult(step, ok=True, skipped=True, label=f"N/A: {reason}", has_detail=False)
        )

    def _write_call(self, domain: str, step: str, result: Any, error_text: str | None, *, note: str = "", error_body: Any = None) -> None:
        slug = step_slug(step)
        f = self._files[domain]
        f.write(f'<a id="{slug}"></a>\n')
        f.write(f"### `{step}`\n\n")
        if error_text is not None:
            f.write(f"- result: {error_text}\n")
            if error_body is not None:
                f.write("```json\n")
                f.write(json.dumps(error_body, indent=2, default=str))
                f.write("\n```\n")
        else:
            display_result, trunc_note = _truncate_result(result)
            f.write("```json\n")
            f.write(json.dumps(to_jsonable(display_result), indent=2, default=str))
            f.write("\n```\n")
            if trunc_note:
                f.write(f"\n{trunc_note}\n")
        f.write("\n")
        f.flush()

        if error_text is None:
            ok, label = True, "PASSED"
        elif "(expected)" in error_text:
            clean = error_text.replace(" (expected)", "")
            ok, label = True, f"PASSED ({clean})"
        elif error_text in ("(no exception raised)", "(no ValueError raised)"):
            ok, label = False, "FAILED: expected error not raised"
        else:
            clean = error_text.replace(" (FAILED)", "")
            ok, label = False, f"FAILED: {clean}"
        self.step_results[domain].append(
            StepResult(step, ok=ok, skipped=False, label=label, has_detail=True, note=note)
        )

    def close(self) -> None:
        for f in self._files.values():
            f.close()
        if self._trace_file is not None:
            self._trace_file.close()
