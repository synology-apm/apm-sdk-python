"""Parse `synology-apm --debug` stderr output into structured API call records.

See ``_http.py``'s ``_debug_print_request``/``_debug_print_response`` for the exact
(ANSI-colored) format this module parses.
"""
from __future__ import annotations

import json
import re
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_REQUEST_RE = re.compile(r"^→ (GET|POST|PUT|DELETE|PATCH) (\S+)$", re.MULTILINE)
_STATUS_RE = re.compile(r"^  ← (\d+)$", re.MULTILINE)
_TRUNCATED_RE = re.compile(r"\n  \.\.\. \(truncated, \d+ chars total\)")


def parse_debug_trace(stderr: str) -> list[dict[str, Any]]:
    """Extract one record per ``--debug``-logged API call from raw CLI stderr.

    Each record has keys ``method``, ``url``, ``headers``, ``params``, ``body``,
    ``status``, ``response``, and (only when the response was truncated by
    ``_DEBUG_MAX_BODY``) ``truncated: True``.
    """
    text = _ANSI_RE.sub("", stderr)
    matches = list(_REQUEST_RE.finditer(text))

    records: list[dict[str, Any]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[match.end():end]

        record: dict[str, Any] = {
            "method": match.group(1),
            "url": match.group(2),
            "headers": _extract_json_field(block, "headers"),
            "params": _extract_json_field(block, "params"),
            "body": _extract_json_field(block, "body"),
            "status": None,
            "response": None,
        }

        status_match = _STATUS_RE.search(block)
        if status_match:
            record["status"] = int(status_match.group(1))

        response, truncated = _extract_response(block)
        record["response"] = response
        if truncated:
            record["truncated"] = True

        records.append(record)

    return records


def _extract_json_field(block: str, field_name: str) -> Any | None:
    marker = f"\n  {field_name}: "
    idx = block.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    try:
        value, _ = json.JSONDecoder().raw_decode(block, start)
    except json.JSONDecodeError:
        return None
    return value


def _extract_response(block: str) -> tuple[Any | None, bool]:
    marker = "\n  response: "
    idx = block.find(marker)
    if idx == -1:
        return None, False
    start = idx + len(marker)
    try:
        value, _ = json.JSONDecoder().raw_decode(block, start)
        return value, False
    except json.JSONDecodeError:
        truncated_match = _TRUNCATED_RE.search(block, start)
        if truncated_match:
            return block[start:truncated_match.start()], True
        return None, False
