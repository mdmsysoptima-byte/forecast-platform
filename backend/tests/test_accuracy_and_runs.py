"""Accuracy metrics, FVA, model contract and run engine behaviour."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.models import DriverSet, DriverSetValue, ModelRegistry, Run, Scenario, Version
from app.domain.series import make_series_key
from app.forecast_models import registry, runner
from app.services import accuracy


def test_wmape_is_volume_weighted():
    pairs = [
        {"forecast": 110.0, "actual": 100.0},   # 10 percent error on volume 100
        {"forecast": 2.0, "actual": 1.0},       # 100 percent error on volume 1
    ]
    m = accuracy.metrics(pairs)
    assert m["wmape"] == pytest.approx(11 / 101 * 100, abs=0.01)
    assert m["mape"] == pytest.approx(55.0, abs=0.01)
    assert m["wmape"] < m["mape"], "hacim ağırlıklı hata basit ortalamadan kucuk olmali"


def test_bias_sign():
    assert accuracy.metrics([{"forecast": 120.0, "actual": 100.0}])["bias"] == 20.0
    assert accuracy.metrics([{"forecast": 80.0, "actual": 100.0}])["bias"] == -20.0


def test_accuracy_degrades_with_lag(db):
    rows = accuracy.accuracy_by_lag(db, "consensus", max_lag=6)
    assert len(rows) >= 4
    wmapes = [r["wmape"] for r in rows]
    assert wmapes[0] < wmapes[-1], f"lag arttıkça hata artmali: {wmapes}"


def test_fva_ladder_is_ordered_and_adds_value(db):
    ladder = accuracy.fva_ladder(db, lag=1)
    steps = {s["layer"]: s for s in ladder["steps"]}
    assert "naive" in steps and "consensus" in steps
    assert steps["naive"]["wmape"] > steps["statistical"]["wmape"]
    assert steps["consensus"]["wmape"] <= steps["statistical"]["wmape"]
    assert ladder["total_value_added"] > 0
    for s in ladder["steps"][1:]:
        assert s["contribution"] is not None


def test_accuracy_by_level_and_heatmap(db):
    rows = accuracy.accuracy_by_level(db, {"product": "category"}, "consensus", 1)
    assert len(rows) == 4
    assert all(r["wmape"] is not None for r in rows)
    heat = accuracy.bias_heatmap(db, {"product": "category"}, "consensus", 1)
    assert heat["rows"] and heat["columns"] and heat["cells"]


def test_series_key_is_deterministic_and_order_free():
    a = make_series_key({"product": "SKU001", "geo": "IST"}, "units")
    b = make_series_key({"geo": "IST", "product": "SKU001"}, "units")
    c = make_series_key({"product": "SKU002", "geo": "IST"}, "units")
    assert a == b
    assert a != c
    assert len(a) == 40


def test_model_registry_reports_contract(db):
    rows = {m.code: m for m in db.execute(select(ModelRegistry)).scalars()}
    assert set(rows) == {"seasonal_naive", "driver_regression"}
    dr = rows["driver_regression"]
    assert dr.required_drivers == ["cpi", "fx_usd"]
    assert dr.output_has_interval is True
    model = registry.load_model(dr)
    assert model.code == "driver_regression"


def test_nearest_supported_level_falls_back_and_explains(db):
    dr = db.execute(select(ModelRegistry).where(ModelRegistry.code == "driver_regression")).scalar_one()
    resolved, notes = registry.nearest_supported_level(dr, {"product": "category", "geo": "city"})
    assert resolved["product"] == "sku"
    assert resolved["geo"] == "city"
    assert notes and "desteklenmiyor" in notes[0]


def test_missing_required_driver_fails_with_clear_message(client, planner_headers, db):
    """A driver set without future values makes the platform refuse the run."""
    empty = DriverSet(name="Boş set", description="Gelecek değeri yok", is_shared=True)
    db.add(empty)
    db.flush()
    assert not db.execute(
        select(DriverSetValue).where(DriverSetValue.driver_set_id == empty.id)
    ).scalars().all()
    scenario = Scenario(
        name="Eksik sürücü testi",
        model_registry_id=db.execute(
            select(ModelRegistry.id).where(ModelRegistry.code == "driver_regression")
        ).scalar_one(),
        driver_set_id=empty.id,
        cycle_id=db.execute(select(Version.cycle_id).where(Version.kind == "baseline")).scalar(),
        scope={}, grain="month", level={"product": "sku", "geo": "city"}, horizon_periods=6,
    )
    db.add(scenario)
    db.flush()
    run = Run(
        scenario_id=scenario.id, model_registry_id=scenario.model_registry_id,
        cycle_id=scenario.cycle_id, status="queued", scope_hash="test", started_by=1,
    )
    db.add(run)
    db.commit()
    run_id = run.id

    runner.execute_run(run_id)
    db.expire_all()
    done = db.get(Run, run_id)
    assert done.status == "failed"
    assert "Zorunlu sürücü değeri eksik" in (done.error or "")
    assert "cpi" in done.error or "TUFE" in done.error


def test_draft_expiry_purges_old_drafts(db):
    draft = Version(name="Eski taslak", kind="draft", status="draft",
                    expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    db.add(draft)
    db.commit()
    purged = runner.purge_expired_drafts(db)
    db.commit()
    assert purged >= 1
    assert db.get(Version, draft.id) is None


def test_run_records_diagnostics_and_duration(db):
    rows = db.execute(select(Run).where(Run.status == "done")).scalars().all()
    assert rows
    r = rows[0]
    assert r.duration_ms > 0
    assert r.series_count == 200
    assert r.diagnostics.get("model")
    assert r.duration_ms < 60_000, "200 seri 60 saniyenin altında kalmalı"


def test_version_carries_full_provenance(db):
    v = db.execute(select(Version).where(Version.kind == "baseline")).scalars().first()
    prov = v.provenance
    for key in ("scenario", "driver_set_id", "model", "level", "grain", "data_as_of", "rules"):
        assert key in prov, f"{key} izlenebilirlik zincirinde yok"
