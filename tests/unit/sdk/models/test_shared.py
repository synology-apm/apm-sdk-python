"""Unit tests for models/_shared.py's auto_to_dict()."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synology_apm.sdk.models._shared import auto_to_dict


@dataclass(frozen=True)
class _Sample:
    value: int

    @property
    def doubled(self) -> int:
        return self.value * 2

    @property
    def _hidden(self) -> str:
        return "internal"

    def to_dict(self) -> dict[str, Any]:
        return auto_to_dict(self)


def test_auto_to_dict_includes_public_properties() -> None:
    """A public @property is serialized automatically, without being listed in extra=."""
    d = _Sample(value=5).to_dict()
    assert d["value"] == 5
    assert d["doubled"] == 10


def test_auto_to_dict_skips_leading_underscore_properties() -> None:
    """A leading-underscore @property is treated as internal and excluded automatically."""
    assert "_hidden" not in _Sample(value=5).to_dict()


def test_auto_to_dict_exclude_drops_a_property() -> None:
    """exclude= can drop a property from the output entirely, not just dataclass fields."""
    assert "doubled" not in auto_to_dict(_Sample(value=5), exclude=frozenset({"doubled"}))


@dataclass(frozen=True)
class _WithExtraOverride:
    value: float

    @property
    def pct(self) -> float:
        return self.value

    def to_dict(self) -> dict[str, Any]:
        return auto_to_dict(self, extra={"pct": round(self.pct, 1)})


def test_auto_to_dict_extra_overrides_auto_included_property() -> None:
    """extra= still wins over the raw auto-included property value."""
    assert _WithExtraOverride(value=12.345).to_dict()["pct"] == 12.3
