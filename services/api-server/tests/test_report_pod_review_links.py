import storage
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
