"""Fail-closed connection boundary for human Legal Citator review."""

from __future__ import annotations

import os
from typing import Any

from db import postgres
from db.postgres_legal_review_privileges import (
    LEGAL_REVIEW_ROLE_ENV,
    LegalReviewPrivilegeError,
    validate_identifier,
)


LEGAL_REVIEW_DATABASE_URL_ENV = "REALYTICS_LEGAL_REVIEW_DATABASE_URL"


class LegalReviewDatabaseConfigurationError(RuntimeError):
    """The dedicated legal-review database principal is not configured."""


def legal_review_database_configured() -> bool:
    return bool(
        str(os.environ.get(LEGAL_REVIEW_DATABASE_URL_ENV) or "").strip()
    )


def connect_legal_review_database() -> Any:
    """Connect only through the dedicated append-only legal-review DSN.

    The API runtime and legal-sync DSNs are deliberately not fallbacks: the
    former is read-only for global Corpus records and the latter is an
    ingestion principal, not a human decision principal.
    """

    dsn = str(os.environ.get(LEGAL_REVIEW_DATABASE_URL_ENV) or "").strip()
    if not dsn:
        raise LegalReviewDatabaseConfigurationError(
            f"{LEGAL_REVIEW_DATABASE_URL_ENV} is required for human Citator "
            "review"
        )
    try:
        role = validate_identifier(
            os.environ.get(LEGAL_REVIEW_ROLE_ENV) or "realytics_legal_review",
            label="legal review database role",
        )
        connection = postgres.connect(dsn)
    except LegalReviewPrivilegeError as exc:
        raise LegalReviewDatabaseConfigurationError(
            "The configured legal review database role is invalid"
        ) from exc
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user, session_user, rolcanlogin, rolsuper,
                       rolcreatedb, rolcreaterole, rolinherit,
                       rolreplication, rolbypassrls
                FROM pg_catalog.pg_roles
                WHERE rolname = current_user
                """
            )
            principal = cursor.fetchone()
        if (
            not principal
            or tuple(principal[:2]) != (role, role)
            or not principal[2]
            or any(principal[3:])
        ):
            raise LegalReviewDatabaseConfigurationError(
                "Legal review requires a direct login with the configured restricted database role"
            )
    except Exception:
        connection.close()
        raise
    return connection


__all__ = [
    "LEGAL_REVIEW_DATABASE_URL_ENV",
    "LegalReviewDatabaseConfigurationError",
    "connect_legal_review_database",
    "legal_review_database_configured",
]
