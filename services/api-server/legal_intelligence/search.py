"""Deterministic local legal snapshot retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Mapping

from .models import (
    CoverageStatus,
    FreshnessStatus,
    LegalResourceKind,
    LegalSearchQuery,
    ProviderResult,
    ProviderStatus,
    SearchHit,
    SearchStage,
    SourceAuthority,
    SourceTransport,
    TrustLevel,
    build_attestation,
    sort_hits,
)


def _normalize(value: str) -> str:
    return re.sub(r"[\s·ㆍ_\-]+", "", value or "").casefold()


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[0-9A-Za-z가-힣]+", value or "")
        if token.strip()
    }


@dataclass(frozen=True)
class LegalSnapshot:
    resource_kind: LegalResourceKind
    official_resource_id: str
    title: str
    content: Mapping[str, object]
    locator: str
    source_authority: SourceAuthority = SourceAuthority.LAW_GO_KR
    identifiers: tuple[str, ...] = ()
    approved_aliases: tuple[str, ...] = ()
    searchable_text: str = ""
    official_version_id: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    fetched_at: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)
    verified_at: datetime | None = None


class InMemoryVerifiedSnapshotProvider:
    """Reference implementation of the PostgreSQL retrieval contract.

    Production repositories can implement the same ``search`` method with exact
    indexes, approved alias joins, FTS and pg_trgm.
    """

    provider_id = "local_verified_legal_snapshot"
    transport = SourceTransport.LOCAL_VERIFIED_SNAPSHOT

    def __init__(
        self,
        snapshots: Iterable[LegalSnapshot],
        *,
        coverage_complete: bool,
        fresh_until: datetime | None,
        now: callable = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._snapshots = tuple(snapshots)
        self._coverage_complete = coverage_complete
        self._fresh_until = fresh_until
        self._now = now

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        coverage = CoverageStatus.COMPLETE if self._coverage_complete else CoverageStatus.PARTIAL
        freshness = (
            FreshnessStatus.FRESH
            if self._fresh_until is not None and self._fresh_until >= self._now()
            else FreshnessStatus.STALE
            if self._fresh_until is not None
            else FreshnessStatus.UNKNOWN
        )
        hits: list[SearchHit] = []
        for snapshot in self._snapshots:
            if snapshot.resource_kind not in query.resource_kinds:
                continue
            if not _is_effective(snapshot, query.as_of):
                continue
            # An ingested row is not a verified snapshot until it has passed an
            # explicit verification step.
            if snapshot.verified_at is None:
                continue
            match = _match_snapshot(snapshot, query)
            if match is None:
                continue
            stage, score = match
            attestation = build_attestation(
                content=snapshot.content,
                source_authority=snapshot.source_authority,
                source_transport=self.transport,
                trust_level=TrustLevel.VERIFIED_SNAPSHOT,
                resource_kind=snapshot.resource_kind,
                official_resource_id=snapshot.official_resource_id,
                official_version_id=snapshot.official_version_id,
                locator=snapshot.locator,
                as_of=query.as_of,
                fetched_at=snapshot.fetched_at,
                verified_at=snapshot.verified_at,
                verified_effective_date=snapshot.effective_from,
                effective_from=snapshot.effective_from,
                effective_to=snapshot.effective_to,
            )
            hits.append(
                SearchHit(
                    title=snapshot.title,
                    excerpt=snapshot.searchable_text or None,
                    stage=stage,
                    lexical_score=score,
                    content=snapshot.content,
                    attestation=attestation,
                )
            )
        ordered = sort_hits(hits, limit=query.limit)
        return ProviderResult(
            provider_id=self.provider_id,
            status=ProviderStatus.SUCCESS if ordered else ProviderStatus.NO_MATCH,
            transport=self.transport,
            hits=ordered,
            coverage=coverage,
            freshness=freshness,
            attempts=1,
        )


def _is_effective(snapshot: LegalSnapshot, as_of: date) -> bool:
    if snapshot.effective_from is not None and as_of < snapshot.effective_from:
        return False
    if snapshot.effective_to is not None and as_of > snapshot.effective_to:
        return False
    return True


def _match_snapshot(
    snapshot: LegalSnapshot,
    query: LegalSearchQuery,
) -> tuple[SearchStage, float] | None:
    query_text = _normalize(query.text)
    identifiers = {_normalize(value) for value in (*snapshot.identifiers, snapshot.official_resource_id)}
    requested_identifiers = {_normalize(value) for value in query.identifiers}
    if query_text in identifiers or bool(identifiers & requested_identifiers):
        return SearchStage.EXACT_IDENTIFIER, 1.0

    if query_text == _normalize(snapshot.title):
        return SearchStage.EXACT_NAME, 1.0

    approved_aliases = {_normalize(value) for value in snapshot.approved_aliases}
    requested_aliases = {_normalize(value) for value in query.approved_aliases}
    if query_text in approved_aliases or bool(approved_aliases & requested_aliases):
        return SearchStage.APPROVED_ALIAS, 1.0

    query_tokens = _tokens(" ".join((query.text, *query.approved_aliases)))
    source_tokens = _tokens(
        " ".join(
            (
                snapshot.title,
                snapshot.searchable_text,
                *snapshot.identifiers,
                *snapshot.approved_aliases,
            )
        )
    )
    if not query_tokens or not source_tokens:
        return None
    overlap = len(query_tokens & source_tokens) / len(query_tokens)
    if overlap <= 0:
        return None
    return SearchStage.LEXICAL, min(1.0, overlap)
