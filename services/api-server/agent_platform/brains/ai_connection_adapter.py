from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Any

from agent_platform.contracts import BrainProfile, BrainRequest, BrainResponse
from agents.ai_connection import AIConnection, safe_ai_context


class BrainExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AIConnectionBrainAdapter:
    """Expose the existing AIConnection through the provider-neutral Brain contract."""

    def __init__(
        self,
        *,
        profile: BrainProfile,
        ai_context: dict[str, Any],
        connection: AIConnection | None = None,
    ) -> None:
        self.profile = profile
        self._ai_context = dict(ai_context)
        self._connection = connection or AIConnection()

    async def generate(self, request: BrainRequest) -> BrainResponse:
        if request.tools:
            raise BrainExecutionError("UNSUPPORTED_CAPABILITY", "AIConnection adapter does not yet expose tool calls")
        prompt = _messages_to_prompt(request.messages)
        started = perf_counter()
        result = await asyncio.to_thread(
            self._connection.extract_json,
            prompt,
            system=request.system_instructions,
            ai_context=self._ai_context,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        if result.data is None:
            raise BrainExecutionError(_error_code(result.error), result.error or "Brain call failed")
        usage = (result.meta or {}).get("token_usage") or {}
        return BrainResponse(
            structured_output=result.data,
            input_tokens=usage.get("promptTokens"),
            output_tokens=usage.get("completionTokens"),
            estimated_cost_usd=self.estimate_cost(request),
            latency_ms=latency_ms,
            finish_reason="stop",
            raw_metadata={
                "provider": self.profile.provider,
                "model": self.profile.model,
                "aiBrainUsedEventId": (result.meta or {}).get("ai_brain_used_event_id"),
                "safeAiContext": safe_ai_context(result.meta or self._ai_context),
            },
        )

    async def health_check(self) -> bool:
        return bool(self.profile.enabled and self._ai_context.get("direct_model_call"))

    def estimate_cost(self, request: BrainRequest) -> float:
        rate = float(self.profile.metadata.get("estimatedCostPer1kTokensUsd") or 0)
        token_estimate = int(request.metadata.get("inputTokenEstimate") or 0) + int(request.max_output_tokens or 0)
        return round((token_estimate / 1000) * rate, 6)


class MockBrain:
    """PROTOTYPE-ONLY deterministic Brain used only by contract tests."""

    def __init__(self, profile: BrainProfile, output: dict[str, Any] | None = None) -> None:
        self.profile = profile
        self.output = output or {"ok": True}

    async def generate(self, request: BrainRequest) -> BrainResponse:
        return BrainResponse(structured_output=dict(self.output), finish_reason="mock_test_only")

    async def health_check(self) -> bool:
        return self.profile.enabled

    def estimate_cost(self, request: BrainRequest) -> float:
        return 0.0


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    if len(messages) == 1 and isinstance(messages[0].get("content"), str):
        return str(messages[0]["content"])
    return json.dumps(messages, ensure_ascii=False, default=str)


def _error_code(message: str | None) -> str:
    text = str(message or "").lower()
    if "rate" in text and "limit" in text:
        return "PROVIDER_RATE_LIMIT"
    if "timeout" in text or "timed out" in text:
        return "PROVIDER_TIMEOUT"
    if "api key" in text or "authentication" in text or "unauthorized" in text:
        return "PROVIDER_AUTH_ERROR"
    if "json" in text:
        return "INVALID_STRUCTURED_OUTPUT"
    return "PROVIDER_UNAVAILABLE"
