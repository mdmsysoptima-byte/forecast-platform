"""Aggregation, allocation, grain conversion and reconciliation (section 5).

Pure functions first, database aware wrappers after. The math is testable
without an API or a user interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domain.models import Series, TimeBucket

log = get_logger(__name__)

Values = dict[tuple[str, int], float]


@dataclass
class AllocationKey:
    """How a parent value is split across its children."""

    method: str = "history_share"  # history_share, last_n_share, layer_ratio, equal, working_days, custom
    n: int = 12
    layer_code: str | None = None
    ratios: dict[str, float] = field(default_factory=dict)
    decimals: int = 0


@dataclass
class AllocationOutcome:
    values: Values
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Pure math
# --------------------------------------------------------------------------


def agg_values(values: Values, mapping: dict[str, str], agg_fn: str = "sum") -> Values:
    """Roll base level values up to the groups given by mapping."""
    buf: dict[tuple[str, int], list[float]] = {}
    for (key, bucket), v in values.items():
        group = mapping.get(key)
        if group is None:
            continue
        buf.setdefault((group, bucket), []).append(v)
    out: Values = {}
    for k, vals in buf.items():
        if agg_fn == "sum":
            out[k] = float(sum(vals))
        elif agg_fn == "avg":
            out[k] = float(sum(vals) / len(vals))
        elif agg_fn == "last":
            out[k] = float(vals[-1])
        elif agg_fn == "max":
            out[k] = float(max(vals))
        elif agg_fn == "min":
            out[k] = float(min(vals))
        else:
            raise ValueError(f"Bilinmeyen toplama fonksiyonu: {agg_fn}")
    return out


def distribute(total: float, weights: dict[str, float], decimals: int = 0) -> dict[str, float]:
    """Largest remainder distribution. The sum of the parts equals the total."""
    if not weights:
        return {}
    wsum = sum(max(0.0, w) for w in weights.values())
    if wsum <= 0:
        share = 1.0 / len(weights)
        weights = {k: share for k in weights}
        wsum = 1.0
    factor = 10 ** decimals
    raw = {k: total * max(0.0, w) / wsum for k, w in weights.items()}
    floored = {k: int(v * factor) / factor for k, v in raw.items()}
    assigned = sum(floored.values())
    missing = round((total - assigned) * factor)
    if missing != 0:
        order = sorted(raw, key=lambda k: (raw[k] - floored[k]), reverse=missing > 0)
        step = (1 if missing > 0 else -1) / factor
        for i in range(abs(int(missing))):
            floored[order[i % len(order)]] += step
    return {k: round(v, max(decimals, 6)) for k, v in floored.items()}


def allocate_values(
    parent_values: Values,
    children_of: dict[str, list[str]],
    weights_of: dict[str, dict[str, float]],
    decimals: int = 0,
) -> AllocationOutcome:
    """Split each parent value across its children using the given weights."""
    out: Values = {}
    warnings: list[str] = []
    for (parent, bucket), total in parent_values.items():
        kids = children_of.get(parent) or []
        if not kids:
            continue
        w = {k: weights_of.get(parent, {}).get(k, 0.0) for k in kids}
        if sum(w.values()) <= 0:
            warnings.append(f"{parent} için geçmiş pay sıfır, eşit dağıtım uygulandı")
            w = {k: 1.0 for k in kids}
        for child, value in distribute(float(total), w, decimals).items():
            out[(child, bucket)] = value
    return AllocationOutcome(values=out, warnings=warnings)


def convert_grain_up(values: Values, parent_of: dict[int, int], method: str = "sum") -> Values:
    """Day or week values rolled into the parent bucket."""
    buf: dict[tuple[str, int], list[float]] = {}
    for (key, bucket), v in values.items():
        parent = parent_of.get(bucket)
        if parent is None:
            continue
        buf.setdefault((key, parent), []).append(v)
    if method == "last":
        return {k: vals[-1] for k, vals in buf.items()}
    if method == "avg":
        return {k: sum(vals) / len(vals) for k, vals in buf.items()}
    return {k: float(sum(vals)) for k, vals in buf.items()}


def convert_grain_down(
    values: Values, children_of: dict[int, list[int]], weights_of: dict[int, dict[int, float]], decimals: int = 0
) -> Values:
    """Month values spread over weeks or days."""
    out: Values = {}
    for (key, bucket), total in values.items():
        kids = children_of.get(bucket) or []
        if not kids:
            continue
        w = weights_of.get(bucket) or {k: 1.0 for k in kids}
        for child, value in distribute(float(total), {k: w.get(k, 0.0) for k in kids}, decimals).items():
            out[(key, child)] = value
    return out


# --------------------------------------------------------------------------
# Database aware wrappers (specification signatures)
# --------------------------------------------------------------------------


def aggregate(db: Session, values: Values, level: dict[str, str], agg_fn: str = "sum") -> Values:
    from app.services.hierarchy import group_map

    keys = {k for k, _ in values}
    return agg_values(values, group_map(db, level, keys), agg_fn)


def history_weights(db: Session, series_keys: Iterable[str], n: int = 12) -> dict[str, float]:
    """Share of the last n actual periods per series."""
    from app.adapters import get_store

    keys = list(series_keys)
    store = get_store()
    rows = store.read_series(keys)
    if not rows:
        return {k: 0.0 for k in keys}
    buckets = sorted({r["bucket_id"] for r in rows})[-n:]
    window = set(buckets)
    out = {k: 0.0 for k in keys}
    for r in rows:
        if r["bucket_id"] in window:
            out[r["series_key"]] = out.get(r["series_key"], 0.0) + float(r["value"])
    return out


def working_day_weights(db: Session, bucket_ids: Iterable[int]) -> dict[int, float]:
    rows = db.execute(
        select(TimeBucket.id, TimeBucket.working_days).where(TimeBucket.id.in_(list(bucket_ids) or [-1]))
    ).all()
    return {bid: float(wd or 0) for bid, wd in rows}


def allocate(
    db: Session,
    parent_values: Values,
    group_of: dict[str, str],
    key: AllocationKey,
    layer_lookup: dict[str, float] | None = None,
) -> AllocationOutcome:
    """Push group level values down to the base series inside each group."""
    children_of: dict[str, list[str]] = {}
    for sk, grp in group_of.items():
        children_of.setdefault(grp, []).append(sk)

    weights_of: dict[str, dict[str, float]] = {}
    if key.method in ("history_share", "last_n_share"):
        n = key.n if key.method == "last_n_share" else 12
        w = history_weights(db, group_of.keys(), n=n)
        for grp, kids in children_of.items():
            weights_of[grp] = {k: w.get(k, 0.0) for k in kids}
    elif key.method == "layer_ratio" and layer_lookup is not None:
        for grp, kids in children_of.items():
            weights_of[grp] = {k: float(layer_lookup.get(k, 0.0)) for k in kids}
    elif key.method == "custom" and key.ratios:
        for grp, kids in children_of.items():
            weights_of[grp] = {k: float(key.ratios.get(k, 0.0)) for k in kids}
    else:  # equal, working_days at series level behaves as equal
        for grp, kids in children_of.items():
            weights_of[grp] = {k: 1.0 for k in kids}

    outcome = allocate_values(parent_values, children_of, weights_of, decimals=key.decimals)
    for w in outcome.warnings:
        log.warning("allocation: %s", w)
    return outcome


def convert_grain(db: Session, values: Values, from_grain: str, to_grain: str, method: str = "auto") -> Values:
    """Move values between grains inside the same calendar."""
    order = ["day", "week", "month", "quarter", "year"]
    if from_grain == to_grain:
        return dict(values)
    if from_grain not in order or to_grain not in order:
        raise ValueError("Desteklenmeyen kova dönüşümü")

    bucket_ids = {b for _, b in values}
    rows = db.execute(select(TimeBucket).where(TimeBucket.id.in_(bucket_ids or {-1}))).scalars().all()
    if not rows:
        return {}
    cal_id = rows[0].calendar_id
    src = {b.id: b for b in rows}
    tgt = list(
        db.execute(
            select(TimeBucket)
            .where(TimeBucket.calendar_id == cal_id, TimeBucket.grain == to_grain)
            .order_by(TimeBucket.seq)
        ).scalars()
    )

    going_up = order.index(to_grain) > order.index(from_grain)
    if going_up:
        parent_of: dict[int, int] = {}
        for bid, b in src.items():
            for t in tgt:
                if t.start_date <= b.start_date <= t.end_date:
                    parent_of[bid] = t.id
                    break
        agg = "last" if method == "last" else "sum"
        return convert_grain_up(values, parent_of, agg)

    children_of: dict[int, list[int]] = {}
    weights_of: dict[int, dict[int, float]] = {}
    for bid, b in src.items():
        kids = [t for t in tgt if t.start_date >= b.start_date and t.start_date <= b.end_date]
        children_of[bid] = [t.id for t in kids]
        if method in ("working_days", "auto"):
            weights_of[bid] = {t.id: float(t.working_days or 0) for t in kids}
        else:
            weights_of[bid] = {t.id: 1.0 for t in kids}
        if sum(weights_of[bid].values()) <= 0:
            weights_of[bid] = {t.id: 1.0 for t in kids}
    return convert_grain_down(values, children_of, weights_of, decimals=2)


def reconcile(db: Session, version_id: int, changed_scope: dict | None = None) -> dict:
    """Make every level of the version consistent again.

    Base level rows are the single source of truth, so upper levels are
    recomputed on read. This call refreshes the derived consensus layer and
    reports any inconsistency it finds.
    """
    from app.services.layers import recompute_consensus

    result = recompute_consensus(db, version_id, scope_keys=None)
    log.info(
        "reconcile tamamlandi", extra={"version_id": version_id, "entity": "version"}
    )
    return result
