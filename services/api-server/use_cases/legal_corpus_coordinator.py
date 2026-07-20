"""Multi-page official corpus coordinator and completion attestation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Callable, Protocol, Sequence

from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from db.repositories.legal_corpus_repository import PublishedCorpusPage
from use_cases.legal_corpus_sync import (
    LegalCorpusSyncRequest,
    LegalCorpusSyncService,
    _deterministic_id,
    _stable_hash,
    sanitize_checkpoint,
    sanitize_requested_scope,
)


class LegalCorpusCoordinationError(RuntimeError):
    """A provider page sequence cannot be certified as complete."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: Sequence["OfficialCorpusPageDiagnostic"] = (),
        completed_page_count: int = 0,
        last_checkpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)
        self.completed_page_count = completed_page_count
        self.last_checkpoint = last_checkpoint
        self.partial = bool(self.diagnostics or completed_page_count)
        self.retryable = any(item.retryable for item in self.diagnostics)


@dataclass(frozen=True)
class OfficialCorpusPageDiagnostic:
    code: str
    message: str
    retryable: bool = False
    official_identifier: str | None = None
    target: str | None = None
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("page diagnostic code and message are required")
        if (
            self.retry_after_seconds is not None
            and self.retry_after_seconds < 0
        ):
            raise ValueError(
                "retry_after_seconds must not be negative"
            )


@dataclass(frozen=True)
class OfficialCorpusPage:
    requests: tuple[LegalCorpusSyncRequest, ...]
    terminal: bool
    partial: bool = False
    diagnostics: tuple[OfficialCorpusPageDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requests", tuple(self.requests))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not self.requests:
            raise ValueError(
                "an official corpus page requires at least one record"
            )
        if self.partial and not self.diagnostics:
            raise ValueError("a partial official corpus page requires diagnostics")


class OfficialCorpusPageSource(Protocol):
    """Provider adapter that preserves official page/checkpoint boundaries."""

    def fetch_page(
        self,
        *,
        checkpoint_before: str | None,
    ) -> OfficialCorpusPage: ...


@dataclass(frozen=True)
class LegalCorpusScopeCompletionAttestation:
    completion_attestation_id: str
    provider_id: str
    requested_scope: str
    requested_scope_hash: str
    coverage_identity: OfficialCorpusCoverageIdentity
    coverage_mode: str
    baseline_completion_attestation_id: str | None
    first_checkpoint_sequence: int
    last_checkpoint_sequence: int
    terminal_checkpoint: str
    page_count: int
    record_count: int
    resource_count: int
    page_hashes: tuple[str, ...]
    manifest_sha256: str
    started_at: datetime
    completed_at: datetime
    attested_by: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_hashes", tuple(self.page_hashes))
        for name in (
            "completion_attestation_id",
            "provider_id",
            "requested_scope",
            "requested_scope_hash",
            "terminal_checkpoint",
            "manifest_sha256",
            "attested_by",
            "coverage_mode",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        for name in ("requested_scope_hash", "manifest_sha256"):
            if not re.fullmatch(
                r"[0-9a-f]{64}", str(getattr(self, name))
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256")
        if self.coverage_mode not in {"initial", "incremental"}:
            raise ValueError("coverage_mode must be initial or incremental")
        if self.coverage_mode == "initial":
            if self.baseline_completion_attestation_id is not None:
                raise ValueError(
                    "initial coverage cannot reference a baseline completion"
                )
        elif not str(
            self.baseline_completion_attestation_id or ""
        ).strip():
            raise ValueError(
                "incremental coverage requires a baseline completion"
            )
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", page_hash)
            for page_hash in self.page_hashes
        ):
            raise ValueError(
                "every page_hash must be a lowercase SHA-256"
            )
        if self.first_checkpoint_sequence < 1:
            raise ValueError(
                "first_checkpoint_sequence must be positive"
            )
        if (
            self.last_checkpoint_sequence
            < self.first_checkpoint_sequence
        ):
            raise ValueError(
                "last checkpoint cannot precede first checkpoint"
            )
        if self.page_count != len(self.page_hashes):
            raise ValueError("page_count must match page_hashes")
        if self.page_count < 1:
            raise ValueError("page_count must be positive")
        if self.record_count < 1:
            raise ValueError("record_count must be positive")
        if not 1 <= self.resource_count <= self.record_count:
            raise ValueError(
                "resource_count must be between 1 and record_count"
            )
        _aware(self.started_at, "started_at")
        _aware(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")


class LegalCorpusCompletionRepository(Protocol):
    def attest_scope_completion(
        self,
        attestation: LegalCorpusScopeCompletionAttestation,
    ) -> LegalCorpusScopeCompletionAttestation: ...


class LegalCorpusBatchCoordinator:
    """Execute a checkpoint chain and seal its ordered page manifest."""

    def __init__(
        self,
        sync_service: LegalCorpusSyncService,
        completion_repository: LegalCorpusCompletionRepository,
        *,
        max_pages: int = 10_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= max_pages <= 1_000_000:
            raise ValueError("max_pages must be between 1 and 1000000")
        self._sync_service = sync_service
        self._completion_repository = completion_repository
        self._max_pages = max_pages
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        source: OfficialCorpusPageSource,
        *,
        initial_checkpoint: str | None = None,
        baseline_completion_attestation_id: str | None = None,
    ) -> LegalCorpusScopeCompletionAttestation:
        started_at = _aware(self._clock(), "clock")
        checkpoint = sanitize_checkpoint(initial_checkpoint)
        baseline: tuple[str, int, str, str, str, str] | None = None
        coverage_identity: OfficialCorpusCoverageIdentity | None = None
        coverage_mode: str | None = None
        results: list[PublishedCorpusPage] = []
        seen_page_hashes: set[str] = set()
        resources: set[str] = set()

        for _page_index in range(1, self._max_pages + 1):
            try:
                page = source.fetch_page(checkpoint_before=checkpoint)
            except LegalCorpusCoordinationError:
                raise
            except Exception as exc:
                raise LegalCorpusCoordinationError(
                    "provider page fetch failed "
                    f"({type(exc).__name__})",
                    completed_page_count=len(results),
                    last_checkpoint=checkpoint,
                ) from exc
            if page.partial:
                codes = ", ".join(
                    diagnostic.code for diagnostic in page.diagnostics
                )
                raise LegalCorpusCoordinationError(
                    "provider page is partial and cannot be certified as "
                    f"complete ({codes})",
                    diagnostics=page.diagnostics,
                    completed_page_count=len(results),
                    last_checkpoint=checkpoint,
                )
            requests = tuple(page.requests)
            first = requests[0]
            page_coverage_identity = getattr(
                first, "coverage_identity", None
            )
            if not isinstance(
                page_coverage_identity,
                OfficialCorpusCoverageIdentity,
            ):
                raise LegalCorpusCoordinationError(
                    "certified completion requires a code-owned coverage "
                    "identity"
                )
            page_coverage_mode = str(
                getattr(first, "sync_kind", "")
            ).strip()
            if page_coverage_mode not in {"initial", "incremental"}:
                raise LegalCorpusCoordinationError(
                    "certified completion requires initial or incremental mode"
                )
            canonical_scope = sanitize_requested_scope(
                first.requested_scope
            )
            current_baseline = (
                first.provider_key,
                first.provider_revision,
                canonical_scope,
                first.requested_by,
                page_coverage_identity.coverage_identity_sha256,
                page_coverage_mode,
            )
            if baseline is None:
                baseline = current_baseline
                coverage_identity = page_coverage_identity
                coverage_mode = page_coverage_mode
            elif current_baseline != baseline:
                raise LegalCorpusCoordinationError(
                    "provider, revision, scope, attestor, coverage identity, "
                    "and mode must remain constant across a completion run"
                )
            for request in requests:
                if (
                    sanitize_checkpoint(request.checkpoint_before)
                    != checkpoint
                ):
                    raise LegalCorpusCoordinationError(
                        "provider page checkpoint_before does not match "
                        "the certified checkpoint head"
                    )
                next_checkpoint = sanitize_checkpoint(
                    request.checkpoint_after
                )
                if not next_checkpoint or next_checkpoint == checkpoint:
                    raise LegalCorpusCoordinationError(
                        "every certified page must advance its checkpoint"
                    )

            try:
                result = self._sync_service.sync_page(requests)
            except LegalCorpusCoordinationError:
                raise
            except Exception as exc:
                raise LegalCorpusCoordinationError(
                    "provider page publication failed "
                    f"({type(exc).__name__})",
                    completed_page_count=len(results),
                    last_checkpoint=checkpoint,
                ) from exc
            expected_provider_id = _deterministic_id(
                "legal-provider",
                {
                    "providerKey": first.provider_key,
                    "providerRevision": first.provider_revision,
                },
            )
            expected_scope_hash = page_coverage_identity.scope_hash(
                requested_scope=canonical_scope
            )
            expected_after = sanitize_checkpoint(
                first.checkpoint_after
            )
            if (
                result.provider_id != expected_provider_id
                or result.requested_scope_hash != expected_scope_hash
                or result.checkpoint_before != checkpoint
                or result.checkpoint_after != expected_after
            ):
                raise LegalCorpusCoordinationError(
                    "published provider/scope/checkpoint does not match "
                    "the provider page"
                )
            if result.page_hash in seen_page_hashes:
                raise LegalCorpusCoordinationError(
                    "provider repeated a page hash in one completion run"
                )
            seen_page_hashes.add(result.page_hash)
            results.append(result)
            resources.update(
                record.legal_resource_id for record in result.records
            )
            checkpoint = result.checkpoint_after
            if page.terminal:
                break
        else:
            raise LegalCorpusCoordinationError(
                "provider did not reach a terminal page within max_pages"
            )

        if baseline is None or checkpoint is None:
            raise LegalCorpusCoordinationError(
                "completion run has no terminal checkpoint"
            )
        (
            provider_key,
            provider_revision,
            requested_scope,
            attested_by,
            _coverage_identity_sha256,
            resolved_coverage_mode,
        ) = baseline
        assert coverage_identity is not None
        assert coverage_mode == resolved_coverage_mode
        if coverage_mode == "initial":
            if initial_checkpoint is not None:
                raise LegalCorpusCoordinationError(
                    "initial coverage must begin without a prior checkpoint"
                )
            if baseline_completion_attestation_id is not None:
                raise LegalCorpusCoordinationError(
                    "initial coverage cannot reference a baseline completion"
                )
        elif (
            initial_checkpoint is None
            or not str(
                baseline_completion_attestation_id or ""
            ).strip()
        ):
            raise LegalCorpusCoordinationError(
                "incremental coverage requires a checkpoint and baseline "
                "completion"
            )
        provider_id = _deterministic_id(
            "legal-provider",
            {
                "providerKey": provider_key,
                "providerRevision": provider_revision,
            },
        )
        coverage_identity.assert_provider(
            provider_key=provider_key,
            provider_revision=provider_revision,
            provider_id=provider_id,
        )
        requested_scope_hash = coverage_identity.scope_hash(
            requested_scope=requested_scope
        )
        manifest_payload = {
            "providerId": provider_id,
            "requestedScope": requested_scope,
            "requestedScopeHash": requested_scope_hash,
            "coverageIdentity": dict(coverage_identity.as_dict()),
            "coverageMode": coverage_mode,
            "baselineCompletionAttestationId": (
                baseline_completion_attestation_id
            ),
            "terminalCheckpoint": checkpoint,
            "pages": [
                {
                    "checkpointSequence": result.checkpoint_sequence,
                    "checkpointBefore": result.checkpoint_before,
                    "checkpointAfter": result.checkpoint_after,
                    "pageHash": result.page_hash,
                    "syncRunId": result.sync_run_id,
                    "sourceFetchId": result.source_fetch_id,
                    "records": [
                        {
                            "legalResourceId": record.legal_resource_id,
                            "legalResourceVersionId": (
                                record.legal_resource_version_id
                            ),
                            "normalizedSha256": record.normalized_sha256,
                        }
                        for record in result.records
                    ],
                }
                for result in results
            ],
        }
        manifest_sha256 = _stable_hash(manifest_payload)
        completed_at = _aware(self._clock(), "clock")
        attestation = LegalCorpusScopeCompletionAttestation(
            completion_attestation_id=_deterministic_id(
                "legal-corpus-completion",
                {
                    "providerId": provider_id,
                    "requestedScopeHash": requested_scope_hash,
                    "coverageIdentitySha256": (
                        coverage_identity.coverage_identity_sha256
                    ),
                    "coverageMode": coverage_mode,
                    "baselineCompletionAttestationId": (
                        baseline_completion_attestation_id
                    ),
                    "terminalCheckpoint": checkpoint,
                    "manifestSha256": manifest_sha256,
                },
            ),
            provider_id=provider_id,
            requested_scope=requested_scope,
            requested_scope_hash=requested_scope_hash,
            coverage_identity=coverage_identity,
            coverage_mode=coverage_mode,
            baseline_completion_attestation_id=(
                baseline_completion_attestation_id
            ),
            first_checkpoint_sequence=results[0].checkpoint_sequence,
            last_checkpoint_sequence=results[-1].checkpoint_sequence,
            terminal_checkpoint=checkpoint,
            page_count=len(results),
            record_count=sum(len(result.records) for result in results),
            resource_count=len(resources),
            page_hashes=tuple(result.page_hash for result in results),
            manifest_sha256=manifest_sha256,
            started_at=started_at,
            completed_at=completed_at,
            attested_by=attested_by,
        )
        try:
            return self._completion_repository.attest_scope_completion(
                attestation
            )
        except LegalCorpusCoordinationError:
            raise
        except Exception as exc:
            raise LegalCorpusCoordinationError(
                "completion attestation persistence failed "
                f"({type(exc).__name__})",
                completed_page_count=len(results),
                last_checkpoint=checkpoint,
            ) from exc


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


__all__ = [
    "LegalCorpusBatchCoordinator",
    "LegalCorpusCompletionRepository",
    "LegalCorpusCoordinationError",
    "LegalCorpusScopeCompletionAttestation",
    "OfficialCorpusPage",
    "OfficialCorpusPageDiagnostic",
    "OfficialCorpusPageSource",
]
