"""Run engine (section 7).

A run never touches a saved version or a user layer. It produces a new draft.
Concurrent runs for the same scenario and scope are queued behind one lock.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_store
from app.core.config import settings
from app.core.db import session_scope
from app.core.logging import get_logger, new_correlation_id
from app.domain.models import (
    Cycle,
    Driver,
    DriverSetValue,
    DriverValue,
    ModelRegistry,
    Run,
    Scenario,
    ScenarioRule,
    Series,
    TimeBucket,
    Version,
)
from app.forecast_models.contract import MissingDriverError, PredictRequest
from app.forecast_models.registry import load_model, nearest_supported_level
from app.services import alerts, audit, calendar_svc, cycle_svc, layers
from app.services.hierarchy import group_map, resolve_scope
from app.services.reconciliation import AllocationKey, allocate
from app.services.scenario_rules import ResolvedRule, apply_rules

log = get_logger(__name__)

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

CARRY_LAYERS = ["sales", "marketing", "planner", "budget", "last_year"]


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


class RunError(RuntimeError):
    pass


def execute_run(run_id: int) -> None:
    """Entry point for the background worker."""
    cid = new_correlation_id()
    with session_scope() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        run.correlation_id = cid
        key = f"{run.scenario_id}:{run.scope_hash}"
    lock = _lock_for(key)
    acquired = lock.acquire(timeout=900)
    if not acquired:  # pragma: no cover
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.status = "failed"
            run.error = "Ayni senaryo ve kapsam için suren çalıştırma zaman asimina ugradi"
        return
    try:
        _run_inner(run_id, cid)
    finally:
        lock.release()


def _run_inner(run_id: int, cid: str) -> None:
    t0 = time.perf_counter()
    with session_scope() as db:
        run = db.get(Run, run_id)
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.flush()
    try:
        with session_scope() as db:
            result = _produce(db, run_id, cid)
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.status = "done"
            run.finished_at = datetime.now(timezone.utc)
            run.duration_ms = int((time.perf_counter() - t0) * 1000)
            run.series_count = result["series_count"]
            run.diagnostics = result["diagnostics"]
            log.info(
                "çalıştırma tamamlandi",
                extra={"run_id": run_id, "duration_ms": run.duration_ms, "version_id": result["version_id"]},
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("çalıştırma başarısız", extra={"run_id": run_id})
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.duration_ms = int((time.perf_counter() - t0) * 1000)
            run.error = str(exc)


def _produce(db: Session, run_id: int, cid: str) -> dict:
    store = get_store()
    run = db.get(Run, run_id)
    scenario = db.get(Scenario, run.scenario_id)
    entry = db.get(ModelRegistry, run.model_registry_id)
    if scenario is None or entry is None:
        raise RunError("Senaryo veya model kaydı bulunamadi")

    cycle = db.get(Cycle, scenario.cycle_id) if scenario.cycle_id else cycle_svc.active_cycle(db)
    if cycle is None:
        raise RunError("Aktif çevrim yok")

    horizon = scenario.horizon_periods or cycle.horizon_periods
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    future = calendar_svc.bucket_window(
        db, cycle.calendar_id, scenario.grain or cycle.grain, current.seq, current.seq + horizon - 1
    )
    history_buckets = calendar_svc.bucket_window(
        db, cycle.calendar_id, scenario.grain or cycle.grain, current.seq - 48, current.seq - 1
    )
    future_ids = [b.id for b in future]
    hist_ids = [b.id for b in history_buckets]

    scope_keys = resolve_scope(db, scenario.scope or {})
    if not scope_keys:
        raise RunError("Kapsamda seri bulunamadi")

    model = load_model(entry)
    level, level_notes = nearest_supported_level(entry, scenario.level or {})

    # History from the analytic store.
    rows = store.read_series(sorted(scope_keys), min(hist_ids), max(hist_ids))
    if not rows:
        raise RunError("Kapsam için geçmiş veri bulunamadi")
    hist = pd.DataFrame(rows)

    # Does the model run at the base level or at a coarser level.
    base_level = _base_level(db)
    run_at_group = level != base_level and level
    gmap: dict[str, str] = {}
    if run_at_group:
        gmap = group_map(db, level, scope_keys)
        hist = hist.assign(series_key=hist["series_key"].map(gmap)).dropna(subset=["series_key"])
        hist = hist.groupby(["series_key", "bucket_id"], as_index=False)["value"].sum()

    drivers_df, driver_notes = _build_driver_frame(db, scenario, entry, hist_ids, future_ids)
    series_meta = _series_meta(db, scope_keys) if not run_at_group else pd.DataFrame(
        columns=["series_key", "dim_members", "attributes"]
    )

    req = PredictRequest(
        history=hist,
        drivers=drivers_df,
        series_meta=series_meta,
        future_buckets=future_ids,
        horizon=horizon,
        grain=scenario.grain or cycle.grain,
        level=level,
        params=scenario.model_params or {},
    )
    try:
        resp = model.predict(req)
    except MissingDriverError as exc:
        raise RunError(str(exc)) from exc

    scores = resp.scores
    if scores.empty:
        raise RunError("Model boş sonuç dondu")
    values = {(r.series_key, int(r.bucket_id)): float(r.value) for r in scores.itertuples()}
    intervals = {
        (r.series_key, int(r.bucket_id)): (
            float(getattr(r, "lower", 0.0) or 0.0), float(getattr(r, "upper", 0.0) or 0.0)
        )
        for r in scores.itertuples()
    } if "lower" in scores.columns else {}

    # Reconcile the model output down to the base level when needed.
    if run_at_group:
        outcome = allocate(db, values, gmap, AllocationKey(method="history_share", decimals=0))
        values = outcome.values
        intervals = {}
        level_notes.extend(outcome.warnings)

    # Scenario output rules.
    resolved = _resolve_rules(db, scenario, scope_keys, future)
    rule_out = apply_rules(values, resolved, future_ids)
    values = rule_out.values

    # Persist raw scores and rule effects in the store.
    store.write_scores(
        run_id,
        [
            {
                "series_key": sk, "bucket_id": bid, "value": v,
                "lower": intervals.get((sk, bid), (None, None))[0],
                "upper": intervals.get((sk, bid), (None, None))[1],
            }
            for (sk, bid), v in values.items()
        ],
    )
    store.write_rule_effects(run_id, rule_out.effects)

    # Draft version.
    base_version = db.get(Version, scenario.base_version_id) if scenario.base_version_id else None
    version = Version(
        name=f"{scenario.name} taslak",
        kind="draft",
        status="draft",
        run_id=run_id,
        scenario_id=scenario.id,
        cycle_id=cycle.id,
        parent_version_id=base_version.id if base_version else None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.draft_ttl_days),
        provenance={
            "scenario": scenario.name,
            "scenario_id": scenario.id,
            "driver_set_id": scenario.driver_set_id,
            "model": f"{entry.code} {entry.version}",
            "model_registry_id": entry.id,
            "rules": [
                {"id": r.id, "type": r.type, "name": r.name, "order": r.order_index} for r in resolved
            ],
            "level": level,
            "grain": scenario.grain or cycle.grain,
            "data_as_of": store.data_as_of(),
            "correlation_id": cid,
            "notes": level_notes + driver_notes,
        },
    )
    db.add(version)
    db.flush()

    freeze_flags = cycle_svc.period_flags(db, cycle)
    layers.write_values(db, version.id, "statistical", values, source="model", replace=True)

    # Carry user layers over from the base version. Freeze protects them.
    carried = 0
    if base_version is not None:
        for code in CARRY_LAYERS:
            prior = layers.read_values(db, base_version.id, code)
            keep = {k: v for k, v in prior.items() if k[1] in set(future_ids)}
            if keep:
                layers.write_values(db, version.id, code, keep, source="allocation", replace=True)
                carried += len(keep)
        prior_consensus = layers.read_values(db, base_version.id, "consensus")
        if prior_consensus:
            layers.write_values(db, version.id, "prior_version", prior_consensus, source="model", replace=True)

    _write_actual_and_last_year(db, version.id, cycle, scope_keys, future)
    _load_budget_if_missing(db, version.id, base_version)
    layers.recompute_consensus(db, version.id)
    alerts.generate_for_version(db, version.id)

    run.data_as_of = _parse_date(store.data_as_of())
    audit.record(db, "run", run_id, "complete", run.started_by, after={"version_id": version.id})

    diagnostics = dict(resp.diagnostics or {})
    diagnostics.update(
        {
            "rules_applied": len(resolved),
            "rule_effect_rows": len(rule_out.effects),
            "rule_warnings": rule_out.warnings[:10],
            "level_notes": level_notes,
            "driver_notes": driver_notes,
            "carried_layer_cells": carried,
            "frozen_periods": sum(1 for b in future_ids if freeze_flags.get(b) == "freeze"),
        }
    )
    return {
        "version_id": version.id,
        "series_count": len({k for k, _ in values}),
        "diagnostics": diagnostics,
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _base_level(db: Session) -> dict[str, str]:
    from app.domain.models import Dimension, Hierarchy

    out: dict[str, str] = {}
    for dim in db.execute(select(Dimension).order_by(Dimension.position)).scalars():
        hier = db.execute(
            select(Hierarchy).where(Hierarchy.dimension_id == dim.id).order_by(Hierarchy.is_default.desc())
        ).scalars().first()
        if hier and hier.levels:
            out[dim.code] = hier.levels[-1]
    return out


def _build_driver_frame(
    db: Session, scenario: Scenario, entry: ModelRegistry, hist_ids: list[int], future_ids: list[int]
) -> tuple[pd.DataFrame, list[str]]:
    codes = list(entry.required_drivers or []) + list(entry.optional_drivers or [])
    notes: list[str] = []
    if not codes:
        return pd.DataFrame(columns=["driver_code", "bucket_id", "member_id", "value"]), notes

    drivers = {
        d.code: d for d in db.execute(select(Driver).where(Driver.code.in_(codes))).scalars()
    }
    unknown = [c for c in codes if c not in drivers]
    if unknown and set(unknown) & set(entry.required_drivers or []):
        raise RunError(f"Sürücü katalogunda tanımlı olmayan zorunlu sürücü: {', '.join(unknown)}")

    rows: list[dict] = []
    for code, d in drivers.items():
        for bid, member_id, value in db.execute(
            select(DriverValue.bucket_id, DriverValue.member_id, DriverValue.value).where(
                DriverValue.driver_id == d.id, DriverValue.bucket_id.in_(hist_ids)
            )
        ).all():
            rows.append({"driver_code": code, "bucket_id": bid, "member_id": member_id, "value": value})

    future_have: dict[str, set[int]] = {}
    if scenario.driver_set_id:
        for code, d in drivers.items():
            for bid, member_id, value in db.execute(
                select(DriverSetValue.bucket_id, DriverSetValue.member_id, DriverSetValue.value).where(
                    DriverSetValue.driver_set_id == scenario.driver_set_id,
                    DriverSetValue.driver_id == d.id,
                    DriverSetValue.bucket_id.in_(future_ids),
                )
            ).all():
                rows.append({"driver_code": code, "bucket_id": bid, "member_id": member_id, "value": value})
                future_have.setdefault(code, set()).add(bid)

    # The platform refuses to run when a required driver has no future value.
    missing: list[str] = []
    for code in entry.required_drivers or []:
        covered = future_have.get(code, set())
        gaps = [b for b in future_ids if b not in covered]
        if gaps:
            labels = [
                lbl for (lbl,) in db.execute(
                    select(TimeBucket.label).where(TimeBucket.id.in_(gaps[:6]))
                ).all()
            ]
            name = drivers[code].name if code in drivers else code
            missing.append(f"{name} ({code}): {len(gaps)} dönem eksik, örnek {', '.join(labels)}")
    if missing:
        raise RunError(
            "Zorunlu sürücü değeri eksik oldugu için çalıştırma reddedildi. " + " | ".join(missing)
        )
    for code in entry.optional_drivers or []:
        if code not in future_have:
            notes.append(f"Opsiyonel sürücü {code} için gelecek değeri yok, etkisi sıfır alındı")
    return pd.DataFrame(rows), notes


def _series_meta(db: Session, keys: set[str]) -> pd.DataFrame:
    rows = db.execute(
        select(Series.series_key, Series.dim_members, Series.attributes).where(Series.series_key.in_(sorted(keys)))
    ).all()
    return pd.DataFrame(
        [{"series_key": sk, "dim_members": dm, "attributes": at} for sk, dm, at in rows]
    )


def _resolve_rules(db: Session, scenario: Scenario, scope_keys: set[str], future: list[TimeBucket]) -> list[ResolvedRule]:
    raw = db.execute(
        select(ScenarioRule)
        .where(ScenarioRule.scenario_id == scenario.id, ScenarioRule.enabled.is_(True))
        .order_by(ScenarioRule.order_index)
    ).scalars().all()
    by_label = {b.label: b for b in future}
    resolved: list[ResolvedRule] = []
    for r in raw:
        series = resolve_scope(db, r.target_scope or {}) & scope_keys if r.target_scope else set(scope_keys)
        if r.bucket_from or r.bucket_to:
            lo = by_label.get(r.bucket_from or future[0].label, future[0])
            hi = by_label.get(r.bucket_to or future[-1].label, future[-1])
            buckets = [b.id for b in future if lo.seq <= b.seq <= hi.seq]
        else:
            buckets = [b.id for b in future]
        params = dict(r.params or {})
        for pkey, bkey in (("source_bucket", "source_bucket_id"), ("target_bucket", "target_bucket_id")):
            if params.get(pkey) in by_label:
                params[bkey] = by_label[params[pkey]].id
        resolved.append(
            ResolvedRule(
                id=r.id, type=r.type, name=r.name or r.type, order_index=r.order_index,
                series=series, buckets=buckets, params=params,
            )
        )
    return resolved


def _write_actual_and_last_year(
    db: Session, version_id: int, cycle: Cycle, scope_keys: set[str], future: list[TimeBucket]
) -> None:
    """actual for the past, last_year for the forecast window."""
    store = get_store()
    hist = cycle_svc.history_window(db, cycle, periods=24)
    if hist:
        rows = store.read_series(sorted(scope_keys), hist[0].id, hist[-1].id)
        layers.write_values(
            db, version_id, "actual",
            {(r["series_key"], r["bucket_id"]): float(r["value"]) for r in rows},
            source="model", replace=True,
        )
    ly: dict[tuple[str, int], float] = {}
    for b in future:
        prior = calendar_svc.holiday_aligned_prior_year(db, cycle.calendar_id, b)
        if prior is None:
            continue
        for r in store.read_series(sorted(scope_keys), prior.id, prior.id):
            ly[(r["series_key"], b.id)] = float(r["value"])
    if ly:
        layers.write_values(db, version_id, "last_year", ly, source="model", replace=True)


def _load_budget_if_missing(db: Session, version_id: int, base_version: Version | None) -> None:
    if layers.read_values(db, version_id, "budget"):
        return
    source = db.execute(
        select(Version).where(Version.kind.in_(["baseline", "consensus", "snapshot"])).order_by(Version.id)
    ).scalars().first()
    if source is None:
        return
    vals = layers.read_values(db, source.id, "budget")
    if vals:
        layers.write_values(db, version_id, "budget", vals, source="allocation", replace=True)


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def purge_expired_drafts(db: Session) -> int:
    """Drafts disappear after DRAFT_TTL_DAYS."""
    from sqlalchemy import delete

    from app.domain.models import LayerValue

    now = datetime.now(timezone.utc)
    stale = db.execute(
        select(Version).where(
            Version.kind == "draft", Version.expires_at.isnot(None), Version.expires_at < now
        )
    ).scalars().all()
    for v in stale:
        db.execute(delete(LayerValue).where(LayerValue.version_id == v.id))
        db.delete(v)
    if stale:
        db.flush()
    return len(stale)
