"""Unit tests for SDK model properties."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from synology_apm.sdk.enums import (
    MachineWorkloadType,
    VersionStatus,
    WorkloadCategory,
    WorkloadStatus,
)
from synology_apm.sdk.models.location import LocationInfo
from synology_apm.sdk.models.protection_plan import ProtectionPlan
from synology_apm.sdk.models.retirement_plan import RetirementPlan
from synology_apm.sdk.models.version import VersionLocation, WorkloadVersion
from synology_apm.sdk.models.workload import (
    M365GroupInfo,
    M365SiteInfo,
    M365TeamInfo,
    M365UserInfo,
    MachineWorkload,
)


def make_machine_wl(**kwargs: Any) -> MachineWorkload:
    defaults: dict[str, Any] = dict(
        workload_id="wl-id-001",
        name="TestPC",
        category=WorkloadCategory.MACHINE,
        namespace="ns-001",
        last_backup_at=None,
        is_retired=False,
        protected_data_bytes=0,
        status=WorkloadStatus.NO_BACKUPS,
        plan=ProtectionPlan(plan_id="plan-001", name="Test Plan", category=WorkloadCategory.MACHINE),
        workload_type=MachineWorkloadType.PC,
        agent_version="1.2.0",
    )
    defaults.update(kwargs)
    return MachineWorkload(**defaults)


def make_version(**kwargs: Any) -> WorkloadVersion:
    defaults: dict[str, Any] = dict(
        version_id="ver-001",
        workload_id="wl-id-001",
        namespace="ns-001",
        execution_id="exec-001",
        status=VersionStatus.SUCCESS,
        created_at=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
        changed_size_bytes=512,
        locked=False,
        locations=[],
    )
    defaults.update(kwargs)
    return WorkloadVersion(**defaults)


def make_location(*, is_remote: bool = False) -> VersionLocation:
    info = LocationInfo(
        is_remote_storage=is_remote,
        identifier="ns-001",
        name="apm-server-01",
        endpoint="192.0.2.1",
        vault=None,
    )
    return VersionLocation(namespace="ns-001", location_info=info, location_id="ver-001")


# ── Workload.is_backing_up ─────────────────────────────────────────────────

def test_is_backing_up_false_when_no_progress() -> None:
    wl = make_machine_wl(backup_progress=None)
    assert wl.is_backing_up is False


def test_is_backing_up_true_when_progress_set() -> None:
    wl = make_machine_wl(backup_progress=42)
    assert wl.is_backing_up is True


def test_is_backing_up_true_when_progress_zero() -> None:
    wl = make_machine_wl(backup_progress=0)
    assert wl.is_backing_up is True


def test_is_backing_up_true_when_items_backed_up_set() -> None:
    wl = make_machine_wl(items_backed_up=100)
    assert wl.is_backing_up is True


def test_is_backing_up_false_when_both_none() -> None:
    wl = make_machine_wl(backup_progress=None, items_backed_up=None)
    assert wl.is_backing_up is False


# ── M365 *Info.label ──────────────────────────────────────────────────────

def test_m365_user_info_label() -> None:
    info = M365UserInfo(user_principal_name="alice@contoso.com")
    assert info.label == "alice@contoso.com"


def test_m365_site_info_label() -> None:
    info = M365SiteInfo(
        site_url="https://contoso.sharepoint.com/sites/hr",
        site_name="HR Site",
    )
    assert info.label == "https://contoso.sharepoint.com/sites/hr"


def test_m365_team_info_label() -> None:
    info = M365TeamInfo(
        team_id="team-001",
        team_name="Engineering",
        web_url="https://teams.microsoft.com/l/team/123",
    )
    assert info.label == "https://teams.microsoft.com/l/team/123"


def test_m365_group_info_label() -> None:
    info = M365GroupInfo(
        group_id="grp-001",
        display_name="Marketing Group",
        mail="marketing@contoso.com",
    )
    assert info.label == "marketing@contoso.com"


# ── VersionLocation.location_info ─────────────────────────────────────────

def test_location_info_appliance() -> None:
    loc = make_location(is_remote=False)
    assert loc.location_info.is_remote_storage is False
    assert loc.location_info.vault is None


def test_location_info_remote_with_vault() -> None:
    info = LocationInfo(
        is_remote_storage=True,
        identifier="storage-uid-001",
        name="DSM-Storage",
        endpoint="192.0.2.20:8444",
        vault="MyVault",
    )
    loc = VersionLocation(namespace="ns-001", location_info=info, location_id="ver-002")
    assert loc.location_info.is_remote_storage is True
    assert loc.location_info.vault == "MyVault"


def test_version_locations_count() -> None:
    version = make_version(locations=[make_location(), make_location(is_remote=True)])
    assert len(version.locations) == 2
    assert version.locations[0].location_info.is_remote_storage is False
    assert version.locations[1].location_info.is_remote_storage is True


def test_version_empty_locations() -> None:
    version = make_version(locations=[])
    assert version.locations == []


# ── Lightweight ProtectionPlan/RetirementPlan (workload-embedded plan reference) ──

def test_protection_plan_lightweight_defaults() -> None:
    plan = ProtectionPlan(plan_id="plan-001", name="Daily Backup", category=WorkloadCategory.MACHINE)
    assert plan.policy is None
    assert plan.workload_count is None


def test_retirement_plan_lightweight_defaults() -> None:
    plan = RetirementPlan(plan_id="plan-002", name="Compliance Retention")
    assert plan.description == ""
    assert plan.retention is None
    assert plan.workload_count is None


def test_workload_plan_field() -> None:
    wl = make_machine_wl(plan=ProtectionPlan(plan_id="plan-003", name="Daily Backup", category=WorkloadCategory.MACHINE))
    assert wl.plan.plan_id == "plan-003"
    assert wl.plan.name == "Daily Backup"
