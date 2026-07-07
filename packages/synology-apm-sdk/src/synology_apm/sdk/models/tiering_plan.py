"""TieringPlan, TieringStatus, and TieringPlanCreateRequest data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from ..enums import CopyReason, VersionCopyStatus
from .location import LocationInfo
from .remote_storage import RemoteStorage


@dataclass(frozen=True)
class TieringStatus:
    """Tiering operation status for a Tiering Plan or backup server.

    Attributes:
        status:               Overall tiering status.
        reason:               Detail reason when status is SKIPPED, RETRY, or FAILED; None otherwise.
        pending_version_count: Number of versions waiting to be tiered. Meaningful when
                              IN_PROGRESS, WAITING, RETRY, or FAILED.
        remaining_bytes:      Estimated bytes remaining for the pending tiering operation;
                              None when unavailable.
    """
    status: VersionCopyStatus
    reason: CopyReason | None
    pending_version_count: int = 0
    remaining_bytes: int | None = None


@dataclass(frozen=True)
class TieringPlan:
    """Tiering plan configuration for moving backup versions to remote storage.

    Attributes:
        plan_id:                       Unique plan identifier.
        name:                          Plan display name.
        description:                   Plan description.
        tiering_after_days:            Number of days after which backup versions are moved to the tiering destination.
        daily_check_time:              Time of day when the tiering check runs (daily).
        destination:                   Remote storage destination; None if the destination lookup failed.
        server_count:                  Number of backup servers included in this plan.
        tiering_status:                Current tiering operation status; None when status is unavailable.
        run_schedule_by_controller_time: Whether schedules run on the APM controller's clock rather
                                       than each backup server's local clock.
    """
    plan_id: str
    name: str
    description: str
    tiering_after_days: int
    daily_check_time: time
    destination: LocationInfo | None
    server_count: int
    tiering_status: TieringStatus | None = None
    run_schedule_by_controller_time: bool = False


@dataclass(frozen=True)
class TieringPlanCreateRequest:
    """Parameters for creating a Tiering Plan.

    Attributes:
        name:                           Plan display name.
        tier_after_days:                Number of days before versions are moved to the destination.
        destination:                    Remote storage destination (from `apm.remote_storages`).
        daily_check_time:               Time of day when the tiering check runs.
        description:                    Plan description.
        run_schedule_by_controller_time: Use APM controller's clock for scheduling.
    """
    name: str
    tier_after_days: int
    destination: RemoteStorage
    daily_check_time: time = field(default_factory=lambda: time(20, 0))
    description: str = ""
    run_schedule_by_controller_time: bool = False
