from __future__ import annotations

from agents.ai_router import describe_selection, select_ai_context

_PROVIDER_ENV = [
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "TOGETHER_API_KEY",
    "REALYTICS_LLM_BASE_URL", "REALYTICS_LLM_API_KEY", "REALYTICS_AI_PROVIDER",
]


def _clear(monkeypatch):
    for key in _PROVIDER_ENV:
        monkeypatch.delenv(key, raising=False)


def test_no_provider_configured_returns_none(monkeypatch):
    _clear(monkeypatch)
    assert select_ai_context() is None
    assert describe_selection(None)["selected"] is False


def test_openai_key_selects_openai(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ctx = select_ai_context()
    assert ctx is not None
    assert ctx["provider"] == "openai"
    assert ctx["autoSelected"] is True
    summary = describe_selection(ctx)
    assert summary["selected"] is True and summary["provider"] == "openai"
    # no secret leaks into the summary
    assert "sk-test" not in str(summary)


def test_local_endpoint_is_preferred_for_residency(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("REALYTICS_LLM_BASE_URL", "http://localhost:11434/v1")
    ctx = select_ai_context()
    assert ctx is not None
    assert ctx["provider"] == "local"
    assert ctx.get("is_local") is True


def test_explicit_preferred_wins(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    ctx = select_ai_context(preferred="groq")
    assert ctx is not None and ctx["provider"] == "groq"
