"""Shared factories for the billing_report.py test files.

All factories are keyword-only and use one coherent rate set so charge literals
stay consistent across test files:

- ``Standard``: $5.00 per instance, $0.20 per GB (the fallback rate card)
- ``Premium``: $10.00 per instance, $0.30 per GB
"""
from __future__ import annotations

from billing_report import (
    _GroupCharge,
    _GroupSpec,
    _PlanCharge,
    _PlanSection,
    _PricingConfig,
    _PricingPlan,
    _ServerCharge,
    _ServerTypeStat,
    _TypeCount,
)

STANDARD_RATE = _PricingPlan("Standard", charge_per_instance=5.0, charge_per_gb=0.20)
PREMIUM_RATE = _PricingPlan("Premium", charge_per_instance=10.0, charge_per_gb=0.30)


def make_default_config() -> _PricingConfig:
    """Config with a single Standard rate card (the fallback for everything)."""
    return _PricingConfig(pricing_plans=[STANDARD_RATE])


def make_two_plan_config() -> _PricingConfig:
    """Standard + Premium rate cards; plan-001 is assigned to Premium."""
    return _PricingConfig(
        pricing_plans=[STANDARD_RATE, PREMIUM_RATE],
        assignments={"plan-001": "Premium"},
    )


def make_group_config_two() -> _PricingConfig:
    """Two overlapping groups (GroupA=Standard, GroupB=Premium), both listing plan-001."""
    return _PricingConfig(
        pricing_plans=[STANDARD_RATE, PREMIUM_RATE],
        groups=[
            _GroupSpec("GroupA", "Standard", plan_ids=["plan-001"]),
            _GroupSpec("GroupB", "Premium", plan_ids=["plan-001"]),
        ],
    )


def make_resolved_config() -> _PricingConfig:
    """Two rate cards, two groups, one plan assignment, server-id-001 resolved to ns-001.

    GroupA (Standard) lists plan-001 and plan-002; GroupB (Premium) lists plan-003
    and backup server server-id-001. plan-001 is assigned the Premium rate card.
    """
    config = _PricingConfig(
        pricing_plans=[STANDARD_RATE, PREMIUM_RATE],
        groups=[
            _GroupSpec(
                name="GroupA",
                pricing_plan_name="Standard",
                plan_ids=["plan-001", "plan-002"],
            ),
            _GroupSpec(
                name="GroupB",
                pricing_plan_name="Premium",
                plan_ids=["plan-003"],
                backup_server_ids=["server-id-001"],
            ),
        ],
        assignments={"plan-001": "Premium"},
        server_assignments={},
    )
    config.resolve_server_ids({"server-id-001": "ns-001"})
    return config


def make_plan_section(
    *,
    plan_name: str = "Daily Backup",
    plan_type: str = "Protection Plan",
    plan_id: str = "plan-001",
    group_names: tuple[str, ...] = (),
    count: int = 1,
    storage_bytes: int = 0,
    type_label: str = "VM",
    type_order: int = 0,
) -> _PlanSection:
    """Build a single-row _PlanSection."""
    tc = _TypeCount(type_label=type_label, type_order=type_order, count=count, storage_bytes=storage_bytes)
    return _PlanSection(
        plan_name=plan_name,
        plan_type=plan_type,
        plan_id=plan_id,
        group_names=group_names,
        rows=[tc],
    )


def make_server_stat(
    *,
    namespace: str = "ns-001",
    plan_id: str = "plan-001",
    group_names: tuple[str, ...] = (),
    type_label: str = "VM",
    type_order: int = 0,
    count: int = 1,
    storage_bytes: int = 0,
) -> _ServerTypeStat:
    """Build a _ServerTypeStat with sensible defaults."""
    return _ServerTypeStat(
        namespace=namespace,
        plan_id=plan_id,
        group_names=group_names,
        type_label=type_label,
        type_order=type_order,
        count=count,
        storage_bytes=storage_bytes,
    )


def make_plan_charge(
    *,
    plan_name: str = "Daily Backup",
    plan_type: str = "Protection Plan",
    plan_id: str = "plan-001",
    pricing_plan_name: str = "Standard",
    charge_per_instance: float = 5.0,
    charge_per_gb: float = 0.20,
    instances: int = 3,
    storage_gb: float = 2.0,
    instance_charge: float = 15.0,
    storage_charge: float = 0.40,
) -> _PlanCharge:
    """Build a _PlanCharge at the Standard rate (3 instances, 2 GB by default)."""
    return _PlanCharge(
        plan_name=plan_name,
        plan_type=plan_type,
        plan_id=plan_id,
        pricing_plan_name=pricing_plan_name,
        charge_per_instance=charge_per_instance,
        charge_per_gb=charge_per_gb,
        instances=instances,
        storage_gb=storage_gb,
        instance_charge=instance_charge,
        storage_charge=storage_charge,
    )


def make_group_charge(
    *,
    group_name: str = "Contoso",
    plan_type: str = "Protection Plan",
    pricing_plan_name: str = "Premium",
    charge_per_instance: float = 10.0,
    charge_per_gb: float = 0.30,
    instances: int = 0,
    storage_gb: float = 0.0,
    instance_charge: float = 0.0,
    storage_charge: float = 0.0,
) -> _GroupCharge:
    """Build a _GroupCharge at the Premium rate (zero tallies by default)."""
    return _GroupCharge(
        group_name=group_name,
        plan_type=plan_type,
        pricing_plan_name=pricing_plan_name,
        charge_per_instance=charge_per_instance,
        charge_per_gb=charge_per_gb,
        instances=instances,
        storage_gb=storage_gb,
        instance_charge=instance_charge,
        storage_charge=storage_charge,
    )


def make_server_charge(
    *,
    server_name: str = "apm-server-01",
    namespace: str = "ns-001",
    pricing_plan_name: str = "Standard",
    charge_per_instance: float = 5.0,
    charge_per_gb: float = 0.20,
    instances: int = 2,
    storage_gb: float = 1.0,
    instance_charge: float = 10.0,
    storage_charge: float = 0.20,
) -> _ServerCharge:
    """Build a _ServerCharge at the Standard rate (2 instances, 1 GB by default)."""
    return _ServerCharge(
        server_name=server_name,
        namespace=namespace,
        pricing_plan_name=pricing_plan_name,
        charge_per_instance=charge_per_instance,
        charge_per_gb=charge_per_gb,
        instances=instances,
        storage_gb=storage_gb,
        instance_charge=instance_charge,
        storage_charge=storage_charge,
    )
