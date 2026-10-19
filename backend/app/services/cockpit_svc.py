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

# Pencere varsayilani: start/end verilmediginde mevcut dönemin bu kadar
# oncesinden baslayip bu kadar sonrasinda biter. Kokpit bir tahmin ekrani,
# gerceklesen kuyrugu tahminden uzun olunca grafigin agirligi gecmise kayiyor.
DEFAULT_BACK = 12
DEFAULT_FORWARD = 18
GRAIN_CHOICES = [("month", "Ay"), ("quarter", "Çeyrek"), ("year", "Yıl")]
VIEW_CHOICES = [("nominal", "Nominal TL"), ("real", "Reel TL"), ("fx", "Döviz")]
REFERENCE_LAYERS = ["budget", "last_year"]

# Kokpitte karsilastirilan modeller, sirayla Model 1..N.
#
# Her modelin bir "dusme zinciri" var. Katkici katmanlari (satis, planlamaci)
# gelecekte yalnizca dokunulan hucreleri tasiyor; ham haliyle okunursa toplam
# istatistikselin yuzde uc'u kadar cikiyor ve model cokmuş gibi gorunuyor.
# Dogrusu her katmani "kendi degeri, yoksa bir altindaki" diye okumak: konsensus
# kurali (planner > sales > statistical) zaten boyle calisiyor ve gecmis
# snapshot'lar da boyle uretilmis. Boylece dort model de tam bir tahmin olur.
MODEL_CHAINS = [
    ("statistical", ["statistical"]),
    ("sales", ["sales", "statistical"]),
    ("planner", ["planner", "sales", "statistical"]),
    ("consensus", ["consensus"]),
]
MODEL_CODES = [code for code, _chain in MODEL_CHAINS]
MODEL_LABELS = {code: f"Model {i + 1}" for i, (code, _c) in enumerate(MODEL_CHAINS)}
NEEDED_LAYERS = sorted({c for _m, chain in MODEL_CHAINS for c in chain} | set(REFERENCE_LAYERS))
# Tabloda kendi gecmis/gelecek sutun ciftini tasiyan olculer.
HEADLINE_MEASURES = ["units", "revenue"]
AGGREGATES = [("total", "Toplam"), ("average", "Ortalama")]
# Butce farki her zaman adet uzerinden okunur: butce adet cinsinden tutuluyor
# ve olcu secimi artik yalnizca ana grafigin filtresi.
GAP_MEASURE = "units"
DEFAULT_LAG = 1
DEFAULT_MODELS = ["statistical", "consensus"]


@dataclass
class CockpitQuery:
    version_id: int | None = None
    measure: str = "units"
    grain: str = "month"
    # Pencere iki donem etiketinden ibaret (ornek: 2026-01). Bos birakilirsa
    # mevcut donemin etrafinda varsayilan pencere kurulur.
    start_label: str | None = None
    end_label: str | None = None
    lag: int = DEFAULT_LAG
    # Karsilastirilacak modeller; tablo satirlari ve grafik cizgileri bu secim.
    models: list[str] = field(default_factory=lambda: list(DEFAULT_MODELS))
    # Butce farki grafigi tek bir model uzerinden okunur, secenekleri models'dan.
    gap_model: str | None = None
    # Toplam mi donem ortalamasi mi; tablo ve grafiklerin tamami buna uyar.
    aggregate: str = "total"
    scope: dict = field(default_factory=dict)
    view: str = "nominal"
    currency: str = "TRY"

    def selected_models(self) -> list[str]:
        picked = [c for c in MODEL_CODES if c in (self.models or [])]
        return picked or list(DEFAULT_MODELS)

    def selected_gap_model(self) -> str:
        picked = self.selected_models()
        return self.gap_model if self.gap_model in picked else picked[-1]


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

    Sınır mevcut dönem: o ve sonrası gelecek, öncesi geçmiş. Uçlardan biri
    verilmezse ya da takvimde yoksa varsayılan pencereye düşülür; ekran hiçbir
    durumda boş kalmaz.
    """
    rows = list(db.execute(
        select(TimeBucket)
        .where(TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == cycle.grain)
        .order_by(TimeBucket.seq)
    ).scalars())
    by_label = {b.label: b for b in rows}
    start = by_label.get(q.start_label or "")
    end = by_label.get(q.end_label or "")
    if start is None:
        start = by_label.get(_label_at(rows, current, -DEFAULT_BACK))
    if end is None:
        end = by_label.get(_label_at(rows, current, DEFAULT_FORWARD - 1))
    # Uclar ters verilmisse duzelt; kullanici bitisi baslangictan geri cekebilir.
    if start is not None and end is not None and start.seq > end.seq:
        start, end = end, start

    picked = [
        b for b in rows
        if (start is None or b.seq >= start.seq) and (end is None or b.seq <= end.seq)
    ]
    if not picked:
        picked = rows
    return ([b for b in picked if b.seq < current.seq],
            [b for b in picked if b.seq >= current.seq])


def _label_at(rows: list[TimeBucket], current: TimeBucket, offset: int) -> str:
    """Mevcut dönemden offset kadar uzaktaki kovanın etiketi; taşarsa uç kova."""
    if not rows:
        return ""
    index = next((i for i, b in enumerate(rows) if b.id == current.id), 0)
    return rows[max(0, min(len(rows) - 1, index + offset))].label


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
            "timeline": {"buckets": [], "current_index": -1, "freeze_index": -1,
                         "freeze_periods": cycle.freeze_periods, "models": [], "series": {}},
            "table": {"column_groups": [], "columns": [], "rows": [], "lag": q.lag},
            "accuracy": {"rows": [], "lag": q.lag, "budget_periods": 0},
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

    def shaped(values, measure_code):
        scaled = _scale(db, values, measure_code, q.view, q.currency, scope_keys, month_ids, cache)
        return _roll_up(db, _totals(scaled), cycle.grain, q.grain)

    # Grafik yalnizca gerceklesen gostermemeli: "gecmiste ne tahmin ettik, ne
    # cikti" sorusu ekranin ana sorusu. Gecmis donemlerin tahmini snapshot'tan
    # gelir; layer_value gecmisi tutmuyor ve zaten dogrusu snapshot'tir.
    past_ids = {b.id for b in history}
    selected = q.selected_models()
    model_raw = {
        code: _effective_model(db, layer_raw, chain, past_ids, scope_keys, q.lag or DEFAULT_LAG, acc)
        for code, chain in MODEL_CHAINS if code in selected
    }
    # Gecen yil, gecmis bir donem icin 12 ay oncesinin gerceklesenidir.
    past_last_year = _shifted_actuals(db, store, cycle, scope_keys, history, 12)

    series_out = {"actual": shaped(actual_raw, q.measure)}
    for code, values in model_raw.items():
        series_out[code] = shaped(values, q.measure)
    series_out["budget"] = shaped(layer_raw["budget"], q.measure)
    last_year_all = dict(past_last_year)
    last_year_all.update({k: v for k, v in layer_raw["last_year"].items() if k[1] not in past_ids})
    series_out["last_year"] = shaped(last_year_all, q.measure)

    target_buckets = _target_buckets(db, cycle, q.grain, month_buckets)
    flags = cycle_svc.period_flags(db, cycle)
    current_index = _current_index(target_buckets, current, q.grain)
    # Ortalama modunda her kova, icine giren ay sayisina bolunur. Pencerenin
    # ucundaki yarim ceyrekler eksik ay tasiyabildigi icin bolen sabit degil,
    # kova basina sayilir.
    span = _bucket_spans(db, cycle, q.grain, month_buckets, target_buckets)
    divisor = (lambda bid: span.get(bid, 1) or 1) if q.aggregate == "average" else (lambda _bid: 1)

    def column(code):
        out = series_out.get(code, {})
        return [round(out[b.id] / divisor(b.id), 2) if b.id in out else None for b in target_buckets]

    # Donma penceresi: mevcut dönemden itibaren cevrimin freeze_periods kadari
    # degistirilemez. Grafikte bu araligi tarayarak gosteriyoruz, sayi ekrandan
    # degil cevrim ayarindan gelir.
    freeze_index = _freeze_index(target_buckets, current, cycle, q.grain, current_index)

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
        "freeze_index": freeze_index,
        "freeze_periods": cycle.freeze_periods,
        "models": [{"code": c, "label": MODEL_LABELS[c], "points": column(c)} for c in selected],
        "series": {
            "actual": column("actual"),
            "budget": column("budget"),
            "last_year": column("last_year"),
        },
    }

    table = _model_table(db, q, cycle, scope_keys, history, future, month_ids,
                         actual_raw, model_raw, layer_raw, cache)
    accuracy = _accuracy_table(db, q, scope_keys, past_ids, future_ids, model_raw, layer_raw)
    gap_model = q.selected_gap_model()
    # Butce farki adet uzerinden; olcu secimi yalnizca ana grafigi ilgilendirir.
    # Secili her model icin ayri seri doner, boylece grafigin kendi model
    # seciciyi degistirmek sunucuya yeni bir istek gerektirmez.
    gap_series = {code: shaped(values, GAP_MEASURE) for code, values in model_raw.items()}
    gap_series["budget"] = shaped(layer_raw["budget"], GAP_MEASURE)
    charts = _charts(q, target_buckets, gap_series, divisor)

    measure = db.execute(select(Measure).where(Measure.code == q.measure)).scalar_one_or_none()
    headline = selected[-1]
    total = sum(series_out[headline].values())
    return {
        "cycle": _cycle_block(db, cycle, current),
        "version": _version_block(version),
        "options": _options(db, cycle, principal),
        "timeline": timeline,
        "table": table,
        "accuracy": accuracy,
        "charts": charts,
        "meta": {
            "measure": q.measure,
            "measure_name": measure.name if measure else q.measure,
            "unit": measure.base_unit if measure else "",
            "unit_type": measure.unit_type if measure else "quantity",
            "grain": q.grain, "view": q.view, "currency": q.currency,
            "aggregate": q.aggregate,
            "models": selected,
            "gap_model": gap_model,
            "scope": q.scope or {},
            "start_label": history[0].label if history else (future[0].label if future else ""),
            "end_label": future[-1].label if future else (history[-1].label if history else ""),
            "horizon_periods": len(future), "history_periods": len(history),
            "series_in_scope": len(scope_keys),
            "restricted": not principal.unrestricted,
        },
        "summary": (
            f"{MODEL_LABELS[headline]}: {len(future)} dönemlik toplam {total:,.0f}, "
            f"{len(scope_keys)} seri, {q.grain} kovası"
        ),
    }


def _effective_model(db, layer_raw, chain, past_ids, scope_keys, lag, acc_mod) -> dict:
    """Bir modelin pencere boyunca kesintisiz değer dizisi.

    Geçmiş snapshot'tan gelir ve orada her katman zaten tam. Gelecekte katkıcı
    katmanları seyrek olduğu için zincir boyunca düşülür: kendi değeri yoksa
    bir altındaki katmanın değeri. Konsensüs kuralının mantığı budur.
    """
    head = chain[0]
    out = dict(acc_mod.snapshot_values(db, head, lag, scope_keys, past_ids))
    for code in reversed(chain):
        for key, value in layer_raw.get(code, {}).items():
            if key[1] not in past_ids:
                out[key] = value
    return out


def _bucket_spans(db, cycle, grain, month_buckets, target_buckets) -> dict[int, int]:
    """Hedef kova başına, pencereden içine giren aylık kova sayısı."""
    if grain == cycle.grain:
        return {b.id: 1 for b in target_buckets}
    spans: dict[int, int] = defaultdict(int)
    for m in month_buckets:
        for t in target_buckets:
            if t.start_date <= m.start_date <= t.end_date:
                spans[t.id] += 1
                break
    return dict(spans)


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


def _freeze_index(target_buckets, current: TimeBucket, cycle: Cycle, grain: str, current_index: int) -> int:
    """Donma penceresinin son kovasının indeksi; yoksa -1.

    Çevrimin freeze_periods değeri aylık dönem sayısıdır. Kova iriyse (çeyrek,
    yıl) o dönemlerin düştüğü son kova aranır, böylece taralı alan her zaman
    gerçek donma sınırında biter.
    """
    if current_index < 0 or cycle.freeze_periods <= 0:
        return -1
    if grain == current.grain:
        return min(len(target_buckets) - 1, current_index + cycle.freeze_periods - 1)
    months = db_month_span(current, cycle.freeze_periods)
    last = current_index
    for i, b in enumerate(target_buckets):
        if b.start_date <= months <= b.end_date:
            last = i
    return max(current_index, last)


def db_month_span(current: TimeBucket, periods: int):
    """Mevcut dönemden periods ay sonrasının başlangıç tarihi."""
    year, month = current.start_date.year, current.start_date.month + periods - 1
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return current.start_date.replace(year=year, month=month, day=1)


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
        "aggregates": [{"code": c, "label": l} for c, l in AGGREGATES],
        # Numaralandirma tek yerde: filtre, tablo ve grafik ayni listeyi kullanir.
        # source yalnizca ipucu icin; ekranda katman adi gecmez.
        "models": [
            {"code": code, "label": MODEL_LABELS[code], "source": _layer_name(db, code)}
            for code, _chain in MODEL_CHAINS
        ],
        # Pencere uclari: cevrimin takvimindeki tum aylik kovalar. Baslangic ve
        # bitis ayni listeden secilir.
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
    db, q, cycle, scope_keys, history, future, month_ids, actual_raw, model_raw, layer_raw, cache,
) -> dict:
    """Model karşılaştırma tablosu: ölçü sütunları.

    Satırlar seçili modeller. Geçmiş sütunu snapshot'tan, gelecek sütunu
    katman değerinden gelir; ikisi de _effective_model içinde birleştirildi.
    Toplam/ortalama ayrımı sütun kırılımı değil, tek bir filtre: aggregate.
    MAPE ve bütçe farkı burada değil, kendi tablosunda.
    """
    past_ids = {b.id for b in history}
    measure_rows = {m.code: m for m in db.execute(select(Measure)).scalars()}
    measure_codes = [c for c in HEADLINE_MEASURES if c in measure_rows]

    past_periods = _period_count(db, cycle, q.grain, history)
    future_periods = _period_count(db, cycle, q.grain, future)
    average = q.aggregate == "average"

    def total(values, measure_code, keep):
        if not values:
            return None
        picked = {k: v for k, v in values.items() if (k[1] in past_ids) == keep}
        if not picked:
            return None
        scaled = _scale(db, picked, measure_code, q.view, q.currency, scope_keys, month_ids, cache)
        return sum(scaled.values())

    def cell(value, periods):
        if value is None:
            return None
        return round(value / periods, 1) if average and periods else round(value, 1)

    # Ölçü üst başlık, altında geçmiş ve gelecek. "Adet geçmiş / Adet gelecek"
    # diye dört düz sütun, hangi ikisinin aynı ölçüye ait olduğunu göstermiyordu.
    column_groups, columns = [], []
    for code in measure_codes:
        column_groups.append({"key": code, "label": measure_rows[code].name, "span": 2})
        for seg, seg_label, periods in (("past", "Geçmiş", past_periods),
                                        ("future", "Gelecek", future_periods)):
            columns.append({"key": f"{code}_{seg}", "label": seg_label,
                            "group": code, "periods": periods})

    def row(code, label, provenance, values, past_only=False):
        cells = {}
        for m in measure_codes:
            cells[f"{m}_past"] = cell(total(values, m, True), past_periods)
            cells[f"{m}_future"] = None if past_only else cell(total(values, m, False), future_periods)
        return {"code": code, "label": label, "provenance": provenance, "cells": cells}

    rows = [row("actual", "Gerçekleşen", "actual", actual_raw, past_only=True)]
    for code in q.selected_models():
        rows.append(row(code, MODEL_LABELS[code], _provenance_of(db, code), model_raw.get(code, {})))

    return {
        "column_groups": column_groups,
        "columns": columns,
        "rows": rows,
        "aggregate": q.aggregate,
        "past_periods": past_periods,
        "future_periods": future_periods,
    }


def _layer_row(db, layer_code: str):
    return db.execute(select(Layer).where(Layer.code == layer_code)).scalar_one_or_none()


def _provenance_of(db, layer_code: str) -> str:
    row = _layer_row(db, layer_code)
    return row.provenance if row else "model"


def _layer_name(db, layer_code: str) -> str:
    row = _layer_row(db, layer_code)
    return row.name if row else layer_code


def _accuracy_table(db, q, scope_keys, past_ids, future_ids, model_raw, layer_raw) -> dict:
    """Doğruluk ve bütçe farkı; ölçü tablosundan ayrı durur.

    Bütçe ufkun tamamını kapsamadığı için karşılaştırma yalnızca bütçesi olan
    dönemler üzerinden yapılır; aksi halde model bütçeyi kat kat aşmış görünür.
    Oran adet üzerinden: bütçe adet cinsinden tutuluyor, fiyat çarpanı iki
    tarafta da sadeleşirdi.
    """
    lag = max(1, int(q.lag or DEFAULT_LAG))
    wanted = set(future_ids)
    budget_future = {k: v for k, v in layer_raw.get("budget", {}).items()
                     if k[1] in wanted and k[1] not in past_ids}
    budget_ids = {bid for _sk, bid in budget_future}
    budget_total = sum(budget_future.values())

    rows = []
    for code in q.selected_models():
        metrics = acc.metrics_for(db, code, lag, scope_keys, past_ids)
        values = model_raw.get(code, {})
        got = sum(v for (_sk, bid), v in values.items() if bid in budget_ids)
        rows.append({
            "code": code,
            "label": MODEL_LABELS[code],
            "provenance": _provenance_of(db, code),
            "mape": metrics["mape"],
            "wmape": metrics["wmape"],
            "n": metrics["n"],
            "budget_gap": round((got / budget_total - 1) * 100, 1) if budget_total and got else None,
        })
    return {"rows": rows, "lag": lag, "budget_periods": len(budget_ids)}


def _charts(q, target_buckets, gap_series, divisor) -> list[dict]:
    """Bütçe farkı: her seçili modelin bütçeye göre sapması.

    Seçili modellerin tamamı döner, grafik aralarından birini gösterir. Ayrı
    ayrı istemek model değiştirmeyi bir sunucu turuna bağlardı; tüm sayfanın
    yenilenmesinin sebebi buydu. Kümülatif çizgi bütçesi olmayan dönemlerde
    sıfırlanmaz, kaldığı yerden devam eder.
    """
    budget = gap_series.get("budget", {})

    def points_for(code):
        model_series = gap_series.get(code, {})
        out, cumulative = [], 0.0
        for b in target_buckets:
            c, g = model_series.get(b.id), budget.get(b.id)
            if c is None or g is None:
                out.append({"label": b.label, "gap": None, "cumulative": None, "pct": None})
                continue
            d = divisor(b.id)
            c, g = c / d, g / d
            cumulative += c - g
            out.append({
                "label": b.label,
                "gap": round(c - g, 1),
                "cumulative": round(cumulative, 1),
                "pct": round((c / g - 1) * 100, 1) if g else None,
            })
        return out

    selected = q.selected_models()
    return [{
        "code": "budget_gap",
        "label": "Bütçe farkı",
        "kind": "bar_line",
        "measure": GAP_MEASURE,
        "model": q.selected_gap_model(),
        "models": [
            {"code": code, "label": MODEL_LABELS.get(code, code), "points": points_for(code)}
            for code in selected
        ],
    }]
