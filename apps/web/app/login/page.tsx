"use client";

import { FormEvent, useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, BrainCircuit, CheckCircle2, Database, KeyRound, ShieldAlert, Sparkles, TrendingUp, Users } from "lucide-react";
import Image from "next/image";
import { apiBase, createLocalPilotSession, getStoredAuthSession, loginLocalPilotSession } from "../../lib/auth-client";

type AuthTab = "social" | "direct";

export default function LoginPage() {
  const router = useRouter();
  const [tab, setTab] = useState<AuthTab>("social");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Stats state (Initialized to 0 to fetch real DB values)
  const [stats, setStats] = useState({
    usersCount: 0,
    dataUsageCount: 0,
    projectScaleTrillionWon: 0,
  });

  // Direct form state
  const [directMode, setDirectMode] = useState<"login" | "signup">("login");
  const [tenantId, setTenantId] = useState("realytics-pilot");
  const [tenantName, setTenantName] = useState("REALYTICS Pilot Workspace");
  const [userId, setUserId] = useState("pilot-reviewer");
  const [displayName, setDisplayName] = useState("파일럿 심사역");
  const [role, setRole] = useState("analyst");

  useEffect(() => {
    const stored = getStoredAuthSession();
    if (stored?.session?.sessionId) {
      router.replace(resolveNextPath());
    }

    // Fetch real DB stats. These hero numbers are cosmetic, so a failure (e.g. the
    // API server momentarily down) must NOT surface a dev error overlay — swallow it.
    let active = true;
    fetch(`${apiBase}/auth/stats`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (active && data && data.usersCount !== undefined) {
          setStats({
            usersCount: data.usersCount,
            dataUsageCount: data.dataUsageCount,
            projectScaleTrillionWon: data.projectScaleTrillionWon,
          });
        }
      })
      .catch(() => {
        /* API server unreachable — keep default stats, do not raise. */
      });
    return () => {
      active = false;
    };
  }, [router]);

  async function handleKakaoLogin() {
    setError(null);
    setSubmitting(true);
    try {
      const nextPath = resolveNextPath();
      const res = await fetch(`${apiBase}/integrations/kakao/login/authorize-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ redirectPath: nextPath }),
      });
      
      if (!res.ok) {
        throw new Error("카카오 로그인 설정이 서버에 준비되지 않았습니다.");
      }
      const data = await res.json();
      if (!data.authorizeUrl) {
        throw new Error("카카오 인증 URL을 가져오지 못했습니다.");
      }
      
      window.location.href = data.authorizeUrl;
    } catch (err) {
      setError(err instanceof Error ? err.message : "카카오 로그인 연동에 실패했습니다.");
      setSubmitting(false);
    }
  }

  async function handleGoogleLogin() {
    setError(null);
    setSubmitting(true);
    try {
      const nextPath = resolveNextPath();
      const res = await fetch(`${apiBase}/integrations/google/login/authorize-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ redirectPath: nextPath }),
      });
      
      if (!res.ok) {
        throw new Error("구글 로그인 설정이 서버에 준비되지 않았습니다.");
      }
      const data = await res.json();
      if (!data.authorizeUrl) {
        throw new Error("구글 인증 URL을 가져오지 못했습니다.");
      }
      
      window.location.href = data.authorizeUrl;
    } catch (err) {
      setError(err instanceof Error ? err.message : "구글 로그인 연동에 실패했습니다.");
      setSubmitting(false);
    }
  }

  async function onDirectSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (directMode === "login") {
        await loginLocalPilotSession({
          tenantId: tenantId.trim() || "realytics-pilot",
          userId: userId.trim() || "pilot-reviewer",
        });
      } else {
        await createLocalPilotSession({
          tenantId: tenantId.trim() || "realytics-pilot",
          tenantName: tenantName.trim() || "REALYTICS Pilot Workspace",
          userId: userId.trim() || "pilot-reviewer",
          displayName: displayName.trim() || userId.trim() || "파일럿 심사역",
          role,
        });
      }
      router.replace(resolveNextPath());
    } catch (err) {
      setError(err instanceof Error ? err.message : "요청을 처리하지 못했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  function formatUsage(val: number) {
    if (val >= 1000000) return (val / 1000000).toFixed(1) + "M+";
    if (val >= 1000) return (val / 1000).toFixed(1) + "K+";
    return val.toLocaleString();
  }

  return (
    <main className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
      
      {/* LEFT: Dynamic Visuals & Stats (Stunning Hero Section with Hover Effect) */}
      <section className="group relative hidden w-[55%] flex-col justify-between overflow-hidden lg:flex xl:w-[60%]">
        
        {/* Background Layer: Character Image with Interactive Hover Clarity */}
        <div className="absolute inset-0 z-0 bg-slate-950">
          <div 
            className="absolute inset-0 bg-cover bg-center bg-no-repeat opacity-50 mix-blend-luminosity transition-all duration-700 group-hover:opacity-90 group-hover:mix-blend-normal group-hover:scale-105"
            style={{ backgroundImage: "url('/images/hero-character.png')" }}
          />
          {/* Gradients to ensure text readability */}
          <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-950/60 to-transparent transition-opacity duration-700 group-hover:opacity-40" />
          <div className="absolute inset-0 bg-gradient-to-r from-slate-950/80 via-slate-950/40 to-transparent transition-opacity duration-700 group-hover:opacity-50" />
        </div>

        {/* Top Content: Title */}
        <div className="relative z-10 flex flex-col items-start p-12 xl:p-16">
          <div className="inline-flex items-center gap-2 rounded-full border border-sky-400/30 bg-sky-500/10 px-3 py-1.5 text-xs font-bold tracking-[0.15em] text-sky-400 backdrop-blur-md mb-6">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-75"></span>
              <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-500"></span>
            </span>
            REALYTICS OS ACTIVE
          </div>

          <h1 className="text-4xl font-black leading-[1.2] text-white tracking-tight xl:text-5xl">
            부동산 심사 및 개발의<br />
            <span className="bg-gradient-to-r from-sky-400 via-indigo-400 to-emerald-400 bg-clip-text text-transparent drop-shadow-sm">새로운 패러다임을 경험하세요</span>
          </h1>
          
          <p className="mt-6 max-w-xl text-base font-medium leading-relaxed text-slate-300 xl:text-lg">
            인증된 심사역 및 테넌트를 기준으로 데이터베이스(DB)에 안전하게 저장된 사업계획서, 토지조서, 사업수지, 에이전트 분석 및 리스크 검토 이력을 실시간으로 조회하고 관리합니다.
          </p>

          <div className="mt-8 max-w-lg rounded-2xl border border-amber-500/30 bg-slate-950/50 p-5 text-sm leading-6 text-amber-200 backdrop-blur-xl">
            <div className="flex items-center gap-2 font-bold text-amber-400">
              <ShieldAlert className="h-5 w-5 shrink-0" />
              <span>강력한 보안 및 접근 권한 보호 정책</span>
            </div>
            <p className="mt-2 text-slate-300">
              회원가입 및 로그인이 되어 있지 않은 미인증 사용자는 서비스 보안 정책에 의해 주요 기능 화면으로 이동할 수 없으며, 모든 접근이 본 로그인 페이지로 철저히 제한됩니다.
            </p>
          </div>

          <div className="mt-4 max-w-lg rounded-2xl border border-sky-400/30 bg-sky-500/10 p-5 text-sm leading-6 text-sky-100 backdrop-blur-xl">
            <div className="flex items-center gap-2 font-bold text-sky-300">
              <BrainCircuit className="h-5 w-5 shrink-0" />
              <span>AI 두뇌 선택 안내</span>
            </div>
            <p className="mt-2 text-slate-300">
              별도 API Key가 없어도 기본 Gemini 무료 fallback으로 문서분류와 1차 판단을 시작할 수 있습니다.
              더 높은 품질, 더 큰 한도, 조직별 모델 통제를 원하면 로그인 후 개인 또는 조직의 AI API Key를 입력하세요.
            </p>
          </div>
        </div>

        {/* Bottom Content: Live Statistics Dashboard */}
        <div className="relative z-10 w-full p-12 xl:p-16">
          <p className="mb-4 text-sm font-bold tracking-widest text-slate-400">LIVE SYSTEM STATISTICS (REAL-TIME DB)</p>
          <div className="grid grid-cols-3 gap-4 xl:gap-6">
            <StatCard 
              icon={<Users className="h-5 w-5 text-sky-400" />}
              label="현재 회원가입자 수" 
              value={stats.usersCount.toLocaleString()} 
              unit="명" 
              trend="DB 실시간 등록" 
              delay="0ms"
            />
            <StatCard 
              icon={<Database className="h-5 w-5 text-indigo-400" />}
              label="실제 데이터 처리 건수" 
              value={formatUsage(stats.dataUsageCount)} 
              unit="건" 
              trend="API 실시간 호출" 
              delay="150ms"
            />
            <StatCard 
              icon={<TrendingUp className="h-5 w-5 text-emerald-400" />}
              label="취급/검토 프로젝트" 
              value={stats.projectScaleTrillionWon.toFixed(1)} 
              unit="조 원" 
              trend="누적 PF 심사 규모" 
              delay="300ms"
            />
          </div>
        </div>
      </section>

      {/* RIGHT: Login/Signup Secure Box with Interactive Watermark Shade */}
      <section className="group/right relative flex w-full flex-col justify-center overflow-hidden bg-white px-6 py-12 shadow-2xl lg:w-[45%] xl:w-[40%] xl:px-16 dark:bg-slate-900">
        {/* Background Logo Watermark / Shade spanning gracefully behind the form without white box borders */}
        <div className="pointer-events-none absolute inset-0 z-0 flex items-center justify-center overflow-hidden">
          <div className="relative h-[130%] w-[130%] max-w-none opacity-15 transition-all duration-700 group-hover/right:opacity-90 group-hover/right:scale-110 dark:opacity-25 dark:group-hover/right:opacity-95 grayscale group-hover/right:grayscale-0">
            <Image src="/images/hero-logo-transparent.png" alt="Realytics Logo Watermark" fill sizes="50vw" className="object-contain" />
          </div>
        </div>

        <div className="relative z-10 mx-auto w-full max-w-md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-sky-600 dark:text-sky-400">Authentication</p>
              <h2 className="mt-1 text-3xl font-black text-slate-950 dark:text-white">보안 로그인</h2>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-bold text-emerald-700 shadow-sm dark:bg-emerald-950/40 dark:text-emerald-300">
              <CheckCircle2 className="h-4 w-4" /> DB 연동
            </span>
          </div>

          <div className="mt-6 overflow-hidden rounded-2xl border border-sky-200 bg-gradient-to-br from-sky-50 via-white to-violet-50 p-5 shadow-sm dark:border-sky-900/50 dark:from-sky-950/30 dark:via-slate-900 dark:to-violet-950/30">
            <div className="flex items-start gap-3">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-slate-950 text-sky-300 shadow-lg dark:bg-white dark:text-slate-950">
                <BrainCircuit className="h-6 w-6" />
              </div>
              <div>
                <p className="text-sm font-black text-slate-950 dark:text-white">REALYTICS AI Brain</p>
                <p className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-300">
                  API Key가 없으면 시스템 기본 Gemini 무료 fallback으로 시작합니다. 더 나은 분석 품질과 한도를 원하면 로그인 후 AI 설정에서 OpenAI, Claude, Gemini, Groq, OpenRouter, DeepSeek, Local LLM API Key를 입력하세요.
                </p>
              </div>
            </div>
            <div className="mt-4 grid gap-2 text-xs font-semibold text-slate-600 dark:text-slate-300 sm:grid-cols-3">
              <div className="rounded-xl border border-white/70 bg-white/80 p-3 dark:border-slate-800 dark:bg-slate-950/50">
                <Sparkles className="mb-2 h-4 w-4 text-sky-500" />
                기본 제공: Gemini 무료 fallback
              </div>
              <div className="rounded-xl border border-white/70 bg-white/80 p-3 dark:border-slate-800 dark:bg-slate-950/50">
                <KeyRound className="mb-2 h-4 w-4 text-violet-500" />
                개인 API Key: 로그인 후 입력
              </div>
              <div className="rounded-xl border border-white/70 bg-white/80 p-3 dark:border-slate-800 dark:bg-slate-950/50">
                <ShieldAlert className="mb-2 h-4 w-4 text-emerald-500" />
                로그아웃 시 브라우저 임시 키 삭제
              </div>
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-500 dark:text-slate-400">
              보안 원칙: API Key는 코드, GitHub, 로그, 감사 화면에 기록하지 않습니다. 서버 저장 방식은 Secret Manager 전환 전까지 파일럿용 암호화 저장으로만 사용하며, 민감 프로젝트는 세션용 키 또는 사내 Local LLM 사용을 권장합니다.
            </p>
          </div>

          <div className="mt-8 grid grid-cols-2 rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
            <button
              type="button"
              onClick={() => setTab("social")}
              className={`h-11 rounded-lg text-sm font-bold transition-all duration-200 ${
                tab === "social"
                  ? "bg-white text-slate-950 shadow-md dark:bg-slate-950 dark:text-white"
                  : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
              }`}
            >
              소셜 연동 (보안)
            </button>
            <button
              type="button"
              onClick={() => setTab("direct")}
              className={`h-11 rounded-lg text-sm font-bold transition-all duration-200 ${
                tab === "direct"
                  ? "bg-white text-slate-950 shadow-md dark:bg-slate-950 dark:text-white"
                  : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
              }`}
            >
              파일럿 ID 로그인
            </button>
          </div>

          {tab === "social" ? (
            <div className="mt-8 space-y-5 animate-in fade-in slide-in-from-bottom-2 duration-500">
              <p className="text-sm leading-6 text-slate-500 dark:text-slate-400">
                실제 카카오 계정으로 안전하게 연동 인증 후 세션을 데이터베이스(DB)에 등록하여 즉시 심사를 진행하실 수 있습니다.
              </p>

              {/* Kakao Button */}
              <button
                type="button"
                onClick={handleKakaoLogin}
                disabled={submitting}
                className="group relative flex h-14 w-full items-center justify-center gap-3 rounded-xl bg-[#FEE500] px-4 font-bold text-[#191919] shadow-lg shadow-[#FEE500]/20 transition-all hover:-translate-y-0.5 hover:shadow-xl hover:shadow-[#FEE500]/30 active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:translate-y-0"
              >
                <svg className="h-6 w-6 fill-current" viewBox="0 0 24 24">
                  <path d="M12 3C6.477 3 2 6.477 2 10.772c0 2.766 1.83 5.19 4.606 6.634l-1.171 4.298c-.098.36.309.658.621.452l5.06-3.342c.291.026.586.04.884.04 5.523 0 10-3.477 10-7.772C22 6.477 17.523 3 12 3z" />
                </svg>
                <span className="text-base">{submitting ? "보안 연결 중..." : "실제 카카오 계정으로 연동하기"}</span>
              </button>

              {/* Google Button */}
              <button
                type="button"
                onClick={handleGoogleLogin}
                disabled={submitting}
                className="group relative flex h-14 w-full items-center justify-center gap-3 rounded-xl border border-slate-200 bg-white px-4 font-bold text-slate-700 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md hover:border-slate-300 active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:translate-y-0 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-slate-600"
              >
                <svg className="h-6 w-6" viewBox="0 0 24 24">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
                </svg>
                <span className="text-base">{submitting ? "보안 연결 중..." : "실제 구글 계정으로 연동하기"}</span>
              </button>

              <div className="relative my-8 text-center">
                <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-slate-200 dark:border-slate-800" /></div>
                <span className="relative bg-white px-3 text-xs font-bold uppercase tracking-wider text-slate-400 dark:bg-slate-900">OAuth 2.0 Enterprise Security</span>
              </div>

              <div className="rounded-2xl border border-slate-100 bg-slate-50 p-5 dark:border-slate-800/50 dark:bg-slate-800/30">
                <p className="flex items-center gap-2 font-bold text-slate-800 dark:text-slate-200">
                  <ShieldAlert className="h-4 w-4 text-emerald-500" />
                  실제 보안 인증 안내
                </p>
                <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">
                  위 버튼을 클릭하면 실제 OAuth 서버를 통해 계정이 연동됩니다. 시스템은 허가되지 않은 접근을 원천 차단하며, 가짜 정보 입력 없이 안전하게 사용자 인증 및 세션 확인이 진행됩니다.
                </p>
              </div>
            </div>
          ) : (
            <form onSubmit={onDirectSubmit} className="mt-8 space-y-5 animate-in fade-in slide-in-from-bottom-2 duration-500">
              <div className="flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
                <button
                  type="button"
                  onClick={() => setDirectMode("login")}
                  className={`flex-1 rounded-md py-2 text-xs font-bold ${directMode === "login" ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-white" : "text-slate-500"}`}
                >
                  기존 ID 로그인
                </button>
                <button
                  type="button"
                  onClick={() => setDirectMode("signup")}
                  className={`flex-1 rounded-md py-2 text-xs font-bold ${directMode === "signup" ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-white" : "text-slate-500"}`}
                >
                  신규 ID 회원가입
                </button>
              </div>

              <Field label="워크스페이스 ID" value={tenantId} onChange={setTenantId} />
              <Field label="사용자 ID" value={userId} onChange={setUserId} />
              {directMode === "signup" ? (
                <>
                  <Field label="워크스페이스명" value={tenantName} onChange={setTenantName} />
                  <Field label="표시 이름" value={displayName} onChange={setDisplayName} />
                  <label className="block">
                    <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">권한</span>
                    <select
                      value={role}
                      onChange={(event) => setRole(event.target.value)}
                      className="mt-2 h-12 w-full rounded-xl border border-slate-200 bg-white px-4 text-sm font-semibold outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500 dark:border-slate-700 dark:bg-slate-950"
                    >
                      <option value="analyst">분석역</option>
                      <option value="reviewer">심사역</option>
                      <option value="admin">관리자</option>
                      <option value="viewer">조회자</option>
                    </select>
                  </label>
                </>
              ) : null}

              <button
                type="submit"
                disabled={submitting}
                className="mt-6 flex h-14 w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-base font-bold text-white shadow-lg transition-all hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
              >
                {submitting ? "DB 연동 처리 중..." : directMode === "login" ? "인증하고 OS 런처로 이동" : "안전하게 DB 가입 후 OS 런처로 이동"} 
                <ArrowRight className="h-5 w-5" />
              </button>
            </form>
          )}

          {error ? (
            <p className="mt-6 animate-in fade-in rounded-xl bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-700 border border-rose-100 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
              {error}
            </p>
          ) : null}
        </div>
      </section>
    </main>
  );
}

function resolveNextPath() {
  if (typeof window === "undefined") return "/start";
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next || next === "/" || next === "/dashboard") return "/start";
  return next.startsWith("/") ? next : "/start";
}

function Field({
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
      <span className="text-sm font-bold text-slate-700 dark:text-slate-300">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 h-12 w-full rounded-xl border border-slate-200 bg-white px-4 text-sm font-semibold outline-none transition-colors focus:border-sky-500 focus:ring-1 focus:ring-sky-500 dark:border-slate-700 dark:bg-slate-950"
      />
    </label>
  );
}

function StatCard({ label, value, unit, trend, icon, delay }: { label: string; value: string; unit: string; trend: string; icon: ReactNode; delay: string }) {
  return (
    <div 
      className="relative overflow-hidden rounded-2xl border border-white/10 bg-slate-900/40 p-5 backdrop-blur-xl transition-all hover:bg-slate-800/60 animate-in fade-in slide-in-from-bottom-4 duration-700 fill-mode-both"
      style={{ animationDelay: delay }}
    >
      <div className="flex items-center gap-2 text-sm font-bold text-slate-300">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-4 flex items-baseline gap-1">
        <span className="text-4xl font-black text-white tracking-tight">{value}</span>
        <span className="text-lg font-bold text-sky-400">{unit}</span>
      </div>
      <p className="mt-2 text-xs font-semibold text-emerald-400">{trend}</p>
    </div>
  );
}
