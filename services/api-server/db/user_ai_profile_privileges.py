"""Owner-approved profile CRUD and exact user/tenant RLS for a deployment role.

Role names are deployment-specific, so this is not a versioned schema migration.
Rendering never executes SQL; callers own the transaction and commit/rollback.
"""
from __future__ import annotations

import re
from typing import Any

from db.postgres_runtime_privileges import (
    USER_AI_PROFILE_FORBIDDEN_PRIVILEGES,
    USER_AI_PROFILE_RUNTIME_TABLE_PRIVILEGES,
    validate_database_identifier,
)

POLICY_NAME = "user_scope_user_ai_profiles_runtime"
PROFILE_SCOPE_SQL = (
    "tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '') "
    "AND user_id = NULLIF(current_setting('realytics.user_id', true), '')"
)


def _normalized_scope(value: str | None) -> str:
    return re.sub(r"[\s()]", "", str(value or "").lower().replace("::text", ""))


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_user_ai_profile_policy_sql(*, role_name: str, schema_name: str, create_missing: bool = True) -> str:
    """Install idempotently; a colliding policy fails without replacement."""
    role = validate_database_identifier(role_name, label="profile runtime role")
    schema = validate_database_identifier(schema_name, label="profile schema")
    relation = f'"{schema}"."user_ai_profiles"'
    create = (f'CREATE POLICY {POLICY_NAME} ON {relation} AS RESTRICTIVE FOR ALL TO "{role}" '
              f"USING ({PROFILE_SCOPE_SQL}) WITH CHECK ({PROFILE_SCOPE_SQL})")
    expected = _literal(_normalized_scope(PROFILE_SCOPE_SQL))
    missing_action = f"EXECUTE {_literal(create)};" if create_missing else "RAISE EXCEPTION 'User AI profile policy is missing';"
    return f"""DO $realytics_user_ai_profile$
DECLARE role_oid oid; table_oid oid;
BEGIN
  SELECT oid INTO role_oid FROM pg_roles WHERE rolname = {_literal(role)}
    AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolbypassrls OR rolreplication);
  IF role_oid IS NULL OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = role_oid) THEN
    RAISE EXCEPTION 'User AI profile role does not satisfy the runtime boundary';
  END IF;
  SELECT c.oid INTO table_oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = {_literal(schema)} AND c.relname = 'user_ai_profiles'
      AND c.relkind IN ('r', 'p') AND c.relrowsecurity AND c.relowner <> role_oid AND n.nspowner <> role_oid;
  IF table_oid IS NULL OR EXISTS (SELECT 1 FROM pg_database WHERE datname = current_database() AND datdba = role_oid) THEN
    RAISE EXCEPTION 'User AI profile table must have RLS and a separate owner';
  END IF;
  LOCK TABLE {relation} IN SHARE ROW EXCLUSIVE MODE;
  IF NOT EXISTS (SELECT 1 FROM pg_policy b WHERE b.polrelid = table_oid AND b.polpermissive AND b.polcmd = '*'
    AND (role_oid = ANY(b.polroles) OR 0::oid = ANY(b.polroles))) THEN
    RAISE EXCEPTION 'Existing permissive profile tenant policy is required';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = table_oid AND polname = {_literal(POLICY_NAME)}) THEN
    IF NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = table_oid AND p.polname = {_literal(POLICY_NAME)}
      AND NOT p.polpermissive AND p.polcmd = '*' AND p.polroles = ARRAY[role_oid]
      AND regexp_replace(replace(lower(pg_get_expr(p.polqual, p.polrelid)), '::text', ''), '[[:space:]()]', '', 'g') = {expected}
      AND regexp_replace(replace(lower(pg_get_expr(p.polwithcheck, p.polrelid)), '::text', ''), '[[:space:]()]', '', 'g') = {expected}) THEN
      RAISE EXCEPTION 'Existing user AI profile policy conflicts with the approved contract';
    END IF;
  ELSE
    {missing_action}
  END IF;
END;
$realytics_user_ai_profile$"""


def render_user_ai_profile_access_sql(*, role_name: str = "realytics_runtime", schema_name: str = "realytics") -> tuple[str, ...]:
    role = validate_database_identifier(role_name, label="profile runtime role")
    schema = validate_database_identifier(schema_name, label="profile schema")
    table = f'"{schema}"."user_ai_profiles"'
    privileges = ", ".join(USER_AI_PROFILE_RUNTIME_TABLE_PRIVILEGES["user_ai_profiles"])
    return (render_user_ai_profile_policy_sql(role_name=role, schema_name=schema),
            f'REVOKE ALL PRIVILEGES ON TABLE {table} FROM "{role}"',
            f'GRANT {privileges} ON TABLE {table} TO "{role}"')


def render_user_ai_profile_access_rollback_sql(*, role_name: str = "realytics_runtime", schema_name: str = "realytics") -> tuple[str, ...]:
    role = validate_database_identifier(role_name, label="profile runtime role")
    schema = validate_database_identifier(schema_name, label="profile schema")
    table = f'"{schema}"."user_ai_profiles"'
    return (render_user_ai_profile_policy_sql(role_name=role, schema_name=schema, create_missing=False),
            f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE {table} FROM "{role}"',
            f'DROP POLICY {POLICY_NAME} ON {table}')


def user_ai_profile_policy_issues(cursor: Any, *, role_name: str, schema_name: str) -> tuple[str, ...]:
    """Read-only catalog audit for the strict runtime startup preflight."""
    role = validate_database_identifier(role_name, label="profile runtime role")
    schema = validate_database_identifier(schema_name, label="profile schema")
    cursor.execute("""
        SELECT c.relrowsecurity AS user_ai_profile_policy_metadata, owner_role.rolname,
          EXISTS (SELECT 1 FROM pg_policy b WHERE b.polrelid = c.oid AND b.polpermissive AND b.polcmd = '*'
            AND (r.oid = ANY(b.polroles) OR 0::oid = ANY(b.polroles))),
          p.polpermissive, p.polcmd, p.polroles = ARRAY[r.oid],
          pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles owner_role ON owner_role.oid = c.relowner JOIN pg_roles r ON r.rolname = %s
        LEFT JOIN pg_policy p ON p.polrelid = c.oid AND p.polname = %s
        WHERE n.nspname = %s AND c.relname = 'user_ai_profiles' AND c.relkind IN ('r', 'p')
        """, (role, POLICY_NAME, schema))
    row = cursor.fetchone()
    if row is None:
        return ("user_ai_profiles.table_missing",)
    issues = []
    if not row[0]:
        issues.append("user_ai_profiles.rls_disabled")
    if row[1] == role:
        issues.append("user_ai_profiles.runtime_owned")
    if not row[2]:
        issues.append("user_ai_profiles.permissive_base_missing")
    if row[3] is not False or row[4] != "*" or row[5] is not True:
        issues.append("user_ai_profiles.restrictive_policy_invalid")
    if any(_normalized_scope(expr) != _normalized_scope(PROFILE_SCOPE_SQL) for expr in row[6:8]):
        issues.append("user_ai_profiles.user_tenant_scope_invalid")
    return tuple(issues)


def user_ai_profile_access_issues(cursor: Any, *, role_name: str, schema_name: str) -> tuple[str, ...]:
    """Audit exact effective table privileges plus the restrictive policy."""
    role = validate_database_identifier(role_name, label="profile runtime role")
    schema = validate_database_identifier(schema_name, label="profile schema")
    issues = list(user_ai_profile_policy_issues(cursor, role_name=role, schema_name=schema))
    if "user_ai_profiles.table_missing" in issues:
        return tuple(issues)
    relation = f'"{schema}"."user_ai_profiles"'
    for manifest, expected in ((USER_AI_PROFILE_RUNTIME_TABLE_PRIVILEGES, True), (USER_AI_PROFILE_FORBIDDEN_PRIVILEGES, False)):
        for privilege in manifest["user_ai_profiles"]:
            cursor.execute("SELECT has_table_privilege(%s, %s, %s)", (role, relation, privilege))
            row = cursor.fetchone()
            if not row or bool(row[0]) is not expected:
                issues.append(f"user_ai_profiles.{privilege}")
    return tuple(issues)
