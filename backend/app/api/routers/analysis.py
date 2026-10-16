"""Accuracy, FVA, variance, budget gap and exceptions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_principal
from app.core.auth import Principal
from app.core.db import get_session
from app.services import accuracy as acc
from app.services import alerts as alert_svc
from app.services import cycle_svc

router = APIRouter(prefix="/api/v1/analysis", tags=["Analiz"])


@router.get("/accuracy", summary="Doğruluk: metrik, lag, seviye")
def accuracy(
    layer: str = "consensus",
    lag: int | None = None,
    level: str | None = Query(default=None, description="Örnek: product=category,geo=region"),
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    level_map = _parse_level(level)
    out = {
        "summary_metrics": acc.accuracy_summary(db, layer, lag),
        "by_lag": acc.accuracy_by_lag(db, layer),
        "worst_series": acc.accuracy_by_series(db, layer, lag or 1, limit=20),
        "metrics": acc.METRIC_LABELS,
        "layer": layer,
        "lag": lag,
    }
    if level_map:
        out["by_level"] = acc.accuracy_by_level(db, level_map, layer, lag or 1)
        out["bias_heatmap"] = acc.bias_heatmap(db, level_map, layer, lag or 1)
    m = out["summary_metrics"]
    out["summary"] = (
        f"{layer} katmanı, lag {lag or 'tümü'}: WMAPE {m['wmape']}, bias {m['bias']}, {m['n']} gözlem"
        if m["n"] else "Doğruluk için snapshot ve gerçekleşen eslesmesi bulunamadi"
    )
    return out


@router.get("/fva", summary="FVA merdiveni ve katkı ayrışması")
def fva(
    lag: int = 1, db: Session = Depends(get_session), _p: Principal = Depends(current_principal)
) -> dict:
    ladder = acc.fva_ladder(db, lag)
    detail = acc.fva_by_user_and_reason(db, lag)
    return {
        **ladder,
        **detail,
        "summary": (
            f"Lag {lag}: naive'den konsensüse toplam {ladder['total_value_added']} puan iyileşme"
            if ladder["total_value_added"] is not None else "FVA için yeterli snapshot yok"
        ),
    }


@router.get("/variance", summary="Varyans ve şelale")
def variance(
    version_a: int,
    version_b: int,
    layer: str = "consensus",
    level: str | None = Query(default="product=category"),
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    res = acc.variance(db, version_a, version_b, _parse_level(level), layer)
    res["summary"] = (
        f"Toplam {res['total_from']:,.0f} degerinden {res['total_to']:,.0f} değerine "
        f"{res['total_delta']:+,.0f} değişim"
    )
    return res


@router.get("/budget-gap", summary="Bütçe gap")
def budget_gap(
    version_id: int, db: Session = Depends(get_session), _p: Principal = Depends(current_principal)
) -> dict:
    cycle = cycle_svc.active_cycle(db)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Aktif çevrim yok")
    window = cycle_svc.forecast_window(db, cycle)
    res = acc.budget_gap(db, version_id, [b.id for b in window])
    res["summary"] = (
        f"Konsensüs {res['total_consensus']:,.0f}, bütçe {res['total_budget']:,.0f}, "
        f"fark {res['total_gap']:+,.0f} ({res['total_gap_pct']} yüzde)"
    )
    return res


@router.get("/alerts", summary="İstisnalar listesi")
def alerts(
    status: str | None = "open",
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    rows = alert_svc.list_alerts(db, status)
    label = {"open": "açık", "ack": "okundu", "closed": "kapalı"}.get(status, status or "tümü")
    return {
        "alerts": rows,
        "thresholds": alert_svc.thresholds(db),
        "summary": f"{len(rows)} uyarı ({label})",
    }


@router.post("/alerts/{alert_id}/status", summary="Uyarı durumunu değiştir")
def set_alert_status(
    alert_id: int,
    status: str,
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    if status not in ("open", "ack", "closed"):
        raise HTTPException(status_code=400, detail="Durum open, ack veya closed olabilir")
    try:
        alert_svc.set_status(db, alert_id, status)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return {"ok": True, "summary": f"Uyarı {status} olarak işaretlendi"}


def _parse_level(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out
