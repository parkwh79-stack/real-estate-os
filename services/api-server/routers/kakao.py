from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Request

from integrations.kakao import (
    build_kakao_authorize_url,
    build_safe_kakao_message_preview,
    create_kakao_oauth_state,
    kakao_integration_status,
)
from storage import record_user_activity_log


router = APIRouter(prefix="/integrations/kakao", tags=["kakao"])


@router.get("/status")
def get_kakao_status():
    return kakao_integration_status()


@router.post("/login/authorize-url")
def create_kakao_authorize_url(payload: Dict[str, Any] | None = None):
    payload = payload or {}
    nonce = str(payload.get("nonce") or uuid4())
    state = str(payload.get("state") or create_kakao_oauth_state(
        nonce=nonce,
        redirect_path=payload.get("redirectPath") or payload.get("redirect_path"),
    ))
    return build_kakao_authorize_url(
        state=state,
        nonce=nonce,
        redirect_uri=payload.get("redirectUri") or payload.get("redirect_uri"),
        prompt=payload.get("prompt"),
    )


@router.post("/messages/preview")
def preview_kakao_message(payload: Dict[str, Any], request: Request):
    preview = build_safe_kakao_message_preview(payload)
    record_user_activity_log(
        {
            "eventType": "kakao_message_preview",
            "userId": request.headers.get("x-realytics-user-id") or "system",
            "projectId": payload.get("projectId") or payload.get("project_id"),
            "metadata": {
                "eventType": preview.get("eventType"),
                "readyToSend": preview.get("readyToSend"),
                "blockedReasons": preview.get("blockedReasons", []),
                "recommendedMobilePath": preview.get("recommendedMobilePath"),
                "messageMode": "preview_only",
            },
        }
    )
    return preview
