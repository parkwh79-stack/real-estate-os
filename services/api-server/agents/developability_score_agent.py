from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEVELOPABILITY_SCHEMA_VERSION = "realytics-developability-score-v1"

# Weighted dimensions for the composite developability score (deck p.2/p.7).
_WEIGHTS = {
    "regulation": 0.25,
    "landReadiness": 0.25,
    "feasibility": 0.30,
    "marketability": 0.20,
}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


class DevelopabilityScoreAgent:
    """Composite developability score from land + regulation + feasibility + market signals.

    Implements the deck's "개발가능성 점수화" as a deterministic, auditable score:
    each available dimension is scored 0-100, the overall is a weight-renormalised
    average of the dimensions that have data, and drivers/gaps are surfaced.
    """

    analysis_type = "developability_score_v1"

    def score(self, project_data: Dict[str, Any], *, return_metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        dimensions: Dict[str, Dict[str, Any]] = {}
        dimensions["regulation"] = self._regulation(project_data.get("regulation") or {})
        dimensions["landReadiness"] = self._land_readiness(project_data)
        dimensions["feasibility"] = self._feasibility(project_data, return_metrics)
        dimensions["marketability"] = self._marketability(project_data.get("marketability") or project_data.get("marketabilityAnalysis") or {})

        available = {k: v for k, v in dimensions.items() if v["available"]}
        if available:
            weight_total = sum(_WEIGHTS[k] for k in available)
            overall = round(sum(v["score"] * _WEIGHTS[k] for k, v in available.items()) / weight_total, 1)
        else:
            overall = 0.0

        drivers = [v["label"] for v in available.values() if v["score"] >= 75]
        gaps = [v["label"] for v in available.values() if v["score"] < 50]
        data_limitations = [dimensions[k]["label"] for k in dimensions if not dimensions[k]["available"]]

        return {
            "schemaVersion": DEVELOPABILITY_SCHEMA_VERSION,
            "analysisType": self.analysis_type,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "overallScore": overall,
            "grade": self._grade(overall) if available else "no_data",
            "dimensions": dimensions,
            "drivers": drivers,
            "gaps": gaps,
            "dataLimitations": data_limitations,
            "summary": self._summary(overall, available, gaps),
        }

    def _regulation(self, reg: Dict[str, Any]) -> Dict[str, Any]:
        applied_far = _num(reg.get("applied_far"))
        legal_far = _num(reg.get("legal_far"))
        zoning = reg.get("zoning")
        if applied_far is None and not zoning:
            return self._dim("규제 적합성", 0.0, False, ["용도지역·용적률 정보 없음"])
        score = 55.0
        notes: List[str] = []
        if applied_far is not None and legal_far:
            utilization = applied_far / legal_far if legal_far else 0.0
            score += max(-15.0, min(35.0, (utilization - 0.5) * 70.0))
            notes.append(f"용적률 활용 {utilization:.0%} (적용 {applied_far:.0f}/법정 {legal_far:.0f})")
        if zoning:
            score += 10.0
            notes.append(f"용도지역 {zoning}")
        return self._dim("규제 적합성", round(min(100.0, max(0.0, score)), 1), True, notes)

    def _land_readiness(self, data: Dict[str, Any]) -> Dict[str, Any]:
        land = data.get("land") or {}
        evidence = data.get("landEvidence") or {}
        signals = {
            "PNU": bool(land.get("pnu") or evidence.get("pnu")),
            "공시지가": bool(evidence.get("officialLandPrice") or evidence.get("official_land_price")),
            "등기 증빙": bool(evidence.get("registry") or evidence.get("registryEvidence") or evidence.get("registry_evidence")),
            "토지취득률": (_num(data.get("land_acquisition_ratio")) or _num(evidence.get("acquisitionAreaRatio")) or 0.0) >= 1.0,
        }
        present = sum(1 for v in signals.values() if v)
        score = round(present / len(signals) * 100.0, 1)
        notes = [f"{k} {'확보' if v else '미확보'}" for k, v in signals.items()]
        # Land readiness is always reported (a project always has a land dimension).
        return self._dim("토지 확보·증빙", score, True, notes)

    def _feasibility(self, data: Dict[str, Any], return_metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        irr = None
        if isinstance(return_metrics, dict):
            irr = _num(((return_metrics.get("metrics") or {}).get("projectIrr") or {}).get("value"))
        fi = data.get("feasibility_input") or {}
        revenue = _num(fi.get("total_revenue")) or 0.0
        cost = _num(fi.get("total_cost")) or 0.0
        margin = _num(fi.get("profit_margin"))
        if margin is None and revenue > 0:
            margin = (revenue - cost) / revenue
        if irr is None and margin is None:
            return self._dim("사업성", 0.0, False, ["사업수지/수익지표 없음"])
        if irr is not None:
            score = self._band(irr, [(0.18, 100), (0.12, 82), (0.07, 62), (0.0, 42)], 12)
            notes = [f"Project IRR {irr:.1%}"]
        else:
            score = self._band(margin, [(0.18, 100), (0.12, 80), (0.07, 60), (0.0, 40)], 12)
            notes = [f"개발이익률 {margin:.1%}"]
        return self._dim("사업성", float(score), True, notes)

    def _marketability(self, mk: Dict[str, Any]) -> Dict[str, Any]:
        grade = mk.get("grade")
        score_val = _num(mk.get("score"))
        if not grade and score_val is None:
            return self._dim("시장성", 0.0, False, ["마케터빌리티 분석 없음"])
        if score_val is not None:
            score = max(0.0, min(100.0, score_val))
        else:
            score = {"A": 95, "B": 80, "C": 65, "D": 45, "E": 25}.get(str(grade).upper(), 50)
        return self._dim("시장성", float(score), True, [f"마케터빌리티 등급 {grade or '-'}"])

    @staticmethod
    def _band(value: float, thresholds: List[tuple], floor: float) -> float:
        for cutoff, score in thresholds:
            if value >= cutoff:
                return float(score)
        return float(floor)

    @staticmethod
    def _dim(label: str, score: float, available: bool, notes: List[str]) -> Dict[str, Any]:
        return {"label": label, "score": score, "available": available, "notes": notes}

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 85:
            return "A"
        if score >= 70:
            return "B"
        if score >= 55:
            return "C"
        if score >= 40:
            return "D"
        return "E"

    @staticmethod
    def _summary(overall: float, available: Dict[str, Any], gaps: List[str]) -> str:
        if not available:
            return "개발가능성 평가에 필요한 토지·법규·사업성·시장성 데이터가 부족합니다."
        text = f"개발가능성 종합점수 {overall:.0f}점."
        if gaps:
            text += f" 보완 필요: {', '.join(gaps)}."
        return text
