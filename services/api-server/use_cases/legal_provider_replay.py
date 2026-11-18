"""Operator request and worker execution flow for legal-provider DLQ replay."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from db.repositories.legal_provider_replay_repository import (
    ProviderReplayMaterial,
)
from legal_intelligence.provider_replay_models import (
    LegalProviderReplayApprovalInput,
    LegalProviderReplayObjectReceipt,
    LegalProviderReplayRequestInput,
    ProviderReplayStatus,
)


_OPERATOR_ROLES = frozenset({"admin", "compliance_officer"})
_APPROVER_ROLES = frozenset({"compliance_officer"})
_MAX_APPROVAL_TTL = timedelta(hours=24)
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_SAFE_IDEMPOTENCY_KEY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$"
)


@dataclass(frozen=True)
class ProviderReplayExecutionResult:
    """Privacy-safe executor outcome.

    Executors must read the exact cataloged object version and verify its bytes
    against ``approved_object_sha256`` before calling the provider.  They return
    codes and opaque execution references only, never provider content.
    """

    completed: bool
    outcome_code: str
    object_sha256_verified: bool
    verified_replay_request_id: str | None = None
    verified_replay_object_id: str | None = None
    verified_object_version_id: str | None = None
    verified_object_sha256: str | None = None
    executor_reference: str | None = None

    def __post_init__(self) -> None:
        if not _SAFE_CODE.fullmatch(self.outcome_code):
            raise ValueError("outcome_code must be a stable privacy-safe code")
        if (
            self.executor_reference is not None
            and not _SAFE_REFERENCE.fullmatch(self.executor_reference)
        ):
            raise ValueError(
                "executor_reference must be an opaque privacy-safe identifier"
            )
        if self.object_sha256_verified:
            if not self.verified_replay_request_id:
                raise ValueError(
                    "verified_replay_request_id is required after digest verification"
                )
            if not self.verified_replay_object_id:
                raise ValueError(
                    "verified_replay_object_id is required after digest verification"
                )
            if not self.verified_object_version_id:
                raise ValueError(
                    "verified_object_version_id is required after digest verification"
                )
            if not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(self.verified_object_sha256 or ""),
            ):
                raise ValueError(
                    "verified_object_sha256 must be the exact verified digest"
                )


class LegalProviderReplayExecutor(Protocol):
    """Injected worker boundary; the API process never reads quarantine bytes."""

    def execute(
        self,
        material: ProviderReplayMaterial,
    ) -> ProviderReplayExecutionResult: ...


class LegalProviderReplayRepository(Protocol):
    def register_replay_object(
        self,
        receipt: LegalProviderReplayObjectReceipt,
    ) -> dict[str, Any]: ...

    def list_dead_letters(
        self,
        *,
        tenant_id: str,
        limit: int,
        provider_id: str | None,
        status: ProviderReplayStatus | None,
    ) -> list[dict[str, Any]]: ...

    def create_replay_approval(
        self,
        dead_letter_id: str,
        *,
        tenant_id: str,
        project_id: str,
        replay_object_id: str,
        replay_requester_id: str,
        approved_by: str,
        approver_role: str,
        approved_at: datetime,
        expires_at: datetime,
        idempotency_key: str,
        supersedes_approval_event_id: str | None,
    ) -> dict[str, Any]: ...

    def request_replay(
        self,
        dead_letter_id: str,
        *,
        tenant_id: str,
        approval_event_id: str,
        idempotency_key: str,
        requested_by: str,
        requester_role: str,
    ) -> dict[str, Any]: ...

    def begin_replay(
        self,
        replay_request_id: str,
        *,
        worker_id: str,
        tenant_id: str,
    ) -> ProviderReplayMaterial: ...

    def record_outcome(
        self,
        replay_request_id: str,
        *,
        completed: bool,
        outcome_code: str,
        executor_reference: str | None,
        worker_id: str,
        tenant_id: str,
    ) -> dict[str, Any]: ...


class LegalProviderReplayService:
    def __init__(
        self,
        repository: LegalProviderReplayRepository,
        *,
        executor: LegalProviderReplayExecutor | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_dead_letters(
        self,
        *,
        tenant_id: str,
        limit: int,
        provider_id: str | None,
        status: ProviderReplayStatus | None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": "realytics-legal-provider-dlq-v1",
            "items": self._repository.list_dead_letters(
                tenant_id=tenant_id,
                limit=limit,
                provider_id=provider_id,
                status=status,
            ),
            "privacy": {
                "rawQueryReturned": False,
                "rawPayloadReturned": False,
                "objectLocatorReturned": False,
            },
        }

    def approve_replay_object(
        self,
        dead_letter_id: str,
        approval: LegalProviderReplayApprovalInput,
        *,
        actor_id: str,
        actor_role: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        role = actor_role.strip().casefold()
        approver = actor_id.strip()
        tenant = tenant_id.strip()
        requester = approval.replay_requester_id.strip()
        if role not in _APPROVER_ROLES:
            raise PermissionError(
                "replay object approval requires compliance_officer"
            )
        if (
            not approver
            or approver.casefold() in {"anonymous", "system", "unknown"}
            or not tenant
        ):
            raise PermissionError(
                "a named tenant-scoped human approver is required"
            )
        if approver.casefold() == requester.casefold():
            raise PermissionError(
                "replay requester and object approver must differ"
            )
        approved_at = _aware(self._clock())
        expires_at = _aware(approval.expires_at)
        if expires_at <= approved_at:
            raise ValueError("replay approval expiry must be in the future")
        if expires_at - approved_at > _MAX_APPROVAL_TTL:
            raise ValueError(
                "replay approval expiry must not exceed 24 hours"
            )
        result = self._repository.create_replay_approval(
            dead_letter_id,
            tenant_id=tenant,
            project_id=approval.project_id,
            replay_object_id=approval.replay_object_id,
            replay_requester_id=requester,
            approved_by=approver,
            approver_role=role,
            approved_at=approved_at,
            expires_at=expires_at,
            idempotency_key=approval.idempotency_key,
            supersedes_approval_event_id=(
                approval.supersedes_approval_event_id
            ),
        )
        return {
            "schemaVersion": (
                "realytics-legal-provider-replay-approval-v1"
            ),
            **result,
            "appendOnly": True,
            "objectLocatorReturned": False,
        }

    def request_replay(
        self,
        dead_letter_id: str,
        request: LegalProviderReplayRequestInput,
        *,
        actor_id: str,
        actor_role: str,
        tenant_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        role = actor_role.strip().casefold()
        actor = actor_id.strip()
        tenant = tenant_id.strip()
        replay_idempotency_key = idempotency_key.strip()
        if role not in _OPERATOR_ROLES:
            raise PermissionError(
                "legal provider replay requires admin or compliance_officer"
            )
        if (
            not actor
            or actor.casefold() in {"anonymous", "system", "unknown"}
            or not tenant
        ):
            raise PermissionError("a named human operator is required")
        if not _SAFE_IDEMPOTENCY_KEY.fullmatch(
            replay_idempotency_key
        ):
            raise ValueError(
                "replay idempotency key is missing or invalid"
            )
        result = self._repository.request_replay(
            dead_letter_id,
            tenant_id=tenant,
            approval_event_id=request.approval_event_id,
            idempotency_key=replay_idempotency_key,
            requested_by=actor,
            requester_role=role,
        )
        return {
            "schemaVersion": "realytics-legal-provider-replay-request-v1",
            **result,
            "appendOnly": True,
        }

    def execute_requested_replay(
        self,
        replay_request_id: str,
        *,
        worker_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Worker-only transition from requested to one terminal event."""

        if self._executor is None:
            raise RuntimeError("a legal provider replay executor is not configured")
        worker = worker_id.strip()
        tenant = tenant_id.strip()
        if not worker or not _SAFE_REFERENCE.fullmatch(worker):
            raise ValueError("worker_id must be a privacy-safe identifier")
        if not tenant or not _SAFE_REFERENCE.fullmatch(tenant):
            raise ValueError("tenant_id must be a privacy-safe identifier")
        material = self._repository.begin_replay(
            replay_request_id,
            worker_id=worker,
            tenant_id=tenant,
        )
        try:
            outcome = self._executor.execute(material)
            if not isinstance(outcome, ProviderReplayExecutionResult):
                raise TypeError("executor returned an invalid replay result")
        except Exception:
            # Exception strings can contain provider query or response details.
            # Persist one stable code and discard the message at this boundary.
            return self._repository.record_outcome(
                replay_request_id,
                completed=False,
                outcome_code="EXECUTOR_EXCEPTION",
                executor_reference=None,
                worker_id=worker,
                tenant_id=tenant,
            )
        binding_verified = (
            outcome.object_sha256_verified
            and outcome.verified_replay_request_id
            == material.replay_request_id
            and outcome.verified_replay_object_id
            == material.replay_object_id
            and outcome.verified_object_version_id
            == material.approved_object_version_id
            and outcome.verified_object_sha256
            == material.approved_object_sha256
        )
        if not binding_verified:
            return self._repository.record_outcome(
                replay_request_id,
                completed=False,
                outcome_code="REPLAY_BINDING_NOT_VERIFIED",
                executor_reference=outcome.executor_reference,
                worker_id=worker,
                tenant_id=tenant,
            )
        return self._repository.record_outcome(
            replay_request_id,
            completed=outcome.completed,
            outcome_code=outcome.outcome_code,
            executor_reference=outcome.executor_reference,
            worker_id=worker,
            tenant_id=tenant,
        )


class LegalProviderReplayObjectRegistrar:
    """Trusted worker-only boundary for immutable object-store receipts.

    This use case is deliberately not mounted on the operational HTTP router.
    Its connection must use the dedicated legal-operations principal, and it
    may be called only after the object-store client has returned a versioned
    retention receipt.
    """

    def __init__(
        self,
        repository: LegalProviderReplayRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def register(
        self,
        receipt: LegalProviderReplayObjectReceipt,
    ) -> dict[str, Any]:
        now = _aware(self._clock())
        if _aware(receipt.retention_until) <= now:
            raise ValueError("replay object retention must be in the future")
        result = self._repository.register_replay_object(receipt)
        return {
            "schemaVersion": "realytics-legal-provider-replay-object-v1",
            **result,
            "appendOnly": True,
            "objectLocatorReturned": False,
            "objectDigestReturned": False,
        }


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("replay approval time must be timezone-aware")
    return value


__all__ = [
    "LegalProviderReplayExecutor",
    "LegalProviderReplayObjectRegistrar",
    "LegalProviderReplayRepository",
    "LegalProviderReplayService",
    "ProviderReplayExecutionResult",
]
