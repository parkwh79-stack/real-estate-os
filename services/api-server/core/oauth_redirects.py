"""Build legacy OAuth redirects without letting state choose the destination."""

from typing import Any
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit


def safe_relative_path(value: Any, *, default: str = "/start") -> str:
    path = str(value or default)
    decoded = path
    for _ in range(4):
        if (
            not decoded.startswith("/")
            or decoded.startswith("//")
            or "\\" in decoded
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            or "://" in decoded
        ):
            return default
        next_value = unquote(decoded)
        if next_value == decoded:
            return path
        decoded = next_value
    return default


def web_session_url(web_base_url: str, session_id: str, redirect_path: Any) -> str:
    """Only operator-configured HTTP(S) origins receive the legacy session."""
    base = urlsplit(web_base_url)
    if (
        base.scheme not in {"https", "http"}
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
        or "\\" in web_base_url
        or any(ord(char) < 32 or ord(char) == 127 for char in web_base_url)
        or (base.scheme == "http" and base.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("Invalid configured web origin")
    receiver = base.path.rstrip("/") + "/auth/kakao/session"
    target = urlunsplit((base.scheme, base.netloc, receiver, urlencode({
        "session_id": session_id,
        "redirect": safe_relative_path(redirect_path),
    }), ""))
    # Keep the destination boundary explicit after encoding untrusted state.
    if urlsplit(target).netloc != base.netloc or urlsplit(target).path != receiver:
        raise ValueError("Invalid session redirect destination")
    return target
