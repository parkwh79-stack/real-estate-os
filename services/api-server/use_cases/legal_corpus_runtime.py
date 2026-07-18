"""Production composition root for the isolated legal corpus worker."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

from db.legal_raw_object_storage import (
    S3ImmutableLegalRawObjectStore,
    get_immutable_legal_raw_object_store,
)
from db.repositories.legal_corpus_completion_repository import (
    PostgresLegalCorpusCompletionRepository,
)
from db.repositories.legal_corpus_repository import (
    LegalCorpusSyncRepository,
    PostgresLegalCorpusSyncRepository,
)
from use_cases.legal_corpus_coordinator import (
    LegalCorpusBatchCoordinator,
)
from use_cases.legal_corpus_sync import LegalCorpusSyncService


def build_legal_corpus_batch_coordinator(
    *,
    connection_factory: Callable[[], Any] | None = None,
    raw_object_store: S3ImmutableLegalRawObjectStore | None = None,
    clock: Callable[[], datetime] | None = None,
    verify_storage: bool = True,
    max_pages: int | None = None,
) -> LegalCorpusBatchCoordinator:
    """Wire sync and completion to one dedicated role and immutable bucket."""

    search_path = os.environ.get(
        "REALYTICS_LEGAL_SYNC_SEARCH_PATH",
        "realytics,extensions,public,pg_temp",
    )
    repository: LegalCorpusSyncRepository = (
        PostgresLegalCorpusSyncRepository(
            connection_factory=connection_factory,
            search_path=search_path,
        )
    )
    completion_repository = PostgresLegalCorpusCompletionRepository(
        connection_factory=connection_factory,
        search_path=search_path,
    )
    object_store = (
        raw_object_store or get_immutable_legal_raw_object_store()
    )
    if verify_storage:
        object_store.verify_configuration()
    sync_service = LegalCorpusSyncService(
        repository,
        object_store,
        clock=clock,
    )
    return LegalCorpusBatchCoordinator(
        sync_service,
        completion_repository,
        max_pages=(
            max_pages
            if max_pages is not None
            else _positive_integer_env(
                "REALYTICS_LEGAL_SYNC_MAX_PAGES", 10_000
            )
        ),
        clock=clock,
    )


def _positive_integer_env(name: str, default: int) -> int:
    text = str(os.environ.get(name) or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


__all__ = ["build_legal_corpus_batch_coordinator"]
