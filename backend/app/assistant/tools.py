"""Assistant layer (section 16), step 8.

The assistant produces no numbers. It only calls platform tools, and every
call goes through the same API, so role, row scope and audit rules apply
unchanged. This module declares the tool catalogue and the system prompt
builder. Wiring it to the Claude API is the remaining step and needs an
ANTHROPIC_API_KEY, so the chat loop itself is marked TODO.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.domain.models import Driver, ReasonCode, SectorConfig, TimeBucket
from app.services import cycle_svc, layers

# Tool groups mirror the table in section 16.
TOOL_CATALOGUE: list[dict] = [
    {
        "group": "Veri",
        "tools": [
            {"name": "list_connections", "method": "GET", "path": "/api/v1/admin/connections",
             "description": "Tanımlı veri baglantilarini listeler.", "roles": ["admin"], "writes": False},
            {"name": "data_profile", "method": "POST", "path": "/api/v1/admin/connections/test",
             "description": "Bağlantıyı test eder ve veri tazeligini dondurur.", "roles": ["admin"],
             "writes": False},
            {"name": "load_hierarchy", "method": "GET", "path": "/api/v1/dimensions/{dimension}/validate",
             "description": "Hiyerarşiyi doğrular ve rapor üretir.", "roles": ["admin", "planner"],
             "writes": False},
        ],
    },
    {
        "group": "Model",
        "tools": [
            {"name": "list_models", "method": "GET", "path": "/api/v1/models",
             "description": "Kayıtlı modelleri, destekledigi seviye ve zorunlu surucleri listeler.",
             "roles": [], "writes": False},
            {"name": "describe_model", "method": "GET", "path": "/api/v1/models",
             "description": "Tek bir modelin sozlesmesini aciklar.", "roles": [], "writes": False},
            {"name": "select_model", "method": "POST", "path": "/api/v1/scenarios",
             "description": "Senaryo olustururken modeli seçer.", "roles": ["planner"], "writes": True},
        ],
    },
    {
        "group": "Sürücü",
        "tools": [
            {"name": "list_drivers", "method": "GET", "path": "/api/v1/drivers",
             "description": "Sürücü katalogunu listeler.", "roles": [], "writes": False},
            {"name": "get_driver_values", "method": "GET", "path": "/api/v1/driver-sets/{set_id}/values",
             "description": "Bir sürücü setinin geçmiş ve gelecek değerlerini dondurur.", "roles": [],
             "writes": False},
            {"name": "set_driver_assumption", "method": "POST",
             "path": "/api/v1/driver-sets/{set_id}/assumption",
             "description": "Gelecek sürücü varsayimini girer. Yöntem: constant, pct_growth, trend, table.",
             "roles": ["planner", "finance"], "writes": True},
            {"name": "save_driver_set", "method": "POST", "path": "/api/v1/driver-sets",
             "description": "Yeni sürücü seti olusturur veya kopyalar.", "roles": ["planner"], "writes": True},
        ],
    },
    {
        "group": "Senaryo",
        "tools": [
            {"name": "create_scenario", "method": "POST", "path": "/api/v1/scenarios",
             "description": "Senaryo olusturur.", "roles": ["planner"], "writes": True},
            {"name": "add_rule", "method": "POST", "path": "/api/v1/scenarios/{id}/rules",
             "description": "Sıralı çıktı kuralı ekler.", "roles": ["planner"], "writes": True},
            {"name": "run_scenario", "method": "POST", "path": "/api/v1/runs",
             "description": "Bir senaryoyu çalıştırır, taslak versiyon üretir.", "roles": [], "writes": True},
            {"name": "run_many", "method": "POST", "path": "/api/v1/runs",
             "description": "Birden fazla senaryoyu paralel çalıştırır.", "roles": [], "writes": True},
            {"name": "compare", "method": "GET", "path": "/api/v1/versions/compare",
             "description": "Versiyonlari kural bazında ayristirarak karsilastirir.", "roles": [],
             "writes": False},
            {"name": "save_version", "method": "POST", "path": "/api/v1/versions/{id}/save",
             "description": "Taslağı ad ve açıklama ile saklar.", "roles": [], "writes": True},
            {"name": "discard_draft", "method": "DELETE", "path": "/api/v1/versions/{id}",
             "description": "Taslağı atar.", "roles": [], "writes": True},
        ],
    },
    {
        "group": "Kırılım ve analiz",
        "tools": [
            {"name": "query_forecast", "method": "POST", "path": "/api/v1/grid/query",
             "description": "Kapsam, seviye, kova, katman ve filtre ile tahmini okur.", "roles": [],
             "writes": False},
            {"name": "accuracy", "method": "GET", "path": "/api/v1/analysis/accuracy",
             "description": "Metrik, lag ve seviye bazında doğruluk.", "roles": [], "writes": False},
            {"name": "fva", "method": "GET", "path": "/api/v1/analysis/fva",
             "description": "FVA merdiveni ve kullanıcı bazında katkı.", "roles": [], "writes": False},
            {"name": "variance", "method": "GET", "path": "/api/v1/analysis/variance",
             "description": "İki versiyon arasindaki degisimin kırılım bazında köprüsü.", "roles": [],
             "writes": False},
            {"name": "budget_gap", "method": "GET", "path": "/api/v1/analysis/budget-gap",
             "description": "Konsensüs ve bütçe farkı.", "roles": [], "writes": False},
        ],
    },
    {
        "group": "Düzenleme",
        "tools": [
            {"name": "override", "method": "POST", "path": "/api/v1/override",
             "description": "Hücre veya toplu override. Gerekçe kodu zorunlu, kullanıcı onayı gerekir.",
             "roles": ["sales_contributor", "marketing_contributor", "planner", "finance"], "writes": True},
            {"name": "add_insight", "method": "POST", "path": "/api/v1/insights",
             "description": "Sayidan bağımsız saha bilgisi ekler.", "roles": [], "writes": True},
            {"name": "copy_layer", "method": "POST", "path": "/api/v1/layers/copy",
             "description": "Bir katmanı digerine kopyalar.", "roles": ["planner"], "writes": True},
        ],
    },
    {
        "group": "Rapor",
        "tools": [
            {"name": "generate_report", "method": "POST", "path": "/api/v1/reports/run",
             "description": "Standart raporu üretir.", "roles": [], "writes": False},
            {"name": "export_excel", "method": "POST", "path": "/api/v1/reports/export",
             "description": "Raporu Excel, CSV veya Parquet olarak verir.", "roles": [], "writes": False},
            {"name": "schedule_report", "method": "POST", "path": "/api/v1/reports/scheduled",
             "description": "Raporu cron ifadesiyle zamanlar.", "roles": ["planner", "finance"], "writes": True},
        ],
    },
    {
        "group": "Durum",
        "tools": [
            {"name": "explain_cell", "method": "POST", "path": "/api/v1/grid/explain",
             "description": "Bu sayı nereden geldi: versiyon, senaryo, sürücü seti, kural, override zinciri.",
             "roles": [], "writes": False},
            {"name": "list_versions", "method": "GET", "path": "/api/v1/versions",
             "description": "Versiyonlari listeler.", "roles": [], "writes": False},
            {"name": "list_alerts", "method": "GET", "path": "/api/v1/analysis/alerts",
             "description": "Açık uyarıları listeler.", "roles": [], "writes": False},
            {"name": "cycle_status", "method": "GET", "path": "/api/v1/time/cycle",
             "description": "Aktif çevrim, current period, fence ve freeze bilgisi.", "roles": [],
             "writes": False},
        ],
    },
]

BEHAVIOUR_RULES = [
    "Eksik parametre varsa tahmin etme, sor: seviye, kova, ufuk, kapsam, varsayım büyüklüğü.",
    "Yazma işlemleri kullanıcı onayı alınmadan çalıştırılmaz; ne yapacağını özetle, onay bekle.",
    "Kullanicinin rolu ve kapsamı disindaki araçlar sunulmaz.",
    "Asistan sayı uretmez; her sayı bir araç sonucundan gelir ve kaynağı gösterilir.",
    "Çok adimli istekler planlanir ve adım adım yurutulur; her adimin sonucu kisaca bildirilir.",
    "Audit log'da aktör assistant olarak işaretlenir.",
]


def tools_for(principal: Principal) -> list[dict]:
    """Only the tools the user may actually call are offered."""
    out: list[dict] = []
    for group in TOOL_CATALOGUE:
        allowed = [
            t for t in group["tools"]
            if not t["roles"] or principal.is_admin or any(r in principal.roles for r in t["roles"])
        ]
        if allowed:
            out.append({"group": group["group"], "tools": allowed})
    return out


def system_prompt(db: Session, principal: Principal) -> str:
    """Sector summary, hierarchies, drivers, reason codes, cycle state, role."""
    cfg = db.execute(select(SectorConfig)).scalars().first()
    payload = (cfg.payload if cfg else {}) or {}
    dims = []
    for d in payload.get("dimensions") or []:
        for h in d.get("hierarchies") or []:
            dims.append(f"{d['code']}.{h['code']}: {' > '.join(h.get('levels', []))}")
    drivers = [f"{d.code} ({d.name})" for d in db.execute(select(Driver)).scalars()]
    reasons = [f"{r.code}: {r.name}{' [not zorunlu]' if r.requires_note else ''}"
               for r in db.execute(select(ReasonCode)).scalars()]
    cycle = cycle_svc.active_cycle(db)
    current = db.get(TimeBucket, cycle.current_period_bucket_id) if cycle else None
    lm = layers.layer_map(db)

    lines = [
        "Sen bir tahmin platformunun asistanisin. Kendi başına sayı uretmezsin;",
        "yalnızca platform araclarini cagirirsin ve sonucun kaynagini gosterirsin.",
        "",
        f"Sektör: {cfg.name if cfg else 'tanimsiz'}",
        "Hiyerarşiler:",
        *[f"  - {x}" for x in dims],
        f"Sürücü kataloğu: {', '.join(drivers)}",
        f"Gerekçe kodlari: {'; '.join(reasons)}",
        f"Katmanlar: {', '.join(lm.keys())}",
        f"Konsensüs kuralı: {layers.consensus_config(db)['rule']}",
    ]
    if cycle and current:
        lines += [
            f"Aktif çevrim: {cycle.name}, durum {cycle.status}, current period {current.label},",
            f"ufuk {cycle.horizon_periods} dönem, fence {cycle.fence_periods}, freeze {cycle.freeze_periods}.",
        ]
    lines += [
        f"Kullanıcı: {principal.name}, roller {', '.join(principal.roles)},",
        f"satır kapsamı {'sınırlı' if not principal.unrestricted else 'tam'}.",
        "",
        "Davranis kuralları:",
        *[f"  - {r}" for r in BEHAVIOUR_RULES],
    ]
    return "\n".join(lines)


def chat(db: Session, principal: Principal, message: str, context: dict | None = None) -> dict:
    """TODO (step 8): Claude API tool use loop.

    The catalogue, the system prompt and the approval rules are ready. Wiring
    needs ANTHROPIC_API_KEY and the anthropic client; until then the endpoint
    reports what it would do instead of pretending to answer.
    """
    return {
        "ok": False,
        "status": "not_connected",
        "message": (
            "Asistan katmanı adım 8 olarak tanımlı. Arac kataloğu, sistem promptu ve onay kuralları "
            "hazır; Claude API baglantisi için ANTHROPIC_API_KEY gerekiyor."
        ),
        "would_use_tools": [t["name"] for g in tools_for(principal) for t in g["tools"]][:10],
        "system_prompt_preview": system_prompt(db, principal)[:900],
        "echo": message,
        "context": context or {},
    }
