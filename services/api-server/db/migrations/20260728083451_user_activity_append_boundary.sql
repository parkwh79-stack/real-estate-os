-- Accelerate tenant/user/project recent-work reads over append-only activity.
SET search_path TO realytics, extensions, pg_catalog;

CREATE INDEX IF NOT EXISTS idx_user_activity_logs_tenant_user_project_success
    ON user_activity_logs (tenant_id, user_id, project_id, created_at DESC)
    WHERE event_type = 'api_request'
      AND status_code >= 200
      AND status_code < 400;

COMMENT ON INDEX idx_user_activity_logs_tenant_user_project_success IS
    'Recent successful project activity by normalized tenant and resolved user identity.';
