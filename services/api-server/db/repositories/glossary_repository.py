"""Read the normalized glossary through the verified tenant's API transaction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
import json
from typing import Any

from core.text_normalization import normalize_match_text
from db.postgres_runtime_pool import api_tenant_transaction


class GlossaryAuthorityUnavailable(RuntimeError):
    def __init__(self, authority: str):
        self.authority = authority
        super().__init__("The normalized glossary authority is unavailable.")


class PostgresGlossaryRepository:
    def __init__(
        self,
        *,
        tenant_id: str,
        transaction_factory: Callable[[str], AbstractContextManager[Any]] = api_tenant_transaction,
    ) -> None:
        if not tenant_id or not tenant_id.strip():
            raise ValueError("A verified tenant is required for glossary access")
        self.tenant_id = tenant_id
        self._transaction = transaction_factory

    def _read(self, authority: str, sql: str, parameters: list[Any]) -> list[Any]:
        try:
            with self._transaction(self.tenant_id) as connection:
                return connection.execute(sql, parameters).fetchall()
        except Exception as exc:
            raise GlossaryAuthorityUnavailable(authority) from exc

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        payload = json.loads(value) if isinstance(value, str) else deepcopy(value)
        if not isinstance(payload, dict):
            raise GlossaryAuthorityUnavailable("payload")
        return payload

    def list_terms(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        canonical_field: str | None = None,
        review_status: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = []
        parameters = []
        for column, value in (
            ("category", category),
            ("canonical_field", canonical_field),
            ("review_status", review_status),
        ):
            if value:
                filters.append(column + " = %s")
                parameters.append(value)
        sql = "SELECT payload_json FROM realytics.glossary_terms"
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY category, term_id"
        terms = [
            self._payload(row["payload_json"] if isinstance(row, Mapping) else row[0])
            for row in self._read("terms", sql, parameters)
        ]
        normalized_query = normalize_match_text(query or "")
        if normalized_query:
            terms = [term for term in terms if normalized_query in normalize_match_text(" ".join([
                str(term.get(key) or "")
                for key in ("termId", "preferredTerm", "displayNameKo", "displayNameEn", "definitionKo", "canonicalField")
            ] + [str(alias) for alias in term.get("aliases") or []]))]
        return sorted(terms, key=lambda term: (term.get("category") or "", term.get("displayNameKo") or term.get("termId") or ""))

    def get_term(self, term_id: str) -> dict[str, Any] | None:
        if not term_id:
            return None
        rows = self._read("terms", "SELECT payload_json FROM realytics.glossary_terms WHERE term_id = %s", [term_id])
        if not rows:
            return None
        row = rows[0]
        return self._payload(row["payload_json"] if isinstance(row, Mapping) else row[0])

    def list_candidates(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters = ["tenant_id = %s"]
        parameters: list[Any] = [self.tenant_id]
        for column, value in (("status", status), ("project_id", project_id), ("category", category)):
            if value:
                filters.append(column + " = %s")
                parameters.append(value)
        sql = (
            "SELECT tenant_id, project_id, payload_json FROM realytics.glossary_term_candidates WHERE "
            + " AND ".join(filters)
            + " ORDER BY status DESC, updated_at DESC, candidate_id"
        )
        results = []
        normalized_query = normalize_match_text(query or "")
        for row in self._read("candidates", sql, parameters):
            tenant, project, value = (
                (row["tenant_id"], row["project_id"], row["payload_json"])
                if isinstance(row, Mapping) else row
            )
            payload = self._payload(value)
            if tenant != self.tenant_id or any(
                payload.get(key) not in (None, "", self.tenant_id) for key in ("tenantId", "tenant_id")
            ):
                raise GlossaryAuthorityUnavailable("candidate_scope")
            if payload.get("projectId") not in (None, "", project):
                raise GlossaryAuthorityUnavailable("candidate_scope")
            if project_id and project != project_id:
                raise GlossaryAuthorityUnavailable("candidate_scope")
            if normalized_query and normalized_query not in normalize_match_text(" ".join(
                str(payload.get(key) or "") for key in ("surface", "sourceField", "suggestedCanonicalField", "reviewReason")
            )):
                continue
            payload.update(tenantId=self.tenant_id, projectId=project)
            results.append(payload)
        return sorted(
            results,
            key=lambda item: (item.get("status") or "", item.get("lastSeenAt") or item.get("updatedAt") or ""),
            reverse=True,
        )[:max(1, min(int(limit), 500))]