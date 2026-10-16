"""Forecast grid read model (sections 10 and 11).

One query returns the columns (periods plus subtotals), the rows (nodes at the
requested level) and every layer the user asked for, with cell state flags.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.domain.models import (
    Alert,
    Cycle,
    FxRate,
    Insight,
    Layer,
    Measure,
    Override,
    PriceIndex,
    Series,
    TimeBucket,
    Version,
)
from app.services import cycle_svc, layers
from app.services.hierarchy import group_labels, group_map, resolve_scope
from app.services.reconciliation import convert_grain

DEFAULT_LAYERS = ["actual", "last_year", "statistical", "sales", "planner", "consensus", "budget"]


@dataclass
class GridQuery:
    version_id: int
    scope: dict = field(default_factory=dict)
    level: dict[str, str] = field(default_factory=dict)
    grain: str = "month"
    layer_codes: list[str] = field(default_factory=lambda: list(DEFAULT_LAYERS))
    measure: str = "units"
    currency: str = "TRY"
    view: str = "nominal"  # nominal, real, fx
    history_periods: int = 12
    include_subtotals: bool = True


def _conversion_factors(db: Session, buckets: list[TimeBucket], q: GridQuery) -> dict[int, float]:
    measure = db.execute(select(Measure).where(Measure.code == q.measure)).scalar_one_or_none()
    if measure is None or measure.unit_type != "value":
        return {b.id: 1.0 for b in buckets}
    factors = {b.id: 1.0 for b in buckets}
    if q.view == "real":
        rows = db.execute(select(PriceIndex).where(PriceIndex.index_code == "tufe")).scalars().all()
        if rows:
            by_month = {(r.date.year, r.date.month): r.value for r in rows}
            base = sorted(rows, key=lambda r: r.date)[0].value or 1.0
            for b in buckets:
                idx = by_month.get((b.start_date.year, b.start_date.month))
                if idx:
                    factors[b.id] = base / idx
    elif q.view == "fx" and q.currency != "TRY":
        rows = db.execute(select(FxRate).where(FxRate.from_ccy == q.currency, FxRate.to_ccy == "TRY")).scalars().all()
        by_month = {(r.date.year, r.date.month): r.rate for r in rows}
        for b in buckets:
            rate = by_month.get((b.start_date.year, b.start_date.month))
            if rate:
                factors[b.id] = 1.0 / rate
    return factors


def apply_measure_factors(
    db: Session,
    raw: dict[str, dict[tuple[str, int], float]],
    q: GridQuery,
    scope_keys: set[str],
    bucket_ids: list[int],
    buckets: list[TimeBucket],
) -> dict[str, dict[tuple[str, int], float]]:
    """Turn stored base measure values into the requested measure and view.

    The price factor is per series and per period (a derived measure such as
    revenue), the view factor is per period (real TRY or a foreign currency).
    """
    from app.services import measures

    price = measures.price_factors(db, q.measure, scope_keys, bucket_ids)
    view = _conversion_factors(db, buckets, q)
    if not price and all(f == 1.0 for f in view.values()):
        return raw
    out: dict[str, dict[tuple[str, int], float]] = {}
    for code, vals in raw.items():
        out[code] = {
            (sk, bid): v * price.get((sk, bid), 1.0) * view.get(bid, 1.0)
            for (sk, bid), v in vals.items()
        }
    return out


def query(db: Session, principal: Principal, q: GridQuery) -> dict:
    version = db.get(Version, q.version_id)
    if version is None:
        raise ValueError("Versiyon bulunamadi")
    cycle = db.get(Cycle, version.cycle_id) if version.cycle_id else cycle_svc.active_cycle(db)
    if cycle is None:
        raise ValueError("Aktif çevrim yok")

    flags = cycle_svc.period_flags(db, cycle)
    history = cycle_svc.history_window(db, cycle, periods=q.history_periods)
    future = cycle_svc.forecast_window(db, cycle)
    month_buckets = history + future
    current = db.get(TimeBucket, cycle.current_period_bucket_id)

    scope_keys = resolve_scope(db, q.scope, principal.view_nodes if not principal.unrestricted else None)
    if not scope_keys:
        return {
            "columns": [], "rows": [], "layers": [], "meta": {"message": "Kapsamda seri yok"},
        }

    gmap = group_map(db, q.level, scope_keys)
    labels = group_labels(db, set(gmap.values()))
    lm = layers.layer_map(db)
    requested = [c for c in q.layer_codes if c in lm]
    bucket_ids = [b.id for b in month_buckets]

    raw: dict[str, dict[tuple[str, int], float]] = {}
    for code in requested:
        raw[code] = layers.read_values(db, q.version_id, code, scope_keys, bucket_ids)

    # Measure derivation and currency view are applied at the stored grain, before
    # any roll up, so a quarter is the sum of its months rather than a month total
    # scaled by a quarterly price.
    raw = apply_measure_factors(db, raw, q, scope_keys, bucket_ids, month_buckets)

    # Grain conversion happens after reading the stored monthly values.
    target_buckets = month_buckets
    if q.grain != cycle.grain:
        converted: dict[str, dict[tuple[str, int], float]] = {}
        for code, vals in raw.items():
            converted[code] = convert_grain(db, vals, cycle.grain, q.grain)
        raw = converted
        ids = {b for _, b in {k for v in raw.values() for k in v}}
        target_buckets = list(
            db.execute(select(TimeBucket).where(TimeBucket.id.in_(ids or {-1})).order_by(TimeBucket.seq)).scalars()
        )

    agg: dict[str, dict[tuple[str, int], float]] = {}
    for code, vals in raw.items():
        buf: dict[tuple[str, int], float] = defaultdict(float)
        for (sk, bid), v in vals.items():
            g = gmap.get(sk)
            if g is None:
                continue
            buf[(g, bid)] += v
        agg[code] = buf

    cell_flags = _cell_flags(db, q.version_id, gmap, bucket_ids)
    consensus_src = layers.consensus_sources(db, q.version_id, scope_keys)
    src_by_group: dict[tuple[str, int], set[str]] = defaultdict(set)
    for (sk, bid), code in consensus_src.items():
        g = gmap.get(sk)
        if g:
            src_by_group[(g, bid)].add(code)

    columns = [
        {
            "bucket_id": b.id,
            "label": b.label,
            "grain": b.grain,
            "flag": flags.get(b.id, "actual" if b.seq < current.seq else "open"),
            "working_days": b.working_days,
            "is_current": b.id == current.id,
        }
        for b in target_buckets
    ]

    groups = sorted(set(gmap.values()), key=lambda g: labels.get(g, {}).get("label", g))
    rows = []
    for g in groups:
        info = labels.get(g, {"label": g, "parts": [], "node_ids": []})
        row = {
            "group": g,
            "label": info["label"],
            "parts": info["parts"],
            "node_ids": info["node_ids"],
            "series_count": sum(1 for sk, gg in gmap.items() if gg == g),
            "layers": {},
            "flags": {},
            "subtotals": {},
        }
        for code in requested:
            vals = {
                str(b.id): round(agg[code].get((g, b.id), 0.0), 2)
                for b in target_buckets
                if (g, b.id) in agg[code]
            }
            row["layers"][code] = vals
        for b in target_buckets:
            f = cell_flags.get((g, b.id))
            src = src_by_group.get((g, b.id))
            if f or src:
                row["flags"][str(b.id)] = {**(f or {}), **({"consensus_from": sorted(src)} if src else {})}
        if q.include_subtotals:
            row["subtotals"] = _subtotals(agg, g, target_buckets, current, requested)
        rows.append(row)

    total_row = _total_row(agg, target_buckets, current, requested, q.include_subtotals)

    return {
        "version": {
            "id": version.id, "name": version.name, "kind": version.kind, "status": version.status,
            "provenance": version.provenance,
        },
        "cycle": {
            "id": cycle.id, "name": cycle.name, "status": cycle.status,
            "current_period": current.label, "fence_periods": cycle.fence_periods,
            "freeze_periods": cycle.freeze_periods, "horizon": cycle.horizon_periods,
        },
        "columns": columns,
        "layers": [
            {
                "code": lm[c].code, "name": lm[c].name, "editable": lm[c].is_editable,
                "provenance": lm[c].provenance, "order": lm[c].order_index,
                "can_edit": _can_edit_layer(principal, lm[c]),
            }
            for c in sorted(requested, key=lambda c: lm[c].order_index)
        ],
        "rows": rows,
        "total": total_row,
        "meta": {
            "level": q.level, "grain": q.grain, "measure": q.measure, "view": q.view,
            "currency": q.currency, "series_in_scope": len(scope_keys),
            "restricted": not principal.unrestricted,
        },
    }


def _can_edit_layer(principal: Principal, layer: Layer) -> bool:
    if not layer.is_editable:
        return False
    if principal.is_admin:
        return True
    return bool(layer.edit_roles) and any(r in principal.roles for r in layer.edit_roles)


def _subtotals(agg, group, buckets, current, codes) -> dict:
    out: dict[str, dict[str, float]] = {}
    year = current.start_date.year
    for code in codes:
        vals = agg[code]
        by_quarter: dict[str, float] = defaultdict(float)
        year_total = ytd = ytg = 0.0
        for b in buckets:
            v = vals.get((group, b.id))
            if v is None:
                continue
            q = f"{b.start_date.year}-Q{(b.start_date.month - 1) // 3 + 1}"
            by_quarter[q] += v
            if b.start_date.year == year:
                year_total += v
                if b.seq < current.seq:
                    ytd += v
                else:
                    ytg += v
        out[code] = {
            **{k: round(v, 2) for k, v in by_quarter.items()},
            "year": round(year_total, 2),
            "ytd": round(ytd, 2),
            "ytg": round(ytg, 2),
        }
    return out


def _total_row(agg, buckets, current, codes, include_subtotals) -> dict:
    row = {"label": "Toplam", "layers": {}, "subtotals": {}}
    for code in codes:
        per_bucket: dict[str, float] = {}
        for b in buckets:
            s = sum(v for (g, bid), v in agg[code].items() if bid == b.id)
            if s:
                per_bucket[str(b.id)] = round(s, 2)
        row["layers"][code] = per_bucket
        if include_subtotals:
            year = current.start_date.year
            year_total = ytd = ytg = 0.0
            by_quarter: dict[str, float] = defaultdict(float)
            for b in buckets:
                v = per_bucket.get(str(b.id))
                if v is None:
                    continue
                by_quarter[f"{b.start_date.year}-Q{(b.start_date.month - 1) // 3 + 1}"] += v
                if b.start_date.year == year:
                    year_total += v
                    if b.seq < current.seq:
                        ytd += v
                    else:
                        ytg += v
            row["subtotals"][code] = {
                **{k: round(v, 2) for k, v in by_quarter.items()},
                "year": round(year_total, 2), "ytd": round(ytd, 2), "ytg": round(ytg, 2),
            }
    return row


def _cell_flags(db: Session, version_id: int, gmap: dict[str, str], bucket_ids: list[int]) -> dict:
    out: dict[tuple[str, int], dict] = defaultdict(dict)
    ov = db.execute(
        select(Override.series_key, Override.bucket_id).where(
            Override.version_id == version_id, Override.reverted.is_(False)
        )
    ).all()
    for sk, bid in ov:
        g = gmap.get(sk)
        if g:
            out[(g, bid)]["override"] = True

    ins = db.execute(
        select(Insight.series_key, Insight.bucket_from_id, Insight.bucket_to_id).where(
            Insight.series_key.isnot(None)
        )
    ).all()
    seqs = {
        b.id: b.seq for b in db.execute(select(TimeBucket).where(TimeBucket.id.in_(bucket_ids or [-1]))).scalars()
    }
    for sk, bf, bt in ins:
        g = gmap.get(sk)
        if not g:
            continue
        lo = seqs.get(bf, -10**9)
        hi = seqs.get(bt, 10**9)
        for bid, seq in seqs.items():
            if lo <= seq <= hi:
                out[(g, bid)]["insight"] = True

    al = db.execute(
        select(Alert.series_key, Alert.bucket_id).where(
            Alert.version_id == version_id, Alert.status == "open"
        )
    ).all()
    for sk, bid in al:
        if sk is None:
            continue
        g = gmap.get(sk)
        if g and bid:
            out[(g, bid)]["alert"] = True
        elif g:
            for b in bucket_ids:
                out[(g, b)].setdefault("alert_series", True)
    return out


def explain_cell(
    db: Session, version_id: int, layer_code: str, group: str, bucket_id: int, level: dict[str, str]
) -> dict:
    """Where did this number come from."""
    from app.services.override_svc import cell_history

    version = db.get(Version, version_id)
    bucket = db.get(TimeBucket, bucket_id)
    scope_keys = {sk for sk, g in group_map(db, level).items() if g == group}
    lm = layers.layer_map(db)

    layer_values = {}
    for code in lm:
        vals = layers.read_values(db, version_id, code, scope_keys, [bucket_id])
        if vals:
            layer_values[code] = round(sum(vals.values()), 2)

    history: list[dict] = []
    for sk in sorted(scope_keys):
        history.extend(cell_history(db, version_id, layer_code, sk, bucket_id))

    rule_effects: list[dict] = []
    if version and version.run_id:
        from app.adapters import get_store

        effects = get_store().read_rule_effects(version.run_id)
        prov_rules = {r["id"]: r for r in (version.provenance or {}).get("rules", [])}
        agg: dict[int, float] = defaultdict(float)
        for e in effects:
            if e["series_key"] in scope_keys and e["bucket_id"] == bucket_id:
                agg[e["rule_id"]] += e["delta"]
        rule_effects = [
            {
                "rule_id": rid,
                "name": prov_rules.get(rid, {}).get("name", f"Kural {rid}"),
                "type": prov_rules.get(rid, {}).get("type", ""),
                "delta": round(d, 2),
            }
            for rid, d in sorted(agg.items())
        ]

    insights = db.execute(
        select(Insight).where(Insight.series_key.in_(sorted(scope_keys) or [""]))
    ).scalars().all()

    return {
        "version": {
            "id": version.id, "name": version.name, "kind": version.kind,
            "status": version.status, "provenance": version.provenance,
        } if version else None,
        "bucket": {"id": bucket_id, "label": bucket.label if bucket else str(bucket_id)},
        "layer": layer_code,
        "layer_values": layer_values,
        "consensus_rule": layers.consensus_config(db)["rule"],
        "override_history": history,
        "rule_effects": rule_effects,
        "insights": [
            {"id": i.id, "text": i.text, "user_id": i.user_id,
             "at": i.created_at.isoformat() if i.created_at else None}
            for i in insights
        ],
        "series_in_cell": len(scope_keys),
    }
