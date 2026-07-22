"""Tests for _security.py: mode_allows, audit_log, run_audited_tool, destructive_tool."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest


class TestModeAllows:
    @pytest.mark.parametrize("required,current,expected", [
        ("readonly",  "readonly",  True),
        ("readonly",  "operator",  True),
        ("readonly",  "admin",     True),
        ("operator",  "readonly",  False),
        ("operator",  "operator",  True),
        ("operator",  "admin",     True),
        ("admin",     "readonly",  False),
        ("admin",     "operator",  False),
        ("admin",     "admin",     True),
    ])
    def test_all_mode_pairs(self, required, current, expected):
        from synology_apm.mcp._security import mode_allows
        assert mode_allows(required, current) is expected


class TestAuditLog:
    def test_writes_when_enabled(self, tmp_path):
        from synology_apm.mcp._security import audit_log

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            audit_log("backup_machine_workload", {"workload_name": "vm-web-01"}, "ok")

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "backup_machine_workload"
        assert entry["params"] == {"workload_name": "vm-web-01"}
        assert entry["outcome"] == "ok"
        assert "ts" in entry

    def test_skips_when_disabled(self, tmp_path):
        from synology_apm.mcp._security import audit_log

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": ""}):
            audit_log("some_tool", {}, "ok")

        assert not log_file.exists()
        assert not any(tmp_path.iterdir())

    def test_appends_multiple_entries(self, tmp_path):
        from synology_apm.mcp._security import audit_log

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            audit_log("tool_a", {}, "ok")
            audit_log("tool_b", {}, "error: ValueError")

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["tool"] == "tool_a"
        assert json.loads(lines[1])["outcome"] == "error: ValueError"

    def test_logs_warning_on_unwritable_path(self, tmp_path, capsys):
        from synology_apm.mcp._security import audit_log

        # Passing a directory path triggers an OSError; audit_log must not raise,
        # but the failure must be surfaced (not silently swallowed).
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(tmp_path)}):
            audit_log("test_tool", {}, "ok")  # should not raise

        captured = capsys.readouterr()
        assert "audit log write failed" in captured.err

    def test_non_json_serializable_param_does_not_raise(self, tmp_path):
        """A non-primitive value in params (e.g. a datetime) must not escape as an
        unhandled TypeError; it is coerced to its str() form instead."""
        from datetime import datetime

        from synology_apm.mcp._security import audit_log

        log_file = tmp_path / "audit.jsonl"
        ts = datetime(2026, 7, 1, 0, 0, 0)
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            audit_log("test_tool", {"since": ts}, "ok")  # should not raise

        entry = json.loads(log_file.read_text().strip())
        assert entry["params"]["since"] == str(ts)


class TestRunAuditedTool:
    @pytest.mark.asyncio
    async def test_success_logs_ok(self, tmp_path):
        from synology_apm.mcp._security import run_audited_tool

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            result = await run_audited_tool(
                AsyncMock(return_value={"ok": True})(),
                action="test_action",
                params={"x": 1},
            )

        assert json.loads(result) == {"ok": True}
        entry = json.loads(log_file.read_text().strip())
        assert entry["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_exception_logs_error_type(self, tmp_path):
        from synology_apm.mcp._security import run_audited_tool
        from synology_apm.sdk import ResourceNotFoundError

        async def _fail():
            raise ResourceNotFoundError("not found", "workload", "wl-001")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            result = await run_audited_tool(_fail(), action="test_action", params={})

        parsed = json.loads(result)
        assert parsed["error"] == "not_found"
        entry = json.loads(log_file.read_text().strip())
        assert "ResourceNotFoundError" in entry["outcome"]

    @pytest.mark.asyncio
    async def test_audit_log_runs_in_thread(self):
        """audit_log() does blocking file I/O; run_audited_tool must not call it
        directly on the event loop, so a slow/network-mounted log path can't stall
        other concurrent tool calls."""
        from synology_apm.mcp import _security
        from synology_apm.mcp._security import run_audited_tool

        with patch.object(_security, "asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock()
            await run_audited_tool(
                AsyncMock(return_value={"ok": True})(),
                action="test_action",
                params={"x": 1},
            )

        mock_asyncio.to_thread.assert_awaited_once_with(
            _security.audit_log, "test_action", {"x": 1}, "ok"
        )


class TestDestructiveTool:
    @pytest.mark.asyncio
    async def test_returns_preview_when_not_confirmed(self):
        from synology_apm.mcp._security import destructive_tool

        result = await destructive_tool(
            confirm=False,
            action="delete_something",
            warning="This is destructive.",
            resolve_coro=AsyncMock(return_value={"id": "x", "name": "foo"})(),
            preview_target_fn=lambda t: {"name": t["name"], "id": t["id"]},
            execute_fn=AsyncMock(),
            params={"id": "x"},
        )

        parsed = json.loads(result)
        assert parsed["preview"] is True
        assert parsed["action"] == "delete_something"
        assert parsed["target"]["name"] == "foo"

    @pytest.mark.asyncio
    async def test_executes_when_confirmed(self):
        from synology_apm.mcp._security import destructive_tool

        execute = AsyncMock(return_value={"deleted": True})

        result = await destructive_tool(
            confirm=True,
            action="delete_something",
            warning="This is destructive.",
            resolve_coro=AsyncMock(return_value={"id": "x"})(),
            preview_target_fn=lambda t: {"id": t["id"]},
            execute_fn=lambda t: execute(t),
            params={"id": "x"},
        )

        parsed = json.loads(result)
        assert parsed == {"deleted": True}
        execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_error_returns_error_json(self):
        from synology_apm.mcp._security import destructive_tool
        from synology_apm.sdk import ResourceNotFoundError

        async def _fail():
            raise ResourceNotFoundError("not found", "workload", "wl-001")

        result = await destructive_tool(
            confirm=True,
            action="delete_something",
            warning="...",
            resolve_coro=_fail(),
            preview_target_fn=lambda t: {},
            execute_fn=AsyncMock(),
            params={},
        )

        parsed = json.loads(result)
        assert parsed["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_resolve_error_with_confirm_is_audit_logged(self, tmp_path):
        """A resolve failure during a confirm=True call is an attempted action
        that failed, and must leave an audit trail like an execute-time failure."""
        from synology_apm.mcp._security import destructive_tool
        from synology_apm.sdk import ResourceNotFoundError

        async def _fail():
            raise ResourceNotFoundError("not found", "workload", "wl-001")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            result = await destructive_tool(
                confirm=True,
                action="delete_something",
                warning="...",
                resolve_coro=_fail(),
                preview_target_fn=lambda t: {},
                execute_fn=AsyncMock(),
                params={"workload_id": "wl-001"},
            )

        parsed = json.loads(result)
        assert parsed["error"] == "not_found"
        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "delete_something"
        assert entry["params"] == {"workload_id": "wl-001"}
        assert "ResourceNotFoundError" in entry["outcome"]

    @pytest.mark.asyncio
    async def test_resolve_error_without_confirm_is_not_audit_logged(self, tmp_path):
        """A resolve failure during a confirm=False (preview-only) call attempted
        nothing, so it must not be logged."""
        from synology_apm.mcp._security import destructive_tool
        from synology_apm.sdk import ResourceNotFoundError

        async def _fail():
            raise ResourceNotFoundError("not found", "workload", "wl-001")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            result = await destructive_tool(
                confirm=False,
                action="delete_something",
                warning="...",
                resolve_coro=_fail(),
                preview_target_fn=lambda t: {},
                execute_fn=AsyncMock(),
                params={"workload_id": "wl-001"},
            )

        parsed = json.loads(result)
        assert parsed["error"] == "not_found"
        assert not log_file.exists()

    @pytest.mark.asyncio
    async def test_preview_does_not_escape_non_ascii(self):
        """Preview and execute paths must render non-ASCII resource names identically
        (both as clean UTF-8), not just the execute path."""
        from synology_apm.mcp._security import destructive_tool

        result = await destructive_tool(
            confirm=False,
            action="delete_something",
            warning="This is destructive.",
            resolve_coro=AsyncMock(return_value={"id": "x", "name": "vm-日本-01"})(),
            preview_target_fn=lambda t: {"name": t["name"], "id": t["id"]},
            execute_fn=AsyncMock(),
            params={"id": "x"},
        )

        assert "\\u" not in result
        parsed = json.loads(result)
        assert parsed["target"]["name"] == "vm-日本-01"

    @pytest.mark.asyncio
    async def test_preview_target_fn_error_returns_error_json(self):
        """A failure while building the preview (not just during resolve) must also
        return the standardized error JSON instead of propagating unhandled."""
        from synology_apm.mcp._security import destructive_tool

        def _bad_preview(_target):
            raise AttributeError("boom")

        result = await destructive_tool(
            confirm=False,
            action="delete_something",
            warning="...",
            resolve_coro=AsyncMock(return_value={"id": "x"})(),
            preview_target_fn=_bad_preview,
            execute_fn=AsyncMock(),
            params={},
        )

        parsed = json.loads(result)
        assert parsed["error"] == "unexpected_error"


class TestConfirmOrPreview:
    def test_shape(self):
        from synology_apm.mcp._security import confirm_or_preview

        result = confirm_or_preview("delete_x", {"name": "foo"}, "Warning message.")
        assert result == {
            "preview": True,
            "action": "delete_x",
            "target": {"name": "foo"},
            "warning": "Warning message.",
        }
