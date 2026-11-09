"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock3, RefreshCw, UserCheck } from "lucide-react";
import {
  AuthClientRequestError,
  decideOnboardingAccessRequest,
  loadOnboardingAccessRequests,
  loadOnboardingStatus,
  type OnboardingAccessRequest,
} from "../../lib/auth-client";

export function OnboardingAccessRequestManagement({
  assuranceLevel,
  currentTenantId,
  canManageUsers,
}: {
  assuranceLevel: "aal1" | "aal2" | null;
  currentTenantId?: string | null;
  canManageUsers: boolean;
}) {
  const [requests, setRequests] = useState<OnboardingAccessRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [platformOperator, setPlatformOperator] = useState<boolean | null>(null);
  const [reviewScope, setReviewScope] = useState<"tenant" | "platform">(
    canManageUsers && currentTenantId ? "tenant" : "platform",
  );

  const refresh = useCallback(async (preserveMessage = false) => {
    setLoading(true);
    if (!preserveMessage) { setNotice(null); setError(null); }
    try {
      const result = await loadOnboardingAccessRequests(
        "pending",
        reviewScope === "tenant" ? currentTenantId : undefined,
        reviewScope,
      );
      setRequests(result.requests);
    } catch (caught) {
      setRequests([]);
      setError(accessRequestErrorMessage(caught));
    } finally { setLoading(false); }
  }, [currentTenantId, reviewScope]);

  useEffect(() => {
    let active = true;
    void loadOnboardingStatus()
      .then((status) => {
        if (active) {
          const operator = Boolean(status.platformOperator);
          setPlatformOperator(operator);
          if (!canManageUsers && operator) setReviewScope("platform");
        }
      })
      .catch(() => {
        if (active) setPlatformOperator(false);
      });
    return () => { active = false; };
  }, [canManageUsers]);

  useEffect(() => {
    const authorizedForScope = reviewScope === "tenant"
      ? canManageUsers && Boolean(currentTenantId)
      : platformOperator === true;
    if (authorizedForScope) void refresh();
    else if (platformOperator !== null) setLoading(false);
  }, [canManageUsers, currentTenantId, platformOperator, refresh, reviewScope]);

  async function decide(request: OnboardingAccessRequest, decision: "approve" | "reject", assignedRole: "admin" | "analyst" | "viewer", reason: string) {
    if (!reason.trim()) { setError("승인 또는 거절 사유를 입력해 주세요."); return; }
    setBusyId(request.requestId); setError(null); setNotice(null);
    try {
      await decideOnboardingAccessRequest(request.requestId, {
        decision,
        expectedRequestVersion: request.requestVersion,
        assignedRole: decision === "approve" ? assignedRole : undefined,
        reason: reason.trim(),
      });
      await refresh(true);
      setNotice(decision === "approve" ? "가입 신청을 승인하고 멤버십을 발급했습니다." : "가입 신청을 거절했습니다.");
    } catch (caught) {
      if (caught instanceof AuthClientRequestError && ["access_request_version_conflict", "access_request_not_pending", "tenant_access_request_version_conflict", "tenant_access_request_not_pending"].includes(caught.code)) await refresh(true);
      setError(accessRequestErrorMessage(caught));
    } finally { setBusyId(null); }
  }

  if (!canManageUsers && platformOperator !== true) return null;

  return (
    <section data-testid="onboarding-access-request-management" className="rounded-re-panel border border-re-border bg-re-surface p-5 text-re-text shadow-re-panel">
      <div className="flex flex-wrap items-start justify-between gap-3"><div className="flex gap-3"><UserCheck className="mt-0.5 h-5 w-5 text-blue-600" /><div><h2 className="text-lg font-bold">기관 가입 신청 승인</h2><p className="mt-1 text-xs leading-5 text-re-secondary">DB 권위 신청을 CAS 버전으로 승인·거절합니다. 승인자 본인 신청은 서버가 차단합니다.</p></div></div><button type="button" onClick={() => void refresh()} disabled={loading} className="inline-flex h-9 items-center gap-2 rounded-lg border border-re-border px-3 text-xs font-bold disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />다시 조회</button></div>
      {platformOperator && canManageUsers && currentTenantId ? (
        <div className="mt-4 grid grid-cols-2 rounded-xl border border-re-border bg-re-subtle p-1" role="tablist" aria-label="가입 신청 검토 범위">
          <button type="button" role="tab" aria-selected={reviewScope === "tenant"} onClick={() => setReviewScope("tenant")} className={`h-9 rounded-lg text-xs font-bold ${reviewScope === "tenant" ? "bg-re-surface shadow-sm" : "text-re-secondary"}`}>현재 Tenant 가입 신청</button>
          <button type="button" role="tab" aria-selected={reviewScope === "platform"} onClick={() => setReviewScope("platform")} className={`h-9 rounded-lg text-xs font-bold ${reviewScope === "platform" ? "bg-re-surface shadow-sm" : "text-re-secondary"}`}>신규 기관 신청 검토</button>
        </div>
      ) : null}
      {reviewScope === "tenant" && currentTenantId ? <p className="mt-3 font-mono text-[11px] text-re-muted">Tenant queue: {currentTenantId}</p> : null}
      {reviewScope === "platform" ? <p className="mt-3 text-xs font-semibold text-violet-700">Platform operator queue · tenantId 없이 신규 기관 신청만 조회</p> : null}
      {assuranceLevel !== "aal2" ? <div className="mt-4 flex gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />승인·거절에는 최근 TOTP AAL2 인증이 필요합니다. 위 계정 보안 영역에서 세션을 승격해 주세요.</div> : null}
      {error ? <p role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs leading-5 text-rose-800">{error}</p> : null}
      {notice ? <p role="status" className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-800">{notice}</p> : null}
      <div className="mt-4 space-y-3">
        {loading ? <p className="py-8 text-center text-sm text-re-muted">가입 신청을 불러오는 중입니다.</p> : null}
        {!loading && !requests.length ? <div className="rounded-xl border border-dashed border-re-border py-8 text-center"><CheckCircle2 className="mx-auto h-6 w-6 text-emerald-600" /><p className="mt-2 text-sm font-bold">대기 중인 가입 신청이 없습니다</p></div> : null}
        {requests.map((request) => <AccessRequestRow key={request.requestId} request={request} disabled={busyId !== null || assuranceLevel !== "aal2"} busy={busyId === request.requestId} onDecide={decide} />)}
      </div>
    </section>
  );
}

function AccessRequestRow({ request, disabled, busy, onDecide }: { request: OnboardingAccessRequest; disabled: boolean; busy: boolean; onDecide: (request: OnboardingAccessRequest, decision: "approve" | "reject", role: "admin" | "analyst" | "viewer", reason: string) => Promise<void> }) {
  const [role, setRole] = useState<"analyst" | "viewer">(request.requestedRole);
  const [reason, setReason] = useState("");
  const assignedRole = request.requestType === "new_organization" ? "admin" : role;
  return (
    <article
      data-testid={`access-request-${request.requestId}`}
      className="rounded-xl border border-re-border p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 text-sm font-bold">
            <Clock3 className="h-4 w-4 text-amber-600" />
            {request.organizationName ?? request.organizationId ?? "기관 미지정"}
          </p>
          <p className="mt-1 text-xs text-re-secondary">
            {request.requestType === "new_organization"
              ? "신규 기관 신청 · 승인 시 최초 기관 관리자"
              : `기존 기관 가입 · 신청 역할 ${request.requestedRole}`} · v{request.requestVersion}
          </p>
          <p className="mt-1 break-all font-mono text-[11px] text-re-muted">
            {request.requesterDisplayName ?? request.requesterEmail ?? request.requesterSubject ?? request.requestId}
          </p>
        </div>
        {request.requestType === "new_organization" ? (
          <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-1 text-[11px] font-bold text-violet-700">
            플랫폼 운영자 승인 필요
          </span>
        ) : null}
      </div>
      {request.message ? (
        <p className="mt-3 rounded-lg bg-re-subtle p-3 text-xs leading-5 text-re-secondary">
          {request.message}
        </p>
      ) : null}
      {request.requestType === "new_organization" ? (
        <div data-testid={`business-verification-${request.requestId}`} className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900">
          <p><strong>사업자등록번호:</strong> {formatBusinessRegistrationNumber(request.businessRegistrationNumber)}</p>
          <p className="mt-1 font-bold">
            {request.businessRegistrationVerified === true
              ? "검증 완료"
              : "미검증—승인 전 확인 필요"}
          </p>
          <p className="mt-1 break-all text-amber-800">
            <strong>증빙:</strong> {request.businessRegistrationEvidenceLocator || "증빙 미첨부"}
          </p>
        </div>
      ) : null}
      <div className="mt-3 grid gap-2 md:grid-cols-[150px_1fr_auto_auto]">
        {request.requestType === "new_organization" ? (
          <div aria-label="발급 역할" className="flex h-10 items-center rounded-lg border border-violet-200 bg-violet-50 px-3 text-xs font-bold text-violet-800">
            최초 기관 관리자
          </div>
        ) : (
          <select value={role} onChange={(event) => setRole(event.target.value as "analyst" | "viewer")} disabled={disabled} aria-label="발급 역할" className="h-10 rounded-lg border border-re-border bg-re-surface px-3 text-xs">
            <option value="analyst">분석 담당</option>
            <option value="viewer">조회자</option>
          </select>
        )}
        <input value={reason} onChange={(event) => setReason(event.target.value)} disabled={disabled} placeholder="필수 결정 사유" aria-label="결정 사유" className="h-10 rounded-lg border border-re-border bg-re-surface px-3 text-xs" />
        <button type="button" onClick={() => void onDecide(request, "reject", assignedRole, reason)} disabled={disabled || busy || !reason.trim()} className="h-10 rounded-lg border border-rose-200 px-3 text-xs font-bold text-rose-700 disabled:opacity-40">거절</button>
        <button type="button" onClick={() => void onDecide(request, "approve", assignedRole, reason)} disabled={disabled || busy || !reason.trim()} className="h-10 rounded-lg bg-blue-700 px-3 text-xs font-bold text-white disabled:opacity-40">{busy ? "처리 중" : "승인"}</button>
      </div>
    </article>
  );
}

function formatBusinessRegistrationNumber(value: string | null) {
  const digits = (value ?? "").replace(/\D/g, "");
  if (digits.length !== 10) return value || "미입력";
  return `${digits.slice(0, 3)}-${digits.slice(3, 5)}-${digits.slice(5)}`;
}

function accessRequestErrorMessage(error: unknown) {
  if (!(error instanceof AuthClientRequestError)) return error instanceof Error ? error.message : "가입 신청을 처리하지 못했습니다.";
  const messages: Record<string, string> = {
    step_up_required: "승인 작업에는 TOTP AAL2 추가 인증이 필요합니다.",
    access_request_self_approval_forbidden: "자신이 제출한 가입 신청은 직접 승인할 수 없습니다. 다른 승인자에게 요청해 주세요.",
    access_request_version_conflict: "다른 관리자가 먼저 신청을 처리했습니다. 최신 목록을 다시 불러왔습니다.",
    access_request_not_pending: "이미 처리된 신청입니다. 최신 목록을 확인해 주세요.",
    platform_operator_required: "신규 기관 신청은 등록된 플랫폼 온보딩 운영자만 승인할 수 있습니다.",
    tenant_access_request_self_approval_forbidden: "자신이 제출한 가입 신청은 직접 승인할 수 없습니다. 다른 승인자에게 요청해 주세요.",
    tenant_access_request_version_conflict: "다른 관리자가 먼저 신청을 처리했습니다. 최신 목록을 다시 불러왔습니다.",
    tenant_access_request_not_pending: "이미 처리된 신청입니다. 최신 목록을 확인해 주세요.",
    platform_operator_authority_required: "신규 기관 신청은 등록된 플랫폼 온보딩 운영자만 승인할 수 있습니다.",
    tenant_admin_authority_required: "기존 기관 가입 신청은 해당 Tenant 관리자만 승인할 수 있습니다.",
    tenant_admin_selector_required: "관리 중인 Tenant가 여러 개입니다. 현재 Settings의 Tenant 컨텍스트를 다시 선택해 주세요.",
  };
  return messages[error.code] ?? error.message;
}
