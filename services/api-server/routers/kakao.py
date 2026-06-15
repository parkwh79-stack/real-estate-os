from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter

from integrations.kakao import (
    build_kakao_authorize_url,
    build_safe_kakao_message_preview,
    kakao_integration_status,
)


router = APIRouter(prefix="/integrations/kakao", tags=["kakao"])


@router.get("/status")
def get_kakao_status():
    return kakao_integration_status()


@router.post("/login/authorize-url")
def create_kakao_authorize_url(payload: Dict[str, Any] | None = None):
    payload = payload or {}
    return build_kakao_authorize_url(
        state=str(payload.get("state") or uuid4()),
        nonce=str(payload.get("nonce") or uuid4()),
        redirect_uri=payload.get("redirectUri") or payload.get("redirect_uri"),
        prompt=payload.get("prompt"),
    )


@router.post("/messages/preview")
def preview_kakao_message(payload: Dict[str, Any]):
    return build_safe_kakao_message_preview(payload)
