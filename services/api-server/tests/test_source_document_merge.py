from copy import deepcopy

from use_cases.common import deep_merge, merge_source_documents
from db.repositories.source_document_repository import SourceDocumentRepository


def test_append_preserves_prior_documents_and_versions_without_mutation():
    old = {"source_documents": [{"archiveId": "a", "fileName": "plan.pdf"}]}
    new = {"source_documents": [{"archiveId": "b", "fileName": "plan.pdf"}]}
    before = deepcopy((old, new))
    result = deep_merge(old, new)
    assert [d["archiveId"] for d in result["source_documents"]] == ["a", "b"]
    assert (old, new) == before
    assert len(deep_merge(result, {"source_documents": []})["source_documents"]) == 2


def test_same_hash_enriches_metadata_without_duplicate():
    result = merge_source_documents(
        [{"archiveId": "a", "fileName": "plan.pdf"}],
        [{"originalFileHash": "a", "validationStatus": "passed"}],
    )
    assert len(result) == 1
    assert result[0]["fileName"] == "plan.pdf"
    assert result[0]["validationStatus"] == "passed"


def test_unidentified_documents_do_not_replace_each_other():
    store = {"p": {"project": {"id": "p"}}}
    repository = SourceDocumentRepository(store, lambda: None)
    repository.save_upload_metadata("p", {"fileName": "a.pdf"})
    repository.save_upload_metadata("p", {"fileName": "b.pdf"})
    assert len(store["p"]["source_documents"]) == 2


def test_non_document_lists_still_replace():
    assert deep_merge({"product_mix": [1]}, {"product_mix": [2]})["product_mix"] == [2]
