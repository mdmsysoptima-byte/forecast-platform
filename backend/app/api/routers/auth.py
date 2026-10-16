"""Login and session."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_principal
from app.core.auth import Principal, create_token, load_principal, verify_password
from app.core.db import get_session
from app.domain.models import Role, User, UserRole

router = APIRouter(prefix="/api/v1/auth", tags=["Kimlik"])


class LoginRequest(BaseModel):
    email: str = Field(description="Kullanıcı e-posta adresi")
    password: str


class LoginResponse(BaseModel):
    token: str
    user: dict
    summary: str


@router.post("/login", response_model=LoginResponse, summary="Oturum ac")
def login(payload: LoginRequest, db: Session = Depends(get_session)) -> LoginResponse:
    user = db.execute(select(User).where(User.email == payload.email.strip().lower())).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı")
    roles = [
        r for (r,) in db.execute(
            select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)
        )
    ]
    principal = load_principal(db, user.id)
    return LoginResponse(
        token=create_token(user.id, user.email, roles),
        user={
            "id": user.id, "name": user.name, "email": user.email, "roles": roles,
            "restricted": not (principal.unrestricted if principal else True),
        },
        summary=f"{user.name} oturum acti, roller: {', '.join(roles)}",
    )


@router.get("/me", summary="Oturumdaki kullanıcı")
def me(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "id": principal.id, "name": principal.name, "email": principal.email,
        "roles": principal.roles, "restricted": not principal.unrestricted,
        "summary": f"{principal.name}, roller: {', '.join(principal.roles)}",
    }


@router.get("/demo-users", summary="Demo kullanıcıları")
def demo_users(db: Session = Depends(get_session)) -> dict:
    rows = db.execute(select(User)).scalars().all()
    out = []
    for u in rows:
        roles = [
            r for (r,) in db.execute(
                select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == u.id)
            )
        ]
        out.append({"email": u.email, "name": u.name, "roles": roles})
    return {"users": out, "password": "demo", "summary": f"{len(out)} demo kullanıcısı"}
