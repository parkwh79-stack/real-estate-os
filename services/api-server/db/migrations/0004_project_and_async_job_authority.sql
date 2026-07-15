-- Promote projects and asynchronous PF jobs from the legacy app_state document
-- into tenant-aware, independently claimable operational records.

ALTER TABLE projects ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS created_by TEXT;
CREATE INDEX IF NOT EXISTS idx_projects_tenant_updated
    ON projects(tenant_id, updated_at DESC);

ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS kind TEXT;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS payload_json JSONB;
UPDATE async_jobs
SET payload_json = payload
WHERE payload_json IS NULL AND payload IS NOT NULL;
ALTER TABLE async_jobs ALTER COLUMN payload_json SET NOT NULL;
ALTER TABLE async_jobs ALTER COLUMN payload DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_async_jobs_claim
    ON async_jobs(status, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_async_jobs_tenant_project
    ON async_jobs(tenant_id, project_id, updated_at DESC);
