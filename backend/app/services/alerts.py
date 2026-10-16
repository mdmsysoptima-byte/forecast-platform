"""Alerts (section 10). Thresholds come from the sector configuration."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.core.logging import get_logger
from app.domain.models import Alert, SectorConfig, Series, TimeBucket, Version
from app.services import cycle_svc, layers

log = get_logger(__name__)

ALERT_LABELS = {
    "change_vs_prior": "Önceki versiyona göre büyük değişim",
    "low_accuracy": "Düşük doğruluk",
    "missing_data": "Eksik gerçekleşen veri",
    "override_worse_than_naive": "Naive'den kötü override",
    "capacity_exceeded": "Kapasite aşımı",
}


def _tr(value: float, decimals: int = 1) -> str:
    """tr-TR number text for messages that reach the user."""
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def thresholds(db: Session) -> dict:
    cfg = db.execute(select(SectorConfig)).scalars().first()
    payload = (cfg.payload if cfg else {}) or {}
    a = payload.get("alerts") or {}
    return {
        "change_vs_prior_pct": float(a.get("change_vs_prior_pct", 15)),
        "wmape_lag1_threshold": float(a.get("wmape_lag1_threshold", 30)),
        "missing_periods": int(a.get("missing_periods", 2)),
    }


def _add(db: Session, **kw) -> Alert:
    row = Alert(**kw)
    db.add(row)
    return row


def generate_for_version(db: Session, version_id: int) -> dict:
    """Run every alert rule for a freshly produced version."""
    version = db.get(Version, version_id)
    if version is None:
        return {"created": 0}
    th = thresholds(db)
    cycle = cycle_svc.active_cycle(db)
    created = 0

    # Clear previous machine generated alerts for this version.
    for a in db.execute(select(Alert).where(Alert.version_id == version_id)).scalars():
        if a.status == "open":
            a.status = "closed"

    stat = layers.read_values(db, version_id, "statistical")
    created += _change_vs_prior(db, version, stat, th, cycle)
    created += _missing_data(db, version, th, cycle)
    created += _low_accuracy(db, version, th, cycle)
    db.flush()
    log.info("uyarılar üretildi", extra={"version_id": version_id})
    return {"created": created}


def _label_for(db: Session, series_key: str) -> str:
    row = db.execute(select(Series.dim_codes).where(Series.series_key == series_key)).scalar_one_or_none()
    if not row:
        return series_key[:8]
    return " / ".join(f"{v}" for _, v in sorted(row.items()))


def _change_vs_prior(db: Session, version: Version, stat: dict, th: dict, cycle) -> int:
    if not version.parent_version_id:
        return 0
    prior = layers.read_values(db, version.parent_version_id, "consensus") or layers.read_values(
        db, version.parent_version_id, "statistical"
    )
    if not prior:
        return 0
    limit = th["change_vs_prior_pct"] / 100.0
    created = 0
    by_series_new: dict[str, float] = {}
    by_series_old: dict[str, float] = {}
    for (sk, _b), v in stat.items():
        by_series_new[sk] = by_series_new.get(sk, 0.0) + v
    for (sk, _b), v in prior.items():
        by_series_old[sk] = by_series_old.get(sk, 0.0) + v
    for sk, new in by_series_new.items():
        old = by_series_old.get(sk)
        if not old:
            continue
        change = (new / old) - 1.0
        if abs(change) > limit:
            _add(
                db, type="change_vs_prior",
                severity="high" if abs(change) > limit * 2 else "medium",
                series_key=sk, version_id=version.id, cycle_id=cycle.id if cycle else None,
                title=f"{_label_for(db, sk)}: önceki versiyona göre yüzde {_tr(change * 100)} değişim",
                payload={"change_pct": round(change * 100, 2), "old": round(old, 1), "new": round(new, 1)},
            )
            created += 1
    return created


def _missing_data(db: Session, version: Version, th: dict, cycle) -> int:
    if cycle is None:
        return 0
    store = get_store()
    recent = cycle_svc.history_window(db, cycle, periods=th["missing_periods"])
    if not recent:
        return 0
    bucket_ids = [b.id for b in recent]
    keys = {k for (k,) in db.execute(select(Series.series_key))}
    rows = store.read_series(list(keys), min(bucket_ids), max(bucket_ids))
    have: dict[str, set[int]] = {}
    for r in rows:
        have.setdefault(r["series_key"], set()).add(r["bucket_id"])
    created = 0
    for sk in keys:
        got = have.get(sk, set())
        if len(got) < len(bucket_ids):
            _add(
                db, type="missing_data", severity="medium", series_key=sk, version_id=version.id,
                cycle_id=cycle.id,
                title=f"{_label_for(db, sk)}: son {len(bucket_ids)} dönemde gerçekleşen eksik",
                payload={"expected": len(bucket_ids), "found": len(got)},
            )
            created += 1
            if created >= 50:
                break
    return created


def _low_accuracy(db: Session, version: Version, th: dict, cycle) -> int:
    from app.services.accuracy import accuracy_by_series

    rows = accuracy_by_series(db, layer_code="consensus", lag=1, limit=200)
    created = 0
    for r in rows:
        if r["wmape"] is not None and r["wmape"] > th["wmape_lag1_threshold"]:
            _add(
                db, type="low_accuracy", severity="high" if r["wmape"] > 2 * th["wmape_lag1_threshold"] else "medium",
                series_key=r["series_key"], version_id=version.id, cycle_id=cycle.id if cycle else None,
                title=f"{_label_for(db, r['series_key'])}: lag 1 WMAPE yüzde {_tr(r['wmape'])}",
                payload={"wmape": r["wmape"], "bias": r["bias"]},
            )
            created += 1
            if created >= 50:
                break
    return created


def check_override_alerts(
    db: Session, version: Version, layer_code: str, keys: set[str], bucket_id: int
) -> list[dict]:
    """Large manual change against the statistical layer raises an alert."""
    th = thresholds(db)
    stat = layers.read_values(db, version.id, "statistical", keys, [bucket_id])
    new = layers.read_values(db, version.id, layer_code, keys, [bucket_id])
    base = sum(stat.values())
    now = sum(new.values())
    out: list[dict] = []
    if base > 0:
        change = (now / base) - 1.0
        if abs(change) * 100 > th["change_vs_prior_pct"]:
            b = db.get(TimeBucket, bucket_id)
            row = _add(
                db, type="change_vs_prior", severity="medium", version_id=version.id,
                bucket_id=bucket_id, cycle_id=version.cycle_id,
                title=f"{b.label if b else bucket_id}: istatistikselden yüzde {_tr(change * 100)} sapma",
                payload={"change_pct": round(change * 100, 2), "layer": layer_code},
            )
            db.flush()
            out.append({"id": row.id, "title": row.title, "severity": row.severity})
    return out


def list_alerts(db: Session, status: str | None = "open", limit: int = 200) -> list[dict]:
    q = select(Alert).order_by(Alert.severity.desc(), Alert.id.desc()).limit(limit)
    if status:
        q = q.where(Alert.status == status)
    rows = db.execute(q).scalars().all()
    buckets = {
        b.id: b.label for b in db.execute(select(TimeBucket).where(
            TimeBucket.id.in_([r.bucket_id for r in rows if r.bucket_id] or [-1])
        )).scalars()
    }
    return [
        {
            "id": r.id, "type": r.type, "type_label": ALERT_LABELS.get(r.type, r.type),
            "severity": r.severity, "title": r.title, "status": r.status,
            "series_key": r.series_key, "version_id": r.version_id,
            "bucket": buckets.get(r.bucket_id), "payload": r.payload,
        }
        for r in rows
    ]


def set_status(db: Session, alert_id: int, status: str) -> Alert:
    row = db.get(Alert, alert_id)
    if row is None:
        raise ValueError("Uyarı bulunamadi")
    row.status = status
    return row
