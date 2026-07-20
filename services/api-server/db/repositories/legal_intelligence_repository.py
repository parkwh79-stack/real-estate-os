"""PostgreSQL adapters for the Legal Intelligence Core.

The official corpus is global and append-only.  Project findings and review
decisions are tenant scoped and must be accessed only after setting the
transaction-local tenant context used by PostgreSQL RLS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from db.postgres import connect
from legal_intelligence.models import (
    CoverageStatus,
    FreshnessStatus,
    LegalResourceKind,
    LegalSearchQuery,
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


ConnectionFactory = Callable[[], Any]


class LegalReviewDecisionConflict(ValueError):
    """The approval-gate predecessor changed after the caller read it."""


_SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_SCHEMA = "realytics"
_DEFAULT_FRESHNESS_DAYS = 7
_APPROVAL_FRESHNESS_POLICY_VERSION = "legal-freshness-v1"
_APPROVAL_FRESHNESS_WINDOW = timedelta(days=7)

_CORE_TO_DATABASE_KINDS: Mapping[LegalResourceKind, tuple[str, ...]] = {
    LegalResourceKind.STATUTE: ("statute", "decree", "ministerial_rule"),
    LegalResourceKind.PROVISION: (
        "statute",
        "decree",
        "ministerial_rule",
        "administrative_rule",
        "local_ordinance",
    ),
    LegalResourceKind.PRECEDENT: ("precedent",),
    LegalResourceKind.INTERPRETATION: ("legal_interpretation",),
    LegalResourceKind.ADMINISTRATIVE_RULE: ("administrative_rule",),
    LegalResourceKind.LOCAL_ORDINANCE: ("local_ordinance",),
    LegalResourceKind.CONSTITUTIONAL_DECISION: ("constitutional_decision",),
    LegalResourceKind.COMMITTEE_DECISION: ("committee_decision",),
    LegalResourceKind.LEGAL_TERM: ("official_term",),
}

_DATABASE_TO_CORE_KIND: Mapping[str, LegalResourceKind] = {
    "statute": LegalResourceKind.STATUTE,
    "decree": LegalResourceKind.STATUTE,
    "ministerial_rule": LegalResourceKind.STATUTE,
    "administrative_rule": LegalResourceKind.ADMINISTRATIVE_RULE,
    "local_ordinance": LegalResourceKind.LOCAL_ORDINANCE,
    "precedent": LegalResourceKind.PRECEDENT,
    "constitutional_decision": LegalResourceKind.CONSTITUTIONAL_DECISION,
    "legal_interpretation": LegalResourceKind.INTERPRETATION,
    "committee_decision": LegalResourceKind.COMMITTEE_DECISION,
    "official_term": LegalResourceKind.LEGAL_TERM,
    "official_form": LegalResourceKind.PROVISION,
    "official_appendix": LegalResourceKind.PROVISION,
}

_AUTHORITY_ALIASES: Mapping[str, SourceAuthority] = {
    "law_go_kr": SourceAuthority.LAW_GO_KR,
    "moleg": SourceAuthority.LAW_GO_KR,
    "court": SourceAuthority.COURT,
    "supreme_court": SourceAuthority.SUPREME_COURT,
    "constitutional_court": SourceAuthority.CONSTITUTIONAL_COURT,
    "central_government": SourceAuthority.CENTRAL_GOVERNMENT,
    "local_government": SourceAuthority.LOCAL_GOVERNMENT,
    "public_agency": SourceAuthority.PUBLIC_AGENCY,
    "national_tax_service": SourceAuthority.NATIONAL_TAX_SERVICE,
    "tenant_document": SourceAuthority.TENANT_DOCUMENT,
    "human_counsel": SourceAuthority.HUMAN_COUNSEL,
}

_SEARCH_SQL = """
WITH ranked AS (
    SELECT
        resource.legal_resource_id,
        resource.resource_type,
        resource.official_identifier,
        resource.issuing_authority,
        resource.metadata_json AS resource_metadata,
        version.legal_resource_version_id,
        version.source_fetch_id,
        version.official_version_identifier,
        version.canonical_title,
        version.alternative_titles_text,
        version.effective_from,
        version.effective_to,
        version.observed_at,
        version.fetched_at,
        version.verified_at,
        version.verification_status,
        version.normalized_sha256,
        version.metadata_json AS version_metadata,
        provider.source_authority,
        provider.canonical_base_url,
        source_fetch.transport AS fetch_transport,
        source_fetch.sanitized_source_locator,
        provision.legal_provision_id,
        provision.provision_identifier,
        provision.display_path,
        provision.heading_text,
        provision.body_text,
        provision.provision_sha256,
        provision.source_locator AS provision_source_locator,
        provision.metadata_json AS provision_metadata,
        CASE
            WHEN regexp_replace(lower(resource.official_identifier), '[[:space:]·ㆍ_-]+', '', 'g')
                 = ANY(%s::text[])
              OR EXISTS (
                    SELECT 1
                      FROM legal_resource_identity_crosswalks AS crosswalk
                     WHERE crosswalk.legal_resource_id =
                           resource.legal_resource_id
                       AND regexp_replace(
                            lower(crosswalk.provider_official_identifier),
                            '[[:space:]·ㆍ_-]+',
                            '',
                            'g'
                       ) = ANY(%s::text[])
              )
                THEN 'exact_identifier'
            WHEN regexp_replace(lower(version.canonical_title), '[[:space:]·ㆍ_-]+', '', 'g') = %s
                THEN 'exact_name'
            WHEN EXISTS (
                SELECT 1
                  FROM jsonb_array_elements_text(
                      CASE
                          WHEN jsonb_typeof(version.metadata_json -> 'approvedAliases') = 'array'
                              THEN version.metadata_json -> 'approvedAliases'
                          ELSE '[]'::jsonb
                      END
                  ) AS alias(value)
                 WHERE regexp_replace(lower(alias.value), '[[:space:]·ㆍ_-]+', '', 'g') = %s
            )
            OR EXISTS (
                SELECT 1
                  FROM legal_term_provision_links AS term_link
                  JOIN glossary_terms AS glossary
                    ON glossary.term_id = term_link.term_id
                 WHERE term_link.legal_resource_id = resource.legal_resource_id
                   AND term_link.review_status = 'approved'
                   AND glossary.review_status = 'approved'
                   AND (
                        regexp_replace(
                            lower(COALESCE(glossary.payload_json ->> 'preferredTerm', '')),
                            '[[:space:]·ㆍ_-]+',
                            '',
                            'g'
                        ) = %s
                        OR EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements_text(
                                  CASE
                                      WHEN jsonb_typeof(glossary.payload_json -> 'aliases') = 'array'
                                          THEN glossary.payload_json -> 'aliases'
                                      ELSE '[]'::jsonb
                                  END
                              ) AS glossary_alias(value)
                             WHERE regexp_replace(
                                 lower(glossary_alias.value),
                                 '[[:space:]·ㆍ_-]+',
                                 '',
                                 'g'
                             ) = %s
                        )
                   )
            )
                THEN 'approved_alias'
            ELSE 'lexical'
        END AS match_stage,
        CASE
            WHEN regexp_replace(lower(resource.official_identifier), '[[:space:]·ㆍ_-]+', '', 'g')
                 = ANY(%s::text[])
              OR EXISTS (
                    SELECT 1
                      FROM legal_resource_identity_crosswalks AS crosswalk
                     WHERE crosswalk.legal_resource_id =
                           resource.legal_resource_id
                       AND regexp_replace(
                            lower(crosswalk.provider_official_identifier),
                            '[[:space:]·ㆍ_-]+',
                            '',
                            'g'
                       ) = ANY(%s::text[])
              )
                THEN 1.0
            WHEN regexp_replace(lower(version.canonical_title), '[[:space:]·ㆍ_-]+', '', 'g') = %s
                THEN 1.0
            ELSE GREATEST(
                ts_rank(version.search_document, plainto_tsquery('simple'::regconfig, %s)),
                extensions.similarity(version.canonical_title, %s),
                COALESCE(provision.match_score, 0.0)
            )
        END AS lexical_score
      FROM legal_resources AS resource
      JOIN legal_resource_versions AS version
        ON version.legal_resource_id = resource.legal_resource_id
      JOIN legal_source_providers AS provider
        ON provider.provider_id = version.provider_id
      JOIN legal_source_fetches AS source_fetch
        ON source_fetch.source_fetch_id = version.source_fetch_id
       AND source_fetch.provider_id = version.provider_id
      LEFT JOIN legal_resource_version_lineages AS closing_lineage
        ON closing_lineage.legal_resource_id =
           version.legal_resource_id
       AND closing_lineage.effective_predecessor_version_id =
           version.legal_resource_version_id
       AND closing_lineage.continuity_status = 'open_end_closed'
      LEFT JOIN LATERAL (
          SELECT
              candidate.legal_provision_id,
              candidate.provision_identifier,
              candidate.display_path,
              candidate.heading_text,
              candidate.body_text,
              candidate.provision_sha256,
              candidate.source_locator,
              candidate.metadata_json,
              GREATEST(
                  candidate.rank_score,
                  candidate.similarity_score
              ) AS match_score
            FROM (
                SELECT
                    legal_provision_id,
                    provision_identifier,
                    display_path,
                    heading_text,
                    body_text,
                    provision_sha256,
                    source_locator,
                    metadata_json,
                    ts_rank(
                        search_document,
                        plainto_tsquery('simple'::regconfig, %s)
                    ) AS rank_score,
                    extensions.similarity(
                        COALESCE(provision_identifier, '')
                        || ' '
                        || COALESCE(display_path, '')
                        || ' '
                        || COALESCE(heading_text, ''),
                        %s
                    ) AS similarity_score
                  FROM legal_provisions
                 WHERE legal_resource_version_id =
                       version.legal_resource_version_id
                   AND legal_resource_id =
                       version.legal_resource_id
            ) AS candidate
           WHERE candidate.rank_score > 0
              OR candidate.similarity_score >= 0.15
           ORDER BY
              GREATEST(
                  candidate.rank_score,
                  candidate.similarity_score
              ) DESC,
              candidate.legal_provision_id
           LIMIT 1
      ) AS provision ON TRUE
     WHERE resource.resource_type = ANY(%s::text[])
       AND provider.authority_tier IN (
            'official_primary',
            'official_secondary'
       )
       AND version.verification_status IN ('source_verified', 'schema_verified')
       AND version.verified_at IS NOT NULL
       AND source_fetch.fetch_status IN ('succeeded', 'not_modified')
       AND (version.effective_from IS NULL OR version.effective_from <= %s::date)
       AND (
            COALESCE(
                version.effective_to,
                closing_lineage.derived_predecessor_effective_to
            ) IS NULL
            OR COALESCE(
                version.effective_to,
                closing_lineage.derived_predecessor_effective_to
            ) >= %s::date
       )
       AND (version.valid_time_start IS NULL OR version.valid_time_start <= %s::date)
       AND (
            COALESCE(
                version.valid_time_end,
                closing_lineage.derived_predecessor_effective_to
            ) IS NULL
            OR COALESCE(
                version.valid_time_end,
                closing_lineage.derived_predecessor_effective_to
            ) >= %s::date
       )
       AND NOT EXISTS (
            SELECT 1
              FROM legal_resource_versions AS correction
              JOIN legal_source_fetches AS correction_fetch
                ON correction_fetch.source_fetch_id =
                   correction.source_fetch_id
               AND correction_fetch.provider_id =
                   correction.provider_id
             WHERE correction.legal_resource_id =
                   version.legal_resource_id
               AND correction.official_version_identifier =
                   version.official_version_identifier
               AND correction.version_sequence >
                   version.version_sequence
               AND correction.verification_status IN (
                    'source_verified',
                    'schema_verified'
               )
               AND correction.verified_at IS NOT NULL
               AND correction_fetch.fetch_status IN (
                    'succeeded',
                    'not_modified'
               )
       )
       AND (
            regexp_replace(lower(resource.official_identifier), '[[:space:]·ㆍ_-]+', '', 'g')
                = ANY(%s::text[])
            OR EXISTS (
                SELECT 1
                  FROM legal_resource_identity_crosswalks AS crosswalk
                 WHERE crosswalk.legal_resource_id =
                       resource.legal_resource_id
                   AND regexp_replace(
                        lower(crosswalk.provider_official_identifier),
                        '[[:space:]·ㆍ_-]+',
                        '',
                        'g'
                   ) = ANY(%s::text[])
            )
            OR regexp_replace(lower(version.canonical_title), '[[:space:]·ㆍ_-]+', '', 'g') = %s
            OR EXISTS (
                SELECT 1
                  FROM jsonb_array_elements_text(
                      CASE
                          WHEN jsonb_typeof(version.metadata_json -> 'approvedAliases') = 'array'
                              THEN version.metadata_json -> 'approvedAliases'
                          ELSE '[]'::jsonb
                      END
                  ) AS alias(value)
                 WHERE regexp_replace(lower(alias.value), '[[:space:]·ㆍ_-]+', '', 'g') = %s
            )
            OR EXISTS (
                SELECT 1
                  FROM legal_term_provision_links AS term_link
                  JOIN glossary_terms AS glossary
                    ON glossary.term_id = term_link.term_id
                 WHERE term_link.legal_resource_id = resource.legal_resource_id
                   AND term_link.review_status = 'approved'
                   AND glossary.review_status = 'approved'
                   AND (
                        regexp_replace(
                            lower(COALESCE(glossary.payload_json ->> 'preferredTerm', '')),
                            '[[:space:]·ㆍ_-]+',
                            '',
                            'g'
                        ) = %s
                        OR EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements_text(
                                  CASE
                                      WHEN jsonb_typeof(glossary.payload_json -> 'aliases') = 'array'
                                          THEN glossary.payload_json -> 'aliases'
                                      ELSE '[]'::jsonb
                                  END
                              ) AS glossary_alias(value)
                             WHERE regexp_replace(
                                 lower(glossary_alias.value),
                                 '[[:space:]·ㆍ_-]+',
                                 '',
                                 'g'
                             ) = %s
                        )
                   )
            )
            OR version.search_document @@ plainto_tsquery('simple'::regconfig, %s)
            OR extensions.similarity(version.canonical_title, %s) >= 0.15
            OR provision.legal_provision_id IS NOT NULL
       )
)
SELECT *
  FROM ranked
 ORDER BY
    CASE match_stage
        WHEN 'exact_identifier' THEN 0
        WHEN 'exact_name' THEN 1
        WHEN 'approved_alias' THEN 2
        ELSE 3
    END,
    lexical_score DESC,
    legal_resource_id,
    legal_resource_version_id
 LIMIT %s
"""

_COVERAGE_SQL = """
WITH active_provider AS (
    SELECT provider.provider_id, provider.provider_key,
           provider.provider_revision
      FROM legal_source_providers AS provider
     WHERE provider.provider_status = 'active'
       AND provider.authority_tier IN (
            'official_primary',
            'official_secondary'
       )
       AND NOT EXISTS (
            SELECT 1
              FROM legal_source_providers AS newer_provider
             WHERE newer_provider.provider_key = provider.provider_key
               AND newer_provider.provider_status = 'active'
               AND newer_provider.provider_revision >
                   provider.provider_revision
       )
),
leaf_registry AS (
    SELECT provider.provider_id,
           registry.coverage_registry_id,
           registry.provider_key,
           registry.coverage_contract_version,
           registry.expected_contract_count,
           registry.expected_contracts_sha256
      FROM active_provider AS provider
      JOIN legal_corpus_coverage_registries AS registry
        ON registry.provider_key = provider.provider_key
       AND registry.provider_revision = provider.provider_revision
     WHERE NOT EXISTS (
            SELECT 1
              FROM legal_corpus_coverage_registries AS successor
             WHERE successor.supersedes_coverage_registry_id =
                   registry.coverage_registry_id
       )
),
valid_registry AS (
    SELECT registry.provider_id,
           registry.coverage_registry_id,
           registry.expected_contract_count
      FROM leaf_registry AS registry
      JOIN legal_corpus_coverage_contracts AS contract
        ON contract.coverage_registry_id =
           registry.coverage_registry_id
      JOIN legal_corpus_required_target_contracts AS required_target
        ON required_target.provider_key = registry.provider_key
       AND required_target.coverage_contract_version =
           registry.coverage_contract_version
       AND required_target.resource_kind = contract.resource_kind
       AND required_target.provider_target = contract.provider_target
       AND required_target.required
     GROUP BY
        registry.provider_id,
        registry.coverage_registry_id,
        registry.expected_contract_count,
        registry.expected_contracts_sha256,
        registry.provider_key,
        registry.coverage_contract_version
    HAVING COUNT(*) = registry.expected_contract_count
       AND COUNT(*) = (
            SELECT COUNT(*)
              FROM legal_corpus_required_target_contracts AS required
             WHERE required.provider_key = registry.provider_key
               AND required.coverage_contract_version =
                   registry.coverage_contract_version
               AND required.required
       )
       AND encode(
            extensions.digest(
                convert_to(
                    string_agg(
                        contract.coverage_contract_id,
                        E'\\n'
                        ORDER BY contract.coverage_contract_id
                    ),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
       ) = registry.expected_contracts_sha256
),
latest_contract_completion AS (
    SELECT DISTINCT ON (
        completion.provider_id,
        completion.coverage_contract_id
    )
        completion.provider_id,
        contract.coverage_registry_id,
        completion.coverage_contract_id,
        terminal_checkpoint.occurred_at AS completed_at
      FROM legal_corpus_scope_completion_attestations AS completion
      JOIN legal_corpus_coverage_contracts AS contract
        ON contract.coverage_contract_id =
           completion.coverage_contract_id
      JOIN valid_registry AS registry
        ON registry.provider_id = completion.provider_id
       AND registry.coverage_registry_id =
           contract.coverage_registry_id
      JOIN legal_sync_checkpoint_events AS terminal_checkpoint
        ON terminal_checkpoint.provider_id = completion.provider_id
       AND terminal_checkpoint.requested_scope_hash =
           completion.requested_scope_hash
       AND terminal_checkpoint.checkpoint_sequence =
           completion.last_checkpoint_sequence
     WHERE completion.coverage_contract_id IS NOT NULL
       AND completion.verification_status = 'verified'
       AND completion.page_count > 0
       AND completion.record_count > 0
       AND completion.resource_count > 0
       AND completion.manifest_sha256 ~ '^[0-9a-f]{64}$'
       AND jsonb_array_length(completion.page_hashes_json) =
           completion.page_count
       AND NOT EXISTS (
            SELECT 1
              FROM legal_corpus_scope_completion_attestations AS successor
             WHERE successor.baseline_completion_attestation_id =
                   completion.completion_attestation_id
       )
       AND (
            completion.coverage_mode = 'initial'
            OR (
                completion.coverage_mode = 'incremental'
                AND EXISTS (
                    SELECT 1
                      FROM legal_corpus_scope_completion_attestations
                           AS baseline
                     WHERE baseline.completion_attestation_id =
                           completion.baseline_completion_attestation_id
                       AND baseline.provider_id = completion.provider_id
                       AND baseline.requested_scope_hash =
                           completion.requested_scope_hash
                       AND baseline.coverage_contract_id =
                           completion.coverage_contract_id
                       AND baseline.coverage_mode IN (
                            'initial',
                            'incremental'
                       )
                       AND baseline.verification_status = 'verified'
                       AND baseline.last_checkpoint_sequence + 1 =
                           completion.first_checkpoint_sequence
                )
            )
       )
     ORDER BY
        completion.provider_id,
        completion.coverage_contract_id,
        completion.last_checkpoint_sequence DESC,
        terminal_checkpoint.occurred_at DESC,
        completion.completion_attestation_id DESC
),
eligible_registry AS (
    SELECT registry.provider_id,
           registry.coverage_registry_id,
           MIN(completion.completed_at) AS completed_at
      FROM valid_registry AS registry
      JOIN legal_corpus_coverage_contracts AS contract
        ON contract.coverage_registry_id =
           registry.coverage_registry_id
      LEFT JOIN latest_contract_completion AS completion
        ON completion.provider_id = registry.provider_id
       AND completion.coverage_registry_id =
           registry.coverage_registry_id
       AND completion.coverage_contract_id =
           contract.coverage_contract_id
     GROUP BY
        registry.provider_id,
        registry.coverage_registry_id,
        registry.expected_contract_count
    HAVING COUNT(contract.coverage_contract_id) =
           registry.expected_contract_count
       AND COUNT(completion.coverage_contract_id) =
           registry.expected_contract_count
)
SELECT CASE
    WHEN (SELECT COUNT(*) FROM active_provider) = 0 THEN NULL
    WHEN (SELECT COUNT(*) FROM leaf_registry)
         <> (SELECT COUNT(*) FROM active_provider) THEN NULL
    WHEN (SELECT COUNT(*) FROM valid_registry)
         <> (SELECT COUNT(*) FROM active_provider) THEN NULL
    WHEN (SELECT COUNT(*) FROM eligible_registry)
         <> (SELECT COUNT(*) FROM active_provider) THEN NULL
    ELSE (SELECT MIN(completed_at) FROM eligible_registry)
END
"""

_RESOURCE_SQL = """
SELECT
    resource.legal_resource_id,
    resource.resource_type,
    resource.official_identifier,
    resource.jurisdiction_code,
    resource.issuing_authority,
    resource.metadata_json,
    version.legal_resource_version_id,
    version.source_fetch_id,
    version.official_version_identifier,
    version.canonical_title,
    version.alternative_titles_text,
    version.publication_date,
    version.promulgation_date,
    version.effective_from,
    version.effective_to,
    version.observed_at,
    version.fetched_at,
    version.verified_at,
    version.verification_status,
    version.normalized_sha256,
    version.parser_schema_version,
    version.metadata_json,
    provider.source_authority,
    provider.canonical_base_url,
    source_fetch.transport,
    source_fetch.sanitized_source_locator,
    source_fetch.raw_sha256
  FROM legal_resources AS resource
  JOIN legal_resource_versions AS version
    ON version.legal_resource_id = resource.legal_resource_id
  JOIN legal_source_providers AS provider
    ON provider.provider_id = version.provider_id
  JOIN legal_source_fetches AS source_fetch
    ON source_fetch.source_fetch_id = version.source_fetch_id
   AND source_fetch.provider_id = version.provider_id
  LEFT JOIN legal_resource_version_lineages AS closing_lineage
    ON closing_lineage.legal_resource_id = version.legal_resource_id
   AND closing_lineage.effective_predecessor_version_id =
       version.legal_resource_version_id
   AND closing_lineage.continuity_status = 'open_end_closed'
 WHERE (
        resource.legal_resource_id = %s
        OR resource.official_identifier = %s
        OR EXISTS (
            SELECT 1
              FROM legal_resource_identity_crosswalks AS crosswalk
             WHERE crosswalk.legal_resource_id =
                   resource.legal_resource_id
               AND crosswalk.provider_official_identifier = %s
        )
       )
   AND (version.effective_from IS NULL OR version.effective_from <= %s::date)
   AND (
        COALESCE(
            version.effective_to,
            closing_lineage.derived_predecessor_effective_to
        ) IS NULL
        OR COALESCE(
            version.effective_to,
            closing_lineage.derived_predecessor_effective_to
        ) >= %s::date
   )
   AND (version.valid_time_start IS NULL OR version.valid_time_start <= %s::date)
   AND (
        COALESCE(
            version.valid_time_end,
            closing_lineage.derived_predecessor_effective_to
        ) IS NULL
        OR COALESCE(
            version.valid_time_end,
            closing_lineage.derived_predecessor_effective_to
        ) >= %s::date
   )
   AND version.verification_status IN ('source_verified', 'schema_verified')
   AND version.verified_at IS NOT NULL
   AND source_fetch.fetch_status IN ('succeeded', 'not_modified')
   AND provider.authority_tier IN (
        'official_primary',
        'official_secondary'
   )
   AND NOT EXISTS (
        SELECT 1
          FROM legal_resource_versions AS correction
          JOIN legal_source_fetches AS correction_fetch
            ON correction_fetch.source_fetch_id =
               correction.source_fetch_id
           AND correction_fetch.provider_id = correction.provider_id
         WHERE correction.legal_resource_id =
               version.legal_resource_id
           AND correction.official_version_identifier =
               version.official_version_identifier
           AND correction.version_sequence > version.version_sequence
           AND correction.verification_status IN (
                'source_verified',
                'schema_verified'
           )
           AND correction.verified_at IS NOT NULL
           AND correction_fetch.fetch_status IN (
                'succeeded',
                'not_modified'
           )
   )
 ORDER BY
    CASE version.verification_status
        WHEN 'schema_verified' THEN 0
        WHEN 'source_verified' THEN 1
        ELSE 2
    END,
    version.effective_from DESC NULLS LAST,
    version.version_sequence DESC
 LIMIT 1
"""

_PROVISIONS_SQL = """
SELECT
    legal_provision_id,
    provision_type,
    provision_identifier,
    display_path,
    heading_text,
    body_text,
    provision_sha256,
    source_locator,
    metadata_json
 FROM legal_provisions
 WHERE legal_resource_version_id = %s
 ORDER BY ordinal_path, legal_provision_id
 LIMIT %s
OFFSET %s
"""

_INSERT_REVIEW_DECISION_SQL = """
INSERT INTO legal_review_decisions (
    legal_review_decision_id,
    tenant_id,
    project_id,
    legal_finding_id,
    legal_evidence_snapshot_id,
    decision_type,
    decision_scope,
    decision_reason,
    reviewer_id,
    reviewer_role,
    legal_approval_delegation_id,
    legal_review_id,
    legal_review_version_id,
    legal_review_version,
    legal_review_projection_sha256,
    expected_predecessor_legal_review_decision_id,
    predecessor_expectation_supplied,
    decided_at,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    TRUE, %s, %s::jsonb
)
ON CONFLICT (legal_review_decision_id) DO NOTHING
RETURNING
    legal_review_decision_id,
    decision_version,
    supersedes_legal_review_decision_id,
    recorded_at
"""

_LATEST_LEGAL_REVIEW_PROJECTION_FOR_DECISION_SQL = """
SELECT
    projection.review_version_id,
    projection.review_version,
    projection.projection_hash_contract,
    projection.projection_sha256,
    CASE
        WHEN projection.projection_hash_contract = 'pg-jsonb-row-v1'
        THEN encode(
            extensions.digest(
                convert_to(
                    (
                        to_jsonb(projection)
                        - 'projection_sha256'
                        - 'projection_hash_contract'
                    )::TEXT,
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        )
        ELSE NULL
    END,
    projection.legal_evidence_snapshot_id,
    projection.created_at,
    snapshot.created_at,
    statement_timestamp()
  FROM project_legal_review_versions AS projection
  LEFT JOIN legal_evidence_snapshots AS snapshot
    ON snapshot.legal_evidence_snapshot_id =
           projection.legal_evidence_snapshot_id
   AND snapshot.tenant_id = projection.tenant_id
   AND snapshot.project_id = projection.project_id
 WHERE projection.tenant_id = %s
   AND projection.project_id = %s
   AND projection.review_id = %s
 ORDER BY
    projection.review_version DESC,
    projection.created_at DESC,
    projection.review_version_id DESC
 LIMIT 1
 FOR SHARE
"""

_LATEST_EVIDENCE_SNAPSHOT_SQL = """
SELECT
    legal_evidence_snapshot_id,
    snapshot_version,
    snapshot_sha256
  FROM legal_evidence_snapshots
 WHERE tenant_id = %s
   AND project_id = %s
 ORDER BY snapshot_version DESC
 LIMIT 1
 FOR UPDATE
"""

_VERIFIED_CORPUS_LINEAGE_SQL = """
SELECT
    version.legal_resource_id,
    version.legal_resource_version_id,
    version.source_fetch_id,
    version.normalized_sha256,
    version.verified_at,
    version.verification_status,
    source_fetch.fetch_status,
    COALESCE(
        provision.source_locator,
        NULLIF(version.metadata_json ->> 'sourceUrl', ''),
        NULLIF(version.metadata_json ->> 'officialUrl', ''),
        NULLIF(version.metadata_json ->> 'locator', '')
    ) AS exact_locator,
    provision.body_text,
    provision.provision_sha256
  FROM legal_resource_versions AS version
  JOIN legal_resources AS resource
    ON resource.legal_resource_id = version.legal_resource_id
  JOIN legal_source_fetches AS source_fetch
    ON source_fetch.source_fetch_id = version.source_fetch_id
   AND source_fetch.provider_id = version.provider_id
  JOIN legal_source_providers AS provider
    ON provider.provider_id = version.provider_id
  LEFT JOIN legal_resource_version_lineages AS closing_lineage
    ON closing_lineage.legal_resource_id = version.legal_resource_id
   AND closing_lineage.effective_predecessor_version_id =
       version.legal_resource_version_id
   AND closing_lineage.continuity_status = 'open_end_closed'
  JOIN legal_provisions AS provision
    ON provision.legal_provision_id = %s
   AND provision.legal_resource_version_id =
       version.legal_resource_version_id
   AND provision.legal_resource_id = version.legal_resource_id
 WHERE version.legal_resource_id = %s
   AND version.legal_resource_version_id = %s
   AND provision.legal_provision_id = %s
   AND (version.effective_from IS NULL OR version.effective_from <= %s::date)
   AND (
        COALESCE(
            version.effective_to,
            closing_lineage.derived_predecessor_effective_to
        ) IS NULL
        OR COALESCE(
            version.effective_to,
            closing_lineage.derived_predecessor_effective_to
        ) >= %s::date
   )
   AND (version.valid_time_start IS NULL OR version.valid_time_start <= %s::date)
   AND (
        COALESCE(
            version.valid_time_end,
            closing_lineage.derived_predecessor_effective_to
        ) IS NULL
        OR COALESCE(
            version.valid_time_end,
            closing_lineage.derived_predecessor_effective_to
        ) >= %s::date
   )
   AND provider.authority_tier IN (
        'official_primary',
        'official_secondary'
   )
   AND NOT EXISTS (
        SELECT 1
          FROM legal_resource_versions AS correction
          JOIN legal_source_fetches AS correction_fetch
            ON correction_fetch.source_fetch_id =
               correction.source_fetch_id
           AND correction_fetch.provider_id = correction.provider_id
         WHERE correction.legal_resource_id =
               version.legal_resource_id
           AND correction.official_version_identifier =
               version.official_version_identifier
           AND correction.version_sequence > version.version_sequence
           AND correction.verification_status IN (
                'source_verified',
                'schema_verified'
           )
           AND correction.verified_at IS NOT NULL
           AND correction_fetch.fetch_status IN (
                'succeeded',
                'not_modified'
           )
   )
 FOR SHARE OF version, resource, source_fetch, provider
"""

_OPEN_CORPUS_ISSUES_SQL = """
SELECT
    EXISTS (
        SELECT 1
          FROM legal_resource_versions AS competing
          JOIN legal_source_fetches AS competing_fetch
            ON competing_fetch.source_fetch_id = competing.source_fetch_id
           AND competing_fetch.provider_id = competing.provider_id
          LEFT JOIN legal_resource_version_lineages
                    AS competing_closing_lineage
            ON competing_closing_lineage.legal_resource_id =
               competing.legal_resource_id
           AND competing_closing_lineage.effective_predecessor_version_id =
               competing.legal_resource_version_id
           AND competing_closing_lineage.continuity_status =
               'open_end_closed'
         WHERE competing.legal_resource_id = %s
           AND competing.legal_resource_version_id <> %s
           AND competing.verification_status IN (
               'source_verified',
               'schema_verified'
           )
           AND competing.verified_at IS NOT NULL
           AND competing_fetch.fetch_status IN (
               'succeeded',
               'not_modified'
           )
           AND (
               competing.effective_from IS NULL
               OR competing.effective_from <= %s::date
           )
           AND (
               COALESCE(
                   competing.effective_to,
                   competing_closing_lineage.derived_predecessor_effective_to
               ) IS NULL
               OR COALESCE(
                   competing.effective_to,
                   competing_closing_lineage.derived_predecessor_effective_to
               ) >= %s::date
           )
           AND (
               competing.valid_time_start IS NULL
               OR competing.valid_time_start <= %s::date
           )
           AND (
               COALESCE(
                   competing.valid_time_end,
                   competing_closing_lineage.derived_predecessor_effective_to
               ) IS NULL
               OR COALESCE(
                   competing.valid_time_end,
                   competing_closing_lineage.derived_predecessor_effective_to
               ) >= %s::date
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM legal_resource_versions AS newer_competing
                 JOIN legal_source_fetches AS newer_competing_fetch
                   ON newer_competing_fetch.source_fetch_id =
                      newer_competing.source_fetch_id
                  AND newer_competing_fetch.provider_id =
                      newer_competing.provider_id
                WHERE newer_competing.legal_resource_id =
                      competing.legal_resource_id
                  AND newer_competing.official_version_identifier =
                      competing.official_version_identifier
                  AND newer_competing.version_sequence >
                      competing.version_sequence
                  AND newer_competing.verification_status IN (
                      'source_verified',
                      'schema_verified'
                  )
                  AND newer_competing.verified_at IS NOT NULL
                  AND newer_competing_fetch.fetch_status IN (
                      'succeeded',
                      'not_modified'
                  )
           )
    )
    OR EXISTS (
        SELECT 1
          FROM legal_citations AS citation
         WHERE citation.source_version_id = %s
           AND citation.source_provision_id = %s
           AND citation.citation_status IN ('unresolved', 'ambiguous')
    ) AS has_open_issue
"""

_APPROVED_REQUIRED_CLAIM_SET_SQL = """
SELECT
    claim_set.claim_set_version,
    claim_set.claim_set_sha256,
    ARRAY_AGG(
        claim.claim_text_sha256
        ORDER BY claim.ordinal, claim.claim_key
    ) AS required_claim_hashes,
    encode(
        extensions.digest(
            convert_to(
                string_agg(
                    claim.claim_key || ':' || claim.claim_text_sha256,
                    E'\n'
                    ORDER BY claim.ordinal, claim.claim_key
                ),
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    ) AS computed_claim_set_sha256
  FROM legal_required_claim_sets AS claim_set
  JOIN legal_required_claims AS claim
    ON claim.legal_required_claim_set_id =
           claim_set.legal_required_claim_set_id
   AND claim.tenant_id = claim_set.tenant_id
   AND claim.project_id = claim_set.project_id
 WHERE claim_set.legal_required_claim_set_id = %s
   AND claim_set.tenant_id = %s
   AND claim_set.project_id = %s
   AND claim_set.review_scope = 'approval_gate'
   AND claim_set.status = 'approved'
   AND claim_set.legal_required_claim_set_id = (
       SELECT latest.legal_required_claim_set_id
         FROM legal_required_claim_sets AS latest
        WHERE latest.tenant_id = claim_set.tenant_id
          AND latest.project_id = claim_set.project_id
          AND latest.review_scope = 'approval_gate'
          AND latest.status = 'approved'
        ORDER BY latest.approved_at DESC,
                 latest.claim_set_version DESC,
                 latest.legal_required_claim_set_id DESC
        LIMIT 1
   )
   AND NOT EXISTS (
       SELECT 1
         FROM legal_required_claim_sets AS successor
        WHERE successor.tenant_id = claim_set.tenant_id
          AND successor.project_id = claim_set.project_id
          AND successor.claim_set_key = claim_set.claim_set_key
          AND successor.claim_set_version > claim_set.claim_set_version
          AND successor.status IN ('approved', 'superseded')
   )
 GROUP BY
    claim_set.claim_set_version,
    claim_set.claim_set_sha256
"""

_LATEST_APPROVED_REQUIRED_CLAIM_SET_ID_SQL = """
SELECT claim_set.legal_required_claim_set_id
  FROM legal_required_claim_sets AS claim_set
 WHERE claim_set.tenant_id = %s
   AND claim_set.project_id = %s
   AND claim_set.review_scope = 'approval_gate'
   AND claim_set.status = 'approved'
   AND NOT EXISTS (
       SELECT 1
         FROM legal_required_claim_sets AS successor
        WHERE successor.tenant_id = claim_set.tenant_id
          AND successor.project_id = claim_set.project_id
          AND successor.claim_set_key = claim_set.claim_set_key
          AND successor.claim_set_version > claim_set.claim_set_version
          AND successor.status IN ('approved', 'superseded')
   )
 ORDER BY claim_set.claim_set_version DESC,
          claim_set.approved_at DESC,
          claim_set.legal_required_claim_set_id DESC
 LIMIT 1
"""

_GATE_ELIGIBLE_SNAPSHOT_SQL = """
SELECT
    snapshot.snapshot_version,
    snapshot.snapshot_sha256,
    snapshot.legal_review_id
  FROM legal_evidence_snapshots AS snapshot
 WHERE snapshot.legal_evidence_snapshot_id = %s
   AND snapshot.tenant_id = %s
   AND snapshot.project_id = %s
   AND snapshot.gate_eligible
   AND snapshot.citation_verification_status = 'verified'
   AND NOT snapshot.has_open_conflicts
   AND NOT snapshot.is_stale
   AND snapshot.legal_required_claim_set_id IS NOT NULL
   AND EXISTS (
       SELECT 1
         FROM legal_required_claim_sets AS claim_set
        WHERE claim_set.legal_required_claim_set_id =
                  snapshot.legal_required_claim_set_id
          AND claim_set.tenant_id = snapshot.tenant_id
          AND claim_set.project_id = snapshot.project_id
          AND claim_set.review_scope = 'approval_gate'
          AND claim_set.status = 'approved'
          AND claim_set.legal_required_claim_set_id = (
              SELECT latest.legal_required_claim_set_id
                FROM legal_required_claim_sets AS latest
               WHERE latest.tenant_id = claim_set.tenant_id
                 AND latest.project_id = claim_set.project_id
                 AND latest.review_scope = 'approval_gate'
                 AND latest.status = 'approved'
               ORDER BY latest.approved_at DESC,
                        latest.claim_set_version DESC,
                        latest.legal_required_claim_set_id DESC
               LIMIT 1
          )
          AND claim_set.claim_set_sha256 =
                  snapshot.required_claim_set_sha256
          AND claim_set.approved_by <> claim_set.created_by
          AND claim_set.approved_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
                FROM legal_required_claim_sets AS successor
               WHERE successor.tenant_id = claim_set.tenant_id
                 AND successor.project_id = claim_set.project_id
                 AND successor.claim_set_key = claim_set.claim_set_key
                 AND successor.claim_set_version >
                         claim_set.claim_set_version
                 AND successor.status IN ('approved', 'superseded')
          )
          AND NOT EXISTS (
              SELECT 1
                FROM legal_required_claims AS required_claim
               WHERE required_claim.legal_required_claim_set_id =
                         claim_set.legal_required_claim_set_id
                 AND NOT EXISTS (
                     SELECT 1
                       FROM legal_evidence_snapshot_items AS covered_item
                      WHERE covered_item.legal_evidence_snapshot_id =
                                snapshot.legal_evidence_snapshot_id
                        AND covered_item.tenant_id = snapshot.tenant_id
                        AND covered_item.project_id = snapshot.project_id
                        AND covered_item.claim_text_sha256 =
                                required_claim.claim_text_sha256
                        AND covered_item.citation_verification_status =
                                'verified'
                        AND covered_item.verified_at IS NOT NULL
                 )
          )
   )
   AND EXISTS (
       SELECT 1
         FROM legal_evidence_snapshot_items AS item
        WHERE item.legal_evidence_snapshot_id =
                  snapshot.legal_evidence_snapshot_id
          AND item.tenant_id = snapshot.tenant_id
          AND item.project_id = snapshot.project_id
          AND item.citation_verification_status = 'verified'
          AND item.verified_at IS NOT NULL
   )
 FOR SHARE OF snapshot
"""

_APPROVAL_READINESS_SQL = """
WITH selected_snapshot AS (
    SELECT snapshot.*
      FROM legal_evidence_snapshots AS snapshot
     WHERE snapshot.legal_evidence_snapshot_id = %s
       AND snapshot.tenant_id = %s
       AND snapshot.project_id = %s
),
latest_decision AS (
    SELECT decision.*
      FROM legal_review_decisions AS decision
     WHERE decision.tenant_id = %s
       AND decision.project_id = %s
       AND decision.decision_scope = 'approval_gate'
       AND decision.legal_review_id = %s
       AND decision.decision_type IN (
           'approve',
           'reject',
           'request_changes',
           'waive_with_conditions'
       )
       AND decision.decision_version IS NOT NULL
       AND decision.recorded_at IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
             FROM legal_review_decisions AS successor
            WHERE successor.supersedes_legal_review_decision_id =
                      decision.legal_review_decision_id
       )
     ORDER BY decision.decision_version DESC,
              decision.recorded_at DESC,
              decision.legal_review_decision_id DESC
     LIMIT 1
),
latest_review_projection AS (
    SELECT projection.*
      FROM selected_snapshot AS snapshot
      LEFT JOIN LATERAL (
          SELECT candidate.*
            FROM project_legal_review_versions AS candidate
           WHERE candidate.tenant_id = snapshot.tenant_id
             AND candidate.project_id = snapshot.project_id
             AND candidate.review_id = snapshot.legal_review_id
             AND NOT EXISTS (
                 SELECT 1
                   FROM project_legal_review_versions AS successor
                  WHERE successor.tenant_id = candidate.tenant_id
                    AND successor.project_id = candidate.project_id
                    AND successor.review_id = candidate.review_id
                    AND (
                        successor.supersedes_review_version_id =
                            candidate.review_version_id
                        OR successor.review_version >
                            candidate.review_version
                    )
             )
           ORDER BY candidate.review_version DESC,
                    candidate.created_at DESC,
                    candidate.review_version_id DESC
           LIMIT 1
      ) AS projection ON TRUE
),
evidence_rollup AS (
    SELECT
        COUNT(item.legal_evidence_item_id)::BIGINT AS item_count,
        COUNT(
            DISTINCT (
                item.legal_resource_id,
                item.legal_resource_version_id,
                item.legal_provision_id
            )
        )::BIGINT AS citation_count,
        COUNT(DISTINCT item.claim_text_sha256)::BIGINT AS claim_count,
        COUNT(*) FILTER (
            WHERE
                item.citation_verification_status <> 'verified'
                OR item.verified_at IS NULL
                OR item.verified_at > statement_timestamp()
                OR snapshot.freshness_policy_version <>
                       'legal-freshness-v1'
                OR item.verified_at + INTERVAL '7 days' <
                       statement_timestamp()
                OR version.legal_resource_version_id IS NULL
                OR version.verification_status NOT IN (
                    'source_verified',
                    'schema_verified'
                )
                OR version.verified_at IS NULL
                OR version.verified_at > statement_timestamp()
                OR version.source_fetch_id IS DISTINCT FROM item.source_fetch_id
                OR resource.legal_resource_id IS NULL
                OR resource.provider_id IS DISTINCT FROM version.provider_id
                OR source_fetch.source_fetch_id IS NULL
                OR source_fetch.fetch_status NOT IN (
                    'succeeded',
                    'not_modified'
                )
                OR source_fetch.fetched_at > statement_timestamp()
                OR provider.provider_id IS NULL
                OR provider.provider_status <> 'active'
                OR lineage.legal_resource_version_lineage_id IS NULL
                OR lineage.validated_at > statement_timestamp()
                OR provision.legal_provision_id IS NULL
                OR btrim(item.exact_locator) = ''
                OR provision.source_locator IS DISTINCT FROM item.exact_locator
                OR provision.provision_sha256 IS DISTINCT FROM encode(
                    extensions.digest(
                        convert_to(provision.body_text, 'UTF8'),
                        'sha256'
                    ),
                    'hex'
                )
                OR (
                    version.effective_from IS NOT NULL
                    AND version.effective_from > snapshot.as_of_date
                )
                OR (
                    version.effective_to IS NOT NULL
                    AND version.effective_to < snapshot.as_of_date
                )
                OR (
                    version.valid_time_start IS NOT NULL
                    AND version.valid_time_start > snapshot.as_of_date
                )
                OR (
                    version.valid_time_end IS NOT NULL
                    AND version.valid_time_end < snapshot.as_of_date
                )
                OR EXISTS (
                    SELECT 1
                      FROM legal_resource_versions AS replacement
                      JOIN legal_source_fetches AS replacement_fetch
                        ON replacement_fetch.source_fetch_id =
                               replacement.source_fetch_id
                       AND replacement_fetch.provider_id =
                               replacement.provider_id
                      JOIN legal_source_providers AS replacement_provider
                        ON replacement_provider.provider_id =
                               replacement.provider_id
                     WHERE replacement.legal_resource_id =
                               item.legal_resource_id
                       AND replacement.version_sequence >
                               version.version_sequence
                       AND replacement.verification_status IN (
                           'source_verified',
                           'schema_verified'
                       )
                       AND replacement.verified_at IS NOT NULL
                       AND replacement.verified_at <= statement_timestamp()
                       AND replacement_fetch.fetch_status IN (
                           'succeeded',
                           'not_modified'
                       )
                       AND replacement_provider.provider_status = 'active'
                       AND (
                           replacement.supersedes_version_id =
                               version.legal_resource_version_id
                           OR (
                               (
                                   replacement.effective_from IS NULL
                                   OR replacement.effective_from <=
                                      snapshot.as_of_date
                               )
                               AND (
                                   replacement.effective_to IS NULL
                                   OR replacement.effective_to >=
                                      snapshot.as_of_date
                               )
                               AND (
                                   replacement.valid_time_start IS NULL
                                   OR replacement.valid_time_start <=
                                      snapshot.as_of_date
                               )
                               AND (
                                   replacement.valid_time_end IS NULL
                                   OR replacement.valid_time_end >=
                                      snapshot.as_of_date
                               )
                           )
                       )
                )
        )::BIGINT AS invalid_lineage_count
      FROM selected_snapshot AS snapshot
      LEFT JOIN legal_evidence_snapshot_items AS item
        ON item.legal_evidence_snapshot_id =
               snapshot.legal_evidence_snapshot_id
       AND item.tenant_id = snapshot.tenant_id
       AND item.project_id = snapshot.project_id
      LEFT JOIN legal_resource_versions AS version
        ON version.legal_resource_version_id =
               item.legal_resource_version_id
       AND version.legal_resource_id = item.legal_resource_id
       AND version.source_fetch_id = item.source_fetch_id
      LEFT JOIN legal_resources AS resource
        ON resource.legal_resource_id = item.legal_resource_id
       AND resource.provider_id = version.provider_id
      LEFT JOIN legal_source_fetches AS source_fetch
        ON source_fetch.source_fetch_id = item.source_fetch_id
       AND source_fetch.provider_id = version.provider_id
      LEFT JOIN legal_source_providers AS provider
        ON provider.provider_id = version.provider_id
      LEFT JOIN legal_resource_version_lineages AS lineage
        ON lineage.legal_resource_version_id =
               item.legal_resource_version_id
       AND lineage.legal_resource_id = item.legal_resource_id
      LEFT JOIN legal_provisions AS provision
        ON provision.legal_provision_id = item.legal_provision_id
       AND provision.legal_resource_version_id =
               item.legal_resource_version_id
       AND provision.legal_resource_id = item.legal_resource_id
),
required_claim_rollup AS (
    SELECT
        claim_set.legal_required_claim_set_id,
        snapshot.required_claim_set_sha256 AS snapshot_policy_sha256,
        claim_set.claim_set_version,
        claim_set.claim_set_sha256,
        encode(
            extensions.digest(
                convert_to(
                    string_agg(
                        required_claim.claim_key || ':' ||
                            required_claim.claim_text_sha256,
                        E'\n'
                        ORDER BY
                            required_claim.ordinal,
                            required_claim.claim_key
                    ),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        ) AS computed_claim_set_sha256,
        COUNT(
            DISTINCT required_claim.legal_required_claim_id
        )::BIGINT AS required_claim_count,
        COUNT(
            DISTINCT required_claim.legal_required_claim_id
        ) FILTER (
            WHERE covered_item.legal_evidence_item_id IS NOT NULL
              AND covered_item.citation_verification_status = 'verified'
              AND covered_item.verified_at IS NOT NULL
        )::BIGINT AS covered_claim_count,
        (
            claim_set.status = 'approved'
            AND claim_set.review_scope = 'approval_gate'
            AND claim_set.legal_required_claim_set_id = (
                SELECT latest.legal_required_claim_set_id
                  FROM legal_required_claim_sets AS latest
                 WHERE latest.tenant_id = claim_set.tenant_id
                   AND latest.project_id = claim_set.project_id
                   AND latest.review_scope = 'approval_gate'
                   AND latest.status = 'approved'
                 ORDER BY latest.approved_at DESC,
                          latest.claim_set_version DESC,
                          latest.legal_required_claim_set_id DESC
                 LIMIT 1
            )
            AND claim_set.approved_by IS NOT NULL
            AND claim_set.approved_by <> claim_set.created_by
            AND claim_set.approved_at IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                  FROM legal_required_claim_sets AS successor
                 WHERE successor.tenant_id = claim_set.tenant_id
                   AND successor.project_id = claim_set.project_id
                   AND successor.claim_set_key = claim_set.claim_set_key
                   AND successor.claim_set_version >
                           claim_set.claim_set_version
                   AND successor.status IN ('approved', 'superseded')
            )
        ) AS policy_active
      FROM selected_snapshot AS snapshot
      LEFT JOIN legal_required_claim_sets AS claim_set
        ON claim_set.legal_required_claim_set_id =
               snapshot.legal_required_claim_set_id
       AND claim_set.tenant_id = snapshot.tenant_id
       AND claim_set.project_id = snapshot.project_id
      LEFT JOIN legal_required_claims AS required_claim
        ON required_claim.legal_required_claim_set_id =
               claim_set.legal_required_claim_set_id
       AND required_claim.tenant_id = claim_set.tenant_id
       AND required_claim.project_id = claim_set.project_id
      LEFT JOIN legal_evidence_snapshot_items AS covered_item
        ON covered_item.legal_evidence_snapshot_id =
               snapshot.legal_evidence_snapshot_id
       AND covered_item.tenant_id = snapshot.tenant_id
       AND covered_item.project_id = snapshot.project_id
       AND covered_item.claim_text_sha256 =
               required_claim.claim_text_sha256
     GROUP BY
        claim_set.legal_required_claim_set_id,
        claim_set.claim_set_version,
        claim_set.claim_set_sha256,
        claim_set.status,
        claim_set.review_scope,
        claim_set.approved_by,
        claim_set.created_by,
        claim_set.approved_at,
        claim_set.tenant_id,
        claim_set.project_id,
        claim_set.claim_set_key,
        snapshot.required_claim_set_sha256
),
latest_matter_leaves AS (
    SELECT matter.*
      FROM selected_snapshot AS snapshot
      JOIN project_legal_matters AS matter
        ON matter.tenant_id = snapshot.tenant_id
       AND matter.project_id = snapshot.project_id
     WHERE NOT EXISTS (
         SELECT 1
           FROM project_legal_matters AS successor
          WHERE successor.tenant_id = matter.tenant_id
            AND successor.project_id = matter.project_id
            AND successor.supersedes_matter_id =
                    matter.project_legal_matter_id
     )
),
matter_waiver_rollup AS (
    SELECT
        COUNT(matter.project_legal_matter_id) FILTER (
            WHERE matter.status = 'blocked'
               OR (
                   matter.status IN ('open', 'under_review')
                   AND matter.severity IN ('high', 'critical')
               )
        )::BIGINT AS active_blocking_matter_count,
        COUNT(matter.project_legal_matter_id) FILTER (
            WHERE matter.status = 'waived'
        )::BIGINT AS waived_matter_count,
        COUNT(matter.project_legal_matter_id) FILTER (
            WHERE matter.status = 'waived'
              AND (
                  waiver.legal_waiver_id IS NULL
                  OR waiver.status <> 'approved'
                  OR waiver.cure_status NOT IN (
                      'not_started',
                      'in_progress',
                      'evidence_submitted'
                  )
                  OR waiver.valid_from IS NULL
                  OR waiver.valid_from > statement_timestamp()
                  OR waiver.valid_until <= statement_timestamp()
                  OR waiver.legal_evidence_snapshot_id IS DISTINCT FROM
                         snapshot.legal_evidence_snapshot_id
                  OR (
                      waiver.cure_evidence_snapshot_id IS NOT NULL
                      AND waiver.cure_evidence_snapshot_id IS DISTINCT FROM
                              snapshot.legal_evidence_snapshot_id
                  )
                  OR jsonb_typeof(waiver.conditions_json) <> 'array'
                  OR jsonb_array_length(waiver.conditions_json) = 0
                  OR jsonb_typeof(
                         waiver.cure_condition_outcomes_json
                     ) <> 'array'
                  OR jsonb_array_length(waiver.conditions_json) <> (
                      SELECT COUNT(
                                 DISTINCT condition_entry ->> 'code'
                             )
                        FROM jsonb_array_elements(
                                 waiver.conditions_json
                             ) AS condition_entry
                  )
                  OR jsonb_array_length(
                         waiver.cure_condition_outcomes_json
                     ) <> (
                      SELECT COUNT(DISTINCT outcome_entry ->> 'code')
                        FROM jsonb_array_elements(
                                 waiver.cure_condition_outcomes_json
                             ) AS outcome_entry
                  )
                  OR EXISTS (
                      SELECT 1
                        FROM jsonb_array_elements(
                                 waiver.cure_condition_outcomes_json
                             ) AS outcome_entry
                       WHERE jsonb_typeof(outcome_entry) <> 'object'
                          OR NULLIF(
                                 btrim(outcome_entry ->> 'code'),
                                 ''
                             ) IS NULL
                          OR outcome_entry ->> 'status' NOT IN (
                              'in_progress',
                              'satisfied',
                              'failed'
                          )
                          OR NOT EXISTS (
                              SELECT 1
                                FROM jsonb_array_elements(
                                         waiver.conditions_json
                                     ) AS condition_entry
                               WHERE condition_entry ->> 'code' =
                                         outcome_entry ->> 'code'
                          )
                  )
                  OR EXISTS (
                      SELECT 1
                        FROM jsonb_array_elements(
                                 waiver.cure_condition_outcomes_json
                             ) AS failed_outcome
                       WHERE failed_outcome ->> 'status' = 'failed'
                  )
                  OR EXISTS (
                      SELECT 1
                        FROM jsonb_array_elements(
                                 waiver.cure_condition_outcomes_json
                             ) AS satisfied_outcome
                       WHERE satisfied_outcome ->> 'status' = 'satisfied'
                         AND (
                             waiver.cure_evidence_snapshot_id
                                 IS DISTINCT FROM
                                     snapshot.legal_evidence_snapshot_id
                             OR jsonb_typeof(
                                    satisfied_outcome ->
                                        'evidenceItemIds'
                                ) <> 'array'
                             OR jsonb_array_length(
                                    satisfied_outcome ->
                                        'evidenceItemIds'
                                ) = 0
                             OR jsonb_array_length(
                                    satisfied_outcome ->
                                        'evidenceItemIds'
                                ) <> (
                                 SELECT COUNT(
                                            DISTINCT evidence_item_id
                                        )
                                   FROM jsonb_array_elements_text(
                                            CASE
                                                WHEN jsonb_typeof(
                                                    satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ) = 'array'
                                                THEN satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ELSE '[]'::JSONB
                                            END
                                        ) AS evidence_item(evidence_item_id)
                             )
                             OR EXISTS (
                                 SELECT 1
                                   FROM jsonb_array_elements_text(
                                            CASE
                                                WHEN jsonb_typeof(
                                                    satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ) = 'array'
                                                THEN satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ELSE '[]'::JSONB
                                            END
                                        ) AS evidence_item(evidence_item_id)
                                  WHERE NULLIF(
                                            btrim(evidence_item_id),
                                            ''
                                        ) IS NULL
                                     OR NOT EXISTS (
                                         SELECT 1
                                           FROM
                                               legal_evidence_snapshot_items
                                                   AS cure_item
                                          WHERE
                                              cure_item.legal_evidence_item_id =
                                                  evidence_item_id
                                            AND cure_item.tenant_id =
                                                  waiver.tenant_id
                                            AND cure_item.project_id =
                                                  waiver.project_id
                                            AND
                                              cure_item
                                                  .legal_evidence_snapshot_id =
                                              waiver
                                                  .cure_evidence_snapshot_id
                                            AND cure_item
                                                  .citation_verification_status =
                                                    'verified'
                                            AND cure_item.verified_at
                                                  IS NOT NULL
                                            AND cure_item.verified_at <=
                                                  statement_timestamp()
                                     )
                             )
                         )
                  )
                  OR EXISTS (
                      SELECT 1
                        FROM jsonb_array_elements(
                                 waiver.conditions_json
                             ) AS condition_entry
                       WHERE jsonb_typeof(condition_entry) <> 'object'
                          OR NULLIF(
                                 btrim(condition_entry ->> 'code'),
                                 ''
                             ) IS NULL
                          OR NOT (
                              EXISTS (
                                  SELECT 1
                                    FROM jsonb_array_elements(
                                             waiver.cure_condition_outcomes_json
                                         ) AS outcome_entry
                                   WHERE outcome_entry ->> 'code' =
                                             condition_entry ->> 'code'
                                     AND outcome_entry ->> 'status' =
                                             'satisfied'
                              )
                              OR (
                                  NULLIF(
                                      btrim(condition_entry ->> 'dueAt'),
                                      ''
                                  ) IS NULL
                                  AND waiver.valid_until >
                                          statement_timestamp()
                              )
                              OR (
                                  NULLIF(
                                      btrim(condition_entry ->> 'dueAt'),
                                      ''
                                  ) IS NOT NULL
                                  AND legal_try_timestamptz(
                                          condition_entry ->> 'dueAt'
                                      ) IS NOT NULL
                                  AND legal_try_timestamptz(
                                          condition_entry ->> 'dueAt'
                                      ) > statement_timestamp()
                                  AND legal_try_timestamptz(
                                          condition_entry ->> 'dueAt'
                                      ) <= waiver.valid_until
                              )
                          )
                  )
              )
        )::BIGINT AS invalid_waiver_count
      FROM selected_snapshot AS snapshot
      LEFT JOIN latest_matter_leaves AS matter ON TRUE
      LEFT JOIN LATERAL (
          SELECT candidate.*
            FROM legal_waivers AS candidate
           WHERE candidate.tenant_id = matter.tenant_id
             AND candidate.project_id = matter.project_id
             AND candidate.project_legal_matter_id =
                     matter.project_legal_matter_id
             AND NOT EXISTS (
                 SELECT 1
                   FROM legal_waivers AS successor
                  WHERE successor.tenant_id = candidate.tenant_id
                    AND successor.project_id = candidate.project_id
                    AND successor.supersedes_waiver_id =
                            candidate.legal_waiver_id
             )
           ORDER BY candidate.waiver_version DESC,
                    candidate.recorded_at DESC,
                    candidate.legal_waiver_id DESC
           LIMIT 1
      ) AS waiver ON matter.status = 'waived'
),
terminal_cure_rollup AS (
    SELECT
        COUNT(waiver.legal_waiver_id) FILTER (
            WHERE waiver.status = 'cured'
              AND waiver.cure_status = 'verified'
              AND (
                  waiver.cure_evidence_snapshot_id IS DISTINCT FROM
                         snapshot.legal_evidence_snapshot_id
                  OR jsonb_typeof(waiver.conditions_json) <> 'array'
                  OR jsonb_array_length(waiver.conditions_json) = 0
                  OR jsonb_typeof(
                         waiver.cure_condition_outcomes_json
                     ) <> 'array'
                  OR jsonb_array_length(waiver.conditions_json) <> (
                      SELECT COUNT(
                                 DISTINCT condition_entry ->> 'code'
                             )
                        FROM jsonb_array_elements(
                                 waiver.conditions_json
                             ) AS condition_entry
                  )
                  OR jsonb_array_length(waiver.conditions_json) <>
                         jsonb_array_length(
                             waiver.cure_condition_outcomes_json
                         )
                  OR EXISTS (
                      SELECT 1
                        FROM jsonb_array_elements(
                                 waiver.conditions_json
                             ) AS condition_entry
                       WHERE jsonb_typeof(condition_entry) <> 'object'
                          OR NULLIF(
                                 btrim(condition_entry ->> 'code'),
                                 ''
                             ) IS NULL
                          OR NOT EXISTS (
                              SELECT 1
                                FROM jsonb_array_elements(
                                         waiver.cure_condition_outcomes_json
                                     ) AS outcome_entry
                               WHERE jsonb_typeof(outcome_entry) = 'object'
                                 AND outcome_entry ->> 'code' =
                                         condition_entry ->> 'code'
                                 AND outcome_entry ->> 'status' =
                                         'satisfied'
                          )
                  )
                  OR EXISTS (
                      SELECT 1
                        FROM jsonb_array_elements(
                                 waiver.cure_condition_outcomes_json
                             ) AS outcome_entry
                       WHERE jsonb_typeof(outcome_entry) <> 'object'
                          OR NULLIF(
                                 btrim(outcome_entry ->> 'code'),
                                 ''
                             ) IS NULL
                          OR outcome_entry ->> 'status' <> 'satisfied'
                          OR NOT EXISTS (
                              SELECT 1
                                FROM jsonb_array_elements(
                                         waiver.conditions_json
                                     ) AS condition_entry
                               WHERE condition_entry ->> 'code' =
                                         outcome_entry ->> 'code'
                          )
                  )
                  OR EXISTS (
                      SELECT 1
                        FROM jsonb_array_elements(
                                 waiver.cure_condition_outcomes_json
                             ) AS satisfied_outcome
                       WHERE satisfied_outcome ->> 'status' = 'satisfied'
                         AND (
                             jsonb_typeof(
                                 satisfied_outcome -> 'evidenceItemIds'
                             ) <> 'array'
                             OR jsonb_array_length(
                                    satisfied_outcome ->
                                        'evidenceItemIds'
                                ) = 0
                             OR jsonb_array_length(
                                    satisfied_outcome ->
                                        'evidenceItemIds'
                                ) <> (
                                 SELECT COUNT(
                                            DISTINCT evidence_item_id
                                        )
                                   FROM jsonb_array_elements_text(
                                            CASE
                                                WHEN jsonb_typeof(
                                                    satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ) = 'array'
                                                THEN satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ELSE '[]'::JSONB
                                            END
                                        ) AS evidence_item(evidence_item_id)
                             )
                             OR EXISTS (
                                 SELECT 1
                                   FROM jsonb_array_elements_text(
                                            CASE
                                                WHEN jsonb_typeof(
                                                    satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ) = 'array'
                                                THEN satisfied_outcome ->
                                                        'evidenceItemIds'
                                                ELSE '[]'::JSONB
                                            END
                                        ) AS evidence_item(evidence_item_id)
                                  WHERE NULLIF(
                                            btrim(evidence_item_id),
                                            ''
                                        ) IS NULL
                                     OR NOT EXISTS (
                                         SELECT 1
                                           FROM
                                               legal_evidence_snapshot_items
                                                   AS cure_item
                                          WHERE
                                              cure_item.legal_evidence_item_id =
                                                  evidence_item_id
                                            AND cure_item.tenant_id =
                                                  waiver.tenant_id
                                            AND cure_item.project_id =
                                                  waiver.project_id
                                            AND
                                              cure_item
                                                  .legal_evidence_snapshot_id =
                                              waiver
                                                  .cure_evidence_snapshot_id
                                            AND cure_item
                                                  .citation_verification_status =
                                                    'verified'
                                            AND cure_item.verified_at
                                                  IS NOT NULL
                                            AND cure_item.verified_at <=
                                                  statement_timestamp()
                                     )
                             )
                         )
                  )
                  OR NOT EXISTS (
                      SELECT 1
                        FROM project_legal_matters AS resolved_successor
                       WHERE resolved_successor.tenant_id =
                                 waiver.tenant_id
                         AND resolved_successor.project_id =
                                 waiver.project_id
                         AND resolved_successor.supersedes_matter_id =
                                 waiver.project_legal_matter_id
                         AND resolved_successor.status = 'resolved'
                         AND resolved_successor.created_at >=
                                 waiver.recorded_at
                  )
              )
        )::BIGINT AS invalid_terminal_cure_count
      FROM selected_snapshot AS snapshot
      LEFT JOIN legal_waivers AS waiver
        ON waiver.tenant_id = snapshot.tenant_id
       AND waiver.project_id = snapshot.project_id
       AND NOT EXISTS (
           SELECT 1
             FROM legal_waivers AS successor
            WHERE successor.tenant_id = waiver.tenant_id
              AND successor.project_id = waiver.project_id
              AND successor.supersedes_waiver_id =
                      waiver.legal_waiver_id
       )
),
governance_event_rollup AS (
    SELECT
        (
            SELECT MAX(matter.created_at)
              FROM project_legal_matters AS matter
             WHERE matter.tenant_id = snapshot.tenant_id
               AND matter.project_id = snapshot.project_id
        ) AS latest_matter_event_at,
        (
            SELECT MAX(waiver.recorded_at)
              FROM legal_waivers AS waiver
             WHERE waiver.tenant_id = snapshot.tenant_id
               AND waiver.project_id = snapshot.project_id
        ) AS latest_waiver_event_at
      FROM selected_snapshot AS snapshot
)
SELECT
    snapshot.legal_evidence_snapshot_id,
    snapshot.snapshot_version,
    snapshot.snapshot_sha256,
    snapshot.as_of_date,
    snapshot.citation_verification_status,
    snapshot.has_open_conflicts,
    snapshot.is_stale,
    snapshot.gate_eligible,
    snapshot.metadata_json,
    snapshot.legal_review_id,
    snapshot.created_by,
    snapshot.created_at,
    EXISTS (
        SELECT 1
          FROM legal_evidence_snapshots AS successor
         WHERE successor.tenant_id = snapshot.tenant_id
           AND successor.project_id = snapshot.project_id
           AND (
               successor.supersedes_snapshot_id =
                   snapshot.legal_evidence_snapshot_id
               OR successor.snapshot_version > snapshot.snapshot_version
           )
    ) AS snapshot_superseded,
    COALESCE(rollup.item_count, 0),
    COALESCE(rollup.citation_count, 0),
    COALESCE(rollup.claim_count, 0),
    COALESCE(rollup.invalid_lineage_count, 0),
    policy.legal_required_claim_set_id,
    policy.snapshot_policy_sha256,
    policy.claim_set_version,
    policy.claim_set_sha256,
    policy.computed_claim_set_sha256,
    COALESCE(policy.required_claim_count, 0),
    COALESCE(policy.covered_claim_count, 0),
    COALESCE(policy.policy_active, FALSE),
    projection.review_version_id,
    projection.review_id,
    projection.review_version,
    projection.legal_evidence_snapshot_id,
    projection.snapshot_version,
    projection.snapshot_sha256,
    projection.snapshot_gate_eligible,
    projection.required_claim_set_id,
    projection.required_claim_set_sha256,
    projection.projection_hash_contract,
    projection.projection_sha256,
    CASE
        WHEN projection.projection_hash_contract = 'pg-jsonb-row-v1'
        THEN encode(
            extensions.digest(
                convert_to(
                    (
                        to_jsonb(projection)
                        - 'projection_sha256'
                        - 'projection_hash_contract'
                    )::TEXT,
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        )
        ELSE NULL
    END AS computed_projection_sha256,
    projection.analysis_only,
    projection.report_eligible,
    EXISTS (
        SELECT 1
          FROM project_legal_review_versions AS successor
         WHERE successor.tenant_id = projection.tenant_id
           AND successor.project_id = projection.project_id
           AND successor.review_id = projection.review_id
           AND (
               successor.supersedes_review_version_id =
                   projection.review_version_id
               OR successor.review_version > projection.review_version
           )
    ) AS projection_superseded,
    projection.created_at,
    decision.legal_review_decision_id,
    decision.decision_version,
    decision.supersedes_legal_review_decision_id,
    decision.expected_predecessor_legal_review_decision_id,
    decision.legal_evidence_snapshot_id,
    decision.decision_type,
    decision.legal_review_version_id,
    decision.legal_review_version,
    decision.legal_review_projection_sha256,
    decision.reviewer_id,
    decision.reviewer_role,
    decision.legal_approval_delegation_id,
    CASE
        WHEN decision.legal_review_decision_id IS NULL THEN FALSE
        WHEN decision.legal_approval_delegation_id IS NULL
            THEN decision.reviewer_role <> 'external_counsel'
        ELSE EXISTS (
            SELECT 1
              FROM legal_approval_delegations AS delegation
             WHERE delegation.legal_approval_delegation_id =
                       decision.legal_approval_delegation_id
               AND delegation.tenant_id = decision.tenant_id
               AND delegation.project_id = decision.project_id
               AND delegation.grantee_id = decision.reviewer_id
               AND delegation.grantee_role = decision.reviewer_role
               AND delegation.scope = decision.decision_scope
               AND delegation.status = 'active'
               AND delegation.created_at <= decision.decided_at
               AND delegation.valid_from <= decision.decided_at
               AND delegation.valid_until >= decision.decided_at
               AND NOT EXISTS (
                    SELECT 1
                      FROM legal_approval_delegations AS successor
                     WHERE successor.tenant_id = delegation.tenant_id
                       AND successor.project_id = delegation.project_id
                       AND successor.delegation_key =
                           delegation.delegation_key
                       AND successor.delegation_version >
                           delegation.delegation_version
                       AND successor.created_at <= decision.decided_at
               )
        )
    END AS delegation_valid_at_decision,
    decision.decided_at,
    decision.recorded_at,
    statement_timestamp(),
    decision.metadata_json,
    COALESCE(
        matter_waiver.active_blocking_matter_count,
        0
    ),
    COALESCE(matter_waiver.waived_matter_count, 0),
    COALESCE(matter_waiver.invalid_waiver_count, 0)
        + COALESCE(terminal_cure.invalid_terminal_cure_count, 0),
    governance.latest_matter_event_at,
    governance.latest_waiver_event_at,
    CASE
        WHEN decision.decided_at IS NULL THEN TRUE
        WHEN governance.latest_matter_event_at > decision.decided_at
            THEN TRUE
        WHEN governance.latest_waiver_event_at > decision.decided_at
            THEN TRUE
        ELSE FALSE
    END AS governance_changed_after_decision
  FROM selected_snapshot AS snapshot
  CROSS JOIN evidence_rollup AS rollup
  CROSS JOIN required_claim_rollup AS policy
  CROSS JOIN latest_review_projection AS projection
  CROSS JOIN matter_waiver_rollup AS matter_waiver
  CROSS JOIN terminal_cure_rollup AS terminal_cure
  CROSS JOIN governance_event_rollup AS governance
  LEFT JOIN latest_decision AS decision ON TRUE
"""

_LATEST_APPROVAL_IDENTITY_SQL = """
SELECT
    snapshot.legal_evidence_snapshot_id,
    snapshot.snapshot_version,
    snapshot.snapshot_sha256,
    snapshot.legal_review_id
  FROM legal_evidence_snapshots AS snapshot
 WHERE snapshot.tenant_id = %s
   AND snapshot.project_id = %s
 ORDER BY snapshot.snapshot_version DESC,
          snapshot.created_at DESC,
          snapshot.legal_evidence_snapshot_id DESC
 LIMIT 1
"""

_INSERT_EVIDENCE_SNAPSHOT_SQL = """
INSERT INTO legal_evidence_snapshots (
    legal_evidence_snapshot_id,
    tenant_id,
    project_id,
    legal_query_run_id,
    snapshot_version,
    as_of_date,
    freshness_policy_version,
    snapshot_sha256,
    citation_verification_status,
    has_open_conflicts,
    is_stale,
    gate_eligible,
    supersedes_snapshot_id,
    legal_required_claim_set_id,
    required_claim_set_sha256,
    legal_review_id,
    created_by,
    created_at,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s::jsonb
)
RETURNING legal_evidence_snapshot_id
"""

_INSERT_EVIDENCE_ITEM_SQL = """
INSERT INTO legal_evidence_snapshot_items (
    legal_evidence_item_id,
    tenant_id,
    project_id,
    legal_evidence_snapshot_id,
    legal_finding_id,
    legal_resource_id,
    legal_resource_version_id,
    legal_provision_id,
    source_fetch_id,
    exact_locator,
    claim_text_sha256,
    excerpt_sha256,
    citation_verification_status,
    verified_at,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s::jsonb
)
RETURNING legal_evidence_item_id
"""


class PostgresVerifiedLegalSnapshotProvider:
    """Read verified, effective-dated legal material from the local corpus."""

    provider_id = "postgres_verified_legal_snapshot"
    transport = SourceTransport.LOCAL_VERIFIED_SNAPSHOT

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory = connect,
        schema: str | None = None,
        freshness_days: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._connection_factory = connection_factory
        self._schema = _schema_name(schema)
        configured_days = freshness_days
        if configured_days is None:
            configured_days = int(
                os.environ.get(
                    "REALYTICS_LEGAL_SNAPSHOT_FRESHNESS_DAYS",
                    str(_DEFAULT_FRESHNESS_DAYS),
                )
            )
        self._freshness = timedelta(days=max(0, configured_days))
        self._now = now

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        database_kinds = _database_kinds(query.resource_kinds)
        normalized_identifiers = tuple(
            dict.fromkeys(
                _normalized(value)
                for value in (query.text, *query.identifiers)
                if str(value).strip()
            )
        )
        normalized_query = _normalized(query.text)
        params = (
            list(normalized_identifiers),
            list(normalized_identifiers),
            normalized_query,
            normalized_query,
            normalized_query,
            normalized_query,
            list(normalized_identifiers),
            list(normalized_identifiers),
            normalized_query,
            query.text,
            query.text,
            query.text,
            query.text,
            list(database_kinds),
            query.as_of,
            query.as_of,
            query.as_of,
            query.as_of,
            list(normalized_identifiers),
            list(normalized_identifiers),
            normalized_query,
            normalized_query,
            normalized_query,
            normalized_query,
            query.text,
            query.text,
            query.limit,
        )
        try:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    _set_search_path(cursor, self._schema)
                    cursor.execute(_SEARCH_SQL, params)
                    rows = list(cursor.fetchall())
                    cursor.execute(_COVERAGE_SQL)
                    coverage_row = cursor.fetchone()
        except Exception as exc:
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.FAILED,
                transport=self.transport,
                coverage=CoverageStatus.UNKNOWN,
                freshness=FreshnessStatus.UNKNOWN,
                diagnostics=(
                    ProviderDiagnostic(
                        code="LEGAL_LOCAL_SNAPSHOT_UNAVAILABLE",
                        message=(
                            "Verified legal snapshot lookup failed: "
                            f"{type(exc).__name__}"
                        ),
                        retryable=True,
                    ),
                ),
                attempts=1,
            )

        try:
            hits = [_row_to_hit(row, query) for row in rows]
        except (TypeError, ValueError) as exc:
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.FAILED,
                transport=self.transport,
                coverage=CoverageStatus.UNKNOWN,
                freshness=FreshnessStatus.UNKNOWN,
                diagnostics=(
                    ProviderDiagnostic(
                        code="LEGAL_LOCAL_CORPUS_INTEGRITY_FAILED",
                        message=(
                            "Verified legal corpus identity/hash validation "
                            f"failed: {type(exc).__name__}"
                        ),
                        retryable=False,
                    ),
                ),
                attempts=1,
            )
        verified_times = [
            hit.attestation.verified_at
            for hit in hits
            if hit.attestation.verified_at is not None
        ]
        hit_freshness = _freshness_status(
            min(verified_times) if verified_times else None,
            now=self._now(),
            freshness=self._freshness,
        )
        coverage_value = coverage_row[0] if coverage_row else None
        if isinstance(coverage_value, bool):
            # Compatibility for lightweight/fake cursors. PostgreSQL returns
            # the completed-at timestamp from ``_COVERAGE_SQL``.
            coverage_freshness = (
                FreshnessStatus.FRESH
                if coverage_value
                else FreshnessStatus.UNKNOWN
            )
        else:
            coverage_freshness = _freshness_status(
                _datetime(coverage_value) if coverage_value is not None else None,
                now=self._now(),
                freshness=self._freshness,
            )
        coverage_complete = coverage_freshness == FreshnessStatus.FRESH
        freshness = (
            coverage_freshness
            if coverage_complete or not verified_times
            else hit_freshness
        )
        ordered = sort_hits(hits, limit=query.limit)
        return ProviderResult(
            provider_id=self.provider_id,
            status=ProviderStatus.SUCCESS if ordered else ProviderStatus.NO_MATCH,
            transport=self.transport,
            hits=ordered,
            coverage=(
                CoverageStatus.COMPLETE
                if coverage_complete
                else CoverageStatus.PARTIAL
            ),
            freshness=freshness,
            attempts=1,
        )

    def get_resource(
        self,
        resource_id: str,
        *,
        as_of: date,
        provision_limit: int = 500,
        provision_offset: int = 0,
    ) -> dict[str, Any] | None:
        """Return one bitemporal resource projection and its exact provisions."""

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                _set_search_path(cursor, self._schema)
                cursor.execute(
                    _RESOURCE_SQL,
                    (
                        resource_id,
                        resource_id,
                        resource_id,
                        as_of,
                        as_of,
                        as_of,
                        as_of,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                limit = max(1, min(int(provision_limit), 2_000))
                offset = max(0, int(provision_offset))
                cursor.execute(
                    _PROVISIONS_SQL,
                    (row[6], limit + 1, offset),
                )
                provisions = list(cursor.fetchall())
        has_more = len(provisions) > limit
        verified_at = _datetime(row[17]) if row[17] is not None else None
        freshness = _freshness_status(
            verified_at,
            now=self._now(),
            freshness=self._freshness,
        )
        return _resource_detail(
            row,
            provisions[:limit],
            as_of,
            freshness=freshness,
            provision_limit=limit,
            provision_offset=offset,
            provisions_have_more=has_more,
        )


class PostgresLegalTenantOverlayRepository:
    """Append tenant-scoped evidence and decisions through the RLS boundary."""

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_factory: ConnectionFactory = connect,
        schema: str | None = None,
        freshness_days: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not str(tenant_id).strip() or not str(project_id).strip():
            raise ValueError("tenant_id and project_id are required")
        self.tenant_id = str(tenant_id)
        self.project_id = str(project_id)
        self._connection_factory = connection_factory
        self._schema = _schema_name(schema)
        configured_days = freshness_days
        if configured_days is None:
            configured_days = int(
                os.environ.get(
                    "REALYTICS_LEGAL_SNAPSHOT_FRESHNESS_DAYS",
                    str(_DEFAULT_FRESHNESS_DAYS),
                )
            )
        self._freshness = timedelta(days=max(0, configured_days))
        self._now = now

    def evaluate_approval_readiness(
        self,
        *,
        legal_review_id: str,
        evidence_snapshot_id: str,
        expected_snapshot_version: int,
        expected_snapshot_sha256: str,
    ) -> dict[str, Any]:
        """Re-evaluate legal approval evidence from normalized PostgreSQL.

        This read deliberately does not trust the legacy Legal Pod projection.
        Every invocation rechecks project/tenant scope, immutable snapshot
        identity, actual item/claim counts, exact corpus lineage, effective-time
        validity, supersession, and the latest append-only human decision.
        """

        review_id = _required(legal_review_id, "legal_review_id")
        snapshot_id = _required(evidence_snapshot_id, "evidence_snapshot_id")
        try:
            snapshot_version = int(expected_snapshot_version)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "expected_snapshot_version must be a positive integer"
            ) from exc
        if snapshot_version < 1:
            raise ValueError(
                "expected_snapshot_version must be a positive integer"
            )
        snapshot_sha256 = _required_sha256(
            expected_snapshot_sha256,
            "expected_snapshot_sha256",
        )

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                _set_search_path(cursor, self._schema)
                cursor.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (self.tenant_id,),
                )
                cursor.execute(
                    _APPROVAL_READINESS_SQL,
                    (
                        snapshot_id,
                        self.tenant_id,
                        self.project_id,
                        self.tenant_id,
                        self.project_id,
                        review_id,
                    ),
                )
                row = cursor.fetchone()

        if row is None:
            return _missing_approval_readiness(
                snapshot_id=snapshot_id,
                reason="snapshot_scope_or_identity_mismatch",
            )

        (
            stored_snapshot_id,
            stored_version,
            stored_hash,
            as_of_date,
            citation_status,
            has_open_conflicts,
            is_stale,
            gate_eligible,
            raw_snapshot_metadata,
            stored_legal_review_id,
            snapshot_created_by,
            snapshot_created_at,
            snapshot_superseded,
            item_count,
            citation_count,
            claim_count,
            invalid_lineage_count,
            required_claim_set_id,
            snapshot_policy_sha256,
            required_claim_set_version,
            required_claim_set_sha256,
            computed_claim_set_sha256,
            required_claim_count,
            covered_claim_count,
            required_claim_policy_active,
            projection_version_id,
            projection_review_id,
            projection_review_version,
            projection_snapshot_id,
            projection_snapshot_version,
            projection_snapshot_sha256,
            projection_snapshot_gate_eligible,
            projection_required_claim_set_id,
            projection_required_claim_set_sha256,
            projection_hash_contract,
            projection_sha256,
            computed_projection_sha256,
            projection_analysis_only,
            projection_report_eligible,
            projection_superseded,
            projection_created_at,
            decision_id,
            decision_chain_version,
            supersedes_decision_id,
            expected_predecessor_decision_id,
            decision_snapshot_id,
            decision_type,
            decision_projection_id,
            decision_projection_version,
            decision_projection_sha256,
            reviewer_id,
            reviewer_role,
            legal_approval_delegation_id,
            delegation_valid_at_decision,
            decided_at,
            decision_recorded_at,
            readiness_evaluated_at,
            raw_decision_metadata,
            active_blocking_matter_count,
            waived_matter_count,
            invalid_waiver_count,
            latest_matter_event_at,
            latest_waiver_event_at,
            governance_changed_after_decision,
        ) = row
        snapshot_metadata = _mapping(raw_snapshot_metadata)
        decision_metadata = _mapping(raw_decision_metadata)
        normalized_stored_hash = str(stored_hash or "").strip().casefold()
        identity_matches = (
            str(stored_snapshot_id) == snapshot_id
            and int(stored_version) == snapshot_version
            and normalized_stored_hash == snapshot_sha256
            and str(stored_legal_review_id or "") == review_id
        )
        coverage_complete = (
            int(item_count or 0) > 0
            and int(citation_count or 0) > 0
        )
        normalized_snapshot_policy_hash = str(
            snapshot_policy_sha256 or ""
        ).strip().casefold()
        normalized_policy_hash = str(
            required_claim_set_sha256 or ""
        ).strip().casefold()
        normalized_computed_policy_hash = str(
            computed_claim_set_sha256 or ""
        ).strip().casefold()
        required_claim_policy_valid = (
            bool(required_claim_set_id)
            and bool(required_claim_policy_active)
            and int(required_claim_set_version or 0) > 0
            and bool(normalized_policy_hash)
            and normalized_snapshot_policy_hash
            == normalized_policy_hash
            == normalized_computed_policy_hash
            and int(required_claim_count or 0) > 0
        )
        claim_coverage_complete = (
            required_claim_policy_valid
            and int(covered_claim_count or 0)
            == int(required_claim_count or 0)
        )
        normalized_projection_snapshot_hash = str(
            projection_snapshot_sha256 or ""
        ).strip().casefold()
        normalized_projection_policy_hash = str(
            projection_required_claim_set_sha256 or ""
        ).strip().casefold()
        normalized_projection_hash = str(
            projection_sha256 or ""
        ).strip().casefold()
        normalized_computed_projection_hash = str(
            computed_projection_sha256 or ""
        ).strip().casefold()
        projection_exists = bool(projection_version_id)
        projection_hash_valid = (
            str(projection_hash_contract or "") == "pg-jsonb-row-v1"
            and bool(
                re.fullmatch(
                    r"[0-9a-f]{64}",
                    normalized_projection_hash,
                )
            )
            and normalized_projection_hash
            == normalized_computed_projection_hash
        )
        try:
            projection_version_matches = (
                int(projection_snapshot_version) == snapshot_version
            )
        except (TypeError, ValueError):
            projection_version_matches = False
        projection_identity_matches = (
            projection_exists
            and str(projection_review_id or "") == review_id
            and int(projection_review_version or 0) > 0
            and str(projection_snapshot_id or "") == snapshot_id
            and projection_version_matches
            and normalized_projection_snapshot_hash == snapshot_sha256
            and bool(projection_snapshot_gate_eligible)
            == bool(gate_eligible)
            and str(projection_required_claim_set_id or "")
            == str(required_claim_set_id or "")
            and normalized_projection_policy_hash
            == normalized_policy_hash
            and not bool(projection_superseded)
        )
        normalized_decision_projection_hash = str(
            decision_projection_sha256 or ""
        ).strip().casefold()
        try:
            decision_projection_version_matches = (
                int(decision_projection_version)
                == int(projection_review_version)
            )
        except (TypeError, ValueError):
            decision_projection_version_matches = False
        decision_projection_binding_valid = (
            bool(decision_id)
            and projection_exists
            and str(decision_projection_id or "")
            == str(projection_version_id or "")
            and decision_projection_version_matches
            and normalized_decision_projection_hash
            == normalized_projection_hash
        )
        try:
            normalized_decision_chain_version = int(
                decision_chain_version
            )
        except (TypeError, ValueError):
            normalized_decision_chain_version = 0
        decision_chain_valid = (
            bool(decision_id)
            and normalized_decision_chain_version > 0
            and (
                (
                    normalized_decision_chain_version == 1
                    and supersedes_decision_id is None
                )
                or (
                    normalized_decision_chain_version > 1
                    and bool(supersedes_decision_id)
                )
            )
            and (
                expected_predecessor_decision_id is None
                or str(expected_predecessor_decision_id)
                == str(supersedes_decision_id or "")
            )
        )
        try:
            normalized_decided_at = _datetime(decided_at)
            normalized_decision_recorded_at = _datetime(
                decision_recorded_at
            )
            normalized_readiness_evaluated_at = _datetime(
                readiness_evaluated_at
            )
            decision_time_valid = (
                decision_chain_valid
                and projection_created_at is not None
                and snapshot_created_at is not None
                and normalized_decided_at
                == normalized_decision_recorded_at
                and normalized_decided_at >= _datetime(projection_created_at)
                and normalized_decided_at >= _datetime(snapshot_created_at)
                and normalized_decided_at <= normalized_readiness_evaluated_at
            )
        except (TypeError, ValueError):
            decision_time_valid = False
        lineage_valid = (
            int(item_count or 0) > 0
            and int(invalid_lineage_count or 0) == 0
        )
        decision_hash = _optional_text(
            decision_metadata.get("evidenceSnapshotSha256")
        )
        if decision_hash:
            try:
                decision_hash = _required_sha256(
                    decision_hash,
                    "decision.evidenceSnapshotSha256",
                )
            except ValueError:
                decision_hash = None
        decision_version = decision_metadata.get("evidenceSnapshotVersion")
        try:
            decision_version_matches = int(decision_version) == snapshot_version
        except (TypeError, ValueError):
            decision_version_matches = False
        latest_decision_is_approve = (
            bool(decision_id)
            and str(decision_snapshot_id or "") == snapshot_id
            and str(decision_type or "") == "approve"
        )
        latest_decision_approves = (
            latest_decision_is_approve
            and decision_projection_binding_valid
            and decision_time_valid
            and str(reviewer_id or "").strip()
            != str(snapshot_created_by or "").strip()
            and str(reviewer_role or "").strip().lower() in {
                "legal_counsel_approver",
                "external_counsel",
            }
            and bool(delegation_valid_at_decision)
            and decision_version_matches
            and decision_hash == snapshot_sha256
        )
        matter_waiver_state_valid = (
            int(active_blocking_matter_count or 0) == 0
            and int(invalid_waiver_count or 0) == 0
        )
        matter_waiver_governance_valid = (
            matter_waiver_state_valid
            and not bool(governance_changed_after_decision)
        )
        passed = (
            identity_matches
            and projection_exists
            and projection_hash_valid
            and projection_identity_matches
            and str(citation_status) == "verified"
            and not bool(has_open_conflicts)
            and not bool(is_stale)
            and bool(gate_eligible)
            and not bool(snapshot_superseded)
            and coverage_complete
            and required_claim_policy_valid
            and claim_coverage_complete
            and lineage_valid
            and latest_decision_approves
            and matter_waiver_governance_valid
        )
        reason = _approval_readiness_reason(
            identity_matches=identity_matches,
            projection_exists=projection_exists,
            projection_hash_valid=projection_hash_valid,
            projection_identity_matches=projection_identity_matches,
            citation_status=str(citation_status or ""),
            has_open_conflicts=bool(has_open_conflicts),
            is_stale=bool(is_stale),
            gate_eligible=bool(gate_eligible),
            snapshot_superseded=bool(snapshot_superseded),
            coverage_complete=coverage_complete,
            required_claim_policy_valid=required_claim_policy_valid,
            claim_coverage_complete=claim_coverage_complete,
            lineage_valid=lineage_valid,
            latest_decision_is_approve=latest_decision_is_approve,
            decision_projection_binding_valid=(
                decision_projection_binding_valid
            ),
            decision_time_valid=decision_time_valid,
            latest_decision_approves=latest_decision_approves,
            active_blocking_matter_count=int(
                active_blocking_matter_count or 0
            ),
            invalid_waiver_count=int(invalid_waiver_count or 0),
            governance_changed_after_decision=bool(
                governance_changed_after_decision
            ),
        )
        normalized_reviewer_role = str(reviewer_role or "").strip().lower()
        human_review_status = (
            "counsel_approved"
            if latest_decision_approves
            and normalized_reviewer_role == "external_counsel"
            else ("approved" if latest_decision_approves else "pending")
        )
        return {
            "schemaVersion": "realytics-normalized-legal-readiness-v1",
            "source": "normalized_postgres",
            "readable": True,
            "pass": passed,
            "reason": reason,
            "legalReviewProjectionId": (
                str(projection_version_id)
                if projection_version_id is not None
                else None
            ),
            "legalReviewProjectionReviewId": (
                str(projection_review_id)
                if projection_review_id is not None
                else None
            ),
            "legalReviewProjectionVersion": (
                int(projection_review_version)
                if projection_review_version is not None
                else None
            ),
            "legalReviewProjectionSha256": (
                f"sha256:{normalized_projection_hash}"
                if projection_hash_valid
                else None
            ),
            "legalReviewProjectionHashContract": (
                str(projection_hash_contract)
                if projection_hash_contract is not None
                else None
            ),
            "legalReviewProjectionHashRecomputed": (
                projection_hash_valid
            ),
            "legalReviewProjectionValid": (
                projection_hash_valid
                and projection_identity_matches
            ),
            "legalReviewProjectionSuperseded": bool(
                projection_superseded
            ),
            "legalReviewProjectionAnalysisOnly": bool(
                projection_analysis_only
            ),
            "legalReviewProjectionReportEligible": bool(
                projection_report_eligible
            ),
            "evidenceStatus": (
                "verified" if str(citation_status) == "verified" else
                str(citation_status or "missing")
            ),
            "verifiedReferenceCount": int(item_count or 0),
            "verifiedCitationCount": int(citation_count or 0),
            "requiredClaimCount": int(required_claim_count or 0),
            "verifiedClaimCount": int(covered_claim_count or 0),
            "claimCoverageComplete": claim_coverage_complete,
            "requiredClaimPolicyValid": required_claim_policy_valid,
            "requiredClaimSetId": (
                str(required_claim_set_id)
                if required_claim_set_id is not None
                else None
            ),
            "requiredClaimSetVersion": (
                int(required_claim_set_version)
                if required_claim_set_version is not None
                else None
            ),
            "requiredClaimSetSha256": (
                f"sha256:{normalized_policy_hash}"
                if normalized_policy_hash
                else None
            ),
            "evidenceSnapshotId": str(stored_snapshot_id),
            "snapshotVersion": int(stored_version),
            "snapshotSha256": f"sha256:{normalized_stored_hash}",
            "snapshotGateEligible": bool(gate_eligible),
            "snapshotSuperseded": bool(snapshot_superseded),
            "stale": bool(is_stale),
            "openConflictCount": int(bool(has_open_conflicts)),
            "lineageValid": lineage_valid,
            "invalidLineageCount": int(invalid_lineage_count or 0),
            "coverageComplete": coverage_complete,
            "asOfDate": (
                as_of_date.isoformat()
                if hasattr(as_of_date, "isoformat")
                else str(as_of_date)
            ),
            "latestDecisionId": (
                str(decision_id) if decision_id is not None else None
            ),
            "latestDecisionVersion": (
                normalized_decision_chain_version
                if normalized_decision_chain_version > 0
                else None
            ),
            "latestDecisionSupersedesId": (
                str(supersedes_decision_id)
                if supersedes_decision_id is not None
                else None
            ),
            "latestDecisionExpectedPredecessorId": (
                str(expected_predecessor_decision_id)
                if expected_predecessor_decision_id is not None
                else None
            ),
            "latestDecisionChainValid": decision_chain_valid,
            "latestDecisionType": (
                str(decision_type) if decision_type is not None else None
            ),
            "latestDecisionLegalReviewProjectionId": (
                str(decision_projection_id)
                if decision_projection_id is not None
                else None
            ),
            "latestDecisionLegalReviewProjectionVersion": (
                int(decision_projection_version)
                if decision_projection_version is not None
                else None
            ),
            "latestDecisionLegalReviewProjectionSha256": (
                f"sha256:{normalized_decision_projection_hash}"
                if re.fullmatch(
                    r"[0-9a-f]{64}",
                    normalized_decision_projection_hash,
                )
                else None
            ),
            "latestDecisionProjectionBindingValid": (
                decision_projection_binding_valid
            ),
            "latestDecisionTimeValid": decision_time_valid,
            "latestDecisionAt": (
                decided_at.isoformat()
                if hasattr(decided_at, "isoformat")
                else (str(decided_at) if decided_at is not None else None)
            ),
            "latestDecisionRecordedAt": (
                decision_recorded_at.isoformat()
                if hasattr(decision_recorded_at, "isoformat")
                else (
                    str(decision_recorded_at)
                    if decision_recorded_at is not None
                    else None
                )
            ),
            "latestDecisionDelegationId": (
                str(legal_approval_delegation_id)
                if legal_approval_delegation_id is not None
                else None
            ),
            "latestDecisionDelegationValid": bool(
                delegation_valid_at_decision
            ),
            "activeBlockingMatterCount": int(
                active_blocking_matter_count or 0
            ),
            "waivedMatterCount": int(waived_matter_count or 0),
            "invalidWaiverCount": int(invalid_waiver_count or 0),
            "matterWaiverStateValid": matter_waiver_state_valid,
            "matterWaiverGovernanceValid": (
                matter_waiver_governance_valid
            ),
            "governanceChangedAfterDecision": bool(
                governance_changed_after_decision
            ),
            "latestMatterGovernanceAt": (
                latest_matter_event_at.isoformat()
                if hasattr(latest_matter_event_at, "isoformat")
                else (
                    str(latest_matter_event_at)
                    if latest_matter_event_at is not None
                    else None
                )
            ),
            "latestWaiverGovernanceAt": (
                latest_waiver_event_at.isoformat()
                if hasattr(latest_waiver_event_at, "isoformat")
                else (
                    str(latest_waiver_event_at)
                    if latest_waiver_event_at is not None
                    else None
                )
            ),
            "humanReviewStatus": human_review_status,
        }

    def evaluate_latest_approval_readiness(self) -> dict[str, Any]:
        """Select and recheck the newest project snapshot from PostgreSQL.

        No legacy Legal Pod projection participates in identity selection.
        The second authoritative read intentionally fails if a concurrent
        successor is appended between selection and evaluation because the
        selected snapshot will then be reported as superseded.
        """

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                _set_search_path(cursor, self._schema)
                cursor.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (self.tenant_id,),
                )
                cursor.execute(
                    _LATEST_APPROVAL_IDENTITY_SQL,
                    (self.tenant_id, self.project_id),
                )
                identity = cursor.fetchone()
        if identity is None:
            return _missing_approval_readiness(
                snapshot_id=None,
                reason="normalized_snapshot_missing",
            )
        snapshot_id, snapshot_version, snapshot_sha256, legal_review_id = (
            identity
        )
        try:
            return self.evaluate_approval_readiness(
                legal_review_id=_required(
                    legal_review_id,
                    "snapshot.metadata.legalReviewId",
                ),
                evidence_snapshot_id=_required(
                    snapshot_id,
                    "legal_evidence_snapshot_id",
                ),
                expected_snapshot_version=int(snapshot_version),
                expected_snapshot_sha256=_required_sha256(
                    snapshot_sha256,
                    "snapshot_sha256",
                ),
            )
        except (TypeError, ValueError):
            return _missing_approval_readiness(
                snapshot_id=(
                    str(snapshot_id)
                    if snapshot_id is not None
                    else None
                ),
                reason="normalized_snapshot_contract_invalid",
            )

    def latest_approved_required_claim_set_id(self) -> str | None:
        """Return the current project approval policy under tenant RLS."""

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                _set_search_path(cursor, self._schema)
                cursor.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (self.tenant_id,),
                )
                cursor.execute(
                    _LATEST_APPROVED_REQUIRED_CLAIM_SET_ID_SQL,
                    (self.tenant_id, self.project_id),
                )
                row = cursor.fetchone()
        return (
            str(row[0])
            if row is not None and str(row[0] or "").strip()
            else None
        )

    def append_verified_evidence_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        created_by: str,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically seal verified local-corpus references and exact items.

        This write path intentionally rejects source-attested direct API and MCP
        results.  A citation becomes gate-eligible evidence only after it has a
        verified local corpus version, immutable source-fetch lineage, and an
        exact locator.  Snapshot and item hashes are derived here; callers
        cannot choose them.
        """

        actor = _required(created_by, "created_by")
        as_of_date = _required_date(
            snapshot.get("asOfDate") or snapshot.get("as_of_date"),
            "asOfDate",
        )
        freshness_policy_version = _required(
            snapshot.get("freshnessPolicyVersion")
            or snapshot.get("freshness_policy_version"),
            "freshnessPolicyVersion",
        )
        if (
            freshness_policy_version
            != _APPROVAL_FRESHNESS_POLICY_VERSION
        ):
            raise ValueError(
                "freshnessPolicyVersion is not an approved fail-closed "
                "snapshot policy"
            )
        raw_items = snapshot.get("references")
        if not isinstance(raw_items, Sequence) or isinstance(
            raw_items,
            (str, bytes, bytearray),
        ):
            raise ValueError("references must be a non-empty sequence")
        if not raw_items:
            raise ValueError("references must contain verified local corpus evidence")

        legal_query_run_id = _optional_text(
            snapshot.get("legalQueryRunId")
            or snapshot.get("legal_query_run_id")
        )
        reported_open_conflicts = _strict_bool(
            snapshot.get("hasOpenConflicts")
            if "hasOpenConflicts" in snapshot
            else snapshot.get("has_open_conflicts", True),
            "hasOpenConflicts",
        )
        reported_stale = _strict_bool(
            snapshot.get("stale")
            if "stale" in snapshot
            else snapshot.get("isStale", True),
            "stale",
        )
        expected_citation_count = _positive_int(
            snapshot.get("expectedCitationCount")
            or snapshot.get("expected_citation_count"),
            "expectedCitationCount",
        )
        required_claim_set_id = _required(
            snapshot.get("requiredClaimSetId")
            or snapshot.get("required_claim_set_id"),
            "requiredClaimSetId",
        )
        reported_expected_claim_count = (
            _positive_int(
                snapshot.get("expectedClaimCount")
                or snapshot.get("expected_claim_count"),
                "expectedClaimCount",
            )
            if (
                snapshot.get("expectedClaimCount") is not None
                or snapshot.get("expected_claim_count") is not None
            )
            else None
        )
        reported_verified_claim_count = (
            _non_negative_int(
                snapshot.get("verifiedClaimCount")
                if snapshot.get("verifiedClaimCount") is not None
                else snapshot.get("verified_claim_count"),
                "verifiedClaimCount",
            )
            if (
                snapshot.get("verifiedClaimCount") is not None
                or snapshot.get("verified_claim_count") is not None
            )
            else None
        )
        top_level_metadata = _mapping(snapshot.get("metadata"))
        legal_review_id = _required(
            snapshot.get("legalReviewId")
            or snapshot.get("legal_review_id")
            or top_level_metadata.get("legalReviewId"),
            "legalReviewId",
        )
        top_level_metadata["legalReviewId"] = legal_review_id
        timestamp = created_at or self._now()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                _set_search_path(cursor, self._schema)
                cursor.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (self.tenant_id,),
                )
                # This closes the empty-table race that SELECT ... FOR UPDATE
                # cannot lock for a project's first snapshot.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (
                        f"{self.tenant_id}:{self.project_id}:"
                        "legal-evidence-snapshot",
                    ),
                )
                cursor.execute(
                    _APPROVED_REQUIRED_CLAIM_SET_SQL,
                    (
                        required_claim_set_id,
                        self.tenant_id,
                        self.project_id,
                    ),
                )
                claim_set = cursor.fetchone()
                if claim_set is None:
                    raise ValueError(
                        "requiredClaimSetId must reference the latest approved "
                        "same-scope approval-gate claim policy"
                    )
                (
                    required_claim_set_version,
                    required_claim_set_sha256,
                    raw_required_claim_hashes,
                    computed_claim_set_sha256,
                ) = claim_set
                normalized_claim_set_sha256 = _required_sha256(
                    required_claim_set_sha256,
                    "required claim set SHA-256",
                )
                if (
                    normalized_claim_set_sha256
                    != _required_sha256(
                        computed_claim_set_sha256,
                        "computed required claim set SHA-256",
                    )
                ):
                    raise ValueError(
                        "approved required claim set content hash mismatch"
                    )
                required_claim_hashes = {
                    _required_sha256(
                        item,
                        "required claim text SHA-256",
                    )
                    for item in (raw_required_claim_hashes or [])
                }
                if not required_claim_hashes:
                    raise ValueError(
                        "approved required claim set must contain claims"
                    )
                expected_claim_count = len(required_claim_hashes)
                if (
                    reported_expected_claim_count is not None
                    and reported_expected_claim_count
                    != expected_claim_count
                ):
                    raise ValueError(
                        "expectedClaimCount does not match the approved "
                        "required claim set"
                    )
                cursor.execute(
                    _LATEST_EVIDENCE_SNAPSHOT_SQL,
                    (self.tenant_id, self.project_id),
                )
                latest = cursor.fetchone()
                latest_snapshot_id = str(latest[0]) if latest else None
                latest_version = int(latest[1]) if latest else 0
                snapshot_version = latest_version + 1
                _assert_expected_snapshot_lineage(
                    snapshot,
                    snapshot_version=snapshot_version,
                    supersedes_snapshot_id=latest_snapshot_id,
                )

                normalized_items = [
                    self._verified_snapshot_item(
                        cursor,
                        raw_item,
                        index=index,
                        as_of_date=as_of_date,
                    )
                    for index, raw_item in enumerate(raw_items)
                ]
                database_open_issues = any(
                    bool(item.pop("_databaseOpenIssue", False))
                    for item in normalized_items
                )
                database_stale = any(
                    _datetime(item["verifiedAt"])
                    + _APPROVAL_FRESHNESS_WINDOW
                    < timestamp
                    for item in normalized_items
                )
                sealed_citations = {
                    (
                        item["resourceId"],
                        item["resourceVersionId"],
                        item["legalProvisionId"],
                    )
                    for item in normalized_items
                }
                sealed_citation_count = len(sealed_citations)
                sealed_claim_hashes = {
                    item["claimTextSha256"]
                    for item in normalized_items
                }
                verified_claim_count = len(
                    required_claim_hashes.intersection(
                        sealed_claim_hashes
                    )
                )
                if (
                    reported_verified_claim_count is not None
                    and reported_verified_claim_count
                    != verified_claim_count
                ):
                    raise ValueError(
                        "verifiedClaimCount does not match normalized "
                        "evidence membership"
                    )
                coverage_complete = (
                    sealed_citation_count == expected_citation_count
                )
                claim_coverage_complete = (
                    verified_claim_count == expected_claim_count
                )
                coverage_complete = (
                    coverage_complete and claim_coverage_complete
                )
                has_open_conflicts = (
                    reported_open_conflicts or database_open_issues
                )
                is_stale = reported_stale or database_stale
                top_level_metadata = {
                    **top_level_metadata,
                    "coverageComplete": coverage_complete,
                    "expectedCitationCount": expected_citation_count,
                    "sealedCitationCount": sealed_citation_count,
                    "expectedClaimCount": expected_claim_count,
                    "verifiedClaimCount": verified_claim_count,
                    "claimCoverageComplete": claim_coverage_complete,
                    "requiredClaimSetId": required_claim_set_id,
                    "requiredClaimSetVersion": int(
                        required_claim_set_version
                    ),
                    "requiredClaimSetSha256": (
                        normalized_claim_set_sha256
                    ),
                    "databaseOpenIssueDetected": database_open_issues,
                    "databaseFreshnessDerived": True,
                }
                normalized_items.sort(
                    key=lambda item: (
                        item["resourceId"],
                        item["resourceVersionId"],
                        item.get("legalProvisionId") or "",
                        item["claimTextSha256"],
                        item["excerptSha256"],
                        item["exactLocator"],
                    )
                )
                item_hashes = [
                    _canonical_sha256(item)
                    for item in normalized_items
                ]
                if len(item_hashes) != len(set(item_hashes)):
                    raise ValueError(
                        "references contain duplicate exact evidence items"
                    )
                canonical_snapshot = {
                    "schemaVersion": "realytics-legal-evidence-snapshot-v1",
                    "tenantId": self.tenant_id,
                    "projectId": self.project_id,
                    "legalQueryRunId": legal_query_run_id,
                    "snapshotVersion": snapshot_version,
                    "asOfDate": as_of_date.isoformat(),
                    "freshnessPolicyVersion": freshness_policy_version,
                    "citationVerificationStatus": "verified",
                    "hasOpenConflicts": has_open_conflicts,
                    "stale": is_stale,
                    "supersedesSnapshotId": latest_snapshot_id,
                    "items": normalized_items,
                    "metadata": top_level_metadata,
                }
                snapshot_sha256 = _canonical_sha256(canonical_snapshot)
                snapshot_id = (
                    "legal-evidence-snapshot:"
                    f"{_id_digest(self.tenant_id, self.project_id)}:"
                    f"v{snapshot_version}:{snapshot_sha256[:20]}"
                )
                gate_eligible = (
                    bool(normalized_items)
                    and coverage_complete
                    and not has_open_conflicts
                    and not is_stale
                )
                cursor.execute(
                    _INSERT_EVIDENCE_SNAPSHOT_SQL,
                    (
                        snapshot_id,
                        self.tenant_id,
                        self.project_id,
                        legal_query_run_id,
                        snapshot_version,
                        as_of_date,
                        freshness_policy_version,
                        snapshot_sha256,
                        "verified",
                        has_open_conflicts,
                        is_stale,
                        gate_eligible,
                        latest_snapshot_id,
                        required_claim_set_id,
                        normalized_claim_set_sha256,
                        legal_review_id,
                        actor,
                        timestamp,
                        _canonical_json(top_level_metadata),
                    ),
                )
                inserted_snapshot = cursor.fetchone()
                if inserted_snapshot is None:
                    raise ValueError(
                        "legal evidence snapshot insert did not return an id"
                    )

                persisted_items: list[dict[str, Any]] = []
                for ordinal, item in enumerate(normalized_items, start=1):
                    item_sha256 = _canonical_sha256(item)
                    item_id = (
                        f"legal-evidence-item:{snapshot_sha256[:16]}:"
                        f"{ordinal}:{item_sha256[:20]}"
                    )
                    cursor.execute(
                        _INSERT_EVIDENCE_ITEM_SQL,
                        (
                            item_id,
                            self.tenant_id,
                            self.project_id,
                            snapshot_id,
                            item.get("legalFindingId"),
                            item["resourceId"],
                            item["resourceVersionId"],
                            item.get("legalProvisionId"),
                            item["sourceFetchId"],
                            item["exactLocator"],
                            item["claimTextSha256"],
                            item["excerptSha256"],
                            "verified",
                            _datetime(item["verifiedAt"]),
                            _canonical_json(item.get("metadata") or {}),
                        ),
                    )
                    inserted_item = cursor.fetchone()
                    if inserted_item is None:
                        raise ValueError(
                            "legal evidence snapshot item insert did not return an id"
                        )
                    persisted_items.append(
                        {
                            "legalEvidenceItemId": str(inserted_item[0]),
                            **item,
                        }
                    )

        return {
            "schemaVersion": "realytics-legal-evidence-snapshot-result-v1",
            "legalEvidenceSnapshotId": str(inserted_snapshot[0]),
            "snapshotVersion": snapshot_version,
            "supersedesSnapshotId": latest_snapshot_id,
            "snapshotSha256": f"sha256:{snapshot_sha256}",
            "citationVerificationStatus": "verified",
            "verifiedReferenceCount": len(persisted_items),
            "verifiedCitationCount": sealed_citation_count,
            "expectedCitationCount": expected_citation_count,
            "coverageComplete": coverage_complete,
            "expectedClaimCount": expected_claim_count,
            "verifiedClaimCount": verified_claim_count,
            "claimCoverageComplete": claim_coverage_complete,
            "requiredClaimSetId": required_claim_set_id,
            "requiredClaimSetVersion": int(required_claim_set_version),
            "requiredClaimSetSha256": (
                f"sha256:{normalized_claim_set_sha256}"
            ),
            "legalReviewId": legal_review_id,
            "hasOpenConflicts": has_open_conflicts,
            "stale": is_stale,
            "gateEligible": gate_eligible,
            "asOfDate": as_of_date.isoformat(),
            "freshnessPolicyVersion": freshness_policy_version,
            "createdBy": actor,
            "createdAt": timestamp.isoformat(),
            "items": persisted_items,
        }

    def _verified_snapshot_item(
        self,
        cursor: Any,
        value: Any,
        *,
        index: int,
        as_of_date: date,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"references[{index}] must be an object")
        transport = _required(value.get("transport"), f"references[{index}].transport")
        if transport != SourceTransport.LOCAL_VERIFIED_SNAPSHOT.value:
            raise ValueError(
                f"references[{index}] is not a verified local corpus reference"
            )
        verification_status = _required(
            value.get("verificationStatus")
            or value.get("trustLevel"),
            f"references[{index}].verificationStatus",
        )
        if verification_status != TrustLevel.VERIFIED_SNAPSHOT.value:
            raise ValueError(
                f"references[{index}] must have verified_snapshot status"
            )

        resource_id = _required(
            value.get("resourceId"),
            f"references[{index}].resourceId",
        )
        resource_version_id = _required(
            value.get("resourceVersionId"),
            f"references[{index}].resourceVersionId",
        )
        source_fetch_id = _required(
            value.get("sourceFetchId"),
            f"references[{index}].sourceFetchId",
        )
        provision_id = _optional_text(
            value.get("legalProvisionId") or value.get("provisionId")
        )
        if provision_id is None:
            raise ValueError(
                f"references[{index}].legalProvisionId is required for an "
                "exact verified citation"
            )
        exact_locator = _required(
            value.get("exactLocator") or value.get("locator"),
            f"references[{index}].exactLocator",
        )
        normalized_content_hash = _required_sha256(
            value.get("normalizedContentSha256"),
            f"references[{index}].normalizedContentSha256",
        )
        verified_at = _required_datetime(
            value.get("verifiedAt"),
            f"references[{index}].verifiedAt",
        )
        claim_hash = _text_or_required_sha256(
            value,
            text_key="claimText",
            hash_key="claimTextSha256",
            field_prefix=f"references[{index}]",
        )
        excerpt_hash = _text_or_required_sha256(
            value,
            text_key="excerpt",
            hash_key="excerptSha256",
            field_prefix=f"references[{index}]",
        )

        cursor.execute(
            _VERIFIED_CORPUS_LINEAGE_SQL,
            (
                provision_id,
                resource_id,
                resource_version_id,
                provision_id,
                as_of_date,
                as_of_date,
                as_of_date,
                as_of_date,
            ),
        )
        lineage = cursor.fetchone()
        if lineage is None:
            raise ValueError(
                f"references[{index}] has no matching local corpus lineage"
            )
        (
            stored_resource_id,
            stored_version_id,
            stored_fetch_id,
            stored_hash,
            stored_verified_at,
            stored_verification_status,
            stored_fetch_status,
            stored_exact_locator,
            stored_provision_text,
            stored_provision_hash,
        ) = lineage
        if str(stored_resource_id) != resource_id:
            raise ValueError(f"references[{index}].resourceId lineage mismatch")
        if str(stored_version_id) != resource_version_id:
            raise ValueError(
                f"references[{index}].resourceVersionId lineage mismatch"
            )
        if str(stored_fetch_id) != source_fetch_id:
            raise ValueError(f"references[{index}].sourceFetchId lineage mismatch")
        if str(stored_hash or "").casefold() != normalized_content_hash:
            raise ValueError(
                f"references[{index}].normalizedContentSha256 lineage mismatch"
            )
        if str(stored_verification_status) not in {
            "source_verified",
            "schema_verified",
        } or stored_verified_at is None:
            raise ValueError(
                f"references[{index}] corpus version is not verified"
            )
        if str(stored_fetch_status) not in {"succeeded", "not_modified"}:
            raise ValueError(
                f"references[{index}] source fetch is not complete"
            )
        if not _same_instant(verified_at, _datetime(stored_verified_at)):
            raise ValueError(f"references[{index}].verifiedAt lineage mismatch")
        if not str(stored_exact_locator or "").strip():
            raise ValueError(
                f"references[{index}] corpus version has no exact locator"
            )
        if str(stored_exact_locator).strip() != exact_locator:
            raise ValueError(f"references[{index}].exactLocator lineage mismatch")
        supplied_excerpt = _optional_text(value.get("excerpt"))
        if supplied_excerpt is not None:
            normalized_excerpt = _normalized_evidence_text(supplied_excerpt)
            normalized_provision = _normalized_evidence_text(
                stored_provision_text
            )
            if (
                not normalized_excerpt
                or normalized_excerpt not in normalized_provision
            ):
                raise ValueError(
                    f"references[{index}].excerpt does not match the exact "
                    "official provision text"
                )
        elif excerpt_hash != str(stored_provision_hash or "").casefold():
            raise ValueError(
                f"references[{index}].excerptSha256 does not match the exact "
                "official provision hash"
            )

        cursor.execute(
            _OPEN_CORPUS_ISSUES_SQL,
            (
                resource_id,
                resource_version_id,
                as_of_date,
                as_of_date,
                as_of_date,
                as_of_date,
                resource_version_id,
                provision_id,
            ),
        )
        issue_row = cursor.fetchone()
        database_open_issue = bool(issue_row and issue_row[0])

        return {
            "resourceId": resource_id,
            "resourceVersionId": resource_version_id,
            "sourceFetchId": source_fetch_id,
            "legalProvisionId": provision_id,
            "legalFindingId": _optional_text(value.get("legalFindingId")),
            "exactLocator": exact_locator,
            "normalizedContentSha256": normalized_content_hash,
            "claimTextSha256": claim_hash,
            "excerptSha256": excerpt_hash,
            "verifiedAt": verified_at.isoformat(),
            "metadata": _mapping(value.get("metadata")),
            "_databaseOpenIssue": database_open_issue,
        }

    def append_review_decision(self, decision: Mapping[str, Any]) -> str:
        decision_id = _required(decision.get("decisionId"), "decisionId")
        decision_type = _required(
            decision.get("decisionType"),
            "decisionType",
        )
        decision_metadata = _mapping(decision.get("metadata"))
        legal_review_id = _required(
            decision_metadata.get("legalReviewId"),
            "metadata.legalReviewId",
        )
        reviewer_role = _required(
            decision.get("reviewerRole"),
            "reviewerRole",
        ).casefold()
        delegation_id = _optional_text(
            decision.get("legalApprovalDelegationId")
        )
        expected_predecessor_id = _optional_text(
            decision.get("expectedLatestDecisionId")
            or decision_metadata.get("expectedLatestDecisionId")
        )
        final_decision = decision_type in {
            "approve",
            "reject",
            "waive_with_conditions",
        }
        decided_at = _required_datetime(
            decision.get("decidedAt"),
            "decidedAt",
        )
        evidence_snapshot_id = (
            _required(
                decision.get("legalEvidenceSnapshotId"),
                "legalEvidenceSnapshotId",
            )
            if final_decision
            else _optional_text(decision.get("legalEvidenceSnapshotId"))
        )
        if final_decision and reviewer_role not in {
            "legal_counsel_approver",
            "external_counsel",
        }:
            raise ValueError(
                "final legal decisions require a dedicated counsel "
                "approver role"
            )
        if (
            final_decision
            and reviewer_role == "external_counsel"
            and not delegation_id
        ):
            raise ValueError(
                "external counsel final decisions require an explicit "
                "legalApprovalDelegationId"
            )
        raw_projection_id = (
            decision.get("legalReviewProjectionId")
            or decision_metadata.get("legalReviewProjectionId")
        )
        raw_projection_version = (
            decision.get("legalReviewProjectionVersion")
            if decision.get("legalReviewProjectionVersion") is not None
            else decision_metadata.get("legalReviewProjectionVersion")
        )
        raw_projection_hash = (
            decision.get("legalReviewProjectionSha256")
            or decision_metadata.get("legalReviewProjectionSha256")
        )
        projection_binding_supplied = any(
            value not in (None, "")
            for value in (
                raw_projection_id,
                raw_projection_version,
                raw_projection_hash,
            )
        )
        if final_decision or projection_binding_supplied:
            legal_review_version_id = _required(
                raw_projection_id,
                "legalReviewProjectionId",
            )
            legal_review_version = _positive_int(
                raw_projection_version,
                "legalReviewProjectionVersion",
            )
            legal_review_projection_sha256 = _required_sha256(
                raw_projection_hash,
                "legalReviewProjectionSha256",
            )
        else:
            legal_review_version_id = None
            legal_review_version = None
            legal_review_projection_sha256 = None
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                _set_search_path(cursor, self._schema)
                cursor.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (self.tenant_id,),
                )
                if legal_review_version_id is not None:
                    cursor.execute(
                        _LATEST_LEGAL_REVIEW_PROJECTION_FOR_DECISION_SQL,
                        (
                            self.tenant_id,
                            self.project_id,
                            legal_review_id,
                        ),
                    )
                    projection_row = cursor.fetchone()
                    if projection_row is None:
                        raise ValueError(
                            "final legal decision requires the latest "
                            "normalized legal review projection"
                        )
                    (
                        stored_projection_id,
                        stored_projection_version,
                        stored_projection_hash_contract,
                        stored_projection_sha256,
                        computed_projection_sha256,
                        projection_snapshot_id,
                        projection_created_at,
                        projection_snapshot_created_at,
                        database_statement_time,
                    ) = projection_row
                    if (
                        str(stored_projection_id or "")
                        != legal_review_version_id
                        or int(stored_projection_version or 0)
                        != legal_review_version
                        or str(stored_projection_hash_contract or "")
                        != "pg-jsonb-row-v1"
                        or str(
                            stored_projection_sha256 or ""
                        ).strip().casefold()
                        != legal_review_projection_sha256
                        or str(
                            computed_projection_sha256 or ""
                        ).strip().casefold()
                        != legal_review_projection_sha256
                    ):
                        raise ValueError(
                            "legal review projection changed before the "
                            "decision was recorded"
                        )
                    if (
                        final_decision
                        and str(projection_snapshot_id or "")
                        != str(evidence_snapshot_id or "")
                    ):
                        raise ValueError(
                            "legal review projection does not match the "
                            "decision evidence snapshot"
                        )
                    if (
                        projection_created_at is None
                        or _datetime(projection_created_at) > decided_at
                    ):
                        raise ValueError(
                            "legal review projection must exist before the "
                            "decision timestamp"
                        )
                    if (
                        final_decision
                        and (
                            projection_snapshot_created_at is None
                            or _datetime(projection_snapshot_created_at)
                            > decided_at
                        )
                    ):
                        raise ValueError(
                            "legal evidence snapshot must exist before the "
                            "decision timestamp"
                        )
                    if final_decision:
                        database_now = _datetime(database_statement_time)
                        tolerance = timedelta(minutes=5)
                        if not (
                            database_now - tolerance
                            <= decided_at
                            <= database_now + tolerance
                        ):
                            raise ValueError(
                                "decidedAt must be within five minutes of "
                                "database statement time"
                            )
                if decision_type == "approve":
                    cursor.execute(
                        _GATE_ELIGIBLE_SNAPSHOT_SQL,
                        (
                            evidence_snapshot_id,
                            self.tenant_id,
                            self.project_id,
                        ),
                    )
                    snapshot_row = cursor.fetchone()
                    if snapshot_row is None:
                        raise ValueError(
                            "approve requires a same-scope gate-eligible "
                            "verified legal evidence snapshot"
                        )
                    snapshot_version, snapshot_sha256, snapshot_review_id = (
                        snapshot_row
                    )
                    if (
                        str(snapshot_review_id or "")
                        != legal_review_id
                    ):
                        raise ValueError(
                            "legal review decision does not match the sealed "
                            "snapshot review lineage"
                        )
                    expected_version = decision_metadata.get(
                        "evidenceSnapshotVersion"
                    )
                    if (
                        expected_version is not None
                        and int(expected_version) != int(snapshot_version)
                    ):
                        raise ValueError(
                            "evidence snapshot version changed before approval"
                        )
                    expected_hash = _optional_text(
                        decision_metadata.get("evidenceSnapshotSha256")
                    )
                    if expected_hash and _required_sha256(
                        expected_hash,
                        "metadata.evidenceSnapshotSha256",
                    ) != str(snapshot_sha256).casefold():
                        raise ValueError(
                            "evidence snapshot hash changed before approval"
                        )
                try:
                    cursor.execute(
                        _INSERT_REVIEW_DECISION_SQL,
                        (
                            decision_id,
                            self.tenant_id,
                            self.project_id,
                            decision.get("legalFindingId"),
                            evidence_snapshot_id,
                            decision_type,
                            _required(
                                decision.get("decisionScope"),
                                "decisionScope",
                            ),
                            _required(decision.get("reason"), "reason"),
                            _required(
                                decision.get("reviewerId"),
                                "reviewerId",
                            ),
                            reviewer_role,
                            delegation_id,
                            legal_review_id,
                            legal_review_version_id,
                            legal_review_version,
                            legal_review_projection_sha256,
                            expected_predecessor_id,
                            decided_at,
                            json.dumps(
                                decision_metadata,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                            ),
                        ),
                    )
                except Exception as exc:
                    if getattr(exc, "sqlstate", None) == "40001":
                        raise LegalReviewDecisionConflict(
                            "legal review decision predecessor changed; "
                            "reload approval readiness before deciding"
                        ) from exc
                    raise
                inserted = cursor.fetchone()
                if inserted is None:
                    raise ValueError(
                        "legal review decision already exists; decisions are append-only"
                    )
        return str(inserted[0])


def _row_to_hit(row: Sequence[Any], query: LegalSearchQuery) -> SearchHit:
    resource_metadata = _mapping(row[4])
    version_metadata = _mapping(row[17])
    has_provision_projection = len(row) >= 32
    provision_id = (
        _optional_text(row[22])
        if has_provision_projection
        else None
    )
    provision_identifier = (
        _optional_text(row[23])
        if has_provision_projection
        else None
    )
    provision_display_path = (
        _optional_text(row[24])
        if has_provision_projection
        else None
    )
    provision_heading = (
        _optional_text(row[25])
        if has_provision_projection
        else None
    )
    provision_text = (
        _optional_text(row[26])
        if has_provision_projection
        else None
    )
    provision_sha256 = (
        _optional_text(row[27])
        if has_provision_projection
        else None
    )
    provision_locator = (
        _optional_text(row[28])
        if has_provision_projection
        else None
    )
    provision_metadata = (
        _mapping(row[29])
        if has_provision_projection
        else {}
    )
    stage_index = 30 if has_provision_projection else 22
    score_index = 31 if has_provision_projection else 23
    resource_kind = (
        LegalResourceKind.PROVISION
        if provision_id
        or (
            LegalResourceKind.PROVISION in query.resource_kinds
            and len(query.resource_kinds) == 1
        )
        else _DATABASE_TO_CORE_KIND.get(str(row[1]), LegalResourceKind.STATUTE)
    )
    locator = (
        provision_locator
        or _first_text(version_metadata, "sourceUrl", "officialUrl", "locator")
        or str(row[21] or "")
        or _official_locator(str(row[3] or ""), str(row[19] or ""), str(row[2]))
    )
    resource_id = str(row[0])
    resource_version_id = str(row[5])
    normalized_hash = str(row[16] or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
        raise ValueError("local corpus normalized hash is malformed")
    if provision_id and not re.fullmatch(
        r"[0-9a-f]{64}",
        str(provision_sha256 or "").casefold(),
    ):
        raise ValueError("local corpus provision hash is malformed")
    if resource_version_id.startswith("legal-resource-version:"):
        expected_version_id = (
            "legal-resource-version:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "legalResourceId": resource_id,
                        "normalizedSha256": normalized_hash,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )
        if resource_version_id != expected_version_id:
            raise ValueError(
                "local corpus version id is not bound to normalized hash"
            )
    content = {
        "resourceId": resource_id,
        "resourceVersionId": resource_version_id,
        "sourceFetchId": str(row[6]),
        "resourceType": resource_kind.value,
        "officialIdentifier": str(row[2]),
        "issuingAuthority": str(row[3]),
        "title": str(row[8]),
        "alternativeTitles": str(row[9] or ""),
        "officialText": (
            provision_text
            or _first_text(
                version_metadata,
                "officialText",
                "official_text",
                "text",
                "content",
            )
        ),
        "metadata": version_metadata,
        "resourceMetadata": resource_metadata,
        "normalizedContentSha256": f"sha256:{normalized_hash}",
    }
    if provision_id:
        content.update(
            {
                "legalProvisionId": provision_id,
                "provisionId": provision_id,
                "provisionIdentifier": provision_identifier,
                "displayPath": provision_display_path,
                "heading": provision_heading,
                "provisionSha256": (
                    f"sha256:{provision_sha256}"
                    if provision_sha256
                    else None
                ),
                "provisionMetadata": provision_metadata,
            }
        )
    authority = _authority(str(row[18] or ""))
    attestation = build_attestation(
        content=content,
        source_authority=authority,
        source_transport=SourceTransport.LOCAL_VERIFIED_SNAPSHOT,
        trust_level=TrustLevel.VERIFIED_SNAPSHOT,
        resource_kind=resource_kind,
        official_resource_id=str(row[2]),
        official_version_id=str(row[7]),
        locator=locator,
        as_of=query.as_of,
        observed_at=_datetime(row[12]),
        fetched_at=_datetime(row[13]),
        verified_at=_datetime(row[14]),
        verified_effective_date=_date(row[10]),
        effective_from=_date(row[10]),
        effective_to=_date(row[11]),
        article=provision_display_path or provision_identifier,
    )
    return SearchHit(
        title=str(row[8]),
        excerpt=(
            provision_text
            or _first_text(version_metadata, "excerpt", "summary")
            or str(row[9] or "")
            or None
        ),
        stage=SearchStage(str(row[stage_index])),
        lexical_score=max(
            0.0,
            min(float(row[score_index] or 0.0), 1.0),
        ),
        content=content,
        attestation=attestation,
    )


def _resource_detail(
    row: Sequence[Any],
    provision_rows: Sequence[Sequence[Any]],
    as_of: date,
    *,
    freshness: FreshnessStatus,
    provision_limit: int,
    provision_offset: int,
    provisions_have_more: bool,
) -> dict[str, Any]:
    resource_metadata = _mapping(row[5])
    version_metadata = _mapping(row[21])
    provisions = [
        {
            "provisionId": str(item[0]),
            "provisionType": str(item[1]),
            "provisionIdentifier": str(item[2]),
            "displayPath": str(item[3]),
            "heading": item[4],
            "officialText": str(item[5]),
            "contentHash": f"sha256:{item[6]}",
            "locator": str(item[7]),
            "metadata": _mapping(item[8]),
        }
        for item in provision_rows
    ]
    locator = (
        _first_text(version_metadata, "sourceUrl", "officialUrl", "locator")
        or str(row[25] or "")
        or _official_locator(str(row[4]), str(row[23] or ""), str(row[2]))
    )
    kind = _DATABASE_TO_CORE_KIND.get(str(row[1]), LegalResourceKind.STATUTE)
    verified_at = _datetime(row[17]) if row[17] is not None else None
    return {
        "resourceId": str(row[0]),
        "resourceVersionId": str(row[6]),
        "resourceType": kind.value,
        "contentClass": "official_text",
        "title": str(row[9]),
        "citation": str(row[2]),
        "sourceAuthority": _authority(str(row[22] or "")).value,
        "transport": SourceTransport.LOCAL_VERIFIED_SNAPSHOT.value,
        "verificationStatus": (
            TrustLevel.VERIFIED_SNAPSHOT.value
            if verified_at is not None
            else "not_verified"
        ),
        "freshnessStatus": freshness.value,
        "stale": freshness != FreshnessStatus.FRESH,
        "asOfDate": as_of.isoformat(),
        "effectiveFrom": _iso(row[13]),
        "effectiveTo": _iso(row[14]),
        "observedAt": _iso(row[15]),
        "fetchedAt": _iso(row[16]),
        "verifiedAt": _iso(row[17]),
        "sourceUrl": locator if locator.startswith(("http://", "https://")) else None,
        "locator": locator,
        "officialText": _first_text(
            version_metadata,
            "officialText",
            "official_text",
            "text",
            "content",
        ),
        "officialIdentifiers": {
            "officialIdentifier": str(row[2]),
            "officialVersionIdentifier": str(row[8]),
            "jurisdiction": str(row[3]),
        },
        "sourcePayloadSha256": str(row[26] or "") or None,
        "normalizedContentSha256": f"sha256:{row[19]}",
        "parserSchemaVersion": str(row[20]),
        "metadata": {
            **resource_metadata,
            **version_metadata,
        },
        "provisions": provisions,
        "provisionPage": {
            "offset": provision_offset,
            "limit": provision_limit,
            "returnedCount": len(provisions),
            "hasMore": provisions_have_more,
            "nextOffset": (
                provision_offset + len(provisions)
                if provisions_have_more
                else None
            ),
        },
    }


def _database_kinds(kinds: Sequence[LegalResourceKind]) -> tuple[str, ...]:
    values: list[str] = []
    for kind in kinds:
        values.extend(_CORE_TO_DATABASE_KINDS[kind])
    return tuple(dict.fromkeys(values))


def _schema_name(value: str | None) -> str:
    schema = (
        value
        or os.environ.get("REALYTICS_DATABASE_SCHEMA")
        or _DEFAULT_SCHEMA
    ).strip()
    if not _SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("REALYTICS_DATABASE_SCHEMA must be a simple SQL identifier")
    return schema


def _set_search_path(cursor: Any, schema: str) -> None:
    cursor.execute(
        "SELECT set_config('search_path', %s, true)",
        (f"{schema},extensions,pg_temp",),
    )


def _normalized(value: Any) -> str:
    return re.sub(r"[\s·ㆍ_\-]+", "", str(value or "")).casefold()


def _authority(value: str) -> SourceAuthority:
    return _AUTHORITY_ALIASES.get(value.strip().casefold(), SourceAuthority.UNKNOWN)


def _freshness_status(
    verified_at: datetime | None,
    *,
    now: datetime,
    freshness: timedelta,
) -> FreshnessStatus:
    if verified_at is None:
        return FreshnessStatus.UNKNOWN
    comparable = verified_at
    if comparable.tzinfo is None:
        comparable = comparable.replace(tzinfo=timezone.utc)
    return (
        FreshnessStatus.FRESH
        if comparable + freshness >= now
        else FreshnessStatus.STALE
    )


def _official_locator(
    issuing_authority: str,
    canonical_base_url: str,
    identifier: str,
) -> str:
    if canonical_base_url.startswith(("http://", "https://")):
        return canonical_base_url.rstrip("/") + "/" + identifier
    return f"official:{issuing_authority or 'unknown'}:{identifier}"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _first_text(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return (
            value
            if value.tzinfo is not None
            else value.replace(tzinfo=timezone.utc)
        )
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _required_date(value: Any, name: str) -> date:
    if value is None or not str(value).strip():
        raise ValueError(f"{name} is required")
    try:
        parsed = _date(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed is None:
        raise ValueError(f"{name} is required")
    return parsed


def _required_datetime(value: Any, name: str) -> datetime:
    if value is None or not str(value).strip():
        raise ValueError(f"{name} is required")
    try:
        return _datetime(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO datetime") from exc


def _strict_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be a boolean")


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _non_negative_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed


def _non_negative_integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _missing_approval_readiness(
    *,
    snapshot_id: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": "realytics-normalized-legal-readiness-v1",
        "source": "normalized_postgres",
        "readable": True,
        "pass": False,
        "reason": reason,
        "legalReviewProjectionId": None,
        "legalReviewProjectionReviewId": None,
        "legalReviewProjectionVersion": None,
        "legalReviewProjectionSha256": None,
        "legalReviewProjectionValid": False,
        "legalReviewProjectionSuperseded": False,
        "legalReviewProjectionAnalysisOnly": True,
        "legalReviewProjectionReportEligible": False,
        "evidenceStatus": "missing",
        "verifiedReferenceCount": 0,
        "verifiedCitationCount": 0,
        "requiredClaimCount": 0,
        "verifiedClaimCount": 0,
        "claimCoverageComplete": False,
        "requiredClaimPolicyValid": False,
        "requiredClaimSetId": None,
        "requiredClaimSetVersion": None,
        "requiredClaimSetSha256": None,
        "evidenceSnapshotId": snapshot_id,
        "snapshotVersion": None,
        "snapshotSha256": None,
        "snapshotGateEligible": False,
        "snapshotSuperseded": False,
        "stale": True,
        "openConflictCount": 0,
        "lineageValid": False,
        "invalidLineageCount": 0,
        "coverageComplete": False,
        "latestDecisionId": None,
        "latestDecisionType": None,
        "latestDecisionLegalReviewProjectionId": None,
        "latestDecisionLegalReviewProjectionVersion": None,
        "latestDecisionLegalReviewProjectionSha256": None,
        "latestDecisionProjectionBindingValid": False,
        "latestDecisionTimeValid": False,
        "latestDecisionAt": None,
        "activeBlockingMatterCount": 0,
        "waivedMatterCount": 0,
        "invalidWaiverCount": 0,
        "matterWaiverStateValid": False,
        "matterWaiverGovernanceValid": False,
        "governanceChangedAfterDecision": True,
        "latestMatterGovernanceAt": None,
        "latestWaiverGovernanceAt": None,
        "humanReviewStatus": "pending",
    }


def _approval_readiness_reason(
    *,
    identity_matches: bool,
    projection_exists: bool,
    projection_hash_valid: bool,
    projection_identity_matches: bool,
    citation_status: str,
    has_open_conflicts: bool,
    is_stale: bool,
    gate_eligible: bool,
    snapshot_superseded: bool,
    coverage_complete: bool,
    required_claim_policy_valid: bool,
    claim_coverage_complete: bool,
    lineage_valid: bool,
    latest_decision_is_approve: bool,
    decision_projection_binding_valid: bool,
    decision_time_valid: bool,
    latest_decision_approves: bool,
    active_blocking_matter_count: int,
    invalid_waiver_count: int,
    governance_changed_after_decision: bool,
) -> str:
    checks = (
        (identity_matches, "snapshot_identity_mismatch"),
        (
            projection_exists,
            "normalized_legal_review_projection_missing",
        ),
        (
            projection_hash_valid,
            "normalized_legal_review_projection_hash_invalid",
        ),
        (
            projection_identity_matches,
            "normalized_legal_review_projection_snapshot_mismatch",
        ),
        (citation_status == "verified", "citation_status_not_verified"),
        (not has_open_conflicts, "snapshot_has_open_conflicts"),
        (not is_stale, "snapshot_is_stale"),
        (gate_eligible, "snapshot_not_gate_eligible"),
        (not snapshot_superseded, "snapshot_superseded"),
        (coverage_complete, "citation_coverage_incomplete"),
        (
            required_claim_policy_valid,
            "required_claim_policy_invalid",
        ),
        (claim_coverage_complete, "required_claim_coverage_incomplete"),
        (lineage_valid, "corpus_lineage_invalid"),
        (
            active_blocking_matter_count == 0,
            "active_blocking_legal_matter",
        ),
        (
            invalid_waiver_count == 0,
            "invalid_or_expired_legal_waiver",
        ),
        (
            latest_decision_is_approve,
            "latest_review_decision_not_approved",
        ),
        (
            decision_projection_binding_valid,
            "latest_review_decision_projection_mismatch",
        ),
        (
            decision_time_valid,
            "latest_review_decision_time_invalid",
        ),
        (
            not governance_changed_after_decision,
            "legal_governance_changed_after_decision",
        ),
        (latest_decision_approves, "latest_review_decision_not_approved"),
    )
    for passed, reason in checks:
        if not passed:
            return reason
    return "ready"


def _normalized_evidence_text(value: Any) -> str:
    """Normalize Unicode and spacing without changing the quoted words."""

    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    ).casefold()


def _sha256_hex(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if text.startswith("sha256:"):
        text = text[7:]
    return text


def _required_sha256(value: Any, name: str) -> str:
    digest = _sha256_hex(value)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return digest


def _text_or_required_sha256(
    value: Mapping[str, Any],
    *,
    text_key: str,
    hash_key: str,
    field_prefix: str,
) -> str:
    supplied_hash = value.get(hash_key)
    supplied_text = value.get(text_key)
    if supplied_hash is None and (
        supplied_text is None or not str(supplied_text).strip()
    ):
        raise ValueError(
            f"{field_prefix}.{text_key} or {field_prefix}.{hash_key} is required"
        )
    calculated = (
        hashlib.sha256(str(supplied_text).encode("utf-8")).hexdigest()
        if supplied_text is not None and str(supplied_text).strip()
        else None
    )
    if supplied_hash is None:
        return str(calculated)
    normalized = _required_sha256(
        supplied_hash,
        f"{field_prefix}.{hash_key}",
    )
    if calculated is not None and normalized != calculated:
        raise ValueError(
            f"{field_prefix}.{hash_key} does not match {field_prefix}.{text_key}"
        )
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _id_digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _same_instant(left: datetime, right: datetime) -> bool:
    def utc(value: datetime) -> datetime:
        return (
            value.astimezone(timezone.utc)
            if value.tzinfo is not None
            else value.replace(tzinfo=timezone.utc)
        )

    return utc(left) == utc(right)


def _assert_expected_snapshot_lineage(
    snapshot: Mapping[str, Any],
    *,
    snapshot_version: int,
    supersedes_snapshot_id: str | None,
) -> None:
    if (
        "expectedSnapshotVersion" in snapshot
        or "expected_snapshot_version" in snapshot
    ):
        raw_version = (
            snapshot.get("expectedSnapshotVersion")
            if "expectedSnapshotVersion" in snapshot
            else snapshot.get("expected_snapshot_version")
        )
        try:
            expected_version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("expectedSnapshotVersion must be an integer") from exc
        if expected_version != snapshot_version:
            raise ValueError(
                "expectedSnapshotVersion no longer matches the latest "
                "database state"
            )
    if (
        "expectedSupersedesSnapshotId" in snapshot
        or "expected_supersedes_snapshot_id" in snapshot
    ):
        raw_supersedes = (
            snapshot.get("expectedSupersedesSnapshotId")
            if "expectedSupersedesSnapshotId" in snapshot
            else snapshot.get("expected_supersedes_snapshot_id")
        )
        expected_supersedes = _optional_text(raw_supersedes)
        if expected_supersedes != supersedes_snapshot_id:
            raise ValueError(
                "expectedSupersedesSnapshotId no longer matches the latest "
                "database state"
            )


__all__ = [
    "LegalReviewDecisionConflict",
    "PostgresLegalTenantOverlayRepository",
    "PostgresVerifiedLegalSnapshotProvider",
]
