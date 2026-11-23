-- Parser V2 tenant isolation at the PostgreSQL boundary.
--
-- Every application transaction that accesses these tables must set the local
-- custom setting before issuing queries:
--
--   SELECT set_config('realytics.tenant_id', '<tenant-id>', true);
--
-- current_setting(..., true) returns NULL when the setting is absent. The
-- NULLIF expression also rejects an explicitly empty setting, so both reads and
-- writes fail closed. FORCE keeps the table owner subject to the policy; the
-- deployment/migration role must remain separate from the application role.

ALTER TABLE parser_document_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_document_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_document_versions;
CREATE POLICY parser_v2_tenant_isolation ON parser_document_versions
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_project_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_project_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_project_profiles;
CREATE POLICY parser_v2_tenant_isolation ON parser_project_profiles
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_runs;
CREATE POLICY parser_v2_tenant_isolation ON parser_runs
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_run_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_run_events;
CREATE POLICY parser_v2_tenant_isolation ON parser_run_events
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_candidate_set_seals ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_candidate_set_seals FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_candidate_set_seals;
CREATE POLICY parser_v2_tenant_isolation ON parser_candidate_set_seals
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parsed_fact_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE parsed_fact_candidates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parsed_fact_candidates;
CREATE POLICY parser_v2_tenant_isolation ON parsed_fact_candidates
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_candidate_inputs ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_candidate_inputs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_candidate_inputs;
CREATE POLICY parser_v2_tenant_isolation ON parser_candidate_inputs
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_validation_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_validation_findings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_validation_findings;
CREATE POLICY parser_v2_tenant_isolation ON parser_validation_findings
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_validation_finding_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_validation_finding_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_validation_finding_events;
CREATE POLICY parser_v2_tenant_isolation ON parser_validation_finding_events
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_review_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_review_decisions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_review_decisions;
CREATE POLICY parser_v2_tenant_isolation ON parser_review_decisions
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_source_conflicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_source_conflicts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_source_conflicts;
CREATE POLICY parser_v2_tenant_isolation ON parser_source_conflicts
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_source_conflict_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_source_conflict_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_source_conflict_events;
CREATE POLICY parser_v2_tenant_isolation ON parser_source_conflict_events
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_conflict_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_conflict_candidates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_conflict_candidates;
CREATE POLICY parser_v2_tenant_isolation ON parser_conflict_candidates
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_review_decision_conflicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_review_decision_conflicts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_review_decision_conflicts;
CREATE POLICY parser_v2_tenant_isolation ON parser_review_decision_conflicts
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_canonical_selections ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_canonical_selections FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_canonical_selections;
CREATE POLICY parser_v2_tenant_isolation ON parser_canonical_selections
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_approved_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_approved_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_approved_snapshots;
CREATE POLICY parser_v2_tenant_isolation ON parser_approved_snapshots
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_snapshot_selections ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_snapshot_selections FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_snapshot_selections;
CREATE POLICY parser_v2_tenant_isolation ON parser_snapshot_selections
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_snapshot_source_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_snapshot_source_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_snapshot_source_runs;
CREATE POLICY parser_v2_tenant_isolation ON parser_snapshot_source_runs
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));

ALTER TABLE parser_validity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE parser_validity_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS parser_v2_tenant_isolation ON parser_validity_events;
CREATE POLICY parser_v2_tenant_isolation ON parser_validity_events
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''));
