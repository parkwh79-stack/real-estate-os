"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  KeyRound,
  LogOut,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { apiBase, authHeaders, createLocalPilotSession } from "../../lib/auth-client";
import { useAuth } from "../../components/auth/auth-context";

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
  const [tenantId, setTenantId] = useState("pilot-tenant");
  const [tenantName, setTenantName] = useState("REALYTICS Pilot Workspace");
  const [userId, setUserId] = useState("credit_officer");
  const [email, setEmail] = useState("credit.officer@example.com");
  const [displayName, setDisplayName] = useState("심사역");
  const [role, setRole] = useState("reviewer");
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("gemini-2.5-flash");
  const [apiKey, setApiKey] = useState("");
  const [allowSystemFallback, setAllowSystemFallback] = useState(true);
  const [aiProfile, setAiProfile] = useState<AiProfileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedProvider = useMemo(
    () => providerOptions.find((item) => item.value === provider),
    [provider],
  );

  const clearMessages = () => {
    setNotice(null);
    setError(null);
  };

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
    setNotice(null);
    setError(null);
    const res = await fetch(`${apiBase}/auth/ai-profile`, {
      headers: authHeaders("web"),
      cache: "no-store",
    });
    if (!res.ok) {
      setAiProfile(null);
      if (res.status === 401) setError("로그인 후 AI 모델 설정을 조회할 수 있습니다.");
      return;
    }
    const body = (await res.json()) as AiProfileResponse;
    setAiProfile(body);
    if (body.profile?.provider) setProvider(body.profile.provider);
    if (body.profile?.model) setModel(body.profile.model);
    if (typeof body.profile?.allowSystemFallback === "boolean") {
      setAllowSystemFallback(body.profile.allowSystemFallback);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    void loadAiProfile();
  }, [user, loadAiProfile]);

  const saveAiProfile = async () => {
    setLoading(true);
    clearMessages();
    try {
      const res = await fetch(`${apiBase}/auth/ai-profile`, {
        method: "POST",
        headers: {
          ...authHeaders("web"),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          provider,
          model: model.trim() || selectedProvider?.defaultModel || "gemini-2.5-flash",
          apiKey: apiKey.trim() || undefined,
          status: "active",
          allowSystemFallback,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const body = (await res.json()) as AiProfileResponse;
      setAiProfile(body);
      setApiKey("");
      setNotice("AI 모델 설정을 저장했습니다. API Key는 서버에 암호화 저장되고 화면에는 다시 표시되지 않습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI 모델 설정 저장에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const resetAiProfile = async () => {
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
      setProvider("gemini");
      setModel("gemini-2.5-flash");
      setApiKey("");
      setAllowSystemFallback(true);
      setNotice("개인 AI 설정을 삭제했습니다. 시스템 기본 Gemini fallback으로 전환됩니다.");
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
          사용자가 로그인하면 본인의 LLM API Key와 모델을 우선 사용합니다. 개인 API가 없으면 시스템 기본 Gemini 무료 프로필을 사용하고,
          그것도 없을 때는 규칙 기반 deterministic fallback으로 분석 흐름을 유지합니다. API Key는 코드·GitHub·로그에 기록하지 않으며,
          로그아웃 시 브라우저에 남아 있는 임시 AI 키 저장소는 자동 삭제됩니다.
        </p>
      </div>

      {error ? <Alert tone="red">{error}</Alert> : null}
      {notice ? <Alert tone="green">{notice}</Alert> : null}

      <section className="grid gap-4 xl:grid-cols-[1fr_0.85fr]">
        <Panel title="로그인 세션" icon={<UserRound className="h-5 w-5 text-indigo-600" />}>
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

          <div className="mt-5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void login()}
              disabled={loading}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-bold text-white disabled:opacity-50 dark:bg-white dark:text-slate-950"
            >
              <ShieldCheck className="h-4 w-4" />
              {loading ? "처리 중..." : "파일럿 로그인"}
            </button>
            <button
              type="button"
              onClick={logout}
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
              <InfoRow label="사용자" value={`${user.displayName ?? user.userId} (${user.userId})`} />
              <InfoRow label="Tenant" value={user.tenantId ?? session.tenantId ?? "-"} />
              <InfoRow label="역할" value={user.role ?? "-"} />
              <InfoRow label="Session ID" value={session.sessionId} mono />
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

      <section className="grid gap-4 xl:grid-cols-[1fr_0.85fr]">
        <Panel title="사용자 LLM API 설정" icon={<KeyRound className="h-5 w-5 text-blue-600" />}>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">AI Provider</span>
              <select
                value={provider}
                onChange={(event) => {
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
            <TextInput label="모델명" value={model} onChange={setModel} />
            <label className="block md:col-span-2">
              <span className="text-xs font-semibold text-slate-500">API Key</span>
              <input
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={aiProfile?.profile?.apiKeyMasked ?? "새 API Key를 입력하면 기존 키를 교체합니다."}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
              <p className="mt-1 text-xs text-slate-500">
                저장 후에는 마스킹된 값만 표시됩니다. 파일럿 저장은 암호화 저장소를 사용하지만, 운영 환경에서는 Secret Manager 연동과 키 회전 정책이 필요합니다.
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
                <span className="block font-bold text-slate-800 dark:text-slate-100">개인 API가 실패하거나 없으면 시스템 Gemini fallback 허용</span>
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
  if (value === "system_free_fallback") return "시스템 무료 fallback";
  return value ?? "-";
}

function Panel({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-5 flex items-center gap-2">
        {icon}
        <h2 className="text-lg font-bold text-slate-950 dark:text-white">{title}</h2>
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
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-500">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
      />
    </label>
  );
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
      <p className="text-xs font-semibold uppercase text-slate-500">{label}</p>
      <p className={`mt-1 break-all text-sm font-bold text-slate-900 dark:text-white ${mono ? "font-mono" : ""}`}>
        {value}
      </p>
    </div>
  );
}
