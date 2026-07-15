from __future__ import annotations

import copy
import hashlib
import os
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parsing_platform.contracts import ApprovedInputSnapshot
from parsing_platform.default_modules import build_default_intake_orchestrator
from routers.project_parser_v2 import build_project_parser_v2_router
from use_cases.parser_v2_records import ParserV2RecordStore


class MemoryRecords:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def upsert(self, project_id: str, record: dict[str, Any]) -> dict[str, Any]:
        assert project_id == record["projectId"]
        copied = copy.deepcopy(record)
        self.records.append(copied)
        return copy.deepcopy(copied)

    def list(
        self,
        project_id: str,
        *,
        record_type: str | None = None,
        limit: int = 1000,
        **_: Any,
    ) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(item)
            for item in self.records
            if item.get("projectId") == project_id
            and (record_type is None or item.get("recordType") == record_type)
        ][:limit]


class SourceDocuments:
    def __init__(self, archive_ids: list[str]) -> None:
        self.documents = {
            archive_id: {
                "archiveId": archive_id,
                "originalFileName": "business-plan.txt",
                "contentType": "text/plain",
                "sourceAuthority": "submitted_document",
            }
            for archive_id in archive_ids
        }

    def find_by_archive_id(self, project_id: str, archive_id: str):
        if project_id != "p-1":
            return None
        return copy.deepcopy(self.documents.get(archive_id))


class UploadArchive:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents

    @staticmethod
    def is_valid_archive_id(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64

    def resolve_download_metadata(self, project_id: str, document: dict[str, Any]):
        archive_id = document.get("archiveId")
        if project_id != "p-1" or archive_id not in self.contents:
            return None
        content = self.contents[archive_id]
        return {
            "path": None,
            "content": content,
            "archiveObjectKey": f"p-1/{archive_id}.txt",
            "archiveId": archive_id,
            "archiveIntegrityStatus": "verified",
            "mediaType": "text/plain",
            "filename": "business-plan.txt",
            "fileSizeBytes": len(content),
        }


def _client(
    monkeypatch,
    *,
    review_pilot_enabled: bool = True,
    principal_type: str | None = "human",
):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    monkeypatch.setenv(
        "REALYTICS_PARSER_V2_REVIEW_PILOT_TENANT_ALLOWLIST", "tenant-a"
    )
    monkeypatch.setenv(
        "REALYTICS_PARSER_V2_REVIEW_PILOT_USER_ALLOWLIST",
        "parser-v2-producer,parser-v2-reviewer,parser-v2-approver",
    )
    monkeypatch.setenv("REALYTICS_PARSER_V2_REVIEW_PILOT_REQUIRE_MFA", "true")
    def fake_authorize_request(request, permission):
        user_id = request.headers.get("x-test-user-id") or "parser-v2-producer"
        metadata = {} if principal_type is None else {"principalType": principal_type}
        return {
            "authenticated": True,
            "user": {
                "userId": user_id,
                "tenantId": "tenant-a",
                "role": "admin",
                "status": "active",
                "metadata": metadata,
            },
            "role": "admin",
            "permissions": [permission],
            "rbacMode": os.environ.get("REALYTICS_RBAC_MODE", "strict"),
            "authSource": "bearer_token",
            "identityProvider": {"mode": "oidc"},
            "identityAssurance": {
                "source": "verified_bearer_claims",
                "mfaVerified": True,
                "amr": ["mfa"],
            },
            "authorization": {"allowed": True, "permission": permission},
        }

    monkeypatch.setattr(
        "routers.project_parser_v2.authorize_request", fake_authorize_request
    )
    monkeypatch.setattr(
        "routers.project_parser_v2.enforce_project_tenant_scope",
        lambda request, project: fake_authorize_request(request, "project:read"),
    )
    content = b"Project Alpha\nGross floor area 100 sqm\n"
    second_content = b"Project Alpha revised\nGross floor area 110 sqm\n"
    archive_id = hashlib.sha256(content).hexdigest()
    second_archive_id = hashlib.sha256(second_content).hexdigest()
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    source_documents = SourceDocuments([archive_id, second_archive_id])
    upload_archive = UploadArchive(
        {archive_id: content, second_archive_id: second_content}
    )
    projects = {
        "p-1": {
            "projectId": "p-1",
            "name": "unchanged",
            "_meta": {"tenantId": "tenant-a"},
        }
    }
    audits: list[dict[str, Any]] = []

    def audit_recorder(_request, **payload):
        audits.append(copy.deepcopy(payload))

    router = build_project_parser_v2_router(
        project_lookup=lambda project_id: projects.get(project_id),
        source_document_repository_factory=lambda: source_documents,
        upload_archive_repository_factory=lambda: upload_archive,
        audit_recorder=audit_recorder,
        record_store_factory=lambda: store,
        orchestrator_factory=build_default_intake_orchestrator,
        profile_upsert_fn=memory.upsert,
        profile_list_fn=memory.list,
        review_pilot_enabled=review_pilot_enabled,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    client.second_archive_id = second_archive_id
    return client, archive_id, projects, audits


def _profile_payload() -> dict[str, Any]:
    return {
        "profileId": "pilot.intake",
        "version": "1.0.0",
        "failurePolicy": "fail_closed",
        "orderedStages": [
            {"stageId": "security", "moduleRefs": ["document_inspection@1.0.0"]},
            {"stageId": "structure", "moduleRefs": ["structural_extraction@1.0.0"]},
            {
                "stageId": "classification",
                "moduleRefs": ["document_classification@1.0.0"],
            },
        ],
        "metadata": {
            "candidateOnly": True,
            "authoritativeProjectionAllowed": False,
        },
    }


def _run(client: TestClient, archive_id: str) -> dict[str, Any]:
    response = client.post(
        "/projects/p-1/parsing-v2/runs",
        json={
            "archiveId": archive_id,
            "profileId": "pilot.intake",
            "profileVersion": "1.0.0",
            "documentId": "document-1",
            "documentVersionId": "document-version-1",
            "parsingRunId": "run-1",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _candidate(
    archive_id: str,
    candidate_id: str,
    raw_value: str,
    normalized_value: int,
    *,
    document_version_id: str = "document-version-1",
) -> dict[str, Any]:
    attested_source_value = "110" if document_version_id == "document-version-2" else "100"
    source_line = f"Gross floor area {attested_source_value} sqm"
    evidence_material = f"{document_version_id}|line-2".encode("utf-8")
    return {
        "candidateId": candidate_id,
        "documentVersionId": document_version_id,
        "fieldKey": "feasibility.gross_floor_area_sqm",
        "rawValue": raw_value,
        "normalizedValue": normalized_value,
        "unit": "sqm",
        "originType": "extracted",
        "presenceStatus": "present",
        "confidence": 0.95,
        "authority": "submitted_document",
        "evidence": [
            {
                "documentId": "document-1",
                "documentName": "business-plan.txt",
                "documentVersionId": document_version_id,
                "evidenceId": "parser-evidence:"
                + hashlib.sha256(evidence_material).hexdigest()[:32],
                "fileSha256": archive_id,
                "jsonPath": "/lines/2",
                "rawTextSha256": hashlib.sha256(source_line.encode()).hexdigest(),
                "parserMethod": "text_structure@1.0.0",
                "sourceType": "text_line",
            }
        ],
    }


def test_catalog_and_immutable_project_profile_api(monkeypatch):
    client, _, _, _ = _client(monkeypatch)

    contracts = client.get("/parser-v2/contracts")
    assert contracts.status_code == 200
    assert "ParsedFactCandidate" in contracts.json()["contracts"]
    assert contracts.json()["authoritativeProjectionAllowed"] is False

    catalog = client.get("/parser-v2/catalog")
    assert catalog.status_code == 200
    assert len(catalog.json()["schemaContracts"]) >= 8
    assert [item["moduleId"] for item in catalog.json()["modules"]] == [
        "document_classification",
        "document_inspection",
        "structural_extraction",
    ]
    assert catalog.json()["domainStandard"]["autoSourceSelectionAllowed"] is False

    bad = {**_profile_payload(), "unexpectedAuthorityOverride": True}
    assert client.post("/projects/p-1/parsing-v2/profiles", json=bad).status_code == 422

    saved = client.post(
        "/projects/p-1/parsing-v2/profiles", json=_profile_payload()
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["profile"]["project_id"] == "p-1"

    listed = client.get("/projects/p-1/parsing-v2/profiles")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    changed = _profile_payload()
    changed["failurePolicy"] = "preserve_partial"
    conflict = client.post("/projects/p-1/parsing-v2/profiles", json=changed)
    assert conflict.status_code == 409


def test_default_intake_profile_is_project_scoped_and_persisted_before_run(monkeypatch):
    client, archive_id, _, _ = _client(monkeypatch)

    response = client.post(
        "/projects/p-1/parsing-v2/runs",
        json={
            "archiveId": archive_id,
            "documentId": "default-profile-document",
            "documentVersionId": "default-profile-document-v1",
            "parsingRunId": "default-profile-run-1",
        },
    )

    assert response.status_code == 201, response.text
    run = response.json()["parsingRun"]
    assert run["profileId"] == "realytics.intake_core"
    profiles = client.get("/projects/p-1/parsing-v2/profiles").json()["items"]
    default_profile = next(
        item for item in profiles if item["profile"]["profile_id"] == "realytics.intake_core"
    )
    assert default_profile["profile"]["project_id"] == "p-1"
    assert default_profile["profile"]["metadata"]["moduleVersionsPinned"] is True
    assert run["planHash"] == default_profile["executionPlanHash"]


def test_candidate_review_conflict_selection_and_snapshot_are_sidecar_only(monkeypatch):
    client, archive_id, projects, audits = _client(monkeypatch)
    initial_project = copy.deepcopy(projects["p-1"])
    assert (
        client.post("/projects/p-1/parsing-v2/profiles", json=_profile_payload()).status_code
        == 201
    )

    result = _run(client, archive_id)
    assert result["candidateOnly"] is True
    assert result["authoritative"] is False
    assert result["parsingRun"]["executionStatus"] == "completed"
    assert result["parsingRun"]["approvalStatus"] == "not_requested"
    assert projects["p-1"] == initial_project

    first = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=_candidate(archive_id, "candidate-1", "100", 100),
    )
    second = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=_candidate(archive_id, "candidate-2", "110", 110),
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["approvalStatus"] == "not_requested"

    evidence_id = first.json()["evidence"][0]["evidenceId"]
    resolved_evidence = client.get(
        f"/projects/p-1/parsing-v2/evidence/{evidence_id}"
    )
    assert resolved_evidence.status_code == 200, resolved_evidence.text
    assert resolved_evidence.json()["references"][0]["candidateId"] == "candidate-1"
    assert resolved_evidence.json()["firstClassEvidenceGraphRecord"] is False
    missing_evidence = client.get(
        "/projects/p-1/parsing-v2/evidence/parser-evidence:missing"
    )
    assert missing_evidence.status_code == 404

    conflict = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/conflicts",
        json={
            "conflictId": "conflict-1",
            "fieldKey": "feasibility.gross_floor_area_sqm",
            "candidateIds": ["candidate-1", "candidate-2"],
            "conflictType": "value_mismatch",
            "severity": "high",
        },
    )
    assert conflict.status_code == 201, conflict.text
    assert conflict.json()["conflictStatus"] == "open"

    seal = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/seal",
        json={
            "expectedCandidateIds": ["candidate-1", "candidate-2"],
            "expectedConflictIds": ["conflict-1"],
        },
    )
    assert seal.status_code == 201, seal.text
    assert seal.json()["candidateCount"] == 2

    late_candidate = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=_candidate(archive_id, "candidate-late", "120", 120),
    )
    assert late_candidate.status_code == 409
    assert "candidate set is sealed" in late_candidate.text

    review = client.post(
        "/projects/p-1/parsing-v2/candidates/candidate-1/reviews",
        json={
            "reviewDecisionId": "decision-1",
            "expectedDecisionSequence": 0,
            "action": "approve",
            "reason": "Source locator checked against the archived document.",
            "conflictIds": ["conflict-1"],
        },
        headers={"x-test-user-id": "parser-v2-reviewer"},
    )
    assert review.status_code == 201, review.text
    assert review.json()["resultingApprovalStatus"] == "approved"

    selection_payload = {
        "selectionId": "selection-1",
        "candidateId": "candidate-1",
        "reviewDecisionId": "decision-1",
        "expectedDecisionSequence": 1,
    }
    blocked = client.post(
        "/projects/p-1/parsing-v2/canonical-selections",
        json=selection_payload,
        headers={"x-test-user-id": "parser-v2-reviewer"},
    )
    assert blocked.status_code == 409
    assert "open source conflicts" in blocked.text

    resolved = client.post(
        "/projects/p-1/parsing-v2/conflicts/conflict-1/resolve",
        json={
            "reviewDecisionId": "decision-1",
            "expectedDecisionSequence": 1,
            "resolutionStatus": "resolved",
            "notes": "candidate-1 selected after source review",
        },
        headers={"x-test-user-id": "parser-v2-reviewer"},
    )
    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["conflictStatus"] == "resolved"

    selection = client.post(
        "/projects/p-1/parsing-v2/canonical-selections",
        json=selection_payload,
        headers={"x-test-user-id": "parser-v2-reviewer"},
    )
    assert selection.status_code == 201, selection.text
    assert selection.json()["approvalStatus"] == "approved"

    snapshot = client.post(
        "/projects/p-1/parsing-v2/approved-snapshots",
        json={
            "snapshotId": "snapshot-1",
            "selectionIds": ["selection-1"],
            "purpose": "pf_review_candidate_input",
            "expectedSelectionVersions": {"selection-1": 1},
        },
        headers={"x-test-user-id": "parser-v2-approver"},
    )
    assert snapshot.status_code == 201, snapshot.text
    snapshot_payload = snapshot.json()
    assert snapshot_payload["approvalStatus"] == "approved"
    assert "recordId" not in snapshot_payload
    ApprovedInputSnapshot.model_validate(snapshot_payload)

    package = client.get("/projects/p-1/parsing-v2/runs/run-1")
    assert package.status_code == 200
    body = package.json()
    assert body["projectProjectionMutated"] is False
    assert len(body["candidates"]) == 2
    assert len(body["conflicts"]) == 1
    assert len(body["canonicalSelections"]) == 1
    assert len(body["approvedInputSnapshots"]) == 1
    assert "recordId" not in body["approvedInputSnapshots"][0]
    ApprovedInputSnapshot.model_validate(body["approvedInputSnapshots"][0])
    assert projects["p-1"] == initial_project

    forbidden_audit_keys = {"rawValue", "normalizedValue", "content", "path"}

    def walk(value: Any):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in forbidden_audit_keys
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(audits)


def test_candidate_submission_rejects_cross_document_and_extra_identity(monkeypatch):
    client, archive_id, _, _ = _client(monkeypatch)
    client.post("/projects/p-1/parsing-v2/profiles", json=_profile_payload())
    _run(client, archive_id)

    wrong_document = _candidate(archive_id, "candidate-wrong", "100", 100)
    wrong_document["documentVersionId"] = "another-document-version"
    response = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=wrong_document,
    )
    assert response.status_code == 409

    injected_identity = _candidate(archive_id, "candidate-extra", "100", 100)
    injected_identity["projectId"] = "another-project"
    response = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=injected_identity,
    )
    assert response.status_code == 422

    unattested = _candidate(archive_id, "candidate-unattested", "100", 100)
    unattested["evidence"][0]["evidenceId"] = "parser-evidence:missing"
    response = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=unattested,
    )
    assert response.status_code == 409
    assert "absent from the parsing run" in response.text

    tampered = _candidate(archive_id, "candidate-tampered", "100", 100)
    tampered["evidence"][0].pop("rawTextSha256")
    response = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=tampered,
    )
    assert response.status_code == 409
    assert "does not match its attested structural artifact" in response.text


def test_project_reconciliation_detects_conflict_across_document_runs(monkeypatch):
    client, archive_id, _, _ = _client(monkeypatch)
    second_archive_id = client.second_archive_id
    assert client.post(
        "/projects/p-1/parsing-v2/profiles", json=_profile_payload()
    ).status_code == 201
    _run(client, archive_id)
    first = client.post(
        "/projects/p-1/parsing-v2/runs/run-1/candidates",
        json=_candidate(archive_id, "candidate-doc-1", "100", 100),
    )
    assert first.status_code == 201, first.text

    second_run = client.post(
        "/projects/p-1/parsing-v2/runs",
        json={
            "archiveId": second_archive_id,
            "profileId": "pilot.intake",
            "profileVersion": "1.0.0",
            "documentId": "document-1",
            "documentVersionId": "document-version-2",
            "versionNumber": 2,
            "predecessorVersionId": "document-version-1",
            "parsingRunId": "run-2",
        },
    )
    assert second_run.status_code == 201, second_run.text
    second_payload = _candidate(
        second_archive_id,
        "candidate-doc-2",
        "110",
        110,
        document_version_id="document-version-2",
    )
    second = client.post(
        "/projects/p-1/parsing-v2/runs/run-2/candidates",
        json=second_payload,
    )
    assert second.status_code == 201, second.text

    conflict = client.post(
        "/projects/p-1/parsing-v2/conflicts",
        json={
            "conflictId": "cross-document-conflict-1",
            "fieldKey": "feasibility.gross_floor_area_sqm",
            "candidateIds": ["candidate-doc-1", "candidate-doc-2"],
            "conflictType": "cross_document_value_mismatch",
            "severity": "high",
        },
    )

    assert conflict.status_code == 201, conflict.text
    body = conflict.json()
    assert body["parsingRunId"] is None
    assert body["sourceParsingRunIds"] == ["run-1", "run-2"]
    assert body["documentVersionIds"] == [
        "document-version-1",
        "document-version-2",
    ]


def test_review_and_approval_routes_are_disabled_outside_phase_two_pilot(monkeypatch):
    client, _, _, _ = _client(monkeypatch, review_pilot_enabled=False)
    response = client.post(
        "/projects/p-1/parsing-v2/candidates/unknown-candidate/reviews",
        json={
            "expectedDecisionSequence": 0,
            "action": "reject",
            "reason": "feature-gate probe",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "parser_v2_review_pilot_disabled"


def test_review_pilot_refuses_permissive_rbac_even_when_feature_flag_is_enabled(
    monkeypatch,
):
    client, _, _, _ = _client(monkeypatch, review_pilot_enabled=True)
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")

    response = client.post(
        "/projects/p-1/parsing-v2/candidates/missing/reviews",
        json={
            "expectedDecisionSequence": 0,
            "action": "approve",
            "reason": "Must never reach storage in permissive mode.",
        },
    )

    assert response.status_code == 503
    assert (
        response.json()["detail"]["error"]
        == "parser_v2_review_pilot_requires_strict_rbac"
    )


def test_review_pilot_fails_closed_without_explicit_human_principal_claim(
    monkeypatch,
):
    client, _, _, _ = _client(
        monkeypatch,
        review_pilot_enabled=True,
        principal_type=None,
    )

    response = client.post(
        "/projects/p-1/parsing-v2/candidates/missing/reviews",
        json={
            "expectedDecisionSequence": 0,
            "action": "approve",
            "reason": "Unknown principals must not reach review storage.",
        },
        headers={"x-test-user-id": "parser-v2-reviewer"},
    )

    assert response.status_code == 403
    assert (
        response.json()["detail"]["error"]
        == "parser_v2_review_pilot_service_identity_forbidden"
    )
