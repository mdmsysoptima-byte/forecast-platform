"""Reference data: dimensions, trees, time, drivers, layers, models."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_principal, require_roles
from app.core.auth import Principal
from app.core.db import get_session
from app.domain.models import (
    Calendar,
    CalendarEvent,
    Dimension,
    Driver,
    DriverSet,
    DriverSetValue,
    DriverValue,
    Hierarchy,
    Measure,
    Member,
    ModelRegistry,
    ReasonCode,
    SavedView,
    SectorConfig,
    TimeBucket,
    UserGroup,
    UserGroupMember,
)
from app.services import calendar_svc, cycle_svc, hierarchy as hier_svc, layers

router = APIRouter(prefix="/api/v1", tags=["Referans"])


@router.get("/sector", summary="Yuklu sektör konfigürasyonu")
def sector(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    cfg = db.execute(select(SectorConfig)).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="Sektör konfigürasyonu yüklenmedi")
    return {
        "sector": cfg.sector, "name": cfg.name, "payload": cfg.payload,
        "summary": f"{cfg.name} konfigürasyonu yuklu",
    }


@router.get("/dimensions", summary="Boyutlar ve hiyerarşiler")
def dimensions(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    out = []
    for dim in db.execute(select(Dimension).order_by(Dimension.position)).scalars():
        hiers = db.execute(select(Hierarchy).where(Hierarchy.dimension_id == dim.id)).scalars().all()
        out.append(
            {
                "code": dim.code, "name": dim.name, "type": dim.type,
                "hierarchies": [
                    {"id": h.id, "code": h.code, "name": h.name, "is_default": h.is_default,
                     "levels": h.levels}
                    for h in hiers
                ],
            }
        )
    return {"dimensions": out, "summary": f"{len(out)} boyut"}


@router.get("/dimensions/{dimension_code}/tree", summary="Hiyerarşi ağacı")
def tree(
    dimension_code: str,
    hierarchy: str | None = None,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    hier = hier_svc.hierarchy_by_code(db, dimension_code, hierarchy)
    if hier is None:
        raise HTTPException(status_code=404, detail="Hiyerarşi bulunamadi")
    allowed = None
    if not principal.unrestricted and hier.id in principal.view_nodes:
        allowed = principal.view_nodes[hier.id]
    nodes = hier_svc.tree(db, hier, allowed)
    return {
        "dimension": dimension_code, "hierarchy": hier.code, "levels": hier.levels,
        "nodes": nodes, "summary": f"{hier.name} ağacı",
    }


@router.get("/dimensions/{dimension_code}/validate", summary="Hiyerarşi doğrulama raporu")
def validate(
    dimension_code: str,
    hierarchy: str | None = None,
    db: Session = Depends(get_session),
    _p: Principal = Depends(require_roles("admin", "planner")),
) -> dict:
    hier = hier_svc.hierarchy_by_code(db, dimension_code, hierarchy)
    rep = hier_svc.validate_tree(db, hier)
    return {
        "hierarchy": rep.hierarchy, "nodes": rep.nodes, "leaves": rep.leaves, "levels": rep.levels,
        "ok": rep.ok, "errors": rep.errors,
        "summary": "Doğrulama başarılı" if rep.ok else f"{len(rep.errors)} hata bulundu",
    }


@router.get("/time/cycle", summary="Aktif çevrim ve dönem bayrakları")
def cycle_info(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    cycle = cycle_svc.active_cycle(db)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Aktif çevrim yok")
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    flags = cycle_svc.period_flags(db, cycle)
    window = cycle_svc.forecast_window(db, cycle)
    history = cycle_svc.history_window(db, cycle, 12)
    return {
        "cycle": {
            "id": cycle.id, "name": cycle.name, "status": cycle.status,
            "status_label": cycle_svc.STEP_LABELS.get(cycle.status, cycle.status),
            "grain": cycle.grain, "horizon_periods": cycle.horizon_periods,
            "fence_periods": cycle.fence_periods, "freeze_periods": cycle.freeze_periods,
            "current_period": current.label,
        },
        "steps": [{"code": s, "label": cycle_svc.STEP_LABELS[s]} for s in cycle_svc.STEPS],
        "buckets": [
            {"bucket_id": b.id, "label": b.label, "flag": flags.get(b.id, "open"),
             "working_days": b.working_days, "is_current": b.id == current.id}
            for b in history + window
        ],
        "summary": f"{cycle.name}, current period {current.label}, durum {cycle.status}",
    }


@router.get("/time/calendar-events", summary="Takvim olaylari")
def calendar_events(
    year: int | None = None, db: Session = Depends(get_session), _p: Principal = Depends(current_principal)
) -> dict:
    cal = db.execute(select(Calendar)).scalars().first()
    q = select(CalendarEvent).where(CalendarEvent.calendar_id == cal.id).order_by(CalendarEvent.start_date)
    rows = db.execute(q).scalars().all()
    if year:
        rows = [r for r in rows if r.start_date.year == year]
    return {
        "events": [
            {"type": r.type, "name": r.name, "start": r.start_date.isoformat(),
             "end": r.end_date.isoformat(), "is_shifting": r.is_shifting}
            for r in rows
        ],
        "summary": f"{len(rows)} olay",
    }


@router.post("/time/cycle/advance", summary="Çevrimi bir dönem ilerlet")
def advance(
    db: Session = Depends(get_session), principal: Principal = Depends(require_roles("admin"))
) -> dict:
    cycle = cycle_svc.active_cycle(db)
    cycle_svc.advance_cycle(db, cycle, principal.id)
    db.commit()
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    return {"ok": True, "current_period": current.label, "summary": f"Çevrim {current.label} dönemine gecti"}


@router.get("/layers", summary="Katmanlar")
def layer_list(db: Session = Depends(get_session), principal: Principal = Depends(current_principal)) -> dict:
    lm = layers.layer_map(db)
    cfg = layers.consensus_config(db)
    return {
        "layers": [
            {
                "code": l.code, "name": l.name, "editable": l.is_editable, "edit_roles": l.edit_roles,
                "order": l.order_index, "provenance": l.provenance,
                "can_edit": l.is_editable and (principal.is_admin or any(r in principal.roles for r in l.edit_roles)),
            }
            for l in sorted(lm.values(), key=lambda x: x.order_index)
        ],
        "consensus_rule": cfg["rule"],
        "summary": f"{len(lm)} katman, konsensüs kuralı {cfg['rule']}",
    }


@router.get("/measures", summary="Ölçüler")
def measures(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(Measure)).scalars().all()
    return {
        "measures": [
            {"code": m.code, "name": m.name, "unit_type": m.unit_type, "base_unit": m.base_unit,
             "formula": m.formula}
            for m in rows
        ],
        "summary": f"{len(rows)} ölçü",
    }


@router.get("/reason-codes", summary="Gerekçe kodlari")
def reason_codes(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(ReasonCode)).scalars().all()
    return {
        "reason_codes": [
            {"id": r.id, "code": r.code, "name": r.name, "requires_note": r.requires_note} for r in rows
        ],
        "summary": f"{len(rows)} gerekçe kodu",
    }


@router.get("/models", summary="Model kayıtları")
def models(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(ModelRegistry)).scalars().all()
    return {
        "models": [
            {
                "id": m.id, "code": m.code, "name": m.name, "version": m.version,
                "supported_grains": m.supported_grains, "supported_levels": m.supported_levels,
                "required_drivers": m.required_drivers, "optional_drivers": m.optional_drivers,
                "output_has_interval": m.output_has_interval, "description": m.description,
            }
            for m in rows
        ],
        "summary": f"{len(rows)} kayıtlı model",
    }


@router.get("/drivers", summary="Sürücü kataloğu")
def drivers(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(Driver)).scalars().all()
    dims = {d.id: d.code for d in db.execute(select(Dimension)).scalars()}
    return {
        "drivers": [
            {
                "id": d.id, "code": d.code, "name": d.name, "unit": d.unit,
                "source_type": d.source_type, "source_ref": d.source_ref, "grain": d.grain,
                "scope": dims.get(d.scope_dimension_id, "global"),
            }
            for d in rows
        ],
        "summary": f"{len(rows)} sürücü",
    }


@router.get("/driver-sets", summary="Sürücü setleri")
def driver_sets(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(DriverSet)).scalars().all()
    return {
        "driver_sets": [
            {"id": s.id, "name": s.name, "description": s.description, "is_shared": s.is_shared}
            for s in rows
        ],
        "summary": f"{len(rows)} sürücü seti",
    }


@router.get("/driver-sets/{set_id}/values", summary="Sürücü seti değerleri ve geçmiş")
def driver_set_values(
    set_id: int,
    driver_code: str | None = None,
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    drivers_by_id = {d.id: d for d in db.execute(select(Driver)).scalars()}
    buckets = {b.id: b for b in db.execute(select(TimeBucket).where(TimeBucket.grain == "month")).scalars()}
    wanted = [d for d in drivers_by_id.values() if not driver_code or d.code == driver_code]

    out = []
    for d in wanted:
        hist = db.execute(
            select(DriverValue.bucket_id, DriverValue.value).where(
                DriverValue.driver_id == d.id, DriverValue.member_id.is_(None)
            )
        ).all()
        future = db.execute(
            select(DriverSetValue.bucket_id, DriverSetValue.value, DriverSetValue.entry_method).where(
                DriverSetValue.driver_set_id == set_id, DriverSetValue.driver_id == d.id
            )
        ).all()
        if not hist and not future:
            continue
        out.append(
            {
                "driver": {"id": d.id, "code": d.code, "name": d.name, "unit": d.unit},
                "history": sorted(
                    [{"bucket_id": b, "label": buckets[b].label, "value": v} for b, v in hist if b in buckets],
                    key=lambda r: buckets[r["bucket_id"]].seq,
                ),
                "future": sorted(
                    [{"bucket_id": b, "label": buckets[b].label, "value": v, "entry_method": e}
                     for b, v, e in future if b in buckets],
                    key=lambda r: buckets[r["bucket_id"]].seq,
                ),
            }
        )
    return {"set_id": set_id, "drivers": out, "summary": f"{len(out)} sürücü için değer"}


class DriverAssumption(BaseModel):
    driver_code: str
    method: str = Field(default="constant", description="constant, pct_growth, trend, table")
    value: float | None = Field(default=None, description="Sabit değer veya aylik yüzde")
    values: dict[str, float] | None = Field(default=None, description="Dönem etiketi ve değer tablosu")
    member_code: str | None = None


@router.post("/driver-sets/{set_id}/assumption", summary="Sürücü varsayımı gir")
def set_assumption(
    set_id: int,
    payload: DriverAssumption,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_roles("planner", "finance", "marketing_contributor")),
) -> dict:
    driver = db.execute(select(Driver).where(Driver.code == payload.driver_code)).scalar_one_or_none()
    if driver is None:
        raise HTTPException(status_code=404, detail="Sürücü bulunamadi")
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    by_label = {b.label: b for b in window}

    member_id = None
    if payload.member_code:
        m = db.execute(select(Member).where(Member.code == payload.member_code)).scalar_one_or_none()
        member_id = m.id if m else None

    last_actual = db.execute(
        select(DriverValue.value)
        .join(TimeBucket, TimeBucket.id == DriverValue.bucket_id)
        .where(DriverValue.driver_id == driver.id, DriverValue.member_id.is_(None))
        .order_by(TimeBucket.seq.desc())
        .limit(1)
    ).scalar_one_or_none() or 1.0

    new_values: dict[int, float] = {}
    if payload.method == "table" and payload.values:
        for label, v in payload.values.items():
            if label in by_label:
                new_values[by_label[label].id] = float(v)
    elif payload.method == "pct_growth":
        v = float(last_actual)
        for b in window:
            v *= 1.0 + float(payload.value or 0.0) / 100.0
            new_values[b.id] = round(v, 4)
    elif payload.method == "trend":
        rows = db.execute(
            select(DriverValue.value)
            .join(TimeBucket, TimeBucket.id == DriverValue.bucket_id)
            .where(DriverValue.driver_id == driver.id, DriverValue.member_id.is_(None))
            .order_by(TimeBucket.seq.desc())
            .limit(13)
        ).scalars().all()
        growth = 0.0
        if len(rows) >= 13 and rows[-1]:
            growth = (rows[0] / rows[-1]) ** (1 / 12) - 1
        v = float(last_actual)
        for b in window:
            v *= 1.0 + growth
            new_values[b.id] = round(v, 4)
    else:
        for b in window:
            new_values[b.id] = float(payload.value or 0.0)

    for bucket_id, value in new_values.items():
        row = db.execute(
            select(DriverSetValue).where(
                DriverSetValue.driver_set_id == set_id,
                DriverSetValue.driver_id == driver.id,
                DriverSetValue.bucket_id == bucket_id,
                DriverSetValue.member_id.is_(member_id) if member_id is None else DriverSetValue.member_id == member_id,
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                DriverSetValue(
                    driver_set_id=set_id, driver_id=driver.id, bucket_id=bucket_id,
                    member_id=member_id, value=value, entry_method=payload.method,
                )
            )
        else:
            row.value = value
            row.entry_method = payload.method
    from app.services import audit

    audit.record(db, "driver_set_value", set_id, "set_assumption", principal.id,
                 actor_kind=principal.actor_kind, after={"driver": driver.code, "periods": len(new_values)})
    db.commit()
    return {
        "ok": True, "driver": driver.code, "periods": len(new_values),
        "summary": f"{driver.name} için {len(new_values)} dönem {payload.method} yöntemiyle güncellendi",
    }


class DriverSetCreate(BaseModel):
    name: str
    description: str = ""
    copy_from_set_id: int | None = None


@router.post("/driver-sets", summary="Sürücü seti oluştur")
def create_driver_set(
    payload: DriverSetCreate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_roles("planner", "finance")),
) -> dict:
    ds = DriverSet(name=payload.name, description=payload.description, owner_user_id=principal.id, is_shared=True)
    db.add(ds)
    db.flush()
    copied = 0
    if payload.copy_from_set_id:
        rows = db.execute(
            select(DriverSetValue).where(DriverSetValue.driver_set_id == payload.copy_from_set_id)
        ).scalars().all()
        for r in rows:
            db.add(
                DriverSetValue(
                    driver_set_id=ds.id, driver_id=r.driver_id, bucket_id=r.bucket_id,
                    member_id=r.member_id, value=r.value, entry_method=r.entry_method,
                )
            )
            copied += 1
    db.commit()
    return {"id": ds.id, "copied_values": copied, "summary": f"{ds.name} seti oluşturuldu"}


class GroupCreate(BaseModel):
    name: str
    dimension_code: str
    member_codes: list[str]
    is_shared: bool = False


@router.get("/groups", summary="Kullanıcı tanımlı gruplar")
def groups(db: Session = Depends(get_session), principal: Principal = Depends(current_principal)) -> dict:
    rows = hier_svc.groups_for_user(db, principal.id)
    dims = {d.id: d.code for d in db.execute(select(Dimension)).scalars()}
    out = []
    for g in rows:
        members = db.execute(
            select(Member.code, Member.name)
            .join(UserGroupMember, UserGroupMember.member_id == Member.id)
            .where(UserGroupMember.group_id == g.id)
        ).all()
        out.append(
            {
                "id": g.id, "name": g.name, "dimension": dims.get(g.dimension_id, ""),
                "is_shared": g.is_shared,
                "members": [{"code": c, "name": n} for c, n in members],
            }
        )
    return {"groups": out, "summary": f"{len(out)} grup"}


@router.post("/groups", summary="Grup oluştur")
def create_group(
    payload: GroupCreate, db: Session = Depends(get_session), principal: Principal = Depends(current_principal)
) -> dict:
    dim = db.execute(select(Dimension).where(Dimension.code == payload.dimension_code)).scalar_one_or_none()
    if dim is None:
        raise HTTPException(status_code=404, detail="Boyut bulunamadi")
    g = UserGroup(name=payload.name, dimension_id=dim.id, owner_user_id=principal.id, is_shared=payload.is_shared)
    db.add(g)
    db.flush()
    members = db.execute(
        select(Member.id).where(Member.dimension_id == dim.id, Member.code.in_(payload.member_codes))
    ).all()
    for (mid,) in members:
        db.add(UserGroupMember(group_id=g.id, member_id=mid))
    db.commit()
    return {"id": g.id, "members": len(members), "summary": f"{g.name} grubu {len(members)} üye ile oluşturuldu"}


class SavedViewPayload(BaseModel):
    name: str
    state: dict
    is_shared: bool = False


@router.get("/saved-views", summary="Kayıtlı görünümler")
def saved_views(db: Session = Depends(get_session), principal: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(
        select(SavedView).where((SavedView.user_id == principal.id) | (SavedView.is_shared.is_(True)))
    ).scalars().all()
    return {
        "views": [
            {"id": v.id, "name": v.name, "is_shared": v.is_shared, "state": v.state, "mine": v.user_id == principal.id}
            for v in rows
        ],
        "summary": f"{len(rows)} kayıtlı görünüm",
    }


@router.post("/saved-views", summary="Görünüm kaydet")
def save_view(
    payload: SavedViewPayload, db: Session = Depends(get_session), principal: Principal = Depends(current_principal)
) -> dict:
    row = SavedView(user_id=principal.id, name=payload.name, state=payload.state, is_shared=payload.is_shared)
    db.add(row)
    db.commit()
    return {"id": row.id, "summary": f"{payload.name} görünümü kaydedildi"}
