from copy import deepcopy
import pytest

from routers import projects
from use_cases.stored_permit_evidence import resolve_stored_permit_documents
from test_parser_v2_records import _ready_candidate_store, _approved_selection, TENANT, PROJECT, HASH_A
from test_parser_v2_records import _seal_run, _document


def approved_permit_store():
    store, _ = _ready_candidate_store()
    candidate = store.create_candidate(tenant_id=TENANT, project_id=PROJECT, run_id='run-1',
        document_version_id='docv-1', candidate_id='permit-candidate', actor_id='parser-worker',
        canonical_field='land.zoning.primary_use_district_name', entity_id='parcel-1',
        value='제2종일반주거지역', raw_value='제2종일반주거지역', confidence=0.99,
        evidence=[{'documentVersionId':'docv-1', 'pageNumber':12, 'paragraphIndex':1,
                   'rawTextSha256':'c'*64, 'parserMethod':'native_pdf_text'}], target_modules=['land_legal'])
    _seal_run(store)
    review = store.append_review_decision(tenant_id=TENANT, project_id=PROJECT,
        candidate_id=candidate['candidateId'], actor_id='reviewer', action='approve', reason='Source checked.')
    selection = store.create_canonical_selection(tenant_id=TENANT, project_id=PROJECT,
        selection_id='permit-selection', actor_id='reviewer', candidate_id=candidate['candidateId'],
        review_decision_id=review['reviewDecisionId'])
    store.create_approved_input_snapshot(tenant_id=TENANT, project_id=PROJECT, snapshot_id='permit-snapshot',
        actor_id='different-approver', selection_ids=[selection['selectionId']], purpose='permit_review')
    return store


def test_verified_stored_permit_reuses_human_review_with_exact_source():
    result = resolve_stored_permit_documents(PROJECT, project(), approved_permit_store(), TENANT)
    assert result[0]['verificationStatus'] == 'human_reviewed'
    assert result[0]['snapshotId'] == 'permit-snapshot'
    assert result[0]['sourceLocator']['contentHash'] == HASH_A
    assert result[0]['sourceLocator']['pageNumber'] == 12


def test_superseded_document_cannot_reuse_old_approval():
    store = approved_permit_store()
    _document(store, version_id='docv-2', source_hash='b'*64, predecessor='docv-1', version_number=2)
    assert resolve_stored_permit_documents(PROJECT, project(), store, TENANT)[0]['verificationStatus'] == 'candidate'


def test_wrong_tenant_store_rows_are_rejected(monkeypatch):
    store, _ = _ready_candidate_store()
    monkeypatch.setattr(store, 'list_document_versions', lambda *_: [{'tenantId':'other', 'projectId':PROJECT}])
    with pytest.raises(ValueError, match='scope_mismatch'):
        resolve_stored_permit_documents(PROJECT, project(), store, TENANT)


def project():
    return {'source_documents': [{'archiveId': 'archive-docv-1', 'originalFileHash': HASH_A,
                                 'documentType': 'building_permit', 'verificationStatus': 'approved'}]}


def test_stored_document_does_not_inherit_metadata_approval():
    store, _ = _ready_candidate_store()
    result = resolve_stored_permit_documents(PROJECT, project(), store, TENANT)
    assert result[0]['verificationStatus'] == 'candidate'
    assert result[0]['evidenceStage'] == 'source_only'


def test_financial_selection_cannot_approve_permit_evidence():
    store, _ = _ready_candidate_store()
    _candidate, _review, selection = _approved_selection(store)
    store.create_approved_input_snapshot(tenant_id=TENANT, project_id=PROJECT, snapshot_id='snapshot-permit',
        actor_id='different-approver', selection_ids=[selection['selectionId']], purpose='monthly_cash_flow')
    result = resolve_stored_permit_documents(PROJECT, project(), store, TENANT)
    assert result[0]['verificationStatus'] == 'candidate'
    assert result[0]['evidenceStage'] == 'review_pending'


def test_stale_source_hash_cannot_reuse_candidates():
    store, _ = _ready_candidate_store()
    _approved_selection(store)
    changed = project()
    changed['source_documents'][0]['originalFileHash'] = 'b' * 64
    result = resolve_stored_permit_documents(PROJECT, changed, store, TENANT)
    assert result[0]['evidenceStage'] == 'source_only'


def test_caller_approved_documents_are_not_forwarded_to_chief(monkeypatch):
    monkeypatch.setattr(projects, 'PROJECTS_DB', {PROJECT: {'source_documents': []}})
    forged = {'parsedDocuments': [{'documentType': 'building_permit', 'verificationStatus': 'approved'}]}
    original = deepcopy(forged)
    result = projects._stored_chief_evidence_options(PROJECT, {'source_documents': []}, forged)
    assert result['parsedDocuments'] == []
    assert forged == original
