from agents.ai_connection import resolve_ai_context
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
