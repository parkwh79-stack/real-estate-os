from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

import db.repositories.legal_corpus_repository as corpus_repository_module
from db.repositories.legal_corpus_repository import (
    CorpusPublication,
    InMemoryLegalCorpusSyncRepository,
    LegalCorpusCheckpointConflict,
    LegalCorpusEffectiveTimeConflict,
    LegalCorpusImmutableConflict,
    LegalCorpusLeaseConflict,
    LegalSyncLease,
    LegalSourceProviderRecord,
    LegalSyncRunRecord,
    PublishedCorpusPage,
    PublishedCorpusVersion,
    SyncRunState,
)
from use_cases.legal_corpus_sync import (
    LegalCorpusSyncRequest,
    LegalCorpusSyncService,
    OfficialLegalRecordInput,
    OfficialProvisionInput,
    OfficialSourceVerification,
    RawObjectIntegrityError,
    RawObjectReceipt,
    canonical_normalized_record,
    sanitize_source_locator,
)


NOW = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)


class FakeImmutableObjectStore:
    def __init__(
        self, trace: list[str], *, corrupt_receipt: bool = False
    ) -> None:
        self.trace = trace
        self.corrupt_receipt = corrupt_receipt
        self.objects: dict[str, RawObjectReceipt] = {}
        self.put_calls = 0
        self.last_metadata: dict[str, str] = {}

    def put_if_absent(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
        expected_sha256: str,
        metadata: dict[str, str],
    ) -> RawObjectReceipt:
        self.trace.append("object.put")
        self.put_calls += 1
        self.last_metadata = dict(metadata)
        receipt = self.objects.get(object_key)
        if receipt is None:
            receipt = RawObjectReceipt(
                object_key=object_key,
                object_version="immutable-v1",
                sha256=expected_sha256,
                size_bytes=len(content),
            )
            self.objects[object_key] = receipt
        if self.corrupt_receipt:
            return replace(receipt, sha256="0" * 64)
        return receipt


class FakeCorpusRepository(InMemoryLegalCorpusSyncRepository):
    """Transactional behavior model used without PostgreSQL/network access."""

    def __init__(
        self,
        store: FakeImmutableObjectStore,
        trace: list[str],
        *,
        fail_publish: bool = False,
    ) -> None:
        super().__init__(clock=lambda: NOW)
        self.store = store
        self.trace = trace
        self.fail_publish = fail_publish
        self.publications: list[CorpusPublication] = []

    def begin_sync(self, run: LegalSyncRunRecord) -> SyncRunState:
        self.trace.append("db.begin")
        return super().begin_sync(run)

    def renew_lease(
        self,
        lease: LegalSyncLease,
        *,
        occurred_at: datetime,
        lease_ttl_seconds: int,
    ) -> LegalSyncLease:
        return super().renew_lease(
            lease,
            occurred_at=occurred_at,
            lease_ttl_seconds=lease_ttl_seconds,
        )

    def publish_page(
        self, publication: CorpusPublication
    ) -> PublishedCorpusPage:
        self.trace.append("db.publish")
        assert publication.fetch.raw_object_key in self.store.objects
        assert (
            self.store.objects[publication.fetch.raw_object_key].sha256
            == publication.fetch.raw_sha256
        )
        if self.fail_publish:
            raise RuntimeError("simulated transaction rollback")
        result = super().publish_page(publication)
        self.publications.append(publication)
        return result

    def mark_failed(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        error_code: str,
        occurred_at: datetime,
    ) -> None:
        super().mark_failed(
            sync_run_id=sync_run_id,
            provider_id=provider_id,
            error_code=error_code,
            occurred_at=occurred_at,
        )


class ProviderLineageCursor:
    def __init__(
        self,
        *,
        previous: tuple[str, int] | None,
        current: tuple[str, int] | None,
        existing: tuple[Any, ...] | None = None,
    ) -> None:
        self.previous = previous
        self.current = current
        self.existing = existing
        self._next: tuple[Any, ...] | None = None
        self.insert_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        if sql == corpus_repository_module._SELECT_PREVIOUS_PROVIDER_SQL:
            self._next = self.previous
        elif sql == corpus_repository_module._SELECT_PROVIDER_SQL:
            self._next = self.existing
        elif sql == corpus_repository_module._SELECT_CURRENT_PROVIDER_REVISION_SQL:
            self._next = self.current
        elif sql == corpus_repository_module._INSERT_PROVIDER_SQL:
            self.insert_params = tuple(params)
            self._next = (str(params[0]),)
        else:  # pragma: no cover - protects the scripted SQL contract
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._next


def _request() -> LegalCorpusSyncRequest:
    child = OfficialProvisionInput(
        provision_type="paragraph",
        provision_identifier="article-1-paragraph-1",
        parent_provision_identifier="article-1",
        display_path="제1조제1항",
        ordinal_path=(1, 1),
        heading_text=None,
        body_text="국토의 계획 및 이용에 관하여 필요한 사항을 정한다.",
        source_locator=(
            "https://law.go.kr/법령/국토계획법/제1조?"
            "token=do-not-store&view=body"
        ),
        metadata={"apiKey": "provision-secret", "language": "ko"},
    )
    parent = OfficialProvisionInput(
        provision_type="article",
        provision_identifier="article-1",
        display_path="제1조",
        ordinal_path=(1,),
        heading_text="목적",
        body_text="이 법은 국토의 이용과 개발을 위한 사항을 정한다.",
        source_locator=(
            "https://law.go.kr/법령/국토계획법/제1조?"
            "OC=do-not-store&view=body"
        ),
    )
    record = OfficialLegalRecordInput(
        resource_type="statute",
        official_identifier="법률-00001",
        official_version_identifier="법률-00001-20260718",
        canonical_title="국토의 계획 및 이용에 관한 법률",
        issuing_authority="대한민국 국회",
        provisions=(child, parent),  # child-first input exercises topo ordering.
        alternative_titles=("국토계획법",),
        promulgation_date=date(2026, 7, 1),
        effective_from=date(2026, 7, 18),
        resource_metadata={"clientSecret": "never-persist", "locale": "ko-KR"},
        version_metadata={"normalizer": "legal-xml-v1"},
    )
    raw_content = b'{"official":"payload","id":"00001"}'
    source_authority = "KOREA_MINISTRY_OF_GOVERNMENT_LEGISLATION"
    return LegalCorpusSyncRequest(
        provider_key="law-go-kr",
        provider_revision=1,
        source_authority=source_authority,
        authority_tier="official_primary",
        provider_kind="official_api",
        adapter_version="law-go-kr-adapter-v1",
        transport="direct_api",
        endpoint_key="law.detail",
        source_locator=(
            "https://www.law.go.kr/DRF/lawService.do?"
            "OC=do-not-store&target=law&token=also-secret"
        ),
        raw_content=raw_content,
        raw_content_type="application/json",
        record=record,
        source_verification=OfficialSourceVerification(
            response_sha256=hashlib.sha256(raw_content).hexdigest(),
            source_authority=source_authority,
            official_identifier=record.official_identifier,
            parser_schema_version=record.parser_schema_version,
            verification_basis="official_api_response",
            verifier_id="law-go-kr-json-normalizer",
            verifier_version="1.0.0",
        ),
        requested_scope="statute:법률-00001",
        requested_by="legal-sync-worker",
        request_identity={"OC": "do-not-store", "target": "law", "ID": "00001"},
        canonical_base_url="https://www.law.go.kr",
        checkpoint_before="cursor=10&token=secret",
        checkpoint_after="cursor=11&token=secret",
        provider_metadata={"apiKey": "provider-secret", "region": "KR"},
        fetch_metadata={"authorization": "Bearer secret", "mode": "detail"},
    )


def _page_requests() -> tuple[
    LegalCorpusSyncRequest, LegalCorpusSyncRequest
]:
    first = _request()
    second_record = replace(
        first.record,
        official_identifier="LAW-00002",
        canonical_official_identifier="LAW-00002",
        official_version_identifier="LAW-00002-20260718",
        canonical_title="Second official legal resource",
        provisions=tuple(
            replace(
                provision,
                body_text=provision.body_text + " second-resource",
            )
            for provision in first.record.provisions
        ),
    )
    second = replace(
        first,
        record=second_record,
        source_verification=replace(
            first.source_verification,
            official_identifier=second_record.official_identifier,
        ),
    )
    return first, second


@pytest.mark.parametrize(
    ("source_authority", "authority_tier"),
    (
        ("tenant_document", "tenant_supplied"),
        ("CALLER_SUPPLIED", "official_primary"),
        ("professional_database", "verified_professional"),
    ),
)
def test_global_corpus_rejects_tenant_caller_and_professional_authorities(
    source_authority,
    authority_tier,
):
    baseline = _request()
    with pytest.raises(ValueError, match="global legal corpus|tenant/caller"):
        replace(
            baseline,
            source_authority=source_authority,
            authority_tier=authority_tier,
        )


def _service(
    *,
    corrupt_receipt: bool = False,
    fail_publish: bool = False,
) -> tuple[
    LegalCorpusSyncService,
    FakeCorpusRepository,
    FakeImmutableObjectStore,
    list[str],
]:
    trace: list[str] = []
    store = FakeImmutableObjectStore(
        trace, corrupt_receipt=corrupt_receipt
    )
    repository = FakeCorpusRepository(
        store, trace, fail_publish=fail_publish
    )
    service = LegalCorpusSyncService(
        repository, store, clock=lambda: NOW
    )
    return service, repository, store, trace


def test_identical_replay_returns_existing_immutable_identities() -> None:
    service, repository, store, trace = _service()
    request = _request()

    first = service.sync(request)
    second = service.sync(request)

    assert first.legal_resource_id == second.legal_resource_id
    assert (
        first.legal_resource_version_id
        == second.legal_resource_version_id
    )
    assert first.source_fetch_id == second.source_fetch_id
    assert first.version_sequence == second.version_sequence == 1
    assert second.run_replayed is True
    assert store.put_calls == 1
    assert trace.index("object.put") < trace.index("db.publish")
    assert repository.events[first.sync_run_id] == [
        "pending",
        "running",
        "completed",
    ]
    assert first.normalized_sha256 == hashlib.sha256(
        _canonical_json(canonical_normalized_record(request.record))
    ).hexdigest()


def test_non_2xx_response_is_rejected_before_ledger_or_object_write() -> None:
    service, repository, store, trace = _service()

    with pytest.raises(
        RuntimeError, match="requires a 2xx source response"
    ):
        service.sync(replace(_request(), response_status=404))

    assert repository.runs == {}
    assert repository.publications == []
    assert store.objects == {}
    assert trace == []


def test_source_verification_hash_mismatch_is_rejected_before_publish() -> None:
    service, repository, store, trace = _service()
    request = _request()
    request = replace(
        request,
        source_verification=replace(
            request.source_verification,
            response_sha256="0" * 64,
        ),
    )

    with pytest.raises(
        RuntimeError, match="response_sha256"
    ):
        service.sync(request)

    assert repository.runs == {}
    assert repository.publications == []
    assert store.objects == {}
    assert trace == []


def test_repository_boundary_rechecks_status_and_contract_hash() -> None:
    service, repository, _store, _trace = _service()
    service.sync(_request())
    publication = repository.publications[0]

    verified = (
        corpus_repository_module._validated_publication_verification(
            publication
        )
    )
    assert verified[0]["schemaValidationStatus"] == "passed"

    with pytest.raises(RuntimeError, match="2xx"):
        corpus_repository_module._validated_publication_verification(
            replace(
                publication,
                fetch=replace(
                    publication.fetch, response_status=503
                ),
            )
        )

    metadata = dict(publication.fetch.metadata)
    metadata["sourceVerification"] = {
        **metadata["sourceVerification"],
        "contractSha256": "0" * 64,
    }
    metadata["sourceVerifications"] = [
        metadata["sourceVerification"]
    ]
    with pytest.raises(RuntimeError, match="contract hash"):
        corpus_repository_module._validated_publication_verification(
            replace(
                publication,
                fetch=replace(publication.fetch, metadata=metadata),
            )
        )


def test_changed_normalized_content_appends_monotonic_superseding_version() -> None:
    service, repository, store, _trace = _service()
    first_request = _request()
    first = service.sync(first_request)

    changed_parent = replace(
        first_request.record.provisions[1],
        body_text="개정된 공식 본문으로 목적과 적용 범위를 정한다.",
    )
    changed_record = replace(
        first_request.record,
        provisions=(
            first_request.record.provisions[0],
            changed_parent,
        ),
    )
    # Same official bytes can be normalized differently after an adapter/parser
    # correction; normalized identity, not raw-object identity, drives versions.
    second = service.sync(
        replace(
            first_request,
            record=changed_record,
            checkpoint_before="cursor=11&token=secret",
            checkpoint_after="cursor=12&token=secret",
        )
    )

    assert first.legal_resource_id == second.legal_resource_id
    assert (
        first.legal_resource_version_id
        != second.legal_resource_version_id
    )
    assert first.version_sequence == 1
    assert second.version_sequence == 2
    assert (
        second.supersedes_version_id
        == first.legal_resource_version_id
    )
    assert second.normalized_sha256 != first.normalized_sha256
    assert (
        second.effective_predecessor_version_id
        == first.legal_resource_version_id
    )
    assert second.effective_lineage_type == "source_correction"
    assert (
        second.effective_continuity_status
        == "same_window_correction"
    )
    assert store.put_calls == 2
    assert len(store.objects) == 1  # content-addressed put-if-absent
    assert len(repository.resource_versions[first.legal_resource_id]) == 2


def test_provider_revision_and_alias_reuse_canonical_resource_version() -> None:
    service, repository, _store, _trace = _service()
    request = _request()
    first = service.sync(request)
    provider_alias = f"{request.record.official_identifier}-provider-v2"
    aliased_record = replace(
        request.record,
        official_identifier=provider_alias,
        canonical_official_identifier=request.record.official_identifier,
    )
    second = service.sync(
        replace(
            request,
            provider_revision=2,
            adapter_version="law-go-kr-adapter-v2",
            record=aliased_record,
            source_verification=replace(
                request.source_verification,
                official_identifier=provider_alias,
            ),
        )
    )

    assert second.legal_resource_id == first.legal_resource_id
    assert (
        second.legal_resource_version_id
        == first.legal_resource_version_id
    )
    assert second.reused_existing_version is True
    assert len(repository.resource_versions[first.legal_resource_id]) == 1
    assert len(repository.crosswalks) == 2
    assert {
        item.provider_official_identifier
        for item in repository.crosswalks.values()
    } == {request.record.official_identifier, provider_alias}
    assert {
        item.cross_revision_key
        for item in repository.crosswalks.values()
    } == {repository.resources[first.legal_resource_id].cross_revision_key}


def test_open_ended_effective_predecessor_is_closed_by_lineage_edge() -> None:
    service, _repository, _store, _trace = _service()
    request = _request()
    first = service.sync(request)
    successor_record = replace(
        request.record,
        official_version_identifier="official-version-2",
        predecessor_official_version_identifier=(
            request.record.official_version_identifier
        ),
        effective_from=date(2026, 8, 1),
    )
    second = service.sync(
        replace(
                request,
                record=successor_record,
                checkpoint_before=request.checkpoint_after,
                checkpoint_after="cursor=12&token=secret",
        )
    )

    assert second.version_sequence == 2
    assert (
        second.effective_predecessor_version_id
        == first.legal_resource_version_id
    )
    assert second.effective_lineage_type == "effective_successor"
    assert second.effective_continuity_status == "open_end_closed"


@pytest.mark.parametrize(
    ("record_changes", "expected_code"),
    (
        (
            {
                "official_version_identifier": "official-version-2",
                "effective_from": date(2026, 8, 1),
            },
            "missing_predecessor",
        ),
        (
            {
                "official_version_identifier": "official-version-2",
                "predecessor_official_version_identifier": (
                    "wrong-official-version"
                ),
                "effective_from": date(2026, 8, 1),
            },
            "predecessor_mismatch",
        ),
        (
            {
                "official_version_identifier": "official-version-2",
                "predecessor_official_version_identifier": (
                    "__CURRENT__"
                ),
                "effective_from": date(2026, 7, 18),
            },
            "effective_overlap",
        ),
    ),
)
def test_effective_successor_conflicts_fail_closed(
    record_changes: dict[str, Any],
    expected_code: str,
) -> None:
    service, repository, _store, _trace = _service()
    request = _request()
    service.sync(request)
    if (
        record_changes.get(
            "predecessor_official_version_identifier"
        )
        == "__CURRENT__"
    ):
        record_changes = {
            **record_changes,
            "predecessor_official_version_identifier": (
                request.record.official_version_identifier
            ),
        }
    successor = replace(request.record, **record_changes)

    with pytest.raises(LegalCorpusEffectiveTimeConflict) as conflict:
        service.sync(
            replace(
                    request,
                    record=successor,
                    checkpoint_before=request.checkpoint_after,
                    checkpoint_after="cursor=12&token=secret",
            )
        )

    assert conflict.value.code == expected_code
    assert repository.failed_codes[-1] == (
        f"EFFECTIVE_TIME_{expected_code.upper()}"
    )


def test_effective_gap_requires_an_explicit_documented_policy() -> None:
    service, _repository, _store, _trace = _service()
    request = _request()
    bounded_record = replace(
        request.record,
        effective_to=date(2026, 7, 31),
    )
    service.sync(replace(request, record=bounded_record))
    successor = replace(
        bounded_record,
        official_version_identifier="official-version-2",
        predecessor_official_version_identifier=(
            bounded_record.official_version_identifier
        ),
        effective_from=date(2026, 8, 2),
        effective_to=None,
    )

    with pytest.raises(LegalCorpusEffectiveTimeConflict) as conflict:
        service.sync(
            replace(
                    request,
                    record=successor,
                    checkpoint_before=request.checkpoint_after,
                    checkpoint_after="cursor=12&token=secret",
            )
        )
    assert conflict.value.code == "effective_gap"

    documented_service, _repo, _store, _trace = _service()
    documented_service.sync(replace(request, record=bounded_record))
    documented = documented_service.sync(
        replace(
            request,
            record=replace(
                successor,
                effective_continuity_policy="allow_documented_gap",
                effective_gap_reason=(
                    "official publication records a one-day hiatus"
                ),
            ),
            checkpoint_before=request.checkpoint_after,
            checkpoint_after="cursor=12&token=secret",
        )
    )
    assert documented.effective_continuity_status == "documented_gap"


def test_new_fetch_with_identical_normalized_content_reuses_version() -> None:
    service, repository, store, _trace = _service()
    request = _request()
    first = service.sync(request)
    later_request = replace(
        request,
        checkpoint_before="cursor=11&token=secret",
        checkpoint_after="cursor=12&token=secret",
    )

    later = service.sync(later_request)
    replay = service.sync(later_request)

    assert later.sync_run_id != first.sync_run_id
    assert later.source_fetch_id != first.source_fetch_id
    assert (
        later.legal_resource_version_id
        == first.legal_resource_version_id
    )
    assert later.version_sequence == 1
    assert later.reused_existing_version is True
    assert replay.reused_existing_version is True
    assert replay.run_replayed is True
    assert len(repository.resource_versions[first.legal_resource_id]) == 1
    assert len(store.objects) == 1


def test_new_poll_time_creates_fresh_fetch_but_reuses_legal_version() -> None:
    trace: list[str] = []
    store = FakeImmutableObjectStore(trace)
    repository = FakeCorpusRepository(store, trace)
    clock_values = iter(
        (
            NOW,
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=2),
            NOW + timedelta(minutes=10),
            NOW + timedelta(minutes=10, seconds=1),
            NOW + timedelta(minutes=10, seconds=2),
        )
    )
    service = LegalCorpusSyncService(
        repository, store, clock=lambda: next(clock_values)
    )
    request = _request()

    first = service.sync(request)
    second = service.sync(
        replace(
            request,
            checkpoint_before="cursor=11&token=secret",
            checkpoint_after="cursor=12&token=secret",
        )
    )

    assert second.sync_run_id != first.sync_run_id
    assert second.source_fetch_id != first.source_fetch_id
    assert (
        second.legal_resource_version_id
        == first.legal_resource_version_id
    )
    assert second.reused_existing_version is True
    assert repository.publications[1].fetch.fetched_at == (
        NOW + timedelta(minutes=10)
    )


def test_failed_identical_run_can_resume_append_only_and_complete() -> None:
    service, repository, store, _trace = _service(
        corrupt_receipt=True
    )
    # The adapter preserves one acquisition timestamp across retries.
    request = replace(
        _request(), source_fetched_at=NOW - timedelta(seconds=1)
    )
    with pytest.raises(RawObjectIntegrityError):
        service.sync(request)
    run_id = next(iter(repository.runs))

    store.corrupt_receipt = False
    completed = service.sync(request)

    assert completed.sync_run_id == run_id
    assert repository.events[run_id] == [
        "pending",
        "running",
        "failed",
        "running",
        "completed",
    ]
    assert (
        len(repository.resource_versions[completed.legal_resource_id])
        == 1
    )


def test_object_receipt_failure_appends_failed_event_without_db_publish() -> None:
    service, repository, _store, trace = _service(
        corrupt_receipt=True
    )

    with pytest.raises(RawObjectIntegrityError):
        service.sync(_request())

    run_id = next(iter(repository.runs))
    assert repository.events[run_id] == ["pending", "running", "failed"]
    assert repository.failed_codes == ["RAW_OBJECT_INTEGRITY_FAILED"]
    assert repository.versions == {}
    assert repository.publications == []
    assert "db.publish" not in trace


def test_database_failure_rolls_back_version_and_records_safe_failure() -> None:
    service, repository, store, trace = _service(fail_publish=True)

    with pytest.raises(RuntimeError, match="simulated transaction rollback"):
        service.sync(_request())

    run_id = next(iter(repository.runs))
    assert repository.events[run_id] == ["pending", "running", "failed"]
    assert repository.failed_codes == ["LEGAL_CORPUS_PUBLISH_FAILED"]
    assert repository.versions == {}
    assert repository.publications == []
    assert len(store.objects) == 1
    assert trace.index("object.put") < trace.index("db.publish")


def test_source_locators_metadata_and_checkpoints_are_secret_redacted() -> None:
    service, repository, store, _trace = _service()
    result = service.sync(_request())
    publication = repository.publications[0]

    assert "do-not-store" not in publication.fetch.sanitized_source_locator
    assert "also-secret" not in publication.fetch.sanitized_source_locator
    assert "target=law" in publication.fetch.sanitized_source_locator
    assert publication.fetch.metadata["authorization"] == "***"
    assert (
        publication.resource.metadata["clientSecret"] == "***"
    )
    assert publication.checkpoint_after == "cursor=11&token=***"
    assert result.checkpoint_after == "cursor=11&token=***"
    assert all(
        "do-not-store" not in provision.source_locator
        for provision in publication.provisions
    )
    assert publication.provisions[0].provision_identifier == "article-1"
    assert (
        publication.provisions[1].parent_provision_id
        == publication.provisions[0].legal_provision_id
    )
    assert set(store.last_metadata) == {
        "provider-id",
        "sync-run-id",
        "sha256",
    }
    assert publication.version.effective_from == date(2026, 7, 18)
    assert publication.version.valid_time_start == date(2026, 7, 18)
    assert publication.version.valid_time_end is None
    assert publication.version.observed_at == NOW
    assert publication.version.fetched_at == NOW
    assert publication.version.verified_at == NOW


def test_nfc_trim_is_shared_by_hash_ids_and_materialized_provisions() -> None:
    service, repository, _store, _trace = _service()
    base = _request()
    official_id = "가-법률-1"
    decomposed_id = f"  {unicodedata.normalize('NFD', official_id)}  "
    provision_id = "제1조"
    decomposed_provision_id = (
        f"  {unicodedata.normalize('NFD', provision_id)}  "
    )
    first_parent = replace(
        base.record.provisions[1],
        provision_identifier=decomposed_provision_id,
    )
    first_child = replace(
        base.record.provisions[0],
        parent_provision_identifier=decomposed_provision_id,
    )
    first_record = replace(
        base.record,
        official_identifier=decomposed_id,
        canonical_official_identifier=decomposed_id,
        provisions=(first_child, first_parent),
    )
    first_request = replace(
        base,
        record=first_record,
        source_verification=replace(
            base.source_verification,
            official_identifier=decomposed_id,
        ),
    )
    first = service.sync(first_request)

    second_parent = replace(
        base.record.provisions[1],
        provision_identifier=provision_id,
    )
    second_child = replace(
        base.record.provisions[0],
        parent_provision_identifier=provision_id,
    )
    second_record = replace(
        base.record,
        official_identifier=official_id,
        canonical_official_identifier=official_id,
        provisions=(second_child, second_parent),
    )
    second_request = replace(
        base,
        record=second_record,
        source_verification=replace(
            base.source_verification,
            official_identifier=official_id,
        ),
        checkpoint_before="cursor=11&token=secret",
        checkpoint_after="cursor=12&token=secret",
    )
    second = service.sync(second_request)

    assert first.legal_resource_id == second.legal_resource_id
    assert (
        first.legal_resource_version_id
        == second.legal_resource_version_id
    )
    assert repository.publications[0].resource.official_identifier == official_id
    assert (
        repository.publications[0].provisions[0].provision_identifier
        == provision_id
    )
    assert [
        item.legal_provision_id
        for item in repository.publications[0].provisions
    ] == [
        item.legal_provision_id
        for item in repository.publications[1].provisions
    ]


def test_fetch_verify_and_complete_times_follow_actual_causal_events() -> None:
    trace: list[str] = []
    store = FakeImmutableObjectStore(trace)
    repository = FakeCorpusRepository(store, trace)
    requested_at = NOW
    verified_at = NOW + timedelta(seconds=5)
    completed_at = NOW + timedelta(seconds=8)
    clock_values = iter((requested_at, verified_at, completed_at))
    service = LegalCorpusSyncService(
        repository, store, clock=lambda: next(clock_values)
    )
    source_fetched_at = NOW - timedelta(seconds=2)

    service.sync(
        replace(_request(), source_fetched_at=source_fetched_at)
    )

    run = next(iter(repository.runs.values()))
    publication = repository.publications[0]
    assert run.requested_at == requested_at
    assert publication.fetch.fetched_at == source_fetched_at
    assert publication.version.fetched_at == source_fetched_at
    assert publication.version.verified_at == verified_at
    assert publication.completed_at == completed_at


def test_two_record_page_commits_one_fetch_and_one_checkpoint() -> None:
    service, repository, store, _trace = _service()

    page = service.sync_page(_page_requests())

    assert len(page.records) == 2
    assert len(repository.fetches) == 1
    assert len(repository.resources) == 2
    assert len(repository.crosswalks) == 2
    assert len(repository.versions) == 2
    assert store.put_calls == 1
    checkpoint_events = next(iter(repository.checkpoint_events.values()))
    assert len(checkpoint_events) == 1
    assert checkpoint_events[0]["pageRecordCount"] == 2
    lease_events = next(iter(repository.lease_events.values()))
    assert [item["eventType"] for item in lease_events] == [
        "acquired",
        "renewed",
        "released",
    ]
    assert repository.events[page.sync_run_id] == [
        "pending",
        "running",
        "completed",
    ]


def test_completed_page_replay_is_idempotent() -> None:
    service, repository, store, _trace = _service()
    requests = _page_requests()

    first = service.sync_page(requests)
    replay = service.sync_page(requests)

    assert replay.run_replayed is True
    assert replay.sync_run_id == first.sync_run_id
    assert replay.source_fetch_id == first.source_fetch_id
    assert replay.page_hash == first.page_hash
    assert replay.checkpoint_sequence == first.checkpoint_sequence == 1
    assert all(item.run_replayed for item in replay.records)
    assert len(repository.fetches) == 1
    assert len(repository.checkpoint_events) == 1
    assert len(repository.publications) == 1
    assert store.put_calls == 1


def test_completed_page_replay_is_order_insensitive_but_returns_call_order() -> None:
    service, repository, store, _trace = _service()
    requests = _page_requests()
    first = service.sync_page(requests)

    reversed_page = service.sync_page(tuple(reversed(requests)))

    assert reversed_page.sync_run_id == first.sync_run_id
    assert reversed_page.page_hash == first.page_hash
    assert [
        item.legal_resource_id for item in reversed_page.records
    ] == [
        item.legal_resource_id for item in reversed(first.records)
    ]
    assert reversed_page.run_replayed is True
    assert len(repository.fetches) == 1
    assert store.put_calls == 1


def test_stale_checkpoint_is_rejected_before_a_second_object_write() -> None:
    service, repository, store, _trace = _service()
    request = _request()
    service.sync(request)

    with pytest.raises(
        LegalCorpusCheckpointConflict, match="checkpoint_before is stale"
    ):
        service.sync(
            replace(
                request,
                checkpoint_before="cursor=10&token=secret",
                checkpoint_after="cursor=12&token=secret",
            )
        )

    assert len(repository.fetches) == 1
    assert len(repository.checkpoint_events) == 1
    assert store.put_calls == 1


def _template_run_and_publication() -> tuple[
    LegalSyncRunRecord, CorpusPublication
]:
    service, repository, _store, _trace = _service()
    result = service.sync_page(_page_requests())
    return (
        repository.runs[result.sync_run_id],
        repository.publications[0],
    )


def test_scope_lease_rejects_a_second_active_worker() -> None:
    run, _publication = _template_run_and_publication()
    repository = InMemoryLegalCorpusSyncRepository(clock=lambda: NOW)
    first_run = replace(run, lease_ttl_seconds=30, requested_at=NOW)
    repository.begin_sync(first_run)
    competing = replace(
        first_run,
        sync_run_id="legal-sync-run:competing",
        idempotency_key="competing",
        input_hash="b" * 64,
        lease_owner_id="worker-b",
    )

    with pytest.raises(LegalCorpusLeaseConflict, match="another"):
        repository.begin_sync(competing)

    lease_events = next(iter(repository.lease_events.values()))
    assert len(lease_events) == 1
    assert lease_events[0]["lease"].fencing_token == 1


def test_expired_lease_is_fenced_and_old_worker_cannot_publish() -> None:
    run, publication = _template_run_and_publication()
    now = {"value": NOW}
    repository = InMemoryLegalCorpusSyncRepository(
        clock=lambda: now["value"]
    )
    first_run = replace(run, lease_ttl_seconds=30, requested_at=NOW)
    first = repository.begin_sync(first_run)
    assert first.lease is not None
    now["value"] = NOW + timedelta(seconds=31)
    competing = replace(
        first_run,
        sync_run_id="legal-sync-run:fence-2",
        idempotency_key="fence-2",
        input_hash="c" * 64,
        lease_owner_id="worker-b",
        requested_at=now["value"],
    )
    second = repository.begin_sync(competing)
    assert second.lease is not None
    assert second.lease.fencing_token == first.lease.fencing_token + 1

    with pytest.raises(LegalCorpusLeaseConflict, match="fenced"):
        repository.publish_page(
            replace(publication, lease=first.lease)
        )

    assert repository.fetches == {}
    assert repository.versions == {}
    assert repository.checkpoint_events == {}


def test_lease_renewal_extends_expiry_and_allows_page_commit() -> None:
    run, publication = _template_run_and_publication()
    now = {"value": NOW}
    repository = InMemoryLegalCorpusSyncRepository(
        clock=lambda: now["value"]
    )
    run = replace(run, lease_ttl_seconds=30, requested_at=NOW)
    started = repository.begin_sync(run)
    assert started.lease is not None
    original_expiry = started.lease.expires_at
    now["value"] = NOW + timedelta(seconds=20)
    renewed = repository.renew_lease(
        started.lease,
        occurred_at=now["value"],
        lease_ttl_seconds=30,
    )

    assert renewed.expires_at > original_expiry
    page = repository.publish_page(
        replace(
            publication,
            lease=renewed,
            completed_at=now["value"],
        )
    )
    assert len(page.records) == 2
    assert len(repository.fetches) == 1
    assert len(repository.checkpoint_events) == 1


class FailOnSecondRecordRepository(
    InMemoryLegalCorpusSyncRepository
):
    def _before_stage_record(self, index, item) -> None:
        if index == 1:
            raise RuntimeError("second record normalization failed")


def test_second_record_failure_rolls_back_entire_database_page() -> None:
    trace: list[str] = []
    store = FakeImmutableObjectStore(trace)
    repository = FailOnSecondRecordRepository(clock=lambda: NOW)
    service = LegalCorpusSyncService(
        repository, store, clock=lambda: NOW
    )

    with pytest.raises(RuntimeError, match="second record"):
        service.sync_page(_page_requests())

    run_id = next(iter(repository.runs))
    assert repository.events[run_id][-1] == "failed"
    assert repository.fetches == {}
    assert repository.resources == {}
    assert repository.crosswalks == {}
    assert repository.versions == {}
    assert repository.provisions == {}
    assert repository.checkpoint_events == {}
    assert repository.pages == {}
    assert len(store.objects) == 1


def test_locator_redaction_removes_userinfo_and_sensitive_query_case_insensitive() -> None:
    locator = sanitize_source_locator(
        "https://user:password@example.com:443/path?"
        "ServiceKey=abc&target=prec#token=hidden"
    )

    assert locator == "https://example.com:443/path?target=prec"


def test_postgres_publisher_sql_is_append_only_and_sets_private_search_path() -> None:
    sql_statements = [
        value
        for name, value in vars(corpus_repository_module).items()
        if name.endswith("_SQL") and isinstance(value, str)
    ]

    assert sql_statements
    assert all(" UPDATE " not in f" {sql.upper()} " for sql in sql_statements)
    assert all(" DELETE " not in f" {sql.upper()} " for sql in sql_statements)
    transaction_source = (
        corpus_repository_module.PostgresLegalCorpusSyncRepository
        ._transaction.__wrapped__.__code__
    )
    assert transaction_source is not None
    import inspect

    source = inspect.getsource(
        corpus_repository_module.PostgresLegalCorpusSyncRepository._transaction
    )
    assert "set_config('search_path'" in source
    repository = (
        corpus_repository_module.PostgresLegalCorpusSyncRepository(
            connection_factory=lambda: None
        )
    )
    assert repository._search_path == "realytics,extensions,public,pg_temp"


def test_provider_revision_appends_and_replay_verifies_supersedes_lineage() -> None:
    repository = (
        corpus_repository_module.PostgresLegalCorpusSyncRepository(
            connection_factory=lambda: None
        )
    )
    provider = LegalSourceProviderRecord(
        provider_id="legal-provider:revision-2",
        provider_key="law-go-kr",
        provider_revision=2,
        source_authority="KOREA_MINISTRY_OF_GOVERNMENT_LEGISLATION",
        authority_tier="official_primary",
        provider_kind="official_api",
        canonical_base_url="https://www.law.go.kr",
        terms_version="2026-01",
        adapter_version="2.0.0",
        configured_by="legal-corpus-sync-worker",
        configured_at=NOW,
        metadata={},
    )
    previous_id = "legal-provider:revision-1"
    cursor = ProviderLineageCursor(
        previous=(previous_id, 1),
        current=(previous_id, 1),
    )

    repository._insert_provider(cursor, provider)

    assert cursor.insert_params is not None
    assert cursor.insert_params[9] == previous_id

    expected_replay = (
        provider.provider_key,
        provider.provider_revision,
        provider.source_authority,
        provider.authority_tier,
        provider.provider_kind,
        provider.canonical_base_url,
        provider.terms_version,
        provider.adapter_version,
        "active",
        previous_id,
    )
    replay_cursor = ProviderLineageCursor(
        previous=(previous_id, 1),
        current=(provider.provider_id, 2),
        existing=expected_replay,
    )
    repository._insert_provider(replay_cursor, provider)
    assert replay_cursor.insert_params is None

    broken_replay_cursor = ProviderLineageCursor(
        previous=(previous_id, 1),
        current=(provider.provider_id, 2),
        existing=(*expected_replay[:-1], None),
    )
    with pytest.raises(
        LegalCorpusImmutableConflict, match="revision lineage"
    ):
        repository._insert_provider(broken_replay_cursor, provider)


def _canonical_json(value: Any) -> bytes:
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
