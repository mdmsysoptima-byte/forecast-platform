"""Accuracy, FVA, variance and budget gap (section 10).

Accuracy is always computed from snapshot_value against actual, by lag.
Totals are volume weighted; a plain average is never used.
"""
from __future__ import annotations

import math
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.core.logging import get_logger
from app.domain.models import Override, ReasonCode, Series, TimeBucket, Version
from app.services import layers
from app.services.hierarchy import group_labels, group_map

log = get_logger(__name__)

FVA_LADDER = ["naive", "statistical", "sales", "marketing", "planner", "consensus"]
LAYER_LABELS = {
    "naive": "Naive",
    "statistical": "İstatistiksel",
    "sales": "Satış",
    "marketing": "Pazarlama",
    "planner": "Planlamacı",
    "consensus": "Konsensüs",
    "budget": "Bütçe",
}
METRIC_LABELS = {"wmape": "WMAPE", "mape": "MAPE", "mae": "MAE", "rmse": "RMSE", "bias": "Bias"}


def _pairs(
    db: Session,
    layer_code: str,
    lag: int | None = None,
    series_keys: set[str] | None = None,
) -> list[dict]:
    """Snapshot forecast joined with the actual that followed it.

    series_keys daraltılmış bir kapsamdır. Kokpit kapsamı süzdüğünde doğruluk da
    aynı kümeden hesaplanmalı; aksi halde ekranda daralmış hacim ile tüm şirketin
    hata oranı yan yana durur ve yanlış okunur.
    """
    store = get_store()
    snaps = store.read_snapshots(layer_codes=[layer_code] if layer_code != "naive" else ["statistical"])
    if series_keys is not None:
        snaps = [s for s in snaps if s["series_key"] in series_keys]
    if not snaps:
        return []
    keys = sorted({s["series_key"] for s in snaps})
    actual_rows = store.read_series(keys)
    actual = {(r["series_key"], r["bucket_id"]): float(r["value"]) for r in actual_rows}

    out: list[dict] = []
    if layer_code == "naive":
        # Seasonal naive reference: the actual of the same period one year earlier.
        seq_of, id_at = _bucket_index(db)
        for s in snaps:
            if lag is not None and s["lag"] != lag:
                continue
            a = actual.get((s["series_key"], s["target_bucket_id"]))
            if a is None:
                continue
            seq = seq_of.get(s["target_bucket_id"])
            prior_id = id_at.get(seq - 12) if seq is not None else None
            f = actual.get((s["series_key"], prior_id)) if prior_id else None
            if f is None:
                continue
            out.append({"series_key": s["series_key"], "bucket_id": s["target_bucket_id"],
                        "lag": s["lag"], "forecast": f, "actual": a, "cycle_id": s["cycle_id"]})
        return out

    for s in snaps:
        if lag is not None and s["lag"] != lag:
            continue
        a = actual.get((s["series_key"], s["target_bucket_id"]))
        if a is None:
            continue
        out.append({"series_key": s["series_key"], "bucket_id": s["target_bucket_id"],
                    "lag": s["lag"], "forecast": float(s["value"]), "actual": a, "cycle_id": s["cycle_id"]})
    return out


def _bucket_index(db: Session) -> tuple[dict[int, int], dict[int, int]]:
    rows = db.execute(
        select(TimeBucket.id, TimeBucket.seq).where(TimeBucket.grain == "month")
    ).all()
    seq_of = {bid: seq for bid, seq in rows}
    id_at = {seq: bid for bid, seq in rows}
    return seq_of, id_at


def metrics(pairs: list[dict]) -> dict:
    if not pairs:
        return {"wmape": None, "mape": None, "mae": None, "rmse": None, "bias": None, "n": 0, "volume": 0.0}
    abs_err = sum(abs(p["forecast"] - p["actual"]) for p in pairs)
    vol = sum(abs(p["actual"]) for p in pairs)
    signed = sum(p["forecast"] - p["actual"] for p in pairs)
    mape_terms = [abs(p["forecast"] - p["actual"]) / abs(p["actual"]) for p in pairs if p["actual"]]
    return {
        "wmape": round(abs_err / vol * 100, 2) if vol else None,
        "mape": round(sum(mape_terms) / len(mape_terms) * 100, 2) if mape_terms else None,
        "mae": round(abs_err / len(pairs), 2),
        "rmse": round(math.sqrt(sum((p["forecast"] - p["actual"]) ** 2 for p in pairs) / len(pairs)), 2),
        "bias": round(signed / vol * 100, 2) if vol else None,
        "n": len(pairs),
        "volume": round(vol, 1),
    }


def accuracy_summary(db: Session, layer_code: str = "consensus", lag: int | None = None) -> dict:
    return metrics(_pairs(db, layer_code, lag))


def snapshot_values(
    db: Session,
    layer_code: str,
    lag: int = 1,
    series_keys: set[str] | None = None,
    bucket_ids: set[int] | None = None,
) -> dict[tuple[str, int], float]:
    """Bir katmanın geçmiş dönemlerdeki tahmini, seri ve dönem bazında.

    layer_value geçmiş dönemleri tutmuyor (yalnızca gerçekleşen tutuluyor), bu
    yüzden "bu katman geçmişte ne demişti" sorusunun tek kaynağı snapshot'tır.
    Zaten doğrusu da budur: snapshot, gerçekleşen gelmeden önce donmuş tahmindir.
    """
    store = get_store()
    out: dict[tuple[str, int], float] = {}
    for s in store.read_snapshots(layer_codes=[layer_code]):
        if s["lag"] != lag:
            continue
        if series_keys is not None and s["series_key"] not in series_keys:
            continue
        if bucket_ids is not None and s["target_bucket_id"] not in bucket_ids:
            continue
        out[(s["series_key"], s["target_bucket_id"])] = float(s["value"])
    return out


def metrics_for(
    db: Session,
    layer_code: str,
    lag: int = 1,
    series_keys: set[str] | None = None,
    bucket_ids: set[int] | None = None,
) -> dict:
    """Tek katmanın seçili kapsam ve dönem penceresindeki hata metrikleri."""
    pairs = _pairs(db, layer_code, lag, series_keys)
    if bucket_ids is not None:
        pairs = [p for p in pairs if p["bucket_id"] in bucket_ids]
    return metrics(pairs)


def accuracy_by_lag(db: Session, layer_code: str = "consensus", max_lag: int = 12) -> list[dict]:
    pairs = _pairs(db, layer_code)
    by_lag: dict[int, list[dict]] = defaultdict(list)
    for p in pairs:
        if p["lag"] <= max_lag:
            by_lag[p["lag"]].append(p)
    return [{"lag": lag, **metrics(by_lag[lag])} for lag in sorted(by_lag)]


def accuracy_by_period(
    db: Session,
    layer_code: str = "consensus",
    lag: int | None = 1,
    limit: int = 24,
    series_keys: set[str] | None = None,
) -> list[dict]:
    """Accuracy of each closed period, newest last. Feeds the cockpit trend chart."""
    pairs = _pairs(db, layer_code, lag, series_keys)
    if not pairs:
        return []
    by_bucket: dict[int, list[dict]] = defaultdict(list)
    for p in pairs:
        by_bucket[p["bucket_id"]].append(p)
    buckets = {
        b.id: b for b in db.execute(
            select(TimeBucket).where(TimeBucket.id.in_(list(by_bucket) or [-1]))
        ).scalars()
    }
    rows = [
        {
            "bucket_id": bid,
            "label": buckets[bid].label if bid in buckets else str(bid),
            "seq": buckets[bid].seq if bid in buckets else 0,
            **metrics(ps),
        }
        for bid, ps in by_bucket.items()
        if bid in buckets
    ]
    rows.sort(key=lambda r: r["seq"])
    return rows[-limit:]


def accuracy_by_series(db: Session, layer_code: str = "consensus", lag: int | None = 1, limit: int = 20) -> list[dict]:
    pairs = _pairs(db, layer_code, lag)
    by_series: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_series[p["series_key"]].append(p)
    rows = [{"series_key": sk, **metrics(ps)} for sk, ps in by_series.items()]
    rows.sort(key=lambda r: (-(r["wmape"] or 0) * (r["volume"] or 0)))
    out = rows[:limit]
    labels = _series_labels(db, [r["series_key"] for r in out])
    for r in out:
        r["label"] = labels.get(r["series_key"], r["series_key"][:8])
    return out


def accuracy_by_level(db: Session, level: dict[str, str], layer_code: str = "consensus", lag: int | None = 1) -> list[dict]:
    pairs = _pairs(db, layer_code, lag)
    gmap = group_map(db, level, {p["series_key"] for p in pairs})
    buckets: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        g = gmap.get(p["series_key"])
        if g:
            buckets[g].append(p)
    labels = group_labels(db, set(buckets))
    return [
        {"group": g, "label": labels.get(g, {}).get("label", g), **metrics(ps)}
        for g, ps in sorted(buckets.items(), key=lambda kv: labels.get(kv[0], {}).get("label", ""))
    ]


def bias_heatmap(db: Session, level: dict[str, str], layer_code: str = "consensus", lag: int = 1) -> dict:
    pairs = _pairs(db, layer_code, lag)
    gmap = group_map(db, level, {p["series_key"] for p in pairs})
    bucket_labels = {
        b.id: b.label for b in db.execute(select(TimeBucket).where(TimeBucket.grain == "month")).scalars()
    }
    cells: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for p in pairs:
        g = gmap.get(p["series_key"])
        if g:
            cells[(g, p["bucket_id"])].append(p)
    groups = sorted({g for g, _ in cells})
    bids = sorted({b for _, b in cells})
    labels = group_labels(db, set(groups))
    return {
        "rows": [{"group": g, "label": labels.get(g, {}).get("label", g)} for g in groups],
        "columns": [{"bucket_id": b, "label": bucket_labels.get(b, str(b))} for b in bids],
        "cells": [
            {"group": g, "bucket_id": b, "bias": metrics(cells[(g, b)])["bias"]}
            for g in groups
            for b in bids
            if (g, b) in cells
        ],
    }


def fva_ladder(db: Session, lag: int | None = 1) -> dict:
    """Each step reports its contribution in WMAPE points."""
    steps: list[dict] = []
    prev: float | None = None
    for code in FVA_LADDER:
        m = metrics(_pairs(db, code, lag))
        if m["wmape"] is None:
            continue
        contribution = None if prev is None else round(prev - m["wmape"], 2)
        steps.append(
            {
                "layer": code,
                "label": LAYER_LABELS.get(code, code),
                "wmape": m["wmape"],
                "bias": m["bias"],
                "n": m["n"],
                "contribution": contribution,
            }
        )
        prev = m["wmape"]
    total = None
    if len(steps) >= 2:
        total = round(steps[0]["wmape"] - steps[-1]["wmape"], 2)
    return {"lag": lag, "steps": steps, "total_value_added": total}


def fva_by_user_and_reason(db: Session, lag: int | None = 1) -> dict:
    """Did the manual change help. Overrides joined with the realised error."""
    stat = {(p["series_key"], p["bucket_id"]): p for p in _pairs(db, "statistical", lag)}
    cons = {(p["series_key"], p["bucket_id"]): p for p in _pairs(db, "consensus", lag)}
    rows = db.execute(select(Override).where(Override.reverted.is_(False))).scalars().all()
    reasons = {r.id: r.name for r in db.execute(select(ReasonCode)).scalars()}
    users = {u.id: u.name for u in db.execute(select(_user_model())).scalars()}

    by_user: dict[int, list[float]] = defaultdict(list)
    by_reason: dict[int, list[float]] = defaultdict(list)
    for o in rows:
        if not o.series_key:
            continue
        key = (o.series_key, o.bucket_id)
        s, c = stat.get(key), cons.get(key)
        if not s or not c or not s["actual"]:
            continue
        gain = (abs(s["forecast"] - s["actual"]) - abs(c["forecast"] - c["actual"])) / abs(s["actual"]) * 100
        by_user[o.user_id].append(gain)
        by_reason[o.reason_code_id].append(gain)

    return {
        "by_user": [
            {"user": users.get(uid, str(uid)), "n": len(v), "avg_gain_points": round(sum(v) / len(v), 2)}
            for uid, v in sorted(by_user.items())
        ],
        "by_reason": [
            {"reason": reasons.get(rid, str(rid)), "n": len(v), "avg_gain_points": round(sum(v) / len(v), 2)}
            for rid, v in sorted(by_reason.items())
        ],
    }


def _user_model():
    from app.domain.models import User

    return User


def _series_labels(db: Session, keys: list[str]) -> dict[str, str]:
    rows = db.execute(
        select(Series.series_key, Series.dim_codes).where(Series.series_key.in_(keys or [""]))
    ).all()
    return {sk: " / ".join(v for _, v in sorted(codes.items())) for sk, codes in rows}


# --------------------------------------------------------------------------
# Variance, waterfall and budget gap
# --------------------------------------------------------------------------


def variance(
    db: Session, version_a: int, version_b: int, level: dict[str, str], layer_code: str = "consensus",
    bucket_ids: list[int] | None = None,
) -> dict:
    a = layers.read_values(db, version_a, layer_code, None, bucket_ids)
    b = layers.read_values(db, version_b, layer_code, None, bucket_ids)
    keys = {k for k, _ in a} | {k for k, _ in b}
    gmap = group_map(db, level, keys)
    agg_a: dict[str, float] = defaultdict(float)
    agg_b: dict[str, float] = defaultdict(float)
    for (sk, _bid), v in a.items():
        g = gmap.get(sk)
        if g:
            agg_a[g] += v
    for (sk, _bid), v in b.items():
        g = gmap.get(sk)
        if g:
            agg_b[g] += v
    labels = group_labels(db, set(agg_a) | set(agg_b))
    rows = []
    for g in set(agg_a) | set(agg_b):
        va, vb = agg_a.get(g, 0.0), agg_b.get(g, 0.0)
        rows.append(
            {
                "group": g,
                "label": labels.get(g, {}).get("label", g),
                "from": round(va, 1),
                "to": round(vb, 1),
                "delta": round(vb - va, 1),
                "delta_pct": round((vb / va - 1) * 100, 2) if va else None,
            }
        )
    rows.sort(key=lambda r: -abs(r["delta"]))
    return {
        "total_from": round(sum(agg_a.values()), 1),
        "total_to": round(sum(agg_b.values()), 1),
        "total_delta": round(sum(agg_b.values()) - sum(agg_a.values()), 1),
        "rows": rows,
    }


def budget_gap(db: Session, version_id: int, bucket_ids: list[int]) -> dict:
    cons = layers.read_values(db, version_id, "consensus", None, bucket_ids)
    bud = layers.read_values(db, version_id, "budget", None, bucket_ids)
    buckets = {
        b.id: b for b in db.execute(select(TimeBucket).where(TimeBucket.id.in_(bucket_ids or [-1]))).scalars()
    }
    order = sorted(bucket_ids, key=lambda b: buckets[b].seq if b in buckets else 0)
    rows, cum_c, cum_b = [], 0.0, 0.0
    for bid in order:
        c = sum(v for (sk, b), v in cons.items() if b == bid)
        bg = sum(v for (sk, b), v in bud.items() if b == bid)
        cum_c += c
        cum_b += bg
        rows.append(
            {
                "bucket_id": bid,
                "label": buckets[bid].label if bid in buckets else str(bid),
                "consensus": round(c, 1),
                "budget": round(bg, 1),
                "gap": round(c - bg, 1),
                "gap_pct": round((c / bg - 1) * 100, 2) if bg else None,
                "cum_consensus": round(cum_c, 1),
                "cum_budget": round(cum_b, 1),
                "cum_gap": round(cum_c - cum_b, 1),
            }
        )
    return {
        "rows": rows,
        "total_consensus": round(cum_c, 1),
        "total_budget": round(cum_b, 1),
        "total_gap": round(cum_c - cum_b, 1),
        "total_gap_pct": round((cum_c / cum_b - 1) * 100, 2) if cum_b else None,
    }
