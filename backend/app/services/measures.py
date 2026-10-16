"""Derived measures (specification section 10, measure derivation).

A measure may declare a formula in the sector YAML, for example
`revenue: units * price`. Rather than materialising a second set of series and
running a second model over them, the derived value is computed at read time by
multiplying the base measure by a per series, per period factor. Volume and
value can therefore never drift apart.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domain.models import Measure, Member, PriceIndex, Series, TimeBucket

log = get_logger(__name__)

PRICE_ATTRIBUTE = "base_price"
PRICE_INDEX_CODE = "tufe"
_FORMULA = re.compile(r"^\s*(\w+)\s*\*\s*price\s*$")


def base_measure_of(db: Session, measure_code: str) -> str:
    """The measure a derived measure is computed from. Itself when not derived."""
    measure = db.execute(select(Measure).where(Measure.code == measure_code)).scalar_one_or_none()
    if measure is None or not measure.formula:
        return measure_code
    match = _FORMULA.match(measure.formula)
    return match.group(1) if match else measure_code


def is_derived(db: Session, measure_code: str) -> bool:
    return base_measure_of(db, measure_code) != measure_code


def _index_ratios(db: Session, bucket_ids: list[int]) -> dict[int, float]:
    """Price level of each bucket relative to the current period.

    The base is the cycle's current period, so a member's stored base_price is
    read as "the price today": history is deflated and the forecast window is
    inflated by the index.
    """
    from app.services import cycle_svc

    rows = db.execute(
        select(PriceIndex.date, PriceIndex.value).where(PriceIndex.index_code == PRICE_INDEX_CODE)
    ).all()
    if not rows:
        return {b: 1.0 for b in bucket_ids}
    rows.sort(key=lambda r: r[0])
    by_month = {(d.year, d.month): v for d, v in rows}
    ordered = [v for _d, v in rows]

    base = ordered[-1]
    cycle = cycle_svc.active_cycle(db)
    if cycle is not None:
        current = db.get(TimeBucket, cycle.current_period_bucket_id)
        if current is not None:
            base = by_month.get((current.start_date.year, current.start_date.month), base)
    base = base or 1.0

    buckets = db.execute(
        select(TimeBucket.id, TimeBucket.start_date).where(TimeBucket.id.in_(bucket_ids or [-1]))
    ).all()
    out: dict[int, float] = {}
    for bid, start in buckets:
        value = by_month.get((start.year, start.month))
        if value is None:
            # Outside the indexed window: carry the nearest end forward or back.
            value = ordered[-1] if (start.year, start.month) > max(by_month) else ordered[0]
        out[bid] = float(value) / float(base)
    return out


def price_factors(
    db: Session, measure_code: str, series_keys: set[str] | list[str], bucket_ids: list[int]
) -> dict[tuple[str, int], float]:
    """Factor to turn the base measure into the derived one.

    Returns an empty mapping when the measure is not derived; callers treat a
    missing key as 1.0, so the common path costs nothing.
    """
    if not is_derived(db, measure_code):
        return {}
    keys = sorted(set(series_keys))
    if not keys or not bucket_ids:
        return {}

    dim_members = {
        sk: dm for sk, dm in db.execute(
            select(Series.series_key, Series.dim_members).where(Series.series_key.in_(keys))
        ).all()
    }
    member_ids = {int(v) for dm in dim_members.values() for v in (dm or {}).values()}
    prices: dict[int, float] = {}
    for mid, attrs in db.execute(
        select(Member.id, Member.attributes).where(Member.id.in_(member_ids or {-1}))
    ).all():
        price = (attrs or {}).get(PRICE_ATTRIBUTE)
        if price is not None:
            prices[mid] = float(price)

    ratios = _index_ratios(db, list(bucket_ids))
    missing: set[str] = set()
    out: dict[tuple[str, int], float] = {}
    for sk in keys:
        base_price = None
        for mid in (dim_members.get(sk) or {}).values():
            base_price = prices.get(int(mid))
            if base_price is not None:
                break
        if base_price is None:
            missing.add(sk)
            continue
        for bid in bucket_ids:
            out[(sk, bid)] = base_price * ratios.get(bid, 1.0)
    if missing:
        log.warning(
            "%s ölçüsü için %s seride fiyat bulunamadı, bu seriler sıfır sayıldı",
            measure_code, len(missing),
        )
        for sk in missing:
            for bid in bucket_ids:
                out[(sk, bid)] = 0.0
    return out
