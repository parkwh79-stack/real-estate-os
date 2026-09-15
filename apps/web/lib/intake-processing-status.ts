type Data = Record<string, unknown>;
const record = (value: unknown): Data => value && typeof value === "object" && !Array.isArray(value) ? value as Data : {};

/** HTTP 200 acknowledges transport, not successful extraction. */
export function intakeProcessingFailure(body: Data): string | null {
  const project = record(body.project);
  const reanalysis = record(body.autoReanalysis ?? project.autoReanalysis);
  if (reanalysis.triggered === true && (reanalysis.status === "failed" || reanalysis.cascadeComplete === false)) {
    return "자료는 저장됐지만 후속 재분석이 완료되지 않았습니다. 딜룸에서 실패·검토 대기 단계를 확인하고 재분석하세요.";
  }
  const statuses = [body.status, record(body.validation).status,
    record(body._meta).parsing_source, record(project._meta).parsing_source,
    record(record(body.upload)._meta).parsing_source];
  if (statuses.includes("host_llm_required")) {
    return "원본은 접수됐지만 LLM 추출이 필요합니다. AI 연결을 확인한 뒤 다시 처리하세요.";
  }
  if (statuses.some(status => ["parse_failed", "extraction_unavailable", "failed", "error"].includes(String(status)))) {
    return "원본 접수와 별개로 파싱 또는 검증에 실패했습니다. 실패 단계를 확인한 뒤 다시 처리하세요.";
  }
  return null;
}
