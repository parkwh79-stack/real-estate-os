from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from db.repositories.legal_corpus_repository import (
    PublishedCorpusPage,
    PublishedCorpusVersion,
)
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from use_cases.legal_corpus_coordinator import (
    LegalCorpusBatchCoordinator,
    LegalCorpusCoordinationError,
    OfficialCorpusPage,
    OfficialCorpusPageDiagnostic,
)
from use_cases.legal_corpus_sync import (
    _deterministic_id,
    sanitize_requested_scope,
)


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
QUERY_SHA256 = "1" * 64


def coverage_identity(
    *,
    provider_revision: int = 1,
) -> OfficialCorpusCoverageIdentity:
    return OfficialCorpusCoverageIdentity(
        provider_key="law-go-kr",
        provider_revision=provider_revision,
        registry_version="official-corpus-registry-v1",
        resource_kind="statute",
        target="law",
        query_sha256=QUERY_SHA256,
        source_adapter_version="test-source-adapter-v1",
        boundary_contract_version="test-boundary-v1",
        coverage_contract_version="official-corpus-coverage-v1",
    )


def request(
    *,
    before,
    after,
    page_hash,
    resource_id,
    provider_revision=1,
    requested_scope="full_corpus",
    sync_kind="initial",
):
    return SimpleNamespace(
        provider_key="law-go-kr",
        provider_revision=provider_revision,
        requested_scope=requested_scope,
        requested_by="legal-sync-worker",
        sync_kind=sync_kind,
        checkpoint_before=before,
        checkpoint_after=after,
        page_hash=page_hash,
        resource_id=resource_id,
        coverage_identity=coverage_identity(
            provider_revision=provider_revision
        ),
    )


class FakeSource:
    def __init__(self, pages):
        self.pages = list(pages)
        self.checkpoints = []

    def fetch_page(self, *, checkpoint_before):
        self.checkpoints.append(checkpoint_before)
        return self.pages.pop(0)


class FakeSyncService:
    def __init__(self):
        self.sequence = 0

    def sync_page(self, requests):
        self.sequence += 1
        value = requests[0]
        provider_id = _deterministic_id(
            "legal-provider",
            {
                "providerKey": value.provider_key,
                "providerRevision": value.provider_revision,
            },
        )
        scope_hash = value.coverage_identity.scope_hash(
            requested_scope=sanitize_requested_scope(
                value.requested_scope
            )
        )
        records = tuple(
            PublishedCorpusVersion(
                sync_run_id=f"sync-{self.sequence}",
                source_fetch_id=f"fetch-{self.sequence}",
                legal_resource_id=item.resource_id,
                legal_resource_version_id=(
                    f"version-{item.resource_id}"
                ),
                version_sequence=1,
                supersedes_version_id=None,
                normalized_sha256=(
                    item.resource_id[-1].lower() * 64
                ),
                checkpoint_after=item.checkpoint_after,
                reused_existing_version=False,
            )
            for item in requests
        )
        return PublishedCorpusPage(
            sync_run_id=f"sync-{self.sequence}",
            provider_id=provider_id,
            requested_scope_hash=scope_hash,
            source_fetch_id=f"fetch-{self.sequence}",
            page_hash=value.page_hash,
            checkpoint_before=value.checkpoint_before,
            checkpoint_after=value.checkpoint_after,
            checkpoint_sequence=self.sequence,
            records=records,
        )


class FakeCompletionRepository:
    def __init__(self):
        self.attestations = []

    def attest_scope_completion(self, attestation):
        self.attestations.append(attestation)
        return attestation


def coordinator(source, *, max_pages=10):
    repository = FakeCompletionRepository()
    service = LegalCorpusBatchCoordinator(
        FakeSyncService(),
        repository,
        max_pages=max_pages,
        clock=lambda: NOW,
    )
    return service, repository, source


def test_coordinator_seals_ordered_terminal_page_manifest():
    source = FakeSource(
        [
            OfficialCorpusPage(
                requests=(
                    request(
                        before=None,
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                    ),
                ),
                terminal=False,
            ),
            OfficialCorpusPage(
                requests=(
                    request(
                        before="checkpoint-1",
                        after="checkpoint-2",
                        page_hash="b" * 64,
                        resource_id="resource-b",
                    ),
                ),
                terminal=True,
            ),
        ]
    )
    service, repository, _ = coordinator(source)

    attestation = service.run(source)

    assert attestation.page_count == 2
    assert attestation.record_count == 2
    assert attestation.resource_count == 2
    assert attestation.first_checkpoint_sequence == 1
    assert attestation.last_checkpoint_sequence == 2
    assert attestation.terminal_checkpoint == "checkpoint-2"
    assert attestation.page_hashes == ("a" * 64, "b" * 64)
    assert repository.attestations == [attestation]
    assert source.checkpoints == [None, "checkpoint-1"]


def test_coordinator_refuses_to_certify_partial_provider_page():
    source = FakeSource(
        [
            OfficialCorpusPage(
                requests=(
                    request(
                        before=None,
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                    ),
                ),
                terminal=True,
                partial=True,
                diagnostics=(
                    OfficialCorpusPageDiagnostic(
                        code="DETAIL_FETCH_FAILED",
                        message="one record could not be normalized",
                        retryable=True,
                    ),
                ),
            )
        ]
    )
    service, repository, _ = coordinator(source)

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="partial",
    ) as raised:
        service.run(source)

    assert repository.attestations == []
    assert raised.value.partial is True
    assert raised.value.retryable is True
    assert raised.value.completed_page_count == 0
    assert (
        raised.value.diagnostics[0].code
        == "DETAIL_FETCH_FAILED"
    )


def test_coordinator_rejects_provider_checkpoint_discontinuity():
    source = FakeSource(
        [
            OfficialCorpusPage(
                requests=(
                    request(
                        before="wrong-checkpoint",
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                    ),
                ),
                terminal=True,
            )
        ]
    )
    service, _, _ = coordinator(source)

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="checkpoint_before",
    ):
        service.run(source)


def test_coordinator_rejects_repeated_page_hash():
    source = FakeSource(
        [
            OfficialCorpusPage(
                requests=(
                    request(
                        before=None,
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                    ),
                ),
                terminal=False,
            ),
            OfficialCorpusPage(
                requests=(
                    request(
                        before="checkpoint-1",
                        after="checkpoint-2",
                        page_hash="a" * 64,
                        resource_id="resource-b",
                    ),
                ),
                terminal=True,
            ),
        ]
    )
    service, _, _ = coordinator(source)

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="repeated a page hash",
    ):
        service.run(source)


def test_coordinator_rejects_provider_revision_change():
    source = FakeSource(
        [
            OfficialCorpusPage(
                requests=(
                    request(
                        before=None,
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                    ),
                ),
                terminal=False,
            ),
            OfficialCorpusPage(
                requests=(
                    request(
                        before="checkpoint-1",
                        after="checkpoint-2",
                        page_hash="b" * 64,
                        resource_id="resource-b",
                        provider_revision=2,
                    ),
                ),
                terminal=True,
            ),
        ]
    )
    service, _, _ = coordinator(source)

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="must remain constant",
    ):
        service.run(source)


def test_coordinator_requires_terminal_page_within_limit():
    source = FakeSource(
        [
            OfficialCorpusPage(
                requests=(
                    request(
                        before=None,
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                    ),
                ),
                terminal=False,
            )
        ]
    )
    service, _, _ = coordinator(source, max_pages=1)

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="terminal page",
    ):
        service.run(source)


def test_coordinator_attests_the_redacted_scope_used_by_sync():
    source = FakeSource(
        [
            OfficialCorpusPage(
                requests=(
                    request(
                        before=None,
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                        requested_scope="full_corpus token=secret",
                    ),
                ),
                terminal=True,
            )
        ]
    )
    service, _, _ = coordinator(source)

    attestation = service.run(source)

    assert attestation.requested_scope == "full_corpus token=***"
    assert "secret" not in attestation.requested_scope


def test_coordinator_wraps_later_source_failure_with_resume_progress():
    class FailingSecondPageSource:
        calls = 0

        def fetch_page(self, *, checkpoint_before):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError("provider timeout")
            return OfficialCorpusPage(
                requests=(
                    request(
                        before=checkpoint_before,
                        after="checkpoint-1",
                        page_hash="a" * 64,
                        resource_id="resource-a",
                    ),
                ),
                terminal=False,
            )

    source = FailingSecondPageSource()
    service, repository, _ = coordinator(source)

    with pytest.raises(
        LegalCorpusCoordinationError,
        match="fetch failed",
    ) as raised:
        service.run(source)

    assert raised.value.partial is True
    assert raised.value.completed_page_count == 1
    assert raised.value.last_checkpoint == "checkpoint-1"
    assert repository.attestations == []
