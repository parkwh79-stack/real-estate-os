from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from agents.ai_connection import AIConnection, resolve_ai_context, safe_ai_context


POD_AI_REVIEW_GUARDRAILS = [
    "LLM is an evidence-aware reviewer, not a deterministic calculator.",
    "Do not create PNU, official land price, registry status, market comparables, or PF metrics without source evidence.",
    "Do not waive blocking risks or mark legal/PF readiness complete without evidence or human approval.",
    "Financial calculations must come from deterministic engines and calculationRun records.",
]


class PodAIReviewerAgent:
    """Review a Chief Pod artifact without replacing deterministic engines.

    The agent attempts an LLM evidence-aware review when an AI provider is
    configured. If no model call is available, it returns a clearly marked
    deterministic fallback review so the UI and audit trail never confuse a
    local guardrail check with an AI opinion.
    """

    def __init__(self, ai: AIConnection | None = None):
        self.ai = ai or AIConnection()

    def review_pod(
        self,
        *,
        project_id: str,
        pod_key: str,
        source_artifact: Dict[str, Any],
        source_artifact_key: str | None = None,
        project_data: Dict[str, Any] | None = None,
        options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        options = options or {}
        now = datetime.now(timezone.utc).isoformat()
        agent_id = _agent_id_for_pod(pod_key)
        evidence_ids = _collect_values(source_artifact, {"evidenceId", "evidence_id"})
        assumption_ids = _collect_values(source_artifact, {"assumptionId", "assumption_id"})
        source_locators = _collect_source_locators(source_artifact)
        ai_context = _review_ai_context(options, project_id=project_id, agent_id=agent_id, evidence_ids=evidence_ids)
        if str(options.get("podAiReviewMode") or options.get("pod_ai_review_mode") or "").lower() in {"deterministic", "deterministic_only"}:
            ai_result = {
                "llmReviewAccepted": False,
                "review": None,
                "aiBrainUsed": {**safe_ai_context(ai_context), "mode": "deterministic_test_or_manual_review"},
                "aiBrainUsedEventId": None,
                "llmError": "LLM review skipped by podAiReviewMode=deterministic.",
            }
        else:
            ai_result = self._try_llm_review(
                pod_key=pod_key,
                source_artifact=source_artifact,
                source_artifact_key=source_artifact_key,
                ai_context=ai_context,
            )

        review_mode = "llm_evidence_aware_review" if ai_result.get("llmReviewAccepted") else "deterministic_fallback_review"
        review_payload = ai_result.get("review") if isinstance(ai_result.get("review"), dict) else {}
        deterministic = self._deterministic_review(
            pod_key=pod_key,
            source_artifact=source_artifact,
            source_artifact_key=source_artifact_key,
            project_data=project_data or {},
            options=options,
            evidence_ids=evidence_ids,
            source_locators=source_locators,
        )
        document_facts = deterministic.get("documentFacts") if isinstance(deterministic.get("documentFacts"), dict) else {}
        findings = _normalize_findings(review_payload.get("findings")) if review_payload else []
        if not findings:
            findings = deterministic["findings"]
        else:
            findings.extend(deterministic["guardrailFindings"])

        review_status = _status_from_findings(findings)
        # Always keep the DETERMINISTIC findings separately, even when an LLM
        # review supplies the display `findings`. Downstream gating (Approval
        # Gate, Risk Committee, report evidence links) must judge from these —
        # never from an LLM opinion alone (AGENTS.md §1).
        deterministic_findings = deterministic["findings"]
        confidence = _bounded_float(review_payload.get("confidence"), default=deterministic["confidence"])
        review_id = f"pod-ai-review:{pod_key}:{uuid4()}"
        report_fragments = review_payload.get("reportFragments") if isinstance(review_payload.get("reportFragments"), dict) else deterministic["reportFragments"]

        return {
            "schemaVersion": "realytics-pod-ai-review-v1",
            "reviewId": review_id,
            "projectId": project_id,
            "runId": options.get("runId") or options.get("run_id"),
            "podKey": pod_key,
            "sourceArtifactKey": source_artifact_key,
            "sourceArtifactKeys": [source_artifact_key] if source_artifact_key else [],
            "agentId": agent_id,
            "reviewMode": review_mode,
            "reviewStatus": review_status,
            "confidence": confidence,
            "findings": findings,
            "deterministicFindings": deterministic_findings,
            "handoffs": _normalize_handoffs(review_payload.get("handoffs"), agent_id=agent_id) if review_payload else deterministic["handoffs"],
            "reportFragments": report_fragments,
            "documentFacts": document_facts,
            "inputEvidenceIds": evidence_ids,
            "inputAssumptionIds": assumption_ids,
            "sourceLocators": source_locators[:50],
            "guardrails": POD_AI_REVIEW_GUARDRAILS,
            "aiBrainUsed": safe_ai_context(ai_result.get("aiBrainUsed") or ai_context),
            "aiBrainUsedEventId": ai_result.get("aiBrainUsedEventId"),
            "llmReviewAccepted": ai_result.get("llmReviewAccepted", False),
            "llmError": ai_result.get("llmError"),
            "createdAt": now,
            "updatedAt": now,
        }

    def _try_llm_review(
        self,
        *,
        pod_key: str,
        source_artifact: Dict[str, Any],
        source_artifact_key: str | None,
        ai_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        system = (
            "You are a REALYTICS Pod AI Reviewer. Review deterministic pod artifacts only. "
            "Do not invent facts, source locators, PNU, official land prices, registry conclusions, "
            "market comparables, or financial calculations. Return JSON only."
        )
        prompt = json.dumps(
            {
                "task": "Review this Chief Deal Orchestrator Pod artifact for evidence gaps, contradictions, handoffs, and report-grade limitations.",
                "podKey": pod_key,
                "sourceArtifactKey": source_artifact_key,
                "requiredOutput": {
                    "confidence": "0..1",
                    "findings": [
                        {
                            "category": "missing_evidence|source_locator_gap|data_quality|recalculation_needed|risk_note|handoff|guardrail",
                            "severity": "info|medium|high|blocking",
                            "message": "Korean or English concise finding",
                            "evidenceIds": [],
                            "sourceLocators": [],
                            "recommendedAction": "specific next action",
                        }
                    ],
                    "handoffs": [],
                    "reportFragments": {
                        "riskMemo": "",
                        "missingDataRequest": "",
                        "approvalConditionDraft": "",
                    },
                },
                "guardrails": POD_AI_REVIEW_GUARDRAILS,
                "artifact": _compact_json(source_artifact, max_chars=16000),
            },
            ensure_ascii=False,
        )
        result = self.ai.extract_json(prompt, system=system, ai_context=ai_context)
        if result.data:
            return {
                "llmReviewAccepted": True,
                "review": result.data,
                "aiBrainUsed": result.meta,
                "aiBrainUsedEventId": (result.meta or {}).get("ai_brain_used_event_id"),
            }
        return {
            "llmReviewAccepted": False,
            "review": None,
            "aiBrainUsed": result.meta,
            "aiBrainUsedEventId": (result.meta or {}).get("ai_brain_used_event_id"),
            "llmError": result.error,
        }

    def _deterministic_review(
        self,
        *,
        pod_key: str,
        source_artifact: Dict[str, Any],
        source_artifact_key: str | None,
        project_data: Dict[str, Any],
        options: Dict[str, Any],
        evidence_ids: List[str],
        source_locators: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        findings: List[Dict[str, Any]] = []
        if not source_locators:
            findings.append(_finding(
                "source_locator_gap",
                "high",
                "Pod artifact has no explicit source locator. Attach document page, Excel cell, API row, or artifact key before report-grade use.",
                recommended_action="Run Evidence Graph/source locator enrichment or attach locator-backed evidence.",
            ))
        if pod_key == "land_legal":
            findings.extend(_land_legal_findings(source_artifact, source_artifact_key))
        document_facts: Dict[str, Any] = {}
        if pod_key == "market_product":
            document_facts = _document_market_facts(source_artifact, project_data)
            findings.extend(_market_product_findings(source_artifact, source_artifact_key, project_data, options))
        if pod_key == "pf_credit":
            findings.extend(_pf_credit_findings(source_artifact, source_artifact_key, project_data))
        if pod_key == "cost_schedule":
            findings.extend(_cost_schedule_findings(source_artifact, source_artifact_key, project_data))
        if pod_key == "feasibility_cashflow":
            findings.extend(_feasibility_cashflow_findings(source_artifact, source_artifact_key, project_data))
        if pod_key == "public_policy":
            findings.extend(_public_policy_findings(source_artifact, source_artifact_key, project_data))
        if not evidence_ids:
            findings.append(_finding(
                "missing_evidence",
                "medium",
                "No input evidence IDs were found in the pod artifact. This review can guide next actions but should not be treated as legal/PF-ready evidence.",
                recommended_action="Attach evidence IDs from uploaded documents, VWorld/public API records, or registry/official land price evidence.",
            ))
        if not findings:
            findings.append(_finding(
                "guardrail",
                "info",
                "No immediate deterministic evidence gap was detected, but human review is still required before report-grade use.",
                recommended_action="Reviewer should confirm evidence and calculation provenance.",
            ))
        handoffs = []
        if any(item["severity"] in {"high", "blocking"} for item in findings):
            handoffs.append({
                "handoffId": f"handoff:pod-ai-review:{pod_key}:{uuid4()}",
                "fromAgent": _agent_id_for_pod(pod_key),
                "toAgent": "ChiefDealOrchestrator",
                "handoffType": "review_request",
                "payload": {"findingCount": len(findings), "sourceArtifactKey": source_artifact_key, "documentFacts": document_facts},
                "evidenceIds": evidence_ids,
                "confidence": 0.72,
            })
        return {
            "findings": findings,
            "guardrailFindings": [],
            "handoffs": handoffs,
            "reportFragments": {
                "riskMemo": "Pod AI review used deterministic guardrails because no usable LLM review was available; findings are evidence-gap checks, not final credit conclusions.",
                "missingDataRequest": "; ".join(item.get("recommendedAction") or item["message"] for item in findings[:3]),
                "approvalConditionDraft": "",
            },
            "documentFacts": document_facts,
            "confidence": 0.68,
        }


def _review_ai_context(options: Dict[str, Any], *, project_id: str, agent_id: str, evidence_ids: List[str]) -> Dict[str, Any]:
    base = dict(options.get("ai_context") or options.get("aiContext") or {})
    base.setdefault("project_id", project_id)
    base.setdefault("agent_id", agent_id)
    base.setdefault("input_evidence_ids", evidence_ids)
    return resolve_ai_context(base)


def _agent_id_for_pod(pod_key: str) -> str:
    return {
        "land_legal": "LandLegalPodAIReviewer",
        "market_product": "MarketProductPodAIReviewer",
        "cost_schedule": "CostSchedulePodAIReviewer",
        "feasibility_cashflow": "FeasibilityCashflowPodAIReviewer",
        "pf_credit": "PFCreditPodAIReviewer",
        "public_policy": "PublicPolicyPodAIReviewer",
    }.get(pod_key, "PodAIReviewerAgent")


def _finding(category: str, severity: str, message: str, *, recommended_action: str | None = None) -> Dict[str, Any]:
    return {
        "findingId": f"finding:{category}:{uuid4()}",
        "category": category,
        "severity": severity,
        "message": message,
        "evidenceIds": [],
        "sourceLocators": [],
        "recommendedAction": recommended_action,
    }


def _land_legal_findings(source_artifact: Dict[str, Any], source_artifact_key: str | None) -> List[Dict[str, Any]]:
    text = json.dumps(source_artifact, ensure_ascii=False, default=str).lower()
    findings: List[Dict[str, Any]] = []
    if "pnu" not in text:
        findings.append(_finding(
            "missing_evidence",
            "blocking",
            "Land-Legal Pod artifact does not show PNU evidence. PNU must be resolved before legal/PF readiness.",
            recommended_action="Run batch PNU enrichment or upload parcel/PNU evidence.",
        ))
    if "official" not in text and "공시지가" not in text:
        findings.append(_finding(
            "missing_evidence",
            "high",
            "Official land price evidence is not visible in the Land-Legal Pod artifact.",
            recommended_action="Attach official land price evidence with base year, base date, source, and fetchedAt.",
        ))
    if "registry" not in text and "등기" not in text and "encumbrance" not in text:
        findings.append(_finding(
            "missing_evidence",
            "high",
            "Registry or encumbrance evidence is not visible in the Land-Legal Pod artifact.",
            recommended_action="Upload registry/encumbrance evidence or create a registry evidence work order.",
        ))
    if source_artifact_key:
        for item in findings:
            item["sourceLocators"].append({"sourceType": "chief_pod_artifact", "artifactKey": source_artifact_key})
    return findings


def _market_product_findings(
    source_artifact: Dict[str, Any],
    source_artifact_key: str | None,
    project_data: Dict[str, Any],
    options: Dict[str, Any],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    marketability = _first_dict_value(source_artifact, {"marketabilityAnalysis", "marketability"})
    pricing = _first_dict_value(marketability, {"pricing"}) if marketability else {}
    comparable_count = _count_market_comparables(source_artifact)
    proposed_price = _first_numeric_value(
        source_artifact,
        {
            "proposedPricePerPy",
            "proposed_price_per_py",
            "weightedAveragePricePerPy",
            "pricePerPy",
            "price_per_py",
        },
    )
    proposed_basis = ""
    if isinstance(pricing, dict):
        proposed_basis = str(pricing.get("proposedPriceBasis") or pricing.get("proposed_price_basis") or "")
    risk_flags = set(_collect_values(source_artifact, {"riskFlags", "risk_flags", "code"}))
    missing_required = set(_collect_values(source_artifact, {"missingRequiredData", "missing_required_data"}))
    document_facts = _document_market_facts(source_artifact, project_data)
    reconciliation = _first_dict_value(source_artifact, {"documentMarketReconciliation"})
    reconciliation_status = str(reconciliation.get("status") or document_facts.get("reconciliationStatus") or "").lower()
    reconciliation_issues = reconciliation.get("issues") if isinstance(reconciliation.get("issues"), list) else []

    if document_facts.get("hasDocumentMarketEvidence") and not reconciliation:
        findings.append(_finding(
            "document_market_reconciliation_missing",
            "medium",
            "Uploaded business plan / IM market facts are visible, but the pod artifact does not include a reconciliation result.",
            recommended_action="Run Market Evidence Agent reconciliation before treating parsed sale price, product mix, or rent assumptions as report-grade inputs.",
        ))
    elif document_facts.get("hasDocumentMarketEvidence") and (
        reconciliation_issues
        or reconciliation_status in {"needs_review", "needs_evidence", "blocking", "blocked"}
    ):
        findings.append(_finding(
            "document_market_reconciliation_needs_review",
            "high",
            "Uploaded business plan / IM market facts require reconciliation before marketability scoring and feasibility handoff.",
            recommended_action="Review pricing checks, rent checks, and evidence records; approve a baseline assumption or attach corrected market evidence with source locators.",
        ))

    if comparable_count == 0 or "NO_COMPARABLE_DATA" in risk_flags:
        findings.append(_finding(
            "no_comparable_data",
            "high",
            "Market-Product Pod has no usable transaction, presale, listing, or manual comparable evidence. Price adequacy and absorption should not be treated as confirmed.",
            recommended_action="Attach source-dated transaction/presale/listing comparables or run the market evidence connector before scoring marketability.",
        ))
    elif comparable_count < 3:
        findings.append(_finding(
            "thin_comparable_data",
            "medium",
            f"Only {comparable_count} comparable case(s) are visible. This is enough for a preliminary view, but not enough for report-grade price judgement.",
            recommended_action="Add at least three relevant comparables with source metadata, base date, product type, area, price, and distance.",
        ))

    pricing_judgement = str(pricing.get("judgement") or "") if isinstance(pricing, dict) else ""
    proposed_basis_status = str(pricing.get("proposedPriceBasisStatus") or "") if isinstance(pricing, dict) else ""
    if (
        proposed_price in (None, 0)
        or not proposed_basis
        or proposed_basis_status == "missing"
        or pricing_judgement == "needs_proposed_price_basis"
        or "PROPOSED_PRICE_BASIS_MISSING" in risk_flags
        or "proposed_price_per_py_basis" in missing_required
    ):
        findings.append(_finding(
            "proposed_price_basis_missing",
            "high",
            "Comparable evidence may exist, but the proposed sale price per py basis is missing or not traceable to product mix, total revenue, saleable area, or an explicit pricing assumption.",
            recommended_action="Derive proposed price per py from product mix/revenue/area, or attach an explicit pricing assumption with evidence ID and source locator.",
        ))

    if _market_income_review_required(project_data, source_artifact, options) and not _has_income_or_cap_rate_evidence(source_artifact):
        findings.append(_finding(
            "rent_cap_rate_evidence_missing",
            "high",
            "This track or asset type needs rent, NOI, or Cap Rate evidence, but the Market-Product Pod artifact does not show a source-backed income basis.",
            recommended_action="Attach rent roll, lease comparable, NOI bridge, appraisal, or Cap Rate comparable before using this for real-asset finance, REIT, fund, or refinancing review.",
        ))

    data_status = str((marketability or {}).get("dataStatus") or "").lower() if isinstance(marketability, dict) else ""
    score = (marketability or {}).get("score") if isinstance(marketability, dict) else None
    checks = (marketability or {}).get("checks") if isinstance(marketability, dict) else None
    report_paragraphs = (marketability or {}).get("reportParagraphs") if isinstance(marketability, dict) else None
    if (
        not marketability
        or data_status in {"needs_comparable_data", "insufficient", "not_applicable"}
        or score in (None, "")
        or not checks
        or not report_paragraphs
    ):
        findings.append(_finding(
            "marketability_judgement_basis_weak",
            "medium",
            "Marketability judgement is preliminary because score, checks, report paragraphs, or data-status support is incomplete.",
            recommended_action="Run Marketability Analysis after market comparables, product mix, proposed price basis, demand/supply assumptions, and source metadata are attached.",
        ))

    # Product planning (자산유형별 상품기획) — deterministic program derived from land/zoning.
    product_planning = _first_dict_value(source_artifact, {"productPlanning", "product_planning"})
    if product_planning:
        rows = product_planning.get("rows") if isinstance(product_planning.get("rows"), list) else []
        priced_count = product_planning.get("pricedCount")
        benchmark_only = [r for r in rows if isinstance(r, dict) and r.get("assumptionBasis") == "planning_benchmark"]
        if priced_count == 0 and rows:
            findings.append(_finding(
                "proposed_price_basis_missing",
                "medium",
                "상품기획(자산유형별 세대/호실·면적)은 산출됐으나 분양가·임대료 근거가 없어 분양수입/NOI가 산정되지 않았습니다.",
                recommended_action="자산유형별 제안 분양가(평당) 또는 임대료·Cap Rate를 근거와 함께 입력한 뒤 상품기획을 재산출하세요.",
            ))
        elif benchmark_only:
            findings.append(_finding(
                "data_quality",
                "info",
                f"상품기획 {len(benchmark_only)}개 자산유형이 계획 벤치마크(전용률·평형·주차원단위) 기준입니다. 실제 설계·인허가 반영 전에는 참고치입니다.",
                recommended_action="건축계획(전용률·평형·주차) 확정 시 상품기획을 실제 설계값으로 재산출하세요.",
            ))

    # Market depth (P1-04): 제공주체 비교 — 매각주관사 자료 부재 / 제안가 고평가.
    depth = _first_dict_value(source_artifact, {"marketProductDepth", "market_product_depth"})
    if depth:
        provenance = depth.get("sourceProvenance") if isinstance(depth.get("sourceProvenance"), dict) else {}
        if provenance and not provenance.get("salesAgentProvided"):
            findings.append(_finding(
                "missing_evidence",
                "medium",
                "매각 주관사(분양대행) 제공 자료가 없어 시행사 제안 분양가·흡수 가정을 교차검증할 수 없습니다.",
                recommended_action="매각/분양대행 주관사 제공 자료를 확보해 시행사 제안치와 비교하세요.",
            ))
        comparison = depth.get("providerComparison") if isinstance(depth.get("providerComparison"), dict) else {}
        for row in comparison.get("rows", []) if isinstance(comparison.get("rows"), list) else []:
            delta = row.get("deltaVsPublicMedian")
            if isinstance(delta, (int, float)) and delta > 0.15:
                findings.append(_finding(
                    "risk_note",
                    "medium",
                    f"{row.get('label')} 제안 분양가가 공공 실거래 중위값 대비 {round(delta * 100)}% 높습니다(고평가 우려).",
                    recommended_action="분양가 산정근거 재확인 및 흡수율·할인분양 스트레스 시나리오를 검토하세요.",
                ))

    if source_artifact_key:
        for item in findings:
            item["sourceLocators"].append({"sourceType": "chief_pod_artifact", "artifactKey": source_artifact_key})
    return findings


def _pf_credit_findings(
    source_artifact: Dict[str, Any],
    source_artifact_key: str | None,
    project_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Evidence-aware review of the PF-Credit pod.

    Reads the deterministic signals the pod already produced — the scenario
    engine's risk flags (SENIOR_SHORTFALL / UNSOLD_LTV_BREACH / tranche recovery
    triggers), the PF structure, and covenant/EOD/CP terms — and turns them into
    reviewer findings + recommended actions. It never re-decides PASS/FAIL and
    never invents numbers; it routes existing deterministic flags and flags
    genuinely-missing evidence.
    """
    findings: List[Dict[str, Any]] = []
    scenarios = _first_dict_value(source_artifact, {"scenariosEvaluation", "scenarios_evaluation"})
    pf_structure = _first_dict_value(source_artifact, {"pfStructure", "pf_structure"})
    credit_review = _first_dict_value(source_artifact, {"creditReviewDecision", "credit_review_decision"})
    # Risk flag codes surfaced anywhere in the pod artifact (scenario/credit/risk
    # register). Collect the `code` values themselves — not the `riskFlags` list
    # wrapper — so nested {"code": "SENIOR_SHORTFALL"} entries are matched.
    codes = set(_collect_values(source_artifact, {"code"}))

    def has(prefixes: tuple) -> bool:
        return any(str(c).startswith(prefixes) for c in codes)

    # 1. Senior principal shortfall (base or stressed waterfall).
    if has(("SENIOR_SHORTFALL", "STRESS_SENIOR_SHORTFALL", "WATERFALL_SHORTFALL")):
        findings.append(_finding(
            "risk_note",
            "blocking",
            "선순위 원금 부족(Senior shortfall)이 Waterfall/스트레스 시나리오에서 감지되었습니다. 회수 재원이 선순위 원리금을 충당하지 못합니다.",
            recommended_action="PF 감액, 자본 확충, 추가 담보, 선분양/대량매각, 리파이낸싱 등 회수 재원 보강 증빙을 첨부한 뒤 Waterfall/Recovery를 재계산하세요.",
        ))
        findings.append(_finding(
            "recalculation_needed",
            "high",
            "선순위 회수 구조 변경(감액/담보/보증) 시 Waterfall·Tranche Recovery·Stress를 다시 실행해야 합니다.",
            recommended_action="pf_credit Pod를 회수 재원 보강 후 재실행하여 CalculationRun 이력을 갱신하세요.",
        ))
    # 2. Unsold LTV breach.
    if has(("UNSOLD_LTV_BREACH",)):
        findings.append(_finding(
            "risk_note",
            "high",
            "미분양 담보 LTV 한도 초과(Unsold LTV breach)가 감지되었습니다.",
            recommended_action="PF 감액, 추가 담보/보강, 선분양 계약, 대량매각(bulk sale) 근거로 미분양 LTV를 한도 내로 낮추는 증빙이 필요합니다.",
        ))
    elif has(("UNSOLD_LTV_HIGH",)):
        findings.append(_finding(
            "risk_note",
            "medium",
            "미분양 담보 LTV가 경고 수준입니다(한도 임박).",
            recommended_action="분양률 가정과 미분양 담보 평가 근거를 재확인하세요.",
        ))
    # 3. Tranche recovery trigger / shortfall.
    if has(("TRIGGER_REQUIRED", "MEZZANINE_SHORTFALL")):
        findings.append(_finding(
            "risk_note",
            "high",
            "Tranche 회수 시나리오에서 트리거 조건 또는 중순위 이하 회수 부족이 감지되었습니다.",
            recommended_action="Cash Sweep/조기상환 트리거, 준공·분양 연동 트리거 조항을 Term Sheet에서 확인하고 회수 시나리오 근거를 첨부하세요.",
        ))
    # 4. PF structure presence (loan amount, tranches, rate, fees, priority).
    tranche_count = _list_count(pf_structure.get("tranches") if isinstance(pf_structure, dict) else None)
    if not pf_structure or tranche_count == 0:
        findings.append(_finding(
            "missing_evidence",
            "high",
            "PF 구조(대출금액·Tranche·금리·수수료·상환순위)가 pod 산출물에 없습니다. 신용 판단을 evidence-ready로 볼 수 없습니다.",
            recommended_action="PF Term Sheet 또는 대출조건표를 업로드하여 Tranche/우선순위/금리를 확정하세요.",
        ))
    # 5. Covenant / EOD — prefer the derived Covenant/EOD engine output when present.
    covenant_eod = _first_dict_value(source_artifact, {"covenantEod", "covenant_eod"})
    if covenant_eod:
        for cov in covenant_eod.get("covenants", []):
            if not isinstance(cov, dict):
                continue
            if cov.get("breached"):
                findings.append(_finding(
                    "risk_note",
                    "high",
                    f"커버넌트 '{cov.get('label')}' 임계 초과 — 현재 {cov.get('currentValue')} > 한도 {cov.get('thresholdValue')}.",
                    recommended_action=cov.get("breachAction") or "회수/레버리지 보강 후 재계산이 필요합니다.",
                ))
            elif cov.get("status") == "needs_evidence":
                findings.append(_finding(
                    "missing_evidence",
                    "medium",
                    f"커버넌트 '{cov.get('label')}' 근거 부족 — 확정 전 근거가 필요합니다.",
                    recommended_action="관련 근거(공사비/등기/시장/NOI)를 첨부한 뒤 Covenant/EOD를 재산출하세요.",
                ))
    else:
        covenants = project_data.get("covenants") or project_data.get("covenant") or {}
        cp = project_data.get("conditions_precedent") or project_data.get("conditionsPrecedent")
        eod = (covenants.get("events_of_default") if isinstance(covenants, dict) else None) or project_data.get("events_of_default")
        if not covenants and not cp and not eod:
            findings.append(_finding(
                "missing_evidence",
                "medium",
                "Covenant / EOD(기한이익상실) / CP(선행조건) 조항 근거가 확인되지 않습니다.",
                recommended_action="대출약정 초안의 covenant·EOD·CP 조항을 첨부하여 트리거/상환/통제 조건을 검토 가능하게 하세요.",
            ))
    # 6. Scenario evaluation missing entirely — cannot assess senior recovery.
    if not scenarios:
        findings.append(_finding(
            "missing_evidence",
            "high",
            "시나리오 평가(Waterfall/미분양 LTV/Tranche 회수)가 실행되지 않아 선순위 회수 안전성을 판단할 수 없습니다.",
            recommended_action="사업수지·PF 구조 확정 후 시나리오 엔진을 실행하여 회수 근거를 생성하세요.",
        ))

    # Attach the deterministic credit decision + scenario set as provenance.
    scenario_set_id = scenarios.get("scenarioSetId") if isinstance(scenarios, dict) else None
    for item in findings:
        if scenario_set_id:
            item["sourceLocators"].append({"sourceType": "scenario_set", "scenarioSetId": scenario_set_id})
        if source_artifact_key:
            item["sourceLocators"].append({"sourceType": "chief_pod_artifact", "artifactKey": source_artifact_key})
        if isinstance(credit_review, dict) and credit_review.get("decision"):
            item.setdefault("context", {})["deterministicCreditDecision"] = credit_review.get("decision")
    return findings


def _attach_pod_provenance(findings: List[Dict[str, Any]], source_artifact_key: str | None, extra: Dict[str, Any] | None = None) -> None:
    for item in findings:
        if source_artifact_key:
            item["sourceLocators"].append({"sourceType": "chief_pod_artifact", "artifactKey": source_artifact_key})
        if extra:
            item["sourceLocators"].append(dict(extra))


def _cost_schedule_findings(
    source_artifact: Dict[str, Any],
    source_artifact_key: str | None,
    project_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Cost-Schedule reviewer: route the deterministic engine-readiness signals.

    This pod does not emit nested {"code": ...} risk flags — the reliable signal
    is ``engineReadiness`` (blockingFields / canRunCashFlow). Empty costBreakdown
    / schedule are routed as missing evidence, not zero cost. Nothing is invented.
    """
    findings: List[Dict[str, Any]] = []
    readiness = _first_dict_value(source_artifact, {"engineReadiness", "downstreamEngineReadiness"})
    blocking_fields = [str(f) for f in (readiness.get("blockingFields") or []) if f] if isinstance(readiness, dict) else []
    cost_breakdown = _first_dict_value(source_artifact, {"costBreakdown"})
    schedule = _first_dict_value(source_artifact, {"schedule"})

    if blocking_fields or not cost_breakdown:
        detail = ", ".join(blocking_fields[:6]) if blocking_fields else "costBreakdown 미첨부"
        findings.append(_finding(
            "missing_evidence",
            "high",
            f"공사비·간접비·금융비용을 포함한 총사업비 근거가 이 Pod 산출물에 확정되어 있지 않습니다(누락: {detail}). 비용 구조가 확정되지 않으면 사업수지·PF 회수 판단을 evidence-ready로 볼 수 없습니다.",
            recommended_action="도급계약·공사비 내역·간접비 내역·금융비용 산정표 또는 사업수지 Excel을 첨부하여 constructionCost/financeCost/otherCost/totalCost를 소스로케이터와 함께 확정하세요.",
        ))
    if not schedule:
        findings.append(_finding(
            "missing_evidence",
            "medium",
            "사업일정(토지잔금·인허가·착공·분양·준공·입주)과 공정률 근거가 없어 준공 시점 및 공정 지연 위험을 판단할 수 없습니다.",
            recommended_action="사업일정표·공정표·분양일정을 첨부하여 준공(completion)과 공정률을 확정하세요. 일정이 확정되어야 월별 현금흐름·Max Minus 산정이 가능합니다.",
        ))
    if isinstance(readiness, dict) and readiness.get("canRunCashFlow") is False:
        findings.append(_finding(
            "recalculation_needed",
            "high",
            "비용·일정 입력이 현금흐름 엔진 실행 요건(수입·비용·일정)을 충족하지 못해(canRunCashFlow=false) 하류 Cash Flow / 시나리오 산출이 아직 실행 가능한 상태가 아닙니다.",
            recommended_action="engineReadiness.blockingFields에 표시된 항목을 보강한 뒤 사업수지·현금흐름 엔진을 실행하여 하류 계산을 재생성하세요.",
        ))
    # Contractor risk / VE are not produced by any agent yet — flag honestly.
    findings.append(_finding(
        "missing_evidence",
        "medium",
        "Cost-Schedule Pod가 계획상 산출하기로 한 시공사 리스크(contractor_risk)와 VE/스펙 최적화(ve_options) 근거가 산출물에 존재하지 않습니다. 현재 Pod는 개발 타당성 계산만 수행합니다.",
        recommended_action="시공사 신용·책임준공·도급조건 자료와 VE 대안 근거를 별도 증빙으로 첨부하고, 해당 리스크는 사람이 직접 검토하세요.",
    ))
    _attach_pod_provenance(findings, source_artifact_key)
    return findings


def _feasibility_cashflow_findings(
    source_artifact: Dict[str, Any],
    source_artifact_key: str | None,
    project_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Feasibility-Cashflow reviewer: route Max Minus / IRR / PF-readiness signals.

    Reads the values the cash-flow engine and feasibility agent already produced
    (never recomputes maxMinus/IRR, never re-decides PASS/FAIL)."""
    findings: List[Dict[str, Any]] = []
    codes = set(_collect_values(source_artifact, {"code"}))

    def has(prefixes: tuple) -> bool:
        return any(str(c).startswith(prefixes) for c in codes)

    cashflow = _first_dict_value(source_artifact, {"cashFlowRunResult"})
    summary = cashflow.get("summary") if isinstance(cashflow.get("summary"), dict) else {}
    status = str(summary.get("status") or "")
    returns = _first_dict_value(source_artifact, {"returnMetrics"})
    return_metrics = returns.get("metrics") if isinstance(returns.get("metrics"), dict) else {}
    project_irr = return_metrics.get("projectIrr") if isinstance(return_metrics.get("projectIrr"), dict) else {}
    pf_readiness = _first_dict_value(source_artifact, {"pfReadiness"})
    data_quality = _first_dict_value(source_artifact, {"dataQuality"})
    missing_fields = [str(f) for f in (data_quality.get("missingFields") or []) if f] if isinstance(data_quality, dict) else []
    cashflow_error = _first_dict_value(source_artifact, {"cashFlowRunError"})
    logistics = _first_dict_value(source_artifact, {"logisticsAnalysis"})
    logistics_flags = [str(f) for f in (logistics.get("riskFlags") or []) if f] if isinstance(logistics, dict) else []

    max_minus_breach = has(("MAX_MINUS_EXCEEDS_PF_LIMIT",)) or status == "pf_limit_shortfall"
    if max_minus_breach:
        findings.append(_finding(
            "risk_note", "blocking",
            "최대 순현금 부족(Max Minus)이 가용 PF 한도를 초과합니다. 사업 기간 중 자금 부족을 조달로 충당할 수 없습니다.",
            recommended_action="PF 한도 증액, 자본 확충, 분양 대금 회수 스케줄(선분양/중도금) 개선 근거를 첨부한 뒤 Cash Flow 엔진을 재실행하세요.",
        ))
        findings.append(_finding(
            "recalculation_needed", "high",
            "자금 조달 구조(PF 한도/트랜치/회수 스케줄)를 변경하면 월별 현금흐름·Max Minus·PF 인출 스케줄을 다시 산출해야 합니다.",
            recommended_action="조달 보강 후 feasibility_cashflow Pod를 재실행하여 CalculationRun 이력과 Max Minus를 갱신하세요.",
        ))
    if has(("PF_LIMIT_FULLY_DRAWN",)):
        findings.append(_finding(
            "risk_note", "medium",
            "사업 기간 중 PF 한도가 전액 소진됩니다(미인출 한도 0). 추가 비용 초과나 회수 지연 시 완충 여력이 없습니다.",
            recommended_action="공사비 초과·분양 지연 민감도에서 예비비/추가 조달 여력을 확인하고 미인출 한도 확보 방안을 검토하세요.",
        ))
    if cashflow_error or not cashflow:
        findings.append(_finding(
            "missing_evidence", "high",
            "현금흐름 엔진이 실행되지 않았습니다(입력 미비). Max Minus·필요 PF 한도·자금 부족 시점을 판단할 근거가 없습니다.",
            recommended_action="매출·총사업비·공사 착공/준공 일정 및 PF 구조를 확정하여 현금흐름 엔진 입력을 완성한 뒤 재실행하세요.",
        ))
    elif str(cashflow.get("dataStatus") or "") == "partial":
        findings.append(_finding(
            "data_quality", "medium",
            "현금흐름 결과가 부분(partial) 상태입니다. 매핑 경고가 있어 일부 가정이 자동 보정되었습니다.",
            recommended_action="매핑 진단(warnings)을 확인하고 회수 스케줄·비용 항목·트랜치 매핑을 보강한 뒤 재산출하세요.",
        ))
    irr_status = str(project_irr.get("status") or "")
    irr_value = project_irr.get("value")
    if irr_status == "loss" or (isinstance(irr_value, (int, float)) and irr_value < 0):
        findings.append(_finding(
            "risk_note", "high",
            "프로젝트 IRR이 음수입니다. 현 가정 하에서 투입 자본을 회수하지 못합니다.",
            recommended_action="분양가·공사비·토지비·사업 일정 가정을 재검토하고, 수정 가정으로 수익성 지표를 재산출하세요.",
        ))
    elif irr_status == "not_available" or not returns:
        findings.append(_finding(
            "missing_evidence", "medium",
            "수익성 지표(IRR/Equity Multiple/ICR)가 산출되지 않았습니다(현금흐름 시계열 근거 부족).",
            recommended_action="유입/유출이 포함된 프로젝트 현금흐름 시계열 또는 매출·비용·일정 가정을 첨부하여 수익성 지표를 산출하세요.",
        ))
    elif project_irr.get("meetsHurdle") is False:
        findings.append(_finding(
            "risk_note", "medium",
            "프로젝트 IRR이 목표 수익률(허들)을 하회합니다. 지분 가격 또는 사업 구조 재검토가 필요합니다.",
            recommended_action="허들 대비 IRR 격차의 원인(분양가/원가/레버리지)을 점검하고 구조 개선안을 검토하세요.",
        ))
    headroom = pf_readiness.get("headroomToRecommendedLimit") if isinstance(pf_readiness, dict) else None
    if (isinstance(pf_readiness, dict) and pf_readiness.get("status") == "leverage_review_required") or (isinstance(headroom, (int, float)) and headroom < 0):
        findings.append(_finding(
            "risk_note", "high",
            "PF 조달 규모가 권고 한도(매출·원가 기준)를 초과하여 레버리지 재검토가 필요합니다.",
            recommended_action="권고 PF 한도 대비 초과분에 대해 자본 확충 또는 조달 규모 조정 근거를 첨부하세요.",
        ))
    if missing_fields:
        findings.append(_finding(
            "missing_evidence", "high",
            f"사업수지 핵심 입력값이 누락되어 예비 산출 상태입니다(누락: {', '.join(missing_fields[:6])}). evidence-ready 판단으로 볼 수 없습니다.",
            recommended_action="누락된 토지면적·연면적/용적률·총매출·총사업비·PF 구조·사업일정 항목을 보강한 뒤 재실행하세요.",
        ))
    if isinstance(logistics, dict) and logistics.get("applicable") and logistics_flags:
        findings.append(_finding(
            "risk_note", "medium",
            "물류 등 임대·선매각형 자산의 수지 전제(Cap Rate, 선매각가, 임차인 안정성, 스펙)에서 결측 또는 경고 플래그가 감지되었습니다.",
            recommended_action="임대료/NOI, Cap Rate, 선매각 계약가, 임차인 신용·임대차 기간 근거를 첨부하여 Exit 가치와 선매각 LTV 전제를 확정하세요.",
        ))
    extra = {"sourceType": "cash_flow_run", "status": status} if status else None
    _attach_pod_provenance(findings, source_artifact_key, extra)
    if status:
        for item in findings:
            item.setdefault("context", {})["deterministicCashFlowStatus"] = status
    return findings


def _public_policy_findings(
    source_artifact: Dict[str, Any],
    source_artifact_key: str | None,
    project_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Public-Policy reviewer: route the developability-score dimension signals.

    Flags dimensions computed without data (dataLimitations), missing regulation
    basis, weak gaps, and the still-stubbed policy-simulation output."""
    findings: List[Dict[str, Any]] = []
    score = _first_dict_value(source_artifact, {"developabilityScore"})
    dimensions = _first_dict_value(score, {"dimensions"}) if score else {}
    grade = str(score.get("grade") or "") if isinstance(score, dict) else ""
    gaps = [str(g) for g in (score.get("gaps") or []) if g] if isinstance(score, dict) else []
    data_limitations = [str(d) for d in (score.get("dataLimitations") or []) if d] if isinstance(score, dict) else []
    policy_notes = " ".join(str(n) for n in (source_artifact.get("policyNotes") or []))

    if grade == "no_data":
        findings.append(_finding(
            "missing_evidence", "blocking",
            "개발가능성 평가에 필요한 토지·법규·사업성·시장성 데이터가 모두 부족하여 점수를 산출할 수 없습니다(grade=no_data).",
            recommended_action="토지 증빙, 규제 자료, 사업수지, 시장성 분석 중 최소 필수 입력을 확보한 뒤 pod를 재실행하세요.",
        ))
    elif data_limitations:
        findings.append(_finding(
            "missing_evidence", "high",
            f"개발가능성 종합점수가 일부 축({', '.join(data_limitations[:5])}) 데이터 없이 산출되었습니다. 해당 축은 가중치에서 제외되어 종합점수가 왜곡될 수 있습니다.",
            recommended_action="dataLimitations에 표기된 축의 근거 자료를 첨부한 뒤 개발가능성 점수를 재산출하세요.",
        ))
    regulation = dimensions.get("regulation") if isinstance(dimensions, dict) else None
    if isinstance(regulation, dict) and regulation.get("available") is False:
        findings.append(_finding(
            "missing_evidence", "high",
            "용도지역·용적률(regulation) 근거가 없어 규제 적합성 축이 점수에서 제외되었습니다. 인허가 가능성·규제 리스크를 evidence-ready로 볼 수 없습니다.",
            recommended_action="용도지역, 법정/적용 용적률, 도시계획 자료를 첨부한 뒤 규제 축을 포함해 재산출하세요.",
        ))
    if gaps:
        findings.append(_finding(
            "risk_note", "medium",
            f"개발가능성 축 중 취약 항목이 감지되었습니다(gaps: {', '.join(gaps[:5])}) — 해당 축 점수가 50점 미만입니다.",
            recommended_action="gaps에 나열된 축의 개선 근거를 검토하고 보완 자료를 첨부하세요. 점수를 재평가하되 PASS/FAIL은 재판정하지 마세요.",
        ))
    if "until dedicated policy simulation" in policy_notes or "policy simulation agents are implemented" in policy_notes:
        findings.append(_finding(
            "missing_evidence", "high",
            "공공성·도시계획·기부채납·정책 시뮬레이션 전용 분석이 아직 실행되지 않았습니다. 현재 산출물은 개발가능성/규제 근거를 재사용한 대체 결과입니다.",
            recommended_action="공공기여 자료·정책/공모 지침을 첨부하고, 전용 정책 시뮬레이션이 실행되기 전까지 정책 판단을 report-grade로 사용하지 마세요.",
        ))
    _attach_pod_provenance(findings, source_artifact_key)
    return findings


def _document_market_facts(source_artifact: Dict[str, Any], project_data: Dict[str, Any]) -> Dict[str, Any]:
    facts = dict(_first_dict_value(source_artifact, {"documentParsedMarketFacts"}))
    document_evidence = _document_market_evidence(project_data)
    if not facts and document_evidence:
        facts = {
            "schemaVersion": "realytics-document-market-facts-v1",
            "hasDocumentMarketEvidence": True,
            "sourceDocument": document_evidence.get("sourceDocument") if isinstance(document_evidence.get("sourceDocument"), dict) else {},
            "parserVersion": document_evidence.get("parserVersion"),
            "productMixCount": _list_count(document_evidence.get("productMix") or document_evidence.get("product_mix")),
            "saleComparableCount": sum(
                _list_count(document_evidence.get(key))
                for key in (
                    "comparables",
                    "manualComparables",
                    "manual_comparables",
                    "transactionComparables",
                    "transaction_comparables",
                    "presaleComparables",
                    "presale_comparables",
                    "listingComparables",
                    "listing_comparables",
                )
            ),
            "rentComparableCount": _list_count(document_evidence.get("rentComparables") or document_evidence.get("rent_comparables")),
            "proposedPricePerPy": document_evidence.get("proposedPricePerPy") or document_evidence.get("proposed_price_per_py"),
            "demandLevel": document_evidence.get("demandLevel") or document_evidence.get("demand_level"),
            "upcomingSupplyUnits": document_evidence.get("upcomingSupplyUnits") or document_evidence.get("upcoming_supply_units"),
        }
    if not facts:
        return {}
    reconciliation = _first_dict_value(source_artifact, {"documentMarketReconciliation"})
    if reconciliation:
        facts.setdefault("reconciliationStatus", reconciliation.get("status"))
        facts.setdefault("issueCount", len(reconciliation.get("issues") or []))
        summary = reconciliation.get("evidenceSummary") if isinstance(reconciliation.get("evidenceSummary"), dict) else {}
        for key in ("evidenceRecordCount", "sourceLocatorCount", "fieldLocatorCoverage"):
            if key in summary:
                facts.setdefault(key, summary.get(key))
    facts.setdefault("factCount", _document_fact_count(facts))
    facts.setdefault("hasDocumentMarketEvidence", True)
    return facts


def _document_market_evidence(project_data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("document_market_evidence", "documentMarketEvidence", "market_evidence", "marketEvidence"):
        value = project_data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _document_fact_count(facts: Dict[str, Any]) -> int:
    total = 0
    for key in ("productMixCount", "saleComparableCount", "rentComparableCount"):
        try:
            total += int(facts.get(key) or 0)
        except (TypeError, ValueError):
            pass
    for key in ("proposedPricePerPy", "demandLevel", "upcomingSupplyUnits"):
        if facts.get(key) not in (None, ""):
            total += 1
    return total


def _first_dict_value(value: Any, keys: set[str]) -> Dict[str, Any]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, dict):
                return item
        for item in value.values():
            found = _first_dict_value(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_dict_value(item, keys)
            if found:
                return found
    return {}


def _count_market_comparables(value: Any) -> int:
    comparable_keys = {
        "comparables",
        "normalizedComparables",
        "normalized_comparables",
        "transactionComparables",
        "transaction_comparables",
        "manualComparables",
        "manual_comparables",
        "presaleComparables",
        "presale_comparables",
        "listingComparables",
        "listing_comparables",
        "rentComparables",
        "rent_comparables",
        "liveMarketComparables",
        "live_market_comparables",
    }
    total = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key in comparable_keys:
                if isinstance(item, list):
                    total += len([row for row in item if row])
                elif isinstance(item, dict):
                    rows = item.get("rows") or item.get("records") or item.get("items") or item.get("comparables")
                    if isinstance(rows, list):
                        total += len([row for row in rows if row])
            total += _count_market_comparables(item)
    elif isinstance(value, list):
        for item in value:
            total += _count_market_comparables(item)
    return total


def _first_numeric_value(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                parsed = _to_float(item)
                if parsed is not None:
                    return parsed
        for item in value.values():
            parsed = _first_numeric_value(item, keys)
            if parsed is not None:
                return parsed
    elif isinstance(value, list):
        for item in value:
            parsed = _first_numeric_value(item, keys)
            if parsed is not None:
                return parsed
    return None


def _market_income_review_required(project_data: Dict[str, Any], source_artifact: Dict[str, Any], options: Dict[str, Any]) -> bool:
    workflow_track = str(options.get("workflowTrack") or options.get("workflow_track") or "").lower()
    project_type = str((project_data.get("project") or {}).get("type") or project_data.get("projectType") or "").lower()
    text = json.dumps({"projectType": project_type, "artifact": source_artifact}, ensure_ascii=False, default=str).lower()
    if workflow_track in {"real_asset_finance", "reit", "fund", "finance"}:
        return True
    income_keywords = {
        "logistics",
        "office",
        "retail",
        "commercial",
        "reit",
        "fund",
        "rental",
        "lease",
        "refinancing",
        "cap rate",
        "cap_rate",
        "noi",
    }
    return any(keyword in text for keyword in income_keywords)


def _has_income_or_cap_rate_evidence(value: Any) -> bool:
    income_keys = {
        "capRate",
        "cap_rate",
        "noi",
        "netOperatingIncome",
        "net_operating_income",
        "rent",
        "rentPerPy",
        "rent_per_py",
        "rentPerPyeong",
        "rent_per_pyeong",
        "monthlyRent",
        "monthly_rent",
        "annualRent",
        "annual_rent",
        "leaseIncome",
        "lease_income",
    }
    return _first_numeric_value(value, income_keys) not in (None, 0)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_from_findings(findings: List[Dict[str, Any]]) -> str:
    severities = {item.get("severity") for item in findings}
    if "blocking" in severities:
        return "blocking"
    if "high" in severities:
        return "needs_human_review"
    if severities.intersection({"medium"}):
        return "warning"
    return "pass"


def _normalize_findings(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    findings: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        finding = {
            "findingId": str(item.get("findingId") or f"finding:llm:{uuid4()}"),
            "category": str(item.get("category") or "risk_note"),
            "severity": str(item.get("severity") or "medium"),
            "message": str(item.get("message") or item.get("finding") or "Review finding"),
            "evidenceIds": _as_str_list(item.get("evidenceIds") or item.get("evidence_ids")),
            "sourceLocators": item.get("sourceLocators") if isinstance(item.get("sourceLocators"), list) else [],
            "recommendedAction": item.get("recommendedAction") or item.get("recommended_action"),
        }
        if finding["severity"] not in {"info", "medium", "high", "blocking"}:
            finding["severity"] = "medium"
        findings.append(finding)
    return findings


def _normalize_handoffs(value: Any, *, agent_id: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    handoffs: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        handoffs.append({
            "handoffId": str(item.get("handoffId") or f"handoff:pod-ai-review:{uuid4()}"),
            "fromAgent": str(item.get("fromAgent") or agent_id),
            "toAgent": str(item.get("toAgent") or "ChiefDealOrchestrator"),
            "handoffType": str(item.get("handoffType") or "review_request"),
            "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
            "evidenceIds": _as_str_list(item.get("evidenceIds") or item.get("evidence_ids")),
            "confidence": _bounded_float(item.get("confidence"), default=0.7),
        })
    return handoffs


def _collect_values(value: Any, keys: set[str]) -> List[str]:
    found: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item is not None:
                found.extend(_as_str_list(item))
            else:
                found.extend(_collect_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_values(item, keys))
    return sorted({item for item in found if item})


def _collect_source_locators(value: Any) -> List[Dict[str, Any]]:
    locators: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"sourceLocator", "source_locator"} and isinstance(item, dict):
                locators.append(item)
            elif key in {"sourceLocators", "source_locators"} and isinstance(item, list):
                locators.extend([locator for locator in item if isinstance(locator, dict)])
            else:
                locators.extend(_collect_source_locators(item))
    elif isinstance(value, list):
        for item in value:
            locators.extend(_collect_source_locators(item))
    return locators


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _compact_json(value: Any, *, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"
