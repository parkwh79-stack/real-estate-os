"""Application service for human-only Citator review."""

from __future__ import annotations

from typing import Any, Protocol

from legal_intelligence.citator_models import (
    CitatorRecordKind,
    CitatorReviewInput,
    CitatorReviewOutcome,
)


class LegalCitatorReviewRepository(Protocol):
    def list_review_queue(
        self,
        *,
        record_kind: CitatorRecordKind | None = None,
        limit: int = 100,
    ) -> dict[str, Any]: ...

    def review_citation(
        self,
        citation_id: str,
        review: CitatorReviewInput,
        *,
        reviewer_id: str,
        reviewer_role: str,
    ) -> CitatorReviewOutcome: ...

    def review_relation(
        self,
        assertion_id: str,
        review: CitatorReviewInput,
        *,
        reviewer_id: str,
        reviewer_role: str,
    ) -> CitatorReviewOutcome: ...


class LegalCitatorReviewService:
    """Expose the review queue without any machine finalization path."""

    def __init__(self, repository: LegalCitatorReviewRepository) -> None:
        self.repository = repository

    def list_queue(
        self,
        *,
        record_kind: CitatorRecordKind | None,
        limit: int,
    ) -> dict[str, Any]:
        queue = self.repository.list_review_queue(
            record_kind=record_kind,
            limit=limit,
        )
        return {
            "schemaVersion": "realytics-legal-citator-review-queue-v2",
            "authorityModel": "tenant_project_review_overlay",
            "corpusRecordsImmutable": True,
            **queue,
        }

    def review_citation(
        self,
        citation_id: str,
        review: CitatorReviewInput,
        *,
        reviewer_id: str,
        reviewer_role: str,
    ) -> dict[str, Any]:
        outcome = self.repository.review_citation(
            citation_id,
            review,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
        )
        return self._response(outcome)

    def review_relation(
        self,
        assertion_id: str,
        review: CitatorReviewInput,
        *,
        reviewer_id: str,
        reviewer_role: str,
    ) -> dict[str, Any]:
        outcome = self.repository.review_relation(
            assertion_id,
            review,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
        )
        return self._response(outcome)

    @staticmethod
    def _response(outcome: CitatorReviewOutcome) -> dict[str, Any]:
        return {
            "schemaVersion": "realytics-legal-citator-review-v2",
            "authorityModel": "tenant_project_review_overlay",
            "corpusRecordMutated": False,
            **outcome.model_dump(mode="json", by_alias=True),
        }


__all__ = [
    "LegalCitatorReviewRepository",
    "LegalCitatorReviewService",
]
