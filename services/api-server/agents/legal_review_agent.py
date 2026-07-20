from typing import Any, Dict, List

from agents.korean_law_context import build_korean_law_mcp_query_plan, normalize_law_context
from agents.legal_developability_agent import LegalDevelopabilityAgent
from core.schedule import schedule_event_map


class LegalReviewAgent:
    def review(
        self,
        project_data: Dict[str, Any],
        law_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_context = normalize_law_context(law_context)
        checks = [
            self._zoning_check(project_data),
            self._permit_check(project_data),
            self._land_rights_check(project_data),
            self._trust_security_check(project_data),
            self._covenant_check(project_data),
            self._law_context_check(normalized_context),
        ]
        developability = LegalDevelopabilityAgent().analyze(project_data, normalized_context)
        checks.append(self._developability_check(developability))
        blocking = [check for check in checks if check["severity"] == "blocking"]
        caution = [check for check in checks if check["severity"] == "caution"]

        return {
            "reviewType": "korean_real_estate_pf_legal_precheck",
            "mcp": {
                "requested": True,
                "connected": normalized_context["connected"],
                "verifiedEvidenceAvailable": normalized_context.get(
                    "verifiedEvidenceAvailable",
                    False,
                ),
                "source": normalized_context.get("source", "not_connected"),
                "adapter": normalized_context.get("version"),
                "note": (
                    "Connectivity only. MCP transport success is never approval "
                    "evidence; verifiedEvidenceAvailable is supplied separately "
                    "by the Legal Intelligence Core."
                ),
            },
            "status": "blocking_issue" if blocking else "caution" if caution else "clear",
            "checks": checks,
            "lawReferences": normalized_context.get("references", []),
            "citationVerification": normalized_context.get("citationVerification"),
            "recommendedMcpQueries": build_korean_law_mcp_query_plan(project_data),
            "requiredFollowUps": self._follow_ups(blocking, caution),
            "developabilityAnalysis": developability,
            "disclaimer": "This is a preliminary PF legal and permit checklist, not a final legal opinion.",
        }

    def _developability_check(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        status = analysis.get("status")
        if status == "blocked":
            severity = "blocking"
        elif status in {"not_assessable", "conditional"}:
            severity = "caution"
        else:
            severity = "clear"
        findings = [*analysis.get("breaches", []), *analysis.get("conditionalFindings", [])]
        if analysis.get("missingRequiredInputs"):
            findings.append("필수 법규·면적 입력 누락: " + ", ".join(analysis["missingRequiredInputs"]))
        return self._check(
            "developability",
            "실제 용도지역·면적·개발가능성",
            severity,
            findings or ["확인된 법정 기준과 계획값 사이에 직접적인 초과가 없습니다."],
            details=analysis,
        )

    def _zoning_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        regulation = data.get("regulation", {})
        legal_far = regulation.get("legal_far", 0)
        applied_far = regulation.get("applied_far", 0)
        legal_bcr = regulation.get("legal_bcr", 0)
        applied_bcr = regulation.get("applied_bcr", 0)
        issues = []
        if legal_far and applied_far and applied_far > legal_far:
            issues.append("Applied FAR exceeds the legal FAR limit.")
        if legal_bcr and applied_bcr and applied_bcr > legal_bcr:
            issues.append("Applied BCR exceeds the legal BCR limit.")
        if not regulation:
            issues.append("Zoning, statutory FAR, and statutory BCR data are missing.")
        return self._check(
            "zoning",
            "Zoning, FAR and BCR",
            "blocking" if any("exceeds" in issue for issue in issues) else "caution" if issues else "clear",
            issues or ["No zoning limit breach was detected from the supplied data."],
        )

    def _permit_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        schedule = schedule_event_map(data.get("schedule"))
        permit = schedule.get("permit_approval")
        construction = schedule.get("construction_start")
        issues = []
        if not permit:
            issues.append("Permit approval or building permit schedule is missing.")
        if construction and not permit:
            issues.append("Construction start is scheduled but permit approval date is missing.")
        return self._check(
            "permit",
            "Permit and construction prerequisites",
            "caution" if issues else "clear",
            issues or ["Permit schedule is reflected in the project schedule."],
        )

    def _land_rights_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        parcels = data.get("land_acquisition", [])
        if isinstance(parcels, dict):
            parcels = parcels.get("parcels") or parcels.get("rows") or []
        if not isinstance(parcels, list):
            parcels = []
        if not parcels:
            land_ledger = data.get("landLedger") if isinstance(data.get("landLedger"), dict) else {}
            parcels = land_ledger.get("parcels") if isinstance(land_ledger.get("parcels"), list) else []
        land_evidence = data.get("landEvidence") or data.get("land_evidence") or {}
        registry_records = self._registry_records(land_evidence)
        issues = []
        if not parcels and not registry_records:
            issues.append("Land ownership, share ratio, area, and encumbrance data are missing.")
        share_values = []
        for parcel in parcels:
            value = parcel.get("share") or parcel.get("ownershipShare") or parcel.get("knownShareSum")
            try:
                if value not in (None, ""):
                    share_values.append(float(value))
            except (TypeError, ValueError):
                continue
        share_sum = sum(share_values)
        if share_values and share_sum < 0.999:
            issues.append(f"Confirmed ownership share totals {share_sum:.2%}, below 100%.")
        mortgages = [p.get("mortgage") for p in parcels if p.get("mortgage")]
        if mortgages:
            issues.append("Existing mortgages or encumbrances require release or trust-transfer conditions.")
        registry_release_required = [
            record
            for record in registry_records
            if record.get("releaseRequired")
            or record.get("mortgageExists")
            or record.get("seizureExists")
            or record.get("provisionalRegistrationExists")
            or record.get("easementExists")
        ]
        owner_unmatched = [
            record
            for record in registry_records
            if record.get("ownerMatchStatus") not in {None, "matched", "not_applicable"}
        ]
        if registry_release_required:
            issues.append("Registry evidence includes encumbrances or release-required items.")
        if owner_unmatched:
            issues.append("Registry owner match status requires legal confirmation.")
        if parcels and not registry_records:
            issues.append("Registry and encumbrance evidence is not attached; legal review remains pending.")
        blocking_issue = bool(registry_release_required or owner_unmatched or (share_values and share_sum < 0.999))
        return self._check(
            "land_rights",
            "Land rights and encumbrances",
            "blocking" if blocking_issue else "caution" if issues else "clear",
            issues or ["Land ownership and rights evidence were reviewed from supplied registry/land data."],
        )

    def _registry_records(self, land_evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(land_evidence, dict):
            return []
        records = land_evidence.get("registryEvidence") or land_evidence.get("registry_evidence")
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]
        if isinstance(records, dict):
            return [records]
        all_records = land_evidence.get("records") or []
        if isinstance(all_records, list):
            return [
                record
                for record in all_records
                if isinstance(record, dict) and record.get("evidenceType") == "registry_record"
            ]
        return []

    def _trust_security_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        claims = data.get("covenants", {}).get("claim_preservation", [])
        has_trust = any(
            "trust" in item.lower()
            or "beneficial interest" in item.lower()
            or "\uc2e0\ud0c1" in item
            or "\uc6b0\uc120\uc218\uc775" in item
            for item in claims
        )
        issues = [] if has_trust else ["Trust, first-priority beneficial interest, or security structure needs confirmation."]
        return self._check(
            "trust_security",
            "Trust and security package",
            "caution" if issues else "clear",
            issues or ["Trust or beneficial-interest security items are reflected."],
        )

    def _covenant_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        covenants = data.get("covenants", {})
        missing = []
        for key, label in [
            ("drawdown_conditions", "drawdown conditions"),
            ("eod", "EOD events"),
            ("execution_order", "fund execution and repayment waterfall"),
        ]:
            if not covenants.get(key):
                missing.append(label)
        return self._check(
            "covenants",
            "Covenants and EOD",
            "caution" if missing else "clear",
            [f"Missing items: {', '.join(missing)}"] if missing else ["Core covenant items are reflected."],
        )

    def _law_context_check(self, law_context: Dict[str, Any]) -> Dict[str, Any]:
        if not law_context.get("verifiedEvidenceAvailable"):
            return self._check(
                "korean_law_mcp",
                "Verified legal-source evidence",
                "caution",
                ["No current, hash-bound official legal evidence was supplied."],
            )

        citation_verification = law_context.get("citationVerification", {})
        failures = citation_verification.get("failures", [])
        if failures:
            return self._check(
                "korean_law_mcp",
                "Verified legal-source evidence",
                "blocking",
                [f"Citation verification failed: {failures}"],
            )

        references = law_context.get("references", [])
        return self._check(
            "korean_law_mcp",
            "Verified legal-source evidence",
            "clear" if references else "caution",
            [f"{len(references)} legal references were connected."] if references else ["law_context was supplied but references is empty."],
        )

    def _check(self, key: str, title: str, severity: str, findings: List[str], details: Dict[str, Any] | None = None) -> Dict[str, Any]:
        result = {
            "key": key,
            "title": title,
            "severity": severity,
            "findings": findings,
        }
        if details is not None:
            result["details"] = details
        return result

    def _follow_ups(self, blocking: List[Dict[str, Any]], caution: List[Dict[str, Any]]) -> List[str]:
        follow_ups = []
        for check in blocking + caution:
            follow_ups.append(f"{check['title']}: {' / '.join(check['findings'])}")
        return follow_ups or ["Attach final legal counsel and permit consultant memo before approval."]
