from agents.term_annotation_agent import TermAnnotationAgent


GLOSSARY = [
    {"termId": "responsible_completion", "preferredTerm": "책임준공확약", "displayNameKo": "책임준공확약",
     "aliases": ["책임준공", "Completion Undertaking"], "canonicalField": "credit_enhancement.responsible_completion",
     "canonicalPath": "credit_enhancement.responsible_completion", "category": "credit_enhancement",
     "dataType": "boolean", "reviewStatus": "approved"},
    {"termId": "senior_tranche", "preferredTerm": "선순위", "displayNameKo": "선순위",
     "aliases": ["Tr.A", "Senior Loan", "A Tranche"], "canonicalField": "pf_structure.tranches.senior.amount",
     "canonicalPath": "pf_structure.tranches.senior.amount", "category": "pf_structure",
     "dataType": "currency", "reviewStatus": "approved"},
    {"termId": "cap_rate", "preferredTerm": "Cap Rate", "displayNameKo": "자본환원율",
     "aliases": ["Cap Rate", "Capitalization Rate"], "canonicalField": "real_asset.capRate",
     "canonicalPath": "real_asset.capRate", "category": "real_asset", "dataType": "rate", "reviewStatus": "approved"},
    {"termId": "ltv", "preferredTerm": "LTV", "displayNameKo": "담보인정비율", "aliases": ["LTV", "Loan-to-Value"],
     "canonicalField": "pf_metrics.ltv", "canonicalPath": "pf_metrics.ltv", "category": "pf_metrics",
     "dataType": "rate", "reviewStatus": "approved"},
    # Not approved -> must be ignored.
    {"termId": "draft_term", "preferredTerm": "초안용어", "aliases": ["초안용어"], "canonicalField": "x.y",
     "reviewStatus": "pending_review"},
]


def test_canonical_field_match_from_source_locator():
    parsed = {
        "source_locators": {
            "pf_metrics.ltv": {"pageNumber": 12, "fieldName": "pf_metrics.ltv", "textExcerpt": "LTV 55%"},
        },
    }
    result = TermAnnotationAgent().annotate(parsed, GLOSSARY)
    ltv = next(a for a in result["annotations"] if a["termId"] == "ltv")
    assert ltv["canonicalField"] == "pf_metrics.ltv"
    assert ltv["matchType"] == "canonical_field"
    assert ltv["sourceLocator"]["pageNumber"] == 12
    assert ltv["status"] == "mapped"


def test_alias_text_match_in_covenants_and_tranche():
    parsed = {
        "pf_structure": [{"tranche": "Tr.A 선순위", "amount": 1200}],
        "covenants": {"claim_preservation": ["시공사 책임준공 확약", "관리신탁 설정"]},
    }
    result = TermAnnotationAgent().annotate(parsed, GLOSSARY)
    terms = {a["termId"] for a in result["annotations"] if a["status"] == "mapped"}
    assert "senior_tranche" in terms  # matched "Tr.A"
    assert "responsible_completion" in terms  # matched "책임준공"
    rc = next(a for a in result["annotations"] if a["termId"] == "responsible_completion")
    assert rc["matchType"] == "alias_text"
    assert rc["sourceField"].startswith("covenants.claim_preservation")


def test_unmapped_candidate_emitted_for_unknown_covenant():
    parsed = {"covenants": {"eod": ["알 수 없는 특수 조항 XYZ"]}}
    result = TermAnnotationAgent().annotate(parsed, GLOSSARY)
    unmapped = [a for a in result["annotations"] if a["status"] == "unmapped_candidate"]
    assert any("XYZ" in a["surface"] for a in unmapped)
    assert all(a["termId"] is None for a in unmapped)


def test_pending_terms_ignored_and_summary():
    parsed = {"source_locators": {"x.y": {"fieldName": "x.y"}},
              "covenants": {"claim_preservation": ["Cap Rate 5% 가정"]}}
    result = TermAnnotationAgent().annotate(parsed, GLOSSARY)
    assert all(a["termId"] != "draft_term" for a in result["annotations"])  # pending ignored
    assert any(a["termId"] == "cap_rate" for a in result["annotations"])
    summary = result["summary"]
    assert summary["mappedCount"] >= 1
    assert summary["distinctCanonicalFields"] >= 1


def test_empty_input_safe():
    result = TermAnnotationAgent().annotate({}, GLOSSARY)
    assert result["annotations"] == []
    assert result["summary"]["mappedCount"] == 0


def test_canonical_field_values_are_tagged_without_locator():
    glossary = [
        {
            "termId": "land_area_sqm",
            "preferredTerm": "대지면적",
            "displayNameKo": "대지면적",
            "aliases": ["대지면적"],
            "canonicalField": "land.area_sqm",
            "canonicalPath": "land.area_sqm",
            "category": "land",
            "dataType": "area",
            "reviewStatus": "approved",
        },
        {
            "termId": "floor_area_ratio",
            "preferredTerm": "용적률",
            "displayNameKo": "용적률",
            "aliases": ["용적률"],
            "canonicalField": "regulation.applied_far",
            "canonicalPath": "regulation.applied_far",
            "category": "regulation",
            "dataType": "rate",
            "reviewStatus": "approved",
        },
        {
            "termId": "construction_cost",
            "preferredTerm": "공사비",
            "displayNameKo": "공사비",
            "aliases": ["공사비"],
            "canonicalField": "feasibility_input.construction_cost",
            "canonicalPath": "feasibility_input.construction_cost",
            "category": "feasibility",
            "dataType": "currency",
            "reviewStatus": "approved",
        },
    ]
    parsed = {
        "land": {"area_sqm": 4691.9},
        "regulation": {"applied_far": 6.5},
        "feasibility_input": {"construction_cost": 98000000000},
    }

    result = TermAnnotationAgent().annotate(parsed, glossary)
    mapped = {a["termId"]: a for a in result["annotations"] if a["status"] == "mapped"}

    assert mapped["land_area_sqm"]["matchType"] == "canonical_field_value"
    assert mapped["land_area_sqm"]["sourceLocator"]["sourceType"] == "parsed_field_path"
    assert mapped["floor_area_ratio"]["canonicalField"] == "regulation.applied_far"
    assert mapped["construction_cost"]["sourceField"] == "feasibility_input.construction_cost"


def test_alias_text_scans_land_legal_cost_and_schedule_roots():
    glossary = [
        {
            "termId": "pledge_right",
            "preferredTerm": "질권",
            "displayNameKo": "질권",
            "aliases": ["질권"],
            "canonicalField": "legal.security.pledge",
            "canonicalPath": "legal.security.pledge",
            "category": "legal",
            "dataType": "boolean",
            "reviewStatus": "approved",
        },
        {
            "termId": "land_ledger",
            "preferredTerm": "토지조서",
            "displayNameKo": "토지조서",
            "aliases": ["토지조서"],
            "canonicalField": "landLedger",
            "canonicalPath": "landLedger",
            "category": "land",
            "dataType": "document",
            "reviewStatus": "approved",
        },
        {
            "termId": "completion",
            "preferredTerm": "준공",
            "displayNameKo": "준공",
            "aliases": ["준공"],
            "canonicalField": "schedule.completion",
            "canonicalPath": "schedule.completion",
            "category": "schedule",
            "dataType": "date",
            "reviewStatus": "approved",
        },
    ]
    parsed = {
        "legal": {"security": {"note": "질권 설정 여부 확인 필요"}},
        "landLedger": {"reviewNote": "토지조서 기준 소유자 지분 확인"},
        "schedule": {"note": "준공 전 인허가 일정 관리"},
    }

    result = TermAnnotationAgent().annotate(parsed, glossary)
    mapped = {(a["termId"], a["sourceField"]) for a in result["annotations"] if a["status"] == "mapped"}

    assert ("pledge_right", "legal.security.note") in mapped
    assert ("land_ledger", "landLedger.reviewNote") in mapped
    assert ("completion", "schedule.note") in mapped


def _seed_terms():
    import json
    from pathlib import Path
    seed = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "real_estate_glossary_seed.json"
    return json.loads(seed.read_text(encoding="utf-8"))["terms"]


def test_sample_corpus_terms_are_in_seed_and_annotate():
    """Standard PF terms learned from the real sample corpus resolve via the seed."""
    terms = _seed_terms()
    by_id = {t["termId"]: t for t in terms}
    for term_id, canon in [
        ("contractor", "contractor.name"),
        ("developer_borrower", "developer.name"),
        ("floor_area_ratio", "regulation.applied_far"),
        ("building_coverage_ratio", "regulation.applied_bcr"),
        ("completion", "schedule.completion"),
        ("sales_rate", "marketability.salesRate"),
        ("bridge_loan", "pf_structure.facilityType.bridge"),
    ]:
        assert term_id in by_id, f"{term_id} missing from glossary seed"
        assert by_id[term_id]["canonicalField"] == canon

    parsed = {
        "pf_structure": [{"tranche": "본PF 선순위"}],
        "covenants": {
            "claim_preservation": ["시공사 책임준공 및 연대보증", "관리형토지신탁 설정"],
            "drawdown_conditions": ["용적률 및 건폐율 인허가 완료", "준공 후 정산"],
        },
    }
    result = TermAnnotationAgent().annotate(parsed, terms)
    mapped = {a["termId"] for a in result["annotations"] if a["status"] == "mapped"}
    # sample-derived standard terms now flow through annotation
    assert {"contractor", "joint_guarantee", "floor_area_ratio", "building_coverage_ratio", "completion", "main_pf"} <= mapped
