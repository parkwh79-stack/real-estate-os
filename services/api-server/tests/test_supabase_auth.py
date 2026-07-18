from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.authz import _identity_assurance
from core.idp import _principal_from_claims


def _claims(**overrides):
    claims = {
        "iss": "https://project.supabase.co/auth/v1",
        "aud": "authenticated",
        "sub": "8d715f09-638a-4f30-b32f-5f7e8bb662bf",
        "email": "reviewer@example.com",
        "role": "authenticated",
        "session_id": "supabase-session",
        "is_anonymous": False,
        "app_metadata": {},
        "user_metadata": {"full_name": "Supabase Reviewer"},
    }
    claims.update(overrides)
    return claims


def test_supabase_uses_server_owned_app_metadata_for_tenant_and_role(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "supabase")
    monkeypatch.delenv("REALYTICS_IDP_PERSONAL_TENANT_FALLBACK", raising=False)
    claims = _claims(
        app_metadata={
            "realytics_tenant_id": "tenant-credit",
            "realytics_role": "reviewer",
            "realytics_principal_type": "human",
        }
    )

    principal = _principal_from_claims(claims)

    assert principal.tenant_id == "tenant-credit"
    assert principal.role == "reviewer"
    assert principal.principal_type == "human"
    assert principal.display_name == "Supabase Reviewer"


def test_supabase_personal_tenant_fallback_is_isolated_by_verified_subject(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "supabase")
    monkeypatch.setenv("REALYTICS_IDP_PERSONAL_TENANT_FALLBACK", "true")
    monkeypatch.setenv("REALYTICS_IDP_PERSONAL_TENANT_ROLE", "analyst")

    principal = _principal_from_claims(_claims())

    assert principal.tenant_id == "supabase-user:8d715f09-638a-4f30-b32f-5f7e8bb662bf"
    assert principal.role == "analyst"
    assert principal.principal_type == "human"


def test_supabase_authenticated_role_never_becomes_realytics_privilege(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "supabase")
    monkeypatch.delenv("REALYTICS_IDP_PERSONAL_TENANT_FALLBACK", raising=False)

    principal = _principal_from_claims(
        _claims(app_metadata={"realytics_tenant_id": "tenant-read-only"})
    )

    assert principal.role == "viewer"


def test_supabase_user_metadata_cannot_supply_authorization_claims(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "supabase")
    monkeypatch.delenv("REALYTICS_IDP_PERSONAL_TENANT_FALLBACK", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        _principal_from_claims(
            _claims(
                user_metadata={
                    "realytics_tenant_id": "attacker-tenant",
                    "realytics_role": "admin",
                }
            )
        )

    assert exc_info.value.detail == {"error": "missing_tenant_claim"}


def test_supabase_aal2_and_object_amr_are_recognized_as_mfa():
    assurance = _identity_assurance(
        {
            "aal": "aal2",
            "amr": [
                {"method": "password", "timestamp": 1},
                {"method": "totp", "timestamp": 2},
            ],
        }
    )

    assert assurance["aal"] == "aal2"
    assert assurance["amr"] == ["password", "totp"]
    assert assurance["mfaVerified"] is True
