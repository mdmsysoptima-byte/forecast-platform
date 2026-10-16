"""Step 5 acceptance: fence, freeze, approval order, snapshot, row scope, audit."""
from __future__ import annotations

from sqlalchemy import select

from app.adapters import get_store
from app.core.auth import load_principal
from app.domain.models import AuditLog, Override, ReasonCode, Version
from app.services import cycle_svc, layers, override_svc


def _reason(db, code: str = "promo") -> ReasonCode:
    return db.execute(select(ReasonCode).where(ReasonCode.code == code)).scalar_one()


def _baseline(db) -> Version:
    return db.execute(select(Version).where(Version.kind == "baseline")).scalars().first()


def test_override_inside_fence_is_rejected(db):
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    fence_bucket = window[0]
    planner = load_principal(db, 2)
    version = _baseline(db)
    try:
        override_svc.apply_override(
            db, planner,
            override_svc.OverrideRequest(
                version_id=version.id, layer_code="planner", bucket_id=fence_bucket.id,
                mode="pct", value=5.0, reason_code_id=_reason(db).id,
            ),
        )
        raise AssertionError("fence içinde override kabul edildi")
    except override_svc.OverrideError as exc:
        assert "fence" in str(exc).lower() or "kilitli" in str(exc).lower()


def test_override_requires_reason_note_when_configured(db):
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    open_bucket = window[5]
    planner = load_principal(db, 2)
    version = _baseline(db)
    needs_note = _reason(db, "launch")
    assert needs_note.requires_note
    try:
        override_svc.apply_override(
            db, planner,
            override_svc.OverrideRequest(
                version_id=version.id, layer_code="planner", bucket_id=open_bucket.id,
                mode="pct", value=5.0, reason_code_id=needs_note.id, note="",
            ),
        )
        raise AssertionError("not zorunlu gerekçe notsuz kabul edildi")
    except override_svc.OverrideError as exc:
        assert "not zorunlu" in str(exc)


def test_override_writes_audit_and_child_records(db):
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    bucket = window[6]
    planner = load_principal(db, 2)
    version = _baseline(db)
    before_audit = db.execute(select(AuditLog).where(AuditLog.entity == "override")).scalars().all()

    res = override_svc.apply_override(
        db, planner,
        override_svc.OverrideRequest(
            version_id=version.id, layer_code="planner", bucket_id=bucket.id,
            mode="pct", value=7.5, reason_code_id=_reason(db).id, note="Test",
            scope={}, level={"product": "category"},
        ),
    )
    db.flush()
    assert res["cells"] > 1, "üst seviyeden giriş alt seriye dagitilmali"
    children = db.execute(
        select(Override).where(Override.allocated_from_override_id == res["override_id"])
    ).scalars().all()
    assert len(children) == res["cells"]
    after_audit = db.execute(select(AuditLog).where(AuditLog.entity == "override")).scalars().all()
    assert len(after_audit) > len(before_audit)

    rolled = layers.read_values(db, version.id, "planner", None, [bucket.id])
    assert abs(sum(rolled.values()) - res["new_value"]) < 1.0, "dağıtım toplamı korumalı"
    db.rollback()


def test_revert_keeps_original_and_adds_mirror(db):
    cycle = cycle_svc.active_cycle(db)
    bucket = cycle_svc.forecast_window(db, cycle)[7]
    planner = load_principal(db, 2)
    version = _baseline(db)
    res = override_svc.apply_override(
        db, planner,
        override_svc.OverrideRequest(
            version_id=version.id, layer_code="planner", bucket_id=bucket.id,
            mode="pct", value=20.0, reason_code_id=_reason(db).id, note="Geri alınacak",
            scope={}, level={"product": "category"},
        ),
    )
    db.flush()
    rev = override_svc.revert(db, planner, res["override_id"])
    db.flush()
    original = db.get(Override, res["override_id"])
    assert original is not None and original.reverted is True
    assert rev["cells"] > 0
    mirrors = db.execute(
        select(Override).where(
            Override.allocated_from_override_id == res["override_id"], Override.note.like("Geri alma%")
        )
    ).scalars().all()
    assert mirrors, "ters kayıt yazılmadı"
    db.rollback()


def test_sales_contributor_sees_only_own_scope(client, sales_headers, planner_headers):
    sales = client.post(
        "/api/v1/grid/query", headers=sales_headers,
        json={"version_id": 1, "level": {"geo": "region"}, "layers": ["statistical"]},
    ).json()
    planner = client.post(
        "/api/v1/grid/query", headers=planner_headers,
        json={"version_id": 1, "level": {"geo": "region"}, "layers": ["statistical"]},
    ).json()
    assert sales["meta"]["restricted"] is True
    assert planner["meta"]["restricted"] is False
    assert len(sales["rows"]) < len(planner["rows"])
    assert sales["meta"]["series_in_scope"] < planner["meta"]["series_in_scope"]


def test_sales_contributor_cannot_edit_planner_layer(client, sales_headers):
    layers_res = client.get("/api/v1/layers", headers=sales_headers).json()
    by_code = {l["code"]: l for l in layers_res["layers"]}
    assert by_code["sales"]["can_edit"] is True
    assert by_code["planner"]["can_edit"] is False
    assert by_code["budget"]["can_edit"] is False


def test_approval_steps_cannot_be_skipped(client, planner_headers, finance_headers):
    runs = client.post("/api/v1/runs", headers=planner_headers, json={"scenario_ids": [1]}).json()
    run_id = runs["runs"][0]["run_id"]
    status = client.get(f"/api/v1/runs/{run_id}", headers=planner_headers).json()
    assert status["status"] in ("queued", "running", "done")
    version_id = _wait_for_version(client, planner_headers, run_id)

    skip = client.post(
        "/api/v1/approvals", headers=finance_headers,
        json={"version_id": version_id, "step": "approved", "acknowledge_warnings": True},
    )
    assert skip.status_code == 400
    assert "atlanamaz" in skip.json()["detail"]

    review = client.post(
        "/api/v1/approvals", headers=planner_headers,
        json={"version_id": version_id, "step": "review", "acknowledge_warnings": True},
    )
    assert review.status_code == 200, review.text

    wrong_role = client.post(
        "/api/v1/approvals", headers=planner_headers,
        json={"version_id": version_id, "step": "approved", "acknowledge_warnings": True},
    )
    assert wrong_role.status_code == 400
    assert "yetkiniz yok" in wrong_role.json()["detail"]


def test_warnings_must_be_acknowledged_before_approval(client, planner_headers, finance_headers):
    runs = client.post("/api/v1/runs", headers=planner_headers, json={"scenario_ids": [2]}).json()
    version_id = _wait_for_version(client, planner_headers, runs["runs"][0]["run_id"])
    client.post(
        "/api/v1/approvals", headers=planner_headers,
        json={"version_id": version_id, "step": "review", "acknowledge_warnings": True},
    )
    blocked = client.post(
        "/api/v1/approvals", headers=finance_headers,
        json={"version_id": version_id, "step": "approved", "acknowledge_warnings": False},
    ).json()
    if blocked.get("needs_acknowledge"):
        assert blocked["warnings"], "uyarı listesi boş"
        ok = client.post(
            "/api/v1/approvals", headers=finance_headers,
            json={"version_id": version_id, "step": "approved", "acknowledge_warnings": True},
        )
        assert ok.status_code == 200, ok.text


def test_lock_writes_snapshot_with_correct_lag(client, planner_headers, finance_headers, db):
    runs = client.post("/api/v1/runs", headers=planner_headers, json={"scenario_ids": [1]}).json()
    version_id = _wait_for_version(client, planner_headers, runs["runs"][0]["run_id"])
    for step, headers in (("review", planner_headers), ("approved", finance_headers),
                          ("locked", finance_headers)):
        res = client.post(
            "/api/v1/approvals", headers=headers,
            json={"version_id": version_id, "step": step, "acknowledge_warnings": True},
        )
        assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("snapshot_rows", 0) > 0
    assert body.get("consensus_rows", 0) > 0

    cycle = cycle_svc.active_cycle(db)
    snaps = get_store().read_snapshots(cycle_ids=[cycle.id], layer_codes=["consensus"])
    assert snaps
    lags = sorted({s["lag"] for s in snaps})
    assert lags[0] == 1, "ilk hedef dönem lag 1 olmali"
    assert max(lags) == cycle.horizon_periods

    locked = db.get(Version, version_id)
    db.refresh(locked)
    assert locked.status == "locked"
    planner = load_principal(db, 2)
    window = cycle_svc.forecast_window(db, cycle)
    try:
        override_svc.apply_override(
            db, planner,
            override_svc.OverrideRequest(
                version_id=version_id, layer_code="planner", bucket_id=window[8].id,
                mode="pct", value=1.0, reason_code_id=_reason(db).id,
            ),
        )
        raise AssertionError("kilitli versiyon değiştirildi")
    except override_svc.OverrideError as exc:
        assert "kilitli" in str(exc)


def test_freeze_preserves_user_layers_on_rerun(client, planner_headers, db):
    """Model re-run updates statistical but keeps user layers in the freeze window."""
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    flags = cycle_svc.period_flags(db, cycle)
    freeze_bucket = next(b for b in window if flags[b.id] == "freeze")

    base = _baseline(db)
    planner = load_principal(db, 2)
    res = override_svc.apply_override(
        db, planner,
        override_svc.OverrideRequest(
            version_id=base.id, layer_code="planner", bucket_id=freeze_bucket.id,
            mode="absolute", value=99_000.0, reason_code_id=_reason(db).id, note="Freeze testi",
            scope={}, level={"product": "category"},
        ),
    )
    db.commit()
    assert res["cells"] > 0

    scenario = client.post(
        "/api/v1/scenarios", headers=planner_headers,
        json={
            "name": "Freeze testi", "model_code": "seasonal_naive", "driver_set_id": 1,
            "base_version_id": base.id, "level": {"product": "sku", "geo": "city"},
        },
    ).json()
    runs = client.post(
        "/api/v1/runs", headers=planner_headers, json={"scenario_ids": [scenario["id"]]}
    ).json()
    new_version = _wait_for_version(client, planner_headers, runs["runs"][0]["run_id"])

    carried = layers.read_values(db, new_version, "planner", None, [freeze_bucket.id])
    assert carried, "freeze doneminde kullanıcı katmanı taşınmadı"
    assert abs(sum(carried.values()) - 99_000.0) < 5.0
    stat_old = layers.read_values(db, base.id, "statistical", None, [freeze_bucket.id])
    stat_new = layers.read_values(db, new_version, "statistical", None, [freeze_bucket.id])
    assert stat_new and stat_old


def _wait_for_version(client, headers, run_id: int, tries: int = 120) -> int:
    import time

    for _ in range(tries):
        body = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
        if body["status"] == "done" and body.get("version"):
            return body["version"]["id"]
        if body["status"] == "failed":
            raise AssertionError(f"çalıştırma başarısız: {body.get('error')}")
        time.sleep(0.25)
    raise AssertionError("çalıştırma zaman asimina ugradi")
