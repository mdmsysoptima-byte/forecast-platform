"""Sector configuration loader (section 13).

Everything sector specific arrives from YAML. No sector name appears in code.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domain.models import (
    Calendar,
    Cycle,
    Dimension,
    Driver,
    Hierarchy,
    HierarchyNode,
    Measure,
    Member,
    ReasonCode,
    SectorConfig,
    SeriesNode,
    TimeBucket,
    UnitConversion,
)
from app.services import calendar_svc, layers
from app.services.hierarchy import ValidationReport, validate_tree

log = get_logger(__name__)


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_sector(
    db: Session, path: Path, first_year: int, last_year: int, current_period: str
) -> tuple[SectorConfig, Cycle]:
    cfg = read_yaml(path)
    sector = cfg.get("sector", "demo")

    row = db.execute(select(SectorConfig).where(SectorConfig.sector == sector)).scalar_one_or_none()
    if row is None:
        row = SectorConfig(sector=sector)
        db.add(row)
    row.name = cfg.get("name", sector)
    row.payload = cfg
    db.flush()

    # Calendar, buckets and events
    cal_cfg = cfg.get("calendar") or {}
    cal = db.execute(select(Calendar).where(Calendar.code == f"{sector}_main")).scalar_one_or_none()
    if cal is None:
        cal = Calendar(code=f"{sector}_main")
        db.add(cal)
    cal.name = "Ana takvim"
    cal.type = cal_cfg.get("type", "gregorian")
    cal.fiscal_start_month = int(cal_cfg.get("fiscal_start_month", 1))
    cal.week_start_day = 1 if cal_cfg.get("week_start", "monday") == "monday" else 7
    db.flush()

    calendar_svc.build_buckets(db, cal, first_year, last_year)
    calendar_svc.load_calendar_events(db, cal, first_year, last_year)

    # Dimensions and hierarchies
    for pos, d in enumerate(cfg.get("dimensions") or []):
        dim = db.execute(select(Dimension).where(Dimension.code == d["code"])).scalar_one_or_none()
        if dim is None:
            dim = Dimension(code=d["code"])
            db.add(dim)
        dim.name = d.get("name", d["code"])
        dim.type = d.get("type", "custom")
        dim.position = pos
        db.flush()
        for h in d.get("hierarchies") or []:
            hier = db.execute(
                select(Hierarchy).where(Hierarchy.dimension_id == dim.id, Hierarchy.code == h["code"])
            ).scalar_one_or_none()
            if hier is None:
                hier = Hierarchy(dimension_id=dim.id, code=h["code"])
                db.add(hier)
            hier.name = h.get("name", h["code"])
            hier.is_default = bool(h.get("is_default", False))
            hier.levels = list(h.get("levels") or [])
            db.flush()

    # Measures and unit conversions
    for m in cfg.get("measures") or []:
        row_m = db.execute(select(Measure).where(Measure.code == m["code"])).scalar_one_or_none()
        if row_m is None:
            row_m = Measure(code=m["code"])
            db.add(row_m)
        row_m.name = m.get("name", m["code"])
        row_m.unit_type = m.get("unit_type", "quantity")
        row_m.base_unit = m.get("base_unit", "")
        row_m.formula = m.get("formula")
    for u in cfg.get("unit_conversions") or []:
        exists = db.execute(
            select(UnitConversion).where(
                UnitConversion.from_unit == u["from_unit"], UnitConversion.to_unit == u["to_unit"]
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(UnitConversion(from_unit=u["from_unit"], to_unit=u["to_unit"], factor=float(u["factor"])))

    # Drivers
    for d in cfg.get("drivers") or []:
        drv = db.execute(select(Driver).where(Driver.code == d["code"])).scalar_one_or_none()
        if drv is None:
            drv = Driver(code=d["code"])
            db.add(drv)
        drv.name = d.get("name", d["code"])
        drv.unit = d.get("unit", "")
        drv.source_type = d.get("source_type", "manual")
        drv.source_ref = d.get("source_ref")
        drv.grain = d.get("grain", "month")
        scope = d.get("scope", "global")
        if scope and scope != "global":
            dim = db.execute(select(Dimension).where(Dimension.code == scope)).scalar_one_or_none()
            drv.scope_dimension_id = dim.id if dim else None
        else:
            drv.scope_dimension_id = None

    # Reason codes
    for r in cfg.get("reason_codes") or []:
        rc = db.execute(select(ReasonCode).where(ReasonCode.code == r["code"])).scalar_one_or_none()
        if rc is None:
            rc = ReasonCode(code=r["code"])
            db.add(rc)
        rc.name = r.get("name", r["code"])
        rc.requires_note = bool(r.get("requires_note", False))
        rc.sector_tag = sector

    layers.ensure_layers(db)
    db.flush()

    # Cycle
    cyc_cfg = cfg.get("cycle") or {}
    current = db.execute(
        select(TimeBucket).where(
            TimeBucket.calendar_id == cal.id,
            TimeBucket.grain == cyc_cfg.get("grain", "month"),
            TimeBucket.label == current_period,
        )
    ).scalar_one_or_none()
    if current is None:
        raise ValueError(f"Current period kovasi bulunamadi: {current_period}")

    cycle = db.execute(select(Cycle).order_by(Cycle.id.desc())).scalars().first()
    if cycle is None:
        cycle = Cycle(name=f"{current_period} çevrimi", calendar_id=cal.id)
        db.add(cycle)
    cycle.calendar_id = cal.id
    cycle.grain = cyc_cfg.get("grain", "month")
    cycle.horizon_periods = int(cyc_cfg.get("horizon_periods", 18))
    cycle.fence_periods = int(cyc_cfg.get("fence_periods", 1))
    cycle.freeze_periods = int(cyc_cfg.get("freeze_periods", 2))
    cycle.current_period_bucket_id = current.id
    cycle.status = "open"
    db.flush()

    log.info("sektör konfigürasyonu yüklendi", extra={"entity": "sector_config"})
    return row, cycle


def load_hierarchy_rows(
    db: Session, dimension_code: str, hierarchy_code: str, rows: list[dict]
) -> ValidationReport:
    """Build members and tree nodes from level columns.

    rows = [{"category": ("C1", "Kategori 1"), "subcategory": (...), "sku": (...)}, ...]
    Each value is a (code, name) pair.
    """
    dim = db.execute(select(Dimension).where(Dimension.code == dimension_code)).scalar_one()
    hier = db.execute(
        select(Hierarchy).where(Hierarchy.dimension_id == dim.id, Hierarchy.code == hierarchy_code)
    ).scalar_one()
    levels = list(hier.levels)

    members: dict[str, Member] = {
        m.code: m for m in db.execute(select(Member).where(Member.dimension_id == dim.id)).scalars()
    }
    nodes: dict[tuple[int, str], HierarchyNode] = {}

    for row in rows:
        parent: HierarchyNode | None = None
        for li, level in enumerate(levels):
            cell = row[level]
            # A level is (code, name) or (code, name, attributes).
            code, name = cell[0], cell[1]
            extra = cell[2] if len(cell) > 2 else {}
            member = members.get(code)
            if member is None:
                member = Member(
                    dimension_id=dim.id, code=code, name=name,
                    attributes={"level": level, **(extra or {})},
                )
                db.add(member)
                db.flush()
                members[code] = member
            elif extra:
                member.attributes = {**(member.attributes or {}), **extra}
            key = (li, code)
            node = nodes.get(key)
            if node is None:
                node = HierarchyNode(
                    hierarchy_id=hier.id, member_id=member.id,
                    parent_node_id=parent.id if parent else None,
                    level=level, level_index=li, path="", is_leaf=(li == len(levels) - 1),
                )
                db.add(node)
                db.flush()
                node.path = f"{parent.path}{node.id}." if parent else f"{node.id}."
                nodes[key] = node
            parent = node
    db.flush()
    return validate_tree(db, hier)


def rebuild_series_nodes(db: Session) -> int:
    """Materialise the series to node map for every hierarchy."""
    from sqlalchemy import delete

    from app.domain.models import Series

    db.execute(delete(SeriesNode))
    db.flush()
    hierarchies = list(db.execute(select(Hierarchy)).scalars())
    dims = {d.id: d for d in db.execute(select(Dimension)).scalars()}
    member_by_code: dict[int, dict[str, int]] = {}
    for m in db.execute(select(Member)).scalars():
        member_by_code.setdefault(m.dimension_id, {})[m.code] = m.id

    nodes_by_member: dict[int, dict[int, HierarchyNode]] = {}
    for n in db.execute(select(HierarchyNode)).scalars():
        nodes_by_member.setdefault(n.hierarchy_id, {})[n.member_id] = n

    rows: list[SeriesNode] = []
    for s in db.execute(select(Series)).scalars():
        for hier in hierarchies:
            dim = dims[hier.dimension_id]
            code = (s.dim_codes or {}).get(dim.code)
            if code is None:
                continue
            member_id = member_by_code.get(dim.id, {}).get(code)
            leaf = nodes_by_member.get(hier.id, {}).get(member_id) if member_id else None
            if leaf is None:
                continue
            # Walk up and register every ancestor including the leaf.
            node = leaf
            while node is not None:
                rows.append(
                    SeriesNode(
                        series_key=s.series_key, hierarchy_id=hier.id,
                        node_id=node.id, level_index=node.level_index,
                    )
                )
                node = db.get(HierarchyNode, node.parent_node_id) if node.parent_node_id else None
    db.add_all(rows)
    db.flush()
    return len(rows)
