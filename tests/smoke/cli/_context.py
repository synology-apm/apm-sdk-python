"""Shared run state: CLI runner, report files, API-trace sequence, and cross-phase registry."""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from .._context import DomainStats, StepResult, step_slug
from ._cli_runner import CliEnv, CliResult, CliRunner

DOMAINS = ("config", "infra", "machine", "m365", "activity", "plan", "log")
M365_SCOPES = ("exchange", "onedrive", "chat", "group", "sharepoint", "teams")


class SmokeContext:
    """Runs CLI invocations, writes per-domain Markdown reports and ``api_trace.jsonl``.

    ``data`` is a free-form registry that phases use to pass discovered IDs/names
    (workload IDs, version IDs, server IDs, ...) forward to later phases.
    """

    def __init__(
        self,
        runner: CliRunner,
        cli_env: CliEnv,
        report_dir: Path,
        *,
        m365_scopes: Sequence[str] = M365_SCOPES,
    ) -> None:
        self.runner = runner
        self.cli_env = cli_env
        self.report_dir = report_dir
        self.m365_scopes = m365_scopes

        self.data: dict[str, Any] = {}
        self.stats: dict[str, DomainStats] = {d: DomainStats() for d in DOMAINS}
        self.step_results: dict[str, list[StepResult]] = {d: [] for d in DOMAINS}

        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._trace_file: TextIO = (self.report_dir / "api_trace.jsonl").open("w", encoding="utf-8")
        self._domain_files: dict[str, TextIO] = {}
        for domain in DOMAINS:
            domain_file = (self.report_dir / f"{domain}.md").open("w", encoding="utf-8")
            domain_file.write(f"# {domain} — CLI smoke test report\n\n")
            self._domain_files[domain] = domain_file

    def run(
        self,
        domain: str,
        step: str,
        args: list[str],
        *,
        output_format: str | None = None,
        expect_codes: tuple[int, ...] = (0,),
        env_overrides: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout: float | None = None,
        note: str = "",
    ) -> CliResult:
        """Run a CLI command, recording it to the domain's report and ``api_trace.jsonl``."""
        run_kwargs: dict[str, Any] = {
            "output_format": output_format,
            "env_overrides": env_overrides,
            "stdin": stdin,
        }
        if timeout is not None:
            run_kwargs["timeout"] = timeout
        result = self.runner.run(args, **run_kwargs)
        self._record(domain, step, output_format, result, expect_codes, note=note)
        return result

    def run_both(
        self,
        domain: str,
        step: str,
        args: list[str],
        *,
        expect_codes: tuple[int, ...] = (0,),
        env_overrides: dict[str, str] | None = None,
        note: str = "",
    ) -> tuple[CliResult, CliResult]:
        """Run ``args`` once with ``-o table`` and once with ``-o json``."""
        table_result = self.run(
            domain, f"{step}[table]", args, output_format="table",
            expect_codes=expect_codes, env_overrides=env_overrides, note=note,
        )
        json_result = self.run(
            domain, f"{step}[json]", args, output_format="json",
            expect_codes=expect_codes, env_overrides=env_overrides, note=note,
        )
        return table_result, json_result

    def run_python(
        self,
        domain: str,
        step: str,
        script_args: list[str],
        *,
        expect_codes: tuple[int, ...] = (0,),
        env_overrides: dict[str, str] | None = None,
        note: str = "",
    ) -> CliResult:
        """Run a Python script as a subprocess, recording it to the domain's report."""
        result = self.runner.run_python(script_args, env_overrides=env_overrides)
        self._record(domain, step, None, result, expect_codes, note=note)
        return result

    def skip(self, domain: str, step: str, reason: str) -> None:
        """Record a conditional skip (e.g. no data of the required kind exists)."""
        self.stats[domain].skipped += 1
        self.step_results[domain].append(
            StepResult(step, ok=True, skipped=True, label=f"SKIPPED: {reason}", has_detail=False)
        )

    def _record(
        self,
        domain: str,
        step: str,
        output_format: str | None,
        result: CliResult,
        expect_codes: tuple[int, ...],
        *,
        note: str = "",
    ) -> None:
        stats = self.stats[domain]
        stats.ran += 1
        ok = result.exit_code in expect_codes
        if not ok:
            stats.unexpected += 1

        seq_first: int | None = None
        for call in result.api_calls:
            self._seq += 1
            if seq_first is None:
                seq_first = self._seq
            entry = {
                "step": step,
                "command": result.args,
                "output_format": output_format,
                "seq": self._seq,
                **call,
            }
            self._trace_file.write(json.dumps(entry, ensure_ascii=False) + "\n")

        write_to_domain = bool(result.api_calls) or not ok
        if write_to_domain:
            slug = step_slug(step)
            f = self._domain_files[domain]
            f.write(f'<a id="{slug}"></a>\n')
            f.write(f"### `{step}`\n\n")

            for i, call in enumerate(result.api_calls, 1):
                method = call.get("method", "")
                url = call.get("url", "")
                path = ("/" + url.split("/", 3)[3]) if url.count("/") >= 3 else url
                status = call.get("status")
                status_str = f" → {status}" if status is not None else ""
                f.write(f"**{i}.** `{method} {path}`{status_str}\n\n")

                params = call.get("params")
                if params:
                    f.write("```json\n")
                    f.write(json.dumps(params, indent=2, ensure_ascii=False))
                    f.write("\n```\n\n")

                body = call.get("body")
                if body:
                    f.write("```json\n")
                    f.write(json.dumps(body, indent=2, ensure_ascii=False))
                    f.write("\n```\n\n")

                response = call.get("response")
                if response is not None:
                    f.write("```json\n")
                    if isinstance(response, str):
                        f.write(response)
                    else:
                        f.write(json.dumps(response, indent=2, ensure_ascii=False))
                    if call.get("truncated"):
                        f.write("\n// (truncated)")
                    f.write("\n```\n\n")

            if not ok and result.stdout:
                out = result.stdout if result.stdout.endswith("\n") else result.stdout + "\n"
                f.write(f"stdout:\n```\n{out}```\n\n")

        label = "PASSED" if ok else f"FAILED: exit {result.exit_code} (expected {expect_codes})"
        self.step_results[domain].append(
            StepResult(step, ok=ok, skipped=False, label=label, has_detail=write_to_domain, note=note)
        )

    def close(self) -> None:
        for domain_file in self._domain_files.values():
            domain_file.close()
        self._trace_file.close()


def parse_json(result: CliResult) -> Any | None:
    """Parse a CLI invocation's stdout as JSON, returning None if it isn't valid JSON."""
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def pick_backed_up_workload(workloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the get/version target: prefer a backed-up workload with an unambiguous name.

    A backed-up workload keeps the version steps from skipping; a unique name keeps the
    search-mode steps deterministic when the listing contains duplicate names.
    """
    name_counts = Counter(w.get("name") for w in workloads)
    backed_up = [w for w in workloads if w.get("last_backup_at")]
    unique_backed_up = [w for w in backed_up if name_counts[w.get("name")] == 1]
    if unique_backed_up:
        return unique_backed_up[0]
    if backed_up:
        return backed_up[0]
    return workloads[0]
