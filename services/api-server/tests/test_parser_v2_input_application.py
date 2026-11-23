"""Approval-to-input behavior; synthetic records never change a live project."""
from contextlib import contextmanager
from copy import deepcopy
import pytest

from use_cases.common import deep_merge
from use_cases.parser_v2_input_application import (
    ParserInputApplicationUnavailable, apply_parser_approved_inputs, build_parser_input_application_preview,
)
from use_cases.parser_v2_records import ParserV2ApprovalError
from db.repositories.postgres_project_repository import ProjectVersionConflict
from test_parser_v2_records import TENANT, PROJECT, _document, _run, _ready_candidate_store, _seal_run


class ProjectAuthority:
    def __init__(self):
        self.project = {"project": {"id": PROJECT, "name": "이전 이름"},
                        "land": {"address": "이전 주소", "area_sqm": 100, "pnu": "preserved"},
                        "feasibility_input": {"total_cost": 123}, "_meta": {"tenantId": TENANT, "stateVersion": 1}}
        self.writes = 0
        self.conflict_on_write = False
        self.deleted_on_write = False

    def get(self, _project_id):
        return deepcopy(self.project)

    def update(self, _project_id, patch, *, expected_state_version):
        if self.deleted_on_write:
            return None
        if self.conflict_on_write or self.project["_meta"]["stateVersion"] != expected_state_version:
            raise ProjectVersionConflict(project_id=PROJECT, expected_state_version=expected_state_version, actual_state_version=2)
        self.project = deep_merge(self.project, patch)
        self.project["_meta"]["stateVersion"] += 1
        self.writes += 1
        return deepcopy(self.project)


def prepared(*, area=1234.56):
    store, _ = _ready_candidate_store()
    for index, (field, value, unit) in enumerate([
        ("project.name", "승인된 사업", None), ("land.address", "서울특별시 중랑구 상봉동 1-1", None),
        ("land.area_sqm", area, "sqm"),
    ]):
        store.create_candidate(tenant_id=TENANT, project_id=PROJECT, run_id="run-1", document_version_id="docv-1",
            candidate_id=f"candidate-{index}", actor_id="parser-maker", canonical_field=field, value=value,
            raw_value=str(value), unit=unit, presence_status="present",
            evidence=[{"documentVersionId": "docv-1", "sheetName": "개요", "cellReference": f"B{index+1}", "parserMethod": "test"}])
    _seal_run(store)
    for index in range(3):
        decision = store.append_review_decision(tenant_id=TENANT, project_id=PROJECT, candidate_id=f"candidate-{index}",
            actor_id="independent-human-reviewer", action="approve", reason="원본 값·단위와 전체 사업 범위를 대조")
        store.create_canonical_selection(tenant_id=TENANT, project_id=PROJECT, selection_id=f"selection-{index}",
            actor_id="independent-human-reviewer", candidate_id=f"candidate-{index}", review_decision_id=decision["reviewDecisionId"])
    store.create_approved_input_snapshot(tenant_id=TENANT, project_id=PROJECT, snapshot_id="snapshot-1",
        actor_id="independent-human-approver", selection_ids=[f"selection-{index}" for index in range(3)], purpose="project-site-inputs")
    repository = ProjectAuthority()

    @contextmanager
    def transaction(tenant_id, project_id):
        assert tenant_id == TENANT and project_id == PROJECT
        yield store, repository

    preview = lambda: build_parser_input_application_preview(store=store, tenant_id=TENANT, project_id=PROJECT,
        snapshot_id="snapshot-1", project=repository.get(PROJECT))
    return store, repository, transaction, preview


def apply(transaction, preview, **overrides):
    return apply_parser_approved_inputs(tenant_id=TENANT, project_id=PROJECT, snapshot_id="snapshot-1",
        actor_id="independent-human-reviewer", application_id=overrides.pop("application_id", "application-1"),
        field_keys=overrides.pop("field_keys", ["project.name", "land.area_sqm"]),
        expected_snapshot_hash=overrides.pop("expected_snapshot_hash", preview["snapshotHash"]),
        expected_state_version=overrides.pop("expected_state_version", preview["expectedStateVersion"]),
        expected_preview_hash=overrides.pop("expected_preview_hash", preview["previewHash"]),
        transaction_pair=transaction, **overrides)


def test_preview_is_read_only_and_explicit_subset_application_preserves_precision_and_sources():
    _, repository, transaction, preview = prepared()
    before = deepcopy(repository.project)
    comparison = preview()
    assert repository.project == before and repository.writes == 0
    assert comparison["items"][2]["approvedValue"] == 1234.56
    result = apply(transaction, comparison)
    assert result["status"] == "applied" and repository.writes == 1
    reopened = repository.get(PROJECT)
    assert reopened["project"]["name"] == "승인된 사업"
    assert reopened["land"] == {"address": "이전 주소", "area_sqm": 1234.56, "pnu": "preserved"}
    assert reopened["feasibility_input"] == before["feasibility_input"]
    binding = reopened["parserApprovedInputBindings"]["land.area_sqm"]
    assert binding["sourceLocator"]["cellReference"] == "B3" and binding["snapshotId"] == "snapshot-1"
    assert binding["unit"] == "sqm" and binding["approvedValue"] == 1234.56
    assert preview()["lastApplication"]["applicationId"] == "application-1"
    assert apply(transaction, comparison)["status"] == "already_applied" and repository.writes == 1


@pytest.mark.parametrize("change", ["project", "snapshot_hash", "preview_hash", "selection", "review", "run"])
def test_changed_authority_or_preview_cannot_write(change):
    store, repository, transaction, preview = prepared()
    original = preview()
    args = {}
    if change == "project":
        repository.project["_meta"]["stateVersion"] += 1
    elif change == "snapshot_hash": args["expected_snapshot_hash"] = "0" * 64
    elif change == "preview_hash": args["expected_preview_hash"] = "0" * 64
    elif change == "selection":
        original_list = store.list_canonical_selections
        store.list_canonical_selections = lambda *args, **kwargs: [row for row in original_list(*args, **kwargs) if row["selectionId"] != "selection-0"]
    elif change == "review":
        store.append_review_decision(tenant_id=TENANT, project_id=PROJECT, candidate_id="candidate-0", actor_id="other-human",
                                    action="request_revision", reason="원본 재확인")
    else:
        original_list = store.list_parsing_runs
        store.list_parsing_runs = lambda *args: [{**row, "executionStatus": "failed"} for row in original_list(*args)]
    with pytest.raises(ParserV2ApprovalError): apply(transaction, original, **args)
    assert repository.writes == 0


def test_project_cas_conflict_does_not_persist_input_or_source_binding():
    _, repository, transaction, preview = prepared()
    before = deepcopy(repository.project)
    repository.conflict_on_write = True
    with pytest.raises(ProjectVersionConflict): apply(transaction, preview())
    assert repository.project == before and repository.writes == 0


@pytest.mark.parametrize("fields", [["financing.pf_amount"], ["land.area_sqm", "land.area_sqm"], []])
def test_financial_unknown_or_duplicate_fields_cannot_be_applied(fields):
    _, repository, transaction, preview = prepared()
    with pytest.raises(ValueError): apply(transaction, preview(), field_keys=fields)
    assert repository.writes == 0


def test_application_identity_cannot_be_reused_with_different_content():
    _, repository, transaction, preview = prepared()
    original = preview()
    apply(transaction, original)
    with pytest.raises(ParserV2ApprovalError): apply(transaction, original, field_keys=["land.address"])
    assert repository.writes == 1


def test_current_reference_only_metadata_cannot_be_ignored_at_application_boundary():
    store, repository, transaction, preview = prepared()
    original = preview()
    original_list = store.list_candidates
    store.list_candidates = lambda *args, **kwargs: [{**row, "metadata": {**row.get("metadata", {}), "referenceOnly": True}}
                                                   for row in original_list(*args, **kwargs)]
    with pytest.raises(ParserV2ApprovalError, match="reference-only"):
        apply(transaction, original)
    assert repository.writes == 0


def test_prior_receipt_does_not_claim_current_values_if_a_later_edit_changed_them():
    _, repository, transaction, preview = prepared()
    original = preview()
    apply(transaction, original)
    repository.project["land"]["area_sqm"] = 777
    with pytest.raises(ParserV2ApprovalError, match="since changed"):
        apply(transaction, original)
    assert repository.writes == 1


def test_nonversioned_project_fails_closed():
    _, repository, _, preview = prepared()
    repository.project["_meta"].pop("stateVersion")
    with pytest.raises(ParserInputApplicationUnavailable): preview()


def test_explicit_zero_area_is_preserved_as_a_value_instead_of_missing():
    _, repository, transaction, preview = prepared(area=0)
    comparison = preview()
    assert next(row for row in comparison["items"] if row["fieldKey"] == "land.area_sqm")["approvedValue"] == 0
    apply(transaction, comparison, field_keys=["land.area_sqm"])
    assert repository.project["land"]["area_sqm"] == 0


def http_client(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routers.project_parser_v2 import build_project_parser_v2_router
    store, repository, transaction, _ = prepared()
    monkeypatch.setenv("REALYTICS_PARSER_V2_REVIEW_PILOT_TENANT_ALLOWLIST", TENANT)
    monkeypatch.setenv("REALYTICS_PARSER_V2_REVIEW_PILOT_USER_ALLOWLIST", "human-approver")

    def authorize(request, permission):
        if request.headers.get("x-test-deny") == "1": raise HTTPException(status_code=403)
        if request.headers.get("x-test-reviewer-only") == "1" and permission == "project:write": raise HTTPException(status_code=403)
        return {"authenticated": True, "rbacMode": "strict", "authSource": "bearer_token",
                "identityProvider": {"mode": "oidc"}, "identityAssurance": {"source": "verified_bearer_claims", "mfaVerified": request.headers.get("x-test-mfa") != "off"},
                "user": {"userId": "human-approver", "tenantId": TENANT, "role": "admin", "metadata": {"principalType": "human"}}}
    monkeypatch.setattr("routers.project_parser_v2.authorize_request", authorize)
    monkeypatch.setattr("routers.project_parser_v2.enforce_project_tenant_scope", lambda *_args: None)
    app = FastAPI()
    audits = []
    app.include_router(build_project_parser_v2_router(project_lookup=lambda _id: repository.get(_id) if _id == PROJECT else None,
        source_document_repository_factory=lambda: None, upload_archive_repository_factory=lambda: None,
        audit_recorder=lambda _request, **payload: audits.append(payload), record_store_factory=lambda: store, review_pilot_enabled=True,
        approved_input_applier=lambda **kwargs: apply_parser_approved_inputs(**kwargs, transaction_pair=transaction)))
    client = TestClient(app)
    client.test_application_audits = audits
    return client, repository


def test_http_preview_apply_reopen_and_body_cannot_choose_value_or_expand_field_scope(monkeypatch):
    client, repository = http_client(monkeypatch)
    path = f"/projects/{PROJECT}/parsing-v2/approved-snapshots/snapshot-1"
    comparison = client.get(path + "/input-preview")
    assert comparison.status_code == 200, comparison.text
    view = comparison.json()
    body = {"applicationId": "http-apply", "fieldKeys": ["land.address", "land.area_sqm"],
            "expectedSnapshotHash": view["snapshotHash"], "expectedStateVersion": view["expectedStateVersion"], "expectedPreviewHash": view["previewHash"]}
    assert client.post(path + "/apply-input", json={**body, "values": {"land.area_sqm": 99999}}).status_code == 422
    assert client.post(path + "/apply-input", json={**body, "fieldKeys": ["financing.pf_amount"]}).status_code == 422
    saved = client.post(path + "/apply-input", json=body)
    assert saved.status_code == 200 and saved.json()["status"] == "applied"
    opened = client.get(path + "/input-preview").json()
    assert opened["lastApplication"]["applicationId"] == "http-apply"
    assert next(row for row in opened["items"] if row["fieldKey"] == "land.address")["currentValue"] == "서울특별시 중랑구 상봉동 1-1"
    assert repository.project["land"]["area_sqm"] == 1234.56
    assert client.post(path + "/apply-input", json=body).json()["status"] == "already_applied"


@pytest.mark.parametrize("headers", [{"x-test-deny": "1"}, {"x-test-mfa": "off"}, {"x-test-reviewer-only": "1"}])
def test_http_apply_keeps_existing_permission_and_mfa_gates(monkeypatch, headers):
    client, repository = http_client(monkeypatch)
    path = f"/projects/{PROJECT}/parsing-v2/approved-snapshots/snapshot-1"
    view = client.get(path + "/input-preview").json()
    body = {"applicationId": "http-blocked", "fieldKeys": ["project.name"], "expectedSnapshotHash": view["snapshotHash"],
            "expectedStateVersion": view["expectedStateVersion"], "expectedPreviewHash": view["previewHash"]}
    assert client.post(path + "/apply-input", json=body, headers=headers).status_code == 403
    assert repository.writes == 0


def test_http_delete_during_apply_returns_not_found_without_success_audit(monkeypatch):
    client, repository = http_client(monkeypatch)
    path = f"/projects/{PROJECT}/parsing-v2/approved-snapshots/snapshot-1"
    view = client.get(path + "/input-preview").json()
    repository.deleted_on_write = True
    response = client.post(path + "/apply-input", json={"applicationId": "deleted-project-write", "fieldKeys": ["project.name"],
        "expectedSnapshotHash": view["snapshotHash"], "expectedStateVersion": view["expectedStateVersion"], "expectedPreviewHash": view["previewHash"]})
    assert response.status_code == 404
    assert repository.writes == 0 and "parserInputApplication" not in repository.project
    assert client.test_application_audits == []


def test_runtime_shared_transaction_takes_parser_lock_and_uses_same_connection(monkeypatch):
    from types import SimpleNamespace
    from use_cases.parser_v2_input_application import _runtime_transaction_pair
    observed = []
    connection = object()

    @contextmanager
    def transaction(tenant):
        assert tenant == TENANT
        observed.append("begin")
        yield connection
        observed.append("commit")

    def bind(_self, conn, tenant, project):
        assert conn is connection and tenant == TENANT and project == PROJECT
        observed.append("parser-project-advisory-lock")
        return SimpleNamespace(list_records=lambda *_args, **_kwargs: [])

    monkeypatch.setattr("db.repositories.request_project_repository.current_project_scope", lambda: SimpleNamespace(tenant_id=TENANT, transaction_factory=transaction))
    monkeypatch.setattr("db.repositories.parser_v2_repository.ParserV2PostgresRepository.bind", bind)
    with _runtime_transaction_pair(TENANT, PROJECT) as (_store, project_repo):
        with project_repo._transaction(TENANT) as existing:
            assert existing is connection
            observed.append("same-project-cas-connection")
    assert observed == ["begin", "parser-project-advisory-lock", "same-project-cas-connection", "commit"]


def postgres_pair(store, seed, *, connection=None):
    """Use the real PostgreSQL repository merge/CAS path with its SQL fake."""
    from test_postgres_project_repository import _repository
    repository, connection, _ = _repository(TENANT, connection=connection)
    connection.rows[(TENANT, PROJECT)] = {"project_id": PROJECT, "tenant_id": TENANT,
        "state_version": 1, "payload_json": deepcopy(seed), "created_by": "test"}

    @contextmanager
    def transaction(_tenant, _project):
        assert _tenant == TENANT and _project == PROJECT
        yield store, repository

    def preview(snapshot_id="snapshot-1"):
        return build_parser_input_application_preview(store=store, tenant_id=TENANT, project_id=PROJECT,
            snapshot_id=snapshot_id, project=repository.get(PROJECT))
    return repository, transaction, preview, connection


def test_actual_project_merge_replaces_excel_binding_with_pdf_and_keeps_unselected_bindings():
    store, source_repository, _, _ = prepared()
    repository, transaction, preview, connection = postgres_pair(store, source_repository.project)
    fields = ["project.name", "land.address", "land.area_sqm"]
    apply(transaction, preview(), field_keys=fields)
    prior = repository.get(PROJECT)
    untouched = deepcopy(prior["parserApprovedInputBindings"]["land.address"])
    stored = connection.rows[(TENANT, PROJECT)]["payload_json"]
    stored["parserApprovedInputBindings"]["project.name"]["sourceLocator"]["metadata"] = {"excelOnly": {"row": 1}}
    stored["parserApprovedInputBindings"]["project.name"]["obsoleteBindingMetadata"] = {"old": True}
    stored["parserInputApplication"]["obsoleteReceiptMetadata"] = {"old": True}
    _document(store, version_id="docv-2", source_hash="b" * 64, predecessor="docv-1", version_number=2)
    _run(store, run_id="run-2", document_version_id="docv-2")
    for status in ("running", "completed"):
        store.append_parsing_run_event(tenant_id=TENANT, project_id=PROJECT, run_id="run-2", actor_id="parser-maker", execution_status=status)
    store.create_candidate(tenant_id=TENANT, project_id=PROJECT, run_id="run-2", document_version_id="docv-2",
        candidate_id="pdf-name", actor_id="parser-maker", canonical_field="project.name", value="PDF 승인 사업명",
        evidence=[{"documentVersionId": "docv-2", "pageNumber": 5, "paragraphIndex": 2,
                   "metadata": {"pdfOnly": {"page": 5}}, "parserMethod": "pdf-text"}])
    _seal_run(store, "run-2")
    decision = store.append_review_decision(tenant_id=TENANT, project_id=PROJECT, candidate_id="pdf-name",
        actor_id="independent-human-reviewer", action="approve", reason="새 PDF 원본 확인")
    store.create_canonical_selection(tenant_id=TENANT, project_id=PROJECT, selection_id="pdf-selection",
        actor_id="independent-human-reviewer", candidate_id="pdf-name", review_decision_id=decision["reviewDecisionId"],
        selection_version=2, supersedes_selection_id="selection-0")
    store.create_approved_input_snapshot(tenant_id=TENANT, project_id=PROJECT, snapshot_id="snapshot-2",
        actor_id="independent-human-approver", selection_ids=["pdf-selection"], purpose="project-site-inputs", supersedes_snapshot_id="snapshot-1")
    comparison = preview("snapshot-2")
    apply_parser_approved_inputs(tenant_id=TENANT, project_id=PROJECT, snapshot_id="snapshot-2", actor_id="independent-human-reviewer",
        application_id="application-2", field_keys=[fields[0]], expected_snapshot_hash=comparison["snapshotHash"],
        expected_state_version=comparison["expectedStateVersion"], expected_preview_hash=comparison["previewHash"], transaction_pair=transaction)
    opened = repository.get(PROJECT)
    binding = opened["parserApprovedInputBindings"]["project.name"]
    assert binding["sourceLocator"] == comparison["items"][0]["sourceLocator"]
    assert binding["sourceLocator"]["pageNumber"] == 5
    assert "sheetName" not in binding["sourceLocator"] and "cellReference" not in binding["sourceLocator"]
    assert binding["sourceLocator"]["metadata"] == {"pdfOnly": {"page": 5}}
    assert "obsoleteBindingMetadata" not in binding and "obsoleteReceiptMetadata" not in opened["parserInputApplication"]
    assert opened["parserApprovedInputBindings"]["land.address"] == untouched
    assert opened["project"]["name"] == "PDF 승인 사업명"


def test_real_repository_delete_between_read_and_write_does_not_report_applied():
    from test_postgres_project_repository import _FakeProjectConnection

    class DeleteBeforeLock(_FakeProjectConnection):
        delete_before_lock = False
        active_reads = 0

        def execute(self, sql, params=None):
            if self.delete_before_lock and "SELECT PROJECT_ID" in " ".join(sql.upper().split()):
                self.active_reads += 1
                if self.active_reads == 2:  # use-case get succeeded; update's own read now sees deletion
                    self.delete_before_lock = False
                    self.rows[(TENANT, PROJECT)]["payload_json"]["_meta"]["deletedAt"] = "2026-11-09T01:00:00Z"
            return super().execute(sql, params)

    store, source_repository, _, _ = prepared()
    repository, transaction, preview, connection = postgres_pair(store, source_repository.project, connection=DeleteBeforeLock())
    comparison = preview()
    connection.delete_before_lock = True
    with pytest.raises(LookupError, match="deleted"):
        apply(transaction, comparison)
    assert "parserInputApplication" not in connection.rows[(TENANT, PROJECT)]["payload_json"]
    assert not any(sql.lower().startswith("update realytics.projects") for sql, _ in connection.statements)
