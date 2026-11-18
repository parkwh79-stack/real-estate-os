export interface StructuredBackendError {
  status: number;
  error: string | null;
  requiredPermission: string | null;
  role: string | null;
  reason: string | null;
  action: string | null;
  message: string | null;
}

type BackendErrorMessageOptions = {
  action: string;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const normalized = String(value).trim();
  return normalized || null;
}

function firstText(...values: unknown[]): string | null {
  for (const value of values) {
    const normalized = text(value);
    if (normalized) return normalized;
  }
  return null;
}

/**
 * Preserve FastAPI's structured error boundary instead of flattening every
 * non-2xx response into an HTTP status. Authorization remains server-owned;
 * this parser only makes the verified failure reason visible to the user.
 */
export async function parseBackendErrorResponse(
  response: Response,
): Promise<StructuredBackendError> {
  const raw = await response.text().catch(() => "");
  let payload: unknown = null;
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = raw;
    }
  }

  const envelope = record(payload);
  const detailValue = envelope?.detail;
  const detail = record(detailValue);
  const message = firstText(
    detail?.message,
    detail?.detail,
    typeof detailValue === "string" ? detailValue : null,
    envelope?.message,
    typeof payload === "string" ? payload : null,
  );

  return {
    status: response.status,
    error: firstText(envelope?.error, detail?.error, detail?.code),
    requiredPermission: firstText(
      detail?.requiredPermission,
      detail?.required_permission,
      envelope?.requiredPermission,
      envelope?.required_permission,
    ),
    role: firstText(detail?.role, envelope?.role),
    reason: firstText(detail?.reason, envelope?.reason),
    action: firstText(detail?.action, envelope?.action),
    message,
  };
}

export function backendErrorMessage(
  error: StructuredBackendError,
  { action }: BackendErrorMessageOptions,
): string {
  if (error.error === "ai_profile_audit_unavailable") {
    return "설정이 이미 반영되었을 수 있으나 감사 기록을 확인하지 못했습니다. 다시 저장하거나 자동 재시도하지 말고 현재 설정을 조회해 확인해 주세요.";
  }
  if (error.error === "ai_profile_authority_unavailable") {
    return "개인 AI 설정을 읽지 못해 실행하지 않았습니다. 시스템 AI로 바꾸지 않았습니다. 계정의 AI 설정을 다시 조회해 주세요.";
  }
  if (error.error === "ai_profile_not_configured") {
    return "저장한 개인 AI 설정이 없습니다. 계정 설정에서 개인 설정을 저장하거나 이 브라우저의 실행 방식을 직접 선택해 주세요.";
  }
  if (error.error === "ai_profile_selection_conflict") {
    return "요청한 AI와 저장한 개인 설정이 일치하지 않습니다. 계정 설정의 제공자와 실행 방식을 확인해 주세요.";
  }
  if (error.error === "intake_transfer_result_uncertain") {
    return "자료가 반영되었을 수 있으나 완료 기록을 확인하지 못했습니다. 프로젝트 자료와 검토 결과를 먼저 확인해 주세요. 같은 자료를 자동으로 다시 적용하지 않았습니다.";
  }
  if (error.error === "intake_transfer_busy") {
    return "이 파일의 다른 요청을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.";
  }
  if (error.error === "intake_transfer_expired") {
    return "파일 전송 대기 시간이 만료되었습니다. 다시 시도하면 현재 선택한 파일을 새로 전송합니다.";
  }
  if (error.status === 413 || error.message?.includes("FUNCTION_PAYLOAD_TOO_LARGE")) {
    return `${action} 중 전송 크기 한도를 넘었습니다. 화면을 새로고침한 뒤 파일을 다시 선택해 주세요. 같은 오류가 계속되면 문서를 나누어 올려 주세요.`;
  }
  if (error.status === 401) {
    return `로그인 세션이 만료되어 ${action}을 수행할 수 없습니다. 다시 로그인한 뒤 시도해 주세요.`;
  }
  if (error.status === 403 && error.error === "cross_site_request_rejected") {
    return "요청 출처를 확인할 수 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.";
  }
  if (error.status === 403 && error.requiredPermission) {
    const subject = error.role ? `현재 역할 '${error.role}'` : "현재 계정";
    return `${subject}에는 '${error.requiredPermission}' 권한이 없습니다. ${action}에 필요한 권한이므로 Tenant 관리자에게 역할 변경을 요청해 주세요.`;
  }
  if (error.status === 403 && error.error === "database_permission_denied") {
    return `${action}에 필요한 Tenant 데이터베이스 권한이 없습니다. 관리자에게 API 역할과 데이터베이스 권한을 확인해 달라고 요청해 주세요.`;
  }
  if (error.status === 403) {
    return error.message
      ? `${action} 권한이 거부되었습니다. ${error.message}`
      : `${action} 권한이 없습니다. Tenant 관리자에게 현재 역할과 권한을 확인해 달라고 요청해 주세요.`;
  }
  if (error.status === 503) {
    return `${action}에 필요한 프로젝트 데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.`;
  }
  return error.message
    ? `${action}에 실패했습니다. ${error.message}`
    : `${action}에 실패했습니다. (HTTP ${error.status})`;
}

export async function backendErrorMessageForResponse(
  response: Response,
  options: BackendErrorMessageOptions,
): Promise<string> {
  return backendErrorMessage(await parseBackendErrorResponse(response), options);
}
