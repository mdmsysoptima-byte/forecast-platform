"""Audit log. Every write operation goes through here."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import correlation_id, get_logger
from app.domain.models import AuditLog

log = get_logger(__name__)


def record(
    db: Session,
    entity: str,
    entity_id: Any,
    action: str,
    actor_id: int | None = None,
    actor_kind: str = "user",
    before: dict | None = None,
    after: dict | None = None,
) -> AuditLog:
    row = AuditLog(
        entity=entity,
        entity_id=str(entity_id),
        action=action,
        actor_id=actor_id,
        actor_kind=actor_kind,
        before=before,
        after=after,
        correlation_id=correlation_id.get(),
    )
    db.add(row)
    log.info(
        "audit %s %s", entity, action,
        extra={"entity": entity, "actor_id": actor_id},
    )
    return row
