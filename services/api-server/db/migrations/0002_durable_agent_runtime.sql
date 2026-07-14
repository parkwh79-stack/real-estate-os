-- Durable Agent Runtime v1 upgrade for databases created before 2026-07-14.
-- CREATE IF NOT EXISTS keeps this compatible with fresh databases whose 0001
-- already contains the same canonical table definitions.

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id                    TEXT PRIMARY KEY,
    project_id                TEXT,
    tenant_id                 TEXT NOT NULL,
    user_id                   TEXT,
    request_id                TEXT NOT NULL,
    correlation_id            TEXT,
    trace_id                  TEXT,
    agent_id                  TEXT NOT NULL,
    agent_version             TEXT NOT NULL,
    task_type                 TEXT NOT NULL,
    idempotency_key           TEXT NOT NULL,
    input_hash                TEXT NOT NULL,
    status                    TEXT NOT NULL,
    attempt_count             INTEGER NOT NULL DEFAULT 0,
    max_attempts              INTEGER NOT NULL DEFAULT 1,
    cancellation_requested    BOOLEAN NOT NULL DEFAULT FALSE,
    state_version             INTEGER NOT NULL DEFAULT 1,
    selected_brain_profile_id TEXT,
    created_at                TIMESTAMPTZ NOT NULL,
    updated_at                TIMESTAMPTZ NOT NULL,
    started_at                TIMESTAMPTZ,
    completed_at              TIMESTAMPTZ,
    payload_json              JSONB NOT NULL,
    UNIQUE (tenant_id, agent_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_project_updated
    ON agent_runs(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated
    ON agent_runs(status, updated_at);

CREATE TABLE IF NOT EXISTS agent_run_steps (
    step_id       TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    project_id    TEXT,
    sequence_no   INTEGER NOT NULL,
    stage         TEXT NOT NULL,
    step_type     TEXT NOT NULL,
    status        TEXT NOT NULL,
    attempt_no    INTEGER,
    error_code    TEXT,
    started_at    TIMESTAMPTZ NOT NULL,
    completed_at  TIMESTAMPTZ,
    payload_json  JSONB NOT NULL,
    UNIQUE (run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run_sequence
    ON agent_run_steps(run_id, sequence_no);

CREATE TABLE IF NOT EXISTS brain_usage_events (
    event_id           TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    step_id            TEXT,
    tenant_id          TEXT NOT NULL,
    project_id         TEXT,
    agent_id           TEXT NOT NULL,
    agent_version      TEXT NOT NULL,
    brain_profile_id   TEXT,
    provider           TEXT,
    model              TEXT,
    status             TEXT NOT NULL,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    estimated_cost_usd NUMERIC,
    latency_ms         INTEGER,
    occurred_at        TIMESTAMPTZ NOT NULL,
    payload_json       JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brain_usage_events_run
    ON brain_usage_events(run_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_brain_usage_events_project
    ON brain_usage_events(project_id, occurred_at);
