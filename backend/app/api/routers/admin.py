"""Administration: connections, models, users, audit log, maintenance."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.api.deps import require_roles
from app.core.auth import Principal
from app.core.config import settings
from app.core.db import get_session
from app.domain.models import (
    AuditLog,
    Dimension,
    HierarchyNode,
    Member,
    ModelRegistry,
    Role,
    RowPermission,
    User,
    UserRole,
)
from app.forecast_models import registry, runner
from app.services import audit, layers

router = APIRouter(prefix="/api/v1/admin", tags=["Yönetim"])


@router.get("/connections", summary="Veri bağlantıları")
def connections(_p: Principal = Depends(require_roles("admin"))) -> dict:
    store = get_store()
    return {
        "app_db": {"url": settings.app_db_url.split("///")[-1], "engine": "sqlite", "status": "bağlı"},
        "store": {
            "backend": getattr(store, "backend", "local"),
            "path": getattr(store, "path", ""),
            "status": "bağlı",
            "data_as_of": store.data_as_of(),
        },
        "available_backends": [
            {"code": "local", "name": "Yerel analitik depo", "status": "hazır"},
            {"code": "bigquery", "name": "BigQuery", "status": "arayüz tanımlı, bağlanmadı"},
            {"code": "mssql", "name": "MS SQL Server", "status": "arayüz tanımlı, bağlanmadı"},
        ],
        "summary": "Uygulama veritabanı ve analitik depo bağlı",
    }


@router.post("/connections/test", summary="Bağlantıyı test et")
def test_connection(db: Session = Depends(get_session), _p: Principal = Depends(require_roles("admin"))) -> dict:
    store = get_store()
    counts = {}
    try:
        counts["actual_data_as_of"] = store.data_as_of()
        counts["series"] = db.execute(select(Member)).scalars().first() is not None
        ok = True
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "summary": "Bağlantı testi başarısız"}
    return {"ok": ok, "details": counts, "summary": "Bağlantı testi başarılı"}


@router.post("/models/sync", summary="Model kayitlarini yenile")
def sync_models(db: Session = Depends(get_session), principal: Principal = Depends(require_roles("admin"))) -> dict:
    rows = registry.sync_registry(db)
    audit.record(db, "model_registry", 0, "sync", principal.id, after={"models": [r.code for r in rows]})
    db.commit()
    return {
        "models": [{"code": r.code, "name": r.name, "version": r.version} for r in rows],
        "summary": f"{len(rows)} model paketi tarandı",
    }


@router.get("/users", summary="Kullanıcı ve yetkiler")
def users(db: Session = Depends(get_session), _p: Principal = Depends(require_roles("admin"))) -> dict:
    rows = db.execute(select(User)).scalars().all()
    out = []
    for u in rows:
        roles = [
            r for (r,) in db.execute(
                select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == u.id)
            )
        ]
        perms = db.execute(select(RowPermission).where(RowPermission.user_id == u.id)).scalars().all()
        scope = []
        for p in perms:
            node = db.get(HierarchyNode, p.hierarchy_node_id)
            member = db.get(Member, node.member_id) if node else None
            dim = db.get(Dimension, p.dimension_id)
            scope.append(
                {
                    "dimension": dim.code if dim else "",
                    "node": member.name if member else "",
                    "can_edit": p.can_edit,
                }
            )
        out.append({"id": u.id, "name": u.name, "email": u.email, "roles": roles, "row_scope": scope})
    return {"users": out, "summary": f"{len(out)} kullanıcı"}


@router.get("/audit", summary="Audit log")
def audit_log(
    limit: int = 200,
    entity: str | None = None,
    db: Session = Depends(get_session),
    _p: Principal = Depends(require_roles("admin", "finance")),
) -> dict:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if entity:
        q = q.where(AuditLog.entity == entity)
    rows = db.execute(q).scalars().all()
    names = {u.id: u.name for u in db.execute(select(User)).scalars()}
    return {
        "entries": [
            {
                "id": r.id, "entity": r.entity, "entity_id": r.entity_id, "action": r.action,
                "actor": names.get(r.actor_id, "sistem"), "actor_kind": r.actor_kind,
                "before": r.before, "after": r.after, "correlation_id": r.correlation_id,
                "at": r.at.isoformat() if r.at else None,
            }
            for r in rows
        ],
        "summary": f"{len(rows)} audit kaydı",
    }


class ConsensusRuleIn(BaseModel):
    rule: str


@router.get("/consensus-rule", summary="Konsensüs kuralı")
def get_consensus_rule(db: Session = Depends(get_session), _p: Principal = Depends(require_roles("admin", "planner"))) -> dict:
    cfg = layers.consensus_config(db)
    return {
        **cfg,
        "options": list(layers.CONSENSUS_RULES.keys()) + ["weighted_average"],
        "summary": f"Aktif kural: {cfg['rule']}",
    }


@router.post("/consensus-rule", summary="Konsensüs kuralini değiştir")
def set_consensus_rule(
    payload: ConsensusRuleIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_roles("admin")),
) -> dict:
    from app.domain.models import SectorConfig

    cfg = db.execute(select(SectorConfig)).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="Sektör konfigürasyonu yok")
    if payload.rule not in list(layers.CONSENSUS_RULES.keys()) + ["weighted_average"]:
        raise HTTPException(status_code=400, detail="Bilinmeyen kural")
    data = dict(cfg.payload or {})
    data.setdefault("layers", {})["consensus_rule"] = payload.rule
    cfg.payload = data
    audit.record(db, "sector_config", cfg.id, "set_consensus_rule", principal.id,
                 after={"rule": payload.rule})
    db.commit()
    return {"ok": True, "rule": payload.rule, "summary": f"Konsensüs kuralı {payload.rule} olarak ayarlandi"}


@router.post("/maintenance/purge-drafts", summary="Süresi geçen taslakları sil")
def purge_drafts(db: Session = Depends(get_session), _p: Principal = Depends(require_roles("admin"))) -> dict:
    n = runner.purge_expired_drafts(db)
    db.commit()
    return {"purged": n, "summary": f"{n} taslak silindi"}


@router.get("/model-packages", summary="Disk üzerindeki model paketleri")
def model_packages(_p: Principal = Depends(require_roles("admin"))) -> dict:
    found = registry.discover_packages()
    return {
        "packages": [
            {
                "code": m.get("code"), "name": m.get("name"), "version": m.get("version"),
                "path": m.get("package_path"), "required_drivers": m.get("required_drivers", []),
                "supported_levels": m.get("supported_levels", {}),
            }
            for m in found
        ],
        "directory": str(settings.model_packages_dir),
        "summary": f"{len(found)} paket bulundu",
    }


@router.get("/registry", summary="Kayıtlı modeller")
def registry_rows(db: Session = Depends(get_session), _p: Principal = Depends(require_roles("admin"))) -> dict:
    rows = db.execute(select(ModelRegistry)).scalars().all()
    return {
        "models": [
            {"code": m.code, "name": m.name, "version": m.version, "package_path": m.package_path}
            for m in rows
        ],
        "summary": f"{len(rows)} kayıt",
    }
