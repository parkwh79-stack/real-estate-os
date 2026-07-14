from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from agent_platform.contracts import (
    LearningCandidate,
    LearningCandidateStatus,
    LearningEvaluation,
    LearningPromotionDecision,
)
from agent_platform.operational import OperationalRepository


class LearningLifecycleError(RuntimeError):
    pass


class LearningLifecycleService:
    """Human-governed candidate, evaluation, canary, promotion, and rollback lifecycle."""

    def __init__(self, repository: OperationalRepository) -> None:
        self._repository = repository

    def create_candidate(self, candidate: LearningCandidate) -> LearningCandidate:
        if not candidate.evidence_ids and not candidate.source_run_ids:
            raise LearningLifecycleError("LEARNING_EVIDENCE_REQUIRED")
        existing = self._repository.get(LearningCandidate, candidate.candidate_id)
        if existing is not None:
            if (
                existing.tenant_id != candidate.tenant_id
                or existing.candidate_type != candidate.candidate_type
                or existing.target_key != candidate.target_key
                or existing.proposed_change != candidate.proposed_change
            ):
                raise LearningLifecycleError("LEARNING_CANDIDATE_ID_CONFLICT")
            return existing
        return self._repository.save(candidate)

    def submit(self, candidate_id: str) -> LearningCandidate:
        candidate = self._candidate(candidate_id)
        if candidate.status != LearningCandidateStatus.DRAFT:
            raise LearningLifecycleError("LEARNING_CANDIDATE_NOT_DRAFT")
        return self._save_status(candidate, LearningCandidateStatus.PENDING_EVALUATION)

    def record_evaluation(self, evaluation: LearningEvaluation) -> LearningCandidate:
        candidate = self._candidate(evaluation.candidate_id)
        if candidate.tenant_id != evaluation.tenant_id:
            raise LearningLifecycleError("LEARNING_TENANT_MISMATCH")
        if candidate.status not in {
            LearningCandidateStatus.PENDING_EVALUATION,
            LearningCandidateStatus.EVALUATING,
        }:
            raise LearningLifecycleError("LEARNING_CANDIDATE_NOT_EVALUATABLE")
        self._repository.save(evaluation)
        return self._save_status(
            candidate,
            LearningCandidateStatus.EVALUATED if evaluation.passed else LearningCandidateStatus.REJECTED,
        )

    def decide(self, decision: LearningPromotionDecision) -> LearningCandidate:
        candidate = self._candidate(decision.candidate_id)
        if candidate.tenant_id != decision.tenant_id:
            raise LearningLifecycleError("LEARNING_TENANT_MISMATCH")
        target = self._decision_target(candidate, decision)
        self._repository.save(decision)
        return self._save_status(candidate, target)

    def _decision_target(
        self,
        candidate: LearningCandidate,
        decision: LearningPromotionDecision,
    ) -> LearningCandidateStatus:
        if decision.action == "start_canary":
            if candidate.status != LearningCandidateStatus.EVALUATED:
                raise LearningLifecycleError("LEARNING_CANARY_REQUIRES_PASSED_EVALUATION")
            return LearningCandidateStatus.CANARY
        if decision.action == "promote":
            if candidate.status != LearningCandidateStatus.CANARY:
                raise LearningLifecycleError("LEARNING_PROMOTION_REQUIRES_CANARY")
            return LearningCandidateStatus.PROMOTED
        if decision.action == "rollback":
            if candidate.status != LearningCandidateStatus.PROMOTED:
                raise LearningLifecycleError("LEARNING_ROLLBACK_REQUIRES_PROMOTED_CANDIDATE")
            return LearningCandidateStatus.ROLLED_BACK
        if decision.action == "reject":
            if candidate.status == LearningCandidateStatus.PROMOTED:
                raise LearningLifecycleError("PROMOTED_CANDIDATE_MUST_BE_ROLLED_BACK")
            return LearningCandidateStatus.REJECTED
        raise LearningLifecycleError("LEARNING_ACTION_UNSUPPORTED")

    def _candidate(self, candidate_id: str) -> LearningCandidate:
        candidate = self._repository.get(LearningCandidate, candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        return candidate

    def _save_status(
        self,
        candidate: LearningCandidate,
        status: LearningCandidateStatus,
    ) -> LearningCandidate:
        return self._repository.save(candidate.model_copy(update={
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }))


def new_promotion_decision(
    *,
    candidate_id: str,
    tenant_id: str,
    action: str,
    environment: str,
    reason: str,
    approved_by: str,
    rollback_of_decision_id: str | None = None,
) -> LearningPromotionDecision:
    return LearningPromotionDecision(
        decision_id=f"learning-decision:{uuid4()}",
        candidate_id=candidate_id,
        tenant_id=tenant_id,
        action=action,
        environment=environment,
        reason=reason,
        approved_by=approved_by,
        approved_at=datetime.now(timezone.utc),
        rollback_of_decision_id=rollback_of_decision_id,
    )
