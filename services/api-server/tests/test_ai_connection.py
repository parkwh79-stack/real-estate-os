from types import SimpleNamespace

from agents.ai_audit import build_ai_brain_used_audit_event
from agents.ai_connection import (
    AIConnection,
    OPENAI_COMPATIBLE_PROVIDERS,
    _normalize_token_usage,
    resolve_ai_context,
)
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


def test_parser_openai_without_key_returns_honest_unavailable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    data = ParserAgent({"provider": "openai", "user_id": "api-user-1"}).extract_project_data(
        "Short business plan text"
    )

    # No AI provider available -> honest "extraction_unavailable", NO fabricated numbers.
    assert data["_meta"]["parsing_source"] == "extraction_unavailable"
    assert data["_meta"]["requiresManualOrLlmExtraction"] is True
    assert data["_meta"]["ai_connection"]["provider"] == "openai"
    assert data["_meta"]["ai_connection"]["user_id"] == "api-user-1"
    # Critically: no invented financials/project values.
    assert "feasibility_input" not in data
    assert "project" not in data
    assert "pf_structure" not in data


def test_parser_chunks_all_document_text_without_dropping_tail():
    prompts = []

    class RecordingAI:
        def extract_json(self, prompt, system=None, ai_context=None):
            prompts.append(prompt)
            return SimpleNamespace(
                data={"project": {"name": "Chunked Project"}},
                error=None,
                meta={"provider": "recording"},
            )

    parser = ParserAgent({"provider": "groq", "api_key": "test-only"})
    parser.ai = RecordingAI()
    tail_marker = "TAIL_FACT_MUST_BE_PARSED"
    text = (
        "--- PAGE 1 ---\n"
        + ("A" * 31_000)
        + "\n--- PAGE 2 ---\n"
        + tail_marker
    )

    result = parser.extract_project_data(text)

    assert len(prompts) > 1
    assert any(tail_marker in prompt for prompt in prompts)
    assert result["_meta"]["chunkCount"] == len(prompts)
    assert result["_meta"]["inputCoverage"] == "complete"
    assert result["_meta"]["sourceTextSha256"]


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


def test_explicit_api_key_enables_openai_compatible_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    context = resolve_ai_context({"provider": "groq", "api_key": "user-key", "model": "llama-3.3-70b-versatile"})

    assert context["provider"] == "groq"
    assert context["model"] == "llama-3.3-70b-versatile"
    assert context["direct_model_call"] is True
    assert context["requires_api_key"] is None


def test_ai_brain_used_audit_event_redacts_keys_and_hashes_prompt():
    event = build_ai_brain_used_audit_event(
        context={
            "provider": "gemini",
            "runtime_provider": "openai_compatible",
            "model": "gemini-2.5-flash",
            "api_key": "secret-user-key",
            "user_id": "u-1",
            "tenant_id": "t-1",
            "project_id": "p-1",
            "agent_id": "ParserAgent",
            "selection_source": "user_ai_profile",
            "direct_model_call": True,
        },
        prompt="extract this",
        system="system",
        status="success",
    )

    assert event["eventType"] == "ai_brain_used"
    assert event["userId"] == "u-1"
    assert event["projectId"] == "p-1"
    metadata = event["metadata"]
    assert metadata["provider"] == "gemini"
    assert metadata["model"] == "gemini-2.5-flash"
    assert metadata["brainSource"] == "user_profile"
    assert metadata["keyMode"] == "byok"
    assert metadata["promptHash"]
    assert metadata["aiContext"]["api_key"] == "[redacted]"
    assert "secret-user-key" not in str(event)


def test_ai_connection_records_ai_brain_used_event(monkeypatch):
    records = []
    monkeypatch.setattr("agents.ai_connection.record_ai_brain_used", lambda event: records.append(event))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    result = AIConnection().extract_json(
        "prompt",
        system="system",
        ai_context={"provider": "groq", "user_id": "u-2", "project_id": "p-2", "agent_id": "IntakeTriageAgent"},
    )

    assert result.data is None
    assert records
    assert records[0]["eventType"] == "ai_brain_used"
    assert records[0]["projectId"] == "p-2"
    assert records[0]["metadata"]["agentId"] == "IntakeTriageAgent"
    assert records[0]["metadata"]["status"] == "fallback_used"


def test_token_usage_normalizes_openai_and_anthropic_shapes():
    class OpenAIUsage:
        prompt_tokens = 12
        completion_tokens = 8
        total_tokens = 20

    assert _normalize_token_usage(OpenAIUsage(), provider="openai_compatible") == {
        "provider": "openai_compatible",
        "promptTokens": 12,
        "completionTokens": 8,
        "totalTokens": 20,
    }
    assert _normalize_token_usage(
        {"input_tokens": 10, "output_tokens": 7},
        provider="anthropic",
    ) == {
        "provider": "anthropic",
        "promptTokens": 10,
        "completionTokens": 7,
        "totalTokens": 17,
    }


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
