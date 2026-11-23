"""Resolve permit evidence from stored source versions and reviewed Parser V2 selections."""
from copy import deepcopy

from agents.legal_permit_route import approval_document_route


PERMIT_TOKENS = ("permit", "approval", "authorization", "regulation", "zoning", "land_use", "building_code", "인허가", "허가", "토지이용")


def resolve_stored_permit_documents(project_id, project, store, tenant_id):
    documents = [
        deepcopy(row) for row in project.get('source_documents') or []
        if approval_document_route(row)
        or any(token in str(row.get('documentType') or '').casefold() for token in PERMIT_TOKENS)
    ]
    for document in documents:
        document['verificationStatus'] = 'candidate'
        document['evidenceStage'] = 'source_only'
    if not documents:
        return []
    if not tenant_id:
        return documents

    def scoped(rows):
        if any(str(row.get('tenantId')) != tenant_id or str(row.get('projectId')) != project_id for row in rows):
            raise ValueError('permit_evidence_scope_mismatch')
        return rows

    versions = scoped(store.list_document_versions(tenant_id, project_id))
    runs = scoped(store.list_parsing_runs(tenant_id, project_id))
    candidates = scoped(store.list_candidates(tenant_id, project_id))
    reviews = scoped(store.list_review_decisions(tenant_id, project_id))
    snapshots = scoped(store.list_approved_input_snapshots(tenant_id, project_id))
    retired_versions = {row.get('supersedesDocumentVersionId') for row in versions}
    retired_snapshots = {row.get('supersedesSnapshotId') for row in snapshots}
    by_candidate = {row['candidateId']: row for row in candidates}
    by_review = {row['reviewDecisionId']: row for row in reviews}
    selections = []
    for snapshot in snapshots:
        if (snapshot.get('snapshotId') in retired_snapshots or snapshot.get('validityStatus') != 'active'
                or snapshot.get('approvalStatus') != 'approved'):
            continue
        # Validate source/selection/hash relationships through the existing strict contract.
        from use_cases.parser_v2_records import approved_input_snapshot_wire_payload
        validated = approved_input_snapshot_wire_payload(snapshot)
        selections.extend((validated['snapshotId'], selection) for selection in validated.get('selections') or [])

    resolved = []
    for document in documents:
        archive = document.get('archiveId') or document.get('originalFileHash')
        source_hash = document.get('originalFileHash') or document.get('sha256') or (archive if len(str(archive or '')) == 64 else None)
        matches = [version for version in versions if version.get('archiveId') == archive
                   and version.get('documentVersionId') not in retired_versions
                   and source_hash and version.get('sha256') == source_hash]
        version_ids = {row['documentVersionId'] for row in matches}
        related = [row for row in candidates if row.get('documentVersionId') in version_ids]
        related_runs = [row for row in runs if row.get('documentVersionId') in version_ids]
        if related:
            candidate_ids = {row.get('candidateId') for row in related}
            document['evidenceStage'] = 'review_pending' if any(row.get('candidateId') in candidate_ids for row in reviews) else 'candidate'
        elif any(row.get('executionStatus') == 'failed' for row in related_runs):
            document['evidenceStage'] = 'parse_failed'
        accepted = []
        for snapshot_id, selection in selections:
            candidate = by_candidate.get(selection.get('candidateId')) or {}
            review = by_review.get(selection.get('reviewDecisionId')) or {}
            locator = candidate.get('sourceLocator') or {}
            exact = locator.get('pageNumber') is not None or locator.get('page') is not None or bool(locator.get('cell'))
            field = str(selection.get('fieldKey') or '')
            if (candidate.get('documentVersionId') in version_ids and candidate.get('sourceHash') == source_hash
                    and field.startswith(('legal.', 'permit.', 'regulation.', 'land.zoning'))
                    and selection.get('approvalStatus') == 'approved'
                    and selection.get('conflictStatus') != 'open'
                    and review.get('candidateId') == candidate.get('candidateId')
                    and review.get('resultingApprovalStatus', review.get('approvalStatus')) == 'approved'
                    and review.get('reviewerId') and exact):
                accepted.append({**deepcopy(document), 'verificationStatus': 'human_reviewed', 'evidenceStage': 'approved',
                    'snapshotId': snapshot_id, 'candidateId': candidate['candidateId'],
                    'reviewDecisionId': selection['reviewDecisionId'],
                    'sourceLocator': {**locator, 'sourceDocumentId': archive, 'contentHash': source_hash,
                                      'pageNumber': locator.get('pageNumber', locator.get('page'))}})
        resolved.extend(accepted or [document])
    return resolved
