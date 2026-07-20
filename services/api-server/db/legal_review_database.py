"""Fail-closed connection boundary for human Legal Citator review."""

from __future__ import annotations

import os
from typing import Any

from db import postgres


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
    return postgres.connect(dsn)


__all__ = [
    "LEGAL_REVIEW_DATABASE_URL_ENV",
    "LegalReviewDatabaseConfigurationError",
    "connect_legal_review_database",
    "legal_review_database_configured",
]
