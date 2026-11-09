"""Production composition root for the isolated legal corpus worker."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

from db.legal_raw_object_storage import (
    S3ImmutableLegalRawObjectStore,
    get_immutable_legal_raw_object_store,
)
from db.legal_sync_database import connect_legal_sync_database
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
from use_cases.legal_corpus_registry import (
    VersionedOfficialCorpusSourceRegistry,
    build_default_official_corpus_source_registry,
)
from use_cases.legal_corpus_sync import LegalCorpusSyncService
from use_cases.legal_corpus_worker import (
    LegalCorpusProgressRepository,
    LegalCorpusRuntimeConfiguration,
    LegalCorpusScheduler,
    LegalCorpusWorker,
    LegalCorpusWorkerConfigurationError,
    PostgresLegalCorpusProgressRepository,
    load_legal_corpus_runtime_configuration,
)


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


def build_legal_corpus_worker(
    *,
    configuration: LegalCorpusRuntimeConfiguration | None = None,
    registry: VersionedOfficialCorpusSourceRegistry | None = None,
    coordinator: LegalCorpusBatchCoordinator | None = None,
    progress_repository: LegalCorpusProgressRepository | None = None,
    connection_factory: Callable[[], Any] | None = None,
    raw_object_store: S3ImmutableLegalRawObjectStore | None = None,
    clock: Callable[[], datetime] | None = None,
    verify_storage: bool = True,
) -> LegalCorpusWorker:
    """Build the external worker without an API-runtime fallback."""

    resolved_configuration = (
        configuration or load_legal_corpus_runtime_configuration()
    )
    if not resolved_configuration.worker_enabled:
        raise LegalCorpusWorkerConfigurationError(
            "REALYTICS_LEGAL_CORPUS_WORKER_ENABLED must be true"
        )
    resolved_coordinator = coordinator or (
        build_legal_corpus_batch_coordinator(
            connection_factory=connection_factory,
            raw_object_store=raw_object_store,
            clock=clock,
            verify_storage=verify_storage,
        )
    )
    resolved_progress = progress_repository or (
        PostgresLegalCorpusProgressRepository(
            connection_factory=(
                connection_factory
                if connection_factory is not None
                else connect_legal_sync_database
            ),
            search_path=os.environ.get(
                "REALYTICS_LEGAL_SYNC_SEARCH_PATH",
                "realytics,extensions,public,pg_temp",
            ),
        )
    )
    return LegalCorpusWorker(
        coordinator=resolved_coordinator,
        registry=(
            registry or build_default_official_corpus_source_registry()
        ),
        progress_repository=resolved_progress,
        configuration=resolved_configuration,
        clock=clock,
    )


def build_legal_corpus_scheduler(
    *,
    configuration: LegalCorpusRuntimeConfiguration | None = None,
    worker: LegalCorpusWorker | None = None,
    **worker_options: Any,
) -> LegalCorpusScheduler:
    resolved_configuration = (
        configuration or load_legal_corpus_runtime_configuration()
    )
    if (
        not resolved_configuration.worker_enabled
        or resolved_configuration.schedule_seconds is None
    ):
        raise LegalCorpusWorkerConfigurationError(
            "legal corpus worker and schedule must be configured"
        )
    resolved_worker = worker or build_legal_corpus_worker(
        configuration=resolved_configuration,
        **worker_options,
    )
    return LegalCorpusScheduler(
        resolved_worker,
        interval_seconds=resolved_configuration.schedule_seconds,
        run_on_startup=resolved_configuration.run_on_startup,
    )


__all__ = [
    "build_legal_corpus_batch_coordinator",
    "build_legal_corpus_scheduler",
    "build_legal_corpus_worker",
]
