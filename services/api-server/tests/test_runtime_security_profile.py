from __future__ import annotations

import pytest

from core import idp, oidc
from core.authz import rbac_mode
from core.runtime_profile import (
    cors_origins,
    legacy_oauth_sessions_enabled,
    local_auth_bootstrap_enabled,
    oauth_mock_callbacks_enabled,
    server_issued_sessions_enabled,
    validate_security_configuration,
)


SECURITY_ENV_KEYS = [
    "REALYTICS_ENVIRONMENT",
    "REALYTICS_CORS_ORIGINS",
    "REALYTICS_RBAC_MODE",
    "REALYTICS_TENANT_ISOLATION_MODE",
    "REALYTICS_IDP_MODE",
    "REALYTICS_IDP_ISSUER",
    "REALYTICS_IDP_AUDIENCE",
    "REALYTICS_IDP_JWKS_URL",
    "REALYTICS_OIDC_ISSUER",
    "REALYTICS_OIDC_AUDIENCE",
    "REALYTICS_OIDC_JWKS_URL",
    "REALYTICS_DATABASE_URL",
    "REALYTICS_PG_HOST",
    "REALYTICS_STORAGE_BACKEND",
    "REALYTICS_SECRET_BACKEND",
    "REALYTICS_ASYNC_JOB_MODE",
    "REALYTICS_OBJECT_STORAGE",
    "REALYTICS_S3_BUCKET",
    "REALYTICS_ENABLE_LOCAL_AUTH",
    "REALYTICS_ENABLE_OAUTH_MOCKS",
    "REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS",
    "REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY",
    "REALYTICS_IDP_PERSONAL_TENANT_FALLBACK",
]


def _clear_security_env(monkeypatch):
    for key in SECURITY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_development_profile_uses_explicit_local_origins_without_wildcard(monkeypatch):
    _clear_security_env(monkeypatch)

    assert cors_origins() == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert "*" not in cors_origins()
    assert rbac_mode() == "permissive"
    assert idp.tenant_isolation_mode() == "permissive"


def test_hardened_profile_fails_fast_when_security_configuration_is_missing(monkeypatch):
    _clear_security_env(monkeypatch)
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="Unsafe REALYTICS hardened security configuration"):
        validate_security_configuration()


def test_hardened_profile_accepts_strict_oidc_and_https_origin(monkeypatch):
    _clear_security_env(monkeypatch)
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    monkeypatch.setenv("REALYTICS_IDP_MODE", "oidc")
    monkeypatch.setenv("REALYTICS_IDP_ISSUER", "https://idp.example")
    monkeypatch.setenv("REALYTICS_IDP_AUDIENCE", "realytics-api")
    monkeypatch.setenv("REALYTICS_IDP_JWKS_URL", "https://idp.example/.well-known/jwks.json")
    monkeypatch.setenv("REALYTICS_CORS_ORIGINS", "https://app.realytics.example")
    monkeypatch.setenv("REALYTICS_DATABASE_URL", "postgresql://realytics@db/realytics")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "aws_secrets_manager")
    monkeypatch.setenv("REALYTICS_ASYNC_JOB_MODE", "external_worker")
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "s3")
    monkeypatch.setenv("REALYTICS_S3_BUCKET", "realytics-documents")

    validate_security_configuration()
    assert rbac_mode() == "strict"
    assert idp.tenant_isolation_mode() == "strict"
    assert cors_origins() == ["https://app.realytics.example"]


def test_hardened_profile_cannot_enable_legacy_auth_or_oauth_mocks(monkeypatch):
    _clear_security_env(monkeypatch)
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "staging")
    monkeypatch.setenv("REALYTICS_ENABLE_LOCAL_AUTH", "true")
    monkeypatch.setenv("REALYTICS_ENABLE_OAUTH_MOCKS", "true")
    monkeypatch.setenv("REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS", "true")

    assert local_auth_bootstrap_enabled() is False
    assert server_issued_sessions_enabled() is False
    assert oauth_mock_callbacks_enabled() is False
    assert legacy_oauth_sessions_enabled() is False
    with pytest.raises(RuntimeError) as exc_info:
        validate_security_configuration()

    message = str(exc_info.value)
    assert "REALYTICS_ENABLE_LOCAL_AUTH=false" in message
    assert "REALYTICS_ENABLE_OAUTH_MOCKS=false" in message
    assert "REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS=false" in message


def test_oidc_mode_verifies_token_before_building_principal(monkeypatch):
    _clear_security_env(monkeypatch)
    monkeypatch.setenv("REALYTICS_IDP_MODE", "oidc")
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "test-oidc")
    monkeypatch.setattr(
        oidc,
        "verify_oidc_token",
        lambda token: {
            "sub": "oidc-user",
            "tenant_id": "tenant-oidc",
            "role": "reviewer",
            "name": "OIDC Reviewer",
            "jti": "oidc-token-1",
        },
    )

    principal = idp.principal_from_bearer_token("header.payload.signature")

    assert principal.user_id == "oidc-user"
    assert principal.tenant_id == "tenant-oidc"
    assert principal.provider == "test-oidc"
    assert principal.token_id == "oidc-token-1"


def test_oidc_config_accepts_canonical_idp_environment_names(monkeypatch):
    _clear_security_env(monkeypatch)
    monkeypatch.setenv("REALYTICS_IDP_ISSUER", "https://idp.example")
    monkeypatch.setenv("REALYTICS_IDP_AUDIENCE", "realytics-api")
    monkeypatch.setenv("REALYTICS_IDP_JWKS_URL", "https://idp.example/jwks")

    assert oidc.oidc_config() == {
        "issuer": "https://idp.example",
        "audience": "realytics-api",
        "jwksUrl": "https://idp.example/jwks",
    }
