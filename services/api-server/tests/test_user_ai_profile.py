from use_cases.user_ai_profile import build_user_ai_context, safe_user_ai_context_summary
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
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    profile = upsert_user_ai_profile(
        {
            "userId": "u-byok",
            "tenantId": "t-1",
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "apiKey": "secret-user-key",
        }
    )
    context = build_user_ai_context(user_id="u-byok", tenant_id="t-1")
    summary = safe_user_ai_context_summary(context)

    assert profile["apiKeyConfigured"] is True
    assert "apiKeyEncrypted" not in profile
    assert summary["provider"] == "groq"
    assert summary["directModelCall"] is True
    assert summary["requiresApiKey"] is None
    assert summary["selectionSource"] == "user_ai_profile"


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
