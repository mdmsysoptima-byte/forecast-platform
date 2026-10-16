"""Demo loader. One command fills an empty environment (section 14, step 1)."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

import numpy as np
from sqlalchemy import delete, select

from app.adapters import get_store
from app.core.auth import Principal, hash_password
from app.core.config import settings
from app.core.db import engine, session_scope
from app.core.logging import get_logger, new_correlation_id
from app.demo import generator
from app.domain.models import (
    Base,
    Cycle,
    Dimension,
    Driver,
    DriverSet,
    DriverSetValue,
    DriverValue,
    FxRate,
    Hierarchy,
    HierarchyNode,
    Measure,
    Member,
    ModelRegistry,
    PriceIndex,
    ReasonCode,
    Role,
    RowPermission,
    Run,
    Scenario,
    ScenarioRule,
    Series,
    TimeBucket,
    User,
    UserRole,
    Version,
)
from app.domain.series import make_series_key, scope_hash
from app.forecast_models import registry, runner
from app.services import alerts, layers, override_svc, sector_loader

log = get_logger(__name__)

CURRENT_PERIOD = "2026-10"
FIRST_YEAR, LAST_YEAR = 2022, 2029
PRIOR_CYCLE_COUNT = 12

DEMO_USERS = [
    ("admin@demo.local", "Ayşe Yılmaz", ["admin"], None),
    ("planner@demo.local", "Mert Demir", ["planner"], None),
    ("sales@demo.local", "Zeynep Kaya", ["sales_contributor"], ("geo", "R1")),
    ("marketing@demo.local", "Can Arslan", ["marketing_contributor"], None),
    ("finance@demo.local", "Elif Şahin", ["finance"], None),
    ("viewer@demo.local", "Burak Çelik", ["viewer"], None),
]

ROLE_NAMES = {
    "admin": "Yönetici",
    "planner": "Planlamacı",
    "sales_contributor": "Satış katkıcısı",
    "marketing_contributor": "Pazarlama katkıcısı",
    "finance": "Finans",
    "viewer": "İzleyici",
}


def _reset_schema() -> None:
    """Start from an empty schema.

    SQLite refuses DROP TABLE while foreign keys are enforced and the model has
    circular references (version to scenario and back), so the database file is
    removed instead.
    """
    from pathlib import Path

    engine.dispose()
    if settings.app_db_url.startswith("sqlite"):
        path = Path(settings.app_db_url.split("///", 1)[1])
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.unlink()
    else:  # pragma: no cover - other backends
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def load_demo(reset: bool = True) -> dict:
    new_correlation_id()
    t0 = time.perf_counter()
    store = get_store()
    if reset:
        _reset_schema()
        store.init_schema()
        store.truncate_all()
    else:
        Base.metadata.create_all(engine)
        store.init_schema()

    summary: dict = {}
    data = generator.build(CURRENT_PERIOD)

    with session_scope() as db:
        cfg, cycle = sector_loader.load_sector(
            db, settings.sector_config_path, FIRST_YEAR, LAST_YEAR, CURRENT_PERIOD
        )
        summary["sector"] = cfg.name

        rep_p = sector_loader.load_hierarchy_rows(db, "product", "default", data.product_rows)
        rep_g = sector_loader.load_hierarchy_rows(db, "geo", "default", data.geo_rows)
        summary["hierarchy_validation"] = [
            {"hierarchy": r.hierarchy, "nodes": r.nodes, "leaves": r.leaves, "levels": r.levels,
             "ok": r.ok, "errors": r.errors}
            for r in (rep_p, rep_g)
        ]

        measure = db.execute(select(Measure).where(Measure.code == "units")).scalar_one()
        buckets = {
            b.label: b for b in db.execute(
                select(TimeBucket).where(TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == "month")
            ).scalars()
        }
        members = {m.code: m for m in db.execute(select(Member)).scalars()}

        # Series
        key_of: dict[tuple[str, str], str] = {}
        for s in data.series:
            dim_codes = {"product": s["sku"], "geo": s["city"]}
            key = make_series_key(dim_codes, measure.code)
            key_of[(s["sku"], s["city"])] = key
            db.add(
                Series(
                    series_key=key,
                    dim_codes=dim_codes,
                    dim_members={"product": members[s["sku"]].id, "geo": members[s["city"]].id},
                    measure_id=measure.id,
                    grain="month",
                    attributes={},
                )
            )
        db.flush()
        summary["series"] = len(data.series)
        summary["series_node_rows"] = sector_loader.rebuild_series_nodes(db)

        # Actuals into the analytic store
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = [
            {
                "series_key": key_of[(a["sku"], a["city"])],
                "bucket_id": buckets[a["bucket"]].id,
                "value": a["value"],
                "loaded_at": now_iso,
            }
            for a in data.actuals
            if a["bucket"] in buckets
        ]
        summary["actual_rows"] = store.write_actuals(rows)

        # Driver history, price index and fx
        drivers = {d.code: d for d in db.execute(select(Driver)).scalars()}
        dv_rows = []
        for code in ("cpi", "fx_usd"):
            for r in data.drivers[code]:
                dv_rows.append(
                    DriverValue(
                        driver_id=drivers[code].id, bucket_id=buckets[r["bucket"]].id,
                        member_id=None, value=r["value"], is_actual=True,
                    )
                )
        for r in data.drivers["promo_intensity"]:
            member = members.get(r["member"])
            if member is None:
                continue
            dv_rows.append(
                DriverValue(
                    driver_id=drivers["promo_intensity"].id, bucket_id=buckets[r["bucket"]].id,
                    member_id=member.id, value=r["value"], is_actual=True,
                )
            )
        db.add_all(dv_rows)
        # Price index and fx cover history and, from the baseline assumption set,
        # the forecast window as well. Without the forward rows the real TRY and
        # foreign currency views would silently do nothing for future periods, and
        # the derived revenue measure would freeze at today's price level.
        baseline_future = generator.driver_set_future(data.meta, "baz")
        index_rows = (
            [(r["bucket"], r["value"]) for r in data.drivers["cpi"]]
            + [(r["bucket"], r["value"]) for r in baseline_future["cpi"]]
        )
        fx_rows = (
            [(r["bucket"], r["value"]) for r in data.drivers["fx_usd"]]
            + [(r["bucket"], r["value"]) for r in baseline_future["fx_usd"]]
        )
        for label, value in index_rows:
            y, m = (int(x) for x in label.split("-"))
            db.add(PriceIndex(date=date(y, m, 1), index_code="tufe", value=value))
        for label, value in fx_rows:
            y, m = (int(x) for x in label.split("-"))
            db.add(FxRate(date=date(y, m, 1), from_ccy="USD", to_ccy="TRY", rate=value))
        db.flush()
        summary["driver_value_rows"] = len(dv_rows)
        summary["price_index_rows"] = len(index_rows)

        # Driver sets
        set_ids: dict[str, int] = {}
        for profile, name, desc in (
            ("baz", "Baz", "Orta vadeli program çizgisinde enflasyon ve kur"),
            ("yuksek_enflasyon", "Yüksek enflasyon", "Aylık yüzde 4 enflasyon, kur yüzde 3,5"),
            ("kur_soku", "Kur şoku", "Kur aylık yüzde 5,5, enflasyon sınırlı"),
        ):
            ds = DriverSet(name=name, description=desc, is_shared=True)
            db.add(ds)
            db.flush()
            set_ids[profile] = ds.id
            future = generator.driver_set_future(data.meta, profile)
            for code, rows_f in future.items():
                for r in rows_f:
                    if r["bucket"] not in buckets:
                        continue
                    db.add(
                        DriverSetValue(
                            driver_set_id=ds.id, driver_id=drivers[code].id,
                            bucket_id=buckets[r["bucket"]].id, member_id=None,
                            value=r["value"], entry_method=r["entry_method"],
                        )
                    )
        db.flush()
        summary["driver_sets"] = len(set_ids)

        # Users, roles, row permissions
        role_rows = {}
        for code, name in ROLE_NAMES.items():
            r = Role(code=code, name=name)
            db.add(r)
            role_rows[code] = r
        db.flush()
        geo_dim = db.execute(select(Dimension).where(Dimension.code == "geo")).scalar_one()
        geo_hier = db.execute(select(Hierarchy).where(Hierarchy.dimension_id == geo_dim.id)).scalar_one()
        for email, name, roles, scope in DEMO_USERS:
            u = User(email=email, name=name, password_hash=hash_password(settings.demo_password))
            db.add(u)
            db.flush()
            for rc in roles:
                db.add(UserRole(user_id=u.id, role_id=role_rows[rc].id))
            if scope:
                _dim, member_code = scope
                node = db.execute(
                    select(HierarchyNode)
                    .join(Member, Member.id == HierarchyNode.member_id)
                    .where(HierarchyNode.hierarchy_id == geo_hier.id, Member.code == member_code)
                ).scalar_one()
                db.add(
                    RowPermission(
                        user_id=u.id, dimension_id=geo_dim.id, hierarchy_node_id=node.id, can_edit=True
                    )
                )
        db.flush()
        summary["users"] = len(DEMO_USERS)

        registry.sync_registry(db)
        summary["models"] = [m.code for m in db.execute(select(ModelRegistry)).scalars()]

        # Prior closed cycles so accuracy and FVA have history
        summary["prior_cycles"] = _build_prior_cycles(db, cycle, buckets, key_of, store)

        scenario_ids = _build_scenarios(db, cycle, set_ids)
        summary["scenarios"] = len(scenario_ids)

    # Baseline run and a comparison run, executed synchronously for the demo.
    versions: list[int] = []
    for idx, (scenario_name, version_name, kind) in enumerate(
        [("Baz senaryo", f"Baz tahmin {CURRENT_PERIOD}", "baseline"),
         ("Yüksek enflasyon", f"Yüksek enflasyon {CURRENT_PERIOD}", "saved")]
    ):
        with session_scope() as db:
            sc = db.execute(select(Scenario).where(Scenario.name == scenario_name)).scalar_one()
            model_id = sc.model_registry_id
            run = Run(
                scenario_id=sc.id, model_registry_id=model_id, cycle_id=sc.cycle_id,
                status="queued", scope_hash=scope_hash(sc.scope or {}), started_by=1,
            )
            db.add(run)
            db.flush()
            run_id = run.id
        runner.execute_run(run_id)
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run.status != "done":
                raise RuntimeError(f"Demo çalıştırması başarısız: {run.error}")
            v = db.execute(select(Version).where(Version.run_id == run_id)).scalar_one()
            v.name = version_name
            v.kind = kind
            v.expires_at = None
            versions.append(v.id)
            if idx == 0:
                _write_budget(db, v.id, data, buckets_of(db), key_of)
                sc2 = db.execute(select(Scenario).where(Scenario.name == "Yüksek enflasyon")).scalar_one()
                sc2.base_version_id = v.id
    summary["versions"] = versions

    # Contributor input, overrides and insights on the baseline version
    with session_scope() as db:
        summary["seeded_edits"] = _seed_edits(db, versions[0], key_of, buckets_of(db))
        layers.recompute_consensus(db, versions[0])
        alerts.generate_for_version(db, versions[0])
    store.create_bi_views()

    summary["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    log.info("demo verisi yüklendi")
    return summary


def buckets_of(db) -> dict[str, TimeBucket]:
    cycle = db.execute(select(Cycle).order_by(Cycle.id)).scalars().first()
    return {
        b.label: b for b in db.execute(
            select(TimeBucket).where(TimeBucket.calendar_id == cycle.calendar_id, TimeBucket.grain == "month")
        ).scalars()
    }


def _write_budget(db, version_id: int, data, buckets, key_of) -> int:
    values = {
        (key_of[(b["sku"], b["city"])], buckets[b["bucket"]].id): b["value"]
        for b in data.budget
        if b["bucket"] in buckets
    }
    layers.write_values(db, version_id, "budget", values, source="allocation", replace=True)
    return len(values)


def _build_prior_cycles(db, active: Cycle, buckets: dict[str, TimeBucket], key_of, store) -> int:
    """Locked cycles with snapshots, so lag based accuracy is real."""
    rng = np.random.default_rng(generator.SEED + 7)
    by_seq = {b.seq: b for b in buckets.values()}
    current = db.get(TimeBucket, active.current_period_bucket_id)
    actual = {
        (r["series_key"], r["bucket_id"]): float(r["value"])
        for r in store.read_series(sorted(set(key_of.values())))
    }
    sigma = {"statistical": 0.055, "sales": 0.050, "marketing": 0.052, "planner": 0.045, "consensus": 0.042}
    bias = {"statistical": 0.012, "sales": -0.004, "marketing": 0.006, "planner": 0.002, "consensus": 0.001}

    rows: list[dict] = []
    made = 0
    for back in range(PRIOR_CYCLE_COUNT, 0, -1):
        c_bucket = by_seq.get(current.seq - back)
        if c_bucket is None:
            continue
        cyc = Cycle(
            name=f"{c_bucket.label} çevrimi", calendar_id=active.calendar_id, grain="month",
            horizon_periods=active.horizon_periods, fence_periods=active.fence_periods,
            freeze_periods=active.freeze_periods, current_period_bucket_id=c_bucket.id,
            status="locked", locked_at=datetime.now(timezone.utc),
        )
        db.add(cyc)
        db.flush()
        made += 1
        snap_at = datetime.now(timezone.utc).isoformat()
        for lag in range(1, 7):
            target = by_seq.get(c_bucket.seq + lag - 1)
            if target is None:
                continue
            for key in key_of.values():
                a = actual.get((key, target.id))
                if a is None:
                    continue
                for code, s in sigma.items():
                    err = float(rng.normal(bias[code] * lag, s * np.sqrt(lag)))
                    rows.append(
                        {
                            "cycle_id": cyc.id, "snapshot_at": snap_at, "layer_code": code,
                            "series_key": key, "target_bucket_id": target.id, "lag": lag,
                            "value": round(max(0.0, a * (1.0 + err)), 2),
                        }
                    )
    if rows:
        store.write_snapshot(rows)
    return made


def _node_id(db, dimension_code: str, member_code: str) -> int:
    dim = db.execute(select(Dimension).where(Dimension.code == dimension_code)).scalar_one()
    hier = db.execute(select(Hierarchy).where(Hierarchy.dimension_id == dim.id)).scalar_one()
    return db.execute(
        select(HierarchyNode.id)
        .join(Member, Member.id == HierarchyNode.member_id)
        .where(HierarchyNode.hierarchy_id == hier.id, Member.code == member_code)
    ).scalar_one()


def _build_scenarios(db, cycle: Cycle, set_ids: dict[str, int]) -> list[int]:
    models = {m.code: m for m in db.execute(select(ModelRegistry)).scalars()}
    base_level = {"product": "sku", "geo": "city"}
    out: list[int] = []

    s1 = Scenario(
        name="Baz senaryo",
        description="Mevsimsel naive model, baz sürücü seti, kural yok",
        model_registry_id=models["seasonal_naive"].id,
        driver_set_id=set_ids["baz"], cycle_id=cycle.id, scope={}, grain="month",
        level=base_level, horizon_periods=cycle.horizon_periods, owner_user_id=2,
    )
    s2 = Scenario(
        name="Yüksek enflasyon",
        description="Sürücü regresyonu, yüksek enflasyon seti, fiyat esnekliği ve kapasite sınırı",
        model_registry_id=models["driver_regression"].id,
        driver_set_id=set_ids["yuksek_enflasyon"], cycle_id=cycle.id, scope={}, grain="month",
        level=base_level, horizon_periods=cycle.horizon_periods, owner_user_id=2,
    )
    s3 = Scenario(
        name="Promosyon dalgası",
        description="Atıştırmalık grubunda kampanya, bayram talebinin bir kısmını öne alma",
        model_registry_id=models["seasonal_naive"].id,
        driver_set_id=set_ids["baz"], cycle_id=cycle.id, scope={}, grain="month",
        level=base_level, horizon_periods=cycle.horizon_periods, owner_user_id=2,
    )
    db.add_all([s1, s2, s3])
    db.flush()

    snack = _node_id(db, "product", "C20")
    drink = _node_id(db, "product", "C10")
    istanbul = _node_id(db, "geo", "IST")

    db.add_all(
        [
            ScenarioRule(
                scenario_id=s2.id, order_index=0, type="elasticity", name="Fiyat esnekliği",
                params={"elasticity": -0.8, "price_pct": 15}, target_scope={},
            ),
            ScenarioRule(
                scenario_id=s2.id, order_index=1, type="cap", name="İçecek kapasite sınırı",
                target_scope={"product": {"nodes": [drink]}},
                params={"max": 9000, "scope_level": "total"},
            ),
            ScenarioRule(
                scenario_id=s3.id, order_index=0, type="multiplier", name="Atıştırmalık kampanyası",
                target_scope={"product": {"nodes": [snack]}},
                bucket_from="2026-11", bucket_to="2027-01", params={"pct": 8},
            ),
            ScenarioRule(
                scenario_id=s3.id, order_index=1, type="shift", name="Bayram talebini öne alma",
                target_scope={}, params={"source_bucket": "2027-03", "target_bucket": "2027-02", "ratio": 30},
            ),
            ScenarioRule(
                scenario_id=s3.id, order_index=2, type="additive", name="İstanbul aralık ek talep",
                target_scope={"geo": {"nodes": [istanbul]}},
                bucket_from="2026-12", bucket_to="2026-12", params={"amount": 5000, "per_bucket": True},
            ),
        ]
    )
    db.flush()
    out = [s1.id, s2.id, s3.id]
    return out


def _seed_edits(db, version_id: int, key_of, buckets) -> dict:
    """Sales input, planner overrides and field insights."""
    from app.core.auth import load_principal

    planner = load_principal(db, 2)
    sales = load_principal(db, 3)
    reasons = {r.code: r for r in db.execute(select(ReasonCode)).scalars()}
    target_label = sorted(buckets)[0]
    # First editable period after the fence
    cycle = db.execute(select(Cycle).order_by(Cycle.id)).scalars().first()
    from app.services import cycle_svc

    window = cycle_svc.forecast_window(db, cycle)
    flags = cycle_svc.period_flags(db, cycle)
    editable = [b for b in window if flags.get(b.id) in ("freeze", "open")]
    if not editable:
        return {"overrides": 0, "insights": 0}
    b1, b2 = editable[0], editable[2] if len(editable) > 2 else editable[-1]

    # Sales contributor raises its own scope
    counts = {"overrides": 0, "insights": 0, "copied_cells": 0}
    try:
        res = override_svc.apply_override(
            db, sales,
            override_svc.OverrideRequest(
                version_id=version_id, layer_code="sales", bucket_id=b1.id, mode="pct", value=6.0,
                reason_code_id=reasons["promo"].id, note="Marmara bölgesinde zincir mağaza kampanyası",
                scope={"geo": {"nodes": [_node_id(db, "geo", "R1")]}}, level={"geo": "region"},
            ),
        )
        counts["overrides"] += 1
        counts["sales_cells"] = res["cells"]
    except Exception as exc:  # noqa: BLE001
        log.warning("demo sales override atlandı: %s", exc)

    try:
        override_svc.apply_override(
            db, planner,
            override_svc.OverrideRequest(
                version_id=version_id, layer_code="planner", bucket_id=b2.id, mode="pct", value=-4.0,
                reason_code_id=reasons["competitor"].id, note="Rakip jenerik lansmanı bekleniyor",
                scope={"product": {"nodes": [_node_id(db, "product", "C20")]}}, level={"product": "category"},
            ),
        )
        counts["overrides"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("demo planner override atlandı: %s", exc)

    first_key = next(iter(key_of.values()))
    override_svc.add_insight(
        db, sales,
        text="İstanbul Anadolu yakasında yeni açılan 3 mağaza, talep ilk çeyrekte yukarı sürpriz yapabilir",
        series_key=first_key, bucket_from_id=b1.id, bucket_to_id=b2.id,
        reason_code_id=reasons["field_info"].id,
    )
    override_svc.add_insight(
        db, planner,
        text="Tedarikçi kapasitesi mart ayında bakım nedeniyle kısıtlı",
        series_key=first_key, bucket_from_id=b2.id, bucket_to_id=b2.id,
        reason_code_id=reasons["field_info"].id,
    )
    counts["insights"] = 2
    return counts


if __name__ == "__main__":  # pragma: no cover
    import json

    from app.core.logging import setup_logging

    setup_logging()
    print(json.dumps(load_demo(reset=True), ensure_ascii=False, indent=2))
