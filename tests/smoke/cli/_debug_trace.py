"""Parse `synology-apm-cli --debug` stderr output into structured API call records.

See ``_http.py``'s ``_debug_print_request``/``_debug_print_response`` for the exact
(ANSI-colored) format this module parses. Request and response blocks carry a shared
``[#N]`` sequence id, so records are paired by id — concurrent requests interleave
their blocks in the stream and cannot be paired positionally.
"""
from __future__ import annotations

import json
import re
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_REQUEST_RE = re.compile(r"^→ \[#(\d+)\] (GET|POST|PUT|DELETE|PATCH) (\S+)$", re.MULTILINE)
_STATUS_RE = re.compile(
    r"^  ← \[#(\d+)\] (\d+) (?:GET|POST|PUT|DELETE|PATCH) \S+ \((\d+\.\d+)s\)$", re.MULTILINE
)
_TRUNCATED_RE = re.compile(r"\n  \.\.\. \(truncated, \d+ chars total\)")


def parse_debug_trace(stderr: str) -> list[dict[str, Any]]:
    """Extract one record per ``--debug``-logged API call from raw CLI stderr.

    Each record has keys ``method``, ``url``, ``headers``, ``params``, ``body``,
    ``status``, ``duration``, ``response``, and (only when the response was truncated
    by ``_DEBUG_MAX_BODY``) ``truncated: True``. Records are ordered by the order the
    requests were issued, even when concurrent responses arrive out of order.
    """
    text = _ANSI_RE.sub("", stderr)

    # Request and response blocks interleave under concurrency: a block's fields run
    # from its marker line to the start of the next marker of either kind.
    markers = sorted(
        [(m, "request") for m in _REQUEST_RE.finditer(text)]
        + [(m, "response") for m in _STATUS_RE.finditer(text)],
        key=lambda pair: pair[0].start(),
    )

    records_by_id: dict[int, dict[str, Any]] = {}
    for i, (match, kind) in enumerate(markers):
        end = markers[i + 1][0].start() if i + 1 < len(markers) else len(text)
        block = text[match.end():end]
        req_id = int(match.group(1))

        if kind == "request":
            records_by_id[req_id] = {
                "method": match.group(2),
                "url": match.group(3),
                "headers": _extract_json_field(block, "headers"),
                "params": _extract_json_field(block, "params"),
                "body": _extract_json_field(block, "body"),
                "status": None,
                "duration": None,
                "response": None,
            }
        else:
            record = records_by_id.get(req_id)
            if record is None:  # response with no seen request — skip defensively
                continue
            record["status"] = int(match.group(2))
            record["duration"] = float(match.group(3))
            response, truncated = _extract_response(block)
            record["response"] = response
            if truncated:
                record["truncated"] = True

    return [records_by_id[req_id] for req_id in sorted(records_by_id)]


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
