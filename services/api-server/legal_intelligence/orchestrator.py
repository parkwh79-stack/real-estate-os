"""Provider-independent legal retrieval orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .models import (
    CoverageStatus,
    DataClassification,
    FreshnessStatus,
    LegalResourceKind,
    LegalSearchQuery,
    LegalSearchResponse,
    ProviderDiagnostic,
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
from .providers import ProviderProtocol


class LegalIntelligenceOrchestrator:
    """Local-first evidence retrieval with explicit external fallbacks."""

    def __init__(
        self,
        *,
        local_provider: ProviderProtocol,
        official_provider: ProviderProtocol,
        self_hosted_mcp: ProviderProtocol | None = None,
        remote_mcp: ProviderProtocol | None = None,
        allow_remote_mcp: bool = False,
    ) -> None:
        self._local = local_provider
        self._official = official_provider
        self._self_hosted_mcp = self_hosted_mcp
        self._remote_mcp = remote_mcp
        self._allow_remote_mcp = allow_remote_mcp

    def search(
        self,
        query: LegalSearchQuery,
        *,
        caller_context: Mapping[str, Any] | None = None,
    ) -> LegalSearchResponse:
        trace: list[ProviderResult] = []
        candidate_hits = _caller_candidate_hits(query, caller_context)

        local = self._local.search(query)
        trace.append(local)
        authoritative, candidates = _partition_hits(local.hits)
        candidate_hits.extend(candidates)
        if local.can_satisfy_from_verified_local_snapshot:
            return LegalSearchResponse(
                query=query,
                authoritative_hits=sort_hits(authoritative, limit=query.limit),
                candidate_hits=sort_hits(candidate_hits, limit=query.limit),
                provider_trace=tuple(trace),
                satisfied_by_local_snapshot=True,
            )

        # ADR order is Local -> Official Direct -> Self-hosted MCP -> Remote.
        # For confidential queries the official stage remains visible as an
        # explicit zero-attempt skip; no payload crosses that external boundary.
        official = self._search_external_or_skip(
            self._official,
            query,
        )
        trace.append(official)
        official_authoritative, official_candidates = _partition_hits(
            official.hits
        )
        authoritative.extend(official_authoritative)
        candidate_hits.extend(official_candidates)

        # A self-hosted bridge is a transport, not source authority. It runs
        # only when the higher-priority local and official stages did not yield
        # authoritative evidence.
        if not authoritative and self._self_hosted_mcp is not None:
            bridge = self._self_hosted_mcp.search(query)
            trace.append(bridge)
            bridge_authoritative, bridge_candidates = _partition_hits(
                bridge.hits
            )
            authoritative.extend(bridge_authoritative)
            candidate_hits.extend(bridge_candidates)

        # Remote MCP is an explicit opt-in last resort.  It is never used merely
        # because it was configured.
        if (
            not authoritative
            and self._remote_mcp is not None
            and self._allow_remote_mcp
        ):
            remote = self._search_external_or_skip(
                self._remote_mcp,
                query,
            )
            trace.append(remote)
            remote_authoritative, remote_candidates = _partition_hits(remote.hits)
            authoritative.extend(remote_authoritative)
            candidate_hits.extend(remote_candidates)

        return LegalSearchResponse(
            query=query,
            authoritative_hits=sort_hits(authoritative, limit=query.limit),
            candidate_hits=sort_hits(candidate_hits, limit=query.limit),
            provider_trace=tuple(trace),
            satisfied_by_local_snapshot=False,
        )

    @staticmethod
    def _search_external_or_skip(
        provider: ProviderProtocol,
        query: LegalSearchQuery,
    ) -> ProviderResult:
        if query.data_classification in {
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        }:
            return provider.search(query)
        return ProviderResult(
            provider_id=provider.provider_id,
            status=ProviderStatus.SKIPPED,
            transport=provider.transport,
            coverage=CoverageStatus.UNKNOWN,
            freshness=FreshnessStatus.UNKNOWN,
            diagnostics=(
                ProviderDiagnostic(
                    code="EXTERNAL_EGRESS_BLOCKED_BY_CLASSIFICATION",
                    message=(
                        "External legal lookup was not called because the query "
                        f"is classified as {query.data_classification.value}."
                    ),
                    retryable=False,
                ),
            ),
            attempts=0,
        )


def _partition_hits(
    hits: tuple[SearchHit, ...],
) -> tuple[list[SearchHit], list[SearchHit]]:
    authoritative: list[SearchHit] = []
    candidates: list[SearchHit] = []
    for hit in hits:
        (authoritative if hit.is_authoritative_evidence else candidates).append(hit)
    return authoritative, candidates


def _caller_candidate_hits(
    query: LegalSearchQuery,
    caller_context: Mapping[str, Any] | None,
) -> list[SearchHit]:
    if not caller_context:
        return []
    raw_references = caller_context.get("references", [])
    if isinstance(raw_references, Mapping):
        raw_references = [raw_references]
    if not isinstance(raw_references, list):
        return []

    hits: list[SearchHit] = []
    for index, raw in enumerate(raw_references):
        if not isinstance(raw, Mapping):
            continue
        content = _caller_candidate_content(raw)
        title = _bounded_text(
            raw.get("lawName")
            or raw.get("title")
            or f"caller-reference-{index + 1}",
            limit=500,
        )
        resource_id = f"caller-candidate:{index}"
        locator = f"caller-context:{resource_id}"
        kind_value = raw.get("resourceKind")
        try:
            kind = LegalResourceKind(str(kind_value)) if kind_value else query.resource_kinds[0]
        except ValueError:
            kind = query.resource_kinds[0]
        attestation = build_attestation(
            content=content,
            source_authority=SourceAuthority.CALLER_SUPPLIED,
            source_transport=SourceTransport.CALLER_CONTEXT,
            trust_level=TrustLevel.UNTRUSTED_CANDIDATE,
            resource_kind=kind,
            official_resource_id=resource_id,
            locator=locator,
            as_of=query.as_of,
            fetched_at=datetime.now(timezone.utc),
        )
        hits.append(
            SearchHit(
                title=title,
                excerpt=None,
                stage=SearchStage.CALLER_CANDIDATE,
                lexical_score=0.0,
                content=content,
                attestation=attestation,
            )
        )
    return hits


def _caller_candidate_content(raw: Mapping[str, Any]) -> dict[str, str]:
    allowed_keys = (
        "lawName",
        "title",
        "citation",
        "article",
        "lawId",
        "resourceId",
        "resourceKind",
        "effectiveDate",
        "summary",
    )
    return {
        key: text
        for key in allowed_keys
        if (text := _bounded_text(raw.get(key), limit=4_000))
    }


def _bounded_text(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]
