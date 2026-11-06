from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from uuid import uuid4


_LOCK = threading.RLock()
_SESSION_SECRETS: Dict[str, Dict[str, Any]] = {}
_DEFAULT_CREDENTIAL_TTL_SECONDS = 60 * 60
_MAX_CREDENTIAL_TTL_SECONDS = 24 * 60 * 60


def secret_backend() -> str:
    return str(os.environ.get("REALYTICS_SECRET_BACKEND") or "session_memory").strip().lower()


def ingest_user_credential(
    *,
    user_id: str,
    tenant_id: str | None,
    session_id: str | None,
    provider: str,
    secret: str,
    expires_on_logout: bool = True,
) -> Dict[str, Any]:
    """Store a user credential and return a non-secret reference."""
    if not secret or not str(secret).strip():
        raise ValueError("apiKey is required")
    normalized_user_id = str(user_id or "").strip()
    normalized_tenant_id = str(tenant_id or "").strip()
    normalized_session_id = str(session_id or "").strip()
    if not normalized_user_id:
        raise ValueError("An authenticated user is required for credentials")
    if not normalized_tenant_id:
        raise ValueError("An authenticated tenant is required for credentials")
    if not normalized_session_id:
        raise ValueError("An authenticated session is required for session-bound credentials")
    now = _utc_now()
    expires_at = now + timedelta(seconds=_credential_ttl_seconds())
    backend = secret_backend()
    if backend == "google_secret_manager":
        from core.google_secret_store import create_record

        reference = create_record(
            {
                "schemaVersion": "realytics-session-secret-v1",
                "secret": str(secret),
                "userId": normalized_user_id,
                "tenantId": normalized_tenant_id,
                "sessionId": normalized_session_id,
                "provider": provider,
                "expiresOnLogout": bool(expires_on_logout),
                "createdAt": now.isoformat(),
                "expiresAt": expires_at.isoformat(),
            },
            ttl_seconds=int((expires_at - now).total_seconds()),
        )
        return {
            "credentialRef": reference,
            "backend": backend,
            "expiresOnLogout": bool(expires_on_logout),
            "expiresAt": expires_at.isoformat(),
            "masked": _mask_secret(str(secret)),
        }
    if backend in {"aws", "aws_secrets_manager"}:
        return _ingest_aws_secret(
            user_id=normalized_user_id,
            tenant_id=normalized_tenant_id,
            session_id=normalized_session_id,
            provider=provider,
            secret=str(secret),
            expires_on_logout=expires_on_logout,
            created_at=now,
            expires_at=expires_at,
        )
    if backend != "session_memory":
        raise RuntimeError(f"Unsupported REALYTICS_SECRET_BACKEND: {backend}")
    credential_id = str(uuid4())
    reference = f"session-secret://{credential_id}"
    with _LOCK:
        _prune_expired_locked(now)
        _SESSION_SECRETS[reference] = {
            "secret": str(secret),
            "userId": normalized_user_id,
            "tenantId": normalized_tenant_id,
            "sessionId": normalized_session_id,
            "provider": provider,
            "expiresOnLogout": bool(expires_on_logout),
            "createdAt": now.isoformat(),
            "expiresAt": expires_at.isoformat(),
        }
    return {
        "credentialRef": reference,
        "backend": "session_memory",
        "expiresOnLogout": bool(expires_on_logout),
        "expiresAt": expires_at.isoformat(),
        "masked": _mask_secret(str(secret)),
    }


def resolve_user_credential(
    credential_ref: str | None,
    *,
    user_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> str | None:
    if not credential_ref:
        return None
    reference = str(credential_ref)
    if reference.startswith("gcp-sm://"):
        from core.google_secret_store import read_record

        record = read_record(reference)
        if record is None or _record_is_expired(record, _utc_now()):
            return None
        if not _record_matches_boundary(
            record, user_id=user_id, tenant_id=tenant_id,
            session_id=session_id, require_all=True,
        ):
            return None
        value = record.get("secret")
        return value if isinstance(value, str) and value else None
    if reference.startswith("session-secret://"):
        with _LOCK:
            _prune_expired_locked(_utc_now())
            record = _SESSION_SECRETS.get(reference)
            if not record or not _record_matches_boundary(
                record,
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                require_all=True,
            ):
                return None
            return str(record["secret"])
    if reference.startswith("aws-sm://"):
        return _resolve_aws_secret(
            reference,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
        )
    return None


def credential_access_status(
    credential_ref: str | None,
    *,
    user_id: str,
    tenant_id: str | None,
    session_id: str | None,
) -> str:
    """Return active/missing/expired/forbidden without exposing a secret."""

    if not credential_ref:
        return "missing"
    reference = str(credential_ref)
    if reference.startswith("gcp-sm://"):
        from core.google_secret_store import read_record

        record = read_record(reference)
        if record is None:
            return "missing"
        if not _record_matches_boundary(
            record, user_id=user_id, tenant_id=tenant_id,
            session_id=session_id, require_all=True,
        ):
            return "forbidden"
        return "expired" if _record_is_expired(record, _utc_now()) else "active"
    if reference.startswith("session-secret://"):
        with _LOCK:
            record = _SESSION_SECRETS.get(reference)
            if not record:
                return "missing"
            if _record_is_expired(record, _utc_now()):
                _SESSION_SECRETS.pop(reference, None)
                return "expired"
            if not _record_matches_boundary(
                record,
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                require_all=True,
            ):
                return "forbidden"
            return "active"
    if reference.startswith("aws-sm://"):
        record = _read_aws_secret_record(reference)
        if record is None:
            return "missing"
        if _record_is_expired(record, _utc_now()):
            _delete_aws_secret(reference)
            return "expired"
        if not _record_matches_boundary(
            record,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            require_all=True,
        ):
            return "forbidden"
        return "active"
    return "missing"


def revoke_credential(
    credential_ref: str | None,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> bool:
    if not credential_ref:
        return False
    reference = str(credential_ref)
    if reference.startswith("gcp-sm://"):
        from core.google_secret_store import delete_record, read_record

        if any(value is not None for value in (user_id, tenant_id, session_id)):
            record = read_record(reference)
            if record is None or not _record_matches_boundary(
                record, user_id=user_id, tenant_id=tenant_id,
                session_id=session_id, require_all=True,
            ):
                return False
        return delete_record(reference)
    if reference.startswith("session-secret://"):
        with _LOCK:
            record = _SESSION_SECRETS.get(reference)
            if record is None:
                return False
            if not _record_matches_boundary(
                record,
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                require_all=False,
            ):
                return False
            return _SESSION_SECRETS.pop(reference, None) is not None
    if reference.startswith("aws-sm://"):
        if any(value is not None for value in (user_id, tenant_id, session_id)):
            record = _read_aws_secret_record(reference)
            if record is None or not _record_matches_boundary(
                record,
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                require_all=False,
            ):
                return False
        _delete_aws_secret(reference)
        return True
    return False


def revoke_session_credentials(
    session_id: str,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> int:
    with _LOCK:
        _prune_expired_locked(_utc_now())
        references = [
            reference
            for reference, record in _SESSION_SECRETS.items()
            if record.get("sessionId") == session_id
            and (user_id is None or record.get("userId") == user_id)
            and (tenant_id is None or record.get("tenantId") == tenant_id)
            and record.get("expiresOnLogout", True)
        ]
        for reference in references:
            _SESSION_SECRETS.pop(reference, None)
    return len(references)


def _ingest_aws_secret(
    *,
    user_id: str,
    tenant_id: str,
    session_id: str,
    provider: str,
    secret: str,
    expires_on_logout: bool,
    created_at: datetime,
    expires_at: datetime,
) -> Dict[str, Any]:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - production dependency
        raise RuntimeError("boto3 is required for AWS Secrets Manager") from exc
    prefix = str(os.environ.get("REALYTICS_SECRET_PREFIX") or "realytics/user-ai").strip("/")
    # BYOK credentials are intentionally ephemeral. A unique secret avoids a
    # logout deletion race with a later login using the same provider.
    secret_id = f"{prefix}/{tenant_id or 'default'}/{user_id}/{provider}/{uuid4()}"
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION"))
    envelope = {
        "schemaVersion": "realytics-session-secret-v1",
        "secret": secret,
        "userId": user_id,
        "tenantId": tenant_id,
        "sessionId": session_id,
        "provider": provider,
        "expiresOnLogout": bool(expires_on_logout),
        "createdAt": created_at.isoformat(),
        "expiresAt": expires_at.isoformat(),
    }
    client.create_secret(
        Name=secret_id,
        SecretString=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
    )
    return {
        "credentialRef": f"aws-sm://{secret_id}",
        "backend": "aws_secrets_manager",
        "expiresOnLogout": bool(expires_on_logout),
        "expiresAt": expires_at.isoformat(),
        "masked": _mask_secret(secret),
    }


def _resolve_aws_secret(
    reference: str,
    *,
    user_id: str,
    tenant_id: str | None,
    session_id: str | None,
) -> str | None:
    record = _read_aws_secret_record(reference)
    if record is None:
        return None
    if _record_is_expired(record, _utc_now()):
        _delete_aws_secret(reference)
        return None
    if not _record_matches_boundary(
        record,
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        require_all=True,
    ):
        return None
    return str(record.get("secret") or "") or None


def _read_aws_secret_record(reference: str) -> Dict[str, Any] | None:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - production dependency
        raise RuntimeError("boto3 is required for AWS Secrets Manager") from exc
    secret_id = reference.removeprefix("aws-sm://")
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION"))
    try:
        response = client.get_secret_value(SecretId=secret_id)
    except client.exceptions.ResourceNotFoundException:
        return None
    raw = response.get("SecretString")
    if not isinstance(raw, str):
        return None
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        # Legacy raw values have no tenant/session attestation and therefore
        # cannot cross the hardened resolution boundary.
        return None
    if not isinstance(record, dict) or record.get("schemaVersion") != "realytics-session-secret-v1":
        return None
    return record


def _delete_aws_secret(reference: str) -> None:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - production dependency
        raise RuntimeError("boto3 is required for AWS Secrets Manager") from exc
    secret_id = reference.removeprefix("aws-sm://")
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION"))
    client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)


def _credential_ttl_seconds() -> int:
    raw = str(
        os.environ.get("REALYTICS_AI_CREDENTIAL_TTL_SECONDS")
        or _DEFAULT_CREDENTIAL_TTL_SECONDS
    ).strip()
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise RuntimeError("REALYTICS_AI_CREDENTIAL_TTL_SECONDS must be an integer") from exc
    if ttl <= 0 or ttl > _MAX_CREDENTIAL_TTL_SECONDS:
        raise RuntimeError(
            "REALYTICS_AI_CREDENTIAL_TTL_SECONDS must be between 1 and 86400"
        )
    return ttl


def _record_matches_boundary(
    record: Dict[str, Any],
    *,
    user_id: str | None,
    tenant_id: str | None,
    session_id: str | None,
    require_all: bool,
) -> bool:
    requested = {
        "userId": str(user_id or "").strip(),
        "tenantId": str(tenant_id or "").strip(),
        "sessionId": str(session_id or "").strip(),
    }
    if require_all and any(not value for value in requested.values()):
        return False
    for key, value in requested.items():
        if value and str(record.get(key) or "") != value:
            return False
    return True


def _record_is_expired(record: Dict[str, Any], now: datetime) -> bool:
    raw = record.get("expiresAt")
    if not isinstance(raw, str) or not raw.strip():
        return True
    try:
        expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        return True
    return expires_at <= now


def _prune_expired_locked(now: datetime) -> int:
    references = [
        reference
        for reference, record in _SESSION_SECRETS.items()
        if _record_is_expired(record, now)
    ]
    for reference in references:
        _SESSION_SECRETS.pop(reference, None)
    return len(references)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return f"{secret[:2]}***{secret[-2:]}"
    return f"{secret[:4]}***{secret[-4:]}"
