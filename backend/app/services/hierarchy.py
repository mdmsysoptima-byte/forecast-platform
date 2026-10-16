"""Dimensions, trees, scope resolution and level mapping (section 5)."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Dimension,
    Hierarchy,
    HierarchyNode,
    Member,
    Series,
    SeriesNode,
    UserGroup,
    UserGroupMember,
)


@dataclass
class ValidationReport:
    hierarchy: str
    nodes: int = 0
    leaves: int = 0
    levels: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def default_hierarchy(db: Session, dimension_code: str) -> Hierarchy:
    return db.execute(
        select(Hierarchy)
        .join(Dimension, Dimension.id == Hierarchy.dimension_id)
        .where(Dimension.code == dimension_code)
        .order_by(Hierarchy.is_default.desc(), Hierarchy.id)
    ).scalars().first()


def hierarchy_by_code(db: Session, dimension_code: str, hierarchy_code: str | None) -> Hierarchy:
    if not hierarchy_code:
        return default_hierarchy(db, dimension_code)
    return db.execute(
        select(Hierarchy)
        .join(Dimension, Dimension.id == Hierarchy.dimension_id)
        .where(Dimension.code == dimension_code, Hierarchy.code == hierarchy_code)
    ).scalar_one()


def validate_tree(db: Session, hierarchy: Hierarchy) -> ValidationReport:
    """Each child has exactly one parent and the tree has no cycle."""
    rep = ValidationReport(hierarchy=f"{hierarchy.code}", levels=list(hierarchy.levels))
    nodes = list(
        db.execute(select(HierarchyNode).where(HierarchyNode.hierarchy_id == hierarchy.id)).scalars()
    )
    rep.nodes = len(nodes)
    rep.leaves = sum(1 for n in nodes if n.is_leaf)
    by_id = {n.id: n for n in nodes}
    seen_member_parent: dict[int, int] = {}
    for n in nodes:
        if n.member_id in seen_member_parent and seen_member_parent[n.member_id] != (n.parent_node_id or 0):
            rep.errors.append(f"Üye {n.member_id} birden fazla ebeveyne bağlı")
        seen_member_parent[n.member_id] = n.parent_node_id or 0
        # cycle check
        walker, hops = n.parent_node_id, 0
        while walker is not None and hops <= len(nodes):
            if walker == n.id:
                rep.errors.append(f"Döngü: düğüm {n.id}")
                break
            walker = by_id[walker].parent_node_id if walker in by_id else None
            hops += 1
        if n.parent_node_id is not None and n.parent_node_id not in by_id:
            rep.errors.append(f"Düğüm {n.id} eksik ebeveyne bağlı")
    if rep.nodes == 0:
        rep.errors.append("Hiyerarşi boş")
    return rep


def subtree_ids(db: Session, node_id: int) -> set[int]:
    node = db.get(HierarchyNode, node_id)
    if node is None:
        return set()
    return {
        n for (n,) in db.execute(
            select(HierarchyNode.id).where(
                HierarchyNode.hierarchy_id == node.hierarchy_id,
                HierarchyNode.path.like(f"{node.path}%"),
            )
        )
    }


def level_index(hierarchy: Hierarchy, level_name: str) -> int:
    levels = list(hierarchy.levels)
    if level_name in levels:
        return levels.index(level_name)
    raise ValueError(f"{level_name} seviyesi {hierarchy.code} hiyerarşisinde yok")


def tree(db: Session, hierarchy: Hierarchy, allowed: set[int] | None = None) -> list[dict]:
    rows = list(
        db.execute(
            select(HierarchyNode, Member)
            .join(Member, Member.id == HierarchyNode.member_id)
            .where(HierarchyNode.hierarchy_id == hierarchy.id)
            .order_by(HierarchyNode.level_index, Member.code)
        )
    )
    nodes: dict[int, dict] = {}
    for node, member in rows:
        if allowed is not None and node.id not in allowed:
            continue
        nodes[node.id] = {
            "node_id": node.id,
            "member_code": member.code,
            "label": member.name,
            "level": node.level,
            "level_index": node.level_index,
            "is_leaf": node.is_leaf,
            "parent_node_id": node.parent_node_id,
            "children": [],
        }
    roots = []
    for nid, n in nodes.items():
        parent = nodes.get(n["parent_node_id"]) if n["parent_node_id"] else None
        if parent is not None:
            parent["children"].append(n)
        else:
            roots.append(n)
    return roots


def resolve_scope(db: Session, scope: dict, principal_nodes: dict[int, set[int]] | None = None) -> set[str]:
    """Scope -> set of base level series keys.

    scope = {"product": {"nodes": [id], "groups": [id], "members": ["code"]}, ...}
    An empty scope means every series the user may see.
    """
    all_keys = {k for (k,) in db.execute(select(Series.series_key))}
    selected: set[str] | None = None

    for dim_code, sel in (scope or {}).items():
        if not isinstance(sel, dict):
            continue
        hier = hierarchy_by_code(db, dim_code, sel.get("hierarchy"))
        if hier is None:
            continue
        node_ids: set[int] = set()
        for nid in sel.get("nodes") or []:
            node_ids |= subtree_ids(db, int(nid))
        for gid in sel.get("groups") or []:
            member_ids = {
                m for (m,) in db.execute(
                    select(UserGroupMember.member_id).where(UserGroupMember.group_id == int(gid))
                )
            }
            node_ids |= {
                n for (n,) in db.execute(
                    select(HierarchyNode.id).where(
                        HierarchyNode.hierarchy_id == hier.id, HierarchyNode.member_id.in_(member_ids or {-1})
                    )
                )
            }
        if sel.get("members"):
            member_ids = {
                m for (m,) in db.execute(
                    select(Member.id).where(
                        Member.dimension_id == hier.dimension_id, Member.code.in_(sel["members"])
                    )
                )
            }
            node_ids |= {
                n for (n,) in db.execute(
                    select(HierarchyNode.id).where(
                        HierarchyNode.hierarchy_id == hier.id, HierarchyNode.member_id.in_(member_ids or {-1})
                    )
                )
            }
        if not node_ids:
            continue
        keys = {
            k for (k,) in db.execute(
                select(SeriesNode.series_key).where(
                    SeriesNode.hierarchy_id == hier.id, SeriesNode.node_id.in_(node_ids)
                )
            )
        }
        selected = keys if selected is None else (selected & keys)

    result = all_keys if selected is None else selected
    if principal_nodes:
        for hier_id, nodes in principal_nodes.items():
            allowed = {
                k for (k,) in db.execute(
                    select(SeriesNode.series_key).where(
                        SeriesNode.hierarchy_id == hier_id, SeriesNode.node_id.in_(nodes or {-1})
                    )
                )
            }
            result = result & allowed
    return result


def group_map(db: Session, level: dict[str, str], series_keys: set[str] | None = None) -> dict[str, str]:
    """Map every base series to a group key at the requested level.

    level = {"product": "subcategory", "geo": "region"}
    Returns {series_key: "product=12|geo=4"}.
    """
    if not level:
        return {k: "total" for k in (series_keys or {k for (k,) in db.execute(select(Series.series_key))})}
    per_dim: dict[str, dict[str, int]] = {}
    for dim_code, lvl in level.items():
        hier = hierarchy_by_code(db, dim_code, None)
        if hier is None:
            continue
        li = level_index(hier, lvl)
        rows = db.execute(
            select(SeriesNode.series_key, SeriesNode.node_id).where(
                SeriesNode.hierarchy_id == hier.id, SeriesNode.level_index == li
            )
        ).all()
        per_dim[dim_code] = {sk: nid for sk, nid in rows}
    keys = series_keys if series_keys is not None else set(next(iter(per_dim.values()), {}).keys())
    out: dict[str, str] = {}
    for sk in keys:
        parts = []
        for dim_code in sorted(per_dim):
            nid = per_dim[dim_code].get(sk)
            if nid is None:
                parts = []
                break
            parts.append(f"{dim_code}={nid}")
        if parts:
            out[sk] = "|".join(parts)
    return out


def group_labels(db: Session, group_keys: set[str]) -> dict[str, dict]:
    """Human readable labels for group keys produced by group_map."""
    node_ids: set[int] = set()
    for gk in group_keys:
        if gk == "total":
            continue
        for part in gk.split("|"):
            node_ids.add(int(part.split("=", 1)[1]))
    rows = db.execute(
        select(HierarchyNode.id, HierarchyNode.level, Member.code, Member.name)
        .join(Member, Member.id == HierarchyNode.member_id)
        .where(HierarchyNode.id.in_(node_ids or {-1}))
    ).all()
    info = {nid: {"level": lvl, "code": code, "name": name} for nid, lvl, code, name in rows}
    out: dict[str, dict] = {}
    for gk in group_keys:
        if gk == "total":
            out[gk] = {"label": "Toplam", "parts": [], "node_ids": []}
            continue
        parts, nids = [], []
        for part in gk.split("|"):
            dim, nid = part.split("=", 1)
            nid = int(nid)
            nids.append(nid)
            meta = info.get(nid, {"name": str(nid), "level": "", "code": ""})
            parts.append({"dimension": dim, "node_id": nid, **meta})
        out[gk] = {
            "label": " / ".join(p["name"] for p in parts),
            "parts": parts,
            "node_ids": nids,
        }
    return out


def groups_for_user(db: Session, user_id: int) -> list[UserGroup]:
    return list(
        db.execute(
            select(UserGroup).where((UserGroup.owner_user_id == user_id) | (UserGroup.is_shared.is_(True)))
        ).scalars()
    )
