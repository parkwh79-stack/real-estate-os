"""Isolated official-corpus worker, scheduler, and progress reporting."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

from db.legal_sync_database import (
    connect_legal_sync_database,
    validate_legal_sync_search_path,
)
from legal_intelligence.models import LegalResourceKind
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from use_cases.legal_corpus_coordinator import (
    LegalCorpusBatchCoordinator,
    LegalCorpusCoordinationError,
)
from use_cases.legal_corpus_registry import (
    LegalCorpusBackfillMode,
    OfficialCorpusSourceAvailability,
    OfficialCorpusSourceRequest,
    OfficialCorpusSourceUnavailable,
    VersionedOfficialCorpusSourceRegistry,
)
from use_cases.legal_corpus_sync import (
    _deterministic_id,
    sanitize_requested_scope,
)


_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$")


class LegalCorpusWorkerConfigurationError(RuntimeError):
    """Required worker credentials or schedule settings are invalid."""


class LegalCorpusJobPlan(BaseModel):
    """One code-registered provider target and public query scope."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    provider_key: str = Field(
        default="law-go-kr",
        alias="providerKey",
        pattern=r"^[a-z0-9][a-z0-9-]{2,79}$",
    )
    resource_kind: LegalResourceKind = Field(alias="resourceKind")
    query: str = Field(min_length=1, max_length=500)
    requested_scope: str = Field(
        alias="requestedScope",
        min_length=1,
        max_length=500,
    )
    public_query_acknowledged: Literal[True] = Field(
        alias="publicQueryAcknowledged",
    )
    mode: LegalCorpusBackfillMode
    requested_as_of_date: date | None = Field(
        default=None,
        alias="requestedAsOfDate",
    )

    @property
    def query_sha256(self) -> str:
        return hashlib.sha256(self.query.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LegalCorpusRuntimeConfiguration:
    worker_enabled: bool
    schedule_seconds: int | None
    run_on_startup: bool
    requested_by: str | None
    worker_id: str | None
    law_go_kr_oc: str | None
    jobs: tuple[LegalCorpusJobPlan, ...]

    def public_view(
        self,
        registry: VersionedOfficialCorpusSourceRegistry,
    ) -> dict[str, object]:
        jobs: list[dict[str, object]] = []
        for plan in self.jobs:
            try:
                registration = registry.registration(
                    plan.provider_key,
                    plan.resource_kind,
                )
                capability = registration.capability.as_public_dict()
            except OfficialCorpusSourceUnavailable as exc:
                capability = {
                    "availability": "blocked",
                    "reasonCode": exc.reason_code,
                    "reason": str(exc),
                    "supportedModes": [],
                }
            jobs.append(
                {
                    "providerKey": plan.provider_key,
                    "resourceKind": plan.resource_kind.value,
                    "mode": plan.mode.value,
                    "requestedScope": sanitize_requested_scope(
                        plan.requested_scope
                    ),
                    "requestedAsOfDate": (
                        plan.requested_as_of_date.isoformat()
                        if plan.requested_as_of_date
                        else None
                    ),
                    "querySha256": plan.query_sha256,
                    "source": capability,
                    "checkpointStrategy": (
                        (
                            "restart_idempotently_for_complete_manifest"
                            if plan.mode
                            == LegalCorpusBackfillMode.INITIAL
                            else "resume_latest_append_only_checkpoint"
                        )
                    ),
                }
            )
        return {
            "workerEnabled": self.worker_enabled,
            "scheduleSeconds": self.schedule_seconds,
            "runOnStartup": self.run_on_startup,
            "requestedByConfigured": bool(self.requested_by),
            "workerIdConfigured": bool(self.worker_id),
            "lawGoKrCredentialConfigured": bool(self.law_go_kr_oc),
            "jobs": jobs,
        }


@dataclass(frozen=True)
class LegalCorpusStoredProgress:
    latest_checkpoint: str | None = None
    latest_checkpoint_sequence: int | None = None
    completion_attestation_id: str | None = None
    terminal_checkpoint: str | None = None
    initial_completion_attestation_id: str | None = None

    @property
    def initial_backfill_complete(self) -> bool:
        return bool(self.initial_completion_attestation_id)


class LegalCorpusProgressRepository(Protocol):
    def load_progress(
        self,
        *,
        provider_key: str,
        provider_revision: int,
        requested_scope: str,
        coverage_identity: OfficialCorpusCoverageIdentity,
    ) -> LegalCorpusStoredProgress: ...


class PostgresLegalCorpusProgressRepository:
    """Read only the append-only checkpoint/completion ledger."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any] = (
            connect_legal_sync_database
        ),
        search_path: str | None = None,
    ) -> None:
        self._connect = connection_factory
        self._search_path = validate_legal_sync_search_path(
            search_path
            or os.environ.get("REALYTICS_LEGAL_SYNC_SEARCH_PATH")
            or "realytics,extensions,public,pg_temp"
        )

    def load_progress(
        self,
        *,
        provider_key: str,
        provider_revision: int,
        requested_scope: str,
        coverage_identity: OfficialCorpusCoverageIdentity,
    ) -> LegalCorpusStoredProgress:
        provider_id = _deterministic_id(
            "legal-provider",
            {
                "providerKey": provider_key,
                "providerRevision": provider_revision,
            },
        )
        canonical_scope = sanitize_requested_scope(requested_scope)
        coverage_identity.assert_provider(
            provider_key=provider_key,
            provider_revision=provider_revision,
            provider_id=provider_id,
        )
        scope_hash = coverage_identity.scope_hash(
            requested_scope=canonical_scope
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('search_path', %s, true)",
                    (self._search_path,),
                )
                cursor.execute(
                    """
                    SELECT checkpoint_after, checkpoint_sequence
                    FROM legal_sync_checkpoint_events AS checkpoint
                    JOIN legal_sync_runs AS run
                      ON run.sync_run_id = checkpoint.sync_run_id
                     AND run.provider_id = checkpoint.provider_id
                     AND run.coverage_contract_id = %s
                    WHERE checkpoint.provider_id = %s
                      AND checkpoint.requested_scope_hash = %s
                    ORDER BY checkpoint.checkpoint_sequence DESC
                    LIMIT 1
                    """,
                    (
                        coverage_identity.coverage_contract_id,
                        provider_id,
                        scope_hash,
                    ),
                )
                checkpoint_row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT completion_attestation_id, terminal_checkpoint
                    FROM legal_corpus_scope_completion_attestations
                         AS completion
                    WHERE completion.provider_id = %s
                      AND completion.requested_scope_hash = %s
                      AND completion.coverage_contract_id = %s
                      AND completion.verification_status = 'verified'
                      AND NOT EXISTS (
                            SELECT 1
                            FROM legal_corpus_scope_completion_attestations
                                 AS successor
                            WHERE successor.baseline_completion_attestation_id =
                                  completion.completion_attestation_id
                      )
                    ORDER BY completion.last_checkpoint_sequence DESC
                    LIMIT 1
                    """,
                    (
                        provider_id,
                        scope_hash,
                        coverage_identity.coverage_contract_id,
                    ),
                )
                completion_row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT completion_attestation_id
                    FROM legal_corpus_scope_completion_attestations
                    WHERE provider_id = %s
                      AND requested_scope_hash = %s
                      AND coverage_contract_id = %s
                      AND coverage_mode = 'initial'
                      AND verification_status = 'verified'
                    ORDER BY last_checkpoint_sequence DESC
                    LIMIT 1
                    """,
                    (
                        provider_id,
                        scope_hash,
                        coverage_identity.coverage_contract_id,
                    ),
                )
                initial_completion_row = cursor.fetchone()
        return LegalCorpusStoredProgress(
            latest_checkpoint=(
                str(checkpoint_row[0]) if checkpoint_row else None
            ),
            latest_checkpoint_sequence=(
                int(checkpoint_row[1]) if checkpoint_row else None
            ),
            completion_attestation_id=(
                str(completion_row[0]) if completion_row else None
            ),
            terminal_checkpoint=(
                str(completion_row[1]) if completion_row else None
            ),
            initial_completion_attestation_id=(
                str(initial_completion_row[0])
                if initial_completion_row
                else None
            ),
        )


class LegalCorpusJobStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class LegalCorpusJobResult:
    provider_key: str
    resource_kind: LegalResourceKind
    mode: LegalCorpusBackfillMode
    requested_scope: str
    query_sha256: str
    status: LegalCorpusJobStatus
    checkpoint_before: str | None = None
    execution_checkpoint_before: str | None = None
    checkpoint_after: str | None = None
    checkpoint_sequence_before: int | None = None
    completion_attestation_id: str | None = None
    page_count: int = 0
    record_count: int = 0
    resource_count: int = 0
    partial: bool = False
    retryable: bool = False
    reason_code: str | None = None
    reason: str | None = None
    diagnostics: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "providerKey": self.provider_key,
            "resourceKind": self.resource_kind.value,
            "mode": self.mode.value,
            "requestedScope": self.requested_scope,
            "querySha256": self.query_sha256,
            "status": self.status.value,
            "checkpointBefore": self.checkpoint_before,
            "executionCheckpointBefore": (
                self.execution_checkpoint_before
            ),
            "checkpointAfter": self.checkpoint_after,
            "checkpointSequenceBefore": self.checkpoint_sequence_before,
            "completionAttestationId": self.completion_attestation_id,
            "pageCount": self.page_count,
            "recordCount": self.record_count,
            "resourceCount": self.resource_count,
            "partial": self.partial,
            "retryable": self.retryable,
            "reasonCode": self.reason_code,
            "reason": self.reason,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class LegalCorpusWorkerReport:
    worker_id: str
    started_at: datetime
    completed_at: datetime
    status: LegalCorpusJobStatus
    jobs: tuple[LegalCorpusJobResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "workerId": self.worker_id,
            "startedAt": self.started_at.isoformat(),
            "completedAt": self.completed_at.isoformat(),
            "status": self.status.value,
            "jobs": [job.as_dict() for job in self.jobs],
        }


class LegalCorpusWorker:
    """Run configured sources through the certified batch coordinator."""

    def __init__(
        self,
        *,
        coordinator: LegalCorpusBatchCoordinator,
        registry: VersionedOfficialCorpusSourceRegistry,
        progress_repository: LegalCorpusProgressRepository,
        configuration: LegalCorpusRuntimeConfiguration,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not configuration.worker_enabled:
            raise LegalCorpusWorkerConfigurationError(
                "legal corpus worker is disabled"
            )
        self._coordinator = coordinator
        self._registry = registry
        self._progress = progress_repository
        self._configuration = configuration
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> LegalCorpusWorkerReport:
        started_at = _aware(self._clock())
        results = tuple(
            self._run_job(plan) for plan in self._configuration.jobs
        )
        return LegalCorpusWorkerReport(
            worker_id=str(self._configuration.worker_id),
            started_at=started_at,
            completed_at=_aware(self._clock()),
            status=_aggregate_status(results),
            jobs=results,
        )

    def _run_job(
        self,
        plan: LegalCorpusJobPlan,
    ) -> LegalCorpusJobResult:
        try:
            registration = self._registry.registration(
                plan.provider_key,
                plan.resource_kind,
            )
        except OfficialCorpusSourceUnavailable as exc:
            return _blocked_result(plan, exc.reason_code, str(exc))
        capability = registration.capability
        if (
            capability.availability
            != OfficialCorpusSourceAvailability.ENABLED
        ):
            return _blocked_result(
                plan,
                str(capability.reason_code),
                str(capability.reason),
            )
        if plan.mode not in capability.supported_modes:
            return _blocked_result(
                plan,
                "OFFICIAL_CORPUS_BACKFILL_MODE_UNSUPPORTED",
                f"{plan.mode.value} is not certified for "
                f"{plan.resource_kind.value}.",
            )
        if plan.query_sha256 != capability.certified_query_sha256:
            return _blocked_result(
                plan,
                "OFFICIAL_CORPUS_QUERY_NOT_CERTIFIED",
                "The configured query does not match the code-owned "
                f"coverage contract for {plan.resource_kind.value}.",
            )
        coverage_identity = capability.coverage_identity()
        try:
            progress = self._progress.load_progress(
                provider_key=plan.provider_key,
                provider_revision=capability.provider_revision,
                requested_scope=plan.requested_scope,
                coverage_identity=coverage_identity,
            )
        except Exception as exc:
            return LegalCorpusJobResult(
                **_result_identity(plan),
                status=LegalCorpusJobStatus.FAILED,
                reason_code="CORPUS_CHECKPOINT_LOOKUP_FAILED",
                reason=type(exc).__name__,
                retryable=True,
            )
        if (
            plan.mode == LegalCorpusBackfillMode.INITIAL
            and progress.initial_backfill_complete
        ):
            return LegalCorpusJobResult(
                **_result_identity(plan),
                status=LegalCorpusJobStatus.SKIPPED,
                checkpoint_before=progress.latest_checkpoint,
                checkpoint_after=progress.terminal_checkpoint,
                checkpoint_sequence_before=(
                    progress.latest_checkpoint_sequence
                ),
                completion_attestation_id=(
                    progress.completion_attestation_id
                ),
                reason_code="INITIAL_BACKFILL_ALREADY_ATTESTED",
                reason=(
                    "A verified completion attestation already exists for "
                    "this provider scope."
                ),
            )
        if (
            plan.mode == LegalCorpusBackfillMode.INCREMENTAL
            and (
                not progress.initial_completion_attestation_id
                or not progress.completion_attestation_id
            )
        ):
            return LegalCorpusJobResult(
                **_result_identity(plan),
                status=LegalCorpusJobStatus.BLOCKED,
                checkpoint_before=progress.latest_checkpoint,
                checkpoint_sequence_before=(
                    progress.latest_checkpoint_sequence
                ),
                reason_code="INCREMENTAL_BASELINE_REQUIRED",
                reason=(
                    "Incremental publication requires a verified initial "
                    "completion and the current completion leaf for the "
                    "same coverage contract."
                ),
            )
        request = OfficialCorpusSourceRequest(
            resource_kind=plan.resource_kind,
            query=plan.query,
            requested_scope=plan.requested_scope,
            requested_by=str(self._configuration.requested_by),
            credential=str(self._configuration.law_go_kr_oc),
            mode=plan.mode,
            requested_as_of_date=plan.requested_as_of_date,
            lease_owner_id=self._configuration.worker_id,
        )
        try:
            source = self._registry.create_source(
                plan.provider_key,
                request,
            )
        except OfficialCorpusSourceUnavailable as exc:
            return LegalCorpusJobResult(
                **_result_identity(plan),
                status=LegalCorpusJobStatus.BLOCKED,
                checkpoint_before=progress.latest_checkpoint,
                checkpoint_sequence_before=(
                    progress.latest_checkpoint_sequence
                ),
                reason_code=exc.reason_code,
                reason=str(exc),
            )
        try:
            execution_checkpoint = (
                progress.latest_checkpoint
                if plan.mode == LegalCorpusBackfillMode.INCREMENTAL
                else None
            )
            attestation = self._coordinator.run(
                source,
                initial_checkpoint=execution_checkpoint,
                baseline_completion_attestation_id=(
                    progress.completion_attestation_id
                    if plan.mode
                    == LegalCorpusBackfillMode.INCREMENTAL
                    else None
                ),
            )
        except LegalCorpusCoordinationError as exc:
            return LegalCorpusJobResult(
                **_result_identity(plan),
                status=(
                    LegalCorpusJobStatus.PARTIAL
                    if exc.partial
                    else LegalCorpusJobStatus.FAILED
                ),
                checkpoint_before=progress.latest_checkpoint,
                execution_checkpoint_before=execution_checkpoint,
                checkpoint_after=exc.last_checkpoint,
                checkpoint_sequence_before=(
                    progress.latest_checkpoint_sequence
                ),
                page_count=exc.completed_page_count,
                partial=exc.partial,
                retryable=exc.retryable,
                reason_code="CORPUS_COORDINATION_INCOMPLETE",
                reason=str(exc),
                diagnostics=tuple(
                    {
                        "code": item.code,
                        "message": item.message,
                        "retryable": item.retryable,
                        "officialIdentifier": item.official_identifier,
                        "target": item.target,
                        "retryAfterSeconds": (
                            item.retry_after_seconds
                        ),
                    }
                    for item in exc.diagnostics
                ),
            )
        except Exception as exc:
            return LegalCorpusJobResult(
                **_result_identity(plan),
                status=LegalCorpusJobStatus.FAILED,
                checkpoint_before=progress.latest_checkpoint,
                checkpoint_sequence_before=(
                    progress.latest_checkpoint_sequence
                ),
                retryable=True,
                reason_code="CORPUS_SOURCE_OR_PUBLICATION_FAILED",
                reason=type(exc).__name__,
            )
        return LegalCorpusJobResult(
            **_result_identity(plan),
            status=LegalCorpusJobStatus.COMPLETED,
            checkpoint_before=progress.latest_checkpoint,
            execution_checkpoint_before=execution_checkpoint,
            checkpoint_after=attestation.terminal_checkpoint,
            checkpoint_sequence_before=progress.latest_checkpoint_sequence,
            completion_attestation_id=(
                attestation.completion_attestation_id
            ),
            page_count=attestation.page_count,
            record_count=attestation.record_count,
            resource_count=attestation.resource_count,
        )


class LegalCorpusScheduler:
    """Small external-process scheduler; the API never performs ingestion."""

    def __init__(
        self,
        worker: LegalCorpusWorker,
        *,
        interval_seconds: int,
        run_on_startup: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 60 <= interval_seconds <= 604_800:
            raise ValueError(
                "legal corpus schedule must be between 60 and 604800 seconds"
            )
        self._worker = worker
        self._interval_seconds = interval_seconds
        self._run_on_startup = run_on_startup
        self._sleep = sleep

    def run_once(self) -> LegalCorpusWorkerReport:
        return self._worker.run_once()

    def run_forever(
        self,
        *,
        should_stop: Callable[[], bool] = lambda: False,
        on_report: Callable[[LegalCorpusWorkerReport], None] | None = None,
    ) -> None:
        if not self._run_on_startup and not should_stop():
            self._sleep(self._interval_seconds)
        while not should_stop():
            report = self.run_once()
            if on_report is not None:
                on_report(report)
            if should_stop():
                break
            self._sleep(self._interval_seconds)


def load_legal_corpus_runtime_configuration(
    environment: Mapping[str, str] | None = None,
    *,
    require_schedule: bool = True,
) -> LegalCorpusRuntimeConfiguration:
    env = environment if environment is not None else os.environ
    enabled = _strict_bool(
        env.get("REALYTICS_LEGAL_CORPUS_WORKER_ENABLED"),
        name="REALYTICS_LEGAL_CORPUS_WORKER_ENABLED",
        default=False,
    )
    run_on_startup = _strict_bool(
        env.get("REALYTICS_LEGAL_CORPUS_RUN_ON_STARTUP"),
        name="REALYTICS_LEGAL_CORPUS_RUN_ON_STARTUP",
        default=False,
    )
    if not enabled:
        return LegalCorpusRuntimeConfiguration(
            worker_enabled=False,
            schedule_seconds=None,
            run_on_startup=run_on_startup,
            requested_by=None,
            worker_id=None,
            law_go_kr_oc=None,
            jobs=(),
        )

    schedule_seconds = (
        _required_integer(
            env,
            "REALYTICS_LEGAL_CORPUS_SCHEDULE_SECONDS",
            minimum=60,
            maximum=604_800,
        )
        if require_schedule
        else _optional_integer(
            env,
            "REALYTICS_LEGAL_CORPUS_SCHEDULE_SECONDS",
            minimum=60,
            maximum=604_800,
        )
    )
    requested_by = _required_safe_actor(
        env,
        "REALYTICS_LEGAL_CORPUS_REQUESTED_BY",
    )
    worker_id = _required_safe_actor(
        env,
        "REALYTICS_LEGAL_CORPUS_WORKER_ID",
    )
    oc = _required(
        env,
        "REALYTICS_LAW_GO_KR_OC",
    )
    _required(env, "REALYTICS_LEGAL_SYNC_DATABASE_URL")
    raw_backend = _required(
        env,
        "REALYTICS_LEGAL_RAW_OBJECT_STORAGE",
    ).casefold()
    if raw_backend != "s3":
        raise LegalCorpusWorkerConfigurationError(
            "REALYTICS_LEGAL_RAW_OBJECT_STORAGE must be s3"
        )
    _required(env, "REALYTICS_LEGAL_RAW_BUCKET")

    raw_jobs = _required(
        env,
        "REALYTICS_LEGAL_CORPUS_JOBS_JSON",
    )
    try:
        payload = json.loads(raw_jobs)
    except json.JSONDecodeError as exc:
        raise LegalCorpusWorkerConfigurationError(
            "REALYTICS_LEGAL_CORPUS_JOBS_JSON must be valid JSON"
        ) from exc
    if not isinstance(payload, list) or not payload or len(payload) > 50:
        raise LegalCorpusWorkerConfigurationError(
            "REALYTICS_LEGAL_CORPUS_JOBS_JSON must contain 1 to 50 jobs"
        )
    jobs: list[LegalCorpusJobPlan] = []
    try:
        for item in payload:
            jobs.append(LegalCorpusJobPlan.model_validate(item))
    except Exception as exc:
        raise LegalCorpusWorkerConfigurationError(
            "REALYTICS_LEGAL_CORPUS_JOBS_JSON contains an invalid job"
        ) from exc
    identities = [
        (
            item.provider_key,
            item.resource_kind,
            sanitize_requested_scope(item.requested_scope),
        )
        for item in jobs
    ]
    if len(identities) != len(set(identities)):
        raise LegalCorpusWorkerConfigurationError(
            "legal corpus jobs must have unique provider/kind/scope"
        )
    return LegalCorpusRuntimeConfiguration(
        worker_enabled=True,
        schedule_seconds=schedule_seconds,
        run_on_startup=run_on_startup,
        requested_by=requested_by,
        worker_id=worker_id,
        law_go_kr_oc=oc,
        jobs=tuple(jobs),
    )


def inspect_legal_corpus_runtime_configuration(
    *,
    environment: Mapping[str, str] | None = None,
    registry: VersionedOfficialCorpusSourceRegistry,
) -> dict[str, object]:
    try:
        configuration = load_legal_corpus_runtime_configuration(environment)
    except LegalCorpusWorkerConfigurationError as exc:
        return {
            "configurationStatus": "invalid",
            "workerEnabled": False,
            "errorCode": "LEGAL_CORPUS_RUNTIME_CONFIGURATION_INVALID",
            "message": "Legal corpus worker configuration is invalid. Contact an operator.",
            "registry": [
                item.as_public_dict()
                for item in registry.capabilities()
            ],
        }
    result = configuration.public_view(registry)
    result["configurationStatus"] = (
        "valid" if configuration.worker_enabled else "disabled"
    )
    result["registry"] = [
        item.as_public_dict() for item in registry.capabilities()
    ]
    return result


def _strict_bool(
    value: str | None,
    *,
    name: str,
    default: bool,
) -> bool:
    text = str(value or "").strip().casefold()
    if not text:
        return default
    if text == "true":
        return True
    if text == "false":
        return False
    raise LegalCorpusWorkerConfigurationError(
        f"{name} must be exactly true or false"
    )


def _required(
    environment: Mapping[str, str],
    name: str,
) -> str:
    value = str(environment.get(name) or "").strip()
    if not value:
        raise LegalCorpusWorkerConfigurationError(f"{name} is required")
    return value


def _required_safe_actor(
    environment: Mapping[str, str],
    name: str,
) -> str:
    value = _required(environment, name)
    if not _SAFE_ACTOR.fullmatch(value):
        raise LegalCorpusWorkerConfigurationError(
            f"{name} must be a privacy-safe worker identifier"
        )
    return value


def _required_integer(
    environment: Mapping[str, str],
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    text = _required(environment, name)
    try:
        value = int(text)
    except ValueError as exc:
        raise LegalCorpusWorkerConfigurationError(
            f"{name} must be an integer"
        ) from exc
    if not minimum <= value <= maximum:
        raise LegalCorpusWorkerConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _optional_integer(
    environment: Mapping[str, str],
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if not str(environment.get(name) or "").strip():
        return None
    return _required_integer(
        environment,
        name,
        minimum=minimum,
        maximum=maximum,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("legal corpus worker clock must be timezone-aware")
    return value


def _result_identity(plan: LegalCorpusJobPlan) -> dict[str, object]:
    return {
        "provider_key": plan.provider_key,
        "resource_kind": plan.resource_kind,
        "mode": plan.mode,
        "requested_scope": plan.requested_scope,
        "query_sha256": plan.query_sha256,
    }


def _blocked_result(
    plan: LegalCorpusJobPlan,
    reason_code: str,
    reason: str,
) -> LegalCorpusJobResult:
    return LegalCorpusJobResult(
        **_result_identity(plan),
        status=LegalCorpusJobStatus.BLOCKED,
        reason_code=reason_code,
        reason=reason,
    )


def _aggregate_status(
    results: tuple[LegalCorpusJobResult, ...],
) -> LegalCorpusJobStatus:
    statuses = {result.status for result in results}
    for status in (
        LegalCorpusJobStatus.FAILED,
        LegalCorpusJobStatus.PARTIAL,
        LegalCorpusJobStatus.BLOCKED,
        LegalCorpusJobStatus.COMPLETED,
        LegalCorpusJobStatus.SKIPPED,
    ):
        if status in statuses:
            return status
    return LegalCorpusJobStatus.SKIPPED


__all__ = [
    "LegalCorpusBackfillMode",
    "LegalCorpusJobPlan",
    "LegalCorpusJobResult",
    "LegalCorpusJobStatus",
    "LegalCorpusProgressRepository",
    "LegalCorpusRuntimeConfiguration",
    "LegalCorpusScheduler",
    "LegalCorpusStoredProgress",
    "LegalCorpusWorker",
    "LegalCorpusWorkerConfigurationError",
    "LegalCorpusWorkerReport",
    "PostgresLegalCorpusProgressRepository",
    "inspect_legal_corpus_runtime_configuration",
    "load_legal_corpus_runtime_configuration",
]
