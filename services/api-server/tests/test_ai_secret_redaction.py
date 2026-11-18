from __future__ import annotations

import json
import sys
import traceback
from types import SimpleNamespace

import pytest

from agent_platform.brains import control
from agents.ai_audit import build_ai_brain_used_audit_event
from agents.ai_connection import AIConnection, safe_ai_context
from agents.orchestration_agent import OrchestrationAgent
from core.secret_redaction import (
    collect_secret_values,
    redact_secret_text,
    redact_sensitive_value,
)


def test_secret_redaction_removes_exact_encoded_and_provider_shaped_tokens() -> None:
    exact = "tenant-key/with+a-query-value"
    google_shaped = "AIza" + "A" * 32
    openai_shaped = "sk-proj-" + "B" * 24
    jwt_shaped = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
    text = (
        f"request api_key={exact}; encoded=tenant-key%2Fwith%2Ba-query-value; "
        f"Authorization: Bearer {jwt_shaped}; provider={google_shaped}; {openai_shaped}"
    )

    redacted = redact_secret_text(text, secrets=(exact,))

    for secret in (exact, "tenant-key%2Fwith%2Ba-query-value", google_shaped, openai_shaped, jwt_shaped):
        assert secret not in redacted
    assert redacted.count("[redacted]") >= 4


def test_recursive_context_redaction_covers_nested_text_without_hiding_usage() -> None:
    exact = "customer-private-provider-key"
    source = {
        "apiKey": exact,
        "tokenUsage": {"totalTokens": 123},
        "metadata": [
            {"diagnostic": f"provider echoed {exact}"},
            {"credentialsPersisted": False},
        ],
    }

    safe = redact_sensitive_value(source, secrets=(exact,))

    assert safe["apiKey"] == "[redacted]"
    assert safe["tokenUsage"] == {"totalTokens": 123}
    assert safe["metadata"][1]["credentialsPersisted"] is False
    assert exact not in json.dumps(safe)


def test_redaction_preserves_null_and_boolean_control_contracts() -> None:
    source = {
        "api_key": None,
        "requires_api_key": True,
        "hasApiKey": False,
        "credentialRef": "session-secret://private-reference",
    }

    safe = redact_sensitive_value(source, secrets=collect_secret_values(source))

    assert safe["api_key"] is None
    assert safe["requires_api_key"] is True
    assert safe["hasApiKey"] is False
    assert safe["credentialRef"] == "[redacted]"
    assert collect_secret_values({"api_key": 1, "password": False}) == ()


def test_provider_exception_is_redacted_in_result_and_safe_context(monkeypatch) -> None:
    exact = "byok-exact-secret-that-must-not-leak"
    google_shaped = "AIza" + "C" * 32

    class FailingCompletions:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError(
                f"provider rejected api_key={exact}; echoed credential {google_shaped}"
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FailingCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    context = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key": exact,
        "supports_json_mode": True,
        "is_local": False,
    }

    result = AIConnection()._extract_with_openai_compatible("prompt", "system", context)

    serialized = json.dumps({"error": result.error, "meta": result.meta})
    assert result.data is None
    assert exact not in serialized
    assert google_shaped not in serialized
    assert "[redacted]" in serialized
    assert safe_ai_context(context)["api_key"] == "[redacted]"


def test_environment_provider_secret_is_exact_redacted_from_sdk_error(monkeypatch) -> None:
    exact = "arbitrary-env-secret-without-a-provider-prefix"

    class FailingCompletions:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError(f"upstream echoed {exact}")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FailingCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("GEMINI_API_KEY", exact)
    context = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "supports_json_mode": True,
        "is_local": False,
    }

    result = AIConnection()._extract_with_openai_compatible("prompt", "system", context)

    assert exact not in str(result.error)
    assert "[redacted]" in str(result.error)


def test_embedding_error_drops_raw_exception_chain(monkeypatch) -> None:
    exact = "embedding-provider-secret-that-must-not-reach-traceback"

    class FailingEmbeddings:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError(f"provider echoed {exact}")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.embeddings = FailingEmbeddings()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(
        "agents.ai_connection.route_ai_connection_candidates",
        lambda candidates, **_kwargs: (
            candidates[0],
            {
                "blocked": False,
                "routingEventId": "routing-redaction-test",
                "routingPolicyVersion": "test",
                "selectedProfileId": "test-profile",
            },
        ),
    )
    connection = AIConnection()

    with pytest.raises(RuntimeError) as captured:
        connection.embed_text(
            "safe input",
            ai_context={
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "embedding_model": "text-embedding-3-small",
                "api_key": exact,
                "direct_model_call": True,
            },
        )

    rendered = "".join(
        traceback.format_exception(
            captured.value.__class__,
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert captured.value.__cause__ is None
    assert exact not in rendered
    assert "[redacted]" in rendered


def test_audit_and_provider_health_redact_the_current_request_secret(monkeypatch) -> None:
    exact = "request-scoped-exact-provider-secret"
    google_shaped = "AIza" + "D" * 32
    context = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key": exact,
        "tenant_id": "tenant-redaction",
        "user_id": "user-redaction",
        "direct_model_call": True,
    }
    error = f"upstream failure: {exact}; token={google_shaped}"

    event = build_ai_brain_used_audit_event(
        context=context,
        prompt="safe prompt",
        system="safe system",
        status="failed",
        error=error,
    )

    class Repository:
        def __init__(self) -> None:
            self.saved = None

        def get(self, *_args, **_kwargs):
            return None

        def save(self, record):
            self.saved = record
            return record

    repository = Repository()
    monkeypatch.setattr(control, "OperationalRepository", lambda: repository)
    health = control.record_brain_provider_result(context, success=False, error=error)

    for payload in (event, health.model_dump(by_alias=True, mode="json")):
        serialized = json.dumps(payload)
        assert exact not in serialized
        assert google_shaped not in serialized
        assert "[redacted]" in serialized
    assert repository.saved is health


def test_pf_review_never_returns_or_persists_resolved_inline_api_key(monkeypatch) -> None:
    exact = "pf-review-request-secret-that-must-not-persist"
    agent = OrchestrationAgent()

    def fail_first_step(*_args, **_kwargs):
        raise RuntimeError(f"project load failed with api_key={exact}")

    monkeypatch.setattr(agent, "_complete_step", fail_first_step)

    run = agent.run_pf_review(
        "project-redaction",
        {},
        options={
            "user_id": "redaction-user",
            "ai_context": {
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "api_key": exact,
            },
        },
    )

    serialized = json.dumps(run)
    assert run["ai_connection"]["api_key"] == "[redacted]"
    assert run["options"]["ai_context"]["api_key"] == "[redacted]"
    assert run["errors"][0]["message"] == (
        "PF review execution failed. Retry or contact an operator with the run ID."
    )
    assert "project load failed with" not in serialized
    assert exact not in serialized
