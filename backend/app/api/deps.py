"""Request dependencies: authentication, roles and row scope."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.auth import Principal, decode_token, load_principal
from app.core.db import get_session


def current_principal(request: Request, db: Session = Depends(get_session)) -> Principal:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Oturum gerekli")
    try:
        payload = decode_token(auth.split(" ", 1)[1].strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Oturum süresi doldu") from exc
    principal = load_principal(db, int(payload["sub"]))
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı bulunamadi")
    return principal


def require_roles(*codes: str):
    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.has_any(*codes):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu işlem için yetkiniz yok")
        return principal

    return _dep
