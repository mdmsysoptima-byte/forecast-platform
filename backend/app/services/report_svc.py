"""Template based reports (section 12). Sector configuration can add more."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.domain.models import (
    Alert,
    ModelRegistry,
    Override,
    ReasonCode,
    Run,
    Scenario,
    TimeBucket,
    User,
    Version,
)
from app.services import accuracy, cycle_svc, grid, layers
from app.services.grid import GridQuery

REPORTS = [
    {"code": "forecast_summary", "title": "Tahmin özeti", "params": ["version", "level", "grain", "measure"]},
    {"code": "breakdown", "title": "Kırılım tablosu", "params": ["version", "level", "grain"]},
    {"code": "trend", "title": "Trend", "params": ["version", "level", "layers"]},
    {"code": "scenario_compare", "title": "Senaryo karşılaştırma", "params": ["versions"]},
    {"code": "version_compare", "title": "Versiyon karşılaştırma", "params": ["versions", "level"]},
    {"code": "budget_gap", "title": "Bütçe gap", "params": ["version", "level"]},
    {"code": "accuracy", "title": "Doğruluk raporu", "params": ["lag", "layer"]},
    {"code": "fva", "title": "FVA raporu", "params": ["lag"]},
    {"code": "override_report", "title": "Override raporu", "params": ["version"]},
    {"code": "alert_report", "title": "Uyarı raporu", "params": ["status"]},
    {"code": "demand_review", "title": "Demand review paketi", "params": ["version"]},
    {"code": "run_log", "title": "Çalıştırma günlüğü", "params": []},
]


def list_reports() -> list[dict]:
    return REPORTS


def build(db: Session, principal: Principal, code: str, params: dict) -> dict:
    fn = _BUILDERS.get(code)
    if fn is None:
        raise ValueError(f"Bilinmeyen rapor: {code}")
    return fn(db, principal, params or {})


def _num(key: str, label: str) -> dict:
    return {"key": key, "label": label, "numeric": True}


def _txt(key: str, label: str) -> dict:
    return {"key": key, "label": label}


def _grid_payload(db: Session, principal: Principal, params: dict, layer_codes: list[str]) -> dict:
    q = GridQuery(
        version_id=int(params["version_id"]),
        scope=params.get("scope") or {},
        level=params.get("level") or {},
        grain=params.get("grain", "month"),
        layer_codes=layer_codes,
        measure=params.get("measure", "units"),
        history_periods=int(params.get("history_periods", 0)),
    )
    return grid.query(db, principal, q)


def _forecast_summary(db, principal, params) -> dict:
    codes = ["consensus", "statistical", "budget", "last_year"]
    payload = _grid_payload(db, principal, params, codes)
    columns = [_txt("label", "Kırılım")] + [_num(c, n) for c, n in
                                            (("consensus", "Konsensüs"), ("statistical", "İstatistiksel"),
                                             ("budget", "Bütçe"), ("last_year", "Geçen yıl"))] + [
        _num("growth_pct", "Büyüme yüzde"), _num("vs_budget_pct", "Bütçe farkı yüzde")
    ]
    rows = []
    for r in payload["rows"] + [dict(payload["total"], group="TOTAL", parts=[], node_ids=[])]:
        vals = {c: sum(r["layers"].get(c, {}).values()) for c in codes}
        ly = vals["last_year"]
        bud = vals["budget"]
        rows.append(
            {
                "label": r["label"],
                **{c: round(vals[c], 1) for c in codes},
                "growth_pct": round((vals["consensus"] / ly - 1) * 100, 2) if ly else None,
                "vs_budget_pct": round((vals["consensus"] / bud - 1) * 100, 2) if bud else None,
            }
        )
    return {"code": "forecast_summary", "title": "Tahmin özeti", "columns": columns, "rows": rows,
            "meta": payload["meta"]}


def _breakdown(db, principal, params) -> dict:
    layer = params.get("layer", "consensus")
    payload = _grid_payload(db, principal, params, [layer])
    columns = [_txt("label", "Kırılım")] + [_num(str(c["bucket_id"]), c["label"]) for c in payload["columns"]] + [
        _num("total", "Toplam")
    ]
    rows = []
    for r in payload["rows"]:
        vals = r["layers"].get(layer, {})
        rows.append({"label": r["label"], **{k: v for k, v in vals.items()},
                     "total": round(sum(vals.values()), 1)})
    total = payload["total"]["layers"].get(layer, {})
    rows.append({"label": "Toplam", **total, "total": round(sum(total.values()), 1)})
    return {"code": "breakdown", "title": "Kırılım tablosu", "columns": columns, "rows": rows,
            "meta": payload["meta"]}


def _trend(db, principal, params) -> dict:
    codes = params.get("layers") or ["actual", "statistical", "consensus", "budget"]
    p = dict(params)
    p.setdefault("history_periods", 18)
    payload = _grid_payload(db, principal, p, codes)
    series = []
    for code in codes:
        series.append(
            {
                "layer": code,
                "points": [
                    {"label": c["label"], "value": payload["total"]["layers"].get(code, {}).get(str(c["bucket_id"]))}
                    for c in payload["columns"]
                ],
            }
        )
    return {"code": "trend", "title": "Trend", "columns": [], "rows": [], "chart": series,
            "meta": payload["meta"]}


def _scenario_compare(db, principal, params) -> dict:
    version_ids = [int(v) for v in params.get("version_ids") or []]
    level = params.get("level") or {}
    out_rows: list[dict] = []
    columns = [_txt("label", "Versiyon"), _num("total", "Toplam"), _num("delta", "Baza fark"),
               _num("delta_pct", "Fark yüzde"), _txt("rules", "Kural etkileri")]
    base_total = None
    from app.adapters import get_store

    store = get_store()
    for vid in version_ids:
        v = db.get(Version, vid)
        if v is None:
            continue
        vals = layers.read_values(db, vid, "consensus") or layers.read_values(db, vid, "statistical")
        total = sum(vals.values())
        if base_total is None:
            base_total = total
        rule_text = ""
        if v.run_id:
            effects = store.read_rule_effects(v.run_id)
            agg: dict[int, float] = {}
            for e in effects:
                agg[e["rule_id"]] = agg.get(e["rule_id"], 0.0) + e["delta"]
            names = {r["id"]: r.get("name") for r in (v.provenance or {}).get("rules", [])}
            rule_text = "; ".join(
                f"{names.get(rid, rid)}: {d:+,.0f}" for rid, d in sorted(agg.items())
            )
        out_rows.append(
            {
                "label": v.name, "total": round(total, 1),
                "delta": round(total - base_total, 1),
                "delta_pct": round((total / base_total - 1) * 100, 2) if base_total else None,
                "rules": rule_text,
            }
        )
    return {"code": "scenario_compare", "title": "Senaryo karşılaştırma", "columns": columns,
            "rows": out_rows, "meta": {"level": level}}


def _version_compare(db, principal, params) -> dict:
    ids = [int(v) for v in params.get("version_ids") or []]
    if len(ids) < 2:
        raise ValueError("İki versiyon seçilmeli")
    level = params.get("level") or {}
    res = accuracy.variance(db, ids[0], ids[1], level, params.get("layer", "consensus"))
    columns = [_txt("label", "Kırılım"), _num("from", "Versiyon A"), _num("to", "Versiyon B"),
               _num("delta", "Fark"), _num("delta_pct", "Fark yüzde")]
    return {"code": "version_compare", "title": "Versiyon karşılaştırma", "columns": columns,
            "rows": res["rows"], "meta": {k: v for k, v in res.items() if k != "rows"}}


def _budget_gap(db, principal, params) -> dict:
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    res = accuracy.budget_gap(db, int(params["version_id"]), [b.id for b in window])
    columns = [_txt("label", "Dönem"), _num("consensus", "Konsensüs"), _num("budget", "Bütçe"),
               _num("gap", "Fark"), _num("gap_pct", "Fark yüzde"), _num("cum_gap", "Kumulatif fark")]
    return {"code": "budget_gap", "title": "Bütçe gap", "columns": columns, "rows": res["rows"],
            "meta": {k: v for k, v in res.items() if k != "rows"}}


def _accuracy(db, principal, params) -> dict:
    layer = params.get("layer", "consensus")
    by_lag = accuracy.accuracy_by_lag(db, layer)
    worst = accuracy.accuracy_by_series(db, layer, int(params.get("lag", 1)), limit=20)
    columns = [_num("lag", "Lag"), _num("wmape", "WMAPE"), _num("mape", "MAPE"), _num("mae", "MAE"),
               _num("rmse", "RMSE"), _num("bias", "Bias"), _num("n", "Gözlem")]
    return {
        "code": "accuracy", "title": "Doğruluk raporu", "columns": columns, "rows": by_lag,
        "extra": {"worst": worst}, "meta": {"layer": layer},
    }


def _fva(db, principal, params) -> dict:
    lag = int(params.get("lag", 1))
    ladder = accuracy.fva_ladder(db, lag)
    detail = accuracy.fva_by_user_and_reason(db, lag)
    columns = [_txt("label", "Katman"), _num("wmape", "WMAPE"), _num("contribution", "Katkı puani"),
               _num("bias", "Bias"), _num("n", "Gözlem")]
    return {"code": "fva", "title": "FVA raporu", "columns": columns, "rows": ladder["steps"],
            "extra": detail, "meta": {"lag": lag, "total_value_added": ladder["total_value_added"]}}


def _override_report(db, principal, params) -> dict:
    version_id = params.get("version_id")
    q = select(Override).order_by(Override.id.desc()).limit(500)
    if version_id:
        q = q.where(Override.version_id == int(version_id))
    rows = db.execute(q).scalars().all()
    users = {u.id: u.name for u in db.execute(select(User)).scalars()}
    reasons = {r.id: r.name for r in db.execute(select(ReasonCode)).scalars()}
    buckets = {b.id: b.label for b in db.execute(select(TimeBucket)).scalars()}
    lm = {l.id: l.name for l in layers.layer_map(db).values()}
    out = [
        {
            "id": o.id, "user": users.get(o.user_id, ""), "layer": lm.get(o.layer_id, ""),
            "scope": o.entry_node_label or (o.series_key[:8] if o.series_key else ""),
            "bucket": buckets.get(o.bucket_id, ""), "mode": o.mode,
            "old": round(o.old_value or 0, 1), "new": round(o.new_value, 1),
            "delta": round(o.new_value - (o.old_value or 0), 1),
            "reason": reasons.get(o.reason_code_id, ""), "note": o.note,
            "reverted": "evet" if o.reverted else "hayır",
            "at": o.created_at.isoformat() if o.created_at else "",
        }
        for o in rows
        if o.allocated_from_override_id is None
    ]
    columns = [_txt("user", "Kullanıcı"), _txt("layer", "Katman"), _txt("scope", "Kapsam"),
               _txt("bucket", "Dönem"), _num("old", "Eski"), _num("new", "Yeni"), _num("delta", "Fark"),
               _txt("reason", "Gerekçe"), _txt("note", "Not"), _txt("reverted", "Geri alındı")]
    return {"code": "override_report", "title": "Override raporu", "columns": columns, "rows": out, "meta": {}}


def _alert_report(db, principal, params) -> dict:
    from app.services.alerts import list_alerts

    rows = list_alerts(db, params.get("status") or None, limit=500)
    columns = [_txt("type_label", "Tip"), _txt("severity", "Önem"), _txt("title", "Açıklama"),
               _txt("status", "Durum")]
    return {"code": "alert_report", "title": "Uyarı raporu", "columns": columns, "rows": rows, "meta": {}}


def _demand_review(db, principal, params) -> dict:
    version_id = int(params["version_id"])
    version = db.get(Version, version_id)
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    gap = accuracy.budget_gap(db, version_id, [b.id for b in window])
    var = None
    if version and version.parent_version_id:
        var = accuracy.variance(db, version.parent_version_id, version_id, params.get("level") or {})
    from app.services.alerts import list_alerts

    return {
        "code": "demand_review",
        "title": "Demand review paketi",
        "columns": [],
        "rows": [],
        "sections": {
            "cycle": {
                "name": cycle.name if cycle else "", "status": cycle.status if cycle else "",
                "current_period": db.get(TimeBucket, cycle.current_period_bucket_id).label if cycle else "",
                "approval_trail": cycle_svc.approval_trail(db, version_id),
            },
            "version": {
                "name": version.name if version else "", "kind": version.kind if version else "",
                "provenance": version.provenance if version else {},
            },
            "budget_gap": {k: v for k, v in gap.items() if k != "rows"},
            "biggest_changes": (var or {}).get("rows", [])[:10],
            "exceptions": list_alerts(db, "open", limit=20),
            "accuracy": accuracy.accuracy_summary(db, "consensus", 1),
            "fva": accuracy.fva_ladder(db, 1),
        },
        "meta": {"version_id": version_id},
    }


def _run_log(db, principal, params) -> dict:
    rows = db.execute(select(Run).order_by(Run.id.desc()).limit(100)).scalars().all()
    scen = {s.id: s.name for s in db.execute(select(Scenario)).scalars()}
    models = {m.id: f"{m.code} {m.version}" for m in db.execute(select(ModelRegistry)).scalars()}
    out = [
        {
            "id": r.id, "scenario": scen.get(r.scenario_id, ""), "model": models.get(r.model_registry_id, ""),
            "status": r.status, "series": r.series_count, "duration_ms": r.duration_ms,
            "started_at": r.started_at.isoformat() if r.started_at else "",
            "error": (r.error or "")[:200],
        }
        for r in rows
    ]
    columns = [_num("id", "Çalıştırma"), _txt("scenario", "Senaryo"), _txt("model", "Model"),
               _txt("status", "Durum"), _num("series", "Seri"), _num("duration_ms", "Süre ms"),
               _txt("started_at", "Başlangıç"), _txt("error", "Hata")]
    return {"code": "run_log", "title": "Çalıştırma günlüğü", "columns": columns, "rows": out, "meta": {}}


_BUILDERS = {
    "forecast_summary": _forecast_summary,
    "breakdown": _breakdown,
    "trend": _trend,
    "scenario_compare": _scenario_compare,
    "version_compare": _version_compare,
    "budget_gap": _budget_gap,
    "accuracy": _accuracy,
    "fva": _fva,
    "override_report": _override_report,
    "alert_report": _alert_report,
    "demand_review": _demand_review,
    "run_log": _run_log,
}
