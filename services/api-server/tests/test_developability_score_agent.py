from agents.developability_score_agent import DevelopabilityScoreAgent


FULL = {
    "regulation": {"applied_far": 600, "legal_far": 700, "zoning": "중심상업지역"},
    "land": {"pnu": "1111010100100010000"},
    "landEvidence": {"officialLandPrice": 1000000, "registry": [{"x": 1}], "acquisitionAreaRatio": 1.0},
    "feasibility_input": {"total_revenue": 2800, "total_cost": 2300},
    "marketability": {"grade": "B", "score": 78},
    "land_acquisition_ratio": 1.0,
}


def test_full_data_scores_and_grades():
    result = DevelopabilityScoreAgent().score(FULL)
    assert 0 <= result["overallScore"] <= 100
    assert result["grade"] in {"A", "B", "C", "D", "E"}
    assert all(result["dimensions"][d]["available"] for d in ("regulation", "landReadiness", "feasibility", "marketability"))
    assert result["dataLimitations"] == []


def test_land_readiness_counts_signals():
    result = DevelopabilityScoreAgent().score({"land": {"pnu": "x"}})
    land = result["dimensions"]["landReadiness"]
    assert land["available"] is True
    assert land["score"] == 25.0  # 1 of 4 signals (PNU only)


def test_feasibility_prefers_return_metrics_irr():
    rm = {"metrics": {"projectIrr": {"value": 0.20}}}
    result = DevelopabilityScoreAgent().score({"feasibility_input": {"total_revenue": 100, "total_cost": 99}}, return_metrics=rm)
    feas = result["dimensions"]["feasibility"]
    assert feas["available"] is True
    assert feas["score"] == 100.0  # IRR 20% >= 18% band, not the thin 1% margin


def test_missing_dimensions_listed_and_renormalized():
    result = DevelopabilityScoreAgent().score({"feasibility_input": {"total_revenue": 1000, "total_cost": 800}})
    # only feasibility + landReadiness available; regulation + marketability are gaps
    assert "규제 적합성" in result["dataLimitations"]
    assert "시장성" in result["dataLimitations"]
    assert result["dimensions"]["feasibility"]["available"] is True


def test_no_data_grade():
    result = DevelopabilityScoreAgent().score({})
    # landReadiness is always available (0 signals) -> still produces a score
    assert result["grade"] in {"A", "B", "C", "D", "E", "no_data", "not_assessable"}
    assert result["dimensions"]["landReadiness"]["available"] is True


def test_legal_far_breach_is_ineligible_and_never_scores_high():
    result = DevelopabilityScoreAgent().score({
        "regulation": {"appliedFar": 200, "legalFar": 100, "zoning": "일반주거지역"},
    })
    regulation = result["dimensions"]["regulation"]
    assert regulation["available"] is True
    assert regulation["score"] == 0
    assert regulation["eligibility"] == "ineligible"
    assert regulation["blockingConstraintIds"] == ["LEGAL_FAR_EXCEEDED"]
    assert result["overallScore"] == 0
    assert result["businessReadinessStatus"] == "blocked"
    assert result["reportGrade"] is False


def test_applied_far_without_legal_limit_is_not_assessable():
    result = DevelopabilityScoreAgent().score({"regulation": {"applied_far": 300, "zoning": "상업지역"}})
    regulation = result["dimensions"]["regulation"]
    assert regulation["available"] is False
    assert regulation["eligibility"] == "not_assessable"
    assert result["grade"] == "not_assessable"
    assert result["overallScore"] == 0


def test_parcel_weighted_far_prevents_stale_top_level_high_score():
    result = DevelopabilityScoreAgent().score({
        "land": {"area_sqm": 3000},
        "regulation": {"applied_far": 400, "legal_far": 500, "zoning": "mixed"},
        "landLedger": {"parcels": [
            {
                "pnu": "1111010100100010000",
                "areaSqm": 1000,
                "landUsePlanning": {"legalFar": 200},
            },
            {
                "pnu": "1111010100100020000",
                "areaSqm": 2000,
                "landUsePlanning": {"legalFar": 300},
            },
        ]},
    })

    regulation = result["dimensions"]["regulation"]
    assert result["buildableEnvelope"]["effectiveLegalFar"] < 300
    assert regulation["eligibility"] == "ineligible"
    assert regulation["score"] == 0
    assert result["overallScore"] == 0
    assert result["businessReadinessStatus"] == "blocked"
