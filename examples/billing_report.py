#!/usr/bin/env python3
"""
Billing report — computes workload count and storage usage per Protection Plan and
Retirement Plan (for both machine and M365 workloads), optionally applying named pricing
plans to produce per-customer charges.

The pricing config (--pricing) has three layers:
  - pricing_plans: named rate cards (charge per instance, charge per GB)
  - groups: one group per customer — aggregates all of that customer's APM plans into
    a single billing row using the group's pricing plan
  - plans: per-plan pricing override for standalone APM plans not assigned to any group

Pass --charge-per-instance / --charge-per-gb to apply a single flat rate to all plans
without a YAML config. Pass --dump-pricing-template to generate a starter template and
exit (no APM connection needed; pipe to a file to save).

Output is split into three tables: Groups (one row per customer), Standalone Plans
(APM plans not in any group), and a Summary. Pass --details to include a flat
per-workload-type breakdown: one row per workload type per plan, sorted by group
then plan name. CSV details also include a Pricing Plans section listing applied
rate cards. JSON details embed the per-type breakdown hierarchically under each
group and plan.

Usage:
    python billing_report.py
    python billing_report.py --charge-per-instance 5 --charge-per-gb 0.1
    python billing_report.py --pricing pricing.yaml
    python billing_report.py --pricing pricing.yaml --details
    python billing_report.py --dump-pricing-template > pricing.yaml
    python billing_report.py -o xlsx --output-file billing.xlsx
    python billing_report.py --pricing pricing.yaml --details -o xlsx --output-file billing.xlsx
    python billing_report.py --concurrency 10

Environment variables (can be set in .env):
    APM_HOST          hostname or IP (supports host:port)
    APM_USERNAME      account
    APM_PASSWORD      password
    APM_NO_VERIFY_SSL set to true to skip SSL verification (self-signed certificate environments)

Additional dependencies:
    pip install pyyaml    # for --pricing / --dump-pricing-template (dev dep, included by `uv sync`)
    pip install openpyxl  # for -o xlsx (dev dep, included by `uv sync`)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import openpyxl
import openpyxl.styles
import yaml
from _common import (
    WORKLOAD_TYPE_ORDER,
    list_m365_tenants,
    make_client,
    paginate,
    run_main,
    workload_type_label,
)
from openpyxl.utils import get_column_letter as _get_col_letter
from openpyxl.worksheet.worksheet import Worksheet as _Worksheet

from synology_apm.sdk import (
    APMClient,
    M365Workload,
    M365WorkloadType,
    MachineWorkload,
    SaasTenant,
)

_PROTECTION = "Protection Plan"
_RETIREMENT = "Retirement Plan"
_MIXED_KIND = "Mixed"
_BYTES_PER_GB = 1024 ** 3
_DEFAULT_CONCURRENCY = 5

_M365_TYPES: list[M365WorkloadType] = [t for t in WORKLOAD_TYPE_ORDER if isinstance(t, M365WorkloadType)]


# ── Pricing config ────────────────────────────────────────────────────────────

@dataclass
class _PricingPlan:
    name: str
    charge_per_instance: float = 0.0
    charge_per_gb: float = 0.0


@dataclass
class _GroupSpec:
    name: str
    pricing_plan_name: str
    plan_ids: list[str]


@dataclass
class _PricingConfig:
    """Named pricing plans with optional groups and per-APM-plan assignments.

    plan_for() returns the applicable _PricingPlan and group name for an APM plan_id.
    Group membership takes precedence over per-plan assignments. The first pricing
    plan is the fallback for any plan not covered by a group or assignment.
    """
    pricing_plans: list[_PricingPlan]
    groups: list[_GroupSpec] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)

    def plan_for(self, plan_id: str) -> tuple[_PricingPlan, str]:
        """Return (pricing_plan, group_name). group_name is '' for standalone plans."""
        for group in self.groups:
            if plan_id in group.plan_ids:
                for pp in self.pricing_plans:
                    if pp.name == group.pricing_plan_name:
                        return pp, group.name
                return self.pricing_plans[0], group.name
        name = self.assignments.get(plan_id)
        if name:
            for pp in self.pricing_plans:
                if pp.name == name:
                    return pp, ""
        return self.pricing_plans[0], ""


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class _TypeCount:
    type_label: str
    count: int
    storage_bytes: int


@dataclass
class _PlanSection:
    plan_name: str
    plan_type: str
    plan_id: str
    rows: list[_TypeCount] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return sum(r.count for r in self.rows)

    @property
    def total_bytes(self) -> int:
        return sum(r.storage_bytes for r in self.rows)


@dataclass
class _PlanCharge:
    plan_name: str
    plan_type: str
    plan_id: str
    group_name: str
    instances: int
    storage_gb: float
    pricing_plan_name: str
    charge_per_instance: float
    charge_per_gb: float
    instance_charge: float
    storage_charge: float

    @property
    def total_charge(self) -> float:
        return self.instance_charge + self.storage_charge


@dataclass
class _GroupCharge:
    group_name: str
    plan_type: str
    pricing_plan_name: str
    charge_per_instance: float
    charge_per_gb: float
    instances: int
    storage_gb: float
    instance_charge: float
    storage_charge: float

    @property
    def total_charge(self) -> float:
        return self.instance_charge + self.storage_charge


@dataclass
class _DetailRow:
    group_name: str
    plan_type: str
    plan_name: str
    plan_id: str
    pricing_plan: str
    workload_type: str
    instances: int
    storage_gb: float
    instance_charge: float
    storage_charge: float

    @property
    def total_charge(self) -> float:
        return self.instance_charge + self.storage_charge


def fmt_money(amount: float) -> str:
    """Render a charge amount with 2 decimal places and thousands separators."""
    return f"${amount:,.2f}"


# ── Pricing config I/O ────────────────────────────────────────────────────────

_PRICING_TEMPLATE = """\
# APM billing report pricing configuration (MSP use case)
# Generate this template: python billing_report.py --dump-pricing-template > pricing.yaml

# Rate cards available to assign to customers (groups) or individual APM plans.
# The first entry is the fallback for any plan not explicitly assigned or grouped.
pricing_plans:
  - name: Standard
    charge_per_instance: 5.0   # charge per workload instance
    charge_per_gb: 0.10         # charge per GB of protected storage
  - name: Premium
    charge_per_instance: 10.0
    charge_per_gb: 0.20
  - name: Compliance
    charge_per_instance: 0.0
    charge_per_gb: 0.05

# One group per customer — all of that customer's APM plans (Protection Plans and
# Retirement Plans alike) are combined into a single billing row for the group.
# Find plan IDs: run `python billing_report.py -o json` and inspect
# the plan_id field in the standalone_plans array.
groups:
  - name: Contoso
    pricing_plan: Premium
    plans:
      - 00000000-0000-0000-0000-000000000001  # Daily Backup
      - 00000000-0000-0000-0000-000000000002  # Compliance Retention
  - name: Acme Corp
    pricing_plan: Standard
    plans:
      - 00000000-0000-0000-0000-000000000003  # Daily Backup

# Optional: per-plan pricing for APM plans not assigned to any customer group.
# Plans not listed here use the first pricing plan above (Standard).
plans:
  # 00000000-0000-0000-0000-000000000004: Compliance
"""


def _dump_pricing_template() -> None:
    print(_PRICING_TEMPLATE, end="")


def _load_pricing_yaml(path: str) -> _PricingConfig:
    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    pricing_plans = [
        _PricingPlan(
            name=str(entry["name"]),
            charge_per_instance=float(entry.get("charge_per_instance", 0.0)),
            charge_per_gb=float(entry.get("charge_per_gb", 0.0)),
        )
        for entry in (raw.get("pricing_plans") or [])
    ]
    if not pricing_plans:
        raise ValueError(f"{path}: pricing_plans must contain at least one entry")
    pricing_plan_names = {pp.name for pp in pricing_plans}

    groups: list[_GroupSpec] = []
    seen_plan_ids: set[str] = set()
    for entry in (raw.get("groups") or []):
        gname = str(entry["name"])
        pp_name = str(entry["pricing_plan"])
        if pp_name not in pricing_plan_names:
            raise ValueError(f"{path}: group '{gname}' references unknown pricing plan '{pp_name}'")
        plan_ids = [str(pid) for pid in (entry.get("plans") or [])]
        duplicates = seen_plan_ids & set(plan_ids)
        if duplicates:
            raise ValueError(
                f"{path}: plan ID(s) appear in multiple groups: {', '.join(sorted(duplicates))}"
            )
        seen_plan_ids.update(plan_ids)
        groups.append(_GroupSpec(name=gname, pricing_plan_name=pp_name, plan_ids=plan_ids))

    assignments = {str(k): str(v) for k, v in (raw.get("plans") or {}).items()}
    unknown = set(assignments.values()) - pricing_plan_names
    if unknown:
        raise ValueError(f"{path}: unknown pricing plan name(s) in plans: {', '.join(sorted(unknown))}")
    return _PricingConfig(pricing_plans=pricing_plans, groups=groups, assignments=assignments)


# ── Data collection ──────────────────────────────────────────────────────────

def _build_section(
    plan_id: str, plan_name: str, plan_type: str, workloads: list[MachineWorkload | M365Workload]
) -> _PlanSection:
    """Group one plan's workloads into per-type rows in canonical workload-type order."""
    buckets: dict[str, list[int]] = {}
    type_order: dict[str, int] = {}
    for wl in workloads:
        type_label = workload_type_label(wl)
        if type_label not in buckets:
            buckets[type_label] = [0, 0]
            type_order[type_label] = WORKLOAD_TYPE_ORDER.index(wl.workload_type)
        buckets[type_label][0] += 1
        buckets[type_label][1] += wl.protected_data_bytes
    rows = [_TypeCount(label, count, storage_bytes) for label, (count, storage_bytes) in buckets.items()]
    rows.sort(key=lambda r: type_order[r.type_label])
    return _PlanSection(plan_name, plan_type, plan_id, rows)


_WorkloadT = TypeVar("_WorkloadT", MachineWorkload, M365Workload)


async def _bounded_paginate(
    sem: asyncio.Semaphore,
    list_call: Callable[[int, int], Awaitable[tuple[list[_WorkloadT], int]]],
) -> tuple[list[_WorkloadT], int]:
    """Run one paginate() call under *sem*, so --concurrency bounds concurrent API calls directly."""
    async with sem:
        return await paginate(list_call)


async def _scan_billing(apm: APMClient, *, concurrency: int) -> list[_PlanSection]:
    """One _PlanSection per plan that has at least one workload, sorted alphabetically by plan name.

    Fetches all workloads in a single pass (machine active/retired + M365 per tenant/type
    active/retired), then groups by plan_id using the lightweight plan reference embedded in
    each workload. Sections are sorted by plan name regardless of plan kind (Protection vs
    Retirement). Within a section, rows follow the canonical workload-type display order.
    """
    sem = asyncio.Semaphore(concurrency)
    tenants = await list_m365_tenants(apm)

    coros: list[Awaitable[tuple[list[MachineWorkload] | list[M365Workload], int]]] = []
    retired_flags: list[bool] = []

    for is_retired in (False, True):
        async def _machine(
            limit: int, offset: int, r: bool = is_retired
        ) -> tuple[list[MachineWorkload], int]:
            return await apm.machine.workloads.list(is_retired=r, limit=limit, offset=offset)
        coros.append(_bounded_paginate(sem, _machine))
        retired_flags.append(is_retired)

    for tenant in tenants:
        for service in _M365_TYPES:
            for is_retired in (False, True):
                async def _m365(
                    limit: int, offset: int,
                    t: SaasTenant = tenant, s: M365WorkloadType = service, r: bool = is_retired,
                ) -> tuple[list[M365Workload], int]:
                    return await apm.m365.workloads.list(
                        tenant_id=t.tenant_id, workload_type=s, is_retired=r, limit=limit, offset=offset,
                    )
                coros.append(_bounded_paginate(sem, _m365))
                retired_flags.append(is_retired)

    plan_buckets: dict[str, list[MachineWorkload | M365Workload]] = {}
    plan_meta: dict[str, tuple[str, str]] = {}

    for is_retired, (items, _) in zip(retired_flags, await asyncio.gather(*coros)):
        for wl in items:
            pid = wl.plan.plan_id
            if pid not in plan_buckets:
                plan_buckets[pid] = []
                plan_meta[pid] = (wl.plan.name, _RETIREMENT if is_retired else _PROTECTION)
            plan_buckets[pid].append(wl)

    sections = [
        _build_section(pid, name, kind, plan_buckets[pid])
        for pid, (name, kind) in plan_meta.items()
    ]
    return sorted(sections, key=lambda s: s.plan_name)


# ── Charge computation ────────────────────────────────────────────────────────

def _compute_plan_charges(sections: list[_PlanSection], pricing: _PricingConfig) -> list[_PlanCharge]:
    """Compute per-plan charges. All group and summary aggregations derive from this."""
    charges = []
    for s in sections:
        pp, group_name = pricing.plan_for(s.plan_id)
        storage_gb = s.total_bytes / _BYTES_PER_GB
        charges.append(_PlanCharge(
            plan_name=s.plan_name,
            plan_type=s.plan_type,
            plan_id=s.plan_id,
            group_name=group_name,
            instances=s.total_count,
            storage_gb=round(storage_gb, 2),
            pricing_plan_name=pp.name,
            charge_per_instance=pp.charge_per_instance,
            charge_per_gb=pp.charge_per_gb,
            instance_charge=round(s.total_count * pp.charge_per_instance, 2),
            storage_charge=round(storage_gb * pp.charge_per_gb, 2),
        ))
    return charges


def _compute_group_charges(
    plan_charges: list[_PlanCharge], groups: list[_GroupSpec]
) -> list[_GroupCharge]:
    """Aggregate plan charges into group charges, preserving YAML group order."""
    buckets: dict[str, list[_PlanCharge]] = {g.name: [] for g in groups}
    for c in plan_charges:
        if c.group_name in buckets:
            buckets[c.group_name].append(c)
    result = []
    for g in groups:
        members = buckets[g.name]
        if not members:
            continue
        kinds = {m.plan_type for m in members}
        plan_type = kinds.pop() if len(kinds) == 1 else _MIXED_KIND
        result.append(_GroupCharge(
            group_name=g.name,
            plan_type=plan_type,
            pricing_plan_name=members[0].pricing_plan_name,
            charge_per_instance=members[0].charge_per_instance,
            charge_per_gb=members[0].charge_per_gb,
            instances=sum(m.instances for m in members),
            storage_gb=round(sum(m.storage_gb for m in members), 2),
            instance_charge=round(sum(m.instance_charge for m in members), 2),
            storage_charge=round(sum(m.storage_charge for m in members), 2),
        ))
    return result


def _build_detail_rows(
    sections: list[_PlanSection],
    plan_charges: list[_PlanCharge],
) -> list[_DetailRow]:
    """One row per workload type per plan, sorted by named groups first then plan_name."""
    charges_by_id = {c.plan_id: c for c in plan_charges}
    rows: list[_DetailRow] = []
    for section in sections:
        charge = charges_by_id[section.plan_id]
        for type_row in section.rows:
            storage_gb = type_row.storage_bytes / _BYTES_PER_GB
            rows.append(_DetailRow(
                group_name=charge.group_name,
                plan_type=_kind_label(section.plan_type),
                plan_name=section.plan_name,
                plan_id=section.plan_id,
                pricing_plan=charge.pricing_plan_name,
                workload_type=type_row.type_label,
                instances=type_row.count,
                storage_gb=round(storage_gb, 4),
                instance_charge=round(type_row.count * charge.charge_per_instance, 2),
                storage_charge=round(storage_gb * charge.charge_per_gb, 2),
            ))
    rows.sort(key=lambda r: (1 if not r.group_name else 0, r.group_name, r.plan_name))
    return rows


# ── Output: table helpers ─────────────────────────────────────────────────────

def _kind_label(plan_type: str) -> str:
    if plan_type == _PROTECTION:
        return "Protection"
    if plan_type == _RETIREMENT:
        return "Retirement"
    return "Mixed"


def _billing_table_header(
    name_header: str, name_width: int, kind_header: str | None, pricing_width: int,
) -> str:
    kind_col = f"  {kind_header:<10}" if kind_header is not None else ""
    pricing_col = f"  {'Pricing Plan':<{pricing_width}}" if pricing_width else ""
    return (
        f"{name_header:<{name_width}}{kind_col}{pricing_col}"
        f"  {'Instances':>9}  {'Storage (GB)':>12}  {'Instance Chg':>13}  {'Storage Chg':>12}  {'Total Chg':>10}"
    )


def _billing_table_rule(name_width: int, has_kind: bool, pricing_width: int) -> str:
    kind_col = f"  {'-'*10}" if has_kind else ""
    pricing_col = f"  {'-'*pricing_width}" if pricing_width else ""
    return (
        f"{'-'*name_width}{kind_col}{pricing_col}"
        f"  {'-'*9}  {'-'*12}  {'-'*13}  {'-'*12}  {'-'*10}"
    )


def _billing_row(
    name: str, kind: str | None, pricing_plan_name: str,
    instances: int, storage_gb: float, instance_charge: float, storage_charge: float,
    name_width: int, pricing_width: int,
) -> str:
    total_charge = instance_charge + storage_charge
    kind_col = f"  {kind:<10}" if kind is not None else ""
    pricing_col = f"  {pricing_plan_name:<{pricing_width}}" if pricing_width else ""
    return (
        f"{name:<{name_width}}{kind_col}{pricing_col}"
        f"  {instances:>9}  {storage_gb:>12.2f}"
        f"  {fmt_money(instance_charge):>13}  {fmt_money(storage_charge):>12}  {fmt_money(total_charge):>10}"
    )


# ── Output: table ─────────────────────────────────────────────────────────────

def _print_groups_table(
    group_charges: list[_GroupCharge], name_width: int, pricing_width: int,
) -> None:
    header = _billing_table_header("Group", name_width, None, pricing_width)
    double_rule = _billing_table_rule(name_width, False, pricing_width).replace("-", "═")
    print(header)
    print(double_rule)
    for g in group_charges:
        print(_billing_row(
            g.group_name, None, g.pricing_plan_name,
            g.instances, g.storage_gb, g.instance_charge, g.storage_charge,
            name_width, pricing_width,
        ))


def _print_standalone_table(
    standalone_charges: list[_PlanCharge], name_width: int, pricing_width: int,
) -> None:
    header = _billing_table_header("Plan", name_width, "Plan Type", pricing_width)
    double_rule = _billing_table_rule(name_width, True, pricing_width).replace("-", "═")
    print(header)
    print(double_rule)
    for c in standalone_charges:
        print(_billing_row(
            c.plan_name, _kind_label(c.plan_type), c.pricing_plan_name,
            c.instances, c.storage_gb, c.instance_charge, c.storage_charge,
            name_width, pricing_width,
        ))


def _summary_row(
    label: str, label_width: int,
    instances: int, storage_gb: float, instance_charge: float, storage_charge: float,
) -> str:
    total_charge = instance_charge + storage_charge
    return (
        f"{label:<{label_width}}  {instances:>9}  {storage_gb:>12.2f}"
        f"  {fmt_money(instance_charge):>13}  {fmt_money(storage_charge):>12}  {fmt_money(total_charge):>10}"
    )


def _print_summary_table(
    plan_charges: list[_PlanCharge], pricing_plans: list[_PricingPlan], label_width: int,
) -> None:
    has_pricing = any(c.pricing_plan_name for c in plan_charges)
    label_header = "Pricing Plan" if has_pricing else ""
    header = (
        f"{label_header:<{label_width}}  {'Instances':>9}  {'Storage (GB)':>12}"
        f"  {'Instance Chg':>13}  {'Storage Chg':>12}  {'Total Chg':>10}"
    )
    rule = f"{'-'*label_width}  {'-'*9}  {'-'*12}  {'-'*13}  {'-'*12}  {'-'*10}"
    double_rule = f"{'═'*label_width}  {'═'*9}  {'═'*12}  {'═'*13}  {'═'*12}  {'═'*10}"
    print(header)
    print(double_rule)
    if has_pricing:
        for pp in pricing_plans:
            subset = [c for c in plan_charges if c.pricing_plan_name == pp.name]
            if not subset:
                continue
            print(_summary_row(
                pp.name, label_width,
                sum(c.instances for c in subset),
                round(sum(c.storage_gb for c in subset), 2),
                round(sum(c.instance_charge for c in subset), 2),
                round(sum(c.storage_charge for c in subset), 2),
            ))
        print(rule)
    print(_summary_row(
        "Grand Total", label_width,
        sum(c.instances for c in plan_charges),
        round(sum(c.storage_gb for c in plan_charges), 2),
        round(sum(c.instance_charge for c in plan_charges), 2),
        round(sum(c.storage_charge for c in plan_charges), 2),
    ))


def _print_details_table(
    sections: list[_PlanSection],
    plan_charges: list[_PlanCharge],
    pricing: _PricingConfig,
) -> None:
    named_plans = [pp for pp in pricing.pricing_plans if pp.name]
    if named_plans:
        name_w = max(len("Name"), max(len(pp.name) for pp in named_plans))
        print("Pricing Plans")
        print(f"{'Name':<{name_w}}  {'Rate/Instance':>13}  {'Rate/GB':>8}")
        print(f"{'═'*name_w}  {'═'*13}  {'═'*8}")
        for pp in named_plans:
            print(f"{pp.name:<{name_w}}  {fmt_money(pp.charge_per_instance):>13}  {fmt_money(pp.charge_per_gb):>8}")
        print()

    detail_rows = _build_detail_rows(sections, plan_charges)
    if not detail_rows:
        return

    uuid_len = 36
    grp_w    = max(len("Group"),        max((len(r.group_name)   for r in detail_rows), default=0))
    type_w   = max(len("Plan Type"),    max(len(r.plan_type)     for r in detail_rows))
    plan_w   = max(len("Plan"),         max(len(r.plan_name)     for r in detail_rows))
    pid_w    = uuid_len
    pp_w     = max(len("Pricing Plan"), max((len(r.pricing_plan) for r in detail_rows), default=0))
    wt_w     = max(len("Workload Type"), max(len(r.workload_type) for r in detail_rows))

    def _hdr(label: str, width: int, align: str = "<") -> str:
        return f"{label:{align}{width}}"

    header = (
        f"{_hdr('Group', grp_w)}  {_hdr('Plan Type', type_w)}  {_hdr('Plan', plan_w)}"
        f"  {_hdr('Plan ID', pid_w)}  {_hdr('Pricing Plan', pp_w)}  {_hdr('Workload Type', wt_w)}"
        f"  {'Instances':>9}  {'Storage (GB)':>12}  {'Instance Chg':>13}  {'Storage Chg':>12}  {'Total Chg':>10}"
    )
    rule = (
        f"{'═'*grp_w}  {'═'*type_w}  {'═'*plan_w}  {'═'*pid_w}  {'═'*pp_w}  {'═'*wt_w}"
        f"  {'═'*9}  {'═'*12}  {'═'*13}  {'═'*12}  {'═'*10}"
    )
    print(header)
    print(rule)
    for r in detail_rows:
        print(
            f"{r.group_name:<{grp_w}}  {r.plan_type:<{type_w}}  {r.plan_name:<{plan_w}}"
            f"  {r.plan_id:<{pid_w}}  {r.pricing_plan:<{pp_w}}  {r.workload_type:<{wt_w}}"
            f"  {r.instances:>9}  {r.storage_gb:>12.4f}"
            f"  {fmt_money(r.instance_charge):>13}  {fmt_money(r.storage_charge):>12}  {fmt_money(r.total_charge):>10}"
        )


def _print_table(
    sections: list[_PlanSection],
    plan_charges: list[_PlanCharge],
    group_charges: list[_GroupCharge],
    pricing: _PricingConfig,
    show_details: bool,
) -> None:
    if not sections:
        print("(no workloads)")
        return

    has_pricing = any(c.pricing_plan_name for c in plan_charges)
    pricing_width = 0
    if has_pricing:
        all_names = (
            [c.pricing_plan_name for c in plan_charges]
            + [g.pricing_plan_name for g in group_charges]
        )
        pricing_width = max(len("Pricing Plan"), max(len(n) for n in all_names))

    standalone = [c for c in plan_charges if not c.group_name]
    extra = (2 + pricing_width) if pricing_width else 0

    # Compute a unified left-column width so all three tables have equal total width.
    group_min   = max(len("Group"), max(len(g.group_name) for g in group_charges)) if group_charges else 0
    plan_min    = max(len("Plan"),  max(len(c.plan_name)  for c in standalone))    if standalone   else 0
    summary_min = max(
        len("Grand Total"),
        len("Pricing Plan") if has_pricing else 0,
        *(len(pp.name) for pp in pricing.pricing_plans) if has_pricing else [0],
    )
    L = max(group_min + extra, plan_min + 12 + extra, summary_min)

    if group_charges:
        _print_groups_table(group_charges, L - extra, pricing_width)
        print()
    if standalone:
        _print_standalone_table(standalone, L - 12 - extra, pricing_width)
        print()
    _print_summary_table(plan_charges, pricing.pricing_plans, L)
    print()

    if show_details:
        _print_details_table(sections, plan_charges, pricing)
        print()


# ── Output: CSV ──────────────────────────────────────────────────────────────

def _print_csv(
    sections: list[_PlanSection],
    plan_charges: list[_PlanCharge],
    group_charges: list[_GroupCharge],
    pricing: _PricingConfig,
    show_details: bool,
) -> None:
    w = csv.writer(sys.stdout)
    standalone = [c for c in plan_charges if not c.group_name]

    if group_charges:
        w.writerow(["Groups"])
        w.writerow(["group", "kind", "pricing_plan", "charge_per_instance", "charge_per_gb",
                    "instances", "storage_gb", "instance_charge", "storage_charge", "total_charge"])
        for g in group_charges:
            w.writerow([
                g.group_name, g.plan_type, g.pricing_plan_name,
                g.charge_per_instance, g.charge_per_gb,
                g.instances, g.storage_gb, g.instance_charge, g.storage_charge,
                round(g.total_charge, 2),
            ])
        w.writerow([])

    if standalone:
        w.writerow(["Standalone Plans"])
        w.writerow(["plan", "plan_id", "kind", "pricing_plan", "charge_per_instance", "charge_per_gb",
                    "instances", "storage_gb", "instance_charge", "storage_charge", "total_charge"])
        for c in standalone:
            w.writerow([
                c.plan_name, c.plan_id, c.plan_type, c.pricing_plan_name,
                c.charge_per_instance, c.charge_per_gb,
                c.instances, c.storage_gb, c.instance_charge, c.storage_charge,
                round(c.total_charge, 2),
            ])
        w.writerow([])

    w.writerow(["Summary"])
    has_pricing = any(c.pricing_plan_name for c in plan_charges)
    w.writerow(["pricing_plan" if has_pricing else "",
                "instances", "storage_gb", "instance_charge", "storage_charge", "total_charge"])
    if has_pricing:
        for pp in pricing.pricing_plans:
            subset = [c for c in plan_charges if c.pricing_plan_name == pp.name]
            if not subset:
                continue
            w.writerow([
                pp.name,
                sum(c.instances for c in subset),
                round(sum(c.storage_gb for c in subset), 2),
                round(sum(c.instance_charge for c in subset), 2),
                round(sum(c.storage_charge for c in subset), 2),
                round(sum(c.total_charge for c in subset), 2),
            ])
    w.writerow([
        "Grand Total",
        sum(c.instances for c in plan_charges),
        round(sum(c.storage_gb for c in plan_charges), 2),
        round(sum(c.instance_charge for c in plan_charges), 2),
        round(sum(c.storage_charge for c in plan_charges), 2),
        round(sum(c.total_charge for c in plan_charges), 2),
    ])

    if not show_details:
        return
    w.writerow([])

    w.writerow(["Pricing Plans"])
    w.writerow(["name", "charge_per_instance", "charge_per_gb"])
    for pp in pricing.pricing_plans:
        w.writerow([pp.name, pp.charge_per_instance, pp.charge_per_gb])
    w.writerow([])

    w.writerow(["Pricing Details"])
    w.writerow([
        "group_name", "plan_type", "plan_name", "plan_id", "pricing_plan",
        "workload_type", "instances", "storage_gb",
        "instance_charge", "storage_charge", "total_charge",
    ])
    for r in _build_detail_rows(sections, plan_charges):
        w.writerow([
            r.group_name, r.plan_type, r.plan_name, r.plan_id, r.pricing_plan,
            r.workload_type, r.instances, r.storage_gb,
            r.instance_charge, r.storage_charge, round(r.total_charge, 2),
        ])


# ── Output: JSON ─────────────────────────────────────────────────────────────

def _print_json(
    sections: list[_PlanSection],
    plan_charges: list[_PlanCharge],
    group_charges: list[_GroupCharge],
    pricing: _PricingConfig,
    show_details: bool,
) -> None:
    standalone = [c for c in plan_charges if not c.group_name]

    def _pricing_obj(name: str, per_instance: float, per_gb: float) -> dict[str, Any]:
        return {"name": name, "charge_per_instance": per_instance, "charge_per_gb": per_gb}

    def _pp_summary(pp_name: str) -> dict[str, Any]:
        subset = [c for c in plan_charges if c.pricing_plan_name == pp_name]
        return {
            "instances": sum(c.instances for c in subset),
            "storage_gb": round(sum(c.storage_gb for c in subset), 2),
            "instance_charge": round(sum(c.instance_charge for c in subset), 2),
            "storage_charge": round(sum(c.storage_charge for c in subset), 2),
            "total_charge": round(sum(c.total_charge for c in subset), 2),
        }

    out: dict[str, Any] = {
        "pricing_plans": [
            _pricing_obj(pp.name, pp.charge_per_instance, pp.charge_per_gb)
            for pp in pricing.pricing_plans
        ],
        "groups": [
            {
                "group_name": g.group_name,
                "plan_type": g.plan_type,
                "pricing_plan": _pricing_obj(
                    g.pricing_plan_name, g.charge_per_instance, g.charge_per_gb
                ),
                "instances": g.instances,
                "storage_gb": g.storage_gb,
                "instance_charge": g.instance_charge,
                "storage_charge": g.storage_charge,
                "total_charge": round(g.total_charge, 2),
            }
            for g in group_charges
        ],
        "standalone_plans": [
            {
                "plan_name": c.plan_name,
                "plan_type": c.plan_type,
                "plan_id": c.plan_id,
                "pricing_plan": _pricing_obj(
                    c.pricing_plan_name, c.charge_per_instance, c.charge_per_gb
                ),
                "instances": c.instances,
                "storage_gb": c.storage_gb,
                "instance_charge": c.instance_charge,
                "storage_charge": c.storage_charge,
                "total_charge": round(c.total_charge, 2),
            }
            for c in standalone
        ],
        "summary": {
            "by_pricing_plan": [
                {"pricing_plan": pp.name, **_pp_summary(pp.name)}
                for pp in pricing.pricing_plans
                if any(c.pricing_plan_name == pp.name for c in plan_charges)
            ],
            "grand_total": {
                "instances": sum(c.instances for c in plan_charges),
                "storage_gb": round(sum(c.storage_gb for c in plan_charges), 2),
                "instance_charge": round(sum(c.instance_charge for c in plan_charges), 2),
                "storage_charge": round(sum(c.storage_charge for c in plan_charges), 2),
                "total_charge": round(sum(c.total_charge for c in plan_charges), 2),
            },
        },
    }

    if show_details:
        sections_by_id = {s.plan_id: s for s in sections}

        def _by_type_list(section: _PlanSection, charge: _PlanCharge) -> list[dict[str, Any]]:
            result = []
            for row in section.rows:
                storage_gb = row.storage_bytes / _BYTES_PER_GB
                inst_charge = round(row.count * charge.charge_per_instance, 2)
                stor_charge = round(storage_gb * charge.charge_per_gb, 2)
                result.append({
                    "workload_type": row.type_label,
                    "instances": row.count,
                    "storage_gb": round(storage_gb, 4),
                    "instance_charge": inst_charge,
                    "storage_charge": stor_charge,
                    "total_charge": round(inst_charge + stor_charge, 2),
                })
            return result

        def _plan_detail_obj(section: _PlanSection, charge: _PlanCharge) -> dict[str, Any]:
            return {
                "plan_name": section.plan_name,
                "plan_type": section.plan_type,
                "plan_id": section.plan_id,
                "instances": charge.instances,
                "storage_gb": charge.storage_gb,
                "instance_charge": charge.instance_charge,
                "storage_charge": charge.storage_charge,
                "total_charge": round(charge.total_charge, 2),
                "by_type": _by_type_list(section, charge),
            }

        for group_dict, g in zip(out["groups"], group_charges):
            group_plan_charges = [c for c in plan_charges if c.group_name == g.group_name]
            group_dict["plans"] = [
                _plan_detail_obj(sections_by_id[c.plan_id], c)
                for c in group_plan_charges
                if c.plan_id in sections_by_id
            ]

        for plan_dict, c in zip(out["standalone_plans"], standalone):
            if c.plan_id in sections_by_id:
                plan_dict["by_type"] = _by_type_list(sections_by_id[c.plan_id], c)

    print(json.dumps(out, indent=2))


# ── Output: XLSX ──────────────────────────────────────────────────────────────

_FMT_CURRENCY = "#,##0.00"
_FMT_GB2 = "#,##0.00"
_FMT_GB4 = "#,##0.0000"
_BOLD = openpyxl.styles.Font(bold=True)


def _xlsx_header(ws: _Worksheet, cols: list[str]) -> None:
    ws.append(cols)
    for cell in ws[1]:
        cell.font = _BOLD


def _xlsx_fmt(
    ws: _Worksheet,
    row_idx: int,
    col_indices: list[int],
    fmt: str,
) -> None:
    for col in col_indices:
        ws.cell(row=row_idx, column=col).number_format = fmt


def _xlsx_autofit(ws: _Worksheet) -> None:
    for idx, col_cells in enumerate(ws.columns):
        max_len = max(
            (len(str(cell.value)) for cell in col_cells if cell.value is not None),
            default=0,
        )
        ws.column_dimensions[_get_col_letter(idx + 1)].width = max_len + 2


def _write_xlsx(
    sections: list[_PlanSection],
    plan_charges: list[_PlanCharge],
    group_charges: list[_GroupCharge],
    pricing: _PricingConfig,
    show_details: bool,
    output_file: str,
) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]

    standalone = [c for c in plan_charges if not c.group_name]

    # ── Groups ────────────────────────────────────────────────────────────────
    if group_charges:
        ws = wb.create_sheet("Groups")
        _xlsx_header(ws, [
            "group", "kind", "pricing_plan", "charge_per_instance", "charge_per_gb",
            "instances", "storage_gb", "instance_charge", "storage_charge", "total_charge",
        ])
        for g in group_charges:
            ws.append([
                g.group_name, g.plan_type, g.pricing_plan_name,
                g.charge_per_instance, g.charge_per_gb,
                g.instances, g.storage_gb, g.instance_charge, g.storage_charge,
                round(g.total_charge, 2),
            ])
        # cols: 4=charge_per_instance, 5=charge_per_gb, 7=storage_gb,
        #       8=instance_charge, 9=storage_charge, 10=total_charge
        for row_idx in range(2, ws.max_row + 1):
            _xlsx_fmt(ws, row_idx, [4, 5, 8, 9, 10], _FMT_CURRENCY)
            _xlsx_fmt(ws, row_idx, [7], _FMT_GB2)
        _xlsx_autofit(ws)

    # ── Standalone Plans ──────────────────────────────────────────────────────
    if standalone:
        ws = wb.create_sheet("Standalone Plans")
        _xlsx_header(ws, [
            "plan", "plan_id", "kind", "pricing_plan", "charge_per_instance", "charge_per_gb",
            "instances", "storage_gb", "instance_charge", "storage_charge", "total_charge",
        ])
        for c in standalone:
            ws.append([
                c.plan_name, c.plan_id, c.plan_type, c.pricing_plan_name,
                c.charge_per_instance, c.charge_per_gb,
                c.instances, c.storage_gb, c.instance_charge, c.storage_charge,
                round(c.total_charge, 2),
            ])
        # cols: 5=charge_per_instance, 6=charge_per_gb, 8=storage_gb,
        #       9=instance_charge, 10=storage_charge, 11=total_charge
        for row_idx in range(2, ws.max_row + 1):
            _xlsx_fmt(ws, row_idx, [5, 6, 9, 10, 11], _FMT_CURRENCY)
            _xlsx_fmt(ws, row_idx, [8], _FMT_GB2)
        _xlsx_autofit(ws)

    # ── Summary ───────────────────────────────────────────────────────────────
    ws = wb.create_sheet("Summary")
    has_pricing = any(c.pricing_plan_name for c in plan_charges)
    _xlsx_header(ws, [
        "pricing_plan" if has_pricing else "",
        "instances", "storage_gb", "instance_charge", "storage_charge", "total_charge",
    ])
    if has_pricing:
        for pp in pricing.pricing_plans:
            subset = [c for c in plan_charges if c.pricing_plan_name == pp.name]
            if not subset:
                continue
            ws.append([
                pp.name,
                sum(c.instances for c in subset),
                round(sum(c.storage_gb for c in subset), 2),
                round(sum(c.instance_charge for c in subset), 2),
                round(sum(c.storage_charge for c in subset), 2),
                round(sum(c.total_charge for c in subset), 2),
            ])
    ws.append([
        "Grand Total",
        sum(c.instances for c in plan_charges),
        round(sum(c.storage_gb for c in plan_charges), 2),
        round(sum(c.instance_charge for c in plan_charges), 2),
        round(sum(c.storage_charge for c in plan_charges), 2),
        round(sum(c.total_charge for c in plan_charges), 2),
    ])
    # cols: 3=storage_gb, 4=instance_charge, 5=storage_charge, 6=total_charge
    for row_idx in range(2, ws.max_row + 1):
        _xlsx_fmt(ws, row_idx, [4, 5, 6], _FMT_CURRENCY)
        _xlsx_fmt(ws, row_idx, [3], _FMT_GB2)
    _xlsx_autofit(ws)

    if not show_details:
        wb.save(output_file)
        print(f"Saved: {output_file}", file=sys.stderr)
        return

    # ── Pricing Plans ─────────────────────────────────────────────────────────
    named_plans = [pp for pp in pricing.pricing_plans if pp.name]
    if named_plans:
        ws = wb.create_sheet("Pricing Plans")
        _xlsx_header(ws, ["name", "charge_per_instance", "charge_per_gb"])
        for pp in named_plans:
            ws.append([pp.name, pp.charge_per_instance, pp.charge_per_gb])
        for row_idx in range(2, ws.max_row + 1):
            _xlsx_fmt(ws, row_idx, [2, 3], _FMT_CURRENCY)
        _xlsx_autofit(ws)

    # ── Details ───────────────────────────────────────────────────────────────
    detail_rows = _build_detail_rows(sections, plan_charges)
    if detail_rows:
        ws = wb.create_sheet("Details")
        _xlsx_header(ws, [
            "group_name", "plan_type", "plan_name", "plan_id", "pricing_plan",
            "workload_type", "instances", "storage_gb",
            "instance_charge", "storage_charge", "total_charge",
        ])
        for r in detail_rows:
            ws.append([
                r.group_name, r.plan_type, r.plan_name, r.plan_id, r.pricing_plan,
                r.workload_type, r.instances, r.storage_gb,
                r.instance_charge, r.storage_charge, round(r.total_charge, 2),
            ])
        # cols: 8=storage_gb, 9=instance_charge, 10=storage_charge, 11=total_charge
        for row_idx in range(2, ws.max_row + 1):
            _xlsx_fmt(ws, row_idx, [9, 10, 11], _FMT_CURRENCY)
            _xlsx_fmt(ws, row_idx, [8], _FMT_GB4)
        _xlsx_autofit(ws)

    wb.save(output_file)
    print(f"Saved: {output_file}", file=sys.stderr)


# ── Entry point ──────────────────────────────────────────────────────────────

async def run(
    output_format: str,
    pricing: _PricingConfig,
    show_details: bool,
    concurrency: int,
    output_file: str | None,
) -> None:
    print("Collecting data...", file=sys.stderr)
    async with make_client() as apm:
        sections = await _scan_billing(apm, concurrency=concurrency)

    plan_charges = _compute_plan_charges(sections, pricing)
    group_charges = _compute_group_charges(plan_charges, pricing.groups)

    if output_format == "csv":
        _print_csv(sections, plan_charges, group_charges, pricing, show_details)
    elif output_format == "json":
        _print_json(sections, plan_charges, group_charges, pricing, show_details)
    elif output_format == "xlsx":
        assert output_file is not None
        _write_xlsx(sections, plan_charges, group_charges, pricing, show_details, output_file)
    else:
        _print_table(sections, plan_charges, group_charges, pricing, show_details)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--pricing", metavar="FILE",
        help=(
            "Path to a YAML pricing config file. Defines named pricing plans, optional groups "
            "that combine multiple APM plans into a single billing row, and optional per-plan "
            "assignments. Generate a template with --dump-pricing-template. "
            "Cannot be combined with --charge-per-instance or --charge-per-gb."
        ),
    )
    parser.add_argument(
        "--dump-pricing-template", dest="dump_pricing_template", action="store_true",
        help="Print a commented YAML pricing config template to stdout and exit.",
    )
    parser.add_argument(
        "--charge-per-instance", dest="charge_per_instance", type=float, default=0.0,
        help=(
            "Monetary charge per workload instance, applied uniformly to all plans (default: 0). "
            "Cannot be combined with --pricing."
        ),
    )
    parser.add_argument(
        "--charge-per-gb", dest="charge_per_gb", type=float, default=0.0,
        help=(
            "Monetary charge per GB of storage usage, applied uniformly to all plans (default: 0). "
            "Cannot be combined with --pricing."
        ),
    )
    parser.add_argument(
        "--details", dest="details", action="store_true",
        help=(
            "Print per-workload-type detail for every plan, including plan ID, "
            "group assignment, and applied pricing plan (default: totals only)."
        ),
    )
    parser.add_argument(
        "--concurrency", type=int, default=_DEFAULT_CONCURRENCY, metavar="N",
        help=f"Max concurrent API calls in flight while fetching workloads (default: {_DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "-o", "--output", dest="output", choices=["table", "csv", "json", "xlsx"], default="table",
        help="Output format: table, csv, json, or xlsx (default: table).",
    )
    parser.add_argument(
        "--output-file", dest="output_file", metavar="FILE",
        help="Path for the output .xlsx file. Required when -o xlsx.",
    )
    args = parser.parse_args()

    if args.output == "xlsx" and not args.output_file:
        parser.error("-o xlsx requires --output-file FILE")

    if args.dump_pricing_template:
        _dump_pricing_template()
        sys.exit(0)

    if args.pricing and (args.charge_per_instance or args.charge_per_gb):
        parser.error("--pricing cannot be combined with --charge-per-instance or --charge-per-gb")

    if args.pricing:
        try:
            pricing = _load_pricing_yaml(args.pricing)
        except (OSError, ValueError, KeyError) as e:
            print(f"Error loading pricing file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        pricing = _PricingConfig(pricing_plans=[
            _PricingPlan(
                name="",
                charge_per_instance=args.charge_per_instance,
                charge_per_gb=args.charge_per_gb,
            ),
        ])

    run_main(run(args.output, pricing, args.details, args.concurrency, args.output_file))


if __name__ == "__main__":
    main()
