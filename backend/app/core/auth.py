"""JWT authentication, roles and row level scope."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.models import HierarchyNode, Role, RowPermission, User, UserRole

ALGO = "HS256"


def hash_password(password: str) -> str:
    salt = settings.jwt_secret.encode()
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000).hex()


def verify_password(password: str, stored: str) -> bool:
    return hmac.compare_digest(hash_password(password), stored)


def create_token(user_id: int, email: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGO])


@dataclass
class Principal:
    id: int
    email: str
    name: str
    roles: list[str]
    actor_kind: str = "user"
    # node ids the user may see / edit, empty dict means unrestricted
    view_nodes: dict[int, set[int]] = field(default_factory=dict)
    edit_nodes: dict[int, set[int]] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    def has_any(self, *codes: str) -> bool:
        return self.is_admin or any(c in self.roles for c in codes)

    @property
    def unrestricted(self) -> bool:
        return self.is_admin or not self.view_nodes


def load_principal(db: Session, user_id: int) -> Principal | None:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    roles = [
        r for (r,) in db.execute(
            select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)
        )
    ]
    p = Principal(id=user.id, email=user.email, name=user.name, roles=roles)
    if "admin" in roles:
        return p
    perms = db.execute(select(RowPermission).where(RowPermission.user_id == user.id)).scalars().all()
    for perm in perms:
        node = db.get(HierarchyNode, perm.hierarchy_node_id)
        if node is None:
            continue
        subtree = {
            n for (n,) in db.execute(
                select(HierarchyNode.id).where(
                    HierarchyNode.hierarchy_id == node.hierarchy_id,
                    HierarchyNode.path.like(f"{node.path}%"),
                )
            )
        }
        p.view_nodes.setdefault(node.hierarchy_id, set()).update(subtree)
        if perm.can_edit:
            p.edit_nodes.setdefault(node.hierarchy_id, set()).update(subtree)
    return p


def system_principal() -> Principal:
    return Principal(id=0, email="system@local", name="Sistem", roles=["admin"], actor_kind="system")
