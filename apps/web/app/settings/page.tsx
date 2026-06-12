"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { CheckCircle2, KeyRound, LogOut, ShieldCheck, UserRound } from "lucide-react";
import {
  apiBase,
  clearStoredAuthSession,
  createLocalPilotSession,
  getStoredAuthSession,
  resolveCurrentIdentity,
  type StoredAuthSession,
} from "../../lib/auth-client";

const roleOptions = [
  { value: "reviewer", label: "심사역 / Reviewer", description: "리스크 검토, 기준값 승인, 보고서 export 권한" },
  { value: "analyst", label: "분석 담당 / Analyst", description: "프로젝트 작성, 증빙 조회, 분석 실행 권한" },
  { value: "admin", label: "관리자 / Admin", description: "사용자 관리와 모든 운영 권한" },
  { value: "viewer", label: "조회자 / Viewer", description: "프로젝트와 보고서 조회 중심 권한" },
];

interface IdpStatus {
  enabled: boolean;
  mode: string;
  provider?: string;
  issuer?: string;
  audience?: string;
  jwksUrlConfigured?: boolean;
  hs256SecretConfigured?: boolean;
  unsignedDevTokensAllowed?: boolean;
  tenantIsolationMode?: string;
  limitations?: string[];
}

export default function SettingsPage() {
  const [tenantId, setTenantId] = useState("pilot-tenant");
  const [tenantName, setTenantName] = useState("REALYTICS Pilot Workspace");
  const [userId, setUserId] = useState("credit_officer");
  const [email, setEmail] = useState("credit.officer@example.com");
  const [displayName, setDisplayName] = useState("심사 담당자");
  const [role, setRole] = useState("reviewer");
  const [session, setSession] = useState<StoredAuthSession | null>(null);
  const [idpStatus, setIdpStatus] = useState<IdpStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const stored = getStoredAuthSession();
    setSession(stored);
    if (stored?.session?.sessionId) {
      resolveCurrentIdentity(stored.session.sessionId)
        .then((identity) => {
          if (identity.authenticated) {
            setSession({
              user: identity.user,
              session: identity.session ?? stored.session,
              permissions: identity.permissions ?? stored.permissions,
            });
          }
        })
        .catch(() => undefined);
    }
    fetch(`${apiBase}/auth/idp/status`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => setIdpStatus(body as IdpStatus | null))
      .catch(() => setIdpStatus(null));
  }, []);

  const selectedRole = useMemo(() => roleOptions.find((item) => item.value === role), [role]);

  const login = async () => {
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      const created = await createLocalPilotSession({
        tenantId: tenantId.trim() || "pilot-tenant",
        tenantName: tenantName.trim() || "REALYTICS Pilot Workspace",
        userId: userId.trim() || "credit_officer",
        email: email.trim() || undefined,
        displayName: displayName.trim() || userId.trim() || "심사 담당자",
        role,
      });
      setSession(created);
      setNotice("파일럿 세션이 생성되었습니다. 주요 화면의 API 요청에 세션 ID가 함께 전송됩니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "세션 생성에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const logout = () => {
    clearStoredAuthSession();
    setSession(null);
    setNotice("브라우저에 저장된 파일럿 세션을 제거했습니다.");
  };

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">REALYTICS Settings</p>
          <h1 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            사용자 인증 및 권한
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
            파일럿에서는 로컬 세션을 사용하고, 운영 배포에서는 외부 IdP Bearer token, tenant isolation,
            production RBAC, secret manager를 사용합니다. API key나 IdP secret은 화면과 브라우저에 저장하지 않습니다.
          </p>
        </div>
        <StatusPill tone={session ? "green" : "slate"}>
          {session ? `현재 사용자: ${session.user.displayName ?? session.user.userId}` : "세션 없음"}
        </StatusPill>
      </div>

      {error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-700 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-300">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm font-semibold text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300">
          {notice}
        </div>
      ) : null}

      <section className="grid gap-4 xl:grid-cols-[1fr_0.9fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-indigo-600" />
            <h2 className="text-lg font-bold text-slate-950 dark:text-white">파일럿 세션 생성</h2>
          </div>

          <div className="mt-5 grid gap-3 md:grid-cols-2">
            <TextInput label="Tenant ID" value={tenantId} onChange={setTenantId} />
            <TextInput label="Tenant Name" value={tenantName} onChange={setTenantName} />
            <TextInput label="User ID" value={userId} onChange={setUserId} />
            <TextInput label="Email" value={email} onChange={setEmail} />
            <TextInput label="Display Name" value={displayName} onChange={setDisplayName} />
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Role</span>
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
              <p className="mt-1 text-xs text-slate-500">{selectedRole?.description ?? ""}</p>
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
              {loading ? "세션 생성 중..." : "로컬 세션 생성"}
            </button>
            <button
              type="button"
              onClick={logout}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 text-sm font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <LogOut className="h-4 w-4" />
              세션 제거
            </button>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center gap-2">
            <UserRound className="h-5 w-5 text-emerald-600" />
            <h2 className="text-lg font-bold text-slate-950 dark:text-white">현재 사용자</h2>
          </div>

          {session ? (
            <div className="mt-4 space-y-3">
              <InfoRow label="User" value={`${session.user.displayName ?? session.user.userId} (${session.user.userId})`} />
              <InfoRow label="Tenant" value={session.user.tenantId ?? session.session.tenantId ?? "-"} />
              <InfoRow label="Role" value={session.user.role ?? "-"} />
              <InfoRow label="Session ID" value={session.session.sessionId} mono />
              <div>
                <p className="text-xs font-semibold uppercase text-slate-500">권한</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {session.permissions.length > 0 ? (
                    session.permissions.map((permission) => (
                      <span
                        key={permission}
                        className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200"
                      >
                        {permission}
                      </span>
                    ))
                  ) : (
                    <span className="text-xs text-slate-500">권한 정보 없음</span>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-5 text-sm text-slate-500 dark:border-slate-700">
              아직 브라우저에 저장된 세션이 없습니다.
            </div>
          )}
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.9fr_1fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-blue-600" />
            <h2 className="text-lg font-bold text-slate-950 dark:text-white">운영 IdP 연동 상태</h2>
          </div>
          <div className="mt-4 grid gap-3">
            <InfoRow label="Mode" value={idpStatus?.mode ?? "확인 중"} />
            <InfoRow label="Provider" value={idpStatus?.provider ?? "-"} />
            <InfoRow label="Tenant Isolation" value={idpStatus?.tenantIsolationMode ?? "-"} />
            <InfoRow label="Issuer" value={idpStatus?.issuer ?? "-"} />
            <InfoRow label="Audience" value={idpStatus?.audience ?? "-"} />
            <div className="flex flex-wrap gap-2">
              <StatusPill tone={idpStatus?.enabled ? "green" : "slate"}>
                {idpStatus?.enabled ? "Bearer token 사용 가능" : "로컬 세션 모드"}
              </StatusPill>
              <StatusPill tone={idpStatus?.hs256SecretConfigured ? "green" : "slate"}>
                HS256 secret {idpStatus?.hs256SecretConfigured ? "설정됨" : "미설정"}
              </StatusPill>
              <StatusPill tone={idpStatus?.jwksUrlConfigured ? "green" : "slate"}>
                JWKS URL {idpStatus?.jwksUrlConfigured ? "설정됨" : "미설정"}
              </StatusPill>
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <h2 className="text-lg font-bold text-slate-950 dark:text-white">운영 전환 체크리스트</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {[
              "REALYTICS_IDP_MODE=hs256 또는 OIDC/JWKS 모드 설정",
              "REALYTICS_IDP_ISSUER / AUDIENCE 고정",
              "REALYTICS_RBAC_MODE=strict 적용",
              "REALYTICS_TENANT_ISOLATION_MODE=strict 적용",
              "API key와 IdP secret은 secret manager에 저장",
              "사용자·tenant·role audit trail 검증",
            ].map((item) => (
              <div
                key={item}
                className="flex items-start gap-2 rounded-lg border border-slate-200 p-3 text-sm font-semibold text-slate-700 dark:border-slate-800 dark:text-slate-200"
              >
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                {item}
              </div>
            ))}
          </div>
          {idpStatus?.limitations?.length ? (
            <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-500 dark:bg-slate-800/70">
              {idpStatus.limitations.join(" ")}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function TextInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
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
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
      <p className="text-xs font-semibold uppercase text-slate-500">{label}</p>
      <p className={`mt-1 truncate text-sm font-bold text-slate-950 dark:text-white ${mono ? "font-mono" : ""}`}>
        {value}
      </p>
    </div>
  );
}

function StatusPill({ children, tone }: { children: ReactNode; tone: "green" | "slate" }) {
  const cls = tone === "green"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300"
    : "border-slate-200 bg-white text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200";
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm font-semibold ${cls}`}>
      {children}
    </div>
  );
}
