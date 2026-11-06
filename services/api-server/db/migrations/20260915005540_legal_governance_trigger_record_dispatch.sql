-- Resolve each trigger record shape only after dispatching on its table.
-- Keep the original approval, delegation, evidence and cure predicates intact.

CREATE OR REPLACE FUNCTION legal_assert_governance_delegation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_governance_delegation$
DECLARE
    actor_id TEXT;
    actor_role TEXT;
    delegation_id TEXT;
    delegation_scope TEXT;
    authority_at TIMESTAMPTZ;
    delegation_valid BOOLEAN;
    cure_delegation_valid BOOLEAN;
BEGIN
    IF TG_TABLE_NAME = 'project_legal_matters' THEN
        IF NEW.status IN (
               'resolved',
               'waived',
               'closed',
               'superseded'
           ) THEN
            actor_id := NEW.resolved_by;
            actor_role := NEW.resolver_role;
            delegation_id := NEW.legal_approval_delegation_id;
            delegation_scope := 'matter_resolution';
            authority_at := NEW.resolved_at;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'legal_required_claim_sets' THEN
        IF NEW.status = 'approved' THEN
            actor_id := NEW.approved_by;
            actor_role := NEW.approver_role;
            delegation_id := NEW.legal_approval_delegation_id;
            delegation_scope := 'approval_gate';
            authority_at := NEW.approved_at;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'legal_waivers' THEN
        IF NEW.status IN ('approved', 'cured', 'revoked') THEN
            actor_id := NEW.approved_by;
            actor_role := NEW.approver_role;
            delegation_id := NEW.legal_approval_delegation_id;
            delegation_scope := 'waiver_approval';
            authority_at := NEW.approved_at;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'counsel_opinions' THEN
        IF NEW.status = 'approved' THEN
            actor_id := NEW.approved_by;
            actor_role := NEW.approver_role;
            delegation_id := NEW.legal_approval_delegation_id;
            delegation_scope := 'counsel_opinion';
            authority_at := NEW.approved_at;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'legal_analysis_versions' THEN
        IF NEW.human_review_status IN ('approved', 'rejected') THEN
            actor_id := NEW.reviewed_by;
            actor_role := NEW.reviewer_role;
            delegation_id := NEW.legal_approval_delegation_id;
            delegation_scope := 'approval_gate';
            authority_at := NEW.reviewed_at;
        ELSE
            RETURN NEW;
        END IF;
    ELSE
        RETURN NEW;
    END IF;

    IF delegation_id IS NOT NULL THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS delegation '
            'WHERE delegation.legal_approval_delegation_id = $1 '
            'AND delegation.tenant_id = $2 '
            'AND delegation.project_id = $3 '
            'AND delegation.grantee_id = $4 '
            'AND delegation.grantee_role = $5 '
            'AND delegation.scope = $6 '
            'AND delegation.status = ''active'' '
            'AND delegation.created_at <= $7 '
            'AND delegation.valid_from <= $7 '
            'AND delegation.valid_until >= $7 '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS successor '
            'WHERE successor.tenant_id = delegation.tenant_id '
            'AND successor.project_id = delegation.project_id '
            'AND successor.delegation_key = delegation.delegation_key '
            'AND successor.delegation_version > '
            'delegation.delegation_version '
            'AND successor.created_at <= $7'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO delegation_valid
        USING
            delegation_id,
            NEW.tenant_id,
            NEW.project_id,
            actor_id,
            actor_role,
            delegation_scope,
            authority_at;
        IF NOT COALESCE(delegation_valid, FALSE) THEN
            RAISE EXCEPTION
                'governance row references invalid delegated authority'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_delegation';
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'legal_waivers' THEN
        IF NEW.cure_status <> 'not_started'
           AND NEW.cure_approval_delegation_id IS NOT NULL THEN
            EXECUTE pg_catalog.format(
                'SELECT EXISTS ('
                'SELECT 1 FROM %I.legal_approval_delegations AS delegation '
                'WHERE delegation.legal_approval_delegation_id = $1 '
                'AND delegation.tenant_id = $2 '
                'AND delegation.project_id = $3 '
                'AND delegation.grantee_id = $4 '
                'AND delegation.grantee_role = $5 '
                'AND delegation.scope = ''waiver_approval'' '
                'AND delegation.status = ''active'' '
                'AND delegation.created_at <= $6 '
                'AND delegation.valid_from <= $6 '
                'AND delegation.valid_until >= $6 '
                'AND NOT EXISTS ('
                'SELECT 1 FROM %I.legal_approval_delegations AS successor '
                'WHERE successor.tenant_id = delegation.tenant_id '
                'AND successor.project_id = delegation.project_id '
                'AND successor.delegation_key = delegation.delegation_key '
                'AND successor.delegation_version > '
                'delegation.delegation_version '
                'AND successor.created_at <= $6'
                ')'
                ')',
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA
            )
            INTO cure_delegation_valid
            USING
                NEW.cure_approval_delegation_id,
                NEW.tenant_id,
                NEW.project_id,
                NEW.cure_reviewed_by,
                NEW.cure_reviewer_role,
                NEW.cure_reviewed_at;
            IF NOT COALESCE(cure_delegation_valid, FALSE) THEN
                RAISE EXCEPTION
                    'waiver cure references invalid delegated authority'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_legal_governance_delegation';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_governance_delegation$;
REVOKE ALL ON FUNCTION legal_assert_governance_delegation()
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION legal_assert_governance_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_governance_snapshot$
DECLARE
    snapshot_id TEXT;
    expected_snapshot_sha256 TEXT;
    require_gate_eligible BOOLEAN := FALSE;
    snapshot_valid BOOLEAN;
    cure_snapshot_valid BOOLEAN;
    cure_snapshot_creator TEXT;
    waiver_condition_outcomes_valid BOOLEAN;
    predecessor_cure_actor TEXT;
BEGIN
    IF TG_TABLE_NAME = 'legal_waivers' THEN
        SELECT
            NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(NEW.conditions_json)
                    AS condition_entry
                WHERE jsonb_typeof(condition_entry) <> 'object'
                   OR NULLIF(
                        btrim(condition_entry ->> 'code'),
                        ''
                    ) IS NULL
                   OR NULLIF(
                        btrim(condition_entry ->> 'description'),
                        ''
                    ) IS NULL
            )
            AND jsonb_array_length(NEW.conditions_json) = (
                SELECT count(DISTINCT condition_entry ->> 'code')
                FROM jsonb_array_elements(NEW.conditions_json)
                    AS condition_entry
            )
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    NEW.cure_condition_outcomes_json
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
            )
            AND jsonb_array_length(
                NEW.cure_condition_outcomes_json
            ) = (
                SELECT count(DISTINCT outcome_entry ->> 'code')
                FROM jsonb_array_elements(
                    NEW.cure_condition_outcomes_json
                ) AS outcome_entry
            )
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    NEW.cure_condition_outcomes_json
                ) AS outcome_entry
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(NEW.conditions_json)
                        AS condition_entry
                    WHERE condition_entry ->> 'code' =
                        outcome_entry ->> 'code'
                )
            )
            AND (
                NEW.cure_status NOT IN ('verified', 'failed')
                OR (
                    NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(NEW.conditions_json)
                            AS condition_entry
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.cure_condition_outcomes_json
                            ) AS outcome_entry
                            WHERE outcome_entry ->> 'code' =
                                condition_entry ->> 'code'
                        )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            NEW.cure_condition_outcomes_json
                        ) AS outcome_entry
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.conditions_json
                            ) AS condition_entry
                            WHERE condition_entry ->> 'code' =
                                outcome_entry ->> 'code'
                        )
                    )
                    AND (
                        NEW.cure_status <> 'verified'
                        OR NOT EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.cure_condition_outcomes_json
                            ) AS outcome_entry
                            WHERE outcome_entry ->> 'status' <>
                                'satisfied'
                        )
                    )
                    AND (
                        NEW.cure_status <> 'failed'
                        OR EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.cure_condition_outcomes_json
                            ) AS outcome_entry
                            WHERE outcome_entry ->> 'status' = 'failed'
                        )
                    )
                )
            )
        INTO waiver_condition_outcomes_valid;

        IF NOT COALESCE(waiver_condition_outcomes_valid, FALSE) THEN
            RAISE EXCEPTION
                'waiver cure outcomes do not match waiver conditions'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_waiver_condition_outcomes';
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'project_legal_matters' THEN
        IF NEW.status IN (
               'resolved',
               'waived',
               'closed',
               'superseded'
           ) THEN
            snapshot_id := NEW.legal_evidence_snapshot_id;
            require_gate_eligible := TRUE;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'legal_waivers' THEN
        IF NEW.status IN ('approved', 'cured', 'revoked') THEN
            snapshot_id := NEW.legal_evidence_snapshot_id;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'counsel_opinions' THEN
        IF NEW.status = 'approved' THEN
            snapshot_id := NEW.legal_evidence_snapshot_id;
            require_gate_eligible := TRUE;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'legal_analysis_versions' THEN
        IF NEW.report_eligible THEN
            snapshot_id := NEW.legal_evidence_snapshot_id;
            expected_snapshot_sha256 := NEW.input_snapshot_sha256;
            require_gate_eligible := TRUE;
        ELSE
            RETURN NEW;
        END IF;
    ELSE
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshots AS snapshot '
        'WHERE snapshot.legal_evidence_snapshot_id = $1 '
        'AND snapshot.tenant_id = $2 '
        'AND snapshot.project_id = $3 '
        'AND snapshot.citation_verification_status = ''verified'' '
        'AND NOT snapshot.is_stale '
        'AND ($5 IS NULL OR snapshot.snapshot_sha256 = $5) '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshots AS successor '
        'WHERE successor.tenant_id = snapshot.tenant_id '
        'AND successor.project_id = snapshot.project_id '
        'AND (successor.supersedes_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'OR successor.snapshot_version > snapshot.snapshot_version)'
        ') '
        'AND EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshot_items AS item '
        'WHERE item.legal_evidence_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'AND item.tenant_id = snapshot.tenant_id '
        'AND item.project_id = snapshot.project_id '
        'AND item.citation_verification_status = ''verified'' '
        'AND item.verified_at IS NOT NULL '
        'AND item.verified_at <= statement_timestamp()'
        ') '
        'AND NOT EXISTS ('
        'SELECT 1 '
        'FROM %I.legal_evidence_snapshot_items AS lineage_item '
        'LEFT JOIN %I.legal_resource_versions AS lineage_version '
        'ON lineage_version.legal_resource_version_id = '
        'lineage_item.legal_resource_version_id '
        'AND lineage_version.legal_resource_id = '
        'lineage_item.legal_resource_id '
        'LEFT JOIN %I.legal_source_fetches AS lineage_fetch '
        'ON lineage_fetch.source_fetch_id = lineage_item.source_fetch_id '
        'AND lineage_fetch.provider_id = lineage_version.provider_id '
        'LEFT JOIN %I.legal_source_providers AS lineage_provider '
        'ON lineage_provider.provider_id = lineage_version.provider_id '
        'LEFT JOIN %I.legal_provisions AS lineage_provision '
        'ON lineage_provision.legal_provision_id = '
        'lineage_item.legal_provision_id '
        'AND lineage_provision.legal_resource_version_id = '
        'lineage_item.legal_resource_version_id '
        'AND lineage_provision.legal_resource_id = '
        'lineage_item.legal_resource_id '
        'WHERE lineage_item.legal_evidence_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'AND lineage_item.tenant_id = snapshot.tenant_id '
        'AND lineage_item.project_id = snapshot.project_id '
        'AND ('
        'lineage_item.citation_verification_status <> ''verified'' '
        'OR lineage_item.verified_at IS NULL '
        'OR lineage_item.verified_at > statement_timestamp() '
        'OR snapshot.freshness_policy_version <> '
        '''legal-freshness-v1'' '
        'OR lineage_item.verified_at + INTERVAL ''7 days'' < '
        'statement_timestamp() '
        'OR lineage_version.legal_resource_version_id IS NULL '
        'OR lineage_version.verification_status NOT IN '
        '(''source_verified'', ''schema_verified'') '
        'OR lineage_version.verified_at IS NULL '
        'OR lineage_version.verified_at > statement_timestamp() '
        'OR lineage_version.source_fetch_id IS DISTINCT FROM '
        'lineage_item.source_fetch_id '
        'OR lineage_fetch.source_fetch_id IS NULL '
        'OR lineage_fetch.fetch_status NOT IN '
        '(''succeeded'', ''not_modified'') '
        'OR lineage_provider.provider_id IS NULL '
        'OR lineage_provider.provider_status <> ''active'' '
        'OR lineage_provision.legal_provision_id IS NULL '
        'OR btrim(lineage_item.exact_locator) = '''' '
        'OR lineage_provision.source_locator IS DISTINCT FROM '
        'lineage_item.exact_locator '
        'OR (lineage_version.effective_from IS NOT NULL '
        'AND lineage_version.effective_from > snapshot.as_of_date) '
        'OR (lineage_version.effective_to IS NOT NULL '
        'AND lineage_version.effective_to < snapshot.as_of_date) '
        'OR (lineage_version.valid_time_start IS NOT NULL '
        'AND lineage_version.valid_time_start > snapshot.as_of_date) '
        'OR (lineage_version.valid_time_end IS NOT NULL '
        'AND lineage_version.valid_time_end < snapshot.as_of_date) '
        'OR EXISTS ('
        'SELECT 1 '
        'FROM %I.legal_resource_versions AS replacement '
        'JOIN %I.legal_source_fetches AS replacement_fetch '
        'ON replacement_fetch.source_fetch_id = '
        'replacement.source_fetch_id '
        'AND replacement_fetch.provider_id = replacement.provider_id '
        'JOIN %I.legal_source_providers AS replacement_provider '
        'ON replacement_provider.provider_id = replacement.provider_id '
        'WHERE replacement.legal_resource_id = '
        'lineage_item.legal_resource_id '
        'AND replacement.version_sequence > '
        'lineage_version.version_sequence '
        'AND replacement.verification_status IN '
        '(''source_verified'', ''schema_verified'') '
        'AND replacement.verified_at IS NOT NULL '
        'AND replacement.verified_at <= statement_timestamp() '
        'AND replacement_fetch.fetch_status IN '
        '(''succeeded'', ''not_modified'') '
        'AND replacement_provider.provider_status = ''active'' '
        'AND (replacement.supersedes_version_id = '
        'lineage_version.legal_resource_version_id '
        'OR ('
        '(replacement.effective_from IS NULL '
        'OR replacement.effective_from <= snapshot.as_of_date) '
        'AND (replacement.effective_to IS NULL '
        'OR replacement.effective_to >= snapshot.as_of_date) '
        'AND (replacement.valid_time_start IS NULL '
        'OR replacement.valid_time_start <= snapshot.as_of_date) '
        'AND (replacement.valid_time_end IS NULL '
        'OR replacement.valid_time_end >= snapshot.as_of_date)'
        ')'
        ')'
        ')'
        ')'
        ') '
        'AND (NOT $4 OR ('
        'snapshot.gate_eligible '
        'AND NOT snapshot.has_open_conflicts '
        'AND EXISTS ('
        'SELECT 1 FROM %I.legal_required_claim_sets AS claim_set '
        'WHERE claim_set.legal_required_claim_set_id = '
        'snapshot.legal_required_claim_set_id '
        'AND claim_set.tenant_id = snapshot.tenant_id '
        'AND claim_set.project_id = snapshot.project_id '
        'AND claim_set.status = ''approved'' '
        'AND claim_set.review_scope = ''approval_gate'' '
        'AND claim_set.claim_set_sha256 = '
        'snapshot.required_claim_set_sha256 '
        'AND claim_set.legal_required_claim_set_id = ('
        'SELECT latest.legal_required_claim_set_id '
        'FROM %I.legal_required_claim_sets AS latest '
        'WHERE latest.tenant_id = snapshot.tenant_id '
        'AND latest.project_id = snapshot.project_id '
        'AND latest.review_scope = ''approval_gate'' '
        'AND latest.status = ''approved'' '
        'ORDER BY latest.approved_at DESC, '
        'latest.claim_set_version DESC, '
        'latest.legal_required_claim_set_id DESC LIMIT 1'
        ') '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_required_claims AS required_claim '
        'WHERE required_claim.legal_required_claim_set_id = '
        'claim_set.legal_required_claim_set_id '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshot_items AS covered '
        'WHERE covered.legal_evidence_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'AND covered.tenant_id = snapshot.tenant_id '
        'AND covered.project_id = snapshot.project_id '
        'AND covered.claim_text_sha256 = '
        'required_claim.claim_text_sha256 '
        'AND covered.citation_verification_status = ''verified'' '
        'AND covered.verified_at IS NOT NULL'
        ')'
        ')'
        ')'
        '))'
        ')',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO snapshot_valid
    USING
        snapshot_id,
        NEW.tenant_id,
        NEW.project_id,
        require_gate_eligible,
        expected_snapshot_sha256;

    IF NOT COALESCE(snapshot_valid, FALSE) THEN
        RAISE EXCEPTION
            'governance approval references stale or ineligible evidence'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_governance_snapshot';
    END IF;

    IF TG_TABLE_NAME = 'legal_waivers' THEN
        IF NEW.cure_status IN ('verified', 'failed') THEN
            EXECUTE pg_catalog.format(
                'SELECT EXISTS ('
                'SELECT 1 FROM %I.legal_evidence_snapshots AS snapshot '
                'WHERE snapshot.legal_evidence_snapshot_id = $1 '
                'AND snapshot.tenant_id = $2 '
                'AND snapshot.project_id = $3 '
                'AND snapshot.gate_eligible '
                'AND snapshot.citation_verification_status = ''verified'' '
                'AND NOT snapshot.has_open_conflicts '
                'AND NOT snapshot.is_stale '
                'AND NOT EXISTS ('
                'SELECT 1 FROM %I.legal_evidence_snapshots AS successor '
                'WHERE successor.tenant_id = snapshot.tenant_id '
                'AND successor.project_id = snapshot.project_id '
                'AND (successor.supersedes_snapshot_id = '
                'snapshot.legal_evidence_snapshot_id '
                'OR successor.snapshot_version > snapshot.snapshot_version)'
                ') '
                'AND EXISTS ('
                'SELECT 1 '
                'FROM %I.legal_evidence_snapshot_items AS item '
                'WHERE item.legal_evidence_snapshot_id = '
                'snapshot.legal_evidence_snapshot_id '
                'AND item.tenant_id = snapshot.tenant_id '
                'AND item.project_id = snapshot.project_id '
                'AND item.citation_verification_status = ''verified'' '
                'AND item.verified_at IS NOT NULL '
                'AND item.verified_at <= statement_timestamp()'
                ') '
                'AND NOT EXISTS ('
                'SELECT 1 '
                'FROM %I.legal_evidence_snapshot_items AS lineage_item '
                'LEFT JOIN %I.legal_resource_versions AS lineage_version '
                'ON lineage_version.legal_resource_version_id = '
                'lineage_item.legal_resource_version_id '
                'AND lineage_version.legal_resource_id = '
                'lineage_item.legal_resource_id '
                'LEFT JOIN %I.legal_source_fetches AS lineage_fetch '
                'ON lineage_fetch.source_fetch_id = '
                'lineage_item.source_fetch_id '
                'AND lineage_fetch.provider_id = '
                'lineage_version.provider_id '
                'LEFT JOIN %I.legal_source_providers AS lineage_provider '
                'ON lineage_provider.provider_id = '
                'lineage_version.provider_id '
                'LEFT JOIN %I.legal_provisions AS lineage_provision '
                'ON lineage_provision.legal_provision_id = '
                'lineage_item.legal_provision_id '
                'AND lineage_provision.legal_resource_version_id = '
                'lineage_item.legal_resource_version_id '
                'AND lineage_provision.legal_resource_id = '
                'lineage_item.legal_resource_id '
                'WHERE lineage_item.legal_evidence_snapshot_id = '
                'snapshot.legal_evidence_snapshot_id '
                'AND lineage_item.tenant_id = snapshot.tenant_id '
                'AND lineage_item.project_id = snapshot.project_id '
                'AND ('
                'lineage_item.citation_verification_status <> ''verified'' '
                'OR lineage_item.verified_at IS NULL '
                'OR lineage_item.verified_at > statement_timestamp() '
                'OR snapshot.freshness_policy_version <> '
                '''legal-freshness-v1'' '
                'OR lineage_item.verified_at + INTERVAL ''7 days'' < '
                'statement_timestamp() '
                'OR lineage_version.legal_resource_version_id IS NULL '
                'OR lineage_version.verification_status NOT IN '
                '(''source_verified'', ''schema_verified'') '
                'OR lineage_version.verified_at IS NULL '
                'OR lineage_version.verified_at > statement_timestamp() '
                'OR lineage_version.source_fetch_id IS DISTINCT FROM '
                'lineage_item.source_fetch_id '
                'OR lineage_fetch.source_fetch_id IS NULL '
                'OR lineage_fetch.fetch_status NOT IN '
                '(''succeeded'', ''not_modified'') '
                'OR lineage_provider.provider_id IS NULL '
                'OR lineage_provider.provider_status <> ''active'' '
                'OR lineage_provision.legal_provision_id IS NULL '
                'OR btrim(lineage_item.exact_locator) = '''' '
                'OR lineage_provision.source_locator IS DISTINCT FROM '
                'lineage_item.exact_locator '
                'OR (lineage_version.effective_from IS NOT NULL '
                'AND lineage_version.effective_from > snapshot.as_of_date) '
                'OR (lineage_version.effective_to IS NOT NULL '
                'AND lineage_version.effective_to < snapshot.as_of_date) '
                'OR (lineage_version.valid_time_start IS NOT NULL '
                'AND lineage_version.valid_time_start > snapshot.as_of_date) '
                'OR (lineage_version.valid_time_end IS NOT NULL '
                'AND lineage_version.valid_time_end < snapshot.as_of_date) '
                'OR EXISTS ('
                'SELECT 1 '
                'FROM %I.legal_resource_versions AS replacement '
                'JOIN %I.legal_source_fetches AS replacement_fetch '
                'ON replacement_fetch.source_fetch_id = '
                'replacement.source_fetch_id '
                'AND replacement_fetch.provider_id = replacement.provider_id '
                'JOIN %I.legal_source_providers AS replacement_provider '
                'ON replacement_provider.provider_id = '
                'replacement.provider_id '
                'WHERE replacement.legal_resource_id = '
                'lineage_item.legal_resource_id '
                'AND replacement.version_sequence > '
                'lineage_version.version_sequence '
                'AND replacement.verification_status IN '
                '(''source_verified'', ''schema_verified'') '
                'AND replacement.verified_at IS NOT NULL '
                'AND replacement.verified_at <= statement_timestamp() '
                'AND replacement_fetch.fetch_status IN '
                '(''succeeded'', ''not_modified'') '
                'AND replacement_provider.provider_status = ''active'' '
                'AND (replacement.supersedes_version_id = '
                'lineage_version.legal_resource_version_id '
                'OR ('
                '(replacement.effective_from IS NULL '
                'OR replacement.effective_from <= snapshot.as_of_date) '
                'AND (replacement.effective_to IS NULL '
                'OR replacement.effective_to >= snapshot.as_of_date) '
                'AND (replacement.valid_time_start IS NULL '
                'OR replacement.valid_time_start <= snapshot.as_of_date) '
                'AND (replacement.valid_time_end IS NULL '
                'OR replacement.valid_time_end >= snapshot.as_of_date)'
                ')'
                ')'
                ')'
                ')'
                ')'
                ')',
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA,
                TG_TABLE_SCHEMA
            )
            INTO cure_snapshot_valid
            USING
                NEW.cure_evidence_snapshot_id,
                NEW.tenant_id,
                NEW.project_id;
            IF NOT COALESCE(cure_snapshot_valid, FALSE) THEN
                RAISE EXCEPTION
                    'terminal waiver cure requires current verified evidence'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_legal_governance_snapshot';
            END IF;

            EXECUTE pg_catalog.format(
                'SELECT snapshot.created_by '
                'FROM %I.legal_evidence_snapshots AS snapshot '
                'WHERE snapshot.legal_evidence_snapshot_id = $1 '
                'AND snapshot.tenant_id = $2 '
                'AND snapshot.project_id = $3',
                TG_TABLE_SCHEMA
            )
            INTO cure_snapshot_creator
            USING
                NEW.cure_evidence_snapshot_id,
                NEW.tenant_id,
                NEW.project_id;
            IF cure_snapshot_creator IS NOT NULL
               AND lower(btrim(cure_snapshot_creator)) =
                   lower(btrim(NEW.cure_reviewed_by)) THEN
                RAISE EXCEPTION
                    'waiver cure evidence maker cannot verify its own snapshot'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_legal_governance_snapshot';
            END IF;

            EXECUTE pg_catalog.format(
                'SELECT predecessor.cure_reviewed_by '
                'FROM %I.legal_waivers AS predecessor '
                'WHERE predecessor.legal_waiver_id = $1 '
                'AND predecessor.tenant_id = $2 '
                'AND predecessor.project_id = $3',
                TG_TABLE_SCHEMA
            )
            INTO predecessor_cure_actor
            USING
                NEW.supersedes_waiver_id,
                NEW.tenant_id,
                NEW.project_id;
            IF predecessor_cure_actor IS NOT NULL
               AND lower(btrim(predecessor_cure_actor)) =
                   lower(btrim(NEW.cure_reviewed_by)) THEN
                RAISE EXCEPTION
                    'waiver cure maker cannot verify its own transition'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_legal_governance_snapshot';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_governance_snapshot$;
REVOKE ALL ON FUNCTION legal_assert_governance_snapshot()
    FROM PUBLIC;

-- Dynamic EXECUTE requires explicit row-count checks for projection lookup.

CREATE OR REPLACE FUNCTION legal_assert_review_projection_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_review_projection_snapshot$
DECLARE
    matched_rows BIGINT;
    referenced_snapshot RECORD;
    predecessor RECORD;
BEGIN
    IF NEW.review_version > 1 THEN
        EXECUTE pg_catalog.format(
            'SELECT tenant_id, project_id, review_id, review_version '
            'FROM %I.%I WHERE review_version_id = $1',
            TG_TABLE_SCHEMA,
            TG_TABLE_NAME
        )
        INTO predecessor
        USING NEW.supersedes_review_version_id;
        GET DIAGNOSTICS matched_rows = ROW_COUNT;
        IF matched_rows <> 1
           OR predecessor.tenant_id IS DISTINCT FROM NEW.tenant_id
           OR predecessor.project_id IS DISTINCT FROM NEW.project_id
           OR predecessor.review_id IS DISTINCT FROM NEW.review_id
           OR predecessor.review_version <> NEW.review_version - 1
        THEN
            RAISE EXCEPTION
                'review projection predecessor is outside the same tenant/project/review chain';
        END IF;
    END IF;

    IF NEW.legal_evidence_snapshot_id IS NULL THEN
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT snapshot.snapshot_version, snapshot.snapshot_sha256, '
        'snapshot.gate_eligible, '
        'snapshot.legal_required_claim_set_id, '
        'snapshot.required_claim_set_sha256, '
        'snapshot.legal_review_id, '
        'snapshot.citation_verification_status, '
        'snapshot.has_open_conflicts, snapshot.is_stale, '
        'EXISTS ('
        '  SELECT 1 FROM %I.legal_evidence_snapshots AS successor '
        '  WHERE successor.tenant_id = snapshot.tenant_id '
        '    AND successor.project_id = snapshot.project_id '
        '    AND ('
        '      successor.supersedes_snapshot_id = '
        '        snapshot.legal_evidence_snapshot_id '
        '      OR successor.snapshot_version > snapshot.snapshot_version'
        '    )'
        ') AS is_superseded '
        'FROM %I.legal_evidence_snapshots AS snapshot '
        'WHERE snapshot.legal_evidence_snapshot_id = $1 '
        '  AND snapshot.tenant_id = $2 '
        '  AND snapshot.project_id = $3 '
        'FOR SHARE OF snapshot',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO referenced_snapshot
    USING
        NEW.legal_evidence_snapshot_id,
        NEW.tenant_id,
        NEW.project_id;
    GET DIAGNOSTICS matched_rows = ROW_COUNT;
    IF matched_rows <> 1 THEN
        RAISE EXCEPTION
            'review projection references a snapshot outside its tenant/project scope';
    END IF;
    IF TG_TABLE_NAME = 'project_legal_review_versions'
       AND referenced_snapshot.legal_review_id IS DISTINCT FROM NEW.review_id
    THEN
        RAISE EXCEPTION
            'legal review projection review_id does not match the evidence snapshot';
    END IF;
    IF referenced_snapshot.snapshot_version IS DISTINCT FROM NEW.snapshot_version
       OR referenced_snapshot.snapshot_sha256 IS DISTINCT FROM
              NEW.snapshot_sha256
       OR referenced_snapshot.gate_eligible IS DISTINCT FROM
              NEW.snapshot_gate_eligible
       OR referenced_snapshot.legal_required_claim_set_id IS DISTINCT FROM
              NEW.required_claim_set_id
       OR referenced_snapshot.required_claim_set_sha256 IS DISTINCT FROM
              NEW.required_claim_set_sha256
    THEN
        RAISE EXCEPTION
            'review projection snapshot identity, policy, hash, or gate state changed';
    END IF;
    IF NEW.snapshot_gate_eligible AND (
        referenced_snapshot.citation_verification_status <> 'verified'
        OR referenced_snapshot.has_open_conflicts
        OR referenced_snapshot.is_stale
        OR referenced_snapshot.is_superseded
    ) THEN
        RAISE EXCEPTION
            'review projection references an ineligible or superseded snapshot';
    END IF;
    RETURN NEW;
END;
$legal_review_projection_snapshot$;
REVOKE ALL ON FUNCTION legal_assert_review_projection_snapshot()
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION
    legal_assert_review_decision_projection_binding()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_review_decision_projection$
DECLARE
    matched_rows BIGINT;
    latest_projection_id TEXT;
    latest_projection_version INTEGER;
    latest_projection_hash_contract TEXT;
    latest_projection_sha256 TEXT;
    recomputed_projection_sha256 TEXT;
    latest_snapshot_id TEXT;
    latest_projection_created_at TIMESTAMPTZ;
    latest_snapshot_created_at TIMESTAMPTZ;
BEGIN
    IF (
        NEW.decision_scope <> 'approval_gate'
        OR NEW.decision_type NOT IN (
            'approve',
            'reject',
            'waive_with_conditions'
        )
    ) THEN
        RETURN NEW;
    END IF;

    IF (
        NULLIF(btrim(NEW.legal_review_id), '') IS NULL
        OR NULLIF(btrim(NEW.legal_review_version_id), '') IS NULL
        OR NEW.legal_review_version IS NULL
        OR NEW.legal_review_projection_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION
            'final legal decision % requires normalized projection binding',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_binding';
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT projection.review_version_id, '
        'projection.review_version, '
        'projection.projection_hash_contract, '
        'projection.projection_sha256, '
        'CASE WHEN projection.projection_hash_contract = '
        '''pg-jsonb-row-v1'' THEN encode('
        'extensions.digest(convert_to(('
        'to_jsonb(projection) - ''projection_sha256'' '
        '- ''projection_hash_contract'')::TEXT, ''UTF8''), '
        '''sha256''), ''hex'') ELSE NULL END, '
        'projection.legal_evidence_snapshot_id, projection.created_at, '
        'snapshot.created_at '
        'FROM %I.project_legal_review_versions AS projection '
        'LEFT JOIN %I.legal_evidence_snapshots AS snapshot '
        'ON snapshot.legal_evidence_snapshot_id = '
        'projection.legal_evidence_snapshot_id '
        'AND snapshot.tenant_id = projection.tenant_id '
        'AND snapshot.project_id = projection.project_id '
        'WHERE projection.tenant_id = $1 '
        'AND projection.project_id = $2 '
        'AND projection.review_id = $3 '
        'ORDER BY projection.review_version DESC, '
        'projection.created_at DESC, projection.review_version_id DESC '
        'LIMIT 1 FOR SHARE OF projection',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO
        latest_projection_id,
        latest_projection_version,
        latest_projection_hash_contract,
        latest_projection_sha256,
        recomputed_projection_sha256,
        latest_snapshot_id,
        latest_projection_created_at,
        latest_snapshot_created_at
    USING NEW.tenant_id, NEW.project_id, NEW.legal_review_id;

    GET DIAGNOSTICS matched_rows = ROW_COUNT;
    IF matched_rows <> 1 THEN
        RAISE EXCEPTION
            'final legal decision % has no normalized legal review projection',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'fk_legal_review_decision_projection_scope';
    END IF;

    IF (
        NEW.legal_review_version_id IS DISTINCT FROM latest_projection_id
        OR NEW.legal_review_version IS DISTINCT FROM
            latest_projection_version
        OR latest_projection_hash_contract IS DISTINCT FROM
            'pg-jsonb-row-v1'
        OR NEW.legal_review_projection_sha256 IS DISTINCT FROM
            latest_projection_sha256
        OR latest_projection_sha256 IS DISTINCT FROM
            recomputed_projection_sha256
    ) THEN
        RAISE EXCEPTION
            'legal review projection changed before decision % was committed',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_current';
    END IF;

    IF NEW.legal_evidence_snapshot_id IS DISTINCT FROM latest_snapshot_id THEN
        RAISE EXCEPTION
            'legal decision % projection and evidence snapshot differ',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_snapshot';
    END IF;

    IF (
        latest_projection_created_at IS NULL
        OR NEW.decided_at < latest_projection_created_at
    ) THEN
        RAISE EXCEPTION
            'legal decision % predates its normalized review projection',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_time';
    END IF;

    IF (
        latest_snapshot_created_at IS NULL
        OR NEW.decided_at < latest_snapshot_created_at
    ) THEN
        RAISE EXCEPTION
            'legal decision % predates its evidence snapshot',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_snapshot_time';
    END IF;

    IF NEW.decided_at NOT BETWEEN
        statement_timestamp() - INTERVAL '5 minutes'
        AND statement_timestamp() + INTERVAL '5 minutes'
    THEN
        RAISE EXCEPTION
            'legal decision % timestamp is outside the database clock window',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_statement_time';
    END IF;

    RETURN NEW;
END;
$legal_review_decision_projection$;
REVOKE ALL ON FUNCTION
    legal_assert_review_decision_projection_binding()
    FROM PUBLIC;
