"""Dependency-free provider resilience and operations contracts.

Provider calls are rate-limited in-process even when no distributed limiter is
configured. Production runtimes should still inject durable sinks and may
replace the limiter with a shared implementation, but constructing a provider
must never silently mean "unlimited".
"""

from __future__ import annotations

import random
import time
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    initial_backoff_seconds: float = 0.1
    multiplier: float = 2.0
    max_backoff_seconds: float = 5.0
    max_retry_after_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must not be negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.max_backoff_seconds < 0:
            raise ValueError("max_backoff_seconds must not be negative")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds must not be negative")


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    retry_after_seconds: float | None = None
    counts_toward_circuit: bool = True


class ProviderHttpError(RuntimeError):
    """Transport-neutral HTTP failure for injected/managed HTTP clients."""

    def __init__(
        self,
        status_code: int,
        message: str = "",
        *,
        retry_after: str | float | int | None = None,
    ) -> None:
        super().__init__(message or f"provider HTTP {status_code}")
        self.status_code = int(status_code)
        self.retry_after = retry_after


def classify_provider_error(
    error: Exception,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RetryDecision:
    """Classify external-provider failures without relying on one HTTP client.

    4xx responses are permanent except request timeout (408) and rate limiting
    (429).  Server failures and non-HTTP transport exceptions remain retryable
    for backward compatibility.
    """

    status_code: int | None = None
    retry_after: str | float | int | None = None
    if isinstance(error, ProviderHttpError):
        status_code = error.status_code
        retry_after = error.retry_after
    elif isinstance(error, urllib.error.HTTPError):
        status_code = int(error.code)
        retry_after = error.headers.get("Retry-After") if error.headers else None
    else:
        raw_status = getattr(error, "status_code", None)
        if isinstance(raw_status, int):
            status_code = raw_status
            headers = getattr(error, "headers", None)
            if headers is not None:
                retry_after = headers.get("Retry-After")
            retry_after = getattr(error, "retry_after", retry_after)

    if status_code is None:
        return RetryDecision(retryable=True)
    parsed_retry_after = _parse_retry_after(retry_after, now=now)
    if status_code == 429:
        return RetryDecision(
            retryable=True,
            retry_after_seconds=parsed_retry_after,
            counts_toward_circuit=False,
        )
    if status_code == 408 or 500 <= status_code <= 599:
        return RetryDecision(
            retryable=True,
            retry_after_seconds=parsed_retry_after,
        )
    if 400 <= status_code <= 499:
        return RetryDecision(retryable=False, counts_toward_circuit=False)
    return RetryDecision(retryable=True, retry_after_seconds=parsed_retry_after)


def _parse_retry_after(
    value: str | float | int | None,
    *,
    now: Callable[[], datetime],
) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(value))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = now()
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            seconds = (target - current).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: float = 0.0


class ProviderRateLimiter(Protocol):
    def acquire(
        self,
        provider_id: str,
        *,
        tokens: float = 1.0,
    ) -> RateLimitDecision: ...


class NoOpProviderRateLimiter:
    def acquire(
        self,
        provider_id: str,
        *,
        tokens: float = 1.0,
    ) -> RateLimitDecision:
        del provider_id, tokens
        return RateLimitDecision(allowed=True)


@dataclass
class _TokenBucket:
    tokens: float
    updated_at: float


class TokenBucketProviderRateLimiter:
    """Thread-safe independent token bucket for each provider id."""

    def __init__(
        self,
        *,
        capacity: float,
        refill_tokens_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_tokens_per_second <= 0:
            raise ValueError("refill_tokens_per_second must be positive")
        self._capacity = float(capacity)
        self._refill_rate = float(refill_tokens_per_second)
        self._clock = clock
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = Lock()

    def acquire(
        self,
        provider_id: str,
        *,
        tokens: float = 1.0,
    ) -> RateLimitDecision:
        if not provider_id.strip():
            raise ValueError("provider_id is required")
        if tokens <= 0 or tokens > self._capacity:
            raise ValueError("tokens must be positive and not exceed capacity")
        with self._lock:
            current = self._clock()
            bucket = self._buckets.get(provider_id)
            if bucket is None:
                bucket = _TokenBucket(tokens=self._capacity, updated_at=current)
                self._buckets[provider_id] = bucket
            elapsed = max(0.0, current - bucket.updated_at)
            bucket.tokens = min(
                self._capacity,
                bucket.tokens + elapsed * self._refill_rate,
            )
            bucket.updated_at = current
            if bucket.tokens >= tokens:
                bucket.tokens -= tokens
                return RateLimitDecision(allowed=True)
            missing = tokens - bucket.tokens
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=missing / self._refill_rate,
            )


_SHARED_LIMITERS: dict[
    tuple[float, float], TokenBucketProviderRateLimiter
] = {}
_SHARED_LIMITERS_LOCK = Lock()


def shared_provider_rate_limiter(
    *,
    capacity: float = 30.0,
    refill_tokens_per_second: float = 5.0,
) -> TokenBucketProviderRateLimiter:
    """Return one process-wide bucket registry for a configured policy.

    ``build_legal_intelligence_service`` is called per HTTP request in the
    current API. A limiter owned by an individual provider instance would
    therefore refill to capacity on every request and provide no protection.
    This registry preserves the bucket across service/provider construction
    while keeping independent buckets for each ``provider_id``.
    """

    key = (float(capacity), float(refill_tokens_per_second))
    with _SHARED_LIMITERS_LOCK:
        limiter = _SHARED_LIMITERS.get(key)
        if limiter is None:
            limiter = TokenBucketProviderRateLimiter(
                capacity=key[0],
                refill_tokens_per_second=key[1],
            )
            _SHARED_LIMITERS[key] = limiter
        return limiter


@dataclass(frozen=True)
class ProviderQuarantineRecord:
    provider_id: str
    operation: str
    reason_code: str
    payload_digest: str
    target: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderQuarantineSink(Protocol):
    """Durable implementations persist immutable rejected-payload metadata."""

    def store(self, record: ProviderQuarantineRecord) -> None: ...


@dataclass(frozen=True)
class ProviderDeadLetterRecord:
    provider_id: str
    operation: str
    reason_code: str
    attempts: int
    target: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderDeadLetterSink(Protocol):
    """Durable implementations persist replayable failure metadata."""

    def enqueue(self, record: ProviderDeadLetterRecord) -> None: ...


class NoOpProviderQuarantineSink:
    def store(self, record: ProviderQuarantineRecord) -> None:
        del record


class NoOpProviderDeadLetterSink:
    def enqueue(self, record: ProviderDeadLetterRecord) -> None:
        del record


@dataclass(frozen=True)
class ProviderMetric:
    """Privacy-safe provider observation; never include query text or payload."""

    provider_id: str
    operation: str
    outcome: str
    latency_ms: float
    attempts: int = 0
    rate_limited: bool = False
    local_hit: bool | None = None
    freshness: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)


class ProviderTelemetrySink(Protocol):
    def record(self, metric: ProviderMetric) -> None: ...


class NoOpProviderTelemetrySink:
    def record(self, metric: ProviderMetric) -> None:
        del metric


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if self._clock() - self._opened_at >= self.reset_timeout_seconds:
                self._opened_at = None
                self._failures = 0
                return False
            return True

    def before_call(self) -> None:
        if self.is_open:
            raise CircuitOpenError("provider circuit is open")

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self._clock()


class RetryExhaustedError(RuntimeError, Generic[T]):
    def __init__(
        self,
        cause: Exception,
        attempts: int,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempts = attempts
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def resilient_call(
    operation: Callable[[], T],
    *,
    retry_policy: RetryPolicy,
    circuit_breaker: CircuitBreaker,
    sleep: Callable[[float], None] = time.sleep,
    classify_error: Callable[[Exception], RetryDecision] = classify_provider_error,
    jitter: Callable[[float, float], float] = random.uniform,
) -> tuple[T, int]:
    circuit_breaker.before_call()
    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            value = operation()
        except Exception as exc:
            decision = classify_error(exc)
            if decision.counts_toward_circuit:
                circuit_breaker.record_failure()
            if (
                not decision.retryable
                or attempt >= retry_policy.max_attempts
                or circuit_breaker.is_open
            ):
                raise RetryExhaustedError(
                    exc,
                    attempt,
                    retryable=decision.retryable,
                    retry_after_seconds=decision.retry_after_seconds,
                ) from exc
            cap = min(
                retry_policy.max_backoff_seconds,
                retry_policy.initial_backoff_seconds
                * (retry_policy.multiplier ** (attempt - 1)),
            )
            delay = jitter(0.0, cap) if cap else 0.0
            if decision.retry_after_seconds is not None:
                delay = max(
                    delay,
                    min(
                        decision.retry_after_seconds,
                        retry_policy.max_retry_after_seconds,
                    ),
                )
            if delay:
                sleep(delay)
        else:
            circuit_breaker.record_success()
            return value, attempt
    raise AssertionError("retry loop terminated unexpectedly")
