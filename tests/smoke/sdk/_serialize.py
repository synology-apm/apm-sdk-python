"""Recursive dataclass/enum/datetime -> JSON-safe converter for ctx.data snapshots and api_trace.jsonl."""
from __future__ import annotations

import dataclasses
import datetime
import enum
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Convert SDK models (frozen dataclasses), enums, and datetimes into JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return str(value)
