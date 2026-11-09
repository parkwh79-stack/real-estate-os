from __future__ import annotations

from fastapi.testclient import TestClient

from core import authz, oidc
from db.repositories.identity_membership_repository import (
    IdentityPrincipalRecord,
    PrincipalMembershipDirectory,
    TenantMembershipRecord,
)
from main import app


def test_local_auth_bootstrap_requires_explicit_non_hardened_opt_in(monkeypatch):
    monkeypatch.delenv("REALYTICS_ENABLE_LOCAL_AUTH", raising=False)
    client = TestClient(app)

    requests = (
        client.post("/auth/tenants", json={"tenantId": "blocked", "name": "Blocked"}),
        client.post(
            "/auth/users",
            json={"userId": "blocked", "tenantId": "blocked", "role": "admin"},
        ),
        client.get("/auth/users"),
        client.post(
            "/auth/session",
            json={"userId": "blocked", "tenantId": "blocked", "provider": "local"},
        ),
    )

    assert {response.status_code for response in requests} == {403}
    assert {
        response.json()["detail"]["error"] for response in requests
    } == {"local_auth_disabled"}


def test_hardened_profile_ignores_local_auth_opt_in_and_raw_user_header(monkeypatch):
    client = TestClient(app)
    tenant_id = "auth-boundary-tenant"
    user_id = "auth-boundary-user"
    assert client.post(
        "/auth/tenants",
        json={"tenantId": tenant_id, "name": "Auth Boundary"},
    ).status_code == 200
    assert client.post(
        "/auth/users",
        json={
            "userId": user_id,
            "tenantId": tenant_id,
            "displayName": "Auth Boundary User",
            "role": "reviewer",
        },
    ).status_code == 200

    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv("REALYTICS_ENABLE_LOCAL_AUTH", "true")
    monkeypatch.setenv("REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS", "true")

    blocked_bootstrap = client.post(
        "/auth/session",
        json={"userId": user_id, "tenantId": tenant_id, "provider": "local"},
    )
    blocked_header = client.get(
        "/auth/me",
        headers={"x-realytics-user-id": user_id},
    )
    blocked_session = client.get(
        "/auth/me",
        headers={"x-realytics-session-id": "opaque-session"},
    )

    assert blocked_bootstrap.status_code == 403
    assert blocked_bootstrap.json()["detail"]["error"] == "local_auth_disabled"
    assert blocked_header.status_code == 401
    assert (
        blocked_header.json()["detail"]["error"]
        == "raw_identity_header_disabled"
    )
    assert blocked_session.status_code == 401
    assert (
        blocked_session.json()["detail"]["error"]
        == "legacy_session_disabled"
    )


def test_hardened_profile_preserves_verified_oidc_bearer_identity(monkeypatch):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv("REALYTICS_IDP_MODE", "oidc")
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "supabase")
    verification_calls = []

    def _verify_oidc_token(token):
        verification_calls.append(token)
        return {
            "iss": "https://project.supabase.co/auth/v1",
            "aud": "authenticated",
            "sub": "b90129f1-1f9f-4ca8-8e69-54d97e665889",
            "email": "verified@example.com",
            "session_id": "verified-supabase-session",
            "is_anonymous": False,
            "app_metadata": {
                "realytics_tenant_id": "attacker-selected-tenant",
                "realytics_role": "admin",
            },
            "user_metadata": {"full_name": "Verified Reviewer"},
        }

    monkeypatch.setattr(oidc, "verify_oidc_token", _verify_oidc_token)

    class _MembershipRepository:
        def load_directory(self, *, issuer: str, subject: str):
            assert issuer == "https://project.supabase.co/auth/v1"
            assert subject == "b90129f1-1f9f-4ca8-8e69-54d97e665889"
            return PrincipalMembershipDirectory(
                principal=IdentityPrincipalRecord(
                    issuer=issuer,
                    subject=subject,
                    principal_type="human",
                    status="active",
                    email="verified@example.com",
                    display_name="Verified Reviewer",
                    legacy_user_id="verified-user",
                ),
                memberships=(
                    TenantMembershipRecord(
                        tenant_id="verified-tenant",
                        tenant_status="active",
                        role="reviewer",
                        status="active",
                        membership_version=2,
                    ),
                ),
            )

    monkeypatch.setattr(
        authz,
        "identity_membership_repository",
        lambda: _MembershipRepository(),
    )
    client = TestClient(app)

    response = client.get(
        "/auth/me",
        headers={"authorization": "Bearer header.payload.signature"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["authSource"] == "bearer_token"
    assert body["user"]["tenantId"] == "verified-tenant"
    assert body["user"]["role"] == "reviewer"
    assert body["user"]["metadata"]["principalType"] == "human"
    assert verification_calls == ["header.payload.signature"]


def test_oidc_jwks_outage_returns_typed_503_without_middleware_reverification(
    monkeypatch,
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv("REALYTICS_IDP_MODE", "oidc")
    verification_calls = []

    def _unavailable(token):
        verification_calls.append(token)
        raise oidc.OidcVerifierUnavailable("OIDC JWKS authority is unavailable.")

    monkeypatch.setattr(oidc, "verify_oidc_token", _unavailable)
    client = TestClient(app)

    response = client.get(
        "/auth/me",
        headers={"authorization": "Bearer header.payload.signature"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "oidc_verifier_unavailable"
    assert verification_calls == ["header.payload.signature"]


def test_hardened_social_login_mocks_ignore_opt_in_and_fail_closed(monkeypatch):
    for key in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
        "KAKAO_REST_API_KEY",
        "KAKAO_CLIENT_SECRET",
        "KAKAO_REDIRECT_URI",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "staging")
    monkeypatch.setenv("REALYTICS_ENABLE_OAUTH_MOCKS", "true")
    client = TestClient(app)

    google_authorize = client.post(
        "/integrations/google/login/authorize-url",
        json={"redirectPath": "/start"},
    )
    google_callback = client.get(
        "/integrations/google/callback",
        follow_redirects=False,
    )
    kakao_authorize = client.post(
        "/integrations/kakao/login/authorize-url",
        json={"redirectPath": "/start"},
    )
    kakao_callback = client.get(
        "/integrations/kakao/mock-callback",
        follow_redirects=False,
    )

    assert google_authorize.status_code == 503
    assert google_authorize.json()["detail"]["error"] == "google_oauth_not_configured"
    assert google_callback.status_code == 403
    assert google_callback.json()["detail"]["error"] == "google_oauth_mock_disabled"
    assert kakao_authorize.status_code == 503
    assert kakao_authorize.json()["detail"]["error"] == "kakao_oauth_not_configured"
    assert kakao_callback.status_code == 403
    assert kakao_callback.json()["detail"]["error"] == "kakao_oauth_mock_disabled"


def test_configured_google_callback_requires_authorization_code(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_CLIENT_ID",
        "client-id.apps.googleusercontent.com",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://realytics.example/integrations/google/callback",
    )
    client = TestClient(app)

    response = client.get(
        "/integrations/google/callback",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]["error"]
        == "missing_google_authorization_code"
    )


def test_hardened_profile_blocks_api_owned_social_oauth_sessions(monkeypatch):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv("REALYTICS_ENABLE_LOCAL_AUTH", "true")
    monkeypatch.setenv("REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS", "true")
    monkeypatch.setenv(
        "GOOGLE_CLIENT_ID",
        "client-id.apps.googleusercontent.com",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://realytics.example/integrations/google/callback",
    )
    client = TestClient(app)

    google_authorize = client.post(
        "/integrations/google/login/authorize-url",
        json={"redirectPath": "/start"},
    )
    google_callback = client.get(
        "/integrations/google/callback?code=untrusted",
        follow_redirects=False,
    )
    kakao_callback = client.get(
        "/auth/kakao/callback?code=untrusted",
        follow_redirects=False,
    )

    for response in (google_authorize, google_callback, kakao_callback):
        assert response.status_code == 403
        assert (
            response.json()["detail"]["error"]
            == "legacy_oauth_session_disabled"
        )
