from __future__ import annotations

import os
import re
from typing import Any, Dict, List
from urllib.parse import urlencode


KAKAO_OIDC_DISCOVERY_URL = "https://kauth.kakao.com/.well-known/openid-configuration"
KAKAO_AUTHORIZATION_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_ISSUER = "https://kauth.kakao.com"

KAKAO_MESSAGE_ENDPOINTS = {
    "memo_default": "https://kapi.kakao.com/v2/api/talk/memo/default/send",
    "memo_custom": "https://kapi.kakao.com/v2/api/talk/memo/send",
    "friends_default": "https://kapi.kakao.com/v1/api/talk/friends/message/default/send",
    "friends_custom": "https://kapi.kakao.com/v1/api/talk/friends/message/send",
}

ALLOWED_NOTIFICATION_EVENTS = {
    "risk_review_required": "리스크 검토 필요",
    "handoff_review_required": "에이전트 인계사항 검토 필요",
    "legal_evidence_required": "법무/인허가 증빙 보완 필요",
    "report_export_ready": "심사보고서 생성 완료",
    "approval_readiness_changed": "승인 가능성 상태 변경",
}

SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{2,3}-\d{2,6}-\d{2,6}\b"),
    re.compile(r"\b\d{6}-\d{7}\b"),
    re.compile(r"(?<!\d)\d{10,}(?!\d)"),
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
]

SENSITIVE_FIELD_KEYWORDS = {
    "amount",
    "pfamount",
    "loan",
    "account",
    "owner",
    "registry",
    "encumbrance",
    "resident",
    "phone",
    "email",
    "pnu",
}


def kakao_integration_status() -> Dict[str, Any]:
    rest_api_key = os.environ.get("KAKAO_REST_API_KEY")
    redirect_uri = os.environ.get("KAKAO_REDIRECT_URI")
    return {
        "schemaVersion": "realytics-kakao-integration-status-v1",
        "login": {
            "provider": "kakao",
            "oidcDiscoveryUrl": KAKAO_OIDC_DISCOVERY_URL,
            "issuer": KAKAO_ISSUER,
            "restApiKeyConfigured": bool(rest_api_key),
            "clientSecretConfigured": bool(os.environ.get("KAKAO_CLIENT_SECRET")),
            "redirectUriConfigured": bool(redirect_uri),
            "redirectUri": redirect_uri,
            "recommendedIdpMode": "oidc_jwks",
            "currentIdpMode": os.environ.get("REALYTICS_IDP_MODE") or "off",
        },
        "message": {
            "mode": os.environ.get("KAKAO_MESSAGE_MODE") or "preview_only",
            "enabled": (os.environ.get("KAKAO_MESSAGE_MODE") or "preview_only") == "send",
            "accessTokenConfigured": bool(os.environ.get("KAKAO_MESSAGE_ACCESS_TOKEN")),
            "supportedEvents": sorted(ALLOWED_NOTIFICATION_EVENTS),
            "safeMessagePolicy": "No PF amounts, owner names, account numbers, registry details, PNU, or personal data in KakaoTalk body.",
            "endpoints": KAKAO_MESSAGE_ENDPOINTS,
        },
        "limitations": [
            "Kakao Login requires a registered redirect URI and Kakao app configuration.",
            "Kakao Talk Message API is for same-service Kakao Login users and requires consent/additional permission for friend messaging.",
            "Operational enterprise users should still prefer enterprise IdP as primary login where available.",
            "Use KakaoTalk only as a notification channel; detailed PF content remains behind REALYTICS login.",
        ],
    }


def build_kakao_authorize_url(
    *,
    state: str,
    nonce: str,
    redirect_uri: str | None = None,
    prompt: str | None = None,
) -> Dict[str, Any]:
    rest_api_key = os.environ.get("KAKAO_REST_API_KEY")
    resolved_redirect_uri = redirect_uri or os.environ.get("KAKAO_REDIRECT_URI")
    missing = []
    if not rest_api_key:
        missing.append("KAKAO_REST_API_KEY")
    if not resolved_redirect_uri:
        missing.append("KAKAO_REDIRECT_URI")
    if missing:
        return {
            "schemaVersion": "realytics-kakao-authorize-url-v1",
            "ready": False,
            "missingConfig": missing,
            "authorizeUrl": None,
        }
    params = {
        "client_id": rest_api_key,
        "redirect_uri": resolved_redirect_uri,
        "response_type": "code",
        "scope": os.environ.get("KAKAO_LOGIN_SCOPE") or "openid profile_nickname account_email",
        "state": state,
        "nonce": nonce,
    }
    if prompt:
        params["prompt"] = prompt
    return {
        "schemaVersion": "realytics-kakao-authorize-url-v1",
        "ready": True,
        "authorizeUrl": f"{KAKAO_AUTHORIZATION_URL}?{urlencode(params)}",
        "redirectUri": resolved_redirect_uri,
        "scope": params["scope"],
        "state": state,
        "nonce": nonce,
    }


def build_safe_kakao_message_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(payload.get("eventType") or "")
    project_name = _safe_text(payload.get("projectName") or payload.get("projectId") or "REALYTICS project")
    action_url = str(payload.get("actionUrl") or "")
    title = ALLOWED_NOTIFICATION_EVENTS.get(event_type, "REALYTICS 알림")
    raw_body = payload.get("body") or f"{project_name}: {title}"
    body = _safe_text(raw_body)
    blocked = _detect_sensitive_fields(payload)
    if event_type not in ALLOWED_NOTIFICATION_EVENTS:
        blocked.append(f"unsupported_event_type:{event_type or 'missing'}")
    if not action_url:
        blocked.append("missing_action_url")
    if len(body) > 180:
        body = body[:177] + "..."
    template = {
        "object_type": "text",
        "text": body,
        "link": {
            "web_url": action_url,
            "mobile_web_url": action_url,
        },
        "button_title": "REALYTICS에서 확인",
    }
    return {
        "schemaVersion": "realytics-kakao-message-preview-v1",
        "readyToSend": not blocked,
        "eventType": event_type,
        "title": title,
        "templateObject": template,
        "blockedReasons": blocked,
        "policy": {
            "noSensitivePfData": True,
            "bodyContainsOnlyNotificationSummary": True,
            "requiresUserConsentAndKakaoPermission": True,
            "detailedReviewMustStayInWebApp": True,
        },
    }


def _safe_text(value: Any) -> str:
    text = str(value or "")
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text.strip()


def _detect_sensitive_fields(payload: Dict[str, Any]) -> List[str]:
    blocked: List[str] = []
    for key, value in payload.items():
        normalized = str(key).replace("_", "").replace("-", "").lower()
        if any(keyword in normalized for keyword in SENSITIVE_FIELD_KEYWORDS):
            if value is not None and value != "" and value != []:
                blocked.append(f"sensitive_field:{key}")
        if isinstance(value, str):
            for pattern in SENSITIVE_PATTERNS:
                if pattern.search(value):
                    blocked.append(f"sensitive_text:{key}")
                    break
    return sorted(set(blocked))
