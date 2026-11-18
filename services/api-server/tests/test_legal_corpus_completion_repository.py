from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from db.legal_sync_database import (
    LegalSyncDatabaseConfigurationError,
    connect_legal_sync_database,
)
from db.repositories.legal_corpus_completion_repository import (
    CompletionCheckpointEvidence,
    CompletionFetchEvidence,
    CompletionRecordEvidence,
    PostgresLegalCorpusCompletionRepository,
    verify_scope_completion_evidence,
)
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from use_cases.legal_corpus_coordinator import (
    LegalCorpusCoordinationError,
    LegalCorpusScopeCompletionAttestation,
)
from use_cases.legal_corpus_sync import _deterministic_id, _stable_hash


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
SCOPE = "full_corpus"
IDENTITY = OfficialCorpusCoverageIdentity(
    provider_key="law-go-kr",
    provider_revision=1,
    registry_version="official-corpus-registry-v1",
    resource_kind="statute",
    target="law",
    query_sha256="1" * 64,
    source_adapter_version="test-source-adapter-v1",
    boundary_contract_version="test-boundary-v1",
    coverage_contract_version="official-corpus-coverage-v1",
)
PROVIDER_ID = IDENTITY.provider_id
SCOPE_HASH = IDENTITY.scope_hash(requested_scope=SCOPE)


def _record(suffix: str) -> CompletionRecordEvidence:
    return CompletionRecordEvidence(
        legal_resource_id=f"resource-{suffix}",
        legal_resource_version_id=f"version-{suffix}",
        normalized_sha256=suffix * 64,
    )


def _evidence():
    first = CompletionCheckpointEvidence(
        checkpoint_sequence=1,
        requested_scope=SCOPE,
        sync_run_id="run-1",
        source_fetch_id="fetch-1",
        checkpoint_before=None,
        checkpoint_after="checkpoint-1",
        page_hash="a" * 64,
        page_record_count=1,
        occurred_at=NOW,
        records=(_record("a"),),
        coverage_identity_sha256=IDENTITY.coverage_identity_sha256,
        coverage_contract_id=IDENTITY.coverage_contract_id,
    )
    second = CompletionCheckpointEvidence(
        checkpoint_sequence=2,
        requested_scope=SCOPE,
        sync_run_id="run-2",
        source_fetch_id="fetch-2",
        checkpoint_before="checkpoint-1",
        checkpoint_after="checkpoint-2",
        page_hash="b" * 64,
        page_record_count=1,
        occurred_at=NOW,
        records=(_record("b"),),
        coverage_identity_sha256=IDENTITY.coverage_identity_sha256,
        coverage_contract_id=IDENTITY.coverage_contract_id,
    )
    fetches = {
        "fetch-1": CompletionFetchEvidence(
            "fetch-1",
            PROVIDER_ID,
            "run-1",
            IDENTITY.coverage_identity_sha256,
        ),
        "fetch-2": CompletionFetchEvidence(
            "fetch-2",
            PROVIDER_ID,
            "run-2",
            IDENTITY.coverage_identity_sha256,
        ),
    }
    return (first, second), fetches


def _attestation(
    checkpoints: tuple[CompletionCheckpointEvidence, ...],
) -> LegalCorpusScopeCompletionAttestation:
    manifest = {
        "providerId": PROVIDER_ID,
        "requestedScope": SCOPE,
        "requestedScopeHash": SCOPE_HASH,
        "coverageIdentity": dict(IDENTITY.as_dict()),
        "coverageMode": "initial",
        "baselineCompletionAttestationId": None,
        "terminalCheckpoint": checkpoints[-1].checkpoint_after,
        "pages": [
            {
                "checkpointSequence": checkpoint.checkpoint_sequence,
                "checkpointBefore": checkpoint.checkpoint_before,
                "checkpointAfter": checkpoint.checkpoint_after,
                "pageHash": checkpoint.page_hash,
                "syncRunId": checkpoint.sync_run_id,
                "sourceFetchId": checkpoint.source_fetch_id,
                "records": [
                    {
                        "legalResourceId": record.legal_resource_id,
                        "legalResourceVersionId": (
                            record.legal_resource_version_id
                        ),
                        "normalizedSha256": record.normalized_sha256,
                    }
                    for record in checkpoint.records
                ],
            }
            for checkpoint in checkpoints
        ],
    }
    return LegalCorpusScopeCompletionAttestation(
        completion_attestation_id=_deterministic_id(
            "legal-corpus-completion",
            {
                "providerId": PROVIDER_ID,
                "requestedScopeHash": SCOPE_HASH,
                "coverageIdentitySha256": (
                    IDENTITY.coverage_identity_sha256
                ),
                "coverageMode": "initial",
                "baselineCompletionAttestationId": None,
                "terminalCheckpoint": (
                    checkpoints[-1].checkpoint_after
                ),
                "manifestSha256": _stable_hash(manifest),
            },
        ),
        provider_id=PROVIDER_ID,
        requested_scope=SCOPE,
        requested_scope_hash=SCOPE_HASH,
        coverage_identity=IDENTITY,
        coverage_mode="initial",
        baseline_completion_attestation_id=None,
        first_checkpoint_sequence=checkpoints[0].checkpoint_sequence,
        last_checkpoint_sequence=checkpoints[-1].checkpoint_sequence,
        terminal_checkpoint=checkpoints[-1].checkpoint_after,
        page_count=len(checkpoints),
        record_count=sum(
            checkpoint.page_record_count for checkpoint in checkpoints
        ),
        resource_count=len(
            {
                record.legal_resource_id
                for checkpoint in checkpoints
                for record in checkpoint.records
            }
        ),
        page_hashes=tuple(
            checkpoint.page_hash for checkpoint in checkpoints
        ),
        manifest_sha256=_stable_hash(manifest),
        started_at=NOW,
        completed_at=NOW,
        attested_by="legal-sync-worker",
    )


def test_completion_rebuilds_exact_ordered_manifest():
    checkpoints, fetches = _evidence()

    verify_scope_completion_evidence(
        _attestation(checkpoints),
        checkpoints=checkpoints,
        fetches=fetches,
    )


def test_completion_rejects_discontinuous_checkpoint_chain():
    checkpoints, fetches = _evidence()
    broken = (
        checkpoints[0],
        replace(checkpoints[1], checkpoint_before="wrong"),
    )

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="discontinuous",
    ):
        verify_scope_completion_evidence(
            _attestation(broken),
            checkpoints=broken,
            fetches=fetches,
        )


def test_completion_rejects_cross_provider_source_fetch():
    checkpoints, fetches = _evidence()
    fetches["fetch-2"] = replace(
        fetches["fetch-2"], provider_id="another-provider"
    )

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="another run",
    ):
        verify_scope_completion_evidence(
            _attestation(checkpoints),
            checkpoints=checkpoints,
            fetches=fetches,
        )


def test_completion_rejects_repeated_version_across_pages():
    checkpoints, fetches = _evidence()
    repeated = (
        checkpoints[0],
        replace(checkpoints[1], records=checkpoints[0].records),
    )

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="multiple pages",
    ):
        verify_scope_completion_evidence(
            _attestation(repeated),
            checkpoints=repeated,
            fetches=fetches,
        )


def test_completion_rejects_manifest_hash_mismatch():
    checkpoints, fetches = _evidence()
    attestation = replace(
        _attestation(checkpoints),
        manifest_sha256="f" * 64,
    )

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="manifest hash",
    ):
        verify_scope_completion_evidence(
            attestation,
            checkpoints=checkpoints,
            fetches=fetches,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_scope_hash", "not-a-hash"),
        ("manifest_sha256", "not-a-hash"),
        ("page_hashes", ("not-a-hash", "b" * 64)),
    ],
)
def test_completion_model_requires_sha256_contracts(field, value):
    checkpoints, _ = _evidence()

    with pytest.raises(ValueError, match="SHA-256"):
        replace(_attestation(checkpoints), **{field: value})


def test_legal_sync_connection_never_falls_back_to_runtime_dsn(
    monkeypatch,
):
    monkeypatch.delenv(
        "REALYTICS_LEGAL_SYNC_DATABASE_URL", raising=False
    )
    monkeypatch.setenv(
        "REALYTICS_DATABASE_URL",
        "postgresql://runtime-should-not-be-used",
    )

    with pytest.raises(
        LegalSyncDatabaseConfigurationError,
        match="REALYTICS_LEGAL_SYNC_DATABASE_URL",
    ):
        connect_legal_sync_database()


def test_completion_repository_accepts_explicit_test_connection_factory():
    marker = object()
    repository = PostgresLegalCorpusCompletionRepository(
        connection_factory=lambda: marker
    )

    assert repository._connect() is marker


def _incremental_attestation(
    *,
    first_sequence: int = 3,
) -> LegalCorpusScopeCompletionAttestation:
    checkpoints, _ = _evidence()
    return replace(
        _attestation(checkpoints),
        completion_attestation_id="completion-incremental",
        coverage_mode="incremental",
        baseline_completion_attestation_id="completion-current-leaf",
        first_checkpoint_sequence=first_sequence,
        last_checkpoint_sequence=first_sequence + 1,
    )


def test_incremental_baseline_accepts_verified_incremental_current_leaf():
    class Cursor:
        def execute(self, statement, params):
            assert "NOT EXISTS" in statement
            assert params == ("completion-current-leaf",)

        def fetchone(self):
            return (
                PROVIDER_ID,
                SCOPE_HASH,
                IDENTITY.coverage_contract_id,
                "incremental",
                2,
                "verified",
            )

    PostgresLegalCorpusCompletionRepository._verify_baseline(
        Cursor(),
        _incremental_attestation(),
    )


def test_incremental_baseline_requires_exact_checkpoint_successor():
    class Cursor:
        def execute(self, _statement, _params):
            return None

        def fetchone(self):
            return (
                PROVIDER_ID,
                SCOPE_HASH,
                IDENTITY.coverage_contract_id,
                "initial",
                1,
                "verified",
            )

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="current leaf",
    ):
        PostgresLegalCorpusCompletionRepository._verify_baseline(
            Cursor(),
            _incremental_attestation(),
        )


class CompletionCursor:
    def __init__(self, attestation, *, replay=False):
        self.attestation = attestation
        self.replay = replay
        self.statement = ""
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, params=None):
        self.statement = statement
        self.params = params
        self.calls.append((statement, params))

    def fetchall(self):
        if "FROM legal_sync_checkpoint_events" in self.statement:
            return [
                (
                    1,
                    SCOPE,
                    "run-1",
                    None,
                    "checkpoint-1",
                    "a" * 64,
                    1,
                    NOW,
                    {
                        "sourceFetchId": "fetch-1",
                        "legalResourceVersionIds": ["version-a"],
                        "coverageIdentity": dict(IDENTITY.as_dict()),
                    },
                    IDENTITY.coverage_contract_id,
                ),
                (
                    2,
                    SCOPE,
                    "run-2",
                    "checkpoint-1",
                    "checkpoint-2",
                    "b" * 64,
                    1,
                    NOW,
                    {
                        "sourceFetchId": "fetch-2",
                        "legalResourceVersionIds": ["version-b"],
                        "coverageIdentity": dict(IDENTITY.as_dict()),
                    },
                    IDENTITY.coverage_contract_id,
                ),
            ]
        if "FROM legal_resource_versions" in self.statement:
            return [
                ("version-a", "resource-a", "a" * 64),
                ("version-b", "resource-b", "b" * 64),
            ]
        if "FROM legal_source_fetches" in self.statement:
            return [
                (
                    "fetch-1",
                    PROVIDER_ID,
                    "run-1",
                    {"coverageIdentity": dict(IDENTITY.as_dict())},
                ),
                (
                    "fetch-2",
                    PROVIDER_ID,
                    "run-2",
                    {"coverageIdentity": dict(IDENTITY.as_dict())},
                ),
            ]
        return []

    def fetchone(self):
        if (
            "INSERT INTO legal_corpus_scope_completion_attestations"
            in self.statement
        ):
            return None if self.replay else (
                self.attestation.completion_attestation_id,
            )
        if (
            "FROM legal_corpus_scope_completion_attestations"
            in self.statement
        ):
            value = self.attestation
            return (
                value.provider_id,
                value.requested_scope,
                value.requested_scope_hash,
                value.first_checkpoint_sequence,
                value.last_checkpoint_sequence,
                value.terminal_checkpoint,
                value.page_count,
                value.record_count,
                value.resource_count,
                list(value.page_hashes),
                value.manifest_sha256,
                value.started_at,
                value.completed_at,
                value.attested_by,
                "verified",
                value.coverage_identity.coverage_contract_id,
                value.coverage_mode,
                value.baseline_completion_attestation_id,
            )
        return None


class CompletionConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.mark.parametrize("replay", [False, True])
def test_postgres_completion_repository_verifies_before_insert(replay):
    checkpoints, _ = _evidence()
    attestation = _attestation(checkpoints)
    cursor = CompletionCursor(attestation, replay=replay)
    connection = CompletionConnection(cursor)
    repository = PostgresLegalCorpusCompletionRepository(
        connection_factory=lambda: connection
    )

    result = repository.attest_scope_completion(attestation)

    assert result == attestation
    assert connection.closed is True
    statements = "\n".join(call[0] for call in cursor.calls)
    assert "set_config('search_path'" in statements
    assert statements.index("FROM legal_sync_checkpoint_events") < (
        statements.index(
            "INSERT INTO legal_corpus_scope_completion_attestations"
        )
    )
    if replay:
        assert (
            "FROM legal_corpus_scope_completion_attestations"
            in statements
        )


@pytest.mark.parametrize(
    "search_path",
    [
        "realytics,extensions,public",
        "realytics;DROP SCHEMA public,pg_temp",
        "public,realytics,pg_temp",
        "realytics,extensions,realytics,pg_temp",
    ],
)
def test_completion_repository_rejects_unsafe_search_path(search_path):
    with pytest.raises(ValueError, match="search_path"):
        PostgresLegalCorpusCompletionRepository(
            connection_factory=lambda: object(),
            search_path=search_path,
        )
