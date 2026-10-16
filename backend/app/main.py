"""Application entry point."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import admin, analysis, assistant, auth, meta, planning, reports, scenarios
from app.core.config import settings
from app.core.db import engine, session_scope
from app.core.logging import correlation_id, get_logger, new_correlation_id, setup_logging
from app.domain.models import Base

log = get_logger(__name__)

DESCRIPTION = """
System Optima tahmin platformu. Hazir modellerden skor uretir, saha ve ekip
girdileriyle konsensus tahmin olusturur, senaryo calistirir, istenen kirilimda
analiz eder ve sonuclari raporlar.

Butun uc noktalar rol ve satir yetkisiyle suzulur. Her yazma islemi audit
log'a duser ve her yanitta kisa bir insan okunur ozet alani bulunur.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    new_correlation_id()
    Base.metadata.create_all(engine)
    from app.adapters import get_store

    get_store().init_schema()
    _seed_if_empty()
    try:
        with session_scope() as db:
            from app.forecast_models import registry, runner
            from app.services import layers

            layers.ensure_layers(db)
            registry.sync_registry(db)
            purged = runner.purge_expired_drafts(db)
            if purged:
                log.info("süresi geçen %s taslak silindi", purged)
    except Exception:  # noqa: BLE001  pragma: no cover
        log.exception("başlangıç bakimi atlandı")
    log.info("%s hazır", settings.app_name)
    yield


def _seed_if_empty() -> None:
    """Boş bir veritabanıyla açılan sunucu demo verisini kendisi kurar.

    Sunucuda disk kalıcı değilse her yeniden başlatmada veri sıfırlanır; demo
    için doğru davranış budur, kurulumun elle yapılması gerekmez. AUTO_SEED
    kapalıyken hiçbir şey yapmaz, yerel geliştirmeyi etkilemez.
    """
    if not settings.auto_seed:
        return
    try:
        from sqlalchemy import func, select

        from app.domain.models import User

        with session_scope() as db:
            if db.execute(select(func.count(User.id))).scalar_one():
                return
        log.info("veritabanı boş, demo verisi kuruluyor")
        from app.demo.loader import load_demo

        summary = load_demo(reset=False)
        log.info("demo verisi hazır", extra={"series": summary.get("series")})
    except Exception:  # noqa: BLE001  pragma: no cover
        log.exception("demo kurulumu başarısız, uygulama boş veriyle açılıyor")


app = FastAPI(
    title="Forecast Platformu",
    description=DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or new_correlation_id()
    correlation_id.set(cid)
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["x-correlation-id"] = cid
    if request.url.path.startswith("/api/"):
        log.info(
            "%s %s %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={"duration_ms": int((time.perf_counter() - t0) * 1000)},
        )
    return response


for router in (auth.router, meta.router, planning.router, scenarios.router,
               analysis.router, reports.router, admin.router, assistant.router):
    app.include_router(router)


@app.get("/api/v1/health", tags=["Sağlık"], summary="Sağlık kontrolü")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "sector": settings.sector, "summary": "Servis ayakta"}


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):  # pragma: no cover
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# Frontend: no build step, served straight from disk.
if settings.frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=settings.frontend_dir / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(settings.frontend_dir / "index.html")
