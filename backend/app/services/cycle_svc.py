"""Cycle, fence, freeze, approval flow and snapshot (sections 6 and 9)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.core.logging import get_logger
from app.domain.models import Approval, Cycle, TimeBucket, Version
from app.services import audit, layers

log = get_logger(__name__)

STEPS = ["draft", "review", "approved", "locked"]
STEP_LABELS = {
    "open": "Açık", "draft": "Taslak", "review": "İnceleme", "approved": "Onaylı", "locked": "Kilitli",
}
STEP_ROLES = {"review": ("planner",), "approved": ("finance",), "locked": ("finance", "planner")}


class CycleError(RuntimeError):
    pass


def active_cycle(db: Session) -> Cycle | None:
    """The cycle with the latest current period. Prior cycles stay queryable."""
    return db.execute(
        select(Cycle)
        .join(TimeBucket, TimeBucket.id == Cycle.current_period_bucket_id)
        .order_by(TimeBucket.seq.desc(), Cycle.id.desc())
    ).scalars().first()


def forecast_window(db: Session, cycle: Cycle) -> list[TimeBucket]:
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    return list(
        db.execute(
            select(TimeBucket)
            .where(
                TimeBucket.calendar_id == cycle.calendar_id,
                TimeBucket.grain == cycle.grain,
                TimeBucket.seq >= current.seq,
                TimeBucket.seq <= current.seq + cycle.horizon_periods - 1,
            )
            .order_by(TimeBucket.seq)
        ).scalars()
    )


def history_window(db: Session, cycle: Cycle, periods: int = 24) -> list[TimeBucket]:
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    return list(
        db.execute(
            select(TimeBucket)
            .where(
                TimeBucket.calendar_id == cycle.calendar_id,
                TimeBucket.grain == cycle.grain,
                TimeBucket.seq >= current.seq - periods,
                TimeBucket.seq <= current.seq - 1,
            )
            .order_by(TimeBucket.seq)
        ).scalars()
    )


def period_flags(db: Session, cycle: Cycle) -> dict[int, str]:
    """actual, fence, freeze or open for every bucket of the cycle grain."""
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    rows = db.execute(
        select(TimeBucket.id, TimeBucket.seq).where(
            TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == cycle.grain
        )
    ).all()
    flags: dict[int, str] = {}
    f_end = current.seq + cycle.fence_periods - 1
    z_end = f_end + cycle.freeze_periods
    for bid, seq in rows:
        if seq < current.seq:
            flags[bid] = "actual"
        elif seq <= f_end:
            flags[bid] = "fence"
        elif seq <= z_end:
            flags[bid] = "freeze"
        else:
            flags[bid] = "open"
    return flags


def is_editable_bucket(db: Session, cycle: Cycle, bucket_id: int) -> tuple[bool, str]:
    flag = period_flags(db, cycle).get(bucket_id, "open")
    if flag == "actual":
        return False, "Gerçekleşen dönem düzenlenemez"
    if flag == "fence":
        return False, "Dönem kilitli (fence); düzenleme kabul edilmiyor"
    return True, ""


def open_cycle(db: Session, cycle: Cycle, actor_id: int | None = None) -> Cycle:
    if cycle.status not in ("open", "locked"):
        raise CycleError("Çevrim yalnızca kilitliyken veya acikken acilabilir")
    before = {"status": cycle.status}
    cycle.status = "open"
    cycle.opened_at = datetime.now(timezone.utc)
    audit.record(db, "cycle", cycle.id, "open", actor_id, before=before, after={"status": "open"})
    return cycle


def advance_cycle(db: Session, cycle: Cycle, actor_id: int | None = None) -> Cycle:
    """Rolling forecast: the window moves one period forward."""
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    nxt = db.execute(
        select(TimeBucket).where(
            TimeBucket.calendar_id == cycle.calendar_id,
            TimeBucket.grain == cycle.grain,
            TimeBucket.seq == current.seq + 1,
        )
    ).scalar_one_or_none()
    if nxt is None:
        raise CycleError("Takvimde sonraki dönem bulunamadi")
    before = {"current_period": current.label, "status": cycle.status}
    cycle.current_period_bucket_id = nxt.id
    cycle.status = "open"
    cycle.locked_at = None
    cycle.opened_at = datetime.now(timezone.utc)
    audit.record(
        db, "cycle", cycle.id, "advance", actor_id, before=before,
        after={"current_period": nxt.label, "status": "open"},
    )
    return cycle


def open_warnings(db: Session, version_id: int) -> list[dict]:
    from app.domain.models import Alert

    rows = db.execute(
        select(Alert).where(Alert.status == "open", Alert.version_id == version_id)
    ).scalars().all()
    return [{"id": a.id, "type": a.type, "severity": a.severity, "title": a.title} for a in rows]


def transition(
    db: Session,
    version_id: int,
    target_step: str,
    actor_id: int,
    roles: list[str],
    comment: str = "",
    acknowledge_warnings: bool = False,
) -> dict:
    """Move a version through draft, review, approved, locked. No step is skipped."""
    version = db.get(Version, version_id)
    if version is None:
        raise CycleError("Versiyon bulunamadi")
    if target_step not in STEPS:
        raise CycleError("Geçersiz onay adımı")

    current = version.status if version.status in STEPS else "draft"
    if STEPS.index(target_step) != STEPS.index(current) + 1:
        raise CycleError(
            f"Adım atlanamaz. Simdiki adım {STEP_LABELS[current]}, sonraki adım "
            f"{STEP_LABELS[STEPS[min(STEPS.index(current) + 1, len(STEPS) - 1)]]}"
        )
    allowed = STEP_ROLES.get(target_step, ())
    if "admin" not in roles and not any(r in roles for r in allowed):
        raise CycleError(f"{STEP_LABELS[target_step]} adımı için yetkiniz yok")

    warnings = open_warnings(db, version_id)
    if warnings and target_step in ("approved", "locked") and not acknowledge_warnings:
        return {
            "ok": False,
            "needs_acknowledge": True,
            "warnings": warnings,
            "message": "Açık uyarılar var. Onaylamadan önce görülmesi gerekiyor.",
        }

    before = {"status": current}
    cycle = db.get(Cycle, version.cycle_id) if version.cycle_id else active_cycle(db)
    result: dict = {"ok": True, "step": target_step}

    if target_step == "review":
        layers.recompute_consensus(db, version_id)
        version.status = "review"
        if cycle:
            cycle.status = "review"
    elif target_step == "approved":
        layers.recompute_consensus(db, version_id)
        version.status = "approved"
        version.kind = "consensus"
        if cycle:
            cycle.status = "approved"
    elif target_step == "locked":
        version.status = "locked"
        version.kind = "snapshot"
        if cycle:
            cycle.status = "locked"
            cycle.locked_at = datetime.now(timezone.utc)
            result.update(write_snapshot(db, cycle, version))
            result.update(push_consensus_to_store(db, version))
    else:
        version.status = "draft"

    db.add(
        Approval(
            version_id=version_id, cycle_id=version.cycle_id, step=target_step,
            actor_id=actor_id, decision="ok", comment=comment,
        )
    )
    audit.record(db, "version", version_id, f"approval:{target_step}", actor_id,
                 before=before, after={"status": version.status})
    return result


def write_snapshot(db: Session, cycle: Cycle, version: Version) -> dict:
    """Immutable copy of every layer at approval time, with lag per target period."""
    store = get_store()
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    buckets = {b.id: b for b in forecast_window(db, cycle)}
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for code in ("statistical", "sales", "marketing", "planner", "consensus", "budget"):
        vals = layers.read_values(db, version.id, code)
        for (sk, bid), v in vals.items():
            b = buckets.get(bid)
            if b is None:
                continue
            rows.append(
                {
                    "cycle_id": cycle.id,
                    "snapshot_at": now,
                    "layer_code": code,
                    "series_key": sk,
                    "target_bucket_id": bid,
                    "lag": b.seq - current.seq + 1,
                    "value": float(v),
                }
            )
    written = store.write_snapshot(rows) if rows else 0
    log.info("snapshot yazıldı", extra={"version_id": version.id})
    return {"snapshot_rows": written}


def push_consensus_to_store(db: Session, version: Version) -> dict:
    """Write back the approved consensus for ERP and production systems."""
    store = get_store()
    vals = layers.read_values(db, version.id, "consensus")
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {"version_id": version.id, "series_key": sk, "bucket_id": bid, "value": float(v), "written_at": now}
        for (sk, bid), v in vals.items()
    ]
    n = store.write_consensus(rows) if rows else 0
    store.create_bi_views()
    return {"consensus_rows": n}


def approval_trail(db: Session, version_id: int) -> list[dict]:
    rows = db.execute(
        select(Approval).where(Approval.version_id == version_id).order_by(Approval.at)
    ).scalars().all()
    return [
        {"step": a.step, "label": STEP_LABELS.get(a.step, a.step), "actor_id": a.actor_id,
         "comment": a.comment, "at": a.at.isoformat() if a.at else None}
        for a in rows
    ]
