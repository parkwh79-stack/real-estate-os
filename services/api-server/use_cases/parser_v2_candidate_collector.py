from __future__ import annotations

"""Fail-closed persistence boundary for Parser V2 module candidates."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from parsing_platform.contracts import ParsedFactCandidate
from parsing_platform.feasibility_domain_modules import (
    ALL_FEASIBILITY_MODULE_IDS,
    DOMAIN_CANDIDATE_CONTENT_HASH_VERSION,
    DOMAIN_MODULE_IDS,
    FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
    FEASIBILITY_DOMAIN_PACK_VERSION,
    RECONCILIATION_MODULE_ID,
    build_domain_candidate_content_hash,
    build_domain_candidate_id,
)
from parsing_platform.field_registry import (
    FIELD_REGISTRY_VERSION,
    validate_candidate_against_registry,
)
from parsing_platform.orchestrator import ParsingOrchestrationResult
from parsing_platform.source_review_candidates import MODULE_ID as SOURCE_REVIEW_MODULE_ID
from use_cases.parser_v2_records import ParserV2RecordStore
from use_cases.parser_v2_records import validate_source_locator_attestations


class ParserModuleCandidateCollectionError(RuntimeError):
    """Raised before persistence when a module candidate violates run scope."""


@dataclass(frozen=True)
class CandidateCollectionResult:
    candidate_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    source_module_ids: tuple[str, ...]
    blocking_diagnostic_codes: tuple[str, ...]
    expected_module_ids: tuple[str, ...] = ()
    completed_module_ids: tuple[str, ...] = ()
    unresolved_module_ids: tuple[str, ...] = ()
    reconciliation_source_candidate_ids: tuple[str, ...] = ()
    reconciliation_bound: bool = False
    blocked_candidate_ids: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.blocking_diagnostic_codes and not self.conflict_ids

    @property
    def seal_eligible(self) -> bool:
        """A seal is an immutable collection closure, not an approval."""

        return True

    @property
    def review_eligible(self) -> bool:
        conflict_codes = {
            "CROSS_SOURCE_VALUE_CONFLICT",
            "CROSS_SOURCE_BASIS_CONFLICT",
            "CROSS_SOURCE_UNIT_CONFLICT",
        }
        unrepresented = set(self.blocking_diagnostic_codes)
        if self.conflict_ids:
            unrepresented.difference_update(conflict_codes)
        reconciliation_required = (
            RECONCILIATION_MODULE_ID in self.expected_module_ids
        )
        return (
            bool(self.candidate_ids)
            and not self.unresolved_module_ids
            and not unrepresented
            and (
                not reconciliation_required
                or self.reconciliation_bound
            )
        )

    @property
    def approval_eligible(self) -> bool:
        # This is a static collection-time signal only.  Resolved conflict
        # state is evaluated by the canonical-selection gate.
        return self.review_eligible and self.clean and not self.blocked_candidate_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "realytics-parser-module-candidate-collection-v1",
            "candidateOnly": True,
            "authoritative": False,
            "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
            "candidateIds": list(self.candidate_ids),
            "candidateCount": len(self.candidate_ids),
            "blockedCandidateIds": list(self.blocked_candidate_ids),
            "blockedCandidateCount": len(self.blocked_candidate_ids),
            "conflictIds": list(self.conflict_ids),
            "conflictCount": len(self.conflict_ids),
            "sourceModuleIds": list(self.source_module_ids),
            "expectedModuleIds": list(self.expected_module_ids),
            "completedModuleIds": list(self.completed_module_ids),
            "unresolvedModuleIds": list(self.unresolved_module_ids),
            "reconciliationSourceCandidateIds": list(
                self.reconciliation_source_candidate_ids
            ),
            "reconciliationBound": self.reconciliation_bound,
            "blockingDiagnosticCodes": list(self.blocking_diagnostic_codes),
            "clean": self.clean,
            "sealEligible": self.seal_eligible,
            "reviewEligible": self.review_eligible,
            "approvalEligible": self.approval_eligible,
        }


def collect_parser_module_candidates(
    *,
    orchestration_result: ParsingOrchestrationResult,
    record_store: ParserV2RecordStore,
    tenant_id: str,
    project_id: str,
    parsing_run_id: str,
    document_version_id: str,
    source_hash: str,
    actor_id: str,
    ontology_version: str,
    expected_module_ids: Sequence[str] = (),
) -> CandidateCollectionResult:
    """Revalidate and persist candidate-only facts from completed modules.

    No module output is trusted merely because orchestration completed.  This
    collector binds every candidate to the current immutable run/document,
    recomputes its deterministic ID, reruns the current field registry, and
    only then calls the append-only record store.
    """

    parsed: list[tuple[str, str, ParsedFactCandidate]] = []
    source_modules: list[str] = []
    blocking_codes: list[str] = []
    reconciliation_candidate_ids: tuple[str, ...] | None = None
    reconciliation_candidate_count: int | None = None
    reconciliation_source_module_ids: tuple[str, ...] | None = None
    reconciliation_source_module_count: int | None = None
    selected_candidate_modules = tuple(
        sorted(
            set(expected_module_ids or orchestration_result.results).intersection(
                (*ALL_FEASIBILITY_MODULE_IDS, SOURCE_REVIEW_MODULE_ID)
            )
        )
    )
    completed_candidate_modules = tuple(
        module_id
        for module_id in selected_candidate_modules
        if module_id in orchestration_result.results
        and orchestration_result.results[module_id].execution_status
        == "completed"
    )
    unresolved_candidate_modules = tuple(
        sorted(
            set(selected_candidate_modules).difference(
                completed_candidate_modules
            )
        )
    )
    if unresolved_candidate_modules:
        blocking_codes.append("DOMAIN_MODULE_COMPLETENESS_UNRESOLVED")
    document_record = record_store.get_document_version(
        tenant_id,
        project_id,
        document_version_id,
    )
    document_authority = str(
        document_record.get("sourceAuthority") or "unknown"
    )
    if str(document_record.get("sha256") or "") != source_hash:
        raise ParserModuleCandidateCollectionError(
            "collector source hash does not match the archived document"
        )
    structural_result = orchestration_result.results.get("structural_extraction")
    structural_artifact = (
        structural_result.output.get("artifact")
        if structural_result is not None
        and structural_result.execution_status == "completed"
        and isinstance(structural_result.output, Mapping)
        and isinstance(structural_result.output.get("artifact"), Mapping)
        else None
    )

    for module_id, module_result in orchestration_result.results.items():
        if module_result.execution_status != "completed":
            continue
        output = module_result.output
        if not isinstance(output, Mapping):
            continue
        if output.get("schemaVersion") != FEASIBILITY_DOMAIN_OUTPUT_SCHEMA:
            continue
        if output.get("domainPackVersion") != FEASIBILITY_DOMAIN_PACK_VERSION:
            raise ParserModuleCandidateCollectionError(
                "domain module output uses an unsupported domain pack version"
            )
        if output.get("moduleId") != module_id:
            raise ParserModuleCandidateCollectionError(
                "domain module output moduleId does not match orchestration"
            )
        if output.get("moduleVersion") != module_result.module_version:
            raise ParserModuleCandidateCollectionError(
                "domain module output moduleVersion does not match orchestration"
            )
        for diagnostic in output.get("diagnostics") or []:
            if not isinstance(diagnostic, Mapping):
                raise ParserModuleCandidateCollectionError(
                    "domain module diagnostic must be an object"
                )
            if diagnostic.get("blocking"):
                code = str(diagnostic.get("code") or "").strip()
                if code:
                    blocking_codes.append(code)
        if output.get("outputKind") == "reconciliation_report":
            if reconciliation_candidate_ids is not None:
                raise ParserModuleCandidateCollectionError(
                    "multiple reconciliation reports were emitted"
                )
            raw_source_ids = output.get("sourceCandidateIds")
            if not isinstance(raw_source_ids, Sequence) or isinstance(
                raw_source_ids, (str, bytes, bytearray)
            ):
                raise ParserModuleCandidateCollectionError(
                    "reconciliation report must declare sourceCandidateIds"
                )
            reconciliation_candidate_ids = tuple(
                sorted(str(item) for item in raw_source_ids)
            )
            summary = output.get("summary")
            if not isinstance(summary, Mapping):
                raise ParserModuleCandidateCollectionError(
                    "reconciliation report summary is required"
                )
            reconciliation_candidate_count = int(
                summary.get("candidateCount", -1)
            )
            raw_source_module_ids = output.get("sourceModuleIds")
            if not isinstance(
                raw_source_module_ids, Sequence
            ) or isinstance(
                raw_source_module_ids, (str, bytes, bytearray)
            ):
                raise ParserModuleCandidateCollectionError(
                    "reconciliation report must declare sourceModuleIds"
                )
            reconciliation_source_module_ids = tuple(
                sorted(str(item) for item in raw_source_module_ids)
            )
            reconciliation_source_module_count = int(
                summary.get("sourceModuleCount", -1)
            )
        if output.get("outputKind") != "candidate_batch":
            continue
        if module_id not in (*DOMAIN_MODULE_IDS, SOURCE_REVIEW_MODULE_ID):
            raise ParserModuleCandidateCollectionError(
                "an unregistered domain module attempted to emit candidates"
            )
        source_modules.append(module_id)
        raw_candidates = output.get("candidates")
        if not isinstance(raw_candidates, Sequence) or isinstance(
            raw_candidates, (str, bytes, bytearray)
        ):
            raise ParserModuleCandidateCollectionError(
                "domain candidate batch must contain a candidate list"
            )
        for raw_candidate in raw_candidates:
            try:
                candidate = ParsedFactCandidate.model_validate(raw_candidate)
            except Exception as exc:
                raise ParserModuleCandidateCollectionError(
                    "domain module emitted an invalid ParsedFactCandidate"
                ) from exc
            _validate_candidate_scope(
                candidate=candidate,
                module_id=module_id,
                module_version=module_result.module_version,
                tenant_id=tenant_id,
                project_id=project_id,
                parsing_run_id=parsing_run_id,
                document_version_id=document_version_id,
                source_hash=source_hash,
                source_authority=document_authority,
                ontology_version=ontology_version,
                structural_artifact=structural_artifact,
            )
            parsed.append((module_id, module_result.module_version, candidate))

    candidate_ids = [item.candidate_id for _, _, item in parsed]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ParserModuleCandidateCollectionError(
            "domain modules emitted duplicate deterministic candidate IDs"
        )
    if reconciliation_candidate_ids is not None:
        expected_ids = tuple(sorted(candidate_ids))
        expected_source_modules = tuple(sorted(set(source_modules)))
        if (
            reconciliation_candidate_ids != expected_ids
            or reconciliation_candidate_count != len(expected_ids)
            or reconciliation_source_module_ids
            != expected_source_modules
            or reconciliation_source_module_count
            != len(expected_source_modules)
        ):
            raise ParserModuleCandidateCollectionError(
                "reconciliation report candidate set does not match persisted module outputs"
            )
    reconciliation_bound = reconciliation_candidate_ids is not None
    if (
        RECONCILIATION_MODULE_ID in selected_candidate_modules
        and not reconciliation_bound
    ):
        blocking_codes.append(
            "RECONCILIATION_CANDIDATE_SET_BINDING_MISSING"
        )

    persisted: list[dict[str, Any]] = []
    for module_id, module_version, candidate in parsed:
        evidence = [
            item.model_dump(by_alias=True, mode="json", exclude_none=True)
            for item in candidate.evidence
        ]
        record = record_store.create_candidate(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=parsing_run_id,
            document_version_id=document_version_id,
            candidate_id=candidate.candidate_id,
            actor_id=actor_id,
            canonical_field=candidate.field_key,
            value=candidate.normalized_value,
            raw_value=candidate.raw_value,
            normalized_value=candidate.normalized_value,
            unit=candidate.unit,
            origin_type=candidate.origin_type.value,
            presence_status=candidate.presence_status.value,
            confidence=candidate.confidence,
            entity_id=candidate.entity_id,
            authority=candidate.authority.value,
            validation_codes=candidate.validation_codes,
            effective_date=candidate.effective_date,
            as_of_date=candidate.as_of_date,
            evidence=evidence,
            target_modules=[module_id],
            metadata={
                **dict(candidate.metadata),
                "collectorContractVersion": (
                    "realytics-parser-module-candidate-collection-v1"
                ),
                "collectedFromModuleId": module_id,
                "collectedFromModuleVersion": module_version,
            },
        )
        review_basis_missing = module_id == SOURCE_REVIEW_MODULE_ID and set(record.get("validationCodes") or []) == {"FACT_BASIS_REQUIRED"}
        # Missing monetary basis remains a candidate-level canonical-selection block.
        # It must not prevent reviewing independently attested identity/area facts.
        if record.get("validationStatus") != "passed" and not review_basis_missing:
            # This should be unreachable because validation happens immediately
            # before persistence.  Treat registry drift as a hard boundary.
            raise ParserModuleCandidateCollectionError(
                "persisted candidate did not pass the current field registry"
            )
        persisted.append(record)

    conflict_ids = _persist_value_conflicts(
        records=persisted,
        record_store=record_store,
        tenant_id=tenant_id,
        project_id=project_id,
        parsing_run_id=parsing_run_id,
        actor_id=actor_id,
    )
    return CandidateCollectionResult(
        candidate_ids=tuple(sorted(candidate_ids)),
        conflict_ids=tuple(sorted(conflict_ids)),
        source_module_ids=tuple(sorted(set(source_modules))),
        blocking_diagnostic_codes=tuple(sorted(set(blocking_codes))),
        expected_module_ids=selected_candidate_modules,
        completed_module_ids=completed_candidate_modules,
        unresolved_module_ids=unresolved_candidate_modules,
        reconciliation_source_candidate_ids=(
            reconciliation_candidate_ids or ()
        ),
        reconciliation_bound=reconciliation_bound,
        blocked_candidate_ids=tuple(sorted(
            str(record["candidateId"]) for record in persisted
            if record.get("validationStatus") == "blocked"
        )),
    )


def seal_collected_candidate_set(
    *,
    collection: CandidateCollectionResult,
    record_store: ParserV2RecordStore,
    tenant_id: str,
    project_id: str,
    parsing_run_id: str,
    actor_id: str,
) -> dict[str, Any] | None:
    """Freeze the exact collection, including zero facts and open issues."""

    if not collection.expected_module_ids:
        # The legacy intake-only profile deliberately keeps its manual pilot
        # candidate endpoint open.  Automatic closure applies to governed
        # domain collections whose expected module manifest is explicit.
        return None
    return record_store.seal_candidate_set(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=parsing_run_id,
        actor_id=actor_id,
        expected_candidate_ids=collection.candidate_ids,
        expected_conflict_ids=collection.conflict_ids,
        expected_module_ids=collection.expected_module_ids,
        completed_module_ids=collection.completed_module_ids,
        unresolved_module_ids=collection.unresolved_module_ids,
        blocking_diagnostic_codes=collection.blocking_diagnostic_codes,
        reconciliation_source_candidate_ids=(
            collection.reconciliation_source_candidate_ids
        ),
        reconciliation_bound=collection.reconciliation_bound,
    )


def _validate_candidate_scope(
    *,
    candidate: ParsedFactCandidate,
    module_id: str,
    module_version: str,
    tenant_id: str,
    project_id: str,
    parsing_run_id: str,
    document_version_id: str,
    source_hash: str,
    source_authority: str,
    ontology_version: str,
    structural_artifact: Mapping[str, Any] | None,
) -> None:
    expected = {
        "tenantId": (candidate.tenant_id, tenant_id),
        "projectId": (candidate.project_id, project_id),
        "parsingRunId": (candidate.parsing_run_id, parsing_run_id),
        "documentVersionId": (
            candidate.document_version_id,
            document_version_id,
        ),
        "ontologyVersion": (candidate.ontology_version, ontology_version),
        "parserVersion": (
            candidate.parser_version,
            f"{module_id}@{module_version}",
        ),
        "authority": (candidate.authority.value, source_authority),
    }
    mismatches = [
        name for name, (actual, wanted) in expected.items() if actual != wanted
    ]
    if mismatches:
        raise ParserModuleCandidateCollectionError(
            "candidate scope mismatch: " + ", ".join(mismatches)
        )
    metadata = candidate.metadata
    if (
        metadata.get("domainModuleId") != module_id
        or metadata.get("domainModuleVersion") != module_version
        or metadata.get("domainPackVersion") != FEASIBILITY_DOMAIN_PACK_VERSION
        or metadata.get("candidateContentHashVersion")
        != DOMAIN_CANDIDATE_CONTENT_HASH_VERSION
        or not str(metadata.get("sourceRowId") or "").strip()
    ):
        raise ParserModuleCandidateCollectionError(
            "candidate module lineage metadata is incomplete"
        )
    candidate_wire = candidate.model_dump(
        by_alias=True,
        mode="json",
        exclude_none=True,
    )
    content_hash = build_domain_candidate_content_hash(candidate_wire)
    if candidate.metadata.get("candidateContentHash") != content_hash:
        raise ParserModuleCandidateCollectionError(
            "candidateContentHash does not match semantic candidate content"
        )
    if build_domain_candidate_id(candidate_wire) != candidate.candidate_id:
        raise ParserModuleCandidateCollectionError(
            "candidateId does not match deterministic candidate content"
        )
    evidence = [
        item.model_dump(by_alias=True, mode="json", exclude_none=True)
        for item in candidate.evidence
    ]
    if not evidence or any(
        item.get("documentVersionId") != document_version_id
        or item.get("fileSha256") != source_hash
        for item in evidence
    ):
        raise ParserModuleCandidateCollectionError(
            "candidate evidence is not bound to the current document/hash"
        )
    if structural_artifact is None:
        raise ParserModuleCandidateCollectionError(
            "candidate persistence requires an attested structural artifact"
        )
    try:
        validate_source_locator_attestations(evidence, structural_artifact)
    except Exception as exc:
        raise ParserModuleCandidateCollectionError(
            "candidate evidence was not emitted by structural extraction"
        ) from exc
    issues = validate_candidate_against_registry(
        ontology_version=ontology_version,
        field_key=candidate.field_key,
        normalized_value=candidate.normalized_value,
        unit=candidate.unit,
        entity_id=candidate.entity_id,
        evidence=evidence,
        presence_status=candidate.presence_status.value,
        origin_type=candidate.origin_type.value,
        metadata=metadata,
    )
    blocking = [item for item in issues if item.blocking and not (module_id == SOURCE_REVIEW_MODULE_ID and item.code == "FACT_BASIS_REQUIRED")]
    if blocking:
        codes = ", ".join(sorted({item.code for item in issues}))
        raise ParserModuleCandidateCollectionError(
            "candidate failed current registry validation: " + codes
        )


def _persist_value_conflicts(
    *,
    records: Sequence[Mapping[str, Any]],
    record_store: ParserV2RecordStore,
    tenant_id: str,
    project_id: str,
    parsing_run_id: str,
    actor_id: str,
) -> list[str]:
    groups: dict[tuple[str, str | None], list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for record in records:
        groups[
            (
                str(record.get("canonicalField") or ""),
                (
                    str(record["entityId"])
                    if record.get("entityId") is not None
                    else None
                ),
            )
        ].append(record)
    conflict_ids: list[str] = []
    for (field_key, entity_id), candidates in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        semantic_signatures = {
            _canonical_json(
                {
                    "normalizedValue": candidate.get("normalizedValue"),
                    "unit": candidate.get("unit"),
                    "basis": (
                        candidate.get("metadata", {}).get("basis")
                        if isinstance(candidate.get("metadata"), Mapping)
                        else None
                    ),
                }
            )
            for candidate in candidates
        }
        if len(semantic_signatures) <= 1:
            continue
        values = {
            _canonical_json(candidate.get("normalizedValue"))
            for candidate in candidates
        }
        units = {
            (
                str(candidate.get("unit"))
                if candidate.get("unit") is not None
                else None
            )
            for candidate in candidates
        }
        bases = {
            _canonical_json(
                (
                    candidate.get("metadata", {}).get("basis")
                    if isinstance(candidate.get("metadata"), Mapping)
                    else None
                )
            )
            for candidate in candidates
        }
        if len(units) > 1:
            conflict_code = "DOMAIN_CANDIDATE_UNIT_CONFLICT"
        elif len(values) > 1:
            conflict_code = "DOMAIN_CANDIDATE_VALUE_CONFLICT"
        elif len(bases) > 1:
            conflict_code = "DOMAIN_CANDIDATE_BASIS_CONFLICT"
        else:
            continue
        candidate_ids = sorted(
            str(candidate["candidateId"]) for candidate in candidates
        )
        material = {
            "schemaVersion": "realytics-domain-semantic-conflict-v1",
            "parsingRunId": parsing_run_id,
            "fieldKey": field_key,
            "entityId": entity_id,
            "candidateIds": candidate_ids,
            "conflictCode": conflict_code,
        }
        conflict_id = "conflict:" + hashlib.sha256(
            _canonical_json(material).encode("utf-8")
        ).hexdigest()
        record_store.create_source_conflict(
            tenant_id=tenant_id,
            project_id=project_id,
            conflict_id=conflict_id,
            actor_id=actor_id,
            canonical_field=field_key,
            candidate_ids=candidate_ids,
            conflict_code=conflict_code,
            severity="high",
            detection_run_id=parsing_run_id,
            notes=(
                "Conflicting same-run value, unit, or basis semantics require "
                "explicit human resolution."
            ),
        )
        conflict_ids.append(conflict_id)
    return conflict_ids


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


__all__ = [
    "CandidateCollectionResult",
    "ParserModuleCandidateCollectionError",
    "collect_parser_module_candidates",
    "seal_collected_candidate_set",
]
