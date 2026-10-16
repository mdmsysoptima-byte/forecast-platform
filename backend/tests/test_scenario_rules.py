"""Step 4 acceptance: every rule type, ordered application, per rule effects."""
from __future__ import annotations

import pytest

from app.services.scenario_rules import ResolvedRule, apply_rules

BUCKETS = [10, 11, 12, 13]
SERIES = {"a", "b"}


def base_values(value: float = 100.0) -> dict:
    return {(s, b): value for s in SERIES for b in BUCKETS}


def rule(type_: str, params: dict, buckets=None, order=0, series=None) -> ResolvedRule:
    return ResolvedRule(
        id=order + 1, type=type_, name=type_, order_index=order,
        series=series or set(SERIES), buckets=buckets or list(BUCKETS), params=params,
    )


def test_multiplier():
    out = apply_rules(base_values(), [rule("multiplier", {"pct": 10})], BUCKETS)
    assert out.values[("a", 10)] == pytest.approx(110.0)
    assert len(out.effects) == len(SERIES) * len(BUCKETS)
    assert all(e["delta"] == pytest.approx(10.0) for e in out.effects)


def test_additive_splits_total_across_scope():
    out = apply_rules(base_values(), [rule("additive", {"amount": 100, "per_bucket": True})], BUCKETS)
    assert out.values[("a", 10)] == 150.0
    assert out.values[("b", 10)] == 150.0


def test_shift_moves_demand_between_periods():
    out = apply_rules(
        base_values(),
        [rule("shift", {"source_bucket_id": 12, "target_bucket_id": 11, "ratio": 30})],
        BUCKETS,
    )
    assert out.values[("a", 12)] == 70.0
    assert out.values[("a", 11)] == 130.0
    assert sum(out.values.values()) == 800.0, "kaydırma toplamı korumalidir"


def test_event_level_affects_every_later_period():
    out = apply_rules(
        base_values(),
        [rule("event", {"effect": "level", "pct": -20}, buckets=[12])],
        BUCKETS,
    )
    assert out.values[("a", 11)] == 100.0
    assert out.values[("a", 12)] == 80.0
    assert out.values[("a", 13)] == 80.0


def test_event_set_value_zero():
    out = apply_rules(
        base_values(), [rule("event", {"effect": "temporary", "set_value": 0}, buckets=[11])], BUCKETS
    )
    assert out.values[("a", 11)] == 0.0
    assert out.values[("a", 12)] == 100.0


def test_cap_on_total_scales_scope_down():
    out = apply_rules(
        base_values(), [rule("cap", {"max": 150, "scope_level": "total"}, buckets=[10])], BUCKETS
    )
    assert out.values[("a", 10)] + out.values[("b", 10)] == 150.0


def test_cap_per_series():
    out = apply_rules(base_values(), [rule("cap", {"max": 80}, buckets=[10])], BUCKETS)
    assert out.values[("a", 10)] == 80.0


def test_launch_curve_sets_level():
    out = apply_rules(
        base_values(0.0), [rule("launch_curve", {"peak": 1000, "template": "linear"})], BUCKETS
    )
    series_a = [out.values[("a", b)] for b in BUCKETS]
    assert series_a == sorted(series_a)
    assert series_a[-1] == 1000.0


def test_phase_out_reaches_zero():
    out = apply_rules(base_values(), [rule("phase_out", {"profile": "linear"})], BUCKETS)
    assert out.values[("a", BUCKETS[-1])] == 0.0


def test_elasticity_applies_power_rule():
    out = apply_rules(base_values(), [rule("elasticity", {"elasticity": -0.8, "price_pct": 10})], BUCKETS)
    assert 90.0 < out.values[("a", 10)] < 94.0


def test_rules_apply_in_order_and_effects_are_separable():
    rules = [
        rule("multiplier", {"pct": 10}, order=0),
        rule("additive", {"amount": 20, "per_bucket": True}, order=1),
    ]
    out = apply_rules(base_values(), rules, BUCKETS)
    # 100 -> 110 -> 110 + 10 (half of 20 across two series)
    assert out.values[("a", 10)] == pytest.approx(120.0)
    by_rule: dict[int, float] = {}
    for e in out.effects:
        by_rule[e["rule_id"]] = by_rule.get(e["rule_id"], 0.0) + e["delta"]
    assert by_rule[1] == pytest.approx(80.0)   # 10 per cell, 8 cells
    assert by_rule[2] == pytest.approx(80.0)   # 10 per cell, 8 cells


def test_unknown_rule_is_reported_not_silent():
    out = apply_rules(base_values(), [rule("bilinmeyen", {})], BUCKETS)
    assert out.warnings and "bilinmeyen" in out.warnings[0]
