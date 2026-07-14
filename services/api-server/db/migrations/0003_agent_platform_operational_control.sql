-- REALYTICS Agent Platform operational control plane.
-- No prompt text, provider credential, or source-document body may be stored here.

ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS consumed_cost_usd NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS consumed_llm_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS consumed_tool_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS retry_of_run_id TEXT;
CREATE INDEX IF NOT EXISTS idx_agent_runs_lease ON agent_runs(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS runtime_outbox_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    available_at TIMESTAMPTZ NOT NULL,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_outbox_claim
    ON runtime_outbox_events(status, available_at, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_runtime_outbox_aggregate
    ON runtime_outbox_events(aggregate_type, aggregate_id, created_at);

CREATE TABLE IF NOT EXISTS brain_provider_health (
    health_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    brain_profile_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    checked_at TIMESTAMPTZ NOT NULL,
    next_check_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL,
    UNIQUE(tenant_id, brain_profile_id)
);
CREATE INDEX IF NOT EXISTS idx_brain_provider_health_lookup
    ON brain_provider_health(tenant_id, status, checked_at);

CREATE TABLE IF NOT EXISTS brain_routing_policies (
    policy_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    version INTEGER NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    UNIQUE(tenant_id, user_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_routing_policy_scope_version
    ON brain_routing_policies(tenant_id, COALESCE(user_id, ''), version);
CREATE INDEX IF NOT EXISTS idx_brain_routing_policy_active
    ON brain_routing_policies(tenant_id, user_id, active, version);

CREATE TABLE IF NOT EXISTS brain_routing_events (
    routing_event_id TEXT PRIMARY KEY,
    run_id TEXT,
    request_id TEXT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    project_id TEXT,
    task_type TEXT NOT NULL,
    policy_id TEXT,
    policy_version INTEGER,
    selected_profile_id TEXT,
    selected_provider TEXT,
    selected_model TEXT,
    blocked BOOLEAN NOT NULL DEFAULT FALSE,
    occurred_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brain_routing_events_run
    ON brain_routing_events(run_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_brain_routing_events_tenant
    ON brain_routing_events(tenant_id, occurred_at);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    project_id TEXT,
    tool_id TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    status TEXT NOT NULL,
    side_effect_level TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 1,
    requested_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL,
    UNIQUE(tenant_id, tool_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id, requested_at);

CREATE TABLE IF NOT EXISTS learning_candidates (
    candidate_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    candidate_type TEXT NOT NULL,
    target_key TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_candidates_review
    ON learning_candidates(tenant_id, status, updated_at);

CREATE TABLE IF NOT EXISTS learning_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_evaluations_candidate
    ON learning_evaluations(candidate_id, evaluated_at);

CREATE TABLE IF NOT EXISTS learning_promotions (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    action TEXT NOT NULL,
    environment TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_promotions_candidate
    ON learning_promotions(candidate_id, approved_at);

CREATE TABLE IF NOT EXISTS coding_pod_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    workspace_ref TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    state_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    UNIQUE(tenant_id, idempotency_key)
);
ALTER TABLE coding_pod_jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE coding_pod_jobs ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE coding_pod_jobs ADD COLUMN IF NOT EXISTS state_version INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_coding_pod_jobs_claim
    ON coding_pod_jobs(status, lease_expires_at, created_at);
