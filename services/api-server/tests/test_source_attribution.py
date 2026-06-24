from __future__ import annotations

from agents.source_attribution import attribute_source
from use_cases.term_annotation import _resolve_value, _enrich_annotation


def test_business_source_labels():
    assert attribute_source("feasibility_input.construction_cost", None, {})["sourceLabel"] == "사업수지 (시행사 제공 Excel)"
    assert attribute_source("land.pnu", None, {})["sourceLabel"].startswith("공공데이터포털")
    assert "공시지가" in attribute_source("land.official_land_price", None, {})["sourceLabel"]
    assert "등기" in attribute_source("registry.encumbrance", None, {})["sourceLabel"]
    assert attribute_source("pf_structure.tranches", None, {})["sourceLabel"] == "금융조건 (대주 제시)"
    assert attribute_source("regulation.applied_far", None, {})["sourceLabel"].startswith("토지이용계획")
    assert attribute_source("something.unknown", None, {})["sourceLabel"] == "시행사 제공자료"


def test_evidence_ref_from_locator():
    assert "Excel" in attribute_source("feasibility_input.total_revenue", None, {"sheet": "2.현금흐름", "cell": "C12"})["evidenceRef"]
    assert "p.7" in attribute_source("land.address", None, {"page": 7})["evidenceRef"]
    assert "공공데이터" in attribute_source("land.pnu", None, {"sourceType": "api_row"})["evidenceRef"]


def test_document_link_when_archive_present():
    out = attribute_source("land.address", None, {"archiveId": "abc123"}, project_id="p1")
    assert out["sourceUrl"] == "/projects/p1/source-documents/abc123/download"
    assert attribute_source("land.address", None, {}, project_id="p1")["sourceUrl"] is None


def test_resolve_value_dotted_and_indexed():
    data = {"feasibility_input": {"total_revenue": 1000}, "pf_structure": [{"tranche": "Tr.A"}]}
    assert _resolve_value(data, "feasibility_input.total_revenue") == 1000
    assert _resolve_value(data, "pf_structure[0].tranche") == "Tr.A"
    assert _resolve_value(data, "missing.path") is None


def test_enrich_annotation_attaches_business_source_and_value():
    project = {"feasibility_input": {"total_revenue": 165_600_000_000}}
    ann = {"canonicalField": "feasibility_input.total_revenue", "sourceField": "feasibility_input.total_revenue", "sourceLocator": {"sheet": "분양", "cell": "B3"}}
    enriched = _enrich_annotation(ann, project, "p1")
    assert enriched["sourceLabel"] == "사업수지 (시행사 제공 Excel)"
    assert enriched["evidenceValue"] == 165_600_000_000
    assert "Excel" in enriched["evidenceRef"]
