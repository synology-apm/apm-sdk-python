"""RetirementPlan, RetirementRetentionPolicy, and RetirementPlanCreateRequest data models."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetirementRetentionPolicy:
    """Version retention policy for a Retirement Plan.

    Attributes:
        days:                 Number of days to retain versions. None means no day limit (keep all).
        keep_latest_version:  Always retain the most recent version, even after the day limit expires.
    """
    days: int | None
    keep_latest_version: bool


@dataclass(frozen=True)
class RetirementPlan:
    """Retirement plan applied to Workloads being retired.

    Attributes:
        plan_id:                       Unique plan identifier.
        name:                          Plan display name.
        description:                   Plan description.
        retention:                     Version retention policy after retirement.
        workload_count:                Total number of workloads this plan is applied to.
        run_schedule_by_controller_time: Whether schedules run on the APM controller's clock rather
                                       than each backup server's local clock.
    """
    plan_id: str
    name: str
    description: str = ""
    retention: RetirementRetentionPolicy | None = None
    workload_count: int | None = None
    run_schedule_by_controller_time: bool = False


@dataclass(frozen=True)
class RetirementPlanCreateRequest:
    """Parameters for creating a Retirement Plan.

    Attributes:
        name:                           Plan display name.
        retention_days:                 Days to retain versions; None keeps all versions indefinitely.
        description:                    Plan description.
        keep_latest_version:            Always retain the most recent version even after retention expires.
        run_schedule_by_controller_time: Use APM controller's clock for scheduling.
    """
    name: str
    retention_days: int | None
    description: str = ""
    keep_latest_version: bool = True
    run_schedule_by_controller_time: bool = False
