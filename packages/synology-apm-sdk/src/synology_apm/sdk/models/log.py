"""Log data models for server-scoped log queries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..enums import APMActivityLogType, LogLevel
from ._shared import auto_to_dict


@dataclass(frozen=True)
class APMActivityLog:
    """A single APM activity log entry.

    Attributes:
        level: Severity level.
        log_type: Log category (PROTECTION / SYSTEM / DATA_ACCESS). None when absent in response.
        timestamp: Event time (UTC).
        username: User who triggered the event (SYSTEM for automated events).
        description: Human-readable event description.
    """
    level: LogLevel
    log_type: APMActivityLogType | None
    timestamp: datetime
    username: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class DriveLog:
    """A single drive information log entry.

    Attributes:
        level: Severity level.
        timestamp: Event time (UTC).
        description: Human-readable event description.
        server_name: Name of the backup server that contains the drive. "-" when not applicable.
        model: Drive model string. "-" when not applicable.
        location: Drive physical location. "-" when not applicable.
        serial: Drive serial number. "-" when not applicable.
    """
    level: LogLevel
    timestamp: datetime
    description: str
    server_name: str
    model: str
    location: str
    serial: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class ConnectionLog:
    """A single connection log entry.

    Attributes:
        level: Severity level.
        timestamp: Event time (UTC).
        username: User involved in the connection event.
        description: Human-readable event description.
    """
    level: LogLevel
    timestamp: datetime
    username: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)


@dataclass(frozen=True)
class SystemLog:
    """A single advanced system log entry.

    Attributes:
        level: Severity level.
        timestamp: Event time (UTC).
        username: User who triggered the event (SYSTEM for automated events).
        description: Human-readable event description.
    """
    level: LogLevel
    timestamp: datetime
    username: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation."""
        return auto_to_dict(self)
