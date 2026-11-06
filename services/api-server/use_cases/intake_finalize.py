from __future__ import annotations

"""Smart-intake finalize pipeline.

After SmartIntake classifies + parses the uploaded documents, this chains the
remaining steps so the workspace is populated end-to-end (the user's "분류대로
처리 → 즉시 작업" expectation):

  1. Identity: keep the parsed real project name + land.address.
  2. Address -> PNU: geocode the project address (VWorld) and resolve the
     cadastral parcel, saving PNU / 공시지가(jiga) / center into the project.
  3. (caller) optionally run the track's analysis (PF review orchestration).

Non-fabricating: PNU/공시지가 come only from VWorld; if the address does not
resolve, nothing is invented — the project keeps whatever was parsed.
"""

from typing import Any, Dict, List, Optional

from integrations.vworld import fetch_parcel_by_point, geocode_address, vworld_configured
from use_cases.track_workflows import get_track_workflow_definition, normalize_workflow_track


def _address_candidates(address: str) -> List[str]:
    raw = address.strip()
    candidates = [raw]
    cleaned = raw.replace("일원", "").strip()
    if cleaned and cleaned != raw:
        candidates.append(cleaned)
    tokens = cleaned.split()
    dong_idx = next((i for i, t in enumerate(tokens) if t.endswith(("동", "리", "가"))), -1)
    if dong_idx >= 0:
        candidates.append(" ".join(tokens[: dong_idx + 1]))
    # No-space cover addresses (e.g. "경기도용인시기흥구상갈동13번지일대") don't geocode as-is.
    # Normalize them to a spaced, geocodable form via the address-mining regex — this
    # is what unblocks land evidence auto-supplement when the parser stored a
    # no-space address (the leading cause of Land-Legal Pod staying blocked).
    try:
        from use_cases.address_mining import mine_addresses_from_text

        for hit in mine_addresses_from_text(raw):
            normalized = (hit.get("address") or "").strip()
            if normalized:
                candidates.append(normalized)  # e.g. "경기도 용인시 기흥구 상갈동 13"
                parts = normalized.split()
                if len(parts) > 1:
                    candidates.append(" ".join(parts[:-1]))  # dong-level fallback
    except Exception:  # mining is best-effort; never break resolution
        pass
    return list(dict.fromkeys([c for c in candidates if c]))


def resolve_address_to_parcel(
    address: str, *, environ: Optional[Dict[str, str]] = None,
    timeout: float = 8.0, max_address_candidates: int | None = None,
) -> Dict[str, Any]:
    """Geocode an address and resolve its cadastral parcel (PNU + 공시지가 + center).

    Returns {status, pnu, center, officialLandPricePerSqm, officialLandPriceBaseYear,
    lotNumber, landCategory, addressFragment}. status is 'resolved' only when a PNU
    is found; otherwise a diagnostic status (skipped/geocode_failed/parcel_failed).
    """
    if not address or not address.strip():
        return {"status": "no_address"}
    if not vworld_configured(environ):
        return {"status": "vworld_not_configured"}

    point = None
    geo_status = "geocode_failed"
    candidates = _address_candidates(address)
    for cand in candidates[:max_address_candidates]:
        point, gdiag = geocode_address(cand, environ=environ, **({} if timeout == 8.0 else {"timeout": timeout}))
        geo_status = gdiag.get("status", "error")
        if point:
            break
    if not point:
        return {"status": geo_status or "geocode_failed"}

    geometry, pdiag = fetch_parcel_by_point(point["lng"], point["lat"], environ=environ, **({} if timeout == 8.0 else {"timeout": timeout}))
    props = pdiag.get("properties") or {} if pdiag.get("status") == "resolved" else {}
    pnu = (props.get("pnu") or "").strip() or None

    jiga = props.get("jiga")
    try:
        jiga_val = float(str(jiga).replace(",", "")) if jiga not in (None, "") else None
    except (TypeError, ValueError):
        jiga_val = None

    return {
        "status": "resolved" if pnu else "parcel_unresolved",
        "pnu": pnu,
        "center": {"lng": point["lng"], "lat": point["lat"]},
        "officialLandPricePerSqm": jiga_val,
        "officialLandPriceBaseYear": props.get("gosi_year"),
        "officialLandPriceBaseDate": props.get("gosi_date") or props.get("pblntfDe"),
        "lotNumber": props.get("jibun"),
        "landCategory": props.get("jimok") or props.get("dgm_nm"),
        "areaSqm": _to_float(props.get("parea") or props.get("area") or props.get("lndpcl_ar")),
        "cadastralAddress": props.get("addr"),
        "addressFragment": props.get("addr") or props.get("jibun") or address,
        "geometry": geometry,
    }


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def finalize_project_intake_use_case(
    project_repository,
    project_id: str,
    *,
    track: str = "development",
    environ: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Promote parsed identity and resolve+save the project's PNU from its address.

    Returns a summary {projectId, address, parcel, persisted}. Does NOT run the
    orchestration — the caller decides whether to trigger the track analysis.
    """
    project = project_repository.get(project_id)
    if not project:
        raise LookupError("Project not found")

    land = dict(project.get("land") or {})
    address = (land.get("address") or "").strip()

    # Fall back to a parcel addressFragment if no top-level address was parsed.
    if not address:
        for parcel in project.get("land_acquisition") or []:
            frag = (parcel.get("addressFragment") or parcel.get("address") or "").strip()
            if frag:
                address = frag
                break

    # Last resort: deterministic regex mining over the archived document text
    # (catches no-space cover addresses the LLM parse misses).
    address_provenance: Dict[str, Any] | None = None
    if not address:
        from use_cases.address_mining import mine_project_address

        mined = mine_project_address(project_id, project)
        if mined:
            address = mined["address"]
            address_provenance = mined.get("provenance")

    parcel = resolve_address_to_parcel(address, environ=environ)
    persisted = False
    if parcel.get("status") == "resolved":
        land["address"] = land.get("address") or address
        land["pnu"] = parcel["pnu"]
        if parcel.get("center"):
            land["center"] = parcel["center"]
        if parcel.get("officialLandPricePerSqm"):
            land["officialLandPricePerSqm"] = parcel["officialLandPricePerSqm"]
            land["officialLandPriceBaseYear"] = parcel.get("officialLandPriceBaseYear")
        project_repository.update(project_id, {"land": land})
        persisted = True

    updated_project = project_repository.get(project_id) or project
    result: Dict[str, Any] = {
        "projectId": project_id,
        "address": address or None,
        "parcel": {k: v for k, v in parcel.items() if k != "geometry"},
        "persisted": persisted,
        "intakeReadinessPlan": build_intake_readiness_plan(updated_project, track=track),
    }
    if address_provenance:
        result["addressProvenance"] = address_provenance
    return result


def build_intake_readiness_plan(project: Dict[str, Any], *, track: str = "development") -> Dict[str, Any]:
    """Create a step-by-step intake plan before any PF review run.

    This is intentionally not an approval or analysis result. It tells the OS
    which material is already present, which agents can run from it, and which
    missing evidence should be supplemented from uploaded files, internal DB,
    historical cases, public APIs, licensed data, or analyst-approved web
    research.
    """
    documents = list(project.get("source_documents") or [])
    doc_types = {str(doc.get("documentType") or doc.get("document_type") or "") for doc in documents}
    land = project.get("land") or {}
    feasibility = project.get("feasibility_input") or project.get("feasibilityStudy") or {}
    pf_structure = project.get("pf_structure") or project.get("pfStructure") or {}
    market = project.get("market") or project.get("marketEvidenceReview") or {}

    has_business_plan = bool({"business_plan", "basic_architectural_plan"} & doc_types or project.get("project", {}).get("name"))
    has_land_ledger = bool("land_ledger" in doc_types or project.get("land_acquisition") or land.get("pnu"))
    has_feasibility = bool("feasibility_workbook" in doc_types or feasibility)
    has_market = bool("market_evidence" in doc_types or market.get("transactionComparables") or market.get("manualComparables"))
    has_pf_terms = bool("risk_report_workbook" in doc_types or pf_structure)
    has_land_evidence = bool(project.get("landEvidence") or project.get("land_evidence") or land.get("officialLandPricePerSqm"))
    normalized_track = normalize_workflow_track(track)
    track_workflow = get_track_workflow_definition(normalized_track)

    stages = [
        _stage(
            "source_intake",
            "자료 접수 및 자동분류",
            "ready" if documents else "needs_user_material",
            [],
            ["ParserAgent", "DocumentClassifierAgent", "IntakeTriageAgent"],
        ),
        _stage(
            "project_identity",
            "사업개요·위치·상품유형 정규화",
            "ready" if has_business_plan else "needs_supplement",
            [] if has_business_plan else ["사업계획서, 기본계획서, IM/IR 중 1개 이상"],
            ["ParserAgent", "DevelopmentFeasibilityAgent"],
        ),
        _stage(
            "land_readiness",
            "토지·PNU·공시지가·등기 증빙",
            "ready" if has_land_ledger and has_land_evidence else "needs_enrichment",
            _missing(
                (has_land_ledger, "토지조서 또는 토지대장"),
                (bool(land.get("pnu")), "PNU 또는 주소 기반 PNU 보강"),
                (has_land_evidence, "공시지가·지적특성·등기/제한물권 증빙"),
            ),
            ["LandAgent", "PNUResolverAgent", "OfficialLandPriceAgent", "RegistryEvidenceAgent"],
        ),
        _stage(
            "marketability_readiness",
            "시장·분양성·가격 근거",
            "ready" if has_market else "needs_db_or_external_data",
            [] if has_market else ["실거래가, 분양사례, 임대료, 미분양/청약 통계 또는 수동 비교사례"],
            ["MarketEvidenceAgent", "MarketabilityAnalysisAgent"],
        ),
        _stage(
            "feasibility_readiness",
            "사업수지·현금흐름 계산 준비",
            "ready" if has_feasibility else "needs_supplement",
            [] if has_feasibility else ["사업수지 Excel, 상품구성표, 공사비/토지비/금융비용 가정"],
            ["DevelopmentFeasibilityAgent", "CashFlowEngine"],
        ),
        _stage(
            "pf_review_readiness",
            "PF 구조·상환재원·리스크 심사 준비",
            "ready" if has_pf_terms and has_feasibility and has_land_evidence else "blocked_until_prior_steps",
            _missing(
                (has_pf_terms, "PF term sheet 또는 대출구조"),
                (has_feasibility, "사업수지 및 월별 현금흐름"),
                (has_land_evidence, "토지 법무·증빙 패키지"),
            ),
            ["PFStructuringAgent", "RiskRegisterAgent", "CreditReviewAgent"],
        ),
    ]

    return {
        "schemaVersion": "realytics-intake-readiness-plan-v1",
        "mode": "step_by_step_before_pf_review",
        "track": normalized_track,
        "trackLabel": track_workflow["label"],
        "trackWorkflow": track_workflow,
        "summary": {
            "readyStageCount": sum(1 for stage in stages if stage["status"] == "ready"),
            "blockedStageCount": sum(1 for stage in stages if stage["status"].startswith("blocked")),
            "needsSupplementCount": sum(1 for stage in stages if stage["status"] not in {"ready"}),
            "trackStepCount": len(track_workflow.get("steps") or []),
        },
        "stages": stages,
        "dataSourcingPolicy": [
            {
                "priority": 1,
                "source": "사용자 업로드 자료",
                "rule": "원본 파일과 파싱값을 보존하고, 필드별 출처를 붙입니다.",
            },
            {
                "priority": 2,
                "source": "프로젝트 DB 및 과거 심사 스냅샷",
                "rule": "동일 위치·상품·리스크 코드의 유사 사례를 찾아 보수적 benchmark로 사용합니다.",
            },
            {
                "priority": 3,
                "source": "공개 API/공공데이터 connector",
                "rule": "PNU, 공시지가, 지적특성, 실거래가 등 source metadata가 남는 데이터만 자동 보강합니다.",
            },
            {
                "priority": 4,
                "source": "제휴/라이선스 데이터",
                "rule": "밸류맵·민간 부동산 데이터 등은 사용권과 기준일을 evidence metadata에 저장합니다.",
            },
            {
                "priority": 5,
                "source": "웹 리서치 또는 LLM 추정",
                "rule": "확정값으로 저장하지 않고 '추가 검증 필요' 상태로만 사용합니다.",
            },
        ],
        "nextRecommendedAction": _next_action(stages, track_workflow),
    }


def _stage(
    key: str,
    title: str,
    status: str,
    missing_materials: List[str],
    agents: List[str],
) -> Dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "status": status,
        "missingMaterials": missing_materials,
        "agents": agents,
    }


def _missing(*checks: tuple[bool, str]) -> List[str]:
    return [label for ok, label in checks if not ok]


def _next_action(stages: List[Dict[str, Any]], track_workflow: Dict[str, Any] | None = None) -> str:
    for stage in stages:
        if stage["status"] != "ready":
            missing = ", ".join(stage["missingMaterials"][:2])
            return f"{stage['title']} 단계 보강 필요" + (f": {missing}" if missing else "")
    steps = (track_workflow or {}).get("steps") or []
    if steps:
        first = steps[0]
        return f"공통 입력 게이트가 준비되었습니다. 선택 트랙의 1단계 '{first['title']}'부터 실행하세요."
    return "모든 입력 단계가 준비되었습니다. 사용자가 명시적으로 다음 분석을 실행할 수 있습니다."
