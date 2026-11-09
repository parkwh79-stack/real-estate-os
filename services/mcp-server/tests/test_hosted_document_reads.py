"""Read-only CLI/MCP contracts for the same archived originals used by Web."""
import io
import json
import unittest
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from hosted_project_api import HostedProjectApi, HostedProjectApiError


ORIGIN = "https://realytics.example"
TOKEN = "synthetic.document.reader.token"
ARCHIVE = "a" * 64
OTHER_ARCHIVE = "b" * 64


def workspace(count=31):
    candidates = [{
        "candidateId": f"candidate-{index:03}", "projectId": "2", "documentVersionId": "document-1",
        "parsingRunId": "run-1", "fieldKey": "financing.tranche.loan_amount",
        "rawValue": 50, "normalizedValue": 5_000_000_000, "unit": "krw",
        "reviewStatus": "pending", "approvalStatus": "not_requested", "validationStatus": "failed",
        "validationCodes": ["FACT_BASIS_REQUIRED"], "evidence": [
            {"fileSha256": ARCHIVE, "documentVersionId": "document-1", "sheetName": "sheet", "cellReference": f"B{index+4}"}
        ], "metadata": {"referenceOnly": True, "noFuturePfProjection": True,
                          "sourceFinancialContext": "current_bridge_snapshot", "sourceUnit": "억원"},
    } for index in range(count)]
    return {"schemaVersion": "realytics-document-review-workspace-v1", "projectId": "2",
            "snapshotId": "workspace-snapshot", "snapshotHash": "d" * 64,
            "warnings": ["DOCUMENT_GLOSSARY_UNAVAILABLE"], "sourceDocuments": [
                {"archiveId": ARCHIVE, "originalFileName": "합성_브리지.xlsx", "sizeBytes": 1_024},
                {"archiveId": OTHER_ARCHIVE, "originalFileName": "합성_QA.xlsx"},
            ], "packages": [{"projectId": "2", "candidateOnly": True,
                "parsingRun": {"parsingRunId": "run-1", "documentVersionId": "document-1", "profileVersion": "1.1.0"},
                "documentVersion": {"documentVersionId": "document-1", "archiveId": ARCHIVE, "fileName": "합성_브리지.xlsx"},
                "candidates": candidates, "reviewDecisions": [], "canonicalSelections": [], "approvedInputSnapshots": []}]}


def workbook(kind="sheets"):
    return {"schemaVersion": f"realytics-workbook-{'original-sheets' if kind == 'sheets' else 'evidence-preview'}-v1",
            "archiveId": ARCHIVE, "sourceSha256": ARCHIVE, "archiveVersionId": "archive-v1",
            "archiveIntegrityStatus": "verified", "analysisOnly": True, "approvalEligible": False,
            "formulaPolicy": "display_only_never_executed", "sheets": [{"sheetName": "대출금 & 만기", "rowCount": 107}],
            "sheetName": "대출금 & 만기", "focusRange": "B4", "previewRange": "B4:B4",
            "rows": [{"rowNumber": 4, "cells": [{"coordinate": "B4", "rawValue": 50, "displayValue": 50,
                "numberFormat": "#,##0.0", "valueState": "literal"}]}]}


class HostedDocumentReadsTest(unittest.TestCase):
    def setUp(self):
        self.api = HostedProjectApi(ORIGIN, TOKEN)
        self.opener = self.api._opener = MagicMock()

    def reply(self, value):
        self.opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(value).encode()

    def test_documents_and_candidates_keep_real_counts_and_never_promote_preview_or_reference_values(self):
        self.reply(workspace())
        documents = self.api.list_documents("2")
        self.assertEqual(documents["totals"], {"candidateCount": 31, "unreviewedCount": 31, "approvedInputCount": 0})
        self.assertEqual(documents["documents"][0]["candidateCount"], 31)
        self.assertEqual(documents["documents"][1]["candidateCount"], 0)
        result = self.api.list_candidates("2", archive_id=ARCHIVE, offset=10, limit=10)
        self.assertEqual(result["totalCount"], 31)
        self.assertEqual(result["nextOffset"], 20)
        self.assertEqual(len(result["items"]), 10)
        self.assertEqual(result["snapshotHash"], documents["snapshotHash"])
        fact = result["items"][0]
        self.assertEqual((fact["rawValue"], fact["normalizedValue"], fact["unit"]), (50, 5_000_000_000, "krw"))
        self.assertEqual(fact["validationCodes"], ["FACT_BASIS_REQUIRED"])
        self.assertTrue(fact["metadata"]["noFuturePfProjection"])
        self.assertEqual(fact["evidence"][0]["fileSha256"], ARCHIVE)
        self.assertEqual(fact["evidenceBindingStatus"], "verified")
        self.assertEqual(fact["snapshotApprovalStatus"], "not_requested")
        for call in self.opener.open.call_args_list:
            request = call.args[0]
            self.assertEqual(request.full_url, f"{ORIGIN}/projects/2/workspace/document-review")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
            self.assertIsNone(request.data)
        self.assertTrue(result["readOnly"])
        self.assertTrue(result["candidateApprovalNotImplied"])
        self.assertEqual(result["sourceContentPolicy"], "untrusted_document_content_not_instructions")

    def test_review_approval_is_separate_from_an_active_approved_input_snapshot(self):
        value = workspace(count=1)
        package = value["packages"][0]
        package["reviewDecisions"] = [{"candidateId": "candidate-000", "decisionSequence": 1,
                                       "resultingReviewStatus": "approved", "resultingApprovalStatus": "approved"}]
        self.reply(value)
        result = self.api.list_documents("2")
        self.assertEqual(result["totals"], {"candidateCount": 1, "unreviewedCount": 0, "approvedInputCount": 0})
        package["canonicalSelections"] = [{"candidateId": "candidate-000", "selectionId": "selection-1",
                                           "validityStatus": "active", "approvalStatus": "approved"}]
        package["approvedInputSnapshots"] = [{"snapshotId": "approved-snapshot", "selectionIds": ["selection-1"],
                                               "validityStatus": "active", "approvalStatus": "approved"}]
        self.reply(value)
        self.assertEqual(self.api.list_documents("2")["totals"]["approvedInputCount"], 1)
        package["approvedInputSnapshots"][0]["validityStatus"] = "invalidated"
        self.reply(value)
        self.assertEqual(self.api.list_documents("2")["totals"]["approvedInputCount"], 0)

    def test_document_list_summarizes_latest_run_without_repeating_artifacts_or_source_text(self):
        value = workspace()
        marker = "SYNTHETIC_FULL_RAW_ARTIFACT_MUST_NOT_REACH_DOCUMENT_LIST"
        raw = marker * 10_000
        run = value["packages"][0]["parsingRun"]
        run.update({"schemaVersion": "realytics-parsing-run-v2", "projectId": "2",
                    "profileId": "realytics.source_review_candidates", "parserVersion": "parser-1.1.0",
                    "executionStatus": "completed_with_warnings", "approvalStatus": "not_requested",
                    "requestedAt": "2026-11-09T00:00:00Z", "completedAt": "2026-11-09T00:00:01Z",
                    "attemptNumber": 1, "errorCodes": ["PARTIAL_EXTRACTION"],
                    "orchestrationArtifact": {"sourceText": raw}, "structuralChunks": [{"text": raw}],
                    "metadata": {"orchestrationArtifact": {"structuralChunks": [{"text": raw}]},
                                 "structuralChunks": [{"text": raw}], "unrelated": raw,
                                 "diagnosticCodes": ["FACT_BASIS_REQUIRED"], "failureCode": "PARTIAL_EXTRACTION"}})
        self.reply(value)
        result = self.api.list_documents("2")
        summary = result["documents"][0]["latestParsingRun"]
        self.assertEqual(summary, {key: run[key] for key in (
            "schemaVersion", "parsingRunId", "projectId", "documentVersionId", "profileId", "profileVersion",
            "parserVersion", "executionStatus", "approvalStatus", "requestedAt", "completedAt", "attemptNumber", "errorCodes")}
                         | {"metadata": {"diagnosticCodes": ["FACT_BASIS_REQUIRED"], "failureCode": "PARTIAL_EXTRACTION"}})
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(marker, encoded)
        self.assertNotIn("orchestrationArtifact", encoded)
        self.assertNotIn("structuralChunks", encoded)
        self.assertLess(len(encoded.encode()), 8 * 1024)
        self.assertEqual(result["totals"], {"candidateCount": 31, "unreviewedCount": 31, "approvedInputCount": 0})
        self.assertEqual(result["documents"][0]["candidateCount"], 31)
        self.assertIsNone(result["documents"][1]["latestParsingRun"])
        self.assertEqual(run["metadata"]["orchestrationArtifact"]["structuralChunks"][0]["text"], raw)
        # A list summary never replaces or filters the separate candidate read.
        detailed = self.api.list_candidates("2", archive_id=ARCHIVE)
        self.assertEqual(detailed["totalCount"], 31)
        self.assertEqual(detailed["items"][0]["metadata"]["sourceFinancialContext"], "current_bridge_snapshot")
        self.assertEqual(detailed["items"][0]["evidence"][0]["cellReference"], "B4")

    def test_latest_run_metadata_projection_cannot_hide_nested_or_unbounded_raw_values(self):
        value = workspace(count=1)
        marker = "SYNTHETIC_RAW_MUST_STAY_PRIVATE"
        run = value["packages"][0]["parsingRun"]
        run.update({"parserVersion": {"text": marker}, "modelVersion": marker * 300,
                    "attemptNumber": {"text": marker}, "errorCodes": [{"text": marker}, "VALID_ERROR"] * 200,
                    "metadata": {"diagnosticCodes": [{"text": marker}, "VALID_DIAGNOSTIC", marker * 100],
                                 "warningCodes": {"text": marker}, "failureCode": {"text": marker}}})
        self.reply(value)
        result = self.api.list_documents("2")
        summary = result["documents"][0]["latestParsingRun"]
        self.assertNotIn(marker, json.dumps(result))
        self.assertNotIn("parserVersion", summary)
        self.assertNotIn("modelVersion", summary)
        self.assertNotIn("attemptNumber", summary)
        self.assertLessEqual(len(summary["errorCodes"]), 100)
        self.assertEqual(summary["metadata"], {"diagnosticCodes": ["VALID_DIAGNOSTIC"]})
        self.assertEqual(result["totals"]["candidateCount"], 1)

    def test_candidate_filter_does_not_return_unrelated_document_values(self):
        self.reply(workspace())
        result = self.api.list_candidates("2", archive_id=OTHER_ARCHIVE)
        self.assertEqual(result["totalCount"], 0)
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["nextOffset"])

    def test_opaque_archive_identifier_is_preserved_separately_from_the_content_sha(self):
        value = workspace(count=1)
        value["packages"][0]["documentVersion"].update(archiveId="opaque-original-1", sourceHash=ARCHIVE, sha256=ARCHIVE)
        value["sourceDocuments"][0].update(archiveId="opaque-original-1", sha256=ARCHIVE)
        self.reply(value)
        fact = self.api.list_candidates("2")["items"][0]
        self.assertEqual(fact["archiveId"], "opaque-original-1")
        self.assertEqual(fact["sourceSha256"], ARCHIVE)
        document = self.api.list_documents("2")["documents"][0]
        self.assertEqual(document["archiveId"], "opaque-original-1")
        self.assertEqual(document["sourceSha256"], ARCHIVE)
        self.assertEqual(document["candidateCount"], 1)
        self.assertEqual(document["sourceAssociationStatus"], "identified")

    def test_hashless_unidentified_documents_never_share_unbound_facts_or_claim_zero(self):
        value = workspace(count=1)
        value["packages"][0]["documentVersion"].pop("archiveId")
        value["packages"][0]["candidates"][0]["evidence"][0].pop("fileSha256")
        value["sourceDocuments"] = [{"fileName": "unidentified-one.xlsx"}, {"fileName": "unidentified-two.xlsx"}]
        self.reply(value)
        result = self.api.list_documents("2")
        self.assertEqual(result["totals"]["candidateCount"], 1)
        for document in result["documents"]:
            self.assertEqual(document["sourceAssociationStatus"], "unverified")
            self.assertIsNone(document["candidateCount"])
            self.assertIsNone(document["unreviewedCount"])
            self.assertIsNone(document["approvedInputCount"])
            self.assertIsNone(document["latestParsingRun"])
            self.assertEqual(document["documentVersionIds"], [])
        # A positive version identity can establish association without a hash;
        # the other unidentified document must remain unknown.
        value["sourceDocuments"][0]["documentVersionId"] = "document-1"
        self.reply(value)
        result = self.api.list_documents("2")
        self.assertEqual(result["documents"][0]["candidateCount"], 1)
        self.assertIsNone(result["documents"][1]["candidateCount"])

    def test_bare_locator_is_explicitly_unverified_rather_than_evidence_bound(self):
        value = workspace(count=1)
        value["packages"][0]["candidates"][0]["evidence"] = [{"sheetName": "sheet", "cellReference": "B4"}]
        self.reply(value)
        result = self.api.list_candidates("2")
        self.assertEqual(result["items"][0]["evidenceBindingStatus"], "unverified")

    def test_wrong_project_run_document_or_source_hash_is_never_displayed(self):
        mutations = (
            lambda value: value.update(projectId="other-project"),
            lambda value: value["packages"][0]["candidates"][0].update(projectId="other-project"),
            lambda value: value["packages"][0]["candidates"][0].update(parsingRunId="another-run"),
            lambda value: value["packages"][0]["candidates"][0].update(documentVersionId="another-document"),
            lambda value: value["packages"][0]["candidates"][0]["evidence"][0].update(fileSha256=OTHER_ARCHIVE),
            lambda value: value["packages"][0]["candidates"][0]["evidence"][0].update(documentVersionId="another-document"),
            lambda value: value["packages"][0]["documentVersion"].update(sourceHash=OTHER_ARCHIVE),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = workspace(count=1)
                mutation(value)
                self.reply(value)
                with self.assertRaises(HostedProjectApiError):
                    self.api.list_candidates("2")

    def test_workbook_read_uses_fixed_get_routes_and_preserves_literal_formula_and_cache_states(self):
        self.reply(workbook())
        result = self.api.workbook_sheets("2", ARCHIVE)
        self.assertTrue(result["readOnly"])
        value = workbook("cells")
        value["rows"][0]["cells"].append({"coordinate": "C4", "formula": "=SUM(B4:B4)",
                                            "cachedValue": None, "valueState": "formula_cache_missing"})
        self.reply(value)
        result = self.api.workbook_cells("2", ARCHIVE, sheet_name="대출금 & 만기", cell_reference="B4", expected_archive_version="archive-v1")
        self.assertEqual(result["rows"], value["rows"])
        self.assertFalse(result["approvalEligible"])
        request = self.opener.open.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).path, f"/projects/2/source-documents/{ARCHIVE}/workbook/cells")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {
            "sheet_name": ["대출금 & 만기"], "cell_reference": ["B4"], "context_rows": ["0"], "context_columns": ["0"]})
        self.assertEqual(request.get_method(), "GET")
        self.assertNotIn(TOKEN, request.full_url)

    def test_malformed_upstream_records_are_not_silently_counted_as_zero_or_approved(self):
        mutations = (
            lambda value: value["packages"][0].update(candidates={}),
            lambda value: value["packages"][0].update(reviewDecisions={}),
            lambda value: value["packages"][0]["candidates"][0].update(candidateId=["candidate-000"]),
            lambda value: value["packages"][0]["candidates"][0].update(evidence={}),
            lambda value: value["packages"][0].update(reviewDecisions=[{"candidateId": "candidate-000", "decisionSequence": "bad"}]),
            lambda value: value["packages"][0].update(canonicalSelections=[{"candidateId": "candidate-000", "selectionId": []}]),
            lambda value: value["packages"][0].update(approvedInputSnapshots=[{"selectionIds": {}}]),
            lambda value: value["packages"][0]["parsingRun"].update(documentVersionId="different-version"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = workspace(count=1)
                mutation(value)
                self.reply(value)
                with self.assertRaises(HostedProjectApiError):
                    self.api.list_candidates("2")

    def test_stale_or_different_workbook_is_not_rendered(self):
        for update in ({"sourceSha256": OTHER_ARCHIVE}, {"archiveVersionId": "archive-v2"},
                       {"approvalEligible": True}, {"sheetName": "other-sheet"},
                       {"formulaPolicy": "executed"}):
            with self.subTest(update=update):
                value = {**workbook("cells"), **update}
                self.reply(value)
                with self.assertRaises(HostedProjectApiError):
                    self.api.workbook_cells("2", ARCHIVE, sheet_name="대출금 & 만기", cell_reference="B4", expected_archive_version="archive-v1")

    def test_bounds_and_paths_are_rejected_before_transport(self):
        for project in ("../2", "2?token=secret", "2/another", ["2"]):
            with self.subTest(project=project), self.assertRaises(HostedProjectApiError):
                self.api.list_documents(project)
        for archive in ("../source", "a" * 63, ARCHIVE + "/content", [ARCHIVE]):
            with self.subTest(archive=archive), self.assertRaises(HostedProjectApiError):
                self.api.workbook_sheets("2", archive)
        for reference in ("B4:A1", "A1:T21", "A1:XFD2", "A10001", "A:A", "1:2", "A0", "A1\n"):
            with self.subTest(reference=reference), self.assertRaises(HostedProjectApiError):
                self.api.workbook_cells("2", ARCHIVE, sheet_name="sheet", cell_reference=reference)
        for offset, limit in ((-1, 1), (0, 101), (True, 1), (0, False), (0, 0)):
            with self.subTest(offset=offset, limit=limit), self.assertRaises(HostedProjectApiError):
                self.api.list_candidates("2", offset=offset, limit=limit)
        self.opener.open.assert_not_called()

    def test_expired_get_refreshes_once_and_permission_denial_never_refreshes(self):
        provider = MagicMock(side_effect=[TOKEN, "new.synthetic.token"])
        api = HostedProjectApi(ORIGIN, credential_provider=provider)
        opener = api._opener = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(workspace()).encode()
        opener.open.side_effect = [HTTPError(ORIGIN, 401, "private-error", {}, io.BytesIO()), response]
        self.assertEqual(api.list_candidates("2")["totalCount"], 31)
        self.assertEqual(provider.call_args_list[0].kwargs, {"force_refresh": False})
        self.assertEqual(provider.call_args_list[1].kwargs, {"force_refresh": True})
        self.assertEqual(opener.open.call_args_list[1].args[0].get_header("Authorization"), "Bearer new.synthetic.token")

        for status in (401, 403):
            provider.reset_mock(side_effect=True)
            provider.return_value = TOKEN
            opener.open.side_effect = HTTPError(ORIGIN, status, TOKEN, {}, io.BytesIO(TOKEN.encode()))
            with self.subTest(status=status), self.assertRaises(HostedProjectApiError) as raised:
                api.list_documents("2")
            self.assertEqual(provider.call_count, 2 if status == 401 else 1)
            self.assertEqual(raised.exception.status_code, status)
            self.assertNotIn(TOKEN, str(raised.exception))

    def test_land_mutation_is_not_automatically_replayed_after_401(self):
        provider = MagicMock(return_value=TOKEN)
        api = HostedProjectApi(ORIGIN, credential_provider=provider)
        api._opener = MagicMock()
        api._opener.open.side_effect = HTTPError(ORIGIN, 401, TOKEN, {}, io.BytesIO())
        with self.assertRaises(HostedProjectApiError):
            api.save_land_legal_summary("2", sections=["height"], expected_preview_hash="e" * 64)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(api._opener.open.call_count, 1)

    def test_recent_projects_are_server_scoped_and_dont_return_full_private_project_payload(self):
        self.reply({"schemaVersion": "realytics-recent-work-v2", "items": [
            {"projectId": "2", "projectName": "합성 검증", "sourceDocumentCount": 2, "unrelatedPrivateField": "not-for-output"}]})
        result = self.api.list_projects(limit=5)
        self.assertTrue(result["recentOnly"])
        self.assertEqual(result["items"][0]["projectId"], "2")
        self.assertNotIn("unrelatedPrivateField", result["items"][0])
        self.assertEqual(self.opener.open.call_args.args[0].full_url, f"{ORIGIN}/projects/recent-work?limit=5")

    def test_unsafe_or_oversized_responses_never_reach_the_model(self):
        for raw in (b'NaN', b'[]', b'{"secret":"' + TOKEN.encode() + b'"}', b'x' * (4 * 1024 * 1024 + 1)):
            self.opener.open.return_value.__enter__.return_value.read.return_value = raw
            with self.subTest(raw_size=len(raw)), self.assertRaises(HostedProjectApiError) as raised:
                self.api.list_documents("2")
            self.assertNotIn(TOKEN, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
