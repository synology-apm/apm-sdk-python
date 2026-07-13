"""Unit tests for _PricingConfig and _load_pricing_yaml in billing_report.py."""
from __future__ import annotations

from pathlib import Path

import pytest
from billing_report import (
    _PRICING_TEMPLATE,
    _GroupSpec,
    _load_pricing_yaml,
    _PricingConfig,
    _PricingPlan,
)

from tests.unit.examples._billing_fixtures import (
    PREMIUM_RATE,
    STANDARD_RATE,
    make_resolved_config,
)


@pytest.fixture
def cfg() -> _PricingConfig:
    """Shared config: two pricing plans, two groups, one plan assignment, server resolved."""
    return make_resolved_config()


# ── groups_for ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("plan_id,namespace,expected", [
    # plan-001 matches GroupA via plan AND GroupB via server ns-001
    ("plan-001", "ns-001", ("GroupA", "GroupB")),
    # plan-003 matches GroupB via plan AND ns-001 matches GroupB via server — deduped to one
    ("plan-003", "ns-001", ("GroupB",)),
    # no match in any group
    ("plan-999", "ns-999", ()),
])
def test_groups_for(
    cfg: _PricingConfig,
    plan_id: str,
    namespace: str,
    expected: tuple[str, ...],
) -> None:
    assert cfg.groups_for(plan_id, namespace) == expected


# ── pricing_plan_for_plan ─────────────────────────────────────────────────────

@pytest.mark.parametrize("plan_id,expected_name", [
    ("plan-001", "Premium"),   # explicit assignment overrides fallback
    ("plan-002", "Standard"),  # not in assignments → fallback
    ("plan-999", "Standard"),  # unknown plan → fallback
])
def test_pricing_plan_for_plan(
    cfg: _PricingConfig,
    plan_id: str,
    expected_name: str,
) -> None:
    result = cfg.pricing_plan_for_plan(plan_id)
    assert result.name == expected_name


# ── pricing_plan_for_server ───────────────────────────────────────────────────

def test_pricing_plan_for_server_fallback(cfg: _PricingConfig) -> None:
    # server_assignments={} → always fallback (pricing_plans[0] = Standard)
    result = cfg.pricing_plan_for_server("ns-001")
    assert result == _PricingPlan("Standard", 5.0, 0.20)


def test_pricing_plan_for_server_assignment() -> None:
    # A config that maps server-id-001 → Premium in server_assignments
    config = _PricingConfig(
        pricing_plans=[STANDARD_RATE, PREMIUM_RATE],
        groups=[
            _GroupSpec(
                name="GroupB",
                pricing_plan_name="Premium",
                plan_ids=[],
                backup_server_ids=["server-id-001"],
            ),
        ],
        assignments={},
        server_assignments={"server-id-001": "Premium"},
    )
    config.resolve_server_ids({"server-id-001": "ns-001"})
    result = config.pricing_plan_for_server("ns-001")
    assert result == _PricingPlan("Premium", 10.0, 0.30)


# ── pricing_plan_for_group ────────────────────────────────────────────────────

@pytest.mark.parametrize("group_name,expected_name", [
    ("GroupA", "Standard"),
    ("GroupB", "Premium"),
])
def test_pricing_plan_for_group(
    cfg: _PricingConfig,
    group_name: str,
    expected_name: str,
) -> None:
    result = cfg.pricing_plan_for_group(group_name)
    assert result.name == expected_name


# ── resolve_server_ids ────────────────────────────────────────────────────────

def test_resolve_server_ids_unknown() -> None:
    # server-id-001 (a GroupB member) and server-id-002 (a rate assignment) are
    # both absent from the supplied map → reported sorted
    config = _PricingConfig(
        pricing_plans=[STANDARD_RATE],
        groups=[
            _GroupSpec(
                name="GroupB",
                pricing_plan_name="Standard",
                plan_ids=[],
                backup_server_ids=["server-id-001"],
            ),
        ],
        assignments={},
        server_assignments={"server-id-002": "Standard"},
    )
    unknown = config.resolve_server_ids({"other-id": "ns-other"})
    assert unknown == ["server-id-001", "server-id-002"]


# ── configured_plan_ids / configured_namespaces ───────────────────────────────

def test_configured_plan_ids(cfg: _PricingConfig) -> None:
    # Union of assignments keys and group plan_ids across all groups
    assert cfg.configured_plan_ids == {"plan-001", "plan-002", "plan-003"}


def test_configured_namespaces(cfg: _PricingConfig) -> None:
    # server-id-001 was resolved to ns-001 via resolve_server_ids
    assert "ns-001" in cfg.configured_namespaces


# ── _load_pricing_yaml — valid inputs ─────────────────────────────────────────

def test_load_pricing_yaml_valid(tmp_path: Path) -> None:
    yaml_content = (
        "pricing_plans:\n"
        "  - name: Standard\n"
        "    charge_per_instance: 5.0\n"
        "    charge_per_gb: 0.1\n"
        "groups:\n"
        "  - name: Contoso\n"
        "    pricing_plan: Standard\n"
        "    plans:\n"
        "      - plan-001\n"
        "plans:\n"
        "  plan-001: Standard\n"
    )
    p = tmp_path / "pricing.yaml"
    p.write_text(yaml_content)
    config = _load_pricing_yaml(str(p))
    assert config.pricing_plans[0] == _PricingPlan("Standard", 5.0, 0.1)
    assert config.groups[0].name == "Contoso"
    assert config.assignments == {"plan-001": "Standard"}


def test_load_pricing_yaml_backup_servers_and_group_server_members(tmp_path: Path) -> None:
    yaml_content = (
        "pricing_plans:\n"
        "  - name: Standard\n"
        "    charge_per_instance: 5.0\n"
        "  - name: Premium\n"
        "    charge_per_instance: 10.0\n"
        "groups:\n"
        "  - name: Contoso\n"
        "    pricing_plan: Premium\n"
        "    backup_servers:\n"
        "      - 123e4567-e89b-12d3-a456-426614174010\n"
        "backup_servers:\n"
        "  123e4567-e89b-12d3-a456-426614174011: Premium\n"
    )
    p = tmp_path / "pricing.yaml"
    p.write_text(yaml_content)
    config = _load_pricing_yaml(str(p))
    assert config.groups[0].backup_server_ids == ["123e4567-e89b-12d3-a456-426614174010"]
    assert config.groups[0].plan_ids == []  # plans membership omitted
    assert config.server_assignments == {"123e4567-e89b-12d3-a456-426614174011": "Premium"}
    # charge_per_gb omitted → defaults to 0
    assert config.pricing_plans[1] == _PricingPlan("Premium", 10.0, 0.0)


def test_load_pricing_yaml_template_drift_guard(tmp_path: Path) -> None:
    # Ensure the bundled template parses without error and has the expected shape
    p = tmp_path / "template.yaml"
    p.write_text(_PRICING_TEMPLATE)
    config = _load_pricing_yaml(str(p))
    assert len(config.pricing_plans) >= 1
    assert config.pricing_plans[0].name == "Standard"
    assert len(config.groups) == 1


# ── _load_pricing_yaml — error cases ─────────────────────────────────────────

_GROUPS_PREFIX = (
    "pricing_plans:\n"
    "  - name: Standard\n"
    "groups:\n"
    "  - name: Contoso\n"
    "    pricing_plan: Standard\n"
)

@pytest.mark.parametrize("yaml_content,match", [
    (
        "pricing_plans: []\n",
        "at least one entry",
    ),
    (
        "pricing_plans:\n  - name: Standard\n  - name: Standard\n",
        "duplicate pricing plan",
    ),
    (
        (
            "pricing_plans:\n"
            "  - name: Standard\n"
            "groups:\n"
            "  - name: Contoso\n"
            "    pricing_plan: Standard\n"
            "  - name: Contoso\n"
            "    pricing_plan: Standard\n"
        ),
        "duplicate group name",
    ),
    (
        (
            "pricing_plans:\n"
            "  - name: Standard\n"
            "groups:\n"
            "  - name: '   '\n"
            "    pricing_plan: Standard\n"
        ),
        "group names must not be blank",
    ),
    (
        (
            "pricing_plans:\n"
            "  - name: Standard\n"
            "groups:\n"
            "  - name: Contoso\n"
            "    pricing_plan: Premium\n"
        ),
        "unknown pricing plan",
    ),
    (
        _GROUPS_PREFIX + "    plans: plan-001\n",
        "plans must be a list",
    ),
    (
        _GROUPS_PREFIX + "    plans:\n      - ''\n",
        "blank entry in plans",
    ),
    (
        _GROUPS_PREFIX + "    backup_servers:\n      - server-id-001\n      - server-id-001\n",
        "duplicate entries in backup_servers: server-id-001",
    ),
    (
        (
            "pricing_plans:\n"
            "  - name: Standard\n"
            "plans:\n"
            "  plan-001: Premium\n"
        ),
        r"unknown pricing plan name\(s\) in plans: Premium",
    ),
    (
        (
            "pricing_plans:\n"
            "  - name: Standard\n"
            "backup_servers:\n"
            "  server-id-001: Premium\n"
        ),
        r"unknown pricing plan name\(s\) in backup_servers: Premium",
    ),
], ids=[
    "no-pricing-plans",
    "duplicate-pricing-plan-name",
    "duplicate-group-name",
    "blank-group-name",
    "unknown-group-pricing-plan",
    "membership-not-a-list",
    "membership-blank-entry",
    "membership-duplicate-entry",
    "unknown-rate-in-plans",
    "unknown-rate-in-backup-servers",
])
def test_load_pricing_yaml_errors(
    tmp_path: Path,
    yaml_content: str,
    match: str,
) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(yaml_content)
    with pytest.raises(ValueError, match=match):
        _load_pricing_yaml(str(p))
