"""Reports, exports, the Excel round trip and the public forecast API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.api.deps import current_principal, require_roles
from app.core.auth import Principal
from app.core.db import get_session
from app.domain.models import ScheduledReport, Series, TimeBucket, Version
from app.services import cycle_svc, export_svc, grid, layers, report_svc
from app.services.grid import GridQuery

router = APIRouter(prefix="/api/v1", tags=["Rapor"])


@router.get("/reports", summary="Standart rapor listesi")
def list_reports(_p: Principal = Depends(current_principal)) -> dict:
    rows = report_svc.list_reports()
    return {"reports": rows, "summary": f"{len(rows)} standart rapor"}


class ReportRequest(BaseModel):
    code: str
    params: dict = Field(default_factory=dict)


@router.post("/reports/run", summary="Rapor üret")
def run_report(
    payload: ReportRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    try:
        res = report_svc.build(db, principal, payload.code, payload.params)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    res["summary"] = f"{res['title']} raporu üretildi, {len(res.get('rows', []))} satır"
    return res


@router.post("/reports/export", summary="Raporu Excel veya CSV olarak indir")
def export_report(
    payload: ReportRequest,
    fmt: str = Query(default="xlsx", description="xlsx, csv, parquet"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Response:
    try:
        res = report_svc.build(db, principal, payload.code, payload.params)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    columns = res.get("columns") or []
    rows = res.get("rows") or []
    if fmt == "csv":
        blob = export_svc.rows_to_csv([c["key"] for c in columns], rows)
        media = "text/csv"
        ext = "csv"
    elif fmt == "parquet":
        try:
            blob = export_svc.rows_to_parquet([c["key"] for c in columns], rows)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        media = "application/octet-stream"
        ext = "parquet"
    else:
        blob = export_svc.table_to_xlsx(res["title"], columns, rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    return Response(
        content=blob,
        media_type=media,
        headers={"content-disposition": f'attachment; filename="{payload.code}.{ext}"'},
    )


class GridExportRequest(BaseModel):
    version_id: int
    scope: dict = Field(default_factory=dict)
    level: dict[str, str] = Field(default_factory=dict)
    grain: str = "month"
    layers: list[str] = Field(default_factory=list)
    measure: str = "units"
    history_periods: int = 6


@router.post("/export/grid", summary="Grid'i Excel'e aktar")
def export_grid(
    payload: GridExportRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Response:
    q = GridQuery(
        version_id=payload.version_id, scope=payload.scope, level=payload.level, grain=payload.grain,
        layer_codes=payload.layers or list(grid.DEFAULT_LAYERS), measure=payload.measure,
        history_periods=payload.history_periods,
    )
    blob = export_svc.export_grid_xlsx(db, principal, q)
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"content-disposition": f'attachment; filename="tahmin-{payload.version_id}.xlsx"'},
    )


@router.post("/import/grid/preview", summary="Excel geri yükleme: fark listesi")
async def import_preview(
    file: UploadFile = File(description="Platformdan indirilen şablon"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    blob = await file.read()
    try:
        res = export_svc.read_grid_xlsx(db, principal, blob)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    res["summary"] = (
        f"{res['count']} fark bulundu, {res['skipped_locked_cells']} kilitli hücre atlandı. "
        "Onaylamadan önce listeyi inceleyin."
    )
    return res


class ApplyDiffsRequest(BaseModel):
    payload: dict = Field(description="On izleme yanitinin tamami")
    reason_code_id: int
    note: str = "Excel geri yükleme"


@router.post("/import/grid/apply", summary="Excel farklarini uygula")
def import_apply(
    body: ApplyDiffsRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    res = export_svc.apply_grid_diffs(db, principal, body.payload, body.reason_code_id, body.note)
    db.commit()
    res["summary"] = f"{res['applied']} fark uygulandı" + (
        f", {len(res['errors'])} satır reddedildi" if res["errors"] else ""
    )
    return res


@router.post("/store/write-consensus", summary="Onaylı konsensüsü depoya yaz")
def write_consensus(
    version_id: int,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_roles("admin", "planner", "finance")),
) -> dict:
    v = db.get(Version, version_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Versiyon bulunamadi")
    if v.status != "locked" and not principal.is_admin:
        raise HTTPException(
            status_code=400,
            detail="Otomatik geri yazma yalnızca kilitli versiyon için yapılır. Yetkili elle yazabilir.",
        )
    res = cycle_svc.push_consensus_to_store(db, v)
    db.commit()
    return {**res, "summary": f"{res['consensus_rows']} satır depoya yazıldı, BI görünümleri güncellendi"}


@router.get("/bi/views", summary="BI görünümleri")
def bi_views(_p: Principal = Depends(require_roles("admin", "planner", "finance"))) -> dict:
    views = get_store().create_bi_views()
    return {"views": views, "summary": f"{len(views)} görünüm hazır: {', '.join(views)}"}


@router.get("/forecast", summary="Onaylı tahmini sorgula (ERP ve üretim sistemleri için)")
def forecast_api(
    version: int,
    layer: str = "consensus",
    grain: str = "month",
    scope: str | None = Query(default=None, description="Örnek: product=SKU001,geo=IST"),
    db: Session = Depends(get_session),
    _p: Principal = Depends(current_principal),
) -> dict:
    v = db.get(Version, version)
    if v is None:
        raise HTTPException(status_code=404, detail="Versiyon bulunamadi")
    values = layers.read_values(db, version, layer)
    buckets = {b.id: b.label for b in db.execute(select(TimeBucket)).scalars()}
    dim_codes = {
        sk: codes for sk, codes in db.execute(select(Series.series_key, Series.dim_codes)).all()
    }
    wanted: dict[str, str] = {}
    if scope:
        for part in scope.split(","):
            if "=" in part:
                k, val = part.split("=", 1)
                wanted[k.strip()] = val.strip()

    rows = []
    for (sk, bid), value in values.items():
        codes = dim_codes.get(sk, {})
        if wanted and any(codes.get(k) != v2 for k, v2 in wanted.items()):
            continue
        rows.append(
            {"series_key": sk, **codes, "period": buckets.get(bid, str(bid)), "value": round(value, 2)}
        )
    rows.sort(key=lambda r: (r["period"], r["series_key"]))
    return {
        "version": {"id": v.id, "name": v.name, "kind": v.kind, "status": v.status},
        "layer": layer, "grain": grain, "rows": rows,
        "summary": f"{len(rows)} satır dondu, versiyon {v.name} ({v.status})",
    }


class ScheduleIn(BaseModel):
    report_code: str
    cron: str = Field(description="Örnek: 0 7 1 * *")
    params: dict = Field(default_factory=dict)
    recipients: str = ""


@router.get("/reports/scheduled", summary="Zamanlanmış raporlar")
def scheduled(db: Session = Depends(get_session), _p: Principal = Depends(current_principal)) -> dict:
    rows = db.execute(select(ScheduledReport)).scalars().all()
    return {
        "scheduled": [
            {
                "id": r.id, "report_code": r.report_code, "cron": r.cron, "params": r.params,
                "recipients": r.recipients, "is_active": r.is_active,
                "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
            }
            for r in rows
        ],
        "note": "Zamanlayıcı bu derlemede kayıt tutar, e-posta gönderimi bağlanmadı (TODO).",
        "summary": f"{len(rows)} zamanlanmış rapor",
    }


@router.post("/reports/scheduled", summary="Rapor zamanla")
def schedule_report(
    payload: ScheduleIn,
    db: Session = Depends(get_session),
    _p: Principal = Depends(require_roles("admin", "planner", "finance")),
) -> dict:
    row = ScheduledReport(
        report_code=payload.report_code, cron=payload.cron, params=payload.params,
        recipients=payload.recipients,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "summary": f"{payload.report_code} raporu {payload.cron} ifadesiyle zamanlandi"}
