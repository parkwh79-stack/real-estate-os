import storage
import json
from copy import deepcopy
from db.repositories.report_repository import ReportRepository
import use_cases.report_generation as reports
from use_cases.report_generation import build_paragraph_evidence_links, _build_section_link


def test_section_link_attaches_pod_findings_by_category():
    pod_findings = {
        "pf_credit": [{"findingId": "f1", "category": "risk_note", "severity": "blocking", "message": "선순위 부족"}],
        "land_legal": [{"findingId": "f2", "category": "missing_evidence", "severity": "high", "message": "등기 미비"}],
    }
    # financialTerms is category 'pf' -> pf_credit pod
    pf_link = _build_section_link("p1", {"key": "financialTerms", "title": "금융조건"}, [], pod_findings)
    assert [f["findingId"] for f in pf_link["podAiReviewFindings"]] == ["f1"]
    assert pf_link["podAiReviewBlocking"] is True

    # landSecuring is category 'land' -> land_legal pod (high, not blocking)
    land_link = _build_section_link("p1", {"key": "landSecuring", "title": "토지확보"}, [], pod_findings)
    assert [f["findingId"] for f in land_link["podAiReviewFindings"]] == ["f2"]
    assert land_link["podAiReviewBlocking"] is False

    # marketAnalysis is category 'market' -> market_product pod (no findings here)
    market_link = _build_section_link("p1", {"key": "marketAnalysis", "title": "시장"}, [], pod_findings)
    assert market_link["podAiReviewFindings"] == []


def test_build_paragraph_links_surfaces_pod_findings_live(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "rep.sqlite3"))
    storage.upsert_pod_ai_review_record("p9", {
        "reviewId": "r1", "podKey": "pf_credit", "reviewStatus": "blocking",
        "findings": [{"findingId": "x1", "category": "risk_note", "severity": "blocking", "message": "선순위 원금 부족"}],
    })
    report = {"sections": {"financialTerms": {"content": "..."}, "marketAnalysis": {"content": "..."}}}
    result = build_paragraph_evidence_links("p9", report)
    assert result["generatedFrom"] == "deal_state_records+pod_ai_reviews"
    assert result["podAiReviewFindingCount"] >= 1
    pf = next((l for l in result["links"] if l["sectionKey"] == "financialTerms"), None)
    assert pf and any(f["findingId"] == "x1" for f in pf["podAiReviewFindings"])


def test_resolved_review_not_linked(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "rep2.sqlite3"))
    storage.upsert_pod_ai_review_record("p8", {
        "reviewId": "r2", "podKey": "pf_credit", "reviewStatus": "approved",
        "findings": [{"findingId": "y1", "category": "risk_note", "severity": "blocking", "message": "해소됨"}],
    })
    result = build_paragraph_evidence_links("p8", {"sections": {"financialTerms": {"content": "x"}}})
    assert result["podAiReviewFindingCount"] == 0


def test_all_six_pods_keep_source_and_run_links_after_report_reopen_and_json_export(monkeypatch):
    pods = ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"]
    reviews = [{"reviewId": f"review:{pod}", "podKey": pod, "runId": "original-chief-run",
        "sourceArtifactKey": f"artifact:{pod}", "reviewStatus": "needs_revision",
        "inputEvidenceIds": [f"evidence:{pod}"], "inputAssumptionIds": ["assumption:original"],
        "deterministicFindings": [{"findingId": f"finding:{pod}", "category": "missing_evidence",
            "severity": "high", "message": f"{pod} 원본 확인", "recommendedAction": "담당자 대조",
            "sourceLocators": [{"documentName": "합성 검토자료.xlsx", "sheet": "입력", "cell": "A7"}]}]} for pod in pods]
    monkeypatch.setattr(reports, "list_pod_ai_review_records", lambda *args, **kwargs: deepcopy(reviews))
    monkeypatch.setattr(reports, "list_deal_state_records", lambda *args, **kwargs: [])
    project = {"chief_orchestration_state": {"podRuns": {pod: {"artifactKeys": [f"artifact:{pod}"]} for pod in pods}}}
    repository = ReportRepository({}, lambda: None)
    record = reports.generate_and_save_pf_report_use_case(repository, "p", project)["reportRecord"]
    links = deepcopy(record["report"]["paragraphEvidenceLinks"])
    findings = {f["podKey"]: f for link in links["links"] for f in link["podAiReviewFindings"]}
    assert set(findings) == set(pods)
    for pod, finding in findings.items():
        assert finding["artifactBindingStatus"] == "matched"
        assert finding["sourceArtifactKeys"] == [f"artifact:{pod}"]
        assert finding["runId"] == "original-chief-run"
        assert finding["sourceLocators"][0]["cell"] == "A7"
        assert finding["inputEvidenceIds"] == [f"evidence:{pod}"]
    reviews.clear()
    project["chief_orchestration_state"]["podRuns"] = {}
    reopened = repository.get("p", record["report_id"])
    assert reopened["report"]["paragraphEvidenceLinks"] == links
    exported = reports.export_report_use_case(reopened, {"format": "json"})
    assert json.loads(exported["content"])["report"]["paragraphEvidenceLinks"] == links


def test_report_prefers_matching_artifact_review_over_newer_unrelated_review(monkeypatch):
    def review(key, updated, message):
        return {"podKey": "pf_credit", "sourceArtifactKey": key, "updatedAt": updated,
            "findings": [{"findingId": message, "message": message, "severity": "blocking", "category": "risk_note"}]}
    monkeypatch.setattr(reports, "list_pod_ai_review_records", lambda *args, **kwargs: [
        review("current", "2026-10-14", "current finding"), review("other", "2026-10-15", "other finding")])
    monkeypatch.setattr(reports, "list_deal_state_records", lambda *args, **kwargs: [])
    report = {"sections": {"financialTerms": {}}}
    links = reports.build_paragraph_evidence_links("p", report, expected_artifacts={"pf_credit": ["current"]})
    assert links["links"][0]["podAiReviewFindings"][0]["message"] == "current finding"
    unmatched = reports.build_paragraph_evidence_links("p", report, expected_artifacts={"pf_credit": ["not-reviewed"]})
    assert unmatched["links"][0]["podAiReviewFindings"][0]["artifactBindingStatus"] == "mismatch"
    assert unmatched["links"][0]["podAiReviewBlocking"] is False
    monkeypatch.setattr(reports, "list_pod_ai_review_records", lambda *args, **kwargs: [
        review(None, "2026-10-15", "unbound finding")])
    unbound = reports.build_paragraph_evidence_links("p", report, expected_artifacts={"pf_credit": ["current"]})
    assert unbound["links"][0]["podAiReviewFindings"][0]["artifactBindingStatus"] == "unverified"


def test_report_lookup_failure_is_captured_without_disguising_an_empty_review(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private storage credentials")
    monkeypatch.setattr(reports, "list_pod_ai_review_records", fail)
    monkeypatch.setattr(reports, "list_deal_state_records", lambda *args, **kwargs: [])
    links = reports.build_paragraph_evidence_links("p", {"sections": {"financialTerms": {}}})
    assert links["podReviewReadStatus"] == "unavailable"
    assert "지적사항이 없다는 뜻은 아닙니다" in links["podReviewReadMessage"]
    assert "private" not in str(links)


def test_report_does_not_truncate_connected_pod_findings():
    findings = [{"findingId": str(i), "severity": "high"} for i in range(12)]
    link = _build_section_link("p", {"key": "financialTerms"}, [], {"pf_credit": findings})
    assert len(link["podAiReviewFindings"]) == 12
