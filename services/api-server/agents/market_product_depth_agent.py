"""Market-Product Pod depth (P1-04).

Adds, on top of the deterministic Marketability analysis:

1. **자료 제공주체 분류** — every market document / comparable is labelled by who
   provided it: 시행사(developer) / 매각 주관사(sales agent) / 공공데이터(public) /
   제휴(partner) / AI 자동보강(auto-supplement). An explicit ``dataProvider`` field
   wins; otherwise it is inferred from documentType/파일명.
2. **시행사 vs 매각 주관사 비교분석** — proposed 분양가/흡수 가정을 제공주체별로 나란히
   비교하고, 공공 실거래가 중위값을 앵커로 괴리를 계산한다.
3. **상품유형별 분양성 Score** — 제안가 적정성 + 비교사례 충분성 기반 A~E.
4. **흡수율 모델** — 월별 흡수 곡선 + 완판 예상 개월.
5. **AI 추가필요작업 판단** — 결정론 gap 목록 + LLM(근거검토자) 우선순위 판단(폴백 결정론).

Invariants: deterministic numbers; the LLM only *judges what else is needed*, never
produces 분양가/흡수/score. Missing values are surfaced, never invented.
"""

from __future__ import annotations

import json
import math
import statistics
from typing import Any, Dict, List, Optional

from agents.ai_connection import AIConnection, resolve_ai_context, safe_ai_context

MARKET_PRODUCT_DEPTH_SCHEMA_VERSION = "realytics-market-product-depth-v1"

PROVIDER_LABELS = {
    "developer": "시행사 제공 자료",
    "sales_agent": "매각 주관사 제공 자료",
    "public_data": "공공데이터(국토부 실거래가)",
    "partner_api": "제휴 데이터",
    "auto_supplement": "AI 자동보강",
    "unspecified": "제공주체 미상",
}

_DEVELOPER_MARKERS = ("사업계획", "사업제안", "개발계획", "사업수지", "시행사", "infomemo", "information memorandum", " im", "im.", "im_", "ir")
_SALES_AGENT_MARKERS = ("매각", "분양대행", "분양안내", "분양마케팅", "티저", "teaser", "주관사", "매각제안", "매각안내", "분양촉진")

# Indicative monthly absorption benchmark (units/month share of total) by asset type.
_ABSORPTION_BENCHMARK = {
    "apartment": 0.06,
    "officetel": 0.045,
    "commercial": 0.03,
    "knowledge_industry_center": 0.035,
    "mixed_use": 0.05,
    "lodging": 0.04,
}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _norm_provider(raw: Any) -> Optional[str]:
    v = str(raw or "").strip().lower()
    if not v:
        return None
    if v in PROVIDER_LABELS:
        return v
    if v in {"developer", "시행사", "sponsor"}:
        return "developer"
    if v in {"sales_agent", "매각주관사", "매각 주관사", "broker", "underwriter", "주관사"}:
        return "sales_agent"
    if v in {"public", "public_data", "molit", "국토부"}:
        return "public_data"
    if v in {"partner", "partner_api", "valuemap"}:
        return "partner_api"
    if v in {"auto", "auto_supplement", "ai"}:
        return "auto_supplement"
    return None


def _infer_document_provider(doc: Dict[str, Any]) -> str:
    explicit = _norm_provider(doc.get("dataProvider") or doc.get("provider") or doc.get("제공주체") or doc.get("sourceParty"))
    if explicit:
        return explicit
    hay = " ".join(str(doc.get(k) or "") for k in ("documentType", "document_type", "originalFileName", "fileName", "title")).lower()
    if any(m in hay for m in _SALES_AGENT_MARKERS):
        return "sales_agent"
    if any(m in hay for m in _DEVELOPER_MARKERS):
        return "developer"
    return "unspecified"


def _infer_comparable_provider(comp: Dict[str, Any]) -> str:
    explicit = _norm_provider(comp.get("dataProvider") or comp.get("제공주체"))
    if explicit:
        return explicit
    if comp.get("factOrigin") == "auto_supplement" or comp.get("sourceAuthority") == "benchmark_proxy":
        return "auto_supplement"
    source = str(comp.get("source") or "").lower()
    source_type = str(comp.get("sourceType") or "").lower()
    if "molit" in source or "rtms" in source or source_type == "public_api":
        return "public_data"
    if "valuemap" in source or source_type == "partner_api":
        return "partner_api"
    return "unspecified"


class MarketProductDepthAgent:
    def __init__(self, ai: Optional[AIConnection] = None) -> None:
        self.ai = ai or AIConnection()

    def analyze(
        self,
        project_data: Dict[str, Any],
        marketability: Optional[Dict[str, Any]] = None,
        *,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        marketability = marketability or project_data.get("marketabilityAnalysis") or project_data.get("marketability") or {}
        options = options or {}

        provenance = self._classify_sources(project_data, marketability)
        public_median = self._public_median(project_data, marketability)
        comparison = self._compare_providers(project_data, marketability, provenance, public_median)
        product_scores = self._product_scores(project_data, marketability, public_median)
        absorption = self._absorption_model(project_data, marketability)
        next_actions = self._recommend_next_actions(
            project_data, marketability, provenance, comparison, absorption, options,
        )

        return {
            "schemaVersion": MARKET_PRODUCT_DEPTH_SCHEMA_VERSION,
            "sourceProvenance": provenance,
            "providerComparison": comparison,
            "productMarketabilityScores": product_scores,
            "absorptionModel": absorption,
            "aiRecommendedNextActions": next_actions,
            "publicTransactionMedianPerPy": public_median,
            "notes": (
                "제공주체(시행사/매각주관사/공공/AI자동보강)별로 시장자료를 명명·비교합니다. "
                "제안 분양가는 공공 실거래 중위값을 앵커로 괴리를 산출하며, 수치는 새로 만들지 않습니다."
            ),
        }

    # --- 1. provenance classification -------------------------------------
    def _classify_sources(self, project_data: Dict[str, Any], marketability: Dict[str, Any]) -> Dict[str, Any]:
        buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in PROVIDER_LABELS}
        documents = project_data.get("source_documents") or project_data.get("sourceDocuments") or []
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            provider = _infer_document_provider(doc)
            buckets[provider].append({
                "kind": "document",
                "name": doc.get("originalFileName") or doc.get("fileName") or doc.get("documentType") or "문서",
                "documentType": doc.get("documentType") or doc.get("document_type"),
            })
        comparables = self._comparables(project_data, marketability)
        for comp in comparables:
            provider = _infer_comparable_provider(comp)
            buckets[provider].append({
                "kind": "comparable",
                "name": comp.get("address") or comp.get("sourceName") or comp.get("source") or "비교사례",
                "pricePerPy": _num(comp.get("pricePerPy")),
            })
        summary = {
            provider: {
                "label": PROVIDER_LABELS[provider],
                "count": len(items),
                "items": items[:20],
            }
            for provider, items in buckets.items()
            if items
        }
        return {
            "byProvider": summary,
            "developerProvided": len(buckets["developer"]),
            "salesAgentProvided": len(buckets["sales_agent"]),
            "publicData": len(buckets["public_data"]),
            "autoSupplemented": len(buckets["auto_supplement"]),
            "unspecified": len(buckets["unspecified"]),
        }

    # --- 2. provider comparison -------------------------------------------
    def _compare_providers(
        self,
        project_data: Dict[str, Any],
        marketability: Dict[str, Any],
        provenance: Dict[str, Any],
        public_median: Optional[float],
    ) -> Dict[str, Any]:
        proposals = self._provider_proposed_prices(project_data)
        rows: List[Dict[str, Any]] = []
        for provider in ("developer", "sales_agent"):
            price = proposals.get(provider)
            delta_vs_public = None
            if price and public_median:
                delta_vs_public = round((price - public_median) / public_median, 4)
            rows.append({
                "provider": provider,
                "label": PROVIDER_LABELS[provider],
                "proposedPricePerPy": price,
                "status": "submitted" if price else "not_submitted",
                "deltaVsPublicMedian": delta_vs_public,
                "assessment": self._price_assessment(delta_vs_public) if delta_vs_public is not None else (
                    "제안 분양가 미제출" if not price else "공공 실거래 비교 불가(실거래 데이터 부족)"
                ),
            })
        dev = proposals.get("developer")
        sa = proposals.get("sales_agent")
        developer_vs_sales = None
        if dev and sa:
            developer_vs_sales = {
                "deltaPct": round((dev - sa) / sa, 4),
                "note": (
                    f"시행사 제안({round(dev/10000):,}만/평)이 매각주관사 제안({round(sa/10000):,}만/평) 대비 "
                    f"{'높음' if dev > sa else '낮음'} — 보수적 측(낮은 값) 기준 심사 권장."
                ),
            }
        elif dev and not sa:
            developer_vs_sales = {"deltaPct": None, "note": "매각 주관사 제안 자료 미제출 — 교차검증 위해 매각/분양대행 자료 확보 권장."}
        elif sa and not dev:
            developer_vs_sales = {"deltaPct": None, "note": "시행사 제안 분양가 근거 미확인 — 사업계획서/IM의 분양가 산정근거 확보 권장."}
        return {
            "publicMedianPerPy": public_median,
            "rows": rows,
            "developerVsSalesAgent": developer_vs_sales,
        }

    def _provider_proposed_prices(self, project_data: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Proposed 분양가(평당) per provider, from provider-tagged parsed facts when
        available, else the aggregated document market facts attributed to 시행사."""
        out: Dict[str, Optional[float]] = {"developer": None, "sales_agent": None}
        # provider-tagged parsed facts (future orchestration tagging)
        for fact in project_data.get("parsed_facts") or []:
            if not isinstance(fact, dict):
                continue
            field = str(fact.get("canonicalField") or fact.get("termId") or "").lower()
            if "price" in field and ("py" in field or "분양" in field):
                provider = _norm_provider(fact.get("dataProvider") or fact.get("제공주체"))
                value = _num(fact.get("normalizedValue") or fact.get("value"))
                if provider in out and value:
                    out[provider] = value
        # aggregated document market facts → attribute to developer (사업계획서 default)
        if out["developer"] is None:
            doc_facts = project_data.get("documentParsedMarketFacts") or {}
            price = _num(doc_facts.get("proposedPricePerPy") or doc_facts.get("proposed_price_per_py"))
            if price is None:
                market = project_data.get("market") or {}
                price = _num(market.get("proposedPricePerPy"))
            if price:
                out["developer"] = price
        return out

    @staticmethod
    def _price_assessment(delta: float) -> str:
        if delta > 0.15:
            return "공공 실거래 대비 고평가(>15%) — 분양가 근거·흡수율 재검토 필요"
        if delta > 0.05:
            return "공공 실거래 대비 다소 높음(5~15%)"
        if delta < -0.15:
            return "공공 실거래 대비 저평가(<-15%) — 사업성/수취가 확인"
        return "공공 실거래와 유사(±5~15% 이내)"

    # --- 3. product marketability scores ----------------------------------
    def _product_scores(
        self,
        project_data: Dict[str, Any],
        marketability: Dict[str, Any],
        public_median: Optional[float],
    ) -> List[Dict[str, Any]]:
        product_mix = project_data.get("product_mix") or project_data.get("productMix") or []
        comparables = self._comparables(project_data, marketability)
        comp_count = len(comparables)
        scores: List[Dict[str, Any]] = []
        for item in product_mix:
            if not isinstance(item, dict):
                continue
            product_type = item.get("productType") or item.get("asset_type") or item.get("type") or "기타"
            price = _num(item.get("pricePerPy") or item.get("price_per_py"))
            price_pts = None
            if price and public_median:
                delta = (price - public_median) / public_median
                price_pts = max(0.0, 1.0 - abs(delta))  # closer to public median → higher
            data_pts = min(1.0, comp_count / 3.0)
            components = [p for p in (price_pts, data_pts) if p is not None]
            score = round(sum(components) / len(components), 2) if components else None
            scores.append({
                "productType": product_type,
                "pricePerPy": price,
                "priceAdequacy": None if price_pts is None else round(price_pts, 2),
                "comparableSufficiency": round(data_pts, 2),
                "score": score,
                "grade": self._grade(score),
                "basis": "public_transaction_anchor" if price_pts is not None else "comparable_sufficiency_only",
            })
        return scores

    @staticmethod
    def _grade(score: Optional[float]) -> str:
        if score is None:
            return "N/A"
        if score >= 0.85:
            return "A"
        if score >= 0.7:
            return "B"
        if score >= 0.55:
            return "C"
        if score >= 0.4:
            return "D"
        return "E"

    # --- 4. absorption model ----------------------------------------------
    def _absorption_model(self, project_data: Dict[str, Any], marketability: Dict[str, Any]) -> Dict[str, Any]:
        forecast = marketability.get("absorptionForecast") if isinstance(marketability, dict) else {}
        forecast = forecast if isinstance(forecast, dict) else {}
        total_units = _num(forecast.get("totalUnits")) or _num((project_data.get("architectural_plan") or {}).get("total_units"))
        product_type = str((marketability or {}).get("productType") or (project_data.get("project") or {}).get("type") or "apartment").lower()
        raw_rate = forecast.get("monthlyAbsorptionRate")
        rate = _num(raw_rate)
        basis = "forecast"
        if raw_rate is not None and (rate is None or rate < 0 or rate > 1):
            return {"monthlyAbsorptionRate": None, "rateBasis": "invalid_forecast",
                    "status": "invalid_input", "totalUnits": total_units,
                    "estimatedMonthsToSellout": None, "curve": [],
                    "note": "월 흡수율은 0~1 범위의 유한한 비율이어야 합니다. 입력 근거를 확인하세요."}
        if raw_rate is None:
            rate = _ABSORPTION_BENCHMARK.get(product_type, 0.05)
            basis = "planning_benchmark"
        months_to_sellout = math.ceil(1 / rate) if rate else None
        curve: List[Dict[str, Any]] = []
        if total_units and rate:
            cumulative = 0.0
            for month in range(1, min(months_to_sellout or 0, 36) + 1):
                sold = min(total_units, total_units * rate)
                cumulative = min(total_units, cumulative + sold)
                curve.append({"month": month, "cumulativeRate": round(cumulative / total_units, 3)})
                if cumulative >= total_units:
                    break
        return {
            "monthlyAbsorptionRate": rate,
            "rateBasis": basis,
            "totalUnits": total_units,
            "estimatedMonthsToSellout": months_to_sellout,
            "curve": curve,
            "horizonMonths": 36,
            "horizonComplete": bool(curve and curve[-1]["cumulativeRate"] >= 1),
            "note": "월별 흡수율은 예측 근거가 없으면 자산유형 계획 벤치마크를 사용합니다(basis 표기)." if basis == "planning_benchmark" else "",
        }

    # --- 5. AI-judged next actions (completeness critic) -------------------
    def _deterministic_next_actions(
        self,
        provenance: Dict[str, Any],
        comparison: Dict[str, Any],
        absorption: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        if not provenance.get("salesAgentProvided"):
            actions.append({"priority": "high", "action": "매각 주관사(분양대행) 제공 자료 확보 — 제안 분양가·흡수 가정 교차검증", "category": "evidence"})
        if not provenance.get("publicData"):
            actions.append({"priority": "high", "action": "국토부 실거래가 비교사례 확보 — 제안가 적정성 앵커", "category": "evidence"})
        dev_row = next((r for r in comparison.get("rows", []) if r.get("provider") == "developer"), {})
        if dev_row.get("status") != "submitted":
            actions.append({"priority": "high", "action": "시행사 사업계획서/IM의 분양가 산정근거 확보", "category": "evidence"})
        elif isinstance(dev_row.get("deltaVsPublicMedian"), (int, float)) and dev_row["deltaVsPublicMedian"] > 0.15:
            actions.append({"priority": "high", "action": "제안 분양가가 공공 실거래 대비 고평가 — 흡수율·할인분양 시나리오 스트레스 필요", "category": "analysis"})
        if absorption.get("rateBasis") == "planning_benchmark":
            actions.append({"priority": "medium", "action": "흡수율을 실제 분양속도(청약경쟁률·인근 분양단지 실적)로 대체", "category": "data"})
        if not actions:
            actions.append({"priority": "low", "action": "현재 시장근거로 예비판단 가능 — 승인 전 심사자 최종 확인", "category": "review"})
        return actions

    def _recommend_next_actions(
        self,
        project_data: Dict[str, Any],
        marketability: Dict[str, Any],
        provenance: Dict[str, Any],
        comparison: Dict[str, Any],
        absorption: Dict[str, Any],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        deterministic = self._deterministic_next_actions(provenance, comparison, absorption)
        mode = str(options.get("podAiReviewMode") or options.get("pod_ai_review_mode") or "").lower()
        if mode in {"deterministic", "deterministic_only"}:
            return {"mode": "deterministic", "actions": deterministic, "aiBrainUsed": {"mode": "deterministic"}}

        ai_context = resolve_ai_context({
            **(options.get("ai_context") or options.get("aiContext") or {}),
            "agent_id": "MarketProductDepthReviewer",
        })
        prompt = (
            "너는 REALYTICS 시장성 심사 보조자다. 아래 시장자료 제공주체 분류·제안가 비교·흡수율 요약을 보고, "
            "여신 심사를 위해 '추가로 확보/분석해야 할 작업'을 우선순위(high/medium/low)와 함께 JSON으로만 판단하라. "
            "숫자를 새로 만들지 말고, 부족한 근거와 다음 액션만 제시하라. "
            'JSON 형식: {"actions":[{"priority":"high","action":"...","category":"evidence|analysis|data|review"}]}\n\n'
            f"제공주체 요약: {json.dumps({k: provenance.get(k) for k in ('developerProvided','salesAgentProvided','publicData','autoSupplemented')}, ensure_ascii=False)}\n"
            f"제안가 비교: {json.dumps(comparison, ensure_ascii=False, default=str)[:1200]}\n"
            f"흡수율: {json.dumps({k: absorption.get(k) for k in ('monthlyAbsorptionRate','rateBasis','estimatedMonthsToSellout')}, ensure_ascii=False)}\n"
        )
        try:
            result = self.ai.extract_json(prompt, system="Return only JSON. You are an evidence-gap reviewer, not a calculator.", ai_context=ai_context)
            payload = result.data if hasattr(result, "data") else (result.get("data") if isinstance(result, dict) else None)
            actions = payload.get("actions") if isinstance(payload, dict) else None
            if isinstance(actions, list) and actions:
                normed = [
                    {
                        "priority": str(a.get("priority") or "medium"),
                        "action": str(a.get("action") or "").strip(),
                        "category": str(a.get("category") or "review"),
                    }
                    for a in actions if isinstance(a, dict) and a.get("action")
                ]
                if normed:
                    return {
                        "mode": "llm_evidence_aware",
                        "actions": normed,
                        "deterministicActions": deterministic,
                        "aiBrainUsed": {**safe_ai_context(ai_context), "eventId": (result.meta or {}).get("ai_brain_used_event_id") if hasattr(result, "meta") else None},
                    }
        except Exception:
            pass
        return {"mode": "deterministic_fallback", "actions": deterministic, "aiBrainUsed": {**safe_ai_context(ai_context), "fallback": True}}

    # --- helpers -----------------------------------------------------------
    def _comparables(self, project_data: Dict[str, Any], marketability: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        market = project_data.get("market") or {}
        for key in ("transactionComparables", "comparables", "listing_comparables", "manualComparables"):
            val = market.get(key)
            if isinstance(val, list):
                out.extend(x for x in val if isinstance(x, dict))
        if isinstance(marketability, dict):
            for key in ("comparables", "normalizedComparables"):
                val = marketability.get(key)
                if isinstance(val, list):
                    out.extend(x for x in val if isinstance(x, dict))
        return out

    def _public_median(self, project_data: Dict[str, Any], marketability: Dict[str, Any]) -> Optional[float]:
        prices = [
            _num(c.get("pricePerPy"))
            for c in self._comparables(project_data, marketability)
            if _infer_comparable_provider(c) == "public_data" and _num(c.get("pricePerPy"))
        ]
        prices = [p for p in prices if p]
        return round(statistics.median(prices)) if prices else None
