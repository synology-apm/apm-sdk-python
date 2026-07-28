"""Shared helpers for model `to_dict()` methods.

This module holds serialization logic common to multiple model modules.
"""
from __future__ import annotations

import dataclasses
import inspect
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


_property_names_cache: dict[type, tuple[str, ...]] = {}


def _property_names(cls: type) -> tuple[str, ...]:
    """Public (non-underscore) @property names defined on cls, including inherited ones."""
    if cls not in _property_names_cache:
        _property_names_cache[cls] = tuple(
            name for name, _ in inspect.getmembers(cls, lambda v: isinstance(v, property)) if not name.startswith("_")
        )
    return _property_names_cache[cls]


def auto_to_dict(
    obj: Any, *, exclude: frozenset[str] = frozenset(), extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a JSON-safe dict from a dataclass instance's own fields and public properties.

    Every dataclass field and every public @property on obj's type (including inherited ones)
    is included automatically via _serialize_field() — a new @property needs no manual step to
    appear in to_dict() output. Name a property with a leading underscore to keep it internal.
    `exclude` drops a field or property being replaced; `extra` supplies a non-formulaic
    conversion or renamed/restructured output, merged on top last.
    """
    d = {f.name: _serialize_field(getattr(obj, f.name)) for f in dataclasses.fields(obj) if f.name not in exclude}
    for name in _property_names(type(obj)):
        if name in exclude or name in d:
            continue
        d[name] = _serialize_field(getattr(obj, name))
    if extra:
        d.update(extra)
    return d
