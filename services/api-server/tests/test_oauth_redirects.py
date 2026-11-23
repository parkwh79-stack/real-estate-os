from urllib.parse import parse_qs, urlsplit

import pytest

from core.oauth_redirects import safe_relative_path, web_session_url


@pytest.mark.parametrize("path", [
    "https://outside.invalid", "//outside.invalid", "/\\outside.invalid",
    "/%5coutside.invalid", "/%2foutside.invalid", "/%252foutside.invalid",
    "/\r\nLocation: outside", "/%0aoutside", "javascript:alert(1)",
])
def test_redirect_state_cannot_choose_an_external_destination(path):
    assert safe_relative_path(path) == "/start"
    result = urlsplit(web_session_url("https://web.example", "test-session", path))
    assert result.scheme == "https"
    assert result.netloc == "web.example"
    assert result.path == "/auth/kakao/session"
    assert parse_qs(result.query)["redirect"] == ["/start"]


def test_redirect_query_is_encoded_without_losing_valid_navigation():
    path = "/projects/751?tab=cashflow&scenario=base#monthly"
    result = urlsplit(web_session_url("https://web.example/", "session&redirect=bad", path))
    assert parse_qs(result.query) == {
        "session_id": ["session&redirect=bad"], "redirect": [path],
    }
    assert not result.fragment


@pytest.mark.parametrize("base", [
    "//outside.invalid", "javascript:alert(1)", "https://user:pass@web.example",
    "https://web.example?override=1", "https://web.example#fragment",
    "https://web.example\\outside", "http://web.example", "https://web.example\n",
])
def test_invalid_configured_web_origin_is_rejected(base):
    with pytest.raises(ValueError, match="configured web origin"):
        web_session_url(base, "test-session", "/start")


def test_local_http_remains_available_for_explicit_legacy_development():
    assert web_session_url("http://localhost:3000", "test-session", "/start").startswith(
        "http://localhost:3000/auth/kakao/session?"
    )
