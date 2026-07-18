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

_SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_SCHEMA = "realytics"
_DEFAULT_FRESHNESS_DAYS = 7

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
WITH required_provider AS (
    SELECT provider.provider_id
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
latest_complete AS (
    SELECT DISTINCT ON (completion.provider_id)
        completion.provider_id,
        terminal_checkpoint.occurred_at AS completed_at
      FROM legal_corpus_scope_completion_attestations AS completion
      JOIN legal_sync_checkpoint_events AS terminal_checkpoint
        ON terminal_checkpoint.provider_id = completion.provider_id
       AND terminal_checkpoint.requested_scope_hash =
           completion.requested_scope_hash
       AND terminal_checkpoint.checkpoint_sequence =
           completion.last_checkpoint_sequence
     WHERE completion.verification_status = 'verified'
       AND completion.page_count > 0
       AND completion.record_count > 0
       AND completion.resource_count > 0
       AND completion.manifest_sha256 ~ '^[0-9a-f]{64}$'
       AND jsonb_array_length(completion.page_hashes_json) =
           completion.page_count
       AND lower(completion.requested_scope) IN (
            'all',
            'full',
            'full_corpus'
       )
     ORDER BY
        completion.provider_id,
        completion.last_checkpoint_sequence DESC,
        terminal_checkpoint.occurred_at DESC,
        completion.completion_attestation_id DESC
)
SELECT
    CASE
        WHEN COUNT(required_provider.provider_id) = 0 THEN NULL
        WHEN COUNT(latest_complete.provider_id)
             <> COUNT(required_provider.provider_id) THEN NULL
        ELSE MIN(latest_complete.completed_at)
    END
  FROM required_provider
  LEFT JOIN latest_complete
    ON latest_complete.provider_id = required_provider.provider_id
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
    decided_at,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT (legal_review_decision_id) DO NOTHING
RETURNING legal_review_decision_id
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

_GATE_ELIGIBLE_SNAPSHOT_SQL = """
SELECT
    snapshot.snapshot_version,
    snapshot.snapshot_sha256,
    snapshot.metadata_json
  FROM legal_evidence_snapshots AS snapshot
 WHERE snapshot.legal_evidence_snapshot_id = %s
   AND snapshot.tenant_id = %s
   AND snapshot.project_id = %s
   AND snapshot.gate_eligible
   AND snapshot.citation_verification_status = 'verified'
   AND NOT snapshot.has_open_conflicts
   AND NOT snapshot.is_stale
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
    created_by,
    created_at,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s::jsonb
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
        expected_claim_count = _positive_int(
            snapshot.get("expectedClaimCount")
            or snapshot.get("expected_claim_count"),
            "expectedClaimCount",
        )
        verified_claim_count = _non_negative_int(
            snapshot.get("verifiedClaimCount")
            if snapshot.get("verifiedClaimCount") is not None
            else snapshot.get("verified_claim_count"),
            "verifiedClaimCount",
        )
        top_level_metadata = _mapping(snapshot.get("metadata"))
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
                    _datetime(item["verifiedAt"]) + self._freshness < timestamp
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
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                _set_search_path(cursor, self._schema)
                cursor.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (self.tenant_id,),
                )
                if decision_type == "approve":
                    evidence_snapshot_id = _required(
                        decision.get("legalEvidenceSnapshotId"),
                        "legalEvidenceSnapshotId",
                    )
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
                    snapshot_version, snapshot_sha256, snapshot_metadata = (
                        snapshot_row
                    )
                    stored_metadata = _mapping(snapshot_metadata)
                    if (
                        str(stored_metadata.get("legalReviewId") or "")
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
                cursor.execute(
                    _INSERT_REVIEW_DECISION_SQL,
                    (
                        decision_id,
                        self.tenant_id,
                        self.project_id,
                        decision.get("legalFindingId"),
                        decision.get("legalEvidenceSnapshotId"),
                        decision_type,
                        _required(decision.get("decisionScope"), "decisionScope"),
                        _required(decision.get("reason"), "reason"),
                        _required(decision.get("reviewerId"), "reviewerId"),
                        _required(decision.get("reviewerRole"), "reviewerRole"),
                        _required(decision.get("decidedAt"), "decidedAt"),
                        json.dumps(
                            decision_metadata,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                )
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
        fetched_at=_datetime(row[13]),
        verified_at=_datetime(row[14]),
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
    "PostgresLegalTenantOverlayRepository",
    "PostgresVerifiedLegalSnapshotProvider",
]
