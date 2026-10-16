"""Cockpit read model.

The cockpit answers one question: what does the company expect to sell, and how
does that compare with last year, with the budget and with how accurate we have
been. Everything is aggregated to the requested scope and period grain, and
history and forecast arrive in the same timeline so the handover at the current
period is visible.

Operational lists (alerts, runs, approvals) are not here; each lives on its own
screen.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.core.auth import Principal
from app.domain.models import (
    Cycle, Dimension, Hierarchy, HierarchyNode, Layer, Measure, Member, TimeBucket, Version,
)
from app.services import accuracy as acc
from app.services import cycle_svc, layers, measures
from app.services.hierarchy import resolve_scope
from app.services.reconciliation import convert_grain

HORIZON_CHOICES = [6, 12, 18]
# Geçmiş seçenekleri ufukla aynı: kokpit bir tahmin ekranı, gerçekleşen kuyruğu
# tahmin penceresinden uzun olunca grafiğin ağırlığı geçmişe kayıyor.
HISTORY_CHOICES = [6, 12, 18]
GRAIN_CHOICES = [("month", "Ay"), ("quarter", "Çeyrek"), ("year", "Yıl")]
VIEW_CHOICES = [("nominal", "Nominal TL"), ("real", "Reel TL"), ("fx", "Döviz")]
CHART_LAYERS = ["statistical", "consensus", "budget", "last_year"]
# Karsilastirma tablosunda satir olan katmanlar, sirayla Model 1..N olarak
# etiketlenir. Butce burada yok: snapshot'i olmadigi icin MAPE'si hesaplanamaz,
# zaten kendi grafiginde duruyor. Pazarlama da yok: gelecek donemlerde hic
# degeri olmadigi icin satiri bastan sona bos kaliyordu.
TABLE_LAYERS = ["statistical", "sales", "planner", "consensus"]
NEEDED_LAYERS = sorted(set(CHART_LAYERS) | set(TABLE_LAYERS))
# Tabloda kendi gecmis/gelecek sutun ciftini tasiyan olculer.
HEADLINE_MEASURES = ["units", "revenue"]
WINDOW_MODES = [("periods", "Dönem sayısı"), ("range", "Tarih aralığı")]
DEFAULT_LAG = 1


@dataclass
class CockpitQuery:
    version_id: int | None = None
    measure: str = "units"
    grain: str = "month"
    # Pencere iki uc noktadan ibaret; window_mode yalnizca ifade bicimini
    # degistirir. "periods" modunda start/end gecmis ve gelecek donem sayisidir,
    # "range" modunda dogrudan donem etiketidir (ornek: 2026-01).
    window_mode: str = "periods"
    horizon_periods: int | None = None
    history_periods: int = 12
    start_label: str | None = None
    end_label: str | None = None
    lag: int = DEFAULT_LAG
    scope: dict = field(default_factory=dict)
    view: str = "nominal"
    currency: str = "TRY"


def _pick_version(db: Session, version_id: int | None) -> Version | None:
    if version_id:
        found = db.get(Version, version_id)
        if found is not None:
            return found
    for kind in ("consensus", "baseline", "saved", "snapshot"):
        found = db.execute(
            select(Version).where(Version.kind == kind).order_by(Version.id.desc())
        ).scalars().first()
        if found is not None:
            return found
    return db.execute(select(Version).order_by(Version.id.desc())).scalars().first()


def _window(db: Session, cycle: Cycle, current: TimeBucket, q: CockpitQuery):
    """Pencerenin iki ucunu aylık kovalara çevirir: (geçmiş, gelecek).

    Sınır her iki modda aynı: mevcut dönem ve sonrası gelecek, öncesi geçmiş.
    """
    if q.window_mode == "range" and (q.start_label or q.end_label):
        rows = list(db.execute(
            select(TimeBucket)
            .where(TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == cycle.grain)
            .order_by(TimeBucket.seq)
        ).scalars())
        by_label = {b.label: b for b in rows}
        start = by_label.get(q.start_label or "")
        end = by_label.get(q.end_label or "")
        picked = [
            b for b in rows
            if (start is None or b.seq >= start.seq) and (end is None or b.seq <= end.seq)
        ]
        if picked:
            return ([b for b in picked if b.seq < current.seq],
                    [b for b in picked if b.seq >= current.seq])

    horizon = max(1, min(int(q.horizon_periods or cycle.horizon_periods), cycle.horizon_periods))
    future = cycle_svc.forecast_window(db, cycle)[:horizon]
    history = cycle_svc.history_window(db, cycle, periods=max(1, int(q.history_periods)))
    return history, future


def _period_count(db: Session, cycle: Cycle, grain: str, months: list[TimeBucket]) -> int:
    """Seçili kovada bu aylık dilimin kaç döneme denk geldiği. Ortalama böleni."""
    if not months:
        return 0
    if grain == cycle.grain:
        return len(months)
    return len(_target_buckets(db, cycle, grain, months))


def _scale(
    db: Session,
    values: dict[tuple[str, int], float],
    measure_code: str,
    view: str,
    currency: str,
    scope_keys: set[str],
    bucket_ids: list[int],
    cache: dict,
) -> dict[tuple[str, int], float]:
    """Apply the derived measure factor and the currency view at monthly grain."""
    key = (measure_code, view, currency)
    if key not in cache:
        from app.services.grid import GridQuery, _conversion_factors

        q = GridQuery(version_id=0, measure=measure_code, view=view, currency=currency)
        buckets = db.execute(
            select(TimeBucket).where(TimeBucket.id.in_(bucket_ids or [-1]))
        ).scalars().all()
        cache[key] = (
            measures.price_factors(db, measure_code, scope_keys, bucket_ids),
            _conversion_factors(db, buckets, q),
        )
    price, view_factors = cache[key]
    if not price and all(f == 1.0 for f in view_factors.values()):
        return values
    return {
        (sk, bid): v * price.get((sk, bid), 1.0) * view_factors.get(bid, 1.0)
        for (sk, bid), v in values.items()
    }


def _totals(values: dict[tuple[str, int], float]) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for (_sk, bid), v in values.items():
        out[bid] += v
    return dict(out)


def _roll_up(db: Session, per_bucket: dict[int, float], from_grain: str, to_grain: str) -> dict[int, float]:
    if from_grain == to_grain or not per_bucket:
        return per_bucket
    shaped = {("total", bid): v for bid, v in per_bucket.items()}
    converted = convert_grain(db, shaped, from_grain, to_grain)
    return {bid: v for (_k, bid), v in converted.items()}


def overview(db: Session, principal: Principal, q: CockpitQuery) -> dict:
    cycle = cycle_svc.active_cycle(db)
    if cycle is None:
        raise ValueError("Aktif çevrim yok")
    version = _pick_version(db, q.version_id)
    if version is None:
        raise ValueError("Gösterilecek versiyon yok")

    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    history, future = _window(db, cycle, current, q)
    month_buckets = history + future
    month_ids = [b.id for b in month_buckets]
    future_ids = [b.id for b in future]

    scope_keys = resolve_scope(db, q.scope or {}, None if principal.unrestricted else principal.view_nodes)
    if not scope_keys:
        return {
            "cycle": _cycle_block(db, cycle, current),
            "version": _version_block(version),
            "options": _options(db, cycle, principal),
            "timeline": {"buckets": [], "current_index": -1, "series": {}},
            "table": {"column_groups": [], "columns": [], "rows": [], "lag": q.lag},
            "charts": [], "summary": "Kapsamda seri yok",
        }

    store = get_store()
    cache: dict = {}

    # Actuals come straight from the analytic store; layer_value only keeps the
    # last 24 periods while the store holds the full history.
    actual_raw: dict[tuple[str, int], float] = {}
    if history:
        for row in store.read_series(sorted(scope_keys), history[0].id, history[-1].id):
            actual_raw[(row["series_key"], row["bucket_id"])] = float(row["value"])

    # Butce gecmis donemleri de kapsar, bu yuzden pencerenin tamami istenir;
    # digerleri icin layer_value yalnizca gelecegi tasidigindan fark etmez.
    layer_raw = {
        code: layers.read_values(db, version.id, code, scope_keys, month_ids)
        for code in NEEDED_LAYERS
    }
    band_raw = _model_band(db, store, version, scope_keys, future_ids)

    def shaped(values, measure_code):
        scaled = _scale(db, values, measure_code, q.view, q.currency, scope_keys, month_ids, cache)
        return _roll_up(db, _totals(scaled), cycle.grain, q.grain)

    # Grafik yalnizca gerceklesen gostermemeli: "gecmiste ne tahmin ettik, ne
    # cikti" sorusu ekranin ana sorusu. Gecmis donemlerin tahmini snapshot'tan
    # gelir; layer_value gecmisi tutmuyor ve zaten dogrusu snapshot'tir.
    past_ids = {b.id for b in history}
    past_forecast = {
        code: acc.snapshot_values(db, code, q.lag or DEFAULT_LAG, scope_keys, past_ids)
        for code in ("statistical", "consensus")
    }
    # Gecen yil, gecmis bir donem icin 12 ay oncesinin gerceklesenidir.
    past_last_year = _shifted_actuals(db, store, cycle, scope_keys, history, 12)

    def joined(code):
        """Geçmiş ve gelecek tek bir seride; çizgi mevcut dönemde kesilmez."""
        merged = dict(past_forecast.get(code, {}))
        merged.update({k: v for k, v in layer_raw[code].items() if k[1] not in past_ids})
        return merged

    series_out = {"actual": shaped(actual_raw, q.measure)}
    series_out["statistical"] = shaped(joined("statistical"), q.measure)
    series_out["consensus"] = shaped(joined("consensus"), q.measure)
    series_out["budget"] = shaped(layer_raw["budget"], q.measure)
    last_year_all = dict(past_last_year)
    last_year_all.update({k: v for k, v in layer_raw["last_year"].items() if k[1] not in past_ids})
    series_out["last_year"] = shaped(last_year_all, q.measure)

    # Uncertainty adds in quadrature under an independence assumption, both
    # across series and across the periods folded into a coarser bucket.
    band_scaled = _scale(db, band_raw, q.measure, q.view, q.currency, scope_keys, month_ids, cache)
    variance = defaultdict(float)
    for (_sk, bid), half in band_scaled.items():
        variance[bid] += half * half
    variance = _roll_up(db, dict(variance), cycle.grain, q.grain)

    target_buckets = _target_buckets(db, cycle, q.grain, month_buckets)
    flags = cycle_svc.period_flags(db, cycle)
    current_index = _current_index(target_buckets, current, q.grain)

    def column(code):
        return [round(series_out[code].get(b.id), 2) if b.id in series_out[code] else None
                for b in target_buckets]

    stat = column("statistical")
    half = [round(math.sqrt(variance.get(b.id, 0.0)), 2) if variance.get(b.id) else None
            for b in target_buckets]
    lower = [round(max(0.0, s - h), 2) if s is not None and h else None for s, h in zip(stat, half)]
    upper = [round(s + h, 2) if s is not None and h else None for s, h in zip(stat, half)]

    timeline = {
        "buckets": [
            {
                "id": b.id, "label": b.label, "grain": b.grain,
                "flag": flags.get(b.id, "actual" if b.seq < current.seq else "open"),
                "is_current": _is_current(b, current, q.grain),
            }
            for b in target_buckets
        ],
        "current_index": current_index,
        "series": {
            "actual": column("actual"),
            "statistical": stat,
            "consensus": column("consensus"),
            "budget": column("budget"),
            "last_year": column("last_year"),
            "lower": lower,
            "upper": upper,
        },
    }

    table = _model_table(
        db, q, cycle, scope_keys, history, future, month_ids, actual_raw, layer_raw, cache,
    )
    charts = _charts(db, q, target_buckets, series_out, current_index)

    measure = db.execute(select(Measure).where(Measure.code == q.measure)).scalar_one_or_none()
    total = sum(v for v in series_out["consensus"].values())
    return {
        "cycle": _cycle_block(db, cycle, current),
        "version": _version_block(version),
        "options": _options(db, cycle, principal),
        "timeline": timeline,
        "table": table,
        "charts": charts,
        "meta": {
            "measure": q.measure,
            "measure_name": measure.name if measure else q.measure,
            "unit": measure.base_unit if measure else "",
            "unit_type": measure.unit_type if measure else "quantity",
            "grain": q.grain, "view": q.view, "currency": q.currency,
            "scope": q.scope or {},
            "window_mode": q.window_mode,
            "start_label": history[0].label if history else (future[0].label if future else ""),
            "end_label": future[-1].label if future else (history[-1].label if history else ""),
            "horizon_periods": len(future), "history_periods": len(history),
            "series_in_scope": len(scope_keys),
            "restricted": not principal.unrestricted,
        },
        "summary": (
            f"{version.name}: {len(future)} dönemlik konsensüs toplamı {total:,.0f}, "
            f"{len(scope_keys)} seri, {q.grain} kovası"
        ),
    }


def _shifted_actuals(db, store, cycle, scope_keys, history, months_back: int) -> dict:
    """Geçmiş pencerenin "geçen yıl" karşılığı: N ay öncesinin gerçekleşeni."""
    if not history:
        return {}
    rows = db.execute(
        select(TimeBucket.id, TimeBucket.seq)
        .where(TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == cycle.grain)
    ).all()
    seq_of = {bid: seq for bid, seq in rows}
    id_at = {seq: bid for bid, seq in rows}
    wanted = {}
    for b in history:
        prior = id_at.get(seq_of.get(b.id, -10 ** 6) - months_back)
        if prior is not None:
            wanted[prior] = b.id
    if not wanted:
        return {}
    ids = sorted(wanted)
    out: dict[tuple[str, int], float] = {}
    for row in store.read_series(sorted(scope_keys), ids[0], ids[-1]):
        target = wanted.get(row["bucket_id"])
        if target is not None:
            out[(row["series_key"], target)] = float(row["value"])
    return out


def _model_band(db: Session, store, version: Version, scope_keys: set[str], future_ids: list[int]) -> dict:
    """Half width of the model interval per series and period."""
    if not version.run_id:
        return {}
    wanted = set(future_ids)
    out: dict[tuple[str, int], float] = {}
    for row in store.read_run_result(version.run_id):
        if row["series_key"] not in scope_keys or row["bucket_id"] not in wanted:
            continue
        lower, upper = row.get("lower"), row.get("upper")
        if lower is None or upper is None:
            continue
        out[(row["series_key"], row["bucket_id"])] = max(0.0, (float(upper) - float(lower)) / 2.0)
    return out


def _target_buckets(db: Session, cycle: Cycle, grain: str, month_buckets: list[TimeBucket]) -> list[TimeBucket]:
    if grain == cycle.grain:
        return month_buckets
    first, last = month_buckets[0], month_buckets[-1]
    rows = db.execute(
        select(TimeBucket)
        .where(
            TimeBucket.calendar_id == cycle.calendar_id,
            TimeBucket.grain == grain,
            TimeBucket.end_date >= first.start_date,
            TimeBucket.start_date <= last.end_date,
        )
        .order_by(TimeBucket.seq)
    ).scalars().all()
    return list(rows)


def _is_current(bucket: TimeBucket, current: TimeBucket, grain: str) -> bool:
    if grain == current.grain:
        return bucket.id == current.id
    return bucket.start_date <= current.start_date <= bucket.end_date


def _current_index(buckets: list[TimeBucket], current: TimeBucket, grain: str) -> int:
    for i, b in enumerate(buckets):
        if _is_current(b, current, grain):
            return i
    return -1


def _cycle_block(db: Session, cycle: Cycle, current: TimeBucket) -> dict:
    return {
        "id": cycle.id, "name": cycle.name, "status": cycle.status,
        "status_label": cycle_svc.STEP_LABELS.get(cycle.status, cycle.status),
        "current_period": current.label if current else "",
        "horizon_periods": cycle.horizon_periods,
        "fence_periods": cycle.fence_periods,
        "freeze_periods": cycle.freeze_periods,
        "grain": cycle.grain,
    }


def _version_block(version: Version) -> dict:
    return {
        "id": version.id, "name": version.name, "kind": version.kind,
        "status": version.status, "provenance": version.provenance,
    }


def _options(db: Session, cycle: Cycle, principal: Principal) -> dict:
    versions = db.execute(
        select(Version).where(Version.kind.in_(["baseline", "saved", "consensus", "snapshot", "draft"]))
        .order_by(Version.id.desc())
    ).scalars().all()
    measures_rows = db.execute(select(Measure)).scalars().all()
    scopes = []
    for dim in db.execute(select(Dimension).order_by(Dimension.position)).scalars():
        hier = db.execute(
            select(Hierarchy).where(Hierarchy.dimension_id == dim.id).order_by(Hierarchy.is_default.desc())
        ).scalars().first()
        if hier is None:
            continue
        nodes = db.execute(
            select(HierarchyNode.id, Member.name)
            .join(Member, Member.id == HierarchyNode.member_id)
            .where(HierarchyNode.hierarchy_id == hier.id, HierarchyNode.level_index == 1)
            .order_by(Member.name)
        ).all()
        if not nodes:
            continue
        scopes.append({
            "dimension": dim.code, "name": dim.name,
            "level": hier.levels[1] if len(hier.levels) > 1 else hier.levels[0],
            "nodes": [{"node_id": nid, "label": name} for nid, name in nodes],
        })
    return {
        "versions": [{"id": v.id, "name": v.name, "kind": v.kind} for v in versions],
        "measures": [{"code": m.code, "name": m.name, "unit_type": m.unit_type, "unit": m.base_unit}
                     for m in measures_rows],
        "grains": [{"code": c, "label": l} for c, l in GRAIN_CHOICES],
        "views": [{"code": c, "label": l} for c, l in VIEW_CHOICES],
        "horizons": [h for h in HORIZON_CHOICES if h <= cycle.horizon_periods] or [cycle.horizon_periods],
        "histories": HISTORY_CHOICES,
        "window_modes": [{"code": c, "label": l} for c, l in WINDOW_MODES],
        # Tarih araligi modunun secenekleri: cevrimin takvimindeki tum aylik
        # kovalar. Baslangic ve bitis ayni listeden secilir.
        "periods": [
            {"label": b.label, "seq": b.seq}
            for b in db.execute(
                select(TimeBucket)
                .where(TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == cycle.grain)
                .order_by(TimeBucket.seq)
            ).scalars()
        ],
        "currencies": ["USD", "EUR"],
        "scopes": scopes,
    }


def _model_table(
    db, q, cycle, scope_keys, history, future, month_ids, actual_raw, layer_raw, cache,
) -> dict:
    """Katman karşılaştırma tablosu.

    Her satır bir tahmin üreticisi. Geçmiş sütunu snapshot'tan gelir, çünkü
    layer_value geçmiş dönemleri tutmuyor ve zaten doğrusu snapshot'tır:
    gerçekleşen gelmeden önce donmuş tahmin. Gelecek sütunu seçili versiyonun
    katman değerinden gelir. MAPE aynı pencere ve aynı kapsamla hesaplanır.
    """
    lag = max(1, int(q.lag or DEFAULT_LAG))
    past_ids = {b.id for b in history}
    measure_rows = {m.code: m for m in db.execute(select(Measure)).scalars()}
    measure_codes = [c for c in HEADLINE_MEASURES if c in measure_rows]
    layer_rows = {
        row.code: row
        for row in db.execute(select(Layer).where(Layer.code.in_(TABLE_LAYERS))).scalars()
    }

    past_periods = _period_count(db, cycle, q.grain, history)
    future_periods = _period_count(db, cycle, q.grain, future)
    show_avg = q.grain != cycle.grain

    def total(values, measure_code):
        if not values:
            return None
        scaled = _scale(db, values, measure_code, q.view, q.currency, scope_keys, month_ids, cache)
        return sum(scaled.values())

    def cell(value, periods):
        if value is None:
            return {"total": None, "avg": None}
        return {
            "total": round(value, 1),
            "avg": round(value / periods, 1) if show_avg and periods else None,
        }

    column_groups, columns = [], []
    for code in measure_codes:
        name = measure_rows[code].name
        for seg, seg_label, periods in (("past", "geçmiş", past_periods),
                                        ("future", "gelecek", future_periods)):
            key = f"{code}_{seg}"
            column_groups.append({"key": key, "label": f"{name} {seg_label}", "span": 2 if show_avg else 1})
            columns.append({"key": key, "part": "total", "label": "Toplam" if show_avg else name,
                            "group": key, "periods": periods})
            if show_avg:
                columns.append({"key": key, "part": "avg", "label": "Ortalama",
                                "group": key, "periods": periods})
    column_groups.append({"key": "mape", "label": "", "span": 1})
    columns.append({"key": "mape", "part": "total", "label": "MAPE", "group": "mape", "periods": None})
    column_groups.append({"key": "budget_gap", "label": "", "span": 1})
    columns.append({"key": "budget_gap", "part": "total", "label": "Bütçe farkı",
                    "group": "budget_gap", "periods": None})

    # Butce yalnizca bir bolum donemi kapsiyor; karsilastirmayi tum ufka yaymak
    # modeli "butceyi ikiye katlamis" gibi gosterir. Iki taraf da butcenin
    # bulundugu donemlerle sinirli. Oran adet uzerinden: butce adet cinsinden
    # tutuluyor, fiyat carpani iki tarafta da sadelesirdi.
    budget_future = {k: v for k, v in layer_raw.get("budget", {}).items() if k[1] not in past_ids}
    budget_ids = {bid for _sk, bid in budget_future}
    budget_total = sum(budget_future.values())

    def budget_gap(future_values):
        if not budget_total or not future_values:
            return None
        got = sum(v for (_sk, bid), v in future_values.items() if bid in budget_ids)
        if not got:
            return None
        return round((got / budget_total - 1) * 100, 1)

    # Satis ve Planlamaci gibi katkici katmanlari tam bir tahmin degil, yalnizca
    # dokunulan hucrelerden olusan bir ortu. Toplamlari Istatistiksel'in yanina
    # ciplak yazmak "Satis 20 bin adet tahmin etti" gibi okunur. Doluluk oranini
    # satirla birlikte tasiyoruz ki ekran kismi oldugunu soyleyebilsin.
    expected_future = len(scope_keys) * len(future)

    def row(code, label, provenance, past_values, future_values, mape, note=None, source=None):
        cells = {}
        for m in measure_codes:
            cells[f"{m}_past"] = cell(total(past_values, m) if past_values is not None else None, past_periods)
            cells[f"{m}_future"] = cell(total(future_values, m) if future_values is not None else None,
                                        future_periods)
        filled = len(future_values or {})
        coverage = round(filled / expected_future, 4) if expected_future else None
        partial = bool(coverage is not None and 0 < coverage < 0.99)
        return {
            "code": code, "label": label, "provenance": provenance,
            "source": source,
            "cells": cells, "mape": mape, "note": note,
            # Kismi bir ortuyu butcenin tamamiyla karsilastirmak "butcenin %96
            # altinda" gibi okunur; dolu olmayan hucreler sifir sayilmis olur.
            # Karsilastirma anlamsizsa sayi uretmiyoruz.
            "budget_gap": None if partial else budget_gap(future_values),
            "future_coverage": coverage,
            "future_partial": partial,
        }

    # Satirlar "Model 1..N" diye numaralanir; hangi katman oldugu yalnizca
    # ipucunda durur. Ekranda katman adi gorunmuyor, cunku bu tablo tahmin
    # ureticilerini karsilastiriyor, katman sozlugunu ogretmiyor.
    rows = [row("actual", "Gerçekleşen", "actual", actual_raw, None, None)]
    n = 0
    for code in TABLE_LAYERS:
        layer = layer_rows.get(code)
        if layer is None:
            continue
        n += 1
        snap = acc.snapshot_values(db, code, lag, scope_keys, past_ids)
        m = acc.metrics_for(db, code, lag, scope_keys, past_ids)
        future_vals = {k: v for k, v in layer_raw.get(code, {}).items() if k[1] not in past_ids}
        rows.append(row(
            code, f"Model {n}", layer.provenance,
            snap or None, future_vals or None, m["mape"],
            note=None if m["n"] else "bu pencerede snapshot yok",
            source=layer.name,
        ))

    return {
        "column_groups": column_groups,
        "columns": columns,
        "rows": rows,
        "lag": lag,
        "show_avg": show_avg,
        "past_periods": past_periods,
        "future_periods": future_periods,
        "budget_periods": len(budget_ids),
    }


def _charts(db, q, target_buckets, series_out, current_index) -> list[dict]:
    labels = [b.label for b in target_buckets]
    out: list[dict] = []

    cons, budget = series_out["consensus"], series_out["budget"]
    gap_points, cumulative = [], 0.0
    for b, label in zip(target_buckets, labels):
        c, g = cons.get(b.id), budget.get(b.id)
        if c is None or g is None:
            gap_points.append({"label": label, "gap": None, "cumulative": None})
            continue
        cumulative += c - g
        gap_points.append({
            "label": label,
            "gap": round(c - g, 1),
            "cumulative": round(cumulative, 1),
            "pct": round((c / g - 1) * 100, 1) if g else None,
        })

    out.append({
        "code": "budget_gap",
        "label": "Bütçe farkı",
        "note": "Dönem bazında fark ve kümülatif seyir",
        "kind": "bar_line",
        "points": gap_points,
    })
    return out
