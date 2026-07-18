from __future__ import annotations

from datetime import date, datetime, timezone

from agents.korean_law_context import build_korean_law_mcp_query_plan
from db import postgres
from db.repositories import legal_intelligence_repository
from db.repositories.legal_intelligence_repository import (
    PostgresVerifiedLegalSnapshotProvider,
)
from legal_intelligence.models import (
    CoverageStatus,
    DataClassification,
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
)
from legal_intelligence.orchestrator import LegalIntelligenceOrchestrator
from legal_intelligence.search import InMemoryVerifiedSnapshotProvider
from use_cases.legal_intelligence import (
    LegalIntelligenceService,
    UnavailableLegalProvider,
    resolve_project_legal_context,
    search_legal_resources,
    seal_legal_review_evidence_snapshot,
    serialize_legal_search_response,
)


NOW = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)


def test_local_coverage_tracks_current_provider_but_corpus_reads_pin_fetch_revision():
    migration = (
        postgres.MIGRATIONS_DIR / "0008_legal_intelligence_core.sql"
    ).read_text(encoding="utf-8")
    provider_body = migration.split(
        "CREATE TABLE IF NOT EXISTS legal_source_providers (",
        1,
    )[1].split("\n);", 1)[0]
    coverage_sql = legal_intelligence_repository._COVERAGE_SQL

    assert "provider_status" in provider_body
    assert "enabled" not in provider_body
    assert "provider_status = 'active'" in coverage_sql
    assert "WHERE enabled" not in coverage_sql
    assert "'official_primary'" in coverage_sql
    assert "'official_secondary'" in coverage_sql
    assert "provider_status = 'active'" in coverage_sql
    assert "newer_provider.provider_revision >" in coverage_sql
    assert "provider.provider_revision" in coverage_sql

    for read_sql in (
        legal_intelligence_repository._SEARCH_SQL,
        legal_intelligence_repository._RESOURCE_SQL,
        legal_intelligence_repository._VERIFIED_CORPUS_LINEAGE_SQL,
    ):
        assert "provider.provider_id = version.provider_id" in read_sql
        assert "version.provider_id = resource.provider_id" not in read_sql
        assert "newer_provider.provider_revision >" not in read_sql
        assert "derived_predecessor_effective_to" in read_sql
        assert "correction.version_sequence >" in read_sql

    assert "legal_corpus_scope_completion_attestations" in coverage_sql
    assert "verification_status = 'verified'" in coverage_sql
    assert "completion.manifest_sha256" in coverage_sql
    assert "completion.record_count > 0" in coverage_sql
    assert "completion.resource_count > 0" in coverage_sql
    assert "completion.last_checkpoint_sequence DESC" in coverage_sql
    assert "terminal_checkpoint.occurred_at AS completed_at" in coverage_sql
    assert (
        "terminal_checkpoint.checkpoint_sequence ="
        in coverage_sql
    )
    assert "scopeCompletenessAttested" not in coverage_sql


def test_cross_revision_alias_and_effective_closure_are_queryable() -> None:
    search_sql = legal_intelligence_repository._SEARCH_SQL
    resource_sql = legal_intelligence_repository._RESOURCE_SQL
    evidence_sql = (
        legal_intelligence_repository._VERIFIED_CORPUS_LINEAGE_SQL
    )
    issues_sql = legal_intelligence_repository._OPEN_CORPUS_ISSUES_SQL

    assert "legal_resource_identity_crosswalks AS crosswalk" in search_sql
    assert "crosswalk.provider_official_identifier" in search_sql
    assert "legal_resource_identity_crosswalks AS crosswalk" in resource_sql
    for sql in (search_sql, resource_sql, evidence_sql, issues_sql):
        assert "legal_resource_version_lineages" in sql
        assert "derived_predecessor_effective_to" in sql
    assert "newer_competing.version_sequence >" in issues_sql
    assert search_sql.count("%s") == 27
    assert resource_sql.count("%s") == 7


def test_project_law_query_plan_does_not_embed_address_or_parcel_identity():
    address = "서울특별시 강남구 비밀로 123"
    plan = build_korean_law_mcp_query_plan(
        {
            "project": {"type": "mixed"},
            "land": {
                "address": address,
                "pnu": "1168010100100010001",
            },
            "regulation": {"zoning": "제3종일반주거지역"},
        }
    )
    serialized = " ".join(str(item.get("query") or "") for item in plan)

    assert address not in serialized
    assert "1168010100100010001" not in serialized


class StaticProvider:
    def __init__(
        self,
        *,
        provider_id: str,
        transport: SourceTransport,
        hits: tuple[SearchHit, ...] = (),
        coverage: CoverageStatus = CoverageStatus.UNKNOWN,
        freshness: FreshnessStatus = FreshnessStatus.FRESH,
    ) -> None:
        self.provider_id = provider_id
        self.transport = transport
        self._result = ProviderResult(
            provider_id=provider_id,
            status=ProviderStatus.SUCCESS if hits else ProviderStatus.NO_MATCH,
            transport=transport,
            hits=hits,
            coverage=coverage,
            freshness=freshness,
        )

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        return self._result


def _official_hit() -> SearchHit:
    content = {
        "resourceId": "legal-resource:building-act",
        "resourceVersionId": "legal-version:building-act:2026",
        "officialText": "건축허가에 관한 공식 원문",
    }
    return SearchHit(
        title="건축법",
        excerpt="건축허가",
        stage=SearchStage.EXACT_NAME,
        lexical_score=1,
        content=content,
        attestation=build_attestation(
            content=content,
            source_authority=SourceAuthority.LAW_GO_KR,
            source_transport=SourceTransport.LAW_GO_KR_DIRECT_API,
            trust_level=TrustLevel.SOURCE_ATTESTED,
            resource_kind=LegalResourceKind.STATUTE,
            official_resource_id="법령:건축법",
            official_version_id="2026-01-01",
            locator="https://www.law.go.kr/법령/건축법",
            as_of=date(2026, 7, 18),
            fetched_at=NOW,
            effective_from=date(2026, 1, 1),
        ),
    )


def _service(
    *,
    official_hits: tuple[SearchHit, ...] = (),
) -> LegalIntelligenceService:
    local = InMemoryVerifiedSnapshotProvider(
        (),
        coverage_complete=False,
        fresh_until=None,
    )
    official = StaticProvider(
        provider_id="official",
        transport=SourceTransport.LAW_GO_KR_DIRECT_API,
        hits=official_hits,
    )
    return LegalIntelligenceService(
        orchestrator=LegalIntelligenceOrchestrator(
            local_provider=local,
            official_provider=official,
        ),
        local_provider=local,
    )


def test_caller_context_is_candidate_and_never_authoritative():
    response = _service().search(
        query="건축법",
        resource_kinds=(LegalResourceKind.STATUTE,),
        as_of=date(2026, 7, 18),
        caller_context={
            "connected": True,
            "references": [
                {
                    "lawName": "건축법",
                    "lawId": "caller-law",
                    "url": "https://untrusted.invalid/law",
                }
            ],
        },
    )

    assert response.authoritative_hits == ()
    assert len(response.candidate_hits) == 1
    candidate = response.candidate_hits[0]
    assert candidate.attestation.source_authority == SourceAuthority.CALLER_SUPPLIED
    assert candidate.attestation.trust_level == TrustLevel.UNTRUSTED_CANDIDATE
    assert not candidate.is_authoritative_evidence


def test_project_resolution_blocks_direct_official_external_egress():
    resolution = resolve_project_legal_context(
        {"project": {"name": "본리동", "type": "mixed"}},
        caller_context={
            "references": [{"lawName": "사용자 주장 법령", "lawId": "caller:1"}]
        },
        service=_service(official_hits=(_official_hit(),)),
        max_queries=1,
        as_of=date(2026, 7, 18),
    )

    intelligence = resolution["legalIntelligence"]
    assert intelligence["evidenceStatus"] == "unverified"
    assert intelligence["verifiedReferenceCount"] == 0
    assert intelligence["candidateReferenceCount"] == 1
    assert intelligence["humanReviewStatus"] == "pending"
    assert resolution["lawContext"]["connected"] is False
    assert intelligence["providerTrace"][1]["status"] == "skipped"
    assert intelligence["providerTrace"][1]["diagnostics"][0]["code"] == (
        "EXTERNAL_EGRESS_BLOCKED_BY_CLASSIFICATION"
    )
    assert (
        intelligence["candidateReferences"][0]["contentHash"]
        .startswith("sha256:")
    )


def test_search_serialization_matches_web_contract():
    response = _service(official_hits=(_official_hit(),)).search(
        query="건축법",
        resource_kinds=(LegalResourceKind.STATUTE,),
        as_of=date(2026, 7, 18),
        data_classification=DataClassification.PUBLIC,
    )
    payload = serialize_legal_search_response(response)

    assert payload["authoritativeCount"] == 0
    assert payload["candidateCount"] == 1
    item = payload["results"][0]
    assert item["resourceId"] == "legal-resource:building-act"
    assert item["sourceAuthority"] == "law_go_kr"
    assert item["transport"] == "law_go_kr_direct_api"
    assert item["verificationStatus"] == "source_attested"
    assert item["sourceUrl"].startswith("https://www.law.go.kr/")
    assert item["normalizedContentSha256"].startswith("sha256:")


def test_global_legal_search_requires_public_acknowledgement_for_egress():
    local_only = search_legal_resources(
        "건축법",
        as_of=date(2026, 7, 18),
        service=_service(official_hits=(_official_hit(),)),
    )
    public = search_legal_resources(
        "건축법",
        as_of=date(2026, 7, 18),
        public_query_acknowledged=True,
        service=_service(official_hits=(_official_hit(),)),
    )

    assert (
        local_only["queryContract"]["data_classification"]
        == "project_confidential"
    )
    assert local_only["externalEgressAcknowledged"] is False
    assert local_only["externalEgressPolicy"] == (
        "local_or_trusted_internal_only_until_public_query_acknowledged"
    )
    assert local_only["candidateCount"] == 0
    assert public["queryContract"]["data_classification"] == "public"
    assert public["externalEgressAcknowledged"] is True
    assert (
        public["externalEgressPolicy"]
        == "public_query_external_fallback_allowed"
    )
    assert public["candidateCount"] == 1


def test_unavailable_provider_is_explicit_not_a_false_success():
    result = UnavailableLegalProvider().search(
        LegalSearchQuery(text="건축법")
    )

    assert result.status == ProviderStatus.SKIPPED
    assert result.hits == ()
    assert result.diagnostics[0].code == "LAW_GO_KR_NOT_CONFIGURED"


class SnapshotRecorder:
    def __init__(self):
        self.calls = []

    def append_verified_evidence_snapshot(self, snapshot, *, created_by):
        self.calls.append((snapshot, created_by))
        verified_citation_count = len(
            {
                (
                    item["resourceId"],
                    item["resourceVersionId"],
                    item["legalProvisionId"],
                )
                for item in snapshot["references"]
            }
        )
        coverage_complete = (
            verified_citation_count == snapshot["expectedCitationCount"]
            and snapshot["verifiedClaimCount"]
            == snapshot["expectedClaimCount"]
        )
        return {
            "legalEvidenceSnapshotId": "snapshot-1",
            "snapshotSha256": "sha256:" + ("d" * 64),
            "snapshotVersion": 1,
            "gateEligible": coverage_complete,
            "verifiedReferenceCount": len(snapshot["references"]),
            "verifiedCitationCount": verified_citation_count,
            "coverageComplete": coverage_complete,
            "expectedClaimCount": snapshot["expectedClaimCount"],
            "verifiedClaimCount": snapshot["verifiedClaimCount"],
            "claimCoverageComplete": (
                snapshot["verifiedClaimCount"]
                == snapshot["expectedClaimCount"]
            ),
            "stale": False,
            "hasOpenConflicts": False,
        }


def test_seal_legal_review_seals_exact_local_subset_but_fails_coverage_gate():
    recorder = SnapshotRecorder()
    review = {
        "reviewId": "review-1",
        "analysisOnly": True,
        "reportEligible": False,
        "legalIntelligence": {
            "asOfDate": "2026-07-18",
            "freshnessPolicyVersion": "legal-freshness-v1",
            "stale": False,
                "openConflictCount": 0,
                "requiredClaimCount": 1,
                "verifiedClaimCount": 1,
                "references": [
                {
                    "lawId": "법령:건축법",
                    "lawName": "건축법",
                    "citation": "제11조",
                    "transport": "local_verified_snapshot",
                    "verificationStatus": "verified_snapshot",
                    "resourceId": "legal-resource:building-act",
                    "resourceVersionId": "legal-version:building-act:2026",
                    "sourceFetchId": "fetch:1",
                    "provisionId": "provision:11",
                    "locator": "https://www.law.go.kr/법령/건축법/제11조",
                    "normalizedContentSha256": "sha256:" + ("a" * 64),
                    "verifiedAt": NOW.isoformat(),
                    "summary": "건축물을 건축하려는 자는 허가를 받아야 한다.",
                    "claimContexts": [
                        {
                            "tool": "verify_citations",
                            "query": "건축법 제11조",
                            "purpose": "건축허가 근거를 확인한다.",
                        }
                    ],
                },
                {
                    "lawId": "direct-only",
                    "transport": "law_go_kr_direct_api",
                    "verificationStatus": "source_attested",
                },
            ],
        },
    }

    result = seal_legal_review_evidence_snapshot(
        review,
        repository=recorder,
        created_by="reviewer-1",
    )

    assert result["sealed"] is True
    assert len(recorder.calls) == 1
    snapshot, actor = recorder.calls[0]
    assert actor == "reviewer-1"
    assert len(snapshot["references"]) == 1
    assert snapshot["expectedCitationCount"] == 2
    assert snapshot["references"][0]["legalProvisionId"] == "provision:11"
    updated = result["legalReview"]
    assert updated["legalEvidenceSnapshotId"] == "snapshot-1"
    assert updated["legalIntelligence"]["snapshotGateEligible"] is False
    assert updated["legalIntelligence"]["citationCoverageComplete"] is False
    assert updated["legalIntelligence"]["evidenceStatus"] == "partial"
    assert updated["analysisOnly"] is True
    assert updated["reportEligible"] is False


def test_seal_legal_review_abstains_when_only_resource_or_direct_rows_exist():
    recorder = SnapshotRecorder()
    result = seal_legal_review_evidence_snapshot(
        {
            "reviewId": "review-2",
            "legalIntelligence": {
                "references": [
                    {
                        "transport": "local_verified_snapshot",
                        "verificationStatus": "verified_snapshot",
                        "resourceId": "resource-1",
                        "resourceVersionId": "version-1",
                        "sourceFetchId": "fetch-1",
                        "locator": "https://www.law.go.kr/법령/건축법",
                        "normalizedContentSha256": "sha256:" + ("a" * 64),
                        "verifiedAt": NOW.isoformat(),
                    }
                ]
            },
        },
        repository=recorder,
        created_by="reviewer-1",
    )

    assert result["sealed"] is False
    assert result["reason"] == "exact_verified_local_citations_missing"
    assert recorder.calls == []
    assert (
        result["legalReview"]["legalIntelligence"][
            "snapshotGateEligible"
        ]
        is False
    )


class FakeCursor:
    def __init__(self, search_rows):
        self.search_rows = search_rows
        self.statement = ""
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, params=None):
        self.statement = statement
        self.params = params
        self.calls.append((statement, params))

    def fetchall(self):
        if "FROM ranked" in self.statement:
            return self.search_rows
        return []

    def fetchone(self):
        if (
            "legal_corpus_scope_completion_attestations"
            in self.statement
        ):
            return (True,)
        return None


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor


def test_postgres_provider_projects_verified_bitemporal_row():
    row = (
        "legal-resource:building-act",
        "statute",
        "법령:건축법",
        "법제처",
        {},
        "legal-version:building-act:2026",
        "fetch:1",
        "version:2026",
        "건축법",
        "Building Act",
        date(2026, 1, 1),
        None,
        NOW,
        NOW,
        NOW,
        "schema_verified",
        "a" * 64,
        {"officialText": "건축법 공식 원문"},
        "law_go_kr",
        "https://www.law.go.kr/법령",
        "direct_api",
        "https://www.law.go.kr/법령/건축법",
        "exact_name",
        1.0,
    )
    provision_text = (
        "A person who intends to construct a building must obtain a permit."
    )
    row = (
        *row[:22],
        "provision:11",
        "article-11",
        "Article 11",
        "Building permit",
        provision_text,
        "c" * 64,
        "https://www.law.go.kr/building-act/article-11",
        {"language": "ko"},
        *row[22:],
    )
    cursor = FakeCursor([row])
    provider = PostgresVerifiedLegalSnapshotProvider(
        connection_factory=lambda: FakeConnection(cursor),
        freshness_days=30,
        now=lambda: NOW,
    )

    result = provider.search(
        LegalSearchQuery(
            text="건축법",
            as_of=date(2026, 7, 18),
        )
    )

    assert result.status == ProviderStatus.SUCCESS
    assert result.coverage == CoverageStatus.COMPLETE
    assert result.freshness == FreshnessStatus.FRESH
    assert result.hits[0].is_authoritative_evidence
    assert result.hits[0].attestation.official_resource_id == "법령:건축법"
    assert result.hits[0].content["resourceId"] == "legal-resource:building-act"
    assert result.hits[0].content["provisionId"] == "provision:11"
    assert result.hits[0].content["officialText"] == provision_text
    assert result.hits[0].excerpt == provision_text
    assert result.hits[0].attestation.article == "Article 11"
    assert (
        result.hits[0].attestation.locator
        == "https://www.law.go.kr/building-act/article-11"
    )
    assert cursor.calls[0][1] == ("realytics,extensions,pg_temp",)


class ResourceCursor:
    def __init__(self, resource_row, provision_rows):
        self.resource_row = resource_row
        self.provision_rows = provision_rows
        self.statement = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, params=None):
        self.statement = statement

    def fetchone(self):
        if "FROM legal_resources AS resource" in self.statement:
            return self.resource_row
        return None

    def fetchall(self):
        if "FROM legal_provisions" in self.statement:
            return self.provision_rows
        return []


def test_postgres_resource_detail_keeps_version_and_provision_locators():
    resource_row = (
        "legal-resource:building-act",
        "statute",
        "법령:건축법",
        "KR",
        "법제처",
        {"classification": "official"},
        "legal-version:building-act:2026",
        "fetch:1",
        "version:2026",
        "건축법",
        "Building Act",
        date(2025, 12, 1),
        date(2025, 12, 15),
        date(2026, 1, 1),
        None,
        NOW,
        NOW,
        NOW,
        "schema_verified",
        "a" * 64,
        "legal-parser-v1",
        {"officialText": "건축법 전체 원문"},
        "law_go_kr",
        "https://www.law.go.kr/법령",
        "direct_api",
        "https://www.law.go.kr/법령/건축법",
        "b" * 64,
    )
    provision_rows = [
        (
            "provision:11",
            "article",
            "제11조",
            "제11조",
            "건축허가",
            "건축물을 건축하려는 자는 허가를 받아야 한다.",
            "c" * 64,
            "https://www.law.go.kr/법령/건축법/제11조",
            {},
        )
    ]
    cursor = ResourceCursor(resource_row, provision_rows)
    provider = PostgresVerifiedLegalSnapshotProvider(
        connection_factory=lambda: FakeConnection(cursor),
    )

    detail = provider.get_resource(
        "legal-resource:building-act",
        as_of=date(2026, 7, 18),
    )

    assert detail["resourceVersionId"] == "legal-version:building-act:2026"
    assert detail["sourceAuthority"] == "law_go_kr"
    assert detail["parserSchemaVersion"] == "legal-parser-v1"
    assert detail["fetchedAt"] == NOW.isoformat()
    assert detail["verifiedAt"] == NOW.isoformat()
    assert detail["provisions"][0]["provisionId"] == "provision:11"
    assert detail["provisions"][0]["contentHash"] == "sha256:" + ("c" * 64)
