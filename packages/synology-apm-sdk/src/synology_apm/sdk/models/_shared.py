"""Shared helpers for model `to_dict()` methods.

This module holds serialization logic common to multiple model modules.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, time
from enum import Enum
from typing import Any


def _serialize_field(value: Any) -> Any:
    """Type-based dispatch for a single dataclass field value, for use by auto_to_dict()."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_serialize_field(v) for v in value]
    return value


def auto_to_dict(
    obj: Any, *, exclude: frozenset[str] = frozenset(), extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a JSON-safe dict from a dataclass instance's own fields.

    Every field is converted by _serialize_field() (Enum -> .value, datetime/date/time ->
    ISO 8601, nested to_dict()-bearing objects -> recursive call, list/tuple -> element-wise).
    Fields needing different treatment (computed properties, non-formulaic conversions such as
    a timedelta reduced to whole seconds, renamed/restructured output) are named in `exclude`
    and supplied via `extra`, which is merged on top last.
    """
    d = {f.name: _serialize_field(getattr(obj, f.name)) for f in dataclasses.fields(obj) if f.name not in exclude}
    if extra:
        d.update(extra)
    return d
