from agents.ai_connection import resolve_ai_context
from agents.parser_agent import ParserAgent


def test_resolve_ai_context_links_user_id_for_claude_desktop(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("REALYTICS_AI_PROVIDER", "claude_desktop")

    context = resolve_ai_context({"user_id": "user-123", "host": "claude_desktop"})

    assert context["provider"] == "claude_desktop"
    assert context["runtime_provider"] == "claude_desktop_host"
    assert context["user_id"] == "user-123"
    assert context["requires_api_key"] == "ANTHROPIC_API_KEY"


def test_parser_fallback_preserves_ai_connection_user(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    data = ParserAgent(
        {
            "provider": "claude_desktop",
            "host": "claude_desktop",
            "user_id": "mcp-user-1",
        }
    ).extract_project_data("Short business plan text")

    assert data["_meta"]["parsing_source"] == "mock"
    assert data["_meta"]["ai_connection"]["provider"] == "claude_desktop"
    assert data["_meta"]["ai_connection"]["user_id"] == "mcp-user-1"
