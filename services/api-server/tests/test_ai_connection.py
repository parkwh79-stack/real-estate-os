from agents.ai_connection import resolve_ai_context, OPENAI_COMPATIBLE_PROVIDERS
from agents.parser_agent import ParserAgent


def test_resolve_ai_context_links_user_id_for_claude_desktop(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("REALYTICS_AI_PROVIDER", "claude_desktop")

    context = resolve_ai_context({"user_id": "user-123", "host": "claude_desktop"})

    assert context["provider"] == "claude_desktop"
    assert context["runtime_provider"] == "mcp_host_llm"
    assert context["user_id"] == "user-123"
    assert context["requires_api_key"] is None
    assert context["direct_model_call"] is False
    assert context["server_side_extraction"] is False
    assert context["host_llm_available"] is True


def test_parser_returns_host_llm_request_for_claude_desktop(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    data = ParserAgent(
        {
            "provider": "claude_desktop",
            "host": "claude_desktop",
            "user_id": "mcp-user-1",
        }
    ).extract_project_data("Short business plan text")

    assert data["_meta"]["parsing_source"] == "host_llm_required"
    assert data["_meta"]["ai_connection"]["provider"] == "claude_desktop"
    assert data["_meta"]["ai_connection"]["user_id"] == "mcp-user-1"
    assert "Short business plan text" in data["host_llm_extraction"]["document_text"]
    assert data["host_llm_extraction"]["expected_output"] == "project_data_json"


def test_parser_openai_without_key_returns_explicit_mock(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    data = ParserAgent({"provider": "openai", "user_id": "api-user-1"}).extract_project_data(
        "Short business plan text"
    )

    assert data["_meta"]["parsing_source"] == "mock"
    assert data["_meta"]["ai_connection"]["provider"] == "openai"
    assert data["_meta"]["ai_connection"]["user_id"] == "api-user-1"


def test_gemini_provider_resolves_openai_compatible_base_url(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    context = resolve_ai_context({"provider": "gemini"})
    assert context["provider"] == "gemini"
    assert context["runtime_provider"] == "openai_compatible"
    assert context["base_url"] == OPENAI_COMPATIBLE_PROVIDERS["gemini"]["base_url"]
    assert context["model"] == "gemini-2.5-flash"
    assert context["direct_model_call"] is True
    assert context["requires_api_key"] is None


def test_groq_provider_requires_key_when_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    context = resolve_ai_context({"provider": "groq"})
    assert context["provider"] == "groq"
    assert context["direct_model_call"] is False
    assert context["requires_api_key"] == "GROQ_API_KEY"


def test_provider_alias_and_model_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    context = resolve_ai_context({"provider": "openrouter", "model": "deepseek/deepseek-r1"})
    assert context["provider"] == "openrouter"
    assert context["model"] == "deepseek/deepseek-r1"
    assert context["supports_json_mode"] is False


def test_local_self_hosted_uses_base_url_without_key(monkeypatch):
    monkeypatch.setenv("REALYTICS_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("REALYTICS_LLM_MODEL", "qwen2.5:14b")
    monkeypatch.delenv("REALYTICS_LLM_API_KEY", raising=False)
    context = resolve_ai_context({"provider": "ollama"})
    assert context["provider"] == "local"  # alias normalised
    assert context["is_local"] is True
    assert context["base_url"] == "http://localhost:11434/v1"
    assert context["model"] == "qwen2.5:14b"
    assert context["direct_model_call"] is True  # usable without an API key
    assert context["requires_api_key"] is None


def test_local_without_base_url_requires_config(monkeypatch):
    monkeypatch.delenv("REALYTICS_LLM_BASE_URL", raising=False)
    context = resolve_ai_context({"provider": "local"})
    assert context["direct_model_call"] is False
    assert context["requires_api_key"] == "REALYTICS_LLM_BASE_URL"


def test_anthropic_default_model_is_current(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    context = resolve_ai_context({"provider": "anthropic"})
    assert context["model"] == "claude-sonnet-4-6"
