-- First-class audit trail for consensus assumptions and immutable project versions.

CREATE TABLE IF NOT EXISTS assumption_proposals (
    proposal_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    field_path TEXT NOT NULL,
    source_agent TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    reviewed_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assumption_proposals_review
    ON assumption_proposals(project_id, run_id, status, created_at);

CREATE TABLE IF NOT EXISTS project_versions (
    version_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    tenant_id TEXT,
    version INTEGER NOT NULL,
    base_version INTEGER,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    state_hash TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    UNIQUE(project_id, version)
);
CREATE INDEX IF NOT EXISTS idx_project_versions_project
    ON project_versions(project_id, version DESC);
