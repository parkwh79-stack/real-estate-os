"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, KeyRound, LogOut, ShieldCheck, UserRound } from "lucide-react";
import {
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

export default function SettingsPage() {
  const [tenantId, setTenantId] = useState("pilot-tenant");
  const [tenantName, setTenantName] = useState("REALYTICS Pilot Workspace");
  const [userId, setUserId] = useState("credit_officer");
  const [email, setEmail] = useState("credit.officer@example.com");
  const [displayName, setDisplayName] = useState("심사역");
  const [role, setRole] = useState("reviewer");
  const [session, setSession] = useState<StoredAuthSession | null>(null);
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
        displayName: displayName.trim() || userId.trim() || "심사역",
        role,
      });
      setSession(created);
      setNotice("파일럿 세션을 생성했습니다. 이후 주요 화면의 API 요청에는 세션 ID가 함께 전송됩니다.");
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
            로그인 / 사용자 세션
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
            현재는 파일럿용 로컬 로그인입니다. 사용자, tenant, role, session을 서버에 저장하고 audit log로 추적합니다.
            운영 배포 전에는 실제 IdP, tenant isolation, secret manager, production RBAC로 교체해야 합니다.
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200">
          {session ? `현재 사용자: ${session.user.displayName ?? session.user.userId}` : "세션 없음"}
        </div>
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
              {loading ? "세션 생성 중..." : "로그인 세션 생성"}
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
            <h2 className="text-lg font-bold text-slate-950 dark:text-white">현재 세션</h2>
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

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-lg font-bold text-slate-950 dark:text-white">API / 데이터 연동 상태</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {["ValueMap API", "VWorld / 공공데이터", "Korean Law MCP"].map((name) => (
            <div
              key={name}
              className="flex items-center gap-2 rounded-lg border border-slate-200 p-3 text-sm font-semibold text-slate-700 dark:border-slate-800 dark:text-slate-200"
            >
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              {name}
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-slate-500">
          이 화면은 로그인과 세션 전파 기준선입니다. 외부 API key는 평문 저장하지 않고 운영에서는 secret manager로 이전해야 합니다.
        </p>
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
