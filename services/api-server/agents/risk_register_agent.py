from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


RISK_REGISTER_SCHEMA_VERSION = "realytics-risk-register-v1"


class RiskRegisterAgent:
    """Aggregate agent-specific risks into one credit-review risk register."""

    def build(self, project_data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[Dict[str, Any]] = []
        risks.extend(self._land_risks(artifacts.get("landAgentReview") or {}))
        risks.extend(self._marketability_risks(artifacts.get("marketabilityAnalysis") or {}))
        risks.extend(self._legal_risks(artifacts.get("legalPermitReview") or {}))
        risks.extend(self._cash_flow_risks(artifacts.get("developerFeasibility") or {}))
        risks.extend(self._pf_structure_risks(artifacts.get("pfStructure") or {}))
        risks.extend(self._scenario_risks(artifacts.get("scenariosEvaluation") or {}))
        risks.extend(self._data_quality_risks(artifacts.get("dataQualityReview") or {}))
        risks.extend(self._matrix_risks(artifacts.get("riskAssessment") or {}))
        risks = self._dedupe_and_number(risks)
        risks = sorted(risks, key=lambda item: (-item["score"], item["riskId"]))
        status = self._status(risks)
        limitations = self._data_limitations(artifacts)
        if status == "clear" and limitations:
            status = "needs_review"
        top_risks = risks[:10]
        approval_conditions = self._approval_conditions(top_risks)
        return {
            "schemaVersion": RISK_REGISTER_SCHEMA_VERSION,
            "analysisType": "risk_register",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "summary": self._summary(risks),
            "risks": risks,
            "topRisks": top_risks,
            "approvalConditions": approval_conditions,
            "creditReviewInputs": {
                "recommendedDecisionBand": self._decision_band(status, risks),
                "mustResolveBeforeApproval": [
                    risk for risk in risks if risk["severity"] in {"critical", "blocking"}
                ],
                "humanReviewRequiredCount": sum(1 for risk in risks if risk["requiresHumanReview"]),
                "approvalConditions": approval_conditions,
                "riskOwnerAgents": sorted({risk["ownerAgent"] for risk in risks if risk.get("ownerAgent")}),
            },
            "dataLimitations": limitations,
        }

    def _land_risks(self, land: Dict[str, Any]) -> List[Dict[str, Any]]:
        risks = []
        evidence_ids = land.get("evidenceIds") or []
        source_locators = land.get("sourceLocators") or []
        for index, issue in enumerate(land.get("blockingIssues", []), 1):
            issue_text = str(issue)
            registry_evidence_pending = "missing_registry_evidence" in issue_text or "Registry and encumbrance evidence is missing" in issue_text
            risks.append(self._risk(
                source_agent="LandAgent",
                source_artifact="landAgentReview",
                category="Land Risk",
                code=f"LAND_BLOCKING_{index}",
                title="Land readiness blocking issue",
                description=issue_text,
                severity="medium" if registry_evidence_pending else "critical",
                probability="medium",
                mitigant="Attach licensed registry evidence and continue legal verification; convert to blocking only if a confirmed encumbrance or ownership mismatch is found." if registry_evidence_pending else "Resolve land title, acquisition, registry, or ledger validation issue before PF approval.",
                evidence_ids=evidence_ids,
                source_locators=source_locators,
            ))
        for index, issue in enumerate(land.get("dataLimitations", []), 1):
            risks.append(self._risk(
                source_agent="LandAgent",
                source_artifact="landAgentReview",
                category="Land Risk",
                code=f"LAND_LIMITATION_{index}",
                title="Land evidence limitation",
                description=str(issue),
                severity="medium",
                probability="medium",
                mitigant="Attach PNU, official land price, registry, acquisition, and source evidence.",
                evidence_ids=evidence_ids,
                source_locators=source_locators,
            ))
        summary = land.get("summary") or {}
        coverage_checks = [
            ("PNU_COVERAGE_INCOMPLETE", "PNU coverage is below 100%.", summary.get("pnuCoverageRatio")),
            ("OFFICIAL_PRICE_COVERAGE_INCOMPLETE", "Official land price coverage is below 100%.", summary.get("officialLandPriceCoverageRatio")),
            ("REGISTRY_EVIDENCE_INCOMPLETE", "Registry evidence coverage is below 100%.", summary.get("registryEvidenceCoverageRatio")),
            ("LAND_ACQUISITION_INCOMPLETE", "Land acquisition area ratio is below 100%.", summary.get("acquisitionAreaRatio")),
        ]
        for code, description, ratio in coverage_checks:
            if isinstance(ratio, (int, float)) and ratio < 1:
                risks.append(self._risk(
                    source_agent="LandAgent",
                    source_artifact="landAgentReview",
                    category="Land Risk",
                    code=code,
                    title=description,
                    description=f"{description} Current ratio: {ratio:.1%}.",
                    severity="high" if ratio < 0.8 else "medium",
                    probability="medium",
                    mitigant="Complete land evidence enrichment and closing checklist.",
                    evidence_ids=evidence_ids,
                    source_locators=source_locators,
                ))
        return risks

    def _marketability_risks(self, marketability: Dict[str, Any]) -> List[Dict[str, Any]]:
        risks = []
        for item in marketability.get("riskFlagDetails", []):
            if not isinstance(item, dict):
                continue
            risks.append(self._risk(
                source_agent="MarketabilityAgent",
                source_artifact="marketabilityAnalysis",
                category=self._market_category(item.get("category")),
                code=item.get("code") or "MARKETABILITY_RISK",
                title=item.get("code") or "Marketability risk",
                description=item.get("message") or item.get("description") or "Marketability risk flag was generated.",
                severity=self._normalize_severity(item.get("severity")),
                probability="medium",
                mitigant=self._market_mitigant(item.get("code")),
                evidence_ids=item.get("evidenceIds") or item.get("evidence_ids") or [],
                source_locators=item.get("sourceLocators") or item.get("source_locators") or [],
                source_document_ids=item.get("sourceDocumentIds") or item.get("source_document_ids") or [],
            ))
        return risks

    def _legal_risks(self, legal: Dict[str, Any]) -> List[Dict[str, Any]]:
        risks = []
        for check in legal.get("checks", []):
            if not isinstance(check, dict) or check.get("severity") == "clear":
                continue
            risks.append(self._risk(
                source_agent="LegalReviewAgent",
                source_artifact="legalPermitReview",
                category="Legal Risk",
                code=f"LEGAL_{str(check.get('key') or 'CHECK').upper()}",
                title=check.get("title") or "Legal review issue",
                description=" / ".join(str(item) for item in check.get("findings", [])),
                severity="critical" if check.get("severity") == "blocking" else "medium",
                probability="medium",
                mitigant="Resolve through counsel memo, permit evidence, security document, or CP checklist.",
            ))
        return risks

    def _cash_flow_risks(self, feasibility: Dict[str, Any]) -> List[Dict[str, Any]]:
        cash_flow = feasibility.get("cashFlowRunResult") or {}
        risks = []
        for flag in cash_flow.get("riskFlags", []):
            if not isinstance(flag, dict):
                continue
            risks.append(self._risk(
                source_agent="CashFlowEngine",
                source_artifact="developerFeasibility.cashFlowRunResult",
                category="Financing Risk",
                code=flag.get("code") or "CASH_FLOW_RISK",
                title=flag.get("code") or "Cash flow risk",
                description=flag.get("message") or "Cash flow risk flag was generated.",
                severity=self._normalize_severity(flag.get("severity")),
                probability="medium",
                mitigant="Rerun monthly cash flow, adjust PF limit/equity, or tighten drawdown controls.",
            ))
        summary = cash_flow.get("summary") or {}
        if isinstance(summary.get("maxMinus"), (int, float)) and isinstance(summary.get("requiredPfLimit"), (int, float)):
            if abs(summary["maxMinus"]) > summary["requiredPfLimit"]:
                risks.append(self._risk(
                    source_agent="CashFlowEngine",
                    source_artifact="developerFeasibility.cashFlowRunResult",
                    category="Financing Risk",
                    code="MAX_MINUS_GT_REQUIRED_PF_LIMIT",
                    title="Max Minus exceeds required PF limit",
                    description="Monthly cash-flow max minus is larger than the required PF limit.",
                    severity="high",
                    probability="medium",
                    mitigant="Increase PF/equity buffer or revise expenditure and collection schedule.",
                ))
        return risks

    def _pf_structure_risks(self, pf_structure: Dict[str, Any]) -> List[Dict[str, Any]]:
        risks = []
        metrics = pf_structure.get("metrics") or {}
        for metric_key, threshold, code, title in [
            ("ltv", 0.75, "PF_LTV_HIGH", "PF LTV is high"),
            ("ltc", 0.85, "PF_LTC_HIGH", "PF LTC is high"),
            ("exit_sale_rate", 0.70, "PF_EXIT_SALE_RATE_HIGH", "PF Exit sale rate is high"),
        ]:
            metric = metrics.get(metric_key) or {}
            value = metric.get("value") if isinstance(metric, dict) else None
            if isinstance(value, (int, float)) and value >= threshold:
                risks.append(self._risk(
                    source_agent="PFStructuringCalculator",
                    source_artifact="pfStructure",
                    category="Financing Risk" if metric_key != "exit_sale_rate" else "Exit Risk",
                    code=code,
                    title=title,
                    description=f"{title}: {value:.1%}.",
                    severity="high" if value >= threshold + 0.1 else "medium",
                    probability="medium",
                    mitigant="Adjust tranche sizing, equity, pricing assumptions, covenants, or repayment waterfall.",
                ))
        credit_summary = pf_structure.get("credit_summary") or pf_structure.get("creditSummary")
        if isinstance(credit_summary, str) and "review" in credit_summary.lower():
            risks.append(self._risk(
                source_agent="PFStructuringCalculator",
                source_artifact="pfStructure",
                category="Financing Risk",
                code="PF_CREDIT_REVIEW_ATTENTION",
                title="PF structure requires credit review attention",
                description=credit_summary,
                severity="medium",
                probability="medium",
                mitigant="Review PF metrics, tranche waterfall, and approval conditions.",
            ))
        return risks

    def _scenario_risks(self, scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
        risks = []
        for flag in scenario.get("riskFlags", []):
            if not isinstance(flag, dict):
                continue
            risks.append(self._risk(
                source_agent="CalculationScenarioEngine",
                source_artifact="scenariosEvaluation",
                category=self._scenario_category(flag.get("code")),
                code=flag.get("code") or "SCENARIO_RISK",
                title=flag.get("code") or "Scenario risk",
                description=flag.get("message") or "Scenario Engine risk flag was generated.",
                severity=self._normalize_severity(flag.get("severity")),
                probability="medium",
                mitigant="Review waterfall, unsold LTV, discount-sale trigger, and tranche recovery scenarios.",
                evidence_ids=flag.get("evidenceIds") or flag.get("evidence_ids") or [],
                source_locators=flag.get("sourceLocators") or flag.get("source_locators") or [],
                source_document_ids=flag.get("sourceDocumentIds") or flag.get("source_document_ids") or [],
            ))
        return risks

    def _data_quality_risks(self, data_quality: Dict[str, Any]) -> List[Dict[str, Any]]:
        risks = []
        for check in data_quality.get("checks", []):
            if not isinstance(check, dict) or check.get("status") not in {"blocking", "warning"}:
                continue
            risks.append(self._risk(
                source_agent="DataQualityAgent",
                source_artifact="dataQualityReview",
                category="Data Quality Risk",
                code=str(check.get("key") or "DATA_QUALITY").upper(),
                title=check.get("label") or "Data quality issue",
                description=check.get("message") or "Data quality check requires review.",
                severity="critical" if check.get("status") == "blocking" else "medium",
                probability="medium",
                mitigant="Select authoritative source, correct source data, or approve explicit assumption override.",
                source_locators=self._data_quality_source_locators(check),
            ))
        return risks

    def _matrix_risks(self, risk_assessment: Dict[str, Any]) -> List[Dict[str, Any]]:
        risks = []
        for item in risk_assessment.get("items", []):
            if not isinstance(item, dict):
                continue
            grade = item.get("grade")
            if grade not in {"High", "Critical"}:
                continue
            risks.append(self._risk(
                source_agent="RiskAssessmentEngine",
                source_artifact="riskAssessment",
                category=item.get("category") or "Project Risk",
                code=self._code(item.get("item") or item.get("category") or "RISK_MATRIX_ITEM"),
                title=item.get("item") or "Risk matrix item",
                description=item.get("description") or "",
                severity="critical" if grade == "Critical" else "high",
                probability=self._probability_label(item.get("probability")),
                mitigant=item.get("mitigation") or "Assign owner, mitigation, and closing condition.",
            ))
        return risks

    def _risk(
        self,
        *,
        source_agent: str,
        source_artifact: str,
        category: str,
        code: str,
        title: str,
        description: str,
        severity: str,
        probability: str,
        mitigant: str,
        evidence_ids: List[str] | None = None,
        source_locators: List[Dict[str, Any]] | None = None,
        source_document_ids: List[str] | None = None,
    ) -> Dict[str, Any]:
        normalized_severity = self._normalize_severity(severity)
        normalized_probability = self._normalize_probability(probability)
        score = self._severity_score(normalized_severity) * self._probability_score(normalized_probability)
        normalized_code = self._code(code)
        normalized_source_locators = source_locators or [
            self._artifact_source_locator(
                source_artifact=source_artifact,
                code=normalized_code,
                title=title,
                description=description,
            )
        ]
        return {
            "riskId": "",
            "sourceAgent": source_agent,
            "sourceArtifact": source_artifact,
            "category": category,
            "code": normalized_code,
            "title": title,
            "description": description,
            "severity": normalized_severity,
            "probability": normalized_probability,
            "score": score,
            "grade": self._grade(score),
            "financialImpact": None,
            "mitigant": mitigant,
            "evidenceIds": evidence_ids or [],
            "sourceLocators": normalized_source_locators,
            "sourceDocumentIds": source_document_ids or [],
            "evidenceGraphLinks": {
                "riskNodeId": None,
                "sourceArtifactNodeId": None,
                "evidenceNodeIds": [],
                "claimNodeIds": [],
                "sourceDocumentNodeIds": [],
                "sourceLocatorNodeIds": [],
            },
            "ownerAgent": self._owner_agent(source_agent),
            "requiresHumanReview": normalized_severity in {"critical", "blocking", "high"},
            "status": "open",
            "reviewStatus": "pending_review" if normalized_severity in {"critical", "blocking", "high"} else "not_required",
            "reviewedBy": None,
            "reviewedAt": None,
            "reviewComment": None,
            "reviewHistory": [],
            "creditDecisionImpact": self._credit_impact(normalized_severity),
        }

    def _artifact_source_locator(
        self,
        *,
        source_artifact: str,
        code: str,
        title: str,
        description: str,
    ) -> Dict[str, Any]:
        artifact_path = source_artifact or "unknownArtifact"
        return {
            "locatorId": f"{artifact_path}:{code}",
            "sourceType": "agent_artifact",
            "source": artifact_path.split(".", 1)[0],
            "path": artifact_path,
            "label": title or code,
            "locatorPrecision": "artifact_level",
            "note": (
                "Fallback locator created from the responsible agent artifact. "
                "Attach document page, Excel cell, API row, or evidence ID before final approval when available."
            ),
            "description": description,
        }

    def _dedupe_and_number(self, risks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[tuple[str, str], Dict[str, Any]] = {}
        for risk in risks:
            key = (risk["sourceArtifact"], risk["code"])
            existing = deduped.get(key)
            if not existing or risk["score"] > existing["score"]:
                deduped[key] = risk
        counters: Dict[str, int] = {}
        for risk in deduped.values():
            prefix = self._risk_prefix(risk["category"])
            counters[prefix] = counters.get(prefix, 0) + 1
            risk["riskId"] = f"R-{prefix}-{counters[prefix]:03d}"
        return list(deduped.values())

    def _summary(self, risks: List[Dict[str, Any]]) -> Dict[str, Any]:
        severity_counts: Dict[str, int] = {}
        category_counts: Dict[str, int] = {}
        for risk in risks:
            severity_counts[risk["severity"]] = severity_counts.get(risk["severity"], 0) + 1
            category_counts[risk["category"]] = category_counts.get(risk["category"], 0) + 1
        return {
            "riskCount": len(risks),
            "criticalCount": severity_counts.get("critical", 0) + severity_counts.get("blocking", 0),
            "highCount": severity_counts.get("high", 0),
            "mediumCount": severity_counts.get("medium", 0),
            "lowCount": severity_counts.get("low", 0),
            "humanReviewRequiredCount": sum(1 for risk in risks if risk["requiresHumanReview"]),
            "bySeverity": severity_counts,
            "byCategory": category_counts,
        }

    def _status(self, risks: List[Dict[str, Any]]) -> str:
        if any(risk["severity"] in {"critical", "blocking"} for risk in risks):
            return "blocking"
        if any(risk["severity"] == "high" for risk in risks):
            return "high_risk"
        if risks:
            return "watch"
        return "clear"

    def _approval_conditions(self, top_risks: List[Dict[str, Any]]) -> List[str]:
        conditions = []
        for risk in top_risks:
            if risk["severity"] in {"critical", "blocking", "high"}:
                conditions.append(f"{risk['riskId']} {risk['title']}: {risk['mitigant']}")
        return conditions[:10]

    def _decision_band(self, status: str, risks: List[Dict[str, Any]]) -> str:
        if status == "needs_review":
            return "insufficient_evidence"
        if status == "blocking":
            return "rework_required"
        if status == "high_risk":
            return "conditional_approval_or_structure_revision"
        if risks:
            return "conditional_approval_possible"
        return "approval_possible"

    def _data_quality_source_locators(self, check: Dict[str, Any]) -> List[Dict[str, Any]]:
        locators: List[Dict[str, Any]] = []
        metadata = check.get("metadata") or {}
        values = metadata.get("values") if isinstance(metadata, dict) else None
        if not isinstance(values, list):
            return locators
        check_key = self._code(check.get("key") or "DATA_QUALITY")
        for index, item in enumerate(values, 1):
            if not isinstance(item, dict):
                continue
            source = item.get("source") or item.get("path") or item.get("field") or item.get("label")
            if not source:
                continue
            locators.append({
                "locatorId": f"{check_key}:{index}",
                "sourceType": "agent_artifact",
                "source": source,
                "path": item.get("path") or item.get("field"),
                "label": item.get("label") or source,
                "value": item.get("value"),
            })
        return locators

    def _data_limitations(self, artifacts: Dict[str, Any]) -> List[str]:
        limitations = []
        if not artifacts.get("scenariosEvaluation"):
            limitations.append("Scenario Engine output is missing; waterfall and unsold LTV risks may be incomplete.")
        if not artifacts.get("developerFeasibility", {}).get("cashFlowRunResult"):
            limitations.append("Cash Flow Engine output is missing; Max Minus and drawdown risks may be incomplete.")
        return limitations

    def _market_category(self, category: Any) -> str:
        category_map = {
            "pricing": "Market Risk",
            "supply": "Market Risk",
            "demand": "Market Risk",
            "market_evidence": "Market Risk",
            "absorption": "Sales Risk",
            "pf_exit": "Exit Risk",
            "unsold_recovery": "Exit Risk",
            "product": "Sales Risk",
        }
        return category_map.get(str(category), "Market Risk")

    def _scenario_category(self, code: Any) -> str:
        code_text = str(code or "")
        if "LTV" in code_text or "UNSOLD" in code_text:
            return "Exit Risk"
        if "SHORTFALL" in code_text or "WATERFALL" in code_text:
            return "Financing Risk"
        if "TRIGGER" in code_text:
            return "Sales Risk"
        return "Scenario Risk"

    def _market_mitigant(self, code: Any) -> str:
        if code in {"NO_COMPARABLE_DATA", "MARKET_EVIDENCE_MISSING", "MARKET_EVIDENCE_NEEDS_REVIEW"}:
            return "Attach comparable, presale, rent, or uploaded market evidence before final pricing conclusion."
        if code == "PROPOSED_PRICE_BASIS_MISSING":
            return "Attach or derive proposed price per py from product mix, saleable area, total revenue, or explicit pricing assumption before confirming price adequacy."
        if code in {"PRICE_PREMIUM_HIGH", "PRICE_PREMIUM_EXCESSIVE"}:
            return "Review sale price adjustment, buyer financing, and discount-sale trigger conditions."
        if code in {"EXIT_RATE_HIGH", "ABSORPTION_BELOW_EXIT", "EXIT_PRESSURE_HIGH"}:
            return "Reduce PF exposure, increase equity, or tighten sales trigger and cash sweep."
        return "Add owner, evidence, and mitigation in the credit review memo."

    def _owner_agent(self, source_agent: str) -> str:
        if source_agent in {"RiskAssessmentEngine", "DataQualityAgent"}:
            return "CreditReviewAgent"
        return source_agent

    def _normalize_severity(self, severity: Any) -> str:
        value = str(severity or "medium").lower()
        mapping = {
            "blocking": "critical",
            "critical": "critical",
            "high": "high",
            "caution": "medium",
            "medium": "medium",
            "low": "low",
            "info": "low",
        }
        return mapping.get(value, "medium")

    def _normalize_probability(self, probability: Any) -> str:
        value = str(probability or "medium").lower()
        if value in {"very_high", "high", "medium", "low", "very_low"}:
            return value
        return self._probability_label(probability)

    def _probability_label(self, value: Any) -> str:
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return "medium"
        if numeric >= 5:
            return "very_high"
        if numeric == 4:
            return "high"
        if numeric == 3:
            return "medium"
        if numeric == 2:
            return "low"
        return "very_low"

    def _severity_score(self, severity: str) -> int:
        return {"critical": 5, "high": 4, "medium": 3, "low": 2}.get(severity, 3)

    def _probability_score(self, probability: str) -> int:
        return {"very_high": 5, "high": 4, "medium": 3, "low": 2, "very_low": 1}.get(probability, 3)

    def _grade(self, score: int) -> str:
        if score >= 16:
            return "Critical"
        if score >= 11:
            return "High"
        if score >= 6:
            return "Moderate"
        return "Low"

    def _credit_impact(self, severity: str) -> str:
        if severity == "critical":
            return "must_resolve_or_reapprove"
        if severity == "high":
            return "condition_required"
        if severity == "medium":
            return "monitor_or_disclose"
        return "monitor"

    def _risk_prefix(self, category: str) -> str:
        prefix_map = {
            "Land Risk": "LAND",
            "Permit Risk": "PERMIT",
            "Legal Risk": "LEGAL",
            "Market Risk": "MKT",
            "Sales Risk": "SALES",
            "Exit Risk": "EXIT",
            "Financing Risk": "FIN",
            "Data Quality Risk": "DQ",
            "Construction Cost Risk": "COST",
        }
        return prefix_map.get(category, "GEN")

    def _code(self, value: Any) -> str:
        text = str(value or "RISK").strip().upper()
        cleaned = []
        previous_underscore = False
        for char in text:
            if char.isalnum():
                cleaned.append(char)
                previous_underscore = False
            elif not previous_underscore:
                cleaned.append("_")
                previous_underscore = True
        return "".join(cleaned).strip("_") or "RISK"

