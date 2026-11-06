"""Read-only, named-role inspection shared by API and isolated workers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def missing_function_privileges(cursor: Any, *, role: str, signatures: Sequence[str]) -> tuple[str, ...]:
    cursor.execute(
        """
        SELECT signature FROM unnest(%s::text[]) AS expected_function(signature)
         WHERE CASE WHEN to_regprocedure(signature) IS NULL THEN FALSE
               ELSE has_function_privilege(%s, to_regprocedure(signature), 'EXECUTE') END = FALSE
         ORDER BY signature
        """,
        (list(signatures), role),
    )
    return tuple(f"{row[0]}.EXECUTE" for row in cursor.fetchall())


def unlisted_schema_privileges(cursor: Any, *, role: str, schema: str, allowed_tables: Sequence[str]) -> tuple[str, ...]:
    """Reject access to private relations outside the worker allowlist.

    Catalog discovery covers newly added tables, views and column-only ACLs.
    All sequences are forbidden because PF execution does not allocate IDs.
    Effective checks include PUBLIC grants without changing those shared ACLs.
    """
    cursor.execute(
        """
        WITH unlisted_runtime_relations AS (
            SELECT c.oid, c.relname, c.relkind FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s
               AND (c.relkind = 'S' OR (c.relkind IN ('r', 'p', 'v', 'm', 'f')
                    AND c.relname <> ALL(%s::text[])))
        ), forbidden_relation_grants AS (
            SELECT r.relname, grant_name FROM unlisted_runtime_relations r
            CROSS JOIN LATERAL unnest(CASE WHEN r.relkind = 'S'
                THEN ARRAY['SELECT', 'USAGE', 'UPDATE']
                ELSE ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']
                    || CASE WHEN current_setting('server_version_num')::int >= 170000
                            THEN ARRAY['MAINTAIN'] ELSE ARRAY[]::text[] END
                END) grant_name
            WHERE CASE WHEN r.relkind = 'S' THEN has_sequence_privilege(%s, r.oid, grant_name)
                       ELSE has_table_privilege(%s, r.oid, grant_name) END
            UNION
            SELECT r.relname, 'column.' || grant_name FROM unlisted_runtime_relations r
            CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) grant_name
             WHERE CASE WHEN r.relkind = 'S' THEN FALSE
                        ELSE has_any_column_privilege(%s, r.oid, grant_name) END
        ) SELECT relname || '.' || grant_name FROM forbidden_relation_grants ORDER BY 1
        """,
        (schema, list(allowed_tables), role, role, role),
    )
    return tuple(str(row[0]) for row in cursor.fetchall())


def owned_runtime_objects(cursor: Any, *, role: str, schemas: Sequence[str]) -> tuple[str, ...]:
    cursor.execute(
        """
        SELECT owned_object FROM (
            SELECT 'database:' || datname AS owned_object
              FROM pg_database WHERE datdba = (SELECT oid FROM pg_roles WHERE rolname = %s)
               AND datname = current_database()
            UNION ALL
            SELECT 'schema:' || nspname FROM pg_namespace
             WHERE nspowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
               AND nspname = ANY(%s)
            UNION ALL
            SELECT 'relation:' || n.nspname || '.' || c.relname
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
               AND n.nspname = ANY(%s)
            UNION ALL
            SELECT 'function:' || n.nspname || '.' || p.proname
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE p.proowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
               AND n.nspname = ANY(%s)
        ) AS runtime_owned_objects ORDER BY owned_object
        """,
        (role, role, list(schemas), role, list(schemas), role, list(schemas)),
    )
    return tuple(str(row[0]) for row in cursor.fetchall())


def inspect_privilege_manifest(
    cursor: Any,
    *,
    role: str,
    schema: str,
    required_tables: Mapping[str, Sequence[str]],
    forbidden_tables: Mapping[str, Sequence[str]],
    required_columns: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    forbidden_columns: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Inspect effective grants in two bounded queries, including PUBLIC grants.

    Column privilege checks are independent: a forbidden table-level privilege
    can still be supplied by a column-only grant. For table contracts without
    column exceptions, inspect every live column for those forbidden operations.
    """
    entries = [
        {"relation_name": table, "privilege": privilege, "required": required}
        for required, manifest in ((True, required_tables), (False, forbidden_tables))
        for table, privileges in manifest.items()
        for privilege in privileges
    ]
    missing: list[str] = []
    forbidden: list[str] = []
    cursor.execute(
        """
        WITH runtime_table_contract AS (
            SELECT item.*, to_regclass(format('%%I.%%I', %s::text, item.relation_name)) AS relation_oid
              FROM jsonb_to_recordset(%s::jsonb)
                AS item(relation_name text, privilege text, required boolean)
        )
        SELECT relation_name || '.' || privilege, required
          FROM runtime_table_contract
         WHERE CASE WHEN relation_oid IS NULL THEN FALSE
               ELSE has_table_privilege(%s, relation_oid, privilege) END <> required
         ORDER BY relation_name, privilege
        """,
        (schema, json.dumps(entries), role),
    )
    for name, required in cursor.fetchall():
        (missing if required else forbidden).append(str(name))

    column_entries = [
        {"relation_name": table, "column_name": column, "privilege": privilege, "required": required}
        for required, manifest in ((True, required_columns or {}), (False, forbidden_columns or {}))
        for table, grants in manifest.items()
        for privilege, columns in grants.items()
        for column in columns
    ]
    # Expand table-wide forbidden column operations using pg_attribute so a
    # stale column ACL cannot bypass an INSERT/UPDATE/SELECT prohibition.
    all_column_entries = [
        {
            "relation_name": table, "privilege": privilege,
            "allowed_columns": list((required_columns or {}).get(table, {}).get(privilege, ())),
        }
        for table, privileges in forbidden_tables.items()
        for privilege in privileges
        if privilege in {"SELECT", "INSERT", "UPDATE", "REFERENCES"}
    ]
    cursor.execute(
        """
        WITH explicit_contract AS (
            SELECT * FROM jsonb_to_recordset(%s::jsonb)
                AS item(relation_name text, column_name text, privilege text, required boolean)
        ), table_column_contract AS (
            SELECT item.relation_name, a.attname AS column_name, item.privilege, FALSE AS required
              FROM jsonb_to_recordset(%s::jsonb) AS item(relation_name text, privilege text, allowed_columns text[])
              JOIN pg_attribute a ON a.attrelid = to_regclass(format('%%I.%%I', %s::text, item.relation_name))
             WHERE a.attnum > 0 AND NOT a.attisdropped
               AND a.attname <> ALL(item.allowed_columns)
        ), runtime_column_contract AS (
            SELECT c.*, to_regclass(format('%%I.%%I', %s::text, c.relation_name)) AS relation_oid
              FROM (SELECT * FROM explicit_contract UNION SELECT * FROM table_column_contract) c
        )
        SELECT relation_name || '.' || column_name || '.' || privilege, required
          FROM runtime_column_contract
         WHERE CASE WHEN relation_oid IS NULL THEN FALSE
               ELSE has_column_privilege(%s, relation_oid, column_name, privilege) END <> required
         ORDER BY relation_name, column_name, privilege
        """,
        (json.dumps(column_entries), json.dumps(all_column_entries), schema, schema, role),
    )
    for name, required in cursor.fetchall():
        (missing if required else forbidden).append(str(name))
    return tuple(sorted(set(missing))), tuple(sorted(set(forbidden)))
