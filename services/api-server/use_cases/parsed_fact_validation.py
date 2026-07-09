from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from storage import list_deal_state_records, upsert_deal_state_record
from use_cases.parsed_facts import parsed_fact_package


PARSED_FACT_POD_VALIDATION_SCHEMA_VERSION = "realytics-parsed-fact-pod-validation-v1"


POD_FACT_REQUIREMENTS: Dict[str, List[Dict[str, Any]]] = {
    "land_legal": [
        {
            "canonicalField": "land.area_sqm",
            "label": "토지면적",
            "severity": "high",
            "message": "Land-Legal Pod requires parsed land area before legal/PF readiness can be trusted.",
        },
        {
            "canonicalField": "land.pnu",
            "label": "PNU",
            "severity": "critical",
            "message": "Land-Legal Pod requires PNU candidate or resolved PNU evidence.",
        },
        {
            "canonicalField": "landEvidence.officialLandPrice.pricePerSqm",
            "label": "공시지가",
            "severity": "high",
            "message": "Land-Legal Pod requires official land price candidate with source/base date.",
        },
        {
            "canonicalField": "registry",
            "label": "등기/제한물권 증빙",
            "severity": "high",
            "prefix": True,
            "message": "Land-Legal Pod requires registry or encumbrance evidence candidate.",
        },
    ],
    "market_product": [
        {
            "canonicalField": "market.proposedPricePerPy",
            "label": "제안 분양가",
            "severity": "high",
            "message": "Market-Product Pod requires proposed price basis before pricing adequacy can be judged.",
            "missingCode": "PROPOSED_PRICE_BASIS_MISSING",
        },
        {
            "canonicalField": "marketComparable.pricePerPy",
            "label": "비교사례 가격",
            "severity": "high",
            "message": "Market-Product Pod requires transaction, presale, listing, or manual comparable price evidence.",
        },
        {
            "canonicalField": "market.rentalAssumptions.monthlyRentPerPy",
            "label": "임대료 가정",
            "severity": "medium",
            "message": "Market-Product Pod should verify rent assumptions for yield/cap-rate based products.",
        },
        {
            "canonicalField": "market.capRate",
            "label": "Cap Rate",
            "severity": "medium",
            "message": "Market-Product Pod should verify cap-rate evidence for income assets.",
        },
    ],
    "feasibility_cashflow": [
        {
            "canonicalField": "feasibility_input.total_revenue",
            "label": "총분양수입",
            "severity": "critical",
            "message": "Feasibility-Cashflow Pod requires total revenue candidate for proforma and cash flow.",
        },
        {
            "canonicalField": "feasibility_input.total_cost",
            "label": "총사업비",
            "severity": "critical",
            "message": "Feasibility-Cashflow Pod requires total project cost candidate.",
        },
        {
            "canonicalField": "feasibility_input.construction_cost",
            "label": "공사비",
            "severity": "high",
            "message": "Feasibility-Cashflow Pod requires construction cost candidate.",
        },
        {
            "canonicalField": "pf_structure.amount.sum",
            "label": "PF 금액",
            "severity": "high",
            "message": "Feasibility-Cashflow Pod requires PF amount candidate to reconcile sources and uses.",
        },
    ],
    "pf_credit": [
        {
            "canonicalField": "pf_structure.amount.sum",
            "label": "PF 금액",
            "severity": "critical",
            "message": "PF-Credit Pod requires PF amount candidate before tranche recovery review.",
        },
        {
            "canonicalField": "pf.ltv",
            "label": "LTV",
            "severity": "high",
            "prefix": True,
            "message": "PF-Credit Pod requires LTV/LTC/DSCR candidates or calculated metrics.",
        },
        {
            "canonicalField": "pf.exitSaleRate",
            "label": "Exit 분양률",
            "severity": "high",
            "prefix": True,
            "message": "PF-Credit Pod requires Exit sale-rate candidate or calculation run output.",
        },
        {
            "canonicalField": "waterfall",
            "label": "Waterfall",
            "severity": "high",
            "prefix": True,
            "message": "PF-Credit Pod requires Waterfall / tranche recovery evidence.",
        },
    ],
}


def validate_parsed_facts_for_pod(
    project_id: str,
    pod_key: str,
    *,
    source_artifact_key: str | None = None,
    actor: str = "PodFactValidator",
) -> Dict[str, Any]:
    """Validate parser candidates for one Chief Pod and materialize findings.

    This is deterministic validation. AI reviewers can comment on findings, but
    the Pod fact gate itself is a source/evidence consistency check.
    """
    pod_key = str(pod_key or "").strip()
    if not pod_key:
        raise ValueError("pod_key is required")

    now = datetime.now(timezone.utc).isoformat()
    all_facts = parsed_fact_package(project_id).get("records") or []
    pod_facts = [fact for fact in all_facts if _targets_pod(fact, pod_key)]
    findings = _build_pod_findings(project_id, pod_key, pod_facts, source_artifact_key=source_artifact_key)
    findings_by_record: Dict[str, List[Dict[str, Any]]] = {}
    for finding in findings:
        for record_id in finding.get("recordIds") or []:
            findings_by_record.setdefault(str(record_id), []).append(finding)

    updated_facts = []
    for fact in pod_facts:
        fact_findings = findings_by_record.get(str(fact.get("recordId")), [])
        updated_facts.append(
            upsert_deal_state_record(
                project_id,
                _with_pod_validation_status(
                    fact,
                    pod_key=pod_key,
                    findings=fact_findings,
                    source_artifact_key=source_artifact_key,
                    actor=actor,
                    now=now,
                ),
            )
        )

    risk_issues = [
        upsert_deal_state_record(project_id, _risk_issue_from_finding(project_id, finding, now=now))
        for finding in findings
        if finding.get("riskCode")
    ]

    summary = _summary(pod_key, pod_facts, updated_facts, findings, risk_issues)
    validation_record = upsert_deal_state_record(
        project_id,
        {
            "schemaVersion": PARSED_FACT_POD_VALIDATION_SCHEMA_VERSION,
            "recordType": "parsed_fact_validation",
            "recordId": f"parsed_fact_validation:{pod_key}:{_safe_key(source_artifact_key or now)}",
            "key": f"parsed_fact_validation.{pod_key}",
            "podKey": pod_key,
            "sourceArtifactKey": source_artifact_key,
            "validatedBy": actor,
            "validatedAt": now,
            "sourceType": "parsed_fact_validation",
            "sourceLocator": {
                "type": "deal_state_records",
                "recordType": "parsed_fact",
                "podKey": pod_key,
                "sourceArtifactKey": source_artifact_key,
            },
            "summary": summary,
            "findings": findings,
        },
    )
    return {
        "schemaVersion": PARSED_FACT_POD_VALIDATION_SCHEMA_VERSION,
        "projectId": project_id,
        "podKey": pod_key,
        "sourceArtifactKey": source_artifact_key,
        "validatedAt": now,
        "summary": summary,
        "findings": findings,
        "updatedFactCount": len(updated_facts),
        "riskIssueCount": len(risk_issues),
        "validationRecord": validation_record,
    }


def validate_parsed_facts_for_pods(
    project_id: str,
    pod_keys: Iterable[str],
    *,
    source_artifact_keys: Dict[str, str] | None = None,
    actor: str = "PodFactValidator",
) -> Dict[str, Any]:
    results = []
    for pod_key in pod_keys:
        key = str(pod_key or "").strip()
        if not key:
            continue
        results.append(
            validate_parsed_facts_for_pod(
                project_id,
                key,
                source_artifact_key=(source_artifact_keys or {}).get(key),
                actor=actor,
            )
        )
    return {
        "schemaVersion": "realytics-parsed-fact-pod-validation-batch-v1",
        "projectId": project_id,
        "count": len(results),
        "results": results,
        "summary": {
            "findingCount": sum(len(item.get("findings") or []) for item in results),
            "riskIssueCount": sum(int((item.get("summary") or {}).get("riskIssueCount") or 0) for item in results),
            "needsReviewCount": sum(int((item.get("summary") or {}).get("needsReviewCount") or 0) for item in results),
        },
    }


def _build_pod_findings(
    project_id: str,
    pod_key: str,
    pod_facts: List[Dict[str, Any]],
    *,
    source_artifact_key: str | None,
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    facts_by_key: Dict[str, List[Dict[str, Any]]] = {}
    for fact in pod_facts:
        key = str(fact.get("canonicalField") or fact.get("factKey") or fact.get("key") or "")
        if key:
            facts_by_key.setdefault(key, []).append(fact)

        if fact.get("critical") and not _has_specific_locator(fact):
            findings.append(
                _finding(
                    project_id,
                    pod_key,
                    "PARSED_FACT_SOURCE_LOCATOR_WEAK",
                    "high",
                    f"{key or fact.get('parserPath')} 후보값에 문서 페이지, 엑셀 셀, API row 같은 명확한 근거 위치가 부족합니다.",
                    [fact],
                    source_artifact_key=source_artifact_key,
                )
            )

        if not fact.get("termId") or _is_unmapped_surface(fact):
            findings.append(
                _finding(
                    project_id,
                    pod_key,
                    "PARSED_FACT_TERM_MAPPING_NEEDED",
                    "medium",
                    f"{key or fact.get('parserPath')} 후보값이 표준 용어 DB에 충분히 매핑되지 않았습니다.",
                    [fact],
                    source_artifact_key=source_artifact_key,
                )
            )

    for key, facts in facts_by_key.items():
        numeric = [(fact, _numeric_value(fact.get("normalizedValue", fact.get("value")))) for fact in facts]
        numeric = [(fact, value) for fact, value in numeric if value is not None]
        if len(numeric) < 2:
            continue
        values = [value for _, value in numeric]
        max_value = max(values)
        min_value = min(values)
        base = max(abs(min_value), 1.0)
        if (max_value - min_value) / base > 0.01:
            findings.append(
                _finding(
                    project_id,
                    pod_key,
                    "PARSED_FACT_VALUE_CONFLICT",
                    "high",
                    f"{key} 후보값이 문서/엑셀/API 근거 간 1% 이상 차이납니다.",
                    [fact for fact, _ in numeric],
                    source_artifact_key=source_artifact_key,
                    extra={"canonicalField": key, "minValue": min_value, "maxValue": max_value},
                )
            )

    for requirement in POD_FACT_REQUIREMENTS.get(pod_key, []):
        if not _has_required_fact(pod_facts, requirement):
            code = str(requirement.get("missingCode") or "PARSED_FACT_REQUIRED_MISSING")
            findings.append(
                _finding(
                    project_id,
                    pod_key,
                    code,
                    str(requirement.get("severity") or "medium"),
                    str(requirement.get("message") or f"{requirement.get('canonicalField')} parsed fact is missing."),
                    [],
                    source_artifact_key=source_artifact_key,
                    extra={
                        "canonicalField": requirement.get("canonicalField"),
                        "label": requirement.get("label"),
                    },
                )
            )

    return findings


def _with_pod_validation_status(
    fact: Dict[str, Any],
    *,
    pod_key: str,
    findings: List[Dict[str, Any]],
    source_artifact_key: str | None,
    actor: str,
    now: str,
) -> Dict[str, Any]:
    status = "validated" if not findings else "needs_review"
    if any(f.get("riskCode") == "PARSED_FACT_SOURCE_LOCATOR_WEAK" for f in findings):
        status = "needs_source_locator"
    if any(f.get("riskCode") == "PARSED_FACT_VALUE_CONFLICT" for f in findings):
        status = "needs_review"

    pod_status = dict(fact.get("podValidationStatusByPod") or {})
    pod_status[pod_key] = {
        "status": status,
        "findingCount": len(findings),
        "findingCodes": sorted({str(f.get("riskCode")) for f in findings if f.get("riskCode")}),
        "sourceArtifactKey": source_artifact_key,
        "validatedBy": actor,
        "validatedAt": now,
    }
    target_pods = [str(p) for p in fact.get("targetPods") or []]
    pod_statuses = [pod_status.get(p, {}).get("status") for p in target_pods if p in pod_status]
    if any(s == "needs_source_locator" for s in pod_statuses):
        validation_status = "needs_source_locator"
    elif any(s == "needs_review" for s in pod_statuses):
        validation_status = "needs_review"
    elif target_pods and len(pod_statuses) == len(target_pods) and all(s == "validated" for s in pod_statuses):
        validation_status = "pod_validated"
    else:
        validation_status = fact.get("validationStatus") or "parsed"

    return {
        **fact,
        "podValidationStatusByPod": pod_status,
        "validationStatus": validation_status,
        "lastPodValidatedAt": now,
        "lastPodValidatedBy": actor,
    }


def _risk_issue_from_finding(project_id: str, finding: Dict[str, Any], *, now: str) -> Dict[str, Any]:
    code = str(finding.get("riskCode") or "PARSED_FACT_VALIDATION_ISSUE")
    pod_key = str(finding.get("podKey") or "unknown")
    canonical = str((finding.get("details") or {}).get("canonicalField") or "general")
    record_ids = [str(record_id) for record_id in finding.get("recordIds") or []]
    suffix = _safe_key(":".join([pod_key, canonical, ",".join(record_ids) or code]))
    return {
        "schemaVersion": "realytics-risk-issue-v1",
        "projectId": project_id,
        "recordType": "risk_issue",
        "recordId": f"risk_issue:{code}:{suffix}",
        "key": f"parsed_fact_validation.{pod_key}.{code}.{canonical}",
        "riskCode": code,
        "category": "data_quality",
        "severity": finding.get("severity") or "medium",
        "message": finding.get("message"),
        "sourceType": "parsed_fact_validation",
        "sourceLocator": finding.get("sourceLocator") or {},
        "sourceRecordIds": record_ids,
        "podKey": pod_key,
        "blocking": (finding.get("severity") in {"critical", "high"}),
        "reviewStatus": "unreviewed",
        "createdAt": now,
    }


def _finding(
    project_id: str,
    pod_key: str,
    risk_code: str,
    severity: str,
    message: str,
    facts: List[Dict[str, Any]],
    *,
    source_artifact_key: str | None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    record_ids = [str(fact.get("recordId")) for fact in facts if fact.get("recordId")]
    locators = [fact.get("sourceLocator") for fact in facts if isinstance(fact.get("sourceLocator"), dict)]
    source_locator = {
        "type": "parsed_fact_validation",
        "projectId": project_id,
        "podKey": pod_key,
        "sourceArtifactKey": source_artifact_key,
        "recordIds": record_ids,
        "locators": locators[:6],
    }
    if extra:
        source_locator.update({k: v for k, v in extra.items() if k in {"canonicalField"}})
    return {
        "schemaVersion": PARSED_FACT_POD_VALIDATION_SCHEMA_VERSION,
        "podKey": pod_key,
        "riskCode": risk_code,
        "severity": severity,
        "message": message,
        "recordIds": record_ids,
        "sourceArtifactKey": source_artifact_key,
        "sourceLocator": source_locator,
        "details": extra or {},
    }


def _summary(
    pod_key: str,
    pod_facts: List[Dict[str, Any]],
    updated_facts: List[Dict[str, Any]],
    findings: List[Dict[str, Any]],
    risk_issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    codes: Dict[str, int] = {}
    for finding in findings:
        code = str(finding.get("riskCode") or "UNKNOWN")
        codes[code] = codes.get(code, 0) + 1
    return {
        "podKey": pod_key,
        "candidateFactCount": len(pod_facts),
        "updatedFactCount": len(updated_facts),
        "findingCount": len(findings),
        "riskIssueCount": len(risk_issues),
        "needsReviewCount": sum(1 for fact in updated_facts if fact.get("validationStatus") == "needs_review"),
        "needsSourceLocatorCount": sum(1 for fact in updated_facts if fact.get("validationStatus") == "needs_source_locator"),
        "validatedCount": sum(1 for fact in updated_facts if fact.get("validationStatus") in {"parsed", "pod_validated"}),
        "findingCodes": codes,
    }


def _targets_pod(fact: Dict[str, Any], pod_key: str) -> bool:
    return pod_key in {str(p) for p in fact.get("targetPods") or []}


def _has_required_fact(facts: List[Dict[str, Any]], requirement: Dict[str, Any]) -> bool:
    wanted = str(requirement.get("canonicalField") or "")
    prefix = bool(requirement.get("prefix"))
    for fact in facts:
        key = str(fact.get("canonicalField") or fact.get("factKey") or fact.get("key") or "")
        if (prefix and key.startswith(wanted)) or key == wanted:
            return True
    return False


def _has_specific_locator(fact: Dict[str, Any]) -> bool:
    locator = fact.get("sourceLocator")
    if not isinstance(locator, dict):
        return False
    if str(fact.get("locatorStatus") or "").lower() == "located":
        return True
    return any(
        locator.get(key) not in (None, "", [], {}, 0)
        for key in (
            "cell",
            "sheet",
            "row",
            "rowIndex",
            "page",
            "pageNumber",
            "apiRowId",
            "evidenceId",
            "sourceUrlOrEvidenceId",
            "artifactKey",
        )
    )


def _is_unmapped_surface(fact: Dict[str, Any]) -> bool:
    surface = fact.get("termSurface")
    if not isinstance(surface, dict):
        return False
    return str(surface.get("matchType") or "").startswith("unmapped")


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("원", "").replace("㎡", "").replace("평", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _safe_key(value: Any) -> str:
    raw = str(value or "none")
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "-", raw).strip("-")[:160] or "none"
