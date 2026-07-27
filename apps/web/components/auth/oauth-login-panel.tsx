"use client";

import {
  AlertCircle,
  ChevronDown,
  Info,
  LoaderCircle,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  createLocalPilotSession,
  loadSupabaseAuthReadiness,
  loginLocalPilotSession,
  signInWithSupabaseOAuth,
  type SupabaseAuthReadiness,
} from "../../lib/auth-client";
import { localPilotAuthEnabled } from "../../lib/auth-policy";

type Provider = "google" | "kakao";
type PolicyKind = "privacy" | "terms";

const AUTH_ERRORS: Record<string, string> = {
  missing_oauth_code:
    "인증 제공자가 로그인 코드를 반환하지 않았습니다. 다시 시도해 주세요.",
  oauth_provider_scope_invalid:
    "로그인 제공자의 동의 항목이 Supabase 설정과 일치하지 않습니다. 관리자에게 문의해 주세요.",
  oauth_access_denied:
    "로그인 동의가 취소되었습니다. 동의 후 다시 시도해 주세요.",
  oauth_provider_failed:
    "로그인 제공자가 인증을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  oauth_code_exchange_failed:
    "인증 세션을 확인하지 못했습니다. 새 로그인으로 다시 시도해 주세요.",
  oauth_claims_invalid:
    "계정 서명을 검증하지 못했습니다. 조직 관리자에게 문의해 주세요.",
  oauth_session_missing:
    "인증 세션이 완성되지 않았습니다. 새 로그인으로 다시 시도해 주세요.",
  identity_not_provisioned:
    "이 계정에 연결된 워크스페이스 멤버십이 없습니다. 관리자 초대를 받은 뒤 다시 로그인해 주세요.",
  tenant_selection_required:
    "이 계정에 여러 워크스페이스가 연결되어 로그인 대상을 자동으로 결정할 수 없습니다. 조직 관리자에게 문의해 주세요.",
  identity_authority_unavailable:
    "워크스페이스 권한 정보를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  backend_unavailable:
    "업무 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  invitation_accept_failed:
    "초대 대상과 로그인 계정이 일치하지 않거나 초대가 만료되었습니다. 관리자에게 새 초대를 요청해 주세요.",
  oauth_not_configured:
    "인증 제공자 설정을 확인하지 못했습니다. 관리자에게 문의해 주세요.",
};

export function OAuthLoginPanel() {
  const [readiness, setReadiness] = useState<SupabaseAuthReadiness | null>(
    null,
  );
  const [checking, setChecking] = useState(true);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [submittingProvider, setSubmittingProvider] = useState<Provider | null>(
    null,
  );
  const [authError, setAuthError] = useState<string | null>(null);
  const [invitationPending, setInvitationPending] = useState(false);
  const [policy, setPolicy] = useState<PolicyKind | null>(null);
  const hasLocalPilot = localPilotAuthEnabled();

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const errorCode = query.get("error");
    setAuthError(
      errorCode
        ? (AUTH_ERRORS[errorCode] ??
            "로그인을 완료하지 못했습니다. 다시 시도해 주세요.")
        : null,
    );
    setInvitationPending(query.get("invitation") === "pending");

    let active = true;
    void loadSupabaseAuthReadiness()
      .then((result) => {
        if (!active) return;
        setReadiness(result);
        setReadinessError(null);
      })
      .catch(() => {
        if (!active) return;
        setReadinessError(
          "인증 서비스 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        );
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const boundaryReady = Boolean(
    readiness?.configured &&
    readiness.settingsReachable &&
    readiness.canonicalOriginReady &&
    readiness.backend.ready,
  );
  const availableProviders = useMemo(
    () =>
      (["google", "kakao"] as Provider[]).filter(
        (provider) => boundaryReady && readiness?.providerUsable[provider],
      ),
    [boundaryReady, readiness],
  );

  async function startProvider(provider: Provider) {
    setSubmittingProvider(provider);
    setAuthError(null);
    try {
      await signInWithSupabaseOAuth(provider, resolveNextPath());
    } catch (error) {
      setAuthError(
        error instanceof Error
          ? error.message
          : `${provider === "google" ? "Google" : "Kakao"} 로그인을 시작하지 못했습니다.`,
      );
      setSubmittingProvider(null);
    }
  }

  return (
    <div>
      {invitationPending ? (
        <StatusMessage tone="info">
          초대 링크를 확인했습니다. 초대받은 계정으로 인증하면 워크스페이스
          가입을 완료합니다. 이메일이 같더라도 다른 외부 계정은 자동으로
          병합되지 않습니다.
        </StatusMessage>
      ) : null}

      {authError ? (
        <StatusMessage tone="error">{authError}</StatusMessage>
      ) : null}

      {readiness?.providers.kakao && !readiness.providerUsable.kakao ? (
        <StatusMessage tone="info">
          Kakao 로그인은 account_email 동의 범위의 실제 검증이 끝날 때까지
          차단되어 있습니다. 현재는 Google 로그인을 이용해 주세요.
        </StatusMessage>
      ) : null}

      {checking ? (
        <div
          aria-live="polite"
          className="flex min-h-28 items-center justify-center rounded-xl border border-slate-200 bg-white text-sm font-medium text-slate-600 shadow-sm"
        >
          <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-blue-600" />
          인증 서비스 확인 중
        </div>
      ) : readinessError ? (
        <StatusMessage tone="error">{readinessError}</StatusMessage>
      ) : availableProviders.length ? (
        <div className="space-y-3">
          {availableProviders.includes("google") ? (
            <GoogleButton
              busy={submittingProvider === "google"}
              disabled={submittingProvider !== null}
              onClick={() => void startProvider("google")}
            />
          ) : null}
          {availableProviders.includes("kakao") ? (
            <KakaoButton
              busy={submittingProvider === "kakao"}
              disabled={submittingProvider !== null}
              onClick={() => void startProvider("kakao")}
            />
          ) : null}
        </div>
      ) : (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
          <div className="flex items-start gap-2.5">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
            <div>
              <p className="font-bold">
                현재 사용할 수 있는 조직 로그인이 없습니다.
              </p>
              <p className="mt-1 text-amber-800">
                Google·Kakao 제공자와 FastAPI 멤버십 경계가 모두 준비된 경우에만
                로그인 버튼이 활성화됩니다. 조직 관리자에게 설정 상태를 문의해
                주세요.
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="mt-5 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
          <div>
            <p className="text-sm font-bold text-slate-900">
              워크스페이스 접근 원칙
            </p>
            <p className="mt-1 text-xs leading-5 text-slate-600">
              OAuth 계정 인증만으로 가입되지 않습니다. 초대 또는 관리자 승인 후
              서버가 테넌트 멤버십과 역할을 확인하며, 최종 승인 작업에는 추가
              인증이 요구될 수 있습니다.
            </p>
          </div>
        </div>
      </div>

      {hasLocalPilot ? <LocalPilotAccess /> : null}

      <div className="mt-6 border-t border-slate-200 pt-5">
        <p className="text-xs leading-5 text-slate-500">
          로그인하면 조직의 계약 조건과 운영 정책에 따른 서비스 이용에 동의하게
          됩니다. 기본 제공: Gemini 무료 fallback · 조직별 AI 정책은 로그인 후
          확인할 수 있습니다.
        </p>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs font-semibold text-slate-600">
          <button
            type="button"
            onClick={() => setPolicy("privacy")}
            className="underline decoration-slate-300 underline-offset-4 hover:text-slate-950"
          >
            개인정보 처리 안내
          </button>
          <button
            type="button"
            onClick={() => setPolicy("terms")}
            className="underline decoration-slate-300 underline-offset-4 hover:text-slate-950"
          >
            서비스 이용약관 안내
          </button>
        </div>
      </div>

      {policy ? (
        <PolicyDialog kind={policy} onClose={() => setPolicy(null)} />
      ) : null}
    </div>
  );
}

function GoogleButton({
  busy,
  disabled,
  onClick,
}: {
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-14 w-full items-center justify-center gap-3 rounded-xl border border-slate-300 bg-white px-4 text-sm font-bold text-slate-800 shadow-sm transition hover:border-slate-400 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60"
    >
      {busy ? (
        <LoaderCircle className="h-5 w-5 animate-spin" />
      ) : (
        <GoogleMark />
      )}
      {busy ? "Google 인증으로 이동 중" : "Google 계정으로 계속"}
    </button>
  );
}

function KakaoButton({
  busy,
  disabled,
  onClick,
}: {
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-14 w-full items-center justify-center gap-3 rounded-xl border border-[#e7d000] bg-[#FEE500] px-4 text-sm font-bold text-[#191919] shadow-sm transition hover:bg-[#f5dc00] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#b39c00] focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60"
    >
      {busy ? <LoaderCircle className="h-5 w-5 animate-spin" /> : <KakaoMark />}
      {busy ? "Kakao 인증으로 이동 중" : "Kakao 계정으로 계속"}
    </button>
  );
}

function StatusMessage({
  tone,
  children,
}: {
  tone: "info" | "error";
  children: ReactNode;
}) {
  const classes =
    tone === "error"
      ? "border-rose-200 bg-rose-50 text-rose-800"
      : "border-blue-200 bg-blue-50 text-blue-900";
  const Icon = tone === "error" ? AlertCircle : Info;
  return (
    <div
      aria-live="polite"
      className={`mb-4 rounded-xl border p-4 text-sm leading-6 ${classes}`}
    >
      <div className="flex items-start gap-2.5">
        <Icon className="mt-0.5 h-5 w-5 shrink-0" />
        <p>{children}</p>
      </div>
    </div>
  );
}

function LocalPilotAccess() {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [tenantId, setTenantId] = useState("realytics-pilot");
  const [tenantName, setTenantName] = useState("REALYTICS Pilot Workspace");
  const [userId, setUserId] = useState("pilot-reviewer");
  const [displayName, setDisplayName] = useState("파일럿 심사역");
  const [role, setRole] = useState("analyst");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "login") {
        await loginLocalPilotSession({ tenantId, userId });
      } else {
        await createLocalPilotSession({
          tenantId,
          tenantName,
          userId,
          displayName,
          role,
        });
      }
      window.location.assign(resolveNextPath());
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "로컬 인증을 완료하지 못했습니다.",
      );
      setSubmitting(false);
    }
  }

  return (
    <details className="mt-5 rounded-xl border border-dashed border-slate-300 bg-slate-50">
      <summary
        data-testid="local-pilot-login-tab"
        className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-xs font-bold text-slate-600"
      >
        개발 환경 전용 로컬 인증
        <ChevronDown className="h-4 w-4" />
      </summary>
      <form
        onSubmit={submit}
        className="space-y-3 border-t border-slate-200 p-4"
      >
        <p className="text-xs leading-5 text-amber-700">
          PROTOTYPE-ONLY: 이 경로는 로컬 개발·테스트 환경에서만 활성화됩니다.
        </p>
        <div className="flex rounded-lg bg-slate-200 p-1 text-xs font-semibold">
          <button
            type="button"
            onClick={() => setMode("login")}
            className={`flex-1 rounded-md px-3 py-2 ${mode === "login" ? "bg-white shadow-sm" : "text-slate-600"}`}
          >
            기존 사용자
          </button>
          <button
            type="button"
            onClick={() => setMode("signup")}
            className={`flex-1 rounded-md px-3 py-2 ${mode === "signup" ? "bg-white shadow-sm" : "text-slate-600"}`}
          >
            로컬 사용자 생성
          </button>
        </div>
        <PilotField
          label="워크스페이스 ID"
          value={tenantId}
          onChange={setTenantId}
        />
        <PilotField label="사용자 ID" value={userId} onChange={setUserId} />
        {mode === "signup" ? (
          <>
            <PilotField
              label="워크스페이스명"
              value={tenantName}
              onChange={setTenantName}
            />
            <PilotField
              label="표시 이름"
              value={displayName}
              onChange={setDisplayName}
            />
            <label className="block text-xs font-semibold text-slate-700">
              역할
              <select
                value={role}
                onChange={(event) => setRole(event.target.value)}
                className="mt-1.5 h-10 w-full rounded-lg border border-slate-300 bg-white px-3"
              >
                <option value="analyst">분석역</option>
                <option value="reviewer">심사역</option>
                <option value="admin">관리자</option>
                <option value="viewer">조회자</option>
              </select>
            </label>
          </>
        ) : null}
        {error ? <p className="text-xs text-rose-700">{error}</p> : null}
        <button
          type="submit"
          disabled={submitting}
          className="h-10 w-full rounded-lg bg-slate-900 text-xs font-bold text-white disabled:opacity-60"
        >
          {submitting ? "처리 중" : "로컬 환경으로 계속"}
        </button>
      </form>
    </details>
  );
}

function PilotField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs font-semibold text-slate-700">
      {label}
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1.5 h-10 w-full rounded-lg border border-slate-300 bg-white px-3"
      />
    </label>
  );
}

function PolicyDialog({
  kind,
  onClose,
}: {
  kind: PolicyKind;
  onClose: () => void;
}) {
  const privacy = kind === "privacy";
  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/50 px-4 py-8 backdrop-blur-sm">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-policy-title"
        className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-blue-700">
              운영 정책 요약
            </p>
            <h3
              id="login-policy-title"
              className="mt-2 text-xl font-black text-slate-950"
            >
              {privacy ? "개인정보 처리 안내" : "서비스 이용약관 안내"}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="안내 닫기"
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        {privacy ? (
          <div className="mt-5 space-y-3 text-sm leading-6 text-slate-600">
            <p>
              인증은 Google·Kakao와 Supabase Auth가 처리하며, 업무 접근 권한은
              PostgreSQL 테넌트 멤버십을 기준으로 확인합니다.
            </p>
            <p>
              프로젝트 문서와 분석 결과의 보존·삭제·내보내기 조건은 소속 기관의
              계약 및 데이터 정책을 따릅니다.
            </p>
          </div>
        ) : (
          <div className="mt-5 space-y-3 text-sm leading-6 text-slate-600">
            <p>
              REALYTICS는 부동산금융 검토를 지원하는 업무 시스템이며, 자동 생성
              결과만으로 최종 투자·여신 승인을 대신하지 않습니다.
            </p>
            <p>
              사용자는 소속 기관이 부여한 역할과 승인 절차를 준수해야 하며,
              승인·내보내기 등 중요 작업은 감사 이력에 기록됩니다.
            </p>
          </div>
        )}
        <p className="mt-5 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
          이 내용은 파일럿 운영 원칙의 요약입니다. 정식 개인정보 처리방침·계약
          약관은 법무 검수와 기관 계약을 거쳐 별도로 제공됩니다.
        </p>
        <button
          type="button"
          onClick={onClose}
          className="mt-5 h-11 w-full rounded-xl bg-slate-950 text-sm font-bold text-white hover:bg-slate-800"
        >
          확인
        </button>
      </section>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg className="h-5 w-5" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09A6.6 6.6 0 0 1 5.49 12c0-.73.13-1.43.35-2.09V7.06H2.18A11 11 0 0 0 1 12c0 1.78.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
      />
    </svg>
  );
}

function KakaoMark() {
  return (
    <svg
      className="h-5 w-5 fill-current"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M12 3C6.477 3 2 6.477 2 10.772c0 2.766 1.83 5.19 4.606 6.634l-1.171 4.298c-.098.36.309.658.621.452l5.06-3.342c.291.026.586.04.884.04 5.523 0 10-3.477 10-7.772C22 6.477 17.523 3 12 3z" />
    </svg>
  );
}

function resolveNextPath() {
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next || next === "/" || next === "/dashboard") return "/start";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/start";
}
