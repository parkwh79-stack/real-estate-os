from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote, quote_plus


REDACTED = "[redacted]"

_SENSITIVE_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "bearer",
    "clientsecret",
    "credential",
    "credentialref",
    "password",
    "passwd",
    "refreshtoken",
    "secret",
    "token",
}

_NAMED_SECRET_PATTERN = re.compile(
    r"(?ix)\b"
    r"(?P<key>api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|"
    r"client[_-]?secret|password|passwd|credential|secret|token)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>(?:bearer\s+)?[^\s,;&\"'}]+)"
    r"(?P=quote)"
)

_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")

# Provider-shaped credentials that may appear in an SDK exception without a
# field name. Keep this deliberately prefix-based to avoid hiding ordinary IDs,
# hashes, or financial reference numbers.
_KNOWN_TOKEN_PATTERNS = (
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-(?:proj-|ant-)?[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"\bgsk_[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[0-9A-Za-z_]{16,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{16,}\b"),
    re.compile(
        r"\beyJ[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\b"
    ),
)


def is_sensitive_key(key: Any) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", str(key).lower())
    # Boolean capability/status fields describe whether a credential is
    # required; they are not credentials themselves. Treating these as secret
    # changes False/None into a truthy "[redacted]" value and can alter routing.
    if compact in {
        "hasapikey",
        "requiresapikey",
        "supportsapikey",
    }:
        return False
    return compact in _SENSITIVE_KEYS or any(
        compact.endswith(suffix)
        for suffix in (
            "apikey",
            "accesstoken",
            "refreshtoken",
            "clientsecret",
            "credentialref",
            "password",
        )
    )


def collect_secret_values(value: Any) -> tuple[str, ...]:
    """Collect exact secret values only from explicitly sensitive fields."""

    found: list[str] = []

    def walk(item: Any, *, sensitive: bool = False) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                walk(nested, sensitive=is_sensitive_key(key))
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                walk(nested, sensitive=sensitive)
            return
        if sensitive and isinstance(item, (str, bytes)):
            candidate = str(item)
            # Very short scalar values are too ambiguous for global exact
            # replacement (for example "1" or "no"). Named-field redaction
            # still protects them when they appear beside a credential label.
            if len(candidate) >= 8 and candidate.lower() != REDACTED:
                found.append(candidate)

    walk(value)
    return tuple(sorted(set(found), key=len, reverse=True))


def redact_secret_text(value: Any, *, secrets: Iterable[Any] = ()) -> str:
    """Remove exact credentials and common token forms from diagnostic text."""

    redacted = str(value)
    exact_secrets = {
        str(secret)
        for secret in secrets
        if secret is not None and str(secret).lower() not in {"", REDACTED}
    }
    for secret in sorted(exact_secrets, key=len, reverse=True):
        variants = {secret, quote(secret, safe=""), quote_plus(secret, safe="")}
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                redacted = redacted.replace(variant, REDACTED)

    redacted = _NAMED_SECRET_PATTERN.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}{REDACTED}",
        redacted,
    )
    redacted = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", redacted)
    for pattern in _KNOWN_TOKEN_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_sensitive_value(value: Any, *, secrets: Iterable[Any] = ()) -> Any:
    """Recursively redact secret fields and token-shaped text."""

    exact_secrets = tuple(secrets)
    if isinstance(value, Mapping):
        return {
            key: _redact_sensitive_field(item)
            if is_sensitive_key(key)
            else redact_sensitive_value(item, secrets=exact_secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item, secrets=exact_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item, secrets=exact_secrets) for item in value)
    if isinstance(value, set):
        return {redact_sensitive_value(item, secrets=exact_secrets) for item in value}
    if isinstance(value, str):
        return redact_secret_text(value, secrets=exact_secrets)
    return value


def _redact_sensitive_field(value: Any) -> Any:
    """Redact an actual value without changing empty/null control semantics."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (str, bytes)) and not value:
        return value
    return REDACTED


__all__ = [
    "REDACTED",
    "collect_secret_values",
    "is_sensitive_key",
    "redact_secret_text",
    "redact_sensitive_value",
]
