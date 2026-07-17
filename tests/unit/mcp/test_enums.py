"""Parity tests: _enums.py Literal aliases must stay in sync with the SDK Enums they mirror.

_enums.py's own docstring acknowledges a Literal can silently drift from its corresponding
SDK Enum (a value added/removed on one side but not the other). These tests fail loudly on
any such drift, except where a Literal intentionally covers a strict subset of its Enum's
values -- those exclusions are declared explicitly below, not silently special-cased.
"""
from __future__ import annotations

from typing import get_args

import pytest

from synology_apm.mcp import _enums
from synology_apm.sdk import (
    BackupActivityStatus,
    BackupServerType,
    FileServerType,
    LogLevel,
    M365WorkloadType,
    MachineWorkloadType,
    RestoreActivityStatus,
    ServerStatus,
)

# (Literal, corresponding SDK Enum, values intentionally excluded from the Literal)
_PARITY_CASES = [
    pytest.param(_enums.MachineWorkloadTypeLiteral, MachineWorkloadType, set(), id="MachineWorkloadType"),
    pytest.param(_enums.M365WorkloadTypeLiteral, M365WorkloadType, set(), id="M365WorkloadType"),
    pytest.param(_enums.BackupActivityStatusLiteral, BackupActivityStatus, set(), id="BackupActivityStatus"),
    pytest.param(_enums.RestoreActivityStatusLiteral, RestoreActivityStatus, set(), id="RestoreActivityStatus"),
    pytest.param(_enums.ServerStatusLiteral, ServerStatus, set(), id="ServerStatus"),
    pytest.param(_enums.BackupServerTypeLiteral, BackupServerType, set(), id="BackupServerType"),
    pytest.param(_enums.LogLevelLiteral, LogLevel, set(), id="LogLevel"),
    pytest.param(
        _enums.FileServerTypeLiteral,
        FileServerType,
        {"unknown"},
        # FileServerType.UNKNOWN is an output-only sentinel for a server type not yet
        # recognised by this SDK version (see its docstring in sdk/enums.py);
        # FileServerTypeLiteral is only ever used as an input parameter (creating/
        # updating a file server), so it is correctly never offered as a choice.
        id="FileServerType-excludes-unknown",
    ),
]


@pytest.mark.parametrize("literal,enum_cls,excluded", _PARITY_CASES)
def test_literal_matches_sdk_enum_values(literal, enum_cls, excluded):
    literal_values = set(get_args(literal))
    enum_values = {member.value for member in enum_cls}
    assert literal_values == enum_values - excluded


def test_weekday_literal_has_seven_distinct_days():
    """WeekDay is int-valued in the SDK (0=Sunday..6=Saturday), so it can't be compared
    directly to WeekDayLiteral's 3-letter strings -- see test_parse_weekdays_covers_all_sdk_weekdays
    in test_plan_builders.py for the behavioral parity check against the SDK enum."""
    assert set(get_args(_enums.WeekDayLiteral)) == {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}
