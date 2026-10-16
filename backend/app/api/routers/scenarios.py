"""Scenarios, rules, runs and versions."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.api.deps import current_principal, require_roles
from app.core.auth import Principal
from app.core.db import get_session
from app.domain.models import (
    DriverSet,
    ModelRegistry,
    Run,
    Scenario,
    ScenarioRule,
    TimeBucket,
    User,
    Version,
)
from app.domain.series import scope_hash
from app.forecast_models import runner
from app.services import audit, cycle_svc, layers
from app.services.scenario_rules import RULE_LABELS, RULE_TYPES

router = APIRouter(prefix="/api/v1", tags=["Senaryo"])


class ScenarioIn(BaseModel):
    name: str
    description: str = ""
    model_code: str = Field(description="Model registry kodu")
    driver_set_id: int | None = None
    base_version_id: int | None = None
    scope: dict = Field(default_factory=dict)
    level: dict[str, str] = Field(default_factory=dict)
    grain: str = "month"
    horizon_periods: int | None = None
    model_params: dict = Field(default_factory=dict)


class RuleIn(BaseModel):
    type: str = Field(description=f"Kural tipi: {', '.join(RULE_TYPES)}")
    name: str = ""
    target_scope: dict = Field(default_factory=dict)
    bucket_from: str | None = None
    bucket_to: str | None = None
    params: dict = Field(default_factory=dict)
    order_index: int | None = None


def _scenario_payload(db: Session, s: Scenario) -> dict:
    model = db.get(ModelRegistry, s.model_registry_id)
    ds = db.get(DriverSet, s.driver_set_id) if s.driver_set_id else None
    rules = db.execute(
        select(ScenarioRule).where(ScenarioRule.scenario_id == s.id).order_by(ScenarioRule.order_index)
    ).scalars().all()
    runs = db.execute(select(Run).where(Run.scenario_id == s.id).order_by(Run.id.desc()).limit(5)).scalars().all()
    return {
        "id": s.id, "name": s.name, "description": s.description,
        "model": {"code": model.code, "name": model.name, "version": model.version} if model else None,
        "driver_set": {"id": ds.id, "name": ds.name} if ds else None,
        "base_version_id": s.base_version_id, "scope": s.scope, "level": s.level, "grain": s.grain,
        "horizon_periods": s.horizon_periods, "model_params": s.model_params,
        "rules": [
            {
                "id": r.id, "order": r.order_index, "type": r.type,
                "type_label": RULE_LABELS.get(r.type, r.type), "name": r.name,
                "target_scope": r.target_scope, "bucket_from": r.bucket_from, "bucket_to": r.bucket_to,
                "params": r.params, "enabled": r.enabled,
            }
            for r in rules
        ],
        "recent_runs": [
            {"id": r.id, "status": r.status, "series": r.series_count, "duration_ms": r.duration_ms,
             "error": r.error}
            for r in runs
        ],
    }


@router.get("/scenarios", summary="Senaryo listesi")
def list_scenarios(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(Scenario).order_by(Scenario.id)).scalars().all()
    return {
        "scenarios": [_scenario_payload(db, s) for s in rows],
        "rule_types": [{"code": c, "label": RULE_LABELS[c]} for c in RULE_TYPES],
        "summary": f"{len(rows)} senaryo",
    }


@router.post("/scenarios", summary="Senaryo oluştur")
def create_scenario(
    payload: ScenarioIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_roles("planner", "marketing_contributor", "sales_contributor", "finance")),
) -> dict:
    model = db.execute(select(ModelRegistry).where(ModelRegistry.code == payload.model_code)).scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="Model bulunamadi")
    cycle = cycle_svc.active_cycle(db)
    s = Scenario(
        name=payload.name, description=payload.description, model_registry_id=model.id,
        driver_set_id=payload.driver_set_id, base_version_id=payload.base_version_id,
        cycle_id=cycle.id if cycle else None, scope=payload.scope, grain=payload.grain,
        level=payload.level, horizon_periods=payload.horizon_periods or (cycle.horizon_periods if cycle else 18),
        model_params=payload.model_params, owner_user_id=principal.id,
    )
    db.add(s)
    db.flush()
    audit.record(db, "scenario", s.id, "create", principal.id, actor_kind=principal.actor_kind,
                 after={"name": s.name})
    db.commit()
    return {"id": s.id, "summary": f"{s.name} senaryosu oluşturuldu"}


@router.post("/scenarios/{scenario_id}/copy", summary="Senaryoyu kopyala")
def copy_scenario(
    scenario_id: int,
    name: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    src = db.get(Scenario, scenario_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Senaryo bulunamadi")
    clone = Scenario(
        name=name, description=src.description, base_version_id=src.base_version_id,
        driver_set_id=src.driver_set_id, model_registry_id=src.model_registry_id, cycle_id=src.cycle_id,
        scope=src.scope, grain=src.grain, level=src.level, horizon_periods=src.horizon_periods,
        model_params=src.model_params, owner_user_id=principal.id,
    )
    db.add(clone)
    db.flush()
    for r in db.execute(select(ScenarioRule).where(ScenarioRule.scenario_id == src.id)).scalars():
        db.add(
            ScenarioRule(
                scenario_id=clone.id, order_index=r.order_index, type=r.type, name=r.name,
                target_scope=r.target_scope, bucket_from=r.bucket_from, bucket_to=r.bucket_to,
                params=r.params, enabled=r.enabled,
            )
        )
    audit.record(db, "scenario", clone.id, "copy", principal.id, after={"from": src.id})
    db.commit()
    return {"id": clone.id, "summary": f"{src.name} senaryosu {name} olarak kopyalandı"}


@router.post("/scenarios/{scenario_id}/rules", summary="Kural ekle")
def add_rule(
    scenario_id: int,
    payload: RuleIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    if payload.type not in RULE_TYPES:
        raise HTTPException(status_code=400, detail=f"Geçersiz kural tipi. Secenekler: {', '.join(RULE_TYPES)}")
    if db.get(Scenario, scenario_id) is None:
        raise HTTPException(status_code=404, detail="Senaryo bulunamadi")
    next_order = payload.order_index
    if next_order is None:
        existing = db.execute(
            select(ScenarioRule.order_index).where(ScenarioRule.scenario_id == scenario_id)
        ).scalars().all()
        next_order = (max(existing) + 1) if existing else 0
    rule = ScenarioRule(
        scenario_id=scenario_id, order_index=next_order, type=payload.type,
        name=payload.name or RULE_LABELS.get(payload.type, payload.type),
        target_scope=payload.target_scope, bucket_from=payload.bucket_from,
        bucket_to=payload.bucket_to, params=payload.params,
    )
    db.add(rule)
    db.flush()
    audit.record(db, "scenario_rule", rule.id, "create", principal.id, after={"type": rule.type})
    db.commit()
    return {"id": rule.id, "order": rule.order_index, "summary": f"{rule.name} kuralı eklendi"}


@router.delete("/scenarios/rules/{rule_id}", summary="Kuralı sil")
def delete_rule(
    rule_id: int, db: Session = Depends(get_session), principal: Principal = Depends(current_principal)
) -> dict:
    rule = db.get(ScenarioRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Kural bulunamadi")
    audit.record(db, "scenario_rule", rule_id, "delete", principal.id, before={"type": rule.type})
    db.delete(rule)
    db.commit()
    return {"ok": True, "summary": "Kural silindi"}


class ReorderIn(BaseModel):
    rule_ids: list[int] = Field(description="Yeni sıra")


@router.post("/scenarios/{scenario_id}/rules/reorder", summary="Kural sirasini değiştir")
def reorder_rules(
    scenario_id: int,
    payload: ReorderIn,
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    for i, rid in enumerate(payload.rule_ids):
        rule = db.get(ScenarioRule, rid)
        if rule and rule.scenario_id == scenario_id:
            rule.order_index = i
    db.commit()
    return {"ok": True, "summary": "Kural sırası güncellendi"}


class RunIn(BaseModel):
    scenario_ids: list[int] = Field(description="Bir veya birden fazla senaryo")


@router.post("/runs", summary="Senaryoyu çalıştır")
def start_runs(
    payload: RunIn,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    created: list[dict] = []
    for sid in payload.scenario_ids:
        s = db.get(Scenario, sid)
        if s is None:
            continue
        run = Run(
            scenario_id=s.id, model_registry_id=s.model_registry_id, cycle_id=s.cycle_id,
            status="queued", scope_hash=scope_hash(s.scope or {}), started_by=principal.id,
        )
        db.add(run)
        db.flush()
        audit.record(db, "run", run.id, "queue", principal.id, actor_kind=principal.actor_kind,
                     after={"scenario": s.name})
        created.append({"run_id": run.id, "scenario": s.name})
    db.commit()
    for c in created:
        background.add_task(runner.execute_run, c["run_id"])
    return {
        "runs": created,
        "summary": f"{len(created)} çalıştırma kuyruga alındı, durum çalıştırma gecmisinde izlenebilir",
    }


@router.get("/runs", summary="Çalıştırma geçmişi")
def list_runs(
    limit: int = 50, db: Session = Depends(get_session), _p: Principal = Depends(current_principal)
) -> dict:
    rows = db.execute(select(Run).order_by(Run.id.desc()).limit(limit)).scalars().all()
    scen = {s.id: s.name for s in db.execute(select(Scenario)).scalars()}
    models = {m.id: f"{m.code} {m.version}" for m in db.execute(select(ModelRegistry)).scalars()}
    versions = {
        v.run_id: {"id": v.id, "name": v.name, "kind": v.kind}
        for v in db.execute(select(Version).where(Version.run_id.isnot(None))).scalars()
    }
    return {
        "runs": [
            {
                "id": r.id, "scenario": scen.get(r.scenario_id, ""), "model": models.get(r.model_registry_id, ""),
                "status": r.status, "series_count": r.series_count, "duration_ms": r.duration_ms,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "data_as_of": r.data_as_of.isoformat() if r.data_as_of else None,
                "error": r.error, "diagnostics": r.diagnostics, "version": versions.get(r.id),
            }
            for r in rows
        ],
        "summary": f"{len(rows)} çalıştırma",
    }


@router.get("/runs/{run_id}", summary="Çalıştırma durumu")
def get_run(run_id: int, db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Çalıştırma bulunamadi")
    version = db.execute(select(Version).where(Version.run_id == run_id)).scalar_one_or_none()
    return {
        "id": run.id, "status": run.status, "series_count": run.series_count,
        "duration_ms": run.duration_ms, "error": run.error, "diagnostics": run.diagnostics,
        "version": {"id": version.id, "name": version.name, "kind": version.kind} if version else None,
        "summary": f"Çalıştırma {run.status}" + (f", hata: {run.error}" if run.error else ""),
    }


@router.get("/versions", summary="Versiyon listesi")
def list_versions(
    kind: str | None = None, db: Session = Depends(get_session), _p: Principal = Depends(current_principal)
) -> dict:
    q = select(Version).order_by(Version.id.desc())
    if kind:
        q = q.where(Version.kind == kind)
    rows = db.execute(q).scalars().all()
    scen = {s.id: s.name for s in db.execute(select(Scenario)).scalars()}
    users = {u.id: u.name for u in db.execute(select(User)).scalars()}
    return {
        "versions": [
            {
                "id": v.id, "name": v.name, "kind": v.kind, "status": v.status,
                "scenario": scen.get(v.scenario_id, ""), "run_id": v.run_id,
                "parent_version_id": v.parent_version_id, "notes": v.notes,
                "provenance": v.provenance,
                "created_by": users.get(v.created_by, ""),
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "expires_at": v.expires_at.isoformat() if v.expires_at else None,
            }
            for v in rows
        ],
        "summary": f"{len(rows)} versiyon",
    }


class SaveVersionIn(BaseModel):
    name: str = Field(description="Versiyon adı zorunludur")
    notes: str = ""
    kind: str = Field(default="saved", description="saved, baseline, consensus_candidate")


@router.post("/versions/{version_id}/save", summary="Taslağı sakla")
def save_version(
    version_id: int,
    payload: SaveVersionIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    v = db.get(Version, version_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Versiyon bulunamadi")
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Versiyon adı zorunludur")
    before = {"kind": v.kind, "name": v.name}
    v.name = payload.name.strip()
    v.notes = payload.notes
    v.kind = payload.kind
    v.expires_at = None
    audit.record(db, "version", v.id, "save", principal.id, actor_kind=principal.actor_kind,
                 before=before, after={"kind": v.kind, "name": v.name})
    db.commit()
    return {"id": v.id, "summary": f"{v.name} saklandı, senaryo tanımı versiyona donduruldu"}


@router.delete("/versions/{version_id}", summary="Taslağı at")
def discard_version(
    version_id: int,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    from sqlalchemy import delete as sql_delete

    from app.domain.models import LayerValue

    v = db.get(Version, version_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Versiyon bulunamadi")
    if v.kind != "draft":
        raise HTTPException(status_code=400, detail="Yalnizca taslak atılabilir")
    db.execute(sql_delete(LayerValue).where(LayerValue.version_id == version_id))
    audit.record(db, "version", version_id, "discard", principal.id, before={"name": v.name})
    db.delete(v)
    db.commit()
    return {"ok": True, "summary": "Taslak atıldı"}


@router.get("/versions/compare", summary="Versiyon ve senaryo karşılaştırma")
def compare_versions(
    ids: str,
    layer: str = "consensus",
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    version_ids = [int(x) for x in ids.split(",") if x.strip()]
    if len(version_ids) < 2:
        raise HTTPException(status_code=400, detail="En az iki versiyon seçilmeli")
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    bucket_ids = [b.id for b in window]
    labels = {b.id: b.label for b in window}
    store = get_store()

    columns = []
    base_total = None
    for vid in version_ids:
        v = db.get(Version, vid)
        if v is None:
            continue
        vals = layers.read_values(db, vid, layer, None, bucket_ids) or layers.read_values(
            db, vid, "statistical", None, bucket_ids
        )
        per_bucket: dict[str, float] = {}
        for (sk, bid), value in vals.items():
            per_bucket[labels.get(bid, str(bid))] = per_bucket.get(labels.get(bid, str(bid)), 0.0) + value
        total = sum(per_bucket.values())
        if base_total is None:
            base_total = total
        rule_effects = []
        if v.run_id:
            effects = store.read_rule_effects(v.run_id)
            agg: dict[int, float] = {}
            for e in effects:
                agg[e["rule_id"]] = agg.get(e["rule_id"], 0.0) + e["delta"]
            names = {r["id"]: r for r in (v.provenance or {}).get("rules", [])}
            rule_effects = [
                {
                    "rule_id": rid, "name": names.get(rid, {}).get("name", f"Kural {rid}"),
                    "type": names.get(rid, {}).get("type", ""), "delta": round(d, 1),
                }
                for rid, d in sorted(agg.items(), key=lambda kv: -abs(kv[1]))
            ]
        columns.append(
            {
                "version_id": v.id, "name": v.name, "kind": v.kind, "status": v.status,
                "provenance": v.provenance,
                "per_bucket": {k: round(val, 1) for k, val in per_bucket.items()},
                "total": round(total, 1),
                "delta_vs_base": round(total - base_total, 1),
                "delta_pct": round((total / base_total - 1) * 100, 2) if base_total else None,
                "rule_effects": rule_effects,
            }
        )
    return {
        "layer": layer,
        "buckets": [b.label for b in window],
        "columns": columns,
        "summary": f"{len(columns)} versiyon karşılaştırıldı",
    }


@router.get("/buckets", summary="Dönem listesi")
def buckets(
    grain: str = "month", db: Session = Depends(get_session), _p: Principal = Depends(current_principal)
) -> dict:
    cycle = cycle_svc.active_cycle(db)
    rows = db.execute(
        select(TimeBucket)
        .where(TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == grain)
        .order_by(TimeBucket.seq)
    ).scalars().all()
    flags = cycle_svc.period_flags(db, cycle)
    return {
        "buckets": [
            {"id": b.id, "label": b.label, "seq": b.seq, "working_days": b.working_days,
             "flag": flags.get(b.id, "open")}
            for b in rows
        ],
        "summary": f"{len(rows)} kova",
    }
