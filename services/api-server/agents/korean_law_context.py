from datetime import datetime, timezone
from typing import Any, Dict, List


REALYTICS_LAW_CONTEXT_VERSION = "realytics-law-context-v1"

LAW_NAME_KO = "\ubc95\ub839\uba85\ud55c\uae00"
ARTICLE_NO_KO = "\uc870\ubb38\ubc88\ud638"
EFFECTIVE_DATE_KO = "\uc2dc\ud589\uc77c\uc790"


def normalize_law_context(law_context: Dict[str, Any] | None) -> Dict[str, Any]:
    raw = law_context or {}
    if not raw:
        return {
            "version": REALYTICS_LAW_CONTEXT_VERSION,
            "source": "not_connected",
            "connected": False,
            "references": [],
            "citationVerification": {"status": "not_requested", "failures": []},
            "raw": {},
        }

    references = []
    for item in raw.get("references", []):
        references.append(_normalize_reference(item, raw_tool=item.get("rawTool")))

    for item in raw.get("results", []):
        references.append(_normalize_reference(item, raw_tool=raw.get("tool")))

    for item in raw.get("laws", []):
        references.append(_normalize_reference(item, raw_tool="search_law"))

    for item in raw.get("citations", []):
        references.append(_normalize_reference(item, raw_tool="verify_citations"))

    tool_results = raw.get("tool_results") or raw.get("toolResults") or []
    if isinstance(tool_results, dict):
        tool_results = [
            {"tool": tool_name, "result": result}
            for tool_name, result in tool_results.items()
        ]
    for tool_result in tool_results:
        references.extend(_references_from_tool_result(tool_result))

    citation_verification = _normalize_citation_verification(raw)
    if citation_verification["status"] == "not_requested":
        nested_verification = _citation_verification_from_tool_results(tool_results)
        if nested_verification:
            citation_verification = nested_verification

    connected = bool(references or raw.get("connected") or raw.get("source") == "korean-law-mcp")

    return {
        "version": raw.get("version", REALYTICS_LAW_CONTEXT_VERSION),
        "source": raw.get("source", "korean-law-mcp" if connected else "not_connected"),
        "connected": connected,
        "retrievedAt": raw.get("retrievedAt") or datetime.now(timezone.utc).isoformat(),
        "references": references,
        "citationVerification": citation_verification,
        "queryPlan": raw.get("queryPlan", []),
        "raw": raw if raw.get("includeRaw", False) else {},
    }


def build_korean_law_mcp_query_plan(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    project = project_data.get("project", {})
    regulation = project_data.get("regulation", {})
    project_type = str(project.get("type", "")).lower()
    zoning = regulation.get("zoning", "")

    queries = [
        {
            "tool": "search_law",
            "query": "\uad6d\ud1a0\uc758 \uacc4\ud68d \ubc0f \uc774\uc6a9\uc5d0 \uad00\ud55c \ubc95\ub960 \uc6a9\ub3c4\uc9c0\uc5ed \uc6a9\uc801\ub960 \uac74\ud3d0\uc728",
            "purpose": "Confirm zoning, FAR, and BCR statutory basis.",
        },
        {
            "tool": "chain_procedure_detail",
            # Project addresses and parcel identifiers are not needed to
            # retrieve the generally applicable statutory procedure.
            "query": "\uac74\ucd95\ubc95 \uac74\ucd95\ud5c8\uac00 \ucc29\uacf5 \uc0ac\uc6a9\uc2b9\uc778 \uc808\ucc28",
            "purpose": "Confirm building permit, construction start, and use approval procedures.",
        },
        {
            "tool": "search_law",
            "query": "\ubd80\ub3d9\uc0b0\uac1c\ubc1c\uc5c5\uc758 \uad00\ub9ac \ubc0f \uc721\uc131\uc5d0 \uad00\ud55c \ubc95\ub960 \uac1c\ubc1c\uc0ac\uc5c5",
            "purpose": "Confirm developer registration and project-level regulatory basis.",
        },
        {
            "tool": "verify_citations",
            "query": "\uad6d\ud1a0\uc758 \uacc4\ud68d \ubc0f \uc774\uc6a9\uc5d0 \uad00\ud55c \ubc95\ub960, \uac74\ucd95\ubc95, \uc2e0\ud0c1\ubc95, \ubd80\ub3d9\uc0b0\uac1c\ubc1c\uc5c5\uc758 \uad00\ub9ac \ubc0f \uc721\uc131\uc5d0 \uad00\ud55c \ubc95\ub960",
            "purpose": "Verify statute names and citation availability before report issuance.",
        },
    ]

    if zoning:
        queries.append({
            "tool": "chain_law_system",
            "query": f"{zoning} \uac74\ucd95\uc81c\ud55c \uc6a9\uc801\ub960 \uac74\ud3d0\uc728 \uc870\ubb38",
            "purpose": "Confirm the law, enforcement decree, and rule hierarchy for the zoning class.",
        })

    if any(token in project_type for token in ["residential", "mixed", "officetel", "commercial"]):
        queries.extend([
            {
                "tool": "search_law",
                "query": "\uc8fc\ud0dd\ubc95 \uc785\uc8fc\uc790\ubaa8\uc9d1\uacf5\uace0 \ubd84\uc591\uc2b9\uc778",
                "purpose": "Confirm housing-sale approval and public notice requirements.",
            },
            {
                "tool": "search_law",
                "query": "\uac74\ucd95\ubb3c\uc758 \ubd84\uc591\uc5d0 \uad00\ud55c \ubc95\ub960 \ubd84\uc591\uc2e0\uace0 \ubd84\uc591\uad11\uace0",
                "purpose": "Confirm sale notice and advertisement rules for non-housing saleable assets.",
            },
        ])

    queries.append({
        "tool": "search_law",
        "query": "\uc2e0\ud0c1\ubc95 \ubd80\ub3d9\uc0b0\uc2e0\ud0c1 \uc6b0\uc120\uc218\uc775\uad8c \ub2f4\ubcf4\uc2e0\ud0c1",
        "purpose": "Confirm real estate trust, beneficial interest, and security trust basis.",
    })

    queries.append({
        "tool": "law_change_history",
        "query": "\uad00\ub828 \ubc95\ub839 \uac1c\uc815\uc774\ub825 \uc2dc\ud589\uc77c \uc785\ubc95\uc608\uace0 \ubc1c\uc758 \uac1c\uc815\ub0b4\uc6a9",
        "purpose": "Identify promulgation, effective-date, legislative-notice, proposal, and amendment-history evidence.",
    })

    asset_queries = {
        "residential": ["주택법 주택공급에 관한 규칙 건축법 분양 관련 적용 법령"],
        "mixed": ["주택법 건축법 건축물의 분양에 관한 법률 주상복합 적용 법령"],
        "officetel": ["건축법 건축물의 분양에 관한 법률 오피스텔 적용 법령"],
        "commercial": ["건축법 건축물의 분양에 관한 법률 상업시설 적용 법령"],
        "logistics": ["물류시설의 개발 및 운영에 관한 법률 건축법 물류센터 적용 법령"],
        "reit": ["부동산투자회사법 자본시장법 리츠 편입 운용 공시 법령"],
        "fund": ["자본시장과 금융투자업에 관한 법률 부동산펀드 설정 운용 공시 법령"],
    }
    for query in asset_queries.get(project_type, ["건축법 국토계획법 부동산개발사업 적용 법령"]):
        queries.append({
            "tool": "search_law",
            "query": query,
            "purpose": f"Confirm asset-type-specific statutory requirements for {project_type or 'unknown asset type'}.",
        })

    return queries


def _references_from_tool_result(tool_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    tool = tool_result.get("tool") or tool_result.get("name")
    result = tool_result.get("result", tool_result)
    refs = []
    if isinstance(result, dict):
        for key in ["references", "results", "laws", "citations"]:
            values = result.get(key, [])
            if isinstance(values, list):
                refs.extend(_normalize_reference(value, raw_tool=tool or key) for value in values)
        if not refs:
            refs.append(_normalize_reference(result, raw_tool=tool))
    elif isinstance(result, list):
        refs.extend(_normalize_reference(value, raw_tool=tool) for value in result)
    elif result:
        refs.append(_normalize_reference({"summary": str(result)}, raw_tool=tool))
    return refs


def _normalize_reference(item: Dict[str, Any], raw_tool: str | None = None) -> Dict[str, Any]:
    return {
        "type": item.get("type") or item.get("category") or item.get("target") or "law",
        "title": (
            item.get("title")
            or item.get("lawName")
            or item.get(LAW_NAME_KO)
            or item.get("name")
            or item.get("citation")
            or "Untitled legal reference"
        ),
        "citation": item.get("citation") or item.get("article") or item.get(ARTICLE_NO_KO) or "",
        "lawName": item.get("lawName") or item.get(LAW_NAME_KO) or item.get("title") or "",
        "article": item.get("article") or item.get(ARTICLE_NO_KO) or item.get("articleNo") or "",
        "effectiveDate": item.get("effectiveDate") or item.get(EFFECTIVE_DATE_KO) or item.get("efYd") or "",
        "promulgationDate": item.get("promulgationDate") or item.get("promulgation_date") or item.get("promulgationDateKo") or item.get("announceDate") or "",
        "amendmentDate": item.get("amendmentDate") or item.get("amendment_date") or item.get("revisionDate") or "",
        "legislativeNoticeDate": item.get("legislativeNoticeDate") or item.get("legislative_notice_date") or item.get("noticeDate") or "",
        "proposalDate": item.get("proposalDate") or item.get("proposal_date") or item.get("billDate") or "",
        "lawId": item.get("lawId") or item.get("law_id") or item.get("법령ID") or "",
        "changeType": item.get("changeType") or item.get("change_type") or item.get("amendmentType") or "",
        "changeSummary": item.get("changeSummary") or item.get("change_summary") or item.get("revisionSummary") or "",
        "status": item.get("status") or item.get("lawStatus") or "",
        "url": item.get("url") or item.get("link") or "",
        "summary": item.get("summary") or item.get("text") or item.get("content") or item.get("reason") or "",
        "verified": item.get("verified", item.get("exists", None)),
        "rawTool": raw_tool or item.get("rawTool") or item.get("tool") or "unknown",
    }


def _normalize_citation_verification(raw: Dict[str, Any]) -> Dict[str, Any]:
    verification = raw.get("citationVerification") or raw.get("citation_verification") or {}
    failures = verification.get("failures", [])
    if raw.get("failCount"):
        failures = failures or raw.get("failed", [])

    if failures:
        status = "failed"
    elif verification:
        status = verification.get("status", "passed")
    else:
        status = "not_requested"

    return {
        "status": status,
        "failures": failures,
        "checkedCount": verification.get("checkedCount", raw.get("checkedCount", 0)),
    }


def _citation_verification_from_tool_results(tool_results: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for tool_result in tool_results:
        result = tool_result.get("result", tool_result)
        if isinstance(result, dict):
            verification = _normalize_citation_verification(result)
            if verification["status"] != "not_requested":
                return verification
    return None
