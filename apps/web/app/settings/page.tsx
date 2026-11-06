"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  CheckCircle2,
  Clock3,
  Copy,
  KeyRound,
  Link2,
  LogOut,
  MailPlus,
  RotateCcw,
  Save,
  ShieldCheck,
  ShieldPlus,
  Sparkles,
  Trash2,
  UserRound,
} from "lucide-react";
import {
  apiBase,
  authHeaders,
  configuredSupabaseAuthIssuer,
  createLocalPilotSession,
  enrollSupabaseTotp,
  issueTenantInvitation,
  loadSupabaseAuthReadiness,
  loadSupabaseSecurityProfile,
  loadTenantInvitations,
  reauthenticateSupabaseTotp,
  revokeTenantInvitation,
  signOutSupabaseSession,
  startSupabaseIdentityLink,
  unenrollSupabaseMfa,
  unlinkSupabaseIdentity,
  verifySupabaseTotp,
  type SupabaseAuthReadiness,
  type SupabaseSecurityProfile,
  type TenantInvitation,
  type TenantInvitationRole,
  type TotpEnrollment,
} from "../../lib/auth-client";
import { localPilotAuthEnabled } from "../../lib/auth-policy";
import { useAuth } from "../../components/auth/auth-context";
import { AccountProfileEditor } from "../../components/auth/account-profile-editor";
import { accountDisplayName } from "../../lib/account-profile";
import { TenantMembershipManagement } from "../../components/auth/tenant-membership-management";
import { OnboardingAccessRequestManagement } from "../../components/auth/onboarding-access-request-management";

const roleOptions = [
  { value: "reviewer", label: "심사역", description: "리스크 검토, 기준값 승인, 보고서 export 권한" },
  { value: "analyst", label: "분석 담당", description: "프로젝트 작성, 증빙 조회, 분석 실행 권한" },
  { value: "admin", label: "관리자", description: "사용자 관리와 전체 운영 권한" },
  { value: "viewer", label: "조회자", description: "프로젝트와 보고서 조회 중심 권한" },
];

const providerOptions = [
  { value: "gemini", label: "Gemini", defaultModel: "gemini-2.5-flash" },
  { value: "openai", label: "OpenAI", defaultModel: "gpt-4.1-mini" },
  { value: "anthropic", label: "Anthropic Claude", defaultModel: "claude-3-5-sonnet-latest" },
  { value: "groq", label: "Groq", defaultModel: "llama-3.3-70b-versatile" },
  { value: "openrouter", label: "OpenRouter", defaultModel: "openai/gpt-4o-mini" },
  { value: "deepseek", label: "DeepSeek", defaultModel: "deepseek-chat" },
  { value: "local", label: "Local LLM", defaultModel: "local-model" },
];

const invitationRoleOptions: Array<{
  value: TenantInvitationRole;
  label: string;
}> = [
  { value: "analyst", label: "분석 담당" },
  { value: "reviewer", label: "심사역" },
  { value: "viewer", label: "조회자" },
  { value: "legal_reviewer", label: "법무 검토자" },
  { value: "external_counsel", label: "외부 법률자문" },
  { value: "compliance_officer", label: "준법 담당" },
  { value: "legal_counsel_approver", label: "법무 승인자" },
  { value: "admin", label: "테넌트 관리자" },
];

type InvitationBindingMode = "email" | "identity";

interface AiProfileResponse {
  profile?: {
    provider?: string;
    model?: string;
    status?: string;
    apiKeyMasked?: string;
    apiKeyConfigured?: boolean;
    allowSystemFallback?: boolean;
    updatedAt?: string;
  } | null;
  effectiveAi?: {
    provider?: string;
    runtimeProvider?: string;
    model?: string;
    directModelCall?: boolean;
    requiresApiKey?: string | null;
    selectionSource?: string;
  };
  fallbackPolicy?: {
    provider?: string;
    model?: string;
    description?: string;
  };
}

export default function SettingsPage() {
  const { user, session, permissions, logout } = useAuth();
  const hasLocalPilot = localPilotAuthEnabled();
  const [tenantId, setTenantId] = useState("pilot-tenant");
  const [tenantName, setTenantName] = useState("REALYTICS Pilot Workspace");
  const [userId, setUserId] = useState("credit_officer");
  const [email, setEmail] = useState("credit.officer@example.com");
  const [displayName, setDisplayName] = useState("심사역");
  const [role, setRole] = useState("reviewer");
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("gemini-2.5-flash");
  const apiKeyInputRef = useRef<HTMLInputElement>(null);
  const [apiKeyLength, setApiKeyLength] = useState(0);
  const [allowSystemFallback, setAllowSystemFallback] = useState(true);
  const [aiProfile, setAiProfile] = useState<AiProfileResponse | null>(null);
  const [authReadiness, setAuthReadiness] = useState<SupabaseAuthReadiness | null>(null);
  const [securityProfile, setSecurityProfile] = useState<SupabaseSecurityProfile | null>(null);
  const [totpEnrollment, setTotpEnrollment] = useState<TotpEnrollment | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [reauthenticationFactorId, setReauthenticationFactorId] = useState("");
  const reauthenticationCodeRef = useRef<HTMLInputElement>(null);
  const [invitations, setInvitations] = useState<TenantInvitation[]>([]);
  const [invitationBindingMode, setInvitationBindingMode] = useState<InvitationBindingMode>("email");
  const [invitationEmail, setInvitationEmail] = useState("");
  const [invitationIssuer, setInvitationIssuer] = useState(
    () => configuredSupabaseAuthIssuer() ?? "",
  );
  const [invitationSubject, setInvitationSubject] = useState("");
  const [invitationRole, setInvitationRole] = useState<TenantInvitationRole>("analyst");
  const [invitationExpiresInHours, setInvitationExpiresInHours] = useState(72);
  const [issuedInvitationUrl, setIssuedInvitationUrl] = useState<string | null>(null);
  const [invitationLoading, setInvitationLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [securityLoading, setSecurityLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedProvider = useMemo(
    () => providerOptions.find((item) => item.value === provider),
    [provider],
  );
  const canManageUsers = permissions.includes("user:manage");
  const verifiedTotpFactors = useMemo(
    () => (securityProfile?.factors ?? []).filter((factor) => (
      factor.factorType === "totp" && factor.status === "verified"
    )),
    [securityProfile?.factors],
  );
  const selectedReauthenticationFactorId = verifiedTotpFactors.some(
    (factor) => factor.factorId === reauthenticationFactorId,
  )
    ? reauthenticationFactorId
    : verifiedTotpFactors[0]?.factorId ?? "";
  const googleIdentityLinked = (securityProfile?.identities ?? []).some(
    (identity) => identity.provider === "google",
  );
  const kakaoIdentityLinked = (securityProfile?.identities ?? []).some(
    (identity) => identity.provider === "kakao",
  );
  const googleLinkAvailable = Boolean(
    authReadiness?.providerReady?.google && !googleIdentityLinked,
  );
  const kakaoLinkAvailable = Boolean(
    authReadiness?.providerReady?.kakao && !kakaoIdentityLinked,
  );
  const totpEnrollmentEligible = Boolean(
    securityProfile?.totpEnrollment?.eligible,
  );
  const recentAal2Eligible = Boolean(
    securityProfile?.assurance.recentAal2?.eligible,
  );

  const clearMessages = () => {
    setNotice(null);
    setError(null);
  };

  const clearApiKeyInput = useCallback(() => {
    if (apiKeyInputRef.current) {
      apiKeyInputRef.current.value = "";
    }
    setApiKeyLength(0);
  }, []);

  const login = async () => {
    setLoading(true);
    clearMessages();
    try {
      await createLocalPilotSession({
        tenantId: tenantId.trim() || "pilot-tenant",
        tenantName: tenantName.trim() || "REALYTICS Pilot Workspace",
        userId: userId.trim() || "credit_officer",
        email: email.trim() || undefined,
        displayName: displayName.trim() || userId.trim() || "심사역",
        role,
      });
      setNotice("로그인 세션을 생성했습니다. 이제 사용자별 LLM 설정과 프로젝트 데이터가 이 세션 기준으로 연결됩니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인 세션 생성에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const loadAiProfile = useCallback(async () => {
    clearApiKeyInput();
    setNotice(null);
    setError(null);
    const res = await fetch(`${apiBase}/auth/ai-profile`, {
      headers: authHeaders("web"),
      cache: "no-store",
    });
    if (!res.ok) {
      setAiProfile(null);
      setError(res.status === 401
        ? "로그인 후 AI 모델 설정을 조회할 수 있습니다."
        : "AI 설정을 불러오지 못했습니다. 다시 조회해 주세요. 기존 설정은 변경되지 않았습니다.");
      return;
    }
    const body = (await res.json()) as AiProfileResponse;
    setAiProfile(body);
    if (body.profile?.provider) setProvider(body.profile.provider);
    else if (body.fallbackPolicy?.provider) setProvider(body.fallbackPolicy.provider);
    if (body.profile?.model) setModel(body.profile.model);
    else if (body.fallbackPolicy?.model) setModel(body.fallbackPolicy.model);
    if (typeof body.profile?.allowSystemFallback === "boolean") {
      setAllowSystemFallback(body.profile.allowSystemFallback);
    }
  }, [clearApiKeyInput]);

  const loadAuthSecurity = useCallback(async () => {
    setSecurityLoading(true);
    try {
      const [readiness, profile] = await Promise.all([
        loadSupabaseAuthReadiness(),
        user ? loadSupabaseSecurityProfile() : Promise.resolve(null),
      ]);
      setAuthReadiness(readiness);
      setSecurityProfile(profile);
    } catch (err) {
      setSecurityProfile(null);
      setError(err instanceof Error ? err.message : "인증 보안 상태 조회에 실패했습니다.");
    } finally {
      setSecurityLoading(false);
    }
  }, [user]);

  const loadInvitations = useCallback(async () => {
    if (!user || !canManageUsers) {
      setInvitations([]);
      return;
    }
    setInvitationLoading(true);
    try {
      setInvitations(await loadTenantInvitations());
    } catch (err) {
      setInvitations([]);
      setError(err instanceof Error ? err.message : "초대 목록 조회에 실패했습니다.");
    } finally {
      setInvitationLoading(false);
    }
  }, [canManageUsers, user]);

  useEffect(() => {
    if (!user) {
      clearApiKeyInput();
      return;
    }
    void loadAiProfile();
  }, [user, loadAiProfile, clearApiKeyInput]);

  useEffect(() => {
    void loadAuthSecurity();
  }, [loadAuthSecurity]);

  useEffect(() => {
    void loadInvitations();
  }, [loadInvitations]);

  const beginTotpEnrollment = async () => {
    setSecurityLoading(true);
    clearMessages();
    try {
      setTotpEnrollment(await enrollSupabaseTotp());
      setTotpCode("");
      setNotice("인증 앱에 QR 코드를 등록한 뒤 일회용 코드를 확인해 주세요.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "MFA 등록을 시작하지 못했습니다.");
    } finally {
      setSecurityLoading(false);
    }
  };

  const confirmTotpEnrollment = async () => {
    if (!totpEnrollment) return;
    setSecurityLoading(true);
    clearMessages();
    try {
      await verifySupabaseTotp(totpEnrollment.factorId, totpCode);
      setTotpEnrollment(null);
      setTotpCode("");
      await loadAuthSecurity();
      setNotice("MFA가 검증되었고 현재 세션이 AAL2로 승격되었습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "MFA 코드 검증에 실패했습니다.");
    } finally {
      setSecurityLoading(false);
    }
  };

  const removeMfaFactor = async (factorId: string) => {
    if (!window.confirm(
      "이 TOTP 인증 요소를 삭제하시겠습니까? 계정 복구 수단이 줄어들 수 있습니다.",
    )) return;
    setSecurityLoading(true);
    clearMessages();
    try {
      await unenrollSupabaseMfa(factorId);
      await loadAuthSecurity();
      setNotice("MFA 요소를 해제했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "MFA 요소를 해제하지 못했습니다.");
    } finally {
      setSecurityLoading(false);
    }
  };

  const linkIdentity = async (provider: "google" | "kakao") => {
    const label = provider === "google" ? "Google" : "Kakao";
    if (!window.confirm(
      `${label} 계정을 현재 REALYTICS 계정의 추가 로그인 수단으로 연결하시겠습니까?`,
    )) return;
    setSecurityLoading(true);
    clearMessages();
    try {
      await startSupabaseIdentityLink(provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : "계정 연결을 시작하지 못했습니다.");
      setSecurityLoading(false);
    }
  };

  const reauthenticateTotpSession = async () => {
    const codeInput = reauthenticationCodeRef.current;
    const code = codeInput?.value.trim() ?? "";
    if (!selectedReauthenticationFactorId || !/^\d{6,8}$/.test(code)) {
      if (codeInput) codeInput.value = "";
      setError("인증 앱의 6~8자리 일회용 코드를 입력해 주세요.");
      return;
    }
    setSecurityLoading(true);
    clearMessages();
    try {
      await reauthenticateSupabaseTotp(selectedReauthenticationFactorId, code);
      if (codeInput) codeInput.value = "";
      await loadAuthSecurity();
      setNotice("현재 세션이 TOTP로 재인증되어 AAL2로 승격되었습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "TOTP 추가 인증에 실패했습니다.");
    } finally {
      if (codeInput) codeInput.value = "";
      setSecurityLoading(false);
    }
  };

  const removeIdentity = async (identityId: string, provider: string) => {
    if (provider === "email") {
      setError("기본 이메일 로그인 수단은 연결 해제할 수 없습니다.");
      return;
    }
    if (!window.confirm(
      `${providerLabel(provider)} 로그인을 현재 계정에서 연결 해제하시겠습니까?`,
    )) return;
    setSecurityLoading(true);
    clearMessages();
    try {
      await unlinkSupabaseIdentity(identityId);
      await loadAuthSecurity();
      setNotice("로그인 공급자 연결을 해제했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인 공급자 연결을 해제하지 못했습니다.");
    } finally {
      setSecurityLoading(false);
    }
  };

  const revokeOtherSessions = async () => {
    setSecurityLoading(true);
    clearMessages();
    try {
      await signOutSupabaseSession("others");
      setNotice("현재 브라우저를 제외한 다른 Supabase 세션을 폐기했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "다른 세션 폐기에 실패했습니다.");
    } finally {
      setSecurityLoading(false);
    }
  };

  const createInvitation = async () => {
    if (invitationBindingMode === "email" && !invitationEmail.trim()) {
      setError("초대 대상 이메일을 입력해 주세요.");
      return;
    }
    if (
      invitationBindingMode === "identity"
      && (!invitationIssuer.trim() || !invitationSubject.trim())
    ) {
      setError("Supabase Auth issuer와 subject를 모두 입력해 주세요.");
      return;
    }
    setInvitationLoading(true);
    setIssuedInvitationUrl(null);
    clearMessages();
    try {
      const issued = await issueTenantInvitation({
        role: invitationRole,
        expiresInHours: invitationExpiresInHours,
        ...(invitationBindingMode === "email"
          ? { invitedEmail: invitationEmail }
          : {
            invitedIssuer: invitationIssuer,
            invitedSubject: invitationSubject,
          }),
      });
      const invitationUrl = new URL(
        "/api/auth/supabase/invitation/start",
        window.location.origin,
      );
      invitationUrl.searchParams.set("token", issued.token);
      invitationUrl.searchParams.set("next", "/start");
      setIssuedInvitationUrl(invitationUrl.toString());
      if (invitationBindingMode === "email") {
        setInvitationEmail("");
      } else {
        setInvitationSubject("");
      }
      await loadInvitations();
      setNotice(
        "초대를 발급했습니다. 원문 토큰은 다시 조회할 수 없으므로 아래 링크를 지금 안전하게 전달해 주세요.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "초대 발급에 실패했습니다.");
    } finally {
      setInvitationLoading(false);
    }
  };

  const copyInvitationLink = async () => {
    if (!issuedInvitationUrl) return;
    try {
      await navigator.clipboard.writeText(issuedInvitationUrl);
      setNotice("일회성 초대 링크를 클립보드에 복사했습니다.");
    } catch {
      setError("클립보드 접근이 차단되었습니다. 링크를 직접 선택해 복사해 주세요.");
    }
  };

  const revokeInvitation = async (invitationId: string) => {
    setInvitationLoading(true);
    setIssuedInvitationUrl(null);
    clearMessages();
    try {
      await revokeTenantInvitation(
        invitationId,
        "tenant administrator revoked invitation from settings",
      );
      await loadInvitations();
      setNotice("대기 중인 초대를 취소했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "초대 취소에 실패했습니다.");
    } finally {
      setInvitationLoading(false);
    }
  };

  const saveAiProfile = async () => {
    const submittedApiKey = apiKeyInputRef.current?.value.trim() ?? "";
    const hasNewCredential = Boolean(submittedApiKey);
    clearApiKeyInput();
    setLoading(true);
    clearMessages();
    try {
      const res = await fetch(`${apiBase}/auth/${hasNewCredential ? "ai-credentials" : "ai-profile"}`, {
        method: "POST",
        headers: {
          ...authHeaders("web"),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          provider,
          model: model.trim() || selectedProvider?.defaultModel || "gemini-2.5-flash",
          ...(hasNewCredential ? { apiKey: submittedApiKey, expiresOnLogout: true } : {}),
          status: "active",
          allowSystemFallback,
        }),
      });
      if (!res.ok) {
        if (hasNewCredential) {
          throw new Error(
            `AI API Key를 안전 저장소에 연결하지 못했습니다. 입력값은 폐기되었습니다. (HTTP ${res.status})`,
          );
        }
        throw new Error(await res.text());
      }
      await res.json();
      await loadAiProfile();
      setNotice(
        hasNewCredential
          ? "AI 모델과 세션용 API Key를 연결했습니다. 원문은 프로필 DB에 저장되지 않으며 로그아웃 시 폐기됩니다."
          : "AI 모델 설정을 저장했습니다.",
      );
    } catch (err) {
      setError(
        hasNewCredential
          ? "AI API Key를 안전 저장소에 연결하지 못했습니다. 입력값은 폐기되었습니다."
          : err instanceof Error
            ? err.message
            : "AI 모델 설정 저장에 실패했습니다.",
      );
    } finally {
      clearApiKeyInput();
      setLoading(false);
    }
  };

  const resetAiProfile = async () => {
    clearApiKeyInput();
    setLoading(true);
    clearMessages();
    try {
      const res = await fetch(`${apiBase}/auth/ai-profile`, {
        method: "DELETE",
        headers: authHeaders("web"),
      });
      if (!res.ok) throw new Error(await res.text());
      const body = (await res.json()) as AiProfileResponse;
      setAiProfile(body);
      setProvider(body.fallbackPolicy?.provider ?? body.effectiveAi?.provider ?? "gemini");
      setModel(body.fallbackPolicy?.model ?? body.effectiveAi?.model ?? "gemini-2.5-flash");
      clearApiKeyInput();
      setAllowSystemFallback(true);
      setNotice("개인 AI 설정을 삭제했습니다. 시스템 기본 AI 설정을 사용합니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI 모델 설정 삭제에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const effective = aiProfile?.effectiveAi;

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-10">
      <div>
        <p className="text-sm font-bold text-slate-500">REALYTICS OS 설정</p>
        <h1 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
          로그인과 AI 두뇌 설정
        </h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
          사용자가 로그인하면 본인의 LLM API Key와 모델을 우선 사용합니다. 개인 API가 없고 대체 사용을 허용하면 시스템에 설정된 AI를 사용합니다.
          모델 호출이 불가능하면 규칙 기반으로 지원되는 검토를 진행합니다. API Key는 코드·GitHub·로그에 기록하지 않으며,
          로그아웃 시 브라우저에 남아 있는 임시 AI 키 저장소는 자동 삭제됩니다.
        </p>
      </div>

      {error ? <Alert tone="red">{error}</Alert> : null}
      {notice ? <Alert tone="green">{notice}</Alert> : null}

      <section className="grid gap-4 xl:grid-cols-[1fr_0.85fr]">
        <Panel title="로그인 세션" icon={<UserRound className="h-5 w-5 text-indigo-600" />}>
          {hasLocalPilot ? (
            <div className="grid gap-3 md:grid-cols-2">
              <TextInput label="Tenant ID" value={tenantId} onChange={setTenantId} />
              <TextInput label="Tenant Name" value={tenantName} onChange={setTenantName} />
              <TextInput label="User ID" value={userId} onChange={setUserId} />
              <TextInput label="Email" value={email} onChange={setEmail} />
              <TextInput label="Display Name" value={displayName} onChange={setDisplayName} />
              <label className="block">
                <span className="text-xs font-semibold text-slate-500">역할</span>
                <select
                  value={role}
                  onChange={(event) => setRole(event.target.value)}
                  className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                >
                  {roleOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-slate-500">
                  {roleOptions.find((item) => item.value === role)?.description}
                </p>
              </label>
            </div>
          ) : (
            <p className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
              운영 환경에서는 Supabase/OIDC로 검증된 계정만 사용할 수 있습니다.
              로컬 파일럿 사용자·세션 생성 기능은 표시되지 않습니다.
            </p>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            {hasLocalPilot ? (
              <button
                type="button"
                onClick={() => void login()}
                disabled={loading}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-bold text-white disabled:opacity-50 dark:bg-white dark:text-slate-950"
              >
                <ShieldCheck className="h-4 w-4" />
                {loading ? "처리 중..." : "파일럿 로그인"}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => {
                clearApiKeyInput();
                clearMessages();
                void logout().catch((err) => {
                  setError(
                    err instanceof Error
                      ? err.message
                      : "로그인 세션을 종료하지 못했습니다. 다시 시도해 주세요.",
                  );
                });
              }}
              data-testid="settings-logout"
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 text-sm font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <LogOut className="h-4 w-4" />
              로그아웃
            </button>
          </div>
        </Panel>

        <Panel title="현재 사용자" icon={<CheckCircle2 className="h-5 w-5 text-emerald-600" />}>
          {user && session ? (
            <div className="space-y-3">
              <InfoRow label="사용자" value={accountDisplayName(user)} />
              <AccountProfileEditor key={user.userId} />
              <InfoRow label="Tenant" value={user.tenantId ?? session.tenantId ?? "-"} />
              <InfoRow label="역할" value={user.role ?? "-"} />
              <InfoRow label="인증 공급자" value={session.provider ?? "verified-cookie"} />
              <div>
                <p className="text-xs font-semibold uppercase text-slate-500">권한</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {permissions.map((permission) => (
                    <span
                      key={permission}
                      className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    >
                      {permission}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-300 p-5 text-sm text-slate-500 dark:border-slate-700">
              아직 로그인 세션이 없습니다. 파일럿 로그인 후 프로젝트와 AI 설정이 사용자 기준으로 저장됩니다.
            </div>
          )}
        </Panel>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.85fr_1.15fr]">
        <Panel title="운영 인증 Readiness" icon={<ShieldCheck className="h-5 w-5 text-emerald-600" />}>
          <div className="space-y-3">
            <InfoRow label="Supabase 설정" value={authReadiness?.configured ? "설정됨" : "미설정"} />
            <InfoRow label="Google provider" value={providerReadinessLabel(authReadiness, "google")} />
            <InfoRow label="Kakao provider" value={providerReadinessLabel(authReadiness, "kakao")} />
            <InfoRow label="FastAPI JWKS + Membership" value={authReadiness?.backend.ready ? "준비됨" : "점검 필요"} />
            <InfoRow label="종합 상태" value={authReadiness?.operational ? "구성 확인" : "운영 검증 미완료"} />
            {authReadiness?.limitations.length ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
                {authReadiness.limitations.join(" · ")}
              </div>
            ) : null}
            <button
              type="button"
              onClick={() => void loadAuthSecurity()}
              disabled={securityLoading}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 text-sm font-bold text-slate-700 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200"
            >
              <RotateCcw className="h-4 w-4" />
              운영 상태 다시 확인
            </button>
          </div>
        </Panel>

        <Panel title="계정 연결·MFA·세션" icon={<ShieldPlus className="h-5 w-5 text-blue-600" />}>
          {!user ? (
            <p className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500 dark:border-slate-700">
              Supabase 계정으로 로그인하면 연결된 공급자와 MFA 상태를 관리할 수 있습니다.
            </p>
          ) : (
            <div className="space-y-5">
              <div>
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-bold text-slate-900 dark:text-white">로그인 공급자</h3>
                  <div className="flex gap-2">
                    <button type="button" onClick={() => void linkIdentity("google")} disabled={!googleLinkAvailable || !recentAal2Eligible || securityLoading} className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700">
                      <Link2 className="mr-1 inline h-3.5 w-3.5" />{securityLoading && !securityProfile ? "Google 확인 중" : googleIdentityLinked ? "Google 연결됨" : "Google 연결"}
                    </button>
                    <button type="button" onClick={() => void linkIdentity("kakao")} disabled={!kakaoLinkAvailable || !recentAal2Eligible || securityLoading} className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700">
                      <Link2 className="mr-1 inline h-3.5 w-3.5" />{securityLoading && !securityProfile ? "Kakao 확인 중" : kakaoIdentityLinked ? "Kakao 연결됨" : authReadiness?.providers.kakao ? "Kakao 설정 필요" : "Kakao 연결"}
                    </button>
                  </div>
                </div>
                <div className="mt-2 space-y-2">
                  {(securityProfile?.identities ?? []).map((identity) => (
                    <div key={identity.identityId} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
                      <div>
                        <p className="text-sm font-bold text-slate-900 dark:text-white">{providerLabel(identity.provider)}</p>
                        <p className="text-xs text-slate-500">{identity.email ?? "공급자 이메일 비공개"}</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => void removeIdentity(identity.identityId, identity.provider)}
                        disabled={
                          securityLoading
                          || !recentAal2Eligible
                          || identity.provider === "email"
                          || !["google", "kakao"].includes(identity.provider)
                          || (securityProfile?.identities.length ?? 0) < 2
                        }
                        className="rounded-md p-2 text-rose-600 disabled:cursor-not-allowed disabled:opacity-30"
                        aria-label={`${providerLabel(identity.provider)} 연결 해제`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-bold text-slate-900 dark:text-white">다중요소 인증</h3>
                    <p className="text-xs text-slate-500">현재 세션: {securityLoading && !securityProfile ? "확인 중" : securityProfile?.assurance.currentLevel ?? "미확인"}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void beginTotpEnrollment()}
                    disabled={
                      securityLoading
                        || Boolean(totpEnrollment)
                        || !totpEnrollmentEligible
                    }
                    className="rounded-md bg-blue-600 px-3 py-2 text-xs font-bold text-white disabled:opacity-50"
                  >
                    TOTP 등록
                  </button>
                </div>
                {securityProfile && !totpEnrollmentEligible ? (
                  <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
                    Supabase TOTP 등록에는 이 Auth 계정에 귀속된 검증된 이메일이 필요합니다.
                    이메일은 Tenant 가입·계정 자동 연결·권한 판단에 사용하지 않습니다. 현재 계정에는
                    MFA용 이메일이 없으므로, 다른 사용자와 중복되지 않는 기관 이메일을 검증한 뒤 등록해 주세요.
                  </p>
                ) : null}
                <div className="mt-2 space-y-2">
                  {(securityProfile?.factors ?? []).map((factor) => (
                    <div key={factor.factorId} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
                      <div>
                        <p className="text-sm font-bold text-slate-900 dark:text-white">{factor.friendlyName ?? factor.factorType}</p>
                        <p className="text-xs text-slate-500">{factor.status}</p>
                      </div>
                      <button type="button" onClick={() => void removeMfaFactor(factor.factorId)} disabled={securityLoading || !recentAal2Eligible} className="rounded-md p-2 text-rose-600 disabled:opacity-30" aria-label="MFA 요소 해제">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>
                {!recentAal2Eligible && verifiedTotpFactors.length ? (
                  <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/20">
                    <p className="text-sm font-bold text-amber-950 dark:text-amber-100">AAL2 추가 인증</p>
                    <p className="mt-1 text-xs leading-5 text-amber-800 dark:text-amber-200">
                      등록된 인증 앱의 현재 코드를 확인하면 이 세션만 AAL2로 승격됩니다. 코드는 저장하거나 다시 표시하지 않습니다.
                    </p>
                    {verifiedTotpFactors.length > 1 ? (
                      <label className="mt-3 block">
                        <span className="text-xs font-semibold text-amber-900 dark:text-amber-100">인증 요소</span>
                        <select
                          aria-label="AAL2 인증 요소"
                          value={selectedReauthenticationFactorId}
                          onChange={(event) => setReauthenticationFactorId(event.target.value)}
                          className="mt-1 h-10 w-full rounded-lg border border-amber-200 bg-white px-3 text-sm dark:border-amber-800 dark:bg-slate-950"
                        >
                          {verifiedTotpFactors.map((factor) => (
                            <option key={factor.factorId} value={factor.factorId}>
                              {factor.friendlyName ?? "TOTP 인증 앱"}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                    <input
                      ref={reauthenticationCodeRef}
                      aria-label="AAL2 일회용 코드"
                      autoComplete="one-time-code"
                      inputMode="numeric"
                      maxLength={8}
                      pattern="[0-9]*"
                      placeholder="6자리 일회용 코드"
                      className="mt-3 h-10 w-full rounded-lg border border-amber-200 bg-white px-3 text-sm dark:border-amber-800 dark:bg-slate-950"
                      onInput={(event) => {
                        event.currentTarget.value = event.currentTarget.value.replace(/\D/g, "").slice(0, 8);
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => void reauthenticateTotpSession()}
                      disabled={securityLoading || !selectedReauthenticationFactorId}
                      className="mt-2 rounded-md bg-amber-900 px-3 py-2 text-xs font-bold text-white disabled:opacity-50 dark:bg-amber-200 dark:text-amber-950"
                    >
                      현재 세션 AAL2 인증
                    </button>
                  </div>
                ) : null}
              </div>

              {totpEnrollment ? (
                <div className="grid gap-4 rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-900 dark:bg-blue-950/20 sm:grid-cols-[160px_1fr]">
                  <Image src={totpEnrollment.qrCode.trimEnd()} alt="REALYTICS MFA QR 코드" width={160} height={160} unoptimized className="rounded bg-white" />
                  <div>
                    <p className="text-sm font-bold text-slate-900 dark:text-white">인증 앱 등록</p>
                    <p className="mt-1 break-all font-mono text-xs text-slate-600 dark:text-slate-300">{totpEnrollment.secret}</p>
                    <input value={totpCode} onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, "").slice(0, 8))} inputMode="numeric" placeholder="일회용 코드" className="mt-3 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-950" />
                    <button type="button" onClick={() => void confirmTotpEnrollment()} disabled={securityLoading || totpCode.length < 6} className="mt-2 rounded-md bg-slate-950 px-3 py-2 text-xs font-bold text-white disabled:opacity-50 dark:bg-white dark:text-slate-950">
                      코드 확인 및 활성화
                    </button>
                  </div>
                </div>
              ) : null}

              <div className="border-t border-slate-200 pt-4 dark:border-slate-800">
                <button type="button" onClick={() => void revokeOtherSessions()} disabled={securityLoading} className="inline-flex h-10 items-center gap-2 rounded-lg border border-rose-200 px-4 text-sm font-bold text-rose-700 disabled:opacity-50 dark:border-rose-900 dark:text-rose-300">
                  <LogOut className="h-4 w-4" />
                  다른 기기의 세션 폐기
                </button>
              </div>
            </div>
          )}
        </Panel>
      </section>

      {canManageUsers ? (
        <TenantMembershipManagement
          currentUser={user}
          assuranceLevel={securityProfile?.assurance.currentLevel ?? null}
        />
      ) : null}

      <OnboardingAccessRequestManagement
        assuranceLevel={securityProfile?.assurance.currentLevel ?? null}
        currentTenantId={user?.tenantId ?? null}
        canManageUsers={canManageUsers}
      />

      {canManageUsers ? (
        <Panel title="테넌트 사용자 초대" icon={<MailPlus className="h-5 w-5 text-blue-600" />}>
          <div className="grid gap-5 xl:grid-cols-[0.8fr_1.2fr]">
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                    초대 바인딩 방식
                  </span>
                  <div className="mt-1 grid grid-cols-2 rounded-lg border border-slate-200 bg-slate-50 p-1 dark:border-slate-700 dark:bg-slate-950">
                    {([
                      ["email", "이메일 초대"],
                      ["identity", "Supabase 신원 초대"],
                    ] as const).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={invitationBindingMode === value}
                        onClick={() => {
                          setInvitationBindingMode(value);
                          setInvitationEmail("");
                          setInvitationSubject("");
                          setIssuedInvitationUrl(null);
                          clearMessages();
                        }}
                        className={`h-9 rounded-md px-3 text-xs font-bold transition ${
                          invitationBindingMode === value
                            ? "bg-white text-blue-700 shadow-sm dark:bg-slate-800 dark:text-blue-300"
                            : "text-slate-500 dark:text-slate-400"
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                {invitationBindingMode === "email" ? (
                  <label className="block sm:col-span-2">
                    <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                      초대 대상 이메일
                    </span>
                    <input
                      type="email"
                      value={invitationEmail}
                      onChange={(event) => setInvitationEmail(event.target.value)}
                      placeholder="name@company.com"
                      autoComplete="off"
                      className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 dark:border-slate-700 dark:bg-slate-950 dark:text-white dark:focus:ring-blue-950"
                    />
                  </label>
                ) : (
                  <div className="space-y-3 sm:col-span-2">
                    <label className="block">
                      <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                        Supabase Auth issuer
                      </span>
                      <input
                        type="url"
                        value={invitationIssuer}
                        onChange={(event) => setInvitationIssuer(event.target.value)}
                        placeholder="https://project-ref.supabase.co/auth/v1"
                        autoComplete="off"
                        spellCheck={false}
                        className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 font-mono text-xs text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 dark:border-slate-700 dark:bg-slate-950 dark:text-white dark:focus:ring-blue-950"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                        Supabase Auth subject (사용자 UUID)
                      </span>
                      <input
                        type="text"
                        value={invitationSubject}
                        onChange={(event) => setInvitationSubject(event.target.value)}
                        placeholder="00000000-0000-4000-8000-000000000000"
                        autoComplete="off"
                        spellCheck={false}
                        className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 font-mono text-xs text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 dark:border-slate-700 dark:bg-slate-950 dark:text-white dark:focus:ring-blue-950"
                      />
                    </label>
                    <p className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-800 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-200">
                      Supabase Auth 사용자 상세의 서명된 <code>iss</code>와 <code>sub</code>를 그대로 사용합니다.
                      이메일은 전송하거나 동일 이메일 계정 연결의 근거로 사용하지 않습니다.
                    </p>
                  </div>
                )}
                <label className="block">
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                    부여 역할
                  </span>
                  <select
                    value={invitationRole}
                    onChange={(event) => setInvitationRole(event.target.value as TenantInvitationRole)}
                    className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                  >
                    {invitationRoleOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                    유효기간
                  </span>
                  <select
                    value={invitationExpiresInHours}
                    onChange={(event) => setInvitationExpiresInHours(Number(event.target.value))}
                    className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                  >
                    <option value={24}>24시간</option>
                    <option value={72}>3일</option>
                    <option value={168}>7일</option>
                    <option value={720}>30일</option>
                  </select>
                </label>
              </div>
              <p className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                역할과 테넌트는 서버가 초대 레코드에서 확정합니다. 관리자·법무 승인자 등 민감 역할은 기존 정책대로 최근 AAL2 인증이 필요합니다.
              </p>
              <button
                type="button"
                onClick={() => void createInvitation()}
                disabled={
                  invitationLoading
                  || (invitationBindingMode === "email"
                    ? !invitationEmail.trim()
                    : !invitationIssuer.trim() || !invitationSubject.trim())
                }
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                <MailPlus className="h-4 w-4" />
                {invitationLoading ? "처리 중" : "일회성 초대 발급"}
              </button>

              {issuedInvitationUrl ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/20">
                  <p className="text-xs font-bold text-amber-900 dark:text-amber-200">
                    지금 한 번만 표시되는 초대 링크
                  </p>
                  <input
                    readOnly
                    value={issuedInvitationUrl}
                    aria-label="일회성 초대 링크"
                    onFocus={(event) => event.currentTarget.select()}
                    className="mt-2 h-10 w-full rounded-lg border border-amber-200 bg-white px-3 font-mono text-xs text-slate-900 dark:border-amber-900 dark:bg-slate-950 dark:text-white"
                  />
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void copyInvitationLink()}
                      className="inline-flex h-9 items-center gap-2 rounded-md bg-slate-950 px-3 text-xs font-bold text-white dark:bg-white dark:text-slate-950"
                    >
                      <Copy className="h-3.5 w-3.5" /> 링크 복사
                    </button>
                    <button
                      type="button"
                      onClick={() => setIssuedInvitationUrl(null)}
                      className="h-9 rounded-md border border-amber-300 px-3 text-xs font-bold text-amber-900 dark:border-amber-800 dark:text-amber-200"
                    >
                      화면에서 지우기
                    </button>
                  </div>
                </div>
              ) : null}
            </div>

            <div>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-bold text-slate-950 dark:text-white">최근 초대</h3>
                  <p className="mt-1 text-xs text-slate-500">토큰 원문은 목록과 데이터베이스에 저장되지 않습니다.</p>
                </div>
                <button
                  type="button"
                  onClick={() => void loadInvitations()}
                  disabled={invitationLoading}
                  className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 px-3 text-xs font-bold text-slate-700 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200"
                >
                  <RotateCcw className="h-3.5 w-3.5" /> 새로고침
                </button>
              </div>
              <div className="mt-3 space-y-2">
                {invitations.length ? invitations.map((invitation) => (
                  <div
                    key={invitation.invitationId}
                    className="grid gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800 sm:grid-cols-[1fr_auto] sm:items-center"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate text-sm font-bold text-slate-950 dark:text-white">
                          {invitation.invitedEmail ?? invitation.invitedSubject ?? "신원 바인딩 초대"}
                        </p>
                        <InvitationStatus status={invitation.status} />
                      </div>
                      {invitation.invitedIssuer && invitation.invitedSubject ? (
                        <p className="mt-1 break-all font-mono text-[11px] leading-4 text-slate-500">
                          {invitation.invitedIssuer} · {invitation.invitedSubject}
                        </p>
                      ) : null}
                      <p className="mt-1 text-xs text-slate-500">
                        {invitationRoleLabel(invitation.role)} · 발급 {formatDateTime(invitation.issuedAt)}
                      </p>
                      <p className="mt-1 inline-flex items-center gap-1 text-xs text-slate-500">
                        <Clock3 className="h-3.5 w-3.5" /> 만료 {formatDateTime(invitation.expiresAt)}
                      </p>
                    </div>
                    {invitation.status === "pending" ? (
                      <button
                        type="button"
                        onClick={() => void revokeInvitation(invitation.invitationId)}
                        disabled={invitationLoading}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-rose-200 px-3 text-xs font-bold text-rose-700 disabled:opacity-50 dark:border-rose-900 dark:text-rose-300"
                      >
                        <Ban className="h-3.5 w-3.5" /> 초대 취소
                      </button>
                    ) : null}
                  </div>
                )) : (
                  <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">
                    {invitationLoading ? "초대 목록을 불러오는 중입니다." : "발급된 초대가 없습니다."}
                  </div>
                )}
              </div>
            </div>
          </div>
        </Panel>
      ) : null}

      <section className="grid gap-4 xl:grid-cols-[1fr_0.85fr]">
        <Panel title="사용자 LLM API 설정" icon={<KeyRound className="h-5 w-5 text-blue-600" />}>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">AI Provider</span>
              <select
                value={provider}
                onChange={(event) => {
                  clearApiKeyInput();
                  const next = providerOptions.find((item) => item.value === event.target.value);
                  setProvider(event.target.value);
                  setModel(next?.defaultModel ?? "");
                }}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              >
                {providerOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <TextInput
              label="모델명"
              value={model}
              onChange={setModel}
              name="realytics-llm-model-id"
              autoComplete="off"
            />
            <label className="block md:col-span-2">
              <span className="text-xs font-semibold text-slate-500">API Key</span>
              <div className="relative mt-1">
                <input
                  ref={apiKeyInputRef}
                  type="text"
                  name="realytics-llm-api-token"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-label="AI API Key"
                  data-testid="ai-api-key-input"
                  data-form-type="other"
                  data-1p-ignore="true"
                  data-lpignore="true"
                  data-bwignore="true"
                  data-protonpass-ignore="true"
                  onChange={(event) => setApiKeyLength(event.currentTarget.value.length)}
                  placeholder={aiProfile?.profile?.apiKeyMasked ?? "새 API Key를 입력하면 기존 키를 교체합니다."}
                  className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-transparent caret-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:caret-white dark:placeholder:text-slate-500"
                  style={{ WebkitTextSecurity: "disc" } as React.CSSProperties}
                />
                {apiKeyLength > 0 ? (
                  <span
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-y-0 left-3 right-3 flex items-center overflow-hidden whitespace-nowrap font-mono text-sm tracking-wider text-slate-900 dark:text-white"
                  >
                    {"•".repeat(Math.min(apiKeyLength, 96))}
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-xs text-slate-500">
                키 원문은 전용 보안 입력 API로만 전달되고 프로필 DB·로그에는 저장되지 않습니다. 세션 저장소 또는 운영 Secret Manager의 참조값만 보관하며 로그아웃 시 폐기합니다.
              </p>
            </label>
            <label className="flex items-start gap-2 rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-700 md:col-span-2">
              <input
                type="checkbox"
                checked={allowSystemFallback}
                onChange={(event) => setAllowSystemFallback(event.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="block font-bold text-slate-800 dark:text-slate-100">개인 API가 실패하거나 없으면 시스템 기본 AI 사용 허용</span>
                <span className="block text-xs text-slate-500">
                  파일럿에서는 흐름이 끊기지 않도록 권장합니다. 단, 민감 문서 분석은 회사 정책에 맞춰 비활성화할 수 있습니다.
                </span>
              </span>
            </label>
          </div>

          <div className="mt-5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void saveAiProfile()}
              disabled={loading || !user}
              data-testid="save-ai-profile"
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-bold text-white disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              AI 설정 저장
            </button>
            <button
              type="button"
              onClick={() => void loadAiProfile()}
              disabled={loading || !user}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <RotateCcw className="h-4 w-4" />
              다시 조회
            </button>
            <button
              type="button"
              onClick={() => void resetAiProfile()}
              disabled={loading || !user}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-rose-200 px-4 text-sm font-bold text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-900 dark:text-rose-300 dark:hover:bg-rose-950/30"
            >
              개인 설정 삭제
            </button>
          </div>
        </Panel>

        <Panel title="현재 적용 AI 두뇌" icon={<Sparkles className="h-5 w-5 text-violet-600" />}>
          <div className="space-y-3">
            <InfoRow label="선택 소스" value={sourceLabel(effective?.selectionSource)} />
            <InfoRow label="Provider" value={effective?.provider ?? "-"} />
            <InfoRow label="Runtime" value={effective?.runtimeProvider ?? "-"} />
            <InfoRow label="Model" value={effective?.model ?? "-"} />
            <InfoRow label="직접 모델 호출" value={effective?.directModelCall ? "가능" : "대기 또는 fallback"} />
            <InfoRow label="필요한 API Key" value={effective?.requiresApiKey ?? "없음"} />
            <div className="rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-600 dark:bg-slate-950 dark:text-slate-300">
              Agent는 이 두뇌를 문서분류, 필드 추출, 가정충돌 설명, 리스크 반박, 보고서 문장 작성에 사용합니다.
              사업수지, Cash Flow, LTV/LTC, Waterfall, Exit 분양률 계산은 계산엔진이 수행합니다.
            </div>
          </div>
        </Panel>
      </section>
    </div>
  );
}

function sourceLabel(value?: string) {
  if (value === "user_ai_profile") return "개인 API 프로필";
  if (value === "user_ai_profile_without_key_system_fallback") return "개인 프로필 + 시스템 fallback";
  if (value === "system_free_fallback") return "시스템 기본 AI";
  return value ?? "-";
}

function providerLabel(value: string) {
  if (value === "google") return "Google";
  if (value === "kakao") return "Kakao";
  if (value === "email") return "Email";
  return value;
}

function providerReadinessLabel(
  readiness: SupabaseAuthReadiness | null,
  provider: "google" | "kakao",
) {
  if (readiness?.providerReady?.[provider]) return "운영 가능";
  if (readiness?.providers[provider]) return "활성·추가 설정 필요";
  return "미확인/비활성";
}

function invitationRoleLabel(role: TenantInvitationRole) {
  return invitationRoleOptions.find((option) => option.value === role)?.label ?? role;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(date);
}

function InvitationStatus({ status }: { status: TenantInvitation["status"] }) {
  const presentation = {
    pending: {
      label: "대기",
      className: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200",
    },
    accepted: {
      label: "수락",
      className: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-200",
    },
    revoked: {
      label: "취소",
      className: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-200",
    },
    expired: {
      label: "만료",
      className: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200",
    },
  }[status];
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-bold ${presentation.className}`}>
      {presentation.label}
    </span>
  );
}

function Panel({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-re-panel border border-re-border bg-re-surface p-5 text-re-text shadow-re-panel">
      <div className="mb-5 flex items-center gap-2">
        {icon}
        <h2 className="text-lg font-bold text-re-text">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Alert({ tone, children }: { tone: "green" | "red"; children: React.ReactNode }) {
  const classes =
    tone === "green"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300"
      : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-300";
  return <div className={`rounded-lg border p-4 text-sm font-semibold ${classes}`}>{children}</div>;
}

function TextInput({
  label,
  value,
  onChange,
  name,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  name?: string;
  autoComplete?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-re-secondary">{label}</span>
      <input
        name={name}
        autoComplete={autoComplete}
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 h-10 w-full rounded-re-control border border-re-border bg-re-surface px-3 text-sm text-re-text outline-none focus:border-re-action"
      />
    </label>
  );
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-re-control border border-re-border px-3 py-2">
      <p className="text-xs font-semibold uppercase text-re-muted">{label}</p>
      <p className={`mt-1 break-all text-sm font-bold text-re-text ${mono ? "font-mono" : ""}`}>
        {value}
      </p>
    </div>
  );
}
