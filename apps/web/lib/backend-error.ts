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
