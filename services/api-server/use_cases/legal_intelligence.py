"""Application boundary for trusted legal retrieval and Legal Pod handoff.

Caller and MCP data remain discovery candidates.  Only official source
attestations or verified local snapshots enter ``authoritativeHits``.
"""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from agents.korean_law_context import (
    build_korean_law_mcp_query_plan,
    ordinance_query_plan,
    build_server_issued_law_context,
)
from db.postgres import postgres_configured
from db.legal_operations_database import (
    legal_operations_database_configured,
)
from db.repositories.legal_query_run_repository import (
    LegalQueryAuditContext,
    PostgresLegalQueryRunRepository,
)
from db.repositories.legal_intelligence_repository import (
    PostgresVerifiedLegalSnapshotProvider,
)
from db.repositories.legal_provider_operations_repository import (
    FailureIsolatedLegalProviderOperationsSink,
    LegalProviderOperationsSink,
    PostgresLegalProviderOperationsSink,
)
from legal_intelligence.models import (
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
    SourceTransport,
)
from legal_intelligence.orchestrator import LegalIntelligenceOrchestrator
from legal_intelligence.providers import (
    DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS,
    DEFAULT_PROVIDER_JSON_MAX_DEPTH,
    DEFAULT_PROVIDER_JSON_MAX_NODES,
    DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    DEFAULT_LAW_GO_KR_ALLOWED_HOSTS,
    LawGoKrDirectProvider,
    McpBridgeProvider,
    ProviderProtocol,
)
from legal_intelligence.search import InMemoryVerifiedSnapshotProvider
from legal_intelligence.resilience import shared_provider_rate_limiter


LEGAL_INTELLIGENCE_SCHEMA_VERSION = "realytics-legal-intelligence-v1"
LEGAL_RANKING_POLICY_VERSION = "legal-ranking-exact-first-v1"
LEGAL_FRESHNESS_POLICY_VERSION = "legal-freshness-v1"


class UnavailableLegalProvider:
    """Explicit fail-closed result for an unconfigured provider."""

    provider_id = "law_go_kr_unconfigured"
    transport = SourceTransport.LAW_GO_KR_DIRECT_API

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        return ProviderResult(
            provider_id=self.provider_id,
            status=ProviderStatus.SKIPPED,
            transport=self.transport,
            coverage=CoverageStatus.UNKNOWN,
            freshness=FreshnessStatus.UNKNOWN,
            diagnostics=(
                ProviderDiagnostic(
                    code="LAW_GO_KR_NOT_CONFIGURED",
                    message=(
                        "Official Law.go.kr lookup is unavailable because LAW_OC "
                        "or REALYTICS_LAW_OC is not configured."
                    ),
                    retryable=False,
                ),
            ),
            attempts=0,
        )


@dataclass
class LegalIntelligenceService:
    orchestrator: LegalIntelligenceOrchestrator
    local_provider: ProviderProtocol
    query_run_repository: Any | None = None

    def search(
        self,
        *,
        query: str,
        resource_kinds: Sequence[LegalResourceKind],
        as_of: date,
        limit: int = 10,
        identifiers: Sequence[str] = (),
        approved_aliases: Sequence[str] = (),
        data_classification: DataClassification = (
            DataClassification.PROJECT_CONFIDENTIAL
        ),
        caller_context: Mapping[str, Any] | None = None,
        audit_context: LegalQueryAuditContext | None = None,
    ) -> LegalSearchResponse:
        contract = LegalSearchQuery(
            text=query,
            resource_kinds=tuple(resource_kinds),
            identifiers=tuple(identifiers),
            approved_aliases=tuple(approved_aliases),
            as_of=as_of,
            limit=limit,
            data_classification=data_classification,
        )
        response = self.orchestrator.search(
            contract,
            caller_context=caller_context,
        )
        if audit_context is not None:
            if self.query_run_repository is None:
                raise RuntimeError(
                    "project-scoped legal search requires the normalized "
                    "query-run repository"
                )
            query_run_id = self.query_run_repository.record_search(
                audit_context,
                contract,
                response,
                freshness_policy_version=LEGAL_FRESHNESS_POLICY_VERSION,
                ranking_policy_version=LEGAL_RANKING_POLICY_VERSION,
            )
            response = response.model_copy(
                update={"query_run_id": query_run_id}
            )
        return response

    def get_resource(
        self,
        resource_id: str,
        *,
        as_of: date,
        provision_limit: int = 500,
        provision_offset: int = 0,
    ) -> dict[str, Any] | None:
        getter = getattr(self.local_provider, "get_resource", None)
        if not callable(getter):
            return None
        return getter(
            resource_id,
            as_of=as_of,
            provision_limit=provision_limit,
            provision_offset=provision_offset,
        )


def build_legal_intelligence_service(
    *,
    environ: Mapping[str, str] | None = None,
    local_provider: ProviderProtocol | None = None,
    official_provider: ProviderProtocol | None = None,
    provider_operations_sink: LegalProviderOperationsSink | None = None,
    retain_provider_operations: bool = True,
) -> LegalIntelligenceService:
    env = os.environ if environ is None else environ
    operations_sink = provider_operations_sink if retain_provider_operations else None
    if (
        retain_provider_operations
        and operations_sink is None
        and postgres_configured()
        and legal_operations_database_configured(environ=env)
    ):
        operations_sink = FailureIsolatedLegalProviderOperationsSink(
            PostgresLegalProviderOperationsSink()
        )
    if local_provider is None:
        local_provider = (
            PostgresVerifiedLegalSnapshotProvider()
            if postgres_configured()
            else InMemoryVerifiedSnapshotProvider(
                (),
                coverage_complete=False,
                fresh_until=None,
            )
        )

    law_oc = str(env.get("LAW_OC") or env.get("REALYTICS_LAW_OC") or "").strip()
    configured_law_hosts = str(
        env.get("REALYTICS_KOREAN_LAW_ALLOWED_HOSTS") or ""
    )
    allowed_law_hosts = tuple(
        item.strip().casefold()
        for item in configured_law_hosts.split(",")
        if item.strip()
    ) or tuple(sorted(DEFAULT_LAW_GO_KR_ALLOWED_HOSTS))
    provider_rate_limiter = shared_provider_rate_limiter(
        capacity=_positive_float(
            env.get("REALYTICS_LEGAL_PROVIDER_RATE_LIMIT_BURST"),
            default=30.0,
            label="REALYTICS_LEGAL_PROVIDER_RATE_LIMIT_BURST",
        ),
        refill_tokens_per_second=_positive_float(
            env.get("REALYTICS_LEGAL_PROVIDER_RATE_LIMIT_PER_SECOND"),
            default=5.0,
            label="REALYTICS_LEGAL_PROVIDER_RATE_LIMIT_PER_SECOND",
        ),
    )
    if official_provider is None:
        official_provider = (
            LawGoKrDirectProvider(
                oc=law_oc,
                base_url=str(
                    env.get("REALYTICS_KOREAN_LAW_API_URL")
                    or "https://www.law.go.kr/DRF/lawSearch.do"
                ).strip(),
                allowed_hosts=allowed_law_hosts,
                max_response_bytes=_positive_int(
                    env.get(
                        "REALYTICS_LEGAL_PROVIDER_MAX_RESPONSE_BYTES"
                    ),
                    default=DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
                    label=(
                        "REALYTICS_LEGAL_PROVIDER_MAX_RESPONSE_BYTES"
                    ),
                ),
                json_max_depth=_positive_int(
                    env.get("REALYTICS_LEGAL_PROVIDER_JSON_MAX_DEPTH"),
                    default=DEFAULT_PROVIDER_JSON_MAX_DEPTH,
                    label="REALYTICS_LEGAL_PROVIDER_JSON_MAX_DEPTH",
                ),
                json_max_collection_items=_positive_int(
                    env.get(
                        "REALYTICS_LEGAL_PROVIDER_JSON_MAX_COLLECTION_ITEMS"
                    ),
                    default=(
                        DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS
                    ),
                    label=(
                        "REALYTICS_LEGAL_PROVIDER_JSON_MAX_COLLECTION_ITEMS"
                    ),
                ),
                json_max_nodes=_positive_int(
                    env.get("REALYTICS_LEGAL_PROVIDER_JSON_MAX_NODES"),
                    default=DEFAULT_PROVIDER_JSON_MAX_NODES,
                    label="REALYTICS_LEGAL_PROVIDER_JSON_MAX_NODES",
                ),
                rate_limiter=provider_rate_limiter,
                quarantine_sink=operations_sink,
                dead_letter_sink=operations_sink,
                telemetry=operations_sink,
            )
            if law_oc
            else UnavailableLegalProvider()
        )

    local_mcp_url = str(
        env.get("REALYTICS_LEGAL_MCP_LOCAL_URL")
        or env.get("REALYTICS_KOREAN_LAW_MCP_URL")
        or ""
    ).strip()
    local_mcp_allowed_hosts = tuple(
        item.strip().casefold()
        for item in str(
            env.get("REALYTICS_LEGAL_MCP_LOCAL_ALLOWED_HOSTS") or ""
        ).split(",")
        if item.strip()
    )
    remote_mcp_url = str(env.get("REALYTICS_LEGAL_MCP_REMOTE_URL") or "").strip()
    remote_enabled = _truthy(env.get("REALYTICS_LEGAL_REMOTE_MCP_ENABLED"))
    self_hosted_mcp = (
        McpBridgeProvider(
            url=local_mcp_url,
            allowed_hosts=local_mcp_allowed_hosts,
            rate_limiter=provider_rate_limiter,
            quarantine_sink=operations_sink,
            dead_letter_sink=operations_sink,
            telemetry=operations_sink,
        )
        if local_mcp_url
        else None
    )
    remote_mcp = (
        McpBridgeProvider(
            url=remote_mcp_url,
            remote=True,
            allow_remote=remote_enabled,
            rate_limiter=provider_rate_limiter,
            quarantine_sink=operations_sink,
            dead_letter_sink=operations_sink,
            telemetry=operations_sink,
        )
        if remote_mcp_url
        else None
    )
    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=local_provider,
        official_provider=official_provider,
        self_hosted_mcp=self_hosted_mcp,
        remote_mcp=remote_mcp,
        allow_remote_mcp=remote_enabled,
        telemetry=operations_sink,
    )
    return LegalIntelligenceService(
        orchestrator=orchestrator,
        local_provider=local_provider,
        query_run_repository=(
            PostgresLegalQueryRunRepository()
            if retain_provider_operations and postgres_configured()
            else None
        ),
    )


def search_legal_resources(
    query: str,
    *,
    resource_kind: LegalResourceKind | None = None,
    as_of: date | None = None,
    limit: int = 10,
    public_query_acknowledged: bool = False,
    audit_context: LegalQueryAuditContext | None = None,
    service: LegalIntelligenceService | None = None,
) -> dict[str, Any]:
    kinds = (
        (resource_kind,)
        if resource_kind is not None
        else tuple(LegalResourceKind)
    )
    response = (service or build_legal_intelligence_service()).search(
        query=query,
        resource_kinds=kinds,
        as_of=as_of or date.today(),
        limit=limit,
        data_classification=(
            DataClassification.PUBLIC
            if public_query_acknowledged
            else DataClassification.PROJECT_CONFIDENTIAL
        ),
        audit_context=audit_context,
    )
    payload = serialize_legal_search_response(response)
    payload["externalEgressAcknowledged"] = public_query_acknowledged
    payload["externalEgressPolicy"] = (
        "public_query_external_fallback_allowed"
        if public_query_acknowledged
        else "local_or_trusted_internal_only_until_public_query_acknowledged"
    )
    return payload


def get_legal_resource(
    resource_id: str,
    *,
    as_of: date | None = None,
    provision_limit: int = 500,
    provision_offset: int = 0,
    service: LegalIntelligenceService | None = None,
) -> dict[str, Any] | None:
    return (service or build_legal_intelligence_service()).get_resource(
        resource_id,
        as_of=as_of or date.today(),
        provision_limit=provision_limit,
        provision_offset=provision_offset,
    )


def seal_legal_review_evidence_snapshot(
    legal_review: Mapping[str, Any],
    *,
    repository: Any,
    created_by: str,
) -> dict[str, Any]:
    """Seal exact local-corpus citations for one Legal Pod review.

    Resource-level search hits, direct API rows, MCP output, caller context,
    and references without an explicit research claim/excerpt stay visible but
    cannot be sealed. The repository independently rechecks every corpus
    identity and locator inside the PostgreSQL transaction.
    """

    updated_review = deepcopy(dict(legal_review))
    intelligence_value = updated_review.get("legalIntelligence")
    intelligence = (
        deepcopy(dict(intelligence_value))
        if isinstance(intelligence_value, Mapping)
        else {}
    )
    references = intelligence.get("references")
    references = references if isinstance(references, list) else []
    review_id = str(updated_review.get("reviewId") or "").strip()
    if not review_id:
        intelligence.update(
            {
                "evidenceSnapshotStatus": "not_sealed",
                "evidenceSnapshotId": None,
                "snapshotGateEligible": False,
                "verifiedCitationCount": 0,
            }
        )
        updated_review["legalIntelligence"] = intelligence
        return {
            "sealed": False,
            "reason": "legal_review_id_missing",
            "eligibleReferenceCount": 0,
            "skippedReferences": [],
            "legalReview": updated_review,
        }
    exact_items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, reference in enumerate(references):
        if not isinstance(reference, Mapping):
            skipped.append(
                {"index": index, "reason": "reference_not_an_object"}
            )
            continue
        missing = _missing_snapshot_reference_fields(reference)
        contexts = reference.get("claimContexts")
        contexts = contexts if isinstance(contexts, list) else []
        excerpt = str(reference.get("summary") or "").strip()
        if not contexts and reference.get("claimText"):
            contexts = [
                {
                    "purpose": str(reference.get("claimText")),
                    "query": "",
                    "tool": "legal_review",
                }
            ]
        if missing or not contexts or not excerpt:
            skipped.append(
                {
                    "index": index,
                    "officialId": reference.get("lawId"),
                    "reason": (
                        "missing_exact_local_corpus_lineage"
                        if missing
                        else "claim_context_or_excerpt_missing"
                    ),
                    "missingFields": missing,
                }
            )
            continue
        for context in contexts:
            if not isinstance(context, Mapping):
                continue
            claim_text = str(
                context.get("purpose")
                or context.get("claimText")
                or ""
            ).strip()
            if not claim_text:
                continue
            exact_items.append(
                {
                    "transport": reference.get("transport"),
                    "verificationStatus": reference.get(
                        "verificationStatus"
                    ),
                    "resourceId": reference.get("resourceId"),
                    "resourceVersionId": reference.get(
                        "resourceVersionId"
                    ),
                    "sourceFetchId": reference.get("sourceFetchId"),
                    "legalProvisionId": (
                        reference.get("legalProvisionId")
                        or reference.get("provisionId")
                    ),
                    "exactLocator": reference.get("locator"),
                    "normalizedContentSha256": reference.get(
                        "normalizedContentSha256"
                    ),
                    "verifiedAt": reference.get("verifiedAt"),
                    "claimText": claim_text,
                    "excerpt": excerpt,
                    "metadata": {
                        "legalReviewId": review_id,
                        "officialId": reference.get("lawId"),
                        "lawName": reference.get("lawName")
                        or reference.get("title"),
                        "citation": reference.get("citation")
                        or reference.get("article"),
                        "retrievalQuery": context.get("query"),
                        "retrievalTool": context.get("tool"),
                        "claimKind": "research_question",
                    },
                }
            )

    if not exact_items:
        intelligence.update(
            {
                "evidenceSnapshotStatus": "not_sealed",
                "evidenceSnapshotId": None,
                "snapshotGateEligible": False,
                "verifiedCitationCount": 0,
            }
        )
        updated_review["legalIntelligence"] = intelligence
        return {
            "sealed": False,
            "reason": "exact_verified_local_citations_missing",
            "eligibleReferenceCount": 0,
            "skippedReferences": skipped,
            "legalReview": updated_review,
        }

    open_conflict_count = _non_negative_count(
        intelligence.get("openConflictCount"),
        intelligence.get("openConflicts"),
    )
    expected_claim_count = _non_negative_count(
        intelligence.get("requiredClaimCount"),
        None,
    )
    verified_claim_count = _non_negative_count(
        intelligence.get("verifiedClaimCount"),
        None,
    )
    if expected_claim_count < 1:
        # Legacy/manual review payloads have no authoritative completeness
        # contract and therefore cannot become approval-grade.
        expected_claim_count = max(1, len(references))
        verified_claim_count = 0
    required_claim_set_id = str(
        intelligence.get("requiredClaimSetId") or ""
    ).strip()
    if not required_claim_set_id:
        resolver = getattr(
            repository,
            "latest_approved_required_claim_set_id",
            None,
        )
        if callable(resolver):
            required_claim_set_id = str(resolver() or "").strip()
    if not required_claim_set_id:
        intelligence.update(
            {
                "evidenceSnapshotStatus": "not_sealed",
                "evidenceSnapshotId": None,
                "snapshotGateEligible": False,
                "verifiedCitationCount": 0,
                "claimCoverageComplete": False,
            }
        )
        updated_review["legalIntelligence"] = intelligence
        return {
            "sealed": False,
            "reason": "approved_required_claim_set_missing",
            "eligibleReferenceCount": len(exact_items),
            "skippedReferences": skipped,
            "legalReview": updated_review,
        }
    snapshot = repository.append_verified_evidence_snapshot(
        {
            "asOfDate": intelligence.get("asOfDate"),
            "freshnessPolicyVersion": intelligence.get(
                "freshnessPolicyVersion"
            )
            or LEGAL_FRESHNESS_POLICY_VERSION,
            "hasOpenConflicts": open_conflict_count > 0,
            "stale": intelligence.get("stale") is not False,
            "expectedCitationCount": len(references),
            "expectedClaimCount": expected_claim_count,
            "verifiedClaimCount": verified_claim_count,
            "requiredClaimSetId": required_claim_set_id,
            "references": exact_items,
            "metadata": {
                "legalReviewId": review_id,
                "rankingPolicyVersion": intelligence.get(
                    "rankingPolicyVersion"
                )
                or LEGAL_RANKING_POLICY_VERSION,
                "referenceCount": len(exact_items),
            },
        },
        created_by=created_by,
    )
    gate_eligible = snapshot.get("gateEligible") is True
    intelligence.update(
        {
            "evidenceSnapshotStatus": "sealed",
            "evidenceSnapshotId": snapshot["legalEvidenceSnapshotId"],
            "snapshotCreatedBy": snapshot.get("createdBy") or created_by,
            "snapshotSha256": snapshot["snapshotSha256"],
            "snapshotVersion": snapshot["snapshotVersion"],
            "snapshotGateEligible": gate_eligible,
            "verifiedCitationCount": snapshot["verifiedCitationCount"],
            "citationCoverageComplete": snapshot["coverageComplete"],
            "requiredClaimCount": snapshot["expectedClaimCount"],
            "verifiedClaimCount": snapshot["verifiedClaimCount"],
            "claimCoverageComplete": snapshot["claimCoverageComplete"],
            "requiredClaimSetId": snapshot["requiredClaimSetId"],
            "requiredClaimSetVersion": snapshot[
                "requiredClaimSetVersion"
            ],
            "requiredClaimSetSha256": snapshot[
                "requiredClaimSetSha256"
            ],
            "stale": snapshot["stale"],
            "openConflictCount": int(
                bool(snapshot["hasOpenConflicts"])
            ),
            "evidenceStatus": (
                "verified" if gate_eligible else "partial"
            ),
        }
    )
    citation_verification = intelligence.get("citationVerification")
    citation_verification = (
        dict(citation_verification)
        if isinstance(citation_verification, Mapping)
        else {}
    )
    citation_verification.update(
        {
            "status": "verified" if gate_eligible else "partial",
            "verifiedCount": snapshot["verifiedCitationCount"],
            "snapshotId": snapshot["legalEvidenceSnapshotId"],
        }
    )
    intelligence["citationVerification"] = citation_verification
    updated_review["legalIntelligence"] = intelligence
    updated_review["legalEvidenceSnapshotId"] = snapshot[
        "legalEvidenceSnapshotId"
    ]
    # Sealing evidence is not a legal opinion and does not promote the
    # preliminary analysis into report eligibility.
    updated_review["analysisOnly"] = True
    updated_review["reportEligible"] = False
    return {
        "sealed": True,
        "reason": None,
        "eligibleReferenceCount": len(exact_items),
        "skippedReferences": skipped,
        "snapshot": snapshot,
        "legalReview": updated_review,
    }


def resolve_project_legal_context(
    project_data: Mapping[str, Any],
    *,
    caller_context: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    service: LegalIntelligenceService | None = None,
    max_queries: int | None = None,
    preview: bool = False,
    parcel_jurisdictions: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Resolve project law sources, with a read-only runtime for previews."""

    if preview and service is not None:
        raise ValueError("Preview requires the read-only legal intelligence runtime.")
    runtime = service or build_legal_intelligence_service(
        retain_provider_operations=not preview,
    )
    query_plan = build_korean_law_mcp_query_plan(dict(project_data))
    # Give exact jurisdiction queries a place in the bounded preview plan.
    # Preserve the project-wide statutory/permit queries first.
    if parcel_jurisdictions:
        statutes = [item for item in query_plan if item.get("tool") != "search_ordinance"]
        parcel_ordinances = [item for jurisdiction in parcel_jurisdictions for item in ordinance_query_plan(jurisdiction)]
        query_plan = [*statutes[:4], *parcel_ordinances, *query_plan]
    unique_queries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in query_plan:
        text = str(item.get("query") or "").strip()
        normalized = " ".join(text.casefold().split())
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        unique_queries.append(dict(item))
    query_limit = max_queries
    if query_limit is None:
        query_limit = int(os.environ.get("REALYTICS_LEGAL_PROJECT_QUERY_LIMIT", "8"))
        if parcel_jurisdictions:
            query_limit = max(query_limit, 4 + 2 * len(parcel_jurisdictions))
    unique_queries = unique_queries[: max(1, min(query_limit, 20))]

    authoritative: list[SearchHit] = []
    candidates: list[SearchHit] = []
    traces: list[ProviderResult] = []
    claim_contexts: dict[
        tuple[str, str | None, str],
        list[dict[str, str]],
    ] = {}
    basis_date = as_of or datetime.now(timezone(timedelta(hours=9))).date()
    for index, plan_item in enumerate(unique_queries):
        query = str(plan_item["query"])
        resource_kind = (
            LegalResourceKind.LOCAL_ORDINANCE
            if plan_item.get("tool") == "search_ordinance"
            else LegalResourceKind.STATUTE
        )
        response = runtime.search(
            query=query,
            resource_kinds=(resource_kind,),
            as_of=basis_date,
            limit=5,
            data_classification=DataClassification.PROJECT_CONFIDENTIAL,
            caller_context=caller_context if index == 0 else None,
        )
        candidates.extend(
            hit for hit in response.candidate_hits
            if resource_kind != LegalResourceKind.LOCAL_ORDINANCE
            or _matches_project_ordinance_title(hit, query)
        )
        for hit in response.authoritative_hits:
            if resource_kind == LegalResourceKind.LOCAL_ORDINANCE and (
                not _matches_project_ordinance_title(hit, query)
                or hit.attestation.requested_as_of_date != basis_date
            ):
                continue
            authoritative.append(hit)
            context = {
                "tool": str(plan_item.get("tool") or "search_law"),
                "query": str(plan_item.get("query") or ""),
                "purpose": str(
                    plan_item.get("purpose")
                    or "Verify the cited legal source for project review."
                ),
            }
            contexts = claim_contexts.setdefault(_hit_identity(hit), [])
            if context not in contexts:
                contexts.append(context)
        traces.extend(response.provider_trace)

    authoritative = _dedupe_hits(authoritative)
    candidates = _dedupe_hits(candidates)
    verified_references = []
    for hit in authoritative:
        if not hit.is_authoritative_evidence:
            continue
        reference = _legacy_reference(hit)
        reference["claimContexts"] = claim_contexts.get(
            _hit_identity(hit),
            [],
        )
        verified_references.append(reference)
    candidate_references = [_legacy_reference(hit) for hit in candidates]
    stale = _authoritative_evidence_is_stale(authoritative, traces)
    diagnostics = _serialized_diagnostics(traces)
    conflicts = _detect_verified_reference_conflicts(verified_references)
    verified_citation_count = sum(
        1
        for reference in verified_references
        if _has_exact_citation(reference)
    )
    required_claim_keys = {
        " ".join(str(item.get("query") or "").casefold().split())
        for item in unique_queries
        if str(item.get("query") or "").strip()
    }
    verified_claim_keys = {
        " ".join(str(context.get("query") or "").casefold().split())
        for reference in verified_references
        if _has_exact_citation(reference)
        for context in (
            reference.get("claimContexts")
            if isinstance(reference.get("claimContexts"), list)
            else []
        )
        if isinstance(context, Mapping)
        and str(context.get("query") or "").strip()
    }
    uncovered_claims = [
        {
            "tool": item.get("tool"),
            "query": item.get("query"),
            "purpose": item.get("purpose"),
        }
        for item in unique_queries
        if " ".join(
            str(item.get("query") or "").casefold().split()
        )
        not in verified_claim_keys
    ]
    claim_coverage_complete = (
        bool(required_claim_keys)
        and required_claim_keys.issubset(verified_claim_keys)
    )
    citation_coverage_complete = (
        bool(verified_references)
        and verified_citation_count == len(verified_references)
    )
    verification_status = (
        "conflicted"
        if conflicts
        else "stale"
        if verified_references
        and stale
        else "verified"
        if citation_coverage_complete and claim_coverage_complete
        else "partial"
        if verified_references
        else "unverified"
    )
    citation_status = (
        verification_status
        if verification_status in {"conflicted", "stale", "unverified"}
        else "verified"
        if citation_coverage_complete and claim_coverage_complete
        else "partial"
    )
    retrieved_at = datetime.now(timezone.utc).isoformat()
    provider_trace_payload = [_provider_payload(item) for item in traces]
    mcp_connected = any(
        item.get("transport") in {"self_hosted_mcp", "remote_mcp"}
        and item.get("status") in {"success", "partial", "no_match"}
        for item in provider_trace_payload
    )
    legal_intelligence = {
        "schemaVersion": LEGAL_INTELLIGENCE_SCHEMA_VERSION,
        "legalQueryRunId": response.query_run_id,
        "evidenceStatus": verification_status,
        "verifiedReferenceCount": len(verified_references),
        "verifiedCitationCount": verified_citation_count,
        "requiredClaimCount": len(required_claim_keys),
        "verifiedClaimCount": len(
            required_claim_keys.intersection(verified_claim_keys)
        ),
        "claimCoverageComplete": claim_coverage_complete,
        "citationCoverageComplete": citation_coverage_complete,
        "uncoveredClaims": uncovered_claims,
        "candidateReferenceCount": len(candidate_references),
        "stale": stale,
        "openConflicts": conflicts,
        "openConflictCount": len(conflicts),
        "asOfDate": basis_date.isoformat(),
        "rankingPolicyVersion": LEGAL_RANKING_POLICY_VERSION,
        "freshnessPolicyVersion": LEGAL_FRESHNESS_POLICY_VERSION,
        "humanReviewStatus": "pending",
        "citationVerification": {
            "status": citation_status,
            "checkedCount": len(verified_references),
            "verifiedCount": verified_citation_count,
            "failures": [
                *diagnostics,
                *(
                    [
                        {
                            "code": "EXACT_CITATION_INCOMPLETE",
                            "message": (
                                f"{len(verified_references) - verified_citation_count} "
                                "verified resource reference(s) do not yet have "
                                "an exact provision locator."
                            ),
                        }
                    ]
                    if verified_references
                    and verified_citation_count < len(verified_references)
                    else []
                ),
                *(
                    [
                        {
                            "code": "LEGAL_CLAIM_COVERAGE_INCOMPLETE",
                            "message": (
                                f"{len(uncovered_claims)} required legal "
                                "research claim(s) have no exact verified "
                                "citation."
                            ),
                        }
                    ]
                    if uncovered_claims
                    else []
                ),
                *conflicts,
            ],
        },
        "references": verified_references,
        "candidateReferences": candidate_references,
        "providerTrace": provider_trace_payload,
        "retrievedAt": retrieved_at,
    }
    law_context = build_server_issued_law_context({
        "version": LEGAL_INTELLIGENCE_SCHEMA_VERSION,
        "source": "legal-intelligence-core",
        # Connectivity and evidence authority are deliberately independent.
        "connected": mcp_connected,
        "retrievedAt": retrieved_at,
        "references": verified_references,
        "candidateReferences": candidate_references,
        "citationVerification": legal_intelligence["citationVerification"],
        "queryPlan": unique_queries,
    })
    return {
        "lawContext": law_context,
        "legalIntelligence": legal_intelligence,
    }


def serialize_legal_search_response(
    response: LegalSearchResponse,
) -> dict[str, Any]:
    freshness_by_transport = {
        item.transport: item.freshness
        for item in response.provider_trace
    }
    authoritative = [
        _hit_payload(
            hit,
            freshness=freshness_by_transport.get(
                hit.attestation.source_transport,
                FreshnessStatus.UNKNOWN,
            ),
        )
        for hit in response.authoritative_hits
    ]
    candidates = [
        _hit_payload(
            hit,
            freshness=freshness_by_transport.get(
                hit.attestation.source_transport,
                FreshnessStatus.UNKNOWN,
            ),
        )
        for hit in response.candidate_hits
    ]
    return {
        "schemaVersion": LEGAL_INTELLIGENCE_SCHEMA_VERSION,
        "query": response.query.text,
        "queryContract": response.query.model_dump(mode="json"),
        "asOfDate": response.query.as_of.isoformat(),
        "resourceType": (
            response.query.resource_kinds[0].value
            if len(response.query.resource_kinds) == 1
            else None
        ),
        "results": [*authoritative, *candidates],
        "authoritativeHits": authoritative,
        "candidateHits": candidates,
        "authoritativeCount": len(authoritative),
        "candidateCount": len(candidates),
        "providerTrace": [
            _provider_payload(item)
            for item in response.provider_trace
        ],
        "satisfiedByLocalSnapshot": response.satisfied_by_local_snapshot,
        "disclaimer": (
            "검색 결과는 예비 법률조사 자료이며 최종 법률의견 또는 "
            "프로젝트 승인 결론이 아닙니다."
        ),
    }


def _hit_payload(
    hit: SearchHit,
    *,
    freshness: FreshnessStatus,
) -> dict[str, Any]:
    attestation = hit.attestation
    content = dict(hit.content)
    stale = freshness == FreshnessStatus.STALE
    locator = attestation.locator
    return {
        "resourceId": str(
            content.get("resourceId")
            or attestation.official_resource_id
        ),
        "resourceVersionId": (
            content.get("resourceVersionId")
            or attestation.official_version_id
        ),
        "officialVersionId": attestation.official_version_id,
        "sourceFetchId": content.get("sourceFetchId"),
        "title": hit.title,
        "citation": attestation.article,
        "excerpt": hit.excerpt,
        "resourceType": attestation.resource_kind.value,
        "contentClass": (
            "official_text"
            if hit.is_authoritative_evidence
            else "unknown"
        ),
        "sourceAuthority": attestation.source_authority.value,
        "transport": attestation.source_transport.value,
        "verificationStatus": attestation.trust_level.value,
        "freshnessStatus": freshness.value,
        "stale": stale,
        "asOfDate": attestation.as_of.isoformat(),
        "requestedAsOfDate": attestation.requested_as_of_date.isoformat(),
        "verifiedEffectiveDate": _iso(
            attestation.verified_effective_date
        ),
        "effectiveFrom": _iso(attestation.effective_from),
        "effectiveTo": _iso(attestation.effective_to),
        "observedAt": _iso(attestation.observed_at),
        "fetchedAt": _iso(attestation.fetched_at),
        "verifiedAt": _iso(attestation.verified_at),
        "applicabilityVerified": (
            attestation.is_applicability_verified
        ),
        "sourceUrl": (
            locator if locator.startswith(("http://", "https://")) else None
        ),
        "locator": locator,
        "officialText": _first_string(
            content,
            "officialText",
            "official_text",
            "text",
            "content",
            "본문",
            "조문내용",
        ),
        "aiSummary": _first_string(content, "aiSummary", "ai_summary"),
        "humanOpinion": _first_string(
            content,
            "humanOpinion",
            "human_opinion",
            "counselOpinion",
        ),
        "normalizedContentSha256": (
            content.get("normalizedContentSha256")
            or attestation.content_hash
        ),
        "attestationSha256": attestation.content_hash,
        "stage": hit.stage.value,
        "score": hit.lexical_score,
        "content": content,
        "attestation": attestation.model_dump(mode="json"),
    }


def _provider_payload(result: ProviderResult) -> dict[str, Any]:
    return {
        "providerId": result.provider_id,
        "status": result.status.value,
        "transport": result.transport.value,
        "coverage": result.coverage.value,
        "freshness": result.freshness.value,
        "attempts": result.attempts,
        "authoritativeHitCount": sum(
            1 for hit in result.hits if hit.is_authoritative_evidence
        ),
        "candidateHitCount": sum(
            1 for hit in result.hits if not hit.is_authoritative_evidence
        ),
        "diagnostics": [
            item.model_dump(mode="json")
            for item in result.diagnostics
        ],
    }


def _matches_project_ordinance_title(hit: SearchHit, title: str) -> bool:
    # Broad search may include ordinances of another jurisdiction or subject.
    return (
        hit.attestation.resource_kind == LegalResourceKind.LOCAL_ORDINANCE
        and "".join(hit.title.split()) == "".join(title.split())
    )


def _legacy_reference(hit: SearchHit) -> dict[str, Any]:
    content = dict(hit.content)
    attestation = hit.attestation
    return {
        "type": attestation.resource_kind.value,
        "title": hit.title,
        "lawName": hit.title,
        "citation": attestation.article or "",
        "article": attestation.article or "",
        "lawId": attestation.official_resource_id,
        "resourceId": content.get("resourceId"),
        "resourceVersionId": (
            content.get("resourceVersionId")
            or attestation.official_version_id
        ),
        "officialVersionId": attestation.official_version_id,
        "sourceFetchId": content.get("sourceFetchId"),
        "provisionId": content.get("provisionId")
        or content.get("legalProvisionId"),
        "effectiveDate": _iso(attestation.effective_from) or "",
        "url": (
            attestation.locator
            if attestation.locator.startswith(("http://", "https://"))
            else ""
        ),
        "locator": attestation.locator,
        "summary": hit.excerpt or "",
        "verified": hit.is_authoritative_evidence,
        "sourceAuthority": attestation.source_authority.value,
        "transport": attestation.source_transport.value,
        "verificationStatus": attestation.trust_level.value,
        "contentHash": attestation.content_hash,
        "normalizedContentSha256": content.get("normalizedContentSha256"),
        "asOfDate": attestation.as_of.isoformat(),
        "requestedAsOfDate": attestation.requested_as_of_date.isoformat(),
        "verifiedEffectiveDate": _iso(
            attestation.verified_effective_date
        ),
        "effectiveFrom": _iso(attestation.effective_from),
        "effectiveTo": _iso(attestation.effective_to),
        "observedAt": attestation.observed_at.isoformat(),
        "fetchedAt": attestation.fetched_at.isoformat(),
        "verifiedAt": _iso(attestation.verified_at),
        "rawTool": "legal_intelligence_core",
    }


def _dedupe_hits(hits: Sequence[SearchHit]) -> list[SearchHit]:
    deduped: list[SearchHit] = []
    seen: set[tuple[str, str | None, str]] = set()
    for hit in hits:
        key = _hit_identity(hit)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


def _hit_identity(hit: SearchHit) -> tuple[str, str | None, str]:
    return (
        hit.attestation.official_resource_id,
        hit.attestation.official_version_id,
        hit.attestation.content_hash,
    )


def _has_exact_citation(reference: Mapping[str, Any]) -> bool:
    return bool(
        (
            reference.get("provisionId")
            or reference.get("legalProvisionId")
            or reference.get("article")
            or reference.get("citation")
        )
        and reference.get("resourceId")
        and reference.get("resourceVersionId")
        and reference.get("sourceFetchId")
        and reference.get("locator")
        and reference.get("normalizedContentSha256")
        and reference.get("verifiedAt")
    )


def _missing_snapshot_reference_fields(
    reference: Mapping[str, Any],
) -> list[str]:
    required = {
        "transport": reference.get("transport")
        == SourceTransport.LOCAL_VERIFIED_SNAPSHOT.value,
        "verificationStatus": reference.get("verificationStatus")
        == "verified_snapshot",
        "resourceId": bool(reference.get("resourceId")),
        "resourceVersionId": bool(reference.get("resourceVersionId")),
        "sourceFetchId": bool(reference.get("sourceFetchId")),
        "provisionId": bool(
            reference.get("provisionId")
            or reference.get("legalProvisionId")
        ),
        "locator": bool(reference.get("locator")),
        "normalizedContentSha256": bool(
            reference.get("normalizedContentSha256")
        ),
        "verifiedAt": bool(reference.get("verifiedAt")),
    }
    return [field for field, present in required.items() if not present]


def _non_negative_count(explicit: Any, value: Any) -> int:
    if explicit is not None:
        try:
            return max(0, int(explicit))
        except (TypeError, ValueError):
            return 0
    if isinstance(value, Mapping):
        return len(value)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return len(value)
    return int(value is True)


def _detect_verified_reference_conflicts(
    references: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Detect overlapping verified corpus identities that disagree.

    This does not infer legal meaning. It only prevents two different
    version/hash identities for the same official resource and as-of date from
    silently becoming a clean evidence set.
    """

    identities: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for reference in references:
        official_id = str(
            reference.get("lawId")
            or reference.get("officialId")
            or ""
        ).strip()
        as_of = str(reference.get("asOfDate") or "").strip()
        version_id = str(reference.get("resourceVersionId") or "").strip()
        content_hash = str(
            reference.get("normalizedContentSha256")
            or reference.get("contentHash")
            or ""
        ).strip()
        if not official_id or not as_of or not version_id or not content_hash:
            continue
        identities.setdefault((official_id, as_of), set()).add(
            (version_id, content_hash)
        )

    conflicts: list[dict[str, Any]] = []
    for (official_id, as_of), versions in sorted(identities.items()):
        if len(versions) <= 1:
            continue
        conflicts.append(
            {
                "code": "VERIFIED_RESOURCE_VERSION_CONFLICT",
                "message": (
                    "Multiple verified version/hash identities overlap for "
                    f"{official_id} as of {as_of}."
                ),
                "officialId": official_id,
                "asOfDate": as_of,
                "versionCount": len(versions),
            }
        )
    return conflicts


def _authoritative_evidence_is_stale(
    authoritative: Sequence[SearchHit],
    traces: Sequence[ProviderResult],
) -> bool:
    transports = {
        hit.attestation.source_transport
        for hit in authoritative
    }
    if not transports:
        return False
    relevant = [
        item
        for item in traces
        if item.transport in transports and item.hits
    ]
    if not relevant:
        return True
    return any(
        item.freshness != FreshnessStatus.FRESH
        for item in relevant
    )


def _serialized_diagnostics(
    traces: Sequence[ProviderResult],
) -> list[dict[str, Any]]:
    return [
        {
            "providerId": result.provider_id,
            **diagnostic.model_dump(mode="json"),
        }
        for result in traces
        for diagnostic in result.diagnostics
        if result.status in {
            ProviderStatus.FAILED,
            ProviderStatus.CIRCUIT_OPEN,
        }
    ]


def _first_string(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _positive_float(
    value: Any,
    *,
    default: float,
    label: str,
) -> float:
    if value is None or not str(value).strip():
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive number") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive number")
    return parsed


def _positive_int(
    value: Any,
    *,
    default: int,
    label: str,
) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return parsed


__all__ = [
    "LEGAL_INTELLIGENCE_SCHEMA_VERSION",
    "LegalIntelligenceService",
    "UnavailableLegalProvider",
    "build_legal_intelligence_service",
    "get_legal_resource",
    "resolve_project_legal_context",
    "seal_legal_review_evidence_snapshot",
    "search_legal_resources",
    "serialize_legal_search_response",
]
