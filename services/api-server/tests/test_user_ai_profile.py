from datetime import datetime, timedelta, timezone
import json
import sys
from types import SimpleNamespace

import pytest

import core.secret_vault as secret_vault
from core.secret_vault import (
    credential_access_status,
    ingest_user_credential,
    resolve_user_credential,
    revoke_credential,
)
from use_cases.user_ai_profile import build_user_ai_context, safe_user_ai_context_summary, system_ai_fallback_policy
from storage import upsert_user_ai_profile


def test_user_ai_context_uses_system_gemini_fallback_without_profile(monkeypatch):
    monkeypatch.delenv("REALYTICS_AI_PROVIDER", raising=False)
    monkeypatch.delenv("REALYTICS_FREE_AI_PROVIDER", raising=False)
    monkeypatch.delenv("REALYTICS_FREE_AI_MODEL", raising=False)

    context = build_user_ai_context(user_id="u-no-profile", tenant_id="t-1")
    summary = safe_user_ai_context_summary(context)

    assert summary["provider"] == "gemini"
    assert summary["model"] == "gemini-2.5-flash"
    assert summary["selectionSource"] == "system_free_fallback"


def test_user_ai_context_prefers_user_byok_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "ai-profile.sqlite3"))
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    credential = ingest_user_credential(
        user_id="u-byok",
        tenant_id="t-1",
        session_id="session-byok-test",
        provider="groq",
        secret="secret-user-key",
    )
    profile = upsert_user_ai_profile(
        {
            "userId": "u-byok",
            "tenantId": "t-1",
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "credentialRef": credential["credentialRef"],
            "credentialBackend": credential["backend"],
            "apiKeyMasked": credential["masked"],
        }
    )
    context = build_user_ai_context(
        user_id="u-byok",
        tenant_id="t-1",
        session_id="session-byok-test",
    )
    summary = safe_user_ai_context_summary(context)

    assert profile["apiKeyConfigured"] is True
    assert "apiKeyEncrypted" not in profile
    assert summary["provider"] == "groq"
    assert summary["directModelCall"] is True
    assert summary["requiresApiKey"] is None
    assert summary["selectionSource"] == "user_ai_profile"
    assert revoke_credential(credential["credentialRef"]) is True


def test_session_secret_requires_exact_user_tenant_and_session(monkeypatch):
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.setenv("REALYTICS_AI_CREDENTIAL_TTL_SECONDS", "3600")
    credential = ingest_user_credential(
        user_id="u-boundary",
        tenant_id="tenant-boundary",
        session_id="provider-session-a",
        provider="gemini",
        secret="boundary-secret-value",
    )
    reference = credential["credentialRef"]

    assert "provider-session-a" not in reference
    assert resolve_user_credential(
        reference,
        user_id="u-boundary",
        tenant_id="tenant-boundary",
        session_id="provider-session-a",
    ) == "boundary-secret-value"
    assert resolve_user_credential(
        reference,
        user_id="u-other",
        tenant_id="tenant-boundary",
        session_id="provider-session-a",
    ) is None
    assert resolve_user_credential(
        reference,
        user_id="u-boundary",
        tenant_id="tenant-other",
        session_id="provider-session-a",
    ) is None
    assert resolve_user_credential(
        reference,
        user_id="u-boundary",
        tenant_id="tenant-boundary",
        session_id="provider-session-b",
    ) is None
    assert resolve_user_credential(
        reference,
        user_id="u-boundary",
        tenant_id="tenant-boundary",
    ) is None
    assert revoke_credential(reference) is True


def test_expired_session_secret_reads_fail_closed_without_deleting(monkeypatch):
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.setenv("REALYTICS_AI_CREDENTIAL_TTL_SECONDS", "60")
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(secret_vault, "_utc_now", lambda: now)
    credential = ingest_user_credential(
        user_id="u-expiry",
        tenant_id="tenant-expiry",
        session_id="provider-session-expiry",
        provider="gemini",
        secret="expiring-secret-value",
    )
    reference = credential["credentialRef"]

    monkeypatch.setattr(
        secret_vault,
        "_utc_now",
        lambda: now + timedelta(seconds=61),
    )
    assert credential_access_status(
        reference,
        user_id="u-expiry",
        tenant_id="tenant-expiry",
        session_id="provider-session-expiry",
    ) == "expired"
    assert resolve_user_credential(
        reference,
        user_id="u-expiry",
        tenant_id="tenant-expiry",
        session_id="provider-session-expiry",
    ) is None
    assert credential_access_status(
        reference,
        user_id="u-expiry",
        tenant_id="tenant-expiry",
        session_id="provider-session-expiry",
    ) == "expired"
    assert revoke_credential(reference) is True


@pytest.mark.parametrize("ttl", ["0", "86401", "not-an-integer"])
def test_session_secret_rejects_unsafe_ttl(monkeypatch, ttl):
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.setenv("REALYTICS_AI_CREDENTIAL_TTL_SECONDS", ttl)

    with pytest.raises(RuntimeError, match="REALYTICS_AI_CREDENTIAL_TTL_SECONDS"):
        ingest_user_credential(
            user_id="u-invalid-ttl",
            tenant_id="tenant-invalid-ttl",
            session_id="provider-session-invalid-ttl",
            provider="gemini",
            secret="never-stored",
        )


def test_user_ai_context_never_resolves_byok_from_a_different_session(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "ai-profile-session.sqlite3"))
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    credential = ingest_user_credential(
        user_id="u-session-profile",
        tenant_id="tenant-session-profile",
        session_id="provider-session-owner",
        provider="groq",
        secret="session-owned-secret",
    )
    upsert_user_ai_profile(
        {
            "userId": "u-session-profile",
            "tenantId": "tenant-session-profile",
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "credentialRef": credential["credentialRef"],
            "credentialBackend": credential["backend"],
            "apiKeyMasked": credential["masked"],
        }
    )

    owner = build_user_ai_context(
        user_id="u-session-profile",
        tenant_id="tenant-session-profile",
        session_id="provider-session-owner",
    )
    other = build_user_ai_context(
        user_id="u-session-profile",
        tenant_id="tenant-session-profile",
        session_id="provider-session-other",
    )

    assert safe_user_ai_context_summary(owner)["selectionSource"] == "user_ai_profile"
    assert (
        safe_user_ai_context_summary(other)["selectionSource"]
        == "user_ai_profile_without_key_system_fallback"
    )
    assert revoke_credential(credential["credentialRef"]) is True


def test_aws_secret_envelope_enforces_session_boundary_and_expiry(monkeypatch):
    class ResourceNotFoundException(Exception):
        pass

    class FakeSecretsManager:
        exceptions = SimpleNamespace(ResourceNotFoundException=ResourceNotFoundException)

        def __init__(self):
            self.values = {}
            self.deleted = []

        def create_secret(self, *, Name, SecretString):
            self.values[Name] = SecretString

        def get_secret_value(self, *, SecretId):
            if SecretId not in self.values:
                raise ResourceNotFoundException(SecretId)
            return {"SecretString": self.values[SecretId]}

        def delete_secret(self, *, SecretId, ForceDeleteWithoutRecovery):
            assert ForceDeleteWithoutRecovery is True
            self.deleted.append(SecretId)
            self.values.pop(SecretId, None)

    client = FakeSecretsManager()
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda service, region_name=None: client),
    )
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "aws_secrets_manager")
    monkeypatch.setenv("REALYTICS_AI_CREDENTIAL_TTL_SECONDS", "60")
    now = datetime(2026, 7, 28, 3, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(secret_vault, "_utc_now", lambda: now)
    credential = ingest_user_credential(
        user_id="u-aws",
        tenant_id="tenant-aws",
        session_id="provider-session-aws",
        provider="gemini",
        secret="aws-envelope-secret",
    )
    stored = json.loads(next(iter(client.values.values())))

    assert stored["schemaVersion"] == "realytics-session-secret-v1"
    assert stored["userId"] == "u-aws"
    assert stored["tenantId"] == "tenant-aws"
    assert stored["sessionId"] == "provider-session-aws"
    assert resolve_user_credential(
        credential["credentialRef"],
        user_id="u-aws",
        tenant_id="tenant-aws",
        session_id="provider-session-aws",
    ) == "aws-envelope-secret"
    assert resolve_user_credential(
        credential["credentialRef"],
        user_id="u-aws",
        tenant_id="tenant-aws",
        session_id="provider-session-other",
    ) is None

    monkeypatch.setattr(
        secret_vault,
        "_utc_now",
        lambda: now + timedelta(seconds=61),
    )
    assert resolve_user_credential(
        credential["credentialRef"],
        user_id="u-aws",
        tenant_id="tenant-aws",
        session_id="provider-session-aws",
    ) is None
    assert len(client.values) == 1 and client.deleted == []
    assert revoke_credential(credential["credentialRef"]) is True
    assert client.values == {} and len(client.deleted) == 1


def test_user_ai_context_uses_system_fallback_when_profile_has_no_key(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "ai-profile-no-key.sqlite3"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    upsert_user_ai_profile(
        {
            "userId": "u-profile-no-key",
            "tenantId": "t-1",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "allowSystemFallback": True,
        }
    )
    context = build_user_ai_context(user_id="u-profile-no-key", tenant_id="t-1")
    summary = safe_user_ai_context_summary(context)

    assert summary["provider"] == "gemini"
    assert summary["selectionSource"] == "user_ai_profile_without_key_system_fallback"


def test_user_ai_context_rejects_inline_api_key_override():
    with pytest.raises(ValueError, match="Inline AI credentials"):
        build_user_ai_context(
            user_id="u-inline",
            tenant_id="t-1",
            override={"provider": "openai", "apiKey": "must-not-pass-through"},
        )


def test_configured_system_default_overrides_the_legacy_free_profile(monkeypatch):
    monkeypatch.setenv("REALYTICS_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "configured-openai-model")
    monkeypatch.setenv("REALYTICS_FREE_AI_PROVIDER", "gemini")
    monkeypatch.setenv("REALYTICS_FREE_AI_MODEL", "old-free-model")
    context = build_user_ai_context(user_id="system-default", tenant_id="system-default-tenant", use_legacy_profile=False)
    assert context["provider"] == "openai" and context["model"] == "configured-openai-model"
    assert system_ai_fallback_policy()["model"] == "configured-openai-model"


def test_missing_personal_key_uses_actual_system_default_instead_of_stale_profile(monkeypatch):
    import use_cases.user_ai_profile as profiles
    monkeypatch.setenv("REALYTICS_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "current-system-model")
    monkeypatch.setattr(profiles, "get_user_ai_profile", lambda *_args, **_kwargs: {
        "status": "active", "provider": "groq", "model": "stale-personal-model", "allowSystemFallback": True,
    })
    monkeypatch.setattr(profiles, "decrypt_user_ai_api_key", lambda *_args, **_kwargs: None)
    context = build_user_ai_context(user_id="missing-key-default", tenant_id="system-default-tenant")
    assert context["provider"] == "openai" and context["model"] == "current-system-model"
    assert context["selection_source"] == "user_ai_profile_without_key_system_fallback"


def test_disabled_system_fallback_blocks_native_calls_even_with_matching_and_other_global_keys(monkeypatch):
    import use_cases.user_ai_profile as profiles
    from agents.ai_connection import AIConnection
    monkeypatch.setenv("OPENAI_API_KEY", "global-openai-fixture")
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-fixture")
    monkeypatch.setattr(profiles, "get_user_ai_profile", lambda *_args, **_kwargs: {
        "status": "active", "provider": "openai", "model": "chosen-model", "allowSystemFallback": False,
    })
    monkeypatch.setattr(profiles, "decrypt_user_ai_api_key", lambda *_args, **_kwargs: None)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("native provider must not execute")
    monkeypatch.setattr(AIConnection, "_extract_for_context", forbidden)
    context = build_user_ai_context(user_id="no-personal-key", tenant_id="fallback-disabled-tenant")
    assert context["direct_model_call"] is False
    result = AIConnection().extract_json("synthetic", system="synthetic", ai_context=context)
    assert result.data is None and result.error
    assert "global-openai-fixture" not in json.dumps(result.meta)


def test_valid_personal_key_with_fallback_disabled_never_switches_provider_after_failure(monkeypatch):
    import use_cases.user_ai_profile as profiles
    from agents.ai_connection import AIConnection, AIExtractionResult
    monkeypatch.setenv("OPENAI_API_KEY", "global-openai-fixture")
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-fixture")
    monkeypatch.setattr(profiles, "get_user_ai_profile", lambda *_args, **_kwargs: {
        "status": "active", "provider": "groq", "model": "personal-model", "allowSystemFallback": False,
    })
    monkeypatch.setattr(profiles, "decrypt_user_ai_api_key", lambda *_args, **_kwargs: "personal-key-fixture")
    calls = []
    def failed(_self, _prompt, _system, context):
        calls.append(context["provider"])
        assert context["api_key"] == "personal-key-fixture"
        context["provider_call_attempted"] = True
        return AIExtractionResult(data=None, meta={}, error="synthetic provider failure")
    monkeypatch.setattr(AIConnection, "_extract_for_context", failed)
    context = build_user_ai_context(user_id="personal-only", tenant_id="personal-only-tenant")
    result = AIConnection().extract_json("synthetic", system="synthetic", ai_context=context)
    assert result.data is None and calls == ["groq"]
    with pytest.raises(ValueError, match="공급자를 요청에서 변경"):
        build_user_ai_context(user_id="personal-only", tenant_id="personal-only-tenant", override={"provider": "openai"})


def test_disabled_fallback_keeps_existing_local_privacy_hard_filter(monkeypatch):
    import use_cases.user_ai_profile as profiles
    from agents.ai_connection import AIConnection
    monkeypatch.setattr(profiles, "get_user_ai_profile", lambda *_args, **_kwargs: {
        "status": "active", "provider": "groq", "model": "personal-model", "allowSystemFallback": False,
    })
    monkeypatch.setattr(profiles, "decrypt_user_ai_api_key", lambda *_args, **_kwargs: "personal-key-fixture")
    context = build_user_ai_context(user_id="private-personal", tenant_id="private-personal-tenant", override={"local_only": True})
    monkeypatch.setattr(AIConnection, "_extract_for_context", lambda *_a, **_k: pytest.fail("privacy gate must block the call"))
    assert AIConnection().extract_json("synthetic", system="synthetic", ai_context=context).data is None
