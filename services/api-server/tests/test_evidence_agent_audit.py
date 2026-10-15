"""Offline regressions for evidence handling, legal input types and visual QA."""

import math

import fitz
import pytest

from agents.data_quality_agent import DataQualityAgent
from agents.legal_developability_agent import LegalDevelopabilityAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.visual_qa_agent import VisualQAAgent
from agents.report_agent import ReportAgent
from agents.risk_register_agent import RiskRegisterAgent
from agents.marketability_agent import MarketabilityAgent, _normalize_source
from agents.market_evidence_agent import _source_metadata, _to_int
from agents.credit_document_agent import _pct_str as credit_percent
from agents.term_sheet_agent import _pct_str as term_percent
from agents.land_ledger_enrichment_agent import LandLedgerEnrichmentAgent, _index_records, _lookup_record


@pytest.mark.parametrize("legal, applied, expected", [("200", "90", "clear"), ("900", "1000", "blocking")])
def test_zoning_compares_numeric_strings_as_numbers(legal, applied, expected):
    check = LegalReviewAgent()._zoning_check({"regulation": {
        "legal_far": legal, "applied_far": applied, "legal_bcr": 60, "applied_bcr": 50,
    }})
    assert check["severity"] == expected


@pytest.mark.parametrize("regulation", [None, {}, {"zoning": "residential"}])
def test_zoning_without_numeric_evidence_is_caution(regulation):
    assert LegalReviewAgent()._zoning_check({"regulation": regulation})["severity"] == "caution"


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, True])
def test_nonfinite_or_boolean_quality_evidence_cannot_pass(invalid):
    check = DataQualityAgent()._numeric_consistency(
        key="land_area", label="Land area", values=[("source_a", invalid), ("source_b", 100)],
        unit="sqm", warning_threshold=0.1, blocking_threshold=0.2, missing_message="Missing comparison",
    )
    assert check["status"] in {"warning", "missing_data"}


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity"])
def test_invalid_legal_limit_cannot_establish_feasibility(invalid):
    result = LegalDevelopabilityAgent().analyze({
        "land": {"area_sqm": 100},
        "regulation": {"zoning": "residential", "legal_far": invalid, "applied_far": 100, "legal_bcr": 60, "applied_bcr": 50},
        "schedule": {"permit_approval": "2026-10-01"},
    })
    assert result["status"] == "not_assessable"
    assert "법정 용적률" in result["missingRequiredInputs"]


def _pdf(page_count):
    with fitz.open() as document:
        for index in range(page_count):
            document.new_page(width=120, height=120).insert_text((15, 40), f"Page {index + 1}")
        return document.tobytes()


def test_initial_visual_baseline_is_not_a_pass(tmp_path):
    result = VisualQAAgent(tmp_path).run_visual_regression(_pdf(1), "audit", dpi=72)
    assert result["status"] == "baseline_created"
    assert result["pages_compared"] == 0
    assert result["matched_pages"] == 0


@pytest.mark.parametrize("before, after", [(1, 2), (2, 1)])
def test_visual_regression_detects_page_count_changes(tmp_path, before, after):
    agent = VisualQAAgent(tmp_path)
    agent.run_visual_regression(_pdf(before), "audit", dpi=72)
    result = agent.run_visual_regression(_pdf(after), "audit", dpi=72)
    assert result["status"] == "regression_detected"
    assert result["regression_pages"] == 1
    assert len(list(tmp_path.glob("baseline_*.png"))) == before


@pytest.mark.parametrize("report_id", ["../../outside", "x/../../outside", "x\\..\\outside", ""])
def test_visual_report_id_cannot_create_nested_or_escaped_paths(tmp_path, report_id):
    agent = VisualQAAgent(tmp_path / "baselines")
    with pytest.raises(ValueError, match="report_id"):
        agent.run_visual_regression(_pdf(1), report_id, dpi=72)
    assert list((tmp_path / "baselines").iterdir()) == []


def test_empty_report_keeps_financials_unconfirmed():
    report = ReportAgent().generate_structured_report({})
    sections = report["sections"]
    assert sections["financialTerms"]["totalAmount"] is None
    assert sections["financialTerms"]["totalAmountMillion"] is None
    for key in ("ltv", "salesValueLtv", "exitSalesRate", "developmentProfit", "developmentProfitMillion", "profitMargin"):
        assert sections["businessFeasibility"]["metrics"][key] is None
    assert sections["summaryCashFlow"]["status"] == "not_assessable"
    assert sections["summaryCashFlow"]["rows"] == []
    assert sections["salesPaymentSchedule"][0]["ratio"] is None


def test_report_does_not_invent_ownership_or_guarantees():
    sections = ReportAgent().generate_structured_report({"land": {"area_sqm": 100}})["sections"]
    assert sections["landSecuring"]["parcels"][0]["share"] is None
    assert "미확인" in sections["claimPreservation"]["summary"]


def test_report_expected_test_values_do_not_override_source_calculations():
    data = {"feasibility_input": {"total_revenue": 100, "total_cost": 90}, "expected_results": {"development_profit": 999, "profit_margin": 9.99}}
    metrics = ReportAgent().generate_structured_report(data)["sections"]["businessFeasibility"]["metrics"]
    assert metrics["developmentProfit"] == 10
    assert metrics["profitMargin"] == 0.1
    markdown = ReportAgent().generate_report(data)
    assert "999" not in markdown


def test_empty_markdown_does_not_assert_repayment_or_market_success():
    markdown = ReportAgent().generate_report({})
    assert "충분히 커버 가능" not in markdown
    assert "달성이 무난" not in markdown
    assert "developmentProfit: -" in markdown
    assert "저장된 현금흐름 계산이 없습니다" in markdown


def test_empty_risk_register_cannot_recommend_approval():
    result = RiskRegisterAgent().build({}, {})
    assert result["status"] == "needs_review"
    assert result["creditReviewInputs"]["recommendedDecisionBand"] == "insufficient_evidence"


def test_market_comparable_preserves_zero_distance():
    row = MarketabilityAgent()._normalize_comparables([{"pricePerPy": 100, "distanceM": 0}], "apartment")[0]
    assert row["distanceM"] == 0
    assert row["adjustedPricePerPy"] == 100


@pytest.mark.parametrize("confidence", [math.nan, math.inf, -0.1, 1.1])
@pytest.mark.parametrize("normalize", [_source_metadata, _normalize_source])
def test_invalid_confidence_is_missing_source_metadata(normalize, confidence):
    source = normalize({"sourceName": "test", "sourceType": "manual_upload", "sourceUrlOrEvidenceId": "fixture:1", "baseDate": "2026-01-01", "collectedAt": "2026-01-02", "licenseOrUsageNote": "synthetic", "confidence": confidence})
    assert source["metadataStatus"] == "partial"
    assert "confidence" in source["missingSourceMetadata"]
    assert math.isfinite(source["confidence"])


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_market_locator_and_credit_formatters_reject_nonfinite_values(value):
    assert _to_int(value) is None
    assert credit_percent(value) == "—"
    assert term_percent(value) == "—"


def test_parcel_sequence_fallback_cannot_override_conflicting_pnu():
    record = {"pnu": "1111111111111111111", "sequence": 1, "pricePerSqm": 100}
    records = _index_records([record])
    assert _lookup_record(records, {"pnu": "2222222222222222222", "sequence": 1}) is None
    assert _lookup_record(records, {"pnu": record["pnu"], "sequence": 5}) is record


@pytest.mark.parametrize("fetched_at", [None, "2020-01-02T00:00:00+00:00"])
def test_land_evidence_does_not_invent_fresh_fetch_time(fetched_at):
    parcel = {"pnu": "1111111111111111111", "areaSqm": 100}
    agent = LandLedgerEnrichmentAgent()
    record = {"pricePerSqm": 100, "baseYear": 2020, "fetchedAt": fetched_at}
    agent._apply_official_price_record(parcel, record)
    agent._apply_registry_record(parcel, {"issuedAt": "2020-01-01", "fetchedAt": fetched_at})
    assert parcel["officialLandPriceFetchedAt"] == fetched_at
    assert parcel["registryEvidence"]["fetchedAt"] == fetched_at
    for evidence in parcel["evidence"]:
        assert evidence["fetchedAt"] == fetched_at
        assert evidence["recordedAt"]
