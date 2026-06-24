from __future__ import annotations

from fastapi import APIRouter, Query, Request

from core.authz import authorize_request
from storage import list_user_activity_logs
from use_cases.usage_insights import build_usage_insights, render_optimization_log_markdown


router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/infra-status")
def get_infra_status():
    """Production-infra readiness: PostgreSQL, OIDC, and object storage backends."""
    import os

    from core.oidc import oidc_status
    from db.postgres import status as postgres_status

    return {
        "schemaVersion": "realytics-infra-status-v1",
        "postgres": postgres_status(),
        "oidc": oidc_status(),
        "objectStorage": {"backend": (os.environ.get("REALYTICS_OBJECT_STORAGE") or "local").strip().lower()},
    }


@router.get("/credit-policy-profiles")
def list_credit_policy_profiles_endpoint():
    """List the built-in lender credit policy profiles used by Credit Review."""
    from schemas.credit_policy import list_credit_policy_profiles

    return {
        "schemaVersion": "realytics-credit-policy-list-v1",
        "profiles": [profile.model_dump() for profile in list_credit_policy_profiles()],
    }


@router.get("/usage-insights")
def get_usage_insights(
    request: Request,
    limit: int = Query(2000, ge=1, le=10000),
    project_id: str | None = Query(None),
):
    """Distil structured user-activity logs into optimization signals.

    Read-only and metadata-only (no PF payloads). Feeds the human-reviewed
    optimization loop documented in docs/product/recursive-optimization-loop.md.
    """
    authorize_request(request, "audit:read")
    logs = list_user_activity_logs(limit=limit, project_id=project_id)
    insights = build_usage_insights(logs)
    return insights


@router.get("/optimization-log/preview")
def preview_optimization_log(
    request: Request,
    generated_at: str = Query(..., description="ISO date stamp for the report section"),
    limit: int = Query(2000, ge=1, le=10000),
):
    """Return the Markdown a maintainer would append to docs/ops/optimization-log.md."""
    authorize_request(request, "audit:read")
    logs = list_user_activity_logs(limit=limit)
    insights = build_usage_insights(logs)
    return {
        "schemaVersion": "realytics-optimization-log-preview-v1",
        "markdown": render_optimization_log_markdown(insights, generated_at),
        "insights": insights,
    }
