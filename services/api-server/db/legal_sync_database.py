"""Fail-closed connection boundary for the legal corpus maintenance role."""

from __future__ import annotations

import os
import re
from typing import Any

from db import postgres


LEGAL_SYNC_DATABASE_URL_ENV = "REALYTICS_LEGAL_SYNC_DATABASE_URL"


class LegalSyncDatabaseConfigurationError(RuntimeError):
    """The dedicated legal-sync database principal is not configured."""


def connect_legal_sync_database() -> Any:
    """Connect only with the explicitly configured legal-sync DSN.

    Falling back to the API runtime or migration DSN would erase the privilege
    boundary between serving verified law and maintaining the global corpus.
    """

    dsn = str(os.environ.get(LEGAL_SYNC_DATABASE_URL_ENV) or "").strip()
    if not dsn:
        raise LegalSyncDatabaseConfigurationError(
            f"{LEGAL_SYNC_DATABASE_URL_ENV} is required for legal corpus "
            "maintenance"
        )
    return postgres.connect(dsn)


def validate_legal_sync_search_path(value: str) -> str:
    """Return a private-first, temp-last, injection-safe search path."""

    components = [component.strip() for component in value.split(",")]
    if not components or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", component)
        for component in components
    ):
        raise ValueError(
            "legal sync search_path contains an unsafe schema name"
        )
    lowered = [component.casefold() for component in components]
    if len(set(lowered)) != len(lowered):
        raise ValueError(
            "legal sync search_path contains duplicate schemas"
        )
    if lowered[-1] != "pg_temp":
        raise ValueError(
            "pg_temp must be the final legal sync search_path component"
        )
    if lowered[0] in {
        "extensions",
        "pg_catalog",
        "pg_temp",
        "public",
    }:
        raise ValueError(
            "legal sync search_path must start with the private app schema"
        )
    return ",".join(components)


__all__ = [
    "LEGAL_SYNC_DATABASE_URL_ENV",
    "LegalSyncDatabaseConfigurationError",
    "connect_legal_sync_database",
    "validate_legal_sync_search_path",
]
