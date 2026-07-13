"""Unit tests for billing_report.py charge computation and details-builder functions."""
from __future__ import annotations

import pytest
from billing_report import (
    _BYTES_PER_GB,
    _aggregate,
    _build_details_view,
    _build_group_plan_rows,
    _build_group_server_rows,
    _build_group_workload_rows,
    _build_plan_type_rows,
    _build_server_type_rows,
    _charge_fields,
    _compute_group_charges,
    _compute_plan_charges,
    _compute_server_charges,
    _GroupServerRow,
    _GroupSpec,
    _PricingConfig,
    fmt_money,
)

from tests.unit.examples._billing_fixtures import (
    STANDARD_RATE,
    make_default_config,
    make_group_charge,
    make_group_config_two,
    make_plan_charge,
    make_plan_section,
    make_server_charge,
    make_server_stat,
    make_two_plan_config,
)

# 111 MiB: a byte count whose GB value (0.1083984375) is non-round at both 2 and
# 4 decimals, so the two rounding depths are distinguishable (0.11 vs 0.1084).
_111_MIB = 111 * 2**20

# ── fmt_money ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "amount,expected",
    [
        (0.0, "$0.00"),
        (0.1, "$0.10"),
        (1234.5, "$1,234.50"),
        (1_000_000.0, "$1,000,000.00"),
    ],
)
def test_fmt_money(amount: float, expected: str) -> None:
    assert fmt_money(amount) == expected


# ── _charge_fields ─────────────────────────────────────────────────────────────


def test_charge_fields_non_round_case() -> None:
    storage_gb, instance_charge, storage_charge = _charge_fields(
        3, _111_MIB, 5.0, 10.0, gb_decimals=2
    )
    assert storage_gb == 0.11
    assert instance_charge == 15.0
    # Charged from the unrounded GB value: 0.1083984375 * $10.00 → $1.08
    assert storage_charge == 1.08


def test_charge_fields_whole_gb() -> None:
    storage_gb, instance_charge, storage_charge = _charge_fields(
        2, 3 * _BYTES_PER_GB, 10.0, 0.20, gb_decimals=2
    )
    assert storage_gb == 3.0
    assert instance_charge == 20.0
    assert storage_charge == 0.60


def test_charge_fields_4dp_decimals() -> None:
    storage_gb, _, _ = _charge_fields(1, _111_MIB, 0.0, 0.0, gb_decimals=4)
    assert storage_gb == 0.1084


# ── _compute_plan_charges ──────────────────────────────────────────────────────


def test_compute_plan_charges_merges_same_plan_across_groups() -> None:
    # Two sections with the same plan_id but different group sets → merged
    s1 = make_plan_section(plan_name="Plan A", group_names=("GroupA",), count=2, storage_bytes=1 * _BYTES_PER_GB)
    s2 = make_plan_section(plan_name="Plan A", group_names=("GroupB",), count=3, storage_bytes=2 * _BYTES_PER_GB)
    charges = _compute_plan_charges([s1, s2], make_default_config())
    assert len(charges) == 1
    c = charges[0]
    assert c.plan_name == "Plan A"
    assert c.instances == 5
    assert c.storage_gb == 3.0
    assert c.instance_charge == 25.0
    assert c.storage_charge == 0.60
    assert c.total_charge == 25.60


def test_compute_plan_charges_sorted_by_plan_name() -> None:
    s1 = make_plan_section(plan_name="Zebra Plan", plan_id="plan-z")
    s2 = make_plan_section(plan_name="Alpha Plan", plan_id="plan-a")
    charges = _compute_plan_charges([s1, s2], make_default_config())
    assert charges[0].plan_name == "Alpha Plan"
    assert charges[1].plan_name == "Zebra Plan"


def test_compute_plan_charges_uses_assignment() -> None:
    section = make_plan_section(plan_name="Plan A", plan_id="plan-001", storage_bytes=_BYTES_PER_GB)
    charges = _compute_plan_charges([section], make_two_plan_config())
    assert charges[0].pricing_plan_name == "Premium"
    assert charges[0].charge_per_instance == 10.0


def test_compute_plan_charges_uses_fallback_when_no_assignment() -> None:
    section = make_plan_section(plan_name="Plan B", plan_id="plan-999")
    charges = _compute_plan_charges([section], make_two_plan_config())
    assert charges[0].pricing_plan_name == "Standard"


# ── _compute_group_charges ─────────────────────────────────────────────────────


def test_compute_group_charges_counts_workloads() -> None:
    cfg = make_group_config_two()
    section = make_plan_section(group_names=("GroupA",), count=2, storage_bytes=1 * _BYTES_PER_GB)
    charges = _compute_group_charges([section], cfg)
    assert len(charges) == 1  # GroupB has no members
    c = charges[0]
    assert c.group_name == "GroupA"
    assert c.instances == 2
    assert c.instance_charge == 10.0
    assert c.pricing_plan_name == "Standard"


def test_compute_group_charges_overlap_double_counts() -> None:
    cfg = make_group_config_two()
    # Both groups contain plan-001 — same section appears in both
    section = make_plan_section(group_names=("GroupA", "GroupB"), count=3, storage_bytes=_BYTES_PER_GB)
    charges = _compute_group_charges([section], cfg)
    assert len(charges) == 2
    total_instances = sum(c.instances for c in charges)
    assert total_instances == 6  # overlap: 3 counted twice


def test_compute_group_charges_empty_group_omitted() -> None:
    cfg = make_group_config_two()
    # Section only belongs to GroupA — GroupB has no members
    section = make_plan_section(group_names=("GroupA",))
    charges = _compute_group_charges([section], cfg)
    assert all(c.group_name != "GroupB" for c in charges)


def test_compute_group_charges_mixed_plan_type() -> None:
    cfg = _PricingConfig(
        pricing_plans=[STANDARD_RATE],
        groups=[_GroupSpec("GroupA", "Standard", plan_ids=["plan-001", "plan-002"])],
    )
    s1 = make_plan_section(plan_name="P1", plan_id="plan-001", group_names=("GroupA",), plan_type="Protection Plan")
    s2 = make_plan_section(plan_name="P2", plan_id="plan-002", group_names=("GroupA",), plan_type="Retirement Plan")
    charges = _compute_group_charges([s1, s2], cfg)
    assert charges[0].plan_type == "Mixed"


# ── _compute_server_charges ────────────────────────────────────────────────────


def test_compute_server_charges_basic() -> None:
    stats = [
        make_server_stat(namespace="ns-001", count=3, storage_bytes=2 * _BYTES_PER_GB),
        make_server_stat(namespace="ns-002", count=1, storage_bytes=_BYTES_PER_GB),
    ]
    server_names = {"ns-001": "Server A", "ns-002": "Server B"}
    charges = _compute_server_charges(stats, make_default_config(), server_names)
    assert len(charges) == 2
    # sorted by server_name
    assert charges[0].server_name == "Server A"
    assert charges[0].instances == 3
    assert charges[0].storage_gb == 2.0
    assert charges[0].instance_charge == 15.0


def test_compute_server_charges_unknown_name_fallback() -> None:
    stats = [make_server_stat(namespace="")]
    charges = _compute_server_charges(stats, make_default_config(), {})
    assert charges[0].server_name == "(unknown)"


def test_compute_server_charges_merges_same_namespace() -> None:
    stats = [
        make_server_stat(namespace="ns-001", count=2, storage_bytes=_BYTES_PER_GB),
        make_server_stat(namespace="ns-001", count=3, storage_bytes=_BYTES_PER_GB),
    ]
    charges = _compute_server_charges(stats, make_default_config(), {"ns-001": "Server A"})
    assert len(charges) == 1
    assert charges[0].instances == 5


# ── _aggregate ─────────────────────────────────────────────────────────────────


def test_aggregate_sums_all_fields() -> None:
    c1 = make_plan_charge(instances=3, storage_gb=1.5, instance_charge=15.0, storage_charge=0.30)
    c2 = make_plan_charge(instances=2, storage_gb=0.5, instance_charge=10.0, storage_charge=0.10)
    totals = _aggregate([c1, c2])
    assert totals.instances == 5
    assert totals.storage_gb == 2.0
    assert totals.instance_charge == 25.0
    assert totals.storage_charge == 0.40
    assert totals.total_charge == 25.40


def test_aggregate_empty_list() -> None:
    totals = _aggregate([])
    assert totals.instances == 0
    assert totals.total_charge == 0.0


# ── _build_group_workload_rows ─────────────────────────────────────────────────


def test_build_group_workload_rows_uses_4dp_storage() -> None:
    group_charge = make_group_charge(group_name="GroupA")
    section = make_plan_section(group_names=("GroupA",), count=2, storage_bytes=_111_MIB)
    result = _build_group_workload_rows([section], [group_charge])
    rows = result["GroupA"]
    assert len(rows) == 1
    row = rows[0]
    assert row.storage_gb == 0.1084  # gb_decimals=4 for detail rows
    assert row.instance_charge == 20.0  # 2 instances * $10.00 (Premium)
    assert row.storage_charge == 0.03  # 0.1083984375 GB * $0.30
    assert row.workload_type == "VM"


def test_build_group_workload_rows_merges_types_across_sections() -> None:
    group_charge = make_group_charge(group_name="GroupA")
    s1 = make_plan_section(plan_id="plan-001", group_names=("GroupA",), count=2, storage_bytes=_BYTES_PER_GB)
    s2 = make_plan_section(plan_id="plan-002", group_names=("GroupA",), count=3, storage_bytes=_BYTES_PER_GB)
    result = _build_group_workload_rows([s1, s2], [group_charge])
    rows = result["GroupA"]
    assert len(rows) == 1  # both sections carry VM rows → one merged VM row
    assert rows[0].instances == 5
    assert rows[0].storage_gb == 2.0


# ── _build_plan_type_rows ──────────────────────────────────────────────────────


def test_build_plan_type_rows_filters_by_plan_charges() -> None:
    s_in = make_plan_section(plan_name="P1", plan_id="plan-in", type_label="VM", count=2, storage_bytes=_BYTES_PER_GB)
    s_out = make_plan_section(plan_name="P2", plan_id="plan-out", type_label="PC", type_order=2, storage_bytes=_BYTES_PER_GB)

    plan_charge = make_plan_charge(
        plan_name="P1", plan_id="plan-in",
        instances=2, storage_gb=1.0, instance_charge=10.0, storage_charge=0.20,
    )

    rows = _build_plan_type_rows([s_in, s_out], [plan_charge])
    # Only plan-in's rows appear
    assert all(r.plan_id == "plan-in" for r in rows)
    assert all(r.workload_type != "PC" for r in rows)
    assert any(r.workload_type == "VM" for r in rows)


# ── _build_group_server_rows ───────────────────────────────────────────────────


def test_build_group_server_rows_distributes_per_group_sorted_by_server() -> None:
    stats = [
        make_server_stat(namespace="ns-002", group_names=("GroupA", "GroupB"), count=1, storage_bytes=_111_MIB),
        make_server_stat(namespace="ns-001", group_names=("GroupA",), count=2, storage_bytes=_BYTES_PER_GB),
        make_server_stat(namespace="ns-001", group_names=(), count=5, storage_bytes=0),  # ungrouped: excluded
    ]
    charges = [make_group_charge(group_name="GroupA"), make_group_charge(group_name="GroupB")]
    names = {"ns-001": "apm-server-01", "ns-002": "apm-server-02"}

    rows = _build_group_server_rows(stats, charges, names)

    assert [(r.server_name, r.instances, r.storage_gb) for r in rows["GroupA"]] == [
        ("apm-server-01", 2, 1.0),
        ("apm-server-02", 1, 0.11),  # distribution rows round storage to 2 decimals
    ]
    assert rows["GroupB"] == [
        _GroupServerRow(
            group_name="GroupB", server_name="apm-server-02", namespace="ns-002",
            instances=1, storage_gb=0.11,
        )
    ]


def test_build_group_server_rows_skips_groups_without_charge_row() -> None:
    stats = [make_server_stat(namespace="ns-001", group_names=("GroupC",))]
    rows = _build_group_server_rows(stats, [make_group_charge(group_name="GroupA")], {})
    assert rows == {"GroupA": []}


# ── _build_group_plan_rows ─────────────────────────────────────────────────────


def test_build_group_plan_rows_merges_same_plan_across_group_sets() -> None:
    # plan-001 is visible to GroupA through two different group sets → one merged row
    s1 = make_plan_section(plan_id="plan-001", group_names=("GroupA",), count=2, storage_bytes=_BYTES_PER_GB)
    s2 = make_plan_section(plan_id="plan-001", group_names=("GroupA", "GroupB"), count=1, storage_bytes=_BYTES_PER_GB)
    s3 = make_plan_section(
        plan_name="Compliance Retention", plan_type="Retirement Plan", plan_id="plan-002",
        group_names=("GroupA",), count=1,
    )
    s4 = make_plan_section(plan_id="plan-003", group_names=("GroupC",))  # no charge row: excluded
    charges = [make_group_charge(group_name="GroupA"), make_group_charge(group_name="GroupB")]

    rows = _build_group_plan_rows([s1, s2, s3, s4], charges)

    assert set(rows) == {"GroupA", "GroupB"}

    assert len(rows["GroupA"]) == 2
    by_plan = {r.plan_id: r for r in rows["GroupA"]}
    assert by_plan["plan-001"].plan_name == "Daily Backup"
    assert by_plan["plan-001"].plan_type == "Protection Plan"
    assert by_plan["plan-001"].instances == 3
    assert by_plan["plan-001"].storage_gb == 2.0
    assert by_plan["plan-002"].plan_type == "Retirement Plan"
    assert [(r.plan_id, r.instances, r.storage_gb) for r in rows["GroupB"]] == [("plan-001", 1, 1.0)]


# ── _build_server_type_rows ────────────────────────────────────────────────────


def test_build_server_type_rows_orders_by_charge_then_type_and_rates_per_server() -> None:
    stats = [
        make_server_stat(namespace="ns-002", type_label="VM", type_order=0, count=1),
        make_server_stat(namespace="ns-001", type_label="PC", type_order=2, count=1),
        make_server_stat(namespace="ns-001", type_label="VM", type_order=0, count=2, storage_bytes=_111_MIB),
        make_server_stat(namespace="ns-003", type_label="VM", type_order=0, count=9),  # not charged: excluded
    ]
    charges = [
        make_server_charge(server_name="apm-server-01", namespace="ns-001"),
        make_server_charge(
            server_name="apm-server-02", namespace="ns-002",
            pricing_plan_name="Premium", charge_per_instance=10.0, charge_per_gb=0.30,
        ),
    ]

    rows = _build_server_type_rows(stats, charges)

    assert [(r.namespace, r.workload_type) for r in rows] == [
        ("ns-001", "VM"), ("ns-001", "PC"), ("ns-002", "VM"),
    ]
    vm = rows[0]
    assert vm.server_name == "apm-server-01"
    assert vm.pricing_plan == "Standard"
    assert vm.instances == 2
    assert vm.storage_gb == 0.1084  # detail rows round storage to 4 decimals
    assert vm.instance_charge == 10.0
    assert vm.storage_charge == 0.02  # 0.1083984375 GB * $0.20
    assert rows[2].instance_charge == 10.0  # 1 instance * $10.00 (Premium)


# ── _build_details_view ────────────────────────────────────────────────────────


def test_build_details_view_assembles_all_row_sets() -> None:
    section = make_plan_section(group_names=("GroupA",), count=2, storage_bytes=_BYTES_PER_GB)
    stat = make_server_stat(group_names=("GroupA",), count=2, storage_bytes=_BYTES_PER_GB)
    plan_charge = make_plan_charge(instances=2, storage_gb=1.0, instance_charge=10.0, storage_charge=0.20)
    server_charge = make_server_charge()
    group_charge = make_group_charge(group_name="GroupA")

    view = _build_details_view(
        [section], [stat], [plan_charge], [server_charge], [group_charge],
        {"ns-001": "apm-server-01"},
    )

    assert view.server_rows_by_group["GroupA"][0].server_name == "apm-server-01"
    assert view.plan_rows_by_group["GroupA"][0].plan_id == "plan-001"
    assert view.workload_rows_by_group["GroupA"][0].workload_type == "VM"
    assert view.server_type_rows[0].namespace == "ns-001"
    assert view.plan_type_rows[0].plan_id == "plan-001"
