from __future__ import annotations

import os
import re
import json
import base64
import hashlib
import hmac
import time
from typing import Any, Dict, List
from urllib import request as urllib_request
from urllib.parse import urlencode

from fastapi import HTTPException


KAKAO_OIDC_DISCOVERY_URL = "https://kauth.kakao.com/.well-known/openid-configuration"
KAKAO_AUTHORIZATION_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USERINFO_URL = "https://kapi.kakao.com/v2/user/me"
KAKAO_ISSUER = "https://kauth.kakao.com"

KAKAO_MESSAGE_ENDPOINTS = {
    "memo_default": "https://kapi.kakao.com/v2/api/talk/memo/default/send",
    "memo_custom": "https://kapi.kakao.com/v2/api/talk/memo/send",
    "friends_default": "https://kapi.kakao.com/v1/api/talk/friends/message/default/send",
    "friends_custom": "https://kapi.kakao.com/v1/api/talk/friends/message/send",
}

ALLOWED_NOTIFICATION_EVENTS = {
    "document_upload_requested": "사업자료 업로드 요청",
    "document_analysis_started": "AI 분석 시작",
    "document_analysis_completed": "AI 분석 완료",
    "analysis_plan_ready": "AI 분석 메뉴 안내",
    "workflow_result_ready": "선택 분석 결과 안내",
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
    resolved_state = state or create_kakao_oauth_state(nonce=nonce)
    params = {
        "client_id": rest_api_key,
        "redirect_uri": resolved_redirect_uri,
        "response_type": "code",
        "scope": os.environ.get("KAKAO_LOGIN_SCOPE") or "openid profile_nickname",
        "state": resolved_state,
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
        "state": resolved_state,
        "nonce": nonce,
    }


def create_kakao_oauth_state(*, nonce: str, redirect_path: str | None = None, ttl_seconds: int = 600) -> str:
    """Create a stateless HMAC-protected OAuth state value for Kakao redirects."""
    issued_at = int(time.time())
    payload = {
        "nonce": nonce,
        "redirectPath": redirect_path or "/dashboard",
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    encoded_payload = _b64url_json(payload)
    signature = _sign_oauth_state(encoded_payload)
    return f"rk1.{encoded_payload}.{signature}"


def verify_kakao_oauth_state(state: str | None) -> Dict[str, Any]:
    if not state:
        raise HTTPException(status_code=400, detail={"error": "missing_state"})
    parts = str(state).split(".")
    if len(parts) != 3 or parts[0] != "rk1":
        if _require_signed_state():
            raise HTTPException(status_code=400, detail={"error": "invalid_state_format"})
        return {"legacyState": state, "nonce": None, "redirectPath": "/dashboard", "signatureVerified": False}
    _, encoded_payload, signature = parts
    expected = _sign_oauth_state(encoded_payload)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail={"error": "invalid_state_signature"})
    payload = _json_b64url_decode(encoded_payload)
    exp = payload.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        raise HTTPException(status_code=400, detail={"error": "state_expired"})
    payload["signatureVerified"] = True
    return payload


def exchange_kakao_authorization_code(code: str, *, redirect_uri: str | None = None) -> Dict[str, Any]:
    rest_api_key = os.environ.get("KAKAO_REST_API_KEY")
    resolved_redirect_uri = redirect_uri or os.environ.get("KAKAO_REDIRECT_URI")
    if not rest_api_key or not resolved_redirect_uri:
        raise HTTPException(status_code=500, detail={"error": "kakao_login_not_configured"})
    form = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": resolved_redirect_uri,
        "code": code,
    }
    client_secret = os.environ.get("KAKAO_CLIENT_SECRET")
    if client_secret:
        form["client_secret"] = client_secret
    return _post_form_json(KAKAO_TOKEN_URL, form)


def fetch_kakao_user_info(access_token: str) -> Dict[str, Any]:
    if not access_token:
        raise HTTPException(status_code=401, detail={"error": "missing_kakao_access_token"})
    req = urllib_request.Request(
        KAKAO_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib_request.urlopen(req, timeout=10) as response:  # nosec - official Kakao endpoint constant
        return json.loads(response.read().decode("utf-8"))


def build_kakao_identity_records(
    *,
    tokens: Dict[str, Any],
    user_info: Dict[str, Any] | None,
    state_payload: Dict[str, Any],
) -> Dict[str, Any]:
    id_token = tokens.get("id_token")
    claims = _decode_id_token_without_signature(str(id_token)) if id_token else {}
    if claims:
        _validate_kakao_claims(claims, expected_nonce=state_payload.get("nonce"))

    kakao_account = (user_info or {}).get("kakao_account") if isinstance(user_info, dict) else {}
    profile = kakao_account.get("profile") if isinstance(kakao_account, dict) else {}
    kakao_id = (user_info or {}).get("id") if isinstance(user_info, dict) else None
    subject = claims.get("sub") or kakao_id
    if not subject:
        raise HTTPException(status_code=401, detail={"error": "missing_kakao_subject"})
    email = claims.get("email") or (kakao_account or {}).get("email")
    display_name = (
        claims.get("nickname")
        or claims.get("name")
        or (profile or {}).get("nickname")
        or (str(email) if email else f"kakao-{subject}")
    )
    tenant_id = os.environ.get("KAKAO_DEFAULT_TENANT_ID") or os.environ.get("REALYTICS_DEFAULT_TENANT_ID") or "pilot-tenant"
    role = os.environ.get("KAKAO_DEFAULT_ROLE") or "viewer"
    user_id = str(email or f"kakao:{subject}")
    user = {
        "userId": user_id,
        "tenantId": tenant_id,
        "email": str(email) if email else None,
        "displayName": str(display_name),
        "role": role,
        "status": "active",
        "metadata": {
            "provider": "kakao",
            "kakaoSubject": str(subject),
            "idTokenPresent": bool(id_token),
            "idTokenSignatureVerification": "not_yet_verified_jwks",
            "stateSignatureVerified": bool(state_payload.get("signatureVerified")),
        },
    }
    session = {
        "userId": user_id,
        "tenantId": tenant_id,
        "provider": "kakao",
        "host": "kakao_oauth_callback",
        "metadata": {
            "kakaoSubject": str(subject),
            "tokenType": tokens.get("token_type"),
            "scope": tokens.get("scope"),
            "idTokenPresent": bool(id_token),
            "stateSignatureVerified": bool(state_payload.get("signatureVerified")),
            "redirectPath": state_payload.get("redirectPath") or "/dashboard",
        },
    }
    return {
        "user": user,
        "session": session,
        "claims": _sanitize_claims(claims),
        "redirectPath": state_payload.get("redirectPath") or "/dashboard",
    }


def build_safe_kakao_message_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(payload.get("eventType") or "")
    project_name = _safe_text(payload.get("projectName") or payload.get("projectId") or "REALYTICS project")
    action_url = str(payload.get("actionUrl") or _default_mobile_action_url(payload, event_type) or "")
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
        "recommendedMobilePath": _mobile_action_path(payload, event_type),
        "policy": {
            "noSensitivePfData": True,
            "bodyContainsOnlyNotificationSummary": True,
            "requiresUserConsentAndKakaoPermission": True,
            "detailedReviewMustStayInWebApp": True,
        },
    }


def _default_mobile_action_url(payload: Dict[str, Any], event_type: str) -> str | None:
    web_base_url = os.environ.get("REALYTICS_WEB_BASE_URL")
    if not web_base_url:
        return None
    path = _mobile_action_path(payload, event_type)
    if not path:
        return None
    return f"{web_base_url.rstrip('/')}{path}"


def _mobile_action_path(payload: Dict[str, Any], event_type: str) -> str | None:
    project_id = payload.get("projectId") or payload.get("project_id")
    if not project_id:
        return None
    event = event_type or "notification"
    if event in {"document_upload_requested", "document_analysis_started", "analysis_plan_ready"}:
        return f"/project/{project_id}/kakao-intake?source=kakao&event={event}"
    return f"/project/{project_id}/mobile?source=kakao&event={event}"


def build_kakao_intake_plan_message(project_id: Any, project_name: Any, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Build a KakaoTalk-safe intent menu message from an orchestration plan.

    The body only carries workflow names and a recommendation; detailed inputs
    and results stay inside REALYTICS. A structured ``intentMenu`` is attached so
    the Kakao intake page can render selectable workflow buttons.
    """
    safe_name = _safe_text(project_name or project_id or "REALYTICS project")
    available = [w for w in plan.get("workflows", []) if w.get("availableNow")]
    recommended = next(
        (w for w in plan.get("workflows", []) if w.get("key") == plan.get("recommendedWorkflow")),
        None,
    )
    body = f"{safe_name} 자료 분석 준비가 완료되었습니다. 진행할 작업을 선택해 주세요"
    if recommended:
        body += f" (추천: {recommended.get('nameKo')})"
    payload = {
        "eventType": "analysis_plan_ready",
        "projectId": project_id,
        "projectName": safe_name,
        "body": body,
    }
    preview = build_safe_kakao_message_preview(payload)
    preview["intentMenu"] = [
        {
            "key": w.get("key"),
            "nameKo": w.get("nameKo"),
            "recommended": w.get("key") == plan.get("recommendedWorkflow"),
            "runWorkflowEndpoint": f"/projects/{project_id}/orchestration/run-workflow",
        }
        for w in available
    ]
    preview["recommendedWorkflow"] = plan.get("recommendedWorkflow")
    return preview


def build_kakao_workflow_result_message(
    project_id: Any, project_name: Any, workflow_name: Any, status: Any
) -> Dict[str, Any]:
    """Build a KakaoTalk-safe completion notice for a selected workflow run."""
    safe_name = _safe_text(project_name or project_id or "REALYTICS project")
    safe_workflow = _safe_text(workflow_name or "선택 분석")
    body = f"{safe_name} '{safe_workflow}' 분석이 완료되었습니다. 상세 결과는 REALYTICS에서 확인해 주세요."
    payload = {
        "eventType": "workflow_result_ready",
        "projectId": project_id,
        "projectName": safe_name,
        "body": body,
    }
    return build_safe_kakao_message_preview(payload)


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


def _post_form_json(url: str, form: Dict[str, Any]) -> Dict[str, Any]:
    data = urlencode(form).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=10) as response:  # nosec - official Kakao endpoint constant
        return json.loads(response.read().decode("utf-8"))


def _validate_kakao_claims(claims: Dict[str, Any], *, expected_nonce: str | None) -> None:
    if claims.get("iss") and claims.get("iss") != KAKAO_ISSUER:
        raise HTTPException(status_code=401, detail={"error": "kakao_issuer_mismatch"})
    audience = os.environ.get("REALYTICS_IDP_AUDIENCE") or os.environ.get("KAKAO_REST_API_KEY")
    if audience:
        aud = claims.get("aud")
        valid = audience in aud if isinstance(aud, list) else aud == audience
        if not valid:
            raise HTTPException(status_code=401, detail={"error": "kakao_audience_mismatch"})
    if expected_nonce and claims.get("nonce") and claims.get("nonce") != expected_nonce:
        raise HTTPException(status_code=401, detail={"error": "kakao_nonce_mismatch"})
    exp = claims.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        raise HTTPException(status_code=401, detail={"error": "kakao_id_token_expired"})


def _decode_id_token_without_signature(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise HTTPException(status_code=401, detail={"error": "invalid_kakao_id_token"})
    return _json_b64url_decode(parts[1])


def _sanitize_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"iss", "aud", "sub", "exp", "iat", "nonce", "email", "nickname", "name"}
    return {key: claims.get(key) for key in allowed if key in claims}


def _require_signed_state() -> bool:
    return (os.environ.get("KAKAO_REQUIRE_SIGNED_STATE") or "false").lower() in {"1", "true", "yes"}


def _state_secret() -> str:
    return os.environ.get("KAKAO_OAUTH_STATE_SECRET") or os.environ.get("REALYTICS_IDP_JWT_SECRET") or "realytics-local-kakao-state"


def _sign_oauth_state(encoded_payload: str) -> str:
    digest = hmac.new(_state_secret().encode("utf-8"), encoded_payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_bytes(digest)


def _b64url_json(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _b64url_bytes(raw)


def _b64url_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _json_b64url_decode(value: str) -> Dict[str, Any]:
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + padding)
        decoded = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail={"error": "invalid_kakao_token_encoding"}) from exc
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=401, detail={"error": "invalid_kakao_token_payload"})
    return decoded
