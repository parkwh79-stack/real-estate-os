"""Review-only profile access choices. Never invoked by runtime provisioning.

Applying either choice requires a reviewed deployment change, including the
matching existing runtime preflight manifest. Rendering is not authorization.
"""
from db.postgres_runtime_privileges import validate_database_identifier


USER_AI_PROFILE_ACCESS_CHOICES = {
    "read_only": ("SELECT",),
    "edit": ("SELECT", "INSERT", "UPDATE", "DELETE"),
}


def render_user_ai_profile_access_review_sql(*, mode: str, role_name: str = "realytics_runtime", schema_name: str = "realytics") -> str:
    if mode not in USER_AI_PROFILE_ACCESS_CHOICES:
        raise ValueError("Choose read_only or edit explicitly")
    role = validate_database_identifier(role_name, label="review runtime role")
    schema = validate_database_identifier(schema_name, label="review schema")
    table = f'"{schema}"."user_ai_profiles"'
    scope = ("tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '') "
             "AND user_id = NULLIF(current_setting('realytics.user_id', true), '')")
    privileges = ", ".join(USER_AI_PROFILE_ACCESS_CHOICES[mode])
    return (
        "-- REVIEW ONLY: verify existing tenant RLS, no ownership/BYPASSRLS, and no prior profile grants.\n"
        "-- No anon/authenticated/identity role, other table, schema CREATE, or Data API change.\n"
        f'CREATE POLICY user_scope_user_ai_profiles_runtime ON {table} AS RESTRICTIVE FOR ALL TO "{role}" '
        f"USING ({scope}) WITH CHECK ({scope});\n"
        f'GRANT {privileges} ON TABLE {table} TO "{role}";\n'
    )
