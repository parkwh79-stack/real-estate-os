from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.legal_corpus_operations as corpus_router
from legal_intelligence.models import LegalResourceKind
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from legal_intelligence.providers import LAW_GO_KR_TARGET_DESCRIPTORS
from use_cases.legal_corpus_coordinator import (
    LegalCorpusCoordinationError,
    OfficialCorpusPageDiagnostic,
)
from use_cases.legal_corpus_registry import (
    LegalCorpusBackfillMode,
    OfficialCorpusSourceAvailability,
    OfficialCorpusSourceRegistration,
    OfficialCorpusSourceRequest,
    OfficialCorpusTargetCapability,
    VersionedOfficialCorpusSourceRegistry,
    build_default_official_corpus_source_registry,
    build_law_go_kr_source_registration,
)
from use_cases.legal_corpus_worker import (
    LegalCorpusJobPlan,
    LegalCorpusJobStatus,
    LegalCorpusRuntimeConfiguration,
    LegalCorpusScheduler,
    LegalCorpusStoredProgress,
    LegalCorpusWorker,
    LegalCorpusWorkerConfigurationError,
    PostgresLegalCorpusProgressRepository,
    load_legal_corpus_runtime_configuration,
)


NOW = datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc)
QUERY = "Housing Act"
QUERY_SHA256 = hashlib.sha256(QUERY.encode("utf-8")).hexdigest()


def _configuration(
    *,
    mode: LegalCorpusBackfillMode = LegalCorpusBackfillMode.INITIAL,
) -> LegalCorpusRuntimeConfiguration:
    return LegalCorpusRuntimeConfiguration(
        worker_enabled=True,
        schedule_seconds=300,
        run_on_startup=True,
        requested_by="legal-sync-worker",
        worker_id="legal-sync-worker:1",
        law_go_kr_oc="secret@example.com",
        jobs=(
            LegalCorpusJobPlan(
                resourceKind="statute",
                query=QUERY,
                requestedScope="statutes:housing",
                mode=mode,
                publicQueryAcknowledged=True,
            ),
        ),
    )


def _enabled_registry(*, modes=(LegalCorpusBackfillMode.INITIAL,)):
    source = object()
    registry = VersionedOfficialCorpusSourceRegistry()
    registry.register(
        OfficialCorpusSourceRegistration(
            capability=OfficialCorpusTargetCapability(
                provider_key="law-go-kr",
                provider_revision=1,
                resource_kind=LegalResourceKind.STATUTE,
                registry_version="official-corpus-registry-v1",
                source_adapter_version="test-source-adapter-v1",
                boundary_contract_version="test-boundary-v1",
                availability=OfficialCorpusSourceAvailability.ENABLED,
                supported_modes=modes,
                detail_verified=True,
                history_verified=True,
                semantic_normalizer_verified=True,
                certified_query_sha256=QUERY_SHA256,
            ),
            factory=lambda _request: source,
        )
    )
    return registry, source


class FakeProgress:
    def __init__(self, value=LegalCorpusStoredProgress()):
        self.value = value
        self.calls = []

    def load_progress(self, **kwargs):
        self.calls.append(kwargs)
        return self.value


class FakeCoordinator:
    def __init__(self, *, result=None, error=None):
        self.result = result or SimpleNamespace(
            completion_attestation_id="completion-1",
            terminal_checkpoint="checkpoint-2",
            page_count=2,
            record_count=3,
            resource_count=2,
        )
        self.error = error
        self.calls = []

    def run(
        self,
        source,
        *,
        initial_checkpoint=None,
        baseline_completion_attestation_id=None,
    ):
        self.calls.append(
            (
                source,
                initial_checkpoint,
                baseline_completion_attestation_id,
            )
        )
        if self.error:
            raise self.error
        return self.result


def test_default_registry_blocks_every_search_target_from_verified_corpus():
    registry = build_default_official_corpus_source_registry()

    assert {
        capability.resource_kind
        for capability in registry.capabilities()
    } == set(LAW_GO_KR_TARGET_DESCRIPTORS)
    assert all(
        capability.availability
        == OfficialCorpusSourceAvailability.BLOCKED
        and capability.detail_verified is True
        and capability.history_verified is False
        and capability.semantic_normalizer_verified is False
        for capability in registry.capabilities()
    )


def test_versioned_law_go_kr_factory_requires_code_owned_verified_adapters():
    registration = build_law_go_kr_source_registration(
        resource_kind=LegalResourceKind.STATUTE,
        record_normalizer=lambda _context: None,
        history_fetcher=lambda *_args: {},
        source_adapter_version="law-go-kr-approved-v2",
        registry_version="official-corpus-registry-v2",
        certified_query_sha256=QUERY_SHA256,
        source_options={"http_get_json": lambda *_args: {}},
    )
    registry = VersionedOfficialCorpusSourceRegistry((registration,))

    source = registry.create_source(
        "law-go-kr",
        OfficialCorpusSourceRequest(
            resource_kind=LegalResourceKind.STATUTE,
            query=QUERY,
            requested_scope="statutes:housing",
            requested_by="legal-sync-worker",
            credential="secret@example.com",
            mode=LegalCorpusBackfillMode.INITIAL,
        ),
    )

    assert registration.capability.availability == (
        OfficialCorpusSourceAvailability.ENABLED
    )
    assert source._adapter_version == "law-go-kr-approved-v2"
    assert source._history_schema_version.endswith("-history-v1")


def test_generic_law_go_kr_factory_cannot_claim_incremental_feed():
    with pytest.raises(ValueError, match="incremental change feed"):
        build_law_go_kr_source_registration(
            resource_kind=LegalResourceKind.STATUTE,
            record_normalizer=lambda _context: None,
            history_fetcher=lambda *_args: {},
            source_adapter_version="law-go-kr-approved-v2",
            registry_version="official-corpus-registry-v2",
            certified_query_sha256=QUERY_SHA256,
            supported_modes=(LegalCorpusBackfillMode.INCREMENTAL,),
        )


def test_worker_reports_blocked_target_without_touching_checkpoint_store():
    progress = FakeProgress()
    worker = LegalCorpusWorker(
        coordinator=FakeCoordinator(),
        registry=build_default_official_corpus_source_registry(),
        progress_repository=progress,
        configuration=_configuration(),
        clock=lambda: NOW,
    )

    report = worker.run_once()

    assert report.status == LegalCorpusJobStatus.BLOCKED
    assert report.jobs[0].reason_code == (
        "VERIFIED_SEMANTIC_AND_HISTORY_ADAPTER_NOT_REGISTERED"
    )
    assert progress.calls == []


def test_initial_worker_replays_from_start_to_attest_complete_manifest():
    registry, source = _enabled_registry()
    progress = FakeProgress(
        LegalCorpusStoredProgress(
            latest_checkpoint="checkpoint-1",
            latest_checkpoint_sequence=7,
        )
    )
    coordinator = FakeCoordinator()
    worker = LegalCorpusWorker(
        coordinator=coordinator,
        registry=registry,
        progress_repository=progress,
        configuration=_configuration(),
        clock=lambda: NOW,
    )

    report = worker.run_once()

    assert report.status == LegalCorpusJobStatus.COMPLETED
    assert coordinator.calls == [(source, None, None)]
    assert report.jobs[0].checkpoint_sequence_before == 7
    assert report.jobs[0].checkpoint_before == "checkpoint-1"
    assert report.jobs[0].execution_checkpoint_before is None
    assert report.jobs[0].checkpoint_after == "checkpoint-2"
    assert report.jobs[0].completion_attestation_id == "completion-1"


def test_worker_skips_already_attested_initial_backfill():
    registry, _ = _enabled_registry()
    progress = FakeProgress(
        LegalCorpusStoredProgress(
            latest_checkpoint="checkpoint-2",
            latest_checkpoint_sequence=8,
            completion_attestation_id="completion-old",
            terminal_checkpoint="checkpoint-2",
            initial_completion_attestation_id="completion-old",
        )
    )
    coordinator = FakeCoordinator()
    worker = LegalCorpusWorker(
        coordinator=coordinator,
        registry=registry,
        progress_repository=progress,
        configuration=_configuration(),
        clock=lambda: NOW,
    )

    report = worker.run_once()

    assert report.jobs[0].status == LegalCorpusJobStatus.SKIPPED
    assert report.jobs[0].reason_code == (
        "INITIAL_BACKFILL_ALREADY_ATTESTED"
    )
    assert coordinator.calls == []


def test_worker_exposes_partial_page_diagnostics_and_resume_checkpoint():
    registry, _ = _enabled_registry()
    coordinator = FakeCoordinator(
        error=LegalCorpusCoordinationError(
            "provider page is partial",
            diagnostics=(
                OfficialCorpusPageDiagnostic(
                    code="DETAIL_FAILED",
                    message="one official detail failed",
                    retryable=True,
                    official_identifier="law-2",
                    target="law",
                ),
            ),
            completed_page_count=1,
            last_checkpoint="checkpoint-1",
        )
    )
    worker = LegalCorpusWorker(
        coordinator=coordinator,
        registry=registry,
        progress_repository=FakeProgress(),
        configuration=_configuration(),
        clock=lambda: NOW,
    )

    report = worker.run_once()

    job = report.jobs[0]
    assert report.status == LegalCorpusJobStatus.PARTIAL
    assert job.partial is True
    assert job.retryable is True
    assert job.page_count == 1
    assert job.checkpoint_after == "checkpoint-1"
    assert job.diagnostics[0]["code"] == "DETAIL_FAILED"


def test_incremental_mode_is_blocked_until_registered_checkpoint_contract():
    registry, _ = _enabled_registry()
    progress = FakeProgress()
    worker = LegalCorpusWorker(
        coordinator=FakeCoordinator(),
        registry=registry,
        progress_repository=progress,
        configuration=_configuration(
            mode=LegalCorpusBackfillMode.INCREMENTAL,
        ),
        clock=lambda: NOW,
    )

    report = worker.run_once()

    assert report.jobs[0].status == LegalCorpusJobStatus.BLOCKED
    assert report.jobs[0].reason_code == (
        "OFFICIAL_CORPUS_BACKFILL_MODE_UNSUPPORTED"
    )
    assert progress.calls == []


def test_registered_incremental_mode_resumes_latest_checkpoint():
    registry, source = _enabled_registry(
        modes=(LegalCorpusBackfillMode.INCREMENTAL,)
    )
    progress = FakeProgress(
        LegalCorpusStoredProgress(
            latest_checkpoint="incremental-7",
            latest_checkpoint_sequence=7,
            completion_attestation_id="completion-latest",
            initial_completion_attestation_id="completion-initial",
        )
    )
    coordinator = FakeCoordinator()
    worker = LegalCorpusWorker(
        coordinator=coordinator,
        registry=registry,
        progress_repository=progress,
        configuration=_configuration(
            mode=LegalCorpusBackfillMode.INCREMENTAL,
        ),
        clock=lambda: NOW,
    )

    report = worker.run_once()

    assert report.jobs[0].status == LegalCorpusJobStatus.COMPLETED
    assert coordinator.calls == [
        (source, "incremental-7", "completion-latest")
    ]
    assert (
        report.jobs[0].execution_checkpoint_before
        == "incremental-7"
    )


def test_postgres_progress_reads_checkpoint_and_verified_completion():
    class Cursor:
        def __init__(self):
            self.statement = ""
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params=None):
            self.statement = statement
            self.calls.append((statement, params))

        def fetchone(self):
            if "FROM legal_sync_checkpoint_events" in self.statement:
                return ("checkpoint-7", 7)
            if (
                "FROM legal_corpus_scope_completion_attestations"
                in self.statement
            ):
                return ("completion-7", "checkpoint-7")
            return None

    class Connection:
        def __init__(self):
            self.value = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self.value

    connection = Connection()
    repository = PostgresLegalCorpusProgressRepository(
        connection_factory=lambda: connection,
    )

    progress = repository.load_progress(
        provider_key="law-go-kr",
        provider_revision=1,
        requested_scope="statutes:housing",
        coverage_identity=OfficialCorpusCoverageIdentity(
            provider_key="law-go-kr",
            provider_revision=1,
            registry_version="official-corpus-registry-v1",
            resource_kind="statute",
            target="law",
            query_sha256=QUERY_SHA256,
            source_adapter_version="test-source-adapter-v1",
            boundary_contract_version="test-boundary-v1",
            coverage_contract_version="official-corpus-coverage-v1",
        ),
    )

    assert progress.latest_checkpoint == "checkpoint-7"
    assert progress.latest_checkpoint_sequence == 7
    assert progress.completion_attestation_id == "completion-7"
    assert "verification_status = 'verified'" in "\n".join(
        statement for statement, _ in connection.value.calls
    )
    statements = "\n".join(
        statement for statement, _ in connection.value.calls
    )
    assert "JOIN legal_sync_runs AS run" in statements
    assert "run.coverage_contract_id = %s" in statements
    checkpoint_call = next(
        params
        for statement, params in connection.value.calls
        if "FROM legal_sync_checkpoint_events AS checkpoint" in statement
    )
    assert checkpoint_call[0].startswith(
        "legal-corpus-coverage-contract:"
    )


def _valid_environment() -> dict[str, str]:
    return {
        "REALYTICS_LEGAL_CORPUS_WORKER_ENABLED": "true",
        "REALYTICS_LEGAL_CORPUS_RUN_ON_STARTUP": "false",
        "REALYTICS_LEGAL_CORPUS_SCHEDULE_SECONDS": "300",
        "REALYTICS_LEGAL_CORPUS_REQUESTED_BY": "legal-sync-worker",
        "REALYTICS_LEGAL_CORPUS_WORKER_ID": "legal-sync-worker:1",
        "REALYTICS_LAW_GO_KR_OC": "secret@example.com",
        "REALYTICS_LEGAL_SYNC_DATABASE_URL": "postgresql://sync",
        "REALYTICS_LEGAL_RAW_OBJECT_STORAGE": "s3",
        "REALYTICS_LEGAL_RAW_BUCKET": "legal-raw",
        "REALYTICS_LEGAL_CORPUS_JOBS_JSON": json.dumps(
            [
                {
                    "resourceKind": "statute",
                    "query": "Housing Act",
                    "requestedScope": "statutes:housing",
                    "mode": "initial",
                    "publicQueryAcknowledged": True,
                }
            ]
        ),
    }


def test_runtime_configuration_requires_explicit_credentials_and_schedule():
    configuration = load_legal_corpus_runtime_configuration(
        _valid_environment()
    )

    assert configuration.worker_enabled is True
    assert configuration.schedule_seconds == 300
    assert configuration.jobs[0].resource_kind == LegalResourceKind.STATUTE

    missing_credential = _valid_environment()
    missing_credential.pop("REALYTICS_LAW_GO_KR_OC")
    with pytest.raises(
        LegalCorpusWorkerConfigurationError,
        match="LAW_GO_KR_OC",
    ):
        load_legal_corpus_runtime_configuration(missing_credential)


def test_one_shot_configuration_does_not_require_schedule():
    environment = _valid_environment()
    environment.pop("REALYTICS_LEGAL_CORPUS_SCHEDULE_SECONDS")

    configuration = load_legal_corpus_runtime_configuration(
        environment,
        require_schedule=False,
    )

    assert configuration.schedule_seconds is None


@pytest.mark.parametrize(
    "run_on_startup,expected_sleeps",
    [(False, [300]), (True, [])],
)
def test_scheduler_honors_run_on_startup(
    run_on_startup,
    expected_sleeps,
):
    reports = []
    sleeps = []

    class Worker:
        def run_once(self):
            report = object()
            reports.append(report)
            return report

    scheduler = LegalCorpusScheduler(
        Worker(),
        interval_seconds=300,
        run_on_startup=run_on_startup,
        sleep=sleeps.append,
    )
    scheduler.run_forever(
        should_stop=lambda: bool(reports),
    )

    assert len(reports) == 1
    assert sleeps == expected_sleeps


@pytest.mark.parametrize(
    "name,value",
    [
        ("REALYTICS_LEGAL_CORPUS_WORKER_ENABLED", "1"),
        ("REALYTICS_LEGAL_CORPUS_RUN_ON_STARTUP", "yes"),
        ("REALYTICS_LEGAL_CORPUS_SCHEDULE_SECONDS", "30"),
    ],
)
def test_runtime_configuration_rejects_ambiguous_or_unsafe_values(
    name,
    value,
):
    environment = _valid_environment()
    environment[name] = value

    with pytest.raises(LegalCorpusWorkerConfigurationError):
        load_legal_corpus_runtime_configuration(environment)


def test_runtime_configuration_requires_public_provider_query_acknowledgment():
    environment = _valid_environment()
    jobs = json.loads(
        environment["REALYTICS_LEGAL_CORPUS_JOBS_JSON"]
    )
    jobs[0]["publicQueryAcknowledged"] = False
    environment["REALYTICS_LEGAL_CORPUS_JOBS_JSON"] = json.dumps(jobs)

    with pytest.raises(
        LegalCorpusWorkerConfigurationError,
        match="invalid job",
    ):
        load_legal_corpus_runtime_configuration(environment)


def test_runtime_status_router_is_operator_only_and_never_returns_secret(
    monkeypatch,
):
    monkeypatch.setattr(
        corpus_router,
        "authorize_request",
        lambda *_: {
            "authenticated": True,
            "role": "admin",
            "user": {"userId": "admin-1"},
        },
    )
    for name, value in _valid_environment().items():
        monkeypatch.setenv(name, value)
    app = FastAPI()
    app.include_router(corpus_router.router)

    response = TestClient(app).get("/ops/legal-corpus/runtime")

    assert response.status_code == 200
    payload = response.json()
    rendered = json.dumps(payload)
    assert payload["configurationStatus"] == "valid"
    assert payload["lawGoKrCredentialConfigured"] is True
    assert "secret@example.com" not in rendered
    assert payload["jobs"][0]["source"]["availability"] == "blocked"
