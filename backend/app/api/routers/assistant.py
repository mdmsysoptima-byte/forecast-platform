"""Assistant endpoints (section 16)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_principal
from app.assistant import tools as assistant_tools
from app.core.auth import Principal
from app.core.db import get_session

router = APIRouter(prefix="/api/v1/assistant", tags=["Asistan"])


@router.get("/tools", summary="Role göre sunulan araç kataloğu")
def tools(
    db: Session = Depends(get_session), principal: Principal = Depends(current_principal)
) -> dict:
    groups = assistant_tools.tools_for(principal)
    count = sum(len(g["tools"]) for g in groups)
    return {
        "groups": groups,
        "behaviour_rules": assistant_tools.BEHAVIOUR_RULES,
        "summary": f"{principal.name} için {count} araç sunuluyor",
    }


@router.get("/system-prompt", summary="Sistem promptu")
def system_prompt(
    db: Session = Depends(get_session), principal: Principal = Depends(current_principal)
) -> dict:
    text = assistant_tools.system_prompt(db, principal)
    return {"system_prompt": text, "summary": f"{len(text)} karakterlik sistem promptu"}


class ChatIn(BaseModel):
    message: str
    context: dict = Field(default_factory=dict, description="Seçili versiyon, senaryo, kapsam, kova")


@router.post("/chat", summary="Sohbetle calis (adım 8, bağlantı bekliyor)")
def chat(
    payload: ChatIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    res = assistant_tools.chat(db, principal, payload.message, payload.context)
    res["summary"] = res["message"]
    return res
