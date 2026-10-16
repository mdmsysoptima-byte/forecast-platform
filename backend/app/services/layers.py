"""Layers and consensus (section 9).

No layer ever writes over another. The consensus layer is derived from a
configurable rule and the chosen source is recorded per cell.
"""
from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domain.models import Layer, LayerValue, SectorConfig, Version

log = get_logger(__name__)

Values = dict[tuple[str, int], float]

# code, name, editable, edit roles, order, provenance
LAYER_DEFS = [
    ("actual", "Gerçekleşen", False, [], 0, "actual"),
    ("last_year", "Geçen yıl", False, [], 1, "actual"),
    ("statistical", "İstatistiksel", False, [], 2, "model"),
    ("sales", "Satış", True, ["sales_contributor", "planner", "admin"], 3, "human"),
    ("marketing", "Pazarlama", True, ["marketing_contributor", "planner", "admin"], 4, "human"),
    ("planner", "Planlamacı", True, ["planner", "admin"], 5, "human"),
    ("consensus", "Konsensüs", True, ["planner", "finance", "admin"], 6, "human"),
    ("budget", "Bütçe", True, ["finance", "admin"], 7, "budget"),
    ("prior_version", "Önceki versiyon", False, [], 8, "model"),
]

CONSENSUS_RULES = {
    "planner_then_sales_then_statistical": ["planner", "sales", "statistical"],
    "planner_then_marketing_then_sales_then_statistical": ["planner", "marketing", "sales", "statistical"],
    "sales_then_statistical": ["sales", "statistical"],
}

DEFAULT_WEIGHTS = {"statistical": 0.5, "sales": 0.3, "marketing": 0.2}


def ensure_layers(db: Session) -> dict[str, Layer]:
    existing = {l.code: l for l in db.execute(select(Layer)).scalars()}
    for code, name, editable, roles, order, prov in LAYER_DEFS:
        row = existing.get(code)
        if row is None:
            row = Layer(code=code)
            db.add(row)
            existing[code] = row
        row.name = name
        row.is_editable = editable
        row.edit_roles = roles
        row.order_index = order
        row.provenance = prov
    db.flush()
    return existing


def layer_map(db: Session) -> dict[str, Layer]:
    return {l.code: l for l in db.execute(select(Layer).order_by(Layer.order_index)).scalars()}


def consensus_config(db: Session) -> dict:
    cfg = db.execute(select(SectorConfig)).scalars().first()
    payload = (cfg.payload if cfg else {}) or {}
    layers_cfg = payload.get("layers") or {}
    rule = layers_cfg.get("consensus_rule", "planner_then_sales_then_statistical")
    return {
        "rule": rule,
        "priority": CONSENSUS_RULES.get(rule, CONSENSUS_RULES["planner_then_sales_then_statistical"]),
        "weights": layers_cfg.get("weights") or DEFAULT_WEIGHTS,
        "mode": "weighted" if rule == "weighted_average" else "priority",
    }


def read_values(
    db: Session,
    version_id: int,
    layer_code: str,
    series_keys: set[str] | None = None,
    bucket_ids: list[int] | None = None,
) -> Values:
    lm = layer_map(db)
    layer = lm.get(layer_code)
    if layer is None:
        return {}
    q = select(LayerValue.series_key, LayerValue.bucket_id, LayerValue.value).where(
        LayerValue.version_id == version_id, LayerValue.layer_id == layer.id
    )
    if bucket_ids:
        q = q.where(LayerValue.bucket_id.in_(bucket_ids))
    rows = db.execute(q).all()
    if series_keys is None:
        return {(sk, b): v for sk, b, v in rows}
    return {(sk, b): v for sk, b, v in rows if sk in series_keys}


def write_values(
    db: Session,
    version_id: int,
    layer_code: str,
    values: Values,
    source: str = "model",
    rule_id: int | None = None,
    user_id: int | None = None,
    replace: bool = False,
) -> int:
    lm = layer_map(db)
    layer = lm[layer_code]
    if replace:
        db.execute(
            delete(LayerValue).where(LayerValue.version_id == version_id, LayerValue.layer_id == layer.id)
        )
        db.flush()
        db.add_all(
            [
                LayerValue(
                    version_id=version_id, layer_id=layer.id, series_key=sk, bucket_id=b,
                    value=float(v), source=source, rule_id=rule_id, updated_by=user_id,
                )
                for (sk, b), v in values.items()
            ]
        )
        db.flush()
        return len(values)

    existing = {
        (sk, b): row_id
        for row_id, sk, b in db.execute(
            select(LayerValue.id, LayerValue.series_key, LayerValue.bucket_id).where(
                LayerValue.version_id == version_id, LayerValue.layer_id == layer.id
            )
        ).all()
    }
    updates, inserts = [], []
    for (sk, b), v in values.items():
        if (sk, b) in existing:
            updates.append({"id": existing[(sk, b)], "value": float(v), "source": source,
                            "rule_id": rule_id, "updated_by": user_id})
        else:
            inserts.append(
                LayerValue(version_id=version_id, layer_id=layer.id, series_key=sk, bucket_id=b,
                           value=float(v), source=source, rule_id=rule_id, updated_by=user_id)
            )
    if updates:
        db.execute(update(LayerValue), updates)
    if inserts:
        db.add_all(inserts)
    db.flush()
    return len(values)


def compute_consensus(
    layer_values: dict[str, Values], config: dict, cells: set[tuple[str, int]] | None = None
) -> tuple[Values, dict[tuple[str, int], str]]:
    """Return consensus values and which layer supplied each cell."""
    keys: set[tuple[str, int]] = set()
    for code in layer_values:
        keys |= set(layer_values[code].keys())
    if cells is not None:
        keys &= cells

    out: Values = {}
    chosen: dict[tuple[str, int], str] = {}

    if config.get("mode") == "weighted":
        weights = config["weights"]
        for key in keys:
            num = den = 0.0
            parts = []
            for code, w in weights.items():
                v = layer_values.get(code, {}).get(key)
                if v is None:
                    continue
                num += float(w) * float(v)
                den += float(w)
                parts.append(code)
            if den > 0:
                out[key] = num / den
                chosen[key] = "+".join(parts)
        return out, chosen

    for key in keys:
        for code in config["priority"]:
            v = layer_values.get(code, {}).get(key)
            if v is not None:
                out[key] = float(v)
                chosen[key] = code
                break
    return out, chosen


def recompute_consensus(db: Session, version_id: int, scope_keys: set[str] | None = None) -> dict:
    """Refresh the consensus layer of a version from its contributing layers."""
    version = db.get(Version, version_id)
    if version is None:
        return {"cells": 0}
    if version.status in ("approved", "locked"):
        return {"cells": 0, "skipped": "versiyon kilitli"}

    cfg = consensus_config(db)
    needed = set(cfg["priority"]) | set(cfg["weights"].keys())
    lv = {code: read_values(db, version_id, code, series_keys=scope_keys) for code in needed}
    values, chosen = compute_consensus(lv, cfg)
    write_values(db, version_id, "consensus", values, source="allocation", replace=scope_keys is None)
    counts: dict[str, int] = {}
    for code in chosen.values():
        counts[code] = counts.get(code, 0) + 1
    log.info("konsensüs yeniden hesaplandı", extra={"version_id": version_id})
    return {"cells": len(values), "sources": counts, "rule": cfg["rule"]}


def consensus_sources(db: Session, version_id: int, series_keys: set[str] | None = None) -> dict:
    cfg = consensus_config(db)
    needed = set(cfg["priority"]) | set(cfg["weights"].keys())
    lv = {code: read_values(db, version_id, code, series_keys=series_keys) for code in needed}
    _, chosen = compute_consensus(lv, cfg)
    return chosen
