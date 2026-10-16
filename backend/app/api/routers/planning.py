"""Forecast grid, cell editing, insights and the approval flow."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_principal
from app.core.auth import Principal
from app.core.db import get_session
from app.domain.models import Insight, ReasonCode, TimeBucket, User
from app.services import cycle_svc, grid, override_svc
from app.services.grid import GridQuery

router = APIRouter(prefix="/api/v1", tags=["Planlama"])


class GridRequest(BaseModel):
    version_id: int = Field(description="Okunacak versiyon")
    scope: dict = Field(default_factory=dict, description="Boyut başına düğüm, grup veya üye seçimi")
    level: dict[str, str] = Field(default_factory=dict, description="Boyut başına tahmin seviyesi")
    grain: str = Field(default="month", description="day, week, month, quarter, year")
    layers: list[str] = Field(default_factory=list, description="Katman kodlari")
    measure: str = "units"
    currency: str = "TRY"
    view: str = Field(default="nominal", description="nominal, real, fx")
    history_periods: int = 12
    include_subtotals: bool = True


@router.post("/grid/query", summary="Tahmin grid'ini oku")
def grid_query(
    payload: GridRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    q = GridQuery(
        version_id=payload.version_id, scope=payload.scope, level=payload.level, grain=payload.grain,
        layer_codes=payload.layers or list(grid.DEFAULT_LAYERS), measure=payload.measure,
        currency=payload.currency, view=payload.view, history_periods=payload.history_periods,
        include_subtotals=payload.include_subtotals,
    )
    try:
        result = grid.query(db, principal, q)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["summary"] = (
        f"{len(result['rows'])} satır, {len(result['columns'])} dönem, "
        f"{result['meta'].get('series_in_scope', 0)} seri"
    )
    return result


class ExplainRequest(BaseModel):
    version_id: int
    layer: str
    group: str
    bucket_id: int
    level: dict[str, str] = Field(default_factory=dict)


@router.post("/grid/explain", summary="Bu sayı nereden geldi")
def explain(
    payload: ExplainRequest,
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    data = grid.explain_cell(
        db, payload.version_id, payload.layer, payload.group, payload.bucket_id, payload.level
    )
    data["summary"] = (
        f"{data['bucket']['label']} dönemi, {payload.layer} katmanı, "
        f"{len(data['rule_effects'])} kural etkisi, {len(data['override_history'])} override kaydı"
    )
    return data


class OverrideRequestIn(BaseModel):
    version_id: int
    layer: str = Field(description="Düzenlenecek katman kodu")
    bucket_id: int
    mode: str = Field(default="absolute", description="absolute, pct, delta")
    value: float
    reason_code_id: int
    note: str = ""
    series_key: str | None = None
    scope: dict | None = Field(default=None, description="Üst seviyeden giriş için kapsam")
    level: dict[str, str] | None = None
    allocation_method: str = Field(
        default="layer_ratio",
        description="layer_ratio, history_share, last_n_share, equal, custom",
    )
    valid_until_bucket_id: int | None = None


@router.post("/override", summary="Hücre veya toplu override")
def do_override(
    payload: OverrideRequestIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    try:
        res = override_svc.apply_override(
            db, principal,
            override_svc.OverrideRequest(
                version_id=payload.version_id, layer_code=payload.layer, bucket_id=payload.bucket_id,
                mode=payload.mode, value=payload.value, reason_code_id=payload.reason_code_id,
                note=payload.note, series_key=payload.series_key, scope=payload.scope,
                level=payload.level, allocation_method=payload.allocation_method,
                valid_until_bucket_id=payload.valid_until_bucket_id,
            ),
        )
    except override_svc.OverrideError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    res["summary"] = (
        f"{res['cells']} hücre güncellendi, {res['old_value']:,.0f} yerine {res['new_value']:,.0f}"
    )
    return res


@router.post("/override/{override_id}/revert", summary="Override'i geri al")
def revert_override(
    override_id: int,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    try:
        res = override_svc.revert(db, principal, override_id)
    except override_svc.OverrideError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    res["summary"] = f"#{override_id} geri alındı, {res['cells']} hücre eski değerine dondu"
    return res


class CopyLayerRequest(BaseModel):
    version_id: int
    from_layer: str
    to_layer: str


@router.post("/layers/copy", summary="Katman kopyala")
def copy_layer(
    payload: CopyLayerRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    try:
        res = override_svc.copy_layer(db, principal, payload.version_id, payload.from_layer, payload.to_layer)
    except override_svc.OverrideError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    res["summary"] = f"{res['cells']} hücre {payload.from_layer} katmanından {payload.to_layer} katmanına kopyalandı"
    return res


class InsightIn(BaseModel):
    text: str
    series_key: str | None = None
    scope: dict | None = None
    bucket_from_id: int | None = None
    bucket_to_id: int | None = None
    reason_code_id: int | None = None


@router.get("/insights", summary="Saha bilgileri")
def list_insights(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(Insight).order_by(Insight.id.desc())).scalars().all()
    users = {u.id: u.name for u in db.execute(select(User)).scalars()}
    reasons = {r.id: r.name for r in db.execute(select(ReasonCode)).scalars()}
    buckets = {b.id: b.label for b in db.execute(select(TimeBucket)).scalars()}
    return {
        "insights": [
            {
                "id": i.id, "text": i.text, "user": users.get(i.user_id, ""),
                "reason": reasons.get(i.reason_code_id, ""), "series_key": i.series_key,
                "from": buckets.get(i.bucket_from_id), "to": buckets.get(i.bucket_to_id),
                "outcome": i.outcome,
                "at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in rows
        ],
        "summary": f"{len(rows)} saha bilgisi",
    }


@router.post("/insights", summary="Saha bilgisi ekle")
def add_insight(
    payload: InsightIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    row = override_svc.add_insight(
        db, principal, payload.text, payload.scope, payload.series_key,
        payload.bucket_from_id, payload.bucket_to_id, payload.reason_code_id,
    )
    db.commit()
    return {"id": row.id, "summary": "Saha bilgisi kaydedildi"}


@router.post("/insights/{insight_id}/outcome", summary="Saha bilgisi sonucunu işaretle")
def mark_insight(
    insight_id: int,
    outcome: str,
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    row = db.get(Insight, insight_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadi")
    row.outcome = outcome
    db.commit()
    return {"ok": True, "summary": f"Sonuç {outcome} olarak işaretlendi"}


class ApprovalIn(BaseModel):
    version_id: int
    step: str = Field(description="review, approved, locked")
    comment: str = ""
    acknowledge_warnings: bool = False


@router.get("/approvals/{version_id}", summary="Onay izi")
def approval_trail(
    version_id: int, db: Session = Depends(get_session), _p: Principal = Depends(current_principal)
) -> dict:
    trail = cycle_svc.approval_trail(db, version_id)
    return {"trail": trail, "warnings": cycle_svc.open_warnings(db, version_id),
            "summary": f"{len(trail)} onay adımı kayıtlı"}


@router.post("/approvals", summary="Onay adimini ilerlet")
def approve(
    payload: ApprovalIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    try:
        res = cycle_svc.transition(
            db, payload.version_id, payload.step, principal.id, principal.roles,
            payload.comment, payload.acknowledge_warnings,
        )
    except cycle_svc.CycleError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not res.get("ok"):
        db.rollback()
        return {**res, "summary": res.get("message", "Onay için uyarıların görülmesi gerekiyor")}
    db.commit()
    res["summary"] = f"{cycle_svc.STEP_LABELS.get(payload.step, payload.step)} adımına geçildi"
    return res


class CockpitRequest(BaseModel):
    version_id: int | None = Field(default=None, description="Boş bırakılırsa en güncel onaylı veya baz versiyon")
    measure: str = Field(default="units", description="Ölçü kodu, örnek: units, revenue")
    grain: str = Field(default="month", description="month, quarter, year")
    window_mode: str = Field(default="periods", description="periods (dönem sayısı) veya range (tarih aralığı)")
    horizon_periods: int | None = Field(default=None, description="Dönem modu: ileri dönem sayısı")
    history_periods: int = Field(default=12, description="Dönem modu: geri dönem sayısı")
    start_label: str | None = Field(default=None, description="Tarih modu: başlangıç dönemi, örnek 2026-01")
    end_label: str | None = Field(default=None, description="Tarih modu: bitiş dönemi, örnek 2027-12")
    lag: int = Field(default=1, description="Geçmiş sütunu ve MAPE için snapshot lag'i")
    scope: dict = Field(default_factory=dict, description="Boyut başına düğüm seçimi")
    view: str = Field(default="nominal", description="nominal, real, fx")
    currency: str = "TRY"


@router.post("/cockpit/overview", summary="Kokpit genel bakış: agrege tahmin ve model karşılaştırma tablosu")
def cockpit_overview(
    payload: CockpitRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    from app.services import cockpit_svc

    try:
        return cockpit_svc.overview(
            db, principal,
            cockpit_svc.CockpitQuery(
                version_id=payload.version_id, measure=payload.measure, grain=payload.grain,
                window_mode=payload.window_mode,
                horizon_periods=payload.horizon_periods, history_periods=payload.history_periods,
                start_label=payload.start_label, end_label=payload.end_label, lag=payload.lag,
                scope=payload.scope, view=payload.view, currency=payload.currency,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
