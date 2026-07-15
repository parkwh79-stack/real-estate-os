from __future__ import annotations

import os


HARDENED_ENVIRONMENTS = {"production", "prod", "staging"}
LOCAL_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def deployment_environment() -> str:
    return (os.environ.get("REALYTICS_ENVIRONMENT") or "development").strip().lower()


def is_hardened_environment() -> bool:
    return deployment_environment() in HARDENED_ENVIRONMENTS


def security_default(*, local: str, hardened: str) -> str:
    return hardened if is_hardened_environment() else local


def cors_origins() -> list[str]:
    configured = os.environ.get("REALYTICS_CORS_ORIGINS")
    origins = _parse_csv(configured) if configured else list(LOCAL_CORS_ORIGINS)
    if "*" in origins:
        raise RuntimeError(
            "REALYTICS_CORS_ORIGINS must not contain '*' when credentialed requests are enabled."
        )
    return origins


def validate_security_configuration() -> None:
    """Fail closed before a hardened deployment accepts traffic."""
    if not is_hardened_environment():
        cors_origins()
        return

    errors: list[str] = []
    if (os.environ.get("REALYTICS_RBAC_MODE") or "").lower() != "strict":
        errors.append("REALYTICS_RBAC_MODE=strict")
    if (os.environ.get("REALYTICS_TENANT_ISOLATION_MODE") or "").lower() != "strict":
        errors.append("REALYTICS_TENANT_ISOLATION_MODE=strict")
    if (os.environ.get("REALYTICS_IDP_MODE") or "").lower() not in {"oidc", "jwks", "rs256"}:
        errors.append("REALYTICS_IDP_MODE=oidc")
    if not _first_env("REALYTICS_IDP_ISSUER", "REALYTICS_OIDC_ISSUER"):
        errors.append("REALYTICS_IDP_ISSUER")
    if not _first_env("REALYTICS_IDP_JWKS_URL", "REALYTICS_OIDC_JWKS_URL"):
        errors.append("REALYTICS_IDP_JWKS_URL")
    if not _first_env("REALYTICS_IDP_AUDIENCE", "REALYTICS_OIDC_AUDIENCE"):
        errors.append("REALYTICS_IDP_AUDIENCE")
    if not os.environ.get("REALYTICS_CORS_ORIGINS"):
        errors.append("REALYTICS_CORS_ORIGINS")
    if not _first_env("REALYTICS_DATABASE_URL", "REALYTICS_PG_HOST"):
        errors.append("REALYTICS_DATABASE_URL or REALYTICS_PG_HOST")
    if (os.environ.get("REALYTICS_STORAGE_BACKEND") or "").lower() not in {
        "postgres",
        "postgresql",
    }:
        errors.append("REALYTICS_STORAGE_BACKEND=postgres")
    if (os.environ.get("REALYTICS_SECRET_BACKEND") or "").lower() not in {
        "aws",
        "aws_secrets_manager",
    }:
        errors.append("REALYTICS_SECRET_BACKEND=aws_secrets_manager")
    if (os.environ.get("REALYTICS_ASYNC_JOB_MODE") or "").lower() != "external_worker":
        errors.append("REALYTICS_ASYNC_JOB_MODE=external_worker")
    if (os.environ.get("REALYTICS_OBJECT_STORAGE") or "").lower() != "s3":
        errors.append("REALYTICS_OBJECT_STORAGE=s3")
    if not os.environ.get("REALYTICS_S3_BUCKET"):
        errors.append("REALYTICS_S3_BUCKET")
    try:
        origins = cors_origins()
        if any(origin in LOCAL_CORS_ORIGINS for origin in origins):
            errors.append("REALYTICS_CORS_ORIGINS must use deployed HTTPS origins")
        if any(not origin.startswith("https://") for origin in origins):
            errors.append("REALYTICS_CORS_ORIGINS must contain HTTPS origins only")
    except RuntimeError as exc:
        errors.append(str(exc))

    if errors:
        unique_errors = list(dict.fromkeys(errors))
        raise RuntimeError(
            "Unsafe REALYTICS hardened security configuration: " + ", ".join(unique_errors)
        )


def _parse_csv(value: str | None) -> list[str]:
    return [item.strip().rstrip("/") for item in (value or "").split(",") if item.strip()]


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None
