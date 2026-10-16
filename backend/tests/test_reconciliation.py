"""Step 2 acceptance: aggregation, allocation and grain conversion."""
from __future__ import annotations

from sqlalchemy import select

from app.services import cycle_svc
from app.services.hierarchy import group_map
from app.services.reconciliation import (
    AllocationKey,
    agg_values,
    allocate,
    convert_grain,
    distribute,
)


def test_distribute_preserves_total():
    parts = distribute(1000.0, {"a": 3.0, "b": 1.0, "c": 1.0}, decimals=0)
    assert sum(parts.values()) == 1000.0
    assert parts["a"] > parts["b"]


def test_distribute_handles_zero_weights():
    parts = distribute(100.0, {"a": 0.0, "b": 0.0}, decimals=0)
    assert sum(parts.values()) == 100.0
    assert parts["a"] == parts["b"] == 50.0


def test_distribute_largest_remainder_is_exact():
    parts = distribute(10.0, {f"s{i}": 1.0 for i in range(3)}, decimals=0)
    assert sum(parts.values()) == 10.0
    assert sorted(parts.values()) == [3.0, 3.0, 4.0]


def test_top_down_then_bottom_up_preserves_total(db):
    """A value entered at a higher level distributes down and sums back up."""
    level = {"product": "category", "geo": "region"}
    gmap = group_map(db, level)
    assert gmap
    group = next(iter(gmap.values()))
    bucket = 1
    total = 12_345.0

    outcome = allocate(db, {(group, bucket): total}, gmap, AllocationKey(method="history_share", decimals=0))
    assert outcome.values, "dağıtım sonucu boş"
    rolled = agg_values(outcome.values, gmap, "sum")
    assert abs(rolled[(group, bucket)] - total) < 0.5


def test_monthly_to_weekly_and_back(db):
    from app.domain.models import TimeBucket

    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)[:3]
    values = {("s1", b.id): 3000.0 for b in window}

    weekly = convert_grain(db, values, "month", "week", method="working_days")
    assert weekly, "haftaya dönüşüm boş"
    back = convert_grain(db, weekly, "week", "month")
    for b in window:
        # Weeks straddle month borders, so the mapped total is close rather than exact.
        assert abs(back.get(("s1", b.id), 0.0) - 3000.0) < 3000.0 * 0.35

    total_in = sum(values.values())
    total_out = sum(weekly.values())
    assert abs(total_in - total_out) < 1.0

    daily = convert_grain(db, {("s1", window[0].id): 1000.0}, "month", "day")
    assert abs(sum(daily.values()) - 1000.0) < 0.5
    rows = db.execute(select(TimeBucket).where(TimeBucket.id.in_(list(daily)[0][1:]))).scalars().all()
    assert rows


def test_aggregate_respects_function(db):
    gmap = {"a": "g1", "b": "g1"}
    values = {("a", 1): 10.0, ("b", 1): 30.0}
    assert agg_values(values, gmap, "sum")[("g1", 1)] == 40.0
    assert agg_values(values, gmap, "avg")[("g1", 1)] == 20.0
    assert agg_values(values, gmap, "max")[("g1", 1)] == 30.0
