-- PostgreSQL/Supabase function and tenant-RLS hardening.
--
-- This migration does not broaden Data API access. It preserves the existing
-- fail-closed tenant contract:
--
--   SELECT set_config('realytics.tenant_id', '<tenant-id>', true);
--
-- A missing or empty setting still evaluates to NULL. Wrapping the stable
-- current_setting() lookup in a scalar subquery lets PostgreSQL cache it as an
-- init plan instead of evaluating it once per protected row.

-- ---------------------------------------------------------------------------
-- Function search paths and execution privileges
-- ---------------------------------------------------------------------------

-- These append-only trigger functions do not resolve application relations, so
-- an empty search path is both sufficient and the narrowest safe setting.
ALTER FUNCTION realytics.parser_reject_append_only_mutation()
    SET search_path = '';
REVOKE ALL ON FUNCTION realytics.parser_reject_append_only_mutation()
    FROM PUBLIC;

ALTER FUNCTION realytics.legal_reject_append_only_mutation()
    SET search_path = '';
REVOKE ALL ON FUNCTION realytics.legal_reject_append_only_mutation()
    FROM PUBLIC;

-- This legacy lineage trigger resolves realytics composite types and relations
-- by name. Pin it to system catalogs plus the private application schema.
ALTER FUNCTION realytics.legal_validate_resource_version_lineage()
    SET search_path = pg_catalog, realytics;
REVOKE ALL ON FUNCTION realytics.legal_validate_resource_version_lineage()
    FROM PUBLIC;

-- 0010 introduced the only SECURITY DEFINER functions in 0008-0010. Their
-- relation references are schema-qualified through TG_TABLE_SCHEMA, so they can
-- use the strict empty search path. Trigger invocation does not require callers
-- to retain direct EXECUTE privileges.
ALTER FUNCTION realytics.legal_assert_coverage_registry_lineage()
    SET search_path = '';
REVOKE ALL ON FUNCTION realytics.legal_assert_coverage_registry_lineage()
    FROM PUBLIC;

ALTER FUNCTION realytics.legal_assert_coverage_contract_identity()
    SET search_path = '';
REVOKE ALL ON FUNCTION realytics.legal_assert_coverage_contract_identity()
    FROM PUBLIC;

ALTER FUNCTION realytics.legal_assert_coverage_registry_complete()
    SET search_path = '';
REVOKE ALL ON FUNCTION realytics.legal_assert_coverage_registry_complete()
    FROM PUBLIC;

ALTER FUNCTION realytics.legal_assert_coverage_bound_sync_run()
    SET search_path = '';
REVOKE ALL ON FUNCTION realytics.legal_assert_coverage_bound_sync_run()
    FROM PUBLIC;

ALTER FUNCTION realytics.legal_assert_coverage_bound_completion()
    SET search_path = '';
REVOKE ALL ON FUNCTION realytics.legal_assert_coverage_bound_completion()
    FROM PUBLIC;

-- Supabase may install this optional public event-trigger helper. Keep the
-- helper/event trigger intact, but remove the default PUBLIC call surface when
-- the function exists. The guard keeps this migration portable to PostgreSQL
-- installations that do not install the helper.
DO $realytics_rls_auto_enable_acl$
BEGIN
    IF pg_catalog.to_regprocedure('public.rls_auto_enable()') IS NOT NULL THEN
        EXECUTE
            'REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC';
    END IF;
END;
$realytics_rls_auto_enable_acl$;

-- ---------------------------------------------------------------------------
-- Parser V2 tenant policies
-- ---------------------------------------------------------------------------

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_document_versions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_project_profiles
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_runs
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_run_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_candidate_set_seals
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parsed_fact_candidates
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_candidate_inputs
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_validation_findings
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_validation_finding_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_review_decisions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_source_conflicts
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_source_conflict_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_conflict_candidates
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_review_decision_conflicts
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_canonical_selections
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_approved_snapshots
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_snapshot_selections
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_snapshot_source_runs
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY parser_v2_tenant_isolation
    ON realytics.parser_validity_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

-- ---------------------------------------------------------------------------
-- Legal tenant policies
-- ---------------------------------------------------------------------------

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_query_runs
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_query_run_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_resource_links
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_findings
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_evidence_snapshots
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_evidence_snapshot_items
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_review_decisions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_approval_delegations
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_required_claim_sets
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_required_claims
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_matters
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_waivers
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.counsel_opinions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_analysis_versions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_annotations
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.tenant_legal_term_aliases
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.tenant_legal_term_alias_review_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_review_versions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_permit_review_versions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_provider_replay_objects
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_provider_replay_approval_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.legal_provider_replay_requests
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

-- Replay events intentionally inherit tenant identity from their immutable
-- request row; this preserves that contract while caching the setting lookup.
ALTER POLICY legal_tenant_isolation
    ON realytics.legal_provider_replay_events
    USING (
        EXISTS (
            SELECT 1
              FROM realytics.legal_provider_replay_requests
                   AS replay_request
             WHERE replay_request.legal_provider_replay_request_id =
                   legal_provider_replay_events
                       .legal_provider_replay_request_id
               AND replay_request.tenant_id = (
                   SELECT NULLIF(
                       pg_catalog.current_setting(
                           'realytics.tenant_id',
                           true
                       ),
                       ''
                   )
               )
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
              FROM realytics.legal_provider_replay_requests
                   AS replay_request
             WHERE replay_request.legal_provider_replay_request_id =
                   legal_provider_replay_events
                       .legal_provider_replay_request_id
               AND replay_request.tenant_id = (
                   SELECT NULLIF(
                       pg_catalog.current_setting(
                           'realytics.tenant_id',
                           true
                       ),
                       ''
                   )
               )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_citation_review_versions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_relation_review_versions
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_citation_review_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

ALTER POLICY legal_tenant_isolation
    ON realytics.project_legal_relation_review_events
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );
