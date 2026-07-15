from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4


_LOCK = threading.RLock()
_SESSION_SECRETS: Dict[str, Dict[str, Any]] = {}


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
    backend = secret_backend()
    if backend in {"aws", "aws_secrets_manager"}:
        return _ingest_aws_secret(
            user_id=user_id,
            tenant_id=tenant_id,
            provider=provider,
            secret=str(secret),
            expires_on_logout=expires_on_logout,
        )
    if backend != "session_memory":
        raise RuntimeError(f"Unsupported REALYTICS_SECRET_BACKEND: {backend}")
    if not session_id:
        raise ValueError("An authenticated session is required for session-bound credentials")
    credential_id = str(uuid4())
    reference = f"session-secret://{session_id}/{credential_id}"
    with _LOCK:
        _SESSION_SECRETS[reference] = {
            "secret": str(secret),
            "userId": user_id,
            "tenantId": tenant_id,
            "sessionId": session_id,
            "provider": provider,
            "expiresOnLogout": bool(expires_on_logout),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "credentialRef": reference,
        "backend": "session_memory",
        "expiresOnLogout": bool(expires_on_logout),
        "masked": _mask_secret(str(secret)),
    }


def resolve_user_credential(
    credential_ref: str | None,
    *,
    user_id: str,
    tenant_id: str | None = None,
) -> str | None:
    if not credential_ref:
        return None
    reference = str(credential_ref)
    if reference.startswith("session-secret://"):
        with _LOCK:
            record = _SESSION_SECRETS.get(reference)
            if not record or record.get("userId") != user_id:
                return None
            if tenant_id and record.get("tenantId") and record.get("tenantId") != tenant_id:
                return None
            return str(record["secret"])
    if reference.startswith("aws-sm://"):
        return _resolve_aws_secret(reference)
    return None


def revoke_credential(credential_ref: str | None) -> bool:
    if not credential_ref:
        return False
    reference = str(credential_ref)
    if reference.startswith("session-secret://"):
        with _LOCK:
            return _SESSION_SECRETS.pop(reference, None) is not None
    if reference.startswith("aws-sm://"):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - production dependency
            raise RuntimeError("boto3 is required for AWS Secrets Manager") from exc
        secret_id = reference.removeprefix("aws-sm://")
        client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION"))
        client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)
        return True
    return False


def revoke_session_credentials(session_id: str) -> int:
    with _LOCK:
        references = [
            reference
            for reference, record in _SESSION_SECRETS.items()
            if record.get("sessionId") == session_id and record.get("expiresOnLogout", True)
        ]
        for reference in references:
            _SESSION_SECRETS.pop(reference, None)
    return len(references)


def _ingest_aws_secret(
    *,
    user_id: str,
    tenant_id: str | None,
    provider: str,
    secret: str,
    expires_on_logout: bool,
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
    client.create_secret(Name=secret_id, SecretString=secret)
    return {
        "credentialRef": f"aws-sm://{secret_id}",
        "backend": "aws_secrets_manager",
        "expiresOnLogout": bool(expires_on_logout),
        "masked": _mask_secret(secret),
    }


def _resolve_aws_secret(reference: str) -> str | None:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - production dependency
        raise RuntimeError("boto3 is required for AWS Secrets Manager") from exc
    secret_id = reference.removeprefix("aws-sm://")
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION"))
    response = client.get_secret_value(SecretId=secret_id)
    return response.get("SecretString")


def _mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return f"{secret[:2]}***{secret[-2:]}"
    return f"{secret[:4]}***{secret[-4:]}"
