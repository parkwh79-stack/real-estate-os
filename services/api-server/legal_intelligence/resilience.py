"""Small dependency-free retry and circuit-breaker primitives."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    initial_backoff_seconds: float = 0.1
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must not be negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")


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
    def __init__(self, cause: Exception, attempts: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempts = attempts


def resilient_call(
    operation: Callable[[], T],
    *,
    retry_policy: RetryPolicy,
    circuit_breaker: CircuitBreaker,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    circuit_breaker.before_call()
    delay = retry_policy.initial_backoff_seconds
    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            value = operation()
        except Exception as exc:
            circuit_breaker.record_failure()
            if attempt >= retry_policy.max_attempts or circuit_breaker.is_open:
                raise RetryExhaustedError(exc, attempt) from exc
            if delay:
                sleep(delay)
            delay *= retry_policy.multiplier
        else:
            circuit_breaker.record_success()
            return value, attempt
    raise AssertionError("retry loop terminated unexpectedly")
