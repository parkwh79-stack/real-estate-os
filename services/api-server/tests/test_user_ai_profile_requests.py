"""Explicit personal execution must never borrow another actor or system key."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from starlette.requests import Request
import pytest

from core import secret_vault
from db.repositories.user_ai_profile_repository import UserAIProfileUnavailable
from routers import projects
from use_cases import user_ai_profile


def _request(source=None):
    headers = [(b"x-ai-selection-source", source.encode())] if source else []
    return Request({"type": "http", "method": "POST", "path": "/projects/p/ai/connection/resolve", "headers": headers})


@pytest.fixture
def actor(monkeypatch):
    state = {"userId": "actor-a", "tenantId": "tenant-a", "sessionId": "session-a"}
    monkeypatch.setattr(projects, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(projects, "identity_context_from_request", lambda _request: {
        "authenticated": True, "user": {key: state[key] for key in ("userId", "tenantId")}})
    monkeypatch.setattr(projects, "credential_session_binding_from_request", lambda *_args: deepcopy(state) if state.get("sessionId") else None)
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.setenv("REALYTICS_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "system-fixture")
    monkeypatch.setenv("OPENAI_API_KEY", "system-key-fixture")
    return state


def test_ordinary_hosted_ai_uses_system_without_querying_unapproved_profile(actor, monkeypatch):
    monkeypatch.setattr(user_ai_profile, "get_user_ai_profile", lambda *_a, **_kw: pytest.fail("system mode must not query personal profiles"))
    for source in (None, "system"):
        context = projects.ai_context_from_request(_request(source))
        assert context["provider"] == "openai" and context["model"] == "system-fixture"
        assert context["selection_source"] == "system_free_fallback"


def test_explicit_personal_authority_failure_returns_503_without_system_fallback(actor, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise UserAIProfileUnavailable("private fixture database failure")
    monkeypatch.setattr(user_ai_profile, "get_user_ai_profile", unavailable)
    with pytest.raises(HTTPException) as caught:
        projects.ai_context_from_request(_request("user_ai_profile"))
    assert caught.value.status_code == 503
    assert caught.value.detail["error"] == "ai_profile_authority_unavailable"
    assert "private fixture" not in str(caught.value.detail)


@pytest.mark.parametrize("via_header", [True, False])
def test_personal_execution_binds_verified_actor_and_ignores_payload_identity(actor, monkeypatch, via_header):
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(secret_vault, "_utc_now", lambda: now)
    key = secret_vault.ingest_user_credential(user_id="actor-a", tenant_id="tenant-a", session_id="session-a", provider="groq", secret="personal-fixture")
    rows = {("actor-a", "tenant-a"): {"provider": "groq", "model": "personal-fixture-model", "status": "active", "credentialRef": key["credentialRef"], "allowSystemFallback": False}}
    reads = []
    def read(user_id, *, tenant_id, include_secret):
        reads.append((user_id, tenant_id))
        return deepcopy(rows.get((user_id, tenant_id)))
    monkeypatch.setattr(user_ai_profile, "get_user_ai_profile", read)
    payload = {"user_id": "actor-b", "tenant_id": "tenant-b", "session_id": "session-b"}
    if not via_header:
        payload["selection_source"] = "user_ai_profile"
    request = _request("user_ai_profile" if via_header else None)
    context = projects.ai_context_from_request(request, explicit_user_id="actor-b", payload_ai_context=payload)
    assert reads == [("actor-a", "tenant-a")]
    assert context["user_id"] == "actor-a" and context["api_key"] == "personal-fixture"
    assert context["provider"] == "groq" and context["allow_system_fallback"] is False

    actor["sessionId"] = "different-session"
    blocked = projects.ai_context_from_request(request, payload_ai_context=payload)
    assert blocked["direct_model_call"] is False and blocked["requires_api_key"] == "personal_api_key"
    monkeypatch.setattr(secret_vault, "_utc_now", lambda: now + timedelta(hours=2))
    actor["sessionId"] = "session-a"
    expired = projects.ai_context_from_request(request, payload_ai_context=payload)
    assert expired["direct_model_call"] is False
    for user_id, tenant_id in (("actor-b", "tenant-a"), ("actor-a", "tenant-b")):
        actor.update(userId=user_id, tenantId=tenant_id)
        with pytest.raises(HTTPException) as caught:
            projects.ai_context_from_request(request, payload_ai_context=payload)
        assert caught.value.status_code == 409
    assert secret_vault.revoke_credential(key["credentialRef"]) is True


def test_personal_execution_requires_verified_session_before_any_profile_read(actor, monkeypatch):
    actor["sessionId"] = ""
    monkeypatch.setattr(user_ai_profile, "get_user_ai_profile", lambda *_a, **_kw: pytest.fail("unbound requests must not query profiles"))
    with pytest.raises(HTTPException) as caught:
        projects.ai_context_from_request(_request("user_ai_profile"), payload_ai_context={"session_id": "spoofed"})
    assert caught.value.status_code == 400 and caught.value.detail["error"] == "credential_session_binding_required"


def test_personal_provider_hint_cannot_substitute_a_global_key(actor, monkeypatch):
    key = secret_vault.ingest_user_credential(user_id="actor-a", tenant_id="tenant-a", session_id="session-a", provider="groq", secret="personal-provider-fixture")
    monkeypatch.setattr(user_ai_profile, "get_user_ai_profile", lambda *_a, **_kw: {
        "provider": "groq", "status": "active", "credentialRef": key["credentialRef"], "allowSystemFallback": True})
    context = projects.ai_context_from_request(_request("user_ai_profile"), payload_ai_context={"provider": "groq"})
    assert context["api_key"] == "personal-provider-fixture"
    with pytest.raises(HTTPException) as caught:
        projects.ai_context_from_request(_request("user_ai_profile"), payload_ai_context={"provider": "openai"})
    assert caught.value.status_code == 400
    assert secret_vault.revoke_credential(key["credentialRef"]) is True


def test_personal_vault_outage_cannot_be_treated_as_missing_key_with_system_fallback(actor, monkeypatch):
    monkeypatch.setattr(user_ai_profile, "get_user_ai_profile", lambda *_a, **_kw: {
        "provider": "groq", "status": "active", "credentialRef": "vault-fixture", "allowSystemFallback": True})
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("sensitive provider detail")
    monkeypatch.setattr(user_ai_profile, "resolve_user_credential", unavailable)
    with pytest.raises(HTTPException) as caught:
        projects.ai_context_from_request(_request("user_ai_profile"))
    assert caught.value.status_code == 503 and "sensitive provider" not in str(caught.value.detail)
