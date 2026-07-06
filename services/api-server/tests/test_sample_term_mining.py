from use_cases.sample_term_mining import (
    TextSource,
    build_sample_term_mining_package,
    extract_term_surface_items_from_text,
)


def _glossary_terms():
    return [
        {
            "termId": "total_sales_revenue",
            "preferredTerm": "total_sales_revenue",
            "displayNameKo": "총분양수입",
            "displayNameEn": "Total sales revenue",
            "aliases": ["분양수입", "총매출", "분양매출"],
            "canonicalField": "feasibility_input.total_revenue",
            "category": "feasibility",
            "dataType": "money",
            "unit": "KRW",
            "reviewStatus": "approved",
        },
        {
            "termId": "senior_tranche",
            "preferredTerm": "senior_tranche",
            "displayNameKo": "선순위 Tranche",
            "displayNameEn": "Senior Tranche",
            "aliases": ["Tr.A", "선순위", "Senior"],
            "canonicalField": "pf_structure.tranches.senior.amount",
            "category": "pf_structure",
            "dataType": "money",
            "unit": "KRW",
            "reviewStatus": "approved",
        },
    ]


def test_extract_term_surface_items_preserves_locator_and_quality_payload():
    items = extract_term_surface_items_from_text(
        "총분양수입 1,650억원\nTr.A 선순위 대출금 630억원\n입주확약서 제출 필요",
        source_name="sample.pdf",
        source_type="pdf",
        source_locator={"documentId": "doc-1"},
    )

    surfaces = {item["surface"] for item in items}
    assert "총분양수입 1" in surfaces or "총분양수입" in " ".join(surfaces)
    assert "Tr.A" in surfaces
    assert any("입주확약서" in surface for surface in surfaces)

    first = items[0]
    assert first["sourceLocator"]["documentId"] == "doc-1"
    assert first["sourceLocator"]["sourceName"] == "sample.pdf"
    assert "termSurface" in first
    assert "normalizedSurface" in first["termSurface"]
    assert "textQualityFlags" in first["termSurface"]


def test_sample_term_mining_classifies_exact_and_new_terms():
    package = build_sample_term_mining_package(
        [
            TextSource(
                source_name="pf-review.pdf",
                source_type="pdf",
                text="분양수입\nTr.A\n입주확약서 제출 필요\n신규 임대차 보증 조건",
                source_locator={"documentId": "doc-2"},
            )
        ],
        _glossary_terms(),
        project_id="project-1",
        run_id="run-1",
    )

    by_surface = {result["surface"]: result for result in package["results"]}
    assert by_surface["분양수입"]["action"] == "matched"
    assert by_surface["분양수입"]["term"]["canonicalField"] == "feasibility_input.total_revenue"
    assert by_surface["Tr.A"]["action"] == "matched"
    assert by_surface["Tr.A"]["term"]["termId"] == "senior_tranche"
    assert any(result["action"] == "needs_ai_review" for result in package["results"])
    assert package["summary"]["matchedCount"] >= 2
    assert package["summary"]["needsAiReviewCount"] >= 1
