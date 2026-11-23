"""Bind domain control-plane identity to the active request or worker tenant."""

from db.repositories.request_project_repository import current_project_scope


def domain_tenant_id(options, safe_context) -> str:
    scope = current_project_scope()
    claims = {
        str(value).strip() for value in (
            safe_context.get("tenant_id"), safe_context.get("tenantId"),
            options.get("tenantId"), options.get("tenant_id"),
            scope.tenant_id if scope is not None else None,
        ) if value is not None and str(value).strip()
    }
    if len(claims) > 1:
        raise ValueError("DOMAIN_AGENT_TENANT_SCOPE_MISMATCH")
    return next(iter(claims), "default")
