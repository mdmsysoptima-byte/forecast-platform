"""Override, bulk edit, layer copy and insight (sections 9 and 10)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.logging import get_logger
from app.domain.models import Cycle, Insight, Layer, Override, ReasonCode, TimeBucket, Version
from app.services import audit, cycle_svc, layers
from app.services.reconciliation import AllocationKey, allocate

log = get_logger(__name__)


class OverrideError(RuntimeError):
    pass


@dataclass
class OverrideRequest:
    version_id: int
    layer_code: str
    bucket_id: int
    mode: str  # absolute, pct, delta
    value: float
    reason_code_id: int
    note: str = ""
    series_key: str | None = None
    scope: dict | None = None
    level: dict[str, str] | None = None
    allocation_method: str = "layer_ratio"
    valid_until_bucket_id: int | None = None
    attachment_ref: str | None = None


def _check_layer_permission(db: Session, principal: Principal, layer: Layer) -> None:
    if not layer.is_editable:
        raise OverrideError(f"{layer.name} katmanı düzenlenemez")
    if principal.is_admin:
        return
    if layer.edit_roles and not any(r in principal.roles for r in layer.edit_roles):
        raise OverrideError(f"{layer.name} katmaninda düzenleme yetkiniz yok")


def _editable_series(db: Session, principal: Principal, keys: set[str]) -> set[str]:
    if principal.is_admin or not principal.edit_nodes:
        return keys
    from app.domain.models import SeriesNode

    allowed = keys
    for hier_id, nodes in principal.edit_nodes.items():
        scoped = {
            k for (k,) in db.execute(
                select(SeriesNode.series_key).where(
                    SeriesNode.hierarchy_id == hier_id, SeriesNode.node_id.in_(nodes or {-1})
                )
            )
        }
        allowed = allowed & scoped
    return allowed


def apply_override(db: Session, principal: Principal, req: OverrideRequest) -> dict:
    version = db.get(Version, req.version_id)
    if version is None:
        raise OverrideError("Versiyon bulunamadi")
    if version.status in ("approved", "locked"):
        raise OverrideError("Onaylı veya kilitli versiyon değiştirilemez")

    cycle = db.get(Cycle, version.cycle_id) if version.cycle_id else cycle_svc.active_cycle(db)
    if cycle is not None:
        ok, why = cycle_svc.is_editable_bucket(db, cycle, req.bucket_id)
        if not ok:
            raise OverrideError(why)

    lm = layers.layer_map(db)
    layer = lm.get(req.layer_code)
    if layer is None:
        raise OverrideError("Katman bulunamadi")
    _check_layer_permission(db, principal, layer)

    reason = db.get(ReasonCode, req.reason_code_id)
    if reason is None:
        raise OverrideError("Gerekçe kodu zorunludur")
    if reason.requires_note and not (req.note or "").strip():
        raise OverrideError(f"{reason.name} gerekçesi için not zorunlu")

    # Resolve the target series set.
    if req.series_key:
        target_keys = {req.series_key}
        entry_level = "base"
    else:
        from app.services.hierarchy import resolve_scope

        target_keys = resolve_scope(db, req.scope or {}, None)
        entry_level = "|".join(f"{k}={v}" for k, v in sorted((req.level or {}).items())) or "total"
    target_keys = _editable_series(db, principal, target_keys)
    if not target_keys:
        raise OverrideError("Düzenleyebileceğiniz seri bulunamadi")

    current = layers.read_values(db, req.version_id, req.layer_code, target_keys, [req.bucket_id])
    fallback_code = layers.consensus_config(db)["priority"][-1]
    if not current and req.layer_code != fallback_code:
        # An empty contributor cell means the statistical value is accepted.
        current = layers.read_values(db, req.version_id, fallback_code, target_keys, [req.bucket_id])
    old_total = float(sum(current.values()))

    if req.mode == "absolute":
        new_total = float(req.value)
    elif req.mode == "pct":
        new_total = old_total * (1.0 + float(req.value) / 100.0)
    elif req.mode == "delta":
        new_total = old_total + float(req.value)
    else:
        raise OverrideError("Geçersiz mod")

    # Distribute the new total down to the base series.
    if len(target_keys) == 1:
        sk = next(iter(target_keys))
        new_values = {(sk, req.bucket_id): new_total}
        warnings: list[str] = []
    else:
        group_of = {sk: "entry" for sk in target_keys}
        weights = {sk: current.get((sk, req.bucket_id), 0.0) for sk in target_keys}
        key = AllocationKey(
            method=req.allocation_method if any(weights.values()) else "history_share",
            decimals=0,
        )
        outcome = allocate(db, {("entry", req.bucket_id): new_total}, group_of, key, layer_lookup=weights)
        new_values = outcome.values
        warnings = outcome.warnings

    parent = Override(
        version_id=req.version_id, layer_id=layer.id,
        series_key=req.series_key or "", bucket_id=req.bucket_id,
        old_value=old_total, new_value=new_total, mode=req.mode,
        reason_code_id=req.reason_code_id, note=req.note or "",
        attachment_ref=req.attachment_ref, valid_until_bucket_id=req.valid_until_bucket_id,
        user_id=principal.id, entry_level=entry_level,
        entry_node_label=_scope_label(db, req.scope) if not req.series_key else "",
    )
    db.add(parent)
    db.flush()

    if len(target_keys) > 1:
        for (sk, bid), v in new_values.items():
            db.add(
                Override(
                    version_id=req.version_id, layer_id=layer.id, series_key=sk, bucket_id=bid,
                    old_value=current.get((sk, bid), 0.0), new_value=float(v), mode="absolute",
                    reason_code_id=req.reason_code_id, note=req.note or "", user_id=principal.id,
                    entry_level="base", allocated_from_override_id=parent.id,
                    valid_until_bucket_id=req.valid_until_bucket_id,
                )
            )

    layers.write_values(
        db, req.version_id, req.layer_code, new_values, source="user", user_id=principal.id
    )
    layers.recompute_consensus(db, req.version_id, scope_keys=target_keys)
    audit.record(
        db, "override", parent.id, "create", principal.id, actor_kind=principal.actor_kind,
        before={"value": old_total}, after={"value": new_total, "cells": len(new_values)},
    )
    from app.services.alerts import check_override_alerts

    alerts = check_override_alerts(db, version, req.layer_code, target_keys, req.bucket_id)
    return {
        "override_id": parent.id,
        "old_value": old_total,
        "new_value": new_total,
        "cells": len(new_values),
        "warnings": warnings,
        "alerts": alerts,
    }


def revert(db: Session, principal: Principal, override_id: int) -> dict:
    """Reverse entry. The original record is never deleted."""
    ov = db.get(Override, override_id)
    if ov is None:
        raise OverrideError("Override bulunamadi")
    if ov.reverted:
        raise OverrideError("Bu kayıt zaten geri alındı")
    version = db.get(Version, ov.version_id)
    if version is None or version.status in ("approved", "locked"):
        raise OverrideError("Onaylı veya kilitli versiyon değiştirilemez")

    layer = db.get(Layer, ov.layer_id)
    children = list(
        db.execute(select(Override).where(Override.allocated_from_override_id == ov.id)).scalars()
    )
    restore: dict[tuple[str, int], float] = {}
    if children:
        for c in children:
            restore[(c.series_key, c.bucket_id)] = float(c.old_value or 0.0)
            c.reverted = True
    elif ov.series_key:
        restore[(ov.series_key, ov.bucket_id)] = float(ov.old_value or 0.0)

    layers.write_values(db, ov.version_id, layer.code, restore, source="user", user_id=principal.id)
    ov.reverted = True
    mirror = Override(
        version_id=ov.version_id, layer_id=ov.layer_id, series_key=ov.series_key, bucket_id=ov.bucket_id,
        old_value=ov.new_value, new_value=ov.old_value or 0.0, mode="absolute",
        reason_code_id=ov.reason_code_id, note=f"Geri alma: #{ov.id}", user_id=principal.id,
        entry_level=ov.entry_level, allocated_from_override_id=ov.id,
    )
    db.add(mirror)
    layers.recompute_consensus(db, ov.version_id, scope_keys={k for k, _ in restore})
    audit.record(db, "override", ov.id, "revert", principal.id, actor_kind=principal.actor_kind)
    return {"reverted": ov.id, "cells": len(restore)}


def copy_layer(
    db: Session, principal: Principal, version_id: int, from_code: str, to_code: str, bucket_ids: list[int] | None = None
) -> dict:
    version = db.get(Version, version_id)
    if version is None or version.status in ("approved", "locked"):
        raise OverrideError("Onaylı veya kilitli versiyon değiştirilemez")
    lm = layers.layer_map(db)
    target = lm.get(to_code)
    if target is None:
        raise OverrideError("Hedef katman bulunamadi")
    _check_layer_permission(db, principal, target)
    values = layers.read_values(db, version_id, from_code, None, bucket_ids)
    if not values:
        return {"cells": 0}
    flags = {}
    cycle = cycle_svc.active_cycle(db)
    if cycle:
        flags = cycle_svc.period_flags(db, cycle)
    values = {k: v for k, v in values.items() if flags.get(k[1], "open") not in ("actual", "fence")}
    allowed = _editable_series(db, principal, {k for k, _ in values})
    values = {k: v for k, v in values.items() if k[0] in allowed}
    layers.write_values(db, version_id, to_code, values, source="allocation", user_id=principal.id)
    layers.recompute_consensus(db, version_id)
    audit.record(db, "layer_value", version_id, f"copy:{from_code}->{to_code}", principal.id,
                 after={"cells": len(values)})
    return {"cells": len(values)}


def add_insight(
    db: Session,
    principal: Principal,
    text: str,
    scope: dict | None = None,
    series_key: str | None = None,
    bucket_from_id: int | None = None,
    bucket_to_id: int | None = None,
    reason_code_id: int | None = None,
) -> Insight:
    row = Insight(
        user_id=principal.id, scope=scope or {}, series_key=series_key,
        bucket_from_id=bucket_from_id, bucket_to_id=bucket_to_id,
        text=text, reason_code_id=reason_code_id,
    )
    db.add(row)
    db.flush()
    audit.record(db, "insight", row.id, "create", principal.id, actor_kind=principal.actor_kind,
                 after={"text": text[:200]})
    return row


def cell_history(db: Session, version_id: int, layer_code: str, series_key: str, bucket_id: int) -> list[dict]:
    lm = layers.layer_map(db)
    layer = lm.get(layer_code)
    if layer is None:
        return []
    rows = db.execute(
        select(Override).where(
            Override.version_id == version_id,
            Override.layer_id == layer.id,
            Override.series_key == series_key,
            Override.bucket_id == bucket_id,
        ).order_by(Override.created_at)
    ).scalars().all()
    reasons = {r.id: r for r in db.execute(select(ReasonCode)).scalars()}
    return [
        {
            "id": o.id, "old_value": o.old_value, "new_value": o.new_value, "mode": o.mode,
            "reason": reasons[o.reason_code_id].name if o.reason_code_id in reasons else "",
            "note": o.note, "user_id": o.user_id, "reverted": o.reverted,
            "allocated_from": o.allocated_from_override_id,
            "at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in rows
    ]


def _scope_label(db: Session, scope: dict | None) -> str:
    if not scope:
        return "Tüm kapsam"
    from app.domain.models import HierarchyNode, Member

    parts = []
    for dim, sel in scope.items():
        for nid in (sel or {}).get("nodes", [])[:3]:
            row = db.execute(
                select(Member.name).join(HierarchyNode, HierarchyNode.member_id == Member.id)
                .where(HierarchyNode.id == int(nid))
            ).scalar_one_or_none()
            if row:
                parts.append(row)
    return ", ".join(parts) or "Tüm kapsam"


def expire_overrides(db: Session, current_bucket_seq: int) -> int:
    """Overrides with a validity limit drop off automatically."""
    rows = db.execute(
        select(Override).where(Override.valid_until_bucket_id.isnot(None), Override.reverted.is_(False))
    ).scalars().all()
    dropped = 0
    for o in rows:
        b = db.get(TimeBucket, o.valid_until_bucket_id)
        if b is not None and b.seq < current_bucket_seq:
            o.reverted = True
            dropped += 1
    return dropped
