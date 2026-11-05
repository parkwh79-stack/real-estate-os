type Data = Record<string, unknown>;
const record = (value: unknown): Data => value && typeof value === "object" && !Array.isArray(value) ? value as Data : {};

/** A server-produced archived draft may be sent for candidate validation only;
 * this does not approve its formulas or promote values into calculation inputs. */
export function hasArchivedFeasibilityReviewDraft(body: Data): boolean {
  const draft = record(body.feasibilityStudyV2Draft);
  const upload = record(body.upload);
  const validation = record(body.validation);
  const archiveId = upload.archiveId;
  return draft.version === "realytics-feasibility-study-v2"
    && typeof draft.project_id === "string" && draft.project_id.length > 0
    && body.project_id === draft.project_id
    && upload.documentType === "feasibility_workbook" && upload.archiveStored === true
    && typeof archiveId === "string" && /^[a-f0-9]{64}$/i.test(archiveId)
    && upload.originalFileHash === archiveId && validation.sourceDocumentHash === archiveId
    && typeof upload.feasibilityStudyV2DraftHash === "string" && /^[a-f0-9]{64}$/i.test(upload.feasibilityStudyV2DraftHash)
    && ((Array.isArray(draft.monthly_cash_flow) && draft.monthly_cash_flow.length > 0)
      || Object.keys(record(draft.metrics)).length > 0);
}

/** A readable, unsupported workbook can request only exact original-value
 * candidates from its verified archive. This is not a financial import. */
export function hasArchivedWorkbookSourceReview(body: Data): boolean {
  const upload = record(body.upload);
  const validation = record(body.validation);
  const review = record(body.sourceReview);
  const hash = upload.archiveId;
  return typeof body.project_id === "string" && body.project_id.length > 0
    && body.feasibilityStudyV2Draft == null && upload.documentType === "feasibility_workbook"
    && upload.archiveStored === true && typeof hash === "string" && /^[a-f0-9]{64}$/i.test(hash)
    && upload.originalFileHash === hash && validation.sourceDocumentHash === hash
    && validation.status === "needs_mapping" && record(validation.templateSupport).status === "unsupported_layout"
    && review.reason === "unsupported_workbook_layout" && review.candidateOnly === true && review.authoritative === false
    && review.archiveId === hash && review.sourceDocumentHash === hash && review.projectId === body.project_id
    && review.profileId === "realytics.source_review_candidates" && review.profileVersion === "1.0.0";
}

/** HTTP 200 acknowledges transport, not successful extraction. */
export function intakeProcessingFailure(body: Data, documentType?: string): string | null {
  const project = record(body.project);
  const reanalysis = record(body.autoReanalysis ?? project.autoReanalysis);
  if (reanalysis.triggered === true && (reanalysis.status === "failed" || reanalysis.cascadeComplete === false)) {
    return "자료는 저장됐지만 후속 재분석이 완료되지 않았습니다. 딜룸에서 실패·검토 대기 단계를 확인하고 재분석하세요.";
  }
  if (documentType === "land_ledger") {
    const ledger = record(body.landLedger);
    if (record(ledger.summary).parcelCount === 0 || (Array.isArray(ledger.parcels) && ledger.parcels.length === 0)) {
      return "토지조서의 지번·면적 행을 찾지 못했습니다. 원본 유형과 열 매핑을 확인해 주세요.";
    }
  }
  const validationStatus = record(body.validation).status;
  if (validationStatus === "needs_mapping" && !hasArchivedWorkbookSourceReview(body)) {
    return "원본과 항목 매핑 상태를 확인하지 못했습니다. 문서 검토에서 저장 원본과 처리 결과를 확인해 주세요.";
  }
  const reviewableValidationFailure = validationStatus === "failed" && hasArchivedFeasibilityReviewDraft(body);
  const statuses = [body.status, ...(reviewableValidationFailure ? [] : [validationStatus]),
    record(body._meta).parsing_source, record(project._meta).parsing_source,
    record(record(body.upload)._meta).parsing_source];
  if (statuses.includes("host_llm_required")) {
    return "원본은 접수됐지만 LLM 추출이 필요합니다. AI 연결을 확인한 뒤 다시 처리하세요.";
  }
  if (statuses.some(status => ["parse_failed", "extraction_unavailable", "failed", "error"].includes(String(status)))) {
    if (documentType === "feasibility_workbook" || documentType === "risk_report_workbook") {
      return "워크북 검증에 실패했습니다. Excel에서 파일이 열리는지, 선택한 유형의 시트와 수식이 있는지 확인해 주세요.";
    }
    if (documentType === "land_ledger") {
      return "토지조서 파싱에 실패했습니다. Excel의 지번·면적 열과 제목 행을 확인해 주세요.";
    }
    if (documentType === "business_plan" || documentType === "basic_architectural_plan") {
      return "문서 추출에 실패했습니다. PDF에서 텍스트를 선택할 수 있는지 확인해 주세요.";
    }
    return "원본 접수와 별개로 파싱 또는 검증에 실패했습니다. 실패 단계를 확인한 뒤 다시 처리하세요.";
  }
  return null;
}
